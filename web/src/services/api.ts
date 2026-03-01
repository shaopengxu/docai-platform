import {
  Document,
  DocumentGroup,
  DocumentGroupCreate,
  DocumentListResponse,
  DocumentUpdate,
  DocumentUploadResponse,
  QueryRequest,
  QueryResponse,
  Chunk,
  VersionHistory,
  VersionDiff,
  AgentStep,
  RouteInfo,
  LoginRequest,
  LoginResponse,
  User,
  RegisterRequest,
} from '../types/api';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

// ── Token 管理 ──

function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('docai_token');
}

function setStoredToken(token: string): void {
  localStorage.setItem('docai_token', token);
}

function clearStoredToken(): void {
  localStorage.removeItem('docai_token');
  localStorage.removeItem('docai_user');
}

function getAuthHeaders(): Record<string, string> {
  const token = getStoredToken();
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

export { getStoredToken, setStoredToken, clearStoredToken };

export class ApiClient {

  // --- Auth (Phase 5) ---

  static async login(data: LoginRequest): Promise<LoginResponse> {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || '登录失败');
    }
    const result: LoginResponse = await response.json();
    setStoredToken(result.access_token);
    localStorage.setItem('docai_user', JSON.stringify(result.user));
    return result;
  }

  static async register(data: RegisterRequest): Promise<User> {
    const response = await fetch(`${API_BASE_URL}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || '注册失败');
    }
    return response.json();
  }

  static async getMe(): Promise<User> {
    const response = await fetch(`${API_BASE_URL}/auth/me`, {
      headers: { ...getAuthHeaders() },
    });
    if (!response.ok) throw new Error('未登录');
    return response.json();
  }

  static logout(): void {
    clearStoredToken();
  }

  static getStoredUser(): User | null {
    if (typeof window === 'undefined') return null;
    const raw = localStorage.getItem('docai_user');
    return raw ? JSON.parse(raw) : null;
  }
  // --- Document Groups ---

  static async createDocumentGroup(data: DocumentGroupCreate): Promise<DocumentGroup> {
    const response = await fetch(`${API_BASE_URL}/documents/groups`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create group: ${response.statusText}`);
    }
    return response.json();
  }

  static async getDocumentGroups(): Promise<DocumentGroup[]> {
    const response = await fetch(`${API_BASE_URL}/documents/groups`);
    if (!response.ok) {
      throw new Error(`Failed to fetch groups: ${response.statusText}`);
    }
    return response.json();
  }

  static async updateDocumentMetadata(docId: string, data: DocumentUpdate): Promise<Document> {
    const response = await fetch(`${API_BASE_URL}/documents/${docId}/metadata`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to update metadata: ${response.statusText}`);
    }
    return response.json();
  }

  // --- Documents ---
  static async uploadDocument(file: File): Promise<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/documents`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`Failed to upload document: ${response.statusText}`);
    }

    return response.json();
  }

  static async getDocuments(page = 1, size = 50): Promise<DocumentListResponse> {
    const offset = (page - 1) * size;
    const response = await fetch(`${API_BASE_URL}/documents?limit=${size}&offset=${offset}`);

    if (!response.ok) {
      throw new Error(`Failed to fetch documents: ${response.statusText}`);
    }

    return response.json();
  }

  static async getDocument(docId: string): Promise<Document> {
    const response = await fetch(`${API_BASE_URL}/documents/${docId}`);

    if (!response.ok) {
      throw new Error(`Failed to fetch document: ${response.statusText}`);
    }

    return response.json();
  }

  static async deleteDocument(docId: string): Promise<{ message: string }> {
    const response = await fetch(`${API_BASE_URL}/documents/${docId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      throw new Error(`Failed to delete document: ${response.statusText}`);
    }

    return response.json();
  }

  static async getDocumentChunks(docId: string): Promise<{ items: Chunk[], total: number }> {
    const response = await fetch(`${API_BASE_URL}/documents/${docId}/chunks`);

    if (!response.ok) {
      throw new Error(`Failed to fetch document chunks: ${response.statusText}`);
    }

    return response.json();
  }

  static async query(request: QueryRequest): Promise<QueryResponse> {
    const response = await fetch(`${API_BASE_URL}/query`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      throw new Error(`Query failed: ${response.statusText}`);
    }

    return response.json();
  }

  // --- Version Management ---

  static async getVersionHistory(docId: string): Promise<VersionHistory> {
    const response = await fetch(`${API_BASE_URL}/versions/${docId}/history`);
    if (!response.ok) {
      throw new Error(`Failed to fetch version history: ${response.statusText}`);
    }
    return response.json();
  }

  static async getVersionDiff(docId: string, otherDocId: string): Promise<VersionDiff> {
    const response = await fetch(`${API_BASE_URL}/versions/${docId}/diff/${otherDocId}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch version diff: ${response.statusText}`);
    }
    return response.json();
  }

  static async linkVersion(docId: string, parentVersionId: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/versions/${docId}/link`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_version_id: parentVersionId }),
    });
    if (!response.ok) {
      throw new Error(`Failed to link version: ${response.statusText}`);
    }
    return response.json();
  }

  static async updateVersionStatus(docId: string, versionStatus: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/versions/${docId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version_status: versionStatus }),
    });
    if (!response.ok) {
      throw new Error(`Failed to update version status: ${response.statusText}`);
    }
    return response.json();
  }

  // --- Query (Phase 4: Agent-aware streaming) ---

  static async queryStream(
    request: QueryRequest,
    onChunk: (chunk: string) => void,
    onSources: (citations: any[]) => void,
    onError: (error: Error) => void,
    onAgentStep?: (step: AgentStep) => void,
    onRouteInfo?: (info: RouteInfo) => void,
  ): Promise<void> {
    try {
      const response = await fetch(`${API_BASE_URL}/query/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        throw new Error(`Stream query failed: ${response.statusText}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) {
        throw new Error('No reader available');
      }

      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process SSE lines
        const lines = buffer.split('\n');
        // Keep the last partial line in the buffer
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.substring(6);
            if (data === '[DONE]') {
              return;
            }

            try {
              const parsed = JSON.parse(data);
              switch (parsed.type) {
                case 'token':
                  onChunk(parsed.content);
                  break;
                case 'sources':
                  onSources(parsed.citations);
                  break;
                case 'error':
                  onError(new Error(parsed.message || 'Stream error'));
                  break;
                case 'agent_step':
                  onAgentStep?.(parsed as AgentStep);
                  break;
                case 'route_info':
                  onRouteInfo?.(parsed as RouteInfo);
                  break;
                case 'done':
                  return;
              }
            } catch (e) {
              console.error('Failed to parse SSE data:', data, e);
            }
          }
        }
      }
    } catch (err) {
      onError(err instanceof Error ? err : new Error(String(err)));
    }
  }
}
