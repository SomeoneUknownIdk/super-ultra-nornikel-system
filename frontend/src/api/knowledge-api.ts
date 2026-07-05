import type {
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
  AuditEntry,
  HealthResponse,
} from "../domain/types";

/**
 * API-контракт «Научный клубок» — точное зеркало `API.md`.
 * Включает весь публичный контракт backend, даже если для метода пока нет страницы.
 */
export interface KnowledgeApi {
  // === §1 Поиск ===
  search(request: SearchRequest): Promise<SearchResponse>;
  getFilterOptions(): Promise<FilterOptions>;
  literatureReview(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse>;
  recommend(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse>;
  parseQuery(request: ParseQueryRequest): Promise<ParseQueryResponse>;
  suggestEntities(q: string): Promise<SuggestEntity[]>;

  // === §2 Граф ===
  graphSubgraph(request: SubgraphRequest): Promise<SubgraphResponse>;
  getGraph(request: NeighborhoodRequest): Promise<NeighborhoodResponse>;

  // === §3 Эталонные запросы ===
  referenceDesalination(maxSulfate?: number): Promise<ReferenceRow[]>;
  referenceCatholyte(): Promise<ReferenceRow[]>;
  referencePgm(years?: number): Promise<ReferenceRow[]>;

  // === §4 Противоречия ===
  fetchContradictions(kind?: ContradictionKind): Promise<ContradictionItem[]>;

  // === §5 Дашборд руководителя ===
  dashboardSummary(): Promise<DashboardSummary>;
  dashboardCoverageDomain(): Promise<CoverageDomain[]>;
  dashboardCoverageYear(): Promise<CoverageYear[]>;
  dashboardCoverageGeo(): Promise<CoverageGeo[]>;
  dashboardRisks(): Promise<RiskZones>;
  dashboardActivity(limit?: number): Promise<DashboardActivity[]>;
  dashboardExperts(limit?: number): Promise<DashboardExpert[]>;
  dashboardCompare(request: CompareRequest): Promise<CompareResponse>;

  // === §6 Экспорт результата ===
  exportResult(format: ExportFormat, payload: SearchResponse): Promise<Blob>;

  // === §7 Ручная правка ===
  curationEdit(request: CurationEdit): Promise<CurationResult>;
  curationAdd(request: CurationAdd): Promise<CurationResult>;
  curationDelete(request: CurationDelete): Promise<CurationResult>;
  curationHistory(limit?: number): Promise<CurationHistory[]>;

  // === §8 Уведомления/подписки (контракт сохранён — UI отсутствует) ===
  notifySubscribe(user: string, query: string): Promise<NotifySubscription>;
  notifyUnsubscribe(user: string, query: string): Promise<boolean>;
  notifyListSubscriptions(user?: string): Promise<NotifySubscription[]>;
  notifyCheck(user: string): Promise<NotifyCheckItem[]>;
  notifyMarkSeen(user: string, query: string): Promise<void>;

  // === §9 Аудит ===
  readAudit(limit?: number): Promise<AuditEntry[]>;

  // === §10 Документы ===
  searchDocuments(params: DocumentsQuery): Promise<DocumentsResponse>;
  getDocument(docId: string): Promise<DocumentCard>;
  uploadDocuments(files: File[], onProgress?: (event: UploadProgress) => void, signal?: AbortSignal): Promise<UploadResult[]>;

  health(): Promise<HealthResponse>;
}
