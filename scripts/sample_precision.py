"""Выборка фактов из живого графа для ручной сверки точности (значение↔цитата↔сущность).

python scripts/sample_precision.py [kg] [n]   # kg: 1|2|3 (нижняя граница-фильтр), n: размер выборки
Печатает n случайных (детерминированный сид) числовых фактов документов данного kg
в формате «канон метрика=значение единица | цитата» — для ручного вердикта.
"""
import json
import random
import sys

sys.path.insert(0, ".")
from src import graph  # noqa: E402

kg = int(sys.argv[1]) if len(sys.argv) > 1 else 2
n = int(sys.argv[2]) if len(sys.argv) > 2 else 30

d = graph.driver()
with d.session() as s:
    rows = [dict(r) for r in s.run(
        "MATCH (doc:Document)-[:HAS_PARAM]->(p:Parameter)-[:MEASURES]->(e) "
        "WHERE doc.kg_value = $kg AND (p.value_low IS NOT NULL OR p.value_high IS NOT NULL) "
        "RETURN e.canon AS canon, p.metric AS metric, p.value_low AS lo, "
        "p.value_high AS hi, p.unit_canon AS unit, p.quote AS quote, "
        "p.source AS source, doc.doc_id AS doc", kg=kg)]
print(f"kg={kg}: всего числовых фактов {len(rows)}")
random.seed(42)
sample = random.sample(rows, min(n, len(rows)))
for i, x in enumerate(sample):
    val = f"{x['lo']}" if x["lo"] == x["hi"] or x["hi"] is None else f"{x['lo']}-{x['hi']}"
    print(f"[{i:02}] {x['canon']} | {x['metric']}={val} {x['unit']} | src={x['source']}")
    print(f"     {' '.join((x['quote'] or '').split())[:160]}")
