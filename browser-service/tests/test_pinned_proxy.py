from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def test_lifespan(_: Any):
        yield

    original_lifespan = main.app.router.lifespan_context
    main.app.router.lifespan_context = test_lifespan
    monkeypatch.setattr(main, "_browser", None)
    monkeypatch.setattr(main, "_playwright", None)
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: ["93.184.216.34"])
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
        main.app.router.lifespan_context = original_lifespan


def set_browser(monkeypatch: pytest.MonkeyPatch, browser: FakeBrowser | None) -> None:
    monkeypatch.setattr(main, "_browser", browser)


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


def test_request_scoped_allowlist_is_removed_after_scope_exits() -> None:
    allowlist = main._PinnedProxyRequestAllowlist()

    assert allowlist.contains("example.com") is False
    with allowlist.allow("example.com"):
        assert allowlist.contains("example.com") is True
    assert allowlist.contains("example.com") is False
