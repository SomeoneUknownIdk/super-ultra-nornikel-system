import type { Role } from "../domain/types";

const PREFIX = "scientific-knot:demo:v1";
export const storageKeys = {
  session: `${PREFIX}:session`,
  history: `${PREFIX}:history`,
  preferences: `${PREFIX}:preferences`,
};

function read<T>(key: string, fallback: T): T {
  try { const value = localStorage.getItem(key); return value ? JSON.parse(value) as T : fallback; } catch { return fallback; }
}
function write<T>(key: string, value: T) { localStorage.setItem(key, JSON.stringify(value)); }

export const persistence = {
  getRole: () => read<Role>(storageKeys.session, "researcher"),
  setRole: (role: Role) => write(storageKeys.session, role),
  getHistory: () => read<string[]>(storageKeys.history, []),
  addHistory(query: string) { write(storageKeys.history, [query, ...this.getHistory().filter((q) => q !== query)].slice(0, 8)); },
  reset() { Object.values(storageKeys).forEach((key) => localStorage.removeItem(key)); },
};