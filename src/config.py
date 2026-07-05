"""Центральная конфигурация. Читает .env без внешних зависимостей (ponytail)."""
from __future__ import annotations
import os, pathlib, unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
GOLDEN = DATA / "golden"
for d in (DATA, ARTIFACTS, GOLDEN):
    d.mkdir(parents=True, exist_ok=True)

def _load_env(path: pathlib.Path = ROOT / ".env") -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_ENV = _load_env()
def env(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key, default)

# --- Артефакты пайплайна ---
DOCS_META = ARTIFACTS / "docs.meta.jsonl"     # едет в git (метаданные, не текст)
DOCS_TEXT = DATA / "docs.text.jsonl"          # локальный, полнотекст, gitignored
FACTS = ARTIFACTS / "facts.jsonl"             # числовые факты (провенанс)
EDGES = ARTIFACTS / "edges.jsonl"             # рёбра графа
GAZETTEER = DATA / "gazetteer.yml"
LLM_CACHE = ROOT / "llm_cache"                # кэш batch-ответов, gitignored

# --- Корпус ---
CORPUS_DIR = pathlib.Path(env("CORPUS_DIR", str(ROOT / "Источники информации")))

# --- LLM/эмбеддинги: провайдер RouterAI (routerai.ru) -------------------------
# Российский OpenAI-совместимый роутер моделей (санкц-чистый). Ключ ROUTERAI_API_KEY.
LLM_PROVIDER = env("LLM_PROVIDER", "routerai").lower()
ROUTERAI_API_KEY = env("ROUTERAI_API_KEY")

LLM_BASE_URL = env("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")
LLM_API_KEY = ROUTERAI_API_KEY
LLM_AUTH_SCHEME = "Bearer"                       # «Authorization: Bearer <k>»
LLM_EXTRA_HEADERS = {"X-Title": "Nornickel-KG"}  # атрибуция (опц.)
# Модели (env-переопределяемы). Дефолты: бюджет + сильный русский.
CHAT_STRONG = env("CHAT_STRONG_MODEL", "deepseek/deepseek-chat")   # build: RU+JSON
CHAT_FAST = env("CHAT_FAST_MODEL", "deepseek/deepseek-chat")       # runtime intent
# эмбеддинги: bge-m3 (1024d, топ-мультиязычный). Альт.:
#   qwen/qwen3-embedding-8b (4096d), openai/text-embedding-3-large (лучший EN+RU).
EMBED_DOC_MODEL = env("EMBED_MODEL", "baai/bge-m3")
EMBED_QUERY_MODEL = EMBED_DOC_MODEL               # одна модель для doc и query
LLM_ENABLED = bool(LLM_API_KEY)

# Обратная совместимость имён (llm.py импортирует QWEN_MODEL/FLASH_MODEL).
QWEN_MODEL = CHAT_STRONG
FLASH_MODEL = CHAT_FAST

# --- VL/OCR: мультимодальная модель для таблиц НОВЫХ загрузок ------------------
# Корпус уже OCR-нут (кэш data/ocr_cache) — VL нужен только для таблиц в свежих
# загрузках через POST /api/documents. Числа из VL всё равно валидируются
# детерминированной грамматикой vision.py.
# САНКЦИИ: Qwen3-VL (Alibaba, Китай) — НЕ под санкциями РФ, open-weight (self-host);
# OCR 32 языка вкл. русский, уровень Gemini 2.5 Pro. Альт.: qwen/qwen3-vl-30b-a3b-instruct (дешевле).
VL_MODEL = env("VL_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
VL_ENABLED = bool(ROUTERAI_API_KEY)

# --- Neo4j ---
NEO4J_URI = env("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = env("NEO4J_USER", "neo4j")
NEO4J_PASS = env("NEO4J_PASS", "task2password")

# --- Эмбеддинги (локальный e5, офлайн; см. план) ---
EMBED_MODEL = "intfloat/multilingual-e5-small"

PIPELINE_VERSION = "0.1.0"

# Человекочитаемые единицы (канон → RU для UI/ответов)
UNIT_DISPLAY = {"mg_L": "мг/л", "g_t": "г/т", "pct": "%", "degC": "°C",
                "A_m2": "А/м²", "m3_h": "м³/ч", "t_day": "т/сут", "pH": "pH",
                "mol_L": "моль/л", "geq_L": "г-экв/л"}
def unit_ru(u):
    return UNIT_DISPLAY.get(u, u or "")

def nfc(s):
    return unicodedata.normalize("NFC", s) if isinstance(s, str) else s

# --- Онтология ТЗ (8 типов, 6 связей) ---
NODE_TYPES = ["Material", "Process", "Equipment", "Property", "Experiment",
              "Publication", "Expert", "Facility"]  # + служебные: Parameter, Metric, Phase, Claim, Domain, Topic, Standard, Organization, Author, Document, Article
EDGE_TYPES = ["uses_material", "operates_at_condition", "produces_output",
              "described_in", "validated_by", "contradicts"]
