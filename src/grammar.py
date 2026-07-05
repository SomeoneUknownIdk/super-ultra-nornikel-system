"""ЭТАП 3 — числовая грамматика (детерминированный парсер числовых фактов).

parse_values(sentence) -> list[dict]  — извлекает факты из предложения корпуса.
parse_query(text)      -> list[dict]  — ТА ЖЕ грамматика на пользовательском запросе.

Детерминизм критичен: никаких словарей с недетерминированным обходом на горячем
пути, только регулярки + фиксированные таблицы. Единицы канонизируются, value_low/
value_high хранятся ПРИВЕДЁННЫМИ; unit_raw — исходная строка единицы.
"""
from __future__ import annotations

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import nfc  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Морфология (лемматизация материалов/фаз). pymorphy3 опционален — есть фолбэк.
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pymorphy3

    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # pragma: no cover
    _MORPH = None


def _lemma(word: str) -> str:
    w = word.strip().lower().replace("ё", "е")
    if _MORPH is not None:
        try:
            return _MORPH.parse(word)[0].normal_form.replace("ё", "е")
        except Exception:
            return w
    return w


# ─────────────────────────────────────────────────────────────────────────────
# Единицы и канонизация.  key: (unit_canon, множитель, смещение)
# value_canon = value_raw * factor + offset   (offset нужен только для K→°C)
# Порядок важен: более длинные/специфичные шаблоны идут первыми.
# ─────────────────────────────────────────────────────────────────────────────
# Каждый элемент: (regex-альтернатива, canon, factor, offset)
_UNIT_TABLE = [
    # плотность тока
    (r"А/дм2|А/дм²|A/дм2", "A_m2", 100.0, 0.0),
    (r"А/м2|А/м²|A/м2|A/m2", "A_m2", 1.0, 0.0),
    # концентрации массовые/объёмные (кириллица + латинские варианты mg/L, g/L…)
    (r"мг/дм3|мг/дм³|мг/л|mg/dm3|mg/dm³|mg/[Ll]", "mg_L", 1.0, 0.0),
    (r"г/дм3|г/дм³|г/л|g/dm3|g/dm³|g/[Ll]", "mg_L", 1000.0, 0.0),
    # ppm: канон «ppm»; в водном контексте перекраивается в mg_L (_resolve_ppm)
    (r"ppm", "ppm", 1.0, 0.0),
    (r"г/т|g/t", "g_t", 1.0, 0.0),
    # расходы/производительность объёмные
    (r"дм³/мин|дм3/мин|л/мин", "m3_h", 0.06, 0.0),
    (r"м³/ч|м3/ч", "m3_h", 1.0, 0.0),
    (r"л/ч", "m3_h", 0.001, 0.0),
    # массовые производительности
    (r"т/сут", "t_day", 1.0, 0.0),
    (r"т/ч", "t_day", 24.0, 0.0),
    (r"т/год", "t_day", 1.0 / 365.0, 0.0),
    # температура (°C, ℃, а также «о/o + C/С» — кириллич./латин. буква-о и C)
    (r"°C|°С|[оo][CС]|℃", "degC", 1.0, 0.0),
    (r"K|К(?![а-яА-Я])", "degC", 1.0, -273.15),  # кельвин (латин/кирил), не «Ка…»
    # проценты (знак и словоформа: «0,3 процента», «5 процентов»)
    (r"%\s*(?:мас|об|отн|ат|вес)?\.?", "pct", 1.0, 0.0),
    (r"[Пп]роцент(?:ов|а|ы|у|е)?(?![а-яё])", "pct", 1.0, 0.0),
    # pH — не конвертируем
    (r"рН|pH|pH", "pH", 1.0, 0.0),
]

# Компилируем единый распознаватель единицы (первый матч выигрывает).
_UNIT_ALT = "|".join(f"(?P<u{i}>{pat})" for i, (pat, *_r) in enumerate(_UNIT_TABLE))
_UNIT_RE = re.compile(_UNIT_ALT)


# fix ppm-контекст: ppm = мг/л ТОЛЬКО в водном контексте (раствор/вода/
# электролит/жидк…/католит/анолит в предложении); иначе канон остаётся «ppm»
# (твёрдые фазы: соль, катодная медь и т.п.). Проверяем всё предложение —
# маркер может стоять и справа от значения («Fe <20 ppm в растворе»).
_AQUEOUS_RE = re.compile(r"\b(раствор|вод|электролит|жидк|католит|анолит)")


