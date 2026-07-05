# Архитектура — «Научный клубок» (граф знаний R&D)

Граф знаний для горно-металлургического R&D Норникеля: вопрос на естественном языке →
ответ с числами, цитатами, источниками и вычисленными противоречиями/подтверждениями.

## Ключевой принцип

**Детерминированное ядро + опциональный LLM/OCR-слой.**

- **Числа** извлекаются regex-грамматикой величин — не галлюцинируют (ТЗ: «ошибки в
  концентрациях/температурах недопустимы»).
- **Сущности** — газетиром (закрытый металлургический домен, RU↔EN).
- **Таблицы составов** — Qwen3-VL OCR (RouterAI) (плоское извлечение текста расцепляет
  элемент↔значение — главная слабость, решённая структурным OCR).
- **LLM / эмбеддинги / OCR** — обогащение поверх ядра, отключаемы. Модели open-weight
  (Qwen3, DeepSeek) → self-hostable on-prem. **Без ключа система полнофункциональна**
  (rule-based путь).

---

## 1. Поток данных (build-time) — конвейер извлечения

```
Сырой корпус (2017 файлов: PDF/DOCX/PPTX/XLS + zip/rar/split-архивы)
   │
[0+1] extract.py
   │   MAGIC-сниффинг типа (не по расширению), склейка split-архивов .001/.002,
   │   диспетчер fitz(PDF) / python-docx / soffice(.doc) / pandas(xls),
   │   дедуп по md5 нормализованного текста, year из имени файла,
   │   sensitivity + kg_value по категории
   │   → artifacts/docs.meta.jsonl (в git)  +  data/docs.text.jsonl (локально, gitignored)
   │
[0.5] normalize.py
   │   OCR-нормализация: омоглифы кириллица↔латиница (только С→C для °С),
   │   дегифенация переносов «выщелачива-\nние», удаление колонтитулов
   │
   ├──────────────── ВЕТКА A: текст → факты из прозы ────────────────┐
   │                                                                  │
[2] gazetteer.py            [3] grammar.py             pipeline.process_doc
   PhraseMatcher RU↔EN,      regex-грамматика величин:  сегментация на предложения,
   3 яруса:                  • диапазоны X–Y, от…до, ÷  для каждого:
   • ORTH (регистр:          • компараторы <,≤,не более  • gazetteer.match → упоминания
     Co≠co, Fe/S/Se…)        • канонизация ~8 семейств   • grammar.parse_values → факты
   • LEMMA (падежи)            единиц (мг/л, °C, А/м2…)   • [4-мини] глагольные паттерны →
   • LOWER (EN-алиасы)       • метрика↔единица матрица    uses_material/produces_output/
   ~150 канонов +            • conditions co-extraction   operates_at_condition (source=pattern)
   гео-слой Deposit/         • sanity + confidence        РЕЗОЛЬВЕР: метрика↔ТИП-сущности
   Enterprise/Country                                     (COMPAT), zip перечислений,
                                                          приоритет grammar-материалу
   │                                                                  │
   └────────────────► artifacts/facts.jsonl + artifacts/edges.jsonl ◄─┘
   │
   ├──────────────── ВЕТКА B: таблицы составов (vision.py) ──────────┐
   │   PDF: рендер страницы (пре-фильтр составности) → Qwen3-VL  │
   │        OCR model=table → структурные ячейки (строка/столбец)     │
   │   DOCX: python-docx таблицы напрямую (без OCR)                    │
   │   table_to_facts: детект строки-заголовка (элементы), привязка   │
   │        элемент↔значение, склейка диапазонов, инференс единицы     │
   │        (major-оксиды→%), фаза из заголовка, метка≠значение,       │
   │        транспонированные таблицы. Кэш OCR на диске (идемпотентно) │
   │        → artifacts/vision_facts.jsonl                             │
   │
[year_fill.py] год из front-matter/выходных данных для null-доков
   │
[6] graph.py  — загрузка в Neo4j (UNWIND-батчи ~10 запросов):
   │   MERGE Document/Material/Process/Equipment/Phase/Facility/Parameter,
   │   реификация Experiment по (doc_id, conditions) с sanity-гейтом,
   │   дедуп pkey (int/float норм), рёбра MENTIONS/HAS_PARAM/MEASURES/
   │   MEASURED_IN/DESCRIBED_IN + типизированные; индексы (constraint pkey/canon,
   │   RANGE value_low/high, составной metric+unit, FULLTEXT, btree year/geo)
   │
[5] contradictions.py — вычисление верификационного слоя (поверх графа):
   │   группировка (metric, canon, phase, unit) → пары из разных доков:
   │   расхождение ≤10% → VALIDATED_BY, >20% → CONTRADICTS;
   │   гейты: comparator-лимиты, фаза-носитель, sanity-границы, дедуп цитат,
   │   широкие диапазоны; ru_vs_world по доле кириллицы В ТЕЛЕ текста;
   │   kind = ru_vs_world | method_vs_method
   │
[7] embed.py — семантический индекс:
       bge-m3 (1024d, RouterAI) → artifacts/emb.npy
       (numpy-матрица, ноль локального torch/RAM)
```

