"""
DocAI Platform - 认证 API (Phase 5)
登录、注册、用户管理、权限管理、审计日志查询
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from app.auth.audit import audit_log
from app.auth.dependencies import (
    get_current_user,
    require_admin,
    require_auth,
)
from app.auth.jwt import create_access_token
from app.auth.models import (
    AuditLogEntry,
    AuditLogListResponse,
    CurrentUser,
    PermissionGrant,
    PermissionResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.auth.password import hash_password, verify_password
from app.core.infrastructure import get_db_session
from config.settings import settings

logger = structlog.get_logger()

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# 登录 / 注册
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, request: Request):
    """用户登录，返回 JWT Token"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT user_id, username, email, password_hash, display_name,
                       department, role, is_active
                FROM users WHERE username = :username
            """),
            {"username": data.username},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user_id, username, email, password_hash, display_name, department, role, is_active = row

    if not is_active:
        raise HTTPException(status_code=401, detail="账号已被禁用")

    if not verify_password(data.password, password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 生成 Token
    token = create_access_token(
        user_id=str(user_id), role=role, username=username
    )

    user_response = UserResponse(
        user_id=str(user_id),
        username=username,
        email=email,
        display_name=display_name,
        department=department,
        role=role,
        is_active=is_active,
    )

    # 审计日志
    await audit_log(
        action="login",
        user=CurrentUser(user_id=str(user_id), username=username, role=role, department=department),
        request=request,
    )

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_hours * 3600,
        user=user_response,
    )


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(data: UserCreate, request: Request):
    """
    用户注册。

    注意: 默认角色为 viewer。
    如需创建 admin/editor 用户，需要已有 admin 操作。
    """
    # 如果指定了非 viewer 角色，需要特殊处理（可选的管理员创建）
    if data.role not in ("viewer", "restricted"):
        raise HTTPException(
            status_code=400,
            detail="注册时只能选择 viewer 或 restricted 角色。如需更高权限请联系管理员。"
        )

    user_id = str(uuid.uuid4())
    hashed = hash_password(data.password)

    try:
        async with get_db_session() as session:
            # 检查用户名和邮箱唯一性
            check = await session.execute(
                text("SELECT username FROM users WHERE username = :u OR email = :e"),
                {"u": data.username, "e": data.email},
            )
            if check.fetchone():
                raise HTTPException(status_code=409, detail="用户名或邮箱已存在")

            await session.execute(
                text("""
                    INSERT INTO users (user_id, username, email, password_hash,
                                       display_name, department, role)
                    VALUES (:uid, :username, :email, :hash,
                            :display_name, :department, :role)
                """),
                {
                    "uid": user_id,
                    "username": data.username,
                    "email": data.email,
                    "hash": hashed,
                    "display_name": data.display_name or data.username,
                    "department": data.department,
                    "role": data.role,
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Registration failed", error=str(e))
        raise HTTPException(status_code=500, detail="注册失败")

    await audit_log(
        action="register",
        user=CurrentUser(user_id=user_id, username=data.username, role=data.role),
        request=request,
    )

    return UserResponse(
        user_id=user_id,
        username=data.username,
        email=data.email,
        display_name=data.display_name or data.username,
        department=data.department,
        role=data.role,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 当前用户
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser = Depends(require_auth)):
    """获取当前登录用户信息"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT user_id, username, email, display_name, department,
                       role, is_active, created_at
                FROM users WHERE user_id = :uid
            """),
            {"uid": current_user.user_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserResponse(
        user_id=str(row[0]),
        username=row[1],
        email=row[2],
        display_name=row[3],
        department=row[4],
        role=row[5],
        is_active=row[6],
        created_at=row[7],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 用户管理 (Admin)
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: CurrentUser = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
):
    """获取用户列表（仅管理员）"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT user_id, username, email, display_name, department,
                       role, is_active, created_at
                FROM users
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        rows = result.fetchall()

    return [
        UserResponse(
            user_id=str(row[0]),
            username=row[1],
            email=row[2],
            display_name=row[3],
            department=row[4],
            role=row[5],
            is_active=row[6],
            created_at=row[7],
        )
        for row in rows
    ]


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdate,
    admin: CurrentUser = Depends(require_admin),
    request: Request = None,
):
    """更新用户信息（仅管理员）"""
    updates = []
    params: dict = {"uid": user_id}

    if data.display_name is not None:
        updates.append("display_name = :display_name")
        params["display_name"] = data.display_name
    if data.department is not None:
        updates.append("department = :department")
        params["department"] = data.department
    if data.role is not None:
        updates.append("role = :role")
        params["role"] = data.role
    if data.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = data.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="未提供任何更新字段")

    set_clause = ", ".join(updates)

    async with get_db_session() as session:
        result = await session.execute(
            text(f"UPDATE users SET {set_clause} WHERE user_id = :uid RETURNING user_id"),
            params,
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="用户不存在")

    await audit_log(
        action="update_user",
        user=admin,
        resource_type="user",
        resource_id=user_id,
        details=data.model_dump(exclude_none=True),
        request=request,
    )

    return await _get_user_by_id(user_id)


