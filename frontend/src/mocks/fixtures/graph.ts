import type { GraphEdgeRef, GraphNodeRef, SubgraphResponse } from "../../domain/types";

/**
 * Демонстрационный граф-виз: цепочки материал→процесс→оборудование→результат.
 * `id` однозначно в формате `"<префикс>:<canon|doc_id>"` (API.md §2).
 * Префиксы: PR — процесс, M — материал, O — продукт, C — условие, EQ — оборудование,
 * D — документ, X — эксперт.
 */
export const graphNodes: GraphNodeRef[] = [
  { id: "PR:электроэкстракция", label: "электроэкстракция", type: "Process" },
  { id: "PR:обессоливание", label: "обессоливание", type: "Process" },
  { id: "PR:плавка", label: "плавка", type: "Process" },
  { id: "PR:глубинная закачка", label: "глубинная закачка", type: "Process" },
  { id: "M:никель", label: "никель", type: "Material" },
  { id: "M:католит", label: "католит", type: "Material" },
  { id: "M:шахтная вода", label: "шахтная вода", type: "Material" },
  { id: "M:золото", label: "золото", type: "Material" },
  { id: "O:шлак", label: "шлак", type: "Phase" },
  { id: "O:штейн", label: "штейн", type: "Phase" },
  { id: "EQ:электролизная ванна", label: "электролизная ванна", type: "Equipment" },
  { id: "EQ:печь взвешенной плавки", label: "ПВП", type: "Equipment" },
  { id: "D:cdd5b92b3ff84174", label: "Обзор технических решений", type: "Document" },
  { id: "D:80361f9171bda73b", label: "Влияние состава штейна", type: "Document" },
  { id: "X:Евграфова А. К.", label: "Евграфова А. К.", type: "Expert" },
];

export const graphEdges: GraphEdgeRef[] = [
  { src: "M:никель", dst: "PR:электроэкстракция", type: "USES_MATERIAL" },
  { src: "M:католит", dst: "PR:электроэкстракция", type: "USES_MATERIAL" },
  { src: "PR:электроэкстракция", dst: "EQ:электролизная ванна", type: "OPERATES_AT_CONDITION" },
  { src: "PR:электроэкстракция", dst: "D:cdd5b92b3ff84174", type: "DESCRIBED_IN" },
  { src: "PR:обессоливание", dst: "M:шахтная вода", type: "USES_MATERIAL" },
  { src: "M:шахтная вода", dst: "PR:глубинная закачка", type: "USES_MATERIAL" },
  { src: "PR:плавка", dst: "O:штейн", type: "PRODUCES_OUTPUT" },
  { src: "PR:плавка", dst: "O:шлак", type: "PRODUCES_OUTPUT" },
  { src: "M:золото", dst: "O:шлак", type: "USES_MATERIAL" },
  { src: "PR:плавка", dst: "D:80361f9171bda73b", type: "DESCRIBED_IN" },
  { src: "X:Евграфова А. К.", dst: "D:80361f9171bda73b", type: "AUTHORED_BY" },
  { src: "D:80361f9171bda73b", dst: "PR:плавка", type: "VALIDATED_BY" },
  { src: "D:cdd5b92b3ff84174", dst: "PR:электроэкстракция", type: "CONTRADICTS" },
];

/**
 * Собирает подграф-цепочку, проходящий через документы из `doc_ids`.
 * Реальный бэкенд: `graph.answer_subgraph(doc_ids, limit)` (API.md §2).
 */
export function buildSubgraph(docIds: string[], limit: number): SubgraphResponse {
  const matched = graphEdges.filter((edge) => {
    const srcDoc = edge.src.startsWith("D:") && docIds.includes(edge.src.slice(2));
    const dstDoc = edge.dst.startsWith("D:") && docIds.includes(edge.dst.slice(2));
    return srcDoc || dstDoc;
  });
  const selectedEdges = matched.slice(0, Math.max(0, limit));
  const ids = new Set<string>();
  selectedEdges.forEach((e) => { ids.add(e.src); ids.add(e.dst); });
  docIds.forEach((id) => ids.add(`D:${id}`));
  const nodes = graphNodes.filter((n) => ids.has(n.id));
  return { nodes, edges: selectedEdges };
}