# API-контракт для фронтенда — «Научный клубок» (Норникель, Задача 2)

> Все формы ниже сняты с **реальных** возвратов функций на боевом графе (не выдуманы).
> Базовый префикс в примерах: `/api`. Кодировка — UTF-8, `ensure_ascii=false`.

---

## 0. Общее

### Роли (RBAC)
Передаётся в каждом запросе (сейчас без аутентификации — прототип; в проде подставит шлюз).
```
researcher | analyst | project_lead | admin | external_partner
```
Метки RU: Исследователь / Аналитик / Руководитель проекта / Администратор / Внешний партнёр.
RBAC применяется на бэке: `external_partner` не видит `sensitivity=internal/secret` факты
(в ответе поле `hidden_count` > 0). Вкладки Дашборд/Правка — `project_lead`+`admin`;
Аудит — только `admin`.

Транспорт роли — HTTP-заголовок `X-Role`. Если заголовок отсутствует, backend
использует `researcher`. В JSON-тела роль не дублируется.

### Объект `Filters` (тело запроса поиска) — все поля опциональны
```jsonc
{
  "year":        [2020, 2024],      // список годов из multiselect → трактуется как диапазон [min,max]; ИЛИ [] (нет фильтра)
  "geo":         ["RU"],            // нормализованные: "RU" | "WORLD" | конкретная страна ("Kazakhstan")
  "material":    ["никель"],        // подстрока по canon сущности
  "process":     ["электроэкстракция"],
  "confidence":  ["высокая"],       // уровни-слова (см. ниже); UI-хинт
  "min_confidence": 0.5             // число 0..1 — реальный порог отсечения
}
```
Пустой объект / отсутствие фильтров = без ограничений.

### Уровень достоверности (`confidence` 0..1 → слово)
`>= 0.8` → **высокая**, `>= 0.5` → **средняя**, иначе → **низкая**.

### Онтология (для раскраски графа) — 8 типов узлов
`Material, Process, Equipment, Property, Experiment, Publication, Expert, Facility`
(+ служебные в графе-виз: `Document, Parameter, Phase, Condition, Domain, Claim`).
Типы рёбер: `USES_MATERIAL, OPERATES_AT_CONDITION, PRODUCES_OUTPUT, DESCRIBED_IN,
VALIDATED_BY, CONTRADICTS, AUTHORED_BY, IN_DOMAIN, SHOWED, MEASURES, HAS_PARAM`.

---

## 0.1 Аутентификация и пользователи (ТЗ: политики ИБ)

Пароли — **bcrypt** (узлы `:User` в Neo4j, хеш никогда не отдаётся). Сессии —
**JWT** (HS256, `Authorization: Bearer <token>`, TTL из `JWT_TTL_HOURS`, дефолт 12ч).
Роль берётся из токена (авторитетно); при отсутствии токена — фолбэк на `X-Role`
(dev). Первый запуск: если пользователей нет, сидится `admin` с паролем из
`ADMIN_PASSWORD` (дефолт `admin123` — сменить). Управление пользователями — только `admin`.

### `POST /api/auth/login`
`{ "username":"admin", "password":"admin123" }` → `{ "token":"<jwt>", "user":{username,role,full_name,active} }`.
Неверный логин/пароль или заблокированный (`active:false`) → **401**.

### `GET /api/auth/me`  (Bearer)
Текущий пользователь по токену: `{ username, role, full_name, active, authenticated:true }`.

### `POST /api/auth/change-password`  (Bearer)
`{ "old_password", "new_password" }` → `{ ok:true }` (нужен верный старый; ≥6 симв.).

### `GET /api/users`  (admin)
Список: `[{ username, role, full_name, active, created_at, created_by }]` (без хешей).

### `POST /api/users`  (admin)
`{ username, password (≥6), role, full_name? }` → созданный пользователь. Дубликат/плохая роль → **400**.

### `PATCH /api/users/{username}`  (admin)
`{ role?, password?, active?, full_name? }` → обновлённый пользователь. Блокировка = `active:false`.

### `DELETE /api/users/{username}`  (admin)
→ `{ ok:true }`. Себя удалить нельзя (**400**).

Роли (RBAC): `researcher | analyst | project_lead | admin | external_partner`.
Не-admin на `/api/users*` → **403**.

---

## 1. Поиск (главный экран)

### `POST /api/search`
Гибридный поиск (числовая + семантическая дорожки) + сборка ответа. **Функция:** `search.search(query, role, filters)`.

**Запрос**
```jsonc
{ "query": "плотность тока в католите 300 А/м2", "filters": { /* Filters */ } }
```

