"""
DocAI Platform - 混合检索模块
向量检索 (Qdrant) + BM25 全文检索 (Elasticsearch) + RRF 融合 + BGE Reranker
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from app.core.embedding import encode_single
from app.core.infrastructure import get_es_client, get_qdrant_client
from app.core.models import RetrievedChunk
from config.settings import settings

logger = structlog.get_logger()

# Reranker 模型（懒加载）
_reranker = None


def _get_reranker():
    """懒加载 BGE Reranker 模型"""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        logger.info(
            "Loading reranker model",
            model=settings.reranker_model_name,
            device=settings.reranker_device,
        )
        _reranker = CrossEncoder(
            settings.reranker_model_name,
            device=settings.reranker_device,
        )
        logger.info("Reranker model loaded")
    return _reranker


async def hybrid_search(
    query: str,
    doc_id: str | None = None,
    top_k: int | None = None,
    use_reranker: bool = True,
) -> list[RetrievedChunk]:
    """
    混合检索：向量 + BM25 + RRF 融合 + Reranker

    Args:
        query: 用户查询文本
        doc_id: 可选，限定在某个文档内检索
        top_k: 最终返回数量
        use_reranker: 是否使用 reranker 重排序

    Returns:
        排序后的检索结果列表
    """
    top_k = top_k or settings.retrieval_final_top_k

    # 并行执行向量检索和 BM25 检索
    vector_results = await _vector_search(query, doc_id)
    bm25_results = await _bm25_search(query, doc_id)

    logger.info(
        "Search results",
        vector_count=len(vector_results),
        bm25_count=len(bm25_results),
    )

    # RRF 融合
    fused_results = _rrf_fusion(vector_results, bm25_results)

    logger.info("RRF fusion done", fused_count=len(fused_results))

    # Reranker 重排序
    if use_reranker and fused_results:
        # 取 top 2*top_k 进行 rerank（reranker 计算成本较高）
        candidates = fused_results[: top_k * 3]
        reranked = _rerank(query, candidates)
        final_results = reranked[:top_k]
    else:
        final_results = fused_results[:top_k]

    # 上下文窗口扩展（可选）
    if settings.context_window_chunks > 0:
        final_results = await _expand_context_window(final_results)

    return final_results


# ═══════════════════════════════════════════════════════════════════════════
# 向量检索 (Qdrant)
# ═══════════════════════════════════════════════════════════════════════════


async def _vector_search(
    query: str,
    doc_id: str | None = None,
) -> list[RetrievedChunk]:
    """通过 Qdrant 进行向量语义检索"""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    qdrant = get_qdrant_client()
    query_vector = encode_single(query)

    # 构建过滤条件
    must_conditions = []
    if doc_id:
        must_conditions.append(
            FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
        )
    # 默认只检索最新版本
    must_conditions.append(
        FieldCondition(key="is_latest", match=MatchValue(value=True))
    )

    query_filter = Filter(must=must_conditions) if must_conditions else None

    results = await qdrant.search(
        collection_name=settings.qdrant_collection_name,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=settings.retrieval_top_k_vector,
        with_payload=True,
    )

    chunks = []
    for point in results:
        payload = point.payload or {}
        chunks.append(
            RetrievedChunk(
                chunk_id=str(point.id),
                doc_id=payload.get("doc_id", ""),
                doc_title=payload.get("doc_title", ""),
                section_path=payload.get("section_path", ""),
                page_numbers=payload.get("page_numbers", []),
                chunk_index=payload.get("chunk_index", 0),
                chunk_type=payload.get("chunk_type", "text"),
                content=payload.get("content", ""),
                score=point.score,
            )
        )

    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# BM25 全文检索 (Elasticsearch)
# ═══════════════════════════════════════════════════════════════════════════


async def _bm25_search(
    query: str,
    doc_id: str | None = None,
) -> list[RetrievedChunk]:
    """通过 Elasticsearch 进行 BM25 关键词检索"""
    es = get_es_client()

    # 构建查询
    must_clauses = [
        {
            "multi_match": {
                "query": query,
                "fields": ["content^3", "section_path", "doc_title"],
                "type": "best_fields",
                "analyzer": "ik_smart",
            }
        }
    ]

    filter_clauses = [{"term": {"is_latest": True}}]

    if doc_id:
        filter_clauses.append({"term": {"doc_id": doc_id}})

    search_body = {
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "size": settings.retrieval_top_k_bm25,
        "_source": True,
    }

    try:
        response = await es.search(
            index=settings.es_index_name,
            body=search_body,
        )
    except Exception as e:
        logger.warning("Elasticsearch search failed, returning empty", error=str(e))
        return []

    chunks = []
    hits = response.get("hits", {}).get("hits", [])

    for hit in hits:
        source = hit.get("_source", {})
        chunks.append(
            RetrievedChunk(
                chunk_id=hit["_id"],
                doc_id=source.get("doc_id", ""),
                doc_title=source.get("doc_title", ""),
                section_path=source.get("section_path", ""),
                page_numbers=source.get("page_numbers", []),
                chunk_index=source.get("chunk_index", 0),
                chunk_type=source.get("chunk_type", "text"),
                content=source.get("content", ""),
                score=hit.get("_score", 0.0),
            )
        )

    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# RRF 融合
# ═══════════════════════════════════════════════════════════════════════════


def _rrf_fusion(
    vector_results: list[RetrievedChunk],
    bm25_results: list[RetrievedChunk],
    k: int | None = None,
) -> list[RetrievedChunk]:
    """
    Reciprocal Rank Fusion (RRF) 融合两路检索结果

    公式: RRF_score(d) = sum(1 / (k + rank_i(d)))
    """
    k = k or settings.retrieval_rrf_k

    # chunk_id -> RRF score
    rrf_scores: dict[str, float] = defaultdict(float)
    # chunk_id -> RetrievedChunk (保留完整信息)
    chunk_map: dict[str, RetrievedChunk] = {}

    # 向量检索结果的 RRF 分数
    for rank, chunk in enumerate(vector_results, start=1):
        rrf_scores[chunk.chunk_id] += 1.0 / (k + rank)
        chunk_map[chunk.chunk_id] = chunk

    # BM25 检索结果的 RRF 分数
    for rank, chunk in enumerate(bm25_results, start=1):
        rrf_scores[chunk.chunk_id] += 1.0 / (k + rank)
        if chunk.chunk_id not in chunk_map:
            chunk_map[chunk.chunk_id] = chunk

    # 按 RRF 分数降序排序
    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

    results = []
    for chunk_id in sorted_ids:
        chunk = chunk_map[chunk_id]
        chunk.score = rrf_scores[chunk_id]
        results.append(chunk)

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Reranker 重排序
# ═══════════════════════════════════════════════════════════════════════════


def _rerank(
    query: str,
    candidates: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """使用 BGE-Reranker 对候选结果重排序"""
    if not candidates:
        return []

    reranker = _get_reranker()

    # 构建 query-document 对
    pairs = [(query, chunk.content) for chunk in candidates]

    # 批量计算相关性分数
    scores = reranker.predict(pairs, show_progress_bar=False)

    # 将分数赋值回 chunks
    for chunk, score in zip(candidates, scores):
        chunk.score = float(score)

    # 按 reranker 分数降序排序
    candidates.sort(key=lambda c: c.score, reverse=True)

    logger.info(
        "Reranking done",
        candidate_count=len(candidates),
        top_score=candidates[0].score if candidates else 0,
    )

    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# 上下文窗口扩展
# ═══════════════════════════════════════════════════════════════════════════


async def _expand_context_window(
    chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """
    对检索到的 chunk 扩展上下文窗口：
    获取每个 chunk 前后相邻的 chunk，拼接为更完整的上下文
    """
    if not chunks or settings.context_window_chunks <= 0:
        return chunks

    es = get_es_client()
    expanded = []

    for chunk in chunks:
        window = settings.context_window_chunks
        target_indices = list(
            range(
                max(0, chunk.chunk_index - window),
                chunk.chunk_index + window + 1,
            )
        )

        # 查询相邻的 chunks
        try:
            response = await es.search(
                index=settings.es_index_name,
                body={
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"doc_id": chunk.doc_id}},
                                {"terms": {"chunk_index": target_indices}},
                            ]
                        }
                    },
                    "sort": [{"chunk_index": "asc"}],
                    "size": len(target_indices),
                },
            )

            hits = response.get("hits", {}).get("hits", [])
            if len(hits) > 1:
                # 拼接相邻 chunk 的内容
                neighbor_contents = []
                for hit in hits:
                    src = hit["_source"]
                    neighbor_contents.append(src.get("content", ""))

                expanded_content = "\n\n".join(neighbor_contents)
                expanded_chunk = RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    doc_title=chunk.doc_title,
                    section_path=chunk.section_path,
                    page_numbers=chunk.page_numbers,
                    chunk_index=chunk.chunk_index,
                    chunk_type=chunk.chunk_type,
                    content=expanded_content,
                    score=chunk.score,
                )
                expanded.append(expanded_chunk)
            else:
                expanded.append(chunk)

        except Exception:
            expanded.append(chunk)

    return expanded
