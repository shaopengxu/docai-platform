"""
DocAI Platform - 语义分块模块
按章节/段落边界自然切分，支持层次化分块策略
"""

from __future__ import annotations

import re
import uuid

import structlog
import tiktoken

from app.core.models import Chunk, ChunkType, ParsedDocument, Section, TableData
from config.settings import settings

logger = structlog.get_logger()

# tiktoken 编码器（用于准确计算 token 数）
_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    """计算文本的 token 数"""
    if not text:
        return 0
    return len(_get_encoder().encode(text))


def semantic_chunk(
    parsed_doc: ParsedDocument,
    doc_id: str,
    target_size: int | None = None,
    max_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """
    对解析后的文档进行语义分块

    策略：
    1. 按章节自然切分
    2. 小于 target_size 的章节整段作为一个 chunk
    3. 大于 target_size 的章节按段落边界进一步切分
    4. 表格单独成 chunk
    5. 相邻 chunk 之间有 overlap

    Args:
        parsed_doc: 解析后的文档
        doc_id: 文档 ID
        target_size: 目标 chunk 大小 (tokens)，默认从 settings 读取
        max_size: 最大 chunk 大小 (tokens)
        overlap: 相邻 chunk 重叠 (tokens)

    Returns:
        分块列表
    """
    target_size = target_size or settings.chunk_target_size
    max_size = max_size or settings.chunk_max_size
    overlap = overlap or settings.chunk_overlap

    chunks: list[Chunk] = []
    chunk_index = 0

    # 1. 处理章节
    for section in parsed_doc.sections:
        section_path = section.get_section_path("")
        section_chunks = _chunk_section(
            section=section,
            doc_id=doc_id,
            doc_title=parsed_doc.title,
            section_path=section_path,
            target_size=target_size,
            max_size=max_size,
            overlap=overlap,
            start_index=chunk_index,
        )
        chunks.extend(section_chunks)
        chunk_index += len(section_chunks)

    # 2. 处理表格（独立 chunk）
    for table in parsed_doc.tables:
        table_chunk = _make_table_chunk(
            table=table,
            doc_id=doc_id,
            doc_title=parsed_doc.title,
            chunk_index=chunk_index,
        )
        chunks.append(table_chunk)
        chunk_index += 1

    # 3. Fallback：如果没有章节也没有表格，用 raw_text
    if not chunks and parsed_doc.raw_text:
        fallback_chunks = _chunk_raw_text(
            text=parsed_doc.raw_text,
            doc_id=doc_id,
            doc_title=parsed_doc.title,
            target_size=target_size,
            max_size=max_size,
            overlap=overlap,
        )
        chunks.extend(fallback_chunks)

    logger.info(
        "Document chunked",
        doc_id=doc_id,
        doc_title=parsed_doc.title,
        chunk_count=len(chunks),
        total_tokens=sum(c.token_count for c in chunks),
    )

    return chunks


def _chunk_section(
    section: Section,
    doc_id: str,
    doc_title: str,
    section_path: str,
    target_size: int,
    max_size: int,
    overlap: int,
    start_index: int,
) -> list[Chunk]:
    """对单个章节进行分块"""
    full_text = section.full_content
    token_count = count_tokens(full_text)

    # 小于目标大小，整段作为一个 chunk
    if token_count <= max_size and token_count > 0:
        return [Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc_id,
            doc_title=doc_title,
            section_path=section_path,
            page_numbers=section.page_numbers,
            chunk_index=start_index,
            chunk_type=ChunkType.TEXT,
            content=full_text,
            token_count=token_count,
        )]

    # 大于目标大小，按段落切分
    if not full_text.strip():
        return []

    paragraphs = _split_into_paragraphs(full_text)
    return _merge_paragraphs_into_chunks(
        paragraphs=paragraphs,
        doc_id=doc_id,
        doc_title=doc_title,
        section_path=section_path,
        page_numbers=section.page_numbers,
        target_size=target_size,
        max_size=max_size,
        overlap=overlap,
        start_index=start_index,
    )


