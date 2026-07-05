"""ЭТАП 0.5 — нормализация текста (чистые функции).

Чинит гомоглифы в единицах/формулах, склеивает переносы, убирает колонтитулы.
Импортирует общие пути/утилиты из src.config (nfc и т.п.), ничего не хардкодит.
"""
from __future__ import annotations

import re
from collections import Counter

from src.config import nfc  # noqa: F401  (общая утилита проекта; используется в normalize_text)

# --- Гомоглифы: кириллица -> латиница внутри единиц измерения ---------------
# Кириллические буквы, визуально совпадающие с латинскими.
# ВАЖНО: грамматика нативно понимает КИРИЛЛИЧЕСКИЕ единицы (мг/л, °С, А/м2, т/сут —
# золотой тест на кириллице проходит). Латинизация «м→m» ломала мг/мкм/м³/мВ/мкСм,
# «т→t» — т/сут, «к→k» — кА/кг. Оставляем только С→C (для °С→°C и normalize-теста).
_CYR2LAT = {
    "С": "C", "с": "c",
}

# Часто встречающиеся составные прилагательные (обе части значимы) — НЕ склеивать.
_COMPOUNDS = {
    "медно", "никелевый", "физико", "химический", "серно", "кислый",
    "окислительно", "восстановительный", "жидко", "твёрдый", "твердый",
    "газо", "жидкостный", "тепло", "массообмен", "электро", "химический",
    "гидро", "металлургический", "пиро", "цветной", "кобальто", "содержащий",
    "железо", "марганцево", "хромо", "титано", "рудно", "термический",
}


def fix_homoglyphs(text: str) -> str:
    """Нормализовать кириллично-латинскую путаницу в единицах/формулах.

    - Варианты градуса Цельсия «°C / °С / оС / 0С / o C» -> «°C».
    - Внутри токенов единиц измерения кириллические буквы, совпадающие с
      латинскими, приводятся к латинским (для стабильного матча).
    Обычные русские слова не трогаются.
    """
    if not isinstance(text, str):
        return text

    s = text

    # 1) Градус Цельсия: разные написания знака градуса + C/С.
    #    Знак градуса «°», либо буква «о/o/0» перед C/С как суррогат градуса.
    #    После числа: «100 °С», «100°C», «100 оС», «100 0С».
    #    Пробел между числом и знаком градуса сохраняем как есть.
    deg = "[°º˚]"  # °, º (masculine ordinal), ˚ (ring above)
    c_any = "[CСcс]"
    # число + [опц. пробелы, сохраняются] + знак градуса + C/С -> «°C»
    s = re.sub(
        r"(?<=\d)(\s*)" + deg + r"\s*" + c_any,
        r"\1°C",
        s,
    )
    # «оС»/«oС»/«0С» как суррогат градуса сразу после числа (без знака °)
    s = re.sub(
        r"(?<=\d)(\s*)[оo0]\s*[СC]\b",
        r"\1°C",
        s,
    )
    # уже стоящий знак градуса без числа перед ним: «°С» -> «°C»
    s = re.sub(deg + r"\s*" + c_any + r"\b", "°C", s)

    # 2) Токены-единицы: последовательности, где смешаны латиница/цифры/
    #    спецсимволы единиц и отдельные кириллические гомоглифы. Чиним ТОЛЬКО
    #    если токен содержит латинскую букву или знак единицы (/, ·, ^, ²…) и
    #    НЕ выглядит как обычное русское слово (нет длинного кир. фрагмента).
    def _fix_unit_token(m: re.Match) -> str:
        tok = m.group(0)
        # Признак единицы: наличие '/', степени, латиницы или знака градуса.
        if not re.search(r"[/·^²³°A-Za-z]", tok):
            return tok
        # Если в токене есть кириллический фрагмент из >=3 подряд кир. букв —
        # это, скорее всего, русское слово, не единица. Не трогаем.
        if re.search(r"[А-Яа-яЁё]{3,}", tok):
            return tok
        return "".join(_CYR2LAT.get(ch, ch) for ch in tok)

    # токен = буквы/цифры/спецсимволы единиц без пробелов
    s = re.sub(r"[A-Za-zА-Яа-яЁё0-9]+(?:[/·^²³°.\-][A-Za-zА-Яа-яЁё0-9]+)*", _fix_unit_token, s)

    return s


def _is_compound(first: str, second: str) -> bool:
    """Составное прилагательное (обе части значимы) — оставить с дефисом."""
    f = first.lower().strip()
    sec = second.lower().strip()
    return f in _COMPOUNDS and sec in _COMPOUNDS


def dehyphenate(text: str) -> str:
    """Склейка переносов «слово-\\nслово» -> «словослово».

    Эвристика: склеиваем, если вторая часть начинается со строчной буквы.
    Составные (обе части в списке частых составных) — оставляем с дефисом,
    заменяя перенос строки на пробел, чтобы слово не разрывалось.
    """
    if not isinstance(text, str):
        return text

    pattern = re.compile(
        r"([А-Яа-яЁёA-Za-z]+)-[ \t]*\n[ \t]*([а-яёa-z][А-Яа-яЁёA-Za-z]*)"
    )

    def _repl(m: re.Match) -> str:
        first, second = m.group(1), m.group(2)
        if _is_compound(first, second):
            # значимое составное: сохранить дефис, убрать перенос
            return f"{first}-{second}"
        # обычный перенос: склеить без дефиса
        return f"{first}{second}"

    return pattern.sub(_repl, text)


def _norm_line(line: str) -> str:
    """Нормализованный вид строки для сравнения колонтитулов: без цифр,
    свёрнутые пробелы, нижний регистр."""
    s = re.sub(r"\d+", "", line)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def strip_headers(pages: list[str]) -> list[str]:
    """Убрать колонтитулы.

    - Строки, повторяющиеся на >=30% страниц (по нормализованному виду без
      цифр), удаляются.
    - Одиночные строки из 1–4 цифр (номера страниц) на границах страницы
      удаляются.
    """
    if not pages:
        return pages

    n = len(pages)
    threshold = max(2, (n * 3 + 9) // 10)  # ceil(0.3 * n), но минимум 2

    # Собираем частоты нормализованных непустых строк по страницам.
    counter: Counter[str] = Counter()
    for page in pages:
        seen_on_page = set()
        for line in page.splitlines():
            norm = _norm_line(line)
            if norm and norm not in seen_on_page:
                seen_on_page.add(norm)
                counter[norm] += 1

    repeating = {k for k, v in counter.items() if v >= threshold}

    page_num_re = re.compile(r"^\s*\d{1,4}\s*$")

    out: list[str] = []
    for page in pages:
        lines = page.splitlines()
        kept: list[str] = []
        last_idx = len(lines) - 1
        for i, line in enumerate(lines):
            norm = _norm_line(line)
            # повторяющийся колонтитул
            if norm and norm in repeating:
                continue
            # одиночный номер страницы на границе (первая/последняя строка)
            if (i == 0 or i == last_idx) and page_num_re.match(line):
                continue
            kept.append(line)
        out.append("\n".join(kept))
    return out


def normalize_text(text: str, pages: list[str] | None = None):
    """Применить весь пайплайн нормализации.

    Если передан `pages` — сначала чистим колонтитулы постранично, затем к
    каждой странице применяем dehyphenate + fix_homoglyphs и возвращаем список
    страниц. Иначе работаем с одним текстом и возвращаем строку.
    """
    if pages is not None:
        cleaned = strip_headers(pages)
        return [fix_homoglyphs(dehyphenate(nfc(p))) for p in cleaned]

    return fix_homoglyphs(dehyphenate(nfc(text)))
