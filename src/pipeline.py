"""Интеграция: docs.text → (normalize → gazetteer mentions → grammar facts → 4-мини rel)
→ facts.jsonl + edges.jsonl → Neo4j. Глубокое извлечение только для плотных доков (kg_value>=3).
4-мини: глагольные паттерны над упоминаниями (source=pattern) — рёбра ТЗ без всякого LLM.
"""
from __future__ import annotations
import os, sys, json, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import DOCS_META, DOCS_TEXT, FACTS, EDGES, PIPELINE_VERSION
from src.normalize import normalize_text
from src.gazetteer import Matcher
from src.grammar import parse_values

SENT = re.compile(r"(?<=[.!?;])\s+(?=[А-ЯA-ZЁ0-9])|\n{2,}")

# --- (Дефект 1) Матрица совместимости метрика ↔ ТИП сущности ------------------
# Числовой факт данной метрики может быть привязан только к сущности
# совместимого типа. Метрики, не перечисленные здесь, ограничений не имеют.
COMPAT = {
    "температура":       {"Process", "Equipment", "Phase"},
    "плотность тока":    {"Process", "Equipment"},
    "расход":            {"Process", "Equipment"},
    "производительность": {"Process", "Equipment", "Facility"},
    "pH":                {"Phase", "Process"},
    "содержание":        {"Material", "Phase"},
    "концентрация":      {"Material", "Phase"},
    "извлечение":        {"Material", "Process"},
    "остаток":           {"Material", "Phase"},
}
# (Дефект 1, исключение) «температура плавления/кипения» рядом с материалом —
# это свойство материала, оставляем Material несмотря на матрицу.
_MELT_RE = re.compile(r"температур\w*\s+(плавлен|кипен|плавк\w*\s+металл)", re.I)

# --- (Дефект 3) Продукты/выходы: при глаголе получения → produces_output ------
_OUTPUT_MATS = {"штейн", "шлак", "файнштейн", "огарок", "кек", "возгон"}

# --- (Остаточный 1) Глаголы контекста для 4-мини рёбер (локально, по спану) ----
# Каждый глагол несёт ОДНО направление. Ближайший к материалу глагол определяет
# тип ребра, вместо глобальных булевых has_uses/has_produces сразу обоих.
_VERB_USES = re.compile(r"выщелачива|плавк|плавл|флотаци|обраб|раствор|экстрак|перераб|загруж|подаю|подают|подач", re.I)
_VERB_PRODUCES = re.compile(r"получа|образ[уо]|выход|извлека|производ|выпуск|выплавля", re.I)

# --- (Остаточный 3) Интрудер-проценты: значения с левым квалификатором или
# конструкцией «N% которого …» не участвуют в enum-паросочетании составов -------
_LEFT_QUALIFIER = re.compile(
    r"(?:^|[\s(,;])(?:до|более|менее|на|около|порядка|свыше|потер\w*|выход\w*)\s*$",
    re.I,
)
_WHICH_OF = re.compile(r"^\s*котор", re.I)  # «N% которого/которой/которых …»