def _resolve_ppm(text: str, canon: str) -> str:
    """Канон для ppm по контексту: водный → mg_L, иначе ppm. Прочие каноны
    возвращаются как есть (фактор 1.0 одинаков, пересчёт не нужен)."""
    if canon != "ppm":
        return canon
    if _AQUEOUS_RE.search(text.lower().replace("ё", "е")):
        return "mg_L"
    return "ppm"


def _match_unit(text: str, start: int):
    """Ищет единицу начиная с позиции start (пропуская пробелы). Возвращает
    (unit_raw, canon, factor, offset, end_pos) либо None."""
    m = re.compile(r"\s*").match(text, start)
    pos = m.end() if m else start
    m = _UNIT_RE.match(text, pos)
    if not m:
        return None
    for i, (_pat, canon, factor, offset) in enumerate(_UNIT_TABLE):
        g = m.group(f"u{i}")
        if g is not None:
            return (g.strip(), canon, factor, offset, m.end())
    return None


# единицы, которые в тексте могут стоять ПЕРЕД числом («рН 2,5», «pH 2,5»,
# «pH = 2,5», «рН: 2,5», «pH составляет 2,5»). Допускаем разделитель [=:] и
# слово-связку «составляет/равен/равно/равна» в окне ~8 симв. слева.
# fix 3: опц. компаратор «pH > 10», «рН < 3», «pH ≤ 4», «pH >= 4».
_PREFIX_CMP = {"<": "<", ">": ">", "≤": "<=", "≥": ">=", "<=": "<=", ">=": ">="}
_PREFIX_UNIT_RE = re.compile(
    r"(рН|pH)\s*(?:([<>≤≥]=?)\s*)?(?:[=:]\s*)?"
    r"(?:составля\w*\s*|рав[внео]\w*\s*)?$",
    re.IGNORECASE,
)


def _match_prefix_unit(text: str, num_start: int):
    """Единица, стоящая слева от числа (случай pH). Окно ~16 симв. слева,
    допускает «pH = N», «рН: N», «pH составляет N» (связка ест окно), а также
    компаратор «pH > N» / «рН < N» → (comparator, mode).

    Возвращает (unit_raw, canon, factor, offset, comparator, mode) либо None.
    comparator/mode = (None,None) если компаратор не указан."""
    left = text[max(0, num_start - 16):num_start]
    m = _PREFIX_UNIT_RE.search(left)
    if not m:
        return None
    comparator, mode = None, None
    sym = m.group(2)
    if sym:
        comparator = _PREFIX_CMP.get(sym)
        mode = "low" if comparator in (">", ">=") else "high"
    return (m.group(1), "pH", 1.0, 0.0, comparator, mode)


# ─────────────────────────────────────────────────────────────────────────────
# Числа: десятичная запятая/точка; разряды пробелом («1 000», «12 345,6»).
# ─────────────────────────────────────────────────────────────────────────────
# число вида: 1 234 567,89  или  9,4  или  200  или  0.705
_NUM = r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"


def _to_float(raw: str) -> float:
    s = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    return float(s)


# Знак-компаратор, приклеенный вплотную слева к числу («<5», «≥300», «±2»).
_LEADING_CMP = {"<": "<", ">": ">", "≤": "<=", "≥": ">=", "±": "±"}
_LEADING_CMP_RE = re.compile(r"([<>≤≥±])\s*$")


def _leading_comparator(text: str, num_start: int):
    """Вернуть компаратор из символа вплотную слева от числа, либо None.

    '<5'→'<', '>5'→'>', '≤5'→'<=', '≥5'→'>=', '±2'→'±'. Иначе None."""
    left = text[max(0, num_start - 2):num_start]
    m = _LEADING_CMP_RE.search(left)
    if m:
        return _LEADING_CMP[m.group(1)]
    return None


# Разделители диапазона: – — - ÷ … ...
_RANGE_SEP = r"\s*(?:–|—|-|÷|…|\.\.\.)\s*"
_RANGE_RE = re.compile(rf"({_NUM}){_RANGE_SEP}({_NUM})")
_NUM_RE = re.compile(_NUM)

# ─────────────────────────────────────────────────────────────────────────────
# Метрики (лексикон). Каноническое имя → формы для поиска в тексте (по лемме).
# ─────────────────────────────────────────────────────────────────────────────
_METRIC_LEMMAS = {
    "концентрация": "концентрация",
    "содержание": "содержание",
    "остаточный": "содержание",  # «остаточное содержание» → содержание (лидирует содержание)
    "извлечение": "извлечение",
    "температура": "температура",
    "плотность": "плотность тока",  # «плотность тока»
    "скорость": "скорость",
    "расход": "расход",
    "производительность": "производительность",
    "остаток": "остаток",
    "ph": "pH",
    "рн": "pH",
}
# однословные ключи метрик, распознаваемые в canon degC/pH и т.п. по единице
_UNIT_METRIC = {"degC": "температура", "pH": "pH", "A_m2": "плотность тока"}

