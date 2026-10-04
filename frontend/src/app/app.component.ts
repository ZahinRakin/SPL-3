import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { ApiService } from './services/api.service';
import { AuthService } from './services/auth.service';

/** Global shell: top bar (when signed in) + the routed page. */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink],
  template: `
    <div class="shell">
      @if (auth.user(); as user) {
        <header class="topbar">
          <a class="brand" routerLink="/cases">
            <svg class="logo-icon" viewBox="0 0 28 28" fill="none">
              <circle cx="7" cy="8"  r="4" fill="var(--accent)"/>
              <circle cx="21" cy="8"  r="4" fill="var(--green)"/>
              <circle cx="14" cy="21" r="4" fill="var(--purple)"/>
              <line x1="7"  y1="8"  x2="21" y2="8"  stroke="var(--border-strong)" stroke-width="1.5"/>
              <line x1="7"  y1="8"  x2="14" y2="21" stroke="var(--border-strong)" stroke-width="1.5"/>
              <line x1="21" y1="8"  x2="14" y2="21" stroke="var(--border-strong)" stroke-width="1.5"/>
            </svg>
            <span class="brand-name">GraphRAG <span class="brand-sub">Investigations</span></span>
          </a>

          <div class="topbar-right">
            <div class="status-pill" [class.ok]="apiOk()" [class.err]="!apiOk()">
              <span class="status-dot"></span>
              {{ apiOk() ? 'API Connected' : 'API Offline' }}
            </div>
            <div class="user">
              <span class="avatar">{{ initials(user.full_name || user.email) }}</span>
              <span class="user-name" [title]="user.email">{{ user.full_name || user.email }}</span>
            </div>
            <button class="btn btn-ghost btn-sm" (click)="auth.logout()">Sign out</button>
          </div>
        </header>
      }
      <div class="page">
        <router-outlet/>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .shell {
      display: flex; flex-direction: column; height: 100%;
      background: var(--bg-base); overflow: hidden;
    }
    .page { flex: 1; overflow: hidden; }

    /* topbar */
    .topbar {
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      padding: 0 20px; height: 52px; flex-shrink: 0;
      border-bottom: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }
    .brand { display: flex; align-items: center; gap: 9px; flex-shrink: 0; text-decoration: none; }
    .logo-icon { width: 28px; height: 28px; }
    .brand-name {
      font-size: 15px; font-weight: 700; color: var(--text-primary);
      letter-spacing: -0.3px;
    }
    .brand-sub { color: var(--text-secondary); font-weight: 400; }

    .topbar-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
    .status-pill {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 99px; font-size: 11px; font-weight: 500;
      border: 1px solid var(--border-normal);
      &.ok  { color: var(--green); border-color: var(--green-dim); background: var(--green-dim); }
      &.err { color: var(--red);   border-color: var(--red-dim);   background: var(--red-dim); }
    }
    .status-dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: currentColor;
      .ok &  { animation: pulse 2s infinite; }
      .err & { animation: pulse 2s infinite; }
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

    .user { display: flex; align-items: center; gap: 8px; }
    .avatar {
      width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 600; color: var(--accent); background: var(--accent-dim);
    }
    .user-name { font-size: 13px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  `],
})
export class AppComponent implements OnInit {
  readonly auth = inject(AuthService);
  private api = inject(ApiService);

  apiOk = signal(false);

  ngOnInit() {
    this.api.health().subscribe({
      next: () => this.apiOk.set(true),
      error: () => this.apiOk.set(false),
    });
  }

  initials(name: string): string {
    return name.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join('');
  }
}
