import { HttpErrorResponse } from '@angular/common/http';

// fastapi-users answers with error codes instead of sentences.
const CODE_MESSAGES: Record<string, string> = {
  LOGIN_BAD_CREDENTIALS: 'Invalid email or password.',
  REGISTER_USER_ALREADY_EXISTS: 'An account with this email already exists.',
  REGISTER_INVALID_PASSWORD: 'That password is not allowed.',
  OAUTH_USER_ALREADY_EXISTS: 'An account with this email already exists.',
  OAUTH_NOT_AVAILABLE_EMAIL: 'Google did not share an email address.',
  OAUTH_INVALID_STATE: 'The Google sign-in expired or was started in another tab. Please try again.',
  ACCESS_TOKEN_DECODE_ERROR: 'The Google sign-in link is invalid. Please try again.',
  ACCESS_TOKEN_ALREADY_EXPIRED: 'The Google sign-in expired. Please try again.',
};

/** Turn any API error into one readable sentence. */
export function describeApiError(err: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (!(err instanceof HttpErrorResponse)) return fallback;
  if (err.status === 0) return 'Cannot reach the server. Is the backend running?';
  const detail: unknown = err.error?.detail;
  if (typeof detail === 'string') return CODE_MESSAGES[detail] ?? detail;
  if (Array.isArray(detail) && detail.length) {
    // Pydantic validation errors: [{loc, msg}, ...]
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : '';
    return field ? `${field}: ${first.msg}` : (first.msg ?? fallback);
  }
  if (detail && typeof detail === 'object') {
    const d = detail as { code?: string; reason?: string };
    if (d.reason) return d.reason;
    if (d.code) return CODE_MESSAGES[d.code] ?? d.code;
  }
  return fallback;
}
