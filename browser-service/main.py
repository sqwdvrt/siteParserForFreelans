"""
Browser Render Service — рендерит JS-страницы через Playwright и возвращает HTML.

Эндпоинты:
  GET /healthz          — проверка живости
  GET /render?url=<url> — возвращает {"html": "...", "url": "..."} с полностью
                          отрендеренным HTML (после networkidle)
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright, Browser, Playwright, TimeoutError as PWTimeoutError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger(__name__)

_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "60000"))
# CSS-селектор, появление которого означает что страница готова.
# Kwork: карточки проектов в .want-card или .wants-list__item
_KWORK_READY_SELECTOR = os.getenv("KWORK_READY_SELECTOR", ".want-card,.wants-list__item,article[data-id]")
_MAX_BODY = 10 * 1024 * 1024  # 10 MB

app = FastAPI(title="Browser Render Service", version="1.0.0")

_playwright: Playwright | None = None
_browser: Browser | None = None
_lock = asyncio.Lock()


@app.on_event("startup")
async def _startup() -> None:
    global _playwright, _browser
    log.info("launching Playwright Chromium")
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
        ],
    )
    log.info("browser ready")


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    log.info("browser stopped")


@app.get("/healthz")
async def healthz() -> dict:
    if _browser is None or not _browser.is_connected():
        raise HTTPException(status_code=503, detail="browser not ready")
    return {"ok": True}


@app.get("/render")
async def render(url: str = Query(..., description="URL страницы для рендера")) -> JSONResponse:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="invalid url scheme")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid url: missing host")

    if _browser is None:
        raise HTTPException(status_code=503, detail="browser not ready")

    # Каждый запрос получает изолированный контекст браузера (инкогнито-подобный),
    # чтобы куки/сессии не перетекали между запросами.
    context = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )
    page = await context.new_page()
    try:
        log.info("render start url=%s", url)

        # Шаг 1: грузим страницу до DOMContentLoaded — это быстро.
        # networkidle на kwork.ru никогда не наступает (фоновые поллинги).
        await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)

        # Шаг 2: ждём появления контента, загружаемого через JS.
        # Для kwork.ru — карточки проектов; для других сайтов пропускаем.
        if "kwork.ru" in parsed.netloc:
            try:
                await page.wait_for_selector(
                    _KWORK_READY_SELECTOR,
                    timeout=min(_TIMEOUT_MS, 20_000),
                )
                log.info("render kwork selector found url=%s", url)
            except PWTimeoutError:
                # Селектор не появился — возможно, другая страница или бот-защита.
                # Берём что есть.
                log.warning("render kwork selector timeout url=%s — returning current DOM", url)

        html = await page.content()
        if len(html) > _MAX_BODY:
            html = html[:_MAX_BODY]
        log.info("render ok url=%s html_len=%d", url, len(html))
        return JSONResponse({"html": html, "url": url})
    except PWTimeoutError as exc:
        log.warning("render timeout url=%s err=%s", url, exc)
        raise HTTPException(status_code=504, detail=f"render timeout: {exc}") from exc
    except Exception as exc:
        log.warning("render failed url=%s err=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await page.close()
        await context.close()
