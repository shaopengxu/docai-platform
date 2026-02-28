"""
基础设施初始化脚本
创建 Qdrant Collection、Elasticsearch Index 等
运行方式: python -m scripts.init_infrastructure
"""

import asyncio
import sys
import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, HnswConfigDiff,
    OptimizersConfigDiff, PayloadSchemaType,
)

sys.path.insert(0, ".")
from config.settings import settings


def init_qdrant():
    """创建 Qdrant 向量集合"""
    print("🔧 Initializing Qdrant collection...")

    client = QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )

    collection_name = settings.qdrant_collection_name

    # 如果已存在则跳过
    collections = [c.name for c in client.get_collections().collections]
    if collection_name in collections:
        print(f"  ⏩ Collection '{collection_name}' already exists, skipping.")
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=settings.embedding_dimension,  # BGE-M3: 1024
            distance=Distance.COSINE,
        ),
        hnsw_config=HnswConfigDiff(
            m=16,                   # HNSW 连接数（平衡精度和内存）
            ef_construct=100,       # 构建时的搜索宽度
        ),
        optimizers_config=OptimizersConfigDiff(
            indexing_threshold=20000,   # 超过 2 万条后启动索引优化
        ),
    )

    # 创建 payload 索引（用于元数据过滤）
    for field, schema_type in [
        ("doc_id", PayloadSchemaType.KEYWORD),
        ("doc_type", PayloadSchemaType.KEYWORD),
        ("chunk_type", PayloadSchemaType.KEYWORD),
        ("is_latest", PayloadSchemaType.BOOL),
        ("group_id", PayloadSchemaType.KEYWORD),
    ]:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=schema_type,
        )

    info = client.get_collection(collection_name)
    print(f"  ✅ Collection '{collection_name}' created. "
          f"Vector dim={info.config.params.vectors.size}")


def init_elasticsearch():
    """创建 Elasticsearch 索引（带 IK 中文分词）"""
    print("🔧 Initializing Elasticsearch index...")

    index_name = settings.es_index_name

    # 检查索引是否已存在
    resp = httpx.head(f"{settings.es_host}/{index_name}")
    if resp.status_code == 200:
        print(f"  ⏩ Index '{index_name}' already exists, skipping.")
        return

    index_config = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,        # 开发环境单节点
            "analysis": {
                "analyzer": {
                    "ik_smart_analyzer": {
                        "type": "custom",
                        "tokenizer": "ik_smart",
                    },
                    "ik_max_analyzer": {
                        "type": "custom",
                        "tokenizer": "ik_max_word",
                    },
                },
            },
        },
        "mappings": {
            "properties": {
                # 文本内容 - 使用 IK 分词
                "content": {
                    "type": "text",
                    "analyzer": "ik_max_word",          # 索引时最大粒度分词
                    "search_analyzer": "ik_smart",       # 搜索时智能分词
                },
                # 章节路径 - 同时支持精确匹配和分词搜索
                "section_path": {
                    "type": "text",
                    "analyzer": "ik_smart",
                    "fields": {
                        "keyword": {"type": "keyword"},
                    },
                },
                # 元数据字段 - keyword 类型（精确匹配 + 过滤）
                "chunk_id":    {"type": "keyword"},
                "doc_id":      {"type": "keyword"},
                "doc_title":   {
                    "type": "text",
                    "analyzer": "ik_smart",
                    "fields": {
                        "keyword": {"type": "keyword"},
                    },
                },
                "doc_type":    {"type": "keyword"},
                "group_id":    {"type": "keyword"},
                "chunk_type":  {"type": "keyword"},
                "is_latest":   {"type": "boolean"},
                "page_numbers": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "token_count": {"type": "integer"},
                "created_at":  {"type": "date"},
            },
        },
    }

    resp = httpx.put(
        f"{settings.es_host}/{index_name}",
        json=index_config,
    )
    resp.raise_for_status()
    print(f"  ✅ Index '{index_name}' created with IK analyzer.")


def main():
    print("=" * 60)
    print("DocAI Platform - Infrastructure Initialization")
    print("=" * 60)

    try:
        init_qdrant()
    except Exception as e:
        print(f"  ❌ Qdrant init failed: {e}")

    try:
        init_elasticsearch()
    except Exception as e:
        print(f"  ❌ Elasticsearch init failed: {e}")

    print("=" * 60)
    print("Done! Run verify script to check: python -m scripts.verify_services")
    print("=" * 60)


if __name__ == "__main__":
    main()
