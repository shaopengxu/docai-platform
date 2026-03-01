# AI 企业级文档管理系统——分阶段实施方案

---

## 总体规划路线图

```
Phase 0 ─── 基础设施 & 技术选型 ──────────── [第 1-2 周]
Phase 1 ─── 单文档检索问答（MVP）──────────── [第 3-6 周]
Phase 2 ─── 多文档检索 & 跨文档总结 ────────── [第 7-12 周]
Phase 3 ─── 版本管理 & 差异对比 ──────────── [第 13-18 周]
Phase 4 ─── 智能编排 & Agent 化 ──────────── [第 19-24 周]
Phase 5 ─── 生产化加固 & 持续优化 ─────────── [第 25-30 周]
```

每个 Phase 都可以独立交付可用的系统，后一个 Phase 在前一个的基础上增量建设。

---

## Phase 0：基础设施 & 技术选型（第 1-2 周）

### 0.1 目标

确定技术栈、搭建开发环境、准备测试文档集。
本阶段不写业务代码，只做决策和基建。

### 0.2 技术选型决策清单

| 组件              | 推荐方案                 | 备选方案                     | 决策依据                     |
| ----------------- | ------------------------ | ---------------------------- | ---------------------------- |
| **LLM**           | Claude Sonnet 4 (API)    | GPT-4o / 开源 Qwen2.5-72B    | 中文能力、长上下文、成本平衡 |
| **嵌入模型**      | BGE-M3 (自部署)          | Cohere Embed v3 / Jina v3    | 中文多语言支持、可私有化部署 |
| **向量数据库**    | Qdrant (Docker 自部署)   | Weaviate / pgvector          | 性能好、混合检索原生支持     |
| **全文搜索**      | Elasticsearch 8.x        | OpenSearch                   | 中文分词 (IK)、成熟稳定      |
| **Reranker**      | BGE-Reranker-v2-m3       | Cohere Rerank v3             | 中文效果好、可私有化         |
| **文档解析**      | Docling + PyMuPDF        | Unstructured.io / LlamaParse | 开源、表格识别能力强         |
| **OCR（扫描件）** | Surya / PaddleOCR        | Azure Document Intelligence  | 中文识别率高                 |
| **应用框架**      | LlamaIndex + FastAPI     | LangChain + LangGraph        | LlamaIndex 文档处理成熟      |
| **元数据库**      | PostgreSQL 16            | MongoDB                      | 版本关系适合关系型           |
| **对象存储**      | MinIO (自部署)           | 阿里云 OSS / AWS S3          | 存放文档原文                 |
| **前端**          | Next.js + React          | Vue 3                        | 生态丰富                     |
| **消息队列**      | Redis Streams / RabbitMQ | Kafka (如文档量极大)         | 异步任务分发                 |

### 0.3 环境搭建

```bash
# docker-compose.yml 核心服务
services:
  qdrant:        # 向量数据库
  elasticsearch: # 全文搜索 + IK 中文分词
  postgres:      # 元数据 + 版本管理
  minio:         # 文档原文存储
  redis:         # 缓存 + 任务队列
  embedding:     # BGE-M3 模型服务 (GPU)
  reranker:      # BGE-Reranker 服务 (GPU)
  api:           # FastAPI 后端
  web:           # Next.js 前端
```

### 0.4 测试文档集准备

- 准备 3 类文档各 10 份（合同/报告/政策等），覆盖：
  - 短文档 (< 10 页) 和长文档 (50+ 页)
  - 含表格、图表的复杂版面
  - 同一文档的 2-3 个不同版本
- 为每类文档准备 20 个测试问题（涵盖事实查询、总结、对比）
- 这个测试集将贯穿所有 Phase 的验证

### 0.5 交付物

- [x] 技术选型决策文档
- [x] docker-compose 开发环境一键启动
- [x] 测试文档集 + 标注好的测试问题集
- [x] 项目代码仓库初始化

---

## Phase 1：单文档检索问答——MVP（第 3-6 周）

### 1.1 目标

实现核心 RAG pipeline：用户上传文档 → 解析 → 分块 → 嵌入 → 检索 → 问答。
这是最小可用产品，解决"从文档中读取部分相关信息"的基本需求。

### 1.2 功能范围

```
用户上传文档(PDF/Word/PPT)
    │
    ▼
[文档解析模块] ──→ 提取文本 + 保留结构（标题层级、页码、表格）
    │
    ▼
[分块模块] ──→ 语义分块（按标题/段落自然切分，300-800 tokens）
    │                └─ 每个 chunk 携带 metadata:
    │                     - doc_id, doc_title
    │                     - section_path (如: "第三章 > 3.2 付款条款")
    │                     - page_number
    │                     - chunk_index (在文档中的顺序)
    ▼
[嵌入模块] ──→ BGE-M3 生成向量
    │
    ▼
[存储模块] ──→ 向量 → Qdrant
             ──→ 全文 → Elasticsearch
             ──→ 原文 → MinIO
             ──→ 元数据 → PostgreSQL
    │
    ▼
[检索模块] ──→ 用户提问
             ──→ 向量检索 (top 20) + BM25 检索 (top 20)
             ──→ RRF 融合 → Reranker 重排 → top 5
    │
    ▼
[生成模块] ──→ Prompt = System Prompt + 检索到的 chunks + 用户问题
             ──→ LLM 生成答案（要求附带引用来源）
             ──→ 返回答案 + 引用链接（可跳转到原文对应位置）
```

