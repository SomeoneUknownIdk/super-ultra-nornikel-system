"""Поиск и скачивание PDF из внешнего источника — CyberLeninka (открытый доступ).

Порт логики new_module/server.js на sync-playwright: CyberLeninka отдаёт контент
только реальному браузеру (обычный HTTP → 400), поэтому скрапим headless-chromium.
Вызывается из sync-эндпоинтов FastAPI (они и так исполняются в threadpool, поэтому
sync-playwright не конфликтует с asyncio-циклом).
"""
from __future__ import annotations

from urllib.parse import quote

BASE = "https://cyberleninka.ru"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# JS-скрапер списка результатов (селекторы — из new_module/server.js).
_SEARCH_JS = r"""() => {
  const items = [];
  const lis = document.querySelectorAll('#search-results li');
  for (const li of lis) {
    const link = li.querySelector('h2.title a');
    if (!link) continue;
    const href = link.getAttribute('href');
    const m = href.match(/\/article\/n\/(.+)/);
    if (!m) continue;
    const title = (link.textContent || '').trim();
    let authors = '';
    li.querySelectorAll('span').forEach(s => {
      const t = (s.textContent || '').trim();
      if (t && !authors) authors = t;
    });
    items.push({ slug: m[1], title, authors, url: 'https://cyberleninka.ru' + href });
  }
  return items;
}"""


def _browser(p):
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
    ctx = b.new_context(user_agent=_UA)
    return b, ctx


def search(query: str, limit: int = 15) -> list[dict]:
    """Ищет статьи на CyberLeninka. Возвращает [{slug,title,authors,url,pdf_url}]."""
    from playwright.sync_api import sync_playwright
    query = (query or "").strip()
    if not query:
        return []
    with sync_playwright() as p:
        b, ctx = _browser(p)
        try:
            page = ctx.new_page()
            page.goto(f"{BASE}/search?q={quote(query)}",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            items = page.evaluate(_SEARCH_JS) or []
        finally:
            b.close()
    for it in items:
        it["pdf_url"] = it["url"].rstrip("/") + "/pdf"  # контракт CyberLeninka
    return items[:limit]


def download_pdf(article_url: str, timeout_ms: int = 45000) -> bytes:
    """Скачивает PDF статьи. article_url — /article/n/<slug> (или уже .../pdf).

    CyberLeninka отдаёт PDF инлайн (application/pdf без attachment), поэтому берём
    его через APIRequestContext браузера (общий UA), а не через навигацию страницы.
    """
    from playwright.sync_api import sync_playwright
    if not article_url:
        raise ValueError("article_url обязателен")
    pdf_url = article_url.rstrip("/")
    if not pdf_url.endswith("/pdf"):
        pdf_url += "/pdf"
    with sync_playwright() as p:
        req = p.request.new_context(extra_http_headers={"User-Agent": _UA})
        try:
            r = req.get(pdf_url, timeout=timeout_ms)
            ctype = (r.headers.get("content-type", "") or "").lower()
            if r.status == 200 and "pdf" in ctype:
                return r.body()
            raise RuntimeError(f"источник вернул HTTP {r.status} ({ctype or 'нет типа'})")
        finally:
            req.dispose()


def demo():
    """Сетевой self-check (best-effort): поиск не падает, поля на месте."""
    try:
        res = search("никель электроэкстракция", limit=3)
    except Exception as e:  # noqa: BLE001 — офлайн/недоступность источника не роняет
        print("demo: источник недоступен —", str(e)[:80]); return
    assert isinstance(res, list)
    for r in res:
        assert {"title", "url", "pdf_url"} <= r.keys(), r
    print(f"demo OK: {len(res)} результатов; пример: {res[0]['title'][:60] if res else '—'}")


if __name__ == "__main__":
    demo()
