"""Этап 6: Neo4j — схема, загрузка, три эталонных Cypher-запроса.

Идемпотентно (MERGE + IF NOT EXISTS). Ключи узлов сущностей — canon (канон газетира).
Узлы фактов — Parameter (числовой факт с провенансом). Реификация Experiment по (doc_id, conditions).
Загрузчик штампует extracted_at = timestamp прогона на каждый факт при MERGE (принцип 4 ТЗ).

Форматы строк (устойчивы к отсутствию полей — .get):
  meta_rows:  {doc_id, year, geo|cat, sensitivity, kg_value, ...}
  facts_rows: {doc_id, node_type(Material/Process/Equipment/Phase/Facility/
               Author/Domain/Claim/Condition/Topic...), canon,
               value_low, value_high, unit_canon, metric, comparator, confidence,
               source, quote, conditions, phase(canon фазы, опц.)}
  edges_rows: {src(canon), src_type, dst(canon), dst_type, type(EDGE_TYPES +
               AUTHORED_BY/IN_DOMAIN/SHOWED/OPERATES_AT_CONDITION), doc_id, source}

ТЗ-онтология (8/8) даётся ДОП. лейблами поверх исходных, не ломая MATCH:
  Document  += :Publication   Parameter += :Property   Author += :Expert
Версионирование фактов (ТЗ): каждый Parameter несёт extracted_at + pipeline_version
+ version. SUPERSEDES ставится лишь к СТРОГО более старой версии того же логического
слота (doc_id, canon, metric, conditions) при инкрементальной догрузке нового
источника; под полной перезагрузкой (единый extracted_at) рёбер 0 — истории ещё нет.
"""
from __future__ import annotations
import os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import neo4j
from src.config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, PIPELINE_VERSION, nfc
from src.obs import get_logger, log_event

# Типы узлов-сущностей, у которых ключ = canon и есть FULLTEXT по name+aliases.
# Material/Process/Equipment/Phase/Facility — исходные; Author/Domain/Claim/
# Condition/Topic — расширение под ТЗ-онтологию (Author получает второй лейбл
# :Expert при загрузке — см. _entity_extra_labels).
ENTITY_LABELS = ["Material", "Process", "Equipment", "Phase", "Facility",
                 "Author", "Domain", "Claim", "Condition", "Topic"]

# Дополнительные (ТЗ-онтологические) лейблы, навешиваемые ПОВЕРХ первичного,
# не ломая существующие MATCH по первичному лейблу. Ключ — первичный лейбл
# сущности, значение — список доп. лейблов. Так MATCH (n:Expert) даёт >0, при
# этом MATCH (n:Author) продолжает работать.
_ENTITY_EXTRA_LABELS = {"Author": ["Expert"], "Document": ["Publication"]}


def driver(uri: str = NEO4J_URI, user: str = NEO4J_USER, password: str = NEO4J_PASS,
           retry_seconds: float = 30.0) -> "neo4j.Driver":
    """neo4j.Driver с ретраем коннекта ~30с (Neo4j может подниматься в docker)."""
    deadline = time.time() + retry_seconds
    last = None
    while True:
        drv = neo4j.GraphDatabase.driver(uri, auth=(user, password))
        try:
            drv.verify_connectivity()
            return drv
        except Exception as e:  # noqa: BLE001 — ServiceUnavailable/AuthError и т.п.
            last = e
            try:
                drv.close()
            except Exception:
                pass
            if time.time() >= deadline:
                raise
            get_logger("graph").warning("neo4j connect retry (%s), %s left",
                                        e, round(deadline - time.time(), 1))
            time.sleep(1.5)


def wipe(drv: "neo4j.Driver") -> None:
    """Полная зачистка ГРАФА ЗНАНИЙ батчами (перед перезагрузкой корпуса).
    Узлы :User (аутентификация) СОХРАНЯЮТСЯ — это не корпус, их нельзя терять
    при перезагрузке данных. `MATCH (n) DETACH DELETE n` в одной транзакции молча
    не осиливает большие графы (сотни тыс. рёбер) и оставляет хвосты."""
    with drv.session() as s:
        s.run("MATCH ()-[r]->() WHERE NOT (startNode(r):User OR endNode(r):User) "
              "CALL(r){DELETE r} IN TRANSACTIONS OF 50000 ROWS")
        s.run("MATCH (n) WHERE NOT n:User "
              "CALL(n){DETACH DELETE n} IN TRANSACTIONS OF 50000 ROWS")


