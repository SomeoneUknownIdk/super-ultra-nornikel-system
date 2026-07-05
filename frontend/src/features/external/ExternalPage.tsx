import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, ExternalLink, FileText, Loader2, Search, Check } from "lucide-react";
import { externalApi, type ExternalArticle, type ExternalImportResult } from "../../api/external-api";
import { PageHeader, Badge, ErrorState } from "../../components/ui/Primitives";
import s from "../../styles/ui.module.css";

type ImportState = { status: "idle" | "loading" | "done" | "error"; result?: ExternalImportResult; error?: string };

export function ExternalPage() {
  const [query, setQuery] = useState("");
  const [imports, setImports] = useState<Record<string, ImportState>>({});

  const search = useMutation({
    mutationFn: (q: string) => externalApi.search(q),
  });

  function submit(e?: React.FormEvent) {
    e?.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    setImports({});
    search.mutate(q);
  }

  async function importArticle(article: ExternalArticle) {
    setImports((m) => ({ ...m, [article.url]: { status: "loading" } }));
    try {
      const result = await externalApi.import(article.url, article.title);
      setImports((m) => ({ ...m, [article.url]: { status: "done", result } }));
    } catch (err) {
      setImports((m) => ({ ...m, [article.url]: { status: "error", error: err instanceof Error ? err.message : "Ошибка" } }));
    }
  }

  const results = search.data?.results ?? [];

  return <div>
    <PageHeader eyebrow="ВНЕШНИЕ ИСТОЧНИКИ" title="Поиск в CyberLeninka"
      description="Найдите научные статьи в открытом доступе, выберите нужную — бэкенд скачает PDF и добавит извлечённые факты в граф." />

    <form onSubmit={submit} style={bar}>
      <div style={searchWrap}>
        <Search size={18} color="#64748b" />
        <input style={inp} value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Материал, процесс или тема — напр. «электроэкстракция никеля»" autoFocus />
      </div>
      <button className={s.primaryButton} type="submit" disabled={search.isPending || query.trim().length < 2}>
        {search.isPending ? <><Loader2 size={16} className={s.spin} /> Ищем…</> : <>Искать <Search size={16} /></>}
      </button>
    </form>

    {search.isError && <ErrorState message={search.error instanceof Error ? search.error.message : "Источник недоступен"} />}
    {search.isSuccess && results.length === 0 && <p className={s.emptyState}>Ничего не найдено. Уточните запрос.</p>}

    {results.length > 0 && <>
      <div style={{ color: "#64748b", fontSize: 13, margin: "4px 2px 14px" }}>Найдено {results.length} статей · CyberLeninka</div>
      <div style={{ display: "grid", gap: 12 }}>
        {results.map((article) => {
          const st = imports[article.url] ?? { status: "idle" };
          return <article key={article.url} style={card}>
            <span style={icon}><FileText size={20} /></span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                <Badge tone="blue">PDF</Badge>
                {st.status === "done" && !st.result?.duplicate && <Badge tone="green">В графе · +{st.result?.facts_added ?? 0} фактов</Badge>}
                {st.status === "done" && st.result?.duplicate && <Badge tone="amber">Уже в базе</Badge>}
              </div>
              <b style={{ display: "block", fontSize: 15, lineHeight: 1.35, color: "#0f172a" }}>{article.title}</b>
              {article.authors && <small style={{ color: "#64748b" }}>{article.authors}</small>}
              {st.status === "error" && <p style={{ color: "#dc2626", fontSize: 12, margin: "6px 0 0" }}>{st.error}</p>}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "none", alignItems: "flex-end" }}>
              <button style={loadBtn(st.status)} disabled={st.status === "loading" || st.status === "done"}
                onClick={() => importArticle(article)}>
                {st.status === "loading" ? <><Loader2 size={15} className={s.spin} /> Загружаем…</>
                  : st.status === "done" ? <><Check size={15} /> Готово</>
                  : <><Download size={15} /> Загрузить в граф</>}
              </button>
              <a href={article.url} target="_blank" rel="noreferrer" style={extLink}>Открыть <ExternalLink size={12} /></a>
            </div>
          </article>;
        })}
      </div>
    </>}
  </div>;
}

const bar: React.CSSProperties = { display: "flex", gap: 10, marginBottom: 20 };
const searchWrap: React.CSSProperties = { flex: 1, display: "flex", alignItems: "center", gap: 10, padding: "0 14px", background: "white", border: "1px solid #e2e8f0", borderRadius: 10 };
const inp: React.CSSProperties = { flex: 1, border: 0, outline: "none", padding: "13px 0", fontSize: 14, background: "transparent" };
const card: React.CSSProperties = { display: "flex", gap: 14, alignItems: "flex-start", padding: 18, background: "white", border: "1px solid #e2e8f0", borderRadius: 14 };
const icon: React.CSSProperties = { width: 40, height: 40, flex: "none", display: "grid", placeItems: "center", color: "#9333ea", background: "#f3e8ff", borderRadius: 10 };
const extLink: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "#64748b", textDecoration: "none" };
function loadBtn(status: ImportState["status"]): React.CSSProperties {
  const base: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 8, border: "none", fontSize: 13, fontWeight: 600, cursor: status === "loading" || status === "done" ? "default" : "pointer", whiteSpace: "nowrap" };
  if (status === "done") return { ...base, background: "#dcfce7", color: "#16a34a" };
  if (status === "loading") return { ...base, background: "#e2e8f0", color: "#475569" };
  return { ...base, background: "#0a66c2", color: "white" };
}
