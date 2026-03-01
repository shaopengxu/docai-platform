"""
DocAI Platform - 文档入库 Pipeline
完整流程：上传 → 解析 → 分块 → 嵌入 → 存储 (Qdrant + ES + PostgreSQL + MinIO)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.embedding import encode_texts
from app.core.infrastructure import (
    get_db_session,
    get_es_client,
    get_minio_client,
    get_qdrant_client,
)
from app.core.models import Chunk, ChunkType, ParsedDocument, ProcessingStatus
from app.ingestion.chunker import semantic_chunk
from app.ingestion.parser import parse_document
from app.ingestion.summarizer import (
    generate_contextual_description,
    generate_doc_summary_and_entities,
    generate_section_summary,
)
from app.versioning.detector import version_detector
from app.versioning.diff_engine import diff_engine
from config.settings import settings

logger = structlog.get_logger()


class IngestionPipeline:
    """文档入库完整 Pipeline"""

    def __init__(self):
        # 保存后台任务引用，防止 asyncio task 被 GC 回收
        self._background_tasks: set[asyncio.Task] = set()

    async def process_document(
        self,
        file_path: str,
        original_filename: str,
        doc_id: str | None = None,
        doc_type: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        处理单个文档的完整流程

        Args:
            file_path: 本地临时文件路径
            original_filename: 原始文件名
            doc_id: 可选的文档 ID，不提供则自动生成
            doc_type: 文档类型 (contract/report/policy/...)
            tags: 标签列表

        Returns:
            doc_id: 文档 ID
        """
        doc_id = doc_id or str(uuid.uuid4())
        tags = tags or []

        logger.info(
            "Starting document ingestion",
            doc_id=doc_id,
            filename=original_filename,
        )

        try:
            # Step 0a: 文件指纹去重
            file_hash = self._compute_file_hash(file_path)
            existing_doc = await self._check_file_hash(file_hash)
            if existing_doc:
                raise ValueError(
                    f"文件内容完全相同的文档已存在 "
                    f"(doc_id={existing_doc['doc_id']}, "
                    f"title={existing_doc['title']})"
                )

            # Step 0b: 注册文档到 PostgreSQL (状态: pending)
            file_size = os.path.getsize(file_path)
            await self._register_document(
                doc_id=doc_id,
                title=Path(original_filename).stem,
                original_filename=original_filename,
                file_size=file_size,
                file_hash=file_hash,
                doc_type=doc_type,
                tags=tags,
            )

            # Step 1: 上传原文到 MinIO
            await self._update_status(doc_id, ProcessingStatus.PARSING)
            minio_path = await self._upload_to_minio(file_path, doc_id, original_filename)
            await self._update_file_path(doc_id, minio_path)

            # Step 2: 解析文档
            parsed_doc = parse_document(file_path)

            # 更新页数和标题
            await self._update_doc_metadata(
                doc_id=doc_id,
                title=parsed_doc.title or Path(original_filename).stem,
                page_count=parsed_doc.page_count,
            )

            # Step 3: 分块
            await self._update_status(doc_id, ProcessingStatus.CHUNKING)
            chunks = semantic_chunk(parsed_doc, doc_id=doc_id)

            if not chunks:
                logger.warning("No chunks generated", doc_id=doc_id)
                await self._update_status(doc_id, ProcessingStatus.READY)
                return doc_id

            # Step 3.5: 生成摘要和实体 (Phase 2)
            await self._update_status(doc_id, ProcessingStatus.SUMMARIZING)
            doc_summary, summary_chunks = await self._generate_summaries_and_metadata(
                chunks, doc_id, parsed_doc.title
            )
            chunks.extend(summary_chunks)

            # Step 3.6: 版本检测 (Phase 3)
            # 返回值表示当前文档是否为最新版（如果上传的是旧版文档，则为 False）
            is_doc_latest = await self._detect_and_link_version(
                doc_id, parsed_doc.title or Path(original_filename).stem,
                doc_summary, doc_type,
            )

            # Step 3.8: Contextual Retrieval 增强
            await self._add_contextual_descriptions(chunks, parsed_doc.title, doc_summary)

            # Step 4: 嵌入
            await self._update_status(doc_id, ProcessingStatus.EMBEDDING)
            embeddings = self._compute_embeddings(chunks)

            # Step 5: 存储到 Qdrant + ES + PostgreSQL
            # is_doc_latest 决定新 chunks 的 is_latest 标记
            await self._store_to_qdrant(chunks, embeddings, doc_id, is_latest=is_doc_latest)
            await self._store_to_elasticsearch(chunks, doc_id, is_latest=is_doc_latest)
            await self._store_chunks_metadata(chunks, doc_id)

            # Step 6: 更新文档状态为 ready
            await self._update_status(doc_id, ProcessingStatus.READY)
            await self._update_chunk_count(doc_id, len(chunks))

            logger.info(
                "Document ingestion completed",
                doc_id=doc_id,
                filename=original_filename,
                chunk_count=len(chunks),
            )

            return doc_id

        except Exception as e:
            logger.error(
                "Document ingestion failed",
                doc_id=doc_id,
                error=str(e),
                exc_info=True,
            )
            await self._update_status(
                doc_id, ProcessingStatus.ERROR, error_msg=str(e)
            )
            raise

    # ─────────────────────────────────────────────────────────────────────
    # PostgreSQL 操作
    # ─────────────────────────────────────────────────────────────────────

    def _compute_file_hash(self, file_path: str) -> str:
        """计算文件的 SHA-256 指纹"""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                sha256.update(block)
        return sha256.hexdigest()

    async def _check_file_hash(self, file_hash: str) -> dict | None:
        """检查是否已存在相同 hash 的文档"""
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT doc_id, title FROM documents
                    WHERE file_hash = :hash AND processing_status != 'error'
                    LIMIT 1
                """),
                {"hash": file_hash},
            )
            row = result.fetchone()
        if row:
            return {"doc_id": str(row[0]), "title": row[1]}
        return None

    async def _register_document(
        self,
        doc_id: str,
        title: str,
        original_filename: str,
        file_size: int,
        file_hash: str,
        doc_type: str | None,
        tags: list[str],
    ):
        """在 PostgreSQL 中注册新文档"""
        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO documents (
                        doc_id, title, original_filename, file_path,
                        file_size_bytes, file_hash, doc_type, tags,
                        processing_status
                    ) VALUES (
                        :doc_id, :title, :original_filename, '',
                        :file_size, :file_hash, :doc_type, :tags, 'pending'
                    )
                """),
                {
                    "doc_id": doc_id,
                    "title": title,
                    "original_filename": original_filename,
                    "file_size": file_size,
                    "file_hash": file_hash,
                    "doc_type": doc_type,
                    "tags": tags,
                },
            )

    async def _update_status(
        self, doc_id: str, status: ProcessingStatus, error_msg: str | None = None
    ):
        """更新文档处理状态"""
        async with get_db_session() as session:
            if error_msg:
                await session.execute(
                    text("""
                        UPDATE documents
                        SET processing_status = :status, processing_error = :error
                        WHERE doc_id = :doc_id
                    """),
                    {"doc_id": doc_id, "status": status.value, "error": error_msg},
                )
            else:
                await session.execute(
                    text("""
                        UPDATE documents
                        SET processing_status = :status
                        WHERE doc_id = :doc_id
                    """),
                    {"doc_id": doc_id, "status": status.value},
                )

    async def _update_file_path(self, doc_id: str, file_path: str):
        async with get_db_session() as session:
            await session.execute(
                text("UPDATE documents SET file_path = :fp WHERE doc_id = :doc_id"),
                {"doc_id": doc_id, "fp": file_path},
            )

    async def _update_doc_metadata(
        self, doc_id: str, title: str, page_count: int
    ):
        async with get_db_session() as session:
            await session.execute(
                text("""
                    UPDATE documents
                    SET title = :title, page_count = :page_count
                    WHERE doc_id = :doc_id
                """),
                {"doc_id": doc_id, "title": title, "page_count": page_count},
            )

    async def _update_chunk_count(self, doc_id: str, count: int):
        async with get_db_session() as session:
            await session.execute(
                text("UPDATE documents SET chunk_count = :cnt WHERE doc_id = :doc_id"),
                {"doc_id": doc_id, "cnt": count},
            )

    async def _update_doc_summary_and_entities(
        self, doc_id: str, doc_summary: str, key_entities: dict
    ):
        import json
        async with get_db_session() as session:
            await session.execute(
                text("""
                    UPDATE documents
                    SET doc_summary = :doc_summary, key_entities = :key_entities
                    WHERE doc_id = :doc_id
                """),
                {
                    "doc_id": doc_id,
                    "doc_summary": doc_summary,
                    "key_entities": json.dumps(key_entities, ensure_ascii=False),
                },
            )

    async def _store_section_summary(
        self, doc_id: str, section_path: str, summary_text: str, key_points: list[str]
    ):
        import json
        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO section_summaries (
                        doc_id, section_path, summary_text, key_points
                    ) VALUES (
                        :doc_id, :section_path, :summary_text, :key_points
                    )
                """),
                {
                    "doc_id": doc_id,
                    "section_path": section_path,
                    "summary_text": summary_text,
                    "key_points": json.dumps(key_points, ensure_ascii=False),
                },
            )

    async def _store_chunks_metadata(self, chunks: list[Chunk], doc_id: str):
        """将 chunk 元数据批量存入 PostgreSQL"""
        if not chunks:
            return

        async with get_db_session() as session:
            params_list = [
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_id": doc_id,
                    "section_path": chunk.section_path,
                    "page_numbers": chunk.page_numbers,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "chunk_type": chunk.chunk_type.value,
                    "token_count": chunk.token_count,
                    "qdrant_point_id": chunk.chunk_id,
                    "es_doc_id": chunk.chunk_id,
                }
                for chunk in chunks
            ]
            stmt = text("""
                INSERT INTO chunks (
                    chunk_id, doc_id, section_path, page_numbers,
                    chunk_index, content, chunk_type, token_count,
                    qdrant_point_id, es_doc_id
                ) VALUES (
                    :chunk_id, :doc_id, :section_path, :page_numbers,
                    :chunk_index, :content, :chunk_type, :token_count,
                    :qdrant_point_id, :es_doc_id
                )
            """)
            for params in params_list:
                await session.execute(stmt, params)

    # ─────────────────────────────────────────────────────────────────────
    # 版本管理 (Phase 3)
    # ─────────────────────────────────────────────────────────────────────

    async def _detect_and_link_version(
        self,
        doc_id: str,
        title: str,
        doc_summary: str,
        doc_type: str | None,
    ) -> bool:
        """
        检测新文档是否为已有文档的新版本，如果是则建立版本链。

        Returns:
            bool: 当前文档是否为最新版 (is_latest)。
                  - True: 新上传的文档更新（默认情况），或未找到版本匹配。
                  - False: 新上传的文档其实是旧版本。
        """
        try:
            match = await version_detector.detect(
                new_doc_id=doc_id,
                title=title,
                doc_summary=doc_summary,
                doc_type=doc_type,
            )

            if not match.is_new_version or not match.matched_doc_id:
                logger.info("No version match found", doc_id=doc_id)
                return True  # 全新文档，默认为最新

            logger.info(
                "Version match found",
                new_doc_id=doc_id,
                matched_doc_id=match.matched_doc_id,
                confidence=match.confidence,
                new_is_newer=match.new_is_newer,
                detected_version=match.detected_version,
            )

            if match.new_is_newer:
                # 常规情况：上传的文档比已有的更新
                await self._link_version(doc_id, match.matched_doc_id)
                is_doc_latest = True
            else:
                # 反向情况：上传的文档其实比已有的更旧
                await self._link_as_older_version(
                    doc_id, match.matched_doc_id, match.detected_version
                )
                is_doc_latest = False

            # 异步触发差异计算（不阻塞主流程）
            # 保存 task 引用到 set，防止被 GC 回收
            if match.new_is_newer:
                old_id, new_id = match.matched_doc_id, doc_id
            else:
                old_id, new_id = doc_id, match.matched_doc_id
            task = asyncio.create_task(
                self._compute_diff_background(old_id, new_id)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

            return is_doc_latest

        except Exception as e:
            logger.warning(
                "Version detection failed (non-fatal)",
                doc_id=doc_id,
                error=str(e),
            )
            return True  # 出错时保守处理，当作最新版

    async def _link_version(self, new_doc_id: str, old_doc_id: str):
        """建立版本链：新文档指向旧文档，旧文档标记为 superseded（上传的更新）"""
        async with get_db_session() as session:
            # 获取旧文档的版本号
            result = await session.execute(
                text("SELECT version_number FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": old_doc_id},
            )
            row = result.fetchone()
            old_version = row[0] if row else "v1.0"

            # 计算新版本号
            new_version = self._increment_version(old_version)

            # 更新新文档: 设置 parent_version_id 和版本号
            await session.execute(
                text("""
                    UPDATE documents
                    SET parent_version_id = :parent_id,
                        version_number = :version
                    WHERE doc_id = :doc_id
                """),
                {
                    "doc_id": new_doc_id,
                    "parent_id": old_doc_id,
                    "version": new_version,
                },
            )

            # 更新旧文档: 标记为 superseded
            await session.execute(
                text("""
                    UPDATE documents
                    SET is_latest = FALSE,
                        version_status = 'superseded',
                        superseded_at = NOW()
                    WHERE doc_id = :doc_id
                """),
                {"doc_id": old_doc_id},
            )

        # 更新旧文档 chunks 在 Qdrant/ES 中的 is_latest 标记
        await self._mark_chunks_not_latest(old_doc_id)

        logger.info(
            "Version link established (uploaded is newer)",
            old_doc_id=old_doc_id,
            new_doc_id=new_doc_id,
            old_version=old_version,
            new_version=new_version,
        )

    async def _link_as_older_version(
        self,
        uploaded_doc_id: str,
        existing_doc_id: str,
        detected_version: str | None = None,
    ):
        """
        反向版本链：上传的文档其实比已有文档更旧。

        将上传文档插入到版本链中作为已有文档的父版本：
        - 已有文档的 parent_version_id 指向上传文档
        - 上传文档标记为 is_latest=FALSE, superseded
        - 已有文档保持 is_latest=TRUE
        """
        async with get_db_session() as session:
            # 获取已有文档的信息
            result = await session.execute(
                text("""
                    SELECT version_number, parent_version_id
                    FROM documents WHERE doc_id = :doc_id
                """),
                {"doc_id": existing_doc_id},
            )
            row = result.fetchone()
            existing_version = row[0] if row else "v1.0"
            existing_parent_id = str(row[1]) if row and row[1] else None

            # 计算上传文档的版本号
            if detected_version:
                older_version = detected_version
            else:
                older_version = self._decrement_version(existing_version)

            # 上传文档: 标记为旧版本，继承已有文档的旧 parent
            await session.execute(
                text("""
                    UPDATE documents
                    SET parent_version_id = :old_parent_id,
                        version_number = :version,
                        is_latest = FALSE,
                        version_status = 'superseded',
                        superseded_at = NOW()
                    WHERE doc_id = :doc_id
                """),
                {
                    "doc_id": uploaded_doc_id,
                    "old_parent_id": existing_parent_id,
                    "version": older_version,
                },
            )

            # 已有文档: 将 parent 指向新上传的旧版本
            await session.execute(
                text("""
                    UPDATE documents
                    SET parent_version_id = :new_parent_id
                    WHERE doc_id = :doc_id
                """),
                {
                    "doc_id": existing_doc_id,
                    "new_parent_id": uploaded_doc_id,
                },
            )

        logger.info(
            "Version link established (uploaded is older)",
            uploaded_doc_id=uploaded_doc_id,
            existing_doc_id=existing_doc_id,
            uploaded_version=older_version,
            existing_version=existing_version,
        )

    async def _mark_chunks_not_latest(self, doc_id: str):
        """将某个文档的所有 chunks 在 Qdrant 和 ES 中标记为非最新"""
        # 获取该文档的所有 chunk_ids
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT chunk_id FROM chunks WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            chunk_ids = [str(row[0]) for row in result.fetchall()]

        if not chunk_ids:
            return

        # 更新 Qdrant
        try:
            from qdrant_client.models import PointIdsList
            qdrant = get_qdrant_client()
            await qdrant.set_payload(
                collection_name=settings.qdrant_collection_name,
                payload={"is_latest": False},
                points=PointIdsList(points=chunk_ids),
            )
        except Exception as e:
            logger.warning("Failed to update Qdrant is_latest", error=str(e))

        # 更新 Elasticsearch
        try:
            es = get_es_client()
            await es.update_by_query(
                index=settings.es_index_name,
                body={
                    "script": {
                        "source": "ctx._source.is_latest = false",
                        "lang": "painless",
                    },
                    "query": {"term": {"doc_id": doc_id}},
                },
            )
        except Exception as e:
            logger.warning("Failed to update ES is_latest", error=str(e))

    def _increment_version(self, version: str) -> str:
        """v1.0 -> v2.0, v2.3 -> v3.0, etc."""
        try:
            v = version.lstrip("v")
            parts = v.split(".")
            major = int(parts[0]) + 1
            return f"v{major}.0"
        except (ValueError, IndexError):
            return "v2.0"

    def _decrement_version(self, version: str) -> str:
        """v2.0 -> v1.0, v3.0 -> v2.0, etc. 最低为 v1.0"""
        try:
            v = version.lstrip("v")
            parts = v.split(".")
            major = max(int(parts[0]) - 1, 1)
            return f"v{major}.0"
        except (ValueError, IndexError):
            return "v1.0"

    async def _compute_diff_background(self, old_doc_id: str, new_doc_id: str):
        """后台计算版本差异（不阻塞主流程）"""
        try:
            await diff_engine.compute_full_diff(old_doc_id, new_doc_id)
            logger.info(
                "Background diff computation completed",
                old_doc_id=old_doc_id,
                new_doc_id=new_doc_id,
            )
        except Exception as e:
            logger.error(
                "Background diff computation failed",
                old_doc_id=old_doc_id,
                new_doc_id=new_doc_id,
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────
    # 摘要生成与上下文增强 (Phase 2)
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_summaries_and_metadata(
        self, chunks: list[Chunk], doc_id: str, title: str
    ) -> tuple[str, list[Chunk]]:
        """
        生成章节摘要和整体文档摘要，存入 DB，并以新 Chunk 返回。
        """
        logger.info("Generating summaries", doc_id=doc_id)
        
        # 提取 unique chapters
        sections = {}
        for c in chunks:
            if c.chunk_type == ChunkType.TEXT and c.section_path:
                sections.setdefault(c.section_path, []).append(c)

        section_summaries_text_parts = []
        summary_chunks = []

        # 1. 章节级摘要
        for section_path, section_chunks in sections.items():
            content = "\n".join(c.content for c in section_chunks)
            summary_text, key_points = await generate_section_summary(title, section_path, content)
            
            if summary_text:
                await self._store_section_summary(doc_id, section_path, summary_text, key_points)
                
                # 创建章节摘要 Chunk
                sc = Chunk(
                    doc_id=doc_id,
                    doc_title=title,
                    section_path=section_path,
                    chunk_type=ChunkType.SECTION_SUMMARY,
                    content=summary_text,
                )
                summary_chunks.append(sc)
                section_summaries_text_parts.append(f"【{section_path}】\n{summary_text}")

        # 2. 文档级摘要及实体提取
        doc_summary = ""
        if section_summaries_text_parts:
            combined_section_summaries = "\n\n".join(section_summaries_text_parts)
            doc_summary, key_entities, detected_doc_type = await generate_doc_summary_and_entities(
                title, combined_section_summaries
            )
            
            if doc_summary:
                await self._update_doc_summary_and_entities(doc_id, doc_summary, key_entities)
                
                # 自动设置 doc_type（如果上传时未指定）
                if detected_doc_type:
                    async with get_db_session() as session:
                        await session.execute(
                            text("UPDATE documents SET doc_type = :dt WHERE doc_id = :did AND doc_type IS NULL"),
                            {"dt": detected_doc_type, "did": doc_id},
                        )
                        await session.commit()
                    logger.info("Auto-detected doc_type", doc_type=detected_doc_type)

                # 创建文档摘要 Chunk
                dc = Chunk(
                    doc_id=doc_id,
                    doc_title=title,
                    chunk_type=ChunkType.DOC_SUMMARY,
                    content=doc_summary,
                )
                summary_chunks.append(dc)

        return doc_summary, summary_chunks

    async def _add_contextual_descriptions(self, chunks: list[Chunk], doc_title: str, doc_summary: str):
        """
        为常规文本 chunk 补充文档上下文描述，以提升混合检索精度。
        """
        if not doc_summary:
            return
            
        logger.info("Adding contextual descriptions", chunks_count=len(chunks))
        
        # 为了速度可以考虑并发执行
        async def process_chunk(chunk: Chunk):
            if chunk.chunk_type == ChunkType.TEXT:
                desc = await generate_contextual_description(
                    doc_title, doc_summary, chunk.section_path, chunk.content
                )
                if desc:
                    # 按照架构设计，把上下文加在内容开头
                    chunk.content = f"{desc}\n\n{chunk.content}"

        # 限制并发度以避免 API 请求过多
        semaphore = asyncio.Semaphore(10)
        async def bounded_process_chunk(chunk):
            async with semaphore:
                await process_chunk(chunk)

        await asyncio.gather(*(bounded_process_chunk(c) for c in chunks))

    # ─────────────────────────────────────────────────────────────────────
    # MinIO 操作
    # ─────────────────────────────────────────────────────────────────────

    async def _upload_to_minio(
        self, file_path: str, doc_id: str, original_filename: str
    ) -> str:
        """上传原文到 MinIO（同步 SDK，通过 executor 避免阻塞事件循环）"""
        import functools

        minio = get_minio_client()
        bucket = settings.minio_bucket_documents
        object_name = f"{doc_id}/{original_filename}"
        loop = asyncio.get_running_loop()

        def _sync_upload():
            if not minio.bucket_exists(bucket):
                minio.make_bucket(bucket)
            minio.fput_object(bucket, object_name, file_path)

        await loop.run_in_executor(None, _sync_upload)

        logger.info(
            "File uploaded to MinIO",
            bucket=bucket,
            object_name=object_name,
        )
        return f"{bucket}/{object_name}"

    # ─────────────────────────────────────────────────────────────────────
    # 嵌入计算
    # ─────────────────────────────────────────────────────────────────────

    def _compute_embeddings(self, chunks: list[Chunk]) -> list[list[float]]:
        """批量计算 chunk 嵌入向量"""
        texts = [chunk.content for chunk in chunks]

        logger.info("Computing embeddings", chunk_count=len(texts))
        embeddings = encode_texts(texts, show_progress=True)
        logger.info("Embeddings computed", count=len(embeddings))

        return embeddings

    # ─────────────────────────────────────────────────────────────────────
    # Qdrant 存储
    # ─────────────────────────────────────────────────────────────────────

    async def _store_to_qdrant(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        doc_id: str,
        is_latest: bool = True,
    ):
        """将 chunk 向量和 payload 存入 Qdrant"""
        from qdrant_client.models import PointStruct

        qdrant = get_qdrant_client()
        collection = settings.qdrant_collection_name

        points = []
        for chunk, embedding in zip(chunks, embeddings):
            point = PointStruct(
                id=chunk.chunk_id,
                vector=embedding,
                payload={
                    "doc_id": doc_id,
                    "doc_title": chunk.doc_title,
                    "section_path": chunk.section_path,
                    "page_numbers": chunk.page_numbers,
                    "chunk_index": chunk.chunk_index,
                    "chunk_type": chunk.chunk_type.value,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "group_id": chunk.group_id,
                    "department": chunk.department,
                    "is_latest": is_latest,
                },
            )
            points.append(point)

        # 分批上传（每批 100 个点）
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            await qdrant.upsert(
                collection_name=collection,
                points=batch,
            )

        logger.info(
            "Chunks stored in Qdrant",
            doc_id=doc_id,
            point_count=len(points),
            is_latest=is_latest,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Elasticsearch 存储
    # ─────────────────────────────────────────────────────────────────────

    async def _store_to_elasticsearch(
        self, chunks: list[Chunk], doc_id: str, is_latest: bool = True
    ):
        """将 chunk 全文批量存入 Elasticsearch（用于 BM25 检索）"""
        from datetime import timezone
        from elasticsearch.helpers import async_bulk

        es = get_es_client()
        index = settings.es_index_name
        now = datetime.now(timezone.utc).isoformat()

        actions = []
        for chunk in chunks:
            actions.append({
                "_index": index,
                "_id": chunk.chunk_id,
                "_source": {
                    "doc_id": doc_id,
                    "doc_title": chunk.doc_title,
                    "section_path": chunk.section_path,
                    "page_numbers": chunk.page_numbers,
                    "chunk_index": chunk.chunk_index,
                    "chunk_type": chunk.chunk_type.value,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "group_id": chunk.group_id,
                    "department": chunk.department,
                    "is_latest": is_latest,
                    "created_at": now,
                },
            })

        if actions:
            await async_bulk(es, actions)

        # 刷新索引使数据立即可搜索
        await es.indices.refresh(index=index)

        logger.info(
            "Chunks stored in Elasticsearch",
            doc_id=doc_id,
            chunk_count=len(chunks),
            is_latest=is_latest,
        )

    # ─────────────────────────────────────────────────────────────────────
    # 文档删除
    # ─────────────────────────────────────────────────────────────────────

    async def delete_document(self, doc_id: str):
        """
        删除文档及其所有关联数据
        (PostgreSQL chunks, Qdrant points, ES docs, MinIO files)
        """
        logger.info("Deleting document", doc_id=doc_id)

        # 1. 获取该文档的所有 chunk_id
        chunk_ids = []
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT chunk_id FROM chunks WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            chunk_ids = [str(row[0]) for row in result.fetchall()]

        # 2. 从 Qdrant 删除
        if chunk_ids:
            qdrant = get_qdrant_client()
            from qdrant_client.models import PointIdsList
            await qdrant.delete(
                collection_name=settings.qdrant_collection_name,
                points_selector=PointIdsList(points=chunk_ids),
            )

        # 3. 从 Elasticsearch 删除
        es = get_es_client()
        await es.delete_by_query(
            index=settings.es_index_name,
            body={"query": {"term": {"doc_id": doc_id}}},
        )

        # 4. 从 MinIO 删除（同步 SDK，通过 executor 避免阻塞）
        try:
            loop = asyncio.get_running_loop()
            minio = get_minio_client()
            bucket = settings.minio_bucket_documents

            def _sync_delete():
                objects = minio.list_objects(bucket, prefix=f"{doc_id}/", recursive=True)
                for obj in objects:
                    minio.remove_object(bucket, obj.object_name)

            await loop.run_in_executor(None, _sync_delete)
        except Exception as e:
            logger.warning("Failed to delete from MinIO", doc_id=doc_id, error=str(e))

        # 5. 从 PostgreSQL 删除（CASCADE 会自动删除 chunks）
        async with get_db_session() as session:
            await session.execute(
                text("DELETE FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )

        logger.info("Document deleted", doc_id=doc_id, chunks_deleted=len(chunk_ids))


# 全局单例
ingestion_pipeline = IngestionPipeline()
