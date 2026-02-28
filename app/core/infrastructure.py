"""
基础设施连接管理
提供所有外部服务的客户端单例
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache

import structlog
from minio import Minio
from qdrant_client import QdrantClient
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from elasticsearch import AsyncElasticsearch

from config.settings import settings

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# PostgreSQL (async SQLAlchemy)
# ═══════════════════════════════════════════════════════════════════════════

_engine = None
_session_factory = None


def get_pg_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.pg_dsn,
            pool_size=10,
            max_overflow=20,
            echo=settings.debug,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_pg_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_db_session():
    """获取数据库 session 的上下文管理器"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ═══════════════════════════════════════════════════════════════════════════
# Qdrant (Vector Database)
# ═══════════════════════════════════════════════════════════════════════════

_qdrant_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            grpc_port=settings.qdrant_grpc_port,
            prefer_grpc=settings.qdrant_use_grpc,
        )
    return _qdrant_client


def get_qdrant_sync_client() -> QdrantClient:
    """同步客户端（用于初始化脚本等场景）"""
    return QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Elasticsearch
# ═══════════════════════════════════════════════════════════════════════════

_es_client: AsyncElasticsearch | None = None


def get_es_client() -> AsyncElasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = AsyncElasticsearch(
            hosts=[settings.es_host],
            request_timeout=30,
        )
    return _es_client


# ═══════════════════════════════════════════════════════════════════════════
# MinIO (Object Storage)
# ═══════════════════════════════════════════════════════════════════════════

_minio_client: Minio | None = None


def get_minio_client() -> Minio:
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
    return _minio_client


# ═══════════════════════════════════════════════════════════════════════════
# Redis
# ═══════════════════════════════════════════════════════════════════════════

_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis_client


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle
# ═══════════════════════════════════════════════════════════════════════════

async def close_all():
    """关闭所有连接（应用退出时调用）"""
    global _engine, _qdrant_client, _es_client, _redis_client

    if _engine:
        await _engine.dispose()
        _engine = None

    if _qdrant_client:
        await _qdrant_client.close()
        _qdrant_client = None

    if _es_client:
        await _es_client.close()
        _es_client = None

    if _redis_client:
        await _redis_client.close()
        _redis_client = None

    logger.info("All infrastructure connections closed")
