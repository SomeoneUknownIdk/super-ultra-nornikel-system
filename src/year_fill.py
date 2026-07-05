"""year-ярус-2: для доков без года в имени — достать год из текста (титул/выходные данные).
Пост-шаг после extract/pipeline (docs.meta может быть перезаписан pipeline'ом)."""
from __future__ import annotations
import os, sys, json, re, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import DOCS_META, DOCS_TEXT

YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-2]\d)\b")   # 1970..2029

def guess_year(text: str):
    """Мода правдоподобных годов в первых ~3000 симв (титул/выходные данные)."""
    head = text[:3000]
    years = [int(y) for y in YEAR_RE.findall(head)]
    if not years:
        return None
    # мода; при равенстве — самый поздний (год издания обычно свежее ссылок)
    cnt = collections.Counter(years)
    top = max(cnt.values())
    return max(y for y, c in cnt.items() if c == top)

def main():
    meta = [json.loads(l) for l in open(DOCS_META, encoding="utf-8")]
    texts = {}
    for l in open(DOCS_TEXT, encoding="utf-8"):
        r = json.loads(l)
        texts[r["doc_id"]] = r.get("text", "")
    filled = 0
    for m in meta:
        if m.get("year") is None:
            y = guess_year(texts.get(m["doc_id"], ""))
            if y:
                m["year"] = y; filled += 1
    with open(DOCS_META, "w", encoding="utf-8") as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    nulls = sum(1 for m in meta if m.get("year") is None)
    print(f"year-ярус-2: заполнено {filled}, осталось null {nulls} из {len(meta)}")

if __name__ == "__main__":
    main()
