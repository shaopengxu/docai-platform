"""
DocAI Platform - Smart Query Router (Phase 4 升级)
智能路由：简单问题走快速通道，复杂问题走 Agent
"""

import structlog
from pydantic import BaseModel

from app.core.llm_client import llm_light
from config.settings import settings

logger = structlog.get_logger()


class QueryPlan(BaseModel):
    query_type: str            # factual, summary, comparison, version_diff, complex_analysis
    search_queries: list[str]  # 可能包含原始 query 和 LLM 改写后的 query
    metadata_filters: dict     # { "doc_type": "...", "group_id": "...", "doc_id": "..." }
    needs_multi_doc: bool
    estimated_scope: str       # narrow, medium, broad
    # Phase 4 新增
    route: str = "simple_rag"  # simple_rag / enhanced_rag / agent


async def route_query(question: str, user_filters: dict | None = None) -> QueryPlan:
    """分析用户问题，决定检索策略和执行路由"""
    user_filters = user_filters or {}

    prompt = f"""分析以下用户问题，针对一个企业文档库系统，返回 JSON：
问题：{question}

提供的已知用户限制条件（如果有的话）：
{user_filters}

返回格式：
{{
  "query_type": "factual|summary|comparison|version_diff|complex_analysis",
  "search_queries": ["改写后的优化的检索词或原始问题", "可选的其他检索角度"],
  "metadata_filters": {{
    "doc_type": "如果问题明确提到需要找某种类型的手册或合同",
    "group_id": null
  }},
  "needs_multi_doc": true/false,
  "estimated_scope": "narrow|medium|broad",
  "route": "simple_rag|enhanced_rag|agent"
}}

路由选择指南：
- **simple_rag**: 简单事实查询，只需在文档中找到一个答案。例如"XX合同的付款周期是多少？"
- **enhanced_rag**: 需要更多上下文的单文档/少文档问题，或跨文档总结。例如"总结这份报告的主要内容"
- **agent**: 复杂的多步推理任务，需要组合多种操作。例如：
  - "对比所有合同中的付款条款，找出最宽松的"（需要先列出所有合同，再分别检索，最后对比）
  - "新版本的人事政策和旧版本相比有哪些重大变化？影响哪些部门？"（需要版本对比+分析）
  - "根据多份年度报告，分析公司三年来的营收增长趋势"（需要多文档分析+推理）
  - 用户问题中包含多个子问题需要分步骤回答
  - 任何需要先查清信息、再做推理分析的复杂任务

注意：
- 如果用户问题询问"这份文档"、"这个"而没有指明特定文档，这通常是 narrow 的 factual 问题，走 simple_rag
- 如果用户问"所有政策"或"比较 A 和 B"，则 needs_multi_doc = true
- version_diff 类的问题建议走 agent 路由
- 需要跨文档总结但不需要复杂推理的，走 enhanced_rag 即可
- 请确保 JSON 格式合法。
"""
    try:
        res = await llm_light.generate_json(prompt)

        # Merge user_filters into metadata_filters
        metadata_filters = res.get("metadata_filters", {})
        metadata_filters.update(user_filters)

        # Cleanup None values from metadata_filters
        metadata_filters = {k: v for k, v in metadata_filters.items() if v is not None}

        # 确定路由
        route = res.get("route", "simple_rag")
        query_type = res.get("query_type", "factual")

        # 路由推断逻辑（如果 LLM 没直接给 route，则根据 query_type 推断）
        if route not in ("simple_rag", "enhanced_rag", "agent"):
            if query_type in ("complex_analysis", "version_diff"):
                route = "agent"
            elif query_type == "summary" and bool(res.get("needs_multi_doc")):
                route = "enhanced_rag"
            elif query_type == "comparison":
                route = "agent"
            else:
                route = "simple_rag"

        plan = QueryPlan(
            query_type=query_type,
            search_queries=res.get("search_queries", [question]) or [question],
            metadata_filters=metadata_filters,
            needs_multi_doc=bool(res.get("needs_multi_doc", False)),
            estimated_scope=res.get("estimated_scope", "narrow"),
            route=route,
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
            estimated_scope="narrow",
            route="simple_rag",
        )
