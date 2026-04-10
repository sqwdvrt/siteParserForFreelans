from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


@dataclass
class FakePage:
    html: str = "<html></html>"
    goto_calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    context: Any | None = None

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        return None

    async def content(self) -> str:
        return self.html

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeContext:
    page: FakePage
    route_calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        self.page.context = self

    async def new_page(self) -> FakePage:
        return self.page

    async def route(self, url: str, handler: Any) -> None:
        self.route_calls.append({"url": url, "handler": handler})

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeBrowser:
    context: FakeContext
    connected: bool = True
    new_context_calls: list[dict[str, Any]] = field(default_factory=list)

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.new_context_calls.append(kwargs)
        return self.context


class _DummyLock:
    async def __aenter__(self) -> "_DummyLock":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeBrowserInstance:
    def __init__(self, fake_browser: FakeBrowser) -> None:
        self._fake_browser = fake_browser
        self.browser_id = 0
        self.lock = _DummyLock()

    @property
    def browser(self) -> FakeBrowser:
        return self._fake_browser


class FakeBrowserPool:
    def __init__(self, fake_browser: FakeBrowser) -> None:
        self._fake_browser = fake_browser

    async def acquire(self) -> FakeBrowserInstance:
        return FakeBrowserInstance(self._fake_browser)

    async def release(self, _instance: FakeBrowserInstance) -> None:
        pass

    async def mark_unhealthy(self, _instance: FakeBrowserInstance, *, wait: bool = False) -> None:
        del wait
        pass

    def get_stats(self) -> dict[str, Any]:
        return {
            "pool_size": 1,
            "healthy_count": 1,
            "total_active": 0,
            "instances": [{"browser_id": 0, "active": 0, "healthy": True, "load_ratio": 0.0}],
        }

    async def start_health_check_loop(self, interval: float = 30.0) -> None:
        pass

    async def shutdown(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def test_lifespan(_: Any):
        yield

    original_lifespan = main.app.router.lifespan_context
    main.app.router.lifespan_context = test_lifespan
    monkeypatch.setattr(main, "_browser_pool", None)
    monkeypatch.setattr(main, "_playwright", None)
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: ["93.184.216.34"])
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
        main.app.router.lifespan_context = original_lifespan


def set_browser(monkeypatch: pytest.MonkeyPatch, browser: FakeBrowser | None) -> None:
    if browser is None:
        monkeypatch.setattr(main, "_browser_pool", None)
    else:
        monkeypatch.setattr(main, "_browser_pool", FakeBrowserPool(browser))


def test_render_configures_browser_context_with_pinned_proxy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(html="<html><body>ok</body></html>")
    browser = FakeBrowser(context=FakeContext(page=page))
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 200
    context_kwargs = browser.new_context_calls[0]
    assert context_kwargs.get("proxy") == main._build_pinned_proxy_config(
        origin_host="example.com",
        resolved_ips=["93.184.216.34"],
    )


def test_pinned_proxy_pins_http_requests_to_resolved_ip() -> None:
    result = main._pin_request_target(
        request_url="http://example.com/assets/app.js?version=1",
        resolved_ips=["93.184.216.34"],
    )

    assert result["url"] == "http://93.184.216.34/assets/app.js?version=1"
    assert result["headers"]["host"] == "example.com"
    assert result["dial_ip"] == "93.184.216.34"


def test_pinned_proxy_keeps_https_url_but_pins_connect_target() -> None:
    result = main._pin_request_target(
        request_url="https://example.com/login",
        resolved_ips=["93.184.216.34"],
    )

    assert result["url"] == "https://example.com/login"
    assert result["connect_ip"] == "93.184.216.34"
    assert result["sni_hostname"] == "example.com"


def _make_mock_writer(*, drain_side_effect: BaseException | None = None) -> MagicMock:
    w = MagicMock()
    w.write = MagicMock()
    w.drain = AsyncMock(side_effect=drain_side_effect)
    w.close = MagicMock()
    w.wait_closed = AsyncMock()
    return w


def test_proxy_closes_upstream_writer_when_client_drain_fails_after_connect() -> None:
    """upstream_writer must be closed even when client drain() raises after CONNECT."""

    async def run() -> None:
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        )
        # drain raises once (on the 200 Connection Established response); _write_error's drain succeeds
        client_writer = _make_mock_writer(drain_side_effect=[ConnectionResetError("client reset"), None])

        upstream_writer = _make_mock_writer()
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_eof()

        proxy = main._PinnedProxy()
        with patch.object(
            main,
            "_open_pinned_connection",
            AsyncMock(return_value=(upstream_reader, upstream_writer)),
        ):
            await proxy._handle_client(client_reader, client_writer)

        assert upstream_writer.close.called, "upstream_writer.close() must be called when client drain fails"

    asyncio.run(run())


def test_proxy_closes_upstream_writer_when_upstream_drain_fails_for_http() -> None:
    """upstream_writer must be closed even when upstream drain() raises for HTTP proxy."""

    async def run() -> None:
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(
            b"GET http://example.com/path HTTP/1.1\r\nHost: example.com\r\n\r\n"
        )
        client_writer = _make_mock_writer()

        upstream_writer = _make_mock_writer(drain_side_effect=ConnectionResetError("upstream reset"))
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_eof()

        proxy = main._PinnedProxy()
        with patch("asyncio.open_connection", AsyncMock(return_value=(upstream_reader, upstream_writer))):
            with patch.object(
                main,
                "_resolve_and_validate_host",
                AsyncMock(return_value=["93.184.216.34"]),
            ):
                await proxy._handle_client(client_reader, client_writer)

        assert upstream_writer.close.called, "upstream_writer.close() must be called when upstream drain fails"

    asyncio.run(run())


def test_request_scoped_allowlist_is_removed_after_scope_exits() -> None:
    allowlist = main._PinnedProxyRequestAllowlist()

    assert allowlist.contains("example.com") is False
    with allowlist.allow("example.com"):
        assert allowlist.contains("example.com") is True
    assert allowlist.contains("example.com") is False
