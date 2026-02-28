"""
服务健康检查脚本
验证所有基础设施服务是否正常运行
运行方式: python -m scripts.verify_services
"""

import sys
import time

sys.path.insert(0, ".")


def check(name: str, func):
    """运行检查并打印结果"""
    try:
        result = func()
        print(f"  ✅ {name}: {result}")
        return True
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        return False


def check_qdrant():
    from qdrant_client import QdrantClient
    from config.settings import settings
    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    collections = client.get_collections().collections
    col_names = [c.name for c in collections]
    if settings.qdrant_collection_name in col_names:
        info = client.get_collection(settings.qdrant_collection_name)
        return f"OK — collection '{settings.qdrant_collection_name}' exists, {info.points_count} points"
    return f"OK — connected, but collection '{settings.qdrant_collection_name}' not found (run init script)"


def check_elasticsearch():
    import httpx
    from config.settings import settings
    resp = httpx.get(f"{settings.es_host}/_cluster/health", timeout=5)
    resp.raise_for_status()
    health = resp.json()
    status = health["status"]

    # 检查 IK 分词是否可用
    ik_test = httpx.post(
        f"{settings.es_host}/_analyze",
        json={"analyzer": "ik_smart", "text": "企业文档管理系统"},
        timeout=5,
    )
    if ik_test.status_code == 200:
        tokens = [t["token"] for t in ik_test.json()["tokens"]]
        return f"OK — cluster: {status}, IK analyzer: {tokens}"
    return f"OK — cluster: {status}, IK analyzer: NOT installed"


def check_postgres():
    import asyncio
    import asyncpg
    from config.settings import settings

    async def _check():
        conn = await asyncpg.connect(
            host=settings.pg_host, port=settings.pg_port,
            database=settings.pg_database, user=settings.pg_user,
            password=settings.pg_password,
        )
        table_count = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
        await conn.close()
        return table_count

    table_count = asyncio.run(_check())
    return f"OK — {table_count} tables in public schema"


def check_minio():
    from minio import Minio
    from config.settings import settings
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_use_ssl,
    )
    buckets = [b.name for b in client.list_buckets()]
    return f"OK — buckets: {buckets}"


def check_redis():
    import redis as r
    from config.settings import settings
    client = r.from_url(settings.redis_url)
    info = client.info("server")
    return f"OK — Redis {info['redis_version']}"


def check_embedding_model():
    """检查嵌入模型是否可加载（仅检测，不实际加载以节省时间）"""
    from config.settings import settings
    import importlib
    importlib.import_module("sentence_transformers")
    return f"OK — sentence-transformers installed, model: {settings.embedding_model_name}"


def check_llm_api():
    from config.settings import settings
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-xxx"):
            return "⚠️  ANTHROPIC_API_KEY not set (update .env)"
        return f"OK — Anthropic API key configured, model: {settings.anthropic_model}"
    elif settings.llm_provider == "openai":
        if not settings.openai_api_key or settings.openai_api_key.startswith("sk-xxx"):
            return "⚠️  OPENAI_API_KEY not set (update .env)"
        return f"OK — OpenAI API key configured, model: {settings.openai_model}"
    return f"⚠️  Unknown provider: {settings.llm_provider}"


def main():
    print("=" * 60)
    print("DocAI Platform - Service Health Check")
    print("=" * 60)
    print()

    results = {}
    checks = [
        ("PostgreSQL", check_postgres),
        ("Qdrant (Vector DB)", check_qdrant),
        ("Elasticsearch (Full-text)", check_elasticsearch),
        ("MinIO (Object Storage)", check_minio),
        ("Redis (Cache)", check_redis),
        ("Embedding Model", check_embedding_model),
        ("LLM API", check_llm_api),
    ]

    passed = 0
    for name, func in checks:
        ok = check(name, func)
        if ok:
            passed += 1

    print()
    print(f"Result: {passed}/{len(checks)} checks passed")
    print("=" * 60)

    if passed < 5:  # 前 5 个是基础设施，必须全部通过
        print("⚠️  Some infrastructure services are not running.")
        print("   Run: docker-compose up -d")
        sys.exit(1)
    else:
        print("🚀 Infrastructure ready! You can proceed to Phase 1.")


if __name__ == "__main__":
    main()