### 1.3 关键实现细节

**1.3.1 文档解析——需要处理好的边界情况：**

```python
# 解析策略路由
def parse_document(file_path: str) -> ParsedDocument:
    ext = get_extension(file_path)
    
    if ext == '.pdf':
        if is_scanned_pdf(file_path):
            return parse_with_ocr(file_path)      # Surya/PaddleOCR
        else:
            return parse_with_pymupdf(file_path)   # PyMuPDF 提取文字
    elif ext in ['.docx', '.doc']:
        return parse_with_docling(file_path)
    elif ext in ['.pptx']:
        return parse_with_docling(file_path)
    elif ext in ['.xlsx', '.csv']:
        return parse_spreadsheet(file_path)        # 表格→Markdown
    else:
        raise UnsupportedFormat(ext)
```

**1.3.2 语义分块——核心逻辑：**

```python
def semantic_chunk(parsed_doc: ParsedDocument) -> list[Chunk]:
    chunks = []
    for section in parsed_doc.sections:
        # 按标题层级自然切分
        if section.token_count <= 800:
            # 小于阈值，整段作为一个 chunk
            chunks.append(make_chunk(section, parsed_doc))
        else:
            # 大于阈值，按段落边界进一步切分
            sub_chunks = split_by_paragraphs(section, 
                                              target_size=500, 
                                              overlap=50)
            chunks.extend(sub_chunks)
    
    # 表格单独成 chunk
    for table in parsed_doc.tables:
        chunks.append(make_table_chunk(table, parsed_doc))
    
    return chunks
```

**1.3.3 Chunk Metadata 结构：**

```json
{
  "chunk_id": "uuid",
  "doc_id": "uuid",
  "doc_title": "XX供应商合同",
  "section_path": "第四章 付款条款 > 4.2 付款周期",
  "page_numbers": [12, 13],
  "chunk_index": 15,
  "chunk_type": "text",       // text | table | image_description
  "token_count": 520,
  "content": "实际文本内容...",
  "created_at": "2025-01-15T10:00:00Z"
}
```

### 1.4 前端 MVP

此阶段前端只需要：
- 文档上传界面（拖拽上传，显示处理进度）
- 文档列表（已上传的文档，处理状态）
- 问答界面（输入问题 → 展示答案 + 引用来源 + 可点击跳转原文）

### 1.5 验证标准

| 指标                | 目标          | 测试方法                           |
| ------------------- | ------------- | ---------------------------------- |
| 文档解析成功率      | ≥ 95%         | 30 份测试文档全部正确解析          |
| 检索召回率 Recall@5 | ≥ 80%         | 测试问题集中的正确答案出现在 top 5 |
| 答案准确率          | ≥ 75%         | 人工评审答案的正确性               |
| 引用准确率          | ≥ 85%         | 标注的来源确实包含答案依据         |
| 单文档处理时间      | < 60 秒/10 页 | 端到端计时                         |
| 查询响应时间        | < 5 秒        | 从提问到答案返回                   |

### 1.6 交付物

- [x] 文档解析 Pipeline（支持 PDF/Word/PPT）
- [x] 分块 + 嵌入 + 双路索引（向量 + 全文）
- [x] 基础 RAG 问答 API
- [x] 简易 Web 界面
- [x] 验证报告（含各指标实测数据）

---

## Phase 2：多文档检索 & 跨文档总结（第 7-12 周）

### 2.1 目标

支持跨多个文档的检索和总结性问答。
解决"对某一类业务做总结式提问"和"需要统领视角"的需求。

### 2.2 新增能力

```
Phase 1 已有               Phase 2 新增
─────────────────────      ─────────────────────────
单文档上传解析         →    批量文档导入 + 文档分组管理
基础 RAG 检索         →    混合检索 + 元数据过滤 + Query 改写
单轮问答             →    多轮对话 + 上下文记忆
基础答案生成         →    跨文档总结 (Map-Reduce)
                          预建摘要层（章节 + 文档级）
                          Contextual Retrieval（上下文增强嵌入）
```

### 2.3 关键模块实现

**2.3.1 文档分组与元数据增强**

