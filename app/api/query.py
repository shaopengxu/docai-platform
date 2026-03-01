"""
DocAI Platform - 问答 API (Phase 4 + Phase 5 权限控制)
支持三种路由通道: simple_rag / enhanced_rag / agent
Agent 模式下支持流式输出推理步骤
"""

from __future__ import annotations

import json
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.agent import document_agent
from app.auth.audit import audit_log
from app.auth.dependencies import get_current_user
from app.auth.models import CurrentUser
from app.auth.permissions import get_accessible_doc_ids
from app.core.llm_client import llm
from app.core.models import AgentResponse, QueryRequest, QueryResponse
from app.generation.answer import (
    ANSWER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    _build_context,
    cross_document_summary,
    cross_document_summary_stream,
    generate_answer,
)
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.router import route_query

logger = structlog.get_logger()

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# 内部辅助：执行检索 Pipeline（simple_rag / enhanced_rag 共用）
# ═══════════════════════════════════════════════════════════════════════════

async def _run_retrieval(request: QueryRequest, plan, accessible_doc_ids: list[str] | None = None):
    """检索并去重、排序"""
    version_mode = request.version_mode
    if plan.query_type == "version_diff":
        version_mode = "all_versions"

    all_chunks = []
    for q in plan.search_queries[:2]:
        chunks = await hybrid_search(
            query=q,
            metadata_filters=plan.metadata_filters,
            top_k=request.top_k,
            version_mode=version_mode,
            accessible_doc_ids=accessible_doc_ids,
        )
        all_chunks.extend(chunks)

    # 去重
    seen = set()
    retrieved_chunks = []
    for c in all_chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            retrieved_chunks.append(c)

    # 排序并截断
    retrieved_chunks.sort(key=lambda x: x.score, reverse=True)
    retrieved_chunks = retrieved_chunks[:request.top_k]
    return retrieved_chunks