# --- (ТЗ п.1) АВТОРЫ → ЭКСПЕРТЫ -----------------------------------------------
# ФИО в двух порядках: «Фамилия И.О.» и «И.О. Фамилия». Инициалы — 1–2 буквы с
# точками (И.О. / И. О. / И.). Фамилия — Заглавная + строчные (кириллица/латиница),
# допускается дефис (Иванов-Петров). Точка после каждого инициала обязательна,
# чтобы не ловить «В Норильске» (предлог+топоним).
_INI = r"[А-ЯЁA-Z]\.\s*(?:[А-ЯЁA-Z]\.\s*)?"
_SURNAME = r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?"
_AUTHOR_SURNAME_FIRST = re.compile(rf"\b({_SURNAME})\s+({_INI})")   # Иванов И.О.
_AUTHOR_INITIALS_FIRST = re.compile(rf"\b({_INI})({_SURNAME})\b")   # И.О. Иванов
# Заведомо не-фамилии (частые слова с заглавной в начале предложения / аббревиатуры).
_NOT_SURNAME = {
    "Рис", "Табл", "Таблица", "Рисунок", "При", "Для", "Как", "Что", "Это",
    "Так", "Все", "Из", "По", "На", "От", "До", "Данные", "Результаты",
    # частые нарицательные/роли/разделы, встающие с заглавной перед инициалами
    "Геофизика", "Геология", "Геомеханика", "Спасибо", "Ведущий", "Автор",
    "Авторы", "Докладчик", "Руководитель", "Научный", "Инженер", "Доктор",
    "Профессор", "Кандидат", "Заведующий", "Начальник", "Директор", "Глава",
    "Работу", "Работа", "Доклад", "Выполнил", "Подготовил", "Рецензент",
    "Институт", "Лаборатория", "Кафедра", "Отдел", "Секция", "Тема", "Цель",
    "Введение", "Заключение", "Выводы", "Аннотация", "Реферат",
    # частые слова заголовков статей (Влияние X…, Изучение Y…) — не фамилии
    "Влияние", "Изучение", "Исследование", "Разработка", "Применение",
    "Использование", "Оценка", "Анализ", "Определение", "Особенности",
    "Обоснование", "Совершенствование", "Повышение", "Снижение", "Получение",
    "Технология", "Методика", "Способ", "Опыт", "Перспективы", "Проблемы",
    "Современные", "Основные", "Новый", "Новые", "Комплексный",
    # (Точность 3) артефакты верстки/ролей, дающих ложных авторов
    "Текст", "Младший", "Старший", "Ведущая", "Общие", "Прочие",
    # английские артикли/служебные (в EN-заголовках)
    "The", "This", "That", "These", "Fig", "Figure", "Table", "Abstract",
    "Study", "Analysis", "Effect", "Influence", "Development", "Review",
    # измерительные существительные в начале предложений тела («Содержание меди … В.Г.»)
    "Содержание", "Плотность", "Температура", "Извлечение", "Концентрация",
    "Масса", "Скорость", "Расход", "Давление", "Напряжение", "Мощность",
    "Объём", "Объем", "Выход", "Потери", "Степень", "Величина", "Значение",
    # структурные разделы документа
    "Оглавление", "Содержание", "Список", "Литература", "Приложение",
    "Раздел", "Глава", "Рисунок", "Формула", "Уравнение", "Примечание",
}

# --- (ТЗ п.2) ЭКСПЕРИМЕНТ → ПОКАЗАЛ → ЭФФЕКТ ----------------------------------
# Явные маркеры вывода. После маркера берём краткий текст вывода (до конца
# предложения/точки), нормализуем и обрезаем до 120 символов → Claim.
_CLAIM_MARK = re.compile(
    r"(показал[аи]?|показано|установлено|выявлено|доказано|получен\s+эффект|"
    r"получены?\s+результат\w*)"
    r"(?:[,\s]+(?:что|о\s+том,?\s+что))?\s*[,:—-]?\s*",
    re.I,
)


def _dist(a, b):  # расстояние между спанами
    return 0 if (a[0] <= b[1] and b[0] <= a[1]) else min(abs(a[0]-b[1]), abs(b[0]-a[1]))


def _quote_window(sent, span):
    """(Точность 2) Провенанс-цитата: окно ±120 символов ВОКРУГ спана факта,
    а не sent[:240] — иначе у длинных предложений само число выпадает из
    цитаты. При обрезке края помечаются «…».
    """
    span = span or [0, 0]
    start = max(0, span[0] - 120)
    end = min(len(sent), span[1] + 120)
    q = sent[start:end]
    if start > 0:
        q = "…" + q
    if end < len(sent):
        q = q + "…"
    return q


def _is_intruder(f, sent):
    """(Остаточный 3) True, если значение НЕ является чистым слотом состава:
    имеет левый квалификатор (до/более/менее/на/около/свыше/потери/выход…) или
    непунктовый comparator, либо за ним следует «которого/которой…». Такие
    значения искажают позиционный enum и исключаются из паросочетания.
    """
    if f.get("comparator") not in ("=", None):
        return True
    span = f.get("span") or [0, 0]
    left = sent[max(0, span[0] - 24):span[0]]
    if _LEFT_QUALIFIER.search(left):
        return True
    # текст сразу справа от значения (после единицы/%): «… которого …»
    right = sent[span[1]:span[1] + 16]
    right = re.sub(r"^\s*%\s*(?:мас|об|отн|ат|вес)?\.?\s*", "", right)
    if _WHICH_OF.search(right):
        return True
    return False


def _is_output_mat(mention):
    """(Дефект 3) True если материал/фаза — продукт передела (штейн, шлак…)."""
    c = (mention.get("canon") or "").lower().replace("ё", "е")
    return c in _OUTPUT_MATS


