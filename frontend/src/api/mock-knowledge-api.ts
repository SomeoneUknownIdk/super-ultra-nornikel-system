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
  Fact,
  FilterOptions,
  Filters,
  LiteratureReviewRequest,
  LiteratureReviewResponse,
  NeighborhoodRequest,
  NeighborhoodResponse,
  NotifyCheckItem,
  NotifySubscription,
  ParseQueryRequest,
  ParseQueryResponse,
  ReferenceRow,
  Role,
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
import { confidenceLabel } from "../domain/types";
import { buildSubgraph, graphEdges, graphNodes } from "../mocks/fixtures/graph";
import { fetchContradictions } from "../mocks/fixtures/contradictions";
import {
  compareTechnologies,
  coverageByDomain,
  coverageByGeo,
  coverageByYear,
  dashboardActivity as activityFixture,
  dashboardExperts as expertsFixture,
  dashboardSummary,
  riskZones,
} from "../mocks/fixtures/dashboard";
import {
  docById,
  docs,
  emptyDocs,
  experts as expertIndex,
  facts as factTable,
  filterOptions,
  resolveScenario,
} from "../mocks/fixtures/facts";
import { useSessionStore } from "../app/session-store";

const delay = (ms: number, signal?: AbortSignal) =>
  import.meta.env.MODE === "test"
    ? Promise.resolve()
    : new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(resolve, ms);
        signal?.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timer);
            reject(new DOMException("Операция отменена", "AbortError"));
          },
          { once: true },
        );
      });

const clone = <T,>(value: T): T => structuredClone(value);

function passesFilters(fact: Fact, filters: Filters): boolean {
  if (filters.min_confidence != null && (fact.confidence ?? 0.5) < filters.min_confidence) return false;
  if (filters.confidence?.length) {
    const level = confidenceLabel(fact.confidence ?? 0.5);
    if (!filters.confidence.includes(level)) return false;
  }
  if (filters.year && Array.isArray(filters.year) && filters.year.length === 2) {
    const [from, to] = filters.year as [number, number];
    if (fact.year != null && (fact.year < from || fact.year > to)) return false;
  }
  if (filters.geo?.length) {
    const doc = docById(fact.doc_id);
    const geo = doc?.geo ?? "WORLD";
    if (!filters.geo.includes(geo)) return false;
  }
  if (filters.material?.length && !filters.material.some((m) => fact.canon.toLowerCase().includes(m.toLowerCase()))) return false;
  if (filters.process?.length && !filters.process.some((p) => fact.canon.toLowerCase().includes(p.toLowerCase()))) return false;
  return true;
}

function describeFilters(filters: Filters): string | null {
  const parts: string[] = [];
  if (filters.year && Array.isArray(filters.year) && filters.year.length === 2) {
    const [from, to] = filters.year as [number, number];
    if (from || to) parts.push(`год ${from}–${to}`);
  }
  if (filters.geo?.length) parts.push(`гео: ${filters.geo.join(", ")}`);
  if (filters.material?.length) parts.push(`материал: ${filters.material.join(", ")}`);
  if (filters.process?.length) parts.push(`процесс: ${filters.process.join(", ")}`);
  if (filters.confidence?.length) parts.push(`достоверность: ${filters.confidence.join(", ")}`);
  if (filters.min_confidence != null) parts.push(`≥ ${filters.min_confidence}`);
  return parts.length ? parts.join("; ") : null;
}

