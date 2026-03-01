export interface Document {
  doc_id: string;
  title: string;
  original_filename: string;
  file_size_bytes?: number | null;
  page_count?: number | null;
  doc_type?: string | null;
  department?: string | null;
  tags?: string[];
  group_id?: string | null;
  doc_summary?: string | null;
  key_entities?: Record<string, string[]>;
  processing_status: 'pending' | 'parsing' | 'chunking' | 'embedding' | 'summarizing' | 'ready' | 'error';
  chunk_count: number;
  // Phase 3: 版本管理
  version_number?: string;
  version_status?: string;
  is_latest?: boolean;
  parent_version_id?: string | null;
  created_at?: string | null;
}

export interface DocumentGroup {
  group_id: string;
  name: string;
  description?: string | null;
  created_at?: string | null;
}

export interface DocumentGroupCreate {
  name: string;
  description?: string | null;
}

export interface DocumentUpdate {
  group_id?: string | null;
  tags?: string[] | null;
  department?: string | null;
}

export interface DocumentUploadResponse {
  doc_id: string;
  message: string;
}

export interface DocumentListResponse {
  documents: Document[];
  total: number;
  page?: number;
  size?: number;
}

export interface Chunk {
  chunk_id: string;
  doc_id: string;
  chunk_index: number;
  content: string;
  chunk_type: string;
  metadata: any;
}

export interface Citation {
  doc_id: string;
  doc_title: string;
  section_path: string;
  page_numbers: number[];
  chunk_id: string;
  content_snippet: string;
}

export interface QueryRequest {
  question: string;
  doc_id?: string | null;
  group_id?: string | null;
  doc_type?: string | null;
  top_k?: number;
  version_mode?: string | null;
}

// Phase 3: 版本管理类型
export interface VersionInfo {
  doc_id: string;
  title: string;
  version_number: string;
  version_status: 'draft' | 'active' | 'superseded' | 'archived';
  is_latest: boolean;
  parent_version_id?: string | null;
  effective_date?: string | null;
  created_at?: string | null;
  chunk_count: number;
}

export interface VersionHistory {
  doc_id: string;
  title: string;
  versions: VersionInfo[];
}

export interface VersionDiffDetail {
  category: string;
  description: string;
  location: string;
  business_impact: string;
}

export interface VersionDiff {
  diff_id: string;
  old_version_id: string;
  new_version_id: string;
  old_title: string;
  new_title: string;
  text_diff_data: any;
  structural_changes: any;
  change_summary: string;
  change_details: VersionDiffDetail[];
  impact_analysis: string;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  confidence: number;
  latency_ms: number;
}


// ═══════════════════════════════════════════════════════════════════════════
// Phase 4: Agent 类型
// ═══════════════════════════════════════════════════════════════════════════

export interface AgentStep {
  step_number: number;
  thought: string;
  action: string;
  action_input?: Record<string, any>;
  observation_preview?: string;
  status: 'executing' | 'done' | 'complete';
  duration_ms?: number;
}

export interface RouteInfo {
  route: 'simple_rag' | 'enhanced_rag' | 'agent';
  query_type: string;
}


// ═══════════════════════════════════════════════════════════════════════════
// Phase 5: 认证 & 审计类型
// ═══════════════════════════════════════════════════════════════════════════

export interface User {
  user_id: string;
  username: string;
  email: string;
  display_name?: string;
  department?: string;
  role: string;
  is_active: boolean;
  created_at?: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  display_name?: string;
  department?: string;
}

export interface AuditLogEntry {
  log_id: string;
  user_id?: string;
  username?: string;
  action: string;
  resource_type?: string;
  resource_id?: string;
  details?: Record<string, any>;
  ip_address?: string;
  created_at?: string;
}
