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
  /** lead: delete evidence, manage members, clear chat, see audit log */
  readonly isLead = computed(() => this.role() === 'lead');
}