def create_constraints_indexes(drv: "neo4j.Driver") -> None:
    """Идемпотентно: уникальность (label,canon); RANGE на value_low/high;
    составной (metric,unit_canon); FULLTEXT name+aliases; btree Document.year/geo."""
    stmts = []
    # Уникальность canon по каждому типу сущности.
    for lbl in ENTITY_LABELS:
        stmts.append(
            f"CREATE CONSTRAINT {lbl.lower()}_canon IF NOT EXISTS "
            f"FOR (n:{lbl}) REQUIRE n.canon IS UNIQUE")
    # Уникальность Document по doc_id.
    stmts.append(
        "CREATE CONSTRAINT document_doc_id IF NOT EXISTS "
        "FOR (d:Document) REQUIRE d.doc_id IS UNIQUE")
    # Уникальность Parameter по pkey — БЕЗ неё MERGE квадратичен (загрузка минуты).
    stmts.append(
        "CREATE CONSTRAINT parameter_pkey IF NOT EXISTS "
        "FOR (p:Parameter) REQUIRE p.pkey IS UNIQUE")
    # Индекс Parameter.doc_id (реификация Experiment + запросы).
    stmts.append(
        "CREATE INDEX parameter_doc_id IF NOT EXISTS "
        "FOR (p:Parameter) ON (p.doc_id)")
    # RANGE-индексы на границы диапазона параметра.
    stmts.append(
        "CREATE RANGE INDEX parameter_value_low IF NOT EXISTS "
        "FOR (p:Parameter) ON (p.value_low)")
    stmts.append(
        "CREATE RANGE INDEX parameter_value_high IF NOT EXISTS "
        "FOR (p:Parameter) ON (p.value_high)")
    # Составной индекс metric+unit_canon.
    stmts.append(
        "CREATE INDEX parameter_metric_unit IF NOT EXISTS "
        "FOR (p:Parameter) ON (p.metric, p.unit_canon)")
    # btree (RANGE) на Document.year и Document.geo.
    stmts.append(
        "CREATE INDEX document_year IF NOT EXISTS FOR (d:Document) ON (d.year)")
    stmts.append(
        "CREATE INDEX document_geo IF NOT EXISTS FOR (d:Document) ON (d.geo)")

    with drv.session() as s:
        for st in stmts:
            s.run(st)
        # FULLTEXT по name+aliases сущностей (по одному индексу на тип).
        for lbl in ENTITY_LABELS:
            s.run(
                f"CREATE FULLTEXT INDEX ft_{lbl.lower()} IF NOT EXISTS "
                f"FOR (n:{lbl}) ON EACH [n.name, n.aliases]")


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load(drv: "neo4j.Driver", meta_rows, facts_rows, edges_rows) -> dict:
    """MERGE документов, сущностей, параметров; рёбра; реификация Experiment.

    Возвращает счётчики загруженных строк.
    """
    logger = get_logger("graph")
    meta_rows = list(meta_rows or [])
    facts_rows = list(facts_rows or [])
    edges_rows = list(edges_rows or [])
    extracted_at = _iso_now()
    log_event(logger, "graph_load_start", documents=len(meta_rows),
              facts=len(facts_rows), edges=len(edges_rows))

    # Батчинг: полный корпус (~126k фактов) в ОДНОЙ транзакции превышал лимит
    # памяти Neo4j (dbms.memory.transaction.total.max ~716 МБ) → грузим порциями.
    BATCH = 4000

    def _chunks(seq, n):
        for i in range(0, len(seq), n):
            yield seq[i:i + n]

    try:
        with drv.session() as s:
            s.execute_write(_load_meta, meta_rows)
            for ch in _chunks(facts_rows, BATCH):
                s.execute_write(_load_facts, ch, extracted_at, PIPELINE_VERSION)
            for ch in _chunks(edges_rows, BATCH):
                s.execute_write(_load_edges, ch)
            # реификация — по чанкам фактов; MERGE Experiment идемпотентен, поэтому
            # факты одного дока в разных чанках корректно доклеиваются к его Experiment.
            for ch in _chunks(facts_rows, BATCH):
                s.execute_write(_reify_experiments, ch)
            # Денормализуем source_count на сущностях (кол-во док в 1-2 хопах):
            # API читает готовое свойство вместо живого [*1..2]-разворота (20-40с/запрос).
            s.execute_write(_backfill_source_count)
    except Exception as e:  # логируем и пробрасываем — поведение не меняется
        logger.error("graph_load failed: %s", e)
        raise

    log_event(logger, "graph_load", documents=len(meta_rows),
              facts=len(facts_rows), edges=len(edges_rows))
    return {"documents": len(meta_rows), "facts": len(facts_rows), "edges": len(edges_rows)}


def _backfill_source_count(tx):
    """Проставляет source_count (число Document в 1-2 хопах) сущностным узлам.
    ~150 узлов, ~1с. Читается API за мс вместо живого [*1..2]-разворота на запрос."""
    tx.run(
        "MATCH (n) WHERE n:Material OR n:Process OR n:Equipment OR n:Facility "
        "OPTIONAL MATCH (n)-[*1..2]-(d:Document) "
        "WITH n, count(DISTINCT d) AS sc SET n.source_count = sc"
    )


def _sane_year(year):
    """Дефект 4: год < 1950 (частый артефакт датакодов в именах файлов, напр.
    1901/1903) → null. Нечисловой / отсутствующий → как есть (None)."""
    if year is None:
        return None
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    return y if y >= 1950 else None


