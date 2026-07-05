"""Уведомления о новых/изменённых данных по темам (ТЗ доп.: «уведомления о новых
публикациях/данных по темам»).

Минимальная честная реализация: живого приёма (push/email) нет — это заглушка над
существующим поиском. Подписка = сохранённый запрос + метка времени последнего
просмотра. check() прогоняет src.search.search(query) и оставляет только факты/доки,
чьё время появления (Parameter.extracted_at из графа, либо mtime docs.meta) позже
last_seen. Так «новизна» честная: она опирается на extracted_at, штампуемый
загрузчиком графа (src.graph._load_facts) при каждом прогоне пайплайна.

ponytail: подписки лежат в data/subscriptions.json (файл-стор, не БД). Для
многопользовательского прод-режима вынести в БД (Postgres/SQLite) + очередь
доставки; здесь же — один процесс, десятки подписок, JSON достаточно.
ponytail: потолок реализации — нет живого приёма событий; check() опрашивается
вызывающим (cron/кнопка UI), push-канала нет.

API:
  subscribe(user, query)      -> dict   добавить/обновить (last_seen = сейчас)
  unsubscribe(user, query)    -> bool
  list_subscriptions(user)    -> list
  check(user, driver=None)    -> list   [{query, new_count, sample:[...]}]  (last_seen НЕ трогает)
  mark_seen(user, query)      -> None    last_seen = сейчас
"""
from __future__ import annotations

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import DATA, DOCS_META, nfc

