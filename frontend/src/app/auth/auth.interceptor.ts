import { HttpErrorResponse, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';

const withBearer = (req: HttpRequest<unknown>, token: string) =>
  req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });

/**
 * Adds the access token to API calls. On a 401 it refreshes the token once and retries;
 * if the refresh fails too, the session is over. Auth endpoints are never retried,
 * which avoids an endless refresh loop.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const isAuthCall = req.url.includes('/api/auth/');
  const token = auth.getAccessToken();
  const outgoing = token && !isAuthCall ? withBearer(req, token) : req;

  return next(outgoing).pipe(
    catchError((err: unknown) => {
      if (!(err instanceof HttpErrorResponse) || err.status !== 401 || isAuthCall || !auth.isLoggedIn()) {
        return throwError(() => err);
      }
      return auth.refreshAccessToken().pipe(
        switchMap(fresh => next(withBearer(req, fresh))),
        catchError(refreshErr => {
          auth.sessionExpired();
          return throwError(() => refreshErr);
        }),
      );
    }),
  );
};
