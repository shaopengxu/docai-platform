"""
DocAI Platform - 审计日志 (Phase 5)
记录用户操作到 audit_logs 表
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import text

from app.auth.models import CurrentUser
from app.core.infrastructure import get_db_session

logger = structlog.get_logger()


async def audit_log(
    action: str,
    user: CurrentUser | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
):
    """
    写入审计日志。

    Args:
        action: 操作类型 (login/query/upload/delete/view/grant_permission/...)
        user: 当前用户（可选，匿名操作时为 None）
        resource_type: 资源类型 (document/group/version/user)
        resource_id: 资源 ID
        details: 操作详情
        request: FastAPI Request 对象（用于获取 IP）
    """
    try:
        ip_address = None
        user_agent = None
        if request:
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent", "")[:500]

        # 截断 details 中的长文本（如查询内容）
        safe_details = _truncate_details(details) if details else {}

        async with get_db_session() as session:
            await session.execute(
                text("""
                    INSERT INTO audit_logs
                    (user_id, username, action, resource_type, resource_id,
                     details, ip_address, user_agent)
                    VALUES (:uid, :uname, :action, :rtype, :rid,
                            :details, :ip, :ua)
                """),
                {
                    "uid": user.user_id if user else None,
                    "uname": user.username if user else "anonymous",
                    "action": action,
                    "rtype": resource_type,
                    "rid": resource_id,
                    "details": json.dumps(safe_details, ensure_ascii=False),
                    "ip": ip_address,
                    "ua": user_agent,
                },
            )

        logger.info(
            "Audit log recorded",
            action=action,
            user=user.username if user else "anonymous",
            resource=f"{resource_type}:{resource_id}" if resource_type else None,
        )

    except Exception as e:
        # 审计日志写入失败不应阻塞业务流程
        logger.error(
            "Failed to write audit log",
            action=action,
            error=str(e),
        )


def _truncate_details(details: dict[str, Any], max_value_len: int = 500) -> dict[str, Any]:
    """截断 details 中过长的值"""
    result = {}
    for k, v in details.items():
        if isinstance(v, str) and len(v) > max_value_len:
            result[k] = v[:max_value_len] + "..."
        else:
            result[k] = v
    return result
