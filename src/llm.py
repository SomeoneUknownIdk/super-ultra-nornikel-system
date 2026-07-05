"""ЭТАП 4: LLM-обогащение через RouterAI (OpenAI-совместимый API).

chat()             — низкоуровневая обвязка: retry+backoff, дисковый кэш, проверка finish_reason.
extract_relations()— build-time (QWEN): рёбра графа из чанка, closed vocabulary + валидация.
parse_query()      — runtime (FLASH): разбор вопроса пользователя в интент/слоты, быстрый таймаут.

Без ключа (not LLM_ENABLED) chat() бросает RuntimeError — вызывающий код деградирует на rule-based.
"""
from __future__ import annotations
import os, sys, json, time, random, hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import (
    LLM_CACHE, QWEN_MODEL, FLASH_MODEL, LLM_API_KEY, LLM_BASE_URL,
    LLM_AUTH_SCHEME, LLM_EXTRA_HEADERS, LLM_ENABLED, EDGE_TYPES, nfc,
)

import requests

# Рёбра, которые LLM разрешено извлекать на этапе связей (подмножество онтологии ТЗ).
RELATION_TYPES = ["uses_material", "produces_output", "operates_at_condition"]

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5


def _cache_key(model: str, messages: list, temperature: float) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str):
    LLM_CACHE.mkdir(parents=True, exist_ok=True)
    return LLM_CACHE / f"{key}.json"


def chat(messages, model: str = QWEN_MODEL, temperature: float = 0,
         max_tokens: int = 1500, timeout: int = 60, max_attempts: int = None) -> str:
    """Один вызов чата → текст ответа (content первого choice).

    Идемпотентность batch: дисковый кэш по sha256(model+messages+temp) —
    повторный идентичный вызов возвращается без обращения к сети.
    Retry с экспоненциальным backoff+jitter на 429/5xx/timeout (до 5 попыток).
    Проверяется finish_reason (обрезанный ответ length → ошибка).
    """
    if not LLM_ENABLED:
        raise RuntimeError(
            "LLM отключён (нет ключа провайдера — ROUTERAI_API_KEY). "
            "chat() недоступен — вызывающий код должен деградировать на rule-based путь."
        )

    key = _cache_key(model, messages, temperature)
    path = _cache_path(key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["content"]
        except Exception:
            pass  # битый кэш — перезапросим

    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"{LLM_AUTH_SCHEME} {LLM_API_KEY}",
               "Content-Type": "application/json", **LLM_EXTRA_HEADERS}
    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}

    last_err = None
    attempts = max_attempts or _MAX_ATTEMPTS   # runtime-путь может ограничить (быстрый фолбэк)
    for attempt in range(attempts):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            if resp.status_code in _RETRY_STATUS:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                finish = choice.get("finish_reason")
                if finish not in (None, "stop", "eos", "end_turn"):
                    # length/content_filter и пр. — ответ неполный, лечится ретраем/большим max_tokens
                    raise RuntimeError(f"неполный ответ, finish_reason={finish!r}")
                content = choice["message"]["content"] or ""
                try:
                    path.write_text(json.dumps({"content": content}, ensure_ascii=False),
                                    encoding="utf-8")
                except Exception:
                    pass  # кэш — best-effort, не роняем вызов
                return content
        except requests.exceptions.Timeout as e:
            last_err = e
        except requests.exceptions.RequestException as e:
            last_err = e
        # backoff только если будут ещё попытки
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt + random.random(), 30))

    raise RuntimeError(f"chat() не удался после {attempts} попыток: {last_err}")