# ═══════════════════════════════════════════════════════════════════════════
# 权限管理 (Admin)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/permissions", response_model=PermissionResponse, status_code=201)
async def grant_permission(
    data: PermissionGrant,
    admin: CurrentUser = Depends(require_admin),
    request: Request = None,
):
    """授予用户文档权限（仅管理员）"""
    if not data.doc_id and not data.group_id and not data.department:
        raise HTTPException(status_code=400, detail="必须指定 doc_id、group_id 或 department 之一")

    perm_id = str(uuid.uuid4())

    async with get_db_session() as session:
        await session.execute(
            text("""
                INSERT INTO document_permissions
                (perm_id, user_id, doc_id, group_id, department, permission, granted_by)
                VALUES (:pid, :uid, :did, :gid, :dept, :perm, :granted_by)
            """),
            {
                "pid": perm_id,
                "uid": data.user_id,
                "did": data.doc_id,
                "gid": data.group_id,
                "dept": data.department,
                "perm": data.permission,
                "granted_by": admin.user_id,
            },
        )

    await audit_log(
        action="grant_permission",
        user=admin,
        resource_type="permission",
        resource_id=perm_id,
        details=data.model_dump(),
        request=request,
    )

    return PermissionResponse(
        perm_id=perm_id,
        user_id=data.user_id,
        doc_id=data.doc_id,
        group_id=data.group_id,
        department=data.department,
        permission=data.permission,
        granted_by=admin.user_id,
    )


@router.get("/permissions/{user_id}", response_model=list[PermissionResponse])
async def get_user_permissions(
    user_id: str,
    _admin: CurrentUser = Depends(require_admin),
):
    """查看某用户的权限列表（仅管理员）"""
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT perm_id, user_id, doc_id, group_id, department,
                       permission, granted_by, created_at
                FROM document_permissions
                WHERE user_id = :uid
                ORDER BY created_at DESC
            """),
            {"uid": user_id},
        )
        rows = result.fetchall()

    return [
        PermissionResponse(
            perm_id=str(row[0]),
            user_id=str(row[1]),
            doc_id=str(row[2]) if row[2] else None,
            group_id=str(row[3]) if row[3] else None,
            department=row[4],
            permission=row[5],
            granted_by=str(row[6]) if row[6] else None,
            created_at=row[7],
        )
        for row in rows
    ]


@router.delete("/permissions/{perm_id}")
async def revoke_permission(
    perm_id: str,
    admin: CurrentUser = Depends(require_admin),
    request: Request = None,
):
    """撤销权限（仅管理员）"""
    async with get_db_session() as session:
        result = await session.execute(
            text("DELETE FROM document_permissions WHERE perm_id = :pid RETURNING perm_id"),
            {"pid": perm_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="权限记录不存在")

    await audit_log(
        action="revoke_permission",
        user=admin,
        resource_type="permission",
        resource_id=perm_id,
        request=request,
    )

    return {"status": "ok", "message": "权限已撤销"}


# ═══════════════════════════════════════════════════════════════════════════
# 审计日志查询 (Admin)
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def get_audit_logs(
    _admin: CurrentUser = Depends(require_admin),
    action: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """查询审计日志（仅管理员）"""
    conditions = []
    params: dict = {"limit": limit, "offset": offset}

    if action:
        conditions.append("action = :action")
        params["action"] = action
    if user_id:
        conditions.append("user_id = :user_id")
        params["user_id"] = user_id

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    async with get_db_session() as session:
        result = await session.execute(
            text(f"""
                SELECT log_id, user_id, username, action, resource_type,
                       resource_id, details, ip_address, created_at
                FROM audit_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()

        count_result = await session.execute(
            text(f"SELECT COUNT(*) FROM audit_logs {where_clause}"),
            params,
        )
        total = count_result.scalar() or 0

    logs = [
        AuditLogEntry(
            log_id=str(row[0]),
            user_id=str(row[1]) if row[1] else None,
            username=row[2],
            action=row[3],
            resource_type=row[4],
            resource_id=row[5],
            details=row[6] if isinstance(row[6], dict) else {},
            ip_address=row[7],
            created_at=row[8],
        )
        for row in rows
    ]

    return AuditLogListResponse(logs=logs, total=total)


# ─── 内部工具函数 ────────────────────────────────────────────────────────

async def _get_user_by_id(user_id: str) -> UserResponse:
    async with get_db_session() as session:
        result = await session.execute(
            text("""
                SELECT user_id, username, email, display_name, department,
                       role, is_active, created_at
                FROM users WHERE user_id = :uid
            """),
            {"uid": user_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserResponse(
        user_id=str(row[0]),
        username=row[1],
        email=row[2],
        display_name=row[3],
        department=row[4],
        role=row[5],
        is_active=row[6],
        created_at=row[7],
    )
