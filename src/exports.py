"""Экспорт результата поиска (src.search.search()) в 3 формата: Markdown / JSON-LD / PDF.

ТЗ: «экспорт PDF/Markdown/JSON-LD». Три чистых функции над dict-результатом:

  to_markdown(result) -> str    цельный .md-отчёт (запрос, ответ, таблица фактов,
                                эксперты, рекомендации).
  to_jsonld(result)   -> dict   граф schema.org-стиля (@context/@graph): узлы
                                (материалы/процессы/параметры/документы/эксперты)
                                и связи; сериализуем json.dumps(ensure_ascii=False).
  to_pdf(result)      -> bytes  PDF-байты. Использует reportlab/fpdf2, если есть;
                                иначе — самодельный минимальный одностраничный PDF
                                из stdlib (без внешних зависимостей).

Ленивость: to_markdown/to_jsonld не трогают граф — работают только над переданным
dict. Никакие чужие модули src/ не изменяются; из проекта берём лишь unit_ru.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import unit_ru


# ─────────────────────────────────────────────────────────────────────────────
# Общие хелперы над фактом.
# ─────────────────────────────────────────────────────────────────────────────
def _num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _fmt_value(f) -> str:
    """value_low/value_high → '12' / '12–15' / '≤300' / '≥5' / ''."""
    lo, hi = f.get("value_low"), f.get("value_high")
    if lo is not None and hi is not None:
        return _num(lo) if lo == hi else f"{_num(lo)}–{_num(hi)}"
    if hi is not None:
        return f"≤{_num(hi)}"
    if lo is not None:
        return f"≥{_num(lo)}"
    return ""


def _year_s(y) -> str:
    return str(y) if y not in (None, "") else "б.г."


# ─────────────────────────────────────────────────────────────────────────────
# Markdown.
# ─────────────────────────────────────────────────────────────────────────────
def to_markdown(result: dict) -> str:
    """Цельный markdown-отчёт из результата search().

    Секции: заголовок-запрос → готовый answer_md (если есть) → таблица фактов
    (метрика/значение/ед./фаза/источник/год/достоверность) → эксперты →
    рекомендации (похожие кейсы / смежные темы / эксперты).
    """
    result = result or {}
    query = result.get("query") or result.get("intent") or "запрос"
    lines = [f"# Отчёт по запросу: {query}", ""]

    answer_md = (result.get("answer_md") or "").strip()
    if answer_md:
        lines += [answer_md, ""]

    facts = result.get("facts") or []
    if facts:
        lines += [
            "## Факты",
            "",
            "| Метрика | Значение | Ед. | Фаза | Источник | Год | Достоверность |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in facts:
            conf = f.get("confidence")
            conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "—"
            lines.append(
                f"| {f.get('metric') or '—'} | {_fmt_value(f) or '—'} "
                f"| {unit_ru(f.get('unit')) or '—'} | {f.get('phase') or '—'} "
                f"| {f.get('source') or '—'} | {_year_s(f.get('year'))} | {conf_s} |"
            )
        lines.append("")

    experts = result.get("experts") or []
    if experts:
        lines += ["## Эксперты", ""]
        for e in experts:
            lines.append(f"- **{e.get('name')}** — документов: {e.get('docs')}")
        lines.append("")

    rec = result.get("recommendations") or {}
    sc, at, ex = (rec.get("similar_cases") or [], rec.get("adjacent_topics") or [],
                  rec.get("experts") or [])
    if sc or at or ex:
        lines += ["## Рекомендации", ""]
        if sc:
            lines.append("**Похожие кейсы:**")
            for c in sc:
                lines.append(f"- {c.get('src') or c.get('doc_id')} ({_year_s(c.get('year'))})")
        if at:
            lines.append("**Смежные темы:**")
            for t in at:
                lines.append(f"- {t.get('canon')} ({t.get('type')})")
        if ex:
            lines.append("**Эксперты:**")
            for e in ex:
                lines.append(f"- {e.get('name')} — документов: {e.get('docs')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# JSON-LD (schema.org-стиль).
# ─────────────────────────────────────────────────────────────────────────────
_CONTEXT = {
    "@vocab": "https://schema.org/",
    "canon": "name",
    "metric": "propertyID",
    "unit": "unitText",
    "valueLow": "minValue",
    "valueHigh": "maxValue",
    "measures": {"@id": "https://schema.org/about", "@type": "@id"},
    "source": {"@id": "https://schema.org/isBasedOn", "@type": "@id"},
    "authoredBy": {"@id": "https://schema.org/author", "@type": "@id"},
}


def _node_id(kind: str, key: str) -> str:
    """Стабильный локальный @id узла."""
    return f"_:{kind}:{key}"


def to_jsonld(result: dict) -> dict:
    """JSON-LD-граф выдачи: узлы (материалы/процессы/параметры/документы/эксперты)
    и связи. Валидный dict, сериализуемый json.dumps(ensure_ascii=False).

    Модель: Parameter (PropertyValue) --measures--> Material/Process (DefinedTerm),
    --isBasedOn--> Document (CreativeWork) --author--> Person. Дедуп узлов по @id.
    """
    result = result or {}
    facts = result.get("facts") or []
    graph = []          # @graph
    seen = set()        # @id уже добавленных узлов

    def add(node):
        nid = node.get("@id")
        if nid and nid not in seen:
            seen.add(nid)
            graph.append(node)

    for i, f in enumerate(facts):
        canon = f.get("canon") or ""
        did = f.get("doc_id")
        # Сущность (материал/процесс) — DefinedTerm.
        ent_id = None
        if canon:
            ent_id = _node_id("entity", canon)
            add({"@id": ent_id, "@type": "DefinedTerm", "name": canon})
        # Документ — CreativeWork.
        doc_id = None
        if did:
            doc_id = _node_id("doc", str(did))
            add({"@id": doc_id, "@type": "CreativeWork",
                 "identifier": str(did), "datePublished": f.get("year")})
        # Параметр — PropertyValue (связывает всё вместе).
        param = {
            "@id": _node_id("param", str(i)),
            "@type": "PropertyValue",
            "propertyID": f.get("metric"),
            "minValue": f.get("value_low"),
            "maxValue": f.get("value_high"),
            "unitText": unit_ru(f.get("unit")) or f.get("unit"),
            "description": f.get("quote") or None,
        }
        if f.get("phase"):
            param["measurementTechnique"] = f.get("phase")
        if isinstance(f.get("confidence"), (int, float)):
            param["marginOfError"] = f.get("confidence")
        if ent_id:
            param["about"] = {"@id": ent_id}
        if doc_id:
            param["isBasedOn"] = {"@id": doc_id}
        add(param)

    # Эксперты — Person.
    for e in (result.get("experts") or []):
        name = e.get("name")
        if name:
            add({"@id": _node_id("person", name), "@type": "Person",
                 "name": name, "worksFor": None})

    return {
        "@context": _CONTEXT,
        "@type": "Dataset",
        "name": result.get("query") or "Результаты поиска",
        "@graph": graph,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PDF.
# ─────────────────────────────────────────────────────────────────────────────
def _cyr_font() -> str:
    """Зарегистрировать кириллический TTF в reportlab → имя шрифта.

    Стандартные 14 PDF-шрифтов (Helvetica) БЕЗ кириллицы — русский отчёт молча
    терял текст. Берём DejaVuSans из matplotlib (едет с anaconda), фолбэк —
    системные пути macOS/Linux; совсем без TTF — Helvetica (латиница).
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = []
    try:
        import matplotlib
        from pathlib import Path
        candidates.append(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf")
    except Exception:
        pass
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",   # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",        # Linux
    ]
    for p in candidates:
        try:
            pdfmetrics.registerFont(TTFont("CyrSans", str(p)))
            return "CyrSans"
        except Exception:
            continue
    return "Helvetica"


