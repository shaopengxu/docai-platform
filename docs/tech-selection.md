# 技术选型决策文档

> 创建日期：2025  
> 状态：已确认

## 选型原则

1. **中文优先**：嵌入模型和分词器必须对中文有优秀支持
2. **可私有化**：核心模型可自部署，不强依赖外部 API
3. **成熟稳定**：优先选择经过生产验证的方案
4. **可替换**：各组件通过抽象层解耦，可独立替换

---

## 决策记录

### 1. LLM — Anthropic Claude Sonnet

| 考量 | 决策 |
|------|------|
| 选定方案 | Claude Sonnet 4（主力） + Claude Haiku 4.5（轻量任务） |
| 备选方案 | OpenAI GPT-4o / 自部署 Qwen2.5-72B |
| 决策理由 | 中文理解能力强；200K 上下文窗口适合长文档；API 稳定性好 |
| 风险 | API 依赖；成本随用量增长。用 Haiku 处理简单任务可降低 60%+ 成本 |
| 退出策略 | LLMClient 抽象层已支持 OpenAI，切换只需改配置 |

### 2. 嵌入模型 — BGE-M3 (自部署)

| 考量 | 决策 |
|------|------|
| 选定方案 | BAAI/bge-m3 (1024 维) |
| 备选方案 | Cohere Embed v3 / Jina Embeddings v3 |
| 决策理由 | 中英文多语言支持优秀；开源可自部署无 API 费用；1024 维平衡精度和性能 |
| 资源需求 | 推理时约 2GB 显存，1 张 A10/L4/T4 足够 |
| 退出策略 | encode_texts() 接口抽象，可替换为任意嵌入模型 |

### 3. 向量数据库 — Qdrant

| 考量 | 决策 |
|------|------|
| 选定方案 | Qdrant v1.12 (Docker 自部署) |
| 备选方案 | Weaviate / pgvector / Pinecone |
| 决策理由 | 原生支持混合检索（dense + sparse）；Payload 索引支持高效过滤；Rust 实现性能好 |
| 未选 pgvector | pgvector 适合 <100 万向量的轻量场景，超过后性能下降明显 |
| 未选 Pinecone | 托管服务，数据出境合规风险 |

### 4. 全文搜索 — Elasticsearch + IK

| 考量 | 决策 |
|------|------|
| 选定方案 | Elasticsearch 8.16 + IK 中文分词插件 |
| 备选方案 | OpenSearch / MeiliSearch |
| 决策理由 | IK 分词是中文搜索事实标准；BM25 成熟可靠；生态丰富 |
| 分词策略 | 索引时用 ik_max_word（最大粒度），搜索时用 ik_smart（智能粒度） |

### 5. Reranker — BGE-Reranker-v2-m3

| 考量 | 决策 |
|------|------|
| 选定方案 | BAAI/bge-reranker-v2-m3 (自部署) |
| 备选方案 | Cohere Rerank v3 |
| 决策理由 | 中文效果好；与 BGE-M3 配套优化；可本地部署无 API 费用 |
| 用法 | 向量+BM25 检索 top 20 后，Reranker 重排取 top 5 |

### 6. 文档解析 — Docling + PyMuPDF

| 考量 | 决策 |
|------|------|
| 选定方案 | Docling (通用) + PyMuPDF (PDF 快速提取) |
| 备选方案 | Unstructured.io / LlamaParse |
| 决策理由 | Docling 开源免费，支持 PDF/Word/PPT/Excel；表格识别能力强 |
| OCR 方案 | Surya (GPU) 或 PaddleOCR (CPU) 处理扫描件 |
| 未选 LlamaParse | 商业服务，按页收费，大量文档成本高 |

### 7. 应用框架 — FastAPI

| 考量 | 决策 |
|------|------|
| 选定方案 | FastAPI + asyncio |
| 决策理由 | 原生异步；自动生成 OpenAPI 文档；类型提示完善；生态成熟 |

### 8. 元数据库 — PostgreSQL

| 考量 | 决策 |
|------|------|
| 选定方案 | PostgreSQL 16 |
| 决策理由 | 版本管理需要关系型数据；pg_trgm 扩展支持文本相似度（版本识别用） |
| 注意 | 不用 PG 做向量检索（交给 Qdrant），只存元数据和版本关系 |

---

## 硬件需求估算

### 开发环境（最小配置）

- CPU: 8 核
- 内存: 32 GB（ES 和模型各需较大内存）
- GPU: 1x NVIDIA T4/A10 (16GB+)，无 GPU 可用 CPU 但推理慢 5-10x
- 磁盘: 100 GB SSD

### 生产环境（100 用户，1 万份文档）

- 应用服务器: 2x 8 核 32GB
- GPU 服务器: 1x A10 24GB（嵌入 + Reranker）
- Qdrant: 2x 8 核 32GB（高可用）
- Elasticsearch: 2x 8 核 16GB
- PostgreSQL: 1x 4 核 16GB
- MinIO: 按存储量扩展