# ─────────────────────────────────────────────────────────────────────────────
# Материалы и фазы.
# ─────────────────────────────────────────────────────────────────────────────
_PHASES = {"штейн", "шлак", "файнштейн", "раствор", "католит", "анолит"}
# материалы: лемма → каноническая запись (в expect обычно совпадает с леммой)
_MATERIAL_CANON = {
    "сульфат": "сульфат",
    "сульфат-ион": "сульфат",
    "никель": "никель",
    "хлорид": "хлорид",
    "мпг": "МПГ",
    "медь": "медь",
    "кобальт": "кобальт",
    "железо": "железо",
    "цинк": "цинк",
    "серебро": "серебро",
    "золото": "золото",
    "платина": "платина",
}

# Латинские символы элементов → канон (для строк вида «52% Ni», «Ni 52%»).
# Регистрозависимо: символ должен совпасть точно (Cu, Ni, ...), чтобы не ловить
# слова. Двухбуквенные проверяем раньше однобуквенных.
_ELEMENT_SYMBOL = {
    "Cu": "медь", "Ni": "никель", "Fe": "железо", "Zn": "цинк",
    "Co": "кобальт", "Au": "золото", "Ag": "серебро", "Pt": "платина",
    "Pd": "палладий", "Sb": "сурьма", "Se": "селен", "Pb": "свинец",
    "As": "мышьяк", "S": "сера",
}
# Отсортированные символы: сначала длинные (двухбуквенные), потом однобуквенные.
_ELEMENT_SYMBOLS_SORTED = sorted(_ELEMENT_SYMBOL, key=len, reverse=True)
# Границы: символ не должен быть частью более длинного латинского слова.
_ELEMENT_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_ELEMENT_SYMBOLS_SORTED) + r")(?![A-Za-z])"
)


def _find_element_symbol(text: str, num_start: int, num_end: int):
    """Латинский символ элемента справа/слева от числа (в узком окне ~6 симв.)."""
    right = text[num_end:num_end + 6]
    left = text[max(0, num_start - 6):num_start]
    # приоритет справа («52% Ni»), затем слева («Ni 52%»)
    m = _ELEMENT_SYMBOL_RE.search(right)
    if m:
        return _ELEMENT_SYMBOL[m.group(1)]
    # слева берём последнее совпадение (ближайшее к числу)
    matches = list(_ELEMENT_SYMBOL_RE.finditer(left))
    if matches:
        return _ELEMENT_SYMBOL[matches[-1].group(1)]
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Компараторы.
# ─────────────────────────────────────────────────────────────────────────────
_CMP_LE = ["не более", "не выше", "не превышает", "до", "≤", "<=", "<", "менее чем"]
_CMP_GE = ["не менее", "не ниже", "от", "≥", ">=", ">", "свыше", "более", "выше"]
_CMP_APPROX = ["около", "порядка", "приблизительно", "примерно", "~", "≈"]
# глаголы достижения → '='
_ATTAIN = ["снизил", "снизили", "снижен", "довел", "довели", "доведен",
           "составил", "составило", "составила", "достиг", "достигл",
           "достигает", "поддержив", "равн"]
# Глаголы достижения ЦЕЛЕВОЙ величины: «довели/нагрели/подняли/достиг … до X °C»
# — это ТОЧКА (=), а не верхняя граница (<=). Проверяются в ветке «до».
_ATTAIN_TARGET = ["довел", "довели", "доведен", "нагрел", "нагрели", "нагрет",
                  "подня", "достиг", "достигл", "разогрел", "разогрет"]

# Библиография: blocklist ±3 токена
_BIB_BLOCK = ["с.", "стр.", "№", "т.", "vol.", "pp.", "doi", "issn", "тираж"]


# ─────────────────────────────────────────────────────────────────────────────
def _tokenize(text: str):
    """Простая токенизация со спанами: слова (буквы/цифры/пунктуация единиц)."""
    return list(re.finditer(r"[^\s]+", text))


def _window_words(text: str, center: int, left: int = 60):
    """Слова слева от позиции center (для резолвера метрики/материала/компаратора)."""
    seg = text[max(0, center - left):center]
    return re.findall(r"[A-Za-zА-Яа-яЁё\-]+", seg)


