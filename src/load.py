"""Этап 6 entrypoint: читает артефакты → Neo4j → прогоняет 3 эталонных запроса ТЗ."""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import DOCS_META, FACTS, EDGES
from src import graph

def _read(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8")]

def main():
    meta = _read(DOCS_META)
    facts = _read(FACTS)
    vision = _read(FACTS.parent / "vision_facts.jsonl")   # Ярус 2: таблицы составов
    if vision:
        facts = facts + vision
        print(f"  + {len(vision)} vision-фактов (таблицы составов, правильная привязка)")
    edges = _read(EDGES)
    print(f"Загрузка: {len(meta)} док, {len(facts)} фактов, {len(edges)} рёбер", flush=True)
    drv = graph.driver()
    graph.create_constraints_indexes(drv)
    stats = graph.load(drv, meta, facts, edges)
    print("Счётчики графа:", json.dumps(stats, ensure_ascii=False))
    # Этап 5: вычислить рёбра противоречий/подтверждений (CONTRADICTS/VALIDATED_BY).
    # Отдельный шаг: считается из фактов ПОСЛЕ загрузки, не входит в edges.jsonl.
    try:
        from src import contradictions
        cedges = contradictions.compute()
        contradictions.load(cedges)
        print(f"Противоречия: {sum(1 for e in cedges if e['rel']=='CONTRADICTS')} CONTRADICTS, "
              f"{sum(1 for e in cedges if e['rel']=='VALIDATED_BY')} VALIDATED_BY")
    except Exception as e:  # noqa: BLE001 — не роняем загрузку графа
        print(f"Противоречия: пропущены ({str(e)[:80]})")
    for name, fn in (("Обессоливание (сульфат ≤300 мг/л)", lambda: graph.q_desalination(drv, 300)),
                     ("Католит / электроэкстракция Ni", lambda: graph.q_catholyte(drv)),
                     ("Au/Ag/МПГ штейн-vs-шлак", lambda: graph.q_pgm(drv, 5))):
        try:
            rows = fn()
            print(f"\n=== {name}: {len(rows)} строк ===")
            for r in rows[:4]:
                print("  ", json.dumps(r, ensure_ascii=False)[:220])
        except Exception as e:
            print(f"\n=== {name}: ОШИБКА {e}")
    drv.close()

if __name__ == "__main__":
    main()