```sql
-- PostgreSQL 表结构
CREATE TABLE documents (
    doc_id          UUID PRIMARY KEY,
    title           TEXT NOT NULL,
    doc_type        VARCHAR(50),     -- 合同/报告/政策/...
    department      VARCHAR(100),    -- 所属部门
    tags            TEXT[],          -- 标签数组
    group_id        UUID,            -- 文档组（如"2024年度审计"）
    status          VARCHAR(20),     -- active/archived
    created_at      TIMESTAMPTZ,
    file_path       TEXT,            -- MinIO 中的路径
    page_count      INT,
    -- Phase 2 新增
    doc_summary     TEXT,            -- 文档级摘要
    key_entities    JSONB,           -- 提取的关键实体
    CONSTRAINT fk_group FOREIGN KEY (group_id)
        REFERENCES document_groups(group_id)
);

CREATE TABLE document_groups (
    group_id    UUID PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ
);

CREATE TABLE section_summaries (
    summary_id      UUID PRIMARY KEY,
    doc_id          UUID REFERENCES documents(doc_id),
    section_path    TEXT,
    summary_text    TEXT,        -- LLM 生成的章节摘要
    key_points      JSONB,      -- 结构化要点
    token_count     INT,
    created_at      TIMESTAMPTZ
);
```

**2.3.2 预建摘要层（离线处理，文档入库时触发）**

```
文档入库完成（Phase 1 的 chunk 已存好）
    │
    ▼
[章节摘要生成] 对每个顶级章节：
    │  Prompt: "请对以下章节内容生成 200-300 字的摘要，
    │           提取 3-5 个关键要点，识别关键实体和数据。"
    │  Input:  章节下所有 chunks 拼接
    │  Output: { summary, key_points, entities }
    │  → 存入 section_summaries 表
    │  → summary 文本也做嵌入存入向量库（标记 chunk_type=section_summary）
    │
    ▼
[文档摘要生成] 基于所有章节摘要：
    │  Prompt: "基于以下各章节摘要，生成整份文档的 300-500 字总结。
    │           包含：文档类型、核心要点(3-5个)、关键数值/日期、涉及的主要实体。"
    │  Input:  所有 section_summaries 拼接
    │  Output: doc_summary
    │  → 更新 documents 表的 doc_summary 字段
    │  → doc_summary 也做嵌入存入向量库（标记 chunk_type=doc_summary）
    │
    ▼
[实体提取] 从文档中提取结构化信息：
    Prompt: "从以下文档摘要中提取关键实体..."
    Output: { 人名:[], 组织:[], 日期:[], 金额:[], 条款类型:[] }
    → 存入 documents.key_entities
```

**2.3.3 Contextual Retrieval（上下文增强嵌入）**

在 Phase 1 的基础上，为每个 chunk 增加上下文描述：

```python
def add_contextual_description(chunk: Chunk, doc: Document) -> str:
    """用 LLM 为 chunk 生成上下文描述，拼接后重新嵌入"""
    prompt = f"""
    <document_title>{doc.title}</document_title>
    <document_summary>{doc.doc_summary}</document_summary>
    <section_path>{chunk.section_path}</section_path>
    <chunk_content>{chunk.content}</chunk_content>
    
    请用 1-2 句话描述这个文本块在整个文档中的位置和作用。
    """
    context_desc = llm.generate(prompt)
    
    # 将描述拼在 chunk 前面，重新生成嵌入向量
    enriched_text = f"{context_desc}\n\n{chunk.content}"
    new_embedding = embed_model.encode(enriched_text)
    
    return new_embedding
```

**注意**：这一步会增加 Ingestion 的成本（每个 chunk 要调一次 LLM），但检索质量提升显著。可以用轻量级 LLM（如 Claude Haiku）降低成本。

**2.3.4 Query 理解与改写**

```python
class QueryRouter:
    def route(self, query: str, context: ConversationContext) -> QueryPlan:
        """分析用户问题，决定检索策略"""
        
        analysis = llm.generate(f"""
        分析以下用户问题，返回 JSON：
        问题：{query}
        
        返回格式：
        {{
          "query_type": "factual|summary|comparison|version_diff",
          "search_queries": ["改写后的检索query1", "query2"],
          "metadata_filters": {{
            "doc_type": "合同",      // 如果能识别出
            "department": null,
            "date_range": null,
            "specific_doc": null     // 如果提到了具体文档名
          }},
          "needs_multi_doc": true/false,
          "estimated_scope": "narrow|medium|broad"
        }}
        """)
        
        return QueryPlan.from_json(analysis)
```

**2.3.5 跨文档 Map-Reduce 总结**

```python
async def cross_document_summary(query: str, doc_ids: list[str]) -> str:
    """跨文档总结的核心流程"""
    
    # Step 1: 检索相关 chunks（从多个文档）
    relevant_chunks = retrieve(query, 
                                doc_ids=doc_ids, 
                                include_summaries=True,
                                top_k=30)
    
    # Step 2: 按文档分组
    chunks_by_doc = group_by_document(relevant_chunks)
    
    # Step 3: Map —— 对每个文档提取与问题相关的要点
    doc_extracts = []
    for doc_id, chunks in chunks_by_doc.items():
        extract = await llm.generate(f"""
        用户问题：{query}
        
        以下是来自《{chunks[0].doc_title}》的相关内容：
        {format_chunks(chunks)}
        
        请提取与用户问题直接相关的要点（3-5 条），每条附带来源页码。
        如果该文档与问题不太相关，回复"无相关内容"。
        """)
        if "无相关内容" not in extract:
            doc_extracts.append({
                "doc_title": chunks[0].doc_title,
                "doc_id": doc_id,
                "extract": extract
            })
    
    # Step 4: Reduce —— 综合所有文档的要点
    final_answer = await llm.generate(f"""
    用户问题：{query}
    
    以下是从 {len(doc_extracts)} 份文档中提取的相关要点：
    
    {format_extracts(doc_extracts)}
    
    请综合以上信息，生成一份结构清晰的回答：
    1. 先给出总体结论/概述
    2. 再分点展开细节
    3. 如果不同文档存在矛盾或差异，明确指出
    4. 每个论述点标注来源文档
    """)
    
    return final_answer
```

