import { Component, Input, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs/operators';
import { ApiService } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { describeApiError } from '../services/api-errors';
import { GoogleButtonComponent } from '../components/google-button.component';
import { AUTH_LAYOUT_STYLES } from './auth-layout.styles';

@Component({
  selector: 'app-login-page',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, GoogleButtonComponent],
  template: `
    <div class="card auth-card">
      <div class="auth-brand">GraphRAG <span>Investigations</span></div>
      <div class="auth-sub">Sign in to your cases.</div>

      @if (error()) { <div class="form-error">{{ error() }}</div> }

      <form [formGroup]="form" (ngSubmit)="submit()">
        <label class="field">
          <span class="field-label">Email</span>
          <input class="input" type="email" formControlName="email" autocomplete="email" />
        </label>
        <label class="field">
          <span class="field-label">Password</span>
          <input class="input" type="password" formControlName="password" autocomplete="current-password" />
        </label>
        <button class="btn btn-primary btn-block" type="submit" [disabled]="form.invalid || busy()">
          {{ busy() ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>

      <app-google-button />

      <div class="auth-foot">No account? <a routerLink="/register">Create one</a></div>
    </div>
  `,
  styles: [AUTH_LAYOUT_STYLES],
})
export class LoginPage {
  /** Bound from ?returnUrl= by withComponentInputBinding. */
  @Input() returnUrl = '';

  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);

  form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });
  busy = signal(false);
  error = signal('');

  submit() {
    if (this.form.invalid) return;
    const { email, password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set('');
    this.api.login(email, password).pipe(switchMap(t => this.auth.completeLogin(t))).subscribe({
      next: () => this.router.navigateByUrl(this.returnUrl || '/cases'),
      error: err => {
        this.busy.set(false);
        this.error.set(describeApiError(err));
      },
    });
  }
}
