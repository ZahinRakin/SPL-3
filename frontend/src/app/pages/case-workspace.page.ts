import { Component, Input, OnDestroy, ViewChild, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { SidebarComponent } from '../components/sidebar.component';
import { GraphViewComponent } from '../components/graph-view.component';
import { QaPanelComponent } from '../components/qa-panel.component';
import { UploadModalComponent } from '../components/upload-modal.component';
import { CaseTeamComponent } from '../components/case-team.component';
import { ApiService, CaseStatus, GraphStats } from '../services/api.service';
import { CaseContextService } from '../services/case-context.service';
import { describeApiError } from '../services/api-errors';

export type ActiveTab = 'graph' | 'qa' | 'analytics' | 'team';

const STATS_POLL_MS = 5_000;

@Component({
  selector: 'app-case-workspace-page',
  standalone: true,
  imports: [
    DecimalPipe, FormsModule, RouterLink,
    SidebarComponent, GraphViewComponent, QaPanelComponent, UploadModalComponent, CaseTeamComponent,
  ],
  template: `
    @if (ctx.case(); as c) {
      <div class="shell">
        <!-- case header -->
        <header class="case-bar">
          <a class="back" routerLink="/cases" title="All cases">←</a>
          <div class="case-id">
            <div class="case-title" [title]="c.title">{{ c.title }}</div>
            <div class="case-meta">
              @if (c.reference_code) { <span class="ref">{{ c.reference_code }}</span> }
              <span class="pill prio-{{ c.priority }}">{{ c.priority }}</span>
              <span class="pill role-{{ c.my_role }}">{{ c.my_role }}</span>
            </div>
          </div>

          <nav class="tabs">
            @for (tab of tabs; track tab.id) {
              <button class="tab" [class.active]="activeTab() === tab.id" (click)="setTab(tab.id)">
                <span class="tab-icon">{{ tab.icon }}</span>
                {{ tab.label }}
              </button>
            }
          </nav>

          <div class="bar-right">
            @if (ctx.canEdit()) {
              <select class="input status-select status-{{ c.status }}" [ngModel]="c.status" (ngModelChange)="setStatus($event)">
                @for (s of statuses; track s.id) { <option [value]="s.id">{{ s.label }}</option> }
              </select>
              <button class="btn btn-primary" (click)="showUpload.set(true)"
                      [disabled]="!ctx.canUpload()"
                      [title]="!ctx.canUpload() ? 'Reopen the case to add evidence' : 'Upload evidence'">
                Upload evidence
              </button>
            } @else {
              <span class="pill status-{{ c.status }}">{{ statusLabel(c.status) }}</span>
            }
          </div>
        </header>
        @if (error()) { <div class="form-error bar-error">{{ error() }}</div> }

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
            @if (activeTab() === 'team') {
              <app-case-team/>
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
    } @else {
      <div class="loading muted">{{ error() || 'Loading case…' }}</div>
    }
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .shell {
      display: flex; flex-direction: column; height: 100%;
      background: var(--bg-base); overflow: hidden;
    }
    .loading { padding: 40px; text-align: center; }

    /* case header */
    .case-bar {
      display: flex; align-items: center; gap: 14px;
      padding: 0 16px; min-height: 56px; flex-shrink: 0;
      border-bottom: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }
    .back {
      font-size: 16px; color: var(--text-secondary); text-decoration: none; padding: 4px 6px;
      border-radius: var(--radius-sm);
      &:hover { color: var(--text-primary); background: var(--bg-elevated); }
    }
    .case-id { min-width: 0; max-width: 320px; }
    .case-title {
      font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .case-meta { display: flex; align-items: center; gap: 6px; margin-top: 2px; }
    .ref { font-size: 11px; color: var(--text-tertiary); font-family: ui-monospace, monospace; }
    .bar-error { margin: 8px 16px 0; }

    /* tabs */
    .tabs { display: flex; gap: 2px; flex: 1; justify-content: center; }
    .tab {
      display: flex; align-items: center; gap: 6px;
      padding: 6px 14px; border-radius: var(--radius-md);
      font-size: 13px; font-weight: 500; cursor: pointer;
      color: var(--text-secondary);
      background: transparent; border: 1px solid transparent;
      transition: all 0.15s;
      &:hover { color: var(--text-primary); background: var(--bg-elevated); }
      &.active {
        color: var(--accent); background: var(--accent-dim);
        border-color: var(--accent-dim);
      }
    }
    .tab-icon { font-size: 14px; }

    .bar-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .status-select { width: auto; padding: 6px 10px; font-size: 12px; }

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
export class CaseWorkspacePage implements OnDestroy {
  @ViewChild(GraphViewComponent) graphView?: GraphViewComponent;

  private api = inject(ApiService);
  private router = inject(Router);
  readonly ctx = inject(CaseContextService);

  activeTab = signal<ActiveTab>('graph');
  showUpload = signal(false);
  stats = signal<GraphStats | null>(null);
  error = signal('');

  private lastIndexedCount = 0;
  private statsTimer?: ReturnType<typeof setInterval>;

  tabs = [
    { id: 'graph' as ActiveTab,     icon: '⬡', label: 'Knowledge Graph' },
    { id: 'qa'    as ActiveTab,     icon: '💬', label: 'Ask Questions'   },
    { id: 'analytics' as ActiveTab, icon: '◈',  label: 'Analytics'       },
    { id: 'team'  as ActiveTab,     icon: '👥', label: 'Team'            },
  ];

  readonly statuses: { id: CaseStatus; label: string }[] = [
    { id: 'open', label: 'Open' },
    { id: 'in_progress', label: 'In progress' },
    { id: 'closed', label: 'Closed' },
    { id: 'archived', label: 'Archived' },
  ];

  /** Bound from the :caseId route param. The page is reused when only the param changes. */
  @Input() set caseId(id: string) {
    this.stopPolling();
    this.ctx.case.set(null);           // destroys the child components of the previous case
    this.stats.set(null);
    this.lastIndexedCount = 0;
    this.activeTab.set('graph');
    this.api.getCase(id).subscribe({
      next: c => {
        this.ctx.case.set(c);
        this.refreshStats();
        this.statsTimer = setInterval(() => this.refreshStats(), STATS_POLL_MS);
      },
      error: () => this.router.navigate(['/cases']),
    });
  }

  ngOnDestroy() {
    this.stopPolling();
    this.ctx.case.set(null);
  }

  refreshStats() {
    const id = this.ctx.caseId();
    if (!id) return;
    this.api.getStats(id).subscribe({
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

  setStatus(status: CaseStatus) {
    this.error.set('');
    this.api.updateCase(this.ctx.caseId(), { status }).subscribe({
      next: c => this.ctx.case.set(c),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  statusLabel(s: CaseStatus): string {
    return s === 'in_progress' ? 'in progress' : s;
  }

  onUploadClose() {
    this.showUpload.set(false);
    this.refreshStats();
  }

  private stopPolling() {
    if (this.statsTimer) clearInterval(this.statsTimer);
    this.statsTimer = undefined;
  }
}
