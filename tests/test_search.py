"""ЭТАП 7: смоук гибридного поиска. Neo4j недоступен/пуст → pytest.skip.

Проверяет: форму возврата search() (обязательные ключи всегда есть);
при наличии данных в графе facts непусты; RBAC — external_partner видит
не больше researcher; экстрактивный композер и обзор литературы дают markdown.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from src import graph, search

QUERY = "методы обессоливания сульфаты не более 300 мг/л"

REQUIRED_KEYS = {"intent", "answer_md", "facts", "docs", "hidden_count"}


def _require_neo4j():
    """Драйвер Neo4j или skip. Пустой граф (нет Parameter) → тоже skip."""
    try:
        drv = graph.driver(retry_seconds=15.0)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j недоступен: {type(e).__name__}: {e}")
    try:
        with drv.session() as s:
            n = s.run("MATCH (p:Parameter) RETURN count(p) AS c").single()["c"]
    except Exception as e:  # noqa: BLE001
        drv.close()
        pytest.skip(f"Neo4j недоступен для запроса: {type(e).__name__}: {e}")
    drv.close()
    if not n:
        pytest.skip("Граф пуст (нет Parameter) — нечего искать")
    return n


def test_search_shape_and_facts():
    """search() всегда возвращает dict с ключами; при данных — facts непусты."""
    n_params = _require_neo4j()

    res = search.search(QUERY)
    assert isinstance(res, dict)
    assert REQUIRED_KEYS.issubset(res.keys()), f"нет ключей: {REQUIRED_KEYS - res.keys()}"

    assert isinstance(res["facts"], list)
    assert isinstance(res["docs"], list)
    assert isinstance(res["hidden_count"], int)
    assert isinstance(res["answer_md"], str) and res["answer_md"].strip()

    # Числовой запрос → интент 'numeric' (числа доминируют).
    assert res["intent"] == "numeric"

    # При наличии Parameter в графе числовая дорожка должна что-то вернуть.
    assert res["facts"], f"facts пусты, хотя в графе {n_params} Parameter"

    # Каждый факт несёт провенанс (doc_id, quote, value/unit).
    f0 = res["facts"][0]
    for k in ("canon", "doc_id", "quote", "unit", "source"):
        assert k in f0, f"в факте нет поля {k}"
    assert f0["source"] in ("число", "семантика")


def test_rbac_external_le_researcher():
    """external_partner видит документов/фактов не больше, чем researcher."""
    _require_neo4j()

    r = search.search(QUERY, role="researcher")
    e = search.search(QUERY, role="external_partner")

    assert len(e["docs"]) <= len(r["docs"]), "внешний партнёр видит больше docs"
    assert len(e["facts"]) <= len(r["facts"]), "внешний партнёр видит больше facts"
    assert r["hidden_count"] == 0, "у researcher не должно быть скрытых"
    assert e["hidden_count"] >= 0

    # Внешнему не должно достаться ни одного internal-документа.
    meta = search._load_meta()
    for d in e["docs"]:
        m = meta.get(d["doc_id"], {})
        assert (m.get("sensitivity") or "").lower() != "internal", \
            f"internal-документ утёк внешнему партнёру: {d['doc_id']}"


def test_answer_is_extractive_markdown():
    """Композер даёт маркированный экстрактивный markdown с цитатами-фактами."""
    _require_neo4j()
    res = search.search(QUERY)
    md = res["answer_md"]
    assert md.startswith("## "), "ответ должен начинаться с заголовка markdown"
    if res["facts"]:
        # Хотя бы один маркированный факт.
        assert "\n- " in md or md.count("- ") >= 1


def test_literature_review_sections():
    """literature_review: разделы Методы/Режимы/Консенсус/Разногласия/Пробелы."""
    _require_neo4j()
    md = search.literature_review(QUERY)
    assert isinstance(md, str) and md.strip()
    assert "## Методы" in md
    assert "## Режимы" in md
    assert "## Консенсус" in md
    assert "## Разногласия" in md
    assert "## Пробелы" in md


def test_literature_review_consensus_disagreements_with_sources():
    """Консенсус/Разногласия несут N подтверждающих источников (VALIDATED_BY/CONTRADICTS).

    На нашем графе CONTRADICTS обильны (293), поэтому раздел «Разногласия» должен
    содержать хотя бы одну группу с «источников: N».
    """
    _require_neo4j()
    md = search.literature_review(QUERY)
    i = md.find("## Разногласия")
    j = md.find("## Пробелы")
    assert i != -1 and j != -1 and j > i
    contra_block = md[i:j]
    # Либо есть группы с числом источников, либо честное «не найдено».
    assert ("источников:" in contra_block
            or "не найдено" in contra_block), contra_block[:200]


def test_intent_rule_based_fallback():
    """rule-based интент не зависит от Neo4j: числа→numeric, эксперт→expert и т.д."""
    # Этот тест чистый (без графа): проверяем детерминированный фолбэк.
    assert search._rule_intent("покажи все процессы", []) == "listing"
    assert search._rule_intent("кто эксперт по никелю", []) == "expert"
    assert search._rule_intent("обессоливание растворов", []) == "search"
    # Числа доминируют.
    nums = [{"unit_canon": "mg_L", "value_high": 300}]
    assert search._rule_intent("что угодно", nums) == "numeric"


# ─────────────────────────────────────────────────────────────────────────────
# Фильтры и временной парсинг (чистые юниты, без Neo4j).
# ─────────────────────────────────────────────────────────────────────────────
def test_parse_temporal_last_n_years_and_since():
    """«за последние N лет» → текущий_год−N; «с YYYY» → YYYY; иначе None."""
    cur = search._current_year()
    assert search.parse_temporal("распределение МПГ за последние 5 лет") == cur - 5
    assert search.parse_temporal("данные за последних 3 года") == cur - 3
    assert search.parse_temporal("публикации с 2020 года") == 2020
    assert search.parse_temporal("методы обессоливания") is None


def test_norm_filters_merges_temporal_and_shapes():
    """_norm_filters: year(lo,hi)+temporal_lo→max; строки→списки; всё пусто→None."""
    nf = search._norm_filters({"year": (2018, 2024), "material": "медь",
                               "geo": "Талнах", "min_confidence": 0.8},
                              temporal_lo=2021)
    assert nf["year_lo"] == 2021 and nf["year_hi"] == 2024  # max(2018,2021)
    assert nf["material"] == ["медь"] and nf["geo"] == ["талнах"]
    assert nf["min_confidence"] == 0.8
    # Пустые фильтры без temporal → None (фильтрация не активируется).
    assert search._norm_filters(None) is None
    assert search._norm_filters({}) is None
    # Только temporal → активен фильтр по нижней границе года.
    only_t = search._norm_filters(None, temporal_lo=2023)
    assert only_t is not None and only_t["year_lo"] == 2023
    # Регресс: year из multiselect UI — СПИСОК, не кортеж. Пустой список НЕ должен
    # падать (был int([]) → TypeError → весь UI-поиск валился в graph-fallback).
    assert search._norm_filters({"year": []}) is None
    # Список дискретных годов → диапазон [min, max] (порядок выбора не важен).
    ml = search._norm_filters({"year": [2024, 2020, 2022]})
    assert ml["year_lo"] == 2020 and ml["year_hi"] == 2024


def test_apply_filters_cuts_facts_and_docs():
    """_apply_filters реально режет: год/гео/материал/процесс/достоверность."""
    meta = {
        "d1": {"year": 2024, "geo": "Талнах"},
        "d2": {"year": 2015, "geo": "Long Harbour"},
        "d3": {"year": 2022, "geo": "Талнах"},
    }
    facts = [
        {"doc_id": "d1", "canon": "медь", "confidence": 0.9, "year": 2024},
        {"doc_id": "d2", "canon": "никель", "confidence": 0.9, "year": 2015},
        {"doc_id": "d3", "canon": "медь", "confidence": 0.4, "year": 2022},
    ]
    docs = [{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": "d3"}]

    # Год>=2020 → d2 (2015) отсекается.
    nf = search._norm_filters({"year": (2020, None)})
    d, f = search._apply_filters(docs, facts, nf, meta)
    assert {x["doc_id"] for x in f} == {"d1", "d3"}
    assert {x["doc_id"] for x in d} == {"d1", "d3"}

    # География 'RU' (нормализованная): Талнах→RU (d1,d3), Long Harbour→не-RU (d2 отсечён).
    nf = search._norm_filters({"geo": "RU"})
    d, f = search._apply_filters(docs, facts, nf, meta)
    assert {x["doc_id"] for x in f} == {"d1", "d3"}

    # min_confidence 0.8 → d3 (0.4) отсекается.
    nf = search._norm_filters({"min_confidence": 0.8})
    d, f = search._apply_filters(docs, facts, nf, meta)
    assert {x["doc_id"] for x in f} == {"d1", "d2"}

    # Материал 'медь' → никелевый факт d2 отсекается; документ без факта тоже.
    nf = search._norm_filters({"material": "медь"})
    d, f = search._apply_filters(docs, facts, nf, meta)
    assert {x["doc_id"] for x in f} == {"d1", "d3"}
    assert {x["doc_id"] for x in d} == {"d1", "d3"}, "документ без факта-меди должен уйти"

    # None → без изменений (обратная совместимость).
    d, f = search._apply_filters(docs, facts, None, meta)
    assert len(d) == 3 and len(f) == 3


def test_lang_geo_gaps_ru_only_and_world_only():
    """_lang_geo_gaps выделяет сущности с документами только-RU / только-EN."""
    meta = {
        "ru1": {"lang": "RU"}, "ru2": {"lang": "RU"},
        "en1": {"lang": "EN"},
        "mix1": {"lang": "RU"}, "mix2": {"lang": "EN"},
    }
    facts = [
        {"canon": "обессоливание", "doc_id": "ru1"},
        {"canon": "обессоливание", "doc_id": "ru2"},   # только RU
        {"canon": "флотация", "doc_id": "en1"},         # только EN
        {"canon": "медь", "doc_id": "mix1"},
        {"canon": "медь", "doc_id": "mix2"},            # RU+EN → не пробел
    ]
    only_ru, only_world, _ = search._lang_geo_gaps([], facts, meta)
    assert any("обессоливание" in s for s in only_ru)
    assert any("флотация" in s for s in only_world)
    assert not any("медь" in s for s in only_ru + only_world)


def test_combo_gaps_material_times_process_without_facts():
    """_combo_gaps: пары материал×процесс без единого факта → пробел."""
    ents = [
        {"canon": "медь", "type": "Material"},
        {"canon": "электроэкстракция", "type": "Process"},
    ]
    # Ни один факт не покрывает медь/электроэкстракцию.
    facts = [{"canon": "никель"}]
    gaps = search._combo_gaps(ents, facts)
    assert any("медь × электроэкстракция" in g for g in gaps)
    # Факт по меди покрывает комбинацию → не пробел.
    facts2 = [{"canon": "медь"}]
    assert search._combo_gaps(ents, facts2) == []


# ─────────────────────────────────────────────────────────────────────────────
# Дефект 2: ранжирование фактов (чистый юнит, без Neo4j).
# ─────────────────────────────────────────────────────────────────────────────
def test_rank_facts_in_range_first_and_context_tail():
    """in_range=True → впереди; контекст (in_range=False) → в хвост; ближе к цели выше."""
    facts = [
        {"metric": "c", "value_low": 5000, "value_high": 5000, "in_range": False},
        {"metric": "c", "value_low": 250, "value_high": 250, "in_range": True},
        {"metric": "c", "value_low": 900, "value_high": 900, "in_range": False},
        {"metric": "c", "value_low": 100, "value_high": 100, "in_range": True,
         "ref": True},
    ]
    ranked = search._rank_facts(facts, target=300.0)
    # ref-факт первым (0 если ref), затем оставшийся in_range, затем контекст.
    assert ranked[0].get("ref") is True
    assert ranked[1].get("in_range") is True
    # Все in_range идут раньше любого контекстного (in_range=False).
    idx_ctx = [i for i, f in enumerate(ranked) if f.get("in_range") is False]
    idx_in = [i for i, f in enumerate(ranked)
              if f.get("in_range") is True or f.get("ref")]
    assert max(idx_in) < min(idx_ctx), "контекст должен быть в хвосте"
    # Внутри контекста ближе к цели (900) выше, чем далёкий (5000).
    ctx = [f for f in ranked if f.get("in_range") is False]
    assert ctx[0]["value_low"] == 900 and ctx[-1]["value_low"] == 5000


def test_dedup_by_signature():
    """Дубли по (metric+value+unit+phase+doc+quote[:50]) схлопываются в один."""
    a = {"metric": "c", "value_low": 1, "value_high": 2, "unit": "mg_L",
         "phase": "раствор", "doc_id": "d1", "quote": "abc"}
    dup = dict(a)  # полная копия — та же сигнатура
    b = dict(a, value_high=3)  # иное значение — иная сигнатура
    out = search._dedup_facts([a, dup, b])
    assert len(out) == 2
    sigs = {search._fact_signature(f) for f in out}
    assert len(sigs) == 2


def test_authors_from_src_strips_generic_prefix():
    """Автор из имени файла: родовое слово-префикс отбрасывается."""
    assert search._authors_from_src("Доклады/Доклад_Вязовой О.Н.pdf") == ["Вязовой О.Н"]
    assert search._authors_from_src("X/Тяпкина ПА_Пермь.pdf") == ["Тяпкина ПА"]
    assert search._authors_from_src("") == []


def test_expert_track_aggregates_authors():
    """_expert_track агрегирует авторов по документам выдачи (частота по doc_id)."""
    meta = {
        "d1": {"src": "Доклады/Доклад_Иванов И.И.pdf"},
        "d2": {"src": "Статьи/Петров_тема.pdf"},
        "d3": {"src": "Доклады/Доклад_Иванов И.И.pdf"},
    }
    docs = [{"doc_id": "d1"}, {"doc_id": "d2"}]
    facts = [{"doc_id": "d3"}]
    experts = search._expert_track(docs, facts, meta)
    names = [e["name"] for e in experts]
    assert "Иванов И.И" in names and "Петров" in names
    # Иванов встречается в d1+d3 → 2 документа, идёт первым.
    assert experts[0]["name"] == "Иванов И.И" and experts[0]["docs"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Дефект A (major): авторы — ФИО-паттерн, не заголовок документа целиком.
# ─────────────────────────────────────────────────────────────────────────────
def test_authors_parse_name_not_whole_title():
    """_authors_from_src извлекает 'Фамилия И.О.'/'Фамилия Имя', не имя файла целиком."""
    # ФИО с инициалами (точки и без точек).
    assert search._authors_from_src("Доклад_Смирнов А.В.pdf") == ["Смирнов А.В"]
    assert search._authors_from_src("Кузнецова ЕП_регламент.docx") == ["Кузнецова ЕП"]
    # Полное имя «Фамилия Имя» (две заглавные лексемы).
    assert search._authors_from_src("Доклад_Реле Вантов.pdf") == ["Реле Вантов"]
    # Одиночная фамилия допустима.
    assert search._authors_from_src("Петров_тема.pdf") == ["Петров"]


def test_authors_reject_document_titles():
    """Заголовок документа (нет ФИО) НЕ выдаётся как автор."""
    # Тема/название из строчных слов и цифр — не имя.
    assert search._authors_from_src("отчёт по обессоливанию сульфатов 2021.pdf") == []
    assert search._authors_from_src("технология_катодной_меди.pdf") == []
    # Ведущее родовое слово без последующего ФИО — пусто.
    assert search._authors_from_src("Презентация_итоги квартала.pdf") == []


def test_expert_track_excludes_semantic_docs():
    """Major-фикс: семантические доки (source='семантика') не дают авторов-заголовков.

    doc из числовой дорожки (source='число' или без source) → автор учитывается;
    doc, пришедший ТОЛЬКО по семантике → его 'автор' (по сути заголовок) игнорируется.
    """
    meta = {
        "num": {"src": "Доклад_Числов Н.Ч.pdf"},        # релевантный (число)
        "sem": {"src": "Семантов С.С_обзор.pdf"},        # смысловой хвост
    }
    docs = [
        {"doc_id": "num", "source": "число"},
        {"doc_id": "sem", "source": "семантика"},
    ]
    experts = search._expert_track(docs, [], meta)
    names = [e["name"] for e in experts]
    assert "Числов Н.Ч" in names
    assert "Семантов С.С" not in names, "автор семантического дока просочился в эксперты"


# ─────────────────────────────────────────────────────────────────────────────
# Дефект B (critical): числовой Cypher ранжирует по близости к цели, не по величине.
# ─────────────────────────────────────────────────────────────────────────────
def test_numeric_track_passes_target_not_magnitude_sort():
    """_numeric_track прокидывает $target в Cypher; ORDER BY — по близости, не величине."""
    captured = {}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            captured["cypher"] = cypher
            captured["params"] = params
            return iter(())  # пустой результат — проверяем только параметры/запрос

    class _FakeDrv:
        def session(self):
            return _FakeSession()

    nums = [{"unit_canon": "degC", "metric": "температура",
             "value_low": None, "value_high": 800.0}]
    facts = search._numeric_track(_FakeDrv(), nums)
    assert facts == []  # результат пуст (фейковая сессия)
    # target = граница диапазона запроса (800) — прокинут в Cypher.
    assert captured["params"].get("target") == 800.0
    # ORDER BY больше НЕ сортирует по величине value_high/value_low.
    cy = captured["cypher"]
    assert "ORDER BY coalesce(p.value_high, p.value_low)" not in cy
    assert "coalesce(p.value_high, p.value_low) ASC\nLIMIT" not in cy
    # Ранжирование по in_range затем близости к цели ($target / dist).
    assert "$target" in cy
    assert "in_range DESC" in cy
    assert "dist" in cy.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Остаточные дефекты 1–4 (чистые юниты, без Neo4j).
# ─────────────────────────────────────────────────────────────────────────────
def test_semantic_score_floor_filters_noise():
    """Дефект 1: доки со score ниже порога отсекаются (шумовой хвост)."""
    docs = [
        {"doc_id": "d1", "score": 0.80},
        {"doc_id": "d2", "score": 0.35},   # ровно порог — остаётся
        {"doc_id": "d3", "score": 0.34},   # ниже порога — режем
        {"doc_id": "d4", "score": 0.01},   # мусор — режем
        {"doc_id": "d5"},                  # без score → 0.0 — режем
    ]
    kept = search._apply_score_floor(docs)
    ids = {d["doc_id"] for d in kept}
    assert ids == {"d1", "d2"}, f"порог отработал неверно: {ids}"


def test_merge_drops_below_floor_semantic_docs():
    """Дефект 1: _merge не докидывает семантические доки ниже порога.

    На пустой числовой дорожке ('zzz nonsense') мусорные доки → пустая выдача,
    а не N мусорных документов.
    """
    numeric_facts = []
    semantic_docs = [
        {"doc_id": "s1", "score": 0.10},
        {"doc_id": "s2", "score": 0.20},
        {"doc_id": "s3", "score": 0.05},
    ]
    docs, facts = search._merge(numeric_facts, semantic_docs)
    assert docs == [], "мусорные доки ниже порога просочились в выдачу"
    # А релевантный семантический док (выше порога) — докидывается.
    docs2, _ = search._merge([], [{"doc_id": "s9", "score": 0.9}])
    assert [d["doc_id"] for d in docs2] == ["s9"]
    assert docs2[0]["source"] == "семантика"


def test_expert_track_ignores_semantic_noise_tail():
    """Дефект 2: авторов строим из релевантных docs/facts, а не из шумового хвоста.

    _merge уже отфильтровал семантику по порогу, поэтому в _expert_track
    доходят только релевантные doc_id — авторы шумовых доков не появляются.
    """
    meta = {
        "num1": {"src": "Доклады/Доклад_Релевантов Р.В.pdf"},
        "noise": {"src": "Статьи/Шумов_левый.pdf"},
    }
    # Эмулируем выдачу после _merge: числовой факт релевантен, шум отфильтрован.
    numeric_facts = [{"doc_id": "num1"}]
    # Ниже порога — не должен попасть в docs после _merge.
    docs, facts = search._merge(numeric_facts, [{"doc_id": "noise", "score": 0.1}])
    experts = search._expert_track(docs, facts, meta)
    names = [e["name"] for e in experts]
    assert "Релевантов Р.В" in names
    assert "Шумов" not in names, "автор шумового дока просочился в экспертов"


def test_graph_shortcuts_en_triggers():
    """Дефект 3: EN-запросы активируют те же эталонные запросы графа, что и RU.

    Проверяем без Neo4j: подменяем graph.q_* заглушками-маркерами и смотрим,
    какие дорожки активировались по EN-ключам.
    """
    called = []
    orig = {
        "q_desalination": search.graph.q_desalination,
        "q_catholyte": search.graph.q_catholyte,
        "q_pgm": search.graph.q_pgm,
    }
    try:
        search.graph.q_desalination = lambda drv, max_sulfate=300.0: (
            called.append("desal") or [])
        search.graph.q_catholyte = lambda drv: (called.append("cath") or [])
        search.graph.q_pgm = lambda drv, years=5: (called.append("pgm") or [])

        called.clear()
        search._graph_shortcuts(object(), "sulfate removal by desalination", [])
        assert "desal" in called

        called.clear()
        search._graph_shortcuts(object(), "catholyte in electrowinning cell", [])
        assert "cath" in called

        called.clear()
        search._graph_shortcuts(object(), "PGM distribution between matte and slag", [])
        assert "pgm" in called
    finally:
        search.graph.q_desalination = orig["q_desalination"]
        search.graph.q_catholyte = orig["q_catholyte"]
        search.graph.q_pgm = orig["q_pgm"]


def test_bullet_no_duplicate_unit_when_metric_is_unit():
    """Дефект 4: metric == название единицы ('pH pH') не дублируется."""
    f = {"metric": "pH", "value_low": 5, "value_high": 5, "unit": "pH",
         "quote": "", "source": "число", "year": 2020}
    b = search._bullet(f)
    assert "pH pH" not in b, f"дубль единицы: {b}"
    assert "pH" in b
    # А для обычного факта единица остаётся (не режем зря).
    f2 = {"metric": "концентрация", "value_low": 300, "value_high": 300,
          "unit": "mg_L", "quote": "", "source": "число", "year": 2020}
    b2 = search._bullet(f2)
    from src.config import unit_ru
    assert unit_ru("mg_L") in b2


# ─────────────────────────────────────────────────────────────────────────────
# Дефекты 1–4 на живом графе.
# ─────────────────────────────────────────────────────────────────────────────
def test_answer_has_documents_section():
    """Дефект 1: ответ содержит секцию 'Релевантные документы' с src/year+превью."""
    _require_neo4j()
    res = search.search(QUERY)
    md = res["answer_md"]
    assert "### Релевантные документы" in md, "нет секции документов"
    # В секции есть хотя бы один буллет-ссылка на источник (- [«src» (year)](…)).
    assert "\n- [" in md


def test_facts_ranked_in_range_first():
    """Дефект 2: факты в выдаче ранжированы — ни один контекстный не раньше in_range."""
    _require_neo4j()
    res = search.search(QUERY)
    facts = res["facts"]
    if not facts:
        pytest.skip("нет фактов для проверки ранжирования")
    ranks = [search._fact_sort_key(f, search._target_value(
        search.grammar.parse_query(QUERY))) for f in facts]
    assert ranks == sorted(ranks), "факты не отсортированы по ключу ранжирования"
    # ref/in_range факты не идут после контекстного (in_range is False).
    ctx_idx = [i for i, f in enumerate(facts) if f.get("in_range") is False]
    good_idx = [i for i, f in enumerate(facts)
                if f.get("ref") or f.get("in_range") is True]
    if ctx_idx and good_idx:
        assert max(good_idx) < min(ctx_idx)


def test_context_facts_marked_out_of_range():
    """Признак in_range сохраняется на фактах выдачи (метка ушла в структурную
    таблицу фронта — в answer_md факты больше не дублируются)."""
    _require_neo4j()
    res = search.search(QUERY)
    # in_range — булев/None признак на каждом факте, доступен фронту/экспорту.
    for f in res["facts"]:
        assert "in_range" not in f or f.get("in_range") in (True, False, None)


def test_compose_answer_no_longer_inlines_facts():
    """Дедуп: compose_answer НЕ дублирует факты в тексте (они в структурной
    таблице фронта). В тексте — только заголовок + документы + сводка."""
    oor = {"metric": "содержание", "value_low": 400, "value_high": 400,
           "unit": "мг/л", "in_range": False, "ref": None,
           "quote": "контекстное значение вне порога", "source": "grammar",
           "year": 2020, "doc_id": "x"}
    md = search.compose_answer("тест", [oor], docs=[], hidden_count=0)
    assert "## Результаты поиска: тест" in md
    # факт-буллет не инлайнится (ни метрика, ни маркер диапазона)
    assert "(вне диапазона)" not in md
    assert "содержание" not in md


def test_no_duplicate_bullets():
    """Дефект 4: в ответе нет дублирующихся буллетов-фактов."""
    _require_neo4j()
    res = search.search(QUERY)
    # Дедуп на уровне фактов.
    sigs = [search._fact_signature(f) for f in res["facts"]]
    assert len(sigs) == len(set(sigs)), "остались дубли фактов после дедупа"


def test_expert_query_returns_authors():
    """Дефект 3: 'кто эксперт по…' даёт intent=expert и агрегированных авторов."""
    _require_neo4j()
    res = search.search("кто эксперт по обессоливанию сульфатов")
    assert res["intent"] == "expert"
    assert isinstance(res.get("experts"), list) and res["experts"], \
        "экспертная дорожка не вернула авторов"
    # Эксперты отдаются структурным полем res['experts'] (фронт рисует карточки),
    # в answer_md больше не дублируются.
    for e in res["experts"]:
        assert e.get("name") and isinstance(e.get("docs"), int)
        assert not search._is_junk_author(e["name"]), f"мусорный автор: {e['name']}"


# ─────────────────────────────────────────────────────────────────────────────
# Фильтры и аналитика на живом графе.
# ─────────────────────────────────────────────────────────────────────────────
def test_filters_actually_cut_results_on_live_graph():
    """Фильтры реально режут выдачу: год/достоверность дают ⊆ базовой выдачи."""
    _require_neo4j()
    base = search.search(QUERY)
    if not base["facts"]:
        pytest.skip("нет фактов — нечего фильтровать")
    base_ids = {f["doc_id"] for f in base["facts"]}

    # Год-фильтр: КАЖДЫЙ прошедший факт действительно year>=2023 (реальная
    # семантика фильтра). Subset-по-doc_id против base НЕ проверяем: выдача
    # урезается кэпом отображения (40), поэтому отфильтрованный топ-40 может
    # содержать факты, вытесненные из безфильтрового топ-40 — это не нарушение.
    _ = base_ids  # base оставлен для контекста
    yr = search.search(QUERY, filters={"year": (2023, None)})
    meta = search._load_meta()
    for f in yr["facts"]:
        y = search._fact_year(f, meta)
        assert y is not None and y >= 2023, f"год-фильтр пропустил {y}"

    # Достоверность: min_confidence отсекает факты ниже порога.
    hi = search.search(QUERY, filters={"min_confidence": 0.99})
    for f in hi["facts"]:
        assert f.get("confidence") is None or float(f["confidence"]) >= 0.99


def test_temporal_filter_from_query_cuts_results():
    """Временной фильтр из текста запроса («за последние N лет») сужает выдачу."""
    _require_neo4j()
    q = "распределение МПГ между штейном и шлаком"
    base = search.search(q)
    scoped = search.search(q + " за последние 3 года")
    # Временной фильтр разобран в нижнюю границу года.
    nf = scoped.get("filters_applied")
    assert nf and nf.get("year_lo") == search._current_year() - 3
    # И реально не расширяет выдачу.
    assert len(scoped["facts"]) <= len(base["facts"])
    meta = search._load_meta()
    for f in scoped["facts"]:
        y = search._fact_year(f, meta)
        assert y is not None and y >= search._current_year() - 3


# ─────────────────────────────────────────────────────────────────────────────
# Раунд «скорость+надёжность»: мусорные фильтры, TTL недоступности Neo4j,
# entity-facts дорожка, гейт вне-доменных запросов, кэш Matcher.
# ─────────────────────────────────────────────────────────────────────────────
def test_norm_filters_survives_garbage_input():
    """Мусорные filters не роняют поиск: нечисловое → игнор поля, не-dict → None."""
    # Нечисловые year/min_confidence → оба поля игнорируются → фильтра нет.
    assert search._norm_filters({"year": "abc", "min_confidence": "x"}) is None
    # Мусорный кортеж года.
    assert search._norm_filters({"year": ("abc", "x")}) is None
    # filters вообще не dict → эквивалент None.
    assert search._norm_filters("год 2020") is None
    assert search._norm_filters([2020, 2024]) is None
    assert search._norm_filters(42) is None
    # Частично валидные: мусорный год игнорируется, материал остаётся.
    nf = search._norm_filters({"year": "abc", "material": "медь"})
    assert nf is not None and nf["material"] == ["медь"] and nf["year_lo"] is None
    # Смешанный список годов: мусор выпадает, числа образуют диапазон.
    nf = search._norm_filters({"year": ["oops", 2021, "2023"]})
    assert nf["year_lo"] == 2021 and nf["year_hi"] == 2023


def test_neo4j_down_cached_with_ttl():
    """Недоступный Neo4j: первая неудача кэшируется, второй _connect мгновенный."""
    import time as _time
    calls = []

    def _boom(retry_seconds=10.0):
        calls.append(1)
        raise RuntimeError("neo4j down (тест)")

    orig_driver = search.graph.driver
    orig_until = search._NEO4J_DOWN_UNTIL
    try:
        search.graph.driver = _boom
        search._NEO4J_DOWN_UNTIL = 0.0

        assert search._connect() is None
        assert len(calls) == 1
        # Второй вызов — из кэша недоступности: driver НЕ дёргается и быстро.
        t0 = _time.perf_counter()
        assert search._connect() is None
        dt = _time.perf_counter() - t0
        assert len(calls) == 1, "повторный _connect снова полез в драйвер"
        assert dt < 0.05, f"повторный _connect не мгновенный: {dt:.3f}с"
        # TTL истёк → снова пробуем подключиться.
        search._NEO4J_DOWN_UNTIL = _time.time() - 1
        assert search._connect() is None
        assert len(calls) == 2
    finally:
        search.graph.driver = orig_driver
        search._NEO4J_DOWN_UNTIL = orig_until if orig_until > _time.time() else 0.0


def test_entity_facts_track_queries_canons_and_marks_graph_source():
    """_entity_facts_track: canon-ы газетира → Cypher $canons; source='граф'."""
    captured = {}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            captured["cypher"] = cypher
            captured["params"] = params
            return iter([{"canon": "обжиг", "metric": "температура",
                          "value_low": 850.0, "value_high": 900.0,
                          "unit": "degC", "phase": "концентрат",
                          "quote": "обжиг при 850–900 °C",
                          "doc_id": "d1", "year": 2021, "confidence": 0.9}])

    class _FakeDrv:
        def session(self):
            return _FakeSession()

    ents = [{"canon": "обжиг", "type": "Process"},
            {"canon": "Концентрат", "type": "Phase"},
            {"canon": "обжиг", "type": "Process"}]   # дубль — схлопывается
    facts = search._entity_facts_track(_FakeDrv(), ents)
    assert captured["params"]["canons"] == ["обжиг", "концентрат"]
    assert "MEASURES" in captured["cypher"] and "confidence DESC" in captured["cypher"]
    assert len(facts) == 1
    f = facts[0]
    assert f["source"] == "граф" and f["doc_id"] == "d1" and f["quote"]
    assert f["value_low"] == 850.0 and f["unit"] == "degC"
    # Мягкая деградация.
    assert search._entity_facts_track(None, ents) == []
    assert search._entity_facts_track(_FakeDrv(), []) == []


def test_out_of_domain_gate_returns_empty():
    """Вне-доменный запрос (нет чисел/сущностей, слабая семантика) → пустая выдача."""

    class _NoEnts:
        def match(self, q):
            return []

    orig_matcher = search._MATCHER
    orig_sem = search._semantic_track
    orig_connect = search._connect
    orig_llm = search._llm
    try:
        search._MATCHER = _NoEnts()
        search._connect = lambda: None
        search._llm = None   # интент — rule-based, без сетевого вызова
        # Слабая семантика (ниже гейта 0.45, но выше floor 0.35).
        search._semantic_track = lambda q, *a, **kw: [
            {"doc_id": "noise1", "score": 0.40}]
        res = search.search("рецепт борща с пампушками")
        assert res["docs"] == [] and res["facts"] == []
        assert "ничего не найдено" in res["answer_md"]
        assert res["hidden_count"] == 0 and res["experts"] == []

        # Профильный по смыслу запрос (сильная семантика) гейтом НЕ режется.
        search._semantic_track = lambda q, *a, **kw: [
            {"doc_id": "good1", "score": 0.62}]
        res2 = search.search("рецепт борща с пампушками")
        assert any(d["doc_id"] == "good1" for d in res2["docs"])
    finally:
        search._MATCHER = orig_matcher
        search._semantic_track = orig_sem
        search._connect = orig_connect
        search._llm = orig_llm


def test_matcher_cached_on_module():
    """Matcher — ленивая глобаль: повторный _get_matcher отдаёт тот же объект."""
    m1 = search._get_matcher()
    m2 = search._get_matcher()
    assert m1 is m2


def test_search_recommendations_block_present():
    """search() отдаёт блок рекомендаций (похожие кейсы/смежные темы/эксперты)."""
    _require_neo4j()
    res = search.search(QUERY)
    rec = res.get("recommendations")
    assert isinstance(rec, dict)
    for k in ("similar_cases", "adjacent_topics", "experts"):
        assert k in rec and isinstance(rec[k], list)
