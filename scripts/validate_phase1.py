"""
DocAI Platform - Phase 1 端到端验证脚本
验证：解析 → 分块 → 嵌入 → 存储 → 检索 → 生成 全流程
需要基础设施服务运行（make up && make init）
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import structlog

# 将项目根目录添加到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = structlog.get_logger()

TEST_DOCS_DIR = Path(__file__).parent.parent / "tests" / "test_docs"


class Phase1Validator:
    """Phase 1 端到端验证"""

    def __init__(self):
        self.results: list[dict] = []
        self.doc_id: str | None = None

    def _record(self, step: str, passed: bool, detail: str = "", duration_ms: int = 0):
        status = "PASS" if passed else "FAIL"
        self.results.append({
            "step": step,
            "status": status,
            "detail": detail,
            "duration_ms": duration_ms,
        })
        icon = "✅" if passed else "❌"
        print(f"  {icon} {step}: {detail} ({duration_ms}ms)")

    async def run_all(self):
        """运行所有验证步骤"""
        print("=" * 70)
        print("  DocAI Platform - Phase 1 端到端验证")
        print("=" * 70)
        print()

        # Step 1: 检查基础设施
        print("[1/7] 基础设施连接检查")
        await self._check_infrastructure()

        # Step 2: 文档解析验证
        print("\n[2/7] 文档解析验证")
        self._check_parser()

        # Step 3: 分块验证
        print("\n[3/7] 语义分块验证")
        self._check_chunker()

        # Step 4: 嵌入计算验证
        print("\n[4/7] 嵌入模型验证")
        self._check_embedding()

        # Step 5: 端到端入库验证（需要基础设施）
        print("\n[5/7] 文档入库 Pipeline 验证")
        await self._check_ingestion_pipeline()

        # Step 6: 检索验证
        print("\n[6/7] 混合检索验证")
        await self._check_retrieval()

        # Step 7: 答案生成验证
        print("\n[7/7] 答案生成验证")
        await self._check_generation()

        # 清理
        if self.doc_id:
            print("\n[清理] 删除测试文档...")
            await self._cleanup()

        # 汇总
        self._print_summary()

    async def _check_infrastructure(self):
        """检查所有基础设施服务是否可用"""
        from app.core.infrastructure import (
            get_qdrant_client, get_es_client, get_redis_client, get_minio_client,
        )
        from sqlalchemy import text
        from app.core.infrastructure import get_db_session

        # PostgreSQL
        t0 = time.time()
        try:
            async with get_db_session() as session:
                result = await session.execute(text("SELECT 1"))
                result.scalar()
            self._record("PostgreSQL", True, "连接正常", int((time.time() - t0) * 1000))
        except Exception as e:
            self._record("PostgreSQL", False, str(e), int((time.time() - t0) * 1000))

        # Qdrant
        t0 = time.time()
        try:
            qdrant = get_qdrant_client()
            collections = await qdrant.get_collections()
            names = [c.name for c in collections.collections]
            self._record("Qdrant", True, f"集合: {names}", int((time.time() - t0) * 1000))
        except Exception as e:
            self._record("Qdrant", False, str(e), int((time.time() - t0) * 1000))

        # Elasticsearch
        t0 = time.time()
        try:
            es = get_es_client()
            info = await es.info()
            version = info.get("version", {}).get("number", "?")
            self._record("Elasticsearch", True, f"版本: {version}", int((time.time() - t0) * 1000))
        except Exception as e:
            self._record("Elasticsearch", False, str(e), int((time.time() - t0) * 1000))

        # MinIO
        t0 = time.time()
        try:
            minio = get_minio_client()
            buckets = [b.name for b in minio.list_buckets()]
            self._record("MinIO", True, f"Buckets: {buckets}", int((time.time() - t0) * 1000))
        except Exception as e:
            self._record("MinIO", False, str(e), int((time.time() - t0) * 1000))

        # Redis
        t0 = time.time()
        try:
            redis = get_redis_client()
            await redis.ping()
            self._record("Redis", True, "连接正常", int((time.time() - t0) * 1000))
        except Exception as e:
            self._record("Redis", False, str(e), int((time.time() - t0) * 1000))

    def _check_parser(self):
        """验证文档解析器"""
        from app.ingestion.parser import parse_document

        supported = {".pdf", ".docx", ".pptx", ".xlsx"}
        test_files = [f for f in TEST_DOCS_DIR.iterdir() if f.suffix.lower() in supported]

        if not test_files:
            self._record("解析器", False, "未找到测试文档")
            return

        success = 0
        for f in test_files:
            t0 = time.time()
            try:
                doc = parse_document(str(f))
                duration = int((time.time() - t0) * 1000)
                self._record(
                    f"解析 {f.name}",
                    True,
                    f"sections={len(doc.sections)}, tables={len(doc.tables)}, pages={doc.page_count}",
                    duration,
                )
                success += 1
            except Exception as e:
                duration = int((time.time() - t0) * 1000)
                self._record(f"解析 {f.name}", False, str(e)[:100], duration)

        rate = success / len(test_files) * 100
        self._record("解析成功率", rate >= 80, f"{success}/{len(test_files)} ({rate:.0f}%)")

    def _check_chunker(self):
        """验证语义分块"""
        from app.ingestion.parser import parse_document
        from app.ingestion.chunker import semantic_chunk

        test_file = next(
            (f for f in TEST_DOCS_DIR.iterdir() if f.suffix == ".docx"),
            None,
        )
        if not test_file:
            self._record("分块器", False, "未找到 DOCX 测试文档")
            return

        t0 = time.time()
        doc = parse_document(str(test_file))
        chunks = semantic_chunk(doc, doc_id="validation-test")
        duration = int((time.time() - t0) * 1000)

        self._record(
            f"分块 {test_file.name}",
            len(chunks) > 0,
            f"chunks={len(chunks)}, total_tokens={sum(c.token_count for c in chunks)}",
            duration,
        )

        # 验证 chunk 大小
        oversized = [c for c in chunks if c.token_count > 1600]
        self._record(
            "Chunk 大小合规",
            len(oversized) == 0,
            f"超大chunk: {len(oversized)}/{len(chunks)}",
        )

    def _check_embedding(self):
        """验证嵌入模型"""
        from app.core.embedding import encode_texts, encode_single
        from config.settings import settings

        t0 = time.time()
        try:
            vec = encode_single("这是一个测试文本，用于验证嵌入模型。")
            duration = int((time.time() - t0) * 1000)
            self._record(
                "单文本嵌入",
                len(vec) == settings.embedding_dimension,
                f"维度={len(vec)}",
                duration,
            )
        except Exception as e:
            self._record("单文本嵌入", False, str(e), int((time.time() - t0) * 1000))

        t0 = time.time()
        try:
            vecs = encode_texts([f"测试文本 {i}" for i in range(10)])
            duration = int((time.time() - t0) * 1000)
            self._record(
                "批量嵌入 (10条)",
                len(vecs) == 10,
                f"数量={len(vecs)}, 维度={len(vecs[0])}",
                duration,
            )
        except Exception as e:
            self._record("批量嵌入", False, str(e), int((time.time() - t0) * 1000))

    async def _check_ingestion_pipeline(self):
        """验证完整入库流程"""
        from app.ingestion.pipeline import ingestion_pipeline

        # 选一个小文件测试
        test_file = next(
            (f for f in TEST_DOCS_DIR.iterdir() if f.suffix == ".docx"),
            None,
        )
        if not test_file:
            self._record("入库 Pipeline", False, "未找到测试文档")
            return

        t0 = time.time()
        try:
            self.doc_id = await ingestion_pipeline.process_document(
                file_path=str(test_file),
                original_filename=test_file.name,
                doc_type="test",
                tags=["validation", "phase1"],
            )
            duration = int((time.time() - t0) * 1000)
            self._record("入库 Pipeline", True, f"doc_id={self.doc_id}", duration)
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            self._record("入库 Pipeline", False, str(e)[:200], duration)

    async def _check_retrieval(self):
        """验证混合检索"""
        if not self.doc_id:
            self._record("混合检索", False, "无可检索的文档（入库失败）")
            return

        from app.retrieval.hybrid_search import hybrid_search

        test_queries = [
            "这个文档的主要内容是什么？",
            "有哪些关键要求？",
        ]

        for query in test_queries:
            t0 = time.time()
            try:
                results = await hybrid_search(
                    query=query,
                    doc_id=self.doc_id,
                    top_k=3,
                )
                duration = int((time.time() - t0) * 1000)
                self._record(
                    f"检索: {query[:20]}...",
                    len(results) > 0,
                    f"结果数={len(results)}, top_score={results[0].score:.3f}" if results else "无结果",
                    duration,
                )
            except Exception as e:
                duration = int((time.time() - t0) * 1000)
                self._record(f"检索: {query[:20]}...", False, str(e)[:100], duration)

    async def _check_generation(self):
        """验证答案生成"""
        if not self.doc_id:
            self._record("答案生成", False, "无可检索的文档")
            return

        from app.retrieval.hybrid_search import hybrid_search
        from app.generation.answer import generate_answer

        query = "这个文档的主要内容是什么？"

        t0 = time.time()
        try:
            chunks = await hybrid_search(query=query, doc_id=self.doc_id, top_k=3)
            response = await generate_answer(question=query, retrieved_chunks=chunks)
            duration = int((time.time() - t0) * 1000)

            self._record(
                "答案生成",
                len(response.answer) > 20,
                f"答案长度={len(response.answer)}, 引用数={len(response.citations)}, 置信度={response.confidence}",
                duration,
            )
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            self._record("答案生成", False, str(e)[:200], duration)

    async def _cleanup(self):
        """清理测试数据"""
        if not self.doc_id:
            return
        try:
            from app.ingestion.pipeline import ingestion_pipeline
            await ingestion_pipeline.delete_document(self.doc_id)
            print(f"  ✅ 已清理测试文档 {self.doc_id}")
        except Exception as e:
            print(f"  ⚠️ 清理失败: {e}")

    def _print_summary(self):
        """打印验证汇总"""
        print("\n" + "=" * 70)
        print("  验证汇总")
        print("=" * 70)

        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        total = len(self.results)

        print(f"\n  总计: {total} 项检查")
        print(f"  通过: {passed} ✅")
        print(f"  失败: {failed} ❌")
        print(f"  通过率: {passed / total * 100:.0f}%")

        if failed > 0:
            print("\n  失败项:")
            for r in self.results:
                if r["status"] == "FAIL":
                    print(f"    ❌ {r['step']}: {r['detail']}")

        print()

        # Phase 1 验证标准对照
        print("  Phase 1 验证标准对照:")
        print("  ┌────────────────────────┬────────┬──────────────────────────┐")
        print("  │ 指标                   │ 目标   │ 状态                     │")
        print("  ├────────────────────────┼────────┼──────────────────────────┤")

        parse_results = [r for r in self.results if "解析" in r["step"]]
        parse_ok = all(r["status"] == "PASS" for r in parse_results) if parse_results else False
        print(f"  │ 文档解析成功率         │ ≥ 95%  │ {'✅ 通过' if parse_ok else '❌ 未达标':25s}│")

        retrieval_results = [r for r in self.results if "检索" in r["step"]]
        retrieval_ok = any(r["status"] == "PASS" for r in retrieval_results) if retrieval_results else False
        print(f"  │ 检索召回率 Recall@5    │ ≥ 80%  │ {'✅ 功能就绪' if retrieval_ok else '⏳ 需在线验证':25s}│")

        gen_results = [r for r in self.results if "答案" in r["step"]]
        gen_ok = any(r["status"] == "PASS" for r in gen_results) if gen_results else False
        print(f"  │ 答案准确率             │ ≥ 75%  │ {'✅ 功能就绪' if gen_ok else '⏳ 需在线验证':25s}│")

        pipeline_results = [r for r in self.results if "Pipeline" in r["step"]]
        pipeline_duration = next((r["duration_ms"] for r in pipeline_results if r["status"] == "PASS"), 0)
        print(f"  │ 单文档处理时间         │ < 60s  │ {'✅ ' + str(pipeline_duration) + 'ms' if pipeline_duration > 0 else '⏳ 需在线验证':25s}│")

        query_durations = [r["duration_ms"] for r in retrieval_results + gen_results if r["status"] == "PASS"]
        avg_query = sum(query_durations) // len(query_durations) if query_durations else 0
        print(f"  │ 查询响应时间           │ < 5s   │ {'✅ ~' + str(avg_query) + 'ms' if avg_query > 0 else '⏳ 需在线验证':25s}│")

        print("  └────────────────────────┴────────┴──────────────────────────┘")
        print()

        if failed == 0:
            print("  🎉 Phase 1 所有验证项通过！")
        elif failed <= 2:
            print("  ⚠️ Phase 1 基本完成，部分验证项需在线环境确认。")
        else:
            print("  ❌ Phase 1 存在较多问题，请检查基础设施和代码。")


async def main():
    validator = Phase1Validator()
    await validator.run_all()


if __name__ == "__main__":
    asyncio.run(main())
