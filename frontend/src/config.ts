export const STORED_USER_ID_KEY = "jobmatchflow_user_id";
export const AUTH_SESSION_KEY = "jobmatchflow_auth_session";
export const ADMIN_VIEW_USER_KEY = "jobmatchflow_admin_view_user";

export type AuthSession = { id: number; email: string; name: string; role: "user" | "admin" };

export function readAuthSession(): AuthSession | null {
  try { const value = localStorage.getItem(AUTH_SESSION_KEY); return value ? JSON.parse(value) as AuthSession : null; } catch { return null; }
}

export function writeAuthSession(session: AuthSession): void { localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(session)); }
export function clearAuthSession(): void { localStorage.removeItem(AUTH_SESSION_KEY); }

/** localStorage only caches the session for an instant first paint — the
 * httpOnly cookie is the actual source of truth, reconciled via fetchMe() at
 * app boot (see App.tsx). Never trust this cache alone for an API call. */

export function readAdminViewUserId(): number | null {
  const value = Number(localStorage.getItem(ADMIN_VIEW_USER_KEY));
  return Number.isFinite(value) && value >= 1 ? value : null;
}

export function writeAdminViewUserId(id: number): void { localStorage.setItem(ADMIN_VIEW_USER_KEY, String(id)); }

export function activeUserId(session: AuthSession | null): number {
  if (session?.role === "admin") return readAdminViewUserId() ?? 0;
  return session?.id ?? 0;
}

export function readStoredUserId(): number {
  if (typeof window === "undefined") return 1;
  const s = localStorage.getItem(STORED_USER_ID_KEY);
  const n = s ? parseInt(s, 10) : 1;
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

export function writeStoredUserId(id: number): void {
  localStorage.setItem(STORED_USER_ID_KEY, String(id));
}

/** Full app-relative path (including the Vite base, e.g. "/app/") for use with window.open. */
export function appPath(path: string): string {
  const base = import.meta.env.BASE_URL.endsWith("/") ? import.meta.env.BASE_URL : `${import.meta.env.BASE_URL}/`;
  return `${base}${path.replace(/^\//, "")}`;
}
