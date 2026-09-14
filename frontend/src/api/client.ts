/**
 * Minimal typed fetch wrapper for the Groovarr backend API: same-origin
 * `/api`, JSON in/out, typed errors (`ApiError`), and pagination-friendly
 * query-param handling. Response/request types are generated from the
 * backend's live OpenAPI schema into `api/schema.ts` (`npm run generate-api`)
 * rather than hand-maintained, so they can't silently drift from the real
 * API — see `api/dashboard.ts`/`api/settings.ts` for endpoint-specific
 * typed functions built on top of this file.
 */

const API_BASE = '/api';

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  readonly body: unknown;

  constructor(message: string, status: number, url: string, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

export interface RequestOptions {
  /** Query string parameters, appended to the path. */
  query?: Record<string, string | number | boolean | undefined>;
  /** AbortSignal for cancelling in-flight requests (e.g. on unmount). */
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.pathname + url.search;
}

async function request<TResponse>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<TResponse> {
  return requestAbsolute<TResponse>(method, buildUrl(path, options?.query), body, options);
}

/**
 * Same as `request`, but `path` is used exactly as given (no `/api` prefix
 * applied) — needed for the one endpoint that deliberately lives outside
 * `/api`: `GET /health` (a plain container liveness probe, not part of the
 * versioned application API).
 */
async function requestAbsolute<TResponse>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<TResponse> {
  const url = path;

  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: options?.signal,
    credentials: 'same-origin',
  });

  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => undefined)
    : undefined;

  if (!response.ok) {
    throw new ApiError(
      `Request failed: ${method} ${url} (${response.status})`,
      response.status,
      url,
      payload,
    );
  }

  return payload as TResponse;
}

/**
 * Same-origin typed client for the Groovarr REST API, served under `/api`
 * by the backend container alongside the built SPA (architecture doc §5).
 * Endpoint-specific modules (`api/dashboard.ts`, `api/settings.ts`, ...)
 * build typed functions on top of `get`/`post`/`put`/`patch`/`delete` here.
 */
export const apiClient = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, body, options),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PUT', path, body, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, body, options),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>('DELETE', path, undefined, options),
};

/**
 * The backend's actual `GET /health` shape (see `backend/app/api/router.py`)
 * — a liveness/readiness probe that lives outside the `/api` prefix
 * entirely, so it's fetched via `requestAbsolute`, not `apiClient`.
 */
export interface HealthResponse {
  status: 'ok';
  version: string;
  db: 'ok' | 'error';
}

export function getHealth(options?: RequestOptions): Promise<HealthResponse> {
  return requestAbsolute<HealthResponse>('GET', '/health', undefined, options);
}
