import type { KnowledgeApi } from "./knowledge-api";
import { HttpKnowledgeApi } from "./http-knowledge-api";
import { MockKnowledgeApi } from "./mock-knowledge-api";

export const api: KnowledgeApi = import.meta.env.VITE_API_MODE === "http" ? new HttpKnowledgeApi() : new MockKnowledgeApi();
export const isMockMode = import.meta.env.VITE_API_MODE !== "http";
