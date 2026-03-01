"""
DocAI Platform - 认证数据模型 (Phase 5)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ═══════════════════════════════════════════════════════════════════════════
# 用户相关模型
# ═══════════════════════════════════════════════════════════════════════════


class UserCreate(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=6, max_length=100)
    display_name: str | None = None
    department: str | None = None
    role: str = "viewer"  # admin / editor / viewer / restricted


class UserLogin(BaseModel):
    """登录请求"""
    username: str
    password: str


class UserResponse(BaseModel):
    """用户信息响应"""
    user_id: str
    username: str
    email: str
    display_name: str | None = None
    department: str | None = None
    role: str
    is_active: bool = True
    created_at: datetime | None = None


class UserUpdate(BaseModel):
    """更新用户信息"""
    display_name: str | None = None
    department: str | None = None
    role: str | None = None
    is_active: bool | None = None


class TokenResponse(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: UserResponse


class CurrentUser(BaseModel):
    """当前已认证用户（从 JWT 中解析）"""
    user_id: str
    username: str
    role: str
    department: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# 权限相关模型
# ═══════════════════════════════════════════════════════════════════════════


class PermissionGrant(BaseModel):
    """授权请求"""
    user_id: str
    doc_id: str | None = None
    group_id: str | None = None
    department: str | None = None
    permission: str = "read"  # read / write / admin


class PermissionResponse(BaseModel):
    """权限响应"""
    perm_id: str
    user_id: str
    doc_id: str | None = None
    group_id: str | None = None
    department: str | None = None
    permission: str
    granted_by: str | None = None
    created_at: datetime | None = None


# ═══════════════════════════════════════════════════════════════════════════
# 审计日志模型
# ═══════════════════════════════════════════════════════════════════════════


class AuditLogEntry(BaseModel):
    """审计日志条目"""
    log_id: str
    user_id: str | None = None
    username: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict | None = None
    ip_address: str | None = None
    created_at: datetime | None = None


class AuditLogListResponse(BaseModel):
    """审计日志列表响应"""
    logs: list[AuditLogEntry]
    total: int
