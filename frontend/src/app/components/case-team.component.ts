import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService, AuditEvent, CaseMember, CasePriority, CaseRole } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { CaseContextService } from '../services/case-context.service';
import { describeApiError } from '../services/api-errors';

@Component({
  selector: 'app-case-team',
  standalone: true,
  imports: [DatePipe, FormsModule],
  template: `
    <div class="team">
      @if (error()) { <div class="form-error">{{ error() }}</div> }

      <!-- case details -->
      <section class="card">
        <div class="sec-title">Case details</div>
        @if (ctx.canEdit()) {
          <form (ngSubmit)="saveDetails()">
            <div class="two-col">
              <label class="field"><span class="field-label">Title</span>
                <input class="input" name="title" [(ngModel)]="title" maxlength="200" required /></label>
              <label class="field"><span class="field-label">Reference code</span>
                <input class="input" name="ref" [(ngModel)]="referenceCode" maxlength="50" /></label>
            </div>
            <label class="field"><span class="field-label">Priority</span>
              <select class="input narrow" name="priority" [(ngModel)]="priority">
                @for (p of priorities; track p) { <option [value]="p">{{ p }}</option> }
              </select></label>
            <label class="field"><span class="field-label">Description</span>
              <textarea class="input" name="desc" [(ngModel)]="description"></textarea></label>
            <button class="btn btn-primary" type="submit" [disabled]="!title.trim() || busy()">Save details</button>
            @if (saved()) { <span class="muted saved">Saved</span> }
          </form>
        } @else {
          <div class="muted">{{ ctx.case()?.description || 'No description.' }}</div>
        }
      </section>

      <!-- members -->
      <section class="card">
        <div class="sec-title">Case members</div>
        <div class="muted hint">
          Leads manage the team and evidence, investigators upload and edit, viewers read and ask questions.
          Organization owners and admins can always open the case.
        </div>
        @if (ctx.isLead()) {
          <form class="add-row" (ngSubmit)="addMember()">
            <input class="input" type="email" name="email" [(ngModel)]="newEmail"
                   placeholder="Email of a member of this organization" />
            <select class="input narrow" name="role" [(ngModel)]="newRole">
              @for (r of roles; track r) { <option [value]="r">{{ r }}</option> }
            </select>
            <button class="btn btn-primary" type="submit" [disabled]="!newEmail.trim() || busy()">Add</button>
          </form>
        }
        @for (m of members(); track m.user_id) {
          <div class="row">
            <div class="who">
              <div class="name">{{ m.full_name || m.email }}</div>
              <div class="muted small">{{ m.email }}</div>
            </div>
            @if (ctx.isLead()) {
              <select class="input narrow" [ngModel]="m.role" (ngModelChange)="changeRole(m, $event)">
                @for (r of roles; track r) { <option [value]="r">{{ r }}</option> }
              </select>
            } @else {
              <span class="pill role-{{ m.role }}">{{ m.role }}</span>
            }
            @if (ctx.isLead() || m.user_id === myId()) {
              <button class="btn btn-danger btn-sm" (click)="removeMember(m)">{{ m.user_id === myId() ? 'Leave' : 'Remove' }}</button>
            }
          </div>
        }
      </section>

      <!-- audit -->
      @if (ctx.isLead()) {
        <section class="card">
          <div class="sec-title">Audit log</div>
          @for (e of audit(); track e.id) {
            <div class="audit-row">
              <span class="muted small when">{{ e.created_at | date:'short' }}</span>
              <span class="action">{{ e.action }}</span>
              <span class="muted small">{{ e.user_email ?? 'system' }}</span>
              @if (auditDetail(e)) { <span class="muted small detail">{{ auditDetail(e) }}</span> }
            </div>
          } @empty {
            <div class="muted">No activity yet.</div>
          }
        </section>

        <section class="danger">
          <button class="btn btn-danger" (click)="deleteCase()">Delete this case</button>
          <span class="muted small">Removes all evidence files, indexes and chat history permanently.</span>
        </section>
      }
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; overflow-y: auto; }
    .team { max-width: 820px; margin: 0 auto; padding: 24px 16px 40px; display: flex; flex-direction: column; gap: 16px; }
    .sec-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
    .hint { font-size: 12px; margin: -6px 0 12px; }
    .two-col { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
    .narrow { width: auto; }
    .saved { margin-left: 10px; font-size: 12px; }
    .add-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
    .add-row .input[type=email] { flex: 1; min-width: 220px; }
    .row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-top: 1px solid var(--border-subtle); }
    .who { flex: 1; min-width: 0; }
    .name { font-weight: 500; }
    .small { font-size: 12px; }
    .audit-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; padding: 6px 0;
      border-top: 1px solid var(--border-subtle); }
    .when { width: 130px; flex-shrink: 0; }
    .action { font-family: ui-monospace, monospace; font-size: 12px; color: var(--accent); }
    .detail { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
    .danger { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  `],
})
export class CaseTeamComponent implements OnInit {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);
  readonly ctx = inject(CaseContextService);

  readonly roles: CaseRole[] = ['lead', 'investigator', 'viewer'];
  readonly priorities: CasePriority[] = ['low', 'medium', 'high', 'critical'];

  members = signal<CaseMember[]>([]);
  audit = signal<AuditEvent[]>([]);
  error = signal('');
  busy = signal(false);
  saved = signal(false);
  myId = computed(() => this.auth.user()?.id ?? '');

  title = '';
  referenceCode = '';
  priority: CasePriority = 'medium';
  description = '';
  newEmail = '';
  newRole: CaseRole = 'investigator';

  ngOnInit() {
    const c = this.ctx.case();
    if (c) {
      this.title = c.title;
      this.referenceCode = c.reference_code ?? '';
      this.priority = c.priority;
      this.description = c.description;
    }
    this.loadMembers();
    if (this.ctx.isLead()) this.loadAudit();
  }

  private get caseId(): string { return this.ctx.caseId(); }

  loadMembers() {
    this.api.listCaseMembers(this.caseId).subscribe({
      next: m => this.members.set(m),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  loadAudit() {
    this.api.getCaseAudit(this.caseId).subscribe({ next: a => this.audit.set(a), error: () => {} });
  }

  saveDetails() {
    this.busy.set(true);
    this.saved.set(false);
    this.error.set('');
    this.api.updateCase(this.caseId, {
      title: this.title.trim(),
      reference_code: this.referenceCode.trim() || null,
      priority: this.priority,
      description: this.description.trim(),
    }).subscribe({
      next: c => { this.ctx.case.set(c); this.busy.set(false); this.saved.set(true); this.refreshAudit(); },
      error: err => { this.busy.set(false); this.error.set(describeApiError(err)); },
    });
  }

  addMember() {
    this.busy.set(true);
    this.error.set('');
    this.api.addCaseMember(this.caseId, this.newEmail.trim(), this.newRole).subscribe({
      next: () => { this.busy.set(false); this.newEmail = ''; this.loadMembers(); this.refreshAudit(); },
      error: err => { this.busy.set(false); this.error.set(describeApiError(err)); },
    });
  }

  changeRole(m: CaseMember, role: CaseRole) {
    this.error.set('');
    this.api.updateCaseMember(this.caseId, m.user_id, role).subscribe({
      next: () => { this.loadMembers(); this.refreshAudit(); },
      error: err => { this.error.set(describeApiError(err)); this.loadMembers(); },
    });
  }

  removeMember(m: CaseMember) {
    const leaving = m.user_id === this.myId();
    if (!confirm(leaving ? 'Leave this case?' : `Remove ${m.email} from this case?`)) return;
    this.api.removeCaseMember(this.caseId, m.user_id).subscribe({
      next: () => (leaving ? this.router.navigate(['/cases']) : (this.loadMembers(), this.refreshAudit())),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  deleteCase() {
    const title = this.ctx.case()?.title ?? 'this case';
    if (!confirm(`Delete "${title}"? All evidence, indexes and chat history are removed permanently.`)) return;
    this.api.deleteCase(this.caseId).subscribe({
      next: () => this.router.navigate(['/cases']),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  auditDetail(e: AuditEvent): string {
    const d = e.details;
    return Object.keys(d).length ? Object.entries(d).map(([k, v]) => `${k}: ${String(v)}`).join(', ') : '';
  }

  private refreshAudit() {
    if (this.ctx.isLead()) this.loadAudit();
  }
}
