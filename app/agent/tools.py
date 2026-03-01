"""
DocAI Platform - Agent 工具集 (Phase 4)
将 Phase 1-3 的所有能力封装为 Agent 可调用的工具。
每个工具是一个 async 函数，接收结构化参数，返回字符串结果。
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text

from app.core.infrastructure import get_db_session, get_es_client, get_qdrant_client
from app.core.models import RetrievedChunk
from app.retrieval.hybrid_search import hybrid_search
from app.generation.answer import cross_document_summary, generate_answer
from app.versioning.diff_engine import diff_engine

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# 工具注册表 & 元数据
# ═══════════════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "search_documents",
        "description": "在文档库中检索与查询相关的内容片段。支持语义检索和关键词检索，可按文档类型、文档组或特定文档过滤。返回最相关的文档片段。",
        "parameters": {
            "query": {"type": "string", "description": "检索查询文本", "required": True},
            "doc_id": {"type": "string", "description": "限定在某个特定文档内检索"},
            "doc_type": {"type": "string", "description": "按文档类型过滤（如 contract/report/policy）"},
            "group_id": {"type": "string", "description": "按文档组过滤"},
            "top_k": {"type": "integer", "description": "返回的结果数量，默认5", "default": 5},
            "version_mode": {"type": "string", "description": "版本检索模式: latest_only / all_versions，默认 latest_only"},
        },
    },
    {
        "name": "read_document_summary",
        "description": "读取指定文档的整体摘要，或指定章节的摘要。适用于快速了解文档概要内容，无需检索全文。",
        "parameters": {
            "doc_id": {"type": "string", "description": "文档ID", "required": True},
            "section_path": {"type": "string", "description": "章节路径，为空则返回文档级摘要"},
        },
    },
    {
        "name": "read_document_detail",
        "description": "读取指定文档的特定章节或页码范围的详细原文内容。当需要查看某个文档的具体内容时使用。",
        "parameters": {
            "doc_id": {"type": "string", "description": "文档ID", "required": True},
            "section_path": {"type": "string", "description": "章节路径"},
            "page_range": {"type": "string", "description": "页码范围，如 '1-5' 或 '3'"},
        },
    },
    {
        "name": "list_documents",
        "description": "列出文档库中符合条件的文档清单。可按文档类型、标签、状态等过滤。返回文档的标题、类型、摘要等元信息。",
        "parameters": {
            "doc_type": {"type": "string", "description": "按文档类型过滤"},
            "group_id": {"type": "string", "description": "按文档组过滤"},
            "tags": {"type": "string", "description": "按标签过滤，逗号分隔"},
            "status": {"type": "string", "description": "按状态过滤: ready / pending / error"},
            "limit": {"type": "integer", "description": "返回数量上限，默认10", "default": 10},
        },
    },
    {
        "name": "compare_versions",
        "description": "对比同一文档的两个版本之间的差异。返回文本差异、结构差异和语义级变更分析。",
        "parameters": {
            "doc_id": {"type": "string", "description": "第一个版本的文档ID", "required": True},
            "other_doc_id": {"type": "string", "description": "第二个版本的文档ID", "required": True},
        },
    },
    {
        "name": "get_version_history",
        "description": "获取某份文档的完整版本历史记录。返回该文档所有已知版本的列表、版本号和时间线。",
        "parameters": {
            "doc_id": {"type": "string", "description": "文档ID", "required": True},
        },
    },
    {
        "name": "cross_document_analysis",
        "description": "对多份文档进行跨文档对比分析或总结。可以对比不同文档在某个主题上的差异，或从多份文档中提取共同点。",
        "parameters": {
            "doc_ids": {"type": "string", "description": "文档ID列表，逗号分隔", "required": True},
            "analysis_topic": {"type": "string", "description": "分析的主题或问题", "required": True},
            "analysis_type": {"type": "string", "description": "分析类型: comparison / summary / extract_common / find_differences", "default": "summary"},
        },
    },
]


def get_tools_description() -> str:
    """将工具定义格式化为 LLM 可理解的描述文本"""
    lines = []
    for tool in TOOL_DEFINITIONS:
        lines.append(f"### {tool['name']}")
        lines.append(f"描述: {tool['description']}")
        lines.append("参数:")
        for pname, pinfo in tool["parameters"].items():
            required = " (必填)" if pinfo.get("required") else ""
            default = f", 默认: {pinfo['default']}" if "default" in pinfo else ""
            lines.append(f"  - {pname} ({pinfo['type']}): {pinfo['description']}{required}{default}")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════════════════════════════════


async def tool_search_documents(params: dict[str, Any], accessible_doc_ids: list[str] | None = None) -> str:
    """调用混合检索，返回相关文档片段"""
    query = params.get("query", "")
    if not query:
        return "错误: 必须提供 query 参数。"

    metadata_filters: dict[str, Any] = {}
    if params.get("doc_id"):
        metadata_filters["doc_id"] = params["doc_id"]
    if params.get("doc_type"):
        metadata_filters["doc_type"] = params["doc_type"]
    if params.get("group_id"):
        metadata_filters["group_id"] = params["group_id"]

    top_k = int(params.get("top_k", 5))
    version_mode = params.get("version_mode", "latest_only")

    try:
        chunks = await hybrid_search(
            query=query,
            metadata_filters=metadata_filters if metadata_filters else None,
            top_k=top_k,
            version_mode=version_mode,
            accessible_doc_ids=accessible_doc_ids,
        )

        if not chunks:
            return "未找到与查询相关的文档内容。"

        results = []
        for i, chunk in enumerate(chunks):
            pages = f" (第{','.join(str(p) for p in chunk.page_numbers)}页)" if chunk.page_numbers else ""
            section = f" [{chunk.section_path}]" if chunk.section_path else ""
            results.append(
                f"[{i+1}] 《{chunk.doc_title}》{section}{pages} (相关度: {chunk.score:.2f})\n"
                f"{chunk.content[:500]}"
            )
        return "\n\n---\n\n".join(results)

    except Exception as e:
        logger.error("tool_search_documents failed", error=str(e))
        return f"检索失败: {str(e)}"


async def tool_read_document_summary(params: dict[str, Any]) -> str:
    """读取文档或章节摘要"""
    doc_id = params.get("doc_id", "")
    if not doc_id:
        return "错误: 必须提供 doc_id 参数。"

    section_path = params.get("section_path")

    try:
        async with get_db_session() as session:
            if section_path:
                # 读取章节摘要
                result = await session.execute(
                    text("""
                        SELECT summary_text, key_points
                        FROM section_summaries
                        WHERE doc_id = :doc_id AND section_path = :section_path
                    """),
                    {"doc_id": doc_id, "section_path": section_path},
                )
                row = result.fetchone()
                if not row:
                    return f"未找到文档 {doc_id} 的章节 '{section_path}' 的摘要。"
                summary = row[0] or ""
                key_points = row[1] or []
                if isinstance(key_points, str):
                    try:
                        key_points = json.loads(key_points)
                    except json.JSONDecodeError:
                        key_points = [key_points]
                points_str = "\n".join(f"  - {p}" for p in key_points) if key_points else ""
                return f"章节「{section_path}」摘要:\n{summary}\n\n关键要点:\n{points_str}"
            else:
                # 读取文档级摘要
                result = await session.execute(
                    text("""
                        SELECT title, doc_summary, key_entities
                        FROM documents
                        WHERE doc_id = :doc_id
                    """),
                    {"doc_id": doc_id},
                )
                row = result.fetchone()
                if not row:
                    return f"未找到文档 ID: {doc_id}"
                title = row[0] or ""
                summary = row[1] or "（暂无摘要）"
                entities = row[2] or {}
                if isinstance(entities, str):
                    try:
                        entities = json.loads(entities)
                    except json.JSONDecodeError:
                        entities = {}
                entities_str = ""
                if entities:
                    entities_str = "\n关键实体:\n" + "\n".join(
                        f"  - {k}: {', '.join(v) if isinstance(v, list) else str(v)}"
                        for k, v in entities.items()
                    )
                return f"《{title}》文档摘要:\n{summary}{entities_str}"

    except Exception as e:
        logger.error("tool_read_document_summary failed", error=str(e))
        return f"读取摘要失败: {str(e)}"


async def tool_read_document_detail(params: dict[str, Any]) -> str:
    """读取文档的特定章节或页码范围的原文"""
    doc_id = params.get("doc_id", "")
    if not doc_id:
        return "错误: 必须提供 doc_id 参数。"

    section_path = params.get("section_path")
    page_range = params.get("page_range")

    try:
        # 从 ES 中检索该文档的 chunks
        es = get_es_client()
        from config.settings import settings

        query_body: dict[str, Any] = {
            "bool": {
                "must": [
                    {"term": {"doc_id": doc_id}},
                ],
            }
        }

        if section_path:
            query_body["bool"]["must"].append(
                {"match_phrase": {"section_path": section_path}}
            )

        if page_range:
            # 解析页码
            pages = []
            if "-" in page_range:
                start, end = page_range.split("-", 1)
                pages = list(range(int(start.strip()), int(end.strip()) + 1))
            else:
                pages = [int(page_range.strip())]
            query_body["bool"]["must"].append(
                {"terms": {"page_numbers": pages}}
            )

        result = await es.search(
            index=settings.es_index_name,
            query=query_body,
            size=20,
            sort=[{"chunk_index": {"order": "asc"}}],
        )

        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return f"未找到文档 {doc_id} 对应内容（section: {section_path}, pages: {page_range}）。"

        content_parts = []
        for hit in hits:
            src = hit["_source"]
            prefix = ""
            if src.get("section_path"):
                prefix += f"[{src['section_path']}]"
            if src.get("page_numbers"):
                prefix += f" (第{','.join(str(p) for p in src['page_numbers'])}页)"
            content_parts.append(f"{prefix}\n{src.get('content', '')}")

        return "\n\n---\n\n".join(content_parts)

    except Exception as e:
        logger.error("tool_read_document_detail failed", error=str(e))
        return f"读取详细内容失败: {str(e)}"


async def tool_list_documents(params: dict[str, Any]) -> str:
    """列出符合条件的文档清单"""
    try:
        async with get_db_session() as session:
            conditions = ["processing_status = 'ready'"]
            bind_params: dict[str, Any] = {}

            if params.get("doc_type"):
                conditions.append("doc_type = :doc_type")
                bind_params["doc_type"] = params["doc_type"]
            if params.get("group_id"):
                conditions.append("group_id = :group_id")
                bind_params["group_id"] = params["group_id"]
            if params.get("tags"):
                tags = [t.strip() for t in params["tags"].split(",")]
                conditions.append("tags && :tags")
                bind_params["tags"] = tags
            if params.get("status"):
                conditions[0] = "processing_status = :status"
                bind_params["status"] = params["status"]

            limit = min(int(params.get("limit", 10)), 50)
            where_clause = " AND ".join(conditions)

            result = await session.execute(
                text(f"""
                    SELECT doc_id, title, doc_type, doc_summary, version_number,
                           is_latest, processing_status, created_at
                    FROM documents
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {**bind_params, "limit": limit},
            )
            rows = result.fetchall()

            if not rows:
                return "未找到符合条件的文档。"

            docs = []
            for row in rows:
                summary_preview = (row[3] or "")[:100]
                if len(row[3] or "") > 100:
                    summary_preview += "..."
                latest_tag = " [最新版]" if row[5] else ""
                docs.append(
                    f"- 《{row[1]}》 (ID: {row[0][:8]}..., 类型: {row[2] or '未分类'}, "
                    f"版本: {row[4]}{latest_tag})\n  摘要: {summary_preview}"
                )

            return f"共找到 {len(rows)} 份文档:\n\n" + "\n\n".join(docs)

    except Exception as e:
        logger.error("tool_list_documents failed", error=str(e))
        return f"列出文档失败: {str(e)}"


