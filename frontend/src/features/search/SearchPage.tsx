import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, ChevronDown, ChevronUp, Download, FileJson, FileText, Filter, Lightbulb, RotateCcw, ShieldOff, Sparkles, TriangleAlert, Users } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, isMockMode } from "../../api/api-provider";
import { confidenceLabel, type ConfidenceLevel, type SearchResponse } from "../../domain/types";
import { confidenceLabels, geoOptions, intentLabels, unitLabels } from "../../domain/labels";
import { useSessionStore } from "../../app/session-store";
import { Badge } from "../../components/ui/Primitives";
import { downloadExport } from "../export/export";
import { buildFilters, type LocalFilters } from "./filters";
import s from "../../styles/ui.module.css";

// Рендер markdown-ответа: ссылки явно кликабельны, GFM-таблицы — с рамками.
const mdComponents = {
  a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} style={{ color: "#0a66c2", textDecoration: "underline", fontWeight: 600 }} />
  ),
  table: (props: React.TableHTMLAttributes<HTMLTableElement>) => (
    <div style={{ overflowX: "auto", margin: "10px 0" }}><table {...props} style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }} /></div>
  ),
  th: (props: React.ThHTMLAttributes<HTMLTableCellElement>) => (
    <th {...props} style={{ border: "1px solid #e2e8f0", padding: "7px 10px", textAlign: "left", background: "#f6f8fb", fontWeight: 700 }} />
  ),
  td: (props: React.TdHTMLAttributes<HTMLTableCellElement>) => (
    <td {...props} style={{ border: "1px solid #e2e8f0", padding: "7px 10px", verticalAlign: "top" }} />
  ),
};
const mdPlugins = [remarkGfm];

const examples = [
  { label: "Циркуляция католита", query: "Какая скорость циркуляции католита при электроэкстракции никеля?" },
  { label: "Обессоливание воды", query: "Какое обессоливание воды применять при сульфатах и хлоридах 200–300 мг/л и сухом остатке 1930 мг/л?" },
  { label: "Au, Ag и МПГ", query: "Распределение Au, Ag и МПГ между штейном и шлаком в плавке" },
  { label: "Шахтные воды", query: "Закачка шахтных вод в глубокие горизонты: российская практика" },
];

type Mode = "search" | "recommend" | "literature_review";
const modeLabels: Record<Mode, string> = { search: "Поиск", recommend: "Рекомендация", literature_review: "Литобзор" };

function plural(n: number, one: string, few: string, many: string) { const m10 = n % 10, m100 = n % 100; if (m10 === 1 && m100 !== 11) return one; if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few; return many; }

function formatValue(fact: { value_low: number | null; value_high: number | null; unit: keyof typeof unitLabels | null }) {
  const lo = fact.value_low, hi = fact.value_high;
  const unit = fact.unit ? ` ${unitLabels[fact.unit]}` : "";
  // односторонние границы (в данных бывает только lo ИЛИ только hi) не должны
  // давать «null–3» / «60–null» — показываем присутствующее число.
  let range: string;
  if (lo != null && hi != null) range = hi !== lo ? `${lo}–${hi}` : `${lo}`;
  else if (lo != null) range = `${lo}`;
  else if (hi != null) range = `${hi}`;
  else return "—";
  return `${range}${unit}`;
}