def _split_into_paragraphs(text: str) -> list[str]:
    """将文本按段落边界切分"""
    # 按双换行、单换行后跟缩进等模式切分
    # 先按双换行分
    raw_parts = re.split(r"\n\s*\n", text)
    paragraphs = []

    for part in raw_parts:
        part = part.strip()
        if not part:
            continue

        # 如果单段仍然太长（>max_size tokens），按单换行进一步切分
        if count_tokens(part) > settings.chunk_max_size:
            lines = part.split("\n")
            current_group: list[str] = []
            for line in lines:
                current_group.append(line)
                if count_tokens("\n".join(current_group)) > settings.chunk_target_size:
                    # 如果加上这行超标了，把之前的归为一段
                    if len(current_group) > 1:
                        paragraphs.append("\n".join(current_group[:-1]))
                        current_group = [line]
                    else:
                        # 单行就超标了，硬切
                        paragraphs.append(line)
                        current_group = []
            if current_group:
                paragraphs.append("\n".join(current_group))
        else:
            paragraphs.append(part)

    return paragraphs


def _merge_paragraphs_into_chunks(
    paragraphs: list[str],
    doc_id: str,
    doc_title: str,
    section_path: str,
    page_numbers: list[int],
    target_size: int,
    max_size: int,
    overlap: int,
    start_index: int,
) -> list[Chunk]:
    """将段落合并为适当大小的 chunk，带 overlap"""
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_idx = start_index

    # 用于 overlap 的前一个 chunk 的最后部分
    overlap_text = ""

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # 如果加上这段会超过 max_size
        if current_tokens + para_tokens > max_size and current_parts:
            # 保存当前 chunk
            content = "\n\n".join(current_parts)
            if overlap_text and chunk_idx > start_index:
                content = overlap_text + "\n\n" + content

            chunk_tokens = count_tokens(content)
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                doc_id=doc_id,
                doc_title=doc_title,
                section_path=section_path,
                page_numbers=page_numbers,
                chunk_index=chunk_idx,
                chunk_type=ChunkType.TEXT,
                content=content,
                token_count=chunk_tokens,
            ))
            chunk_idx += 1

            # 计算 overlap：取当前 chunk 的最后一段作为下一 chunk 的前缀
            overlap_text = _extract_overlap(current_parts, overlap)

            current_parts = [para]
            current_tokens = para_tokens
        else:
            current_parts.append(para)
            current_tokens += para_tokens

    # 保存最后一个 chunk
    if current_parts:
        content = "\n\n".join(current_parts)
        if overlap_text and chunk_idx > start_index:
            content = overlap_text + "\n\n" + content

        chunk_tokens = count_tokens(content)
        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc_id,
            doc_title=doc_title,
            section_path=section_path,
            page_numbers=page_numbers,
            chunk_index=chunk_idx,
            chunk_type=ChunkType.TEXT,
            content=content,
            token_count=chunk_tokens,
        ))

    return chunks


def _extract_overlap(parts: list[str], target_overlap_tokens: int) -> str:
    """从段落列表的末尾提取 overlap 文本"""
    if not parts or target_overlap_tokens <= 0:
        return ""

    # 从最后一段开始向前取，直到达到 overlap token 数
    overlap_parts: list[str] = []
    total_tokens = 0

    for part in reversed(parts):
        part_tokens = count_tokens(part)
        if total_tokens + part_tokens > target_overlap_tokens and overlap_parts:
            break
        overlap_parts.insert(0, part)
        total_tokens += part_tokens

    # 如果整段都太长，取最后 N 个字符
    result = "\n\n".join(overlap_parts)
    if count_tokens(result) > target_overlap_tokens * 2:
        # 截取末尾部分
        encoder = _get_encoder()
        tokens = encoder.encode(result)
        result = encoder.decode(tokens[-target_overlap_tokens:])

    return result


def _make_table_chunk(
    table: TableData,
    doc_id: str,
    doc_title: str,
    chunk_index: int,
) -> Chunk:
    """为表格创建独立的 chunk"""
    content = table.content
    if table.caption:
        content = f"[表格: {table.caption}]\n{content}"

    return Chunk(
        chunk_id=str(uuid.uuid4()),
        doc_id=doc_id,
        doc_title=doc_title,
        section_path=table.section_path,
        page_numbers=[table.page_number] if table.page_number else [],
        chunk_index=chunk_index,
        chunk_type=ChunkType.TABLE,
        content=content,
        token_count=count_tokens(content),
    )


def _chunk_raw_text(
    text: str,
    doc_id: str,
    doc_title: str,
    target_size: int,
    max_size: int,
    overlap: int,
) -> list[Chunk]:
    """Fallback：对无结构化信息的原始文本进行分块"""
    paragraphs = _split_into_paragraphs(text)

    return _merge_paragraphs_into_chunks(
        paragraphs=paragraphs,
        doc_id=doc_id,
        doc_title=doc_title,
        section_path="",
        page_numbers=[],
        target_size=target_size,
        max_size=max_size,
        overlap=overlap,
        start_index=0,
    )
