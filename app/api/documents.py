"""
DocAI Platform - 文档管理 API
文档上传、列表、状态查询、删除
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from sqlalchemy import text

from app.core.infrastructure import get_db_session
from app.core.models import (
    DocumentGroupCreate,
    DocumentGroupResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUpdate,
)
from app.ingestion.pipeline import ingestion_pipeline
from config.settings import settings

logger = structlog.get_logger()

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# 文档上传
# ═══════════════════════════════════════════════════════════════════════════


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    tags: str | None = Form(default=None),
):
    """
    上传文档并启动异步处理

    - **file**: 文档文件 (PDF/Word/PPT/Excel/CSV/TXT/MD)
    - **doc_type**: 文档类型 (contract/report/policy/manual)
    - **tags**: 标签，逗号分隔

    上传后立即返回文档 ID，后台异步执行解析、分块、嵌入。
    通过 GET /api/v1/documents/{doc_id} 查询处理状态。
    """
    # 验证文件扩展名
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in settings.supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。支持: {', '.join(settings.supported_extensions)}",
        )

    # 验证文件大小
    content = await file.read()
    file_size = len(content)
    max_size = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大: {file_size / 1024 / 1024:.1f}MB，上限 {settings.max_file_size_mb}MB",
        )

    # 生成文档 ID
    doc_id = str(uuid.uuid4())

    # 解析标签
    tag_list = []
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # 保存到临时文件
    tmp_dir = tempfile.mkdtemp(prefix="docai_")
    tmp_path = os.path.join(tmp_dir, filename)
    with open(tmp_path, "wb") as f:
        f.write(content)

    logger.info(
        "Document upload received",
        doc_id=doc_id,
        filename=filename,
        file_size=file_size,
        doc_type=doc_type,
    )

    # 后台异步处理
    background_tasks.add_task(
        _process_document_task,
        file_path=tmp_path,
        original_filename=filename,
        doc_id=doc_id,
        doc_type=doc_type,
        tags=tag_list,
        tmp_dir=tmp_dir,
    )

    return DocumentResponse(
        doc_id=doc_id,
        title=Path(filename).stem,
        original_filename=filename,
        file_size_bytes=file_size,
        doc_type=doc_type,
        processing_status="pending",
    )


async def _process_document_task(
    file_path: str,
    original_filename: str,
    doc_id: str,
    doc_type: str | None,
    tags: list[str],
    tmp_dir: str,
):
    """后台文档处理任务"""
    try:
        await ingestion_pipeline.process_document(
            file_path=file_path,
            original_filename=original_filename,
            doc_id=doc_id,
            doc_type=doc_type,
            tags=tags,
        )
    except Exception as e:
        logger.error("Background document processing failed", doc_id=doc_id, error=str(e))
    finally:
        # 清理临时文件
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 文档列表
# ═══════════════════════════════════════════════════════════════════════════


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    doc_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    获取文档列表

    - **doc_type**: 按文档类型过滤
    - **status**: 按处理状态过滤 (pending/parsing/chunking/embedding/ready/error)
    - **limit**: 返回数量上限
    - **offset**: 偏移量（分页）
    """
    async with get_db_session() as session:
        # 构建查询条件
        conditions = []
        params: dict = {"limit": limit, "offset": offset}

        if doc_type:
            conditions.append("doc_type = :doc_type")
            params["doc_type"] = doc_type
        if status:
            conditions.append("processing_status = :status")
            params["status"] = status

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # 查询文档列表
        result = await session.execute(
            text(f"""
                SELECT doc_id, title, original_filename, file_size_bytes,
                       page_count, doc_type, processing_status, chunk_count, created_at
                FROM documents
                {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()

        # 查询总数
        count_result = await session.execute(
            text(f"SELECT COUNT(*) FROM documents {where_clause}"),
            params,
        )
        total = count_result.scalar() or 0

    documents = [
        DocumentResponse(
            doc_id=str(row[0]),
            title=row[1],
            original_filename=row[2],
            file_size_bytes=row[3],
            page_count=row[4],
            doc_type=row[5],
            processing_status=row[6],
            chunk_count=row[7] or 0,
            created_at=row[8],
        )
        for row in rows
    ]

    return DocumentListResponse(documents=documents, total=total)


# ═══════════════════════════════════════════════════════════════════════════
# 文档组管理 (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/groups", response_model=DocumentGroupResponse, status_code=201)
async def create_document_group(group: DocumentGroupCreate):
    """创建新的文档组"""
    group_id = str(uuid.uuid4())
    async with get_db_session() as session:
        await session.execute(
            text("""
                INSERT INTO document_groups (group_id, name, description)
                VALUES (:group_id, :name, :description)
            """),
            {
                "group_id": group_id,
                "name": group.name,
                "description": group.description,
            },
        )
    return DocumentGroupResponse(
        group_id=group_id,
        name=group.name,
        description=group.description,
    )


@router.get("/groups", response_model=list[DocumentGroupResponse])
async def list_document_groups():
    """获取所有文档组"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT group_id, name, description, created_at
                FROM document_groups
                ORDER BY created_at DESC
            """)
        )
        rows = result.fetchall()

    return [
        DocumentGroupResponse(
            group_id=str(row[0]),
            name=row[1],
            description=row[2],
            created_at=row[3],
        )
        for row in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 文档详情及元数据修改
# ═══════════════════════════════════════════════════════════════════════════


@router.put("/{doc_id}/metadata", response_model=DocumentResponse)
async def update_document_metadata(doc_id: str, update_data: DocumentUpdate):
    """更新文档的元数据（分配到组、打标签等）"""
    async with get_db_session() as session:
        # Check if doc exists
        result = await session.execute(
            text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        # Update fields dynamically
        updates = []
        params = {"doc_id": doc_id}
        if update_data.group_id is not None:
            updates.append("group_id = :group_id")
            params["group_id"] = update_data.group_id
        if update_data.tags is not None:
            updates.append("tags = :tags")
            params["tags"] = update_data.tags
        if update_data.department is not None:
            updates.append("department = :department")
            params["department"] = update_data.department

        if updates:
            set_clause = ", ".join(updates)
            await session.execute(
                text(f"UPDATE documents SET {set_clause} WHERE doc_id = :doc_id"),
                params,
            )

    return await get_document(doc_id)


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(doc_id: str):
    """获取单个文档的详细信息"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT doc_id, title, original_filename, file_size_bytes,
                       page_count, doc_type, processing_status, chunk_count, created_at
                FROM documents
                WHERE doc_id = :doc_id
            """),
            {"doc_id": doc_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    return DocumentResponse(
        doc_id=str(row[0]),
        title=row[1],
        original_filename=row[2],
        file_size_bytes=row[3],
        page_count=row[4],
        doc_type=row[5],
        processing_status=row[6],
        chunk_count=row[7] or 0,
        created_at=row[8],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 文档删除
# ═══════════════════════════════════════════════════════════════════════════


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """
    删除文档及其所有关联数据

    删除内容包括：PostgreSQL 记录、Qdrant 向量、Elasticsearch 索引、MinIO 原文
    """
    # 先检查文档是否存在
    async with get_db_session() as session:
        result = await session.execute(
            text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    await ingestion_pipeline.delete_document(doc_id)

    return {"status": "ok", "message": f"文档 {doc_id} 已删除"}


# ═══════════════════════════════════════════════════════════════════════════
# 文档 chunks 查看（调试用）
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: str, limit: int = 20, offset: int = 0):
    """获取文档的分块列表（调试用）"""
    async with get_db_session() as session:
        # 检查文档是否存在
        doc_result = await session.execute(
            text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        if not doc_result.fetchone():
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        result = await session.execute(
            text("""
                SELECT chunk_id, section_path, page_numbers, chunk_index,
                       chunk_type, token_count, content
                FROM chunks
                WHERE doc_id = :doc_id
                ORDER BY chunk_index
                LIMIT :limit OFFSET :offset
            """),
            {"doc_id": doc_id, "limit": limit, "offset": offset},
        )
        rows = result.fetchall()

        count_result = await session.execute(
            text("SELECT COUNT(*) FROM chunks WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        total = count_result.scalar() or 0

    chunks = [
        {
            "chunk_id": str(row[0]),
            "section_path": row[1],
            "page_numbers": row[2],
            "chunk_index": row[3],
            "chunk_type": row[4],
            "token_count": row[5],
            "content": row[6][:200] + "..." if len(row[6]) > 200 else row[6],
        }
        for row in rows
    ]

    return {"doc_id": doc_id, "chunks": chunks, "total": total}
