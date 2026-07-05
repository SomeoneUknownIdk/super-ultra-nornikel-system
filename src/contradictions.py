"""Этап 5: вычислить CONTRADICTS / VALIDATED_BY / VARIES_WITH_CONDITIONS из фактов.
Группировка по (metric, canon-сущность, phase, unit); пары из РАЗНЫХ документов:
  расхождение ≤10% → VALIDATED_BY; >20% → CONTRADICTS.
kind = ru_vs_world (доки разного происхождения) | method_vs_method.
Пишет рёбра Document-[:REL {kind,metric,...}]->Document прямо в Neo4j.

Гейты против ложных срабатываний (2611+ CONTRADICTS были в основном мусором):
  - сопоставимость по conditions (нельзя доказать → не сталкивать);
  - факты-лимиты (comparator <,<=,>,>=) — не измерения, не сталкиваем;
  - phase-носители без вещества исключаются;
  - sanity-границы по единицам (выбросы выкидываются) ДО сравнения;
  - дедуп одинаковых фактов / почти одинаковых цитат внутри дока;
  - происхождение (RU/WORLD) по языку дока — ДО sensitivity.
"""
from __future__ import annotations
import os, sys, json, itertools, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import FACTS, DOCS_META, DOCS_TEXT
from src import graph

MAX_PAIRS_PER_GROUP = 40   # ponytail: кап против O(n^2) в больших группах

# Компараторы, помечающие факт как лимит/норматив, а не измерение.
LIMIT_COMPARATORS = frozenset({"<", "<=", ">", ">="})

# Метрики-составы: 'содержание'/'концентрация'. Проблема: в русских статьях
# 'содержание' грамматически смешивает состав-состав (Ni в штейне 45%) с
# извлечением-%, долей-владения-%, долей-продаж-% ("содержание ... составило 30%").
# Поэтому состав без conditions доверяем ТОЛЬКО table-anchored фактам (таблицы
# составов, source=vision_ocr) — там элемент↔значение привязаны точно; либо
# требуем общий непустой condition. Обычные grammar-составы без conditions НЕ
# сталкиваем.
COMPOSITION_METRICS = frozenset({"содержание", "концентрация"})
TABLE_SOURCE = "vision_ocr"

# Sanity-границы по каноническим единицам: (lo, hi). Значения вне → выброс.
UNIT_SANITY = {
    "pct":  (0.0, 105.0),
    "degC": (-50.0, 2000.0),
    "g_t":  (0.0, float("inf")),
    "mg_L": (0.0, float("inf")),
}


def _rep(f):
    lo, hi = f.get("value_low"), f.get("value_high")
    if lo is not None and hi is not None: return (lo + hi) / 2
    return lo if lo is not None else hi


# Порог относительной ширины диапазона: (hi-lo)/max(|lo|,|hi|) > этого →
# факт не точечный замер, а широкий диапазон — не сталкиваем (иначе _rep(mid)
# даёт ложные CONTRADICTS/0.0-артефакты).
WIDE_RANGE_REL = 0.5


def _is_wide_range(f):
    """True, если факт — широкий диапазон (не точечный замер). Относительная
    ширина (hi-lo)/max(|lo|,|hi|) > WIDE_RANGE_REL. Точечные (lo==hi или один
    край) — не широкие."""
    lo, hi = f.get("value_low"), f.get("value_high")
    if lo is None or hi is None:
        return False
    denom = max(abs(lo), abs(hi))
    if denom == 0:
        return False
    return (abs(hi - lo) / denom) > WIDE_RANGE_REL


# Доля кириллицы в теле текста дока, выше которой док считается RU.
_CYR_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LAT_RE = re.compile(r"[a-z]", re.IGNORECASE)
_CYR_RATIO_HEAD = 4000   # анализируем начало тела (шапка+первые абзацы)
_CYR_RU_THRESHOLD = 0.5


