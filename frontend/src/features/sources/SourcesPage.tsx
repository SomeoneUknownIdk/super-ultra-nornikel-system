import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, ChevronRight, FileText, Filter, Plus, Search, X } from "lucide-react";
import { api } from "../../api/api-provider";
import { geographyLabels, sourceTypeLabels, sensitivityLabels } from "../../domain/labels";
import type { DocumentRecord, Geography, SourceType } from "../../domain/types";
import { Badge, EmptyState, ErrorState, LoadingBlock, PageHeader, Stars } from "../../components/ui/Primitives";
import { ImportDialog } from "../import/ImportDialog";
import { useSessionStore } from "../../app/session-store";
import s from "../../styles/ui.module.css";

export function SourcesPage() {
  const [query, setQuery] = useState(""); const [geography, setGeography] = useState<Geography | "">(""); const [type, setType] = useState<SourceType | "">(""); const [sort, setSort] = useState<"relevance"|"date"|"trust">("relevance"); const [selected, setSelected] = useState<DocumentRecord | null>(null); const [importOpen, setImportOpen] = useState(false); const client = useQueryClient(); const { role } = useSessionStore();
  const result = useQuery({ queryKey: ["documents", query, geography, type, sort], queryFn: () => api.searchDocuments({ q: query || undefined, geo: geography || undefined, doc_type: type || undefined, sort }) });
  // Открытие по ссылке из ответа поиска: ?q=<запрос> предзаполняет поиск, ?doc=<id> открывает документ.
  const [searchParams] = useSearchParams();
  useEffect(() => { const q = searchParams.get("q"); if (q) setQuery(q); const g = searchParams.get("geo"); if (g) setGeography(g as Geography); /* eslint-disable-next-line */ }, [searchParams]);
  // Открытие дровера по ?doc — ТОЛЬКО один раз на docId. Раньше эффект зависел от
  // result.data: ввод в поиск менял выдачу → эффект перезапускался → закрытый
  // дровер снова открывался. Guard + зависимость только от URL это чинит.
  const openedDoc = useRef<string | null>(null);
  useEffect(() => {
    const docId = searchParams.get("doc");
    if (!docId || openedDoc.current === docId) return;
    openedDoc.current = docId;
    const found = result.data?.items.find((d) => d.id === docId);
    setSelected(found ?? ({ id: docId, title: docId } as DocumentRecord));
    /* eslint-disable-next-line */
  }, [searchParams]);
  const card = useQuery({ queryKey: ["document", selected?.id], queryFn: () => api.getDocument(selected!.id), enabled: Boolean(selected) });
  const canImport = role !== "external_partner"; const visibleItems = result.data?.items.filter((doc) => role !== "external_partner" || doc.sensitivity === "public") ?? [];
  return <div><PageHeader eyebrow="ДОКАЗАТЕЛЬНАЯ БАЗА" title="Источники" description="Документы, метаданные и извлечённые факты корпуса." actions={canImport ? <button className={s.primaryButton} onClick={() => setImportOpen(true)}><Plus/>Добавить источники</button> : <Badge tone="amber">Только просмотр</Badge>}/>
    <div className={s.sourceToolbar}><div className={s.toolbarSearch}><Search/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Название, автор или тема…"/></div><label><Filter/>География<select value={geography} onChange={(e) => setGeography(e.target.value as Geography | "")}><option value="">Любая</option>{Object.entries(geographyLabels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Тип<select value={type} onChange={(e) => setType(e.target.value as SourceType | "")}><option value="">Все</option>{Object.entries(sourceTypeLabels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Сортировка<select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}><option value="relevance">Релевантность</option><option value="date">Сначала новые</option><option value="trust">Доверие</option></select></label></div>
    <div className={s.documentSummary}><span>Доступно <b>{result.data?.total ?? visibleItems.length}</b> документов</span><span><i className={s.statusDot}/>Индекс актуален</span></div>
    {result.isLoading ? <LoadingBlock/> : result.isError ? <ErrorState message={result.error.message}/> : !visibleItems.length ? <EmptyState title="Источники не найдены" text="Измените запрос или сбросьте фильтры."/> : <div className={s.documentList}>{visibleItems.map((doc) => <button className={s.documentCard} key={doc.id} onClick={() => setSelected(doc)}><span className={s.documentIcon}><FileText/></span><span className={s.documentInfo}><span><Badge tone={doc.sourceType === "scientific_article" ? "green" : "blue"}>{sourceTypeLabels[doc.sourceType] ?? doc.sourceType}</Badge>{doc.sensitivity !== "public" && <Badge tone="amber">{sensitivityLabels[doc.sensitivity] ?? doc.sensitivity}</Badge>}</span><b>{doc.title}</b><small>{doc.filename}</small><span className={s.documentMeta}>{[doc.year ?? "Год не указан", geographyLabels[doc.geography], `${doc.factCount} фактов`].filter(Boolean).join(" · ")}</span></span><Stars value={doc.trust}/><span className={s.indexed}><i/>Проиндексирован</span><ChevronRight/></button>)}</div>}
    {selected && (() => { const doc = card.data?.meta ?? selected; return <div className={s.drawerBackdrop} onClick={() => setSelected(null)}><aside className={s.documentDrawer} onClick={(e) => e.stopPropagation()}><button className={s.closeButton} onClick={() => setSelected(null)}><X/></button><span className={s.documentIcon}><BookOpen/></span><Badge tone="blue">{sourceTypeLabels[doc.sourceType] ?? doc.sourceType ?? "Документ"}</Badge><h2>{doc.title}</h2><p>{doc.filename}</p>{card.isLoading ? <LoadingBlock/> : card.isError ? <ErrorState message={card.error.message}/> : <><dl><div><dt>Год</dt><dd>{doc.year ?? "Не указан"}</dd></div><div><dt>География</dt><dd>{geographyLabels[doc.geography] ?? doc.geography}</dd></div><div><dt>Доверие</dt><dd><Stars value={doc.trust}/></dd></div><div><dt>Извлечено фактов</dt><dd>{card.data?.facts_count ?? doc.factCount}</dd></div><div><dt>Доступ</dt><dd>{sensitivityLabels[doc.sensitivity] ?? doc.sensitivity}</dd></div></dl>{card.data?.facts.slice(0, 3).map((fact, index) => <blockquote key={index}>«{fact.quote ?? `${fact.canon}: ${fact.metric}`}»</blockquote>)}</>}</aside></div>; })()}
    {importOpen && <ImportDialog onClose={() => setImportOpen(false)} onComplete={() => { setImportOpen(false); void client.invalidateQueries({ queryKey: ["documents"] }); }}/>} 
  </div>;
}