**Ответ** (`200`)
```jsonc
{
  "intent": "numeric",                 // numeric | search | expert | listing
  "answer_md": "## Результаты поиска…", // готовый Markdown-ответ (grounded), рендерить как markdown
  "facts": [ /* Fact[] */ ],
  "docs":  [ { "doc_id": "cdd5b92b3ff84174", "source": "число" } ],
  "experts": [ /* Expert[] — носители компетенции по теме */ ],
  "recommendations": {
    "similar_cases":  [ /* Doc-подобные */ ],
    "adjacent_topics":[ /* граф-соседи Process/Material */ ],
    "experts":        [ /* авторы релевантных доков */ ]
  },
  "hidden_count": 0,                    // сколько фактов скрыто ролью (RBAC)
  "filters_applied": null              // null | краткое описание применённых фильтров
}
```

**Тип `Fact`** (карточка результата)
```jsonc
{
  "canon": "электроэкстракция",   // сущность (материал/процесс/фаза), к которой относится число
  "metric": "плотность тока",     // измеряемая величина (может быть null)
  "value_low": 300, "value_high": 300, // диапазон; при точечном значении low==high
  "unit": "A_m2",                 // канон единицы (см. таблицу единиц ниже)
  "phase": "католит",             // фаза/среда (может быть null)
  "quote": "плотность тока в католите 300 А/м2", // дословная цитата-первоисточник
  "doc_id": "cdd5b92b3ff84174",
  "year": 2024,                   // может быть null («год не определён»)
  "source": "pattern",            // происхождение факта (pattern|table|manual|…)
  "track": "numeric",             // дорожка поиска
  "ref": "…",                     // внутр. ссылка/ключ
  "confidence": 0.8,              // ОПЦИОНАЛЬНО (0..1) — для бейджа достоверности; при отсутствии показывать «средняя»
  "extracted_at": "2026-07-04T00:08:16Z" // ОПЦИОНАЛЬНО — дата актуализации; при отсутствии «—»
}
```
Единицы (`unit`) → отображение: `pct`→%, `mg_L`→мг/л, `g_t`→г/т, `degC`→°C, `pH`→pH,
`A_m2`→А/м², `t_day`→т/сут, `m3_h`→м³/ч.

**Тип `Expert`**: `{ "name": "Иванов И.И.", "doc_ids": ["…"], "domains": ["гидрометаллургия"] }`.

---

### `GET /api/filters/options`
Значения для наполнения контролов фильтров (годы/гео/материалы/процессы из графа).
**Функция:** DISTINCT-запросы из `src.api.api_filter_options`.
```jsonc
{
  "years":     [2025, 2024, 2023, …],
  "geos":      ["RU", "WORLD", "Kazakhstan", …],
  "materials": ["никель", "медь", "сульфат", …],   // до 200
  "processes": ["электроэкстракция", "плавка", …], // до 200
  "confidence_levels": ["высокая", "средняя", "низкая"]
}
```

---

### `POST /api/literature-review`
Автогенерация литобзора (методы/режимы + Консенсус/Разногласия + N источников + группировка по году/гео).
**Функция:** `search.literature_review(query, filters)`. **Ответ:** `{ "markdown": "…" }` (рендерить как markdown).

### `POST /api/parse-query`
Быстрый rule-based разбор текста для live-подсказок. Тело: `{ "text": "…" }`.
Ответ: `{ "intent", "has_numbers", "values", "entities" }`.

### `GET /api/suggest-entities?q=...`
Автокомплит по canon, алиасам и символам. Ответ:
`[{ "id":"никель", "label":"Ni", "type":"Material", "source_count":12 }]`.

---

## 2. Граф-визуализация

### `POST /api/graph/subgraph`
Реальный подграф-цепочка `материал→процесс→оборудование→результат` + подсветка `CONTRADICTS` + эксперты.
**Функция:** `graph.answer_subgraph(doc_ids, limit)`. `doc_ids` берут из `docs[]`/`facts[].doc_id` ответа поиска.

**Запрос:** `{ "doc_ids": ["cdd5b92b…", "1b2c18e5…"], "limit": 60 }`

**Ответ** (сериализованная форма; на бэке — кортежи):
```jsonc
{
  "nodes": [ { "id": "PR:выщелачивание", "label": "выщелачивание", "type": "Process" } ],
  "edges": [ { "src": "M:магний", "dst": "PR:выщелачивание", "type": "USES_MATERIAL" } ]
}
```
`id` = `"<префикс>:<canon|doc_id>"` (PR: процесс, M: материал, O: продукт, C: условие,
EQ: оборудование, D: документ, X: эксперт). Раскраску вести по `type`.

### `POST /api/graph/neighborhood`
Окрестность сущности для интерактивного графа. Тело:
`{ "entity_id":"PR:электроэкстракция", "depth":2, "limit":60 }`; `depth` ограничен 1…3.
Ответ: `{ "nodes":[{id,label,type,canonical,aliases,source_count,confidence}],
"edges":[{id,source,target,type}] }`.

