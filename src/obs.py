"""Единая точка логирования/мониторинга (ТЗ NFR). Обёртка над stdlib logging (ponytail).

get_logger — сконфигурированный логгер (basicConfig-подобно, идемпотентно).
log_event — структурная строка события (event + key=val) для мониторинга.
timed — контекст-менеджер, логирующий длительность блока.
Никаких внешних систем; Prometheus/OTel — если понадобится централизованный мониторинг.
"""
from __future__ import annotations
import logging, os, sys, time
from contextlib import contextmanager

try:
    from src.config import env, DATA  # переиспользуем .env-читалку и путь к data/
except Exception:  # автономный запуск/тесты без пакета
    import pathlib
    DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
    def env(key: str, default: str = "") -> str:
        return os.environ.get(key, default)

_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_CONFIGURED = False


def _configure() -> None:
    """Один раз навесить хендлеры на root (идемпотентно)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, (env("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_FMT)
    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(fmt)
    root.addHandler(stderr_h)
    log_file = env("LOG_FILE", "")
    if log_file:
        path = DATA / log_file if not os.path.isabs(log_file) else log_file
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            file_h = logging.FileHandler(path, encoding="utf-8")
            file_h.setFormatter(fmt)
            root.addHandler(file_h)
        except OSError:
            pass  # файл недоступен — довольствуемся stderr
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Сконфигурированный логгер. Уровень из env LOG_LEVEL (по умолч. INFO)."""
    _configure()
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """Структурная строка события: 'event k1=v1 k2=v2' (для мониторинга)."""
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(f"{event} {parts}".rstrip())


@contextmanager
def timed(logger: logging.Logger, label: str):
    """Контекст-менеджер: логирует длительность блока (perf_counter), INFO."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        log_event(logger, "timed", label=label, ms=round((time.perf_counter() - t0) * 1000, 1))


if __name__ == "__main__":
    a = get_logger("obs.selfcheck")
    b = get_logger("obs.other")
    root = logging.getLogger()
    n = len(root.handlers)
    # идемпотентность: повторный вызов не добавляет хендлеры
    get_logger("obs.again")
    assert len(root.handlers) == n, "handlers добавлены повторно"
    assert n >= 1, "нет хендлеров"
    # log_event форматирует поля
    import io
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(h)
    log_event(a, "query", role="expert", results=5)
    with timed(a, "block"):
        pass
    root.removeHandler(h)
    out = buf.getvalue()
    assert "query role=expert results=5" in out, out
    assert "timed label=block ms=" in out, out
    print("OK: idempotent handlers, log_event fields, timed duration")
