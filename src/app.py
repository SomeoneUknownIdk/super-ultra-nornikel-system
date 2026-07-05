"""Этап 8: Streamlit UI («Научный клубок»).

Три вкладки: Поиск | Граф | Противоречия. Шапка: селектор роли + строка
поиска + 3 чипа-примера (дословные эталонные запросы ТЗ). RBAC применяется
WHERE-условием композера по оси sensitivity; аудит query/view/export пишется
в artifacts/audit.jsonl.

ВАЖНО (тестируемость): ВЕСЬ Streamlit-рендер обёрнут в функции и вызывается
только из main() под `if __name__ == "__main__"` (Streamlit исполняет модуль как
скрипт → __name__ == "__main__"; при `import src.app` в тесте — нет). Поэтому
`import src.app` НЕ рисует виджеты и НЕ требует запущенного сервера.

Устойчивость к холодному старту: любое обращение к Neo4j обёрнуто try/except →
пользователю показывается «База знаний загружается…» вместо traceback.

Композер (`from src.search import search`) импортируется мягко: модуль этапа 7
может ещё отсутствовать → фолбэк на эталонные Cypher-запросы графа. UI не падает.
"""
from __future__ import annotations

import os
import sys
import json
import html
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import ARTIFACTS, nfc  # noqa: E402
from src import graph  # noqa: E402
from src import exports, curation, notify, dashboard  # noqa: E402
from src.obs import get_logger, log_event as _obs_log  # noqa: E402

_LOG = get_logger("app")

# --- Мягкий импорт композера этапа 7 (может отсутствовать) --------------------
try:  # pragma: no cover — зависит от наличия модуля этапа 7
    from src.search import search as _search_fn  # type: ignore
except Exception:  # noqa: BLE001
    _search_fn = None

try:  # pragma: no cover
    from src.search import literature_review as _litreview_fn  # type: ignore
except Exception:  # noqa: BLE001
    _litreview_fn = None


# =============================================================================
# Константы UI
# =============================================================================

AUDIT_PATH = ARTIFACTS / "audit.jsonl"

ROLES = ["researcher", "analyst", "project_lead", "admin", "external_partner"]

ROLE_LABELS = {
    "researcher": "Исследователь",
    "analyst": "Аналитик",
    "project_lead": "Руководитель проекта",
    "admin": "Администратор",
    "external_partner": "Внешний партнёр",
}

# Дословные эталонные запросы ТЗ (TZ.md, задачи 1–3) — чипы-примеры.
EXAMPLE_CHIPS = [
    ("Обессоливание 200–300 мг/л",
     "Какие методы обессоливания воды подходят для обогатительной фабрики, "
     "если исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, "
     "а требуемый сухой остаток — ≤1000 мг/дм³?"),
    ("Циркуляция католита, электроэкстракция Ni",
     "Какие технические решения организации циркуляции католита при "
     "электроэкстракции никеля описаны в мировой практике, и какая скорость "
     "потока считается оптимальной?"),
    ("Распределение Au/Ag/МПГ штейн–шлак",
     "Покажите все эксперименты и публикации по распределению Au, Ag и МПГ "
     "между медным/никелевым штейном и шлаком за последние 5 лет"),
]

# Оси фильтров (multiselect в UI строятся из графа, здесь — дефолтные каноны).
CONFIDENCE_LEVELS = ["высокая", "средняя", "низкая"]

# Пороги перевода числовой достоверности (confidence 0..1) в слово ТЗ.
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MED = 0.5


def confidence_word(conf) -> str:
    """Число достоверности (0..1) → слово ТЗ: высокая/средняя/низкая.

    Устойчиво к None/строке: неизвестное значение → «средняя» (нейтрально).
    Если пришло уже слово (высокая/средняя/низкая) — возвращается как есть.
    """
    if isinstance(conf, str):
        s = conf.strip().lower()
        if s in CONFIDENCE_LEVELS:
            return s
        try:
            conf = float(s.replace(",", "."))
        except (ValueError, AttributeError):
            return "средняя"
    if conf is None:
        return "средняя"
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return "средняя"
    if c >= CONFIDENCE_HIGH:
        return "высокая"
    if c >= CONFIDENCE_MED:
        return "средняя"
    return "низкая"

# RBAC-матрица ТЗ (PLAN.md): доступ к уровням sensitivity по роли.
# secret_rd = чтение секретного слоя; admin_ops = служебные функции (аудит).
RBAC = {
    "external_partner": {"levels": {"public"}},
    "researcher":       {"levels": {"public", "internal"}},
    "analyst":          {"levels": {"public", "internal"}},
    "project_lead":     {"levels": {"public", "internal", "secret"}},
    "admin":            {"levels": {"public", "internal", "secret"}, "admin_ops": True},
}