def _find_metric(text: str, num_start: int):
    """Ближайшая метрика слева от числа. Возвращает каноническое имя либо None."""
    seg = text[:num_start].lower().replace("ё", "е")
    words = re.findall(r"[a-zа-я\-]+", seg)
    # идём справа налево, лемматизируем, ищем первый ключ
    for w in reversed(words):
        lem = _lemma(w)
        if lem in _METRIC_LEMMAS:
            # спец: «плотность» → «плотность тока» только если рядом «ток»
            if _METRIC_LEMMAS[lem] == "плотность тока" and "ток" not in seg:
                continue
            return _METRIC_LEMMAS[lem]
        # прямое совпадение по словоформе (pH/рН)
        if w in ("ph", "рн"):
            return "pH"
        # глагол «содержал/содержит…» → метрика «содержание»
        if w.startswith("содерж"):
            return "содержание"
    return None


# fix (мис-привязка 1): разделитель перечисления — обрезаем правое окно на
# первом таком символе после числа, чтобы не «перепрыгнуть» к следующему
# элементу списка («…69,7%, Zn – 3,40» — Zn относится уже к цинку).
_ENUM_SEP_RE = re.compile(r"[,;–—-]")
# fix (мис-привязка 3): «% от стехиометрии» / «расход <реагент>» — не считаем
# это содержанием продукта.
_STOICH_RE = re.compile(r"от\s+стехиометри", re.IGNORECASE)
_RASHOD_REAGENT_RE = re.compile(r"расход\s+[а-яё\-]+\s*$", re.IGNORECASE)


def _is_stoichiometry_ratio(text: str, num_start: int, num_end: int) -> bool:
    """True для «расход алюминия 120% от стехиометрии» и «расход <реагента> N%»:
    величина — доля от стехиометрии/расход реагента, а НЕ содержание продукта."""
    right = text[num_end:num_end + 25]
    if _STOICH_RE.search(right):
        return True
    left = text[max(0, num_start - 40):num_start].lower().replace("ё", "е")
    if _RASHOD_REAGENT_RE.search(left):
        return True
    return False


def _left_materials(left_seg: str):
    """Материалы левого окна как список (char_pos, canon, is_matrix) в порядке
    появления. «Матрица» — материал, которому (через прилагательные) предшествует
    предлог «в/во» («серебра в катодной меди» → медь=матрица, серебро — нет)."""
    out = []
    toks = list(re.finditer(r"[a-zа-яё\-]+", left_seg))
    words = [t.group(0) for t in toks]
    for i, w in enumerate(words):
        lem = _lemma(w)
        canon = _MATERIAL_CANON.get(lem) or _MATERIAL_CANON.get(w)
        if canon is None:
            continue
        is_matrix = False
        j = i - 1
        while j >= 0:
            tj = words[j]
            if tj in ("в", "во"):
                is_matrix = True
                break
            if tj in ("и", "с", "со", "на", "из", "от", "по", "к", "о", "об", "у"):
                break
            j -= 1
        out.append((toks[i].start(), canon, is_matrix))
    return out


def _find_material(text: str, num_start: int, num_end: int):
    """Ближайший материал. Приоритет:
      1) левый материал в ~15 симв. до числа (если есть, не матрица);
      2) правый материал, но правое окно обрезано на первом разделителе
         перечисления после числа (не «перепрыгиваем» к следующему элементу);
      3) остальной левый материал (с игнором матрицы после «в/во»);
      4) фолбэк — латинский символ элемента (в том же обрезанном правом окне)."""
    left_start = max(0, num_start - 80)
    left_full = text[left_start:num_start].lower().replace("ё", "е")
    # правое окно: обрезаем на первом разделителе перечисления после числа
    right_raw = text[num_end:num_end + 80]
    sep = _ENUM_SEP_RE.search(right_raw)
    right_end = sep.start() if sep else len(right_raw)
    right_seg = right_raw[:right_end].lower().replace("ё", "е")

    # материалы левого окна с признаком «матрица» (после «в/во»).
    lefts = _left_materials(left_full)
    # порог «вблизи числа» ~15 симв.: абсолютная позиция начала слова.
    near_cut = num_start - 15 - left_start

    # (1) приоритет левому НЕ-матричному материалу вблизи числа (~15 симв.)
    for pos, canon, is_matrix in reversed(lefts):
        if is_matrix:
            continue
        if pos >= near_cut:
            return canon
        break  # ближайший не-матрица дальше 15 симв. — сперва пробуем правое окно

    # (2) правый материал в обрезанном окне
    for w in re.findall(r"[a-zа-яё\-]+", right_seg):
        lem = _lemma(w)
        if lem in _MATERIAL_CANON:
            return _MATERIAL_CANON[lem]
        if w in _MATERIAL_CANON:
            return _MATERIAL_CANON[w]

    # (3) остальной левый НЕ-матричный материал (широкое окно)
    for pos, canon, is_matrix in reversed(lefts):
        if not is_matrix:
            return canon

    # (4) фолбэк: латинский символ элемента — только в обрезанном правом окне
    #     и слева (чтобы «…%, Zn – 3,40» не утёк символ Zn следующего элемента).
    right_sym_end = num_end + right_end
    m = _ELEMENT_SYMBOL_RE.search(text[num_end:right_sym_end])
    if m:
        return _ELEMENT_SYMBOL[m.group(1)]
    left6 = text[max(0, num_start - 6):num_start]
    matches = list(_ELEMENT_SYMBOL_RE.finditer(left6))
    if matches:
        return _ELEMENT_SYMBOL[matches[-1].group(1)]
    return None


