"""
DocAI Platform - 数据模型
Pydantic 模型定义：文档解析结果、分块、API 请求/响应
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════════════════

class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE_DESCRIPTION = "image_description"
    SECTION_SUMMARY = "section_summary"
    DOC_SUMMARY = "doc_summary"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SUMMARIZING = "summarizing"
    READY = "ready"
    ERROR = "error"


# ═══════════════════════════════════════════════════════════════════════════
# 文档解析结果模型
# ═══════════════════════════════════════════════════════════════════════════

class TableData(BaseModel):
    """解析出的表格"""
    content: str                 # Markdown 格式的表格文本
    page_number: int | None = None
    section_path: str = ""       # 所属章节路径
    caption: str = ""            # 表格标题


class Section(BaseModel):
    """文档章节"""
    title: str = ""
    level: int = 0               # 标题层级 (1=H1, 2=H2, ...)
    content: str = ""            # 章节正文
    page_numbers: list[int] = Field(default_factory=list)
    children: list[Section] = Field(default_factory=list)

    @property
    def full_content(self) -> str:
        """包含标题的完整内容"""
        parts = []
        if self.title:
            parts.append(self.title)
        if self.content:
            parts.append(self.content)
        return "\n".join(parts)

    def get_section_path(self, parent_path: str = "") -> str:
        """生成章节路径，如 '第三章 > 3.2 付款条款'"""
        if parent_path and self.title:
            return f"{parent_path} > {self.title}"
        return self.title or parent_path


class ParsedDocument(BaseModel):
    """文档解析后的结构化结果"""
    title: str = ""
    filename: str = ""
    page_count: int = 0
    sections: list[Section] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    raw_text: str = ""           # 全文原始文本 (fallback)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# 分块模型
# ═══════════════════════════════════════════════════════════════════════════

class Chunk(BaseModel):
    """文档分块"""
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_id: str = ""
    doc_title: str = ""
    section_path: str = ""       # 如: "第四章 付款条款 > 4.2 付款周期"
    page_numbers: list[int] = Field(default_factory=list)
    chunk_index: int = 0         # 在文档中的顺序
    chunk_type: ChunkType = ChunkType.TEXT
    content: str = ""
    token_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════════════
# API 请求/响应模型
# ═══════════════════════════════════════════════════════════════════════════

class DocumentResponse(BaseModel):
    """文档信息响应"""
    doc_id: str
    title: str
    original_filename: str
    file_size_bytes: int | None = None
    page_count: int | None = None
    doc_type: str | None = None
    processing_status: str = "pending"
    chunk_count: int = 0
    created_at: datetime | None = None


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    documents: list[DocumentResponse]
    total: int


class Citation(BaseModel):
    """引用信息"""
    doc_id: str
    doc_title: str
    section_path: str = ""
    page_numbers: list[int] = Field(default_factory=list)
    chunk_id: str = ""
    content_snippet: str = ""    # 被引用的原文片段


class QueryRequest(BaseModel):
    """问答请求"""
    question: str
    doc_id: str | None = None    # 如果指定则仅在该文档中检索
    top_k: int = 5               # 最终返回的 chunk 数量


class QueryResponse(BaseModel):
    """问答响应"""
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0      # 0-1 之间的置信度
    latency_ms: int = 0          # 响应时间


class RetrievedChunk(BaseModel):
    """检索到的 chunk（内部使用）"""
    chunk_id: str
    doc_id: str
    doc_title: str
    section_path: str = ""
    page_numbers: list[int] = Field(default_factory=list)
    chunk_index: int = 0
    chunk_type: str = "text"
    content: str = ""
    score: float = 0.0           # 检索/重排得分