_RU_GEO_HINTS = (
    "талнах", "норильск", "надежд", "кольск", "мончегорск", "заполярн",
    "мурманск", "печенга", "россия", "росси", "гмк", "норникел",
)
# страны/регионы, которые считаем WORLD-геолокацией (зарубеж).
_WORLD_GEO_HINTS = (
    "china", "usa", "canada", "australia", "africa", "finland", "sweden",
    "germany", "japan", "india", "chile", "peru", "brazil", "europe",
    "китай", "финлянд", "канад", "австрал", "япони", "чили",
)


def _norm_geo(lang=None, sensitivity=None, geo=None, src=None, cat=None):
    """Нормализация гео к {RU|WORLD|<явная страна>}.

    Раньше geo брался из cat (категория «Доклады»/«Журналы» — мусор). Теперь:
    - явный geo с известным гео-хинтом → RU/WORLD;
    - internal-документ или русский язык → RU (внутренние отчёты ГМК);
    - EN-документ → WORLD (зарубежная периодика);
    - иначе None (не выдумываем страну из категории).

    Возвращает нормализованную строку или None. Явный geo, если он не пуст и не
    совпадает с категорией, имеет приоритет как есть (страна/регион)."""
    blob = " ".join(str(x) for x in (geo, src) if x).lower()
    if any(h in blob for h in _RU_GEO_HINTS):
        return "RU"
    if any(h in blob for h in _WORLD_GEO_HINTS):
        return "WORLD"
    # Явный geo, не являющийся категорией-прокси, — уважаем как есть.
    g = (geo or "").strip()
    if g and g != (cat or ""):
        return nfc(g)
    sens = (sensitivity or "").strip().lower()
    lg = (lang or "").strip().upper()
    if sens == "internal" or lg == "RU":
        return "RU"
    if lg == "EN":
        return "WORLD"
    return None


def _load_meta(tx, rows):
    tx.run(
        """
        UNWIND $rows AS r
        MERGE (d:Document {doc_id: r.doc_id})
        SET d:Publication,
            d.name        = r.name,
            d.year        = r.year,
            d.geo         = r.geo,
            d.lang        = r.lang,
            d.sensitivity = r.sensitivity,
            d.kg_value    = r.kg_value,
            d.doc_type    = r.doc_type
        """,
        rows=[{
            "doc_id": nfc(r.get("doc_id")),
            # Человекочитаемое имя (демо жюри): басейм src без расширения,
            # обрезан до 80 симв; если src пуст — fallback на doc_id.
            "name": (os.path.basename(str(r.get("src") or "")).rsplit(".", 1)[0][:80]
                     or nfc(r.get("doc_id"))),
            "year": _sane_year(r.get("year")),
            # geo нормализуется (_norm_geo): RU для internal/русских, WORLD для EN,
            # явная страна как есть. Мусорный fallback на cat убран.
            "geo": _norm_geo(lang=r.get("lang"), sensitivity=r.get("sensitivity"),
                             geo=r.get("geo"), src=r.get("src"), cat=r.get("cat")),
            "lang": nfc(r.get("lang")),
            "sensitivity": nfc(r.get("sensitivity")),
            "kg_value": r.get("kg_value"),
            "doc_type": nfc(r.get("doc_type")),   # patent/standard/report/article/…
        } for r in rows],
    )


def _label_for(node_type: str) -> str:
    t = (node_type or "").strip().capitalize()
    return t if t in ENTITY_LABELS else "Material"


def _pkey_num(x):
    """Нормализация числа для pkey: int(95) и float(95.0) → один и тот же токен.
    Дефект 3 (cross-source дедуп): без нормализации str(95) != str(95.0) →
    два Parameter на один факт. Числа приводим к float и берём repr;
    нечисловые значения — str как есть."""
    if x is None:
        return ""
    if isinstance(x, bool):  # bool — подтип int, но здесь это не число значения
        return str(x)
    if isinstance(x, (int, float)):
        return repr(float(x))
    return str(x)


def _sane_value(unit, value_low, value_high):
    """Санитарный гейт значений (дефект 2). Возвращает True, если факт валиден.

    pct∈[0,100], pH∈[0,14], degC≥-273, содержания (g_t/mg_L/mol_L)≥0.
    Отрицательные содержания и pct>100 → факт отбрасывается."""
    lo, hi = value_low, value_high
    for v in (lo, hi):
        if v is None or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        v = float(v)
        if unit == "pct":
            if v < 0.0 or v > 100.0:
                return False
        elif unit == "pH":
            if v < 0.0 or v > 14.0:
                return False
        elif unit == "degC":
            if v < -273.0:
                return False
        elif unit in ("g_t", "mg_L", "mol_L", "geq_L", "A_m2", "m3_h", "t_day"):
            if v < 0.0:  # отрицательные содержания/расходы бессмысленны
                return False
    return True