# Цвета типизированных рёбер подграфа (Document–Experiment–Parameter–Material–Phase…).
EDGE_COLORS = {
    "MENTIONS": "#8892b0",
    "HAS_PARAM": "#64ffda",
    "MEASURES": "#f78c6c",
    "MEASURED_IN": "#c792ea",
    "DESCRIBED_IN": "#82aaff",
    "USES_MATERIAL": "#addb67",
    "OPERATES_AT_CONDITION": "#ffcb6b",
    "PRODUCES_OUTPUT": "#ff5370",
    "VALIDATED_BY": "#22da6e",
    "CONTRADICTS": "#ff5370",
    "AUTHORED_BY": "#c3a6ff",
}

NODE_COLORS = {
    "Document": "#82aaff",
    "Experiment": "#c792ea",
    "Parameter": "#64ffda",
    "Material": "#addb67",
    "Phase": "#f78c6c",
    "Process": "#ffcb6b",
    "Equipment": "#ff5370",
    "Condition": "#ffd580",
    "Expert": "#c3a6ff",
    "Facility": "#22da6e",
}


# =============================================================================
# Аудит (чистая функция — тестируется без Streamlit)
# =============================================================================

def log_event(role: str, event: str, payload=None, path=None) -> dict:
    """Дописывает одну JSONL-строку аудита в artifacts/audit.jsonl.

    Событие — одно из query / view / export (три типа ТЗ). Возвращает
    записанную запись (ts, role, event, payload, n_results).
    """
    path = AUDIT_PATH if path is None else path
    payload = payload if payload is not None else {}
    n_results = None
    if isinstance(payload, dict):
        n_results = payload.get("n_results")
    rec = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "role": nfc(role),
        "event": nfc(event),
        "payload": payload,
        "n_results": n_results,
    }
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# =============================================================================
# RBAC
# =============================================================================

def allowed_levels(role: str) -> set:
    """Множество уровней sensitivity, доступных роли."""
    return set(RBAC.get(role, RBAC["external_partner"])["levels"])


def apply_rbac(rows, role: str):
    """Разбивает строки выдачи на видимые/скрытые по оси sensitivity роли.

    Возвращает (visible, hidden_count). Строки без sensitivity считаются
    public (публичный дефолт, не отсекаются).
    """
    levels = allowed_levels(role)
    visible, hidden = [], 0
    for r in rows or []:
        sens = (r.get("sensitivity") if isinstance(r, dict) else None) or "public"
        if sens in levels:
            visible.append(r)
        else:
            hidden += 1
    return visible, hidden


# =============================================================================
# Поиск: композер этапа 7 с фолбэком на эталонные Cypher-запросы графа
# =============================================================================

def run_search(query_text: str, filters=None, drv=None, role="researcher"):
    """Единая точка поиска. Если доступен композер этапа 7 (src.search.search),
    делегирует ему; иначе — фолбэк-маршрутизация на три эталонных запроса графа.

    Возвращает dict: {answer, facts, source, hidden_count}. Никогда не бросает
    (устойчивость к холодному старту: при недоступности графа → пустая выдача).

    Композер этапа 7 сам применяет RBAC и коннектится к Neo4j; его возврат
    {intent, answer_md, facts, docs, hidden_count} нормализуется к общему контракту.
    """
    filters = filters or {}
    if _search_fn is not None:
        try:  # pragma: no cover — путь этапа 7
            res = _search_fn(query_text, role=role, filters=filters)
            if isinstance(res, dict):
                res.setdefault("query", query_text)
                return {
                    "answer": res.get("answer_md") or res.get("answer") or "",
                    "facts": res.get("facts", []),
                    "docs": res.get("docs", []),
                    "hidden_count": res.get("hidden_count", 0),
                    "experts": res.get("experts", []),
                    "source": "composer",
                    "raw": res,  # исходный результат search() — вход для exports.*
                }
        except Exception:  # noqa: BLE001 — деградируем на фолбэк
            pass

    # --- Фолбэк: маршрутизация по ключевым словам на эталонные запросы графа ---
    facts = []
    note = "graph-fallback"
    if drv is not None:
        q = (query_text or "").lower()
        try:
            if "обессол" in q or "сульфат" in q or "сухой остаток" in q:
                facts = graph.q_desalination(drv, max_sulfate=300.0)
            elif "католит" in q or "электроэкстракц" in q or "циркуляц" in q:
                facts = graph.q_catholyte(drv)
            elif ("au" in q or "золот" in q or "мпг" in q or "серебр" in q
                  or "штейн" in q or "шлак" in q):
                facts = graph.q_pgm(drv, years=5)
            else:
                facts = graph.q_desalination(drv, max_sulfate=300.0)
        except Exception:  # noqa: BLE001
            note = "graph-unavailable"
            facts = []
    # Фолбэк сам применяет RBAC по sensitivity (композер этапа 7 недоступен).
    facts, hidden = apply_rbac(facts, role)
    answer = (f"Найдено фактов: {len(facts)}. "
              f"Композер этапа 7 недоступен — показаны факты эталонного запроса графа.")
    raw = {"query": query_text, "answer_md": answer, "facts": facts,
           "docs": [], "experts": [], "hidden_count": hidden}
    return {"answer": answer, "facts": facts, "docs": [],
            "hidden_count": hidden, "experts": [], "source": note, "raw": raw}


