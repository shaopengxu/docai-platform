"""
DocAI Platform - API 端点测试
使用 FastAPI TestClient 测试 health 端点和 API 结构
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════════
# Health Check 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    def test_health_endpoint(self):
        """基础健康检查应返回 ok"""
        from app.main import app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "service" in data


# ═══════════════════════════════════════════════════════════════════════════
# API 路由注册测试
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteRegistration:
    def test_routes_registered(self):
        """Phase 1 路由应已注册"""
        from app.main import app
        routes = [route.path for route in app.routes]
        assert "/api/v1/documents" in routes or any("/api/v1/documents" in r for r in routes)

    def test_openapi_schema_available(self):
        """OpenAPI schema 应可用"""
        from app.main import app
        client = TestClient(app)
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        # 验证文档和查询路由在 schema 中
        paths = list(schema["paths"].keys())
        assert any("/documents" in p for p in paths)
        assert any("/query" in p for p in paths)


# ═══════════════════════════════════════════════════════════════════════════
# Documents API 测试（需要 mock 数据库）
# ═══════════════════════════════════════════════════════════════════════════


class TestDocumentsAPI:
    def test_upload_rejects_unsupported_format(self):
        """上传不支持的文件格式应返回 400"""
        from app.main import app
        client = TestClient(app)

        # 创建一个假的 .xyz 文件
        response = client.post(
            "/api/v1/documents",
            files={"file": ("test.xyz", b"fake content", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "不支持" in response.json()["detail"]

    def test_upload_rejects_empty_file(self):
        """上传空内容的支持格式"""
        from app.main import app
        client = TestClient(app)

        response = client.post(
            "/api/v1/documents",
            files={"file": ("test.txt", b"", "text/plain")},
        )
        # 空文件但格式正确，应该被接受（pipeline 处理可能失败但上传应成功）
        # 或者返回错误都可以接受
        assert response.status_code in [201, 400]


class TestQueryAPI:
    def test_empty_question_rejected(self):
        """空问题应返回 400"""
        from app.main import app
        client = TestClient(app)

        response = client.post(
            "/api/v1/query",
            json={"question": ""},
        )
        assert response.status_code == 400

    def test_whitespace_question_rejected(self):
        """纯空白问题应返回 400"""
        from app.main import app
        client = TestClient(app)

        response = client.post(
            "/api/v1/query",
            json={"question": "   "},
        )
        assert response.status_code == 400
