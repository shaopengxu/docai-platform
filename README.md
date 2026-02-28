# DocAI Platform

AI 驱动的企业级文档管理系统 —— 支持精确检索、跨文档总结、多版本对比。

## 快速开始

### 前置要求

- Docker & Docker Compose
- Python 3.11+
- GPU（推荐，用于嵌入模型和 Reranker；无 GPU 可改用 CPU 模式）
- LLM API Key（Anthropic Claude 或 OpenAI）

### 一键启动

```bash
# 1. 克隆项目
git clone <repo-url> && cd docai-platform

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 ANTHROPIC_API_KEY

# 3. 完整安装（安装依赖 → 启动服务 → 初始化 → 验证）
make setup

# 4. 启动开发服务器
make dev
# API 地址: http://localhost:8000
# API 文档: http://localhost:8000/docs
```

### 手动步骤

```bash
# 安装 Python 依赖
pip install -e ".[dev]"

# 启动基础服务
docker-compose up -d

# 等待服务就绪后初始化索引
python -m scripts.init_infrastructure

# 验证所有服务
python -m scripts.verify_services
```

## 项目结构

```
docai-platform/
├── app/                        # 应用代码
│   ├── api/                    #   API 路由 (FastAPI)
│   ├── core/                   #   核心模块
│   │   ├── infrastructure.py   #     数据库/缓存连接管理
│   │   ├── llm_client.py       #     LLM 调用抽象层
│   │   └── embedding.py        #     嵌入模型管理
│   ├── ingestion/              #   文档解析 & 入库 (Phase 1)
│   ├── retrieval/              #   检索模块 (Phase 1-2)
│   ├── generation/             #   答案生成模块 (Phase 1-2)
│   ├── versioning/             #   版本管理 (Phase 3)
│   └── main.py                 #   FastAPI 入口
├── config/
│   └── settings.py             # 配置管理 (Pydantic Settings)
├── docker/
│   ├── elasticsearch/          #   ES + IK 中文分词 Dockerfile
│   └── postgres/               #   PG 初始化 SQL
├── scripts/
│   ├── init_infrastructure.py  # 基础设施初始化
│   └── verify_services.py      # 服务健康检查
├── tests/
│   ├── test_docs/              # 测试文档（不提交 git）
│   └── test_questions/         # 标注好的测试问题集
├── docker-compose.yml          # 开发环境一键启动
├── pyproject.toml              # Python 项目配置 & 依赖
├── Makefile                    # 常用命令快捷方式
└── .env.example                # 环境变量模板
```

## 基础服务访问

| 服务 | 地址 | 用途 |
|------|------|------|
| API Server | http://localhost:8000 | 后端 API |
| API Docs | http://localhost:8000/docs | Swagger 文档 |
| Qdrant Dashboard | http://localhost:6333/dashboard | 向量数据库管理 |
| Elasticsearch | http://localhost:9200 | 全文搜索引擎 |
| MinIO Console | http://localhost:9001 | 对象存储管理 (admin/minio_dev_2025) |
| PostgreSQL | localhost:5432 | 元数据库 (docai/docai_dev_2025) |
| Redis | localhost:6379 | 缓存 |

## 常用命令

```bash
make help       # 查看所有命令
make up         # 启动基础服务
make down       # 停止基础服务
make dev        # 启动后端开发服务器
make web-dev    # 启动前端开发服务器 (localhost:3000)
make web-build  # 构建前端 MVP
make verify     # 验证服务状态
make test       # 运行测试 (62 tests)
make validate   # Phase 1 端到端验证（需基础设施运行）
make logs       # 查看服务日志
make clean      # 清理所有数据（⚠️ 不可逆）
```

## Phase 1 API 使用指南

### API 端点概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/documents` | 上传文档（支持 PDF/Word/PPT/Excel/CSV/TXT/MD） |
| GET | `/api/v1/documents` | 文档列表（支持按类型、状态过滤） |
| GET | `/api/v1/documents/{doc_id}` | 文档详情 |
| DELETE | `/api/v1/documents/{doc_id}` | 删除文档及全部关联数据 |
| GET | `/api/v1/documents/{doc_id}/chunks` | 查看文档分块（调试用） |
| POST | `/api/v1/query` | 文档问答（RAG） |
| POST | `/api/v1/query/stream` | 流式问答（SSE） |

### 使用示例

```bash
# 1. 上传文档
curl -X POST http://localhost:8000/api/v1/documents \
  -F "file=@my_contract.pdf" \
  -F "doc_type=contract" \
  -F "tags=供应商,2025年"

# 返回: {"doc_id": "xxx", "processing_status": "pending", ...}

# 2. 查询处理状态（等待 status 变为 "ready"）
curl http://localhost:8000/api/v1/documents/{doc_id}

# 3. 提问
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "付款周期是多少天？", "doc_id": "xxx"}'

# 返回: {"answer": "...", "citations": [...], "confidence": 0.85, "latency_ms": 2300}

# 4. 流式问答（SSE）
curl -X POST http://localhost:8000/api/v1/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "合同的违约责任条款是什么？"}'
```

### 核心处理流程

```
文档上传 → 解析(PDF/Word/PPT) → 语义分块(300-800 tokens)
    → BGE-M3 嵌入 → 双路存储(Qdrant向量 + ES全文)
    → 用户提问 → 混合检索(向量+BM25) → RRF融合 → BGE-Reranker重排
    → LLM生成答案(Claude Sonnet) → 返回答案+引用来源+置信度
```

## 开发路线图

- [x] **Phase 0**: 基础设施 & 技术选型
- [x] **Phase 1 (Backend)**: 单文档检索问答 (MVP) — 62 tests passing
- [x] **Phase 1 (Frontend)**: Next.js MVP 界面 (上传/管理/流式问答)
- [ ] **Phase 2**: 多文档检索 & 跨文档总结
- [ ] **Phase 3**: 版本管理 & 差异对比
- [ ] **Phase 4**: 智能编排 & Agent 化
- [ ] **Phase 5**: 生产化加固 & 持续优化
