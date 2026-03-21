from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


@dataclass
class FakePage:
    html: str = "<html></html>"
    goto_error: Exception | None = None
    wait_error: Exception | None = None
    content_error: Exception | None = None
    request_urls: list[str] | None = None
    on_goto: Any | None = None
    context: Any | None = None
    goto_calls: list[dict[str, Any]] = field(default_factory=list)
    wait_calls: list[dict[str, Any]] = field(default_factory=list)
    intercepted_routes: list[Any] = field(default_factory=list)
    closed: bool = False

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})
        if self.on_goto is not None:
            self.on_goto(url)
        if self.context is not None and self.context.route_handler is not None:
            for request_url in self.request_urls or [url]:
                route = FakeRoute(request=FakeRequest(url=request_url))
                self.intercepted_routes.append(route)
                await self.context.route_handler(route)
        if self.goto_error is not None:
            raise self.goto_error

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.wait_calls.append({"selector": selector, "timeout": timeout})
        if self.wait_error is not None:
            raise self.wait_error

    async def content(self) -> str:
        if self.content_error is not None:
            raise self.content_error
        return self.html

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeRoute:
    request: FakeRequest
    aborted: bool = False
    continued: bool = False
    continue_kwargs: dict[str, Any] | None = None

    async def abort(self) -> None:
        self.aborted = True

    async def continue_(self, **kwargs: Any) -> None:
        self.continued = True
        self.continue_kwargs = kwargs


@dataclass
class FakeContext:
    page: FakePage
    new_page_error: Exception | None = None
    route_handler: Any | None = None
    route_calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        self.page.context = self

    async def new_page(self) -> FakePage:
        if self.new_page_error is not None:
            raise self.new_page_error
        return self.page

    async def route(self, url: str, handler: Any) -> None:
        self.route_calls.append({"url": url})
        self.route_handler = handler

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeBrowser:
    context: FakeContext | None = None
    connected: bool = True
    new_context_error: Exception | None = None
    new_context_calls: list[dict[str, Any]] = field(default_factory=list)

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.new_context_calls.append(kwargs)
        if self.new_context_error is not None:
            raise self.new_context_error
        if self.context is None:
            raise RuntimeError("context is not configured")
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


def test_healthz_returns_200_when_browser_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    set_browser(monkeypatch, FakeBrowser(connected=True))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_healthz_returns_503_when_browser_not_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    set_browser(monkeypatch, FakeBrowser(connected=False))

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["detail"] == "browser not ready"


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("ftp://example.com/page", "invalid url scheme"),
        ("https:///missing-host", "invalid url: missing host"),
    ],
)
def test_render_rejects_invalid_urls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    detail: str,
) -> None:
    set_browser(monkeypatch, FakeBrowser())

    response = client.get("/render", params={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_render_returns_503_when_browser_is_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    set_browser(monkeypatch, None)

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 503
    assert response.json()["detail"] == "browser not ready"


def test_render_returns_400_when_request_time_dns_resolution_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage()
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    def fail_resolution(_host: str) -> list[str]:
        raise socket.gaierror("lookup failed")

    monkeypatch.setattr(main, "_resolve_host_ips", fail_resolution)

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 400
    assert "invalid target host" in response.json()["detail"]
    assert browser.new_context_calls != []
    assert "ignore_https_errors" not in browser.new_context_calls[0]
    assert context.route_calls == [{"url": "**/*"}]
    assert page.goto_calls == [{"url": "https://example.com/page", "wait_until": "domcontentloaded", "timeout": main._TIMEOUT_MS}]
    assert page.closed is True
    assert context.closed is True


def test_render_enforces_host_policy_during_intercepted_request(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    page = FakePage(on_goto=lambda _url: events.append("goto"))
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    def resolve_private_ip(_host: str) -> list[str]:
        events.append("resolve")
        return ["127.0.0.1"]

    monkeypatch.setattr(main, "_resolve_host_ips", resolve_private_ip)

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 400
    assert response.json()["detail"] == "target host is not allowed"
    assert events == ["goto", "resolve"]
    assert context.route_calls == [{"url": "**/*"}]
    assert page.intercepted_routes[0].aborted is True


def test_render_keeps_hostname_requests_on_the_original_url(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(html="<html><body>plain</body></html>")
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)
    to_thread_calls: list[tuple[Any, tuple[Any, ...]]] = []

    async def fake_to_thread(func: Any, *args: Any, **_kwargs: Any) -> Any:
        to_thread_calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(main, "asyncio", SimpleNamespace(to_thread=fake_to_thread), raising=False)
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: ["93.184.216.34"])

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 200
    assert to_thread_calls == [(main._resolve_host_ips, ("example.com",))]
    assert page.intercepted_routes[0].continue_kwargs == {}


def test_render_keeps_subresource_hostnames_without_rewriting_to_ip(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(
        html="<html><body>plain</body></html>",
        request_urls=["https://example.com/assets/app.js"],
    )
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: ["93.184.216.34"])

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 200
    assert page.intercepted_routes[0].continue_kwargs == {}


@pytest.mark.parametrize(
    ("url", "resolved_ips"),
    [
        ("http://127.0.0.1/admin", ["127.0.0.1"]),
        ("http://localhost/admin", ["127.0.0.1"]),
        ("http://10.0.0.8/private", ["10.0.0.8"]),
        ("http://169.254.169.254/latest/meta-data", ["169.254.169.254"]),
    ],
)
def test_render_rejects_loopback_and_private_targets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    resolved_ips: list[str],
) -> None:
    browser = FakeBrowser(context=FakeContext(page=FakePage()))
    set_browser(monkeypatch, browser)
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: resolved_ips)

    response = client.get("/render", params={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == "target host is not allowed"
    assert browser.new_context_calls == []


@pytest.mark.parametrize(
    ("url", "resolved_ips", "expected_status"),
    [
        ("http://host.docker.internal:8080/index.html", ["172.17.0.1"], 200),
        ("http://10.0.0.8/private", ["10.0.0.8"], 400),
    ],
)
def test_render_honors_test_only_host_allowlist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    resolved_ips: list[str],
    expected_status: int,
) -> None:
    page = FakePage(html="<html><body>allowed</body></html>")
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)
    monkeypatch.setenv("BROWSER_SERVICE_TEST_HOST_ALLOWLIST", "host.docker.internal")
    monkeypatch.setattr(main, "_resolve_host_ips", lambda _host: resolved_ips)

    response = client.get("/render", params={"url": url})

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json() == {"html": "<html><body>allowed</body></html>", "url": url}
        assert page.goto_calls == [{"url": url, "wait_until": "domcontentloaded", "timeout": main._TIMEOUT_MS}]
    else:
        assert response.json()["detail"] == "target host is not allowed"
        assert browser.new_context_calls == []