def _cyr_ratio(text):
    """Доля кириллицы среди буквенных символов в начале текста."""
    head = (text or "")[:_CYR_RATIO_HEAD]
    cyr = len(_CYR_RE.findall(head))
    lat = len(_LAT_RE.findall(head))
    total = cyr + lat
    if total == 0:
        return None
    return cyr / total


def _load_doc_origins():
    """Происхождение по ДОЛЕ кириллицы в ТЕЛЕ текста (docs.text.jsonl), а не по
    языку заголовка/аннотации (кириллический док часто имеет англ. title+abstract
    → ложный EN). cyr_ratio>0.5 → RU, иначе → WORLD. Возвращает {doc_id: origin}.
    Если файла нет — пусто (падаем на эвристику по meta)."""
    origins = {}
    path = str(DOCS_TEXT)
    if not os.path.exists(path):
        return origins
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            did = d.get("doc_id")
            if not did:
                continue
            r = _cyr_ratio(d.get("text"))
            if r is None:
                continue
            origins[did] = "RU" if r >= _CYR_RU_THRESHOLD else "WORLD"
    return origins


def _origin(meta, body_origin=None):
    """Происхождение дока. ПЕРВИЧНО — доля кириллицы в теле (body_origin), т.к.
    язык заголовка/аннотации кириллического дока часто EN → ложный WORLD.
    Только если тело недоступно — падаем на meta.lang, затем на эвристику."""
    if body_origin in ("RU", "WORLD"):
        return body_origin
    lang = (meta.get("lang") or "").upper()
    if lang == "EN":
        return "WORLD"
    if lang == "RU":
        return "RU"
    # язык неизвестен → падаем на прежнюю эвристику
    if meta.get("sensitivity") == "internal":
        return "RU"
    if meta.get("cat") == "Материалы конференций":
        return "WORLD"
    return "WORLD"


def _is_limit(f):
    """Факт-лимит/норматив (порог), а не точечное измерение."""
    return (f.get("comparator") or "=") in LIMIT_COMPARATORS


def _is_phase_carrier(f):
    """Нет вещества-носителя: canon совпадает с phase или это узел-фаза."""
    if f.get("node_type") == "Phase":
        return True
    canon = f.get("canon")
    phase = f.get("phase")
    return bool(canon) and canon == phase


def _in_sanity(f):
    """True, если значение факта укладывается в физичные границы своей единицы."""
    unit = f.get("unit_canon")
    bounds = UNIT_SANITY.get(unit)
    if bounds is None:
        return True  # единица без заданных границ — не фильтруем
    lo_b, hi_b = bounds
    for v in (f.get("value_low"), f.get("value_high")):
        if v is None:
            continue
        if v < lo_b or v > hi_b:
            return False
    return True


def _cond_key(f):
    """Нормализованное представление conditions для проверки сопоставимости."""
    c = f.get("conditions")
    if not c:
        return ()
    if isinstance(c, dict):
        return tuple(sorted((str(k).strip().lower(), str(v).strip().lower())
                            for k, v in c.items() if v not in (None, "")))
    if isinstance(c, (list, tuple)):
        return tuple(sorted(str(x).strip().lower() for x in c if x not in (None, "")))
    return (str(c).strip().lower(),)


def _is_table_anchored(f):
    """Факт из таблицы состава (Vision OCR): элемент↔значение привязаны точно —
    самый надёжный источник, не подвержен грамматической омонимии 'содержания'."""
    return f.get("source") == TABLE_SOURCE


