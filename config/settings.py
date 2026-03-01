"""
DocAI Platform - 配置管理
所有配置通过环境变量或 .env 文件注入
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置，支持 .env 文件和环境变量覆盖"""

    # ── 应用 ──
    app_name: str = "DocAI Platform"
    app_env: str = Field(default="development", description="development / staging / production")
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── 认证 (Phase 5) ──
    jwt_secret_key: str = Field(
        default="docai-dev-secret-key-change-in-production",
        description="JWT 签名密钥，生产环境必须修改"
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24              # Token 有效期
    auth_enabled: bool = False              # 是否启用认证（渐进式开关）

    # ── LLM ──
    llm_provider: str = Field(default="anthropic", description="anthropic / openai")
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_max_tokens: int = 4096
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # 轻量级 LLM（用于摘要、实体提取等低复杂度任务，降低成本）
    light_llm_provider: str = "anthropic"
    light_anthropic_model: str = "claude-haiku-4-5-20251001"

    # ── 嵌入模型 ──
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_device: str = "cuda"          # cuda / cpu / mps
    embedding_batch_size: int = 32
    embedding_dimension: int = 1024         # BGE-M3 输出维度

    # ── Reranker ──
    reranker_model_name: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "cuda"
    reranker_top_k: int = 5                 # 重排后取 top k

    # ── Qdrant ──
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "doc_chunks"
    qdrant_use_grpc: bool = True

    # ── Elasticsearch ──
    es_host: str = "http://localhost:9200"
    es_index_name: str = "doc_chunks"
    es_analyzer: str = "ik_max_word"        # IK 中文分词

    # ── PostgreSQL ──
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "docai"
    pg_user: str = "docai"
    pg_password: str = "docai_dev_2025"

    @property
    def pg_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

    # ── MinIO ──
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "admin"
    minio_secret_key: str = "minio_dev_2025"
    minio_bucket_documents: str = "documents"
    minio_bucket_parsed: str = "parsed"
    minio_use_ssl: bool = False

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600           # 查询缓存 1 小时

    # ── 文档处理 ──
    chunk_target_size: int = 500            # 目标 chunk 大小 (tokens)
    chunk_max_size: int = 800               # 最大 chunk 大小 (tokens)
    chunk_overlap: int = 50                 # 相邻 chunk 重叠 (tokens)
    max_file_size_mb: int = 100             # 单文件大小限制
    supported_extensions: list[str] = [
        ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".csv", ".txt", ".md"
    ]

    # ── 检索 ──
    retrieval_top_k_vector: int = 20        # 向量检索 top k
    retrieval_top_k_bm25: int = 20          # BM25 检索 top k
    retrieval_rrf_k: int = 60               # RRF 融合参数
    retrieval_final_top_k: int = 5          # 最终返回 top k
    context_window_chunks: int = 1          # 上下文扩展窗口（前后各 N 个 chunk）

    # ── 生成 ──
    generation_max_context_tokens: int = 12000  # 传入 LLM 的最大上下文 tokens
    require_citations: bool = True              # 强制要求引用

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# 全局单例
settings = Settings()
