import type { Role } from "../domain/types";

const base = import.meta.env.VITE_API_BASE_URL ?? "";

export interface AuthUser {
  username: string;
  role: Role;
  full_name?: string | null;
  active?: boolean;
  created_at?: string | null;
  created_by?: string | null;
}

export interface LoginResult {
  token: string;
  user: AuthUser;
}

function authHeaders(token?: string): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

async function req<T>(path: string, init: RequestInit): Promise<T> {
  const r = await fetch(`${base}${path}`, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export const authApi = {
  login: (username: string, password: string) =>
    req<LoginResult>("/api/auth/login", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ username, password }),
    }),

  me: (token: string) =>
    req<AuthUser & { authenticated: boolean }>("/api/auth/me", {
      headers: authHeaders(token),
    }),

  changePassword: (token: string, oldPassword: string, newPassword: string) =>
    req<{ ok: boolean }>("/api/auth/change-password", {
      method: "POST", headers: authHeaders(token),
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),

  listUsers: (token: string) =>
    req<AuthUser[]>("/api/users", { headers: authHeaders(token) }),

  createUser: (token: string, u: { username: string; password: string; role: Role; full_name?: string }) =>
    req<AuthUser>("/api/users", {
      method: "POST", headers: authHeaders(token), body: JSON.stringify(u),
    }),

  updateUser: (token: string, username: string,
    patch: { role?: Role; password?: string; active?: boolean; full_name?: string }) =>
    req<AuthUser>(`/api/users/${encodeURIComponent(username)}`, {
      method: "PATCH", headers: authHeaders(token), body: JSON.stringify(patch),
    }),

  deleteUser: (token: string, username: string) =>
    req<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}`, {
      method: "DELETE", headers: authHeaders(token),
    }),
};