## 2. Поток запроса (runtime) — search.py + api.py

```
Вопрос пользователя (RU/EN)
   │
 ┌─ grammar.parse_query — ТА ЖЕ грамматика величин (симметрия запрос↔документ)
 └─ llm.parse_query — DeepSeek-V3 (RouterAI) интент (race с rule-based, таймаут 8с → фолбэк)
   │
 Три дорожки:
   ├─ ЧИСЛОВАЯ:  Cypher по Parameter (RANGE-пересечение диапазонов, сортировка по
   │             близости к цели), + эталонные shortcuts q_desalination/q_catholyte/
   │             q_pgm (RU + EN-триггеры)
   ├─ СЕМАНТИЧЕСКАЯ: emb.npy dot-product по запросу (bge-m3-эмбеддинг (RouterAI)),
   │             score-floor против шума
   └─ (графовая — окрестность узлов, P2)
   │
 Слияние по doc_id (ref → in_range → близость), дедуп
   │
 RBAC-фильтр (5 ролей: researcher/analyst/project_lead/admin/external_partner;
   sensitivity-сегментация; аудит query/view/export → artifacts/audit.jsonl)
   │
 Композер: экстрактивный ответ (факт + значение + единица + фаза + verbatim-цитата +
   документ), блок «⚠ Противоречия», «✓ Подтверждено», релевантные документы, эксперты
   │
 Отдача: FastAPI (api.py) → React-интерфейс (Поиск / Граф / Качество / Аналитика /
   Источники / Внешние источники / Пользователи); JWT+bcrypt, экспорт MD/JSON-LD/PDF
```

## 3. Онтология графа (Neo4j)

### Узлы (8 типов ТЗ + служебные)

| Узел | Тип ТЗ | Роль |
|---|---|---|
| `Document` | Publication | публикация/отчёт; year, geo, sensitivity, kg_value |
| `Parameter` | Property | числовой факт: value_low/high, unit_canon, metric, comparator, **confidence, quote, source, extracted_at, pipeline_version** |
| `Experiment` | Experiment | реификация группы Parameter одного (doc_id, conditions) |
| `Material` | Material | вещества/элементы (canon + aliases RU/EN/символы) |
| `Process` | Process | процессы |
| `Equipment` | Equipment | оборудование |
| `Phase` | — | штейн/файнштейн/шлак/раствор/католит/анолит… |
| `Facility` | Facility | месторождения/предприятия (+ geo) |
| `Author`/`Organization` | Expert | из front-matter журналов (индекс-слой) |

### Рёбра (6 связей ТЗ + служебные)

| Ребро | Связь ТЗ |
|---|---|
| `USES_MATERIAL` | uses_material |
| `OPERATES_AT_CONDITION` | operates_at_condition |
| `PRODUCES_OUTPUT` | produces_output |
| `DESCRIBED_IN` | described_in |
| `VALIDATED_BY` | validated_by |
| `CONTRADICTS` | contradicts (+ VARIES_WITH_CONDITIONS) |
| `MEASURES`, `MEASURED_IN`, `HAS_PARAM`, `MENTIONS` | служебные (провенанс/навигация) |

**Модель верификации знаний (ТЗ):** каждый факт несёт источник (doc_id + quote),
уровень достоверности (confidence), дату актуализации (extracted_at) и версию
пайплайна (pipeline_version).

## 4. Стек и обоснование решений

| Слой | Технология | Почему |
|---|---|---|
| Числа | собственная regex-грамматика | детерминизм, «ошибки в концентрациях недопустимы»; одна грамматика на документы И запросы |
| Сущности | spaCy PhraseMatcher + pymorphy3 | закрытый домен, падежи; словарь из корпуса |
| Таблицы | **Qwen3-VL OCR (RouterAI)** (model=table) | плоский текст расцепляет состав; API → ноль локального RAM; RU+EN |
| LLM build-time | DeepSeek-V3 (RouterAI) | лучший RU+JSON; open-weight → self-hostable |
| LLM runtime | DeepSeek-V3 (RouterAI) | быстрый парсинг запросов; фолбэк на rule-based |
| Семантика | bge-m3 (1024d, RouterAI) | RU/EN одно пространство; ноль локального torch |
| Граф | Neo4j 5.26 community (docker) | Cypher, RANGE-индексы, dump, граф-виз |
| API | FastAPI (JWT + bcrypt, 42 эндпоинта) | контракт фронта, авторизация, Swagger |
| UI | React + Vite + React Flow | интерактивный граф; отдаётся тем же сервером |

