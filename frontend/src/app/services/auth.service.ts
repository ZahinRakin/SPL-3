import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, firstValueFrom, of } from 'rxjs';
import { catchError, finalize, map, shareReplay, switchMap, tap } from 'rxjs/operators';
import { ApiService, TokenResponse, User } from './api.service';

/**
 * Session state. The access token lives only in memory (never localStorage), so a page
 * reload restores the session from the httpOnly refresh cookie via restoreSession().
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private api = inject(ApiService);
  private router = inject(Router);

  readonly user = signal<User | null>(null);
  readonly isLoggedIn = computed(() => this.user() !== null);

  private accessToken: string | null = null;
  private refreshInFlight: Observable<string> | null = null;

  getAccessToken(): string | null {
    return this.accessToken;
  }

  /** Store a fresh token pair and load the profile. */
  completeLogin(tokens: TokenResponse): Observable<User> {
    this.accessToken = tokens.access_token;
    return this.api.me().pipe(tap(u => this.user.set(u)));
  }

  /** Runs once at start-up (APP_INITIALIZER). Never throws. */
  restoreSession(): Promise<void> {
    return firstValueFrom(
      this.api.refresh().pipe(
        switchMap(t => this.completeLogin(t)),
        map(() => undefined),
        catchError(() => {
          this.clearSession();
          return of(undefined);
        }),
      ),
    );
  }

  /** Parallel 401s share a single refresh request. */
  refreshAccessToken(): Observable<string> {
    if (!this.refreshInFlight) {
      this.refreshInFlight = this.api.refresh().pipe(
        map(t => {
          this.accessToken = t.access_token;
          return t.access_token;
        }),
        finalize(() => (this.refreshInFlight = null)),
        shareReplay(1),
      );
    }
    return this.refreshInFlight;
  }

  logout(): void {
    this.api.logout().subscribe({ error: () => {} });
    this.clearSession();
    this.router.navigate(['/login']);
  }

  /** Called when a refresh fails: the cookie expired or was revoked. */
  sessionExpired(): void {
    this.clearSession();
    this.router.navigate(['/login'], { queryParams: { returnUrl: this.router.url } });
  }

  private clearSession(): void {
    this.accessToken = null;
    this.user.set(null);
  }
}