def _blocked_by_foreign_value(f, m, vals):
    """(Точность 1) True, если между спаном факта f и спаном сущности m лежит
    спан ДРУГОГО числового факта предложения: привязка f→m «перепрыгивала» бы
    через чужое значение, у сущности m «своё» число ближе. Пример бенчмарка:
    «содержание мышьяка … 10 % при 500-700 °C и <0,5 % О2» — 0,5 % не должно
    уходить к мышьяку через головы 10 % (и температуры).
    """
    if not vals:
        return False
    fspan = f.get("span") or [0, 0]
    mspan = m.get("span") or [0, 0]
    lo = min(fspan[1], mspan[1])   # конец левого спана
    hi = max(fspan[0], mspan[0])   # начало правого спана
    if lo >= hi:
        return False               # смежные/пересекающиеся спаны — зазора нет
    for g in vals:
        if g is f:
            continue
        gspan = g.get("span") or [0, 0]
        if gspan == fspan:
            continue
        if lo <= gspan[0] and gspan[1] <= hi:
            return True
    return False


def _resolve_entity(f, mentions, vals=None):
    """(Дефект 1) Ближайшая сущность СОВМЕСТИМОГО с метрикой типа.

    Возвращает mention-dict либо None (факт без сущности → пропустить).
    Правила:
      * метрика с ограничением в COMPAT → берём ближайшую сущность
        разрешённого типа; если такой в предложении нет — НЕ форсируем на
        несовместимую (исключение: температура плавления/кипения + Material).
      * метрика без ограничения → ближайшая из базовых типов (старое поведение).
      * (Точность 1) кандидат, до которого пришлось бы «перепрыгнуть» через
        спан ЧУЖОГО числового факта (vals — все значения предложения),
        пессимизируется: у той сущности своё число ближе. Для Material/Phase
        (слоты состава) блокировка окончательна — иначе «<0,5% О2» уйдёт к
        мышьяку. Для Process/Equipment блокированный кандидат остаётся
        ПОСЛЕДНИМ резервом: перечисление режимов («электролиз: температура 57,
        ток 200, скорость 6») законно вешает ВСЕ значения на один процесс,
        промежуточные числа — соседи по перечислению, не владельцы.
    """
    fspan = f.get("span") or [0, 0]
    ents_all = sorted(mentions, key=lambda m: _dist(fspan, m["span"]))
    ents = [m for m in ents_all if not _blocked_by_foreign_value(f, m, vals)]
    # резерв: заблокированные процессы/оборудование (режимные носители)
    ents_reserve = [m for m in ents_all
                    if m not in ents and m["type"] in ("Process", "Equipment")]
    metric = f.get("metric")
    allowed = COMPAT.get(metric)
    # (раунд-3) ПРИОРИТЕТ материалу, который резолвила грамматика (_find_material уже
    # учитывает границу перечисления и «измеряемое-в-матрице»): если grammar дала
    # material и в предложении есть упоминание с таким каноном совместимого типа —
    # берём его, а не слепо ближайшее (иначе «серебро в меди» → медь).
    # (Точность 1) заблокированные кандидаты уже отфильтрованы выше — grammar-
    # приоритет не может перепрыгнуть через чужое число.
    gmat = f.get("material")
    if gmat:
        for m in ents:
            if m.get("canon") == gmat and (allowed is None or m["type"] in allowed):
                return m
    if allowed is None:
        # метрика без ограничений — прежнее поведение (+ резерв Process/Equipment)
        ent = next((m for m in ents
                    if m["type"] in ("Material", "Process", "Equipment", "Phase")),
                   None)
        return ent or next(iter(ents_reserve), None)
    entity = next((m for m in ents if m["type"] in allowed), None)
    if entity is not None:
        return entity
    # незаблокированных совместимых нет → резервные Process/Equipment (если допущены)
    entity = next((m for m in ents_reserve if m["type"] in allowed), None)
    if entity is not None:
        return entity
    # совместимой сущности нет — исключение для температуры плавления/кипения
    if metric == "температура":
        mat = next((m for m in ents if m["type"] == "Material"), None)
        if mat is not None and _MELT_RE.search(f.get("_sent") or ""):
            return mat
    return None


