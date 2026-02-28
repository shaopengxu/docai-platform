"""
DocAI Platform - 答案生成模块单元测试
测试上下文构建、引用提取、置信度评估（不调用真实 LLM）
"""

import pytest

from app.core.models import Citation, RetrievedChunk
from app.generation.answer import (
    _build_context,
    _extract_citations_from_chunks,
    _estimate_confidence,
)


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════


def _make_chunk(
    chunk_id: str = "chunk-1",
    doc_id: str = "doc-1",
    doc_title: str = "测试合同",
    section_path: str = "第三章 付款条款",
    page_numbers: list[int] | None = None,
    content: str = "默认 chunk 内容",
    score: float = 0.85,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        doc_title=doc_title,
        section_path=section_path,
        page_numbers=page_numbers or [5, 6],
        chunk_index=0,
        chunk_type="text",
        content=content,
        score=score,
    )


# ═══════════════════════════════════════════════════════════════════════════
# _build_context 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildContext:
    def test_empty_chunks(self):
        context = _build_context([])
        assert context == ""

    def test_single_chunk(self):
        chunks = [_make_chunk(content="付款周期为 30 天。")]
        context = _build_context(chunks)
        assert "付款周期为 30 天" in context
        assert "测试合同" in context
        assert "第三章 付款条款" in context
        assert "5, 6" in context  # page numbers

    def test_multiple_chunks(self):
        chunks = [
            _make_chunk(chunk_id="c1", content="第一个片段内容。"),
            _make_chunk(chunk_id="c2", content="第二个片段内容。"),
            _make_chunk(chunk_id="c3", content="第三个片段内容。"),
        ]
        context = _build_context(chunks)
        assert "片段 1" in context
        assert "片段 2" in context
        assert "片段 3" in context
        assert "第一个片段内容" in context
        assert "第三个片段内容" in context

    def test_max_tokens_limit(self):
        """超出 token 限制时应截断"""
        # 创建很多 chunks，内容不包含 "### " 以避免计数干扰
        chunks = [
            _make_chunk(chunk_id=f"c{i}", content=f"这是第 {i} 段的详细内容描述文本。" * 50)
            for i in range(20)
        ]
        context = _build_context(chunks, max_tokens=500)
        # 统计 "### " 开头的 chunk header 数量，不应包含所有 20 个
        chunk_header_count = context.count("### ")
        assert chunk_header_count < 20

    def test_chunk_metadata_in_context(self):
        """上下文中应包含 chunk 的元数据"""
        chunks = [_make_chunk(
            doc_title="供应商A合同",
            section_path="第五章 > 5.2 违约责任",
            page_numbers=[12, 13],
            content="违约金为合同总金额的10%。",
        )]
        context = _build_context(chunks)
        assert "供应商A合同" in context
        assert "5.2 违约责任" in context
        assert "12" in context


# ═══════════════════════════════════════════════════════════════════════════
# _extract_citations_from_chunks 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractCitations:
    def test_top_chunks_always_cited(self):
        """排名前 3 的 chunk 即使答案中没有明确引用也应被标记为引用"""
        chunks = [
            _make_chunk(chunk_id="c1", doc_title="文档A"),
            _make_chunk(chunk_id="c2", doc_title="文档B"),
            _make_chunk(chunk_id="c3", doc_title="文档C"),
        ]
        answer = "一些不包含任何文档名称的回答内容。"
        citations = _extract_citations_from_chunks(answer, chunks)
        # 前 3 个 chunk 应被引用
        assert len(citations) == 3

    def test_explicit_doc_title_cited(self):
        """答案中明确提到的文档标题对应的 chunk 应被引用"""
        chunks = [
            _make_chunk(chunk_id="c1", doc_title="供应商合同"),
            _make_chunk(chunk_id="c2", doc_title="审计报告"),
            _make_chunk(chunk_id="c3", doc_title="管理制度"),
            _make_chunk(chunk_id="c4", doc_title="技术规范"),
        ]
        answer = "根据供应商合同中的规定，技术规范要求如下..."
        citations = _extract_citations_from_chunks(answer, chunks)
        cited_titles = [c.doc_title for c in citations]
        assert "供应商合同" in cited_titles
        assert "技术规范" in cited_titles

    def test_no_duplicate_citations(self):
        """引用不应重复"""
        chunks = [
            _make_chunk(chunk_id="c1", doc_title="文档A"),
            _make_chunk(chunk_id="c1", doc_title="文档A"),  # 重复 ID
        ]
        answer = "文档A 中提到..."
        citations = _extract_citations_from_chunks(answer, chunks)
        chunk_ids = [c.chunk_id for c in citations]
        assert len(chunk_ids) == len(set(chunk_ids))

    def test_citation_has_snippet(self):
        """引用应包含 content_snippet"""
        chunks = [_make_chunk(content="这是被引用的内容片段，用于显示在引用信息中。")]
        answer = "测试合同 中规定了..."
        citations = _extract_citations_from_chunks(answer, chunks)
        assert len(citations) > 0
        assert citations[0].content_snippet != ""


# ═══════════════════════════════════════════════════════════════════════════
# _estimate_confidence 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestEstimateConfidence:
    @pytest.mark.asyncio
    async def test_no_chunks_zero_confidence(self):
        """没有检索到任何 chunk 时置信度为 0"""
        confidence = await _estimate_confidence(
            question="测试问题",
            answer="没有找到相关信息",
            chunks=[],
        )
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_uncertainty_phrase_lowers_confidence(self):
        """答案中包含不确定表达应降低置信度"""
        chunks = [_make_chunk(score=0.8)]
        confidence_uncertain = await _estimate_confidence(
            question="测试问题",
            answer="无法完整回答此问题，信息不足。",
            chunks=chunks,
        )
        confidence_certain = await _estimate_confidence(
            question="测试问题",
            answer="根据文档，付款周期为30天。",
            chunks=chunks,
        )
        assert confidence_uncertain < confidence_certain

    @pytest.mark.asyncio
    async def test_confidence_between_0_and_1(self):
        """置信度应在 0-1 之间"""
        chunks = [_make_chunk(score=0.95)]
        confidence = await _estimate_confidence(
            question="测试",
            answer="回答内容。",
            chunks=chunks,
        )
        assert 0.0 <= confidence <= 1.0

    @pytest.mark.asyncio
    async def test_more_chunks_higher_confidence(self):
        """更多相关 chunk 应提高置信度"""
        few_chunks = [_make_chunk(chunk_id="c1", score=0.8)]
        many_chunks = [
            _make_chunk(chunk_id=f"c{i}", score=0.8)
            for i in range(5)
        ]
        conf_few = await _estimate_confidence("测试", "回答", few_chunks)
        conf_many = await _estimate_confidence("测试", "回答", many_chunks)
        assert conf_many >= conf_few
