import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

// ── auth ─────────────────────────────────────────────────────────────────────
export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface RegisterRequest {
  email: string;
  password: string;
  full_name: string;
}

// ── workspaces + cases ───────────────────────────────────────────────────────
export type WorkspaceRole = 'owner' | 'admin' | 'member';
export type CaseRole = 'lead' | 'investigator' | 'viewer';
export type CaseStatus = 'open' | 'in_progress' | 'closed' | 'archived';
export type CasePriority = 'low' | 'medium' | 'high' | 'critical';

export interface Workspace {
  id: string;
  name: string;
  kind: 'personal' | 'organization';
  my_role: WorkspaceRole;
  member_count: number;
  created_at: string;
}

export interface WorkspaceMember {
  user_id: string;
  email: string;
  full_name: string;
  role: WorkspaceRole;
  created_at: string;
}

export interface Case {
  id: string;
  workspace_id: string;
  reference_code: string | null;
  title: string;
  description: string;
  status: CaseStatus;
  priority: CasePriority;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  my_role: CaseRole;
  document_count: number;
}

export interface CaseCreate {
  title: string;
  description: string;
  reference_code: string | null;
  priority: CasePriority;
}

export type CaseUpdate = Partial<Pick<Case, 'title' | 'description' | 'reference_code' | 'status' | 'priority'>>;

export interface CaseMember {
  user_id: string;
  email: string;
  full_name: string;
  role: CaseRole;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  action: string;
  user_id: string | null;
  user_email: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

// ── documents, graph, query ──────────────────────────────────────────────────
export interface DocRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: 'uploaded' | 'indexing' | 'indexed' | 'error';
  error?: string;
  chunks: number;
  case_id: string;
  sha256: string;
  uploaded_by: string | null;
  created_at: string | null;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  description: string;
  community: number;
  degree: number;
  doc_count: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  weight: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: Record<string, string>;
}

export interface GraphStats {
  total_entities: number;
  total_relationships: number;
  total_communities: number;
  density: number;
  components: number;
  indexed_documents: number;
  raptor: { total_nodes: number; levels: Record<string, number> };
  hippo:  { total_nodes: number; levels: Record<string, number> };
}

export interface EntityDetail {
  id: string;
  name: string;
  type: string;
  description: string;
  neighbors: { id: string; name: string; type: string }[];
  doc_count: number;
  community: number;
}

export interface QueryResponse {
  answer: string;
  entities: { id: string; name: string; type: string }[];
  sources: string[];
  confidence: number;
  reasoning: string;
  method: string;
}

export interface ChatMessage {
  id: string;
  question: string;
  method: string;
  top_k: number;
  answer: string;
  reasoning: string;
  confidence: number;
  entities: { id: string; name: string; type: string }[];
  sources: string[];
  latency_ms: number;
  user_id: string | null;
  user_name: string | null;
  created_at: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = 'http://localhost:8000/api';
  // Auth calls carry the httpOnly refresh cookie (and the OAuth CSRF cookie).
  private withCookies = { withCredentials: true };

  constructor(private http: HttpClient) {}

  health(): Observable<{ status: string }> {
    return this.http.get<{ status: string }>(`${this.base}/health`);
  }

  // ── auth ───────────────────────────────────────────────────────────────────
  getAuthConfig(): Observable<{ google_enabled: boolean }> {
    return this.http.get<{ google_enabled: boolean }>(`${this.base}/auth/config`);
  }

  register(body: RegisterRequest): Observable<User> {
    return this.http.post<User>(`${this.base}/auth/register`, body);
  }

  login(email: string, password: string): Observable<TokenResponse> {
    // fastapi-users uses the OAuth2 password form: urlencoded username + password.
    const form = new HttpParams().set('username', email).set('password', password);
    return this.http.post<TokenResponse>(`${this.base}/auth/jwt/login`, form, this.withCookies);
  }

  googleAuthorizeUrl(): Observable<{ authorization_url: string }> {
    return this.http.get<{ authorization_url: string }>(`${this.base}/auth/google/authorize`, this.withCookies);
  }

  googleCallback(code: string, state: string): Observable<TokenResponse> {
    const params = new HttpParams().set('code', code).set('state', state);
    return this.http.get<TokenResponse>(`${this.base}/auth/google/callback`, { params, ...this.withCookies });
  }

  refresh(): Observable<TokenResponse> {
    return this.http.post<TokenResponse>(`${this.base}/auth/refresh`, {}, this.withCookies);
  }

  logout(): Observable<unknown> {
    return this.http.post(`${this.base}/auth/logout`, {}, this.withCookies);
  }

  me(): Observable<User> {
    return this.http.get<User>(`${this.base}/users/me`);
  }

  updateMe(body: { full_name?: string; password?: string }): Observable<User> {
    return this.http.patch<User>(`${this.base}/users/me`, body);
  }

  // ── workspaces ─────────────────────────────────────────────────────────────
  listWorkspaces(): Observable<Workspace[]> {
    return this.http.get<Workspace[]>(`${this.base}/workspaces`);
  }

  getWorkspace(id: string): Observable<Workspace> {
    return this.http.get<Workspace>(`${this.base}/workspaces/${id}`);
  }

