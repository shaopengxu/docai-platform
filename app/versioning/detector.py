"""
DocAI Platform - 版本自动识别模块
上传新文档时，自动判断是否为已有文档的新版本
"""

from __future__ import annotations

import structlog
from sqlalchemy import text

from app.core.embedding import encode_single
from app.core.infrastructure import get_db_session, get_qdrant_client
from app.core.llm_client import llm_light
from app.core.models import VersionMatchResult
from config.settings import settings

logger = structlog.get_logger()


class VersionDetector:
    """判断新上传的文档是否为已有文档的新版本"""

    # 只有置信度高于此阈值才自动关联
    AUTO_LINK_THRESHOLD = 0.8

    async def detect(
        self,
        new_doc_id: str,
        title: str,
        doc_summary: str | None = None,
        doc_type: str | None = None,
    ) -> VersionMatchResult:
        """
        综合多种策略判断新文档是否为某个已有文档的新版本。

        Returns:
            VersionMatchResult: 包含是否为新版本、匹配的文档ID、置信度和原因
        """
        candidates: list[dict] = []

        # 策略 1: 标题相似度 (pg_trgm)
        title_candidates = await self._find_by_title_similarity(
            title, new_doc_id, doc_type
        )
        candidates.extend(title_candidates)

        # 策略 2: 文档摘要向量相似度
        if doc_summary:
            content_candidates = await self._find_by_content_similarity(
                doc_summary, new_doc_id
            )
            candidates.extend(content_candidates)

        if not candidates:
            return VersionMatchResult(is_new_version=False)

        # 去重 (按 doc_id)
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["doc_id"] not in seen:
                seen.add(c["doc_id"])
                unique_candidates.append(c)

        # 策略 3: LLM 最终确认
        result = await self._llm_verify(
            title, doc_summary or "", unique_candidates
        )

        logger.info(
            "Version detection completed",
            new_doc_id=new_doc_id,
            result=result.model_dump(),
        )

        return result

    async def _find_by_title_similarity(
        self, title: str, exclude_doc_id: str, doc_type: str | None
    ) -> list[dict]:
        """使用 pg_trgm 的 similarity 函数查找标题相似的文档"""
        async with get_db_session() as session:
            # 查找标题相似且仍为最新版本的文档
            query = text("""
                SELECT doc_id, title, version_number, doc_type, doc_summary,
                       similarity(title, :title) AS sim_score
                FROM documents
                WHERE doc_id != :exclude_id
                  AND processing_status = 'ready'
                  AND is_latest = TRUE
                  AND similarity(title, :title) > 0.4
                ORDER BY sim_score DESC
                LIMIT 5
            """)
            result = await session.execute(
                query, {"title": title, "exclude_id": exclude_doc_id}
            )
            rows = result.fetchall()

        return [
            {
                "doc_id": str(row[0]),
                "title": row[1],
                "version_number": row[2],
                "doc_type": row[3],
                "doc_summary": (row[4] or "")[:300],
                "match_source": "title_similarity",
                "sim_score": float(row[5]),
            }
            for row in rows
        ]

    async def _find_by_content_similarity(
        self, doc_summary: str, exclude_doc_id: str
    ) -> list[dict]:
        """用文档摘要向量在 Qdrant 中查找内容相似的文档"""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        try:
            qdrant = get_qdrant_client()
            summary_vector = encode_single(doc_summary[:2000])

            results = await qdrant.search(
                collection_name=settings.qdrant_collection_name,
                query_vector=summary_vector,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="chunk_type",
                            match=MatchValue(value="doc_summary"),
                        ),
                        FieldCondition(
                            key="is_latest",
                            match=MatchValue(value=True),
                        ),
                    ]
                ),
                limit=5,
                score_threshold=0.75,
            )

            candidates = []
            for point in results:
                payload = point.payload or {}
                pid = payload.get("doc_id", "")
                if pid and pid != exclude_doc_id:
                    candidates.append(
                        {
                            "doc_id": pid,
                            "title": payload.get("doc_title", ""),
                            "version_number": "",
                            "doc_type": "",
                            "doc_summary": payload.get("content", "")[:300],
                            "match_source": "content_similarity",
                            "sim_score": float(point.score),
                        }
                    )
            return candidates

        except Exception as e:
            logger.warning("Content similarity search failed", error=str(e))
            return []

    async def _llm_verify(
        self,
        new_title: str,
        new_summary: str,
        candidates: list[dict],
    ) -> VersionMatchResult:
        """用 LLM 最终确认新文档是否为候选文档的新版本，并判断谁更新"""
        candidates_text = "\n".join(
            f"- [{c['doc_id'][:8]}...] 标题:「{c['title']}」 "
            f"版本:{c['version_number'] or '未知'} "
            f"匹配来源:{c['match_source']} "
            f"相似度:{c['sim_score']:.2f} "
            f"摘要:{c['doc_summary'][:150]}"
            for c in candidates
        )

        prompt = f"""你是一个文档版本识别助手。请判断新上传文档是否为以下候选文档中某一个的新版本/修订版。

新上传文档标题：「{new_title}」
新上传文档摘要：{new_summary[:500]}

候选已有文档（从标题相似度和内容相似度检索）：
{candidates_text}

判断标准：
1. 如果文档标题核心部分相同（允许版本号、日期等后缀不同），且内容主题一致，则很可能是新版本。
2. 如果只是标题略有相似但内容主题不同，则不是新版本。
3. 需要区分"同一文档的不同版本"和"同一类别但不同文档"。
4. 如果判断为同一文档的不同版本，请进一步判断**谁是更新的版本**，依据包括：
   - 文档标题或摘要中出现的版本号（如 v1.0、v2.0、第X版、修订版等）
   - 文档中出现的日期（签署日期、生效日期、修订日期等）
   - 内容范围的变化（如新增了条款、扩展了内容说明更新）

请返回 JSON（只返回 JSON，不要其他文字）：
{{
  "is_new_version": true/false,
  "matched_doc_id": "匹配到的文档完整 doc_id（如果 is_new_version 为 true）或 null",
  "confidence": 0.0到1.0之间的置信度,
  "reason": "简要说明判断理由",
  "new_is_newer": true/false,
  "detected_version": "从新上传文档内容中提取的版本号（如 v2.0、第二版等），提取不到则为 null"
}}

字段说明：
- new_is_newer: 新上传文档是否确实比已有文档更新。如果无法判断，默认为 true。
- detected_version: 尝试从新上传文档标题/摘要中提取版本标识。"""

        try:
            result = await llm_light.generate_json(prompt)

            matched_doc_id = result.get("matched_doc_id")
            # 确保 matched_doc_id 是完整的（LLM 可能只返回截断的 ID）
            if matched_doc_id and len(matched_doc_id) < 36:
                for c in candidates:
                    if c["doc_id"].startswith(matched_doc_id):
                        matched_doc_id = c["doc_id"]
                        break

            confidence = float(result.get("confidence", 0.0))

            return VersionMatchResult(
                is_new_version=bool(result.get("is_new_version", False))
                and confidence >= self.AUTO_LINK_THRESHOLD,
                matched_doc_id=matched_doc_id if result.get("is_new_version") else None,
                matched_title=next(
                    (c["title"] for c in candidates if c["doc_id"] == matched_doc_id),
                    None,
                ),
                confidence=confidence,
                reason=result.get("reason", ""),
                new_is_newer=bool(result.get("new_is_newer", True)),
                detected_version=result.get("detected_version"),
            )

        except Exception as e:
            logger.error("LLM version verification failed", error=str(e))
            return VersionMatchResult(is_new_version=False, reason=f"LLM 调用失败: {e}")


# 全局单例
version_detector = VersionDetector()
