import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface DocRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: 'uploaded' | 'indexing' | 'indexed' | 'error';
  error?: string;
  chunks: number;
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

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = '/api';

  constructor(private http: HttpClient) {}

  health(): Observable<{ status: string }> {
    return this.http.get<{ status: string }>(`${this.base}/health`);
  }

  // ── documents ──────────────────────────────────────────────────────────────
  listDocuments(): Observable<DocRecord[]> {
    return this.http.get<DocRecord[]>(`${this.base}/documents`);
  }

  uploadDocument(file: File): Observable<DocRecord> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<DocRecord>(`${this.base}/documents/upload`, fd);
  }

  deleteDocument(id: string): Observable<unknown> {
    return this.http.delete(`${this.base}/documents/${id}`);
  }

  // ── graph ──────────────────────────────────────────────────────────────────
  getGraph(): Observable<GraphData> {
    return this.http.get<GraphData>(`${this.base}/graph`);
  }

  getStats(): Observable<GraphStats> {
    return this.http.get<GraphStats>(`${this.base}/graph/stats`);
  }

  getEntity(id: string): Observable<EntityDetail> {
    return this.http.get<EntityDetail>(`${this.base}/graph/entity/${id}`);
  }

  // ── query ──────────────────────────────────────────────────────────────────
  query(question: string, method = 'hybrid', top_k = 6): Observable<QueryResponse> {
    return this.http.post<QueryResponse>(`${this.base}/query`, { question, method, top_k });
  }

  getSuggestions(): Observable<{ suggestions: string[] }> {
    return this.http.get<{ suggestions: string[] }>(`${this.base}/query/suggestions`);
  }
}