def test_render_returns_html_for_non_kwork_pages(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(html="<html><body>plain</body></html>")
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/page"})

    assert response.status_code == 200
    assert response.json() == {"html": "<html><body>plain</body></html>", "url": "https://example.com/page"}
    assert len(browser.new_context_calls) == 1
    assert browser.new_context_calls[0]["locale"] == "ru-RU"
    assert browser.new_context_calls[0]["timezone_id"] == "Europe/Moscow"
    assert page.goto_calls == [{"url": "https://example.com/page", "wait_until": "domcontentloaded", "timeout": main._TIMEOUT_MS}]
    assert page.wait_calls == []
    assert page.closed is True
    assert context.closed is True


def test_render_waits_for_kwork_selector(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(html="<html><div class='want-card'>ready</div></html>")
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://kwork.ru/projects"})

    assert response.status_code == 200
    assert response.json()["html"] == "<html><div class='want-card'>ready</div></html>"
    assert page.wait_calls == [{"selector": main._KWORK_READY_SELECTOR, "timeout": min(main._TIMEOUT_MS, 20_000)}]


def test_render_returns_current_dom_when_kwork_selector_times_out(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(
        html="<html><body>fallback DOM</body></html>",
        wait_error=main.PWTimeoutError("selector timeout"),
    )
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://www.kwork.ru/projects"})

    assert response.status_code == 200
    assert response.json()["html"] == "<html><body>fallback DOM</body></html>"
    assert len(page.wait_calls) == 1


def test_render_truncates_large_html(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_MAX_BODY", 16)
    page = FakePage(html="<html><body>0123456789abcdef</body></html>")
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/large"})

    assert response.status_code == 200
    assert response.json()["html"] == "<html><body>0123"


def test_render_maps_playwright_timeout_to_504_and_cleans_up(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(goto_error=main.PWTimeoutError("goto timeout"))
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/slow"})

    assert response.status_code == 504
    assert "render timeout" in response.json()["detail"]
    assert page.closed is True
    assert context.closed is True


def test_render_maps_generic_errors_to_500_and_cleans_up(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(content_error=RuntimeError("boom"))
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/fail"})

    assert response.status_code == 500
    assert response.json()["detail"] == "boom"
    assert page.closed is True
    assert context.closed is True


def test_render_maps_context_creation_errors_to_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    browser = FakeBrowser(new_context_error=RuntimeError("context boom"))
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/context-fail"})

    assert response.status_code == 500
    assert response.json()["detail"] == "context boom"


def test_render_maps_page_creation_errors_to_500_and_closes_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = FakeContext(page=FakePage(), new_page_error=RuntimeError("page boom"))
    browser = FakeBrowser(context=context)
    set_browser(monkeypatch, browser)

    response = client.get("/render", params={"url": "https://example.com/page-fail"})

    assert response.status_code == 500
    assert response.json()["detail"] == "page boom"
    assert context.closed is True