def _enum_pairs(vals, mentions, sent=""):
    """(Дефект 2) Позиционная привязка для перечислений составов «N% Элемент, …»
    (или «Элемент N%»). Устойчива к ПРОПУСКАМ элементов (напр. газетир не поймал
    один символ): каждое значение → ближайшая НЕзанятая сущность справа (паттерн
    «N% Эл»); если справа нет — ближайшая слева. Не требует равенства количеств.

    Возвращает {id(fact): mention}. Пусто, если это не перечисление
    (≥2 концентрационных значений и ≥2 материалов).

    (Остаточный 2) Слоты состава — ТОЛЬКО Material. Phase («в штейне 45% Ni»)
    задаёт фазу-контекст, а не слот, и не крадёт значение у элемента.
    (Остаточный 3) Значения-интрудеры (левый квалификатор / «N% которого…»)
    исключаются ДО паросочетания, чтобы не сдвигать каскад.
    """
    ents = [m for m in mentions if m["type"] == "Material"]
    conc = [f for f in vals
            if f.get("metric") in ("содержание", "концентрация")
            and not _is_intruder(f, sent)]
    if len(conc) < 2 or len(ents) < 2:
        return {}
    vs = sorted(conc, key=lambda f: (f.get("span") or [0, 0])[0])
    used, out = set(), {}
    for f in vs:
        fspan = f.get("span") or [0, 0]
        cand = [e for e in ents if id(e) not in used]
        if not cand:
            break                       # элементов не хватило (пропуск) → факт дропнется
        pick = min(cand, key=lambda e: _dist(fspan, e["span"]))  # ближайшая с любой стороны
        used.add(id(pick)); out[id(f)] = pick
    return out

# (Точность 3) кириллическая фамилия с хвостовой ОДИНОЧНОЙ латинской буквой —
# артефакт сноски/переносов OCR («Лазаревb» → «Лазарев»). Чистим ТОЛЬКО у
# кириллических фамилий: у чисто латинских (Lazarevb) хвост неотличим от
# легитимной буквы, агрессивный regex по словарности слишком рискован.
_CYR_LATIN_TAIL = re.compile(r"^([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)[a-z]$")


def _clean_surname(surname):
    """(Точность 3) Убрать хвостовую латинскую букву у кириллической фамилии."""
    m = _CYR_LATIN_TAIL.match(surname)
    return m.group(1) if m else surname


def _norm_author(surname, initials):
    """Каноническая форма автора «Фамилия И.О.» из фамилии и строки инициалов."""
    ini = re.sub(r"\s+", "", initials).strip()          # «И. О.» → «И.О.»
    if ini and not ini.endswith("."):
        ini += "."
    return f"{surname} {ini}".strip()


_AUTHOR_HEADER_CHARS = 1500   # ponytail: авторы — только из шапки; ссылки/цитаты в теле не авторы
_AUTHOR_MAX = 8               # больше 8 ФИО в шапке — почти всегда список литературы, не байлайн


def extract_authors(text, header_only=True):
    """(ТЗ п.1) Извлечь авторов ФИО из ШАПКИ документа регексом в обоих порядках.

    Возвращает отсортированный список канонов «Фамилия И.О.» (без дублей, ≤_AUTHOR_MAX).
    Порядок «И.О. Фамилия» нормализуется к тому же канону, что и «Фамилия И.О.».
    По умолчанию сканируется только первые _AUTHOR_HEADER_CHARS символов (байлайн):
    сканирование всего тела ловит списки литературы/цитаты как «авторов» (25 vs 4 на
    типовой статье). header_only=False — сканировать весь текст (для юнит-тестов).
    """
    if header_only:
        text = text[:_AUTHOR_HEADER_CHARS]
    found = {}
    for m in _AUTHOR_SURNAME_FIRST.finditer(text):
        surname, initials = _clean_surname(m.group(1)), m.group(2)
        if surname in _NOT_SURNAME:
            continue
        found[_norm_author(surname, initials)] = None
    for m in _AUTHOR_INITIALS_FIRST.finditer(text):
        initials, surname = m.group(1), _clean_surname(m.group(2))
        if surname in _NOT_SURNAME:
            continue
        found[_norm_author(surname, initials)] = None
    names = sorted(found)
    return names if len(names) <= _AUTHOR_MAX else []   # переполнение шапки → байлайна нет


