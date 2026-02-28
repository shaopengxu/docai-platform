"""
DocAI Platform - 文档入库 Pipeline
完整流程：上传 → 解析 → 分块 → 嵌入 → 存储 (Qdrant + ES + PostgreSQL + MinIO)
"""

from __future__ import annotations

import io
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.embedding import encode_texts
from app.core.infrastructure import (
    get_db_session,
    get_es_client,
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
from config.settings import settings

logger = structlog.get_logger()


class IngestionPipeline:
    """文档入库完整 Pipeline"""

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
            # Step 0: 注册文档到 PostgreSQL (状态: pending)
            file_size = os.path.getsize(file_path)
            await self._register_document(
                doc_id=doc_id,
                title=Path(original_filename).stem,
                original_filename=original_filename,
                file_size=file_size,
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

            # Step 3.8: Contextual Retrieval 增强
            await self._add_contextual_descriptions(chunks, parsed_doc.title, doc_summary)

            # Step 4: 嵌入
            await self._update_status(doc_id, ProcessingStatus.EMBEDDING)
            embeddings = self._compute_embeddings(chunks)

            # Step 5: 存储到 Qdrant + ES + PostgreSQL
            await self._store_to_qdrant(chunks, embeddings, doc_id)
            await self._store_to_elasticsearch(chunks, doc_id)
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

    async def _register_document(
        self,
        doc_id: str,
        title: str,
        original_filename: str,
        file_size: int,
        doc_type: str | None,
        tags: list[str],
    ):
        """在 PostgreSQL 中注册新文档"""
        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO documents (
                        doc_id, title, original_filename, file_path,
                        file_size_bytes, doc_type, tags, processing_status
                    ) VALUES (
                        :doc_id, :title, :original_filename, '',
                        :file_size, :doc_type, :tags, 'pending'
                    )
                """),
                {
                    "doc_id": doc_id,
                    "title": title,
                    "original_filename": original_filename,
                    "file_size": file_size,
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
        """将 chunk 元数据存入 PostgreSQL"""
        async with get_db_session() as session:
            for chunk in chunks:
                await session.execute(
                    text("""
                        INSERT INTO chunks (
                            chunk_id, doc_id, section_path, page_numbers,
                            chunk_index, content, chunk_type, token_count,
                            qdrant_point_id, es_doc_id
                        ) VALUES (
                            :chunk_id, :doc_id, :section_path, :page_numbers,
                            :chunk_index, :content, :chunk_type, :token_count,
                            :qdrant_point_id, :es_doc_id
                        )
                    """),
                    {
                        "chunk_id": chunk.chunk_id,
                        "doc_id": doc_id,
                        "section_path": chunk.section_path,
                        "page_numbers": chunk.page_numbers,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "chunk_type": chunk.chunk_type.value,
                        "token_count": chunk.token_count,
                        "qdrant_point_id": chunk.chunk_id,  # 与 Qdrant point_id 一致
                        "es_doc_id": chunk.chunk_id,         # 与 ES doc_id 一致
                    },
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
            content = "\\n".join(c.content for c in section_chunks)
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
                section_summaries_text_parts.append(f"【{section_path}】\\n{summary_text}")

        # 2. 文档级摘要及实体提取
        doc_summary = ""
        if section_summaries_text_parts:
            combined_section_summaries = "\\n\\n".join(section_summaries_text_parts)
            doc_summary, key_entities = await generate_doc_summary_and_entities(
                title, combined_section_summaries
            )
            
            if doc_summary:
                await self._update_doc_summary_and_entities(doc_id, doc_summary, key_entities)
                
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
        import asyncio
        async def process_chunk(chunk: Chunk):
            if chunk.chunk_type == ChunkType.TEXT:
                desc = await generate_contextual_description(
                    doc_title, doc_summary, chunk.section_path, chunk.content
                )
                if desc:
                    # 按照架构设计，把上下文加在内容开头
                    chunk.content = f"{desc}\\n\\n{chunk.content}"

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
        """上传原文到 MinIO"""
        minio = get_minio_client()
        bucket = settings.minio_bucket_documents

        # 确保 bucket 存在
        if not minio.bucket_exists(bucket):
            minio.make_bucket(bucket)

        # 存储路径：documents/{doc_id}/{original_filename}
        ext = Path(original_filename).suffix
        object_name = f"{doc_id}/{original_filename}"

        minio.fput_object(bucket, object_name, file_path)

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
                    "is_latest": True,  # Phase 3 版本管理用
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
        )

    # ─────────────────────────────────────────────────────────────────────
    # Elasticsearch 存储
    # ─────────────────────────────────────────────────────────────────────

    async def _store_to_elasticsearch(self, chunks: list[Chunk], doc_id: str):
        """将 chunk 全文存入 Elasticsearch（用于 BM25 检索）"""
        es = get_es_client()
        index = settings.es_index_name

        for chunk in chunks:
            doc_body = {
                "doc_id": doc_id,
                "doc_title": chunk.doc_title,
                "section_path": chunk.section_path,
                "page_numbers": chunk.page_numbers,
                "chunk_index": chunk.chunk_index,
                "chunk_type": chunk.chunk_type.value,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "is_latest": True,
                "created_at": datetime.utcnow().isoformat(),
            }
            await es.index(
                index=index,
                id=chunk.chunk_id,
                document=doc_body,
            )

        # 刷新索引使数据立即可搜索
        await es.indices.refresh(index=index)

        logger.info(
            "Chunks stored in Elasticsearch",
            doc_id=doc_id,
            chunk_count=len(chunks),
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

        # 4. 从 MinIO 删除
        try:
            minio = get_minio_client()
            bucket = settings.minio_bucket_documents
            objects = minio.list_objects(bucket, prefix=f"{doc_id}/", recursive=True)
            for obj in objects:
                minio.remove_object(bucket, obj.object_name)
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