  createWorkspace(name: string): Observable<Workspace> {
    return this.http.post<Workspace>(`${this.base}/workspaces`, { name });
  }

  renameWorkspace(id: string, name: string): Observable<Workspace> {
    return this.http.patch<Workspace>(`${this.base}/workspaces/${id}`, { name });
  }

  deleteWorkspace(id: string): Observable<unknown> {
    return this.http.delete(`${this.base}/workspaces/${id}`);
  }

  listWorkspaceMembers(id: string): Observable<WorkspaceMember[]> {
    return this.http.get<WorkspaceMember[]>(`${this.base}/workspaces/${id}/members`);
  }

  addWorkspaceMember(id: string, email: string, role: 'admin' | 'member'): Observable<WorkspaceMember> {
    return this.http.post<WorkspaceMember>(`${this.base}/workspaces/${id}/members`, { email, role });
  }

  updateWorkspaceMember(id: string, userId: string, role: 'admin' | 'member'): Observable<WorkspaceMember> {
    return this.http.patch<WorkspaceMember>(`${this.base}/workspaces/${id}/members/${userId}`, { role });
  }

  removeWorkspaceMember(id: string, userId: string): Observable<unknown> {
    return this.http.delete(`${this.base}/workspaces/${id}/members/${userId}`);
  }

  // ── cases ──────────────────────────────────────────────────────────────────
  listCases(workspaceId: string, status?: CaseStatus): Observable<Case[]> {
    const params = status ? new HttpParams().set('status', status) : undefined;
    return this.http.get<Case[]>(`${this.base}/workspaces/${workspaceId}/cases`, { params });
  }

  createCase(workspaceId: string, body: CaseCreate): Observable<Case> {
    return this.http.post<Case>(`${this.base}/workspaces/${workspaceId}/cases`, body);
  }

  getCase(caseId: string): Observable<Case> {
    return this.http.get<Case>(`${this.base}/cases/${caseId}`);
  }

  updateCase(caseId: string, body: CaseUpdate): Observable<Case> {
    return this.http.patch<Case>(`${this.base}/cases/${caseId}`, body);
  }

  deleteCase(caseId: string): Observable<unknown> {
    return this.http.delete(`${this.base}/cases/${caseId}`);
  }

  listCaseMembers(caseId: string): Observable<CaseMember[]> {
    return this.http.get<CaseMember[]>(`${this.base}/cases/${caseId}/members`);
  }

  addCaseMember(caseId: string, email: string, role: CaseRole): Observable<CaseMember> {
    return this.http.post<CaseMember>(`${this.base}/cases/${caseId}/members`, { email, role });
  }

  updateCaseMember(caseId: string, userId: string, role: CaseRole): Observable<CaseMember> {
    return this.http.patch<CaseMember>(`${this.base}/cases/${caseId}/members/${userId}`, { role });
  }

  removeCaseMember(caseId: string, userId: string): Observable<unknown> {
    return this.http.delete(`${this.base}/cases/${caseId}/members/${userId}`);
  }

  getCaseAudit(caseId: string, limit = 50): Observable<AuditEvent[]> {
    return this.http.get<AuditEvent[]>(`${this.base}/cases/${caseId}/audit`, { params: { limit } });
  }

  // ── documents ──────────────────────────────────────────────────────────────
  listDocuments(caseId: string): Observable<DocRecord[]> {
    return this.http.get<DocRecord[]>(`${this.base}/cases/${caseId}/documents`);
  }

  uploadDocument(caseId: string, file: File): Observable<DocRecord> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<DocRecord>(`${this.base}/cases/${caseId}/documents/upload`, fd);
  }

  deleteDocument(caseId: string, id: string): Observable<unknown> {
    return this.http.delete(`${this.base}/cases/${caseId}/documents/${id}`);
  }

  // ── graph ──────────────────────────────────────────────────────────────────
  getGraph(caseId: string): Observable<GraphData> {
    return this.http.get<GraphData>(`${this.base}/cases/${caseId}/graph`);
  }

  getStats(caseId: string): Observable<GraphStats> {
    return this.http.get<GraphStats>(`${this.base}/cases/${caseId}/graph/stats`);
  }

  getEntity(caseId: string, id: string): Observable<EntityDetail> {
    return this.http.get<EntityDetail>(`${this.base}/cases/${caseId}/graph/entity/${id}`);
  }

  // ── query + chat history ───────────────────────────────────────────────────
  query(caseId: string, question: string, method = 'hybrid', top_k = 6): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(`${this.base}/cases/${caseId}/query`, { question, method, top_k });
  }

  getSuggestions(caseId: string): Observable<{ suggestions: string[] }> {
    return this.http.get<{ suggestions: string[] }>(`${this.base}/cases/${caseId}/query/suggestions`);
  }

  getChatHistory(caseId: string, limit = 100): Observable<ChatMessage[]> {
    return this.http.get<ChatMessage[]>(`${this.base}/cases/${caseId}/chat`, { params: { limit } });
  }

  clearChatHistory(caseId: string): Observable<unknown> {
    return this.http.delete(`${this.base}/cases/${caseId}/chat`);
  }
}
