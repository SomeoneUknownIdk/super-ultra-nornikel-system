import { useMemo, useRef, useState } from "react";
import { useDismiss } from "../../app/use-dismiss";
import { useQuery } from "@tanstack/react-query";
import { Background, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { ArrowRight, BookOpen, GitFork, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/api-provider";
import { entityLabels, relationLabels } from "../../domain/labels";
import type { EntityType, KnowledgeNode } from "../../domain/types";
import { Badge, ErrorState, LoadingBlock, PageHeader } from "../../components/ui/Primitives";
import s from "../../styles/ui.module.css";

const colors: Record<EntityType, string> = { Process: "#2563eb", Material: "#d97706", Equipment: "#059669", Facility: "#64748b", Experiment: "#7c3aed", Publication: "#9333ea", Expert: "#ea580c", Parameter: "#dc2626", Phase: "#0891b2", Property: "#dc2626", Document: "#9333ea", Condition: "#0f766e", Domain: "#4f46e5", Claim: "#be123c", Author: "#ea580c", Topic: "#475569" };

export function GraphPage() {
  const [term, setTerm] = useState("электроэкстракция"); const [rootId, setRootId] = useState("PR:электроэкстракция"); const [depth, setDepth] = useState<1|2|3>(2); const [selected, setSelected] = useState<KnowledgeNode | null>(null);
  const [showSuggest, setShowSuggest] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  useDismiss(showSuggest, () => setShowSuggest(false), searchRef);  // закрытие подсказок по клику вне / Escape
  const suggestions = useQuery({ queryKey: ["entities", term], queryFn: () => api.suggestEntities(term), enabled: term.length > 1 });
  const graph = useQuery({ queryKey: ["graph", rootId, depth], queryFn: () => api.getGraph({ entity_id: rootId, depth }) });
  const flow = useMemo(() => layout(graph.data?.nodes ?? [], graph.data?.edges ?? [], rootId), [graph.data, rootId]);
  return <div>
    <PageHeader eyebrow="КАРТА СВЯЗЕЙ" title="Граф знаний" description="Исследуйте локальную окрестность сущности без шума полного графа." />
    <div className={s.graphToolbar}><div className={s.entitySearch} ref={searchRef}><Search/><input value={term} onFocus={() => setShowSuggest(true)} onChange={(e) => { setTerm(e.target.value); setShowSuggest(true); }} placeholder="Материал, процесс, оборудование…"/>{showSuggest && suggestions.data && term && <div className={s.suggestions}>{suggestions.data.map((item) => <button key={item.id} onClick={() => { setRootId(item.id); setTerm(item.label); setSelected(null); setShowSuggest(false); }}><span style={{ background: colors[item.type] }}/><b>{item.label}</b><small>{entityLabels[item.type]} · {item.source_count} источников</small></button>)}</div>}</div><div className={s.depthControl}><span>Глубина</span>{([1,2,3] as const).map((value) => <button key={value} className={depth === value ? s.depthActive : ""} onClick={() => setDepth(value)}>{value}</button>)}</div><div className={s.graphLegend}>{(["Process","Material","Equipment","Parameter"] as EntityType[]).map((type) => <span key={type}><i style={{ background: colors[type] }}/>{entityLabels[type]}</span>)}</div></div>
    <section className={s.graphWorkspace}>{graph.isLoading ? <LoadingBlock label="Строим окрестность сущности"/> : graph.isError ? <ErrorState message={graph.error.message}/> : <div className={s.flowCanvas}><ReactFlow nodes={flow.nodes} edges={flow.edges} onNodeClick={(_, node) => setSelected(graph.data?.nodes.find((item) => item.id === node.id) ?? null)} fitView minZoom={0.35} maxZoom={1.8} proOptions={{ hideAttribution: true }}><Background gap={22} color="#d9dee7"/><Controls/><MiniMap className={s.miniMap} nodeColor={(node) => String(node.style?.background ?? "#94a3b8")} pannable zoomable/></ReactFlow></div>}
      {selected && <aside className={s.nodePanel}><button className={s.closeButton} onClick={() => setSelected(null)} aria-label="Закрыть"><X/></button><span className={s.nodeType} style={{ color: colors[selected.type] }}>{entityLabels[selected.type]}</span><h2>{selected.label}</h2><p className={s.canonical}>{selected.canonical}</p>{selected.aliases?.length ? <div className={s.aliasList}>{selected.aliases.map((alias) => <Badge key={alias}>{alias}</Badge>)}</div> : null}<div className={s.nodeStats} style={selected.confidence == null ? { gridTemplateColumns: "1fr" } : undefined}><div><strong>{selected.sourceCount ?? 0}</strong><span>источников</span></div>{selected.confidence != null && <div><strong>{Math.round(selected.confidence <= 1 ? selected.confidence * 100 : selected.confidence)}%</strong><span>уверенность</span></div>}</div><h3>Связи</h3><div className={s.nodeRelations}>{(() => { const byId = new Map((graph.data?.nodes ?? []).map((n) => [n.id, n])); const seen = new Set<string>(); const out: React.ReactNode[] = []; (graph.data?.edges ?? []).filter((edge) => edge.source === selected.id || edge.target === selected.id).forEach((edge) => { const otherId = edge.source === selected.id ? edge.target : edge.source; const key = `${edge.type}-${otherId}`; if (seen.has(key)) return; seen.add(key); const other = byId.get(otherId); out.push(<button key={edge.id} type="button" onClick={() => { setRootId(otherId); setSelected(null); }} style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: 0, cursor: "pointer", color: "#0a66c2", padding: "2px 0", textAlign: "left" }}><GitFork size={13}/>{relationLabels[edge.type as keyof typeof relationLabels] ?? edge.type} · {other?.label ?? otherId}</button>); }); return out.length ? out : <span style={{ color: "#94a3b8" }}>прямых связей нет</span>; })()}</div><button className={s.secondaryButton} onClick={() => { setRootId(selected.id); setSelected(null); }}>Раскрыть связи <GitFork/></button><Link className={s.primaryLink} to={`/?entity=${encodeURIComponent(selected.canonical)}`}>Искать по сущности <ArrowRight/></Link><div className={s.nodeNote}><BookOpen/>Показаны связи с доказательствами из корпуса</div></aside>}
    </section>
  </div>;
}

