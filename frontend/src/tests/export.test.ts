import { describe, expect, it } from "vitest";
import { MockKnowledgeApi } from "../api/mock-knowledge-api";

function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe("exports", () => {
  it("markdown export contains facts and values from search response", async () => {
    const api = new MockKnowledgeApi();
    const res = await api.search({ query: "циркуляция католита", filters: {} });
    const blob = await api.exportResult("markdown", res);
    const markdown = await readBlobText(blob);
    expect(markdown).toContain("20–30 л/ч");
    expect(markdown).toContain("электроэкстракция");
  });
});
