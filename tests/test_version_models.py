"""
Phase 3: 版本管理模型单元测试
"""

from app.core.models import (
    VersionDiffResponse,
    VersionHistoryResponse,
    VersionInfoResponse,
    VersionLinkRequest,
    VersionMatchResult,
    VersionStatus,
    VersionStatusUpdate,
)


class TestVersionStatus:
    def test_enum_values(self):
        assert VersionStatus.DRAFT == "draft"
        assert VersionStatus.ACTIVE == "active"
        assert VersionStatus.SUPERSEDED == "superseded"
        assert VersionStatus.ARCHIVED == "archived"


class TestVersionMatchResult:
    def test_default(self):
        r = VersionMatchResult()
        assert r.is_new_version is False
        assert r.matched_doc_id is None
        assert r.confidence == 0.0

    def test_positive_match(self):
        r = VersionMatchResult(
            is_new_version=True,
            matched_doc_id="abc-123",
            confidence=0.92,
            reason="标题高度相似",
        )
        assert r.is_new_version is True
        assert r.matched_doc_id == "abc-123"


class TestVersionInfoResponse:
    def test_basic(self):
        v = VersionInfoResponse(
            doc_id="d1",
            title="Test Doc",
            version_number="v2.0",
            version_status="active",
            is_latest=True,
        )
        assert v.version_number == "v2.0"
        assert v.is_latest is True
        assert v.parent_version_id is None


class TestVersionHistoryResponse:
    def test_with_versions(self):
        h = VersionHistoryResponse(
            doc_id="d1",
            title="Test",
            versions=[
                VersionInfoResponse(
                    doc_id="d0",
                    title="Test v1",
                    version_number="v1.0",
                    version_status="superseded",
                    is_latest=False,
                ),
                VersionInfoResponse(
                    doc_id="d1",
                    title="Test v2",
                    version_number="v2.0",
                    version_status="active",
                    is_latest=True,
                ),
            ],
        )
        assert len(h.versions) == 2
        assert h.versions[0].version_status == "superseded"
        assert h.versions[1].is_latest is True


class TestVersionDiffResponse:
    def test_basic(self):
        d = VersionDiffResponse(
            diff_id="diff1",
            old_version_id="d0",
            new_version_id="d1",
            change_summary="更新了付款条款",
        )
        assert d.diff_id == "diff1"
        assert d.text_diff_data == {}
        assert d.change_details == []


class TestVersionLinkRequest:
    def test_basic(self):
        r = VersionLinkRequest(parent_version_id="abc")
        assert r.parent_version_id == "abc"


class TestVersionStatusUpdate:
    def test_basic(self):
        u = VersionStatusUpdate(version_status="archived")
        assert u.version_status == "archived"