def _extract_json(text: str):
    """Достать JSON из ответа LLM (возможен ```json fence или лишний текст вокруг)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # вырезать первый сбалансированный [...] или {...}
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i, j = s.find(open_c), s.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    return None


_REL_SYSTEM = (
    "Ты извлекаешь связи (рёбра графа знаний) из русскоязычного научно-технического текста "
    "про металлургию и материаловедение. Отвечай ТОЛЬКО валидным JSON-массивом, без пояснений."
)


def _rel_prompt(chunk_text: str, canons: list) -> str:
    types = ", ".join(RELATION_TYPES)
    cano = ", ".join(canons)
    schema = (
        '[{"src": "<сущность>", "dst": "<сущность>", '
        '"type": "<тип>", "quote": "<дословная цитата из текста>"}]'
    )
    return (
        f"Из текста ниже извлеки связи между сущностями.\n"
        f"Разрешённые сущности (closed vocabulary, используй ТОЛЬКО их, дословно): {cano}.\n"
        f"Разрешённые типы связей: {types}.\n"
        f"Правила:\n"
        f"- src и dst — строго из списка разрешённых сущностей.\n"
        f"- quote — дословная подстрока исходного текста, подтверждающая связь.\n"
        f"- Если связей нет — верни пустой массив [].\n"
        f"Формат ответа (JSON-массив объектов): {schema}\n\n"
        f"ТЕКСТ:\n{chunk_text}"
    )


def extract_relations(chunk_text: str, canons: list) -> list:
    """Извлечь валидные рёбра {src,dst,type,quote} из чанка (build-time, QWEN).

    Closed vocabulary: src/dst обязаны быть в canons. quote обязана быть подстрокой
    чанка (регистронезависимо). Тип — из RELATION_TYPES. Невалидные рёбра отброшены.
    """
    chunk_text = nfc(chunk_text or "")
    canons = [nfc(c) for c in (canons or [])]
    if not chunk_text.strip() or not canons:
        return []

    messages = [
        {"role": "system", "content": _REL_SYSTEM},
        {"role": "user", "content": _rel_prompt(chunk_text, canons)},
    ]
    raw = chat(messages, model=QWEN_MODEL, temperature=0, max_tokens=1500)
    parsed = _extract_json(raw)
    if not isinstance(parsed, list):
        return []

    canon_set = {c.lower() for c in canons}
    text_lower = chunk_text.lower()
    valid = []
    for edge in parsed:
        if not isinstance(edge, dict):
            continue
        src = nfc(str(edge.get("src", ""))).strip()
        dst = nfc(str(edge.get("dst", ""))).strip()
        etype = str(edge.get("type", "")).strip()
        quote = nfc(str(edge.get("quote", ""))).strip()
        if src.lower() not in canon_set or dst.lower() not in canon_set:
            continue
        if etype not in RELATION_TYPES:
            continue
        if not quote or quote.lower() not in text_lower:
            continue
        valid.append({"src": src, "dst": dst, "type": etype, "quote": quote})
    return valid


_QUERY_SYSTEM = (
    "Ты разбираешь вопрос пользователя к базе знаний по металлургии и материаловедению. "
    "Отвечай ТОЛЬКО валидным JSON-объектом, без пояснений."
)

_QUERY_PROMPT = (
    "Разбери вопрос в структуру:\n"
    '{{"intent": "search|compare|lookup|explain", '
    '"material": "<материал или null>", '
    '"process": "<процесс или null>", '
    '"metric": "<измеряемая величина или null>", '
    '"has_number": true|false}}\n'
    "Правила:\n"
    "- Числа НЕ извлекай (их разберёт грамматика). has_number — просто флаг: "
    "есть ли в вопросе число/диапазон/единица.\n"
    "- Пустые слоты — null.\n\n"
    "ВОПРОС: {q}"
)


def parse_query(q: str) -> dict:
    """Разобрать пользовательский запрос в интент/слоты (runtime, FLASH, таймаут 3с).

    Числа не парсит (вернёт только флаг has_number). При любой ошибке —
    возврат {'intent': 'search'} (снаружи включается rule-based фолбэк).
    """
    q = nfc(q or "").strip()
    if not q:
        return {"intent": "search"}
    try:
        messages = [
            {"role": "system", "content": _QUERY_SYSTEM},
            {"role": "user", "content": _QUERY_PROMPT.format(q=q)},
        ]
        # runtime-путь: 1 попытка, таймаут 10с → быстрый фолбэк на rule-based (не 32с ретраев).
        # max_tokens=2000 (не 200!): FLASH — reasoning-модель, скрытые reasoning-токены
        # съедали бюджет → finish_reason='length' → chat() бросал → ответ НЕ кэшировался
        # и каждый некэшированный parse_query платил ~3с и падал в rule-based фолбэк.
        raw = chat(messages, model=FLASH_MODEL, temperature=0, max_tokens=2000,
                   timeout=10, max_attempts=1)
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return {"intent": "search"}
        out = {
            "intent": str(parsed.get("intent") or "search"),
            "material": parsed.get("material") or None,
            "process": parsed.get("process") or None,
            "metric": parsed.get("metric") or None,
            "has_number": bool(parsed.get("has_number", False)),
        }
        return out
    except Exception:
        return {"intent": "search"}
