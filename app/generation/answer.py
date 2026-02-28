"""
DocAI Platform - 答案生成模块
基于检索到的 chunks 构建 prompt，调用 LLM 生成带引用的答案
"""

from __future__ import annotations

import json

import structlog

from app.core.llm_client import llm
from app.core.models import Citation, QueryResponse, RetrievedChunk
from app.ingestion.chunker import count_tokens
from config.settings import settings

logger = structlog.get_logger()

# System prompt for RAG answer generation
SYSTEM_PROMPT = """你是一个专业的企业文档分析助手。你的任务是根据提供的文档片段，准确回答用户的问题。

## 核心规则

1. **严格基于提供的文档内容回答**，不要添加文档中没有的信息。
2. **每个论述点必须标注引用来源**，格式为 [来源: 文档名, 章节, 页码]。
3. 如果提供的文档片段中没有足够信息来回答问题，明确告知用户"根据已有文档信息，无法完整回答此问题"，并说明已有信息覆盖了哪些方面。
4. 如果多个文档片段有矛盾信息，指出矛盾并分别引用。
5. 回答要**简洁、结构化**，使用标题、列表、表格等格式组织信息。
6. 使用中文回答。

## 引用格式

在每个关键论述后标注来源，例如：
- 付款周期为 30 天 [来源: XX合同, 第四章付款条款, 第12页]
- 违约金上限为合同总金额的 10% [来源: XX合同, 第六章违约责任, 第18页]
"""

ANSWER_PROMPT_TEMPLATE = """## 参考文档片段

{context}

## 用户问题

{question}

## 要求

请根据以上文档片段回答用户问题。每个关键论述必须标注引用来源 [来源: 文档名, 章节, 页码]。
如果文档片段信息不足以回答，请明确说明。"""


CITATION_EXTRACTION_PROMPT = """请分析以下回答中引用了哪些文档来源，以 JSON 数组格式返回。

回答内容：
{answer}

可用的来源信息：
{sources}

返回格式（JSON 数组）：
[
  {{
    "doc_id": "文档ID",
    "doc_title": "文档标题",
    "section_path": "章节路径",
    "page_numbers": [页码],
    "chunk_id": "chunk ID",
    "content_snippet": "被引用的原文关键片段（50字以内）"
  }}
]

只返回在回答中实际被引用的来源。"""


CONFIDENCE_PROMPT = """请评估以下问答的置信度（0-1之间的浮点数）。

评估标准：
- 1.0: 答案完全基于文档内容，信息充分，引用准确
- 0.7-0.9: 答案主要基于文档，但部分细节可能需要更多上下文
- 0.4-0.6: 文档中有部分相关信息，但不足以完整回答
- 0.0-0.3: 文档中几乎没有相关信息

问题：{question}
答案：{answer}
检索到的相关文档片段数量：{chunk_count}
最高相关性分数：{top_score}

只返回一个 0-1 之间的浮点数，不要其他文字。"""


async def generate_answer(
    question: str,
    retrieved_chunks: list[RetrievedChunk],
) -> QueryResponse:
    """
    基于检索结果生成带引用的答案

    Args:
        question: 用户问题
        retrieved_chunks: 检索并排序后的文档片段

    Returns:
        QueryResponse: 包含答案、引用和置信度的响应
    """
    if not retrieved_chunks:
        return QueryResponse(
            answer="抱歉，未在文档库中找到与您问题相关的信息。请尝试换一种方式提问，或确认文档是否已上传。",
            citations=[],
            confidence=0.0,
        )

    # Step 1: 构建上下文
    context = _build_context(retrieved_chunks)

    # Step 2: 生成答案
    prompt = ANSWER_PROMPT_TEMPLATE.format(
        context=context,
        question=question,
    )

    answer = await llm.generate(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        temperature=0.1,
    )

    # Step 3: 提取引用
    citations = _extract_citations_from_chunks(answer, retrieved_chunks)

    # Step 4: 评估置信度
    confidence = await _estimate_confidence(
        question=question,
        answer=answer,
        chunks=retrieved_chunks,
    )

    logger.info(
        "Answer generated",
        question=question[:100],
        answer_length=len(answer),
        citation_count=len(citations),
        confidence=confidence,
    )

    return QueryResponse(
        answer=answer,
        citations=citations,
        confidence=confidence,
    )