def run_literature_review(query_text: str, filters=None, drv=None,
                          role="researcher") -> str:
    """Литобзор (Методы/Режимы/Противоречия/Пробелы). Делегирует этапу 7,
    иначе строит минимальный markdown из фактов фолбэк-поиска."""
    filters = filters or {}
    if _litreview_fn is not None:
        try:  # pragma: no cover
            md = _litreview_fn(query_text, role=role)
            if isinstance(md, str) and md.strip():
                return md
        except Exception:  # noqa: BLE001
            pass
    res = run_search(query_text, filters=filters, drv=drv, role=role)
    facts = res.get("facts", [])
    lines = [f"# Литобзор\n", f"**Запрос:** {query_text}\n",
             "## Методы\n", f"- Источников с фактами: {len(facts)}\n",
             "## Режимы\n"]
    for f in facts[:20]:
        lines.append(
            f"- {f.get('material') or f.get('process') or ''}: "
            f"{f.get('metric') or ''} = {f.get('value_low')}"
            f"{'–' + str(f.get('value_high')) if f.get('value_high') not in (None, f.get('value_low')) else ''} "
            f"{f.get('unit') or ''} (док {f.get('doc_id')}, {f.get('year')})")
    lines += ["\n## Противоречия\n", "- (см. вкладку «Противоречия»)\n",
              "## Пробелы\n", "- (комбинации материал×процесс×условие без источников)\n"]
    return "\n".join(lines)


# =============================================================================
# Подграф ответа → pyvis → HTML-строка (чистая функция — тестируется)
# =============================================================================

def _subgraph_from_facts(facts):
    """Собирает синтетический подграф (nodes, edges) из строк фактов выдачи.

    Цепочка ТЗ (визуализация графа): материал → процесс → оборудование →
    результат. Узлы: Document / Parameter / Material / Process / Equipment /
    Phase. Рёбра типизированы; противоречивые факты помечаются CONTRADICTS
    (подсветка цветом на слое визуализации).

    Устойчиво к отсутствию полей (facts могут быть неполными).
    """
    nodes = {}   # node_id -> {"label", "type"}
    edges = []   # (src_id, dst_id, edge_type)

    def add(node_id, label, ntype):
        if node_id and node_id not in nodes:
            nodes[node_id] = {"label": str(label), "type": ntype}

    for i, f in enumerate(facts or []):
        doc = f.get("doc_id")
        material = f.get("material")
        process = f.get("process")
        equipment = f.get("equipment")
        phase = f.get("phase")
        metric = f.get("metric") or "параметр"
        value = f.get("value_low")
        unit = f.get("unit") or ""
        pid = f"P{i}"

        if doc:
            add(f"D:{doc}", f"doc {doc}", "Document")
        # Параметр = «результат» в цепочке материал→процесс→оборудование→результат.
        add(pid, f"{metric}={value}{unit}", "Parameter")

        mid = f"M:{material}" if material else None
        prid = f"PR:{process}" if process else None
        eqid = f"EQ:{equipment}" if equipment else None
        if material:
            add(mid, material, "Material")
        if process:
            add(prid, process, "Process")
        if equipment:
            add(eqid, equipment, "Equipment")

        # Цепочка: материал → процесс → оборудование → результат(Parameter).
        if mid and prid:
            edges.append((mid, prid, "USES_MATERIAL"))
        if prid and eqid:
            edges.append((prid, eqid, "OPERATES_AT_CONDITION"))
        # результат крепим к последнему присутствующему звену цепи.
        chain_tail = eqid or prid or mid
        if chain_tail:
            edges.append((chain_tail, pid, "PRODUCES_OUTPUT"))
        # Обратная связь параметр→сущность (совместимость: MEASURES остаётся).
        ent = mid or prid or eqid
        if ent:
            edges.append((pid, ent, "MEASURES"))

        if phase:
            add(f"F:{phase}", phase, "Phase")
            edges.append((pid, f"F:{phase}", "MEASURED_IN"))
        if doc:
            edges.append((f"D:{doc}", pid, "HAS_PARAM"))
            edges.append((pid, f"D:{doc}", "DESCRIBED_IN"))

        # Подсветка противоречий: факт помечен contradicts=True/список → ребро CONTRADICTS.
        contra = f.get("contradicts") or f.get("contradicted_by")
        if contra:
            targets = contra if isinstance(contra, (list, tuple, set)) else [contra]
            for t in targets:
                tid = f"D:{t}"
                add(tid, f"doc {t}", "Document")
                edges.append((pid, tid, "CONTRADICTS"))
    return nodes, edges


def _result_doc_ids(res, limit=40):
    """doc_id выдачи: из res['docs'] и из фактов (для запроса реального подграфа)."""
    ids = []
    for d in (res.get("docs") or []):
        did = d.get("doc_id") if isinstance(d, dict) else d
        if did and did not in ids:
            ids.append(did)
    for f in (res.get("facts") or []):
        did = f.get("doc_id")
        if did and did not in ids:
            ids.append(did)
    return ids[:limit]


def build_subgraph_html(facts, height="520px", drv=None, doc_ids=None) -> str:
    """Строит pyvis-подграф ответа и возвращает HTML-СТРОКУ (для st.components.v1.html).

    Основной источник — РЕАЛЬНАЯ топология Neo4j (graph.answer_subgraph по doc_ids
    выдачи): цепочка материал→процесс→оборудование→результат + CONTRADICTS + эксперты.
    Fallback (нет drv/doc_ids/пусто/ошибка) — реконструкция из плоских фактов.
    Типизированные рёбра/узлы — цветом. Никогда не бросает.
    """
    nodes = edges = None
    if drv is not None and doc_ids:
        try:
            from src import graph as _graph
            nodes, edges = _graph.answer_subgraph(drv, doc_ids)
        except Exception:  # noqa: BLE001 — граф недоступен → fallback
            nodes = edges = None
    if not nodes:
        nodes, edges = _subgraph_from_facts(facts)

    try:
        from pyvis.network import Network
    except Exception:  # noqa: BLE001 — pyvis нет → деградируем на HTML-таблицу
        return _fallback_html(nodes, edges, height)

    try:
        net = Network(height=height, width="100%", directed=True,
                      bgcolor="#0a192f", font_color="#ccd6f6", notebook=False)
        net.toggle_physics(True)
        for nid, meta in nodes.items():
            net.add_node(
                nid, label=meta["label"], title=f"{meta['type']}: {meta['label']}",
                color=NODE_COLORS.get(meta["type"], "#8892b0"),
                shape="dot", size=18,
            )
        seen = set()
        for src, dst, etype in edges:
            if src in nodes and dst in nodes and (src, dst, etype) not in seen:
                seen.add((src, dst, etype))
                net.add_edge(src, dst, color=EDGE_COLORS.get(etype, "#8892b0"),
                             title=etype)
        # generate_html не пишет на диск и не требует сети.
        return net.generate_html(notebook=False)
    except Exception:  # noqa: BLE001
        return _fallback_html(nodes, edges, height)


def _fallback_html(nodes, edges, height) -> str:
    """HTML-заглушка подграфа (без pyvis): список узлов и типизированных рёбер."""
    rows = []
    for nid, meta in nodes.items():
        color = NODE_COLORS.get(meta["type"], "#8892b0")
        rows.append(
            f'<li><span style="color:{color}">&#9679;</span> '
            f'<b>{html.escape(meta["type"])}</b>: {html.escape(meta["label"])}</li>')
    erows = []
    for src, dst, etype in edges:
        color = EDGE_COLORS.get(etype, "#8892b0")
        erows.append(
            f'<li><span style="color:{color}">{html.escape(etype)}</span>: '
            f'{html.escape(str(src))} &rarr; {html.escape(str(dst))}</li>')
    return (
        f'<div style="height:{height};overflow:auto;background:#0a192f;'
        f'color:#ccd6f6;padding:12px;font-family:sans-serif">'
        f'<h4>Подграф ответа ({len(nodes)} узлов, {len(edges)} рёбер)</h4>'
        f'<p>pyvis недоступен — текстовое представление.</p>'
        f'<b>Узлы:</b><ul>{"".join(rows)}</ul>'
        f'<b>Рёбра:</b><ul>{"".join(erows)}</ul></div>')


# =============================================================================
# Противоречия: рёбра CONTRADICTS / VALIDATED_BY (Cypher)
# =============================================================================

def fetch_contradictions(drv, kind=None):
    """Возвращает список рёбер CONTRADICTS / VALIDATED_BY.

    kind (опц.) фильтрует по атрибуту ребра e.kind (например ru_vs_world).
    Устойчиво: при ошибке графа → пустой список (вызывающий покажет заглушку).
    """
    cy = """
    MATCH (a)-[e:CONTRADICTS|VALIDATED_BY]->(b)
    WHERE $kind IS NULL OR e.kind = $kind
    RETURN type(e) AS rel, e.kind AS kind,
           coalesce(a.canon, a.name, a.doc_id, '?') AS src,
           coalesce(b.canon, b.name, b.doc_id, '?') AS dst,
           a.doc_id AS src_id, b.doc_id AS dst_id,
           e.entity AS entity, e.metric AS metric, e.phase AS phase, e.unit AS unit,
           e.val_a AS val_a, e.val_b AS val_b,
           e.sources AS sources
    LIMIT 500
    """
    try:
        with drv.session() as s:
            return [dict(r) for r in s.run(cy, kind=kind)]
    except Exception:  # noqa: BLE001
        return []


# =============================================================================
# Экспорт результата поиска (ТЗ: PDF / JSON-LD / Markdown). Чистые хелперы —
# зовут exports.* и тестируются без Streamlit.
# =============================================================================