### 2.4 前端增强

- 文档管理界面：分组、标签、筛选
- 高级问答界面：
  - 可选择问答范围（全部文档 / 指定文档组 / 指定文档）
  - 多轮对话（保持上下文）
  - 答案中的引用可展开查看原文片段
  - 总结类答案显示"涉及 N 份文档"及文档列表

### 2.5 验证标准

| 指标                      | 目标                | 说明                             |
| ------------------------- | ------------------- | -------------------------------- |
| 跨文档总结准确率          | ≥ 70%               | 人工评审总结是否涵盖所有关键信息 |
| 跨文档总结完整度          | ≥ 75%               | 是否遗漏了重要文档               |
| Contextual Retrieval 提升 | Recall@5 提升 ≥ 10% | 对比 Phase 1 的 baseline         |
| Query 改写有效率          | ≥ 80%               | 改写后的检索结果优于原始 query   |
| 总结类查询响应时间        | < 30 秒             | 涉及 5-10 份文档时               |

### 2.6 交付物

- [x] 文档分组管理功能
- [x] 预建摘要层（章节 + 文档级）
- [x] Contextual Retrieval 实现
- [x] Query Router + Query 改写
- [x] 跨文档 Map-Reduce 总结
- [x] 多轮对话支持
- [x] 验证报告

---

## Phase 3：版本管理 & 差异对比（第 13-18 周）

### 3.1 目标

实现文档多版本管理、版本自动识别、三层差异对比。
解决"同一文档多个版本"和"版本间差异"的需求。

### 3.2 数据模型扩展

```sql
-- 在 Phase 2 的 documents 表基础上新增版本字段
ALTER TABLE documents ADD COLUMN version_number VARCHAR(20);
ALTER TABLE documents ADD COLUMN version_status VARCHAR(20) 
    DEFAULT 'active';  -- draft/active/superseded/archived
ALTER TABLE documents ADD COLUMN parent_version_id UUID 
    REFERENCES documents(doc_id);
ALTER TABLE documents ADD COLUMN is_latest BOOLEAN DEFAULT TRUE;
ALTER TABLE documents ADD COLUMN effective_date DATE;
ALTER TABLE documents ADD COLUMN superseded_at TIMESTAMPTZ;

-- 版本差异记录表
CREATE TABLE version_diffs (
    diff_id          UUID PRIMARY KEY,
    old_version_id   UUID REFERENCES documents(doc_id),
    new_version_id   UUID REFERENCES documents(doc_id),
    diff_type        VARCHAR(20),   -- text/structural/semantic
    
    -- 文本级差异
    text_diff_data   JSONB,         -- 段落级别的增删改记录
    
    -- 结构级差异
    structural_changes JSONB,       -- 章节增删、顺序调整
    
    -- 语义级差异（LLM 生成）
    change_summary   TEXT,          -- 变更概述
    change_details   JSONB,         -- 分类的变更条目
    impact_analysis  TEXT,          -- 影响分析
    
    created_at       TIMESTAMPTZ
);

-- 版本链索引（加速版本追溯查询）
CREATE INDEX idx_version_chain 
    ON documents(parent_version_id) WHERE parent_version_id IS NOT NULL;
CREATE INDEX idx_latest_version 
    ON documents(doc_type, is_latest) WHERE is_latest = TRUE;
```

### 3.3 核心功能模块

**3.3.1 版本自动识别（含新旧判断）**

