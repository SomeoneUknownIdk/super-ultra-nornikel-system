"""Этап 7: семантика на эмбеддингах bge-m3 (RouterAI, 1024d).
БЕЗ локального torch — кодирование идёт через API → ноль RAM на этой машине.
Семантика — опциональный онлайн-слой (рядом с LLM); ядро числовое+графовое офлайн.
Нет ключа → build/search мягко деградируют (search возвращает []).

build_index(): режет доки на чанки, кодирует моделью -doc, пишет artifacts/emb.npy + emb_meta.json.
class Semantic: search(query, k) кодирует запрос моделью -query, dot-product по норм. векторам.
"""
from __future__ import annotations
import json, time
from typing import Optional

import numpy as np
import requests

from src.config import (DOCS_TEXT, ARTIFACTS, LLM_API_KEY, LLM_BASE_URL,
                        LLM_AUTH_SCHEME, LLM_EXTRA_HEADERS, LLM_ENABLED,
                        EMBED_DOC_MODEL, EMBED_QUERY_MODEL, nfc)

EMB_NPY = ARTIFACTS / "emb.npy"
EMB_META = ARTIFACTS / "emb_meta.json"

# Одна модель bge-m3 для doc и query (RouterAI).
DOC_MODEL = EMBED_DOC_MODEL
QUERY_MODEL = EMBED_QUERY_MODEL

MAX_CHARS = 1200            # символов на чанк (эмбеддинг-модель ~ до 2000 токенов)
MAX_CHUNKS_PER_DOC = 15     # кэп числа API-вызовов на документ
MAX_TOTAL_CHUNKS = 4000     # глобальный потолок вызовов на прогон
PREVIEW_CHARS = 200


def _embed(text: str, model: str, session: requests.Session, retries: int = 4):
    """Один вектор через /v1/embeddings. None при неудаче (вызывающий деградирует)."""
    if not LLM_ENABLED:
        return None
    if not (text or "").strip():
        return None                       # пустой чанк — нечего кодировать
    for attempt in range(retries):
        try:
            r = session.post(
                f"{LLM_BASE_URL.rstrip('/')}/embeddings",
                headers={"Authorization": f"{LLM_AUTH_SCHEME} {LLM_API_KEY}",
                         **LLM_EXTRA_HEADERS},
                json={"model": model, "input": text[:8000]}, timeout=30)
            if r.status_code == 200:
                data = (r.json() or {}).get("data")   # редкий 200-без-data не роняет прогон
                if not data:
                    time.sleep(0.5 * (2 ** attempt)); continue
                a = np.asarray(data[0]["embedding"], dtype=np.float32)
                n = np.linalg.norm(a)
                return a / n if n else a
            if r.status_code in (429, 500, 502, 503):
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
        except (requests.RequestException, ValueError, KeyError, IndexError):
            time.sleep(0.5 * (2 ** attempt))
            continue
    return None


def _split(text: str):
    """Параграфы → чанки ≤ MAX_CHARS по границам предложений."""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) <= MAX_CHARS:
            yield para
        else:
            buf = ""
            for sent in para.replace("\n", " ").split(". "):
                if len(buf) + len(sent) > MAX_CHARS and buf:
                    yield buf.strip()
                    buf = ""
                buf += sent + ". "
            if buf.strip():
                yield buf.strip()


BATCH_SIZE = 32            # текстов на один /embeddings-вызов (OpenAI-совм. массив input)
_BATCH_WARNED = False


def _embed_batch(texts, model, session, retries: int = 4):
    """Список текстов → список нормир. векторов (None-элемент при сбое отдельного).
    Устойчиво: 200-без-'data' (ошибка/лимит) и битый ответ НЕ роняют прогон —
    батч уходит в фолбэк на одиночные _embed, чтобы не терять весь батч."""
    global _BATCH_WARNED
    if not LLM_ENABLED or not texts:
        return [None] * len(texts)
    payload = [t[:8000] for t in texts]
    for attempt in range(retries):
        try:
            r = session.post(
                f"{LLM_BASE_URL.rstrip('/')}/embeddings",
                headers={"Authorization": f"{LLM_AUTH_SCHEME} {LLM_API_KEY}",
                         **LLM_EXTRA_HEADERS},
                json={"model": model, "input": payload}, timeout=120)
            if r.status_code == 200:
                data = (r.json() or {}).get("data")
                if not isinstance(data, list) or len(data) != len(texts):
                    break   # 200 без корректного data → фолбэк на одиночные
                data = sorted(data, key=lambda d: d.get("index", 0))
                out = []
                for item in data:
                    a = np.asarray(item["embedding"], dtype=np.float32)
                    n = np.linalg.norm(a)
                    out.append(a / n if n else a)
                return out
            if r.status_code in (429, 500, 502, 503):
                time.sleep(0.5 * (2 ** attempt)); continue
            break   # прочий не-200 → фолбэк на одиночные
        except (requests.RequestException, ValueError, KeyError):
            time.sleep(0.5 * (2 ** attempt))
    # фолбэк: по одному (один битый чанк не теряет весь батч)
    if not _BATCH_WARNED:
        print("  [warn] батч-эмбеддинг деградировал на одиночные вызовы", flush=True)
        _BATCH_WARNED = True
    return [_embed(t, model, session) for t in texts]


