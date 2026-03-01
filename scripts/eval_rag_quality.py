"""
DocAI Platform - RAG 质量评估与消融实验 (Ablation Study)
对比本系统的增强 RAG 与简单 RAG 基线的效果差异。

用法:
    # 1. 先确保基础服务运行且至少上传了一些文档
    # 2. 运行完整评估
    python -m scripts.eval_rag_quality

    # 3. 只运行检索对比
    python -m scripts.eval_rag_quality --retrieval-only

    # 4. 使用自定义测试集
    python -m scripts.eval_rag_quality --test-file tests/my_questions.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

# ── 导入系统模块 ──
from app.core.infrastructure import get_db_session
from app.core.llm_client import llm, llm_light
from app.core.models import RetrievedChunk
from app.generation.answer import generate_answer
from app.retrieval.hybrid_search import (
    _bm25_search,
    _rerank,
    _rrf_fusion,
    _vector_search,
    hybrid_search,
)
from config.settings import settings

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class TestCase:
    """一个测试用例"""
    question: str
    expected_answer: str = ""          # 标准答案（可选，如有则评估准确性）
    expected_doc_id: str = ""          # 预期命中的文档 ID
    expected_section: str = ""         # 预期命中的章节路径
    category: str = "factual"          # factual / summary / comparison / version_diff


@dataclass
class RetrievalResult:
    """检索结果"""
    method: str                        # 检索方法名称
    chunks: list[RetrievedChunk]
    latency_ms: int
    top_scores: list[float] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.chunks)

    @property
    def avg_score(self) -> float:
        return sum(self.top_scores) / len(self.top_scores) if self.top_scores else 0

    @property
    def max_score(self) -> float:
        return max(self.top_scores) if self.top_scores else 0


@dataclass
class GenerationResult:
    """生成结果"""
    method: str
    answer: str
    confidence: float
    latency_ms: int
    citation_count: int


@dataclass
class EvalReport:
    """单个测试用例的评估报告"""
    question: str
    category: str
    retrieval_results: dict[str, RetrievalResult] = field(default_factory=dict)
    generation_results: dict[str, GenerationResult] = field(default_factory=dict)
    relevance_scores: dict[str, float] = field(default_factory=dict)  # LLM 评判的相关性


# ═══════════════════════════════════════════════════════════════════════════
# 检索方法: 从最简单到最完整，逐层叠加
# ═══════════════════════════════════════════════════════════════════════════


async def retrieval_vector_only(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """方法 A: 仅向量检索 (最简单的基线)"""
    results = await _vector_search(query)
    return sorted(results, key=lambda x: x.score, reverse=True)[:top_k]


async def retrieval_bm25_only(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """方法 B: 仅 BM25 关键词检索"""
    results = await _bm25_search(query)
    return sorted(results, key=lambda x: x.score, reverse=True)[:top_k]


async def retrieval_hybrid_no_rerank(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """方法 C: 混合检索 (向量 + BM25 + RRF) 但不用 Reranker"""
    vector_results = await _vector_search(query)
    bm25_results = await _bm25_search(query)
    fused = _rrf_fusion(vector_results, bm25_results)
    return sorted(fused, key=lambda x: x.score, reverse=True)[:top_k]


async def retrieval_hybrid_with_rerank(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """方法 D: 混合检索 + Reranker (不含上下文扩展)"""
    vector_results = await _vector_search(query)
    bm25_results = await _bm25_search(query)
    fused = _rrf_fusion(vector_results, bm25_results)
    reranked = _rerank(query, fused)  # 同步函数
    return reranked[:top_k]


async def retrieval_full_pipeline(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """方法 E: 完整 Pipeline (向量+BM25+RRF+Reranker+上下文扩展)"""
    return await hybrid_search(query=query, top_k=top_k, use_reranker=True)


# ═══════════════════════════════════════════════════════════════════════════
# 生成方法: 对比不同检索基础上的答案质量
# ═══════════════════════════════════════════════════════════════════════════


async def generate_with_chunks(
    question: str, chunks: list[RetrievedChunk], method_name: str
) -> GenerationResult:
    """基于给定 chunks 生成答案"""
    start = time.time()
    try:
        response = await generate_answer(question, chunks)
        latency = int((time.time() - start) * 1000)
        return GenerationResult(
            method=method_name,
            answer=response.answer,
            confidence=response.confidence,
            latency_ms=latency,
            citation_count=len(response.citations),
        )
    except Exception as e:
        return GenerationResult(
            method=method_name,
            answer=f"[ERROR: {e}]",
            confidence=0.0,
            latency_ms=int((time.time() - start) * 1000),
            citation_count=0,
        )


# ═══════════════════════════════════════════════════════════════════════════
# LLM 评判器: 自动评估答案质量
# ═══════════════════════════════════════════════════════════════════════════


async def judge_answer_quality(
    question: str,
    answer_a: str,
    answer_b: str,
    label_a: str = "简单RAG",
    label_b: str = "增强RAG",
) -> dict:
    """
    让 LLM 当裁判，对比两个答案的质量。
    返回: { winner, reason, score_a, score_b }
    """
    prompt = f"""你是一个专业的文档问答系统评估专家。请对比以下两个系统对同一问题的回答质量。