```python
class VersionDetector:
    """判断新上传的文档是否为已有文档的新版本，并判断谁更新"""
    
    AUTO_LINK_THRESHOLD = 0.8  # 只有置信度高于此阈值才自动关联
    
    async def detect(self, new_doc_id, title, doc_summary, doc_type) -> VersionMatchResult:
        candidates = []
        
        # 策略 1: 标题相似度 (pg_trgm, similarity > 0.4)
        title_candidates = await self._find_by_title_similarity(
            title, new_doc_id, doc_type
        )
        candidates.extend(title_candidates)
        
        # 策略 2: 文档摘要向量相似度 (Qdrant, score > 0.75)
        if doc_summary:
            content_candidates = await self._find_by_content_similarity(
                doc_summary, new_doc_id
            )
            candidates.extend(content_candidates)
        
        if not candidates:
            return VersionMatchResult(is_new_version=False)
        
        # 去重后用 LLM 做最终判断（包含新旧版本判断）
        result = await self._llm_verify(title, doc_summary, unique_candidates)
        return result
    
    async def _llm_verify(self, new_title, new_summary, candidates) -> VersionMatchResult:
        """用 LLM 最终确认是否为同一文档的不同版本，并判断谁更新"""
        prompt = f"""
        新上传文档标题：{new_title}
        新上传文档摘要：{new_summary[:500]}
        
        候选已有文档：
        {format_candidates(candidates)}
        
        判断标准：
        1. 标题核心部分相同 + 内容主题一致 → 同一文档的不同版本
        2. 区分“同一文档的不同版本”和“同一类别但不同文档”
        3. 如果是同一文档，进一步判断谁是更新的版本，依据包括：
           - 文档内部的版本号（v1.0、v2.0、第X版等）
           - 文档中的日期（签署/生效/修订日期）
           - 内容范围变化
        
        返回 JSON:
        {{
          "is_new_version": true/false,
          "matched_doc_id": "...",
          "confidence": 0.95,
          "reason": "...",
          "new_is_newer": true/false,      // 上传文档是否确实比已有文档更新
          "detected_version": "v2.0"       // 从文档内容中提取的版本号
        }}
        """
        result = await llm.generate_json(prompt)
        return VersionMatchResult(
            is_new_version=result["is_new_version"] and result["confidence"] >= 0.8,
            matched_doc_id=result.get("matched_doc_id"),
            confidence=result["confidence"],
            reason=result["reason"],
            new_is_newer=result.get("new_is_newer", True),
            detected_version=result.get("detected_version"),
        )
```

**3.3.2 三层差异对比引擎**

```python
class DiffEngine:
    """三层差异对比"""
    
    async def compute_diff(self, old_doc_id: str, new_doc_id: str) -> VersionDiff:
        old_doc = await load_parsed_document(old_doc_id)
        new_doc = await load_parsed_document(new_doc_id)
        
        # Layer 1: 文本级差异（段落对齐 + diff）
        text_diff = self.compute_text_diff(old_doc, new_doc)
        
        # Layer 2: 结构级差异（章节增删改）
        structural_diff = self.compute_structural_diff(old_doc, new_doc)
        
        # Layer 3: 语义级差异（LLM 分析）
        semantic_diff = await self.compute_semantic_diff(
            old_doc, new_doc, text_diff, structural_diff
        )
        
        return VersionDiff(
            text_diff=text_diff,
            structural_diff=structural_diff,
            semantic_diff=semantic_diff
        )
    
    def compute_text_diff(self, old_doc, new_doc) -> TextDiff:
        """段落级别的文本对比"""
        # 将两个文档按章节对齐
        aligned_sections = self.align_sections(
            old_doc.sections, new_doc.sections
        )
        
        diffs = []
        for old_section, new_section in aligned_sections:
            if old_section is None:
                diffs.append(SectionDiff(type="added", 
                                         new=new_section))
            elif new_section is None:
                diffs.append(SectionDiff(type="deleted", 
                                         old=old_section))
            else:
                # 段落级 diff
                para_diffs = difflib.unified_diff(
                    old_section.paragraphs,
                    new_section.paragraphs,
                    lineterm=""
                )
                if para_diffs:
                    diffs.append(SectionDiff(
                        type="modified",
                        old=old_section,
                        new=new_section,
                        paragraph_diffs=para_diffs
                    ))
        return TextDiff(section_diffs=diffs)
    
    def compute_structural_diff(self, old_doc, new_doc) -> StructuralDiff:
        """章节结构对比"""
        old_toc = extract_toc(old_doc)   # 提取目录结构
        new_toc = extract_toc(new_doc)
        
        return StructuralDiff(
            added_sections=[s for s in new_toc if s not in old_toc],
            deleted_sections=[s for s in old_toc if s not in new_toc],
            reordered_sections=detect_reordering(old_toc, new_toc),
            renamed_sections=detect_renames(old_toc, new_toc)
        )
    
    async def compute_semantic_diff(self, old_doc, new_doc, 
                                     text_diff, structural_diff) -> SemanticDiff:
        """LLM 分析变更的业务含义"""
        prompt = f"""
        请分析以下两个版本文档之间的变更。

        文档标题：{new_doc.title}
        旧版本：{old_doc.version_number}
        新版本：{new_doc.version_number}

        结构变化：
        - 新增章节：{structural_diff.added_sections}
        - 删除章节：{structural_diff.deleted_sections}

        主要文本变化（摘要）：
        {summarize_text_diff(text_diff, max_length=2000)}

        请返回 JSON 格式的分析：
        {{
          "change_summary": "一段话概述主要变更",
          "changes": [
            {{
              "category": "实质性变更|措辞调整|格式变更|新增内容|删除内容",
              "description": "具体变更描述",
              "location": "涉及的章节",
              "old_text_snippet": "旧版原文关键片段",
              "new_text_snippet": "新版原文关键片段",
              "business_impact": "对业务的潜在影响"
            }}
          ],
          "risk_flags": ["需要注意的高风险变更"],
          "overall_impact": "总体影响评估"
        }}
        """
        return llm.generate_json(prompt)
```

**3.3.3 版本感知检索**

