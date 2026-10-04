import { Component, OnInit, inject, signal } from '@angular/core';
import { ApiService } from '../services/api.service';
import { describeApiError } from '../services/api-errors';

/**
 * "Continue with Google": OAuth2 authorization-code flow. The API returns Google's
 * consent URL (and sets a CSRF cookie); Google later redirects to /auth/google/callback.
 * Renders nothing when the server has no Google credentials configured.
 */
@Component({
  selector: 'app-google-button',
  standalone: true,
  template: `
    @if (enabled()) {
      <div class="divider"><span>or</span></div>
      <button type="button" class="btn btn-block google" [disabled]="busy()" (click)="start()">
        <span class="g-mark">G</span>
        {{ busy() ? 'Redirecting to Google…' : 'Continue with Google' }}
      </button>
      @if (error()) { <div class="form-error g-error">{{ error() }}</div> }
    }
  `,
  styles: [`
    .divider {
      display: flex; align-items: center; gap: 10px; margin: 18px 0 14px;
      color: var(--text-tertiary); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
      &::before, &::after { content: ''; flex: 1; height: 1px; background: var(--border-normal); }
    }
    .google { padding: 10px 14px; }
    .g-mark { font-weight: 700; color: var(--accent); }
    .g-error { margin: 12px 0 0; }
  `],
})
export class GoogleButtonComponent implements OnInit {
  private api = inject(ApiService);

  enabled = signal(false);
  busy = signal(false);
  error = signal('');

  ngOnInit() {
    this.api.getAuthConfig().subscribe({
      next: c => this.enabled.set(c.google_enabled),
      error: () => this.enabled.set(false),
    });
  }

  start() {
    this.busy.set(true);
    this.error.set('');
    this.api.googleAuthorizeUrl().subscribe({
      next: r => (window.location.href = r.authorization_url),
      error: err => {
        this.busy.set(false);
        this.error.set(describeApiError(err));
      },
    });
  }
}