def extract_claims(text):
    """(ТЗ п.2) Извлечь текстовые выводы по явным маркерам «показал/установлено…».

    Возвращает список канонов-выводов (краткий текст, ≤120 симв, без дублей).
    Берётся хвост предложения после маркера до ближайшего терминатора.
    """
    out, seen = [], set()
    for sent in SENT.split(text):
        sent = sent.strip()
        # (Точность 4) обрывки середины предложения (кандидат начинается со
        # строчной буквы/цифры/скобки) — не кандидаты: их «показал…» вырван
        # из контекста разбиением на предложения.
        if not sent or sent[0].islower() or sent[0].isdigit() or sent[0] in "([{":
            continue
        m = _CLAIM_MARK.search(sent)
        if not m:
            continue
        tail = sent[m.end():].strip(" \t\n,:—-")
        # обрезать по первому терминатору предложения, если попал в хвост
        tail = re.split(r"[.!?;]", tail, maxsplit=1)[0]
        tail = re.sub(r"\s+", " ", tail).strip()  # схлопнуть переносы/пробелы
        if len(tail) < 4:
            continue
        # (Точность 4) резать по границе слова: длинный хвост — до последнего
        # пробела в пределах лимита + «…» (итог ≤ 120 символов).
        if len(tail) > 120:
            cut = tail[:119]
            sp = cut.rfind(" ")
            if sp > 0:
                cut = cut[:sp]
            canon = cut.rstrip(" ,;:—-–") + "…"
        else:
            canon = tail
        canon = canon.strip()
        key = canon.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(canon)
    return out


