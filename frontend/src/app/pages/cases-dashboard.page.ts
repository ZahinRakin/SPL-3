import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService, Case, CasePriority, CaseStatus, Workspace } from '../services/api.service';
import { describeApiError } from '../services/api-errors';

const LAST_WORKSPACE_KEY = 'graphrag.lastWorkspace';

const STATUS_FILTERS: { id: CaseStatus | ''; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'open', label: 'Open' },
  { id: 'in_progress', label: 'In progress' },
  { id: 'closed', label: 'Closed' },
  { id: 'archived', label: 'Archived' },
];

@Component({
  selector: 'app-cases-dashboard-page',
  standalone: true,
  imports: [DatePipe, FormsModule, ReactiveFormsModule, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <div class="page-title">Cases</div>
          <div class="muted">Each case keeps its own evidence, knowledge graph and chat history.</div>
        </div>
        <div class="head-actions">
          <select class="input ws-select" [ngModel]="workspaceId()" (ngModelChange)="selectWorkspace($event)">
            @for (w of workspaces(); track w.id) {
              <option [value]="w.id">{{ w.kind === 'personal' ? 'Personal' : w.name }} · {{ w.my_role }}</option>
            }
          </select>
          @if (workspace()?.kind === 'organization') {
            <a class="btn" [routerLink]="['/workspaces', workspaceId(), 'members']">Members ({{ workspace()?.member_count }})</a>
          }
          <button class="btn" (click)="showNewOrg.set(true)">New organization</button>
          <button class="btn btn-primary" [disabled]="!workspaceId()" (click)="openNewCase()">New case</button>
        </div>
      </div>

      <div class="filters">
        @for (f of statusFilters; track f.id) {
          <button class="btn btn-sm" [class.btn-primary]="statusFilter() === f.id" (click)="setFilter(f.id)">{{ f.label }}</button>
        }
      </div>

      @if (error()) { <div class="form-error">{{ error() }}</div> }

      @if (loading()) {
        <div class="muted empty">Loading cases…</div>
      } @else if (cases().length === 0) {
        <div class="card empty">
          <div class="empty-title">No cases here yet</div>
          <div class="muted">Create a case, then upload evidence files to investigate them.</div>
        </div>
      } @else {
        <div class="grid">
          @for (c of cases(); track c.id) {
            <a class="card case-card" [routerLink]="['/cases', c.id]">
              <div class="case-top">
                <span class="pill status-{{ c.status }}">{{ statusLabel(c.status) }}</span>
                <span class="pill prio-{{ c.priority }}">{{ c.priority }}</span>
              </div>
              <div class="case-title">{{ c.title }}</div>
              @if (c.reference_code) { <div class="case-ref">{{ c.reference_code }}</div> }
              @if (c.description) { <div class="case-desc">{{ c.description }}</div> }
              <div class="case-foot">
                <span>{{ c.document_count }} file{{ c.document_count === 1 ? '' : 's' }}</span>
                <span class="pill role-{{ c.my_role }}">{{ c.my_role }}</span>
                <span>Updated {{ c.updated_at | date:'mediumDate' }}</span>
              </div>
            </a>
          }
        </div>
      }
    </div>

    @if (showNewCase()) {
      <div class="dialog-backdrop" (click)="showNewCase.set(false)">
        <form class="card dialog" [formGroup]="caseForm" (ngSubmit)="createCase()" (click)="$event.stopPropagation()">
          <div class="dialog-title">New case in {{ workspace()?.kind === 'personal' ? 'your personal workspace' : workspace()?.name }}</div>
          @if (dialogError()) { <div class="form-error">{{ dialogError() }}</div> }
          <label class="field"><span class="field-label">Title</span>
            <input class="input" formControlName="title" placeholder="e.g. Riverside warehouse data breach" /></label>
          <label class="field"><span class="field-label">Reference code (optional)</span>
            <input class="input" formControlName="reference_code" placeholder="e.g. INV-2026-014" /></label>
          <label class="field"><span class="field-label">Priority</span>
            <select class="input" formControlName="priority">
              @for (p of priorities; track p) { <option [value]="p">{{ p }}</option> }
            </select></label>
          <label class="field"><span class="field-label">Description</span>
            <textarea class="input" formControlName="description" placeholder="What is being investigated?"></textarea></label>
          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" (click)="showNewCase.set(false)">Cancel</button>
            <button type="submit" class="btn btn-primary" [disabled]="caseForm.invalid || busy()">Create case</button>
          </div>
        </form>
      </div>
    }

    @if (showNewOrg()) {
      <div class="dialog-backdrop" (click)="showNewOrg.set(false)">
        <form class="card dialog" (ngSubmit)="createOrg()" (click)="$event.stopPropagation()">
          <div class="dialog-title">New organization</div>
          <div class="muted org-hint">Organizations let you share cases with colleagues.</div>
          @if (dialogError()) { <div class="form-error">{{ dialogError() }}</div> }
          <label class="field"><span class="field-label">Name</span>
            <input class="input" name="orgName" [(ngModel)]="orgName" maxlength="120" /></label>
          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" (click)="showNewOrg.set(false)">Cancel</button>
            <button type="submit" class="btn btn-primary" [disabled]="!orgName.trim() || busy()">Create</button>
          </div>
        </form>
      </div>
    }
  `,
  styles: [`
    :host { display: block; height: 100%; overflow-y: auto; }
    .page { max-width: 1100px; margin: 0 auto; padding: 28px 16px 40px; }
    .page-head { display: flex; flex-wrap: wrap; gap: 16px; justify-content: space-between; align-items: flex-end; margin-bottom: 18px; }
    .page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }
    .head-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .ws-select { width: auto; min-width: 200px; }
    .head-actions a { text-decoration: none; }
    .filters { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
    .empty { text-align: center; padding: 40px 20px; }
    .empty-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
    .case-card {
      display: flex; flex-direction: column; gap: 6px; text-decoration: none; color: inherit;
      transition: border-color 0.15s, background 0.15s;
      &:hover { border-color: var(--border-strong); background: var(--bg-elevated); }
    }
    .case-top { display: flex; gap: 6px; }
    .case-title { font-size: 15px; font-weight: 600; margin-top: 4px; }
    .case-ref { font-size: 12px; color: var(--text-tertiary); font-family: ui-monospace, monospace; }
    .case-desc {
      font-size: 12px; color: var(--text-secondary);
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .case-foot { display: flex; align-items: center; gap: 10px; margin-top: auto; padding-top: 8px;
      font-size: 11px; color: var(--text-tertiary); }
    .org-hint { font-size: 12px; margin: -8px 0 14px; }
  `],
})
export class CasesDashboardPage implements OnInit {
  private api = inject(ApiService);
  private router = inject(Router);

  readonly statusFilters = STATUS_FILTERS;
  readonly priorities: CasePriority[] = ['low', 'medium', 'high', 'critical'];

  workspaces = signal<Workspace[]>([]);
  workspaceId = signal('');
  workspace = computed(() => this.workspaces().find(w => w.id === this.workspaceId()) ?? null);
  cases = signal<Case[]>([]);
  statusFilter = signal<CaseStatus | ''>('');
  loading = signal(true);
  error = signal('');

  showNewCase = signal(false);
  showNewOrg = signal(false);
  dialogError = signal('');
  busy = signal(false);
  orgName = '';
  caseForm = inject(FormBuilder).nonNullable.group({
    title: ['', [Validators.required, Validators.maxLength(200)]],
    reference_code: ['', Validators.maxLength(50)],
    priority: ['medium' as CasePriority],
    description: [''],
  });

  ngOnInit() {
    this.loadWorkspaces();
  }

  private loadWorkspaces(preferId?: string) {
    this.api.listWorkspaces().subscribe({
      next: ws => {
        this.workspaces.set(ws);
        const remembered = preferId ?? this.readLastWorkspace();
        const pick = ws.find(w => w.id === remembered) ?? ws[0];
        if (pick) this.selectWorkspace(pick.id);
        else this.loading.set(false);
      },
      error: err => {
        this.loading.set(false);
        this.error.set(describeApiError(err));
      },
    });
  }

  selectWorkspace(id: string) {
    this.workspaceId.set(id);
    try { localStorage.setItem(LAST_WORKSPACE_KEY, id); } catch { /* storage unavailable: just don't remember */ }
    this.loadCases();
  }

  setFilter(status: CaseStatus | '') {
    this.statusFilter.set(status);
    this.loadCases();
  }

  loadCases() {
    const id = this.workspaceId();
    if (!id) return;
    this.loading.set(true);
    this.error.set('');
    this.api.listCases(id, this.statusFilter() || undefined).subscribe({
      next: cs => { this.cases.set(cs); this.loading.set(false); },
      error: err => { this.loading.set(false); this.error.set(describeApiError(err)); },
    });
  }

  statusLabel(s: CaseStatus): string {
    return s === 'in_progress' ? 'in progress' : s;
  }

  openNewCase() {
    this.caseForm.reset();
    this.dialogError.set('');
    this.showNewCase.set(true);
  }

  createCase() {
    if (this.caseForm.invalid) return;
    const v = this.caseForm.getRawValue();
    this.busy.set(true);
    this.dialogError.set('');
    this.api.createCase(this.workspaceId(), {
      title: v.title.trim(),
      description: v.description.trim(),
      reference_code: v.reference_code.trim() || null,
      priority: v.priority,
    }).subscribe({
      next: c => {
        this.busy.set(false);
        this.showNewCase.set(false);
        this.router.navigate(['/cases', c.id]);
      },
      error: err => { this.busy.set(false); this.dialogError.set(describeApiError(err)); },
    });
  }

  createOrg() {
    const name = this.orgName.trim();
    if (!name) return;
    this.busy.set(true);
    this.dialogError.set('');
    this.api.createWorkspace(name).subscribe({
      next: ws => {
        this.busy.set(false);
        this.showNewOrg.set(false);
        this.orgName = '';
        this.loadWorkspaces(ws.id);
      },
      error: err => { this.busy.set(false); this.dialogError.set(describeApiError(err)); },
    });
  }

  private readLastWorkspace(): string | null {
    try { return localStorage.getItem(LAST_WORKSPACE_KEY); } catch { return null; }
  }
}