def _find_phase(text: str, num_start: int, num_end: int):
    seg = (text[max(0, num_start - 80):num_start] + " " +
           text[num_end:num_end + 80]).lower().replace("ё", "е")
    words = re.findall(r"[a-zа-я\-]+", seg)
    for w in words:
        lem = _lemma(w)
        if lem in _PHASES:
            return lem
        if w in _PHASES:
            return w
    return None


def _detect_comparator(text: str, num_start: int, is_range: bool):
    """Определить компаратор по контексту слева от числа.

    Возвращает (comparator, mode) где mode ∈ {'point','low','high'}:
      point — одно значение, low==high (=)
      low   — только value_low (>=, >)
      high  — только value_high (<=, <)
    Диапазон всегда point-подобен (low/high задаются числами).
    """
    if is_range:
        # fix 3: компаратор слева от диапазона.
        #   «не более X–Y» → ('<=','range_high')  — оставляем только value_high=Y
        #   «не менее X–Y» → ('>=','range_low')   — оставляем только value_low=X
        #   иначе обычный диапазон.
        left = text[max(0, num_start - 40):num_start].lower().replace("ё", "е")
        tail = left.rstrip()
        for ph in ("не более", "не выше", "не превышает", "до", "≤", "<=", "<"):
            if tail.endswith(ph):
                return "<=", "range_high"
        for ph in ("не менее", "не ниже", "от", "≥", ">=", ">", "свыше"):
            if tail.endswith(ph):
                return ">=", "range_low"
        return None, "range"
    # окно слева (до 40 символов) в нижнем регистре
    left = text[max(0, num_start - 40):num_start].lower().replace("ё", "е")
    left_words = re.findall(r"[a-zа-я≤≥<>~≈]+|[≤≥<>~≈]", left)

    # «от X до Y» обрабатывается на уровне диапазона; здесь одиночное число.
    # Проверяем есть ли «от» непосредственно перед числом (→ >=, low)
    tail = left.rstrip()

    def ends_with(phrase):
        return tail.endswith(phrase)

    # символьные компараторы вплотную (fix 4: <,>,≤,≥,± приклеены к числу)
    if re.search(r"±\s*$", tail):
        return "±", "point"
    if re.search(r"[≤]\s*$", tail) or re.search(r"<=\s*$", tail) or re.search(r"<\s*$", tail):
        return ("<=" if ("≤" in tail or "<=" in tail) else "<"), "high"
    if re.search(r"[≥]\s*$", tail) or re.search(r">=\s*$", tail) or re.search(r">\s*$", tail):
        return (">=" if ("≥" in tail or ">=" in tail) else ">"), "low"

    # многословные фразы (проверяем «не более/не менее» раньше «более/менее»)
    for ph in ("не более", "не выше", "не превышает"):
        if ends_with(ph):
            return "<=", "high"
    for ph in ("не менее", "не ниже"):
        if ends_with(ph):
            return ">=", "low"
    if ends_with("до"):
        # «до X»: глагол достижения цели слева («нагрели до 90 °C») → точка '='.
        wider = text[:num_start].lower().replace("ё", "е")
        wwords = re.findall(r"[a-zа-я]+", wider)
        for w in wwords:
            for stem in _ATTAIN_TARGET:
                if w.startswith(stem):
                    return "=", "point"
        # «от X до Y» — обработан на уровне диапазона; здесь одиночная верхняя граница.
        return "<=", "high"
    if ends_with("от"):
        return ">=", "low"
    for ph in ("свыше", "более", "выше", "превышает"):
        if ends_with(ph):
            return ">", "low"
    for ph in ("менее", "ниже"):
        if ends_with(ph):
            return "<", "high"
    # «около/порядка/примерно/~/≈» → приблизительное значение, канон '~'
    for ph in _CMP_APPROX:
        if ph in ("~", "≈"):
            if ph in tail:
                return "~", "point"
        elif ends_with(ph):
            return "~", "point"

    # глагол достижения где-то слева в предложении → '='
    wider = text[:num_start].lower().replace("ё", "е")
    wwords = re.findall(r"[a-zа-я]+", wider)
    for w in wwords:
        for stem in _ATTAIN:
            if w.startswith(stem):
                return "=", "point"

    # по умолчанию точное значение
    return "=", "point"