```python
class VersionAwareRetriever:
    """根据用户意图决定版本检索策略"""
    
    async def retrieve(self, query: str, query_plan: QueryPlan) -> list[Chunk]:
        
        if query_plan.query_type == "version_diff":
            # 版本对比模式：获取两个版本的内容
            return await self.retrieve_for_comparison(query, query_plan)
        
        elif query_plan.query_type == "version_history":
            # 版本追溯模式：获取所有版本的相关内容
            return await self.retrieve_all_versions(query, query_plan)
        
        else:
            # 默认模式：只检索最新版本
            filters = {
                **query_plan.metadata_filters,
                "is_latest": True      # ← 关键：默认只看最新版
            }
            return await self.standard_retrieve(query, filters)
    
    async def retrieve_for_comparison(self, query, query_plan):
        """版本对比检索"""
        old_version, new_version = identify_versions(query, query_plan)
        
        # 获取预计算的 diff 记录
        diff = await db.get_version_diff(old_version.doc_id, 
                                          new_version.doc_id)
        
        if diff:
            return diff  # 返回已有的对比结果
        else:
            # 触发实时计算
            diff = await diff_engine.compute_diff(
                old_version.doc_id, new_version.doc_id
            )
            await db.save_version_diff(diff)
            return diff
```

### 3.4 版本入库完整流程

```
新文档上传
    │
    ▼
[Phase 1: 解析 + 分块]
    │
    ▼
[Phase 2: 生成摘要 + 实体提取]
    │
    ▼
[版本检测] ──→ 是否为已有文档的不同版本？
    │              │
    │       是     │      否
    │       ▼      │      ▼
    │  [判断新旧]   │  [作为全新文档入库]
    │       │      │  │  全部 chunks is_latest=TRUE
    │  ┌────┴────┐
    │  │          │
    │  ▼          ▼
    │ 上传更新    上传更旧
    │ (new_is_newer=true)  (new_is_newer=false)
    │  │          │
    │  ▼          ▼
    │ [_link_version]     [_link_as_older_version]
    │  - 新文档.parent = 旧文档    - 新文档插入为已有文档的父版本
    │  - 新文档 is_latest=TRUE     - 新文档 is_latest=FALSE
    │  - 旧文档 is_latest=FALSE    - 已有文档保持 is_latest=TRUE
    │  - 旧文档 status=superseded  - 新文档 status=superseded
    │  │          │
    │  └────┬────┘
    │       ▼
    │  [触发差异计算（异步）]
    │  - 文本级 diff
    │  - 结构级 diff
    │  - 语义级 diff (LLM)
    │  - 存入 version_diffs 表
    │       │
    ▼       ▼
[上下文增强嵌入 + 存储]
    │  chunks 使用 is_doc_latest 决定 is_latest 标记
    ▼
[完成] 用户可查询最新版本、对比版本、追溯历史
```

### 3.5 前端新增

- 版本时间线视图（点击任一版本可查看详情）
- 双栏对比视图（红绿标注差异，类似 Git diff）
- 语义变更摘要卡片（分类展示：实质性变更 / 措辞调整 / 格式变更）
- 版本上传时的"识别为新版本"确认弹窗

### 3.6 验证标准

| 指标               | 目标                    |
| ------------------ | ----------------------- |
| 版本自动识别准确率 | ≥ 90%                   |
| 文本级 diff 准确率 | ≥ 95%                   |
| 语义变更摘要质量   | 人工评审 ≥ 80% 满意度   |
| 版本检索正确性     | 默认查询返回最新版 100% |
| diff 计算时间      | < 2 分钟 / 50 页文档对  |

### 3.7 交付物

- [x] 版本自动识别模块
- [x] 三层差异对比引擎
- [x] 版本感知检索
- [x] 版本管理 UI（时间线 + 对比视图）
- [x] 验证报告

---

## Phase 4：智能编排 & Agent 化（第 19-24 周）

### 4.1 目标

引入 Agent 模式，让系统能自主规划复杂查询的处理步骤。
将 Phase 1-3 的所有能力编排成可组合的工具集。

### 4.2 Agent 工具集定义

```python
# Agent 可调用的工具
TOOLS = [
    Tool(
        name="search_documents",
        description="在文档库中检索相关内容。支持语义检索和关键词检索。",
        params=["query", "doc_type_filter", "group_filter", 
                "date_range", "version_filter", "top_k"]
    ),
    Tool(
        name="read_document_summary",
        description="读取指定文档的整体摘要或指定章节的摘要。",
        params=["doc_id", "section_path"]  # section_path 为空则返回文档摘要
    ),
    Tool(
        name="read_document_detail",
        description="读取指定文档的特定章节或页码范围的详细内容。",
        params=["doc_id", "section_path", "page_range"]
    ),
    Tool(
        name="list_documents",
        description="列出符合条件的文档清单。",
        params=["doc_type", "group", "tags", "date_range", "status"]
    ),
    Tool(
        name="compare_versions",
        description="对比同一文档的两个版本之间的差异。",
        params=["doc_id", "old_version", "new_version"]
    ),
    Tool(
        name="get_version_history",
        description="获取某份文档的版本历史记录。",
        params=["doc_id"]
    ),
    Tool(
        name="cross_document_analysis",
        description="对多份文档的指定主题进行跨文档对比分析。",
        params=["doc_ids", "analysis_topic", "analysis_type"]
        # analysis_type: comparison | summary | extract_common | find_differences
    ),
    Tool(
        name="generate_report",
        description="基于已收集的信息生成结构化报告。",
        params=["collected_info", "report_format", "audience"]
    ),
]
```