def build_index(limit: Optional[int] = None) -> dict:
    """Собрать семантический индекс через embeddings API провайдера (ноль лок. RAM).
    Батч-режим: чанки (dense-first) кодируются пачками по BATCH_SIZE."""
    if not LLM_ENABLED:
        raise RuntimeError("Нет ключа LLM-провайдера — индекс не строится (ядро работает без него)")
    if not DOCS_TEXT.exists():
        raise FileNotFoundError(f"Нет входного корпуса: {DOCS_TEXT}")

    session = requests.Session()
    vectors, meta, n_docs = [], [], 0
    t0 = time.time()
    # приоритет плотным докам (kg_value): семантика важнее по R&D-ядру, чем по рынку
    from src.config import DOCS_META
    kg = {}
    try:
        for l in open(DOCS_META, encoding="utf-8"):
            r = json.loads(l); kg[r["doc_id"]] = r.get("kg_value") or 0
    except Exception:
        pass
    records = []
    with DOCS_TEXT.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: records.append(json.loads(line))
                except json.JSONDecodeError: pass
    records.sort(key=lambda r: -kg.get(r.get("doc_id"), 0))   # плотные первыми

    # 1) собрать чанки (уважая кэпы), 2) закодировать пачками.
    pending = []   # (doc_id, chunk)
    for rec in records:
        if limit and n_docs >= limit:
            break
        if len(pending) >= MAX_TOTAL_CHUNKS:
            break
        doc_id, text = rec.get("doc_id"), rec.get("text") or ""
        if not doc_id or not text.strip():
            continue
        n_docs += 1
        per_doc = 0
        for chunk in _split(text):
            if per_doc >= MAX_CHUNKS_PER_DOC or len(pending) >= MAX_TOTAL_CHUNKS:
                break
            pending.append((doc_id, chunk))
            per_doc += 1

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        vecs = _embed_batch([c for _, c in batch], DOC_MODEL, session)
        for (doc_id, chunk), v in zip(batch, vecs):
            if v is None:
                continue
            vectors.append(v)
            meta.append({"doc_id": doc_id, "preview": chunk[:PREVIEW_CHARS]})
        print(f"  {i + len(batch)}/{len(pending)} чанков, {len(vectors)} векторов, "
              f"{time.time()-t0:.0f}s", flush=True)

    if not vectors:
        raise RuntimeError("Нет векторов (нет ключа/сети?)")
    arr = np.vstack(vectors).astype(np.float32)
    np.save(EMB_NPY, arr)
    EMB_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"ГОТОВО: {arr.shape[0]} векторов dim={arr.shape[1]} из {n_docs} док, {time.time()-t0:.0f}s")
    return {"docs": n_docs, "chunks": arr.shape[0], "npy": str(EMB_NPY), "meta": str(EMB_META)}


class Semantic:
    """Семантический поиск по индексу. Мягко пустой, если индекс/ключ недоступны."""

    def __init__(self):
        self.mat = None
        self.meta = []
        self._session = requests.Session()
        self.load()

    def load(self):
        try:
            if EMB_NPY.exists() and EMB_META.exists():
                self.mat = np.load(EMB_NPY)
                self.meta = json.loads(EMB_META.read_text(encoding="utf-8"))
        except Exception:
            self.mat, self.meta = None, []
        return self   # для чейнинга Semantic().load()

    def search(self, query: str, k: int = 10):
        if self.mat is None or not self.meta:
            return []
        qv = _embed(query, QUERY_MODEL, self._session)
        if qv is None:
            return []
        # Индекс мог быть собран другой моделью (иная размерность) — после смены
        # провайдера пересоберите emb.npy; до этого мягко деградируем на пусто.
        if self.mat.ndim != 2 or self.mat.shape[1] != qv.shape[0]:
            return []
        scores = self.mat @ qv
        order = np.argsort(-scores)
        out, seen = [], set()
        for i in order:
            m = self.meta[int(i)]
            did = m.get("doc_id")
            if did in seen:
                continue
            seen.add(did)
            out.append({"doc_id": did, "score": float(scores[int(i)]), "preview": m.get("preview", "")})
            if len(out) >= k:
                break
        return out


if __name__ == "__main__":
    import sys
    build_index(int(sys.argv[1]) if len(sys.argv) > 1 else None)
