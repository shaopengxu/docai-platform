"""
DocAI Platform - 三层差异对比引擎
Layer 1: 文本级差异（difflib 段落对齐）
Layer 2: 结构级差异（章节增删）
Layer 3: 语义级差异（LLM 分析业务影响）
"""

from __future__ import annotations

import difflib
import json
import uuid
from collections import OrderedDict

import structlog
from sqlalchemy import text

from app.core.infrastructure import get_db_session
from app.core.llm_client import llm
from app.core.models import VersionDiffResponse

logger = structlog.get_logger()


class DiffEngine:
    """三层差异对比引擎"""

    async def compute_full_diff(
        self, old_doc_id: str, new_doc_id: str
    ) -> VersionDiffResponse:
        """
        计算两个文档版本之间的完整差异（文本+结构+语义），
        结果存入 version_diffs 表并返回。
        """
        # 检查是否已有缓存的 diff
        existing = await self._get_existing_diff(old_doc_id, new_doc_id)
        if existing:
            return existing

        logger.info(
            "Computing version diff",
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
        )

        # 加载两个版本的 chunks 和元数据
        old_chunks = await self._load_text_chunks(old_doc_id)
        new_chunks = await self._load_text_chunks(new_doc_id)
        old_meta = await self._load_doc_metadata(old_doc_id)
        new_meta = await self._load_doc_metadata(new_doc_id)

        # Layer 1: 文本级差异
        text_diff = self._compute_text_diff(old_chunks, new_chunks)

        # Layer 2: 结构级差异
        structural_diff = self._compute_structural_diff(old_chunks, new_chunks)

        # Layer 3: 语义级差异 (LLM)
        semantic_diff = await self._compute_semantic_diff(
            old_title=old_meta.get("title", ""),
            new_title=new_meta.get("title", ""),
            old_version=old_meta.get("version_number", ""),
            new_version=new_meta.get("version_number", ""),
            text_diff=text_diff,
            structural_diff=structural_diff,
        )

        # 存入数据库
        diff_id = str(uuid.uuid4())
        await self._store_diff(
            diff_id=diff_id,
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
            text_diff=text_diff,
            structural_diff=structural_diff,
            semantic_diff=semantic_diff,
        )

        return VersionDiffResponse(
            diff_id=diff_id,
            old_version_id=old_doc_id,
            new_version_id=new_doc_id,
            old_title=old_meta.get("title", ""),
            new_title=new_meta.get("title", ""),
            text_diff_data=text_diff,
            structural_changes=structural_diff,
            change_summary=semantic_diff.get("change_summary", ""),
            change_details=semantic_diff.get("change_details", []),
            impact_analysis=semantic_diff.get("impact_analysis", ""),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Layer 1: 文本级差异
    # ─────────────────────────────────────────────────────────────────────

    def _compute_text_diff(
        self,
        old_chunks: list[dict],
        new_chunks: list[dict],
    ) -> dict:
        """按 section_path 对齐后逐段 diff"""
        old_sections = self._group_chunks_by_section(old_chunks)
        new_sections = self._group_chunks_by_section(new_chunks)

        all_section_paths = list(
            OrderedDict.fromkeys(
                list(old_sections.keys()) + list(new_sections.keys())
            )
        )

        section_diffs = []
        for sp in all_section_paths:
            old_text = old_sections.get(sp, "")
            new_text = new_sections.get(sp, "")

            if not old_text and new_text:
                section_diffs.append(
                    {
                        "section_path": sp,
                        "status": "added",
                        "new_text": new_text[:1000],
                    }
                )
            elif old_text and not new_text:
                section_diffs.append(
                    {
                        "section_path": sp,
                        "status": "deleted",
                        "old_text": old_text[:1000],
                    }
                )
            elif old_text != new_text:
                # 产出 unified diff
                old_lines = old_text.splitlines(keepends=True)
                new_lines = new_text.splitlines(keepends=True)
                diff_lines = list(
                    difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile="旧版本",
                        tofile="新版本",
                        lineterm="",
                    )
                )
                # 生成更可读的变更列表
                changes = self._parse_diff_changes(old_text, new_text)
                section_diffs.append(
                    {
                        "section_path": sp,
                        "status": "modified",
                        "changes": changes,
                        "diff_preview": "".join(diff_lines[:50]),
                    }
                )

        stats = {
            "added": sum(1 for d in section_diffs if d["status"] == "added"),
            "deleted": sum(1 for d in section_diffs if d["status"] == "deleted"),
            "modified": sum(1 for d in section_diffs if d["status"] == "modified"),
            "unchanged": len(all_section_paths) - len(section_diffs),
        }

        return {"sections": section_diffs, "stats": stats}

    def _parse_diff_changes(self, old_text: str, new_text: str) -> list[dict]:
        """使用 SequenceMatcher 提取具体的增删改操作"""
        matcher = difflib.SequenceMatcher(None, old_text, new_text)
        changes = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            change = {"type": tag}
            if tag in ("replace", "delete"):
                change["old_text"] = old_text[i1:i2][:500]
            if tag in ("replace", "insert"):
                change["new_text"] = new_text[j1:j2][:500]
            changes.append(change)
        return changes[:30]  # 限制数量

    # ─────────────────────────────────────────────────────────────────────
    # Layer 2: 结构级差异
    # ─────────────────────────────────────────────────────────────────────

    def _compute_structural_diff(
        self,
        old_chunks: list[dict],
        new_chunks: list[dict],
    ) -> dict:
        """比较两个版本的章节结构"""
        old_sections = set(
            c["section_path"] for c in old_chunks if c.get("section_path")
        )
        new_sections = set(
            c["section_path"] for c in new_chunks if c.get("section_path")
        )

        added = sorted(new_sections - old_sections)
        deleted = sorted(old_sections - new_sections)
        common = sorted(old_sections & new_sections)

        # 检测可能的重命名（在 added 和 deleted 中找标题相似的对）
        renamed = []
        remaining_added = list(added)
        remaining_deleted = list(deleted)

        for d_sec in list(remaining_deleted):
            best_match = None
            best_ratio = 0.0
            for a_sec in remaining_added:
                ratio = difflib.SequenceMatcher(None, d_sec, a_sec).ratio()
                if ratio > 0.6 and ratio > best_ratio:
                    best_ratio = ratio
                    best_match = a_sec
            if best_match:
                renamed.append(
                    {
                        "old_name": d_sec,
                        "new_name": best_match,
                        "similarity": round(best_ratio, 2),
                    }
                )
                remaining_added.remove(best_match)
                remaining_deleted.remove(d_sec)

        return {
            "added_sections": remaining_added,
            "deleted_sections": remaining_deleted,
            "renamed_sections": renamed,
            "common_sections": common,
            "total_old": len(old_sections),
            "total_new": len(new_sections),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Layer 3: 语义级差异 (LLM)
    # ─────────────────────────────────────────────────────────────────────

    async def _compute_semantic_diff(
        self,
        old_title: str,
        new_title: str,
        old_version: str,
        new_version: str,
        text_diff: dict,
        structural_diff: dict,
    ) -> dict:
        """用 LLM 分析变更的业务含义"""
        # 准备 diff 摘要传给 LLM
        diff_summary_parts = []

        # 结构变化
        sd = structural_diff
        if sd.get("added_sections"):
            diff_summary_parts.append(
                f"新增章节: {', '.join(sd['added_sections'][:10])}"
            )
        if sd.get("deleted_sections"):
            diff_summary_parts.append(
                f"删除章节: {', '.join(sd['deleted_sections'][:10])}"
            )
        if sd.get("renamed_sections"):
            renames = [
                f"「{r['old_name']}」→「{r['new_name']}」"
                for r in sd["renamed_sections"][:5]
            ]
            diff_summary_parts.append(f"重命名章节: {', '.join(renames)}")

        # 文本变化摘要（取前 10 个修改过的章节的概要）
        modified_sections = [
            s
            for s in text_diff.get("sections", [])
            if s["status"] == "modified"
        ]
        for ms in modified_sections[:10]:
            changes = ms.get("changes", [])
            change_types = [c["type"] for c in changes[:5]]
            diff_summary_parts.append(
                f"章节「{ms['section_path']}」: {len(changes)} 处变更 "
                f"(类型: {', '.join(change_types)})"
            )

        stats = text_diff.get("stats", {})
        diff_summary_parts.append(
            f"统计: {stats.get('added', 0)} 新增, "
            f"{stats.get('deleted', 0)} 删除, "
            f"{stats.get('modified', 0)} 修改, "
            f"{stats.get('unchanged', 0)} 未变"
        )

        diff_summary = "\n".join(diff_summary_parts)

        prompt = f"""请分析以下两个版本文档之间的变更。

文档标题：{new_title}
旧版本：{old_version}
新版本：{new_version}

变更概况：
{diff_summary}

请返回 JSON 格式的分析（只返回 JSON，不要其他文字）：
{{
  "change_summary": "一段话概述主要变更（100-200字）",
  "change_details": [
    {{
      "category": "实质性变更|措辞调整|格式变更|新增内容|删除内容",
      "description": "具体变更描述",
      "location": "涉及的章节",
      "business_impact": "对业务的潜在影响（如适用）"
    }}
  ],
  "impact_analysis": "总体影响评估（50-100字）"
}}

注意：change_details 最多列出 10 条最重要的变更。"""

        try:
            result = await llm.generate_json(prompt)
            return {
                "change_summary": result.get("change_summary", ""),
                "change_details": result.get("change_details", []),
                "impact_analysis": result.get("impact_analysis", ""),
            }
        except Exception as e:
            logger.error("Semantic diff LLM call failed", error=str(e))
            return {
                "change_summary": f"语义分析失败: {e}",
                "change_details": [],
                "impact_analysis": "",
            }

    # ─────────────────────────────────────────────────────────────────────
    # 数据访问辅助
    # ─────────────────────────────────────────────────────────────────────

    def _group_chunks_by_section(self, chunks: list[dict]) -> dict[str, str]:
        """将 chunks 按 section_path 分组拼接文本"""
        sections: dict[str, list[str]] = {}
        for c in chunks:
            sp = c.get("section_path") or "(无章节)"
            sections.setdefault(sp, []).append(c.get("content", ""))
        return {sp: "\n".join(texts) for sp, texts in sections.items()}

    async def _load_text_chunks(self, doc_id: str) -> list[dict]:
        """从 PostgreSQL 加载文档的文本 chunks"""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT chunk_id, section_path, page_numbers, chunk_index,
                           content, chunk_type
                    FROM chunks
                    WHERE doc_id = :doc_id AND chunk_type = 'text'
                    ORDER BY chunk_index
                """),
                {"doc_id": doc_id},
            )
            rows = result.fetchall()

        return [
            {
                "chunk_id": str(row[0]),
                "section_path": row[1] or "",
                "page_numbers": row[2] or [],
                "chunk_index": row[3],
                "content": row[4],
                "chunk_type": row[5],
            }
            for row in rows
        ]

    async def _load_doc_metadata(self, doc_id: str) -> dict:
        """加载文档元数据"""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT title, version_number, doc_type, doc_summary
                    FROM documents
                    WHERE doc_id = :doc_id
                """),
                {"doc_id": doc_id},
            )
            row = result.fetchone()

        if not row:
            return {}
        return {
            "title": row[0],
            "version_number": row[1],
            "doc_type": row[2],
            "doc_summary": row[3],
        }

    async def _get_existing_diff(
        self, old_doc_id: str, new_doc_id: str
    ) -> VersionDiffResponse | None:
        """检查是否已有预计算的 diff"""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT vd.diff_id, vd.text_diff_data, vd.structural_changes,
                           vd.change_summary, vd.change_details, vd.impact_analysis,
                           vd.created_at,
                           d1.title AS old_title, d2.title AS new_title
                    FROM version_diffs vd
                    JOIN documents d1 ON d1.doc_id = vd.old_version_id
                    JOIN documents d2 ON d2.doc_id = vd.new_version_id
                    WHERE vd.old_version_id = :old_id
                      AND vd.new_version_id = :new_id
                """),
                {"old_id": old_doc_id, "new_id": new_doc_id},
            )
            row = result.fetchone()

        if not row:
            return None

        text_diff = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
        structural = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
        details = row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]")

        return VersionDiffResponse(
            diff_id=str(row[0]),
            old_version_id=old_doc_id,
            new_version_id=new_doc_id,
            old_title=row[7],
            new_title=row[8],
            text_diff_data=text_diff,
            structural_changes=structural,
            change_summary=row[3] or "",
            change_details=details,
            impact_analysis=row[5] or "",
            created_at=row[6],
        )

    async def _store_diff(
        self,
        diff_id: str,
        old_doc_id: str,
        new_doc_id: str,
        text_diff: dict,
        structural_diff: dict,
        semantic_diff: dict,
    ):
        """将 diff 结果存入 version_diffs 表"""
        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO version_diffs (
                        diff_id, old_version_id, new_version_id,
                        text_diff_data, structural_changes,
                        change_summary, change_details, impact_analysis
                    ) VALUES (
                        :diff_id, :old_id, :new_id,
                        :text_diff, :structural,
                        :summary, :details, :impact
                    )
                """),
                {
                    "diff_id": diff_id,
                    "old_id": old_doc_id,
                    "new_id": new_doc_id,
                    "text_diff": json.dumps(text_diff, ensure_ascii=False),
                    "structural": json.dumps(structural_diff, ensure_ascii=False),
                    "summary": semantic_diff.get("change_summary", ""),
                    "details": json.dumps(
                        semantic_diff.get("change_details", []), ensure_ascii=False
                    ),
                    "impact": semantic_diff.get("impact_analysis", ""),
                },
            )


# 全局单例
diff_engine = DiffEngine()
