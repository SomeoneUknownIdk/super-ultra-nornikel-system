"""Keyword-индекс всего корпуса: лемма → документы. Третья (fallback) дорожка поиска.

Зачем: семантический индекс покрывает ~273 плотных дока (кап 4000 чанков) — журналы
(kg=2, выпуски по ~500K симв.) в него не попадают, и темы вроде «закачка шахтных вод»
(живут в «Горном журнале») недостижимы ни числовой, ни семантической дорожкой.
Этот индекс строится ОДНИМ проходом по ВСЕМУ корпусу (1288 доков) и отвечает мгновенно.

build() -> dict         — собрать artifacts/kw_index.json (~один раз после extract).
search(query, k) -> list — топ-доки по сумме idf пересечения лемм запроса.

ponytail: плоский json-словарь вместо полноценного FTS (Lucene/tantivy) — хватает для
fallback-дорожки; вынести в полноценный FTS, если корпус вырастет на порядок.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import ARTIFACTS, DOCS_TEXT

KW_INDEX = ARTIFACTS / "kw_index.json"

_TOKEN = re.compile(r"[а-яёa-z]{4,}")   # слова от 4 букв (шум короче)
_MAX_DF = 0.30                          # лемма в >30% доков — стоп-слово по факту

_morph = None
_lemma_cache: dict = {}


def _lemma(tok: str) -> str:
    """Лемма через pymorphy3 (уже в зависимостях) с кэшем словоформ."""
    global _morph
    hit = _lemma_cache.get(tok)
    if hit is not None:
        return hit
    if _morph is None:
        import pymorphy3
        _morph = pymorphy3.MorphAnalyzer()
    lem = _morph.parse(tok)[0].normal_form
    _lemma_cache[tok] = lem
    return lem


def _doc_lemma_tf(text: str) -> dict:
    """Частоты лемм документа {lemma: tf} — tf нужен ранжированию (док с 26
    упоминаниями темы должен стоять выше дока с одним)."""
    tf: dict = {}
    for t in _TOKEN.findall(text.lower()):
        lem = _lemma(t)
        tf[lem] = tf.get(lem, 0) + 1
    return tf


def build(path=DOCS_TEXT, out=KW_INDEX) -> dict:
    """Один проход корпуса → {"docs": [doc_id...], "lemmas": {lemma: [[idx, tf]...]}}."""
    doc_ids, postings = [], {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            did, text = rec.get("doc_id"), rec.get("text") or ""
            if not did or not text.strip():
                continue
            idx = len(doc_ids)
            doc_ids.append(did)
            for lem, tf in _doc_lemma_tf(text).items():
                postings.setdefault(lem, []).append([idx, tf])
            if idx % 200 == 199:
                print(f"  {idx + 1} док, лемм {len(postings)}", flush=True)
    n = len(doc_ids)
    # выкинуть сверхчастые (df>30%, но не меньше 2 доков — иначе мини-корпус
    # в тестах обнуляется) и одиночные хапаксы длиной >25 (мусор OCR)
    df_cap = max(2, _MAX_DF * n)
    postings = {l: v for l, v in postings.items()
                if len(v) <= df_cap and not (len(v) == 1 and len(l) > 25)}
    data = {"docs": doc_ids, "lemmas": postings}
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"ГОТОВО: {n} док, {len(postings)} лемм → {out}")
    return {"docs": n, "lemmas": len(postings)}


_INDEX = None


def _load():
    global _INDEX
    if _INDEX is None and KW_INDEX.exists():
        _INDEX = json.loads(KW_INDEX.read_text(encoding="utf-8"))
    return _INDEX


def search(query: str, k: int = 8) -> list:
    """Топ-k doc_id по sum(idf·log(1+tf)) лемм запроса. Нет индекса → [] (мягко)."""
    ix = _load()
    if not ix:
        return []
    docs, lem = ix["docs"], ix["lemmas"]
    n = len(docs)
    scores: dict = {}
    seen_q = set()
    for tok in _TOKEN.findall((query or "").lower()):
        ql = _lemma(tok)
        if ql in seen_q:          # повтор леммы в запросе не даёт двойной вес
            continue
        seen_q.add(ql)
        post = lem.get(ql)
        if not post:
            continue
        idf = math.log(n / len(post))
        for i, tf in post:
            scores[i] = scores.get(i, 0.0) + idf * math.log1p(tf)
    top = sorted(scores.items(), key=lambda x: -x[1])[:k]
    return [{"doc_id": docs[i], "score": round(s, 2)} for i, s in top]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        print(json.dumps(search(" ".join(sys.argv[2:])), ensure_ascii=False, indent=1))
    else:
        build()
