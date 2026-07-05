import { create } from "zustand";
import type { Role } from "../domain/types";
import { persistence } from "../mocks/persistence";

const TOKEN_KEY = "nk.auth.token";
const USER_KEY = "nk.auth.user";

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

interface SessionState {
  role: Role;
  token: string | null;
  username: string | null;
  history: string[];
  setRole: (role: Role) => void;
  /** Успешный логин: сохранить токен+роль (роль становится авторитетной). */
  setAuth: (token: string, username: string, role: Role) => void;
  logout: () => void;
  addHistory: (query: string) => void;
  reset: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  role: persistence.getRole(),
  token: read(TOKEN_KEY),
  username: read(USER_KEY),
  history: persistence.getHistory(),
  setRole(role) {
    persistence.setRole(role);
    set({ role });
  },
  setAuth(token, username, role) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, username);
    } catch { /* ignore */ }
    persistence.setRole(role);
    set({ token, username, role });
  },
  logout() {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch { /* ignore */ }
    set({ token: null, username: null });
  },
  addHistory(query) {
    persistence.addHistory(query);
    set({ history: persistence.getHistory() });
  },
  reset() {
    persistence.reset();
    set({ role: "researcher", history: [] });
  },
}));
