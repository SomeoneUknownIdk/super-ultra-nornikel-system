import type { ConfidenceLevel, Filters } from "../../domain/types";

export type LocalFilters = {
  geo: string[];
  material: string[];
  process: string[];
  confidence: ConfidenceLevel[];
  minConfidence: number;
};

/** Преобразует локальное состояние фильтров UI в Filters из API-контракта (см. API.md §0). */
export function buildFilters(local: LocalFilters): Filters {
  return {
    geo: local.geo.length ? local.geo : undefined,
    material: local.material.length ? local.material : undefined,
    process: local.process.length ? local.process : undefined,
    confidence: local.confidence.length ? local.confidence : undefined,
    min_confidence: local.minConfidence > 0 ? local.minConfidence : undefined,
  };
}