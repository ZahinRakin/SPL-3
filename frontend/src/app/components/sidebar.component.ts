import { Component, Input, OnInit, Output, EventEmitter, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService, DocRecord, GraphStats } from '../services/api.service';
import { interval } from 'rxjs';
import { switchMap } from 'rxjs/operators';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [CommonModule],
  template: `
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-title">Documents</div>
        <button class="add-btn" (click)="uploadClick.emit()" title="Upload documents">
          <svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13">
            <path d="M8 2a1 1 0 0 1 1 1v4h4a1 1 0 1 1 0 2H9v4a1 1 0 1 1-2 0V9H3a1 1 0 0 1 0-2h4V3a1 1 0 0 1 1-1z"/>
          </svg>
        </button>
      </div>

      <div class="doc-list">
        @if (docs().length === 0) {
          <div class="doc-empty">
            <div class="doc-empty-icon">📄</div>
            <div class="doc-empty-text">No documents yet</div>
            <button class="doc-empty-btn" (click)="uploadClick.emit()">Upload files</button>
          </div>
        }

        @for (doc of docs(); track doc.id) {
          <div class="doc-item" [class.indexing]="doc.status === 'indexing'" [class.error]="doc.status === 'error'">
            <div class="doc-icon">{{ fileIcon(doc.filename) }}</div>
            <div class="doc-info">
              <div class="doc-name" [title]="doc.filename">{{ doc.filename }}</div>
              <div class="doc-meta">
                {{ formatSize(doc.size_bytes) }}
                @if (doc.status === 'indexing') {
                  · <span class="status-indexing">indexing…</span>
                } @else if (doc.status === 'indexed') {
                  · <span class="status-indexed">{{ doc.chunks }} chunks</span>
                } @else if (doc.status === 'error') {
                  · <span class="status-error">error</span>
                }
              </div>
            </div>
            <button class="doc-delete" (click)="deleteDoc(doc.id)" title="Remove">✕</button>
          </div>
        }
      </div>

      <div class="sidebar-stats">
        <div class="stat-item">
          <span class="stat-val accent">{{ stats?.total_entities ?? '—' }}</span>
          <span class="stat-lbl">Entities</span>
        </div>
        <div class="stat-sep"></div>
        <div class="stat-item">
          <span class="stat-val green">{{ stats?.total_relationships ?? '—' }}</span>
          <span class="stat-lbl">Relations</span>
        </div>
        <div class="stat-sep"></div>
        <div class="stat-item">
          <span class="stat-val purple">{{ stats?.total_communities ?? '—' }}</span>
          <span class="stat-lbl">Clusters</span>
        </div>
      </div>
    </aside>
  `,
  styles: [`
    .sidebar {
      width: 240px; flex-shrink: 0; display: flex; flex-direction: column;
      border-right: 1px solid var(--border-subtle); background: var(--bg-surface);
      overflow: hidden;
    }

    .sidebar-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 14px 16px 10px; flex-shrink: 0;
    }
    .sidebar-title { font-size: 12px; font-weight: 600; color: var(--text-secondary); letter-spacing: 0.05em; text-transform: uppercase; }
    .add-btn {
      width: 26px; height: 26px; border-radius: var(--radius-sm);
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-secondary); cursor: pointer; display: flex; align-items: center; justify-content: center;
      transition: all 0.15s;
      &:hover { background: var(--accent-dim); border-color: rgba(79,135,255,0.3); color: var(--accent); }
    }

    .doc-list { flex: 1; overflow-y: auto; padding: 4px 8px 8px; }

    .doc-empty {
      display: flex; flex-direction: column; align-items: center;
      padding: 32px 16px; text-align: center; gap: 8px;
    }
    .doc-empty-icon { font-size: 28px; opacity: 0.3; }
    .doc-empty-text { font-size: 12px; color: var(--text-tertiary); }
    .doc-empty-btn {
      margin-top: 4px; padding: 6px 14px; border-radius: var(--radius-md);
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-secondary); font-size: 12px; cursor: pointer;
      transition: all 0.15s;
      &:hover { border-color: var(--accent); color: var(--accent); }
    }

    .doc-item {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 10px; border-radius: var(--radius-md);
      margin-bottom: 2px; transition: background 0.15s;
      &:hover { background: var(--bg-elevated); }
      &:hover .doc-delete { opacity: 1; }
      &.indexing { border-left: 2px solid var(--amber); padding-left: 8px; }
      &.error    { border-left: 2px solid var(--red);   padding-left: 8px; }
    }
    .doc-icon { font-size: 18px; flex-shrink: 0; }
    .doc-info { flex: 1; min-width: 0; }
    .doc-name {
      font-size: 12px; font-weight: 500; color: var(--text-primary);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .doc-meta { font-size: 10px; color: var(--text-tertiary); margin-top: 1px; }
    .status-indexing { color: var(--amber); }
    .status-indexed  { color: var(--green); }
    .status-error    { color: var(--red); }
    .doc-delete {
      opacity: 0; background: none; border: none; color: var(--text-tertiary);
      cursor: pointer; font-size: 10px; flex-shrink: 0; padding: 2px;
      transition: color 0.15s;
      &:hover { color: var(--red); }
    }

    .sidebar-stats {
      display: flex; align-items: center; padding: 14px 16px;
      border-top: 1px solid var(--border-subtle); flex-shrink: 0;
    }
    .stat-item {
      flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px;
    }
    .stat-sep { width: 1px; height: 28px; background: var(--border-subtle); }
    .stat-val { font-size: 18px; font-weight: 700; &.accent{color:var(--accent)} &.green{color:var(--green)} &.purple{color:var(--purple)} }
    .stat-lbl { font-size: 9px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.05em; }
  `],
})
export class SidebarComponent implements OnInit {
  @Input() stats: GraphStats | null = null;
  @Output() uploadClick = new EventEmitter<void>();

  docs = signal<DocRecord[]>([]);

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.refresh();
    setInterval(() => this.refresh(), 4000);
  }

  refresh() {
    this.api.listDocuments().subscribe({
      next: d => this.docs.set(d),
      error: () => {},
    });
  }

  deleteDoc(id: string) {
    this.api.deleteDocument(id).subscribe({ next: () => this.refresh() });
  }

  fileIcon(name: string): string {
    const ext = name.split('.').pop()?.toLowerCase() ?? '';
    if (ext === 'pdf')  return '📄';
    if (ext === 'docx' || ext === 'doc') return '📝';
    if (ext === 'txt')  return '📃';
    if (ext === 'html') return '🌐';
    return '📋';
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
}
