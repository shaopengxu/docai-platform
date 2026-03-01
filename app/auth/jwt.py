"""
DocAI Platform - JWT Token 管理 (Phase 5)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import structlog

from config.settings import settings

logger = structlog.get_logger()


def create_access_token(user_id: str, role: str, username: str) -> str:
    """生成 JWT Token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": user_id,
        "role": role,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """解码并验证 JWT Token"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token 已过期")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Token 无效: {str(e)}")
