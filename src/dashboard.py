"""ТЗ доп.: дашборды для руководителей + сценарии сравнительного анализа.

«Дашборды для руководителей: метрики покрытия по направлениям, активность команд,
зоны риска (мало источников / противоречия)» и «таблицы сравнения технологий:
эффективность, CAPEX, применимость в холодном климате, экологические ограничения».

Все функции возвращают ДАННЫЕ (dict/list), не рендерят UI — рендер (Streamlit/HTML/
PDF) остаётся вызывающему (app.py). Чистый Cypher поверх neo4j driver
(src.graph.driver), без ORM и лишних абстракций.

Опора на РЕАЛЬНУЮ схему графа (проверено на живой БД):
  Document -[:MENTIONS]-> Domain|Expert|Condition   (покрытие по направлениям)
  Document -[:HAS_PARAM]-> Parameter                 (факты дока)
  Parameter -[:MEASURES]-> Process|Material          (факт о процессе/материале)
  Process -[:OPERATES_AT_CONDITION]-> Condition      (холодный климат/Заполярье…)
  Expert  <-[:IN_DOMAIN]-  (Expert)  →  Domain        (экспертиза по направлению)
  Material -[:AUTHORED_BY]-> Expert                   (авторство → активность)
  (Document)-[:CONTRADICTS {kind,metric,entity,val_a,val_b,unit,…}]->(Document)
  Parameter.extracted_at, Document.year               (свежесть/активность)
  Document.geo ∈ {RU, WORLD, <страна>}                (отеч. vs зарубеж.)

Сравнение технологий (compare_technologies): строки — процессы, колонки — оси.
Заполняем тем, что ЕСТЬ (Parameter по процессу + operates_at_condition); отсутствующее
поле — None (честно, не выдумываем). CAPEX в графе как класс сущности отсутствует —
ось capex = None везде, помечена как unavailable в meta.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import unit_ru


# ─────────────────────────────────────────────────────────────────────────────
# Хелперы.
# ─────────────────────────────────────────────────────────────────────────────
# sum(CASE…)/count(CASE…) над строками с null дают безобидный варнинг
# «null value eliminated in set function» — результат корректен. Глушим его на
# уровне сессии, чтобы не зашумлять вывод дашборда.
def _session(drv):
    try:
        return drv.session(notifications_min_severity="OFF")
    except TypeError:  # старый драйвер без параметра — обычная сессия
        return drv.session()


def _rows(drv, cy, **params):
    with _session(drv) as s:
        return [dict(r) for r in s.run(cy, **params)]


def _one(drv, cy, **params):
    with _session(drv) as s:
        rec = s.run(cy, **params).single()
        return dict(rec) if rec is not None else {}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Верхнеуровневые KPI.
# ─────────────────────────────────────────────────────────────────────────────
def summary_metrics(drv) -> dict:
    """KPI: docs, факты, эксперты, домены, противоречия, доля RU/WORLD,
    доля документов, покрытых хотя бы одним фактом."""
    d = _one(drv, """
        MATCH (doc:Document)
        WITH count(DISTINCT doc) AS docs
        MATCH (p:Parameter)
        WITH docs, count(p) AS facts
        OPTIONAL MATCH (e:Expert)
        WITH docs, facts, count(DISTINCT e) AS experts
        OPTIONAL MATCH (dom:Domain)
        WITH docs, facts, experts, count(DISTINCT dom) AS domains
        OPTIONAL MATCH ()-[c:CONTRADICTS]->()
        RETURN docs, facts, experts, domains, count(c) AS contradictions
    """)
    geo = _one(drv, """
        MATCH (doc:Document)
        RETURN
          sum(CASE WHEN doc.geo = 'RU' THEN 1 ELSE 0 END)    AS ru,
          sum(CASE WHEN doc.geo = 'WORLD' THEN 1 ELSE 0 END) AS world,
          sum(CASE WHEN doc.geo IS NULL THEN 1 ELSE 0 END)   AS geo_unknown,
          count(*) AS total
    """)
    covered = _one(drv, """
        MATCH (doc:Document)
        OPTIONAL MATCH (doc)-[:HAS_PARAM]->(p:Parameter)
        WITH doc, count(p) AS n
        RETURN sum(CASE WHEN n > 0 THEN 1 ELSE 0 END) AS with_facts,
               count(doc) AS total
    """)
    total = geo.get("total") or 0
    with_facts = covered.get("with_facts") or 0
    cov_total = covered.get("total") or 0
    return {
        "docs": d.get("docs", 0),
        "facts": d.get("facts", 0),
        "experts": d.get("experts", 0),
        "domains": d.get("domains", 0),
        "contradictions": d.get("contradictions", 0),
        "ru": geo.get("ru", 0),
        "world": geo.get("world", 0),
        "geo_unknown": geo.get("geo_unknown", 0),
        "ru_share": round((geo.get("ru", 0) / total), 3) if total else None,
        "world_share": round((geo.get("world", 0) / total), 3) if total else None,
        "docs_with_facts": with_facts,
        "fact_coverage": round((with_facts / cov_total), 3) if cov_total else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Покрытие по направлениям (Domain).
# ─────────────────────────────────────────────────────────────────────────────
def coverage_by_domain(drv) -> list:
    """По каждому Domain: сколько документов его упоминают (MENTIONS), сколько
    фактов в этих документах (HAS_PARAM), сколько экспертов закреплено (IN_DOMAIN)."""
    return _rows(drv, """
        MATCH (dom:Domain)
        OPTIONAL MATCH (doc:Document)-[:MENTIONS]->(dom)
        WITH dom, collect(DISTINCT doc) AS docs
        OPTIONAL MATCH (d2:Document)-[:MENTIONS]->(dom), (d2)-[:HAS_PARAM]->(p:Parameter)
        WITH dom, docs, count(p) AS facts
        OPTIONAL MATCH (e:Expert)-[:IN_DOMAIN]->(dom)
        RETURN dom.canon AS domain,
               size(docs) AS documents,
               facts AS facts,
               count(DISTINCT e) AS experts
        ORDER BY documents DESC, facts DESC
    """)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Покрытие по годам и по географии.
# ─────────────────────────────────────────────────────────────────────────────
def coverage_by_year(drv) -> list:
    """По году публикации: число документов и фактов. Год = null → бакет 'н/д'."""
    return _rows(drv, """
        MATCH (doc:Document)
        OPTIONAL MATCH (doc)-[:HAS_PARAM]->(p:Parameter)
        WITH doc.year AS year, count(DISTINCT doc) AS documents, count(p) AS facts
        RETURN year, documents, facts
        ORDER BY year IS NULL, year
    """)


def coverage_by_geo(drv) -> list:
    """Отечественная vs мировая практика (Document.geo): документы и факты по гео.
    RU/WORLD плюс явные страны/регионы; null → 'н/д'."""
    return _rows(drv, """
        MATCH (doc:Document)
        OPTIONAL MATCH (doc)-[:HAS_PARAM]->(p:Parameter)
        WITH coalesce(doc.geo, 'н/д') AS geo,
             count(DISTINCT doc) AS documents, count(p) AS facts
        RETURN geo, documents, facts
        ORDER BY documents DESC
    """)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Зоны риска.
# ─────────────────────────────────────────────────────────────────────────────
def risk_zones(drv, min_sources: int = 2, limit: int = 20) -> dict:
    """{low_sources, contradictions, only_ru, only_world}.

    low_sources    — сущности (Process/Material/Equipment), про которые факты есть
                     менее чем в `min_sources` документах (тонкое покрытие).
    contradictions — топ CONTRADICTS-пар документов (сущность/метрика/значения).
    only_ru        — процессы, встречающиеся ТОЛЬКО в отечественных документах.
    only_world     — процессы, встречающиеся ТОЛЬКО в зарубежных документах.
    """
    low_sources = _rows(drv, """
        MATCH (p:Parameter)-[:MEASURES]->(e)
        WHERE e:Process OR e:Material OR e:Equipment
        WITH e, count(DISTINCT p.doc_id) AS sources
        WHERE sources < $min_sources
        RETURN e.canon AS entity, labels(e)[0] AS type, sources
        ORDER BY sources ASC, entity
        LIMIT $limit
    """, min_sources=min_sources, limit=limit)

    contradictions = _rows(drv, """
        MATCH (a:Document)-[c:CONTRADICTS]->(b:Document)
        RETURN a.doc_id AS doc_a, b.doc_id AS doc_b,
               c.entity AS entity, c.metric AS metric, c.phase AS phase,
               c.unit AS unit, c.val_a AS val_a, c.val_b AS val_b, c.kind AS kind
        ORDER BY c.kind, entity
        LIMIT $limit
    """, limit=limit)

    # Процессы только в отеч. / только в зарубеж. практике (пробел покрытия).
    geo_split = _rows(drv, """
        MATCH (p:Parameter)-[:MEASURES]->(e:Process)
        MATCH (doc:Document {doc_id: p.doc_id})
        WITH e.canon AS proc,
             sum(CASE WHEN doc.geo = 'RU' THEN 1 ELSE 0 END)    AS ru,
             sum(CASE WHEN doc.geo = 'WORLD' THEN 1 ELSE 0 END) AS world
        RETURN proc, ru, world
    """)
    only_ru = sorted(r["proc"] for r in geo_split if r["ru"] > 0 and r["world"] == 0)
    only_world = sorted(r["proc"] for r in geo_split if r["world"] > 0 and r["ru"] == 0)

    return {
        "low_sources": low_sources,
        "contradictions": contradictions,
        "only_ru": only_ru[:limit],
        "only_world": only_world[:limit],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Активность (свежие документы / эксперты).
# ─────────────────────────────────────────────────────────────────────────────
def activity(drv, limit: int = 20) -> list:
    """Свежие документы: сортировка по году (свежие сверху), затем по времени
    извлечения фактов (extracted_at) — прокси активности загрузки/команд."""
    return _rows(drv, """
        MATCH (doc:Document)
        OPTIONAL MATCH (doc)-[:HAS_PARAM]->(p:Parameter)
        OPTIONAL MATCH (doc)-[:MENTIONS]->(e:Expert)
        RETURN doc.doc_id AS doc_id, doc.year AS year, doc.geo AS geo,
               count(DISTINCT p) AS facts,
               count(DISTINCT e) AS experts,
               max(p.extracted_at) AS last_extracted
        ORDER BY year IS NULL, year DESC, last_extracted DESC
        LIMIT $limit
    """, limit=limit)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Покрытие экспертами.
# ─────────────────────────────────────────────────────────────────────────────
def expert_coverage(drv, limit: int = 20) -> list:
    """Топ Expert по числу документов, где встречается (MENTIONS), и по числу
    закреплённых направлений (IN_DOMAIN) — носители компетенций."""
    return _rows(drv, """
        MATCH (e:Expert)
        // Только русская ФИО: без латиницы (China P.R./Treasury U.S.) и без
        // ролей/гео/организаций (Санкт-Петербург, Федерация, редакторы).
        WHERE NOT e.canon =~ '.*[A-Za-z].*'
          AND NOT e.canon =~ '(?iu).*(редактор|редакция|рецензент|специалист|директор|начальник|заведующий|федерация|республика|институт|университет|санкт|петербург|москва|россия|область).*'
        // Отсечь административную над-атрибуцию: подписант >15 отчётов (директор/
        // завотделом из «Списка исполнителей») — не топик-эксперт.
        OPTIONAL MATCH (e)<-[:AUTHORED_BY]-(ad:Document)
        WITH e, count(DISTINCT ad) AS authored
        WHERE authored <= 15
        OPTIONAL MATCH (doc:Document)-[:MENTIONS]->(e)
        WITH e, count(DISTINCT doc) AS documents
        OPTIONAL MATCH (e)-[:IN_DOMAIN]->(dom:Domain)
        RETURN e.canon AS expert, documents,
               count(DISTINCT dom) AS domains,
               collect(DISTINCT dom.canon) AS domain_list
        ORDER BY documents DESC, domains DESC
        LIMIT $limit
    """, limit=limit)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Сравнение технологий (процессов) по осям.
# ─────────────────────────────────────────────────────────────────────────────
# Оси сравнения. capex отсутствует в графе как класс сущности — честно None.
COMPARE_AXES = ["efficiency_pct", "energy", "temperature_c",
                "cold_climate", "ecology", "capex"]

# Метрики-эффективность (извлечение/выход, %). Метрики-энергозатраты.
_EFF_METRIC_HINTS = ("извлеч", "выход", "степень")
_ENERGY_METRIC_HINTS = ("энерг", "расход электро", "удельн")
_ENERGY_UNITS = ("A_m2",)  # плотность тока — прокси энергозатрат электропроцессов
_COLD_CONDITIONS = ("холодный климат", "Заполярье")
_ECOLOGY_HINTS = ("выброс", "sO2", "so2", "co2", "пыл", "стоки", "шлам", "отвал",
                  "экологи", "загрязн")


def _agg_number(rows):
    """Список фактов → (min, max, unit, n). Берём числовые границы value_low/high."""
    vals, unit, n = [], None, 0
    for r in rows:
        lo, hi = r.get("value_low"), r.get("value_high")
        for v in (lo, hi):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        if r.get("unit_canon"):
            unit = r["unit_canon"]
        n += 1
    if not vals:
        return None
    return {"min": min(vals), "max": max(vals), "unit": unit,
            "unit_ru": unit_ru(unit), "samples": n}


def compare_technologies(drv, process_canons: list) -> dict:
    """Таблица сравнения процессов по осям (COMPARE_AXES).

    Строки — процессы из `process_canons`. Колонки:
      efficiency_pct — извлечение/выход (%), агрегат по фактам процесса;
      energy         — энергозатраты (метрика 'энерг…' или плотность тока А/м²);
      temperature_c  — температурный режим (°C);
      cold_climate   — применим ли в холодном климате (OPERATES_AT_CONDITION к
                       'холодный климат'/'Заполярье') → bool;
      ecology        — есть ли экологические факты/ограничения (выбросы/стоки/…);
      capex          — None (в графе нет; помечено в meta.unavailable).

    Отсутствующая ось у процесса → None (честно). Возвращает
    {axes, meta, rows:[{process, efficiency_pct, energy, temperature_c,
                        cold_climate, ecology, capex, sources}]}.
    """
    process_canons = [c for c in (process_canons or []) if c]
    result_rows = []
    for canon in process_canons:
        facts = _rows(drv, """
            MATCH (p:Parameter)-[:MEASURES]->(e:Process {canon: $canon})
            RETURN p.metric AS metric, p.unit_canon AS unit_canon,
                   p.value_low AS value_low, p.value_high AS value_high,
                   p.doc_id AS doc_id
        """, canon=canon)

        def _pick(pred):
            return _agg_number([f for f in facts if pred(f)])

        def _m_has(f, hints):
            m = (f.get("metric") or "").lower()
            return any(h in m for h in hints)

        efficiency = _pick(lambda f: f.get("unit_canon") == "pct"
                           and _m_has(f, _EFF_METRIC_HINTS))
        energy = _pick(lambda f: _m_has(f, _ENERGY_METRIC_HINTS)
                       or f.get("unit_canon") in _ENERGY_UNITS)
        temperature = _pick(lambda f: f.get("unit_canon") == "degC")

        # Экология: факт с эко-метрикой ИЛИ упоминание эко-условия документом.
        eco_fact = _pick(lambda f: _m_has(f, _ECOLOGY_HINTS))
        ecology = eco_fact if eco_fact else None

        cold = _one(drv, """
            MATCH (e:Process {canon: $canon})-[:OPERATES_AT_CONDITION]->(c:Condition)
            RETURN count(CASE WHEN c.canon IN $cold THEN 1 END) AS n,
                   collect(DISTINCT c.canon) AS conds
        """, canon=canon, cold=list(_COLD_CONDITIONS))
        cold_climate = bool(cold.get("n"))

        sources = len({f["doc_id"] for f in facts if f.get("doc_id")})
        result_rows.append({
            "process": canon,
            "efficiency_pct": efficiency,
            "energy": energy,
            "temperature_c": temperature,
            "cold_climate": cold_climate,
            "ecology": ecology,
            "capex": None,
            "conditions": cold.get("conds", []),
            "sources": sources,
            "total_facts": len(facts),
        })
    return {
        "axes": COMPARE_AXES,
        "meta": {
            "unavailable": ["capex"],  # нет как класс сущности в графе (честно)
            "note": "capex отсутствует в корпусе; оси заполнены тем, что есть в графе",
        },
        "rows": result_rows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-check: если Neo4j доступен — прогон всех функций; иначе SKIP без падения.
# ─────────────────────────────────────────────────────────────────────────────
def _top_processes(drv, k: int = 2) -> list:
    """k самых частых процессов (по числу фактов) — для демонстрации сравнения."""
    return [r["canon"] for r in _rows(drv, """
        MATCH (p:Parameter)-[:MEASURES]->(e:Process)
        RETURN e.canon AS canon, count(*) AS c
        ORDER BY c DESC LIMIT $k
    """, k=k)]


def main() -> int:
    from src import graph
    try:
        drv = graph.driver(retry_seconds=6)
    except Exception as e:  # noqa: BLE001 — Neo4j недоступен
        print(f"SKIP: Neo4j недоступен ({e.__class__.__name__}) — self-check пропущен")
        return 0
    try:
        sm = summary_metrics(drv)
        assert sm and sm.get("docs", 0) > 0, "summary_metrics пустой"
        print("summary_metrics:", sm)

        cov_dom = coverage_by_domain(drv)
        cov_year = coverage_by_year(drv)
        cov_geo = coverage_by_geo(drv)
        print(f"coverage: {len(cov_dom)} доменов, {len(cov_year)} лет, {len(cov_geo)} гео")

        rz = risk_zones(drv)
        print("risk_zones:", {k: len(v) for k, v in rz.items()})

        act = activity(drv)
        exp = expert_coverage(drv)
        print(f"activity: {len(act)} доков, expert_coverage: {len(exp)} экспертов")

        procs = _top_processes(drv, 2)
        cmp = compare_technologies(drv, procs)
        assert len(cmp["rows"]) == len(procs) and len(cmp["rows"]) >= 2, \
            "compare_technologies не дал строк для 2 частых процессов"
        print(f"compare_technologies по {procs}:")
        for row in cmp["rows"]:
            print("  ", {k: row[k] for k in ("process", "efficiency_pct",
                  "temperature_c", "cold_climate", "sources")})
        print("OK: все функции отработали, ассерты прошли")
        return 0
    finally:
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