function layout(nodes: KnowledgeNode[], edges: { id: string; source: string; target: string; type: string }[], rootId: string): { nodes: Node[]; edges: Edge[] } {
  const root = nodes.findIndex((node) => node.id === rootId); const ordered = root > 0 ? [nodes[root], ...nodes.filter((_, index) => index !== root)] : nodes;
  // Концентрические кольца с растущей ёмкостью (ring k держит ~ (k+1)*7 узлов),
  // чтобы соседи не наезжали друг на друга «клубком» при большой окрестности.
  const ringCap = (k: number) => (k + 1) * 7;   // 7, 14, 21, …
  const place = (index: number) => {
    if (index === 0) return { ring: 0, local: 0, count: 1 };
    let rest = index - 1, ring = 1;
    while (rest >= ringCap(ring)) { rest -= ringCap(ring); ring += 1; }
    // сколько узлов реально на этом кольце (для равномерного угла)
    let base = 1; for (let k = 1; k < ring; k++) base += ringCap(k);
    const count = Math.min(ringCap(ring), ordered.length - base);
    return { ring, local: rest, count: Math.max(1, count) };
  };
  const flowNodes = ordered.map((node, index) => {
    const { ring, local, count } = place(index);
    const angle = (local / count) * Math.PI * 2 - Math.PI / 2 + ring * 0.4;  // сдвиг колец
    const radius = ring === 0 ? 0 : 200 + (ring - 1) * 210;
    return { id: node.id, position: { x: 560 + Math.cos(angle) * radius, y: 420 + Math.sin(angle) * radius }, data: { label: node.label }, style: { background: colors[node.type], color: "white", border: "3px solid white", boxShadow: "0 4px 18px #1f293733", borderRadius: 12, width: index === 0 ? 176 : 148, padding: 11, fontWeight: 700, fontSize: 13 } };
  });
  const flowEdges = edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target,  style: { stroke: "#94a3b8", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8" } }));
  return { nodes: flowNodes, edges: flowEdges };
}
