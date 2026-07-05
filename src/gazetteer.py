"""ЭТАП 2: газетир RU↔EN. Двухъярусный spaCy PhraseMatcher.

Ярус ORTH (регистр важен): аббревиатуры/формулы/имена/топонимы (Co≠co≠со, ПВП, МПГ).
  Топонимы (type=Facility) матчатся только если рядом (±5 токенов) есть
  завод|рудник|шахта|месторождение|комбинат.
Ярус LEMMA (ru_core_news_sm, disable=[parser,ner]): нарицательные ≥4 симв.
EN-алиасы — lowercase-матч (отдельный ORTH-матчер по нижнему регистру).
"""
from __future__ import annotations
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from src.config import GAZETTEER, nfc

# Кириллица в аббревиатурах ловится ORTH-ярусом; латиница формул — тоже ORTH.
_CYR = re.compile(r"[а-яё]", re.IGNORECASE)
_LAT = re.compile(r"[a-z]", re.IGNORECASE)
# Триггеры-соседи для топонимов (леммы).
_GEO_TRIGGERS = {"завод", "рудник", "шахта", "месторождение", "комбинат", "фабрика"}
_GEO_WINDOW = 5

# Единицы концентрации/содержания — контекст для символов-омонимов (S/As/V).
_NUM_UNITS = {"%", "г-т", "г/т", "мг/л", "ppm", "ppb", "г", "мг", "кг", "т"}


def build_gazetteer(path=GAZETTEER):
    """Загрузить и провалидировать YAML газетира. Возвращает list[dict]."""
    with open(path, "r", encoding="utf-8") as f:
        rows = yaml.safe_load(f)
    assert isinstance(rows, list) and rows, "газетир пуст или не список"
    canons = set()
    for r in rows:
        assert isinstance(r, dict), f"запись не dict: {r!r}"
        for k in ("type", "canon", "aliases"):
            assert k in r and r[k], f"нет поля {k}: {r!r}"
        assert isinstance(r["aliases"], list), f"aliases не список: {r!r}"
        for opt in ("aliases_en", "symbols", "symbols_guarded", "aliases_infl"):
            r.setdefault(opt, [])
            assert isinstance(r[opt], list), f"{opt} не список: {r!r}"
        # aliases_infl имеет смысл только для топонимов (косвенные падежи).
        assert not r["aliases_infl"] or r["type"] == "Facility", \
            f"aliases_infl только для Facility: {r!r}"
        key = (r["type"], nfc(r["canon"]))
        assert key not in canons, f"дубль канона: {key}"
        canons.add(key)
    return rows


def _has_cyr(s):
    return bool(_CYR.search(s))


def _min_word_len(phrase):
    """Длина самого длинного слова алиаса (для порога ≥4 у нарицательных)."""
    words = re.findall(r"\w+", phrase, flags=re.UNICODE)
    return max((len(w) for w in words), default=0)


