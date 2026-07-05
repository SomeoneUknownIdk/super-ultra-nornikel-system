import type { ContradictionItem } from "../../domain/types";

/**
 * Демонстрационный набор противоречий (API.md §4).
 * `src`/`dst` — doc_id карточек; для деталей значений A/B тянутся карточки по src/dst.
 */
export const contradictions: ContradictionItem[] = [
  { rel: "CONTRADICTS", kind: "method_vs_method", src: "cdd5b92b3ff84174", dst: "6d48e64d934f8d7d", sources: null },
  { rel: "CONTRADICTS", kind: "ru_vs_world", src: "43f9157e458b127e", dst: "2efcea298dad83ca", sources: null },
  { rel: "VALIDATED_BY", kind: "method_vs_method", src: "80361f9171bda73b", dst: "a2f56b6b3438d22f", sources: null },
];

export function fetchContradictions(kind?: string): ContradictionItem[] {
  if (!kind) return contradictions.map((c) => ({ ...c }));
  return contradictions.filter((c) => c.kind === kind).map((c) => ({ ...c }));
}