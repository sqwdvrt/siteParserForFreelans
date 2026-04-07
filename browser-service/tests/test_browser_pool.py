"""Tests for Browser Pool Manager."""
import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_pool import BrowserPool, BrowserInstance, PoolExhausted


@pytest.fixture
def mock_playwright():
    """Create a mock Playwright instance."""
    playwright = MagicMock()
    browser = AsyncMock()
    browser.is_connected.return_value = True
    browser.close = AsyncMock()

    context = AsyncMock()
    page = AsyncMock()
    context.new_page.return_value = page
    page.goto = AsyncMock()
    page.close = AsyncMock()

    playwright.chromium = AsyncMock()
    playwright.chromium.launch.return_value = browser
    playwright.chromium.new_context = AsyncMock(return_value=context)

    return playwright, browser, context, page


@pytest.fixture
def mock_browser_context():
    """Create mock browser and context."""
    browser = AsyncMock()
    browser.is_connected.return_value = True
    browser.close = AsyncMock()

    context = AsyncMock()
    context.new_page = AsyncMock()
    page = AsyncMock()
    page.goto = AsyncMock()
    page.close = AsyncMock()
    context.new_page.return_value = page

    return browser, context


class TestBrowserInstance:
    def test_init(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)

        assert instance.browser == browser
        assert instance.context == context
        assert instance.browser_id == 0
        assert instance.active == 0
        assert instance.max_concurrent == 5
        assert instance.healthy is True
        assert instance.created_at > 0

    def test_load_ratio_idle(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        assert instance.load_ratio == 0.0

    def test_load_ratio_full(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        instance.active = 5
        assert instance.load_ratio == 1.0

    def test_load_ratio_partial(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        instance.active = 3
        assert instance.load_ratio == 0.6

    def test_is_expired_false(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        assert instance.is_expired(3600) is False

    def test_is_expired_true(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        instance.created_at = time.time() - 7200  # 2 hours ago
        assert instance.is_expired(3600) is True


class TestBrowserPool:
    @pytest.mark.asyncio
    async def test_pool_initialization(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=3, max_concurrent=5)

        await pool.initialize()

        assert len(pool._instances) == 3
        assert pw.chromium.launch.call_count == 3

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_least_loaded(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=2, max_concurrent=5)

        await pool.initialize()

        # Simulate browser-0 has 3 active, browser-1 has 0
        pool._instances[0].active = 3
        pool._instances[1].active = 0

        acquired = await pool.acquire()
        assert acquired.browser_id == 1  # least-loaded
        assert acquired.active == 1

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_exhausted(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=2, max_concurrent=2)

        await pool.initialize()

        # Fill all browsers
        pool._instances[0].active = 2
        pool._instances[1].active = 2

        with pytest.raises(PoolExhausted):
            await pool.acquire()

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_acquire_skips_unhealthy(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=2, max_concurrent=5)

        await pool.initialize()

        pool._instances[0].healthy = False
        pool._instances[1].active = 0

        acquired = await pool.acquire()
        assert acquired.browser_id == 1

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_release_decrements_active(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=1, max_concurrent=5)

        await pool.initialize()

        instance = await pool.acquire()
        assert instance.active == 1

        await pool.release(instance)
        assert instance.active == 0

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_mark_unhealthy(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=1, max_concurrent=5)

        await pool.initialize()

        instance = pool._instances[0]
        assert instance.healthy is True

        await pool.mark_unhealthy(instance)
        assert instance.healthy is False

        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_detects_disconnected(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=1, max_concurrent=5)

        await pool.initialize()

        # Simulate disconnect
        pool._instances[0].browser.is_connected.return_value = False

        await pool.health_check()
        assert pool._instances[0].healthy is False

        await pool.shutdown()

    def test_get_stats(self, mock_playwright):
        pw, browser, context, page = mock_playwright
        pool = BrowserPool(pw, pool_size=2, max_concurrent=5)

        # Don't need to initialize for stats test
        pool._instances = [
            BrowserInstance(browser, context, 0, 5),
            BrowserInstance(browser, context, 1, 5),
        ]
        pool._instances[0].active = 2
        pool._instances[1].active = 3

        stats = pool.get_stats()

        assert stats["pool_size"] == 2
        assert stats["healthy_count"] == 2
        assert stats["total_active"] == 5
        assert len(stats["instances"]) == 2
        assert stats["instances"][0]["active"] == 2
        assert stats["instances"][1]["load_ratio"] == 0.6

    def test_ttl_expiration(self, mock_browser_context):
        browser, context = mock_browser_context
        instance = BrowserInstance(browser, context, browser_id=0, max_concurrent=5)
        instance.created_at = time.time() - 14400  # 4 hours ago

        assert instance.is_expired(14400) is True
        assert instance.is_expired(18000) is False  # 5 hours TTL
