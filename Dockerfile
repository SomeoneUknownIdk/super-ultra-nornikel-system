# ═══ Stage 1: сборка фронтенда (относительный /api → same-origin) ═══
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN VITE_API_MODE=http VITE_API_BASE_URL= npm run build

# ═══ Stage 2: рантайм — FastAPI отдаёт и /api, и собранный SPA ═══
FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app

# зависимости + spaCy RU-модель + chromium для внешних источников (playwright)
COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && python -m spacy download ru_core_news_sm \
 && python -m playwright install --with-deps chromium

# код
COPY src/ ./src/

# данные рантайма: индексы поиска + метаданные + газетир + полнотекст (превью)
COPY artifacts/emb.npy artifacts/emb_meta.json artifacts/kw_index.json artifacts/docs.meta.jsonl ./artifacts/
COPY data/gazetteer.yml data/docs.text.jsonl ./data/

# собранный фронтенд из stage 1
COPY --from=frontend /fe/dist ./frontend/dist

EXPOSE 8000
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
