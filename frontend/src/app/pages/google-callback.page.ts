import { Component, Input, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs/operators';
import { ApiService } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { describeApiError } from '../services/api-errors';
import { AUTH_LAYOUT_STYLES } from './auth-layout.styles';

/** Google redirects here; we hand code + state to the API, which returns our own tokens. */
@Component({
  selector: 'app-google-callback-page',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="card auth-card">
      <div class="auth-brand">GraphRAG <span>Investigations</span></div>
      @if (error()) {
        <div class="form-error">{{ error() }}</div>
        <div class="auth-foot"><a routerLink="/login">Back to sign in</a></div>
      } @else {
        <div class="auth-sub">Signing you in with Google…</div>
      }
    </div>
  `,
  styles: [AUTH_LAYOUT_STYLES],
})
export class GoogleCallbackPage implements OnInit {
  @Input() code = '';
  @Input() state = '';
  /** Set by Google when the user cancels on the consent screen. */
  @Input() error_description = '';

  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);

  error = signal('');

  ngOnInit() {
    if (!this.code || !this.state) {
      this.error.set(this.error_description || 'Google sign-in was cancelled.');
      return;
    }
    this.api.googleCallback(this.code, this.state).pipe(
      switchMap(t => this.auth.completeLogin(t)),
    ).subscribe({
      next: () => this.router.navigate(['/cases'], { replaceUrl: true }),
      error: err => this.error.set(describeApiError(err)),
    });
  }
}