def _load_facts(tx, rows, extracted_at, pipeline_version):
    """Батчевая загрузка через UNWIND (динамические метки — по группам).
    ~10 запросов вместо ~8×N."""
    prepared = []
    entities_only = []   # чистые узлы-сущности (без метрики/значения)
    for r in rows:
        label = _label_for(r.get("node_type") or r.get("type"))
        canon = nfc(r.get("canon"))
        doc_id = nfc(r.get("doc_id"))
        unit_canon = nfc(r.get("unit_canon"))
        value_low = r.get("value_low")
        value_high = r.get("value_high")
        # Нормализация: value_low должно быть ≤ value_high. Инверсия — убывающий
        # ряд в тексте («0,3-0,27») или битый парс — своп (значения те же, порядок верный).
        if (isinstance(value_low, (int, float)) and isinstance(value_high, (int, float))
                and value_low > value_high):
            value_low, value_high = value_high, value_low
        metric = nfc(r.get("metric"))
        # Чистая декларация сущности (напр. node_type Author/Domain/Claim/Condition/
        # Topic, либо любой факт без метрики и значений) → создаём ТОЛЬКО узел
        # сущности (+ MENTIONS), без Parameter. Иначе такой факт превращался бы в
        # пустой Parameter (canon=имя сущности) — мусор.
        if not metric and value_low is None and value_high is None:
            if canon:
                entities_only.append({"canon": canon, "label": label,
                                      "doc_id": doc_id})
            continue
        # Дефект 2: санитарный гейт — невалидные значения не грузим.
        if not _sane_value(unit_canon, value_low, value_high):
            continue
        # pkey БЕЗ индекса строки i: идентичные факты (doc_id,canon,metric,unit,
        # value_low,value_high,conditions,comparator) схлопываются MERGE-ем →
        # идемпотентная повторная загрузка без дублей Parameter.
        # Дефект 3: числа нормализованы (_pkey_num), чтобы int(95) и float(95.0)
        # давали один pkey — иначе кросс-источниковый дубль.
        pkey = "|".join((
            _pkey_num(doc_id), _pkey_num(canon), _pkey_num(r.get("metric")),
            _pkey_num(unit_canon), _pkey_num(value_low), _pkey_num(value_high),
            _pkey_num(r.get("conditions")), _pkey_num(r.get("comparator"))))
        prepared.append({
            "pkey": pkey, "doc_id": doc_id, "canon": canon, "label": label,
            "value_low": value_low, "value_high": value_high,
            "unit_canon": unit_canon, "metric": metric,
            "comparator": nfc(r.get("comparator")), "confidence": r.get("confidence"),
            "source": nfc(r.get("source")), "quote": nfc(r.get("quote")),
            "conditions": nfc(r.get("conditions")), "phase": nfc(r.get("phase")),
            "extracted_at": extracted_at, "pipeline_version": pipeline_version,
        })
    # Чистые узлы-сущности (Author/Domain/Claim/Condition/Topic и пр. без метрики)
    # грузим отдельными группами по метке: MERGE узел (+ доп. лейбл) + MENTIONS.
    if entities_only:
        from collections import defaultdict as _dd
        ent_by_label = _dd(list)
        for e in entities_only:
            ent_by_label[e["label"]].append(e)
        for label, elist in ent_by_label.items():
            extra = _ENTITY_EXTRA_LABELS.get(label, [])
            set_extra = ("SET e:" + ":".join(extra) + "\n            "
                         if extra else "")
            tx.run(
                f"""
                UNWIND $rows AS r
                MERGE (e:{label} {{canon: r.canon}})
                  ON CREATE SET e.name = r.canon
                {set_extra}SET e.aliases = coalesce(e.aliases, r.canon)
                WITH r, e WHERE r.doc_id IS NOT NULL AND r.doc_id <> ''
                MERGE (d:Document {{doc_id: r.doc_id}})
                MERGE (d)-[:MENTIONS]->(e)
                """, rows=elist)
    if not prepared:
        return
    # 1) Document + Parameter + HAS_PARAM + DESCRIBED_IN (метка-независимо).
    # Parameter получает второй лейбл :Property (ТЗ-онтология) — не ломая MATCH
    # по :Parameter. version инициализируется 1 на CREATE (см. SUPERSEDES ниже).
    tx.run(
        """
        UNWIND $rows AS r
        MERGE (d:Document {doc_id: r.doc_id})
        MERGE (p:Parameter {pkey: r.pkey})
          ON CREATE SET p.version = 1
        SET p:Property,
            p.value_low=r.value_low, p.value_high=r.value_high, p.unit_canon=r.unit_canon,
            p.metric=r.metric, p.comparator=r.comparator, p.confidence=r.confidence,
            p.source=r.source, p.quote=r.quote, p.conditions=r.conditions,
            p.doc_id=r.doc_id, p.canon=r.canon,
            p.extracted_at=r.extracted_at, p.pipeline_version=r.pipeline_version
        MERGE (d)-[:HAS_PARAM]->(p)
        MERGE (p)-[:DESCRIBED_IN]->(d)
        """, rows=prepared)
    # 1b) SUPERSEDES-механика (версионирование фактов ТЗ). Ребро ставится ТОЛЬКО
    # к СТРОГО более старой версии того же ЛОГИЧЕСКОГО слота
    # (doc_id, canon, metric, conditions) — гейт q.extracted_at < r.extracted_at.
    # Под полной перезагрузкой у всех фактов один extracted_at → 0 рёбер (истории
    # ещё нет — честно). Под инкрементальной догрузкой нового источника свежий факт
    # супёрсидит прежний того же слота. Без гейта — декартов взрыв внутри батча
    # (разные значения одной группы = РАЗНЫЕ факты, не версии).
    tx.run(
        """
        UNWIND $rows AS r
        MATCH (p:Parameter {pkey: r.pkey})
        MATCH (q:Parameter)
        WHERE q.doc_id = r.doc_id AND q.canon = r.canon
          AND coalesce(q.metric,'') = coalesce(r.metric,'')
          AND coalesce(q.conditions,'') = coalesce(r.conditions,'')
          AND q.pkey <> r.pkey
          AND q.extracted_at < r.extracted_at
        MERGE (p)-[:SUPERSEDES]->(q)
        """, rows=prepared)
    tx.run(
        """
        MATCH (p:Parameter)
        WHERE (p)-[:SUPERSEDES]->()
        WITH p, size([(p)-[:SUPERSEDES]->() | 1]) AS older
        SET p.version = 1 + older
        """)
    # 2) сущность (динамическая метка) + MENTIONS + MEASURES — по группам меток
    from collections import defaultdict
    by_label = defaultdict(list)
    for p in prepared:
        by_label[p["label"]].append(p)
    for label, plist in by_label.items():
        # ТЗ-онтология: доп. лейблы поверх первичного (Author→:Expert).
        extra = _ENTITY_EXTRA_LABELS.get(label, [])
        set_extra = ("SET e:" + ":".join(extra) + "\n            "
                     if extra else "")
        tx.run(
            f"""
            UNWIND $rows AS r
            MERGE (e:{label} {{canon: r.canon}})
              ON CREATE SET e.name = r.canon
            {set_extra}SET e.aliases = coalesce(e.aliases, r.canon)
            WITH r, e
            MATCH (d:Document {{doc_id: r.doc_id}}), (p:Parameter {{pkey: r.pkey}})
            MERGE (d)-[:MENTIONS]->(e)
            MERGE (p)-[:MEASURES]->(e)
            """, rows=plist)
    # 3) MEASURED_IN->Phase (где фаза задана).
    # Анти-цикл: не создавать MEASURED_IN, если фаза совпадает с canon самой
    # сущности (self-loop) или сущность уже помечена Phase — иначе Parameter
    # одновременно MEASURES фазу X и MEASURED_IN ту же X (1730 циклических рёбер).
    ph_rows = [p for p in prepared
               if p["phase"] and p["phase"] != p["canon"] and p["label"] != "Phase"]
    if ph_rows:
        tx.run(
            """
            UNWIND $rows AS r
            MERGE (ph:Phase {canon: r.phase})
              ON CREATE SET ph.name = r.phase
            SET ph.aliases = coalesce(ph.aliases, r.phase)
            WITH r, ph
            MATCH (p:Parameter {pkey: r.pkey})
            MERGE (p)-[:MEASURED_IN]->(ph)
            """, rows=ph_rows)


