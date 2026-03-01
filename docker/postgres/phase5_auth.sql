-- ==========================================================================
-- DocAI Platform - Phase 5: 权限控制 & 审计日志
-- 执行方式: psql -U docai -d docai -f docker/postgres/phase5_auth.sql
-- ==========================================================================

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username      VARCHAR(100) UNIQUE NOT NULL,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(200),
    department    VARCHAR(100),
    role          VARCHAR(50) NOT NULL DEFAULT 'viewer',
        -- admin / editor / viewer / restricted
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_department ON users(department);

-- 文档权限表 (用户 ↔ 文档/文档组/部门 的授权关系)
CREATE TABLE IF NOT EXISTS document_permissions (
    perm_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    -- 三个维度至少填一个
    doc_id        UUID REFERENCES documents(doc_id) ON DELETE CASCADE,
    group_id      UUID REFERENCES document_groups(group_id) ON DELETE CASCADE,
    department    VARCHAR(100),
    permission    VARCHAR(20) NOT NULL DEFAULT 'read',
        -- read / write / admin
    granted_by    UUID REFERENCES users(user_id),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    -- 约束：三个维度至少选一个
    CONSTRAINT chk_perm_target CHECK (
        doc_id IS NOT NULL OR group_id IS NOT NULL OR department IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_doc_perms_user ON document_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_doc_perms_doc ON document_permissions(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_perms_group ON document_permissions(group_id);
CREATE INDEX IF NOT EXISTS idx_doc_perms_dept ON document_permissions(department);

-- 审计日志表 (替代旧版 query_audit_log，更通用)
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID REFERENCES users(user_id),
    username      VARCHAR(100),
    action        VARCHAR(50) NOT NULL,
        -- login / logout / query / upload / delete / view / download / update_meta
        -- grant_permission / revoke_permission
    resource_type VARCHAR(50),
        -- document / group / version / user / permission
    resource_id   VARCHAR(255),
    details       JSONB DEFAULT '{}',
    ip_address    VARCHAR(45),
    user_agent    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);

-- documents 表新增字段
ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(user_id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'public';
    -- public: 全员可见
    -- department: 同部门可见
    -- private: 仅授权可见

CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_documents_visibility ON documents(visibility);

-- users 表也加 updated_at 触发器
CREATE TRIGGER trg_users_updated
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 插入默认管理员账号 (密码: admin123，bcrypt hash)
-- 注意: 生产环境部署后务必修改此密码
INSERT INTO users (username, email, password_hash, display_name, role)
VALUES (
    'admin',
    'admin@docai.local',
    '$2b$12$LJ3m4ys3Ld5H1Xq5eK6Jn.QGrKQh0xBqZ3JH1yK8vN0wX9pZm6Uy',
    '系统管理员',
    'admin'
) ON CONFLICT (username) DO NOTHING;

DO $$
BEGIN
    RAISE NOTICE '✅ Phase 5 auth tables created successfully';
END $$;
