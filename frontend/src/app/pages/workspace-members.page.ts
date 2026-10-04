import { Component, Input, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService, Workspace, WorkspaceMember } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { describeApiError } from '../services/api-errors';

@Component({
  selector: 'app-workspace-members-page',
  standalone: true,
  imports: [DatePipe, FormsModule, RouterLink],
  template: `
    <div class="page">
      <a class="back" routerLink="/cases">← Cases</a>
      <div class="page-title">{{ workspace()?.name ?? 'Organization' }}</div>
      <div class="muted sub">
        Members can create cases and be added to cases. Owners and admins can open every case in the organization.
      </div>

      @if (error()) { <div class="form-error">{{ error() }}</div> }

      @if (isAdmin()) {
        <form class="card add-row" (ngSubmit)="add()">
          <input class="input" name="email" type="email" [(ngModel)]="newEmail" placeholder="colleague@example.com (must be registered)" />
          <select class="input role-select" name="role" [(ngModel)]="newRole">
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          <button class="btn btn-primary" type="submit" [disabled]="!newEmail.trim() || busy()">Add member</button>
        </form>
      }

      <div class="card table">
        @for (m of members(); track m.user_id) {
          <div class="row">
            <div class="who">
              <div class="name">{{ m.full_name || m.email }}</div>
              <div class="muted email">{{ m.email }} · joined {{ m.created_at | date:'mediumDate' }}</div>
            </div>
            @if (isAdmin() && m.role !== 'owner') {
              <select class="input role-select" [ngModel]="m.role" (ngModelChange)="changeRole(m, $event)">
                <option value="member">member</option>
                <option value="admin">admin</option>
              </select>
            } @else {
              <span class="pill role-{{ m.role }}">{{ m.role }}</span>
            }
            @if (m.role !== 'owner' && (isAdmin() || m.user_id === myId())) {
              <button class="btn btn-danger btn-sm" (click)="remove(m)">{{ m.user_id === myId() ? 'Leave' : 'Remove' }}</button>
            }
          </div>
        }
      </div>

      @if (workspace()?.my_role === 'owner') {
        <div class="danger-zone">
          <button class="btn btn-danger" (click)="deleteWorkspace()">Delete organization and all its cases</button>
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; overflow-y: auto; }
    .page { max-width: 760px; margin: 0 auto; padding: 28px 16px 40px; }
    .back { font-size: 13px; color: var(--text-secondary); text-decoration: none; &:hover { color: var(--text-primary); } }
    .page-title { font-size: 22px; font-weight: 700; margin-top: 10px; }
    .sub { font-size: 13px; margin: 4px 0 18px; }
    .add-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; padding: 14px; }
    .add-row .input[type=email] { flex: 1; min-width: 220px; }
    .role-select { width: auto; }
    .table { padding: 6px 14px; }
    .row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border-subtle);
      &:last-child { border-bottom: none; } }
    .who { flex: 1; min-width: 0; }
    .name { font-weight: 500; }
    .email { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .danger-zone { margin-top: 28px; }
  `],
})
export class WorkspaceMembersPage implements OnInit {
  @Input() workspaceId = '';

  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);

  workspace = signal<Workspace | null>(null);
  members = signal<WorkspaceMember[]>([]);
  error = signal('');
  busy = signal(false);
  isAdmin = computed(() => ['owner', 'admin'].includes(this.workspace()?.my_role ?? ''));
  myId = computed(() => this.auth.user()?.id ?? '');
  newEmail = '';
  newRole: 'admin' | 'member' = 'member';

  ngOnInit() {
    this.api.getWorkspace(this.workspaceId).subscribe({
      next: w => this.workspace.set(w),
      error: () => this.router.navigate(['/cases']),
    });
    this.load();
  }

  load() {
    this.api.listWorkspaceMembers(this.workspaceId).subscribe({
      next: m => this.members.set(m),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  add() {
    this.busy.set(true);
    this.error.set('');
    this.api.addWorkspaceMember(this.workspaceId, this.newEmail.trim(), this.newRole).subscribe({
      next: () => { this.busy.set(false); this.newEmail = ''; this.load(); },
      error: err => { this.busy.set(false); this.error.set(describeApiError(err)); },
    });
  }

  changeRole(m: WorkspaceMember, role: 'admin' | 'member') {
    this.api.updateWorkspaceMember(this.workspaceId, m.user_id, role).subscribe({
      next: () => this.load(),
      error: err => { this.error.set(describeApiError(err)); this.load(); },
    });
  }

  remove(m: WorkspaceMember) {
    const leaving = m.user_id === this.myId();
    const question = leaving
      ? 'Leave this organization? You will lose access to its cases.'
      : `Remove ${m.email}? They will lose access to every case in this organization.`;
    if (!confirm(question)) return;
    this.api.removeWorkspaceMember(this.workspaceId, m.user_id).subscribe({
      next: () => (leaving ? this.router.navigate(['/cases']) : this.load()),
      error: err => this.error.set(describeApiError(err)),
    });
  }

  deleteWorkspace() {
    if (!confirm('Delete this organization? Every case, evidence file and chat history in it is deleted permanently.')) return;
    this.api.deleteWorkspace(this.workspaceId).subscribe({
      next: () => this.router.navigate(['/cases']),
      error: err => this.error.set(describeApiError(err)),
    });
  }
}
