# Развёртывание «Научный клубок»

Прод-бандл: FastAPI отдаёт и REST API, и веб-интерфейс с одного порта **8000**.
Граф знаний едет готовым дампом Neo4j — переизвлечение не нужно.

## Требования
- Docker + Docker Compose
- Ключ RouterAI (для семантического поиска, VL и верификации)

## Запуск (3 шага)

```bash
# 1. Настроить окружение
cp .env.example .env
#    → впишите ROUTERAI_API_KEY, при желании смените JWT_SECRET и ADMIN_PASSWORD

# 2. Поднять всё (сборка образа + загрузка дампа графа + старт)
docker compose up -d --build

# 3. Открыть
#    http://localhost:8000   — веб-интерфейс + API
```

Первый вход: **admin / <ADMIN_PASSWORD из .env>** (по умолчанию `admin123`).
Смените пароль в интерфейсе: аватар → «Сменить пароль».

## Что происходит при старте
1. `neo4j-init` разово загружает `artifacts/neo4j.dump` в том Neo4j (76 831 узел,
   446 998 связей, 1288 документов). Повторные `up` — пропускают (маркер `.dump-loaded`).
2. `neo4j` поднимается на загруженном томе.
3. `app` (наш образ) стартует, дожидается healthcheck Neo4j, отдаёт UI+API на :8000.

## Состав
- **app** — Python 3.12 + FastAPI + spaCy(ru) + chromium (внешние источники);
  внутри собранный React-фронтенд (`frontend/dist`), отдаётся тем же сервером.
- **neo4j** — 5.26, граф из дампа.

## Обновить граф (если переизвлекали)
```bash
make dump            # снять свежий artifacts/neo4j.dump с работающего Neo4j
docker compose down && docker volume rm task02_neo4j_data   # сбросить старый том
docker compose up -d --build                                # загрузит новый дамп
```

## Локально без Docker (разработка)
```bash
pip install -r requirements.txt && python -m spacy download ru_core_news_sm
python -m playwright install chromium         # для внешних источников
uvicorn src.api:app --port 8000               # API (+ SPA, если собран frontend/dist)
cd frontend && npm install && npm run dev     # dev-фронт на :5173 (HMR)
```
