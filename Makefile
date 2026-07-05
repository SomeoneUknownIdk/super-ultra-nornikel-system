.PHONY: help extract normalize gazetteer facts relations load search test demo up down dump load-dump check-secrets
help:
	@echo "extract   — этап 0+1: сырой корпус → docs.meta.jsonl + docs.text.jsonl"
	@echo "normalize — этап 0.5: омоглифы/дегифенация/колонтитулы"
	@echo "gazetteer — этап 2: собрать газетир, разметить упоминания"
	@echo "facts     — этап 3: числовые параметры (грамматика)"
	@echo "relations — этап 4-мини + 4' (LLM): рёбра"
	@echo "load      — этап 6: загрузка графа в Neo4j"
	@echo "test      — все тесты этапов"
	@echo "demo      — up + load дампа + streamlit"

extract:   ; python -m src.extract
normalize: ; python -m src.normalize
gazetteer: ; python -m src.gazetteer
facts:     ; python -m src.grammar --run
relations: ; python -m src.relations
load:      ; python -m src.graph --load
search:    ; python -m src.search
test:      ; python -m pytest tests/ -q
up:        ; docker compose up -d
down:      ; docker compose down
dump:      ; docker compose stop neo4j && docker run --rm -v task2_neo4j_data:/data -v $(PWD)/artifacts:/artifacts neo4j:5.26.0 neo4j-admin database dump neo4j --to-path=/artifacts && docker compose start neo4j
load-dump: ; docker run --rm -v task2_neo4j_data:/data -v $(PWD)/artifacts:/artifacts neo4j:5.26.0 neo4j-admin database load neo4j --from-path=/artifacts --overwrite-destination
# объективный чек белого списка (план, чеклист 22:00): вывод должен быть ПУСТ
check-secrets:
	@git ls-files | grep -vE '^(src/|tests/|ops/|Makefile|README|Dockerfile|requirements\.txt|docker-compose|\.env\.example|\.gitignore|TZ\.md|PLAN\.md|data/gazetteer|artifacts/docs\.meta\.jsonl|artifacts/(facts|edges)\.jsonl|artifacts/.*\.(dump|npy))' || echo "OK: репозиторий чист"
