"""
DocAI Platform - FastAPI 应用入口
启动方式: uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.infrastructure import close_all
from config.settings import settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting DocAI Platform", env=settings.app_env)
    yield
    logger.info("Shutting down DocAI Platform")
    await close_all()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="AI-powered Enterprise Document Management System",
    lifespan=lifespan,
)

# CORS（开发环境允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 健康检查 ──

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/detailed")
async def detailed_health_check():
    """详细健康检查（检测各基础服务连接）"""
    from app.core.infrastructure import (
        get_qdrant_client, get_es_client, get_redis_client,
    )
    checks = {}

    try:
        qdrant = get_qdrant_client()
        collections = await qdrant.get_collections()
        checks["qdrant"] = "ok"
    except Exception as e:
        checks["qdrant"] = f"error: {e}"

    try:
        es = get_es_client()
        info = await es.info()
        checks["elasticsearch"] = "ok"
    except Exception as e:
        checks["elasticsearch"] = f"error: {e}"

    try:
        redis = get_redis_client()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", "services": checks}


# ── Phase 1 路由 ──
from app.api import documents, query
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(query.router, prefix="/api/v1/query", tags=["query"])
