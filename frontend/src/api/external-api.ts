import { useSessionStore } from "../app/session-store";

const base = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ExternalArticle {
  slug: string;
  title: string;
  authors: string;
  url: string;
  pdf_url: string;
}

export interface ExternalImportResult {
  doc_id: string;
  duplicate: boolean;
  facts_added?: number;
  edges_added?: number;
  message?: string;
}

function headers(): HeadersInit {
  const token = useSessionStore.getState().token;
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${base}${path}`, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

/** Внешние источники (CyberLeninka): поиск и импорт PDF в граф. */
export const externalApi = {
  search: (q: string) =>
    req<{ query: string; results: ExternalArticle[] }>(
      `/api/external/search?q=${encodeURIComponent(q)}`, { headers: headers() }),
  import: (url: string, title: string) =>
    req<ExternalImportResult>("/api/external/import", {
      method: "POST", headers: headers(), body: JSON.stringify({ url, title }),
    }),
};
