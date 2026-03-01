"""
DocAI Platform - 权限检查逻辑 (Phase 5)
核心: 获取用户可访问的文档 ID 列表，用于检索层过滤
"""

from __future__ import annotations

import structlog
from sqlalchemy import text

from app.auth.models import CurrentUser
from app.core.infrastructure import get_db_session

logger = structlog.get_logger()


async def get_accessible_doc_ids(user: CurrentUser | None) -> list[str] | None:
    """
    获取用户可访问的文档 ID 列表。

    Returns:
        - None: 表示可访问全部文档（admin 角色 或 未启用认证）
        - list[str]: 用户被授权访问的文档 ID 列表

    权限来源（OR 关系，任一满足即可访问）:
    1. 用户角色为 admin → 全部
    2. 文档 visibility = 'public' → 全部用户可见
    3. 文档 visibility = 'department' 且用户部门匹配
    4. 用户是文档 owner
    5. document_permissions 表中有对该文档的授权
    6. document_permissions 表中有对该文档所属组的授权
    7. document_permissions 表中有对该文档部门的授权
    """
    # 未认证 or admin → 不做过滤
    if user is None or user.role == "admin":
        return None

    try:
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    -- 1. public 文档
                    SELECT doc_id FROM documents
                    WHERE visibility = 'public'
                      AND processing_status = 'ready'

                    UNION

                    -- 2. 同部门文档 (visibility = 'department')
                    SELECT doc_id FROM documents
                    WHERE visibility = 'department'
                      AND department = :department
                      AND processing_status = 'ready'

                    UNION

                    -- 3. 用户自己上传的文档
                    SELECT doc_id FROM documents
                    WHERE owner_id = :user_id
                      AND processing_status = 'ready'

                    UNION

                    -- 4. 直接按 doc_id 授权
                    SELECT dp.doc_id FROM document_permissions dp
                    WHERE dp.user_id = :user_id
                      AND dp.doc_id IS NOT NULL

                    UNION

                    -- 5. 按 group_id 授权 → 展开为 doc_id
                    SELECT d.doc_id FROM document_permissions dp
                    JOIN documents d ON d.group_id = dp.group_id
                    WHERE dp.user_id = :user_id
                      AND dp.group_id IS NOT NULL
                      AND d.processing_status = 'ready'

                    UNION

                    -- 6. 按 department 授权 → 展开为 doc_id
                    SELECT d.doc_id FROM document_permissions dp
                    JOIN documents d ON d.department = dp.department
                    WHERE dp.user_id = :user_id
                      AND dp.department IS NOT NULL
                      AND d.processing_status = 'ready'
                """),
                {
                    "user_id": user.user_id,
                    "department": user.department or "",
                },
            )
            doc_ids = [str(row[0]) for row in result.fetchall()]

        logger.info(
            "User accessible docs resolved",
            user=user.username,
            count=len(doc_ids),
        )
        return doc_ids

    except Exception as e:
        logger.error("Failed to resolve user permissions", error=str(e))
        # 权限查询失败时，安全起见返回空列表（不允许访问任何文档）
        return []


async def check_document_access(
    user: CurrentUser | None, doc_id: str, required_permission: str = "read"
) -> bool:
    """
    检查用户是否有某个具体文档的指定权限。

    Args:
        user: 当前用户
        doc_id: 文档 ID
        required_permission: 需要的权限级别 (read / write / admin)

    Returns:
        True: 有权限; False: 无权限
    """
    # 未认证 or admin → 全部通过
    if user is None or user.role == "admin":
        return True

    accessible = await get_accessible_doc_ids(user)
    if accessible is None:
        return True
    return doc_id in accessible


async def check_document_write_access(
    user: CurrentUser | None, doc_id: str
) -> bool:
    """检查用户是否有文档的写权限（上传/编辑/删除）"""
    if user is None or user.role == "admin":
        return True
    if user.role == "viewer" or user.role == "restricted":
        return False

    # editor 角色：检查文档 owner 或 write 权限
    try:
        async with get_db_session() as session:
            result = await session.execute(
                text("""
                    SELECT EXISTS (
                        -- owner
                        SELECT 1 FROM documents
                        WHERE doc_id = :doc_id AND owner_id = :user_id

                        UNION ALL

                        -- write/admin 权限
                        SELECT 1 FROM document_permissions
                        WHERE user_id = :user_id
                          AND (doc_id = :doc_id OR permission IN ('write', 'admin'))
                    )
                """),
                {"doc_id": doc_id, "user_id": user.user_id},
            )
            return result.scalar() or False
    except Exception as e:
        logger.error("Failed to check write access", error=str(e))
        return False
