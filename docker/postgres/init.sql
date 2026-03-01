-- ==========================================================================
-- DocAI Platform - Database Schema
-- 包含 Phase 1-5 的完整表结构
-- ==========================================================================

-- 启用必要扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- 文本相似度（版本识别用）

-- ==========================================================================
-- Phase 1: 核心表
-- ==========================================================================

-- 文档组（Phase 2 使用，预先建好）
CREATE TABLE document_groups (
    group_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 文档主表
CREATE TABLE documents (
    doc_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_path       TEXT NOT NULL,          -- MinIO 中的路径
    file_size_bytes BIGINT,
    file_hash       VARCHAR(64),             -- SHA-256 文件指纹（去重用）
    mime_type       VARCHAR(100),
    page_count      INT,

    -- 分类与标签
    doc_type        VARCHAR(50),            -- contract/report/policy/manual/...
    department      VARCHAR(100),
    tags            TEXT[] DEFAULT '{}',
    group_id        UUID REFERENCES document_groups(group_id),

    -- 处理状态
    processing_status VARCHAR(20) DEFAULT 'pending',
        -- pending → parsing → chunking → embedding → summarizing → ready → error
    processing_error  TEXT,
    chunk_count       INT DEFAULT 0,

    -- Phase 2: 摘要（预留）
    doc_summary     TEXT,
    key_entities    JSONB DEFAULT '{}',

    -- Phase 3: 版本管理（预留）
    version_number    VARCHAR(20) DEFAULT 'v1.0',
    version_status    VARCHAR(20) DEFAULT 'active',
        -- draft / active / superseded / archived
    parent_version_id UUID REFERENCES documents(doc_id),
    is_latest         BOOLEAN DEFAULT TRUE,
    effective_date    DATE,
    superseded_at     TIMESTAMPTZ,

    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_documents_status ON documents(processing_status);
CREATE INDEX idx_documents_type ON documents(doc_type);
CREATE INDEX idx_documents_group ON documents(group_id);
CREATE INDEX idx_documents_latest ON documents(is_latest) WHERE is_latest = TRUE;
CREATE INDEX idx_documents_title_trgm ON documents USING gin(title gin_trgm_ops);
CREATE INDEX idx_documents_tags ON documents USING gin(tags);
CREATE UNIQUE INDEX idx_documents_file_hash ON documents(file_hash)
    WHERE file_hash IS NOT NULL AND processing_status != 'error';

-- 文档 chunks 元数据（向量存在 Qdrant，全文存在 ES，这里存元数据）
CREATE TABLE chunks (
    chunk_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,

    -- 内容定位
    section_path    TEXT,                   -- "第三章 > 3.2 付款条款"
    page_numbers    INT[] DEFAULT '{}',
    chunk_index     INT NOT NULL,           -- 在文档中的顺序

    -- 内容
    content         TEXT NOT NULL,
    chunk_type      VARCHAR(30) DEFAULT 'text',
        -- text / table / image_description / section_summary / doc_summary
    token_count     INT,

    -- 向量库引用（用于回溯和删除）
    qdrant_point_id UUID,
    es_doc_id       VARCHAR(100),

    -- 时间戳
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_chunks_doc ON chunks(doc_id);
CREATE INDEX idx_chunks_type ON chunks(chunk_type);
CREATE INDEX idx_chunks_section ON chunks(section_path);

-- ==========================================================================
-- Phase 2: 摘要表（预留）
-- ==========================================================================

CREATE TABLE section_summaries (
    summary_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    section_path    TEXT NOT NULL,
    summary_text    TEXT NOT NULL,
    key_points      JSONB DEFAULT '[]',
    token_count     INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_section_summaries_doc ON section_summaries(doc_id);

-- ==========================================================================
-- Phase 3: 版本差异表（预留）
-- ==========================================================================

CREATE TABLE version_diffs (
    diff_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    old_version_id   UUID NOT NULL REFERENCES documents(doc_id),
    new_version_id   UUID NOT NULL REFERENCES documents(doc_id),

    -- 文本级差异
    text_diff_data   JSONB DEFAULT '{}',

    -- 结构级差异
    structural_changes JSONB DEFAULT '{}',

    -- 语义级差异（LLM 生成）
    change_summary   TEXT,
    change_details   JSONB DEFAULT '[]',
    impact_analysis  TEXT,

    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_version_diffs_versions
    ON version_diffs(old_version_id, new_version_id);

-- ==========================================================================
-- 通用：处理任务队列（用于异步文档处理）
-- ==========================================================================

CREATE TABLE processing_tasks (
    task_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    task_type       VARCHAR(30) NOT NULL,
        -- parse / chunk / embed / summarize / diff
    status          VARCHAR(20) DEFAULT 'pending',
        -- pending / running / completed / failed
    priority        INT DEFAULT 5,          -- 1=highest, 10=lowest
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error_message   TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tasks_status ON processing_tasks(status, priority);
CREATE INDEX idx_tasks_doc ON processing_tasks(doc_id);

-- ==========================================================================
-- 通用：查询审计日志（Phase 5 使用，预留）
-- ==========================================================================

CREATE TABLE query_audit_log (
    log_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         VARCHAR(100),
    query_text      TEXT NOT NULL,
    query_type      VARCHAR(30),
    response_summary TEXT,
    source_doc_ids  UUID[] DEFAULT '{}',
    latency_ms      INT,
    feedback        VARCHAR(10),            -- thumbs_up / thumbs_down
    feedback_comment TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_created ON query_audit_log(created_at DESC);
CREATE INDEX idx_audit_feedback ON query_audit_log(feedback)
    WHERE feedback IS NOT NULL;

-- ==========================================================================
-- 辅助函数
-- ==========================================================================

-- 自动更新 updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documents_updated
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_groups_updated
    BEFORE UPDATE ON document_groups
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 打印初始化完成信息
DO $$
BEGIN
    RAISE NOTICE '✅ DocAI database initialized successfully';
END $$;
