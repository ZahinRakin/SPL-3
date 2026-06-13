import { Component, OnInit, ViewChild, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SidebarComponent } from './components/sidebar.component';
import { GraphViewComponent } from './components/graph-view.component';
import { QaPanelComponent } from './components/qa-panel.component';
import { UploadModalComponent } from './components/upload-modal.component';
import { ApiService, GraphStats } from './services/api.service';

export type ActiveTab = 'graph' | 'qa' | 'analytics';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, SidebarComponent, GraphViewComponent, QaPanelComponent, UploadModalComponent],
  template: `
    <div class="shell">
      <!-- top bar -->
      <header class="topbar">
        <div class="brand">
          <svg class="logo-icon" viewBox="0 0 28 28" fill="none">
            <circle cx="7" cy="8"  r="4" fill="#4f87ff"/>
            <circle cx="21" cy="8"  r="4" fill="#34d399"/>
            <circle cx="14" cy="21" r="4" fill="#a78bfa"/>
            <line x1="7"  y1="8"  x2="21" y2="8"  stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>
            <line x1="7"  y1="8"  x2="14" y2="21" stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>
            <line x1="21" y1="8"  x2="14" y2="21" stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>
          </svg>
          <span class="brand-name">GraphRAG <span class="brand-sub">Intelligence</span></span>
        </div>

        <nav class="tabs">
          @for (tab of tabs; track tab.id) {
            <button class="tab" [class.active]="activeTab() === tab.id" (click)="setTab(tab.id)">
              <span class="tab-icon">{{ tab.icon }}</span>
              {{ tab.label }}
            </button>
          }
        </nav>

        <div class="topbar-right">
          <div class="status-pill" [class.ok]="apiOk()" [class.err]="!apiOk()">
            <span class="status-dot"></span>
            {{ apiOk() ? 'API Connected' : 'API Offline' }}
          </div>
          <button class="btn-primary" (click)="showUpload.set(true)">
            <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14">
              <path d="M10 3a1 1 0 0 1 .707.293l4 4a1 1 0 0 1-1.414 1.414L11 6.414V14a1 1 0 1 1-2 0V6.414L6.707 8.707a1 1 0 0 1-1.414-1.414l4-4A1 1 0 0 1 10 3Z"/>
              <path d="M3 15a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H4a1 1 0 0 1-1-1Z"/>
            </svg>
            Upload Documents
          </button>
        </div>
      </header>

      <!-- body -->
      <div class="body">
        <app-sidebar [stats]="stats()" (uploadClick)="showUpload.set(true)"/>

        <main class="main-area">
          @if (activeTab() === 'graph') {
            <app-graph-view/>
          }
          @if (activeTab() === 'qa') {
            <app-qa-panel/>
          }
          @if (activeTab() === 'analytics') {
            <div class="analytics-placeholder">
              <div class="analytics-grid">
                <div class="a-card">
                  <div class="a-label">Total Entities</div>
                  <div class="a-value accent">{{ stats()?.total_entities ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">Relationships</div>
                  <div class="a-value green">{{ stats()?.total_relationships ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">Communities</div>
                  <div class="a-value purple">{{ stats()?.total_communities ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">Documents Indexed</div>
                  <div class="a-value amber">{{ stats()?.indexed_documents ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">Graph Density</div>
                  <div class="a-value">{{ stats()?.density ?? 0 | number:'1.4-4' }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">RAPTOR Nodes</div>
                  <div class="a-value cyan">{{ stats()?.raptor?.total_nodes ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">HiPPO Nodes</div>
                  <div class="a-value">{{ stats()?.hippo?.total_nodes ?? 0 }}</div>
                </div>
                <div class="a-card">
                  <div class="a-label">Connected Components</div>
                  <div class="a-value">{{ stats()?.components ?? 0 }}</div>
                </div>
              </div>
            </div>
          }
        </main>
      </div>
    </div>

    @if (showUpload()) {
      <app-upload-modal (close)="onUploadClose()"/>
    }
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .shell {
      display: flex; flex-direction: column; height: 100%;
      background: var(--bg-base); overflow: hidden;
    }

    /* topbar */
    .topbar {
      display: flex; align-items: center; gap: 16px;
      padding: 0 20px; height: 52px; flex-shrink: 0;
      border-bottom: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }
    .brand { display: flex; align-items: center; gap: 9px; flex-shrink: 0; }
    .logo-icon { width: 28px; height: 28px; }
    .brand-name {
      font-size: 15px; font-weight: 700; color: var(--text-primary);
      letter-spacing: -0.3px;
    }
    .brand-sub { color: var(--text-secondary); font-weight: 400; }

    /* tabs */
    .tabs { display: flex; gap: 2px; flex: 1; justify-content: center; }
    .tab {
      display: flex; align-items: center; gap: 6px;
      padding: 6px 16px; border-radius: var(--radius-md);
      font-size: 13px; font-weight: 500; cursor: pointer;
      color: var(--text-secondary);
      background: transparent; border: 1px solid transparent;
      transition: all 0.15s;
      &:hover { color: var(--text-primary); background: var(--bg-elevated); }
      &.active {
        color: var(--accent); background: var(--accent-dim);
        border-color: rgba(79,135,255,0.2);
      }
    }
    .tab-icon { font-size: 14px; }

    /* topbar-right */
    .topbar-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
    .status-pill {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 99px; font-size: 11px; font-weight: 500;
      border: 1px solid var(--border-normal);
      &.ok  { color: var(--green);  border-color: rgba(52,211,153,0.3); background: rgba(52,211,153,0.08); }
      &.err { color: var(--red);    border-color: rgba(248,113,113,0.3); background: rgba(248,113,113,0.08); }
    }
    .status-dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: currentColor;
      .ok &  { animation: pulse-green 2s infinite; }
      .err & { animation: pulse-red   2s infinite; }
    }
    @keyframes pulse-green { 0%,100%{opacity:1} 50%{opacity:0.4} }
    @keyframes pulse-red   { 0%,100%{opacity:1} 50%{opacity:0.4} }
    .btn-primary {
      display: flex; align-items: center; gap: 6px;
      padding: 7px 14px; border-radius: var(--radius-md);
      background: var(--accent); color: #fff; font-size: 13px; font-weight: 500;
      border: none; cursor: pointer; transition: background 0.15s;
      &:hover { background: var(--accent-hover); }
    }

    /* body */
    .body { display: flex; flex: 1; overflow: hidden; }
    .main-area { flex: 1; overflow: hidden; display: flex; flex-direction: column; }

    /* analytics */
    .analytics-placeholder {
      flex: 1; padding: 24px; overflow-y: auto;
    }
    .analytics-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px;
    }
    .a-card {
      background: var(--bg-surface); border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg); padding: 20px 24px;
    }
    .a-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
    .a-value {
      font-size: 32px; font-weight: 700; color: var(--text-primary);
      &.accent { color: var(--accent); }
      &.green  { color: var(--green); }
      &.purple { color: var(--purple); }
      &.amber  { color: var(--amber); }
      &.cyan   { color: var(--cyan); }
    }
  `],
})
export class AppComponent implements OnInit {
  @ViewChild(GraphViewComponent) graphView?: GraphViewComponent;

  activeTab = signal<ActiveTab>('graph');
  showUpload = signal(false);
  apiOk = signal(false);
  stats = signal<GraphStats | null>(null);

  private lastIndexedCount = 0;

  tabs = [
    { id: 'graph' as ActiveTab,     icon: '⬡', label: 'Knowledge Graph' },
    { id: 'qa'    as ActiveTab,     icon: '💬', label: 'Ask Questions'   },
    { id: 'analytics' as ActiveTab, icon: '◈',  label: 'Analytics'       },
  ];

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.api.health().subscribe({
      next: () => this.apiOk.set(true),
      error: () => this.apiOk.set(false),
    });
    this.refreshStats();
    setInterval(() => this.refreshStats(), 5_000);
  }

  refreshStats() {
    this.api.getStats().subscribe({
      next: s => {
        this.stats.set(s);
        const current = s.indexed_documents ?? 0;
        if (current > this.lastIndexedCount) {
          this.lastIndexedCount = current;
          this.graphView?.loadGraph();
        }
      },
      error: () => {},
    });
  }

  setTab(tab: ActiveTab) { this.activeTab.set(tab); }

  onUploadClose() {
    this.showUpload.set(false);
    this.refreshStats();
  }
}
