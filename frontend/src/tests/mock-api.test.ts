import { describe, expect, it } from "vitest";
import { MockKnowledgeApi } from "../api/mock-knowledge-api";
import { useSessionStore } from "../app/session-store";

function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe("MockKnowledgeApi", () => {
  it("catholyte query returns numeric facts and markdown answer", async () => {
    const api = new MockKnowledgeApi();
    const res = await api.search({ query: "Какая скорость циркуляции католита при электроэкстракции никеля?", filters: {} });
    expect(res.intent).toBe("numeric");
    expect(res.answer_md).toContain("20–30 л/ч");
    const flow = res.facts.find((f) => f.ref === "catholyte-flow");
    expect(flow).toBeDefined();
    expect(flow!.value_low).toBe(20);
    expect(flow!.value_high).toBe(30);
    expect(flow!.doc_id).toBe("cdd5b92b3ff84174");
  });

  it("does not invent facts for an unknown query", async () => {
    const api = new MockKnowledgeApi();
    const res = await api.search({ query: "qzxzy abracadabra флюксоид 999999", filters: {} });
    // либо пустой факт-лист, либо маркированный «поиск» без сущностных фактов
    expect(res.facts.length).toBe(0);
    expect(res.intent).toBe("search");
  });

  it("hides internal facts from external_partner and reports hidden_count", async () => {
    const api = new MockKnowledgeApi();
    useSessionStore.getState().setRole("external_partner");
    const res = await api.search({ query: "Закачка шахтных вод в глубокие горизонты", filters: {} });
    useSessionStore.getState().setRole("researcher");
    expect(res.facts.every((f) => (f.sensitivity ?? "public") === "public")).toBe(true);
    expect(res.hidden_count).toBeGreaterThan(0);
  });

  it("literature review returns markdown with sources", async () => {
    const api = new MockKnowledgeApi();
    const res = await api.literatureReview({ query: "католит" });
    expect(res.markdown).toContain("## ");
    expect(res.markdown).toContain("Источники");
  });

  it("export markdown returns text/markdown blob", async () => {
    const api = new MockKnowledgeApi();
    const res = await api.search({ query: "католит", filters: {} });
    const blob = await api.exportResult("markdown", res);
    expect(blob.type).toContain("text/markdown");
    expect(await readBlobText(blob)).toContain("20–30 л/ч");
  });

  it("dashboard summary exposes KPI fields from API.md §5", async () => {
    const api = new MockKnowledgeApi();
    const s = await api.dashboardSummary();
    expect(s.docs).toBeGreaterThan(0);
    expect(s.ru_share).toBeGreaterThan(0);
    expect(s.fact_coverage).toBeLessThan(1);
  });

  it("contradictions filter by kind", async () => {
    const api = new MockKnowledgeApi();
    const all = await api.fetchContradictions();
    const method = await api.fetchContradictions("method_vs_method");
    expect(method.every((c) => c.kind === "method_vs_method")).toBe(true);
    expect(method.length).toBeLessThanOrEqual(all.length);
  });
});