### 4.3 Agent 核心逻辑

```python
class DocumentAgent:
    """文档分析 Agent，可自主规划和执行复杂查询"""
    
    SYSTEM_PROMPT = """
    你是一个企业文档分析助手。你可以使用以下工具来回答用户的问题：
    {tools_description}
    
    处理问题的原则：
    1. 先理解用户问题的类型和范围
    2. 对于简单事实性问题，直接检索后回答
    3. 对于总结性问题，先看文档摘要了解全局，再深入细节
    4. 对于对比类问题，分别获取各方信息后综合分析
    5. 对于版本相关问题，先查版本历史，再做针对性对比
    6. 每步操作后评估是否已有足够信息回答问题
    7. 答案必须标注信息来源
    
    思考步骤格式：
    Thought: 分析当前情况，决定下一步操作
    Action: 调用工具名
    Action Input: 工具参数
    Observation: 工具返回结果
    ... (重复直到有足够信息)
    Final Answer: 最终答案
    """
    
    async def run(self, query: str, context: ConversationContext) -> AgentResponse:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            *context.history,
            {"role": "user", "content": query}
        ]
        
        max_steps = 8  # 防止无限循环
        steps = []
        
        for step in range(max_steps):
            response = await llm.generate(messages, tools=TOOLS)
            
            if response.has_tool_call:
                # 执行工具调用
                tool_result = await self.execute_tool(
                    response.tool_name, response.tool_params
                )
                steps.append(AgentStep(
                    thought=response.thought,
                    action=response.tool_name,
                    observation=tool_result
                ))
                messages.append({"role": "assistant", "content": response.raw})
                messages.append({"role": "tool", "content": str(tool_result)})
            else:
                # Agent 认为信息足够，生成最终答案
                return AgentResponse(
                    answer=response.final_answer,
                    steps=steps,      # 可在前端展示推理过程
                    sources=extract_sources(steps)
                )
        
        # 超过最大步骤，强制生成答案
        return self.force_final_answer(messages, steps)
```

### 4.4 Query Router 升级

```python
class SmartQueryRouter:
    """智能路由：简单问题走快速通道，复杂问题走 Agent"""
    
    async def route(self, query: str, context) -> Response:
        intent = await self.classify_intent(query)
        
        match intent:
            case "simple_factual":
                # 快速通道：直接 RAG，不走 Agent
                return await simple_rag_pipeline(query)
            
            case "single_doc_query":
                # 单文档问答：带上下文扩展的 RAG
                return await enhanced_rag_pipeline(query)
            
            case "cross_doc_summary":
                # 跨文档总结：走 Agent
                return await document_agent.run(query, context)
            
            case "version_comparison":
                # 版本对比：走专用 workflow
                return await version_comparison_workflow(query, context)
            
            case "complex_analysis":
                # 复杂分析：走 Agent
                return await document_agent.run(query, context)
            
            case _:
                # 兜底：走 Agent
                return await document_agent.run(query, context)
```

### 4.5 前端增强

- Agent 推理过程可视化（展示"正在搜索..." → "正在分析..." → "生成答案..."）
- 用户可干预 Agent 步骤（如："不用看这份文档，看那份"）
- 复杂查询的进度指示器
- 收藏/分享分析结果

### 4.6 验证标准

| 指标             | 目标                          |
| ---------------- | ----------------------------- |
| Agent 路由准确率 | ≥ 90%（正确选择处理通道）     |
| 复杂查询成功率   | ≥ 75%（Agent 能给出有用答案） |
| Agent 平均步骤数 | ≤ 5 步（效率）                |
| Agent 超时率     | < 10%（8 步内完成）           |
| 用户满意度       | ≥ 80%                         |

### 4.7 交付物

- [x] Agent 框架 + 工具集
- [x] 智能 Query Router
- [x] 推理过程可视化 UI
- [x] 验证报告

---

## Phase 5：生产化加固 & 持续优化（第 25-30 周）

### 5.1 目标

将系统从"能用"提升到"好用 + 可靠 + 安全"。

### 5.2 工作清单

**5.2.1 权限与安全**

```python
# 检索时的权限过滤（在向量检索层实现）
async def search_with_permissions(query, user: User):
    # 获取用户可访问的文档列表
    accessible_docs = await get_user_accessible_docs(user.id)
    
    # 在向量检索时就做权限过滤（而非检索后过滤）
    results = await qdrant.search(
        query_vector=embed(query),
        query_filter=Filter(
            must=[
                FieldCondition(key="doc_id", 
                               match=MatchAny(any=accessible_docs))
            ]
        )
    )
    return results
```

