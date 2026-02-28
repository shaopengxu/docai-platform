"""
DocAI Platform - 检索模块单元测试
测试 RRF 融合、Reranker 逻辑（mock 外部服务）
"""

import pytest

from app.core.models import RetrievedChunk
from app.retrieval.hybrid_search import _rrf_fusion


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════


def _make_chunk(chunk_id: str, doc_id: str = "doc-1", score: float = 0.9, content: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        doc_title="测试文档",
        section_path="测试章节",
        page_numbers=[1],
        chunk_index=0,
        chunk_type="text",
        content=content or f"Chunk {chunk_id} 的内容",
        score=score,
    )


# ═══════════════════════════════════════════════════════════════════════════
# RRF 融合测试
# ═══════════════════════════════════════════════════════════════════════════


class TestRRFFusion:
    def test_empty_inputs(self):
        """两路结果都为空"""
        result = _rrf_fusion([], [])
        assert len(result) == 0

    def test_single_source_vector_only(self):
        """只有向量检索结果"""
        vector = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        result = _rrf_fusion(vector, [])
        assert len(result) == 3
        # 第一个的 RRF 分应最高
        assert result[0].chunk_id == "c1"

    def test_single_source_bm25_only(self):
        """只有 BM25 结果"""
        bm25 = [_make_chunk("c1"), _make_chunk("c2")]
        result = _rrf_fusion([], bm25)
        assert len(result) == 2

    def test_overlapping_results(self):
        """两路结果有重叠的 chunk，重叠 chunk 应排更前"""
        vector = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        bm25 = [_make_chunk("c2"), _make_chunk("c4"), _make_chunk("c1")]

        result = _rrf_fusion(vector, bm25)

        # c1 和 c2 在两路都出现，RRF 分应比只出现一次的高
        chunk_ids = [c.chunk_id for c in result]
        assert "c1" in chunk_ids
        assert "c2" in chunk_ids

        # c1 和 c2 应排在前面（它们的 RRF 总分更高）
        c1_idx = chunk_ids.index("c1")
        c2_idx = chunk_ids.index("c2")
        c3_idx = chunk_ids.index("c3")
        c4_idx = chunk_ids.index("c4")

        # 两路都出现的 chunk 的最高分应 >= 只出现一次的
        c1_score = result[c1_idx].score
        c3_score = result[c3_idx].score
        assert c1_score >= c3_score

    def test_no_overlap(self):
        """两路结果完全不重叠"""
        vector = [_make_chunk("c1"), _make_chunk("c2")]
        bm25 = [_make_chunk("c3"), _make_chunk("c4")]

        result = _rrf_fusion(vector, bm25)
        assert len(result) == 4

    def test_rrf_scores_are_positive(self):
        """所有 RRF 分数应为正数"""
        vector = [_make_chunk(f"v{i}") for i in range(5)]
        bm25 = [_make_chunk(f"b{i}") for i in range(5)]

        result = _rrf_fusion(vector, bm25)
        for chunk in result:
            assert chunk.score > 0

    def test_rrf_order_is_descending(self):
        """结果应按 RRF 分数降序排列"""
        vector = [_make_chunk(f"c{i}") for i in range(10)]
        bm25 = [_make_chunk(f"c{i}") for i in range(5, 15)]

        result = _rrf_fusion(vector, bm25)
        for i in range(len(result) - 1):
            assert result[i].score >= result[i + 1].score

    def test_custom_k_parameter(self):
        """自定义 k 参数"""
        # 每次调用使用独立的 chunk 对象，避免 score 被后续调用覆盖
        result_k10 = _rrf_fusion([_make_chunk("c1")], [_make_chunk("c1")], k=10)
        score_k10 = result_k10[0].score

        result_k100 = _rrf_fusion([_make_chunk("c1")], [_make_chunk("c1")], k=100)
        score_k100 = result_k100[0].score

        # k 越大，RRF 分数越小（但排序不变）
        assert score_k10 > score_k100
