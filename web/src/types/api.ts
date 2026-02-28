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
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  confidence: number;
  latency_ms: number;
}