async def tool_compare_versions(params: dict[str, Any]) -> str:
    """对比两个版本"""
    doc_id = params.get("doc_id", "")
    other_doc_id = params.get("other_doc_id", "")
    if not doc_id or not other_doc_id:
        return "错误: 必须提供 doc_id 和 other_doc_id。"

    try:
        # 尝试从缓存中获取已计算的 diff
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT change_summary, change_details, impact_analysis,
                           structural_changes
                    FROM version_diffs
                    WHERE (old_version_id = :id1 AND new_version_id = :id2)
                       OR (old_version_id = :id2 AND new_version_id = :id1)
                """),
                {"id1": doc_id, "id2": other_doc_id},
            )
            row = result.fetchone()

        if row:
            change_summary = row[0] or ""
            change_details = row[1] or []
            impact = row[2] or ""
            structural = row[3] or {}

            if isinstance(change_details, str):
                try:
                    change_details = json.loads(change_details)
                except json.JSONDecodeError:
                    change_details = []
            if isinstance(structural, str):
                try:
                    structural = json.loads(structural)
                except json.JSONDecodeError:
                    structural = {}

            details_str = ""
            if change_details:
                for d in change_details[:10]:
                    details_str += (
                        f"\n- [{d.get('category', '未分类')}] {d.get('description', '')}"
                        f" (位置: {d.get('location', '')})"
                    )

            return (
                f"版本对比结果:\n\n"
                f"变更概述: {change_summary}\n\n"
                f"具体变更:{details_str}\n\n"
                f"影响分析: {impact}"
            )
        else:
            # 触发实时计算
            diff_result = await diff_engine.compute_diff(doc_id, other_doc_id)
            return f"版本对比结果:\n\n{json.dumps(diff_result, ensure_ascii=False, indent=2)[:2000]}"

    except Exception as e:
        logger.error("tool_compare_versions failed", error=str(e))
        return f"版本对比失败: {str(e)}"


async def tool_get_version_history(params: dict[str, Any]) -> str:
    """获取版本历史"""
    doc_id = params.get("doc_id", "")
    if not doc_id:
        return "错误: 必须提供 doc_id。"

    try:
        async with get_db_session() as session:
            # 收集所有相关版本（父辈 + 子辈）
            versions = []
            visited: set[str] = set()

            async def collect(current_id: str):
                if current_id in visited:
                    return
                visited.add(current_id)
                result = await session.execute(
                    text("""
                        SELECT doc_id, title, version_number, version_status,
                               is_latest, parent_version_id, created_at
                        FROM documents WHERE doc_id = :doc_id
                    """),
                    {"doc_id": current_id},
                )
                row = result.fetchone()
                if not row:
                    return
                versions.append({
                    "doc_id": row[0],
                    "title": row[1],
                    "version": row[2],
                    "status": row[3],
                    "is_latest": row[4],
                    "created_at": str(row[6]) if row[6] else "",
                })
                # 向上追溯
                if row[5]:
                    await collect(row[5])
                # 向下查找子版本
                children = await session.execute(
                    text("""
                        SELECT doc_id FROM documents
                        WHERE parent_version_id = :doc_id
                    """),
                    {"doc_id": current_id},
                )
                for child in children.fetchall():
                    await collect(child[0])

            await collect(doc_id)

            if not versions:
                return f"未找到文档 {doc_id} 的版本信息。"

            lines = [f"版本历史（共 {len(versions)} 个版本）:"]
            for v in sorted(versions, key=lambda x: x["version"]):
                latest = " ★(当前版)" if v["is_latest"] else ""
                lines.append(
                    f"  - {v['version']} [{v['status']}]{latest}: "
                    f"《{v['title']}》 (创建于 {v['created_at'][:10] if v['created_at'] else '未知'})"
                )
            return "\n".join(lines)

    except Exception as e:
        logger.error("tool_get_version_history failed", error=str(e))
        return f"获取版本历史失败: {str(e)}"


async def tool_cross_document_analysis(params: dict[str, Any]) -> str:
    """跨文档对比分析"""
    doc_ids_str = params.get("doc_ids", "")
    topic = params.get("analysis_topic", "")
    if not doc_ids_str or not topic:
        return "错误: 必须提供 doc_ids 和 analysis_topic。"

    doc_ids = [d.strip() for d in doc_ids_str.split(",") if d.strip()]
    if len(doc_ids) < 2:
        return "错误: 至少需要 2 个文档 ID 进行跨文档分析。"

    try:
        # 对每个文档做检索
        all_chunks: list[RetrievedChunk] = []
        for did in doc_ids:
            chunks = await hybrid_search(
                query=topic,
                metadata_filters={"doc_id": did},
                top_k=5,
            )
            all_chunks.extend(chunks)

        if not all_chunks:
            return "在指定的文档中未找到与主题相关的内容。"

        # 使用 cross_document_summary 生成分析
        response = await cross_document_summary(topic, all_chunks)
        return f"跨文档分析结果:\n\n{response.answer}"

    except Exception as e:
        logger.error("tool_cross_document_analysis failed", error=str(e))
        return f"跨文档分析失败: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════
# 工具分发器
# ═══════════════════════════════════════════════════════════════════════════

TOOL_REGISTRY: dict[str, Any] = {
    "search_documents": tool_search_documents,
    "read_document_summary": tool_read_document_summary,
    "read_document_detail": tool_read_document_detail,
    "list_documents": tool_list_documents,
    "compare_versions": tool_compare_versions,
    "get_version_history": tool_get_version_history,
    "cross_document_analysis": tool_cross_document_analysis,
}


async def execute_tool(
    tool_name: str,
    params: dict[str, Any],
    accessible_doc_ids: list[str] | None = None,
) -> str:
    """执行指定工具，返回结果字符串"""
    func = TOOL_REGISTRY.get(tool_name)
    if not func:
        return f"未知的工具: {tool_name}。可用工具: {', '.join(TOOL_REGISTRY.keys())}"

    logger.info("Executing agent tool", tool=tool_name, params={k: str(v)[:50] for k, v in params.items()})

    # 对 search_documents 传递权限过滤
    if tool_name == "search_documents":
        result = await func(params, accessible_doc_ids=accessible_doc_ids)
    else:
        result = await func(params)

    # 截断过长的返回
    if len(result) > 3000:
        result = result[:3000] + "\n\n... (结果已截断，共 {} 字符)".format(len(result))
    return result
