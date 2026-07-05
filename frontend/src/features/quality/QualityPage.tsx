import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, GitCompareArrows, ShieldCheck, TriangleAlert } from "lucide-react";
import { api } from "../../api/api-provider";
import type { ContradictionKind } from "../../domain/types";
import { contradictionKindLabels, relationLabels, unitLabels } from "../../domain/labels";
import { Badge, ErrorState, LoadingBlock, PageHeader } from "../../components/ui/Primitives";
import s from "../../styles/ui.module.css";

const kindOptions: { value: ContradictionKind | ""; label: string }[] = [
  { value: "", label: "Все виды" },
  { value: "ru_vs_world", label: "Россия vs мир" },
  { value: "method_vs_method", label: "Метод vs метод" },
];

export function QualityPage() {
  const [kind, setKind] = useState<ContradictionKind | "">("");
  const [openCard, setOpenCard] = useState<number | null>(null);
  const data = useQuery({ queryKey: ["contradictions", kind], queryFn: () => api.fetchContradictions(kind || undefined) });
  // «Противоречий» — только CONTRADICTS (иначе цифра включала VALIDATED_BY и
  // расходилась с дашбордом: 77 total = 65 CONTRADICTS + 12 подтверждений).
  const contradictionCount = data.data?.filter((c) => c.rel === "CONTRADICTS").length;
  return <div><PageHeader eyebrow="ВЕРИФИКАЦИЯ" title="Противоречия" description="Числовые расхождения между источниками."/>
    <div className={s.qualitySummary}>
      <div><span className={s.qualityIconRed}><TriangleAlert/></span><strong>{contradictionCount ?? "—"}</strong><small>противоречий</small></div>
      <div><span className={s.qualityIconGreen}><ShieldCheck/></span><strong>{data.data?.filter((c) => c.rel === "VALIDATED_BY").length ?? "—"}</strong><small>подтверждений</small></div>
    </div>
    <div className={s.sourceToolbar}>
      <label>Вид расхождения<select value={kind} onChange={(e) => setKind(e.target.value as ContradictionKind | "")}>{kindOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}</select></label>
    </div>
    {data.isLoading ? <LoadingBlock label="Загружаем противоречия"/> : data.isError ? <ErrorState message={data.error.message}/> : <div className={s.qualityList}>
      {data.data?.map((item, index) => <article className={`${s.qualityCard} ${item.rel === "VALIDATED_BY" ? s.validationCard : ""}`} key={`${item.src}-${item.dst}-${index}`}>
        <header><span className={item.rel === "VALIDATED_BY" ? s.qualityIconGreen : s.qualityIconRed}><GitCompareArrows/></span><div><Badge tone={item.rel === "CONTRADICTS" ? "red" : "green"}>{relationLabels[item.rel]}</Badge><h3>{item.entity ? `${item.entity}${item.metric ? " · " + item.metric : ""}${item.phase ? " [" + item.phase + "]" : ""}` : ((item.kind && contradictionKindLabels[item.kind]) ?? "расхождение значений")}</h3>{item.val_a != null && item.val_b != null ? <p style={{ fontWeight: 600, color: "var(--text)" }}>{item.rel === "CONTRADICTS" ? <>{item.val_a} <span style={{ color: "#c9721f" }}>≠</span> {item.val_b}{item.unit ? " " + ((unitLabels as Record<string, string>)[item.unit] ?? item.unit) : ""}</> : <>≈{item.val_a}{item.unit ? " " + ((unitLabels as Record<string, string>)[item.unit] ?? item.unit) : ""} · согласовано</>}{item.kind ? ` · ${contradictionKindLabels[item.kind] ?? item.kind}` : ""}</p> : <p>Расхождение между источниками</p>}</div><span title={`${item.src} ↔ ${item.dst}`}><Badge tone="amber">{item.src.slice(0, 14)}{item.src.length > 14 ? "…" : ""} ↔ {item.dst.slice(0, 14)}{item.dst.length > 14 ? "…" : ""}</Badge></span></header>
        {openCard === index && <div className={s.qualityDetail}>
          <div><b>A</b>{item.src_id ? <a href={`/sources?doc=${item.src_id}`} style={{ color: "#0a66c2" }}>{item.src} ↗</a> : <span>{item.src}</span>}</div>
          <div><b>B</b>{item.dst_id ? <a href={`/sources?doc=${item.dst_id}`} style={{ color: "#0a66c2" }}>{item.dst} ↗</a> : <span>{item.dst}</span>}</div>
        </div>}
        <footer><p><TriangleAlert/>{item.rel === "CONTRADICTS" ? "Требует эксперта" : "Поддержано несколькими источниками"}</p><button onClick={() => setOpenCard(openCard === index ? null : index)}>{openCard === index ? "Скрыть" : "Открыть карточки A/B"} <ArrowRight/></button></footer>
      </article>)}
      {data.data?.length === 0 && <p className={s.emptyState}>Противоречий выбранного вида нет.</p>}
    </div>}
  </div>;
}