def _build_context(
    chunks: list[RetrievedChunk],
    max_tokens: int | None = None,
) -> str:
    """
    将检索到的 chunks 组装为 LLM 上下文

    按相关性排序，控制总 token 数不超过限制
    """
    max_tokens = max_tokens or settings.generation_max_context_tokens

    context_parts: list[str] = []
    total_tokens = 0

    for i, chunk in enumerate(chunks, 1):
        # 构建单个 chunk 的上下文块
        chunk_header = f"### 片段 {i}"
        meta_parts = []
        if chunk.doc_title:
            meta_parts.append(f"文档: {chunk.doc_title}")
        if chunk.section_path:
            meta_parts.append(f"章节: {chunk.section_path}")
        if chunk.page_numbers:
            pages = ", ".join(str(p) for p in chunk.page_numbers)
            meta_parts.append(f"页码: {pages}")
        meta_parts.append(f"类型: {chunk.chunk_type}")

        meta_line = " | ".join(meta_parts)
        chunk_text = f"{chunk_header}\n{meta_line}\n\n{chunk.content}"

        chunk_tokens = count_tokens(chunk_text)

        # 检查是否超出限制
        if total_tokens + chunk_tokens > max_tokens:
            # 如果是第一个 chunk 就超出了，截断内容
            if not context_parts:
                remaining_tokens = max_tokens - count_tokens(f"{chunk_header}\n{meta_line}\n\n")
                if remaining_tokens > 100:
                    # 粗略截断
                    truncated = chunk.content[: remaining_tokens * 3]  # 大致 3 chars/token
                    chunk_text = f"{chunk_header}\n{meta_line}\n\n{truncated}...\n[内容已截断]"
                    context_parts.append(chunk_text)
            break

        context_parts.append(chunk_text)
        total_tokens += chunk_tokens

    return "\n\n---\n\n".join(context_parts)


def _extract_citations_from_chunks(
    answer: str,
    chunks: list[RetrievedChunk],
) -> list[Citation]:
    """
    从 chunks 中提取引用信息

    策略：检查答案中是否引用了特定文档/章节/页码的内容，
    将所有被使用的 chunks 标记为引用来源。
    """
    citations: list[Citation] = []
    seen_chunk_ids: set[str] = set()

    for chunk in chunks:
        if chunk.chunk_id in seen_chunk_ids:
            continue

        # 检查答案中是否引用了该 chunk 的信息
        # 简单策略：如果 chunk 的文档标题或章节出现在答案中，则认为被引用
        is_cited = False

        if chunk.doc_title and chunk.doc_title in answer:
            is_cited = True
        elif chunk.section_path and chunk.section_path in answer:
            is_cited = True
        elif chunk.page_numbers:
            for page in chunk.page_numbers:
                if f"第{page}页" in answer or f"页{page}" in answer or f"p{page}" in answer.lower():
                    is_cited = True
                    break

        # 如果没有明确引用标记，但是排名靠前的 chunk（top 3），也作为引用来源
        if not is_cited and chunks.index(chunk) < 3:
            is_cited = True

        if is_cited:
            # 提取 content_snippet（取前 100 字）
            snippet = chunk.content[:100].replace("\n", " ")
            if len(chunk.content) > 100:
                snippet += "..."

            citations.append(Citation(
                doc_id=chunk.doc_id,
                doc_title=chunk.doc_title,
                section_path=chunk.section_path,
                page_numbers=chunk.page_numbers,
                chunk_id=chunk.chunk_id,
                content_snippet=snippet,
            ))
            seen_chunk_ids.add(chunk.chunk_id)

    return citations


async def _estimate_confidence(
    question: str,
    answer: str,
    chunks: list[RetrievedChunk],
) -> float:
    """
    评估答案的置信度

    综合考虑：检索分数、chunk 数量、答案中是否包含"无法回答"等信号
    """
    # 快速启发式评估（不调用 LLM，节省成本）
    if not chunks:
        return 0.0

    # 信号 1：最高检索分数
    top_score = max(c.score for c in chunks) if chunks else 0
    # 信号 2：答案中是否有明确的"不确定"表达
    uncertainty_phrases = [
        "无法", "没有找到", "不确定", "信息不足", "无法完整回答",
        "未找到", "缺乏", "没有相关",
    ]
    has_uncertainty = any(phrase in answer for phrase in uncertainty_phrases)
    # 信号 3：引用了多少个 chunk
    chunk_coverage = min(len(chunks) / 3.0, 1.0)  # 3 个 chunk 以上给满分

    # 综合计算
    if has_uncertainty:
        confidence = 0.3 * chunk_coverage
    else:
        # 基础分 0.5 + 检索质量 0.3 + 覆盖度 0.2
        score_component = min(top_score / 1.0, 1.0) * 0.3 if top_score > 0 else 0.1
        confidence = 0.5 + score_component + 0.2 * chunk_coverage

    return round(min(max(confidence, 0.0), 1.0), 2)
