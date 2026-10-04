import { Component, Output, EventEmitter, signal, HostListener, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService, DocRecord } from '../services/api.service';
import { CaseContextService } from '../services/case-context.service';
import { describeApiError } from '../services/api-errors';
import { fileIcon, formatSize } from '../shared/format';

interface UploadEntry {
  file: File;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
  record?: DocRecord;
}

@Component({
  selector: 'app-upload-modal',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="modal-backdrop" (click)="onBackdrop($event)">
      <div class="modal" role="dialog" aria-label="Upload documents">
        <div class="modal-header">
          <div class="modal-title">Upload Documents</div>
          <button class="modal-close" (click)="close.emit()">✕</button>
        </div>

        <!-- drop zone -->
        <div class="drop-zone"
             [class.drag-over]="dragOver()"
             (dragover)="$event.preventDefault(); dragOver.set(true)"
             (dragleave)="dragOver.set(false)"
             (drop)="onDrop($event)"
             (click)="fileInput.click()">
          <input #fileInput type="file" multiple hidden (change)="onFileInput($event)"
                 accept=".pdf,.txt,.docx,.doc,.html,.htm"/>
          <div class="dz-icon">{{ dragOver() ? '📂' : '📁' }}</div>
          <div class="dz-title">Drop files here or click to browse</div>
          <div class="dz-sub">PDF, DOCX, TXT, HTML — up to 50 MB each</div>
        </div>

        <!-- file queue -->
        @if (queue().length) {
          <div class="queue">
            @for (entry of queue(); track entry.file.name) {
              <div class="queue-item" [class.done]="entry.status === 'done'" [class.err]="entry.status === 'error'">
                <div class="qi-icon">{{ fileIcon(entry.file.name) }}</div>
                <div class="qi-info">
                  <div class="qi-name">{{ entry.file.name }}</div>
                  <div class="qi-size">{{ formatSize(entry.file.size) }}</div>
                </div>
                <div class="qi-status">
                  @if (entry.status === 'uploading') {
                    <span class="status-spinner"></span>
                  } @else if (entry.status === 'done') {
                    <span class="status-ok">✓</span>
                  } @else if (entry.status === 'error') {
                    <span class="status-err" [title]="entry.error">✗</span>
                  } @else {
                    <span class="status-pend">•••</span>
                  }
                </div>
              </div>
            }
          </div>
        }

        <div class="modal-footer">
          <div class="footer-note">
            Documents are indexed automatically after upload. This may take a moment.
          </div>
          <div class="footer-btns">
            <button class="btn-sec" (click)="close.emit()">{{ allDone() ? 'Close' : 'Cancel' }}</button>
            @if (!allDone() && queue().length) {
              <button class="btn-primary" (click)="uploadAll()" [disabled]="uploading()">
                @if (uploading()) {
                  Uploading…
                } @else {
                  Upload {{ queue().length }} file{{ queue().length !== 1 ? 's' : '' }}
                }
              </button>
            }
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
      animation: fadeIn 0.15s ease;
    }
    @keyframes fadeIn { from{opacity:0} to{opacity:1} }

    .modal {
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      border-radius: var(--radius-xl); width: 500px; max-width: 95vw;
      max-height: 85vh; display: flex; flex-direction: column;
      box-shadow: 0 24px 64px rgba(0,0,0,0.6);
      animation: slideUp 0.2s ease;
    }
    @keyframes slideUp { from{transform:translateY(16px);opacity:0} to{transform:none;opacity:1} }

    .modal-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 18px 20px 16px; border-bottom: 1px solid var(--border-subtle); flex-shrink: 0;
    }
    .modal-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
    .modal-close {
      width: 28px; height: 28px; border-radius: var(--radius-sm);
      background: var(--bg-hover); border: none; color: var(--text-secondary);
      cursor: pointer; font-size: 12px; display: flex; align-items: center; justify-content: center;
      transition: all 0.15s;
      &:hover { background: var(--red-dim); color: var(--red); }
    }

    .drop-zone {
      margin: 16px 20px; border: 1.5px dashed var(--border-normal);
      border-radius: var(--radius-lg); padding: 32px 20px; text-align: center;
      cursor: pointer; transition: all 0.2s;
      &:hover, &.drag-over {
        border-color: var(--accent); background: var(--accent-dim);
      }
    }
    .dz-icon  { font-size: 36px; margin-bottom: 10px; }
    .dz-title { font-size: 14px; font-weight: 500; color: var(--text-primary); margin-bottom: 4px; }
    .dz-sub   { font-size: 12px; color: var(--text-tertiary); }

    .queue {
      flex: 1; overflow-y: auto; padding: 0 20px 8px; display: flex; flex-direction: column; gap: 6px;
    }
    .queue-item {
      display: flex; align-items: center; gap: 10px;
      padding: 9px 12px; border-radius: var(--radius-md);
      background: var(--bg-surface); border: 1px solid var(--border-subtle);
      &.done { border-color: rgba(52,211,153,0.2); }
      &.err  { border-color: rgba(248,113,113,0.2); }
    }
    .qi-icon  { font-size: 18px; flex-shrink: 0; }
    .qi-info  { flex: 1; min-width: 0; }
    .qi-name  { font-size: 12px; font-weight: 500; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .qi-size  { font-size: 10px; color: var(--text-tertiary); }
    .qi-status { flex-shrink: 0; width: 20px; text-align: center; }
    .status-spinner {
      display: inline-block; width: 14px; height: 14px;
      border: 2px solid var(--border-normal); border-top-color: var(--accent);
      border-radius: 50%; animation: spin 0.7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .status-ok   { color: var(--green);  font-size: 14px; }
    .status-err  { color: var(--red);    font-size: 14px; cursor: help; }
    .status-pend { color: var(--text-tertiary); font-size: 10px; letter-spacing: 2px; }

    .modal-footer {
      padding: 14px 20px 18px; border-top: 1px solid var(--border-subtle); flex-shrink: 0;
    }
    .footer-note { font-size: 11px; color: var(--text-tertiary); margin-bottom: 12px; line-height: 1.5; }
    .footer-btns { display: flex; gap: 8px; justify-content: flex-end; }
    .btn-sec {
      padding: 8px 16px; border-radius: var(--radius-md); font-size: 13px; font-weight: 500;
      background: var(--bg-hover); border: 1px solid var(--border-normal); color: var(--text-secondary);
      cursor: pointer; transition: all 0.15s;
      &:hover { color: var(--text-primary); border-color: var(--border-strong); }
    }
    .btn-primary {
      padding: 8px 18px; border-radius: var(--radius-md); font-size: 13px; font-weight: 500;
      background: var(--accent); border: none; color: white; cursor: pointer; transition: background 0.15s;
      &:hover:not(:disabled) { background: var(--accent-hover); }
      &:disabled { opacity: 0.5; cursor: not-allowed; }
    }
  `],
})
export class UploadModalComponent {
  @Output() close = new EventEmitter<void>();

  queue    = signal<UploadEntry[]>([]);
  dragOver = signal(false);
  uploading = signal(false);

  private ctx = inject(CaseContextService);

  constructor(private api: ApiService) {}

  @HostListener('document:keydown.escape')
  onEsc() { if (!this.uploading()) this.close.emit(); }

  onBackdrop(e: MouseEvent) {
    if ((e.target as HTMLElement).classList.contains('modal-backdrop')) {
      if (!this.uploading()) this.close.emit();
    }
  }

  onDrop(e: DragEvent) {
    e.preventDefault();
    this.dragOver.set(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    this.addFiles(files);
  }

  onFileInput(e: Event) {
    const files = Array.from((e.target as HTMLInputElement).files ?? []);
    this.addFiles(files);
    (e.target as HTMLInputElement).value = '';
  }

  addFiles(files: File[]) {
    const entries: UploadEntry[] = files.map(f => ({ file: f, status: 'pending' }));
    this.queue.update(q => [...q, ...entries]);
  }

  allDone(): boolean {
    return this.queue().length > 0 && this.queue().every(e => e.status === 'done' || e.status === 'error');
  }

  async uploadAll() {
    if (this.uploading()) return;
    this.uploading.set(true);
    const entries = this.queue().filter(e => e.status === 'pending');
    for (const entry of entries) {
      this.queue.update(q => q.map(e => e === entry ? { ...e, status: 'uploading' } : e));
      await new Promise<void>(resolve => {
        this.api.uploadDocument(this.ctx.caseId(), entry.file).subscribe({
          next: rec => {
            this.queue.update(q => q.map(e => e === entry ? { ...e, status: 'done', record: rec } : e));
            resolve();
          },
          error: err => {
            this.queue.update(q => q.map(e => e === entry ? { ...e, status: 'error', error: describeApiError(err, 'Upload failed') } : e));
            resolve();
          },
        });
      });
    }
    this.uploading.set(false);
    if (this.queue().every(e => e.status === 'done')) {
      setTimeout(() => this.close.emit(), 1200);
    }
  }

  readonly fileIcon = fileIcon;
  readonly formatSize = formatSize;
}