## 用户问题
{question}

## 答案 A ({label_a})
{answer_a[:2000]}

## 答案 B ({label_b})
{answer_b[:2000]}

## 评估维度
1. **准确性**: 答案是否准确、有事实依据
2. **完整性**: 是否回答了问题的所有方面
3. **引用质量**: 是否提供了有效的来源引用
4. **清晰度**: 答案是否条理清晰、易于理解
5. **相关性**: 答案是否切题，没有冗余信息

## 要求
返回 JSON:
{{
  "score_a": 1-10,
  "score_b": 1-10,
  "winner": "A" 或 "B" 或 "tie",
  "reason": "简要说明为什么一个更好"
}}
"""
    try:
        result = await llm.generate_json(prompt)
        return result
    except Exception as e:
        return {"score_a": 5, "score_b": 5, "winner": "tie", "reason": f"评估失败: {e}"}


async def judge_retrieval_relevance(
    question: str, chunks: list[RetrievedChunk]
) -> float:
    """评估检索结果对问题的整体相关性 (0-1)"""
    if not chunks:
        return 0.0

    chunks_text = "\n---\n".join(
        f"[文档: {c.doc_title}, 章节: {c.section_path}]\n{c.content[:300]}"
        for c in chunks[:5]
    )
    prompt = f"""请评估以下检索结果与用户问题的总体相关性。

问题: {question}

检索到的文档片段:
{chunks_text}

请只返回一个 0 到 1 之间的小数，表示相关性:
- 0.0-0.3: 几乎不相关
- 0.3-0.5: 部分相关，但缺少关键信息
- 0.5-0.7: 大部分相关
- 0.7-0.9: 高度相关
- 0.9-1.0: 完美匹配