class Matcher:
    """Двухъярусный матчер. Держит own spaCy nlp (RU) для лемматизации.

    Ярусы:
      * _orth   — ORTH (регистр важен): RU-аббревиатуры/формулы/имена + топонимы.
      * _lemma  — LEMMA: RU-нарицательные (≥4 симв) + косвенные падежи топонимов
                  (aliases_infl; для Facility по-прежнему нужен гео-триггер).
      * _en     — ORTH по lowercased doc: EN-алиасы (без коротких омонимов co/u/mg).
      * _sym    — ORTH (регистр важен, однотокенно): символы элементов (Co, Fe — не co/fe).
      * _sym_g  — ORTH: символы-омонимы англ. слов (S/As/V); принимаются только рядом
                  с числом/%/г-т или в списке символов через запятую.
    Топонимы помечены в _facility_ids → требуют гео-триггер рядом.
    Символы помечены в _symbol_ids; охраняемые — ещё и в _guarded_ids.
    """

    def __init__(self, rows=None, nlp=None):
        from spacy.matcher import PhraseMatcher
        import spacy

        self.rows = rows if rows is not None else build_gazetteer()
        self.nlp = nlp or spacy.load("ru_core_news_sm", disable=["parser", "ner"])

        # id -> (canon, type)
        self.meta = {}
        self._facility_ids = set()
        self._symbol_ids = set()      # ключи с символами элементов (_sym и/или _sym_g)
        self._guarded_ids = set()     # подмножество: нужен числовой/списочный контекст

        self._orth = PhraseMatcher(self.nlp.vocab, attr="ORTH")
        self._lemma = PhraseMatcher(self.nlp.vocab, attr="LEMMA")
        self._en = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self._sym = PhraseMatcher(self.nlp.vocab, attr="ORTH")
        self._sym_g = PhraseMatcher(self.nlp.vocab, attr="ORTH")

        for i, r in enumerate(self.rows):
            key = f"g{i}"
            self.meta[key] = (nfc(r["canon"]), r["type"])
            is_facility = r["type"] == "Facility"
            if is_facility:
                self._facility_ids.add(key)

            orth_docs, lemma_docs = [], []
            for alias in r["aliases"]:
                alias = nfc(alias)
                longest = _min_word_len(alias)
                # Аббревиатуры/формулы/имена собственные/короткие → ORTH (регистр важен).
                # Facility-топонимы всегда ORTH (имена собственные).
                is_abbrev = alias.isupper() or any(c.isupper() for c in alias)
                to_orth = is_facility or is_abbrev or longest < 4 or not _has_cyr(alias)
                if to_orth:
                    orth_docs.append(self.nlp.make_doc(alias))
                else:
                    # LEMMA-паттерн: нужны леммы → прогоняем через полный pipeline.
                    lemma_docs.append(self.nlp(alias))
            # Косвенные падежи топонимов → LEMMA-ярус (гео-триггер остаётся обязательным).
            for alias in r.get("aliases_infl", []):
                lemma_docs.append(self.nlp(nfc(alias)))
            if orth_docs:
                self._orth.add(key, orth_docs)
            if lemma_docs:
                self._lemma.add(key, lemma_docs)

            en_docs = [self.nlp.make_doc(nfc(a).lower()) for a in r.get("aliases_en", [])]
            if en_docs:
                self._en.add(key, en_docs)

            # Символы элементов: ORTH (регистр важен) + однотокенная форма (word-boundary).
            sym_docs = [self.nlp.make_doc(nfc(s)) for s in r.get("symbols", [])]
            if sym_docs:
                self._symbol_ids.add(key)
                self._sym.add(key, sym_docs)
            symg_docs = [self.nlp.make_doc(nfc(s)) for s in r.get("symbols_guarded", [])]
            if symg_docs:
                self._symbol_ids.add(key)
                self._guarded_ids.add(key)
                self._sym_g.add(key, symg_docs)

    def _facility_ok(self, doc, s, e):
        """Топоним валиден только при гео-триггере в окне ±_GEO_WINDOW токенов."""
        lo = max(0, s - _GEO_WINDOW)
        hi = min(len(doc), e + _GEO_WINDOW)
        for t in doc[lo:hi]:
            if t.lemma_.lower() in _GEO_TRIGGERS or t.text.lower() in _GEO_TRIGGERS:
                return True
        return False

    def _is_hyphen_compound(self, doc, s, e):
        """Символ — префикс дефисного слова (Co-operation, Co-worker)? Тогда это не
        формула элемента: токен символа, затем '-', затем буквенный токен."""
        if e + 1 < len(doc) and doc[e].text == "-" and doc[e + 1].text[:1].isalpha():
            return True
        return False

    def _symbol_context_ok(self, doc, s, e):
        """Охраняемый символ (S/As/V) валиден, только если это изолированный токен
        рядом с числом/%/г-т ИЛИ в списке через запятую с другими символами.

        Соседи слева/справа (непосредственно примыкающие токены) проверяются на:
          * число (like_num),
          * единицу измерения (%/г-т/…),
          * запятую (пункт списка символов).
        """
        # Предыдущий и следующий значащие токены.
        prev_t = doc[s - 1] if s - 1 >= 0 else None
        next_t = doc[e] if e < len(doc) else None
        for t in (prev_t, next_t):
            if t is None:
                continue
            txt = t.text.lower()
            if t.like_num or txt in _NUM_UNITS or txt == ",":
                return True
        return False

    def match(self, text, lang="RU"):
        """text -> list[{canon, type, span:[char_s, char_e], form}].

        lang не меняет логику ярусов (EN-ярус работает всегда, lowercase),
        но оставлен в сигнатуре для совместимости с пайплайном.
        """
        text = nfc(text)
        doc = self.nlp(text)
        hits = {}  # (char_s, char_e, key) -> record (дедуп перекрытий идентичных)

        def add(key, s_tok, e_tok):
            span = doc[s_tok:e_tok]
            canon, typ = self.meta[key]
            if key in self._facility_ids and not self._facility_ok(doc, s_tok, e_tok):
                return
            k = (span.start_char, span.end_char, key)
            hits[k] = {
                "canon": canon,
                "type": typ,
                "span": [span.start_char, span.end_char],
                "form": span.text,
            }

        for mid, s, e in self._orth(doc):
            add(self.nlp.vocab.strings[mid], s, e)
        for mid, s, e in self._lemma(doc):
            add(self.nlp.vocab.strings[mid], s, e)
        for mid, s, e in self._en(doc):
            add(self.nlp.vocab.strings[mid], s, e)
        # Символы элементов (ORTH, регистр важен). Однотокенные по построению.
        # Отсекаем префикс дефисного слова (Co-operation → не кобальт).
        for mid, s, e in self._sym(doc):
            if not self._is_hyphen_compound(doc, s, e):
                add(self.nlp.vocab.strings[mid], s, e)
        # Охраняемые символы (S/As/V): только рядом с числом/%/г-т или в списке.
        for mid, s, e in self._sym_g(doc):
            if not self._is_hyphen_compound(doc, s, e) and self._symbol_context_ok(doc, s, e):
                add(self.nlp.vocab.strings[mid], s, e)

        # Снять вложенные дубли: если один и тот же (span, canon) пришёл из
        # нескольких ярусов, hits уже дедуплицировал по ключу (s,e,key).
        out = list(hits.values())
        out.sort(key=lambda h: (h["span"][0], h["span"][1]))
        return out


if __name__ == "__main__":
    m = Matcher()
    demo = "В Скалистом руднике получают никель и nickel; содержание Co и со временем МПГ."
    for h in m.match(demo):
        print(h)