function genericResponse(query: string, role: Role, filters: Filters): SearchResponse {
  const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 3);
  const ranked = factTable
    .map((fact) => ({ fact, score: words.filter((w) => `${fact.canon} ${fact.metric ?? ""} ${fact.quote}`.toLowerCase().includes(w)).length }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((x) => x.fact);
  const selected = ranked.filter((f) => passesFilters(f, filters)).slice(0, 8);
  return assemble(role, filters, "search", selected, "Недостаточно структурированных данных для полного вывода. Уточните материал, процесс, географию или числовой диапазон.");
}

function assemble(role: Role, filters: Filters, intent: SearchResponse["intent"], selectedRaw: Fact[], answerMd: string): SearchResponse {
  const visible = selectedRaw.filter((f) => passesFilters(f, filters));
  const restricted = role === "external_partner";
  const publicFacts = restricted ? visible.filter((f) => (f.sensitivity ?? "public") === "public") : visible;
  const hiddenCount = visible.length - publicFacts.length;
  const docIds = Array.from(new Set(publicFacts.map((f) => f.doc_id)));
  const docHits = emptyDocs(docIds);
  const expertsWithDocs = expertIndex.filter((e) => e.doc_ids.some((id: string) => docIds.includes(id))).map((e) => ({ name: e.name, docs: e.doc_ids.length }));
  const adjacentTopics = Array.from(
    new Set(publicFacts.map((f) => f.canon).filter((c) => !filters.process?.includes(c) && !filters.material?.includes(c))),
  ).slice(0, 4).map((canon) => {
    const doc = factTable.find((f) => f.canon === canon);
    return { doc_id: doc?.doc_id ?? canon, source: canon };
  });
  return {
    intent,
    answer_md: answerMd,
    facts: publicFacts,
    docs: docHits,
    experts: expertsWithDocs,
    recommendations: {
      similar_cases: docHits.slice(1),
      adjacent_topics: adjacentTopics.map((t) => ({ doc_id: t.doc_id, source: t.source })),
      experts: expertsWithDocs,
    },
    hidden_count: hiddenCount,
    filters_applied: describeFilters(filters),
  };
}

function literatureMarkdown(query: string): string {
  const scenario = resolveScenario(query);
  if (!scenario) {
    return `## Литературный обзор\n\nПо запросу «${query}» не найдено достаточно профильных публикаций. Уточните материал, процесс или период.`;
  }
  const ids = scenario.selectDocIds;
  const docsList = ids.map((id, i) => `${i + 1}. ${docById(id)?.title ?? id}`).join("\n");
  return `${scenario.answerMd(ids)}\n\n## Источники\n\n${docsList}`;
}

export class MockKnowledgeApi implements KnowledgeApi {
  async search(request: SearchRequest): Promise<SearchResponse> {
    await delay(1850);
    const { query, filters } = request;
    const role = useSessionStore.getState().role;
    const scenario = resolveScenario(query);
    const selected = scenario ? factTable.filter((f) => scenario.selectDocIds.includes(f.doc_id)) : [];
    const response = scenario
      ? assemble(role, filters, scenario.intent, selected, scenario.answerMd(scenario.selectDocIds))
      : genericResponse(query, role, filters);
    return clone(response);
  }
  async getFilterOptions(): Promise<FilterOptions> {
    await delay(180);
    return clone(filterOptions);
  }
  async literatureReview(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse> {
    await delay(1500);
    return { markdown: literatureMarkdown(request.query) };
  }
  async recommend(request: LiteratureReviewRequest): Promise<LiteratureReviewResponse> {
    await delay(1500);
    return { markdown: `## Рекомендация\n\nАвтономное демо не синтезирует рекомендацию — подключите бэкенд.\n\n_Запрос: ${request.query}_` };
  }
  async parseQuery(request: ParseQueryRequest): Promise<ParseQueryResponse> {
    await delay(80);
    const values = [...request.text.matchAll(/\d+(?:[.,]\d+)?/g)].map((match) => ({ value: Number(match[0].replace(",", ".")), span: [match.index ?? 0, (match.index ?? 0) + match[0].length] }));
    const entities = graphNodes.filter((node) => request.text.toLowerCase().includes(node.label.toLowerCase())).map((node) => ({ canon: node.label, type: node.type, span: [request.text.toLowerCase().indexOf(node.label.toLowerCase()), request.text.toLowerCase().indexOf(node.label.toLowerCase()) + node.label.length] as [number, number] }));
    return { intent: values.length ? "numeric" : "search", has_numbers: values.length > 0, values, entities };
  }
  async suggestEntities(q: string): Promise<SuggestEntity[]> {
    await delay(100);
    const needle = q.toLowerCase();
    return graphNodes.filter((node) => !["Document", "Expert"].includes(node.type) && node.label.toLowerCase().includes(needle)).slice(0, 10).map((node) => ({ id: node.id, label: node.label, type: node.type, source_count: graphEdges.filter((edge) => edge.src === node.id || edge.dst === node.id).length }));
  }
  async graphSubgraph(request: SubgraphRequest): Promise<SubgraphResponse> {
    await delay(420);
    return clone(buildSubgraph(request.doc_ids, request.limit));
  }
  async getGraph(request: NeighborhoodRequest): Promise<NeighborhoodResponse> {
    await delay(300);
    const root = graphNodes.find((node) => node.id === request.entity_id || node.label === request.entity_id) ?? graphNodes[0];
    const included = new Set([root.id]);
    let frontier = new Set([root.id]);
    for (let level = 0; level < request.depth; level += 1) {
      const next = new Set<string>();
      graphEdges.forEach((edge) => {
        if (frontier.has(edge.src)) next.add(edge.dst);
        if (frontier.has(edge.dst)) next.add(edge.src);
      });
      next.forEach((id) => included.add(id));
      frontier = next;
    }
    const edges = graphEdges.filter((edge) => included.has(edge.src) && included.has(edge.dst)).slice(0, request.limit ?? 60).map((edge, index) => ({ id: `${edge.src}-${edge.dst}-${index}`, source: edge.src, target: edge.dst, type: edge.type }));
    const nodes = graphNodes.filter((node) => included.has(node.id)).map((node) => ({ ...node, canonical: node.label, sourceCount: edges.filter((edge) => edge.source === node.id || edge.target === node.id).length, confidence: 80 }));
    return clone({ nodes, edges });
  }
  async referenceDesalination(): Promise<ReferenceRow[]> { return []; }
  async referenceCatholyte(): Promise<ReferenceRow[]> { return []; }
  async referencePgm(): Promise<ReferenceRow[]> { return []; }
  async fetchContradictions(kind?: ContradictionKind): Promise<ContradictionItem[]> {
    await delay(280);
    return fetchContradictions(kind);
  }
  async dashboardSummary(): Promise<DashboardSummary> { await delay(280); return clone(dashboardSummary); }
  async dashboardCoverageDomain(): Promise<CoverageDomain[]> { await delay(180); return clone(coverageByDomain); }
  async dashboardCoverageYear(): Promise<CoverageYear[]> { await delay(180); return clone(coverageByYear); }
  async dashboardCoverageGeo(): Promise<CoverageGeo[]> { await delay(180); return clone(coverageByGeo); }
  async dashboardRisks(): Promise<RiskZones> { await delay(280); return clone(riskZones); }
  async dashboardActivity(limit = 50): Promise<DashboardActivity[]> { await delay(280); return clone(activityFixture.slice(0, limit)); }
  async dashboardExperts(limit = 50): Promise<DashboardExpert[]> { await delay(280); return clone(expertsFixture.slice(0, limit)); }
  async dashboardCompare(request: CompareRequest): Promise<CompareResponse> { await delay(320); return clone(compareTechnologies(request.processes)); }
  async exportResult(format: ExportFormat, payload: SearchResponse): Promise<Blob> {
    await delay(200);
    if (format === "markdown") {
      const body = `# Экспорт результата\n\n## Запрос\n${payload.answer_md}\n\n## Факты (${payload.facts.length})\n` +
        payload.facts.map((f, i) => `${i + 1}. ${f.canon} · ${f.metric ?? "—"}: ${f.value_low}${f.value_high !== f.value_low ? `–${f.value_high}` : ""} · ${f.quote}`).join("\n");
      return new Blob([body], { type: "text/markdown;charset=utf-8" });
    }
    if (format === "jsonld") {
      const json = {
        "@context": { "@vocab": "https://schema.org/" },
        "@type": "Dataset",
        name: "Результат поиска",
        description: payload.answer_md,
        hasPart: payload.facts.map((f) => ({
          "@type": "Claim",
          name: `${f.canon}: ${f.metric ?? ""}`,
          value: [f.value_low, f.value_high],
          citation: f.doc_id,
        })),
      };
      return new Blob([JSON.stringify(json, null, 2)], { type: "application/ld+json;charset=utf-8" });
    }
    // pdf — %{payload.answer_md}
    const body = `%PDF-1.4\n% Экспорт результата\n% ${payload.answer_md.replace(/\n/g, " ")}\n%%EOF`;
    return new Blob([body], { type: "application/pdf" });
  }
  async curationEdit(_request: CurationEdit): Promise<CurationResult> { return { ok: true }; }
  async curationAdd(_request: CurationAdd): Promise<CurationResult> { return { ok: true }; }
  async curationDelete(_request: CurationDelete): Promise<CurationResult> { return { ok: true }; }
  async curationHistory(): Promise<CurationHistory[]> { return []; }
  async notifySubscribe(user: string, query: string): Promise<NotifySubscription> {
    await delay(120);
    return { user, query, last_seen_iso: new Date().toISOString() };
  }
  async notifyUnsubscribe(): Promise<boolean> { await delay(120); return true; }
  async notifyListSubscriptions(): Promise<NotifySubscription[]> { await delay(120); return []; }
  async notifyCheck(): Promise<NotifyCheckItem[]> { await delay(120); return []; }
  async notifyMarkSeen(): Promise<void> { await delay(120); }
  async readAudit(): Promise<AuditEntry[]> { return []; }
  async searchDocuments(params: DocumentsQuery): Promise<DocumentsResponse> {
    await delay(180);
    const records = docs.map(mockDocument).filter((doc) => (!params.q || `${doc.title} ${doc.filename}`.toLowerCase().includes(params.q.toLowerCase())) && (!params.geo || doc.geography === params.geo) && (!params.doc_type || doc.sourceType === params.doc_type));
    records.sort((a, b) => params.sort === "date" ? (b.year ?? 0) - (a.year ?? 0) : b.factCount - a.factCount);
    const page = params.page ?? 1; const pageSize = params.page_size ?? 20;
    return { total: records.length, page, page_size: pageSize, items: clone(records.slice((page - 1) * pageSize, page * pageSize)) };
  }
  async getDocument(docId: string): Promise<DocumentCard> {
    await delay(120);
    const doc = docs.find((item) => item.doc_id === docId);
    if (!doc) throw new Error("Документ не найден");
    const selectedFacts = factTable.filter((fact) => fact.doc_id === docId);
    return { meta: mockDocument(doc), facts_count: selectedFacts.length, facts: selectedFacts.map((fact) => ({ canon: fact.canon, metric: fact.metric, value_low: fact.value_low, value_high: fact.value_high, unit: fact.unit, quote: fact.quote, confidence: fact.confidence ?? null })) };
  }
  async uploadDocuments(files: File[], onProgress?: (event: UploadProgress) => void, signal?: AbortSignal): Promise<UploadResult[]> {
    const results: UploadResult[] = [];
    for (const file of files) {
      onProgress?.({ fileName: file.name, stage: "upload", percent: 15 });
      await delay(150, signal);
      onProgress?.({ fileName: file.name, stage: "extraction", percent: 60 });
      await delay(250, signal);
      results.push({ doc_id: `mock-${file.name}`, duplicate: false, doc_type: "report", facts_added: 3, edges_added: 2 });
      onProgress?.({ fileName: file.name, stage: "complete", percent: 100 });
    }
    return results;
  }
  async health(): Promise<HealthResponse> { return { ok: true, neo4j: true, parameters: factTable.length }; }
}

function mockDocument(doc: (typeof docs)[number]): DocumentRecord {
  const factCount = factTable.filter((fact) => fact.doc_id === doc.doc_id).length;
  return { id: doc.doc_id, title: doc.title, filename: `${doc.title}.pdf`, year: doc.year, geography: doc.geo, sensitivity: doc.sensitivity, sourceType: "article", factCount, trust: Math.min(5, Math.max(1, Math.round(factCount + 2))) };
}
