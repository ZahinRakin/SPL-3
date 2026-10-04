import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs/operators';
import { ApiService } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { describeApiError } from '../services/api-errors';
import { GoogleButtonComponent } from '../components/google-button.component';
import { AUTH_LAYOUT_STYLES } from './auth-layout.styles';

const MIN_PASSWORD_LENGTH = 8;   // mirrors UserManager.validate_password

@Component({
  selector: 'app-register-page',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, GoogleButtonComponent],
  template: `
    <div class="card auth-card">
      <div class="auth-brand">GraphRAG <span>Investigations</span></div>
      <div class="auth-sub">Create an account. You'll get a personal workspace for your cases.</div>

      @if (error()) { <div class="form-error">{{ error() }}</div> }

      <form [formGroup]="form" (ngSubmit)="submit()">
        <label class="field">
          <span class="field-label">Full name</span>
          <input class="input" formControlName="full_name" autocomplete="name" />
        </label>
        <label class="field">
          <span class="field-label">Email</span>
          <input class="input" type="email" formControlName="email" autocomplete="email" />
        </label>
        <label class="field">
          <span class="field-label">Password (at least {{ minLength }} characters)</span>
          <input class="input" type="password" formControlName="password" autocomplete="new-password" />
        </label>
        <button class="btn btn-primary btn-block" type="submit" [disabled]="form.invalid || busy()">
          {{ busy() ? 'Creating account…' : 'Create account' }}
        </button>
      </form>

      <app-google-button />

      <div class="auth-foot">Already registered? <a routerLink="/login">Sign in</a></div>
    </div>
  `,
  styles: [AUTH_LAYOUT_STYLES],
})
export class RegisterPage {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  private router = inject(Router);

  readonly minLength = MIN_PASSWORD_LENGTH;
  form = inject(FormBuilder).nonNullable.group({
    full_name: ['', [Validators.required, Validators.maxLength(120)]],
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(MIN_PASSWORD_LENGTH)]],
  });
  busy = signal(false);
  error = signal('');

  submit() {
    if (this.form.invalid) return;
    const body = this.form.getRawValue();
    this.busy.set(true);
    this.error.set('');
    // Registering doesn't sign in, so log in straight after.
    this.api.register(body).pipe(
      switchMap(() => this.api.login(body.email, body.password)),
      switchMap(t => this.auth.completeLogin(t)),
    ).subscribe({
      next: () => this.router.navigate(['/cases']),
      error: err => {
        this.busy.set(false);
        this.error.set(describeApiError(err));
      },
    });
  }
}
