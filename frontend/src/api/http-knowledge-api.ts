import type { KnowledgeApi } from "./knowledge-api";
import type {
  AuditEntry,
  CompareRequest,
  CompareResponse,
  ContradictionKind,
  ContradictionItem,
  CoverageDomain,
  CoverageGeo,
  CoverageYear,
  DashboardActivity,
  DashboardExpert,
  DashboardSummary,
  DocumentCard,
  DocumentRecord,
  DocumentsQuery,
  DocumentsResponse,
  ExportFormat,
  FilterOptions,
  LiteratureReviewRequest,
  LiteratureReviewResponse,
  NeighborhoodRequest,
  NeighborhoodResponse,
  NotifyCheckItem,
  NotifySubscription,
  ParseQueryRequest,
  ParseQueryResponse,
  ReferenceRow,
  RiskZones,
  SearchRequest,
  SearchResponse,
  SuggestEntity,
  SubgraphRequest,
  SubgraphResponse,
  UploadProgress,
  UploadResult,
  CurationAdd,
  CurationDelete,
  CurationEdit,
  CurationHistory,
  CurationResult,
  HealthResponse,
} from "../domain/types";
import { useSessionStore } from "../app/session-store";

const base = import.meta.env.VITE_API_BASE_URL ?? "";

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-Role", useSessionStore.getState().role);
  { const t = useSessionStore.getState().token; if (t) headers.set("Authorization", `Bearer ${t}`); }
  if (init?.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${base}${path}`, { ...init, headers });
  if (!response.ok) throw new Error(`API ${response.status}: ${response.statusText}`);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function blobRequest(path: string, init: RequestInit): Promise<Blob> {
  const headers = new Headers(init.headers);
  headers.set("X-Role", useSessionStore.getState().role);
  { const t = useSessionStore.getState().token; if (t) headers.set("Authorization", `Bearer ${t}`); }
  const response = await fetch(`${base}${path}`, { ...init, headers });
  if (!response.ok) throw new Error(`API ${response.status}: ${response.statusText}`);
  return response.blob();
}

/**
 * HTTP-адаптер — точное зеркало API.md.
 * Роль синхронно читается из session store и передаётся заголовком `X-Role`.
 */
export class HttpKnowledgeApi implements KnowledgeApi {
  search(request: SearchRequest): Promise<SearchResponse> {
    return jsonRequest("/api/search", { method: "POST", body: JSON.stringify(request) });
  }
  getFilterOptions(): Promise<FilterOptions> {
    return jsonRequest("/api/filters/options");
  }
  literatureReview(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse> {
    return jsonRequest("/api/literature-review", { method: "POST", body: JSON.stringify(request) });
  }
  recommend(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse> {
    return jsonRequest("/api/recommend", { method: "POST", body: JSON.stringify(request) });
  }
  parseQuery(request: ParseQueryRequest): Promise<ParseQueryResponse> {
    return jsonRequest("/api/parse-query", { method: "POST", body: JSON.stringify(request) });
  }
  suggestEntities(q: string): Promise<SuggestEntity[]> {
    return jsonRequest(`/api/suggest-entities?q=${encodeURIComponent(q)}`);
  }
  graphSubgraph(request: SubgraphRequest): Promise<SubgraphResponse> {
    return jsonRequest("/api/graph/subgraph", { method: "POST", body: JSON.stringify(request) });
  }
  async getGraph(request: NeighborhoodRequest): Promise<NeighborhoodResponse> {
    const response = await jsonRequest<{ nodes: Array<Omit<NeighborhoodResponse["nodes"][number], "sourceCount"> & { source_count?: number }>; edges: NeighborhoodResponse["edges"] }>(
      "/api/graph/neighborhood", { method: "POST", body: JSON.stringify(request) },
    );
    return { ...response, nodes: response.nodes.map(({ source_count, ...node }) => ({ ...node, sourceCount: source_count })) };
  }
  referenceDesalination(maxSulfate = 300): Promise<ReferenceRow[]> { return jsonRequest(`/api/reference/desalination?max_sulfate=${maxSulfate}`); }
  referenceCatholyte(): Promise<ReferenceRow[]> { return jsonRequest("/api/reference/catholyte"); }
  referencePgm(years = 5): Promise<ReferenceRow[]> { return jsonRequest(`/api/reference/pgm?years=${years}`); }
  fetchContradictions(kind?: ContradictionKind): Promise<ContradictionItem[]> {
    const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
    return jsonRequest(`/api/contradictions${qs}`);
  }
  dashboardSummary(): Promise<DashboardSummary> { return jsonRequest("/api/dashboard/summary"); }
  dashboardCoverageDomain(): Promise<CoverageDomain[]> { return jsonRequest("/api/dashboard/coverage/domain"); }
  dashboardCoverageYear(): Promise<CoverageYear[]> { return jsonRequest("/api/dashboard/coverage/year"); }
  dashboardCoverageGeo(): Promise<CoverageGeo[]> { return jsonRequest("/api/dashboard/coverage/geo"); }
  dashboardRisks(): Promise<RiskZones> { return jsonRequest("/api/dashboard/risks"); }
  dashboardActivity(limit = 50): Promise<DashboardActivity[]> { return jsonRequest(`/api/dashboard/activity?limit=${limit}`); }
  dashboardExperts(limit = 50): Promise<DashboardExpert[]> { return jsonRequest(`/api/dashboard/experts?limit=${limit}`); }
  dashboardCompare(request: CompareRequest): Promise<CompareResponse> {
    return jsonRequest("/api/dashboard/compare", { method: "POST", body: JSON.stringify(request) });
  }
  exportResult(format: ExportFormat, payload: SearchResponse): Promise<Blob> {
    return blobRequest(`/api/export/${format}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  }
  curationEdit(request: CurationEdit): Promise<CurationResult> { return jsonRequest("/api/curation/edit", { method: "POST", body: JSON.stringify(request) }); }
  curationAdd(request: CurationAdd): Promise<CurationResult> { return jsonRequest("/api/curation/add", { method: "POST", body: JSON.stringify(request) }); }
  curationDelete(request: CurationDelete): Promise<CurationResult> { return jsonRequest("/api/curation/delete", { method: "POST", body: JSON.stringify(request) }); }
  curationHistory(limit = 50): Promise<CurationHistory[]> { return jsonRequest(`/api/curation/history?limit=${limit}`); }
  notifySubscribe(user: string, query: string): Promise<NotifySubscription> {
    return jsonRequest("/api/notify/subscribe", { method: "POST", body: JSON.stringify({ user, query }) });
  }
  notifyUnsubscribe(user: string, query: string): Promise<boolean> {
    return jsonRequest("/api/notify/unsubscribe", { method: "POST", body: JSON.stringify({ user, query }) });
  }
  notifyListSubscriptions(user?: string): Promise<NotifySubscription[]> {
    const qs = user ? `?user=${encodeURIComponent(user)}` : "";
    return jsonRequest(`/api/notify/subscriptions${qs}`);
  }
  notifyCheck(user: string): Promise<NotifyCheckItem[]> {
    return jsonRequest(`/api/notify/check?user=${encodeURIComponent(user)}`);
  }
  notifyMarkSeen(user: string, query: string): Promise<void> {
    return jsonRequest("/api/notify/mark-seen", { method: "POST", body: JSON.stringify({ user, query }) });
  }
  readAudit(limit = 500): Promise<AuditEntry[]> { return jsonRequest(`/api/audit?limit=${limit}`); }
  async searchDocuments(params: DocumentsQuery): Promise<DocumentsResponse> {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value != null && value !== "") query.set(key, String(value)); });
    const raw = await jsonRequest<{ total: number; page: number; page_size: number; items: RawDocument[] }>(`/api/documents?${query}`);
    return { ...raw, items: raw.items.map(mapDocument) };
  }
  async getDocument(docId: string): Promise<DocumentCard> {
    const raw = await jsonRequest<{ meta: RawDocument; facts_count: number; facts: DocumentCard["facts"] }>(`/api/documents/${encodeURIComponent(docId)}`);
    return { ...raw, meta: mapDocument({ ...raw.meta, fact_count: raw.facts_count }) };
  }
  async uploadDocuments(files: File[], onProgress?: (event: UploadProgress) => void, signal?: AbortSignal): Promise<UploadResult[]> {
    const results: UploadResult[] = [];
    for (const file of files) {
      onProgress?.({ fileName: file.name, stage: "upload", percent: 15 });
      const body = new FormData();
      body.append("file", file);
      const result = await jsonRequest<UploadResult>("/api/documents", { method: "POST", body, signal });
      onProgress?.({ fileName: file.name, stage: "extraction", percent: 85 });
      results.push(result);
      onProgress?.({ fileName: file.name, stage: "complete", percent: 100 });
    }
    return results;
  }
  health(): Promise<HealthResponse> { return jsonRequest("/health"); }
}

interface RawDocument {
  doc_id: string;
  name?: string | null;
  src?: string | null;
  doc_type?: string | null;
  year?: number | null;
  geo?: string | null;
  sensitivity?: DocumentRecord["sensitivity"] | null;
  fact_count?: number | null;
  /** средняя confidence фактов документа 0..1 (бэкенд); null если фактов нет */
  trust?: number | null;
}

function mapDocument(raw: RawDocument): DocumentRecord {
  // trust = ср.confidence (0..1) → 1..5 звёзд; проиндексированный документ ≥1 звезды
  const stars = raw.trust == null ? 0 : Math.max(1, Math.min(5, Math.round(raw.trust * 5)));
  return {
    id: raw.doc_id,
    title: raw.name || raw.src || raw.doc_id,
    filename: raw.src || raw.name || raw.doc_id,
    year: raw.year ?? null,
    geography: raw.geo || "WORLD",
    sensitivity: raw.sensitivity || "internal",
    sourceType: raw.doc_type || "report",
    factCount: raw.fact_count ?? 0,
    trust: stars,
  };
}