def process_doc(doc_id, text, matcher, noise_guard=False):
    """noise_guard=True (журналы/сборники): предложения с рыночной статистикой,
    гарблом и нулевой информативностью не подаются в грамматику (src/guards.py)."""
    text = normalize_text(text)
    facts, edges, geos = [], [], []
    # (ТЗ п.1/п.3) doc-level накопители: домены документа (по газетир-темам/
    # процессам) и все процессы дока — для рёбер IN_DOMAIN (Author→Domain) и
    # operates_at_condition (Process→Condition).
    doc_domains, doc_procs, doc_conds = {}, {}, {}
    for sent in SENT.split(text):
        sent = sent.strip()
        if len(sent) < 12 or len(sent) > 1200:
            continue
        if noise_guard:
            from src.guards import is_noise_sentence
            noisy, _why = is_noise_sentence(sent)
            if noisy:
                continue
        mentions = matcher.match(sent, lang="RU")
        if not mentions:
            vals = parse_values(sent)
            if not vals:
                continue
        else:
            vals = parse_values(sent)
        # (ТЗ п.1/п.3) собрать домены/процессы/условия предложения в doc-накопители.
        for m in mentions:
            if m["type"] == "Domain":
                doc_domains.setdefault(m["canon"], None)
            elif m["type"] == "Process":
                doc_procs.setdefault(m["canon"], None)
            elif m["type"] == "Condition":
                doc_conds.setdefault(m["canon"], None)
        # (ТЗ п.3) качественные условия предложения — для conditions факта и узла.
        conds = [m for m in mentions if m["type"] == "Condition"]
        # --- факты: привязать числовой факт к сущности совместимого типа ---
        # (Дефект 2) Перечисление: если ≥2 значений и ≥2 материалов/фаз, а их
        # количества совпадают — сопоставляем ПОЗИЦИОННО (zip по спанам), а не
        # nearest. При неоднозначности (кол-во ≠) — откат на nearest.
        enum_map = _enum_pairs(vals, mentions, sent)
        for f in vals:
            f["_sent"] = sent  # для исключения температуры плавления/кипения
            # (Остаточный 3) если в предложении есть перечисление составов, значение-
            # интрудер (до/более/менее/…/«N% которого») НЕ цепляем к элементу
            # nearest-ом — иначе оно крадёт слот и сдвигает каскад.
            if (enum_map
                    and id(f) not in enum_map
                    and f.get("metric") in ("содержание", "концентрация")
                    and _is_intruder(f, sent)):
                continue
            # (Точность 1) vals передаются в резольвер: кандидат с чужим
            # числом между фактом и сущностью отфильтровывается.
            entity = enum_map.get(id(f)) or _resolve_entity(f, mentions, vals)
            if not entity:
                continue  # факт без сущности — в граф не кладём (чистота)
            # (Дефект 4) фаза-контекст: не дублируем phase, если сама сущность Phase
            if entity["type"] == "Phase":
                phase_canon = None
            else:
                # (Остаточный 4) ближайшая Phase СЛЕВА в пределах клаузы
                # (запятая/точка с запятой рвут клаузу), не глобально по всему
                # предложению.
                fspan = f.get("span") or [0, 0]
                clause_start = max(
                    (sent.rfind(sep, 0, fspan[0]) for sep in (",", ";", ":", "—", "(")),
                    default=-1,
                ) + 1
                left_phases = [m for m in mentions
                               if m["type"] == "Phase"
                               and m["span"][1] <= fspan[0]
                               and m["span"][0] >= clause_start]
                ph = max(left_phases, key=lambda m: m["span"][1], default=None)
                phase_canon = ph["canon"] if ph else (f.get("phase") or None)
            # (ТЗ п.3) качественные условия из газетира, упомянутые в том же
            # предложении, добавляем в conditions факта (грамматика их не видит).
            cond_list = list(f.get("conditions") or [])
            for c in conds:
                if c["canon"] not in cond_list:
                    cond_list.append(c["canon"])
            facts.append({
                "doc_id": doc_id, "node_type": entity["type"], "canon": entity["canon"],
                "metric": f.get("metric"), "unit_canon": f.get("unit_canon"),
                "value_low": f.get("value_low"), "value_high": f.get("value_high"),
                "comparator": f.get("comparator"), "confidence": f.get("confidence", 1.0),
                "conditions": (cond_list or None) and ",".join(map(str, cond_list)),
                "phase": phase_canon,
                # (Точность 2) окно вокруг спана факта — число всегда в цитате
                "source": "grammar", "quote": _quote_window(sent, f.get("span")),
            })
        # --- 4-мини рёбра: процесс + материал в предложении ---
        procs = [m for m in mentions if m["type"] == "Process"]
        mats = [m for m in mentions if m["type"] in ("Material", "Phase")]
        equips = [m for m in mentions if m["type"] == "Equipment"]
        facils = [m for m in mentions if m["type"] == "Facility"]
        # (Остаточный 1) все глагольные упоминания предложения с их направлением,
        # чтобы выбирать глагол-контекст ЛОКАЛЬНО (ближайший по спану к материалу),
        # а не глобально навешивать оба ребра сразу.
        verbs = [(m.start(), m.end(), "produces_output")
                 for m in _VERB_PRODUCES.finditer(sent)]
        verbs += [(m.start(), m.end(), "uses_material")
                  for m in _VERB_USES.finditer(sent)]
        for mt in mats:
            if not procs:
                continue
            # (Дефект 3) глагол привязываем к БЛИЖАЙШЕМУ по спану процессу.
            p = min(procs, key=lambda pr: _dist(mt["span"], pr["span"]))
            # (Остаточный 1) продукты передела (штейн/шлак/огарок/кек/файнштейн/
            # возгон) взаимоисключающе: при глаголе получения → produces_output и
            # НИКОГДА не uses_material.
            if _is_output_mat(mt):
                if any(d == "produces_output" for _s, _e, d in verbs):
                    edges.append({"src": p["canon"], "src_type": "Process", "dst": mt["canon"],
                                  "dst_type": mt["type"], "type": "produces_output",
                                  "doc_id": doc_id, "source": "pattern"})
                continue
            # (Остаточный 1) не-output материал: направление = БЛИЖАЙШИЙ по спану
            # глагол к материалу. Одно ребро на пару, а не оба сразу.
            if not verbs:
                continue
            _s, _e, direction = min(
                verbs, key=lambda v: _dist(mt["span"], (v[0], v[1])))
            edges.append({"src": p["canon"], "src_type": "Process", "dst": mt["canon"],
                          "dst_type": mt["type"], "type": direction,
                          "doc_id": doc_id, "source": "pattern"})
        for p in procs:
            for eq in equips:  # процесс идёт в оборудовании → цепочка «процесс→оборудование»
                edges.append({"src": p["canon"], "src_type": "Process", "dst": eq["canon"],
                              "dst_type": "Equipment", "type": "operates_at_condition",
                              "doc_id": doc_id, "source": "pattern"})
        # (ТЗ п.3) качественные условия: operates_at_condition (Process→Condition)
        # для каждого процесса предложения. Узлы Condition эмитим один раз на
        # документ (ниже), чтобы не плодить дубли в facts.jsonl.
        for c in conds:
            for p in procs:
                edges.append({"src": p["canon"], "src_type": "Process",
                              "dst": c["canon"], "dst_type": "Condition",
                              "type": "operates_at_condition",
                              "doc_id": doc_id, "source": "pattern"})
        # (Дефект 5) Facility-упоминания → узлы графа (node_type=Facility) для гео-слоя.
        for fc in facils:
            facts.append({
                "doc_id": doc_id, "node_type": "Facility", "canon": fc["canon"],
                "metric": None, "unit_canon": None,
                "value_low": None, "value_high": None,
                "comparator": None, "confidence": 1.0,
                "conditions": None, "phase": None,
                "source": "mention", "quote": sent[:240],
            })
        if facils:
            geos.append(facils[0]["canon"])
    # (ТЗ п.1) АВТОРЫ → ЭКСПЕРТЫ: узлы Author + AUTHORED_BY (Document→Author) +
    # IN_DOMAIN (Author→Domain) по доменам документа (газетир-темы/процессы).
    authors = extract_authors(text)
    domains = list(doc_domains)
    # (ТЗ п.1) fallback: если явных Domain-упоминаний нет, домены выводим из
    # процессов дока (гидро/пиро) — грубая, но связывающая эксперта с областью.
    if not domains and doc_procs:
        pyro = {"плавка", "взвешенная плавка", "конвертирование", "обжиг",
                "обеднение шлаков"}
        beneficiation = {"флотация", "магнитная сепарация",
                         "гравитационное обогащение", "измельчение",
                         "флотоконцентрирование"}
        dom_set = {}
        for pc in doc_procs:
            if pc in pyro:
                dom_set.setdefault("пирометаллургия", None)
            elif pc in beneficiation:
                dom_set.setdefault("обогащение", None)
            else:
                dom_set.setdefault("гидрометаллургия", None)
        domains = list(dom_set)
    for a in authors:
        facts.append({
            "doc_id": doc_id, "node_type": "Author", "canon": a,
            "metric": None, "unit_canon": None,
            "value_low": None, "value_high": None,
            "comparator": None, "confidence": 1.0,
            "conditions": None, "phase": None,
            "source": "regex", "quote": a,
        })
        edges.append({"src": doc_id, "src_type": "Document", "dst": a,
                      "dst_type": "Author", "type": "AUTHORED_BY",
                      "doc_id": doc_id, "source": "regex"})
        for dom in domains:
            edges.append({"src": a, "src_type": "Author", "dst": dom,
                          "dst_type": "Domain", "type": "IN_DOMAIN",
                          "doc_id": doc_id, "source": "regex"})
    # (ТЗ п.1) Domain-упоминания → узлы Domain (для графа/фильтрации).
    for dom in domains:
        facts.append({
            "doc_id": doc_id, "node_type": "Domain", "canon": dom,
            "metric": None, "unit_canon": None,
            "value_low": None, "value_high": None,
            "comparator": None, "confidence": 1.0,
            "conditions": None, "phase": None,
            "source": "mention", "quote": dom,
        })
    # (ТЗ п.3) узлы Condition — один раз на документ (edges уже эмитятся выше).
    for cond in doc_conds:
        facts.append({
            "doc_id": doc_id, "node_type": "Condition", "canon": cond,
            "metric": None, "unit_canon": None,
            "value_low": None, "value_high": None,
            "comparator": None, "confidence": 1.0,
            "conditions": None, "phase": None,
            "source": "mention", "quote": cond,
        })
    # (ТЗ п.2) ЭКСПЕРИМЕНТ/ДОКУМЕНТ → ПОКАЗАЛ → ЭФФЕКТ: узлы Claim + SHOWED.
    for cl in extract_claims(text):
        facts.append({
            "doc_id": doc_id, "node_type": "Claim", "canon": cl,
            "metric": None, "unit_canon": None,
            "value_low": None, "value_high": None,
            "comparator": None, "confidence": 1.0,
            "conditions": None, "phase": None,
            "source": "regex", "quote": cl,
        })
        edges.append({"src": doc_id, "src_type": "Document", "dst": cl,
                      "dst_type": "Claim", "type": "SHOWED",
                      "doc_id": doc_id, "source": "regex"})
    return facts, edges, geos

