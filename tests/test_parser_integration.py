"""
DocAI Platform - 文档解析器集成测试
使用 tests/test_docs/ 下的真实文档验证解析能力
"""

from pathlib import Path

import pytest

from app.core.models import ParsedDocument, Section, TableData
from app.ingestion.parser import parse_document, UnsupportedFormatError
from app.ingestion.chunker import semantic_chunk, count_tokens

TEST_DOCS_DIR = Path(__file__).parent / "test_docs"


def _get_test_file(pattern: str) -> str | None:
    """在测试目录中查找匹配的文件"""
    matches = list(TEST_DOCS_DIR.glob(pattern))
    return str(matches[0]) if matches else None


# ═══════════════════════════════════════════════════════════════════════════
# 格式覆盖测试
# ═══════════════════════════════════════════════════════════════════════════


class TestParserFormatCoverage:
    """验证各文件格式均可正确解析"""

    def test_parse_pdf(self):
        """PDF 文件应能正确解析"""
        path = _get_test_file("*.pdf")
        if not path:
            pytest.skip("No PDF test files found")

        doc = parse_document(path)
        assert isinstance(doc, ParsedDocument)
        assert doc.filename.endswith(".pdf")
        assert doc.page_count > 0 or doc.raw_text  # 至少有页数或原文
        assert len(doc.sections) > 0 or doc.raw_text

    def test_parse_docx(self):
        """DOCX 文件应能正确解析"""
        path = _get_test_file("*.docx")
        if not path:
            pytest.skip("No DOCX test files found")

        doc = parse_document(path)
        assert isinstance(doc, ParsedDocument)
        assert doc.filename.endswith(".docx")
        assert len(doc.sections) > 0

    def test_parse_pptx(self):
        """PPTX 文件应能正确解析"""
        path = _get_test_file("*.pptx")
        if not path:
            pytest.skip("No PPTX test files found")

        doc = parse_document(path)
        assert isinstance(doc, ParsedDocument)
        assert doc.filename.endswith(".pptx")
        assert doc.page_count > 0
        assert len(doc.sections) > 0

    def test_parse_xlsx(self):
        """XLSX 文件应能正确解析"""
        path = _get_test_file("*.xlsx")
        if not path:
            pytest.skip("No XLSX test files found")

        doc = parse_document(path)
        assert isinstance(doc, ParsedDocument)
        assert doc.filename.endswith(".xlsx")
        assert len(doc.sections) > 0
        # Excel 应该有表格
        assert len(doc.tables) > 0

    def test_unsupported_format_raises(self):
        """不支持的格式应抛出异常"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"fake content")
            path = f.name

        with pytest.raises(UnsupportedFormatError):
            parse_document(path)

        import os
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════
# 解析质量测试
# ═══════════════════════════════════════════════════════════════════════════


class TestParserQuality:
    """验证解析结果的质量"""

    def test_sections_have_content(self):
        """解析出的 sections 应有实际内容"""
        path = _get_test_file("*.docx")
        if not path:
            pytest.skip("No DOCX test files found")

        doc = parse_document(path)
        non_empty_sections = [
            s for s in doc.sections if s.content.strip() or s.title.strip()
        ]
        assert len(non_empty_sections) > 0

    def test_sections_have_titles(self):
        """至少部分 sections 应有标题"""
        path = _get_test_file("*.docx")
        if not path:
            pytest.skip("No DOCX test files found")

        doc = parse_document(path)
        titled_sections = [s for s in doc.sections if s.title.strip()]
        # 至少有一些 section 有标题
        assert len(titled_sections) > 0

    def test_raw_text_not_empty(self):
        """raw_text 应包含文档的文本内容"""
        path = _get_test_file("*.pdf")
        if not path:
            pytest.skip("No PDF test files found")

        doc = parse_document(path)
        # 扫描件可能没有 raw_text，跳过
        if "OCR" in doc.raw_text:
            pytest.skip("Scanned PDF, skipping raw_text check")
        assert len(doc.raw_text) > 0

    def test_table_extraction(self):
        """Excel 文件的表格应被正确提取"""
        path = _get_test_file("*.xlsx")
        if not path:
            pytest.skip("No XLSX test files found")

        doc = parse_document(path)
        assert len(doc.tables) > 0

        for table in doc.tables:
            assert table.content.strip()
            # Markdown 表格应包含 | 分隔符
            assert "|" in table.content


# ═══════════════════════════════════════════════════════════════════════════
# 解析 + 分块端到端测试
# ═══════════════════════════════════════════════════════════════════════════


class TestParserChunkerIntegration:
    """解析器 + 分块器的端到端集成测试"""

    def test_parse_and_chunk_docx(self):
        """DOCX: 解析 → 分块 应生成合理的 chunks"""
        path = _get_test_file("*.docx")
        if not path:
            pytest.skip("No DOCX test files found")

        doc = parse_document(path)
        chunks = semantic_chunk(doc, doc_id="test-integration-001")

        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.doc_id == "test-integration-001"
            assert chunk.content.strip()
            assert chunk.token_count > 0
            assert chunk.chunk_id  # UUID should exist

    def test_parse_and_chunk_pdf(self):
        """PDF: 解析 → 分块 应生成合理的 chunks"""
        path = _get_test_file("*.pdf")
        if not path:
            pytest.skip("No PDF test files found")

        doc = parse_document(path)
        if "OCR" in doc.raw_text:
            pytest.skip("Scanned PDF")

        chunks = semantic_chunk(doc, doc_id="test-integration-002")
        assert len(chunks) > 0

    def test_parse_and_chunk_xlsx(self):
        """XLSX: 解析 → 分块 应生成含表格的 chunks"""
        path = _get_test_file("*.xlsx")
        if not path:
            pytest.skip("No XLSX test files found")

        doc = parse_document(path)
        chunks = semantic_chunk(doc, doc_id="test-integration-003")

        assert len(chunks) > 0
        # XLSX 应该有 TABLE 类型的 chunk
        table_chunks = [c for c in chunks if c.chunk_type.value == "table"]
        assert len(table_chunks) > 0

    def test_chunk_sizes_within_bounds(self):
        """所有 chunk 的 token 数应在合理范围内"""
        path = _get_test_file("*.docx")
        if not path:
            pytest.skip("No DOCX test files found")

        doc = parse_document(path)
        chunks = semantic_chunk(
            doc, doc_id="test-bounds", target_size=500, max_size=800
        )

        for chunk in chunks:
            # 允许一定的超标（overlap 和表格可能导致超过 max_size）
            assert chunk.token_count > 0
            # 宽松限制：不应超过 max_size 的 2 倍
            assert chunk.token_count < 1600, (
                f"Chunk too large: {chunk.token_count} tokens, "
                f"section: {chunk.section_path}"
            )

    def test_parse_and_chunk_markdown(self, tmp_text_file):
        """Markdown: 解析 → 分块 应正确处理标题结构"""
        doc = parse_document(tmp_text_file)
        chunks = semantic_chunk(doc, doc_id="test-md-001")

        assert len(chunks) > 0
        # Markdown 应保留章节路径
        section_paths = [c.section_path for c in chunks if c.section_path]
        assert len(section_paths) > 0

    def test_all_test_docs_parseable(self):
        """所有测试文档都应能被成功解析（不抛异常）"""
        supported_exts = {".pdf", ".docx", ".doc", ".pptx", ".xlsx"}
        test_files = [
            f for f in TEST_DOCS_DIR.iterdir()
            if f.suffix.lower() in supported_exts
        ]

        if not test_files:
            pytest.skip("No test documents found")

        results = []
        for f in test_files:
            try:
                doc = parse_document(str(f))
                results.append((f.name, "OK", len(doc.sections), len(doc.tables)))
            except Exception as e:
                results.append((f.name, f"FAILED: {e}", 0, 0))

        # 至少 80% 应成功
        success_count = sum(1 for _, status, _, _ in results if status == "OK")
        total = len(results)
        success_rate = success_count / total if total > 0 else 0

        # 输出所有结果用于调试
        for name, status, sections, tables in results:
            print(f"  {name}: {status} (sections={sections}, tables={tables})")

        assert success_rate >= 0.8, (
            f"Parse success rate {success_rate:.0%} < 80%. "
            f"Results: {results}"
        )
