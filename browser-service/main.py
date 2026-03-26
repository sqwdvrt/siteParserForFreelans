"""
Browser Render Service — рендерит JS-страницы через Playwright и возвращает HTML.

Эндпоинты:
  GET /healthz          — проверка живости
  GET /render?url=<url> — возвращает {"html": "...", "url": "..."} с полностью
                          отрендеренным HTML (после networkidle)
"""
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse, urlunparse

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
_TEST_HOST_ALLOWLIST_ENV = "BROWSER_SERVICE_TEST_HOST_ALLOWLIST"
_BLOCKED_HOSTS = {"localhost"}

_playwright: Playwright | None = None
_browser: Browser | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    try:
        yield
    finally:
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
        log.info("browser stopped")


app = FastAPI(title="Browser Render Service", version="1.0.0", lifespan=lifespan)


def _resolve_host_ips(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips: list[str] = []
    seen: set[str] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            ip = sockaddr[0]
        elif family == socket.AF_INET6:
            ip = sockaddr[0]
        else:
            continue
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _is_public_ip(raw_ip: str) -> bool:
    try:
        return ipaddress.ip_address(raw_ip).is_global
    except ValueError:
        return False


def _is_test_allowed_host(host: str) -> bool:
    allowed_hosts = {
        allowed_host.strip().lower()
        for allowed_host in os.getenv(_TEST_HOST_ALLOWLIST_ENV, "").split(",")
        if allowed_host.strip()
    }
    return host in allowed_hosts


async def _resolve_host_ips_async(host: str) -> list[str]:
    return await asyncio.to_thread(_resolve_host_ips, host)


def _validate_target_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="invalid url scheme")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid url: missing host")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail="invalid url: missing host")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="target host is not allowed")
    if _is_test_allowed_host(host):
        return parsed

    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        return parsed

    if not direct_ip.is_global:
        raise HTTPException(status_code=400, detail="target host is not allowed")
    return parsed


async def _resolve_request_target(request_url: str, host_cache: dict[str, list[str]]) -> tuple[str, str | None]:
    parsed = urlparse(request_url)
    if parsed.scheme not in ("http", "https"):
        return request_url, None

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail="invalid url: missing host")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="target host is not allowed")
    if _is_test_allowed_host(host):
        return request_url, None

    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        resolved_ips = host_cache.get(host)
        if resolved_ips is None:
            try:
                resolved_ips = await _resolve_host_ips_async(host)
            except socket.gaierror as exc:
                raise HTTPException(status_code=400, detail=f"invalid target host: {exc}") from exc
            host_cache[host] = resolved_ips
        if not resolved_ips or any(not _is_public_ip(ip) for ip in resolved_ips):
            raise HTTPException(status_code=400, detail="target host is not allowed")
        # HTTP only: rewrite hostname → resolved IP to prevent DNS rebinding (TOCTOU).
        # HTTPS is protected by TLS certificate validation — no rewrite needed.
        if parsed.scheme == "http":
            first_ip = resolved_ips[0]
            ip_host = f"[{first_ip}]" if ":" in first_ip else first_ip
            port_part = f":{parsed.port}" if parsed.port else ""
            new_netloc = f"{ip_host}{port_part}"
            rewritten_url = urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
            return rewritten_url, host
        return request_url, None

    if not direct_ip.is_global:
        raise HTTPException(status_code=400, detail="target host is not allowed")
    return request_url, None


class _RequestGuard:
    def __init__(self) -> None:
        self.error: HTTPException | None = None
        self._host_cache: dict[str, list[str]] = {}

    async def handle(self, route) -> None:
        try:
            rewritten_url, original_host = await _resolve_request_target(route.request.url, self._host_cache)
        except HTTPException as exc:
            self.error = exc
            await route.abort()
            return

        if rewritten_url != route.request.url:
            headers = dict(getattr(route.request, "headers", {}) or {})
            if original_host is not None:
                headers["host"] = original_host
            await route.continue_(url=rewritten_url, headers=headers)
            return

        await route.continue_()

    def raise_if_blocked(self) -> None:
        if self.error is not None:
            raise self.error


@app.get("/healthz")
async def healthz() -> dict:
    if _browser is None or not _browser.is_connected():
        raise HTTPException(status_code=503, detail="browser not ready")
    return {"ok": True}


@app.get("/render")
async def render(url: str = Query(..., description="URL страницы для рендера")) -> JSONResponse:
    if _browser is None:
        raise HTTPException(status_code=503, detail="browser not ready")

    parsed = _validate_target_url(url)
    request_guard = _RequestGuard()

    context = None
    page = None
    try:
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
        await context.route("**/*", request_guard.handle)
        page = await context.new_page()
        log.info("render start url=%s", url)

        # Шаг 1: грузим страницу до DOMContentLoaded — это быстро.
        # networkidle на kwork.ru никогда не наступает (фоновые поллинги).
        await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
        request_guard.raise_if_blocked()

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
        request_guard.raise_if_blocked()

        html = await page.content()
        request_guard.raise_if_blocked()
        if len(html) > _MAX_BODY:
            html = html[:_MAX_BODY]
        log.info("render ok url=%s html_len=%d", url, len(html))
        return JSONResponse({"html": html, "url": url})
    except HTTPException:
        raise
    except PWTimeoutError as exc:
        if request_guard.error is not None:
            raise request_guard.error from exc
        log.warning("render timeout url=%s err=%s", url, exc)
        raise HTTPException(status_code=504, detail=f"render timeout: {exc}") from exc
    except Exception as exc:
        if request_guard.error is not None:
            raise request_guard.error from exc
        log.warning("render failed url=%s err=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