def _conditions_comparable(a, b):
    """Сопоставимость по conditions.
    - оба пусты  → сопоставимы ТОЛЬКО для table-anchored составов одной фазы;
    - оба заданы → сопоставимы только при совпадении ключей/значений;
    - один пуст  → не сопоставимы.
    """
    ca, cb = _cond_key(a), _cond_key(b)
    if not ca and not cb:
        # оба пусты: допустимо ТОЛЬКО для составов (содержание/концентрация) с
        # одинаковой непустой фазой И только если ОБА факта table-anchored
        # (source=vision_ocr). Обычные grammar-составы без conditions грамматически
        # смешивают состав с извлечением/долей — не сталкиваем. Процессные метрики
        # (извлечение/T) без conditions тоже недоказуемы.
        pa, pb = a.get("phase"), b.get("phase")
        both_composition = (a.get("metric") in COMPOSITION_METRICS
                            and b.get("metric") in COMPOSITION_METRICS)
        both_table = _is_table_anchored(a) and _is_table_anchored(b)
        return bool(pa) and pa == pb and both_composition and both_table
    if not ca or not cb:
        return False
    return ca == cb


_WS = re.compile(r"\s+")


def _quote_sig(f):
    """Грубая сигнатура цитаты для дедупа почти одинаковых фактов."""
    q = (f.get("quote") or "").lower()
    q = _WS.sub(" ", q).strip()
    return q[:120]


def _dup_quote(a, b):
    """True, если цитаты двух фактов практически идентичны (нормализованные
    первые ~80 символов совпадают). Такой «источник» — дубль документа с той же
    цитатой, а не независимое подтверждение → VALIDATED_BY создавать нельзя."""
    qa = _WS.sub(" ", (a.get("quote") or "").lower()).strip()[:80]
    qb = _WS.sub(" ", (b.get("quote") or "").lower()).strip()[:80]
    return bool(qa) and qa == qb


def _dedup(fs):
    """Убрать одинаковые (canon,metric,value,unit) и почти одинаковые цитаты
    внутри ОДНОГО документа. Первый встреченный факт остаётся."""
    seen = set()
    out = []
    for f in fs:
        vkey = (f.get("doc_id"), f.get("canon"), f.get("metric"),
                f.get("value_low"), f.get("value_high"), f.get("unit_canon"))
        qkey = (f.get("doc_id"), _quote_sig(f))
        if vkey in seen or qkey in seen:
            continue
        seen.add(vkey)
        seen.add(qkey)
        out.append(f)
    return out


def _eligible(f):
    """Общие гейты для факта, применяемые ДО группировки/сравнения."""
    if (f.get("confidence") or 0) < 0.7:
        return False
    if _rep(f) is None or not f.get("metric"):
        return False
    if _is_limit(f):          # (2) лимит/норматив — не измерение
        return False
    if _is_phase_carrier(f):  # (3) нет вещества-носителя
        return False
    if not _in_sanity(f):     # (4) выброс по единице
        return False
    if _is_wide_range(f):     # (8) широкий диапазон — не точечный замер
        return False
    return True


def _read_facts():
    """Читаем И facts.jsonl И vision_facts.jsonl (как load.py). Vision-факты
    (13469 table-фактов) — самый надёжный источник составов, без них
    contradictions теряет опору для чистых состав-сопоставлений."""
    out = [json.loads(l) for l in open(FACTS, encoding="utf-8")]
    vpath = os.path.join(os.path.dirname(str(FACTS)), "vision_facts.jsonl")
    if os.path.exists(vpath):
        out += [json.loads(l) for l in open(vpath, encoding="utf-8")]
    return out