def _pdf_reportlab(text: str) -> bytes | None:
    """PDF через reportlab, если установлен; иначе None. Кириллица через TTF."""
    try:
        from io import BytesIO
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm
    except Exception:
        return None
    font = _cyr_font()
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x, y = 2 * cm, height - 2 * cm
    c.setFont(font, 10)
    for raw in text.splitlines():
        for line in _wrap(raw, 95):
            if y < 2 * cm:
                c.showPage()
                c.setFont(font, 10)
                y = height - 2 * cm
            c.drawString(x, y, line)
            y -= 13
    c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_fpdf(text: str) -> bytes | None:
    """PDF через fpdf2, если установлен; иначе None."""
    try:
        from fpdf import FPDF
    except Exception:
        return None
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for raw in text.splitlines():
        # fpdf2 (latin-1 ядро) не рисует кириллицу без TTF-шрифта — деградируем
        # до транслита/ascii, чтобы не падать; реальный PDF-байтпоток всё равно.
        safe = raw.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 5, safe or " ")
    out = pdf.output()
    return bytes(out)


def _wrap(s: str, width: int):
    """Простой перенос строки по ширине (для самодельного PDF)."""
    s = s.rstrip()
    if not s:
        return [""]
    out = []
    while len(s) > width:
        out.append(s[:width])
        s = s[width:]
    out.append(s)
    return out