def _endpoint(node_type: str):
    """(label, key_field) для конца ребра. Document/Publication адресуются по
    doc_id к узлу :Document (а НЕ создают canon-Material-мусор из doc_id-хэша —
    дефект: рёбра AUTHORED_BY/SHOWED с src_type='Document' плодили 160 фейковых
    Material). Прочие сущности — canon-ключ."""
    if (node_type or "").strip().lower() in ("document", "publication"):
        return ("Document", "doc_id")
    return (_label_for(node_type), "canon")


def _load_edges(tx, rows):
    """Батч по группам (src_label, src_key, dst_label, dst_key, тип) — динамические."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        etype = (r.get("type") or "RELATED").strip().upper().replace(" ", "_")
        src_lbl, src_key = _endpoint(r.get("src_type"))
        dst_lbl, dst_key = _endpoint(r.get("dst_type"))
        groups[(src_lbl, src_key, dst_lbl, dst_key, etype)].append({
            "src": nfc(r.get("src")), "dst": nfc(r.get("dst")),
            "doc_id": nfc(r.get("doc_id")), "source": nfc(r.get("source")),
        })
    for (src_lbl, src_key, dst_lbl, dst_key, etype), erows in groups.items():
        # ТЗ-онтология: доп. лейблы поверх первичного (Author→:Expert, Document→
        # :Publication) и на src, и на dst — если узел создаётся впервые в рёбрах.
        a_extra = _ENTITY_EXTRA_LABELS.get(src_lbl, [])
        b_extra = _ENTITY_EXTRA_LABELS.get(dst_lbl, [])
        set_a = ("SET a:" + ":".join(a_extra) + "\n            ") if a_extra else ""
        set_b = ("SET b:" + ":".join(b_extra) + "\n            ") if b_extra else ""
        # name проставляем только canon-сущностям (у Document ключ — doc_id).
        name_a = "ON CREATE SET a.name=r.src\n            " if src_key == "canon" else ""
        name_b = "ON CREATE SET b.name=r.dst\n            " if dst_key == "canon" else ""
        tx.run(
            f"""
            UNWIND $rows AS r
            MERGE (a:{src_lbl} {{{src_key}: r.src}})
            {name_a}{set_a}MERGE (b:{dst_lbl} {{{dst_key}: r.dst}})
            {name_b}{set_b}MERGE (a)-[e:{etype} {{doc_id: r.doc_id}}]->(b)
            SET e.sources = CASE
                WHEN r.source IS NULL THEN coalesce(e.sources, [])
                WHEN e.sources IS NULL THEN [r.source]
                WHEN r.source IN e.sources THEN e.sources
                ELSE e.sources + r.source END
            """, rows=erows)


def _reify_experiments(tx, facts_rows):
    """Группа Parameter одного (doc_id, conditions) → Experiment.
    (Experiment)-[:HAS_PARAM]->(Parameter), (Experiment)-[:DESCRIBED_IN]->(Document)."""
    groups = {}
    for r in facts_rows:
        doc_id = nfc(r.get("doc_id"))
        conditions = nfc(r.get("conditions")) or ""
        if not doc_id:
            continue
        # Тот же санитарный гейт, что и в _load_facts (ДО группировки): невалидный
        # факт не грузится Parameter-ом, поэтому не должен и порождать Experiment.
        # Иначе группа из одних невалидных фактов даёт повисший Experiment с 0
        # HAS_PARAM (нет Parameter, к которому привязаться).
        if not _sane_value(nfc(r.get("unit_canon")),
                           r.get("value_low"), r.get("value_high")):
            continue
        # Реифицируем только осмысленные эксперименты: пустой conditions даёт
        # вырожденный 1-Experiment/док, объединяющий несвязанные факты. Пропускаем.
        if not conditions:
            continue
        groups.setdefault((doc_id, conditions), True)
    for (doc_id, conditions) in groups:
        exp_id = f"{doc_id}::{conditions}"
        tx.run(
            """
            MERGE (x:Experiment {exp_id: $exp_id})
            SET x.doc_id = $doc_id, x.conditions = $conditions
            WITH x
            MATCH (d:Document {doc_id: $doc_id})
            MERGE (x)-[:DESCRIBED_IN]->(d)
            WITH x
            MATCH (p:Parameter {doc_id: $doc_id})
            WHERE coalesce(p.conditions, '') = $conditions
            MERGE (x)-[:HAS_PARAM]->(p)
            """,
            exp_id=exp_id, doc_id=doc_id, conditions=conditions,
        )
    # ТЗ «эксперимент → показал → эффект»: Claim привязан к документу (doc-level,
    # без conditions) ребром Document-[:SHOWED]->Claim. Пробрасываем на эксперименты
    # дока — каждый Experiment документа показал выводы этого документа.
    if groups:
        tx.run(
            """
            MATCH (x:Experiment)-[:DESCRIBED_IN]->(d:Document)-[:SHOWED]->(c:Claim)
            MERGE (x)-[:SHOWED]->(c)
            """)


def answer_subgraph(drv: "neo4j.Driver", doc_ids, limit: int = 60):
    """Read-only подграф для визуализации выдачи (ТЗ: цепочка материал→процесс→
    оборудование→результат + подсветка CONTRADICTS + эксперты).

    Берёт РЕАЛЬНУЮ топологию Neo4j вокруг документов выдачи (а не восстанавливает
    из плоских фактов — те не несут типов сущностей). Возвращает (nodes, edges) в
    том же формате, что _subgraph_from_facts: nodes={id:{label,type}}, edges=[(s,d,t)].
    Пустой doc_ids → ({}, []).
    """
    ids = [nfc(str(x)) for x in (doc_ids or []) if x]
    if not ids:
        return {}, []
    ids = ids[:40]                       # ограничим окно документов
    nodes, edges, seen = {}, [], set()

    def add(nid, label, ntype):
        if nid and nid not in nodes:
            nodes[nid] = {"label": str(label)[:40], "type": ntype}

    def link(s, d, t):
        if s in nodes and d in nodes and (s, d, t) not in seen:
            seen.add((s, d, t)); edges.append((s, d, t))

    with drv.session() as s:
        # 1) Процессы выдачи + их цепочка (USES_MATERIAL/PRODUCES_OUTPUT/условия).
        # NFR ≤5с: pattern comprehensions вместо 4 последовательных OPTIONAL MATCH —
        # те создавали декартов взрыв строк (mats×outs×conds×eqs на каждый процесс)
        # ДО collect(): 10 doc_ids = 84 c. Comprehension собирает каждый список
        # независимо, без перемножения строк.
        rows = s.run(
            """
            MATCH (d:Document)-[:HAS_PARAM]->(:Parameter)-[:MEASURES]->(pr:Process)
            WHERE d.doc_id IN $ids
            WITH DISTINCT pr LIMIT $limit
            RETURN pr.canon AS proc,
                   [(pr)-[:USES_MATERIAL]->(m:Material) | m.canon][..6] AS mats,
                   [(pr)-[:PRODUCES_OUTPUT]->(o) | o.canon][..6] AS outs,
                   [(pr)-[:OPERATES_AT_CONDITION]->(c:Condition) | c.canon][..4] AS conds,
                   [(pr)-[:OPERATES_AT_CONDITION]->(e:Equipment) | e.canon][..4] AS eqs
            """, ids=ids, limit=limit)
        for r in rows:
            pr = r["proc"]
            if not pr:
                continue
            pid = f"PR:{pr}"; add(pid, pr, "Process")
            for m in r["mats"] or []:
                if m:
                    add(f"M:{m}", m, "Material"); link(f"M:{m}", pid, "USES_MATERIAL")
            for o in r["outs"] or []:
                if o:
                    add(f"O:{o}", o, "Phase"); link(pid, f"O:{o}", "PRODUCES_OUTPUT")
            for c in r["conds"] or []:
                if c:
                    add(f"C:{c}", c, "Condition"); link(pid, f"C:{c}", "OPERATES_AT_CONDITION")
            # звено «оборудование» 4-цепочки ТЗ: рёбра Process→Equipment реально
            # есть в графе (435 шт.) — без link() узлы EQ висели изолированно.
            for e in r["eqs"] or []:
                if e:
                    add(f"EQ:{e}", e, "Equipment"); link(pid, f"EQ:{e}", "OPERATES_AT_CONDITION")
        # 2) Оборудование выдачи без процессной связи (измерено напрямую) — узлы.
        for r in s.run(
            """
            MATCH (d:Document)-[:HAS_PARAM]->(:Parameter)-[:MEASURES]->(eq:Equipment)
            WHERE d.doc_id IN $ids
            RETURN DISTINCT eq.canon AS eq LIMIT 15
            """, ids=ids):
            if r["eq"]:
                add(f"EQ:{r['eq']}", r["eq"], "Equipment")
        # 2b) Лаборатории/площадки (ТЗ: «показ связанных экспертов и лабораторий»):
        # Facility-узлы, упомянутые документами выдачи.
        for r in s.run(
            """
            MATCH (d:Document)-[:MENTIONS]->(f:Facility)
            WHERE d.doc_id IN $ids
            RETURN d.doc_id AS doc, f.canon AS fac LIMIT 15
            """, ids=ids):
            if r["fac"]:
                add(f"D:{r['doc']}", f"док {r['doc'][:8]}", "Document")
                add(f"FA:{r['fac']}", r["fac"], "Facility")
                link(f"D:{r['doc']}", f"FA:{r['fac']}", "MENTIONS")
        # 3) CONTRADICTS между документами выдачи (подсветка противоречий).
        for r in s.run(
            """
            MATCH (a:Document)-[:CONTRADICTS]-(b:Document)
            WHERE a.doc_id IN $ids AND b.doc_id IN $ids AND a.doc_id < b.doc_id
            RETURN a.doc_id AS a, b.doc_id AS b LIMIT 30
            """, ids=ids):
            add(f"D:{r['a']}", f"док {r['a'][:8]}", "Document")
            add(f"D:{r['b']}", f"док {r['b'][:8]}", "Document")
            link(f"D:{r['a']}", f"D:{r['b']}", "CONTRADICTS")
        # 4) Эксперты выдачи (Author/:Expert) → документ.
        for r in s.run(
            """
            MATCH (d:Document)-[:AUTHORED_BY]->(a:Author)
            WHERE d.doc_id IN $ids
            RETURN d.doc_id AS doc, a.canon AS expert LIMIT 25
            """, ids=ids):
            if r["expert"]:
                add(f"D:{r['doc']}", f"док {r['doc'][:8]}", "Document")
                add(f"X:{r['expert']}", r["expert"], "Expert")
                link(f"X:{r['expert']}", f"D:{r['doc']}", "AUTHORED_BY")
    return nodes, edges


# ---------------------------------------------------------------------------
# Три эталонных запроса ТЗ. Каждый возвращает список dict-строк.
# ---------------------------------------------------------------------------

def q_desalination(drv: "neo4j.Driver", max_sulfate: float = 300.0):
    """Обессоливание: Parameter c metric~содержание/концентрация, сущность~сульфат,
    значение <= max_sulfate."""
    cy = """
    MATCH (p:Parameter)-[:MEASURES]->(e)
    WHERE (toLower(coalesce(e.canon,'')) CONTAINS 'сульфат'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'хлорид')
      AND p.unit_canon = 'mg_L'           // мг/л сульфат-иона = концентрация по определению
                                          // (фильтр по строке-метрике избыточен и терял
                                          //  факты с непойманной метрикой)
      AND coalesce(p.value_high, p.value_low) <= $max_sulfate
    OPTIONAL MATCH (p)-[:DESCRIBED_IN]->(d:Document)
    RETURN e.canon AS material, p.metric AS metric,
           p.value_low AS value_low, p.value_high AS value_high,
           p.unit_canon AS unit, p.quote AS quote, d.doc_id AS doc_id, d.year AS year
    ORDER BY coalesce(p.value_high, p.value_low) ASC
    """
    with drv.session() as s:
        return [dict(r) for r in s.run(cy, max_sulfate=max_sulfate)]


def q_catholyte(drv: "neo4j.Driver"):
    """Электроэкстракция + фаза католит + метрика плотность тока / скорость."""
    # Дефект 1: провенанс. DESCRIBED_IN — обязательный MATCH (у факта всегда есть
    # документ), иначе doc_id обнулялся. Фильтр по сущности вынесен в OPTIONAL
    # MATCH-паттерн (`WHERE ... CONTAINS 'электроэкстракц'` внутри OPTIONAL): факты
    # без совпадающей сущности сохраняются (e IS NULL), но при этом провенанс (d)
    # не зависит от этой ветки и остаётся непустым.
    # Две ветки UNION: (1) факты, измеренные ПРЯМО в фазе католит; (2) факты
    # скорости потока/перекачки из ТЕХ ЖЕ документов-экспериментов с католитом
    # (ответ на вторую половину вопроса ТЗ №2 «какая скорость потока оптимальна» —
    # у скоростей перекачки фазовое ребро часто не извлекается, но провенанс тот же).
    cy = """
    MATCH (p:Parameter)-[:MEASURED_IN]->(ph:Phase)
    WHERE toLower(coalesce(ph.canon,'')) CONTAINS 'католит'
      AND (toLower(coalesce(p.metric,'')) CONTAINS 'плотност'
           OR toLower(coalesce(p.metric,'')) CONTAINS 'ток'
           OR toLower(coalesce(p.metric,'')) CONTAINS 'скорост')
    MATCH (p)-[:DESCRIBED_IN]->(d:Document)
    OPTIONAL MATCH (p)-[:MEASURES]->(e)
      WHERE toLower(coalesce(e.canon,'')) CONTAINS 'электроэкстракц'
    RETURN e.canon AS process, ph.canon AS phase, p.metric AS metric,
           p.value_low AS value_low, p.value_high AS value_high,
           p.unit_canon AS unit, p.quote AS quote,
           d.doc_id AS doc_id, d.year AS year
    ORDER BY d.year DESC
    """
    cy2 = """
    MATCH (pc:Parameter)-[:MEASURED_IN]->(ph:Phase)
    WHERE toLower(coalesce(ph.canon,'')) CONTAINS 'католит'
    WITH collect(DISTINCT pc.doc_id) AS cat_docs
    MATCH (p:Parameter)
    WHERE p.doc_id IN cat_docs
      AND toLower(coalesce(p.metric,'')) CONTAINS 'скорост'
      AND coalesce(p.value_low, p.value_high) IS NOT NULL
      AND NOT (p)-[:MEASURED_IN]->(:Phase)
    MATCH (p)-[:DESCRIBED_IN]->(d:Document)
    RETURN null AS process, 'католит (эксперимент)' AS phase, p.metric AS metric,
           p.value_low AS value_low, p.value_high AS value_high,
           p.unit_canon AS unit, p.quote AS quote,
           d.doc_id AS doc_id, d.year AS year
    ORDER BY coalesce(p.value_low, p.value_high)
    LIMIT 10
    """
    with drv.session() as s:
        rows = [dict(r) for r in s.run(cy)]
        rows += [dict(r) for r in s.run(cy2)]
        return rows


def q_pgm(drv: "neo4j.Driver", years: int = 5):
    """МПГ/Au/Ag в фазах штейн/шлак, свежее последних `years` лет, ORDER BY year DESC."""
    cutoff = datetime.date.today().year - int(years)
    cy = """
    MATCH (p:Parameter)-[:MEASURES]->(e)
    MATCH (p)-[:MEASURED_IN]->(ph:Phase)
    WHERE (toLower(coalesce(e.canon,'')) CONTAINS 'au'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'ag'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'золот'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'серебр'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'мпг'
           OR toLower(coalesce(e.canon,'')) CONTAINS 'платин')
      AND (toLower(coalesce(ph.canon,'')) CONTAINS 'штейн'
           OR toLower(coalesce(ph.canon,'')) CONTAINS 'шлак')
      // только составные метрики (содержание/распределение), не T/pH — они шумят по фазе
      AND (p.unit_canon IN ['pct','g_t']
           OR toLower(coalesce(p.metric,'')) CONTAINS 'содержан'
           OR toLower(coalesce(p.metric,'')) CONTAINS 'концентрац'
           OR toLower(coalesce(p.metric,'')) CONTAINS 'распределен')
    // Дефект 1: провенанс. DESCRIBED_IN обязателен (у факта всегда есть документ)
    // — WHERE по d.year больше не обнуляет doc_id. d.year может быть null (год
    // не извлечён / отфильтрован дефектом 4) → факт проходит фильтр.
    MATCH (p)-[:DESCRIBED_IN]->(d:Document)
    WHERE d.year IS NULL OR d.year >= $cutoff
    RETURN e.canon AS material, ph.canon AS phase, p.metric AS metric,
           p.value_low AS value_low, p.value_high AS value_high,
           p.unit_canon AS unit, p.quote AS quote,
           d.doc_id AS doc_id, d.year AS year
    ORDER BY d.year DESC
    """
    with drv.session() as s:
        return [dict(r) for r in s.run(cy, cutoff=cutoff)]
