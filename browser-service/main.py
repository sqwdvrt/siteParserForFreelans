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
from playwright.async_api import async_playwright, Browser, Playwright

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger(__name__)

_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))
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
        await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
        html = await page.content()
        if len(html) > _MAX_BODY:
            html = html[:_MAX_BODY]
        log.info("render ok url=%s html_len=%d", url, len(html))
        return JSONResponse({"html": html, "url": url})
    except Exception as exc:
        log.warning("render failed url=%s err=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await page.close()
        await context.close()
