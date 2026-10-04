/** Shared layout for the login, register and Google callback pages. */
export const AUTH_LAYOUT_STYLES = `
  :host { display: flex; align-items: center; justify-content: center; height: 100%; overflow-y: auto; padding: 24px 16px; }
  .auth-card { width: 100%; max-width: 400px; padding: 32px 28px; }
  .auth-brand { font-size: 20px; font-weight: 700; letter-spacing: -0.3px; margin-bottom: 4px; }
  .auth-brand span { color: var(--text-secondary); font-weight: 400; }
  .auth-sub { font-size: 13px; color: var(--text-secondary); margin-bottom: 24px; }
  .auth-foot { margin-top: 20px; font-size: 13px; color: var(--text-secondary); text-align: center; }
  .auth-foot a { color: var(--accent); text-decoration: none; }
  .auth-foot a:hover { text-decoration: underline; }
`;
