"""
Phase 3: DiffEngine 单元测试
测试三层差异对比逻辑（不依赖外部服务）
"""

from app.versioning.diff_engine import DiffEngine


engine = DiffEngine()


# ─────────────────────────────────────────────────────────────────────
# Layer 1: 文本级差异
# ─────────────────────────────────────────────────────────────────────


class TestTextDiff:
    def test_empty_inputs(self):
        result = engine._compute_text_diff([], [])
        assert result["sections"] == []
        assert result["stats"]["added"] == 0

    def test_added_section(self):
        old_chunks = []
        new_chunks = [
            {"section_path": "第一章", "content": "新增内容"},
        ]
        result = engine._compute_text_diff(old_chunks, new_chunks)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["status"] == "added"
        assert result["stats"]["added"] == 1

    def test_deleted_section(self):
        old_chunks = [
            {"section_path": "第一章", "content": "旧内容"},
        ]
        new_chunks = []
        result = engine._compute_text_diff(old_chunks, new_chunks)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["status"] == "deleted"
        assert result["stats"]["deleted"] == 1

    def test_modified_section(self):
        old_chunks = [
            {"section_path": "第一章", "content": "原始内容 A"},
        ]
        new_chunks = [
            {"section_path": "第一章", "content": "修改后内容 B"},
        ]
        result = engine._compute_text_diff(old_chunks, new_chunks)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["status"] == "modified"
        assert result["sections"][0]["changes"]
        assert result["stats"]["modified"] == 1

    def test_unchanged_section(self):
        old_chunks = [
            {"section_path": "第一章", "content": "相同内容"},
        ]
        new_chunks = [
            {"section_path": "第一章", "content": "相同内容"},
        ]
        result = engine._compute_text_diff(old_chunks, new_chunks)
        assert len(result["sections"]) == 0  # 未变化的章节不出现在列表中
        assert result["stats"]["unchanged"] == 1

    def test_mixed_changes(self):
        old_chunks = [
            {"section_path": "第一章", "content": "不变"},
            {"section_path": "第二章", "content": "旧内容"},
            {"section_path": "第三章", "content": "将删除"},
        ]
        new_chunks = [
            {"section_path": "第一章", "content": "不变"},
            {"section_path": "第二章", "content": "新内容"},
            {"section_path": "第四章", "content": "新增"},
        ]
        result = engine._compute_text_diff(old_chunks, new_chunks)
        statuses = {s["section_path"]: s["status"] for s in result["sections"]}
        assert statuses["第二章"] == "modified"
        assert statuses["第三章"] == "deleted"
        assert statuses["第四章"] == "added"
        assert result["stats"]["unchanged"] == 1

    def test_multiple_chunks_same_section(self):
        old_chunks = [
            {"section_path": "第一章", "content": "段落一"},
            {"section_path": "第一章", "content": "段落二"},
        ]
        new_chunks = [
            {"section_path": "第一章", "content": "段落一"},
            {"section_path": "第一章", "content": "段落二修改了"},
        ]
        result = engine._compute_text_diff(old_chunks, new_chunks)
        assert len(result["sections"]) == 1
        assert result["sections"][0]["status"] == "modified"


# ─────────────────────────────────────────────────────────────────────
# Layer 2: 结构级差异
# ─────────────────────────────────────────────────────────────────────


class TestStructuralDiff:
    def test_identical_structure(self):
        chunks = [
            {"section_path": "第一章"},
            {"section_path": "第二章"},
        ]
        result = engine._compute_structural_diff(chunks, chunks)
        assert len(result["added_sections"]) == 0
        assert len(result["deleted_sections"]) == 0
        assert len(result["common_sections"]) == 2

    def test_added_sections(self):
        old = [{"section_path": "第一章"}]
        new = [{"section_path": "第一章"}, {"section_path": "第二章"}]
        result = engine._compute_structural_diff(old, new)
        assert "第二章" in result["added_sections"]
        assert result["total_new"] == 2

    def test_deleted_sections(self):
        old = [{"section_path": "第一章"}, {"section_path": "第二章"}]
        new = [{"section_path": "第一章"}]
        result = engine._compute_structural_diff(old, new)
        assert "第二章" in result["deleted_sections"]

    def test_renamed_section_detection(self):
        old = [{"section_path": "第三章 付款条款"}]
        new = [{"section_path": "第三章 支付条款"}]
        result = engine._compute_structural_diff(old, new)
        assert len(result["renamed_sections"]) == 1
        assert result["renamed_sections"][0]["old_name"] == "第三章 付款条款"
        assert result["renamed_sections"][0]["new_name"] == "第三章 支付条款"

    def test_empty_section_paths_ignored(self):
        old = [{"section_path": ""}, {"section_path": "第一章"}]
        new = [{"section_path": ""}, {"section_path": "第一章"}]
        result = engine._compute_structural_diff(old, new)
        # Empty section paths are filtered out
        assert result["total_old"] == 1
        assert result["total_new"] == 1


# ─────────────────────────────────────────────────────────────────────
# parse_diff_changes 辅助
# ─────────────────────────────────────────────────────────────────────


class TestParseDiffChanges:
    def test_simple_replacement(self):
        changes = engine._parse_diff_changes("旧文本", "新文本")
        assert len(changes) >= 1
        assert any(c["type"] == "replace" for c in changes)

    def test_insertion(self):
        changes = engine._parse_diff_changes("ABC", "ABXYZC")
        assert any(c["type"] == "insert" for c in changes)

    def test_deletion(self):
        changes = engine._parse_diff_changes("ABXYZC", "ABC")
        assert any(c["type"] == "delete" for c in changes)

    def test_no_change(self):
        changes = engine._parse_diff_changes("相同", "相同")
        assert len(changes) == 0

    def test_limit_changes(self):
        old = "A" * 100
        new = "B" * 100
        changes = engine._parse_diff_changes(old, new)
        assert len(changes) <= 30  # 限制数量


# ─────────────────────────────────────────────────────────────────────
# group_chunks_by_section 辅助
# ─────────────────────────────────────────────────────────────────────


class TestGroupChunksBySection:
    def test_grouping(self):
        chunks = [
            {"section_path": "A", "content": "1"},
            {"section_path": "A", "content": "2"},
            {"section_path": "B", "content": "3"},
        ]
        result = engine._group_chunks_by_section(chunks)
        assert result["A"] == "1\n2"
        assert result["B"] == "3"

    def test_empty_section_path(self):
        chunks = [{"section_path": "", "content": "text"}]
        result = engine._group_chunks_by_section(chunks)
        assert "(无章节)" in result
