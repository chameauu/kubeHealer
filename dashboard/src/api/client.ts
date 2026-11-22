/**
 * The one HTTP seam. Every service module calls `request`; every failure is an
 * `ApiError` carrying the status the service returned and its `detail` string
 * unchanged, so the UI can show the upstream message verbatim.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly url: string;

  constructor(status: number, detail: string, url: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.url = url;
  }
}

type QueryValue = string | number | boolean | null | undefined | string[];

export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, item);
    } else {
      search.append(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

function validationDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (!item || typeof item !== 'object') return String(item);
      const entry = item as { loc?: unknown; msg?: unknown };
      const where = Array.isArray(entry.loc) ? entry.loc.join('.') : '';
      const message = typeof entry.msg === 'string' ? entry.msg : JSON.stringify(item);
      return where ? `${where}: ${message}` : message;
    });
    return parts.join('; ');
  }
  if (detail === null || detail === undefined) return null;
  return JSON.stringify(detail);
}

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const formatted = validationDetail((body as { detail: unknown }).detail);
    if (formatted) return formatted;
  }
  if (typeof body === 'string' && body.trim()) return body.trim();
  return fallback;
}

export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('accept', 'application/json');
  if (init.body !== undefined && !headers.has('content-type')) {
    headers.set('content-type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new ApiError(0, `network error: ${reason}`, url);
  }

  const text = await response.text();

  if (!response.ok) {
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    const fallback = text.trim() || `${response.status} ${response.statusText}`.trim();
    throw new ApiError(response.status, extractDetail(body, fallback), url);
  }

  if (response.status === 204 || !text) {
    return undefined as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, `response is not JSON: ${text.slice(0, 200)}`, url);
  }
}

/** Coerce anything thrown into an ApiError for uniform rendering. */
export function toApiError(error: unknown, url = ''): ApiError {
  if (error instanceof ApiError) return error;
  const detail = error instanceof Error ? error.message : String(error);
  return new ApiError(0, detail, url);
}

export function errorLabel(error: ApiError): string {
  return error.status > 0 ? `HTTP ${error.status}` : 'unreachable';
}
