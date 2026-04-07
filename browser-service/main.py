"""
Browser Render Service — рендерит JS-страницы через Playwright и возвращает HTML.

Эндпоинты:
  GET /healthz          — проверка живости
  GET /metrics          — Prometheus метрики
  GET /render?url=<url> — возвращает {"html": "...", "url": "..."} с полностью
                          отрендеренным HTML (после networkidle)
"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import ipaddress
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from playwright.async_api import async_playwright, Browser, Playwright, TimeoutError as PWTimeoutError
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger(__name__)

# Prometheus метрики
RENDER_DURATION = Histogram(
    "browser_service_render_duration_seconds",
    "Duration of browser render operations",
    ["status"],  # success, timeout, error
    buckets=[0.5, 1, 2, 5, 10, 15, 20, 30, 60],
)

RENDER_REQUESTS = Counter(
    "browser_service_render_requests_total",
    "Total number of render requests",
    ["status"],  # success, timeout, error
)

ACTIVE_CONNECTIONS = Gauge(
    "browser_service_active_connections",
    "Number of active browser connections"
)

RENDER_ERRORS = Counter(
    "browser_service_render_errors_total",
    "Total number of render errors",
    ["error_type"],  # timeout, validation, internal
)

BROWSER_UP = Gauge(
    "browser_service_up",
    "Whether the browser service is up and ready"
)

PAGE_RENDERED = Counter(
    "browser_service_pages_rendered_total",
    "Total number of pages rendered by domain",
    ["domain"]
)

_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "60000"))
# CSS-селектор, появление которого означает что страница готова.
# Kwork: карточки проектов в .want-card или .wants-list__item
_KWORK_READY_SELECTOR = os.getenv("KWORK_READY_SELECTOR", ".want-card,.wants-list__item,article[data-id]")
_MAX_BODY = 10 * 1024 * 1024  # 10 MB
_TEST_HOST_ALLOWLIST_ENV = "BROWSER_SERVICE_TEST_HOST_ALLOWLIST"
_BLOCKED_HOSTS = {"localhost"}

_playwright: Playwright | None = None
_browser: Browser | None = None
_proxy_server: "_PinnedProxy | None" = None


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


class _PinnedProxyRequestAllowlist:
    def __init__(self) -> None:
        self._refcounts: dict[str, int] = {}

    @contextmanager
    def allow(self, host: str):
        normalized = host.strip().lower()
        if normalized:
            self._refcounts[normalized] = self._refcounts.get(normalized, 0) + 1
        try:
            yield
        finally:
            if not normalized:
                return
            remaining = self._refcounts.get(normalized, 0) - 1
            if remaining > 0:
                self._refcounts[normalized] = remaining
            else:
                self._refcounts.pop(normalized, None)

    def contains(self, host: str) -> bool:
        return self._refcounts.get(host.strip().lower(), 0) > 0


class _PinnedHostRegistry:
    def __init__(self) -> None:
        self._allowlist = _PinnedProxyRequestAllowlist()
        self._pins: dict[str, list[str]] = {}

    @contextmanager
    def pin(self, host: str, resolved_ips: list[str]):
        normalized = host.strip().lower()
        with self._allowlist.allow(normalized):
            if normalized:
                self._pins[normalized] = list(resolved_ips)
            try:
                yield
            finally:
                self._pins.pop(normalized, None)

    def get(self, host: str) -> list[str] | None:
        return self._pins.get(host.strip().lower())

    def contains(self, host: str) -> bool:
        return self._allowlist.contains(host)


_pinned_host_registry = _PinnedHostRegistry()


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


async def _resolve_and_validate_host(host: str) -> list[str]:
    normalized = (host or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="invalid url: missing host")
    if normalized in _BLOCKED_HOSTS or normalized.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="target host is not allowed")
    if _is_test_allowed_host(normalized):
        resolved_ips = await _resolve_host_ips_async(normalized)
        return resolved_ips or [normalized]

    try:
        direct_ip = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            resolved_ips = await _resolve_host_ips_async(normalized)
        except socket.gaierror as exc:
            raise HTTPException(status_code=400, detail=f"invalid target host: {exc}") from exc
        if not resolved_ips or any(not _is_public_ip(ip) for ip in resolved_ips):
            raise HTTPException(status_code=400, detail="target host is not allowed")
        return resolved_ips

    if not direct_ip.is_global:
        raise HTTPException(status_code=400, detail="target host is not allowed")
    return [normalized]


def _build_pinned_proxy_config(origin_host: str, resolved_ips: list[str]) -> dict[str, str]:
    del origin_host, resolved_ips
    server_url = _proxy_server.server_url if _proxy_server is not None else "http://127.0.0.1:0"
    return {"server": server_url}


def _pin_request_target(request_url: str, resolved_ips: list[str]) -> dict[str, Any]:
    parsed = urlparse(request_url)
    if parsed.scheme not in ("http", "https"):
        return {"url": request_url}

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail="invalid url: missing host")
    if not resolved_ips:
        raise HTTPException(status_code=400, detail="target host is not allowed")

    first_ip = resolved_ips[0]
    ip_host = f"[{first_ip}]" if ":" in first_ip else first_ip
    port_part = f":{parsed.port}" if parsed.port else ""

    if parsed.scheme == "http":
        rewritten_url = urlunparse(
            (parsed.scheme, f"{ip_host}{port_part}", parsed.path, parsed.params, parsed.query, parsed.fragment)
        )
        return {
            "url": rewritten_url,
            "headers": {"host": host},
            "dial_ip": first_ip,
        }

    return {
        "url": request_url,
        "connect_ip": first_ip,
        "sni_hostname": host,
    }


def _format_host_for_url(host: str, port: int | None = None) -> str:
    bracketed = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{bracketed}:{port}" if port is not None else bracketed


def _parse_host_port(value: str, default_port: int) -> tuple[str, int]:
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            raise ValueError("invalid ipv6 host")
        host = value[1:end]
        remainder = value[end + 1 :]
        if remainder.startswith(":"):
            return host, int(remainder[1:])
        return host, default_port
    if value.count(":") == 1:
        host, raw_port = value.rsplit(":", 1)
        if raw_port.isdigit():
            return host, int(raw_port)
    return value, default_port


async def _open_pinned_connection(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    normalized = host.strip().lower()
    resolved_ips = _pinned_host_registry.get(normalized)
    if resolved_ips is None:
        resolved_ips = await _resolve_and_validate_host(normalized)
    pinned = _pin_request_target(f"https://{_format_host_for_url(normalized, port)}", resolved_ips)
    return await asyncio.open_connection(pinned["connect_ip"], port)


class _PinnedProxy:
    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None

    @property
    def server_url(self) -> str:
        if self._server is None or not self._server.sockets:
            return "http://127.0.0.1:0"
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        log.info("pinned proxy ready url=%s", self.server_url)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            request_head = head.decode("latin1")
            lines = request_head.split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            headers: list[tuple[str, str]] = []
            for line in lines[1:]:
                if not line:
                    continue
                name, value = line.split(":", 1)
                headers.append((name, value.lstrip()))

            if method.upper() == "CONNECT":
                host, port = _parse_host_port(target, 443)
                upstream_reader, upstream_writer = await _open_pinned_connection(host, port)
                writer.write(f"{version} 200 Connection Established\r\n\r\n".encode("latin1"))
                await writer.drain()
                _uw, upstream_writer = upstream_writer, None  # transfer ownership to _proxy_bidirectional
                await self._proxy_bidirectional(reader, writer, upstream_reader, _uw)
                return

            parsed = urlparse(target)
            if parsed.scheme != "http" or not parsed.hostname:
                raise HTTPException(status_code=400, detail="proxy only supports absolute-form http requests")

            resolved_ips = await _resolve_and_validate_host(parsed.hostname)
            pinned = _pin_request_target(target, resolved_ips)
            upstream_reader, upstream_writer = await asyncio.open_connection(
                pinned["dial_ip"],
                parsed.port or 80,
            )

            origin_form = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, parsed.fragment))
            forwarded_headers: list[tuple[str, str]] = []
            saw_host = False
            for name, value in headers:
                lower_name = name.lower()
                if lower_name in {"proxy-authorization", "proxy-connection"}:
                    continue
                if lower_name == "host":
                    value = pinned["headers"]["host"]
                    saw_host = True
                forwarded_headers.append((name, value))
            if not saw_host:
                forwarded_headers.append(("Host", pinned["headers"]["host"]))

            forwarded_head = "".join(
                [f"{method} {origin_form} {version}\r\n"]
                + [f"{name}: {value}\r\n" for name, value in forwarded_headers]
                + ["\r\n"]
            ).encode("latin1")
            upstream_writer.write(forwarded_head)
            await upstream_writer.drain()
            _uw, upstream_writer = upstream_writer, None  # transfer ownership to _proxy_bidirectional
            await self._proxy_bidirectional(reader, writer, upstream_reader, _uw)
        except HTTPException as exc:
            await self._write_error(writer, 403 if exc.status_code == 400 else 502, exc.detail)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, ValueError) as exc:
            await self._write_error(writer, 502, str(exc))
        except Exception as exc:
            log.warning("pinned proxy unexpected failure: %s", exc)
            await self._write_error(writer, 500, "proxy failure")
        finally:
            if upstream_writer is not None:
                try:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                except Exception:
                    pass
            writer.close()
            await writer.wait_closed()

    async def _proxy_bidirectional(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def _pipe(source: asyncio.StreamReader, dest: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await source.read(64 * 1024)
                    if not chunk:
                        break
                    dest.write(chunk)
                    await dest.drain()
            finally:
                try:
                    dest.close()
                    await dest.wait_closed()
                except Exception:
                    pass

        await asyncio.gather(
            _pipe(client_reader, upstream_writer),
            _pipe(upstream_reader, client_writer),
        )

    async def _write_error(self, writer: asyncio.StreamWriter, status_code: int, detail: str) -> None:
        payload = f"{status_code} {detail}".encode("utf-8", errors="replace")
        writer.write(
            b"HTTP/1.1 "
            + str(status_code).encode("ascii")
            + b" Proxy Error\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
            + str(len(payload)).encode("ascii")
            + b"\r\nConnection: close\r\n\r\n"
            + payload
        )
        await writer.drain()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _playwright, _browser, _proxy_server
    log.info("launching Playwright Chromium")
    _playwright = await async_playwright().start()
    _proxy_server = _PinnedProxy()
    await _proxy_server.start()
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
    BROWSER_UP.set(1)
    try:
        yield
    finally:
        BROWSER_UP.set(0)
        if _browser:
            await _browser.close()
        if _proxy_server:
            await _proxy_server.stop()
        if _playwright:
            await _playwright.stop()
        log.info("browser stopped")


app = FastAPI(title="Browser Render Service", version="1.0.0", lifespan=lifespan)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
    resolved_origin_ips = await _resolve_and_validate_host(parsed.hostname or "")

    context = None
    page = None
    start_time = time.time()
    ACTIVE_CONNECTIONS.inc()
    
    try:
        with _pinned_host_registry.pin(parsed.hostname or "", resolved_origin_ips):
            context = await _browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                service_workers="block",
                proxy=_build_pinned_proxy_config(parsed.hostname or "", resolved_origin_ips),
            )
            page = await context.new_page()
            log.info("render start url=%s", url)

            await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)

            if "kwork.ru" in parsed.netloc:
                try:
                    await page.wait_for_selector(
                        _KWORK_READY_SELECTOR,
                        timeout=min(_TIMEOUT_MS, 20_000),
                    )
                    log.info("render kwork selector found url=%s", url)
                except PWTimeoutError:
                    log.warning("render kwork selector timeout url=%s — returning current DOM", url)

            html = await page.content()
            if len(html) > _MAX_BODY:
                html = html[:_MAX_BODY]
            
            duration = time.time() - start_time
            RENDER_DURATION.labels(status="success").observe(duration)
            RENDER_REQUESTS.labels(status="success").inc()
            PAGE_RENDERED.labels(domain=parsed.hostname or "unknown").inc()
            
            log.info("render ok url=%s html_len=%d duration=%.2fs", url, len(html), duration)
            return JSONResponse({"html": html, "url": url})
    except HTTPException:
        raise
    except PWTimeoutError as exc:
        duration = time.time() - start_time
        RENDER_DURATION.labels(status="timeout").observe(duration)
        RENDER_REQUESTS.labels(status="timeout").inc()
        RENDER_ERRORS.labels(error_type="timeout").inc()
        log.warning("render timeout url=%s err=%s", url, exc)
        raise HTTPException(status_code=504, detail=f"render timeout: {exc}") from exc
    except Exception as exc:
        duration = time.time() - start_time
        RENDER_DURATION.labels(status="error").observe(duration)
        RENDER_REQUESTS.labels(status="error").inc()
        RENDER_ERRORS.labels(error_type="internal").inc()
        log.warning("render failed url=%s err=%s", url, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        ACTIVE_CONNECTIONS.dec()
        if page is not None:
            await page.close()
        if context is not None:
            await context.close()
