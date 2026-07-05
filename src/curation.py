"""Ручная корректировка графа экспертами (ТЗ §72: уточнение связей, комментарии,
фиксация изменений с автором и датой).

Правки применяются к узлам Parameter (числовой факт с провенансом). Каждая правка
штампует edited_by/edited_at/edit_comment и флаг manually_edited=true — лента аудита
собирается в edit_history. Удаление мягкое (deleted=true), чтобы правка была
обратима и не рвала связи графа (никакого DETACH DELETE).

Прямые Cypher, без ORM (ponytail). Все функции принимают neo4j driver первым арг.
Ключ факта (param_key) — dict; поддерживаются две формы адресации:
  {"doc_id":..., "canon":..., "metric":...}  — доменный ключ (может дать >1 узла)
  {"id": <elementId>}                          — точный узел по elementId
"""
from __future__ import annotations
import os, sys, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import nfc


def _iso_now() -> str:
    return datetime.datetime.now().isoformat()


def _match_clause(param_key: dict):
    """Строит MATCH-паттерн + параметры по param_key.

    Возвращает (cypher_match, params). Форма {"id": elementId} адресует точный
    узел; иначе фильтр по doc_id/canon/metric (все переданные ключи — AND).
    Всегда исключаем уже мягко-удалённые узлы из выборки на правку/чтение-до.
    """
    if param_key.get("id"):
        return ("MATCH (p:Parameter) WHERE elementId(p) = $__id",
                {"__id": param_key["id"]})
    conds, params = [], {}
    for k in ("doc_id", "canon", "metric"):
        if param_key.get(k) is not None:
            conds.append(f"p.{k} = ${k}")
            params[k] = nfc(param_key[k])
    if not conds:
        raise ValueError("param_key должен содержать 'id' или хотя бы одно из "
                         "doc_id/canon/metric")
    return ("MATCH (p:Parameter) WHERE " + " AND ".join(conds), params)


def _snapshot(p) -> dict:
    """Снимок редактируемых полей узла Parameter для before/after."""
    return {
        "value_low": p.get("value_low"), "value_high": p.get("value_high"),
        "edited_by": p.get("edited_by"), "edited_at": p.get("edited_at"),
        "edit_comment": p.get("edit_comment"),
        "manually_edited": p.get("manually_edited"),
        "deleted": p.get("deleted"),
    }


def edit_fact(driver, param_key: dict, new_value: float, editor: str,
              comment: str = "") -> dict:
    """Найти Parameter по ключу и записать new_value в оба края диапазона
    (value_low=value_high=new_value), проставив провенанс правки.

    Возвращает {ok, before, after} для первого совпавшего узла (или ok=False,
    если не найдено). Правит ВСЕ совпавшие узлы, но снимки before/after —
    по первому (при адресации по elementId он единственный)."""
    match, params = _match_clause(param_key)
    now = _iso_now()
    cy = f"""
    {match}
    WITH p, p {{ .* }} AS before
    SET p.value_low = $new_value, p.value_high = $new_value,
        p.edited_by = $editor, p.edited_at = $now,
        p.edit_comment = $comment, p.manually_edited = true
    RETURN before AS before, p {{ .* }} AS after
    """
    params.update(new_value=float(new_value), editor=editor, now=now, comment=comment)
    with driver.session() as s:
        rows = [r for r in s.run(cy, **params)]
    if not rows:
        return {"ok": False, "before": None, "after": None}
    r = rows[0]
    return {"ok": True, "before": _snapshot(r["before"]),
            "after": _snapshot(r["after"]), "affected": len(rows)}


def add_fact(driver, doc_id, canon, metric, value, unit, editor) -> dict:
    """Создать Parameter вручную (+ Document, + MEASURES-сущность, + HAS_PARAM/
    DESCRIBED_IN), пометив source='manual' и провенанс правки.

    Возвращает {ok, id, node}. pkey с префиксом manual|, чтобы ручной факт не
    схлопывался MERGE-ем пайплайна с автоматическим."""
    doc_id, canon, metric, unit = nfc(doc_id), nfc(canon), nfc(metric), nfc(unit)
    now = _iso_now()
    v = float(value)
    pkey = "manual|" + "|".join(str(x) for x in (doc_id, canon, metric, unit, v, now))
    cy = """
    MERGE (d:Document {doc_id: $doc_id})
    MERGE (p:Parameter {pkey: $pkey})
    SET p:Property,
        p.value_low = $v, p.value_high = $v, p.unit_canon = $unit,
        p.metric = $metric, p.canon = $canon, p.doc_id = $doc_id,
        p.source = 'manual', p.manually_edited = true,
        p.edited_by = $editor, p.edited_at = $now, p.version = 1
    MERGE (d)-[:HAS_PARAM]->(p)
    MERGE (p)-[:DESCRIBED_IN]->(d)
    MERGE (e:Material {canon: $canon}) ON CREATE SET e.name = $canon
    MERGE (d)-[:MENTIONS]->(e)
    MERGE (p)-[:MEASURES]->(e)
    RETURN elementId(p) AS id, p { .* } AS node
    """
    with driver.session() as s:
        r = s.run(cy, doc_id=doc_id, pkey=pkey, v=v, unit=unit, metric=metric,
                  canon=canon, editor=editor, now=now).single()
    return {"ok": True, "id": r["id"], "node": dict(r["node"])}


