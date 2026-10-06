import { Injectable, computed, signal } from '@angular/core';
import { Case } from './api.service';

/**
 * The case currently open in CaseWorkspacePage. The graph, Q&A, sidebar and upload
 * components read the case id and the user's role from here.
 */
@Injectable({ providedIn: 'root' })
export class CaseContextService {
  readonly case = signal<Case | null>(null);

  readonly caseId = computed(() => this.case()?.id ?? '');
  readonly role = computed(() => this.case()?.my_role ?? 'viewer');
  /** investigator or lead: upload evidence, edit case details */
  readonly canEdit = computed(() => this.role() !== 'viewer');
  /** Evidence can only be added to open / in-progress cases (the API returns 400 otherwise). */
  readonly canUpload = computed(() => {
    const status = this.case()?.status;
    return this.canEdit() && status !== 'closed' && status !== 'archived';
  });
  /** lead: delete evidence, manage members, clear chat, see audit log */
  readonly isLead = computed(() => this.role() === 'lead');
}