核心安全措施清单：
- 文档级 RBAC 权限控制
- 检索层权限过滤（非生成层过滤）
- 审计日志（谁在什么时间查了什么，得到了什么结果）
- 数据加密（传输加密 TLS + 存储加密 AES-256）
- 敏感文档隔离存储
- LLM 输出安全审查（防止泄露敏感信息）

**5.2.2 性能优化**

- 嵌入计算批处理 + GPU 加速
- 热门查询缓存（Redis，版本更新时自动失效）
- 文档处理异步队列（上传即返回，后台处理）
- LLM 调用流式输出（Streaming SSE）
- 大文档分片并行处理

**5.2.3 可观测性**

```yaml
# 监控指标
metrics:
  - document_processing_duration_seconds    # 文档处理耗时
  - retrieval_latency_seconds               # 检索延迟
  - llm_call_duration_seconds               # LLM 调用耗时
  - llm_token_usage_total                   # Token 消耗
  - query_success_rate                      # 查询成功率
  - agent_step_count                        # Agent 步骤数
  - cache_hit_rate                          # 缓存命中率

# 告警规则
alerts:
  - query_latency > 30s for 5 minutes       # 查询延迟告警
  - llm_error_rate > 5% for 10 minutes      # LLM 错误率告警
  - document_processing_queue > 100         # 处理队列堆积
```

**5.2.4 持续评估 Pipeline**

```python
class EvaluationPipeline:
    """定期自动评估系统质量"""
    
    async def run_weekly_eval(self):
        test_set = load_test_questions()   # 标注好的测试集
        
        results = {
            "retrieval_recall": [],
            "answer_accuracy": [],
            "citation_accuracy": [],
            "latency": []
        }
        
        for question in test_set:
            response = await system.query(question.text)
            
            # 自动评估（用 LLM 作为评判）
            eval_result = await llm_judge.evaluate(
                question=question.text,
                expected_answer=question.ground_truth,
                actual_answer=response.answer,
                retrieved_chunks=response.sources,
                expected_sources=question.expected_sources
            )
            
            results["retrieval_recall"].append(eval_result.recall)
            results["answer_accuracy"].append(eval_result.accuracy)
            # ...
        
        # 生成评估报告 + 趋势对比
        report = generate_eval_report(results, previous_results)
        await notify_team(report)
```

**5.2.5 用户反馈闭环**

- 每个回答旁边的 👍👎 反馈按钮
- 👎 反馈自动收集到评估队列
- 定期分析 bad case，针对性优化
- 反馈数据反哺 Query Router 和检索策略的迭代

### 5.3 交付物

- [x] 权限系统 (RBAC)
- [x] 审计日志系统
- [x] 性能优化（缓存、异步、流式）
- [x] 监控告警 Dashboard
- [x] 自动评估 Pipeline
- [x] 用户反馈闭环
- [x] 运维手册 + API 文档

---

## 附录 A：成本估算参考

| 项目                       | Phase 1 月成本 | Phase 5 月成本 | 说明                        |
| -------------------------- | -------------- | -------------- | --------------------------- |
| LLM API (Claude Sonnet)    | ~$200          | ~$2,000        | 按 100 用户日均 20 查询估算 |
| GPU 服务器 (嵌入+Reranker) | ~$500          | ~$500          | 1 张 A10/L4 足够            |
| 云服务器 (应用+数据库)     | ~$300          | ~$800          | 根据用户量弹性扩展          |
| 对象存储                   | ~$10           | ~$50           | 按 1TB 文档估算             |
| **月总计**                 | **~$1,010**    | **~$3,350**    |                             |

> 注：如选择私有化部署 LLM（如 Qwen2.5-72B），需额外 GPU 成本但可消除 API 费用。

## 附录 B：团队配置建议

| Phase     | 最小团队               | 建议团队                              |
| --------- | ---------------------- | ------------------------------------- |
| Phase 0-1 | 1 全栈 + 1 ML/NLP      | 2 后端 + 1 前端 + 1 ML                |
| Phase 2-3 | 2 后端 + 1 ML          | 3 后端 + 1 前端 + 1 ML + 0.5 PM       |
| Phase 4-5 | 2 后端 + 1 ML + 1 前端 | 3 后端 + 2 前端 + 1 ML + 1 SRE + 1 PM |

## 附录 C：关键风险与应对

| 风险                   | 影响     | 应对策略                             |
| ---------------------- | -------- | ------------------------------------ |
| LLM API 不稳定/限流    | 查询失败 | 多 LLM 备选 + 降级策略               |
| 文档解析质量差         | 检索不准 | 多引擎组合 + 人工抽检                |
| 向量库数据量增长超预期 | 性能下降 | 分片策略 + 定期清理旧版本索引        |
| 跨文档总结幻觉         | 用户误导 | 强制引用 + 置信度评分 + 人工评审     |
| 版本识别误判           | 版本混乱 | 高阈值 + 人工确认兜底                |
| LLM 成本超预期         | 预算压力 | 缓存 + 小模型做轻量任务 + Token 监控 |
