"""
DocAI Platform - 版本管理 API
版本历史查询、差异对比、手动关联、状态变更
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.infrastructure import get_db_session, get_es_client, get_qdrant_client
from app.core.models import (
    VersionDiffResponse,
    VersionHistoryResponse,
    VersionInfoResponse,
    VersionLinkRequest,
    VersionStatusUpdate,
)
from app.versioning.diff_engine import diff_engine
from config.settings import settings

logger = structlog.get_logger()

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# 版本历史
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{doc_id}/history", response_model=VersionHistoryResponse)
async def get_version_history(doc_id: str):
    """
    获取文档的完整版本历史

    沿 parent_version_id 链向上追溯所有祖先版本，
    同时向下查找所有子版本，组成完整版本树。
    """
    # 获取当前文档信息
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT doc_id, title, version_number, version_status,
                       is_latest, parent_version_id, effective_date,
                       created_at, chunk_count
                FROM documents
                WHERE doc_id = :doc_id
            """),
            {"doc_id": doc_id},
        )
        current = result.fetchone()

    if not current:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    # 收集整个版本链
    all_versions = []
    visited = set()

    # 向上追溯祖先
    await _collect_ancestors(doc_id, all_versions, visited)
    # 向下查找所有后代
    await _collect_descendants(doc_id, all_versions, visited)
    # 加入自身（如果尚未被收集）
    if doc_id not in visited:
        all_versions.append(_row_to_version_info(current))

    # 按 created_at 排序
    all_versions.sort(key=lambda v: v.created_at or "", reverse=False)

    return VersionHistoryResponse(
        doc_id=doc_id,
        title=str(current[1]),
        versions=all_versions,
    )


async def _collect_ancestors(
    doc_id: str,
    versions: list[VersionInfoResponse],
    visited: set[str],
):
    """递归收集祖先版本"""
    current_id = doc_id
    while current_id and current_id not in visited:
        visited.add(current_id)
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT doc_id, title, version_number, version_status,
                           is_latest, parent_version_id, effective_date,
                           created_at, chunk_count
                    FROM documents
                    WHERE doc_id = :doc_id
                """),
                {"doc_id": current_id},
            )
            row = result.fetchone()

        if not row:
            break
        versions.append(_row_to_version_info(row))
        current_id = str(row[5]) if row[5] else None


async def _collect_descendants(
    doc_id: str,
    versions: list[VersionInfoResponse],
    visited: set[str],
):
    """查找所有直接子版本（以当前文档为 parent 的文档）"""
    # 先找到版本链的根节点
    root_id = doc_id
    while True:
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT parent_version_id FROM documents
                    WHERE doc_id = :doc_id AND parent_version_id IS NOT NULL
                """),
                {"doc_id": root_id},
            )
            row = result.fetchone()
        if row and row[0]:
            root_id = str(row[0])
            if root_id in visited:
                break
        else:
            break

    # 从根往下 BFS 收集
    queue = [root_id]
    while queue:
        pid = queue.pop(0)
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT doc_id, title, version_number, version_status,
                           is_latest, parent_version_id, effective_date,
                           created_at, chunk_count
                    FROM documents
                    WHERE parent_version_id = :parent_id
                """),
                {"parent_id": pid},
            )
            rows = result.fetchall()

        for row in rows:
            child_id = str(row[0])
            if child_id not in visited:
                visited.add(child_id)
                versions.append(_row_to_version_info(row))
                queue.append(child_id)


def _row_to_version_info(row) -> VersionInfoResponse:
    return VersionInfoResponse(
        doc_id=str(row[0]),
        title=row[1],
        version_number=row[2] or "v1.0",
        version_status=row[3] or "active",
        is_latest=bool(row[4]),
        parent_version_id=str(row[5]) if row[5] else None,
        effective_date=str(row[6]) if row[6] else None,
        created_at=row[7],
        chunk_count=row[8] or 0,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 版本差异对比
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/{doc_id}/diff/{other_doc_id}", response_model=VersionDiffResponse)
async def get_version_diff(doc_id: str, other_doc_id: str):
    """
    获取两个版本之间的差异

    如果已有缓存则直接返回，否则实时计算。
    """
    # 验证两个文档都存在
    for did in [doc_id, other_doc_id]:
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": did},
            )
            if not result.fetchone():
                raise HTTPException(status_code=404, detail=f"文档不存在: {did}")

    result = await diff_engine.compute_full_diff(doc_id, other_doc_id)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 手动版本关联
# ═══════════════════════════════════════════════════════════════════════════


@router.put("/{doc_id}/link")
async def link_version(doc_id: str, request: VersionLinkRequest):
    """
    手动将文档关联为另一个文档的新版本

    - doc_id: 新版本的文档 ID
    - parent_version_id: 旧版本的文档 ID
    """
    parent_id = request.parent_version_id

    # 验证两个文档存在
    for did in [doc_id, parent_id]:
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": did},
            )
            if not result.fetchone():
                raise HTTPException(status_code=404, detail=f"文档不存在: {did}")

    # 使用 pipeline 中相同的链接逻辑
    from app.ingestion.pipeline import ingestion_pipeline
    await ingestion_pipeline._link_version(doc_id, parent_id)

    # 触发 diff 计算
    try:
        await diff_engine.compute_full_diff(parent_id, doc_id)
    except Exception as e:
        logger.warning("Diff computation after manual link failed", error=str(e))

    return {"status": "ok", "message": f"文档 {doc_id} 已关联为 {parent_id} 的新版本"}


# ═══════════════════════════════════════════════════════════════════════════
# 版本状态变更
# ═══════════════════════════════════════════════════════════════════════════


@router.put("/{doc_id}/status")
async def update_version_status(doc_id: str, request: VersionStatusUpdate):
    """
    修改版本状态 (active / superseded / archived)
    """
    valid_statuses = {"draft", "active", "superseded", "archived"}
    if request.version_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"无效状态: {request.version_status}. 可选: {valid_statuses}",
        )

    async with get_db_session() as session:
        result = await session.execute(
            text("SELECT doc_id FROM documents WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        is_latest = request.version_status == "active"

        await session.execute(
            text("""
                UPDATE documents
                SET version_status = :status,
                    is_latest = :is_latest
                WHERE doc_id = :doc_id
            """),
            {
                "doc_id": doc_id,
                "status": request.version_status,
                "is_latest": is_latest,
            },
        )

    # 更新 Qdrant/ES 中的 is_latest
    try:
        chunk_ids = []
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT chunk_id FROM chunks WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            chunk_ids = [str(row[0]) for row in result.fetchall()]

        if chunk_ids:
            from qdrant_client.models import PointIdsList

            qdrant = get_qdrant_client()
            await qdrant.set_payload(
                collection_name=settings.qdrant_collection_name,
                payload={"is_latest": is_latest},
                points=PointIdsList(points=chunk_ids),
            )

            es = get_es_client()
            await es.update_by_query(
                index=settings.es_index_name,
                body={
                    "script": {
                        "source": f"ctx._source.is_latest = {str(is_latest).lower()}",
                        "lang": "painless",
                    },
                    "query": {"term": {"doc_id": doc_id}},
                },
            )
    except Exception as e:
        logger.warning("Failed to sync is_latest to vector stores", error=str(e))

    return {
        "status": "ok",
        "message": f"文档 {doc_id} 状态已更新为 {request.version_status}",
    }