---

## 3. Эталонные запросы ТЗ (готовые витрины)

| Метод/Путь | Функция | Параметры | Ответ |
|---|---|---|---|
| `GET /api/reference/desalination` | `graph.q_desalination(max_sulfate)` | `max_sulfate=300` | `Row[]` |
| `GET /api/reference/catholyte`    | `graph.q_catholyte()` | — | `Row[]` |
| `GET /api/reference/pgm`          | `graph.q_pgm(years)` | `years=5` | `Row[]` |

**`Row`** (пример pgm): `{ "material":"золото", "phase":"шлак", "metric":"содержание",
"value_low":2.77, "value_high":2.77, "unit":"g_t", "doc_id":"1b2c18e5…", "year":null }`
(desalination: `{material, metric, value_low, value_high, unit, quote, doc_id, year}`).

---

## 4. Противоречия

### `GET /api/contradictions?kind=`
**Функция:** `src.app.fetch_contradictions(kind)`. `kind` = `ru_vs_world` | `method_vs_method` | (пусто = все).
```jsonc
[ { "rel": "CONTRADICTS", "kind": "method_vs_method",
    "src": "de096679…", "dst": "8775b657…", "sources": null } ]
```
(`rel` также `VALIDATED_BY`). Для деталей значений A/B тянуть карточки по `src`/`dst`.

---

## 5. Дашборд руководителя (роль `project_lead`/`admin`)

| Метод/Путь | Функция | Ответ (форма) |
|---|---|---|
| `GET /api/dashboard/summary` | `dashboard.summary_metrics()` | KPI-объект (ниже) |
| `GET /api/dashboard/coverage/domain` | `dashboard.coverage_by_domain()` | `[{domain, documents, facts, experts}]` |
| `GET /api/dashboard/coverage/year` | `dashboard.coverage_by_year()` | `[{year, documents, facts}]` |
| `GET /api/dashboard/coverage/geo` | `dashboard.coverage_by_geo()` | `[{geo, documents, facts}]` |
| `GET /api/dashboard/risks` | `dashboard.risk_zones()` | `{low_sources, contradictions, only_ru, only_world}` |
| `GET /api/dashboard/activity` | `dashboard.activity(limit)` | `[{doc_id, year, geo, facts, experts, last_extracted}]` |
| `GET /api/dashboard/experts` | `dashboard.expert_coverage(limit)` | `[{expert, documents, domains, domain_list}]` |
| `POST /api/dashboard/compare` | `dashboard.compare_technologies(processes)` | таблица сравнения (ниже) |

**`summary`:**
```jsonc
{ "docs":1288, "facts":21198, "experts":148, "domains":5, "contradictions":272,
  "ru":514, "world":755, "geo_unknown":0, "ru_share":0.40, "world_share":0.59,
  "docs_with_facts":144, "fact_coverage":0.112 }
```
**`risks.low_sources[]`:** `{ "entity":"CO", "type":"Material", "sources":1 }`.
**`compare` (запрос `{ "processes":["выщелачивание","обжиг"] }`):**
```jsonc
{
  "axes": ["efficiency_pct","energy","temperature_c","cold_climate","ecology","capex"],
  "meta": { "unavailable": ["capex"] },     // осей нет в корпусе → null (честно)
  "rows": [ { "process":"выщелачивание",
              "efficiency_pct": {"min":15.0,"max":98.0,"unit":"pct","unit_ru":"%","samples":11},
              "energy": null,
              "temperature_c": {"min":0.0,"max":1200.0,"unit":"degC","unit_ru":"°C","samples":…},
              "cold_climate": true, "ecology": {…}, "capex": null } ]
}
```

---

## 6. Экспорт результата

### `POST /api/export/{format}` — `format` = `markdown` | `jsonld` | `pdf`
**Функции:** `exports.to_markdown|to_jsonld|to_pdf`. **Тело:** объект-ответ `/api/search` (целиком).
- `markdown` → `text/markdown` (строка);
- `jsonld` → `application/ld+json` (JSON-LD, schema.org: `@context`/`@graph`);
- `pdf` → `application/pdf` (бинарь, начинается с `%PDF`).
Отдавать как файл (`Content-Disposition: attachment`).

---

## 7. Ручная правка графа (роль `project_lead`/`admin`)

