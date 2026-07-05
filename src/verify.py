"""LLM-верификация извлечённых фактов против их цитат (слой 3 точности журналов).

Паттерн «LLM-as-prune» (Microsoft self-verification, ACL-25 judge): детерминированная
грамматика ИЗВЛЕКАЕТ, LLM только РЕЖЕТ кандидатов, сверяя факт с его же цитатой —
галлюцинация значений невозможна, провенанс/воспроизводимость целы (T=0 + дисковый
кэш src/llm.py). Замерено на 30 размеченных журнальных фактах: accuracy 96.7%,
recall хороших 100% (промпт v2 c грамматическим владельцем числа).

verify_facts(facts, threads=16) -> (kept, dropped_count)
Верифицируются ТОЛЬКО числовые grammar-факты; сущности (Author/Claim/…) и
vision-факты проходят без LLM. Сбой вызова → факт СОХРАНЯЕТСЯ (fail-open: LLM
недоступен — деградируем к чистой грамматике, а не теряем данные).
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import llm
from src.obs import get_logger

log = get_logger("verify")

BATCH = 12

_SYSTEM = ("Ты — верификатор фактов для графа знаний по металлургии и "
           "материаловедению. Отвечай ТОЛЬКО валидным JSON-массивом, без "
           "пояснений и markdown.")

_PROMPT = """Ниже факты, автоматически извлечённые из журнальных статей по цветной металлургии.
Каждый факт: сущность (canon), метрика, значение с единицей, и дословная цитата-источник.

Для каждого факта реши keep=true/false.

АЛГОРИТМ (обязателен для каждого факта):
1. Найди в цитате число, равное value (учитывая диапазон/знак сравнения).
2. Определи, к какому веществу/параметру это число относится ГРАММАТИЧЕСКИ
   (что стоит сразу при числе: «10,3 % CaO» — это CaO; «концентрациях до 160 г/дм3»
   после «в соляной кислоте» — это концентрация кислоты; «добавками 0,5 % (Sc + Zr)» — это Sc+Zr).
3. Если владелец числа НЕ canon (и не очевидный синоним/соединение canon) — keep=false.
4. Иначе проверь категорию утверждения по правилам ниже.

keep=true ТОЛЬКО если цитата ЯВНО утверждает ТЕХНИЧЕСКОЕ значение, относящееся ИМЕННО к этой сущности:
- состав/содержание/концентрация вещества,
- режим процесса (температура, давление, pH, время, расход),
- показатель процесса (извлечение, выход, степень превращения),
- свойство материала.

keep=false если:
- РЫНОК: цена, прогноз, импорт/экспорт, производство/потребление в тоннах, динамика «+N% к году», биржи, доллары;
- ЧУЖАЯ СУЩНОСТЬ: владелец числа из шага 2 — не canon
  (например, «10,3 % CaO» приписано магнетиту; «>=60 % Sb» приписано золоту; «0,5 % (Sc+Zr)» приписано меди;
  концентрация кислоты-реагента приписана растворяемому металлу);
- ДОЛЯ/АССОЦИАЦИЯ canon — это НЕ чужая сущность: «до 85 % золота связано с пиритом» —
  число о золоте, keep=true;
- ГАРБЛ/НЕ-ТЕКСТ: обрывки таблиц, осей графиков, подписей рисунков, многоколоночная каша
  («0 1 2 3 4 5 6 7 8 pH», ряды температур без предложения);
- ССЫЛКИ/МЕТАДАННЫЕ: номера страниц, DOI, УДК, выходные данные журнала, библиография;
- значение физически бессмысленно для метрики (отрицательная температура процесса плавки и т.п.).

ВАЖНО: колонтитул в начале цитаты (год, № журнала, рубрика) — не повод отбрасывать,
если само предложение осмысленно и утверждает техпараметр.

Ответ — СТРОГО JSON-массив: [{{"id": <int>, "keep": true|false, "reason": "<кратко, до 10 слов>"}}]

ФАКТЫ:
{facts_json}"""


def _needs_verify(f: dict) -> bool:
    """Числовой grammar/mention-факт → верифицируем; сущности/vision — нет."""
    return ((f.get("value_low") is not None or f.get("value_high") is not None)
            and f.get("source") in ("grammar", "mention"))


def _view(i: int, f: dict) -> dict:
    lo, hi = f.get("value_low"), f.get("value_high")
    value = f"{lo}..{hi}" if (lo is not None and hi is not None and lo != hi) \
        else str(lo if lo is not None else hi)
    return {"id": i, "canon": f.get("canon"), "type": f.get("node_type"),
            "metric": f.get("metric"),
            "value": f"{f.get('comparator') or '='}{value}",
            "unit": f.get("unit_canon"),
            "quote": " ".join((f.get("quote") or "").split())[:400]}


def _judge_batch(batch):
    """[(idx, fact)] → {idx: keep}. Сбой → все keep (fail-open)."""
    fj = json.dumps([_view(i, f) for i, f in batch], ensure_ascii=False)
    try:
        raw = llm.chat(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _PROMPT.format(facts_json=fj)}],
            model=llm.FLASH_MODEL, temperature=0, max_tokens=1500,
            timeout=90, max_attempts=2)
        parsed = llm._extract_json(raw)
        if isinstance(parsed, list):
            return {int(v["id"]): bool(v.get("keep", True)) for v in parsed
                    if isinstance(v, dict) and "id" in v}
    except Exception as e:  # noqa: BLE001 — LLM недоступен → не теряем факты
        log.warning("verify batch fail-open: %s", str(e)[:120])
    return {i: True for i, _ in batch}


def verify_facts(facts, threads: int = 16):
    """Отфильтровать факты LLM-судьёй. Возвращает (kept_facts, dropped_count)."""
    numeric = [(i, f) for i, f in enumerate(facts) if _needs_verify(f)]
    if not numeric:
        return list(facts), 0
    batches = [numeric[i:i + BATCH] for i in range(0, len(numeric), BATCH)]
    verdicts = {}
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for res in ex.map(_judge_batch, batches):
            verdicts.update(res)
    kept, dropped = [], 0
    for i, f in enumerate(facts):
        if verdicts.get(i, True):
            kept.append(f)
        else:
            dropped += 1
    return kept, dropped


if __name__ == "__main__":  # смоук: 3 факта (2 плохих) через живой LLM
    demo = [
        {"canon": "цинк", "node_type": "Material", "metric": "содержание",
         "value_low": 60.0, "value_high": 60.0, "unit_canon": "pct", "source": "grammar",
         "comparator": "=", "quote": "560,0 цинк (+60,0 % к 2011 г.) стоимостью 1 млрд долл."},
        {"canon": "никель", "node_type": "Material", "metric": "содержание",
         "value_low": 15.7, "value_high": 15.7, "unit_canon": "pct", "source": "grammar",
         "comparator": "=", "quote": "Содержание никеля в концентрате составило 15,7 %."},
        {"canon": "магнетит", "node_type": "Material", "metric": "содержание",
         "value_low": 10.3, "value_high": 10.3, "unit_canon": "pct", "source": "grammar",
         "comparator": "=", "quote": "шлак содержал 10,3 % CaO при наличии магнетита"},
    ]
    kept, dropped = verify_facts(demo, threads=2)
    names = [f["canon"] for f in kept]
    print(f"kept={names} dropped={dropped}")
    assert "никель" in names and dropped >= 1, "верификатор не отсёк мусор"
    print("verify self-check OK")