# ═══════════════════════════════════════════════════════════════════════════
# 问答接口 (POST /api/v1/query)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("", response_model=QueryResponse)
async def ask_question(
    request: QueryRequest,
    http_request: Request,
    current_user: CurrentUser | None = Depends(get_current_user),
):
    """
    文档问答（智能路由）

    - **question**: 用户问题
    - **doc_id**: 可选，限定在某个文档内检索
    - **top_k**: 最终使用的文档片段数量（默认 5）

    系统自动判断问题复杂度，选择最佳处理通道:
    - 简单事实查询 → 快速 RAG
    - 跨文档总结 → 增强 RAG (Map-Reduce)
    - 复杂分析 → Agent 多步推理
    """
    start_time = time.time()

    if not request.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    logger.info(
        "Query received",
        question=request.question[:100],
        doc_id=request.doc_id,
        top_k=request.top_k,
    )

    try:
        # Phase 5: 获取用户可访问文档列表
        accessible_doc_ids = await get_accessible_doc_ids(current_user)

        # 准备用户过滤器
        user_filters = {}
        if request.doc_id:
            user_filters["doc_id"] = request.doc_id
        if request.group_id:
            user_filters["group_id"] = request.group_id
        if request.doc_type:
            user_filters["doc_type"] = request.doc_type

        # Step 0: 智能路由
        plan = await route_query(request.question, user_filters)

        # ── Agent 路由 ──
        if plan.route == "agent":
            logger.info("Routing to Agent", query_type=plan.query_type)
            agent_response = await document_agent.run(
                request.question, accessible_doc_ids=accessible_doc_ids
            )
            # 转换为 QueryResponse 格式兼容
            return QueryResponse(
                answer=agent_response.answer,
                citations=agent_response.citations,
                confidence=agent_response.confidence,
                latency_ms=agent_response.latency_ms,
            )

        # ── simple_rag / enhanced_rag 路由 ──
        retrieved_chunks = await _run_retrieval(request, plan, accessible_doc_ids)

        # 降级策略：如果路由器推断的 metadata_filters 导致检索为空，去掉过滤重试
        if not retrieved_chunks and plan.metadata_filters:
            logger.warning(
                "Retrieval empty with inferred metadata_filters, retrying without filters",
                filters=plan.metadata_filters,
            )
            plan.metadata_filters = {}
            retrieved_chunks = await _run_retrieval(request, plan, accessible_doc_ids)

        logger.info(
            "Retrieval completed",
            route=plan.route,
            chunk_count=len(retrieved_chunks),
            top_score=retrieved_chunks[0].score if retrieved_chunks else 0,
        )

        # 生成答案
        if plan.needs_multi_doc and len(set(c.doc_id for c in retrieved_chunks)) > 1:
            logger.info("Using Map-Reduce cross-document generation")
            response = await cross_document_summary(request.question, retrieved_chunks)
        else:
            response = await generate_answer(
                question=request.question,
                retrieved_chunks=retrieved_chunks,
            )

        # 计算延迟
        latency_ms = int((time.time() - start_time) * 1000)
        response.latency_ms = latency_ms

        logger.info(
            "Query completed",
            route=plan.route,
            question=request.question[:100],
            latency_ms=latency_ms,
            citation_count=len(response.citations),
            confidence=response.confidence,
        )

        # Phase 5: 审计日志
        await audit_log(
            action="query",
            user=current_user,
            resource_type="document",
            details={
                "question": request.question[:200],
                "route": plan.route,
                "result_count": len(response.citations),
            },
            request=http_request,
        )

        return response

    except Exception as e:
        logger.error(
            "Query failed",
            question=request.question[:100],
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"问答处理失败: {str(e)}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# 流式问答接口 (POST /api/v1/query/stream)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/stream")
async def ask_question_stream(
    request: QueryRequest,
    http_request: Request,
    current_user: CurrentUser | None = Depends(get_current_user),
):
    """
    流式文档问答（SSE）

    Phase 4 升级: 支持 Agent 推理过程可视化。SSE 事件类型:
    - `agent_step`: Agent 的思考步骤和工具调用
    - `sources`: 引用来源
    - `token`: 答案 token
    - `route_info`: 路由信息
    - `error`: 错误
    - `[DONE]`: 完成
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    logger.info(
        "Stream query received",
        question=request.question[:100],
        doc_id=request.doc_id,
    )

    # Phase 5: 获取用户可访问文档列表
    accessible_doc_ids = await get_accessible_doc_ids(current_user)

    # 准备用户过滤器
    user_filters = {}
    if request.doc_id:
        user_filters["doc_id"] = request.doc_id
    if request.group_id:
        user_filters["group_id"] = request.group_id
    if request.doc_type:
        user_filters["doc_type"] = request.doc_type

    # Step 0: 智能路由
    plan = await route_query(request.question, user_filters)

    # Phase 5: 审计日志
    await audit_log(
        action="query_stream",
        user=current_user,
        details={"question": request.question[:200], "route": plan.route},
        request=http_request,
    )

    # ── Agent 流式路由 ──
    if plan.route == "agent":
        logger.info("Routing to Agent (stream)", query_type=plan.query_type)

        async def agent_stream():
            # 先发送路由信息
            route_data = json.dumps(
                {"type": "route_info", "route": "agent", "query_type": plan.query_type},
                ensure_ascii=False,
            )
            yield f"data: {route_data}\n\n"

            try:
                async for event in document_agent.run_stream(request.question):
                    event_type = event.get("type", "")
                    event_data = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_data}\n\n"

                    if event_type == "done":
                        yield "data: [DONE]\n\n"
                        return
            except Exception as e:
                logger.error("Agent stream failed", error=str(e))
                error_data = json.dumps(
                    {"type": "error", "message": str(e)}, ensure_ascii=False
                )
                yield f"data: {error_data}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            agent_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # ── simple_rag / enhanced_rag 流式路由 ──
    version_mode = request.version_mode
    if plan.query_type == "version_diff":
        version_mode = "all_versions"

    # 检索
    all_chunks = []
    for q in plan.search_queries[:2]:
        chunks = await hybrid_search(
            query=q,
            metadata_filters=plan.metadata_filters,
            top_k=request.top_k,
            version_mode=version_mode,
        )
        all_chunks.extend(chunks)

    # 去重
    seen = set()
    retrieved_chunks = []
    for c in all_chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            retrieved_chunks.append(c)

    retrieved_chunks.sort(key=lambda x: x.score, reverse=True)
    retrieved_chunks = retrieved_chunks[:request.top_k]

    if not retrieved_chunks:
        async def empty_stream():
            route_data = json.dumps(
                {"type": "route_info", "route": plan.route, "query_type": plan.query_type},
                ensure_ascii=False,
            )
            yield f"data: {route_data}\n\n"
            token_data = json.dumps(
                {"type": "token", "content": "抱歉，未在文档库中找到与您问题相关的信息。"},
                ensure_ascii=False,
            )
            yield f"data: {token_data}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            empty_stream(),
            media_type="text/event-stream",
        )

    async def generate_stream():
        """SSE 流式生成"""
        try:
            # 发送路由信息
            route_data = json.dumps(
                {"type": "route_info", "route": plan.route, "query_type": plan.query_type},
                ensure_ascii=False,
            )
            yield f"data: {route_data}\n\n"

            # 决定使用哪种流
            if plan.needs_multi_doc and len(set(c.doc_id for c in retrieved_chunks)) > 1:
                logger.info("Using Map-Reduce stream")
                citations_list, stream_gen = await cross_document_summary_stream(
                    request.question, retrieved_chunks
                )
            else:
                logger.info("Using Standard RAG stream")
                citations_list = retrieved_chunks[:5]
                context = _build_context(retrieved_chunks)
                prompt = ANSWER_PROMPT_TEMPLATE.format(
                    context=context, question=request.question
                )
                stream_gen = llm.generate_stream(
                    prompt=prompt, system_prompt=SYSTEM_PROMPT, temperature=0.1
                )

            # 提前发送引用来源
            citations = []
            for chunk in citations_list:
                snippet = chunk.content[:100].replace("\n", " ")
                if len(chunk.content) > 100:
                    snippet += "..."
                citations.append({
                    "doc_id": chunk.doc_id,
                    "doc_title": chunk.doc_title,
                    "section_path": chunk.section_path,
                    "page_numbers": chunk.page_numbers,
                    "chunk_id": chunk.chunk_id,
                    "content_snippet": snippet,
                })

            sources_data = json.dumps(
                {"type": "sources", "citations": citations}, ensure_ascii=False
            )
            yield f"data: {sources_data}\n\n"

            async for token in stream_gen:
                if isinstance(token, str):
                    token_data = json.dumps(
                        {"type": "token", "content": token}, ensure_ascii=False
                    )
                    yield f"data: {token_data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error("Stream generation failed", error=str(e))
            error_data = json.dumps(
                {"type": "error", "message": str(e)}, ensure_ascii=False
            )
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