| Метод/Путь | Функция | Тело |
|---|---|---|
| `POST /api/curation/edit`   | `curation.edit_fact(param_key, new_value, editor, comment)` | `{param_key:{doc_id?,canon?,metric?}\|{id}, new_value, editor, comment}` |
| `POST /api/curation/add`    | `curation.add_fact(doc_id, canon, metric, value, unit, editor)` | соответств. поля |
| `POST /api/curation/delete` | `curation.delete_fact(param_key, editor, reason)` | `{param_key, editor, reason}` (мягкое удаление) |
| `GET  /api/curation/history`| `curation.edit_history(limit)` | — → `[{editor, edited_at, comment, deleted}]` |

`edit_fact` → `{ok, before, after, affected}`. Каждая правка штампует `edited_by`/`edited_at`/`manually_edited`.

---

## 8. Уведомления/подписки

| Метод/Путь | Функция | Прим. |
|---|---|---|
| `POST /api/notify/subscribe`   | `notify.subscribe(user, query)` | → `{user, query, last_seen_iso}` |
| `POST /api/notify/unsubscribe` | `notify.unsubscribe(user, query)` | → `bool` |
| `GET  /api/notify/subscriptions`| `notify.list_subscriptions(user?)` | → `[{user, query, last_seen_iso}]` |
| `GET  /api/notify/check`       | `notify.check(user)` | → `[{query, new_count, sample:[{doc_id,canon,metric,quote,when}]}]` |
| `POST /api/notify/mark-seen`   | `notify.mark_seen(user, query)` | сдвигает `last_seen` |

---

## 9. Аудит (роль `admin`)

### `GET /api/audit?limit=500`
**Функция:** `src.app.read_audit(limit)`. Журнал query/view/export.
```jsonc
[ { "ts":"2026-07-03T21:13:14Z", "role":"researcher", "event":"view",
    "payload": { "query":"…", "n_results":3 } } ]
```
`event` = `query | view | export | edit | subscribe`. Экспорт журнала — тот же массив как JSONL-файл.

---

## 10. Документы

### `GET /api/documents`
Параметры: `q`, `doc_type`, `lang`, `sensitivity`, `geo`, `year_from`, `year_to`,
`page`, `page_size`, `sort=relevance|date|trust`. Ответ:
`{total,page,page_size,items:[{doc_id,name,doc_type,year,lang,sensitivity,pages,cat,src,fact_count,geo}]}`.

### `GET /api/documents/{doc_id}`
Карточка документа: `{meta,facts_count,facts}` с топ-50 фактами и цитатами.

### `POST /api/documents`
`multipart/form-data`, ровно одно поле `file` на запрос. Ответ содержит `doc_id`,
`duplicate`, а для нового документа также `doc_type`, `pages`, `chars`, `lang`,
`facts_added`, `edges_added`, `vl_table_facts`.

### `GET /api/external/search`
Поиск статей во внешнем источнике (CyberLeninka) через headless-браузер.
Параметры: `q` (≥2), `limit`. Ответ: `{query, results:[{slug,title,authors,url,pdf_url}]}`.

### `POST /api/external/import`
Скачивает PDF статьи и грузит в граф тем же путём, что `POST /documents`.
Тело: `{url, title?}`. Ответ: как у загрузки + `source`, `src_url`.

---

## Сводка «эндпоинт → функция» (для сборки адаптера)
```
POST /api/search                 → search.search(query, role, filters)
POST /api/parse-query            → grammar.parse_values + gazetteer.Matcher
GET  /api/suggest-entities       → gazetteer + source_count из графа
GET  /api/filters/options        → DISTINCT-запросы из src.api
POST /api/literature-review      → search.literature_review(query, filters)
POST /api/graph/subgraph         → graph.answer_subgraph(doc_ids, limit)
POST /api/graph/neighborhood     → локальная Cypher-окрестность сущности
GET  /api/reference/desalination → graph.q_desalination(max_sulfate)
GET  /api/reference/catholyte    → graph.q_catholyte()
GET  /api/reference/pgm          → graph.q_pgm(years)
GET  /api/contradictions         → src.app.fetch_contradictions(kind)
GET  /api/dashboard/*            → dashboard.{summary_metrics,coverage_by_*,risk_zones,activity,expert_coverage}
POST /api/dashboard/compare      → dashboard.compare_technologies(processes)
POST /api/export/{fmt}           → exports.{to_markdown,to_jsonld,to_pdf}
POST /api/curation/{edit,add,delete}, GET /api/curation/history → curation.*
POST /api/notify/*, GET /api/notify/*  → notify.*
GET  /api/audit                  → src.app.read_audit(limit)
GET/POST /api/documents          → библиотека, карточка и pipeline загрузки
```

Драйвер графа один на процесс: `graph.driver()` (Neo4j `bolt://localhost:7687`).
Ошибки: все функции деградируют мягко (пустой список/`{}`), Neo4j-недоступность → `503`
в адаптере. Роль передавать только заголовком `X-Role`.
