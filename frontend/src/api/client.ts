/**
 * Tiny typed fetch wrapper. All routes are relative — Vite proxies them in
 * dev and they go same-origin in production.
 */

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
    ...init,
  });
  if (!res.ok) {
    let parsed: unknown = null;
    try { parsed = await res.json(); } catch { /* non-JSON body */ }
    const msg =
      (parsed && typeof parsed === "object" && parsed && "detail" in parsed && String((parsed as any).detail)) ||
      `Request failed: ${method} ${path} → ${res.status}`;
    throw new ApiError(msg, res.status, parsed);
  }
  // The /static/* endpoints don't have a body — guard for empty
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get:  <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  del:  <T>(path: string) => request<T>("DELETE", path),
};
