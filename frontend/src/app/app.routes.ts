import { Routes } from '@angular/router';
import { authGuard, guestGuard } from './auth/auth.guards';

export const routes: Routes = [
  {
    path: 'login',
    canActivate: [guestGuard],
    loadComponent: () => import('./pages/login.page').then(m => m.LoginPage),
  },
  {
    path: 'register',
    canActivate: [guestGuard],
    loadComponent: () => import('./pages/register.page').then(m => m.RegisterPage),
  },
  {
    // Google redirects here with ?code&state (GOOGLE redirect URI = FRONTEND_URL/auth/google/callback).
    path: 'auth/google/callback',
    loadComponent: () => import('./pages/google-callback.page').then(m => m.GoogleCallbackPage),
  },
  {
    path: 'cases',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/cases-dashboard.page').then(m => m.CasesDashboardPage),
  },
  {
    path: 'cases/:caseId',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/case-workspace.page').then(m => m.CaseWorkspacePage),
  },
  {
    path: 'workspaces/:workspaceId/members',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/workspace-members.page').then(m => m.WorkspaceMembersPage),
  },
  { path: '', pathMatch: 'full', redirectTo: 'cases' },
  { path: '**', redirectTo: 'cases' },
];