_WORKER_MATCHER = None


def _process_compilation(did, txt, matcher):
    """Мультистатейный сборник (журнал kg=2 / материалы конференций kg=1):
    препроцесс служебных строк → сегментация на статьи (src/journal.py) →
    извлечение per-статья с sentence-гвардами. Привязка сущностей автоматически
    ограничена одной статьёй (сегменты обрабатываются раздельно)."""
    from src.guards import preprocess_text
    from src.journal import science_texts
    facts, edges, geos = [], [], []
    for seg in science_texts(preprocess_text(txt)):
        f, e, g = process_doc(did, seg, matcher, noise_guard=True)
        facts += f; edges += e; geos += g
    return facts, edges, geos


def _worker(args):
    """Процесс-воркер: свой Matcher на процесс (spaCy не шарится между процессами)."""
    global _WORKER_MATCHER
    did, txt, kg = args
    if _WORKER_MATCHER is None:
        _WORKER_MATCHER = Matcher()
    try:
        if kg <= 2:      # журналы/сборники — путь с сегментацией и гвардами
            return did, _process_compilation(did, txt, _WORKER_MATCHER)
        return did, process_doc(did, txt, _WORKER_MATCHER)
    except Exception as e:  # noqa: BLE001 — один битый док не роняет весь прогон
        return did, ([], [], [])


