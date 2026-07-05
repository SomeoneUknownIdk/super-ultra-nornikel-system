import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpKnowledgeApi } from "../api/http-knowledge-api";
import { useSessionStore } from "../app/session-store";

const api = new HttpKnowledgeApi();

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().setRole("researcher");
});

describe("HttpKnowledgeApi", () => {
  it("sends the current role in X-Role and not in the search body", async () => {
    useSessionStore.getState().setRole("analyst");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.search({ query: "никель", filters: {} });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("X-Role")).toBe("analyst");
    expect(JSON.parse(String(init.body))).toEqual({ query: "никель", filters: {} });
  });

  it("maps backend document fields to the UI model", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      total: 1,
      page: 1,
      page_size: 20,
      items: [{ doc_id: "doc-1", name: "Отчёт", src: "report.pdf", doc_type: "report", year: 2025, geo: "RU", sensitivity: "internal", fact_count: 7 }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    const result = await api.searchDocuments({ geo: "RU" });

    expect(result.items[0]).toMatchObject({ id: "doc-1", title: "Отчёт", filename: "report.pdf", geography: "RU", factCount: 7 });
  });
});
