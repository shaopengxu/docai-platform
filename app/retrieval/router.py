"""
DocAI Platform - Query Router
分析用户问题，决定检索和生成策略
"""

import structlog
from pydantic import BaseModel

from app.core.llm_client import llm_light
from config.settings import settings

logger = structlog.get_logger()

class QueryPlan(BaseModel):
    query_type: str            # factual, summary, comparison, version_diff
    search_queries: list[str]  # 可能包含原始 query 和 LLM 改写后的 query
    metadata_filters: dict     # { "doc_type": "...", "group_id": "...", "doc_id": "..." }
    needs_multi_doc: bool
    estimated_scope: str       # narrow, medium, broad

async def route_query(question: str, user_filters: dict | None = None) -> QueryPlan:
    """分析用户问题，决定检索策略"""
    user_filters = user_filters or {}
    
    prompt = f"""分析以下用户问题，针对一个企业文档库系统，返回 JSON：
问题：{question}

提供的已知用户限制条件（如果有的话）：
{user_filters}

返回格式：
{{
  "query_type": "factual|summary|comparison|version_diff",
  "search_queries": ["改写后的优化的检索词或原始问题", "可选的其他检索角度"],
  "metadata_filters": {{
    "doc_type": "如果问题明确提到需要找某种类型的手册或合同",
    "group_id": null
  }},
  "needs_multi_doc": true/false,
  "estimated_scope": "narrow|medium|broad"
}}

注意：如果用户问题询问"这份文档"、"这个"而没有指明特定文档且之前上下文中没有，这通常是一个 narrow 的 factual 问题。
如果询问"所有政策"或"比较 A 和 B"，则是 multi_doc = true。
请确保 JSON 格式合法。
"""
    try:
        res = await llm_light.generate_json(prompt)
        
        # Merge user_filters into metadata_filters
        metadata_filters = res.get("metadata_filters", {})
        metadata_filters.update(user_filters)
        
        # Cleanup None values from metadata_filters
        metadata_filters = {k: v for k, v in metadata_filters.items() if v is not None}
        
        plan = QueryPlan(
            query_type=res.get("query_type", "factual"),
            search_queries=res.get("search_queries", [question]) or [question],
            metadata_filters=metadata_filters,
            needs_multi_doc=bool(res.get("needs_multi_doc", False)),
            estimated_scope=res.get("estimated_scope", "narrow")
        )
        logger.info("Query routed", plan=plan.model_dump())
        return plan
    except Exception as e:
        logger.error("Failed to route query", error=str(e))
        return QueryPlan(
            query_type="factual",
            search_queries=[question],
            metadata_filters=user_filters,
            needs_multi_doc=False,
            estimated_scope="narrow"
        )