def main(limit=0, min_kg=3, workers=1):
    meta = [json.loads(l) for l in open(DOCS_META, encoding="utf-8")]
    texts = {json.loads(l)["doc_id"]: json.loads(l)["text"]
             for l in open(DOCS_TEXT, encoding="utf-8")}
    dense = [m for m in meta if (m.get("kg_value") or 0) >= min_kg and m.get("ok")]
    dense.sort(key=lambda m: -(m.get("kg_value") or 0))
    if limit:
        dense = dense[:limit]
    print(f"Доков (kg>={min_kg}): {len(dense)}, воркеров: {workers}", flush=True)
    all_facts, all_edges, doc_geo = [], [], {}
    t0 = time.time()
    kg_of = {m["doc_id"]: (m.get("kg_value") or 0) for m in dense}
    items = [(m["doc_id"], texts.get(m["doc_id"], ""), kg_of[m["doc_id"]])
             for m in dense if texts.get(m["doc_id"])]

    if workers and workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for did, (f, e, geos) in ex.map(_worker, items, chunksize=4):
                all_facts += f; all_edges += e
                if geos:
                    doc_geo[did] = geos[0]
                done += 1
                if done % 50 == 0:
                    print(f"  {done}/{len(items)}  факты={len(all_facts)} "
                          f"рёбра={len(all_edges)}  {time.time()-t0:.0f}s", flush=True)
    else:
        matcher = Matcher()
        for i, (did, txt, kg) in enumerate(items, 1):
            if kg <= 2:
                f, e, geos = _process_compilation(did, txt, matcher)
            else:
                f, e, geos = process_doc(did, txt, matcher)
            all_facts += f; all_edges += e
            if geos:
                doc_geo[did] = geos[0]
            if i % 20 == 0:
                print(f"  {i}/{len(items)}  факты={len(all_facts)} "
                      f"рёбра={len(all_edges)}  {time.time()-t0:.0f}s", flush=True)

    # СЛОЙ 3 (журналы/сборники): LLM-верификация числовых фактов против цитат
    # (deepseek T=0, кэш, fail-open). Плотные (kg>=3) не трогаем — там 100%.
    comp_ids = {d for d, k in kg_of.items() if k <= 2}
    if comp_ids:
        from src.verify import verify_facts
        comp = [f for f in all_facts if f.get("doc_id") in comp_ids]
        rest = [f for f in all_facts if f.get("doc_id") not in comp_ids]
        n_numeric = sum(1 for f in comp if f.get("value_low") is not None
                        or f.get("value_high") is not None)
        print(f"LLM-верификация сборников: {len(comp)} фактов "
              f"({n_numeric} числовых)…", flush=True)
        kept, dropped = verify_facts(comp)
        print(f"  верификатор отклонил {dropped}, осталось {len(kept)}", flush=True)
        all_facts = rest + kept
    # дедуп рёбер
    seen, ded = set(), []
    for e in all_edges:
        k = (e["src"], e["dst"], e["type"], e["doc_id"])
        if k not in seen:
            seen.add(k); ded.append(e)
    with open(FACTS, "w", encoding="utf-8") as fh:
        for f in all_facts: fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    with open(EDGES, "w", encoding="utf-8") as fh:
        for e in ded: fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    # geo обратно в meta
    for m in meta:
        if m["doc_id"] in doc_geo:
            m["geo"] = doc_geo[m["doc_id"]]
    with open(DOCS_META, "w", encoding="utf-8") as fh:
        for m in meta: fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"ГОТОВО: фактов {len(all_facts)}, рёбер {len(ded)} (из {len(all_edges)}), гео у {len(doc_geo)} доков, {time.time()-t0:.0f}s", flush=True)
    assert len(all_facts) > 0, "фактов не извлечено"

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("limit", nargs="?", type=int, default=0)
    ap.add_argument("--min-kg", type=int, default=3)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()
    main(a.limit, min_kg=a.min_kg, workers=a.workers)
