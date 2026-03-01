"""
DocAI Platform - 密码哈希工具 (Phase 5)
"""

from __future__ import annotations

import bcrypt


def hash_password(plain_password: str) -> str:
    """将明文密码哈希为 bcrypt 格式"""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码是否匹配哈希"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )
