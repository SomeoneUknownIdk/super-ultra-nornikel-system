"""ЭТАП 7 — гибридный поиск (числовая + семантическая дорожки) и композер ответа.

search(query, role, filters) -> dict
    Разбор запроса (grammar/gazetteer/llm) → интент; числовая дорожка по графу
    (Parameter, RANGE-пересечение) + готовые эталонные запросы графа; семантическая
    дорожка (src.embed.Semantic, опционально) — докидывает доки по смыслу; слияние
    по doc_id (порядок задаёт числовая, семантика добавляет хвост); RBAC-скрытие
    internal-документов для внешних партнёров; экстрактивный композер ответа.

literature_review(query) -> markdown
    Группировка фактов выдачи по (canon/process, unit): разделы Методы/Режимы/Пробелы.

Изоляция: модуль импортирует только готовые src.* (config/grammar/gazetteer/graph/llm)
и (опционально) src.embed. Ничего не изменяет в чужих модулях. Все внешние сбои
(Neo4j недоступен, LLM выключен, embed отсутствует) деградируют мягко.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from urllib.parse import quote as _q

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import DOCS_META, DOCS_TEXT, nfc, unit_ru
from src import grammar, gazetteer, graph
from src.obs import get_logger, log_event

try:  # LLM опционален (может быть выключен ключами) — фолбэк на rule-based.
    from src import llm as _llm
except Exception:  # pragma: no cover
    _llm = None


# ─────────────────────────────────────────────────────────────────────────────
# Метаданные документов (sensitivity/year/src) — загрузка в dict один раз.
# ─────────────────────────────────────────────────────────────────────────────
_META_CACHE = None


def _load_meta(path=DOCS_META) -> dict:
    """docs.meta.jsonl → {doc_id: {src, year, sensitivity, cat, geo, lang, ...}}."""
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE
    meta = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                did = d.get("doc_id")
                if did:
                    meta[nfc(did)] = d
    except FileNotFoundError:
        meta = {}
    _META_CACHE = meta
    return meta


def _load_previews(doc_ids, path=DOCS_TEXT, n=180) -> dict:
    """{doc_id: preview} — по одному проходу текстового корпуса, только для нужных.

    docs.text.jsonl большой (сотни МБ) — не грузим целиком: один линейный скан,
    собираем превью (первые n символов) лишь для запрошенных doc_id. При отсутствии
    файла возвращаем {} (мягкая деградация).
    """
    want = {nfc(str(d)) for d in doc_ids if d}
    out = {}
    if not want:
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if len(out) >= len(want):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                did = nfc(str(d.get("doc_id") or ""))
                if did and did in want and did not in out:
                    txt = " ".join((d.get("text") or "").split())
                    out[did] = (txt[: n - 1].rstrip() + "…") if len(txt) > n else txt
    except FileNotFoundError:
        return out
    return out


import re

_GENERIC = ("доклад", "презентация", "обзор", "статья", "отчет", "отчёт",
            "тэр", "материалы", "тезисы", "реферат", "протокол")

# ФИО в форме «Фамилия И.О.» / «Фамилия И.» / «Фамилия ИО» (кириллица):
# фамилия с заглавной + 1–2 инициала (точки опциональны).
_NAME_INITIALS_RE = re.compile(
    r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"      # Фамилия (возм. дефисная)
    r"\s+[А-ЯЁ]\.?(?![а-яё])"                 # первый инициал (не начало слова)
    r"\s*(?:[А-ЯЁ]\.?(?![а-яё]))?"            # второй инициал (опц.)
    r"(?![А-ЯЁ])"                             # не акроним ('НДС' — не инициалы)
)
# Полное имя «Фамилия Имя» — РОВНО две заглавные лексемы (анкор до конца строки),
# чтобы длинный заголовок ('Презентация Измерение НДС …') не сошёл за имя.
_NAME_FULL_RE = re.compile(
    r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    r"\s+[А-ЯЁ][а-яё]+$"
)
# Одиночная фамилия (заглавная кириллическая лексема).
_SURNAME_RE = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?$")


def _parse_author(chunk: str):
    """Из одной '_'-лексемы имени файла извлечь ФИО «Фамилия И.О.» (или фамилию).

    Порядок: ФИО с инициалами → полное имя «Фамилия Имя» → одиночная фамилия.
    Заголовки/темы (нет имени) → None. НЕ возвращает лексему целиком, если это не имя.
    """
    chunk = re.sub(r"\s+", " ", (chunk or "").strip())
    if not chunk:
        return None
    m = _NAME_INITIALS_RE.match(chunk)
    if m:
        return m.group(0).strip().rstrip(".")
    m = _NAME_FULL_RE.match(chunk)
    if m:
        return m.group(0).strip()
    if _SURNAME_RE.match(chunk):
        return chunk
    return None


def _authors_from_src(src: str):
    """Извлечь автора(ов) в форме «Фамилия И.О.» из имени файла / названия.

    Явного поля author в метаданных нет — парсим ФИО-паттерн (Фамилия И.О.),
    а НЕ отдаём имя файла целиком. Стратегия:
      1) ФИО с инициалами ('Клименко И.В.') ищем ГДЕ УГОДНО в строке — реальные
         имена часто в середине ('Статья - Клименко И.В. (ЛГМ)'). Guard (?![а-яё])
         не даёт спутать инициал с началом слова ('Презентация Измерение').
      2) Если инициалов нет — берём ведущую значащую лексему (после отброса
         родового слова) и пробуем 'Фамилия Имя' / одиночную фамилию.
    Заголовок без ФИО → [] (документ автором не считаем).
    """
    if not src:
        return []
    name = os.path.basename(str(src))
    stem = name.rsplit(".", 1)[0] if "." in name else name

    # 1) ФИО с инициалами — где угодно в строке (нормализуем, дедуп по порядку).
    out = []
    for m in _NAME_INITIALS_RE.finditer(stem):
        cand = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(".")
        if cand and cand not in out:
            out.append(cand)
    if out:
        return out

    # 2) Фолбэк: ведущая лексема имени файла ('Петров_тема' → 'Петров',
    #    'Доклад_Реле Вантов' → 'Реле Вантов').
    parts = [p.strip() for p in stem.split("_") if p.strip()]
    while parts and parts[0].lower() in _GENERIC:
        parts.pop(0)
    if not parts:
        return []
    author = _parse_author(parts[0])
    return [author] if author else []


# ─────────────────────────────────────────────────────────────────────────────
# Газетир: Matcher строится 0.7-2.6с (spaCy nlp + словари) — кэшируем на модуле,
# иначе КАЖДЫЙ search() платит полную стоимость построения.
# ─────────────────────────────────────────────────────────────────────────────
_MATCHER = None


def _get_matcher():
    """Ленивая глобаль gazetteer.Matcher() — строится один раз на процесс."""
    global _MATCHER
    if _MATCHER is None:
        _MATCHER = gazetteer.Matcher()
    return _MATCHER


# ─────────────────────────────────────────────────────────────────────────────
# Разбор запроса → интент.
# ─────────────────────────────────────────────────────────────────────────────
_EXPERT_HINTS = ("кто", "эксперт", "автор", "специалист")
_LISTING_HINTS = ("все ", "показать", "список", "перечисл", "покажи")


def _rule_intent(query: str, nums) -> str:
    """Rule-based интент (фолбэк при выключенном/ошибочном LLM)."""
    q = (query or "").lower()
    if nums:
        return "numeric"
    if any(h in q for h in _EXPERT_HINTS):
        return "expert"
    if any(h in q for h in _LISTING_HINTS):
        return "listing"
    return "search"


def _is_expert_query(query: str) -> bool:
    """Явный запрос эксперта: 'кто …' + маркер экспертизы (эксперт/автор/специалист).

    Такой запрос должен доминировать над LLM (как числа) — иначе активируется
    неверная дорожка и экспертная агрегация не срабатывает.
    """
    q = (query or "").lower()
    return ("кто" in q) and any(
        h in q for h in ("эксперт", "автор", "специалист", "занимал", "исследов")
    )


def _detect_intent(query: str, nums) -> str:
    """LLM-интент при доступности; иначе/при ошибке — rule-based.

    Числа всегда доминируют: если грамматика нашла числовой факт — 'numeric'
    (даже если LLM сказал иначе), т.к. это активирует числовую дорожку графа.
    Явный «кто эксперт …» тоже доминирует — активирует экспертную дорожку.
    """
    if nums:
        return "numeric"
    if _is_expert_query(query):
        return "expert"
    if _llm is not None:
        try:
            parsed = _llm.parse_query(query)
            intent = (parsed or {}).get("intent")
            if intent:
                return str(intent)
        except Exception:
            pass
    return _rule_intent(query, nums)


# ─────────────────────────────────────────────────────────────────────────────
# Готовые эталонные запросы графа — активируются по ключевым словам.
# ─────────────────────────────────────────────────────────────────────────────
def _graph_shortcuts(drv, query: str, nums, pgm_years: int = 50):
    """Списки фактов от graph.q_desalination/q_catholyte/q_pgm, если запрос узнан.

    Возвращает list[fact-dict] в единой форме (см. _fact_from_graph_row).
    pgm_years — окно свежести для q_pgm (по умолчанию 50 = «все»); если запрос
    содержит временной фильтр «за последние N лет / с YYYY» — сужается вызывающим.
    """
    q = (query or "").lower()
    out = []
    try:
        # Обессоливание / сульфаты (RU + EN: desalination/sulfate).
        if any(t in q for t in ("обессоливан", "сульфат", "desalinat", "sulfate",
                                 "sulphate")):
            max_s = 300.0
            for f in nums:
                if f.get("unit_canon") == "mg_L" and f.get("value_high") is not None:
                    max_s = float(f["value_high"])
                    break
            for r in graph.q_desalination(drv, max_sulfate=max_s):
                out.append(_norm_graph_row(r, kind="desalination"))
        # Католит / электроэкстракция (RU + EN: catholyte/electrowinning).
        if any(t in q for t in ("католит", "электроэкстракц", "catholyte",
                                "electrowinning", "electro-winning")):
            for r in graph.q_catholyte(drv):
                out.append(_norm_graph_row(r, kind="catholyte"))
        # Распределение МПГ по штейн/шлак (RU + EN: distribution + matte/slag).
        if ((("распределени" in q) and (("штейн" in q) or ("шлак" in q)))
                or (("distribution" in q) and (("matte" in q) or ("slag" in q)))):
            for r in graph.q_pgm(drv, years=pgm_years):
                out.append(_norm_graph_row(r, kind="pgm"))
    except Exception:
        pass
    return out


def _norm_graph_row(r: dict, kind: str) -> dict:
    """Строка эталонного запроса → унифицированный факт."""
    canon = r.get("material") or r.get("process") or ""
    return {
        "canon": canon,
        "metric": r.get("metric"),
        "value_low": r.get("value_low"),
        "value_high": r.get("value_high"),
        "unit": r.get("unit"),
        "phase": r.get("phase"),
        "quote": r.get("quote") or "",
        "doc_id": r.get("doc_id"),
        "year": r.get("year"),
        "source": "число",
        "track": kind,
        "ref": True,   # факт из готового эталонного запроса — приоритет в выдаче
    }


# ─────────────────────────────────────────────────────────────────────────────
# Числовая дорожка: Cypher по Parameter (RANGE-пересечение).
# ─────────────────────────────────────────────────────────────────────────────
_NUMERIC_CYPHER = """
MATCH (p:Parameter)
OPTIONAL MATCH (p)-[:MEASURES]->(e)
OPTIONAL MATCH (p)-[:DESCRIBED_IN]->(d:Document)
OPTIONAL MATCH (p)-[:MEASURED_IN]->(ph:Phase)
WITH p, e, d, ph,
     coalesce(p.value_low, p.value_high)  AS lo,
     coalesce(p.value_high, p.value_low)  AS hi
WHERE ($unit   IS NULL OR p.unit_canon = $unit)
  AND ($metric IS NULL OR toLower(coalesce(p.metric,'')) CONTAINS $metric)
  AND ($material IS NULL
       OR toLower(coalesce(e.canon,'')) CONTAINS $material
       OR toLower(coalesce(p.canon,'')) CONTAINS $material)
// пересечение диапазона запроса [$qlo,$qhi] с диапазоном факта [lo,hi]
WITH p, e, d, ph, lo, hi,
     (lo IS NOT NULL AND hi IS NOT NULL
      AND ($qhi IS NULL OR lo <= $qhi)
      AND ($qlo IS NULL OR hi >= $qlo)) AS in_range
// близость середины диапазона факта к целевому значению запроса $target
WITH p, e, d, ph, lo, hi, in_range,
     CASE
       WHEN $target IS NULL THEN NULL
       WHEN lo IS NOT NULL AND hi IS NOT NULL THEN abs((lo + hi) / 2.0 - $target)
       WHEN hi IS NOT NULL THEN abs(hi - $target)
       WHEN lo IS NOT NULL THEN abs(lo - $target)
       ELSE NULL
     END AS dist
RETURN e.canon AS canon, p.metric AS metric,
       p.value_low AS value_low, p.value_high AS value_high,
       p.unit_canon AS unit, ph.canon AS phase, p.quote AS quote,
       d.doc_id AS doc_id, d.year AS year, p.confidence AS confidence,
       in_range AS in_range
// НЕ сортируем по величине: сперва in_range, затем близость |value-target|.
// Строки без dist (нет цели/значения) — в хвост, чтобы LIMIT не срезал целевые.
ORDER BY in_range DESC,
         CASE WHEN dist IS NULL THEN 1 ELSE 0 END ASC,
         dist ASC
LIMIT 50
"""


def _numeric_track(drv, nums):
    """По каждому числовому факту запроса — факты графа, пересекающие диапазон.

    Возвращает list[fact]. Факты в диапазоне идут первыми (in_range=True); если
    строгое пересечение пусто, те же metric/material/unit-факты возвращаются как
    контекст (in_range=False) — дорожка не «схлопывается» в ноль при узком пороге.
    """
    facts = []
    seen = set()
    for f in nums:
        material = (f.get("material") or "").lower() or None
        unit = f.get("unit_canon")
        metric = (f.get("metric") or "").lower() or None
        qlo = f.get("value_low")
        qhi = f.get("value_high")
        # Целевое значение для ранжирования по близости (не по величине!):
        # середина диапазона запроса, либо его единственная граница.
        target = _target_value([f])
        params = {
            "unit": unit, "metric": metric, "material": material,
            "qlo": qlo, "qhi": qhi, "target": target,
        }
        try:
            with drv.session() as s:
                rows = [dict(r) for r in s.run(_NUMERIC_CYPHER, **params)]
        except Exception:
            rows = []
        for r in rows:
            key = (r.get("doc_id"), r.get("canon"), r.get("metric"),
                   r.get("value_low"), r.get("value_high"), r.get("unit"))
            if key in seen:
                continue
            seen.add(key)
            facts.append({
                "canon": r.get("canon") or "",
                "metric": r.get("metric"),
                "value_low": r.get("value_low"),
                "value_high": r.get("value_high"),
                "unit": r.get("unit"),
                "phase": r.get("phase"),
                "quote": r.get("quote") or "",
                "doc_id": r.get("doc_id"),
                "year": r.get("year"),
                "confidence": r.get("confidence"),
                "source": "число",
                "in_range": bool(r.get("in_range")),
                "track": "numeric",
            })
    return facts


# ─────────────────────────────────────────────────────────────────────────────
# Entity-facts дорожка: естественный запрос без чисел, но газетир нашёл сущности —
# подтянуть из графа топ-факты (по confidence) по этим canon. Даёт числовые факты
# с цитатами на «температура обжига концентрата», «ПВП» и т.п. (source='граф').
# ─────────────────────────────────────────────────────────────────────────────
_ENTITY_FACTS_CYPHER = """
MATCH (p:Parameter)-[:MEASURES]->(e)
WHERE toLower(coalesce(e.canon,'')) IN $canons
OPTIONAL MATCH (p)-[:DESCRIBED_IN]->(d:Document)
OPTIONAL MATCH (p)-[:MEASURED_IN]->(ph:Phase)
RETURN e.canon AS canon, p.metric AS metric,
       p.value_low AS value_low, p.value_high AS value_high,
       p.unit_canon AS unit, ph.canon AS phase, p.quote AS quote,
       d.doc_id AS doc_id, d.year AS year, p.confidence AS confidence
ORDER BY p.confidence DESC
LIMIT 10
"""


def _entity_facts_track(drv, ents):
    """Топ-факты графа по canon сущностей запроса (source='граф').

    Возвращает list[fact] в единой форме (как _numeric_track). Мягкая
    деградация: drv=None / нет сущностей / ошибка Cypher → [].
    """
    canons = [str(e.get("canon")).strip().lower()
              for e in (ents or []) if e.get("canon")]
    canons = list(dict.fromkeys(c for c in canons if c))
    if drv is None or not canons:
        return []
    try:
        with drv.session() as s:
            rows = [dict(r) for r in s.run(_ENTITY_FACTS_CYPHER, canons=canons)]
    except Exception:
        return []
    facts = []
    for r in rows:
        facts.append({
            "canon": r.get("canon") or "",
            "metric": r.get("metric"),
            "value_low": r.get("value_low"),
            "value_high": r.get("value_high"),
            "unit": r.get("unit"),
            "phase": r.get("phase"),
            "quote": r.get("quote") or "",
            "doc_id": r.get("doc_id"),
            "year": r.get("year"),
            "confidence": r.get("confidence"),
            "source": "граф",
            "track": "entity",
        })
    return facts


# ─────────────────────────────────────────────────────────────────────────────
# Семантическая дорожка (опциональна: src.embed.Semantic).
# ─────────────────────────────────────────────────────────────────────────────
# Порог косинуса: индекс мал и без порога семантика возвращает одни и те же
# top_k доков на ЛЮБОЙ запрос (включая бессмысленный). Доки со score ниже порога
# считаем шумом и не докидываем в выдачу.
_SCORE_FLOOR = 0.35

# Гейт вне-доменных запросов: если нет чисел, газетир пуст и семантический
# топ-score ниже этого порога — считаем запрос вне корпуса R&D (пустая выдача).
_OOD_SCORE_GATE = 0.45


def _semantic_track(query: str, top_k: int = 10, score_floor: float = _SCORE_FLOOR):
    """Топ-доки по смыслу через src.embed.Semantic (если модуль доступен).

    Возвращает list[{doc_id, score}] со score >= score_floor. Если embed
    недоступен/упал — []. Ищем гибко несколько API (search/query/top_docs/topk).
    Порог отсекает шумовой хвост: на 'zzz nonsense' → пусто, не top_k мусора.
    """
    try:
        from src.embed import Semantic  # noqa: WPS433 — опциональная зависимость
    except Exception:
        return []
    try:
        sem = Semantic()
    except Exception:
        return []
    for meth in ("search", "query", "top_docs", "topk", "most_similar"):
        fn = getattr(sem, meth, None)
        if not callable(fn):
            continue
        try:
            res = fn(query, top_k)
        except TypeError:
            try:
                res = fn(query)
            except Exception:
                continue
        except Exception:
            continue
        return _apply_score_floor(_norm_semantic(res), score_floor)
    return []


def _apply_score_floor(docs, score_floor=_SCORE_FLOOR):
    """Отсечь доки со score ниже порога (шумовой семантический хвост)."""
    return [d for d in docs if float(d.get("score") or 0.0) >= score_floor]


def _norm_semantic(res):
    """Нормализовать разнородный результат embed в [{doc_id, score}]."""
    out = []
    if not res:
        return out
    for item in res:
        if isinstance(item, dict):
            did = item.get("doc_id") or item.get("id")
            score = item.get("score") or item.get("sim") or 0.0
        elif isinstance(item, (tuple, list)) and item:
            did = item[0]
            score = item[1] if len(item) > 1 else 0.0
        else:
            did = item
            score = 0.0
        if did:
            out.append({"doc_id": nfc(str(did)), "score": float(score or 0.0)})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Слияние дорожек по doc_id.
# ─────────────────────────────────────────────────────────────────────────────
def _merge(numeric_facts, semantic_docs, score_floor=_SCORE_FLOOR):
    """Слить: числовая дорожка задаёт порядок doc_id, семантика докидывает хвост.

    Возвращает (docs, facts) где docs — список {doc_id, source} в порядке
    релевантности, facts — числовые факты (с бейджем source). Семантические доки
    ниже score_floor отсекаются (защита от шумового хвоста малого индекса).
    """
    semantic_docs = _apply_score_floor(semantic_docs or [], score_floor)
    order = []
    doc_source = {}

    def touch(did, src):
        if not did:
            return
        if did not in doc_source:
            doc_source[did] = src
            order.append(did)

    # 1) Порядок: эталонные факты (ref) → in_range → остальное.
    ranked = sorted(
        numeric_facts,
        key=lambda f: (0 if f.get("ref") else 1, 0 if f.get("in_range") else 1),
    )
    for f in ranked:
        touch(f.get("doc_id"), f.get("source") or "число")

    # 2) Семантика/keyword докидывают новые doc_id (которых нет в числовой).
    for sd in semantic_docs:
        did = sd.get("doc_id")
        if did in doc_source:
            continue
        touch(did, sd.get("source") or "семантика")

    docs = [{"doc_id": d, "source": doc_source[d]} for d in order]
    return docs, list(numeric_facts)


# ─────────────────────────────────────────────────────────────────────────────
# RBAC: скрыть internal-документы для внешних партнёров.
# ─────────────────────────────────────────────────────────────────────────────
_EXTERNAL_ROLES = ("external_partner",)


def _apply_rbac(docs, facts, role, meta):
    """Скрыть doc с sensitivity='internal' если роль внешняя. Счётчик hidden.

    Фильтрует и docs, и facts по одному правилу (факт привязан к doc_id).
    Возвращает (docs, facts, hidden_count).
    """
    if role not in _EXTERNAL_ROLES:
        return docs, facts, 0

    hidden_ids = set()
    for did, m in meta.items():
        if (m.get("sensitivity") or "").lower() == "internal":
            hidden_ids.add(did)

    kept_docs = [d for d in docs if d.get("doc_id") not in hidden_ids]
    hidden_count = len(docs) - len(kept_docs)
    kept_facts = [f for f in facts if f.get("doc_id") not in hidden_ids]
    return kept_docs, kept_facts, hidden_count


# ─────────────────────────────────────────────────────────────────────────────
# Фильтры выдачи (5 измерений ТЗ): год, география, материал, процесс, достоверность.
# Применяются пост-фильтром к facts/docs (Neo4j-агностично): и числовая, и
# семантическая дорожки уже слиты по doc_id, поэтому единый фильтр режет обе.
# ─────────────────────────────────────────────────────────────────────────────
_TEMPORAL_LAST_RE = re.compile(
    r"за\s+последн(?:ие|их)\s+(\d{1,3})\s+"
    r"(?:год|года|лет)",
    re.IGNORECASE,
)
_TEMPORAL_SINCE_RE = re.compile(r"\bс\s+((?:19|20)\d{2})\b", re.IGNORECASE)


def _current_year():
    import datetime
    return datetime.date.today().year


def parse_temporal(query: str):
    """Из запроса «за последние N лет» / «с YYYY» → нижняя граница года (year>=lo).

    Возвращает int-год или None. «за последние 5 лет» → текущий_год−5;
    «с 2020» → 2020. Ничего не найдено → None (фильтр по времени не применяется).
    """
    q = query or ""
    m = _TEMPORAL_LAST_RE.search(q)
    if m:
        try:
            return _current_year() - int(m.group(1))
        except Exception:
            return None
    m = _TEMPORAL_SINCE_RE.search(q)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _safe_int(v):
    """int(v) либо None (нечисловое/None — игнорируем, не падаем)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_filters(filters, temporal_lo=None):
    """Нормализовать пользовательские filters + временной год в единый вид.

    filters={'year':(lo,hi),'geo':...,'material':[...],'process':[...],
             'min_confidence':...}. Строки → списки; year (lo,hi) объединяется
     с temporal_lo (берём максимум нижних границ). Пустой результат → None.

    Устойчивость к мусору: filters не-dict → игнор (None-эквивалент); нечисловые
    year/min_confidence → игнор поля (поиск не падает на мусорном вводе UI/API).
    """
    if filters is not None and not isinstance(filters, dict):
        filters = None
    f = dict(filters or {})

    def _as_list(v):
        if v is None:
            return None
        if isinstance(v, (list, tuple, set)):
            out = [str(x).strip().lower() for x in v if str(x).strip()]
            return out or None
        s = str(v).strip().lower()
        return [s] if s else None

    # year принимает ДВЕ формы: кортеж (lo, hi) — диапазон (контракт search()),
    # ИЛИ список/множество дискретных годов из multiselect UI → диапазон [min, max].
    # Пустой список/None → без ограничения. (Баг: раньше year=[] давал int([]).)
    year_lo, year_hi = None, None
    yr = f.get("year")
    if isinstance(yr, tuple):
        year_lo = _safe_int(yr[0]) if len(yr) > 0 else None
        year_hi = _safe_int(yr[1]) if len(yr) > 1 else None
    elif isinstance(yr, (list, set)):
        yrs = [y for y in (_safe_int(x) for x in yr) if y is not None]
        if yrs:
            year_lo, year_hi = min(yrs), max(yrs)
    elif yr not in (None, ""):
        year_lo = _safe_int(yr)
    if temporal_lo is not None:
        tlo = _safe_int(temporal_lo)
        if tlo is not None:
            year_lo = tlo if year_lo is None else max(year_lo, tlo)

    # min_confidence: нечисловое ('x') → игнор поля, а не TypeError на весь search.
    mc = f.get("min_confidence")
    try:
        mc = float(mc) if mc is not None else None
    except (TypeError, ValueError):
        mc = None

    out = {
        "year_lo": year_lo,
        "year_hi": year_hi,
        "geo": _as_list(f.get("geo")),
        "material": _as_list(f.get("material")),
        "process": _as_list(f.get("process")),
        "min_confidence": mc,
    }
    if not any(v is not None for v in out.values()):
        return None
    return out


def _fact_year(f, meta):
    """Год факта: явный f['year'] либо год документа из meta."""
    y = f.get("year")
    if y in (None, ""):
        m = meta.get(f.get("doc_id"), {})
        y = m.get("year")
    try:
        return int(y) if y not in (None, "") else None
    except Exception:
        return None


def _doc_geo(did, meta):
    """Нормализованная гео (RU/WORLD/страна) — как в графе (graph._norm_geo), а НЕ
    сырой meta.geo. Иначе фильтр geo=['RU'] не совпадал бы с нормализованным d.geo
    графа (сырой meta.geo — региональные имена/None)."""
    m = meta.get(did, {}) or {}
    ng = graph._norm_geo(lang=m.get("lang"), sensitivity=m.get("sensitivity"),
                         geo=m.get("geo"), src=m.get("src"), cat=m.get("cat"))
    return (ng or "").lower()


def _fact_matches_filters(f, nf, meta):
    """True, если факт проходит ВСЕ активные фильтры (год/гео/материал/процесс/conf)."""
    did = f.get("doc_id")
    # Год (по факту или документу).
    if nf.get("year_lo") is not None or nf.get("year_hi") is not None:
        y = _fact_year(f, meta)
        if y is None:
            return False
        if nf.get("year_lo") is not None and y < nf["year_lo"]:
            return False
        if nf.get("year_hi") is not None and y > nf["year_hi"]:
            return False
    # География (по документу).
    if nf.get("geo"):
        geo = _doc_geo(did, meta)
        if not geo or not any(g in geo for g in nf["geo"]):
            return False
    # Достоверность (confidence>=порог).
    if nf.get("min_confidence") is not None:
        c = f.get("confidence")
        if c is None or float(c) < nf["min_confidence"]:
            return False
    # Материал — по canon факта. Процесс — по canon+цитате+условиям
    # (процесс редко = canon факта-материала; он всплывает в контексте цитаты).
    canon = (f.get("canon") or "").lower()
    if nf.get("material") and not any(mm in canon for mm in nf["material"]):
        return False
    if nf.get("process"):
        src = ((meta.get(did, {}) or {}).get("src") or "")
        hay = (canon + " " + (f.get("quote") or "") + " " + str(f.get("conditions") or "")
               + " " + (f.get("metric") or "") + " " + src).lower()
        if not any(pp.lower() in hay for pp in nf["process"]):
            return False
    return True


def _doc_matches_filters(d, nf, meta):
    """True, если документ проходит доступные ему фильтры (год/гео/достоверность).

    Материал/процесс к «голому» документу (без факта) не применяем — у него нет
    canon; такие фильтры режут только фактовую выдачу. Это осознанно: документ,
    добавленный семантикой, остаётся, если явно не отсечён годом/гео.
    """
    did = d.get("doc_id")
    m = meta.get(did, {}) or {}
    if nf.get("year_lo") is not None or nf.get("year_hi") is not None:
        y = m.get("year")
        try:
            y = int(y) if y not in (None, "") else None
        except Exception:
            y = None
        if y is None:
            return False
        if nf.get("year_lo") is not None and y < nf["year_lo"]:
            return False
        if nf.get("year_hi") is not None and y > nf["year_hi"]:
            return False
    if nf.get("geo"):
        geo = _doc_geo(did, meta)   # нормализованная RU/WORLD (как в графе)
        if not geo or not any(g in geo for g in nf["geo"]):
            return False
    # Материал/процесс: если заданы, документ обязан иметь факт, прошедший фильтр —
    # проверяется отдельно в _apply_filters (по множеству оставшихся doc_id).
    return True


def _apply_filters(docs, facts, nf, meta):
    """Применить нормализованные фильтры к facts и docs. nf=None → без изменений.

    Возвращает (docs, facts). Факты фильтруются по всем 5 измерениям; документы —
    по году/гео; при заданном материале/процессе документ остаётся, только если
    у него есть прошедший фильтр факт (или он не отсечён иными измерениями и
    материал/процесс не заданы).
    """
    if not nf:
        return docs, facts
    kept_facts = [f for f in facts if _fact_matches_filters(f, nf, meta)]
    fact_docs = {f.get("doc_id") for f in kept_facts}
    need_material_process = bool(nf.get("material") or nf.get("process"))
    kept_docs = []
    for d in docs:
        if not _doc_matches_filters(d, nf, meta):
            continue
        if need_material_process and d.get("doc_id") not in fact_docs:
            # Материал/процесс задан, но у документа нет подходящего факта → режем.
            continue
        kept_docs.append(d)
    return kept_docs, kept_facts


# ─────────────────────────────────────────────────────────────────────────────
# Композер экстрактивного ответа (без генерации).
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_value(f) -> str:
    """value_low/value_high → строка ('12', '12–15', '≤300', '≥5')."""
    lo, hi = f.get("value_low"), f.get("value_high")
    if lo is not None and hi is not None:
        if lo > hi:                       # инвертированные диапазоны («100–12» → «12–100»)
            lo, hi = hi, lo
        if lo == hi:
            return _num(lo)
        return f"{_num(lo)}–{_num(hi)}"
    if hi is not None:
        return f"≤{_num(hi)}"
    if lo is not None:
        return f"≥{_num(lo)}"
    return ""


def _num(v) -> str:
    """Число → строка без плавающего мусора (0.8049999→0.805, 0.00028 сохранён)."""
    if not isinstance(v, float):
        return str(v)
    if v.is_integer():
        return str(int(v))
    av = abs(v)
    digits = 3 if av >= 1 else (4 if av >= 0.01 else 6)  # хватает значащих
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


# Санити-границы значений по единице: за ними — табличный/OCR-мусор, не факт.
_VALUE_BOUNDS = {"degC": (-50, 3500), "pct": (0, 100), "pH": (0, 14)}
_NONNEG_UNITS = {"mg_L", "g_t", "A_m2", "m3_h", "t_day"}


def _implausible(f) -> bool:
    """True, если значение физически невозможно (−273 °C, 14100 %) → не показывать."""
    u = f.get("unit")
    vals = [x for x in (f.get("value_low"), f.get("value_high")) if x is not None]
    if not vals:
        return False
    if u in _VALUE_BOUNDS:
        blo, bhi = _VALUE_BOUNDS[u]
        return any(v < blo or v > bhi for v in vals)
    if u in _NONNEG_UNITS:
        return any(v < 0 for v in vals)
    return False


# Сравнительно-разностная конструкция: «на 400 °C ниже, чем…» — число это РАЗНОСТЬ,
# а не абсолютное значение параметра. Такой факт ложный, из выдачи убираем.
_CMP_ARTIFACT = re.compile(
    r"\bна\s+[\d.,]+\s*[°%\w/]*\s*(ниже|выше|больше|меньше)\b", re.I)


def _comparison_artifact(f) -> bool:
    """True, если число в цитате — часть сравнения-разности («на N ниже, чем»)."""
    return bool(_CMP_ARTIFACT.search(f.get("quote") or ""))


def _bullet(f, meta=None) -> str:
    """«<metric> <value> <unit> [<phase>] — «<quote>» (<src>, <year>)»."""
    from src.config import unit_ru
    metric = f.get("metric") or ""
    value = _fmt_value(f)
    unit = unit_ru(f.get("unit"))
    # Не дублировать единицу, когда metric и есть название единицы ('pH pH',
    # 'мг/л мг/л'): сравниваем metric с отображаемой и канонической формой.
    if unit and metric:
        _u_raw = str(f.get("unit") or "")
        if metric.strip().lower() in {unit.strip().lower(), _u_raw.strip().lower()}:
            unit = ""
    phase = f.get("phase") or ""
    quote = (f.get("quote") or "").strip()
    src = f.get("source") or ""
    year = f.get("year")
    year_s = str(year) if year not in (None, "") else "б.г."

    head = " ".join(p for p in (metric, value, unit) if p).strip()
    if phase:
        head = f"{head} [{phase}]".strip()
    doc_id = f.get("doc_id") or ""
    title = ((meta or {}).get(doc_id) or {}).get("src") or doc_id or "источник"
    ref = f"[«{title}» ({year_s})](/sources?doc={doc_id})" if doc_id else f"({year_s})"
    if quote:
        if len(quote) > 240:
            quote = quote[:237].rstrip() + "…"
        return f"- {head} — «{quote}» — {ref}"
    return f"- {head} — {ref}"


def _doc_link(did, meta) -> str:
    """`[«title» (year)](/sources?doc=id)` — ссылка на первоисточник по doc_id."""
    m = ((meta or {}).get(did) or {})
    title = m.get("src") or did or "источник"
    yr = m.get("year")
    ys = f" ({yr})" if yr not in (None, "") else ""
    return f"[«{title}»{ys}](/sources?doc={did})" if did else "источник"


def _group_sources(fs, meta, cap=3) -> str:
    """Хвост строки обзора: ` — ист.: <ссылка>, <ссылка>` (уникальные doc_id)."""
    dids = list(dict.fromkeys(f.get("doc_id") for f in fs if f.get("doc_id")))
    if not dids:
        return ""
    links = ", ".join(_doc_link(d, meta) for d in dids[:cap])
    more = f" +{len(dids) - cap}" if len(dids) > cap else ""
    return f" — ист.: {links}{more}"


def _clean_quote(q: str) -> str:
    """Причесать провенанс-цитату: убрать табличную разметку и повторы шапки.

    Табличные факты хранят «цитатой» сырую строку/шапку таблицы
    («Содержание в шлаке, г/т Содержание в шлаке, г/т … | Pt: 0,0162»). Режем
    '|' и \\xa0, схлопываем переводы строк и ПОДРЯД идущие повторы словосочетаний.
    """
    if not q:
        return q
    s = q.replace("\xa0", " ").replace("\n", " ").replace("\t", " ").replace("|", " ")
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split()
    # схлопнуть немедленные повторы фраз (окна от длинных к коротким)
    for L in range(min(8, len(words) // 2), 1, -1):
        i = 0
        while i + 2 * L <= len(words):
            if words[i:i + L] == words[i + L:i + 2 * L]:
                del words[i + L:i + 2 * L]
            else:
                i += 1
    s = " ".join(words)
    s = re.sub(r"\s+([,;])", r"\1", s)
    return s.strip(" ,;—-")


def _bad_quote(q: str) -> int:
    """1, если цитата вырождена (табличная шапка), а не связное предложение — иначе 0.

    Используется как хвостовой ключ ранжирования: осмысленные цитаты — выше.
    """
    if not q or "|" in q or "\xa0" in q:
        return 1
    words = q.split()
    if len(words) < 4:
        return 1
    grams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    if grams:
        from collections import Counter
        if Counter(grams).most_common(1)[0][1] >= 3:  # повтор 3-граммы ≥3× → таблица
            return 1
    return 0


def _target_value(nums):
    """Целевое значение запроса для ранжирования: единая точка из числовых фактов.

    Берём середину диапазона первого числового факта (или его границу). Если чисел
    нет — None (тогда близость к цели не учитывается).
    """
    for f in (nums or []):
        lo, hi = f.get("value_low"), f.get("value_high")
        if lo is not None and hi is not None:
            return (float(lo) + float(hi)) / 2.0
        if hi is not None:
            return float(hi)
        if lo is not None:
            return float(lo)
    return None


def _fact_sort_key(f, target):
    """Ключ ранжирования факта: (0 если ref, 0 если in_range, |значение−цель|).

    Эталонные (ref) и попавшие в диапазон (in_range) — вперёд; внутри — по близости
    значения факта к целевому значению запроса. Контекст (in_range=False) — в хвост.
    """
    is_ref = 0 if f.get("ref") else 1
    is_in_range = 0 if f.get("in_range") else 1
    lo, hi = f.get("value_low"), f.get("value_high")
    if lo is not None and hi is not None:
        val = (float(lo) + float(hi)) / 2.0
    elif hi is not None:
        val = float(hi)
    elif lo is not None:
        val = float(lo)
    else:
        val = None
    if target is None or val is None:
        dist = float("inf")
    else:
        dist = abs(val - target)
    # хвостовой ключ: при равной релевантности осмысленная цитата — выше табличного мусора
    return (is_ref, is_in_range, dist, _bad_quote(f.get("quote")))


def _rank_facts(facts, target):
    """Стабильная сортировка фактов по _fact_sort_key (in_range первыми)."""
    return sorted(facts, key=lambda f: _fact_sort_key(f, target))


def _fact_signature(f):
    """Сигнатура факта для дедупа: (metric, value, unit, phase, doc, quote[:50])."""
    return (
        f.get("metric"),
        (f.get("value_low"), f.get("value_high")),
        f.get("unit"),
        f.get("phase"),
        f.get("doc_id"),
        (f.get("quote") or "")[:50],
    )


def _dedup_facts(facts):
    """Убрать дубли фактов по сигнатуре, сохранив порядок."""
    seen = set()
    out = []
    for f in facts:
        sig = _fact_signature(f)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(f)
    return out


def _doc_preview_line(d, meta, previews) -> str:
    """Строка секции 'Релевантные документы': src/year (из meta) + превью текста."""
    did = d.get("doc_id")
    m = meta.get(did, {}) if did else {}
    src = m.get("src") or did or "—"
    year = m.get("year")
    year_s = str(year) if year not in (None, "") else "б.г."
    label = f"[**{src}** ({year_s})](/sources?doc={did})" if did else f"**{src}** ({year_s})"
    prev = (previews.get(did) or "").strip() if did else ""
    if prev:
        return f"- {label} — {prev}"
    return f"- {label}"


# Порог корпус-частоты автора: >15 диверсных документов = административная
# над-атрибуция (директор/завотделом в «Списке исполнителей» титула), не эксперт.
_AUTHOR_CORPUS_CAP = 15

# Редакторские роли/шум, ошибочно распознанные regex как авторы.
_JUNK_AUTHOR = re.compile(
    r"\b(editor|editors|junior|senior|researcher|reviewer|corresponding|"
    r"professor|prof|редактор|редакция|рецензент)\b", re.I)
# Маска русской ФИО: «Фамилия», «Фамилия И.», «Фамилия И.О.», «Фамилия И.И» (без
# финальной точки), дефисные фамилии. Латиница/смешанное/слова НЕ проходят.
_NAME_RU = re.compile(r"^[А-ЯЁ][а-яё]{2,}(-[А-ЯЁ][а-яё]+)?(\s+[А-ЯЁ]\.?[А-ЯЁ]?\.?)?$")
# Частые слова, ошибочно ставшие «фамилией» (причастия/глаголы/термины).
_WORD_SURN = {
    "проведен", "проведён", "взаимосвязь", "интересны", "установлено", "показано",
    "получен", "получено", "определен", "определён", "рассмотрен", "предложен",
    "разработан", "выполнен", "исследован", "приведен", "приведён", "представлен",
    "отмечено", "заключение", "введение", "результаты", "таблица", "рисунок",
    # организации/география/роли, ошибочно попадающие под ФИО-маску
    "федерация", "республика", "область", "институт", "университет", "компания",
    "общество", "комбинат", "завод", "фабрика", "корпорация", "академия",
    "лаборатория", "кафедра", "министерство", "россия", "москва",
    "российская", "российской", "федеральное", "государственное",
    # роли/должности, ошибочно ставшие «фамилией»
    "специалист", "автор", "докладчик", "руководитель", "начальник",
    "главный", "ведущий", "инженер", "директор", "заведующий", "профессор",
}


def _is_junk_author(name: str) -> bool:
    """True для НЕ-эксперта: роли, латиница/иностр. огрызки, слова-как-фамилии,
    смешанные скрипты. Оставляем только чистую русскую ФИО «Фамилия И.[О.]» —
    для русского R&D-корпуса это и есть верифицируемые эксперты."""
    if not name or _JUNK_AUTHOR.search(name):
        return True
    n = name.strip()
    if not _NAME_RU.match(n):            # латиница/смешанное/не-ФИО → мусор
        return True
    return n.split()[0].lower() in _WORD_SURN  # «Проведен С.», «Взаимосвязь …»


def _experts_from_graph(drv, doc_ids):
    """doc_ids → [{name, docs}] из рёбер AUTHORED_BY графа (точные ФИО, не из имён
    файлов). Приоритетный источник экспертов: граф несёт извлечённых авторов
    (Author/:Expert), тогда как meta.src-парсинг ловит их лишь у части документов."""
    if drv is None or not doc_ids:
        return []
    ids = list(dict.fromkeys(d for d in doc_ids if d))
    try:
        with drv.session() as s:
            # Отсекаем административную над-атрибуцию: имена с корпус-частотой >15
            # (директора/завотделами из «Списка исполнителей» титула КАЖДОГО отчёта:
            # «УТВЕРЖДАЮ Директор … Цымбулов» = 52 дока, Евграфова = 77) — это
            # подписанты, а не топик-эксперты. Реальные авторы имеют ≤15 док.
            rows = s.run(
                "MATCH (d:Document)-[:AUTHORED_BY]->(a:Author) "
                "WHERE d.doc_id IN $ids "
                "WITH a, count(DISTINCT d) AS docs "
                "MATCH (a)<-[:AUTHORED_BY]-(td:Document) "
                "WITH a, docs, count(DISTINCT td) AS total "
                "WHERE total <= $cap "
                "RETURN a.canon AS name, docs "
                "ORDER BY docs DESC, name LIMIT 25", ids=ids, cap=_AUTHOR_CORPUS_CAP)
            return [{"name": r["name"], "docs": r["docs"]} for r in rows
                    if r["name"] and not _is_junk_author(r["name"])]
    except Exception:  # noqa: BLE001 — граф недоступен → фолбэк на meta.src
        return []


def _aggregate_experts(doc_ids, meta):
    """doc_ids → [{name, docs}] по частоте (парсинг ФИО из meta.src, дедуп по doc)."""
    counts = {}   # author -> set(doc_id)
    order = []
    for did in doc_ids:
        m = meta.get(did, {})
        for a in _authors_from_src(m.get("src")):
            if _is_junk_author(a):
                continue
            if a not in counts:
                counts[a] = set()
                order.append(a)
            counts[a].add(did)
    experts = [{"name": a, "docs": len(counts[a])} for a in order]
    experts.sort(key=lambda e: (-e["docs"], e["name"]))
    return experts


def _expert_track(docs, facts, meta, drv=None):
    """Дорожка 'кто эксперт по…': агрегировать авторов по РЕЛЕВАНТНЫМ документам.

    Приоритет — doc_id числовых фактов и документы числовой дорожки
    (source!='семантика'): их авторы и есть эксперты. Имена парсим паттерном
    «Фамилия И.О.» (_authors_from_src), а НЕ имя файла целиком — заголовки
    документов экспертами больше не становятся. Только если релевантные (числовые)
    доки не дали ни одного ФИО, добираем авторов из уже прошедших порог score
    семантических доков (fallback), чтобы не отдавать пустой ответ на осмысленный
    запрос. Возвращает список {name, docs} по убыванию встречаемости.
    """
    numeric_ids, semantic_ids = [], []
    # Числовые факты — самый релевантный источник авторов.
    for f in facts:
        if f.get("doc_id"):
            numeric_ids.append(f["doc_id"])
    for d in docs:
        did = d.get("doc_id")
        if not did:
            continue
        if d.get("source") == "семантика":
            semantic_ids.append(did)
        else:
            numeric_ids.append(did)

    # Приоритет — точные авторы из графа (AUTHORED_BY) по релевантным докам;
    # meta.src-парсинг остаётся фолбэком (граф недоступен / у дока нет рёбер).
    g = (_experts_from_graph(drv, numeric_ids)
         or _experts_from_graph(drv, numeric_ids + semantic_ids))
    if g:
        return g
    experts = _aggregate_experts(numeric_ids, meta)
    if experts:
        return experts
    # Fallback: релевантных ФИО в числовых доках нет — берём из семантических
    # (они уже отфильтрованы порогом score в _merge, шумовой хвост не доходит).
    return _aggregate_experts(numeric_ids + semantic_ids, meta)


def compose_answer(query: str, facts, docs, hidden_count: int,
                   meta=None, previews=None, experts=None) -> str:
    """Экстрактивный markdown-ответ: авторы (для expert) + факты + документы + шапка.

    facts должны приходить уже ранжированными и дедуплицированными. Контекстные
    факты (in_range=False) помечаются '(вне диапазона)'. Секция 'Релевантные
    документы' строится из семантической/числовой выдачи (meta.src/year + превью).
    """
    meta = meta or {}
    previews = previews or {}
    lines = [f"## Результаты поиска: {query}", ""]

    # Факты и эксперты выводятся СТРУКТУРНЫМИ панелями фронта (таблица «Факты»,
    # карточки «Эксперты», чипы «Смежные темы») — здесь их НЕ дублируем текстом,
    # иначе выдача превращается в «простыню». В тексте — только сводка + документы.
    if not facts:
        lines.append("_Числовых фактов по запросу не найдено._")
        lines.append("")

    # Секция релевантных документов (нет структурного аналога — оставляем в тексте).
    if docs:
        lines.append("### Релевантные документы")
        lines.append("")
        for d in docs[:15]:
            lines.append(_doc_preview_line(d, meta, previews))
        lines.append("")

    lines.append(f"Документов в выдаче: {len(docs)}.")
    if hidden_count:
        lines.append(f"Скрыто по правам доступа: {hidden_count}.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Аналитика: разногласия/консенсус (CONTRADICTS/VALIDATED_BY), пробелы,
# рекомендации (похожие кейсы / смежные темы / эксперты), сравнительные таблицы.
# Все запросы к графу — с мягкой деградацией (drv=None или ошибка → пусто).
# ─────────────────────────────────────────────────────────────────────────────
_REL_CYPHER = """
MATCH (a:Document)-[r:%(REL)s]->(b:Document)
WHERE a.doc_id IN $doc_ids OR b.doc_id IN $doc_ids
RETURN a.doc_id AS a, b.doc_id AS b,
       r.metric AS metric, r.entity AS entity, r.phase AS phase,
       r.unit AS unit, r.kind AS kind, r.val_a AS val_a, r.val_b AS val_b
LIMIT 300
"""


def _rel_rows(drv, rel: str, doc_ids):
    """Строки CONTRADICTS/VALIDATED_BY, где хотя бы один конец — в выдаче."""
    if drv is None or not doc_ids:
        return []
    cy = _REL_CYPHER % {"REL": rel}
    try:
        with drv.session() as s:
            return [dict(r) for r in s.run(cy, doc_ids=list(doc_ids))]
    except Exception:
        return []


def _doc_confidence_map(facts):
    """{doc_id: средняя Parameter.confidence его фактов} — реальная (0..1) достоверность."""
    from collections import defaultdict
    acc = defaultdict(list)
    for f in (facts or []):
        did = f.get("doc_id")
        c = f.get("confidence")
        if did and isinstance(c, (int, float)):
            acc[did].append(float(c))
    return {d: (sum(v) / len(v)) for d, v in acc.items() if v}


def _rel_group_lines(rows, meta, doc_conf=None, by="metric"):
    """Сгруппировать связи по методу/ГОДУ/географии → строки с N источников + ср.conf.

    Для каждой группы (по entity+metric+phase+unit) выводим: N подтверждающих
    источников (уникальные doc_id обоих концов), среднюю Parameter.confidence этих
    документов (0..1, из doc_conf при наличии), группировку по методу (kind), году
    и географии концов.
    """
    doc_conf = doc_conf or {}
    from collections import defaultdict
    groups = defaultdict(lambda: {"docs": set(), "kinds": set(),
                                   "years": set(), "geos": set(),
                                   "va": [], "vb": [], "unit": None,
                                   "entity": None, "metric": None, "phase": None})
    for r in rows:
        key = (r.get("entity") or "—", r.get("metric") or "—",
               r.get("phase") or "—", r.get("unit") or "—")
        g = groups[key]
        g["entity"] = r.get("entity") or "—"
        g["metric"] = r.get("metric") or "—"
        g["phase"] = r.get("phase") or "—"
        g["unit"] = r.get("unit") or "—"
        for did in (r.get("a"), r.get("b")):
            if not did:
                continue
            g["docs"].add(did)
            m = meta.get(did, {}) or {}
            y = m.get("year")
            if y not in (None, ""):
                g["years"].add(str(y))
            geo = m.get("geo")
            if geo:
                g["geos"].add(str(geo))
        if r.get("kind"):
            g["kinds"].add(r["kind"])
        if r.get("val_a") is not None:
            g["va"].append(r["val_a"])
        if r.get("val_b") is not None:
            g["vb"].append(r["val_b"])

    lines = []
    for key, g in sorted(groups.items(),
                         key=lambda kv: (-len(kv[1]["docs"]), kv[0])):
        n = len(g["docs"])
        # Средняя реальная confidence фактов документов группы (0..1).
        confs = [doc_conf[did] for did in g["docs"] if did in doc_conf]
        conf_s = (f", ср.confidence {sum(confs) / len(confs):.2f}"
                  if confs else "")
        head = g["entity"]
        if g["metric"] and g["metric"] != "—":
            head = f"{head} · {g['metric']}"
        if g["phase"] and g["phase"] != "—":
            head = f"{head} [{g['phase']}]"
        _KIND_RU = {"method_vs_method": "метод vs метод",
                    "ru_vs_world": "Россия vs мир"}
        method = ("/".join(_KIND_RU.get(k, k) for k in sorted(g["kinds"])) or "—")
        years = ", ".join(sorted(g["years"])) or "б.г."
        geos = ", ".join(sorted(g["geos"])) or "—"
        vals = ""
        if g["va"] or g["vb"]:
            a_set = list(dict.fromkeys(g["va"]))
            b_set = list(dict.fromkeys(g["vb"]))
            unit = unit_ru(g["unit"]) if g["unit"] != "—" else ""
            if set(a_set) == set(b_set):
                # Согласие: значения совпадают — «≈X», а не бессмысленное «X vs X».
                vals = f" (≈{'/'.join(_num(v) for v in a_set)} {unit})".rstrip()
            else:
                # Различие: показываем стороны БЕЗ пересечения (иначе декартова размазня
                # «50/0.81 vs 0.81/50» — одни и те же числа по обе стороны).
                common = set(a_set) & set(b_set)
                a_only = [v for v in a_set if v not in common] or a_set
                b_only = [v for v in b_set if v not in common] or b_set
                a_s = "/".join(_num(v) for v in a_only)
                b_s = "/".join(_num(v) for v in b_only)
                vals = f" ({a_s} vs {b_s} {unit})".rstrip()
        src_links = ", ".join(_doc_link(d, meta) for d in list(g["docs"])[:3])
        more = f" +{len(g['docs']) - 3}" if len(g["docs"]) > 3 else ""
        lines.append(
            f"- **{head}**{vals} — источников: {n}{conf_s}; "
            f"метод: {method}; годы: {years}; география: {geos}"
            + (f"; ист.: {src_links}{more}" if src_links else "")
        )
    return lines


def _lang_geo_gaps(docs, facts, meta):
    """Пробелы охвата: сущности выдачи, чьи доки ВСЕ одного lang / одной geo.

    Возвращает (only_ru, only_world, geo_bound) — списки строк. Только-RU: все
    документы сущности lang='RU' (нет зарубежного подтверждения); только-зарубеж:
    все lang='EN'. geo_bound: сущность встречается лишь в одной географии.
    """
    from collections import defaultdict
    ent_docs = defaultdict(set)
    for f in facts:
        canon = f.get("canon")
        did = f.get("doc_id")
        if canon and did:
            ent_docs[canon].add(did)
    only_ru, only_world, geo_bound = [], [], []
    for canon, dids in sorted(ent_docs.items()):
        langs = {((meta.get(d, {}) or {}).get("lang") or "").upper()
                 for d in dids}
        langs.discard("")
        geos = {((meta.get(d, {}) or {}).get("geo") or "")
                for d in dids}
        geos.discard("")
        if langs == {"RU"}:
            only_ru.append(f"- **{canon}** — только отечественные источники "
                           f"({len(dids)} док.), нет зарубежного подтверждения")
        elif langs == {"EN"}:
            only_world.append(f"- **{canon}** — только зарубежные источники "
                              f"({len(dids)} док.), нет отечественного аналога")
        if len(geos) == 1:
            geo_bound.append(f"- **{canon}** — только география «{next(iter(geos))}»")
    return only_ru, only_world, geo_bound


def _combo_gaps(ents, facts):
    """Комбинации материал×процесс без фактов (сущности запроса, нет пересечения).

    Из gazetteer-сущностей запроса берём материалы и процессы; для каждой пары
    материал×процесс проверяем, есть ли факт, чей canon совпадает с материалом
    ИЛИ процессом одновременно упоминает оба. Пары без единого факта → пробел.
    """
    materials = [e.get("canon") for e in (ents or [])
                 if e.get("type") == "Material" and e.get("canon")]
    processes = [e.get("canon") for e in (ents or [])
                 if e.get("type") == "Process" and e.get("canon")]
    fact_canons = {(f.get("canon") or "").lower() for f in facts}
    gaps = []
    for mat in dict.fromkeys(materials):
        for proc in dict.fromkeys(processes):
            ml, pl = mat.lower(), proc.lower()
            covered = any((ml in fc or pl in fc) for fc in fact_canons if fc)
            if not covered:
                gaps.append(f"- **{mat} × {proc}** — нет числовых фактов "
                            f"по этой комбинации материал×процесс")
    return gaps


_NEIGHBOR_CYPHER = """
UNWIND $canons AS c
MATCH (n)
WHERE (n:Process OR n:Material) AND toLower(coalesce(n.canon,'')) = toLower(c)
MATCH (n)-[]-(m)
WHERE (m:Process OR m:Material) AND m.canon <> n.canon
RETURN DISTINCT labels(m)[0] AS type, m.canon AS canon
LIMIT 40
"""


def _graph_neighbors(drv, ents):
    """Смежные темы: граф-соседи Process/Material сущностей выдачи (для рекомендаций)."""
    canons = [e.get("canon") for e in (ents or [])
              if e.get("type") in ("Process", "Material") and e.get("canon")]
    if drv is None or not canons:
        return []
    try:
        with drv.session() as s:
            rows = [dict(r) for r in s.run(_NEIGHBOR_CYPHER, canons=canons)]
    except Exception:
        return []
    seen, out = set(), []
    for r in rows:
        c = r.get("canon")
        if c and c not in seen:
            seen.add(c)
            out.append({"type": r.get("type"), "canon": c})
    return out


def _similar_cases(semantic_docs, docs, meta, n=8):
    """Похожие кейсы: e5-соседи выдачи (семантика), не вошедшие в основную выдачу.

    Берём семантические doc_id (уже прошедшие порог score в _semantic_track),
    исключаем те, что уже в выдаче — это «смежные темы» по смыслу.
    """
    in_out = {d.get("doc_id") for d in (docs or [])}
    out = []
    for sd in (semantic_docs or []):
        did = sd.get("doc_id")
        if not did or did in in_out:
            continue
        m = meta.get(did, {}) or {}
        out.append({"doc_id": did, "src": m.get("src") or did,
                    "year": m.get("year"), "score": sd.get("score")})
        if len(out) >= n:
            break
    return out


def build_recommendations(drv, ents, docs, facts, semantic_docs, meta):
    """Блок рекомендаций: похожие кейсы + смежные темы + эксперты. dict для UI."""
    return {
        "similar_cases": _similar_cases(semantic_docs, docs, meta),
        "adjacent_topics": _graph_neighbors(drv, ents),
        "experts": _expert_track(docs, facts, meta, drv),
    }


def _recommendations_md(rec):
    """Markdown-блок рекомендаций (Похожие кейсы / Смежные темы / Эксперты)."""
    lines = []
    sc = rec.get("similar_cases") or []
    at = rec.get("adjacent_topics") or []
    ex = rec.get("experts") or []
    if not (sc or at or ex):
        return ""
    _RU_TYPE = {"Process": "процесс", "Material": "материал", "Equipment": "оборудование",
                "Facility": "объект", "Phase": "фаза", "Parameter": "параметр",
                "Document": "документ", "Publication": "публикация"}
    lines.append("### Рекомендации")
    lines.append("")
    if sc:
        lines.append("**Похожие документы:**")
        lines.append("")
        for c in sc[:8]:
            y = c.get("year"); y = str(y) if y not in (None, "") else "б.г."
            _d = c.get("doc_id")
            lines.append(f"- [«{c.get('src')}» ({y})](/sources?doc={_d})" if _d else f"- «{c.get('src')}» ({y})")
        lines.append("")
    if at:
        lines.append("**Смежные темы:**")
        lines.append("")
        topics = ", ".join(f"[{tt.get('canon')}](/?q={_q(tt.get('canon'))}) ({_RU_TYPE.get(tt.get('type'), 'тема')})"
                           for tt in at[:12] if tt.get('canon'))
        lines.append(topics)
        lines.append("")
    if ex:
        lines.append("**Эксперты по теме** (авторы релевантных публикаций):")
        lines.append("")
        for e in ex[:8]:
            lines.append(f"- [{e['name']}](/sources?q={_q(e['name'])}) — публикаций: {e['docs']}")
        lines.append("")
    return "\n".join(lines)


def _comparative_md(facts, meta):
    """Сравнительные таблицы: «отеч vs мир» (по lang RU/EN) и по географии.

    Группируем факты по (canon, metric, unit); внутри — колонки RU / зарубеж
    (по lang документа). Отдаём markdown-таблицу метрик, если есть что сравнивать.
    """
    from collections import defaultdict
    groups = defaultdict(lambda: {"ru": [], "world": []})
    for f in facts:
        did = f.get("doc_id")
        lang = ((meta.get(did, {}) or {}).get("lang") or "").upper()
        key = (f.get("canon") or "—", f.get("metric") or "—", f.get("unit") or "—")
        side = "ru" if lang == "RU" else ("world" if lang == "EN" else None)
        if side is None:
            continue
        groups[key][side].append(_fmt_value(f))
    # Оставляем только группы, где есть обе стороны (иначе сравнивать нечего).
    rows = [(k, v) for k, v in groups.items() if v["ru"] and v["world"]]
    if not rows:
        return ""
    lines = ["### Сравнение: отечественное vs мировое", "",
             "| Сущность | Метрика | Ед. | Отечественное | Зарубежное |",
             "|---|---|---|---|---|"]
    def _side(vals):
        uniq = [x for x in dict.fromkeys(vals) if x]
        if not uniq:
            return "—"
        if len(uniq) <= 4:
            return ", ".join(uniq)
        return f"{', '.join(uniq[:4])} … (ещё {len(uniq) - 4})"
    for (canon, metric, unit), v in sorted(rows)[:15]:
        lines.append(f"| {canon} | {metric} | {unit_ru(unit)} | {_side(v['ru'])} | {_side(v['world'])} |")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Публичный API.
# ─────────────────────────────────────────────────────────────────────────────
# Кэш факта недоступности Neo4j: первая неудача _connect стоит ~10.5с (retry);
# без кэша КАЖДЫЙ последующий search платил бы её снова. TTL 30с — после истечения
# пробуем переподключиться (Neo4j мог подняться).
_NEO4J_DOWN_UNTIL = 0.0
_NEO4J_DOWN_TTL = 30.0


def _connect():
    """Драйвер Neo4j или None (мягкая деградация при недоступности).

    Недоступность кэшируется на модуле с TTL: в течение _NEO4J_DOWN_TTL секунд
    после неудачи сразу возвращаем None (без повторных ~10с retry на каждый поиск).
    """
    global _NEO4J_DOWN_UNTIL
    if time.time() < _NEO4J_DOWN_UNTIL:
        return None
    try:
        drv = graph.driver(retry_seconds=10.0)
        _NEO4J_DOWN_UNTIL = 0.0
        return drv
    except Exception as e:
        _NEO4J_DOWN_UNTIL = time.time() + _NEO4J_DOWN_TTL
        get_logger("search").warning("neo4j unavailable, degrading: %s", e)
        return None


def search(query: str, role: str = "researcher", filters=None) -> dict:
    """Гибридный поиск + композер ответа.

    Возврат: {intent, answer_md, facts, docs, hidden_count}.
    Мягко деградирует: без Neo4j — пустые facts/docs; без LLM — rule-based интент;
    без embed — только числовая дорожка.
    """
    logger = get_logger("search")
    _t0 = time.perf_counter()
    query = nfc(query or "")
    meta = _load_meta()

    # 1) Разбор запроса (Matcher — ленивая глобаль, не строим на каждый вызов).
    nums = grammar.parse_query(query)
    try:
        ents = _get_matcher().match(query)
    except Exception:
        ents = []
    intent = _detect_intent(query, nums)

    # 1a) Фильтры: пользовательские (filters) + временной из текста запроса.
    temporal_lo = parse_temporal(query)
    nf = _norm_filters(filters, temporal_lo=temporal_lo)
    # Окно свежести q_pgm: узкое при временном фильтре, иначе «все» (50 лет).
    pgm_years = ((_current_year() - int(nf["year_lo"]))
                 if (nf and nf.get("year_lo") is not None) else 50)

    drv = _connect()

    # 2) Числовая дорожка + эталонные запросы графа.
    numeric_facts = []
    if drv is not None:
        try:
            if nums:
                numeric_facts.extend(_numeric_track(drv, nums))
            numeric_facts.extend(_graph_shortcuts(drv, query, nums,
                                                  pgm_years=pgm_years))
        except Exception:
            pass

    # 2b) Entity-facts: естественный запрос без чисел, но газетир нашёл сущности —
    # топ-факты графа по этим canon (числовые факты с цитатами, source='граф').
    # Поисковые интенты: search + LLM-варианты lookup/explain/compare (LLM зовёт
    # «температура обжига концентрата» lookup-ом — дорожка обязана сработать).
    _searchlike = intent not in ("expert", "listing", "numeric")
    if drv is not None and _searchlike and ents and not nums:
        try:
            numeric_facts.extend(_entity_facts_track(drv, ents))
        except Exception:
            pass

    # 3) Семантическая дорожка (опциональна).
    semantic_docs = _semantic_track(query)

    # 3a) Гейт вне-доменных запросов («рецепт борща»): нет чисел, газетир пуст,
    # ни одна графовая дорожка не дала фактов и семантический топ-score слаб —
    # честная пустая выдача вместо 8-10 нерелевантных доков (kw-fallback тоже
    # НЕ запускаем: для мусорного запроса он лишь досыпал бы шум).
    if (not nums) and (not ents) and (not numeric_facts):
        sem_top = max((float(d.get("score") or 0.0) for d in semantic_docs),
                      default=0.0)
        if sem_top < _OOD_SCORE_GATE:
            if drv is not None:
                try:
                    drv.close()
                except Exception:
                    pass
            answer_md = (f"## Результаты поиска: {query}\n\n"
                         "По запросу ничего не найдено в корпусе R&D.")
            log_event(logger, "query", role=role, intent=intent,
                      n_facts=0, n_docs=0, hidden=0, filters=bool(filters),
                      gated="out_of_domain",
                      ms=round((time.perf_counter() - _t0) * 1000, 1))
            return {
                "intent": intent,
                "answer_md": answer_md,
                "facts": [],
                "docs": [],
                "hidden_count": 0,
                "entities": ents,
                "experts": [],
                "filters_applied": nf,
                "recommendations": {"similar_cases": [],
                                    "adjacent_topics": [], "experts": []},
            }

    # 3b) Keyword-fallback по ВСЕМУ корпусу (src.kwindex): семантический индекс
    # покрывает лишь плотные доки (кап чанков) — журналы (kg=2) для него невидимы.
    # Если обе дорожки дали <3 доков, добираем полнотекстовым индексом лемм
    # (закрывает эталонный запрос 4 «закачка шахтных вод» — тема живёт в журналах).
    if len({d.get("doc_id") for d in semantic_docs}
           | {f.get("doc_id") for f in numeric_facts}) < 3:
        try:
            from src import kwindex
            kw_docs = [{"doc_id": h["doc_id"], "score": h["score"],
                        "source": "keyword"} for h in kwindex.search(query, k=8)]
            seen_ids = {d.get("doc_id") for d in semantic_docs}
            semantic_docs = semantic_docs + [d for d in kw_docs
                                             if d["doc_id"] not in seen_ids]
        except Exception:  # noqa: BLE001 — индекс не собран → дорожки как были
            pass

    # 4) Слияние по doc_id.
    docs, facts = _merge(numeric_facts, semantic_docs)

    # Обогатим факты годом из meta, если граф не дал.
    for f in facts:
        if f.get("year") in (None, "") and f.get("doc_id") in meta:
            f["year"] = meta[f["doc_id"]].get("year")

    # 4a) Фильтры выдачи (5 измерений ТЗ): реально режут и facts, и docs.
    docs, facts = _apply_filters(docs, facts, nf, meta)

    # 5) RBAC.
    docs, facts, hidden_count = _apply_rbac(docs, facts, role, meta)

    # 6) Ранжирование + дедуп фактов (in_range первыми, ближе к цели — выше,
    # осмысленные цитаты выше табличного мусора).
    target = _target_value(nums)
    facts = _rank_facts(facts, target)
    facts = _dedup_facts(facts)
    # Отбраковать физически невозможные значения (−273 °C, 14100 %) и ложные
    # факты-сравнения («на 400 °C ниже, чем хром» → не «температура 400 °C»).
    facts = [f for f in facts if not _implausible(f) and not _comparison_artifact(f)]
    # Причесать провенанс-цитаты (табличная разметка/повторы шапок).
    for f in facts:
        if f.get("quote"):
            f["quote"] = _clean_quote(f["quote"])
    # Кэп ОТОБРАЖЕНИЯ: не грузить браузер сотнями строк (было до 275 фактов).
    # Полные facts/docs остаются для аналитики (сравнение/рекомендации/эксперты) —
    # иначе кэп лишил бы сравнение обеих сторон RU/мир.
    _FACTS_CAP, _DOCS_CAP = 40, 24
    total_facts = len(facts)
    disp_facts = facts[:_FACTS_CAP]
    disp_docs = docs[:_DOCS_CAP]

    # 7) Экспертная дорожка (intent=='expert'): по ПОЛНЫМ докам (больше авторов).
    experts = _expert_track(docs, facts, meta, drv) if intent == "expert" else None

    # 8) Превью документов для секции 'Релевантные документы'.
    previews = _load_previews([d.get("doc_id") for d in disp_docs[:15]])

    # 9) Композер — по УРЕЗАННЫМ спискам (отображение).
    answer_md = compose_answer(query, disp_facts, disp_docs, hidden_count,
                               meta=meta, previews=previews, experts=experts)

    # 10) Рекомендации — по ПОЛНЫМ (аналитика, граф ещё открыт).
    # НЕ приписываем в answer_md: смежные темы/эксперты/похожие показаны фронтом
    # структурно (чипы/карточки). Дублировать текстом → «простыня».
    recommendations = build_recommendations(drv, ents, docs, facts,
                                            semantic_docs, meta)

    # 11) Сравнительная таблица «отеч vs мир» — по ПОЛНЫМ фактам.
    comp_md = _comparative_md(facts, meta)
    if comp_md:
        answer_md = f"{answer_md}\n\n{comp_md}"

    # Честная пометка, если выдача была урезана кэпом.
    if total_facts > len(disp_facts):
        answer_md += (f"\n\n_Показаны {len(disp_facts)} наиболее релевантных фактов "
                      f"из {total_facts}. Уточните запрос, чтобы сузить выдачу._")
    facts, docs = disp_facts, disp_docs  # наружу — урезанные

    if drv is not None:
        try:
            drv.close()
        except Exception:
            pass

    log_event(logger, "query", role=role, intent=intent,
              n_facts=len(facts), n_docs=len(docs),
              hidden=hidden_count, filters=bool(filters),
              ms=round((time.perf_counter() - _t0) * 1000, 1))

    return {
        "intent": intent,
        "answer_md": answer_md,
        "facts": facts,
        "docs": docs,
        "hidden_count": hidden_count,
        "entities": ents,
        "experts": experts or [],
        "filters_applied": nf,
        "recommendations": recommendations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Обзор литературы (literature review).
# ─────────────────────────────────────────────────────────────────────────────
def literature_review(query: str, role: str = "researcher", filters=None) -> str:
    """Markdown-обзор: факты выдачи + аналитика консенсуса/разногласий/пробелов.

    Разделы:
      Методы   — что и чем измеряют (метрика × сущность × единица);
      Режимы   — числовые диапазоны по группам;
      Консенсус    — сущности выдачи с VALIDATED_BY (N подтверждающих источников,
                     ср.confidence; группировка по методу/году/географии);
      Разногласия  — те же по CONTRADICTS (расхождение значений A vs B);
      Пробелы  — группы без чисел/цитат + только-RU/только-зарубеж сущности +
                 комбинации материал×процесс без фактов.
    Экстрактивно, без генерации.
    """
    res = search(query, role=role, filters=filters)
    facts = res.get("facts") or []
    docs = res.get("docs") or []
    ents = res.get("entities") or []
    meta = _load_meta()

    groups = {}  # (canon, unit) -> list[fact]
    for f in facts:
        key = (f.get("canon") or "—", f.get("unit") or "—")
        groups.setdefault(key, []).append(f)

    lines = [f"# Обзор литературы: {query}", ""]

    # Методы: перечень (метрика × сущность × единица).
    lines.append("## Методы")
    if not groups:
        lines.append("_Нет данных для обзора._")
    else:
        seen_methods = set()
        for (canon, unit), fs in sorted(groups.items()):
            metrics = sorted({(f.get("metric") or "—") for f in fs})
            for mt in metrics:
                mk = (canon, mt, unit)
                if mk in seen_methods:
                    continue
                seen_methods.add(mk)
                _u = unit_ru(unit) if unit and unit != "—" else "—"
                lines.append(f"- **{canon}**: {mt} ({_u}){_group_sources(fs, meta)}")
    lines.append("")

    # Режимы: числовые диапазоны по группам.
    lines.append("## Режимы")
    any_regime = False
    for (canon, unit), fs in sorted(groups.items()):
        valued = [f for f in fs if f.get("value_low") is not None
                  or f.get("value_high") is not None]
        if not valued:
            continue
        any_regime = True
        vals = []
        for f in valued:
            vals.append(_fmt_value(f))
        _u = unit_ru(unit) if unit and unit != "—" else "—"
        lines.append(f"- **{canon}** ({_u}): " + ", ".join(dict.fromkeys(vals))
                     + _group_sources(valued, meta))
    if not any_regime:
        lines.append("_Числовые режимы не выделены._")
    lines.append("")

    # Консенсус / Разногласия: по связям графа между документами выдачи.
    doc_ids = [d.get("doc_id") for d in docs if d.get("doc_id")]
    doc_ids += [f.get("doc_id") for f in facts if f.get("doc_id")]
    doc_ids = list(dict.fromkeys(doc_ids))
    drv = _connect()
    try:
        valid_rows = _rel_rows(drv, "VALIDATED_BY", doc_ids)
        contra_rows = _rel_rows(drv, "CONTRADICTS", doc_ids)
    finally:
        if drv is not None:
            try:
                drv.close()
            except Exception:
                pass

    doc_conf = _doc_confidence_map(facts)
    lines.append("## Консенсус")
    v_lines = _rel_group_lines(valid_rows, meta, doc_conf=doc_conf)
    if v_lines:
        lines.extend(v_lines)
    else:
        lines.append("_Подтверждающих связей (VALIDATED_BY) не найдено._")
    lines.append("")

    lines.append("## Разногласия")
    c_lines = _rel_group_lines(contra_rows, meta, doc_conf=doc_conf)
    if c_lines:
        lines.extend(c_lines)
    else:
        lines.append("_Противоречий (CONTRADICTS) не найдено._")
    lines.append("")

    # Пробелы: группы без чисел/цитат + охват (RU/зарубеж) + материал×процесс.
    lines.append("## Пробелы")
    gaps = []
    for (canon, unit), fs in sorted(groups.items()):
        has_value = any(f.get("value_low") is not None
                        or f.get("value_high") is not None for f in fs)
        has_quote = any((f.get("quote") or "").strip() for f in fs)
        _u = unit_ru(unit) if unit and unit != "—" else "—"
        if not has_value:
            gaps.append(f"- **{canon}** ({_u}): нет числовых значений")
        elif not has_quote:
            gaps.append(f"- **{canon}** ({_u}): нет подтверждающих цитат")

    only_ru, only_world, _geo_bound = _lang_geo_gaps(docs, facts, meta)
    combo = _combo_gaps(ents, facts)

    if gaps:
        lines.extend(gaps)
    if only_ru:
        lines.append("**Только отечественные источники:**")
        lines.extend(only_ru)
    if only_world:
        lines.append("**Только зарубежные источники:**")
        lines.extend(only_world)
    if combo:
        lines.append("**Комбинации материал×процесс без фактов:**")
        lines.extend(combo)
    if not (gaps or only_ru or only_world or combo):
        lines.append("_Явных пробелов не выявлено._")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Grounded-синтез рекомендации (LLM строго на извлечённых фактах, с цитатами).
# Извлечение остаётся детерминированным; здесь LLM ТОЛЬКО собирает ответ из наших
# проверенных фактов и обязан ссылаться на каждый тезис — провенанс сохраняется.
# ─────────────────────────────────────────────────────────────────────────────
_RECOMMEND_SYS = (
    "Ты — инженер-металлург R&D «Норникеля». Отвечай на инженерный вопрос СТРОГО и "
    "ТОЛЬКО на основе приведённого списка проверенных фактов из корпуса. "
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО: выдумывать методы, значения, источники или выводы, "
    "которых нет в фактах. Каждый содержательный тезис подкрепляй ссылкой вида [N] "
    "на номер факта из списка (можно несколько: [3][7]). Если фактов недостаточно "
    "для обоснованной рекомендации — честно напиши, чего не хватает, и не придумывай. "
    "Различай отечественную и зарубежную практику, если это видно из фактов. "
    "Пиши по-русски, кратко и по делу. Формат (markdown):\n\n"
    "## Рекомендация\n<1–3 фразы прямого ответа на вопрос>\n\n"
    "## Обоснование\n- <метод/решение/режим> — <числа с единицами> [N]\n\n"
    "## Сравнение вариантов\n<таблица или список: варианты, их значения, плюсы/минусы; "
    "если сравнивать нечего — «данных для сравнения недостаточно»>\n\n"
    "## Ограничения и уверенность\n- <пробелы в данных, применимость, уровень "
    "уверенности: высокая/средняя/низкая и почему>"
)


def recommend(query: str, role: str = "researcher", filters=None) -> dict:
    """Grounded-рекомендация: retrieval (детерминированный) → LLM-синтез на фактах.

    Возвращает {markdown, grounded: bool}. При отсутствии LLM/фактов честно
    деградирует на обычный экстрактивный ответ (grounded=False).
    """
    res = search(query, role=role, filters=filters)
    facts = res.get("facts") or []
    docs = res.get("docs") or []
    meta = _load_meta()

    if not facts:
        return {"markdown": res.get("answer_md")
                or f"## {query}\n\nВ корпусе не найдено релевантных фактов для рекомендации.",
                "grounded": False}

    # Компактный нумерованный контекст: только проверенные факты + провенанс.
    ctx = []
    used = facts[:28]
    for i, f in enumerate(used, 1):
        did = f.get("doc_id") or ""
        src = ((meta.get(did) or {}).get("src")) or did or "источник"
        yr = f.get("year")
        yr = str(yr) if yr not in (None, "") else "б.г."
        val = _fmt_value(f)
        unit = unit_ru(f.get("unit")) if f.get("unit") else ""
        quote = _clean_quote(f.get("quote") or "")[:220]
        geo = (meta.get(did) or {}).get("geo") or ""
        ctx.append(
            f"[{i}] {f.get('canon') or '—'} · {f.get('metric') or 'значение'}: "
            f"{val} {unit} [{f.get('phase') or '—'}] {('('+geo+')') if geo else ''} "
            f"— «{quote}» (ист.: {src}, {yr})".strip())
    context = "\n".join(ctx)
    user = (f"Вопрос: {query}\n\nПроверенные факты из корпуса "
            f"(нумерация — для ссылок [N]):\n{context}")

    try:
        from src import llm
        from src import config as _cfg
        answer = llm.chat(
            [{"role": "system", "content": _RECOMMEND_SYS},
             {"role": "user", "content": user}],
            model=_cfg.CHAT_STRONG, temperature=0, max_tokens=1400, timeout=90)
    except Exception:  # noqa: BLE001 — нет LLM/сеть упала → честная деградация
        return {"markdown": res.get("answer_md") or "", "grounded": False}

    # Ссылки [N] → кликабельные на первоисточник (сохраняем верифицируемость).
    def _linkref(m):
        n = int(m.group(1))
        if 1 <= n <= len(used):
            did = used[n - 1].get("doc_id") or ""
            return f"[[{n}]](/sources?doc={did})" if did else m.group(0)
        return m.group(0)
    answer = re.sub(r"\[(\d+)\]", _linkref, answer)

    # Легенда источников: [N] → название документа (для сверки жюри).
    legend = ["", "---", "**Источники:**", ""]
    for i, f in enumerate(used, 1):
        did = f.get("doc_id") or ""
        src = ((meta.get(did) or {}).get("src")) or did
        yr = f.get("year"); yr = str(yr) if yr not in (None, "") else "б.г."
        legend.append(f"{i}. [{src} ({yr})](/sources?doc={did})")
    answer = answer.rstrip() + "\n" + "\n".join(legend)

    return {"markdown": answer, "grounded": True}


if __name__ == "__main__":  # ручная проба
    out = search("методы обессоливания сульфаты не более 300 мг/л")
    print("intent:", out["intent"])
    print("facts:", len(out["facts"]), "docs:", len(out["docs"]),
          "hidden:", out["hidden_count"])
    print(out["answer_md"][:800])