def _is_bibliography(text: str, num_start: int, num_end: int):
    """True если число окружено библиографическими маркерами (±3 токена) —
    отбрасываем. «руб.» блокируем только рядом с ценой/тиражом."""
    lo = text[max(0, num_start - 30):num_start].lower()
    hi = text[num_end:num_end + 30].lower()
    ctx = lo + " " + hi
    for marker in _BIB_BLOCK:
        if marker in ctx:
            return True
    return False


# Матрица совместимости метрика↔единица (A): какие единицы допустимы у метрики.
_METRIC_UNITS = {
    "температура": {"degC"}, "плотность тока": {"A_m2"},
    "расход": {"m3_h"}, "скорость": {"m3_h"}, "производительность": {"t_day"},
    "pH": {"pH"}, "остаток": {"mg_L", "ppm"},
    "содержание": {"mg_L", "g_t", "pct", "mol_L", "ppm"},
    "концентрация": {"mg_L", "g_t", "pct", "mol_L", "ppm"},
    "извлечение": {"pct"}, "выход": {"pct"},
}
# Обратная инференция: единица → наиболее вероятная метрика.
_UNIT_METRIC = {"degC": "температура", "A_m2": "плотность тока", "m3_h": "расход",
                "t_day": "производительность", "pH": "pH"}

def _harmonize_metric(metric, unit):
    """Если метрика несовместима с единицей — заменить на выведенную из единицы.
    Убирает «содержание 1299°C» → «температура 1299°C»."""
    if not unit:
        return metric
    allowed = _METRIC_UNITS.get(metric)
    if allowed and unit in allowed:
        return metric                       # совместимо — не трогаем
    if unit in _UNIT_METRIC:                 # T/ток/расход/pH — однозначны
        return _UNIT_METRIC[unit]
    if unit in ("mg_L", "g_t", "pct", "mol_L", "ppm"):   # концентрационные единицы
        if metric in ("содержание", "концентрация", "извлечение",
                      "выход", "остаток"):
            return metric
        # fix 2: голый % без слова-метрики НЕ метим «содержание» (мусор:
        # доли/потери/выход/убыль). Если метрика вообще не найдена — оставляем
        # None (confidence упадёт до 0.6); иначе метрику из «прочих» не трогаем.
        if metric is None:
            return None
        return "содержание"
    return metric


# fix 7: сопутствующие условия. Лёгкие регулярки для T / pH / времени в
# предложении — их со-извлекаем в поле conditions (для реификации Experiment
# и гейтов противоречий). Собственный спан факта исключается.
_COND_TEMP_RE = re.compile(
    rf"({_NUM})\s*(?:°\s*[CСc]|[оo][CС]|℃|градус\w*)", re.IGNORECASE)
_COND_PH_RE = re.compile(
    rf"(?:рН|pH)\s*(?:[=:]\s*)?(?:составля\w*\s*|рав[внео]\w*\s*)?({_NUM})",
    re.IGNORECASE)
_COND_TIME_RE = re.compile(
    rf"({_NUM})\s*(мин(?:ут\w*)?|ч(?:ас\w*)?|сек(?:унд\w*)?|сут(?:ок|ки)?)\b",
    re.IGNORECASE)
_COND_TIME_UNIT = {"мин": "min", "ч": "h", "сек": "s", "сут": "day"}


def _cond_time_canon(raw_unit: str) -> str:
    u = raw_unit.lower()
    if u.startswith("мин"):
        return "min"
    if u.startswith("сек"):
        return "s"
    if u.startswith("сут"):
        return "day"
    if u.startswith("ч"):
        return "h"
    return u