def _pdf_minimal(text: str) -> bytes:
    """Самодельный минимальный одностраничный PDF без внешних зависимостей.

    ponytail: это «потолок» ручного PDF — один встроенный шрифт (Helvetica,
    WinAnsi), кириллица кодируется как есть в latin-1-совместимую строку с заменой
    непредставимых символов; переносы/пагинации нет (одна страница A4, хвост
    обрезается). Для честного многостраничного вывода с кириллицей нужна либа
    (reportlab/fpdf2 + TTF). Достаточно для «PDF существует и валиден».
    """
    # A4 в пунктах: 595 x 842. Печатаем сверху вниз, шрифт 10pt.
    lines = []
    for raw in text.splitlines():
        lines.extend(_wrap(raw, 90))
    max_lines = 58  # что влезает на одну страницу
    lines = lines[:max_lines]

    def esc(s: str) -> str:
        # PDF-строка: экранируем ( ) \, непредставимое в WinAnsi заменяем '?'.
        s = s.encode("cp1252", "replace").decode("cp1252")
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    # Тело потока текста (BT/ET), одна строка = один Tj со сдвигом.
    body = ["BT", "/F1 10 Tf", "12 TL", "50 800 Td"]
    for ln in lines:
        body.append(f"({esc(ln)}) Tj")
        body.append("T*")
    body.append("ET")
    stream = "\n".join(body).encode("cp1252", "replace")

    # Собираем объекты PDF вручную, считая байтовые смещения для xref.
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body_bytes in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body_bytes + b"\nendobj\n"

    xref_pos = len(out)
    n = len(objs) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {n} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def to_pdf(result: dict) -> bytes:
    """PDF-байты отчёта. Приоритет реальной либе (reportlab → fpdf2); при их
    отсутствии — самодельный минимальный PDF из stdlib. Всегда начинается с b'%PDF'.
    """
    text = to_markdown(result)
    for gen in (_pdf_reportlab, _pdf_fpdf):
        try:
            data = gen(text)
        except Exception:
            data = None
        if data and data[:4] == b"%PDF":
            return data
    return _pdf_minimal(text)


# ─────────────────────────────────────────────────────────────────────────────
# Self-check.
# ─────────────────────────────────────────────────────────────────────────────
def _fake_result() -> dict:
    return {
        "query": "методы обессоливания сульфаты не более 300 мг/л",
        "intent": "numeric",
        "answer_md": "## Результаты поиска\n\n- pH 8.5 — «рабочий режим» (число, 2021)",
        "facts": [
            {"canon": "серебро", "metric": "извлечение", "value_low": 92.0,
             "value_high": 95.0, "unit": "pct", "phase": "медь", "quote": "извлечение серебра",
             "doc_id": "doc-1", "year": 2021, "confidence": 0.88, "source": "число"},
            {"canon": "сульфат", "metric": "концентрация", "value_high": 300.0,
             "unit": "mg_L", "quote": "", "doc_id": "doc-2", "year": 2019,
             "confidence": None, "source": "число"},
        ],
        "docs": [{"doc_id": "doc-1", "source": "число"}],
        "experts": [{"name": "Клименко И.В.", "docs": 3}],
        "recommendations": {
            "similar_cases": [{"doc_id": "doc-9", "src": "Обзор.pdf", "year": 2020}],
            "adjacent_topics": [{"canon": "электроэкстракция", "type": "Process"}],
            "experts": [{"name": "Петров А.А.", "docs": 2}],
        },
    }


if __name__ == "__main__":
    import json

    r = _fake_result()

    md = to_markdown(r)
    assert md and "# Отчёт" in md and "| Метрика |" in md and "Клименко" in md, "markdown пуст/неполон"
    # answer_md вставлен:
    assert "Результаты поиска" in md, "answer_md не вставлен"

    ld = to_jsonld(r)
    assert isinstance(ld, dict) and ld.get("@graph"), "jsonld пуст"
    s = json.dumps(ld, ensure_ascii=False)
    assert "серебро" in s and "@context" in s, "jsonld не сериализуется корректно"
    types = {n.get("@type") for n in ld["@graph"]}
    assert {"PropertyValue", "DefinedTerm", "CreativeWork", "Person"} <= types, f"нет ожидаемых узлов: {types}"

    pdf = to_pdf(r)
    assert isinstance(pdf, bytes) and pdf[:4] == b"%PDF", "PDF не начинается с %PDF"
    assert pdf.rstrip().endswith(b"%%EOF") or b"%%EOF" in pdf, "PDF без %%EOF"

    # Пустой результат не должен падать.
    assert to_markdown({}) and isinstance(to_jsonld({}), dict) and to_pdf({})[:4] == b"%PDF"

    print("OK markdown:", len(md), "chars; jsonld:", len(ld["@graph"]), "nodes; pdf:", len(pdf), "bytes")
