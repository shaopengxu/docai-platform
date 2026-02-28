"""
DocAI Platform - 问答 API
基于 RAG 的文档问答接口
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.llm_client import llm
from app.core.models import QueryRequest, QueryResponse
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
# 问答接口
# ═══════════════════════════════════════════════════════════════════════════


@router.post("", response_model=QueryResponse)
async def ask_question(request: QueryRequest):
    """
    文档问答

    - **question**: 用户问题
    - **doc_id**: 可选，限定在某个文档内检索
    - **top_k**: 最终使用的文档片段数量（默认 5）

    返回包含答案、引用来源和置信度的响应。
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
        # 准备用户过滤器
        user_filters = {}
        if request.doc_id:
            user_filters["doc_id"] = request.doc_id
        if request.group_id:
            user_filters["group_id"] = request.group_id
        if request.doc_type:
            user_filters["doc_type"] = request.doc_type

        # Step 0: 路由分析
        plan = await route_query(request.question, user_filters)

        # Step 1: 混合检索
        all_chunks = []
        for q in plan.search_queries[:2]: # 最多用 LLM 优化的前2个检索词
            chunks = await hybrid_search(
                query=q,
                metadata_filters=plan.metadata_filters,
                top_k=request.top_k,
            )
            all_chunks.extend(chunks)

        # 去重
        seen = set()
        retrieved_chunks = []
        for c in all_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                retrieved_chunks.append(c)

        # 再次按分数排序并截断
        retrieved_chunks.sort(key=lambda x: x.score, reverse=True)
        retrieved_chunks = retrieved_chunks[:request.top_k]

        logger.info(
            "Retrieval completed",
            chunk_count=len(retrieved_chunks),
            top_score=retrieved_chunks[0].score if retrieved_chunks else 0,
        )

        # Step 2: 生成答案
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
            question=request.question[:100],
            latency_ms=latency_ms,
            citation_count=len(response.citations),
            confidence=response.confidence,
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
# 流式问答接口
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/stream")
async def ask_question_stream(request: QueryRequest):
    """
    流式文档问答（SSE）

    与 /query 相同的检索逻辑，但答案以 Server-Sent Events 流式返回。
    适用于前端实时展示生成过程。
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    logger.info(
        "Stream query received",
        question=request.question[:100],
        doc_id=request.doc_id,
    )

    # 准备用户过滤器
    user_filters = {}
    if request.doc_id:
        user_filters["doc_id"] = request.doc_id
    if request.group_id:
        user_filters["group_id"] = request.group_id
    if request.doc_type:
        user_filters["doc_type"] = request.doc_type

    # Step 0: 路由分析
    plan = await route_query(request.question, user_filters)

    # 检索
    all_chunks = []
    for q in plan.search_queries[:2]:
        chunks = await hybrid_search(
            query=q,
            metadata_filters=plan.metadata_filters,
            top_k=request.top_k,
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
            import json
            token_data = json.dumps({"type": "token", "content": "抱歉，未在文档库中找到与您问题相关的信息。"}, ensure_ascii=False)
            yield f"data: {token_data}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            empty_stream(),
            media_type="text/event-stream",
        )

    async def generate_stream():
        """SSE 流式生成"""
        import json
        try:
            # 决定使用哪种流
            if plan.needs_multi_doc and len(set(c.doc_id for c in retrieved_chunks)) > 1:
                logger.info("Using Map-Reduce stream")
                # cross_document_summary_stream 返回 (citations, generator)
                citations_list, stream_gen = await cross_document_summary_stream(request.question, retrieved_chunks)
            else:
                logger.info("Using Standard RAG stream")
                citations_list = retrieved_chunks[:5]
                context = _build_context(retrieved_chunks)
                prompt = ANSWER_PROMPT_TEMPLATE.format(context=context, question=request.question)
                stream_gen = llm.generate_stream(prompt=prompt, system_prompt=SYSTEM_PROMPT, temperature=0.1)

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
                    "content_snippet": snippet
                })
            
            sources_data = json.dumps({"type": "sources", "citations": citations}, ensure_ascii=False)
            yield f"data: {sources_data}\n\n"

            async for token in stream_gen:
                if isinstance(token, str):
                    token_data = json.dumps({"type": "token", "content": token}, ensure_ascii=False)
                    yield f"data: {token_data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error("Stream generation failed", error=str(e))
            error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
