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
from app.generation.answer import generate_answer, _build_context, SYSTEM_PROMPT, ANSWER_PROMPT_TEMPLATE
from app.retrieval.hybrid_search import hybrid_search

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
        # Step 1: 混合检索
        retrieved_chunks = await hybrid_search(
            query=request.question,
            doc_id=request.doc_id,
            top_k=request.top_k,
        )

        logger.info(
            "Retrieval completed",
            chunk_count=len(retrieved_chunks),
            top_score=retrieved_chunks[0].score if retrieved_chunks else 0,
        )

        # Step 2: 生成答案
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

    # 先执行检索（非流式）
    retrieved_chunks = await hybrid_search(
        query=request.question,
        doc_id=request.doc_id,
        top_k=request.top_k,
    )

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

    # 构建 prompt
    context = _build_context(retrieved_chunks)
    prompt = ANSWER_PROMPT_TEMPLATE.format(
        context=context,
        question=request.question,
    )

    async def generate_stream():
        """SSE 流式生成"""
        import json
        try:
            # 提前发送引用来源
            citations = []
            for chunk in retrieved_chunks[:5]:
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

            async for token in llm.generate_stream(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.1,
            ):
                # SSE 格式 JSON
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
