"""
DocAI Platform - FastAPI 认证依赖注入 (Phase 5)
提供 get_current_user / require_role 等可复用的依赖
"""

from __future__ import annotations

from functools import wraps
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from app.auth.jwt import decode_access_token
from app.auth.models import CurrentUser
from app.core.infrastructure import get_db_session
from config.settings import settings

logger = structlog.get_logger()

# Bearer Token 提取器
_security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> CurrentUser | None:
    """
    从 JWT Token 中解析当前用户。

    - 如果 auth_enabled=False（开发模式），返回 None（允许匿名访问）
    - 如果 auth_enabled=True，必须提供有效 Token
    """
    # 开发模式：认证关闭时允许匿名
    if not settings.auth_enabled:
        if credentials is None:
            return None
        # 即使关闭认证，如果提供了 token 也尝试解析
        try:
            payload = decode_access_token(credentials.credentials)
            return CurrentUser(
                user_id=payload["sub"],
                username=payload.get("username", ""),
                role=payload.get("role", "viewer"),
                department=payload.get("department"),
            )
        except ValueError:
            return None

    # 生产模式：必须提供 Token
    if credentials is None:
        raise HTTPException(status_code=401, detail="未提供认证 Token")

    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token 格式错误")

    # 从数据库验证用户是否仍然有效
    try:
        async with get_db_session() as session:
            result = await session.execute(
                text("SELECT is_active, department FROM users WHERE user_id = :uid"),
                {"uid": user_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="用户不存在")
            if not row[0]:
                raise HTTPException(status_code=401, detail="用户已被禁用")
            department = row[1]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to verify user", error=str(e))
        raise HTTPException(status_code=500, detail="用户验证失败")

    return CurrentUser(
        user_id=user_id,
        username=payload.get("username", ""),
        role=payload.get("role", "viewer"),
        department=department,
    )


async def require_auth(
    current_user: CurrentUser | None = Depends(get_current_user),
) -> CurrentUser:
    """强制要求认证（即使 auth_enabled=False 也需要 token）"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="此操作需要登录")
    return current_user


async def require_admin(
    current_user: CurrentUser | None = Depends(get_current_user),
) -> CurrentUser:
    """要求管理员角色"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="此操作需要登录")
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return current_user


async def require_editor_or_above(
    current_user: CurrentUser | None = Depends(get_current_user),
) -> CurrentUser:
    """要求 editor 或 admin 角色"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="此操作需要登录")
    if current_user.role not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="您没有编辑权限")
    return current_user
