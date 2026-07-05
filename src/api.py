"""HTTP-адаптер (FastAPI) над ядром «Научного клубка» — контракт для фронтенда.

Тонкая обёртка: каждый эндпоинт делегирует уже существующей и протестированной
функции ядра (search/graph/dashboard/exports/curation/notify/grammar/gazetteer/
extract/pipeline). Бизнес-логику НЕ дублирует. Полный контракт — API.md.

Запуск:  uvicorn src.api:app --host 0.0.0.0 --port 8000
Doсs:    http://localhost:8000/docs  (OpenAPI авто-генерится FastAPI)

Роль передаётся заголовком `X-Role` (по умолчанию researcher). Neo4j недоступен →
503 на эндпоинтах, которым нужен граф; поиск/парсинг деградируют мягко (ядро само).
CORS открыт (dev) — фронт на другом origin.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import (FastAPI, APIRouter, Depends, Header, HTTPException, Query,
                     UploadFile, File)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import DOCS_META, DOCS_TEXT, nfc
from src import graph, search, dashboard, exports, curation, notify, grammar, gazetteer
from src import extract as extract_mod
from src import pipeline
from src.app import fetch_contradictions, read_audit, log_event
from src.obs import get_logger

log = get_logger("api")
app = FastAPI(title="Научный клубок — API", version="1.0",
              description="Граф знаний R&D по горно-металлургии. Контракт: API.md")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
api = APIRouter(prefix="/api")   # весь контракт под /api; /health — на корне для проб


@app.on_event("startup")
def _seed_admin_on_start():
    """Первый запуск: если пользователей нет — создать админа (src/auth.seed_admin)."""
    try:
        from src import auth
        msg = auth.seed_admin(_drv())
        if msg:
            log.warning("AUTH seed: %s", msg)
    except Exception as e:  # noqa: BLE001 — Neo4j недоступен на старте → сид позже
        log.warning("AUTH seed отложен: %s", str(e)[:100])

# ── общие ресурсы (ленивые синглтоны) ────────────────────────────────────────
_DRV = None
_GAZ = None
_MATCHER = None


def _drv():
    """Кэшированный neo4j.Driver. Недоступен → HTTP 503."""
    global _DRV
    if _DRV is None:
        try:
            _DRV = graph.driver(retry_seconds=8.0)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"Граф (Neo4j) недоступен: {type(e).__name__}")
    return _DRV


def _gaz():
    global _GAZ
    if _GAZ is None:
        _GAZ = gazetteer.build_gazetteer()
    return _GAZ


def _matcher():
    global _MATCHER
    if _MATCHER is None:
        _MATCHER = gazetteer.Matcher()
    return _MATCHER


def _role(x_role: str = Header(default="researcher", alias="X-Role")) -> str:
    return (x_role or "researcher").strip().lower()


def current_user(authorization: str = Header(default="", alias="Authorization"),
                 x_role: str = Header(default="", alias="X-Role")) -> dict:
    """Текущий пользователь: JWT (Bearer) авторитетен; иначе X-Role (dev-фолбэк);
    иначе researcher. Возвращает {username, role}."""
    from src import auth
    if authorization.lower().startswith("bearer "):
        payload = auth.decode_token(authorization[7:].strip())
        if payload:
            return {"username": payload.get("sub"), "role": payload.get("role", "researcher")}
    return {"username": None, "role": (x_role or "researcher").strip().lower()}


def admin_user(u: dict = Depends(current_user)) -> dict:
    """Зависимость: пускает только админа (управление пользователями)."""
    from src import auth
    if not auth.is_admin(u.get("role", "")):
        raise HTTPException(403, "требуется роль admin")
    return u


# ── тела запросов (pydantic → авто-OpenAPI для фронта) ────────────────────────
class SearchReq(BaseModel):
    query: str
    filters: dict | None = None


class ParseReq(BaseModel):
    text: str


class SubgraphReq(BaseModel):
    doc_ids: list[str]
    limit: int = 60


class NeighborhoodReq(BaseModel):
    entity_id: str
    depth: int = 2
    limit: int = 60


class CompareReq(BaseModel):
    processes: list[str]


class CurationEditReq(BaseModel):
    param_key: dict
    new_value: float
    editor: str
    comment: str = ""


class CurationAddReq(BaseModel):
    doc_id: str
    canon: str
    metric: str
    value: float
    unit: str
    editor: str


class CurationDeleteReq(BaseModel):
    param_key: dict
    editor: str
    reason: str


class SubReq(BaseModel):
    user: str
    query: str


class LoginReq(BaseModel):
    username: str
    password: str


class UserCreateReq(BaseModel):
    username: str
    password: str
    role: str
    full_name: str = ""


class UserUpdateReq(BaseModel):
    role: str | None = None
    password: str | None = None
    active: bool | None = None
    full_name: str | None = None


class PasswordReq(BaseModel):
    old_password: str
    new_password: str


# ═════════════════════════════ 0. Аутентификация ═════════════════════════════
@api.post("/auth/login")
def api_login(req: LoginReq):
    """Логин: username+password → JWT. Пароли bcrypt (src/auth). Логируется в аудит."""
    from src import auth
    u = auth.authenticate(_drv(), req.username, req.password)
    if not u:
        log_event(req.username or "?", "login_fail", {"username": req.username})
        raise HTTPException(401, "неверный логин или пароль")
    token = auth.issue_token(u["username"], u["role"])
    log_event(u["username"], "login", {"role": u["role"]})
    return {"token": token, "user": u}


@api.get("/auth/me")
def api_me(u: dict = Depends(current_user)):
    """Текущий пользователь (по JWT). Для проверки токена фронтом."""
    if not u.get("username"):
        return {"username": None, "role": u.get("role"), "authenticated": False}
    from src import auth
    full = auth.get_user(_drv(), u["username"]) or {}
    return {**full, "authenticated": True}


@api.post("/auth/change-password")
def api_change_password(req: PasswordReq, u: dict = Depends(current_user)):
    """Смена собственного пароля (нужен старый)."""
    from src import auth
    if not u.get("username"):
        raise HTTPException(401, "требуется вход")
    if not auth.authenticate(_drv(), u["username"], req.old_password):
        raise HTTPException(403, "старый пароль неверен")
    auth.update_user(_drv(), u["username"], password=req.new_password)
    return {"ok": True}


# ── управление пользователями (только admin) ─────────────────────────────────
@api.get("/users")
def api_users(admin: dict = Depends(admin_user)):
    from src import auth
    return auth.list_users(_drv())


@api.post("/users")
def api_user_create(req: UserCreateReq, admin: dict = Depends(admin_user)):
    from src import auth
    try:
        user = auth.create_user(_drv(), req.username, req.password, req.role,
                                created_by=admin["username"], full_name=req.full_name)
        log_event(admin["username"], "user_create",
                  {"username": req.username, "role": req.role})
        return user
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.patch("/users/{username}")
def api_user_update(username: str, req: UserUpdateReq, admin: dict = Depends(admin_user)):
    from src import auth
    try:
        user = auth.update_user(_drv(), username, role=req.role, password=req.password,
                                active=req.active, full_name=req.full_name)
        log_event(admin["username"], "user_update",
                  {"username": username, "role": req.role, "active": req.active})
        return user
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.delete("/users/{username}")
def api_user_delete(username: str, admin: dict = Depends(admin_user)):
    from src import auth
    if username == admin["username"]:
        raise HTTPException(400, "нельзя удалить самого себя")
    ok = auth.delete_user(_drv(), username)
    if not ok:
        raise HTTPException(404, "пользователь не найден")
    log_event(admin["username"], "user_delete", {"username": username})
    return {"ok": True}


# ═════════════════════════════ health ════════════════════════════════════════
@app.get("/health")
def health():
    try:
        n = _drv().session().run("MATCH (p:Parameter) RETURN count(p) AS c").single()["c"]
        return {"ok": True, "neo4j": True, "parameters": n}
    except HTTPException:
        return {"ok": True, "neo4j": False}


# ═════════════════════════════ 1. Поиск ══════════════════════════════════════
@api.post("/search")
def api_search(req: SearchReq, u: dict = Depends(current_user)):
    role = u.get("role", "researcher")           # роль из JWT (или X-Role фолбэк)
    res = search.search(req.query, role=role, filters=req.filters)
    res["query"] = req.query  # эхо запроса → заголовок экспорта (md/pdf/jsonld) и фронт
    log_event(u.get("username") or role, "query",
              {"query": req.query, "n_results": len(res.get("facts") or [])})
    return res


@api.post("/parse-query")
def api_parse_query(req: ParseReq):
    """Live-разбор запроса (детерминированный, без LLM — быстрый для as-you-type).

    Возвращает распознанные числовые ограничения (со span'ами для подсветки),
    сущности газетира и rule-based интент.
    """
    text = req.text or ""
    values = grammar.parse_values(text)
    try:
        ents = _matcher().match(text, lang="RU")
    except Exception:  # noqa: BLE001
        ents = []
    entities = [{"canon": m.get("canon"), "type": m.get("type"),
                 "span": m.get("span")} for m in ents]
    intent = search._rule_intent(text, values)
    return {"intent": intent, "has_numbers": bool(values),
            "values": values, "entities": entities}


@api.get("/suggest-entities")
def api_suggest_entities(q: str = Query(..., min_length=1), limit: int = 10):
    """Автокомплит сущностей по префиксу/подстроке (canon + aliases RU/EN + symbols)."""
    ql = q.strip().lower()
    out, seen = [], set()
    for rec in _gaz():
        canon = rec.get("canon")
        if not canon or canon in seen:
            continue
        forms = ([canon] + rec.get("aliases", []) + rec.get("aliases_en", [])
                 + rec.get("symbols", []))
        hit = next((f for f in forms if ql in str(f).lower()), None)
        if hit:
            # приоритет префиксным совпадениям; форма ответа — контракт фронтенда
            # (id/label/type/source_count), source_count добирается из графа ниже.
            rank = 0 if str(hit).lower().startswith(ql) else 1
            out.append((rank, {"id": canon, "label": hit, "type": rec.get("type"),
                               "source_count": 0}))
            seen.add(canon)
    out.sort(key=lambda x: (x[0], x[1]["id"]))
    suggestions = [o[1] for o in out[:limit]]
    if not suggestions:
        return []
    try:
        with _drv().session() as s:
            # canon разделяют сущность и тысячи Parameter-узлов → берём max
            # (source_count проставлен только на сущностном узле).
            counts = {r["canon"]: r["source_count"] for r in s.run(
                "MATCH (n) WHERE n.canon IN $canons "
                "RETURN n.canon AS canon, max(coalesce(n.source_count, 0)) AS source_count",
                canons=[item["id"] for item in suggestions],
            )}
        for item in suggestions:
            item["source_count"] = counts.get(item["id"], 0)
    except HTTPException:
        # Автокомплит остаётся доступным при недоступном Neo4j; газетир локальный.
        pass
    return suggestions


@api.post("/literature-review")
def api_litreview(req: SearchReq):
    return {"markdown": search.literature_review(req.query, filters=req.filters)}


@api.post("/recommend")
def api_recommend(req: SearchReq):
    """Grounded-рекомендация: LLM-синтез ответа строго на извлечённых фактах."""
    return search.recommend(req.query, filters=req.filters)


@api.get("/filters/options")
def api_filter_options():
    """Значения для контролов фильтров (годы/гео/материалы/процессы из графа)."""
    d = _drv()
    with d.session() as s:
        years = [r["y"] for r in s.run("MATCH (d:Document) WHERE d.year IS NOT NULL "
                                       "RETURN DISTINCT d.year AS y ORDER BY y DESC")]
        geos = [r["g"] for r in s.run("MATCH (d:Document) WHERE d.geo IS NOT NULL "
                                      "RETURN DISTINCT d.geo AS g ORDER BY g")]
        mats = [r["c"] for r in s.run("MATCH (m:Material) RETURN DISTINCT m.canon AS c "
                                      "ORDER BY c LIMIT 300")]
        procs = [r["c"] for r in s.run("MATCH (p:Process) RETURN DISTINCT p.canon AS c "
                                       "ORDER BY c LIMIT 300")]
    return {"years": years, "geos": geos, "materials": mats, "processes": procs,
            "confidence_levels": ["высокая", "средняя", "низкая"]}


# ═════════════════════════════ 2. Граф-виз ════════════════════════════════════
@api.post("/graph/subgraph")
def api_subgraph(req: SubgraphReq):
    nodes, edges = graph.answer_subgraph(_drv(), req.doc_ids, limit=req.limit)
    return {"nodes": [{"id": nid, **meta} for nid, meta in nodes.items()],
            "edges": [{"src": s, "dst": d, "type": t} for s, d, t in edges]}


def _knowledge_node(node):
    """Neo4j-узел → нормализованная форма фронтенда (GraphPage neighborhood)."""
    labels = list(node.labels)
    node_type = next((label for label in labels if label != "Author"),
                     labels[0] if labels else "Claim")
    canonical = (node.get("canon") or node.get("doc_id") or node.get("pkey")
                 or node.element_id)
    return {
        "id": canonical,
        "label": node.get("name") or node.get("canon") or node.get("doc_id") or canonical,
        "type": node_type,
        "canonical": canonical,
        "aliases": node.get("aliases") if isinstance(node.get("aliases"), list) else [],
        "source_count": node.get("source_count") or 0,  # денормализовано при load()
        "confidence": node.get("confidence"),
    }


@api.post("/graph/neighborhood")
def api_neighborhood(req: NeighborhoodReq):
    """Локальная окрестность сущности с ограниченными глубиной и размером."""
    depth = max(1, min(3, req.depth))
    limit = max(1, min(200, req.limit))
    raw_id = req.entity_id.strip()
    prefix, sep, value = raw_id.partition(":")
    label = {"M": "Material", "PR": "Process", "EQ": "Equipment"}.get(prefix) if sep else None
    canon = value if label else raw_id
    label_clause = f":{label}" if label else ""
    # Граф-виз: только сущностные узлы (материалы/процессы/оборудование/объекты/фазы).
    # Без Claim (обрывки предложений) / Parameter / Document-хешей — иначе окрестность
    # превращается в кашу. Корень без префикса тоже ограничен сущностью (canon делят
    # тысячи Parameter-узлов → матч без фильтра брал произвольный).
    _ENT = "z:Material OR z:Process OR z:Equipment OR z:Facility OR z:Phase"
    root_where = "" if label else \
        f"WHERE ({_ENT.replace('z:', 'n:')}) "
    # Монотонная окрестность: корень + до limit РАЗНЫХ сущностных соседей в пределах
    # глубины, затем ВСЕ рёбра между набором. Больше глубина → не меньше узлов.
    cypher = (
        f"MATCH (n{label_clause} {{canon:$canon}}) {root_where}"
        f"CALL {{ WITH n MATCH (n)-[*1..{depth}]-(m) "
        f"WHERE ({_ENT.replace('z:', 'm:')}) RETURN DISTINCT m LIMIT $limit }} "
        "WITH n, collect(DISTINCT m) AS nbrs WITH [n] + nbrs AS ns "
        "CALL { WITH ns UNWIND ns AS a MATCH (a)-[r]->(b) "
        "WHERE a IN ns AND b IN ns RETURN r LIMIT 400 } "
        "RETURN ns AS nodes, collect(r) AS relationships"
    )
    node_map, edge_map = {}, {}
    with _drv().session() as s:
        for record in s.run(cypher, canon=canon, limit=limit):
            for node in record["nodes"]:
                meta = _knowledge_node(node)
                node_map[meta["id"]] = meta
            for rel in record["relationships"]:
                source = _knowledge_node(rel.start_node)["id"]
                target = _knowledge_node(rel.end_node)["id"]
                dedup = f"{source}|{rel.type}|{target}"   # схлопнуть параллельные рёбра
                if dedup not in edge_map:
                    edge_map[dedup] = {"id": dedup, "source": source,
                                       "target": target, "type": rel.type}
    if not node_map:
        raise HTTPException(404, "сущность не найдена")
    # source_count берётся из свойства узла (денормализовано в graph.load) —
    # без живого [*1..2]-разворота, дававшего 20–40 с на запрос.
    return {"nodes": list(node_map.values()), "edges": list(edge_map.values())}


# ═════════════════════════════ 3. Эталонные запросы ══════════════════════════
@api.get("/reference/desalination")
def api_ref_desal(max_sulfate: float = 300.0):
    return graph.q_desalination(_drv(), max_sulfate=max_sulfate)


@api.get("/reference/catholyte")
def api_ref_cat():
    return graph.q_catholyte(_drv())


@api.get("/reference/pgm")
def api_ref_pgm(years: int = 5):
    return graph.q_pgm(_drv(), years=years)


# ═════════════════════════════ 4. Противоречия ═══════════════════════════════
@api.get("/contradictions")
def api_contradictions(kind: str | None = None):
    return fetch_contradictions(_drv(), kind=kind)


# ═════════════════════════════ 5. Дашборд ════════════════════════════════════
@api.get("/dashboard/summary")
def api_dash_summary():
    return dashboard.summary_metrics(_drv())


@api.get("/dashboard/coverage/{axis}")
def api_dash_coverage(axis: str):
    fn = {"domain": dashboard.coverage_by_domain, "year": dashboard.coverage_by_year,
          "geo": dashboard.coverage_by_geo}.get(axis)
    if not fn:
        raise HTTPException(404, "axis: domain|year|geo")
    return fn(_drv())


@api.get("/dashboard/risks")
def api_dash_risks():
    return dashboard.risk_zones(_drv())


@api.get("/dashboard/activity")
def api_dash_activity(limit: int = 20):
    return dashboard.activity(_drv(), limit=limit)


@api.get("/dashboard/experts")
def api_dash_experts(limit: int = 20):
    return dashboard.expert_coverage(_drv(), limit=limit)


@api.post("/dashboard/compare")
def api_dash_compare(req: CompareReq):
    return dashboard.compare_technologies(_drv(), req.processes)


# ═════════════════════════════ 6. Экспорт ════════════════════════════════════
@api.post("/export/{fmt}")
def api_export(fmt: str, result: dict,
               role: str = Header(default="researcher", alias="X-Role")):
    log_event((role or "researcher").lower(), "export",
              {"fmt": fmt, "query": result.get("query")})
    if fmt == "markdown":
        return Response(exports.to_markdown(result), media_type="text/markdown; charset=utf-8")
    if fmt == "jsonld":
        return JSONResponse(exports.to_jsonld(result), media_type="application/ld+json")
    if fmt == "pdf":
        return Response(exports.to_pdf(result), media_type="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=report.pdf"})
    raise HTTPException(404, "fmt: markdown|jsonld|pdf")


# ═════════════════════════════ 7. Ручная правка ═════════════════════════════
@api.post("/curation/edit")
def api_cur_edit(req: CurationEditReq):
    try:
        return curation.edit_fact(_drv(), req.param_key, req.new_value, req.editor, req.comment)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@api.post("/curation/add")
def api_cur_add(req: CurationAddReq):
    try:
        return curation.add_fact(_drv(), req.doc_id, req.canon, req.metric,
                                 req.value, req.unit, req.editor)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@api.post("/curation/delete")
def api_cur_delete(req: CurationDeleteReq):
    try:
        return curation.delete_fact(_drv(), req.param_key, req.editor, req.reason)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@api.get("/curation/history")
def api_cur_history(limit: int = 50):
    return curation.edit_history(_drv(), limit=limit)


# ═════════════════════════════ 8. Уведомления ═══════════════════════════════
@api.post("/notify/subscribe")
def api_sub(req: SubReq):
    return notify.subscribe(req.user, req.query)


@api.post("/notify/unsubscribe")
def api_unsub(req: SubReq):
    return {"ok": notify.unsubscribe(req.user, req.query)}


@api.get("/notify/subscriptions")
def api_subs(user: str | None = None):
    return notify.list_subscriptions(user)


@api.get("/notify/check")
def api_check(user: str):
    return notify.check(user, driver=_drv())


@api.post("/notify/mark-seen")
def api_mark_seen(req: SubReq):
    notify.mark_seen(req.user, req.query)
    return {"ok": True}


# ═════════════════════════════ 9. Аудит ══════════════════════════════════════
@api.get("/audit")
def api_audit(limit: int = 500):
    return read_audit(limit=limit)


# ═════════════════════════════ 10. Документы (библиотека + загрузка) ═════════
def _meta_rows():
    """docs.meta.jsonl → список записей (src/doc_type/year/lang/… + производное name)."""
    rows = []
    try:
        with open(DOCS_META, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = r.get("src") or ""
                r["name"] = os.path.basename(src).rsplit(".", 1)[0][:80] or r.get("doc_id")
                rows.append(r)
    except FileNotFoundError:
        pass
    return rows


@api.get("/documents")
def api_documents(
    q: str | None = None, doc_type: str | None = None,
    lang: str | None = None, sensitivity: str | None = None,
    geo: str | None = None, sort: str = "relevance",
    year_from: int | None = None, year_to: int | None = None,
    page: int = 1, page_size: int = 20,
):
    """Библиотека документов: фильтры (тип/язык/чувствительность/год/гео) +
    сортировка (date по году; relevance/trust по числу фактов) + пагинация.
    fact_count/geo добираются из графа (SourcesPage фронтенда)."""
    rows = _meta_rows()
    # Резолв документов по АВТОРУ через граф: клик по эксперту ведёт на
    # /sources?q=«Фамилия И.О.», а такого нет в имени файла — ищем по AUTHORED_BY.
    author_doc_ids: set[str] = set()
    if q:
        try:
            with _drv().session() as s:
                for r in s.run(
                    "MATCH (d:Document)-[:AUTHORED_BY]->(a) "
                    "WHERE toLower(a.canon) CONTAINS toLower($q) "
                    "RETURN DISTINCT d.doc_id AS id", q=q):
                    author_doc_ids.add(r["id"])
        except Exception:  # noqa: BLE001 — граф недоступен → только имя-фильтр
            pass
    def keep(r):
        if q:
            # По-токенно (AND): «никель катод» находит «катодный никель». ИЛИ
            # документ написан автором, совпавшим с запросом (клик по эксперту).
            hay = ((r.get("name") or "") + " " + (r.get("src") or "")).lower()
            name_match = all(tok in hay for tok in q.lower().split())
            if not (name_match or r.get("doc_id") in author_doc_ids):
                return False
        if doc_type and r.get("doc_type") != doc_type:
            return False
        if lang and (r.get("lang") or "").upper() != lang.upper():
            return False
        if sensitivity and r.get("sensitivity") != sensitivity:
            return False
        y = r.get("year")
        if year_from and (y is None or y < year_from):
            return False
        if year_to and (y is None or y > year_to):
            return False
        return True
    items = [r for r in rows if keep(r)]
    if items:
        try:
            with _drv().session() as s:
                enriched = {r["doc_id"]: dict(r) for r in s.run(
                    "UNWIND $ids AS id "
                    "OPTIONAL MATCH (d:Document {doc_id:id}) "
                    "OPTIONAL MATCH (d)-[:HAS_PARAM]->(p:Parameter) "
                    "RETURN id AS doc_id, d.geo AS geo, count(p) AS fact_count, "
                    "avg(p.confidence) AS trust",
                    ids=[r.get("doc_id") for r in items],
                )}
            for item in items:
                graph_meta = enriched.get(item.get("doc_id"), {})
                item["geo"] = graph_meta.get("geo") or item.get("geo")
                item["fact_count"] = graph_meta.get("fact_count", 0)
                item["trust"] = graph_meta.get("trust")  # ср.confidence 0..1
        except HTTPException:
            for item in items:
                item.setdefault("geo", None)
                item.setdefault("fact_count", 0)
                item.setdefault("trust", None)
    if geo:
        items = [item for item in items if item.get("geo") == geo]
    if sort == "date":
        items.sort(key=lambda item: item.get("year") or 0, reverse=True)
    elif sort in {"relevance", "trust"}:
        items.sort(key=lambda item: item.get("fact_count") or 0, reverse=True)
    total = len(items)
    start = max(0, (page - 1) * page_size)
    page_items = items[start:start + page_size]
    cols = ("doc_id", "name", "doc_type", "year", "lang", "sensitivity", "pages",
            "cat", "src", "fact_count", "geo", "trust")
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{k: r.get(k) for k in cols} for r in page_items]}


@api.get("/documents/{doc_id}")
def api_document(doc_id: str):
    """Карточка документа: метаданные + счётчик фактов + топ-факты с цитатами."""
    meta = next((r for r in _meta_rows() if r.get("doc_id") == doc_id), None)
    if not meta:
        raise HTTPException(404, "документ не найден")
    facts = []
    try:
        with _drv().session() as s:
            for r in s.run(
                "MATCH (d:Document {doc_id:$id})-[:HAS_PARAM]->(p:Parameter) "
                "OPTIONAL MATCH (p)-[:MEASURES]->(e) "
                "RETURN e.canon AS canon, p.metric AS metric, p.value_low AS value_low, "
                "p.value_high AS value_high, p.unit_canon AS unit, p.quote AS quote, "
                "p.confidence AS confidence ORDER BY p.confidence DESC LIMIT 50", id=doc_id):
                row = dict(r)
                if row.get("quote"):
                    row["quote"] = search._clean_quote(row["quote"])  # чистка табличного мусора
                facts.append(row)
    except HTTPException:
        pass
    # Верифицируемые (осмысленные) цитаты — первыми, затем по достоверности.
    facts.sort(key=lambda f: (search._bad_quote(f.get("quote")),
                              -(f.get("confidence") or 0)))
    confs = [f["confidence"] for f in facts if f.get("confidence") is not None]
    meta_out = {k: meta.get(k) for k in
                ("doc_id", "name", "doc_type", "year", "lang", "sensitivity",
                 "pages", "chars", "cat", "src", "geo")}
    meta_out["fact_count"] = len(facts)
    meta_out["trust"] = (sum(confs) / len(confs)) if confs else None  # ср.confidence
    return {"meta": meta_out, "facts_count": len(facts), "facts": facts}


def _ingest_pdf(raw: bytes, filename: str, role: str,
                cat: str = "Загружено", sensitivity: str = "internal") -> dict:
    """Общая ingestion: извлечение → NLP-пайплайн → инкрементально в граф.

    Единый путь для ручной загрузки (/documents) и внешних источников
    (/external/import). Идемпотентно по md5 текста. VL-дорожка — только для PDF.
    """
    if not raw:
        raise HTTPException(400, "пустой файл")
    suffix = os.path.splitext(filename or "")[1] or ".bin"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        text, pages = extract_mod.extract_any(tmp_path)
        text = nfc(text or "")
        if not text.strip() or text.startswith("[EXTRACT-ERR"):
            raise HTTPException(422, f"не удалось извлечь текст ({text[:80]})")
        import hashlib
        md5 = hashlib.md5(text.encode()).hexdigest()
        doc_id = md5[:16]

        existing = {r.get("doc_id") for r in _meta_rows()}
        if doc_id in existing:
            return {"doc_id": doc_id, "duplicate": True,
                    "message": "документ с таким содержимым уже загружен"}

        cyr = sum(1 for c in text[:4000] if "а" <= c.lower() <= "я")
        lat = sum(1 for c in text[:4000] if "a" <= c.lower() <= "z")
        doc_type = extract_mod.classify_doc_type(text, filename or "")
        meta = {
            "doc_id": doc_id, "src": filename or doc_id, "cat": cat,
            "pages": pages, "chars": len(text),
            "lang": "RU" if cyr >= lat else "EN",
            "year": extract_mod.year_from_name(filename or ""),
            "sensitivity": sensitivity, "kg_value": 3, "doc_type": doc_type, "ok": True,
        }
        facts, edges, _geos = pipeline.process_doc(doc_id, text, _matcher())
        # VL-дорожка: таблицы состава из PDF через мультимодальную модель (Qwen3-VL,
        # RouterAI). Best-effort: сбой не роняет загрузку; VL выключен → 0.
        vl_facts = 0
        if (filename or "").lower().endswith(".pdf"):
            try:
                from src import vision
                vfacts = vision.extract_pdf_vl(tmp_path, doc_id)
                facts += vfacts
                vl_facts = len(vfacts)
            except Exception as e:  # noqa: BLE001
                log.warning("VL OCR skip for %s: %s", doc_id, e)
        # инкрементальная загрузка в граф (MERGE идемпотентен)
        graph.load(_drv(), [meta], facts, edges)

        # персист: docs.meta.jsonl (без текста) + docs.text.jsonl (текст) — для
        # библиотеки, поиска и последующей пересборки индексов.
        with open(DOCS_META, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in meta.items()}, ensure_ascii=False) + "\n")
        with open(DOCS_TEXT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"doc_id": doc_id, "text": text}, ensure_ascii=False) + "\n")
        search._META_CACHE = None    # сброс кэша, чтобы новый док был виден поиску

        log_event((role or "researcher").lower(), "upload",
                  {"doc_id": doc_id, "doc_type": doc_type, "facts": len(facts)})
        return {"doc_id": doc_id, "duplicate": False, "doc_type": doc_type,
                "pages": pages, "chars": len(text), "lang": meta["lang"],
                "facts_added": len(facts), "edges_added": len(edges),
                "vl_table_facts": vl_facts}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@api.post("/documents")
async def api_upload(file: UploadFile = File(...),
                     role: str = Header(default="researcher", alias="X-Role")):
    """Ручная загрузка документа (multipart). Извлечение → пайплайн → граф."""
    raw = await file.read()
    return _ingest_pdf(raw, file.filename or "upload.bin", role)


# ═══════════════════════ 8. Внешние источники (CyberLeninka) ══════════════════
@api.get("/external/search")
def api_external_search(q: str = Query(..., min_length=2), limit: int = 15):
    """Поиск статей в открытом источнике CyberLeninka (headless-браузер)."""
    from src import external
    try:
        return {"query": q, "results": external.search(q, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"источник недоступен: {str(e)[:120]}")


class ExternalImportReq(BaseModel):
    url: str
    title: str | None = None


@api.post("/external/import")
def api_external_import(req: ExternalImportReq,
                        role: str = Header(default="researcher", alias="X-Role")):
    """Скачивает PDF из внешнего источника и грузит в граф тем же путём, что и
    ручная загрузка (`_ingest_pdf`). Возвращает счётчики добавленных фактов."""
    from src import external
    try:
        raw = external.download_pdf(req.url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"не удалось скачать PDF: {str(e)[:120]}")
    slug = req.url.rstrip("/").rsplit("/", 1)[-1]
    fname = ((req.title or slug)[:120] + ".pdf")
    result = _ingest_pdf(raw, fname, role, cat="CyberLeninka", sensitivity="public")
    log_event((role or "researcher").lower(), "external_import",
              {"url": req.url, "facts": result.get("facts_added", 0)})
    return {**result, "source": "cyberleninka", "src_url": req.url}


app.include_router(api)


# ═══════════════════════ 9. Раздача собранного фронтенда (прод) ═══════════════
# Единый контейнер: FastAPI отдаёт /api (роутер выше) и SPA из frontend/dist.
# В деве (dist нет) блок пропускается — фронт крутит Vite на :5173.
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        """SPA-fallback: реальный файл → отдать; иначе index.html (клиент-роутинг).
        /api/* уже перехвачен роутером выше (регистрируется раньше)."""
        candidate = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