**Отвергнуто:** LLM-извлечение чисел (галлюцинации), FAISS (numpy достаточно),
локальный e5/torch (RAM), C++/Rust (профиль I/O+regex — горячее уже в C/Cython).

## 5. Модули (ядро; ключевые)

| Модуль | Строк | Назначение |
|---|---:|---|
| `grammar.py` | 808 | числовая грамматика величин (ядро) |
| `search.py` | 900 | гибридный поиск + композер ответа |
| `api.py` | — | FastAPI: 42 эндпоинта, JWT+bcrypt, отдача React-SPA |
| `vision.py` | 437 | Vision OCR таблиц + DOCX-таблицы |
| `graph.py` | 423 | Neo4j: схема, батч-загрузка, 3 эталонных запроса |
| `contradictions.py` | 370 | вычисление CONTRADICTS/VALIDATED_BY |
| `pipeline.py` | 309 | оркестрация build-time + резольвер |
| `llm.py` | 239 | RouterAI OpenAI-совместимый клиент + кэш |
| `gazetteer.py` | 223 | газетир RU↔EN, 3 яруса |
| `normalize.py` | 184 | OCR-нормализация текста |
| `extract.py` | 182 | извлечение текста из корпуса |
| `embed.py` | 171 | семантика (bge-m3, RouterAI) |
| `config.py`, `load.py`, `year_fill.py` | 151 | конфиг / загрузчик-энтрипоинт / год |

**Тесты:** 12 модулей, **242 теста** (`pytest tests/`); grammar golden 98.8%.

## 6. Артефакты пайплайна

| Файл | В git | Содержимое |
|---|---|---|
| `artifacts/docs.meta.jsonl` | ✓ | метаданные документов (не текст) |
| `artifacts/facts.jsonl` | ✓ | числовые факты грамматики (провенанс) |
| `artifacts/vision_facts.jsonl` | ✓ | факты из таблиц составов |
| `artifacts/edges.jsonl` | ✓ | типизированные рёбра |
| `artifacts/emb.npy` | ✓ | семантический индекс (numpy) |
| `artifacts/*.dump` | ✓ | Neo4j dump (воспроизводимость) |
| `data/docs.text.jsonl` | ✗ (gitignored) | полнотекст (редистрибуция) |
| `data/ocr_cache/` | ✗ | кэш OCR-ответов |
| `.env` | ✗ | ключ RouterAI (не в поставке) |

Белый список в `.gitignore`: полнотекст платных журналов не попадает в git.

## 7. Развёртывание

```bash
cp .env.example .env               # впишите ROUTERAI_API_KEY
docker compose up -d --build       # сборка + загрузка дампа графа + старт
# → http://localhost:8000           React-интерфейс + REST API
```

- `docker-compose.yml`: neo4j-init грузит `artifacts/neo4j.dump` → neo4j → app
  (python:3.12-slim + chromium). Единый контейнер отдаёт и UI, и API на :8000.
- **Без API-ключа система полнофункциональна**: rule-based парсер запросов, ядро
  числовое+графовое офлайн; отключаются только LLM-обогащение, vision-ре-билд, семантика.
- Кросс-платформенно: build-time бинари (soffice/tesseract) опциональны с graceful skip;
  судьям нужны только артефакты + Neo4j dump (build-time не требуется).

## 8. Соответствие ТЗ (сводка)

- **Онтология 8/8**, **связи 6/6** (см. §3).
- **Числовые диапазоны** («сульфаты <200 мг/л») — грамматика + RANGE-индексы.
- **Мультиязычность RU/EN** — омоглифы, aliases_en, латинские единицы, кросс-язычные эмбеддинги.
- **Модель верификации** — confidence + quote + extracted_at + pipeline_version на каждом факте.
- **Отеч-vs-мир** — ru_vs_world по языку тела; фильтр в UI.
- **Пробелы, противоречия, литобзор** — вычислимые из графа.
- **RBAC 5 ролей + аудит** — конфиг-матрица + sensitivity + JSONL-лог.
- **3 эталонных запроса ТЗ** — отвечают с провенансом.
- Полная трассировка требований — в исходном репозитории (ссылка VCS в форме подачи).

## 9. Границы и осознанные решения

- **Покрытие:** обработаны все **1288 документов**; факты извлечены из **709** (55%).
  Журналы (цельные номера) прошли трёхслойную защиту точности (сегментация по УДК +
  гварды + LLM-верификатор) — точность извлечения из них доведена до **99.95%**.
- **Точность:** значения/единицы — детерминированно (golden precision 0.991); привязка
  к сущности гейтится confidence (механизм «уровня достоверности» из ТЗ).
- ТЗ **не задаёт числовой порог** точности — требует корректных чисел + указания
  достоверности; и то, и другое обеспечено.