只返回数字，不要其他文字。"""
    try:
        text = await llm_light.generate(prompt, temperature=0.0)
        return float(text.strip())
    except Exception:
        return 0.5


# ═══════════════════════════════════════════════════════════════════════════
# 默认测试集
# ═══════════════════════════════════════════════════════════════════════════


def get_default_test_cases() -> list[TestCase]:
    """
    通用测试问题集。
    实际使用时，请替换为你自己文档库中的真实问题。
    """
    return [
        # ── 事实性问题 (适合比较检索精度) ──
        TestCase(
            question="这份文档的主要内容是什么？",
            category="factual",
        ),
        TestCase(
            question="文档中提到了哪些关键日期或时间节点？",
            category="factual",
        ),
        TestCase(
            question="文档中的核心结论或建议是什么？",
            category="factual",
        ),

        # ── 总结性问题 (适合比较上下文组装) ──
        TestCase(
            question="请总结这份文档的主要内容和关键要点",
            category="summary",
        ),
        TestCase(
            question="文档中各章节分别讨论了哪些主题？",
            category="summary",
        ),

        # ── 分析性问题 (适合比较深度理解) ──
        TestCase(
            question="文档中的数据或指标说明了什么趋势？",
            category="comparison",
        ),
    ]


async def generate_test_cases_from_db() -> list[TestCase]:
    """从数据库中自动生成测试用例（基于已有文档）"""
    test_cases = []
    async with get_db_session() as session:
        from sqlalchemy import text
        result = await session.execute(text("""
            SELECT doc_id, title, doc_summary
            FROM documents
            WHERE processing_status = 'ready' AND doc_summary IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 5
        """))
        docs = result.fetchall()

    if not docs:
        print("⚠️  数据库中没有已处理的文档，使用默认测试集")
        return get_default_test_cases()

    for doc_id, title, summary in docs:
        # 为每个文档生成针对性问题
        test_cases.extend([
            TestCase(
                question=f"《{title}》这份文档的主要内容是什么？",
                expected_doc_id=str(doc_id),
                category="factual",
            ),
            TestCase(
                question=f"请总结《{title}》的核心要点",
                expected_doc_id=str(doc_id),
                category="summary",
            ),
        ])

    # 如果有多个文档，添加跨文档问题
    if len(docs) >= 2:
        titles = [row[1] for row in docs[:3]]
        test_cases.append(TestCase(
            question=f"对比《{titles[0]}》和《{titles[1]}》的主要异同点",
            category="comparison",
        ))

    return test_cases


# ═══════════════════════════════════════════════════════════════════════════
# 主评估流程
# ═══════════════════════════════════════════════════════════════════════════


RETRIEVAL_METHODS = {
    "A_仅向量": retrieval_vector_only,
    "B_仅BM25": retrieval_bm25_only,
    "C_混合无Rerank": retrieval_hybrid_no_rerank,
    "D_混合+Rerank": retrieval_hybrid_with_rerank,
    "E_完整Pipeline": retrieval_full_pipeline,
}


async def run_retrieval_comparison(test_cases: list[TestCase]) -> list[EvalReport]:
    """运行检索对比实验"""
    reports = []

    for i, tc in enumerate(test_cases):
        print(f"\n{'='*60}")
        print(f"问题 {i+1}/{len(test_cases)}: {tc.question}")
        print(f"类型: {tc.category}")
        print(f"{'='*60}")

        report = EvalReport(question=tc.question, category=tc.category)

        for method_name, method_fn in RETRIEVAL_METHODS.items():
            start = time.time()
            try:
                chunks = await method_fn(tc.question)
                latency = int((time.time() - start) * 1000)

                result = RetrievalResult(
                    method=method_name,
                    chunks=chunks,
                    latency_ms=latency,
                    top_scores=[c.score for c in chunks[:5]],
                )
                report.retrieval_results[method_name] = result

                # 用 LLM 评估检索相关性
                relevance = await judge_retrieval_relevance(tc.question, chunks)
                report.relevance_scores[method_name] = relevance

                print(f"  {method_name}: {result.hit_count} chunks, "
                      f"max_score={result.max_score:.4f}, "
                      f"relevance={relevance:.2f}, "
                      f"{latency}ms")
            except Exception as e:
                print(f"  {method_name}: ❌ ERROR - {e}")

        reports.append(report)

    return reports


async def run_full_evaluation(test_cases: list[TestCase]) -> list[EvalReport]:
    """运行完整评估（检索 + 生成 + LLM 评判）"""
    reports = await run_retrieval_comparison(test_cases)

    print(f"\n\n{'='*60}")
    print("开始答案质量对比 (简单RAG vs 增强RAG)")
    print(f"{'='*60}")

    for i, report in enumerate(reports):
        tc = test_cases[i]
        print(f"\n问题: {tc.question}")

        # 用最简单检索的结果生成答案 (基线)
        baseline_chunks = report.retrieval_results.get("A_仅向量")
        full_chunks = report.retrieval_results.get("E_完整Pipeline")

        if baseline_chunks and full_chunks:
            baseline_gen = await generate_with_chunks(
                tc.question, baseline_chunks.chunks, "简单RAG(仅向量)"
            )
            full_gen = await generate_with_chunks(
                tc.question, full_chunks.chunks, "增强RAG(完整Pipeline)"
            )

            report.generation_results["baseline"] = baseline_gen
            report.generation_results["enhanced"] = full_gen

            # LLM 对比评判
            if baseline_gen.answer and full_gen.answer and "[ERROR" not in baseline_gen.answer:
                judgment = await judge_answer_quality(
                    tc.question, baseline_gen.answer, full_gen.answer
                )
                print(f"  基线答案:  置信度={baseline_gen.confidence:.2f}, {baseline_gen.latency_ms}ms")
                print(f"  增强答案:  置信度={full_gen.confidence:.2f}, {full_gen.latency_ms}ms")
                print(f"  LLM 评判: {judgment.get('winner', '?')} 获胜")
                print(f"    基线: {judgment.get('score_a', '?')}/10, "
                      f"增强: {judgment.get('score_b', '?')}/10")
                print(f"    原因: {judgment.get('reason', '?')}")
            else:
                print(f"  ⚠️ 跳过答案对比（生成出错）")

    return reports


def print_summary(reports: list[EvalReport]):
    """打印汇总报告"""
    print(f"\n\n{'='*60}")
    print("📊 评估汇总报告")
    print(f"{'='*60}")

    # 1. 检索质量汇总
    print(f"\n## 检索质量对比 (LLM 评估的相关性 0-1)")
    print(f"{'方法':<20} {'平均相关性':>10} {'平均延迟':>10}")
    print("-" * 45)

    for method_name in RETRIEVAL_METHODS:
        scores = [r.relevance_scores.get(method_name, 0) for r in reports if method_name in r.relevance_scores]
        latencies = [r.retrieval_results[method_name].latency_ms for r in reports if method_name in r.retrieval_results]
        if scores:
            avg_score = sum(scores) / len(scores)
            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            print(f"{method_name:<20} {avg_score:>10.3f} {avg_latency:>8.0f}ms")

    # 2. 答案质量汇总
    baseline_confs = []
    enhanced_confs = []
    for r in reports:
        if "baseline" in r.generation_results:
            baseline_confs.append(r.generation_results["baseline"].confidence)
        if "enhanced" in r.generation_results:
            enhanced_confs.append(r.generation_results["enhanced"].confidence)

    if baseline_confs and enhanced_confs:
        print(f"\n## 答案质量对比")
        print(f"  简单 RAG 平均置信度: {sum(baseline_confs)/len(baseline_confs):.3f}")
        print(f"  增强 RAG 平均置信度: {sum(enhanced_confs)/len(enhanced_confs):.3f}")

    # 3. 关键结论
    print(f"\n## 增强特性贡献分析")
    methods = list(RETRIEVAL_METHODS.keys())
    for i in range(1, len(methods)):
        prev = methods[i - 1]
        curr = methods[i]
        prev_scores = [r.relevance_scores.get(prev, 0) for r in reports if prev in r.relevance_scores]
        curr_scores = [r.relevance_scores.get(curr, 0) for r in reports if curr in r.relevance_scores]
        if prev_scores and curr_scores:
            delta = (sum(curr_scores) / len(curr_scores)) - (sum(prev_scores) / len(prev_scores))
            direction = "📈" if delta > 0.01 else ("📉" if delta < -0.01 else "➡️")
            feature = {
                "B_仅BM25": "+ BM25关键词检索",
                "C_混合无Rerank": "+ RRF融合",
                "D_混合+Rerank": "+ Reranker重排",
                "E_完整Pipeline": "+ 上下文扩展",
            }.get(curr, curr)
            print(f"  {direction} {feature}: 相关性变化 {delta:+.3f}")

    print(f"\n{'='*60}")
    print("💡 提示: 用更多针对性的测试问题可以得到更准确的评估结果。")
    print("   建议: 创建 tests/eval_questions.json，包含 20+ 个标注了标准答案的问题。")
    print(f"{'='*60}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════


async def main():
    parser = argparse.ArgumentParser(description="DocAI RAG 质量评估")
    parser.add_argument("--retrieval-only", action="store_true", help="只运行检索对比，不生成答案")
    parser.add_argument("--test-file", type=str, help="自定义测试集文件 (JSON)")
    parser.add_argument("--auto", action="store_true", help="自动从数据库生成测试用例")
    args = parser.parse_args()

    print("🧪 DocAI RAG 质量评估工具")
    print("=" * 60)

    # 加载测试用例
    if args.test_file:
        with open(args.test_file) as f:
            data = json.load(f)
        test_cases = [TestCase(**tc) for tc in data]
        print(f"从 {args.test_file} 加载了 {len(test_cases)} 个测试用例")
    elif args.auto:
        test_cases = await generate_test_cases_from_db()
        print(f"自动生成了 {len(test_cases)} 个测试用例")
    else:
        test_cases = await generate_test_cases_from_db()
        print(f"生成了 {len(test_cases)} 个测试用例")

    # 运行评估
    if args.retrieval_only:
        reports = await run_retrieval_comparison(test_cases)
    else:
        reports = await run_full_evaluation(test_cases)

    # 打印汇总
    print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