def export_payloads(result: dict):
    """Из result (dict от search()) собирает три артефакта экспорта:
    (markdown:str, jsonld_str:str, pdf:bytes) через exports.*.

    Никогда не бросает — при сбое любого форматтера отдаёт безопасную заглушку,
    чтобы download-кнопки в UI всегда существовали."""
    result = result or {}
    try:
        md = exports.to_markdown(result)
    except Exception:  # noqa: BLE001
        md = "# Отчёт\n(экспорт недоступен)\n"
    try:
        jsonld = json.dumps(exports.to_jsonld(result), ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        jsonld = "{}"
    try:
        pdf = exports.to_pdf(result)
    except Exception:  # noqa: BLE001
        pdf = b"%PDF-1.4\n"
    return md, jsonld, pdf


def render_export_buttons(st, role, query, result):
    """Три st.download_button под результатами поиска (Markdown/JSON-LD/PDF).
    Клик логируется в аудит (ТЗ: аудит export) через on_click."""
    md, jsonld, pdf = export_payloads(result)
    n = len((result or {}).get("facts") or [])
    cols = st.columns(3)
    with cols[0]:
        st.download_button(
            "Экспорт .md", data=md, file_name="report.md", mime="text/markdown",
            on_click=log_event,
            args=(role, "export", {"query": query, "fmt": "markdown", "n_results": n}))
    with cols[1]:
        st.download_button(
            "Экспорт JSON-LD", data=jsonld, file_name="report.jsonld",
            mime="application/ld+json",
            on_click=log_event,
            args=(role, "export", {"query": query, "fmt": "jsonld", "n_results": n}))
    with cols[2]:
        st.download_button(
            "Экспорт PDF", data=pdf, file_name="report.pdf",
            mime="application/pdf",
            on_click=log_event,
            args=(role, "export", {"query": query, "fmt": "pdf", "n_results": n}))


# =============================================================================
# Дашборд руководителя (ТЗ): KPI, покрытие, зоны риска, активность, эксперты,
# сравнение технологий. Сбор данных — чистая функция (тестируется без Streamlit).
# =============================================================================

def build_dashboard_data(drv) -> dict:
    """Собирает данные дашборда через src.dashboard.*. Устойчиво: при сбое любой
    секции → пустой дефолт (UI показывает «нет данных», не падает)."""
    def safe(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default
    return {
        "summary": safe(lambda: dashboard.summary_metrics(drv), {}),
        "by_domain": safe(lambda: dashboard.coverage_by_domain(drv), []),
        "by_year": safe(lambda: dashboard.coverage_by_year(drv), []),
        "by_geo": safe(lambda: dashboard.coverage_by_geo(drv), []),
        "risk": safe(lambda: dashboard.risk_zones(drv), {}),
        "activity": safe(lambda: dashboard.activity(drv), []),
        "experts": safe(lambda: dashboard.expert_coverage(drv), []),
    }


def render_dashboard_tab(st, role, drv):
    """Вкладка «Дашборд» (роли manager/admin — здесь project_lead/admin): KPI,
    покрытие по направлениям/годам/гео, зоны риска, активность, топ-эксперты,
    сравнение технологий."""
    data = build_dashboard_data(drv)
    log_event(role, "view", {"view": "dashboard"})

    s = data["summary"] or {}
    st.subheader("KPI")
    m = st.columns(4)
    m[0].metric("Документы", s.get("docs", 0))
    m[1].metric("Факты", s.get("facts", 0))
    m[2].metric("Эксперты", s.get("experts", 0))
    m[3].metric("Противоречия", s.get("contradictions", 0))
    m2 = st.columns(4)
    m2[0].metric("Направления", s.get("domains", 0))
    m2[1].metric("RU / WORLD", f"{s.get('ru', 0)} / {s.get('world', 0)}")
    m2[2].metric("Доля RU", s.get("ru_share"))
    m2[3].metric("Покрытие фактами", s.get("fact_coverage"))

    st.subheader("Покрытие по направлениям")
    st.dataframe(data["by_domain"], use_container_width=True)
    c = st.columns(2)
    with c[0]:
        st.caption("По годам")
        st.dataframe(data["by_year"], use_container_width=True)
    with c[1]:
        st.caption("По географии")
        st.dataframe(data["by_geo"], use_container_width=True)

    st.subheader("Зоны риска")
    risk = data["risk"] or {}
    st.caption("Тонкое покрытие (мало источников)")
    st.dataframe(risk.get("low_sources", []), use_container_width=True)
    st.caption("Противоречия")
    st.dataframe(risk.get("contradictions", []), use_container_width=True)
    rc = st.columns(2)
    rc[0].caption("Только отечественная практика")
    rc[0].write(risk.get("only_ru", []))
    rc[1].caption("Только мировая практика")
    rc[1].write(risk.get("only_world", []))

    st.subheader("Активность")
    st.dataframe(data["activity"], use_container_width=True)

    st.subheader("Топ эксперты")
    st.dataframe(data["experts"], use_container_width=True)

    render_compare_section(st, drv)


def render_compare_section(st, drv):
    """Сравнение технологий (ТЗ): multiselect процессов → compare_technologies →
    st.dataframe таблица сравнения."""
    st.subheader("Сравнение технологий")
    processes = []
    if drv is not None:
        try:
            with drv.session() as se:
                processes = [r["c"] for r in se.run(
                    "MATCH (p:Process) RETURN DISTINCT p.canon AS c "
                    "ORDER BY c LIMIT 200")]
        except Exception:  # noqa: BLE001
            pass
    chosen = st.multiselect("Процессы для сравнения", processes)
    if chosen:
        cmp = dashboard.compare_technologies(drv, chosen)
        st.dataframe(cmp.get("rows", []), use_container_width=True)
        if cmp.get("meta", {}).get("note"):
            st.caption(cmp["meta"]["note"])


# =============================================================================
# Ручная правка графа (ТЗ): форма правки факта + история (провенанс).
# =============================================================================

def render_curation_tab(st, role, drv):
    """Вкладка «Правка» (роли expert/admin — здесь project_lead/admin): форма
    выбора факта (doc_id/canon/metric) + новое значение + комментарий →
    curation.edit_fact; ниже — edit_history (кто/когда/что)."""
    st.subheader("Правка факта")
    doc_id = st.text_input("doc_id", key="cur_doc")
    canon = st.text_input("Сущность (canon)", key="cur_canon")
    metric = st.text_input("Метрика (metric)", key="cur_metric")
    new_value = st.number_input("Новое значение", value=0.0, key="cur_val")
    comment = st.text_input("Комментарий", key="cur_comment")
    if st.button("Сохранить правку"):
        key = {"doc_id": doc_id, "canon": canon, "metric": metric}
        try:
            res = curation.edit_fact(drv, key, float(new_value), editor=role,
                                     comment=comment)
            log_event(role, "edit", {"key": key, "new_value": new_value,
                                     "ok": res.get("ok")})
            if res.get("ok"):
                st.success(f"Обновлено узлов: {res.get('affected', 1)}")
            else:
                st.warning("Факт не найден по указанному ключу.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Ошибка правки: {e}")

    st.subheader("История правок (провенанс)")
    try:
        hist = curation.edit_history(drv, limit=50)
    except Exception:  # noqa: BLE001
        hist = []
    if hist:
        st.dataframe(hist, use_container_width=True)
    else:
        st.info("Ручных правок пока нет.")


# =============================================================================
# Подписки / уведомления (ТЗ): subscribe/unsubscribe + «Проверить новое».
# =============================================================================

def render_subscriptions_tab(st, role, drv):
    """Вкладка «Подписки»: подписка/отписка на запрос-тему; «Проверить новое» →
    notify.check → new_count/примеры; mark_seen после показа."""
    user = role  # в этой демо-роль = идентификатор пользователя
    new_query = st.text_input("Тема подписки (запрос)", key="sub_query")
    cols = st.columns(2)
    if cols[0].button("Подписаться"):
        if new_query:
            notify.subscribe(user, new_query)
            log_event(role, "subscribe", {"query": new_query})
            st.success("Подписка добавлена.")
    if cols[1].button("Отписаться"):
        if new_query and notify.unsubscribe(user, new_query):
            log_event(role, "unsubscribe", {"query": new_query})
            st.success("Отписка выполнена.")

    st.subheader("Мои подписки")
    subs = notify.list_subscriptions(user)
    st.dataframe(subs, use_container_width=True)

    if st.button("Проверить новое"):
        results = notify.check(user, driver=drv)
        log_event(role, "view", {"view": "notify_check",
                                 "n_results": sum(r.get("new_count", 0) for r in results)})
        for r in results:
            st.markdown(f"**{r.get('query')}** — новых: {r.get('new_count', 0)}")
            for item in (r.get("sample") or [])[:5]:
                st.caption(
                    f"· {item.get('canon') or ''} {item.get('metric') or ''} "
                    f"(док {item.get('doc_id')}, {item.get('when') or ''})")
            notify.mark_seen(user, r.get("query"))


# =============================================================================
# Аудит: просмотр журнала (ТЗ) + экспорт журнала.
# =============================================================================

def read_audit(path=None, limit: int = 500) -> list:
    """Читает последние строки artifacts/audit.jsonl как список dict."""
    path = AUDIT_PATH if path is None else path
    try:
        lines = open(path, encoding="utf-8").read().strip().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def render_audit_tab(st, role, drv):
    """Вкладка «Аудит» (роли admin): журнал query/view/export + экспорт журнала."""
    rows = read_audit()
    st.subheader("Журнал аудита")
    st.dataframe(rows, use_container_width=True)
    st.download_button(
        "Экспорт журнала (JSONL)",
        data="\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        file_name="audit.jsonl", mime="application/x-ndjson")


# =============================================================================
# Streamlit-рендер (НЕ вызывается при импорте — только из main())
# =============================================================================

def _get_driver():
    """Пытается поднять драйвер Neo4j. Возвращает (drv|None, err|None).

    Короткий retry: UI не должен висеть 30с на холодном старте.
    """
    try:
        return graph.driver(retry_seconds=3.0), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def render_header(st):
    """Шапка: селектор роли + строка поиска + 3 чипа-примера."""
    st.title("Научный клубок — поиск по институциональной памяти")
    cols = st.columns([2, 5])
    with cols[0]:
        role = st.selectbox("Роль", ROLES,
                            format_func=lambda r: f"{ROLE_LABELS[r]} ({r})")
    with cols[1]:
        query = st.text_input("Запрос",
                              value=st.session_state.get("query", ""),
                              placeholder="Введите вопрос на естественном языке…")

    st.caption("Примеры запросов:")
    chip_cols = st.columns(len(EXAMPLE_CHIPS))
    for i, (label, text) in enumerate(EXAMPLE_CHIPS):
        with chip_cols[i]:
            if st.button(label, key=f"chip_{i}"):
                # Клик по чипу → заполняет строку и запускает поиск.
                st.session_state["query"] = text
                st.session_state["run"] = True
                query = text
    return role, query


def render_filters(st, drv):
    """Фильтры выдачи (5/5 ТЗ): год, география, материал, процесс, confidence.

    Множества значений подтягиваются из графа; при недоступности — пустые
    multiselect (UI не падает).
    """
    years, geos, materials, processes = [], [], [], []
    if drv is not None:
        try:
            with drv.session() as s:
                years = [r["y"] for r in s.run(
                    "MATCH (d:Document) WHERE d.year IS NOT NULL "
                    "RETURN DISTINCT d.year AS y ORDER BY y DESC")]
                geos = [r["g"] for r in s.run(
                    "MATCH (d:Document) WHERE d.geo IS NOT NULL "
                    "RETURN DISTINCT d.geo AS g ORDER BY g")]
                materials = [r["c"] for r in s.run(
                    "MATCH (m:Material) RETURN DISTINCT m.canon AS c ORDER BY c LIMIT 200")]
                processes = [r["c"] for r in s.run(
                    "MATCH (p:Process) RETURN DISTINCT p.canon AS c ORDER BY c LIMIT 200")]
        except Exception:  # noqa: BLE001
            pass
    f = {}
    with st.expander("Фильтры", expanded=False):
        c = st.columns(5)
        f["year"] = c[0].multiselect("Год", years)
        f["geo"] = c[1].multiselect("География", geos)
        f["material"] = c[2].multiselect("Материал", materials)
        f["process"] = c[3].multiselect("Процесс", processes)
        f["confidence"] = c[4].multiselect("Достоверность", CONFIDENCE_LEVELS)
        # (4) Слайдер минимальной достоверности (0..1) → filters.min_confidence.
        f["min_confidence"] = st.slider(
            "Мин. достоверность", min_value=0.0, max_value=1.0,
            value=0.0, step=0.05,
            help="Скрыть факты с confidence ниже порога")
    return f


def render_fact_card(st, f):
    """Карточка факта: значение крупно, цитата курсивом, документ+год, бейдж source."""
    val = f.get("value_low")
    vh = f.get("value_high")
    unit = f.get("unit") or ""
    value_str = f"{val}" if vh in (None, val) else f"{val}–{vh}"
    st.markdown(f"### {value_str} {unit}")
    if f.get("metric"):
        st.caption(f.get("metric"))
    quote = f.get("quote")
    if quote:
        st.markdown(f"> *{quote}*")
    doc = f.get("doc_id")
    year = f.get("year")
    src_badge = f.get("source") or "graph"
    st.caption(f"Документ {doc} · {year or 'год не определён'} · `{src_badge}`")

    # (2) Верификация ТЗ: уровень достоверности словом + дата актуализации + источник.
    conf_word = confidence_word(f.get("confidence"))
    extracted = f.get("extracted_at") or "—"
    source = f.get("source") or "—"
    st.caption(
        f"Достоверность: **{conf_word}** · "
        f"Актуализировано: {extracted} · "
        f"Источник: {source}")
    st.divider()


def render_search(st, role, query, drv):
    """Вкладка «Поиск». Чистая от Streamlit-глобалей: st передаётся аргументом,
    что упрощает тестовую подмену/мок. Пишет аудит query/view/export.
    """
    filters = render_filters(st, drv)
    # Сохраняем собранные фильтры для переиспользования вкладкой «Граф».
    try:
        st.session_state["filters"] = filters
    except Exception:  # noqa: BLE001 — st без session_state (тест/мок)
        pass
    if not query:
        st.info("Введите запрос или выберите пример выше.")
        return

    # run_search применяет RBAC внутри (композер этапа 7 или фолбэк) и
    # возвращает уже отфильтрованные факты + счётчик скрытого.
    res = run_search(query, filters=filters, drv=drv, role=role)
    visible = res.get("facts", [])
    hidden = res.get("hidden_count", 0)

    log_event(role, "query",
              {"query": query, "n_results": len(visible), "filters": filters})
    _obs_log(_LOG, "search", role=role, query=query, n_results=len(visible))

    # (1) grounded-ответ композера + плашка источника.
    st.markdown(f"**Ответ:** {res.get('answer', '')}")
    st.caption(f"Источник: {res.get('source')}")

    # Аудит просмотра результатов (ТЗ: view).
    log_event(role, "view", {"query": query, "view": "results",
                             "n_results": len(visible)})

    # (2) карточки фактов.
    for f in visible:
        render_fact_card(st, f)

    # Экспорт результата: Markdown / JSON-LD / PDF (ТЗ: PDF/JSON-LD; аудит export).
    if visible or res.get("raw"):
        render_export_buttons(st, role, query, res.get("raw") or res)

    # Блок «Скрыто вашей ролью: N».
    if hidden:
        st.warning(f"Скрыто вашей ролью: {hidden}")

    # (3) эксперты/лаборатории выдачи (носители компетенций по теме).
    render_experts(st, res.get("experts", []))

    # (4) Литобзор.
    if st.button("Литобзор"):
        md = run_literature_review(query, filters=filters, drv=drv, role=role)
        log_event(role, "view", {"query": query, "view": "literature_review"})
        st.markdown(md)
        # (5) Скачать .md — экспорт логируется в аудит через on_click.
        st.download_button(
            "Скачать .md", data=md,
            file_name="literature_review.md", mime="text/markdown",
            on_click=log_event, args=(role, "export",
                                      {"query": query, "n_results": len(visible)}))


def render_experts(st, experts):
    """Показ экспертов/лабораторий выдачи (связанные носители компетенций ТЗ)."""
    if not experts:
        return
    st.subheader("Эксперты и лаборатории по теме")
    for e in experts[:15]:
        if isinstance(e, dict):
            name = e.get("name") or e.get("lab") or e.get("facility") or "—"
            ndocs = e.get("docs")
            st.markdown(f"- **{name}**" + (f" · источников: {ndocs}" if ndocs else ""))
        else:
            st.markdown(f"- **{e}**")


def render_graph_tab(st, query, drv, role="researcher", filters=None):
    """Вкладка «Граф»: подграф-цепочка материал→процесс→оборудование→результат
    через pyvis; подсветка CONTRADICTS цветом; показ экспертов/лабораторий."""
    import streamlit.components.v1 as components
    if not query:
        st.info("Сначала выполните поиск на вкладке «Поиск».")
        return
    res = run_search(query, filters=filters, drv=drv, role=role)
    html_str = build_subgraph_html(res.get("facts", []), drv=drv,
                                   doc_ids=_result_doc_ids(res))
    components.html(html_str, height=560, scrolling=True)
    render_experts(st, res.get("experts", []))


def render_contradictions_tab(st, role, drv):
    """Вкладка «Противоречия»: таблица CONTRADICTS/VALIDATED_BY + фильтр kind."""
    kinds = ["(все)", "ru_vs_world"]
    kind = st.selectbox("Тип связи (kind)", kinds)
    kind_arg = None if kind == "(все)" else kind
    rows = fetch_contradictions(drv, kind=kind_arg)
    log_event(role, "view", {"view": "contradictions", "kind": kind_arg,
                             "n_results": len(rows)})
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("Рёбер CONTRADICTS/VALIDATED_BY не найдено (или база загружается).")


def main():  # pragma: no cover — исполняется только под Streamlit-сервером
    import streamlit as st
    st.set_page_config(page_title="Научный клубок", layout="wide")

    if "query" not in st.session_state:
        st.session_state["query"] = ""

    role, query = render_header(st)
    if query:
        st.session_state["query"] = query

    drv, err = _get_driver()
    if err is not None:
        st.info("База знаний загружается… (Neo4j недоступен, повторите позже)")
        st.caption(f"Диагностика: {err}")

    # Базовые вкладки + аддитивные (роль-гейт по ТЗ).
    names = ["Поиск", "Граф", "Противоречия"]
    is_manager = role in ("project_lead", "admin")   # manager/admin
    is_expert = role in ("project_lead", "admin")     # expert/admin (правка графа)
    if is_manager:
        names.append("Дашборд")
    if is_expert:
        names.append("Правка")
    names.append("Подписки")
    if RBAC.get(role, {}).get("admin_ops"):
        names.append("Аудит")

    tabs = st.tabs(names)
    tab = dict(zip(names, tabs))
    with tab["Поиск"]:
        render_search(st, role, st.session_state["query"], drv)
    with tab["Граф"]:
        render_graph_tab(st, st.session_state["query"], drv, role=role,
                         filters=st.session_state.get("filters"))
    with tab["Противоречия"]:
        render_contradictions_tab(st, role, drv)
    if "Дашборд" in tab:
        with tab["Дашборд"]:
            render_dashboard_tab(st, role, drv)
    if "Правка" in tab:
        with tab["Правка"]:
            render_curation_tab(st, role, drv)
    with tab["Подписки"]:
        render_subscriptions_tab(st, role, drv)
    if "Аудит" in tab:
        with tab["Аудит"]:
            render_audit_tab(st, role, drv)

    if drv is not None:
        try:
            drv.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
