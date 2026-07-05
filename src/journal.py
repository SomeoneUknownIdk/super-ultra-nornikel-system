"""Сегментация мультистатейных выпусков (журналы kg=2, материалы конференций kg=1).

Выпуск «Цветных металлов»/«Горного журнала» — это ~20 статей + преамбула
(обложка, оглавление, «Международный обзор рынка») + экономические статьи +
реклама. Извлечение из ЦЕЛОГО выпуска даёт ~35% точности (рыночная статистика
как составы). Сегментация по ГОСТ-якорям (УДК в начале каждой научной статьи;
«Для цитирования:» у «Горной промышленности») + классификация сегментов
отсекает ~80% рыночного шума ДО грамматики. Детерминировано, без LLM.

science_texts(text) -> list[str] — тексты научных сегментов (для process_doc).
Замерено на 8 реальных выпусках 4 изданий: 9-21 статья/выпуск, научная доля ~87%.
"""
from __future__ import annotations

import re

# Якорь начала научной статьи (ГОСТ Р 7.0.7: строка «УДК <цифры>»);
# «Горная промышленность» УДК не ставит — якорь «Для цитирования:».
_ANCHOR = re.compile(r"^\s*(?:УДК\s*[\d\[]|Для цитирования:)")
# Экономические статьи: УДК раздела 33x (экономика) — рыночная аналитика.
_ECON_UDK = re.compile(r"^\s*УДК\s*(?:\[)?3[23]")
# Рубрики-шапки не-научных секций (КАПС в первых строках сегмента).
_STOP_RUBRIC = re.compile(
    r"^(?:ЭКОНОМИКА И УПРАВЛЕНИЕ ПРОИЗВОДСТВОМ|НОВОСТИ|ХРОНИКА|НАШИ ЮБИЛЯРЫ|"
    r"ПОЗДРАВЛЯЕМ|РЕКЛАМА|ИНФОРМАЦИЯ)\s*$")

MIN_SEG_CHARS = 400          # огрызки (колонтитулы между якорями) не статьи


def segments(text: str):
    """[(start_line, end_line, kind)] — kind: 'preamble'|'science'|'econ'|'rubric'."""
    lines = text.split("\n")
    anchors = [i for i, l in enumerate(lines) if _ANCHOR.match(l)]
    if not anchors:                      # якорей нет — не мультистатейник
        return [(0, len(lines), "science")]
    bounds = [0] + anchors + [len(lines)]
    out = []
    for k in range(len(bounds) - 1):
        s, e = bounds[k], bounds[k + 1]
        if s == e:
            continue
        if k == 0:                       # до первого якоря: обложка/оглавление/рынок
            out.append((s, e, "preamble"))
            continue
        head = lines[s]
        kind = "econ" if _ECON_UDK.match(head) else "science"
        if kind == "science":
            # стоп-рубрика в первых 5 строках сегмента → не наука
            for l in lines[s:min(e, s + 5)]:
                if _STOP_RUBRIC.match(l.strip()):
                    kind = "rubric"
                    break
        out.append((s, e, kind))
    return out


def science_texts(text: str) -> list:
    """Тексты научных сегментов выпуска (каждый — одна статья; привязка сущностей
    в process_doc автоматически ограничивается статьёй)."""
    lines = text.split("\n")
    out = []
    for s, e, kind in segments(text):
        if kind != "science":
            continue
        seg = "\n".join(lines[s:e])
        if len(seg) >= MIN_SEG_CHARS:
            out.append(seg)
    return out


if __name__ == "__main__":  # самопроверка на синтетике
    demo = ("Обложка\nЦены: цинк +60% к 2011 г.\n"
            "УДК 669.2\nСтатья про штейн. Содержание никеля 15%.\n" + "х" * 400 +
            "\nУДК 338.4\nРынок меди вырос.\n" + "у" * 400 +
            "\nУДК 622.7\nФлотация. Извлечение 92%.\n" + "z" * 400)
    segs = segments(demo)
    kinds = [k for _, _, k in segs]
    assert kinds == ["preamble", "science", "econ", "science"], kinds
    st = science_texts(demo)
    assert len(st) == 2 and "штейн" in st[0] and "Флотация" in st[1]
    print("journal self-check OK:", kinds)