# ponytail: путь-модульная переменная (не константа), чтобы __main__/тесты могли
# подменить его на temp-файл без monkeypatch внутренностей.
SUBS_PATH = DATA / "subscriptions.json"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Хранилище: плоский список подписок в JSON.
# ─────────────────────────────────────────────────────────────────────────────
def _load() -> list:
    """Прочитать список подписок из SUBS_PATH ([] если файла нет/битый)."""
    try:
        with open(SUBS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _save(subs: list) -> None:
    """Атомарно-ish записать подписки (tmp + replace)."""
    path = str(SUBS_PATH)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _find(subs, user, query):
    """Индекс подписки (user, query) в списке или -1."""
    u, q = nfc(user), nfc(query)
    for i, s in enumerate(subs):
        if s.get("user") == u and s.get("query") == q:
            return i
    return -1


# ─────────────────────────────────────────────────────────────────────────────
# Публичный API.
# ─────────────────────────────────────────────────────────────────────────────
def subscribe(user: str, query: str) -> dict:
    """Добавить/обновить подписку пользователя на тему-запрос. last_seen = сейчас."""
    user, query = nfc(user), nfc(query)
    subs = _load()
    rec = {"user": user, "query": query, "last_seen_iso": _now_iso()}
    i = _find(subs, user, query)
    if i >= 0:
        subs[i] = rec
    else:
        subs.append(rec)
    _save(subs)
    return rec


def unsubscribe(user: str, query: str) -> bool:
    """Удалить подписку. True — была и удалена, False — не найдена."""
    subs = _load()
    i = _find(subs, user, query)
    if i < 0:
        return False
    subs.pop(i)
    _save(subs)
    return True


def list_subscriptions(user=None) -> list:
    """Все подписки (user=None) или подписки одного пользователя."""
    subs = _load()
    if user is None:
        return subs
    u = nfc(user)
    return [s for s in subs if s.get("user") == u]


def mark_seen(user: str, query: str) -> None:
    """Обновить last_seen подписки на «сейчас» (вызывать после показа check())."""
    user, query = nfc(user), nfc(query)
    subs = _load()
    i = _find(subs, user, query)
    if i < 0:
        return
    subs[i]["last_seen_iso"] = _now_iso()
    _save(subs)


# ─────────────────────────────────────────────────────────────────────────────
# Определение новизны факта/документа.
# ─────────────────────────────────────────────────────────────────────────────
def _param_times(driver, doc_ids):
    """{doc_id: max Parameter.extracted_at} из графа для нужных doc_id.

    extracted_at штампуется загрузчиком (src.graph._load_facts) на каждый факт при
    MERGE — это и есть «когда данные появились». driver=None/сбой → {} (деградация
    на mtime docs.meta).
    """
    ids = [d for d in {nfc(str(x)) for x in doc_ids if x}]
    if driver is None or not ids:
        return {}
    cy = """
    UNWIND $ids AS did
    MATCH (p:Parameter {doc_id: did})
    RETURN did AS doc_id, max(p.extracted_at) AS t
    """
    try:
        with driver.session() as s:
            return {r["doc_id"]: r["t"] for r in s.run(cy, ids=ids) if r["t"]}
    except Exception:
        return {}


def _meta_mtime_iso():
    """ISO mtime файла docs.meta.jsonl — грубый фолбэк «времени данных», если графа
    нет. Один timestamp на весь корпус (файл пересобирается пайплайном)."""
    try:
        ts = os.path.getmtime(DOCS_META)
        return datetime.datetime.fromtimestamp(ts).isoformat()
    except OSError:
        return None


def _is_new(item_time, last_seen, fallback):
    """True, если item_time (или fallback при отсутствии) строго позже last_seen.

    Времена сравниваем как ISO-строки (лексикографически = хронологически для
    одного формата). Нет ни item_time, ни fallback → не новое (не шумим)."""
    t = item_time or fallback
    if not t:
        return False
    return str(t) > str(last_seen)


# ─────────────────────────────────────────────────────────────────────────────
# check: что нового по каждой подписке пользователя.
# ─────────────────────────────────────────────────────────────────────────────
def check(user: str, driver=None) -> list:
    """Для каждой подписки user — новые (после last_seen) факты/доки по её query.

    Релевантность: src.search.search(query). Новизна: время документа/факта
    (Parameter.extracted_at из графа, иначе mtime docs.meta) строго позже
    last_seen. last_seen НЕ обновляется здесь (это делает mark_seen).

    Возвращает [{query, new_count, sample:[{doc_id, canon, metric, quote, when}]}].
    Мягкая деградация: пустой граф / нет search → new_count=0, sample=[].
    """
    from src import search as _search  # локальный импорт: search тянет граф/embed

    fallback = _meta_mtime_iso()
    out = []
    for sub in list_subscriptions(user):
        query = sub.get("query") or ""
        last_seen = sub.get("last_seen_iso") or ""
        try:
            res = _search.search(query)
        except Exception:
            res = {}
        facts = res.get("facts") or []
        docs = res.get("docs") or []

        # Времена появления по doc_id (граф) для всех фигурирующих документов.
        doc_ids = [f.get("doc_id") for f in facts] + [d.get("doc_id") for d in docs]
        times = _param_times(driver, doc_ids)

        sample = []
        seen_docs = set()
        for f in facts:
            did = f.get("doc_id")
            when = times.get(nfc(str(did))) if did else None
            if not _is_new(when, last_seen, fallback):
                continue
            sample.append({
                "doc_id": did,
                "canon": f.get("canon"),
                "metric": f.get("metric"),
                "quote": (f.get("quote") or "")[:160],
                "when": when or fallback,
            })
            if did:
                seen_docs.add(nfc(str(did)))

        # Документы без числовых фактов (семантическая дорожка) — тоже «новизна».
        for d in docs:
            did = d.get("doc_id")
            didn = nfc(str(did)) if did else None
            if didn and didn in seen_docs:
                continue
            when = times.get(didn) if didn else None
            if not _is_new(when, last_seen, fallback):
                continue
            sample.append({"doc_id": did, "canon": None, "metric": None,
                           "quote": "", "when": when or fallback})
            if didn:
                seen_docs.add(didn)

        out.append({"query": query, "new_count": len(sample), "sample": sample[:10]})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Self-check (assert-based) — на temp-файле, без падений если граф пуст.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import pathlib
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp()) / "subscriptions.json"
    SUBS_PATH = tmp  # подмена пути на temp (модульная переменная, не константа)

    U, Q = "alice", "методы обессоливания сульфаты"

    # subscribe → появляется в списке.
    rec = subscribe(U, Q)
    assert rec["user"] == U and rec["query"] == Q and rec["last_seen_iso"]
    subs = list_subscriptions(U)
    assert len(subs) == 1 and subs[0]["query"] == Q

    # subscribe тем же (user, query) — обновление, не дубль.
    subscribe(U, Q)
    assert len(list_subscriptions(U)) == 1
    # другой пользователь — своя подписка, list(None) видит обе.
    subscribe("bob", "католит плотность тока")
    assert len(list_subscriptions("bob")) == 1
    assert len(list_subscriptions()) == 2

    # check не падает даже без графа (driver=None) и возвращает форму на каждую подписку.
    res = check(U, driver=None)
    assert isinstance(res, list) and len(res) == 1
    r0 = res[0]
    assert r0["query"] == Q and "new_count" in r0 and isinstance(r0["sample"], list)
    assert r0["new_count"] == len(r0["sample"]) or r0["new_count"] >= len(r0["sample"])

    # mark_seen обновляет last_seen (двигает вперёд) и НЕ вызывался внутри check.
    before = list_subscriptions(U)[0]["last_seen_iso"]
    mark_seen(U, Q)
    after = list_subscriptions(U)[0]["last_seen_iso"]
    assert after >= before

    # После mark_seen «сейчас» — прошлые данные уже не новые (new_count падает до 0,
    # т.к. mtime docs.meta заведомо раньше только что выставленного last_seen).
    res2 = check(U, driver=None)
    assert res2[0]["new_count"] == 0, res2

    # unsubscribe.
    assert unsubscribe(U, Q) is True
    assert unsubscribe(U, Q) is False
    assert len(list_subscriptions(U)) == 0

    print("notify self-check OK")
