PYTHON := .venv/bin/python
PIP    := .venv/bin/pip

.PHONY: help up down init verify dev clean logs setup test lint

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── 基础设施 ──

up: ## 启动所有基础服务 (docker-compose up -d)
	docker-compose up -d
	@echo "⏳ Waiting for services to be ready..."
	@sleep 10
	@echo "✅ Services started. Run 'make verify' to check."

down: ## 停止所有基础服务
	docker-compose down

init: ## 初始化基础设施 (创建 Qdrant collection, ES index 等)
	$(PYTHON) -m scripts.init_infrastructure

verify: ## 验证所有服务是否正常
	$(PYTHON) -m scripts.verify_services

# ── 开发 ──

dev: ## 启动后端开发服务器 (带热重载)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web-dev: ## 启动前端开发服务器
	cd web && npm run dev

web-build: ## 构建前端 MVP
	cd web && npm run build

# ── 工具 ──

logs: ## 查看 docker 服务日志
	docker-compose logs -f --tail=50

clean: ## 清理所有数据卷 (⚠️ 会删除所有数据)
	docker-compose down -v
	@echo "⚠️  All data volumes removed."

# ── 测试 ──

test: ## 运行测试
	$(PYTHON) -m pytest tests/ -v --cov=app

validate: ## Phase 1 端到端验证 (需要基础设施运行)
	$(PYTHON) -m scripts.validate_phase1

lint: ## 代码检查
	$(PYTHON) -m ruff check app/ scripts/
	$(PYTHON) -m mypy app/ --ignore-missing-imports

# ── 快速开始 (首次使用) ──

setup: ## 首次完整设置: 安装依赖 → 启动服务 → 初始化 → 验证
	@echo "📦 Step 1/4: Installing dependencies..."
	$(PIP) install -e ".[dev]"
	@echo "🐳 Step 2/4: Starting infrastructure..."
	docker-compose up -d
	@echo "⏳ Waiting 15s for services..."
	@sleep 15
	@echo "🔧 Step 3/4: Initializing indices..."
	$(PYTHON) -m scripts.init_infrastructure
	@echo "✅ Step 4/4: Verifying..."
	$(PYTHON) -m scripts.verify_services
	@echo ""
	@echo "🎉 Setup complete! Next steps:"
	@echo "   1. Copy .env.example to .env and set your API keys"
	@echo "   2. Place test documents in tests/test_docs/"
	@echo "   3. Run 'make dev' to start the development server"