def _find_conditions(text: str, own_start: int, own_end: int):
    """Со-извлечь сопутствующие величины предложения (T, pH, время).

    Возвращает список словарей {kind, value, unit_canon} для всех совпадений,
    НЕ пересекающихся со спаном самого факта. Пусто → None."""
    conds = []

    def not_own(mstart, mend):
        return mend <= own_start or mstart >= own_end

    for m in _COND_TEMP_RE.finditer(text):
        if not_own(m.start(), m.end()):
            conds.append({"kind": "temperature",
                          "value": _to_float(m.group(1)),
                          "unit_canon": "degC"})
    for m in _COND_PH_RE.finditer(text):
        if not_own(m.start(), m.end()):
            conds.append({"kind": "pH",
                          "value": _to_float(m.group(1)),
                          "unit_canon": "pH"})
    for m in _COND_TIME_RE.finditer(text):
        if not_own(m.start(), m.end()):
            conds.append({"kind": "time",
                          "value": _to_float(m.group(1)),
                          "unit_canon": _cond_time_canon(m.group(2))})
    return conds or None


def _emit_fact(text, num_start, num_end, value_low, value_high, comparator, mode,
               unit_raw, canon, factor, offset):
    """Собрать один факт-словарь с канонизацией значений."""
    metric = _find_metric(text, num_start)
    if metric is None and canon in _UNIT_METRIC:
        metric = _UNIT_METRIC[canon]

    # fix (мис-привязка 3): «% от стехиометрии» / «расход <реагент> N%» —
    # это доля/расход реагента, а не содержание продукта. Не эмитим материал
    # и переводим метрику в «расход реагента» (гармонизация не сделает содержанием).
    if canon == "pct" and _is_stoichiometry_ratio(text, num_start, num_end):
        material = None
        phase = None
        fact = {
            "metric": "расход реагента",
            "comparator": comparator,
            "unit_canon": canon,
            "unit_raw": unit_raw,
            "span": [num_start, num_end],
        }

        def _cv(v):
            if v is None:
                return None
            r = round(v * factor + offset, 6)
            return int(r) if abs(r - round(r)) < 1e-9 else r

        _vl, _vh = _cv(value_low), _cv(value_high)
        if mode in ("range", "point"):
            fact["value_low"] = _vl
            fact["value_high"] = _vh
            if mode == "range":
                fact["comparator"] = "="
        elif mode == "range_high" or mode == "high":
            fact["value_high"] = _vh
        elif mode == "range_low" or mode == "low":
            fact["value_low"] = _vl
        fact["confidence"] = 0.6
        return fact

    material = _find_material(text, num_start, num_end)
    phase = _find_phase(text, num_start, num_end)

    def canon_val(v):
        if v is None:
            return None
        return round(v * factor + offset, 6)

    vl = canon_val(value_low)
    vh = canon_val(value_high)
    # нормализация целых
    def clean(v):
        if v is None:
            return None
        return int(v) if abs(v - round(v)) < 1e-9 else v
    # (harmonize применяется ниже)

    vl = clean(vl)
    vh = clean(vh)

    metric = _harmonize_metric(metric, canon)   # матрица метрика↔единица (A)
    fact = {
        "metric": metric,
        "comparator": comparator,
        "unit_canon": canon,
        "unit_raw": unit_raw,
        "span": [num_start, num_end],
    }
    # значения по режиму
    descending = False  # fix 5: нисходящий диапазон lo>hi (OCR-порча вроде 300→30)
    if mode == "range":
        fact["value_low"] = vl
        fact["value_high"] = vh
        fact["comparator"] = "="
        if vl is not None and vh is not None and vl > vh:
            descending = True
    elif mode == "range_high":
        # fix 3: «не более X–Y» → верхняя граница = Y
        fact["value_high"] = vh
    elif mode == "range_low":
        # fix 3: «не менее X–Y» → нижняя граница = X
        fact["value_low"] = vl
    elif mode == "point":
        fact["value_low"] = vl
        fact["value_high"] = vh
    elif mode == "low":
        fact["value_low"] = vl
    elif mode == "high":
        fact["value_high"] = vh

    if material:
        fact["material"] = material
    if phase:
        fact["phase"] = phase

    # fix 7: сопутствующие условия предложения (T, pH, время) для реификации.
    conditions = _find_conditions(text, num_start, num_end)
    if conditions:
        fact["conditions"] = conditions

    # confidence: 1.0 если метрика И единица в одном предложении
    conf = 1.0 if (metric and canon) else 0.6
    if descending:                 # fix 5: не дропаем, понижаем до 0.5
        conf = 0.5
    fact["confidence"] = conf
    return fact


