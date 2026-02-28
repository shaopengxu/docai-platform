"""
DocAI Platform - 测试共享 fixtures
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.core.models import (
    Chunk,
    ChunkType,
    Citation,
    ParsedDocument,
    RetrievedChunk,
    Section,
    TableData,
)

# 测试文档目录
TEST_DOCS_DIR = Path(__file__).parent / "test_docs"


@pytest.fixture
def test_docs_dir() -> Path:
    """返回测试文档目录路径"""
    return TEST_DOCS_DIR


@pytest.fixture
def sample_section() -> Section:
    """简单的测试章节"""
    return Section(
        title="第三章 付款条款",
        level=1,
        content="付款周期为 30 个工作日。甲方应在收到乙方发票后 30 个工作日内完成付款。",
        page_numbers=[5, 6],
    )


@pytest.fixture
def sample_parsed_doc(sample_section) -> ParsedDocument:
    """简单的解析后文档"""
    return ParsedDocument(
        title="测试合同文档",
        filename="test_contract.pdf",
        page_count=20,
        sections=[
            Section(
                title="第一章 总则",
                level=1,
                content="本合同由甲方和乙方共同签订。",
                page_numbers=[1, 2],
            ),
            Section(
                title="第二章 服务内容",
                level=1,
                content="乙方应提供以下服务：\n1. 系统开发\n2. 系统维护\n3. 技术支持",
                page_numbers=[3, 4],
            ),
            sample_section,
            Section(
                title="第四章 违约责任",
                level=1,
                content="违约方应承担合同总金额 10% 的违约金。",
                page_numbers=[7, 8],
            ),
        ],
        tables=[
            TableData(
                content="| 服务项 | 费用 |\n|---|---|\n| 开发 | 100万 |\n| 维护 | 20万 |",
                page_number=4,
                section_path="第二章 服务内容",
                caption="表1：服务费用明细",
            ),
        ],
        raw_text="全文原始文本...",
    )


@pytest.fixture
def sample_chunks() -> list[Chunk]:
    """预构建的 chunk 列表"""
    return [
        Chunk(
            chunk_id="chunk-001",
            doc_id="doc-001",
            doc_title="测试合同",
            section_path="第三章 付款条款",
            page_numbers=[5, 6],
            chunk_index=0,
            chunk_type=ChunkType.TEXT,
            content="付款周期为 30 个工作日。甲方应在收到乙方发票后 30 个工作日内完成付款。",
            token_count=35,
        ),
        Chunk(
            chunk_id="chunk-002",
            doc_id="doc-001",
            doc_title="测试合同",
            section_path="第四章 违约责任",
            page_numbers=[7, 8],
            chunk_index=1,
            chunk_type=ChunkType.TEXT,
            content="违约方应承担合同总金额 10% 的违约金。如因不可抗力导致违约，双方可协商减免。",
            token_count=40,
        ),
        Chunk(
            chunk_id="chunk-003",
            doc_id="doc-001",
            doc_title="测试合同",
            section_path="第二章 服务内容",
            page_numbers=[4],
            chunk_index=2,
            chunk_type=ChunkType.TABLE,
            content="| 服务项 | 费用 |\n|---|---|\n| 开发 | 100万 |\n| 维护 | 20万 |",
            token_count=25,
        ),
    ]


@pytest.fixture
def sample_retrieved_chunks() -> list[RetrievedChunk]:
    """预构建的检索结果列表"""
    return [
        RetrievedChunk(
            chunk_id="chunk-001",
            doc_id="doc-001",
            doc_title="供应商A合同",
            section_path="第三章 付款条款 > 3.2 付款周期",
            page_numbers=[12, 13],
            chunk_index=5,
            chunk_type="text",
            content="付款周期为 30 个工作日。甲方应在收到乙方正式发票后 30 个工作日内完成付款。",
            score=0.92,
        ),
        RetrievedChunk(
            chunk_id="chunk-002",
            doc_id="doc-001",
            doc_title="供应商A合同",
            section_path="第四章 违约责任",
            page_numbers=[18],
            chunk_index=8,
            chunk_type="text",
            content="如甲方逾期付款，应按逾期天数支付应付款项 0.05% 的滞纳金。",
            score=0.78,
        ),
        RetrievedChunk(
            chunk_id="chunk-003",
            doc_id="doc-002",
            doc_title="供应商B合同",
            section_path="第三章 付款方式",
            page_numbers=[8],
            chunk_index=3,
            chunk_type="text",
            content="付款采用银行转账方式，付款周期为 45 个工作日。",
            score=0.71,
        ),
    ]


@pytest.fixture
def tmp_text_file():
    """创建临时文本文件，返回路径，测试后自动清理"""
    content = """# 测试文档

## 第一章 概述

这是一份测试文档，用于验证文档解析和分块功能。

## 第二章 详细说明

### 2.1 功能描述

本系统提供以下核心功能：
1. 文档上传与解析
2. 智能分块与索引
3. 混合检索与问答

### 2.2 技术架构

系统采用分层架构设计，包括文档处理层、存储层、检索层和生成层。

## 第三章 总结

以上为系统的主要功能和架构说明。
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name

    yield path

    os.unlink(path)