def delete_fact(driver, param_key: dict, editor: str, reason: str) -> dict:
    """Мягкое удаление: SET p.deleted=true + провенанс. НЕ DETACH DELETE —
    факт остаётся в графе (обратимо), просто помечен удалённым.

    Возвращает {ok, deleted: <кол-во>}."""
    match, params = _match_clause(param_key)
    now = _iso_now()
    cy = f"""
    {match}
    SET p.deleted = true, p.edited_by = $editor, p.edited_at = $now,
        p.delete_reason = $reason, p.manually_edited = true
    RETURN count(p) AS n
    """
    params.update(editor=editor, now=now, reason=reason)
    with driver.session() as s:
        n = s.run(cy, **params).single()["n"]
    return {"ok": n > 0, "deleted": n}


def edit_history(driver, limit: int = 50) -> list:
    """Лента аудита: все вручную-правленные/удалённые Parameter с автором, датой,
    комментарием/причиной. Сортировка по edited_at (свежие сверху)."""
    cy = """
    MATCH (p:Parameter)
    WHERE p.manually_edited = true OR p.deleted = true
    RETURN elementId(p) AS id, p.doc_id AS doc_id, p.canon AS canon,
           p.metric AS metric, p.value_low AS value_low, p.value_high AS value_high,
           p.unit_canon AS unit, p.source AS source,
           p.edited_by AS editor, p.edited_at AS edited_at,
           coalesce(p.edit_comment, p.delete_reason) AS comment,
           coalesce(p.deleted, false) AS deleted
    ORDER BY p.edited_at DESC
    LIMIT $limit
    """
    with driver.session() as s:
        return [dict(r) for r in s.run(cy, limit=int(limit))]


# ---------------------------------------------------------------------------
# Self-check: если Neo4j доступен — полный цикл add→edit→delete→history на
# временном doc_id, затем очистка. Если недоступен — skip (не падать).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from src.config import NEO4J_URI, NEO4J_USER, NEO4J_PASS
    try:
        import neo4j
        drv = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        drv.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        print(f"[curation self-check] SKIP — Neo4j недоступен: {e}")
        sys.exit(0)

    DOC = f"__curation_selfcheck__{int(datetime.datetime.now().timestamp())}"
    try:
        # add
        a = add_fact(drv, DOC, "тест-никель", "содержание", 5.0, "pct", "expert_a")
        assert a["ok"] and a["node"]["source"] == "manual", a
        assert a["node"]["manually_edited"] is True, a
        assert a["node"]["edited_by"] == "expert_a" and a["node"]["edited_at"], a
        node_id = a["id"]

        # edit по elementId
        e = edit_fact(drv, {"id": node_id}, 7.5, "expert_b", comment="уточнение")
        assert e["ok"], e
        assert e["before"]["value_low"] == 5.0 and e["after"]["value_low"] == 7.5, e
        assert e["after"]["value_high"] == 7.5, e
        assert e["after"]["edited_by"] == "expert_b", e
        assert e["after"]["edit_comment"] == "уточнение", e
        assert e["after"]["manually_edited"] is True, e

        # edit по доменному ключу (doc_id+canon+metric)
        e2 = edit_fact(drv, {"doc_id": DOC, "canon": "тест-никель",
                             "metric": "содержание"}, 8.0, "expert_c")
        assert e2["ok"] and e2["after"]["value_low"] == 8.0, e2

        # history содержит наш факт
        h = edit_history(drv, limit=100)
        assert any(r["doc_id"] == DOC for r in h), "факт не попал в edit_history"
        mine = next(r for r in h if r["doc_id"] == DOC)
        assert mine["editor"] == "expert_c" and mine["deleted"] is False, mine

        # delete (мягко)
        d = delete_fact(drv, {"id": node_id}, "expert_d", reason="дубль")
        assert d["ok"] and d["deleted"] == 1, d

        # узел ещё существует (не DETACH DELETE), помечен deleted и в истории
        h2 = edit_history(drv, limit=100)
        mine2 = next(r for r in h2 if r["doc_id"] == DOC)
        assert mine2["deleted"] is True and mine2["editor"] == "expert_d", mine2

        print("[curation self-check] OK — add/edit(id+ключ)/delete/history проверены")
    finally:
        # очистка тестового факта (здесь DETACH DELETE уместен — это temp-данные)
        with drv.session() as s:
            s.run("MATCH (d:Document {doc_id:$doc})--(p:Parameter) DETACH DELETE p",
                  doc=DOC)
            s.run("MATCH (d:Document {doc_id:$doc}) DETACH DELETE d", doc=DOC)
        drv.close()
