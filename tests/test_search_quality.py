"""Тесты хелперов качества выдачи search.py (по находкам тотального ревью).

Чистые функции — без Neo4j/LLM: чистка цитат, детект мусора, фильтр авторов,
санити значений, ложные факты-сравнения, форматирование чисел/диапазонов.
Гонять: pytest tests/test_search_quality.py -p no:randomly
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.search import (  # noqa: E402
    _clean_quote, _bad_quote, _is_junk_author, _implausible,
    _comparison_artifact, _fmt_value, _num,
)


# ── _clean_quote: убирает табличную разметку и подряд идущие повторы шапок ──
def test_clean_quote_collapses_table_header_repeats():
    raw = ("Номер опыта Содержание в шлаке, г/т Содержание в шлаке, г/т "
           "Содержание в шлаке, | Pt: 0,0162")
    out = _clean_quote(raw)
    assert "|" not in out
    # тройной повтор «Содержание в шлаке, г/т» схлопнут (осталось ≤2 вхождения)
    assert out.count("Содержание в шлаке") <= 2
    assert "Pt: 0,0162" in out


def test_clean_quote_strips_nbsp_and_newlines():
    assert _clean_quote("Класс\xa0100\nмкм") == "Класс 100 мкм"


def test_clean_quote_empty():
    assert _clean_quote("") == ""


# ── _bad_quote: 1 для вырожденной табличной цитаты, 0 для связного предложения ──
def test_bad_quote_flags_pipes_and_short():
    assert _bad_quote("A | B | C") == 1
    assert _bad_quote("три слова тут") == 1          # < 4 слов
    assert _bad_quote("") == 1


def test_bad_quote_accepts_real_sentence():
    q = ("Электролиз длился 24 часа при температуре 57 градусов и плотности "
         "тока около 200 ампер на квадратный метр")
    assert _bad_quote(q) == 0


def test_bad_quote_flags_repeated_ngram():
    assert _bad_quote("класс мкм класс мкм класс мкм класс мкм") == 1


# ── _is_junk_author: только чистая русская ФИО проходит ──
def test_is_junk_author_keeps_valid_ru_fio():
    # полная ФИО, только фамилия, слитные инициалы без финальной точки, дефисная
    for name in ("Евграфова А.К.", "Цымбулов Л.Б.", "Петров", "Иванов И.И",
                 "Иванов И.И.", "Сидоров-Кузнецов А.Б.", "Релевантов Р.В"):
        assert _is_junk_author(name) is False, name


def test_is_junk_author_drops_junk():
    junk = ["Gennadiy L.", "Henao M.", "Mackey J.", "Advantages T.V.", "Any U.S.",
            "Asia Д.К.", "Взаимосвязь A.И.", "Проведен С.", "Editors J.",
            "Achurra G.", "Российская Федерация", "Федерация И.О."]
    for name in junk:
        assert _is_junk_author(name) is True, name


# ── _implausible: физически невозможные значения ──
def test_implausible_bounds():
    assert _implausible({"unit": "degC", "value_low": -273.08, "value_high": -273.08})
    assert _implausible({"unit": "pct", "value_low": 14100, "value_high": 14100})
    assert _implausible({"unit": "pH", "value_low": 30, "value_high": 30})
    assert _implausible({"unit": "mg_L", "value_low": -5, "value_high": -5})


def test_implausible_accepts_normal():
    assert not _implausible({"unit": "degC", "value_low": 57, "value_high": 57})
    assert not _implausible({"unit": "pct", "value_low": 3.4, "value_high": 3.4})
    assert not _implausible({"unit": None, "value_low": 999999, "value_high": None})


# ── _comparison_artifact: число из сравнения-разности — ложный факт ──
def test_comparison_artifact_detects_difference():
    f = {"quote": "температура плавления никеля на 400 °C ниже, чем хрома"}
    assert _comparison_artifact(f) is True


def test_comparison_artifact_ignores_absolute():
    f = {"quote": "Электролиз при температуре 400 °C и плотности тока 200 А/м2"}
    assert _comparison_artifact(f) is False


# ── _fmt_value: инвертированные диапазоны нормализуются ──
def test_fmt_value_inverted_range():
    assert _fmt_value({"value_low": 100, "value_high": 12}) == "12–100"
    assert _fmt_value({"value_low": 5, "value_high": 5}) == "5"
    assert _fmt_value({"value_low": None, "value_high": 300}) == "≤300"
    assert _fmt_value({"value_low": 5, "value_high": None}) == "≥5"


# ── _num: без плавающего мусора, малые значения сохранены ──
def test_num_rounding():
    assert _num(0.8049999999999999) == "0.805"
    assert _num(100.0) == "100"
    assert _num(0.00028) == "0.00028"
    assert _num(19.355) == "19.355"


if __name__ == "__main__":  # быстрый прогон без pytest
    import types
    g = dict(globals())
    n = 0
    for name, fn in g.items():
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn(); n += 1
    print(f"OK — {n} тестов пройдено")