def compute():
    facts = _read_facts()
    meta = {m["doc_id"]: m for m in (json.loads(l) for l in open(DOCS_META, encoding="utf-8"))}
    # (1) происхождение по доле кириллицы в ТЕЛЕ текста (приоритет над meta.lang).
    doc_origins = _load_doc_origins()

    # (5) дедуп до группировки
    facts = _dedup(facts)

    groups = {}
    for f in facts:
        if not _eligible(f):
            continue
        key = (f.get("metric"), f.get("canon"), f.get("phase") or "", f.get("unit_canon"))
        groups.setdefault(key, []).append(f)

    edges = []
    for key, fs in groups.items():
        # только пары из РАЗНЫХ документов
        byid = {}
        for f in fs:
            byid.setdefault(f["doc_id"], f)  # один представитель на документ
        reps = list(byid.values())
        if len(reps) < 2:
            continue
        pairs = itertools.combinations(reps, 2)
        cnt = 0
        for a, b in pairs:
            if cnt >= MAX_PAIRS_PER_GROUP:
                break
            cnt += 1
            # (1) сопоставимость по conditions: нельзя доказать → не сталкивать
            if not _conditions_comparable(a, b):
                continue
            va, vb = _rep(a), _rep(b)
            denom = max(abs(va), abs(vb)) or 1.0
            diff = abs(va - vb) / denom
            if diff <= 0.10:
                # (2) почти идентичная цитата в разных doc_id = дубль документа,
                # а не независимое подтверждение → не создаём VALIDATED_BY.
                if _dup_quote(a, b):
                    continue
                rel = "VALIDATED_BY"   # (7) пересечение диапазонов ≤10% — с теми же гейтами
            elif diff > 0.20:
                rel = "CONTRADICTS"
            else:
                continue
            oa = _origin(meta.get(a["doc_id"], {}), doc_origins.get(a["doc_id"]))
            ob = _origin(meta.get(b["doc_id"], {}), doc_origins.get(b["doc_id"]))
            # ru_vs_world — только для table-anchored составов одного элемента+фазы
            # (иначе 15+ ложных ru_vs_world из омонимичного grammar-'содержания').
            cross_origin = oa != ob
            table_composition = (
                _is_table_anchored(a) and _is_table_anchored(b)
                and a.get("metric") in COMPOSITION_METRICS
                and b.get("metric") in COMPOSITION_METRICS
                and bool(key[2])  # непустая фаза (element+phase группа)
            )
            kind = "ru_vs_world" if (cross_origin and table_composition) else "method_vs_method"
            edges.append({
                "rel": rel, "kind": kind, "metric": key[0], "entity": key[1],
                "phase": key[2], "unit": key[3],
                "doc_a": a["doc_id"], "val_a": va, "quote_a": a.get("quote", "")[:200],
                "doc_b": b["doc_id"], "val_b": vb, "quote_b": b.get("quote", "")[:200],
            })
    return edges


def load(edges):
    drv = graph.driver()
    with drv.session() as s:
        for e in edges:
            s.run(
                f"""
                MATCH (a:Document {{doc_id:$da}}), (b:Document {{doc_id:$db}})
                MERGE (a)-[r:{e['rel']} {{metric:$metric, entity:$entity, phase:$phase}}]->(b)
                SET r.kind=$kind, r.unit=$unit, r.val_a=$va, r.val_b=$vb,
                    r.quote_a=$qa, r.quote_b=$qb
                """,
                da=e["doc_a"], db=e["doc_b"], metric=e["metric"], entity=e["entity"],
                phase=e["phase"], kind=e["kind"], unit=e["unit"],
                va=e["val_a"], vb=e["val_b"], qa=e["quote_a"], qb=e["quote_b"],
            )
    drv.close()


def main():
    edges = compute()
    contra = [e for e in edges if e["rel"] == "CONTRADICTS"]
    valid = [e for e in edges if e["rel"] == "VALIDATED_BY"]
    ruw = [e for e in edges if e["kind"] == "ru_vs_world"]
    print(f"CONTRADICTS: {len(contra)}, VALIDATED_BY: {len(valid)}, из них ru_vs_world: {len(ruw)}")
    load(edges)
    print("загружено в Neo4j")
    for e in contra[:4]:
        print(f"  ⚠ {e['entity']}/{e['metric']} [{e['phase']}]: {e['val_a']} vs {e['val_b']} {e['unit']} ({e['kind']})")
    assert len(edges) >= 0


if __name__ == "__main__":
    main()
