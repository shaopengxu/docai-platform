"""
DocAI Platform - 分块模块单元测试
"""

import uuid

import pytest

from app.core.models import ChunkType, ParsedDocument, Section, TableData
from app.ingestion.chunker import (
    count_tokens,
    semantic_chunk,
    _split_into_paragraphs,
    _extract_overlap,
)


# ═══════════════════════════════════════════════════════════════════════════
# count_tokens 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_english_text(self):
        tokens = count_tokens("Hello, world!")
        assert tokens > 0
        assert tokens < 10

    def test_chinese_text(self):
        tokens = count_tokens("这是一个中文测试文档，用于验证分块模块的功能。")
        assert tokens > 0

    def test_long_text(self):
        text = "这是一段重复的测试文本。" * 100
        tokens = count_tokens(text)
        assert tokens > 100


# ═══════════════════════════════════════════════════════════════════════════
# _split_into_paragraphs 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestSplitIntoParagraphs:
    def test_double_newline_split(self):
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        paragraphs = _split_into_paragraphs(text)
        assert len(paragraphs) == 3
        assert paragraphs[0] == "第一段内容。"
        assert paragraphs[1] == "第二段内容。"
        assert paragraphs[2] == "第三段内容。"

    def test_empty_text(self):
        paragraphs = _split_into_paragraphs("")
        assert len(paragraphs) == 0

    def test_single_paragraph(self):
        text = "这是一段没有分隔的文本内容。"
        paragraphs = _split_into_paragraphs(text)
        assert len(paragraphs) == 1

    def test_whitespace_between_paragraphs(self):
        text = "段落一。\n\n   \n\n段落二。"
        paragraphs = _split_into_paragraphs(text)
        assert len(paragraphs) == 2


# ═══════════════════════════════════════════════════════════════════════════
# semantic_chunk 核心测试
# ═══════════════════════════════════════════════════════════════════════════


class TestSemanticChunk:
    def _make_doc(
        self,
        sections: list[Section] | None = None,
        tables: list[TableData] | None = None,
        raw_text: str = "",
    ) -> ParsedDocument:
        return ParsedDocument(
            title="测试文档",
            filename="test.pdf",
            page_count=10,
            sections=sections or [],
            tables=tables or [],
            raw_text=raw_text,
        )

    def test_empty_document(self):
        doc = self._make_doc()
        chunks = semantic_chunk(doc, doc_id="test-id")
        assert len(chunks) == 0

    def test_single_small_section(self):
        """小于 max_size 的章节应整段成为一个 chunk"""
        doc = self._make_doc(sections=[
            Section(
                title="第一章 概述",
                level=1,
                content="这是第一章的内容，比较短。",
                page_numbers=[1],
            )
        ])
        chunks = semantic_chunk(doc, doc_id="test-id")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == ChunkType.TEXT
        assert chunks[0].section_path == "第一章 概述"
        assert chunks[0].page_numbers == [1]
        assert chunks[0].doc_title == "测试文档"
        assert chunks[0].token_count > 0

    def test_multiple_sections(self):
        """多个章节各自成为独立 chunk"""
        doc = self._make_doc(sections=[
            Section(title="第一章", level=1, content="第一章内容。", page_numbers=[1]),
            Section(title="第二章", level=1, content="第二章内容。", page_numbers=[2]),
            Section(title="第三章", level=1, content="第三章内容。", page_numbers=[3]),
        ])
        chunks = semantic_chunk(doc, doc_id="test-id")
        assert len(chunks) == 3
        # 验证 chunk_index 递增
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_large_section_splits(self):
        """大于 max_size 的章节应被切分"""
        # 构造一个很长的章节
        long_content = "\n\n".join([f"这是第 {i} 个段落的内容，包含一些详细的描述信息。" * 10 for i in range(20)])
        doc = self._make_doc(sections=[
            Section(title="长章节", level=1, content=long_content, page_numbers=[1, 2, 3]),
        ])
        chunks = semantic_chunk(doc, doc_id="test-id", target_size=100, max_size=200)
        assert len(chunks) > 1

    def test_table_becomes_separate_chunk(self):
        """表格应成为独立的 TABLE 类型 chunk"""
        doc = self._make_doc(
            sections=[
                Section(title="正文", level=1, content="一些正文内容。", page_numbers=[1]),
            ],
            tables=[
                TableData(
                    content="| 列1 | 列2 |\n|---|---|\n| A | B |",
                    page_number=2,
                    section_path="附录",
                    caption="表1：示例表格",
                ),
            ],
        )
        chunks = semantic_chunk(doc, doc_id="test-id")
        assert len(chunks) == 2

        text_chunks = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE]
        assert len(text_chunks) == 1
        assert len(table_chunks) == 1
        assert "表1：示例表格" in table_chunks[0].content

    def test_fallback_to_raw_text(self):
        """没有章节和表格时应用 raw_text fallback"""
        doc = self._make_doc(raw_text="这是原始文本内容。\n\n这是第二段。")
        chunks = semantic_chunk(doc, doc_id="test-id")
        assert len(chunks) > 0

    def test_chunk_metadata_populated(self):
        """验证 chunk 的元数据字段都被正确填充"""
        doc = self._make_doc(sections=[
            Section(title="测试章节", level=2, content="章节内容。", page_numbers=[5, 6]),
        ])
        chunks = semantic_chunk(doc, doc_id="my-doc-123")
        assert len(chunks) == 1

        chunk = chunks[0]
        assert chunk.doc_id == "my-doc-123"
        assert chunk.doc_title == "测试文档"
        assert chunk.chunk_id  # UUID should be generated
        assert chunk.created_at is not None

    def test_chunk_ids_are_unique(self):
        """所有 chunk 的 ID 应唯一"""
        doc = self._make_doc(sections=[
            Section(title=f"章节{i}", level=1, content=f"内容{i}。", page_numbers=[i])
            for i in range(10)
        ])
        chunks = semantic_chunk(doc, doc_id="test-id")
        chunk_ids = [c.chunk_id for c in chunks]
        assert len(chunk_ids) == len(set(chunk_ids))


# ═══════════════════════════════════════════════════════════════════════════
# _extract_overlap 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractOverlap:
    def test_empty_parts(self):
        result = _extract_overlap([], 50)
        assert result == ""

    def test_zero_overlap(self):
        result = _extract_overlap(["一些内容"], 0)
        assert result == ""

    def test_basic_overlap(self):
        parts = ["第一段内容。", "第二段内容。", "第三段需要被重叠的内容。"]
        result = _extract_overlap(parts, 50)
        assert len(result) > 0