def _parse(text: str) -> list:
    text = nfc(text)
    facts = []
    consumed = []  # интервалы уже разобранных чисел

    def overlaps(a, b):
        for (x, y) in consumed:
            if not (b <= x or a >= y):
                return True
        return False

    # 1) «от X до Y» → диапазон
    for m in re.finditer(rf"\bот\s+({_NUM})\s+до\s+({_NUM})", text, re.IGNORECASE):
        s, e = m.start(), m.end()
        if overlaps(s, e):
            continue
        lo = _to_float(m.group(1))
        hi = _to_float(m.group(2))
        u = _match_unit(text, e)
        if u:
            unit_raw, canon, factor, offset, _uend = u
        else:
            # fix 4: диапазон без хвостовой единицы — пробуем префикс pH слева.
            pre = _match_prefix_unit(text, s)
            if not pre:
                continue
            unit_raw, canon, factor, offset, _pc, _pm = pre
        canon = _resolve_ppm(text, canon)  # ppm → mg_L только в водном контексте
        if _is_bibliography(text, s, e):
            continue
        facts.append(_emit_fact(text, s, e, lo, hi, "=", "range",
                                unit_raw, canon, factor, offset))
        consumed.append((s, e))

    # 2) диапазоны «X–Y», «X÷Y», «X…Y»
    for m in _RANGE_RE.finditer(text):
        s, e = m.start(), m.end()
        if overlaps(s, e):
            continue
        lo = _to_float(m.group(1))
        hi = _to_float(m.group(2))
        u = _match_unit(text, e)
        if u:
            unit_raw, canon, factor, offset, _uend = u
        else:
            # fix 4: диапазон без хвостовой единицы — пробуем префикс pH слева.
            pre = _match_prefix_unit(text, s)
            if not pre:
                continue
            unit_raw, canon, factor, offset, _pc, _pm = pre
        canon = _resolve_ppm(text, canon)  # ppm → mg_L только в водном контексте
        if _is_bibliography(text, s, e):
            continue
        # fix 3: компаратор слева от диапазона («не более X–Y» → high=Y,'<=')
        rcmp, rmode = _detect_comparator(text, s, True)
        if rmode in ("range_high", "range_low"):
            facts.append(_emit_fact(text, s, e, lo, hi, rcmp, rmode,
                                    unit_raw, canon, factor, offset))
        else:
            facts.append(_emit_fact(text, s, e, lo, hi, "=", "range",
                                    unit_raw, canon, factor, offset))
        consumed.append((s, e))

    # 3) одиночные числа с единицей
    for m in _NUM_RE.finditer(text):
        s, e = m.start(), m.end()
        if overlaps(s, e):
            continue
        val = _to_float(m.group(0))
        prefix_cmp = None
        prefix_mode = None
        u = _match_unit(text, e)
        if u:
            unit_raw, canon, factor, offset, _uend = u
        else:
            pre = _match_prefix_unit(text, s)
            if not pre:
                continue  # число без единицы отбрасываем (библиография/шум)
            unit_raw, canon, factor, offset, prefix_cmp, prefix_mode = pre
        canon = _resolve_ppm(text, canon)  # ppm → mg_L только в водном контексте
        if _is_bibliography(text, s, e):
            continue
        # fix 3: префиксный компаратор pH («pH > 10») задаёт mode high/low
        # и берёт верх над контекстным резолвером.
        if prefix_cmp is not None:
            comparator, mode = prefix_cmp, prefix_mode
        else:
            comparator, mode = _detect_comparator(text, s, False)
        # fix 4: «X <ед.> или выше» справа от значения → нижняя граница '>='
        # (только поверх дефолтной точки '=', явные компараторы не трогаем)
        if comparator == "=" and mode == "point":
            right_from = _uend if u else e
            if re.match(r"\s*или\s+выше", text[right_from:right_from + 16]):
                comparator, mode = ">=", "low"
        facts.append(_emit_fact(text, s, e, val, val, comparator, mode,
                                unit_raw, canon, factor, offset))
        consumed.append((s, e))

    # финальный фильтр: факт без метрики И без единицы — шум
    facts = [f for f in facts if f.get("unit_canon")]
    facts.sort(key=lambda f: f["span"][0])
    return facts


# ─────────────────────────────────────────────────────────────────────────────
# Публичный API
# ─────────────────────────────────────────────────────────────────────────────
def parse_values(sentence: str) -> list:
    """Извлечь числовые факты из предложения корпуса."""
    return _parse(sentence)


def parse_query(text: str) -> list:
    """ТА ЖЕ грамматика на пользовательском запросе (симметрия)."""
    return _parse(text)


if __name__ == "__main__":  # ручная проба
    import json

    for s in [
        "остаточное содержание сульфат-ионов составило 9,4 мг/л",
        "снизили концентрацию сульфата до 200 мг/л",
        "плотность тока 200–240 А/м2 представляет компромисс",
    ]:
        print(s)
        print(json.dumps(parse_values(s), ensure_ascii=False, indent=2))