export function SearchPage() {
  const { history, addHistory } = useSessionStore();
  const [query, setQuery] = useState(""); const [mode, setMode] = useState<Mode>("search");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filters, setFilters] = useState<LocalFilters>({ geo: [], material: [], process: [], confidence: [], minConfidence: 0 });
  const [answer, setAnswer] = useState<SearchResponse | null>(null);
  const [literature, setLiterature] = useState<{ markdown: string } | null>(null);
  const [recommendation, setRecommendation] = useState<{ markdown: string } | null>(null);

  const options = useQuery({ queryKey: ["filter-options"], queryFn: () => api.getFilterOptions() });

  const search = useMutation<SearchResponse, Error, { query: string; filters: LocalFilters }>({
    mutationFn: (req) => api.search({ query: req.query, filters: buildFilters(req.filters) }),
    onSuccess: (result, variables) => { setAnswer(result); setLiterature(null); setRecommendation(null); addHistory(variables.query); },
  });
  const review = useMutation<{ markdown: string }, Error, { query: string }>({
    mutationFn: (req) => api.literatureReview({ query: req.query }),
    onSuccess: (result, variables) => { setLiterature(result); setAnswer(null); setRecommendation(null); addHistory(variables.query); },
  });
  const recommend = useMutation<{ markdown: string }, Error, { query: string }>({
    mutationFn: (req) => api.recommend({ query: req.query }),
    onSuccess: (result, variables) => { setRecommendation(result); setAnswer(null); setLiterature(null); addHistory(variables.query); },
  });

  const pending = search.isPending || review.isPending || recommend.isPending;

  // Переход из графа/литобзора: ?entity=<сущность> или ?q=<запрос> → авто-поиск.
  const [searchParams] = useSearchParams();
  const autoRan = useRef("");
  useEffect(() => {
    const q = searchParams.get("entity") || searchParams.get("q");
    if (q && autoRan.current !== q) { autoRan.current = q; setQuery(q); submit(q); }
    /* eslint-disable-next-line */
  }, [searchParams]);

  function submit(nextQuery = query) {
    if (!nextQuery.trim()) return;
    setQuery(nextQuery);
    // Очистить прошлый результат сразу — иначе при смене режима во время синтеза
    // на экране висит старая выдача под спиннером.
    setAnswer(null); setLiterature(null); setRecommendation(null);
    if (mode === "literature_review") review.mutate({ query: nextQuery });
    else if (mode === "recommend") recommend.mutate({ query: nextQuery });
    else search.mutate({ query: nextQuery, filters });
  }

  return <div className={s.searchPage}>
    <section className={`${s.hero} ${answer || literature || recommendation ? s.heroCompact : ""}`}>
      <div className={s.heroIntro}>{isMockMode && <Badge tone="blue"><Sparkles size={13}/> Автономное демо</Badge>}<h1>Знания R&D — в одном ответе</h1><p>Задайте вопрос о материалах, процессах и условиях. Система найдёт связи, проверит числа и покажет доказательства.</p></div>
      <div className={s.queryCard}>
        <div className={s.modeTabs} aria-label="Режим анализа">{(["search", "recommend", "literature_review"] as const).map((item) => <button key={item} className={mode === item ? s.modeActive : ""} onClick={() => setMode(item)}>{modeLabels[item]}</button>)}</div>
        <div className={s.queryInputWrap}><textarea value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") submit(); }} placeholder={mode === "recommend" ? "Инженерный вопрос: какое обессоливание при сульфатах 200–300 мг/л и сухом остатке ≤1000 мг/дм³?" : "Например: сравните способы циркуляции католита при электроэкстракции никеля…"} rows={answer || literature || recommendation ? 2 : 3}/><button className={s.primaryButton} disabled={!query.trim() || pending} onClick={() => submit()}>{pending ? "Анализируем" : "Исследовать"}<ArrowRight size={18}/></button></div>
        <div className={s.queryFooter}><button className={s.textButton} onClick={() => setFiltersOpen((v) => !v)}><Filter size={16}/>Фильтры{filtersOpen ? <ChevronUp size={15}/> : <ChevronDown size={15}/>}</button><span>Ctrl + Enter</span></div>
        {filtersOpen && mode === "search" && (
          <div className={s.filterPanel}>
            <label>География
              <select multiple value={filters.geo} onChange={(e) => setFilters((f) => ({ ...f, geo: Array.from(e.target.selectedOptions).map((o) => o.value) }))}>
                {geoOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                {(options.data?.geos ?? []).filter((g) => !geoOptions.some((o) => o.value === g)).map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            <label>Материал (через запятую)<input value={filters.material.join(", ")} onChange={(e) => setFilters((f) => ({ ...f, material: e.target.value.split(",").map((m) => m.trim()).filter(Boolean) }))} placeholder="никель, медь"/></label>
            <label>Процесс (через запятую)<input value={filters.process.join(", ")} onChange={(e) => setFilters((f) => ({ ...f, process: e.target.value.split(",").map((p) => p.trim()).filter(Boolean) }))} placeholder="электроэкстракция"/></label>
            <label>Достоверность
              <select multiple value={filters.confidence} onChange={(e) => setFilters((f) => ({ ...f, confidence: Array.from(e.target.selectedOptions).map((o) => o.value) as ConfidenceLevel[] }))}>
                {options.data?.confidence_levels.map((lvl) => <option key={lvl} value={lvl}>{confidenceLabels[lvl]}</option>)}
              </select>
            </label>
            <label>Порог достоверности (0–1)<input type="range" min={0} max={1} step={0.1} value={filters.minConfidence} onChange={(e) => setFilters((f) => ({ ...f, minConfidence: Number(e.target.value) }))}/><span>{filters.minConfidence.toFixed(1)}</span></label>
          </div>
        )}
      </div>
      {!answer && !literature && !pending && <><div className={s.exampleRow}>{examples.map((example) => <button key={example.label} onClick={() => { setQuery(example.query); submit(example.query); }}><span>{example.label}</span><ArrowRight size={15}/></button>)}</div>{history.length > 0 && (<div className={s.history}><Lightbulb size={16}/><span>Недавние:</span>{history.slice(0, 3).map((item) => <button key={item} onClick={() => setQuery(item)}>{item}</button>)}</div>)}</>}
    </section>

    {pending && <section className={s.processing} aria-live="polite"><div className={s.processingOrb}><Sparkles/></div><div><h2>{mode === "search" ? "Ищем факты в корпусе" : mode === "recommend" ? "Синтезируем рекомендацию" : "Готовим литобзор"}</h2><p>Сопоставляем запрос с документами и графом знаний</p></div></section>}
    {(search.isError || review.isError || recommend.isError) && <div className={s.errorBanner}><TriangleAlert/>Ошибка: {search.error?.message ?? review.error?.message ?? recommend.error?.message}<button onClick={() => submit()}><RotateCcw size={16}/>Повторить</button></div>}
    {answer && <AnswerView answer={answer} onExport={(fmt) => downloadExport(fmt, answer)} />}
    {literature && <LiteratureView markdown={literature.markdown} />}
    {recommendation && <LiteratureView markdown={recommendation.markdown} title="Рекомендация" eyebrow="Grounded-синтез по фактам корпуса"/>}
  </div>;
}

function AnswerView({ answer, onExport }: { answer: SearchResponse; onExport: (fmt: "markdown" | "jsonld" | "pdf") => void }) {
  // Эксперты: expert-intent даёт их в answer.experts, обычный поиск — в
  // recommendations.experts. Раньше панель читала только первое и не показывалась.
  const experts = answer.experts.length ? answer.experts : answer.recommendations.experts;
  return <article className={s.answerView}>
    <header className={s.answerHeader}>
      <div><div className={s.eyebrow}><Badge tone="blue">{intentLabels[answer.intent]}</Badge></div><h2>Результат исследования</h2><div className={s.answerMeta}><span><BookOpen/> {answer.facts.length} {plural(answer.facts.length, "факт", "факта", "фактов")}</span><span><FileText/> {answer.docs.length} {plural(answer.docs.length, "источник", "источника", "источников")}</span></div></div>
      {(() => { const empty = !answer.facts.length && !answer.docs.length; const t = empty ? "Нет данных для экспорта" : undefined; return <div className={s.exportGroup}><button disabled={empty} onClick={() => onExport("markdown")} title={t ?? "Markdown"}><FileText/>MD</button><button disabled={empty} onClick={() => onExport("jsonld")} title={t ?? "JSON-LD"}><FileJson/>JSON-LD</button><button disabled={empty} onClick={() => onExport("pdf")} title={t ?? "PDF"}><Download/>PDF</button></div>; })()}
    </header>
    {answer.hidden_count > 0 && <div className={s.errorBanner}><ShieldOff/>По вашему уровню доступа скрыто {answer.hidden_count} {answer.hidden_count === 1 ? "факт" : "фактов"}</div>}
    <div className={s.answerGrid}><div className={s.answerMain}>
      <section className={`${s.panel} ${s.summaryPanel}`}><div className={s.panelIcon}><Lightbulb/></div><div><h3>Ответ</h3><ReactMarkdown remarkPlugins={mdPlugins} components={mdComponents}>{answer.answer_md}</ReactMarkdown></div></section>
      {answer.facts.length > 0 && (
        <section className={s.panel}><SectionTitle icon={<FileText/>} title={`Факты · ${answer.facts.length}`} subtitle="Карточки результата поиска"/><div className={s.comparisonTable}><div className={s.tableHead}><span>Сущность</span><span>Параметр</span><span>Значение</span><span>Фаза</span><span>Источник</span><span>Достоверность</span></div>{answer.facts.map((fact, i) => <div className={s.tableRow} key={`${fact.doc_id}-${i}`}><span data-label="Сущность"><b>{fact.canon}</b></span><span data-label="Параметр">{fact.metric ?? "—"}</span><span data-label="Значение"><b>{formatValue(fact)}</b></span><span data-label="Фаза">{fact.phase ?? "—"}</span><span data-label="Источник"><a href={`/sources?doc=${fact.doc_id}`} title={fact.quote} style={{ color: "#0a66c2", textDecoration: "none", whiteSpace: "nowrap" }}>{fact.year ?? "источник"} ↗</a></span><span data-label="Достоверность">{fact.confidence != null ? <Badge tone={toneForConfidence(fact.confidence)}>{confidenceLabel(fact.confidence)}</Badge> : <span style={{ color: "#94a3b8", fontSize: 12 }}>н/д</span>}</span></div>)}</div></section>
      )}
      {experts.length > 0 && (
        <section className={s.panel}><SectionTitle icon={<Users/>} title="Эксперты" subtitle="Носители компетенций по теме"/><div className={s.expertGrid}>{experts.map((expert) => <a key={expert.name} className={s.expertCard} href={`/sources?q=${encodeURIComponent(expert.name)}`} style={{ textDecoration: "none", color: "inherit" }}><span>{expert.name.split(" ").map((n) => n[0]).join("").slice(0, 2)}</span><div><b>{expert.name}</b><p>{expert.docs} {plural(expert.docs, "публикация", "публикации", "публикаций")} ↗</p></div></a>)}</div></section>
      )}
      {(answer.recommendations.similar_cases.length > 0 || answer.recommendations.adjacent_topics.length > 0) && (
        <section className={s.panel}><SectionTitle icon={<Lightbulb/>} title="Смежные темы" subtitle="Граф-соседи процессов и материалов"/><div className={s.tokenList}>{[...answer.recommendations.similar_cases, ...answer.recommendations.adjacent_topics].map((hit, i) => {
          // similar_cases → {doc_id, source}; adjacent_topics → {type, canon}. Берём то, что есть.
          const h = hit as { source?: string; canon?: string; doc_id?: string };
          const label = h.source ?? h.canon;
          const href = h.doc_id ? `/sources?doc=${h.doc_id}` : `/?q=${encodeURIComponent(h.canon ?? "")}`;
          return label ? <a key={i} className={s.token} href={href} style={{ textDecoration: "none" }}>{label}</a> : null;
        })}</div></section>
      )}
    </div></div>
  </article>;
}

function LiteratureView({ markdown, title = "Литобзор", eyebrow = "Литературный обзор" }: { markdown: string; title?: string; eyebrow?: string }) {
  return <article className={s.answerView}><header className={s.answerHeader}><div><div className={s.eyebrow}><Badge tone="blue">{eyebrow}</Badge></div><h2>{title}</h2></div></header><div className={s.answerGrid}><div className={s.answerMain}><section className={`${s.panel} ${s.summaryPanel}`}><div className={s.panelIcon}><BookOpen/></div><div><h3>{title}</h3><ReactMarkdown remarkPlugins={mdPlugins} components={mdComponents}>{markdown}</ReactMarkdown></div></section></div></div></article>;
}

function toneForConfidence(value?: number): "green" | "amber" | "red" {
  if (value == null) return "amber";
  if (value >= 0.8) return "green";
  if (value >= 0.5) return "amber";
  return "red";
}
function SectionTitle({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle: string }) { return <div className={s.sectionTitle}><span>{icon}</span><div><h3>{title}</h3><p>{subtitle}</p></div></div>; }
