"""
Browser Pool Manager — manages multiple Playwright browser instances.

Provides a pool of BrowserInstance objects, each wrapping a Playwright Browser
with a shared BrowserContext. Supports least-loaded acquisition, health checks,
automatic recycling (TTL-based), and background recreation of unhealthy browsers.
"""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from playwright.async_api import Browser, BrowserContext, Playwright

logger = logging.getLogger(__name__)


DEFAULT_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
]


class PoolExhausted(Exception):
    """Raised when the pool cannot serve requests."""
    pass


class BrowserInstance:
    """Wraps a single Playwright Browser with a shared BrowserContext."""

    def __init__(
        self,
        browser: Browser,
        context: BrowserContext,
        browser_id: int,
        max_concurrent: int = 5,
    ) -> None:
        self.browser: Browser = browser
        self.context: BrowserContext = context
        self.browser_id: int = browser_id
        self.active: int = 0
        self.max_concurrent: int = max_concurrent
        self.healthy: bool = True
        self.created_at: float = time.time()
        self.lock: asyncio.Lock = asyncio.Lock()

    @property
    def load_ratio(self) -> float:
        """0.0 = idle, 1.0 = full."""
        if self.max_concurrent <= 0:
            return 1.0
        return self.active / self.max_concurrent

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if browser should be recycled (memory leak prevention)."""
        return (time.time() - self.created_at) > ttl_seconds

    def __repr__(self) -> str:
        return (
            f"BrowserInstance(id={self.browser_id}, active={self.active}/"
            f"{self.max_concurrent}, healthy={self.healthy})"
        )


class BrowserPool:
    """Manages a pool of Playwright browser instances."""

    def __init__(
        self,
        playwright_instance: Playwright,
        pool_size: int = 2,
        max_concurrent: int = 5,
        browser_ttl_seconds: int = 14400,
        launch_args: list[str] | None = None,
        playwright_factory: Callable[[], Awaitable[Playwright]] | None = None,
        playwright_shutdown: Callable[[Playwright], Awaitable[None]] | None = None,
    ) -> None:
        self._playwright: Playwright = playwright_instance
        self._pool_size: int = pool_size
        self._max_concurrent: int = max_concurrent
        self._browser_ttl: int = browser_ttl_seconds
        self._launch_args: list[str] = launch_args or list(DEFAULT_ARGS)
        self._playwright_factory = playwright_factory
        self._playwright_shutdown = playwright_shutdown
        self._instances: list[BrowserInstance] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._recovery_lock: asyncio.Lock = asyncio.Lock()
        self._health_check_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Create all browser instances."""
        for i in range(self._pool_size):
            await self._create_browser(i)
        logger.info("browser pool initialized with %d instances", self._pool_size)

    async def shutdown(self) -> None:
        """Close all browsers and cancel the health check task."""
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        for instance in self._instances:
            try:
                await instance.browser.close()
            except Exception:
                logger.exception("failed to close browser %d during shutdown", instance.browser_id)
        self._instances.clear()
        logger.info("browser pool shut down")

    async def acquire(self) -> BrowserInstance:
        """
        Get the least-loaded healthy browser.

        Raises:
            PoolExhausted: If all browsers are busy or unhealthy.
        """
        async with self._lock:
            healthy = [
                b for b in self._instances
                if b.healthy and b.active < b.max_concurrent
            ]
            if not healthy:
                raise PoolExhausted("all browsers busy or unhealthy")
            # least-loaded
            browser = min(healthy, key=lambda b: b.load_ratio)
            browser.active += 1
            return browser

    async def release(self, browser: BrowserInstance) -> None:
        """Release browser back to pool."""
        async with self._lock:
            browser.active = max(0, browser.active - 1)

    async def mark_unhealthy(self, browser: BrowserInstance, *, wait: bool = False) -> None:
        """Mark browser unhealthy and recreate it.

        Render retries can wait for recreation to avoid immediately retrying
        into an empty or recovering pool after Chromium transport failures.
        """
        async with self._lock:
            browser.healthy = False
            browser.active = max(0, browser.active - 1)
        if wait:
            await self._recreate_browser(browser.browser_id)
        else:
            asyncio.create_task(
                self._recreate_browser(browser.browser_id),
                name=f"recreate-browser-{browser.browser_id}",
            )

    async def health_check(self) -> None:
        """Check all browsers, recreate unhealthy ones."""
        for instance in list(self._instances):
            if not instance.healthy:
                continue
            if not instance.browser.is_connected():
                logger.warning("browser %d disconnected, marking unhealthy", instance.browser_id)
                await self.mark_unhealthy(instance)
                continue
            # Test with blank page
            try:
                async with instance.lock:
                    page = await instance.context.new_page()
                    await page.goto("about:blank", timeout=5000)
                    await page.close()
            except Exception as exc:
                logger.warning("browser %d health check failed: %s", instance.browser_id, exc)
                await self.mark_unhealthy(instance)

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        return {
            "pool_size": len(self._instances),
            "healthy_count": sum(1 for b in self._instances if b.healthy),
            "total_active": sum(b.active for b in self._instances),
            "instances": [
                {
                    "browser_id": b.browser_id,
                    "active": b.active,
                    "max_concurrent": b.max_concurrent,
                    "load_ratio": round(b.load_ratio, 2),
                    "healthy": b.healthy,
                }
                for b in self._instances
            ],
        }

    async def start_health_check_loop(self, interval: float = 30.0) -> None:
        """Start a periodic health check loop."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.health_check()
                except Exception:
                    logger.exception("health check loop error")

                # Also recycle expired browsers
                for instance in list(self._instances):
                    if instance.healthy and instance.is_expired(self._browser_ttl):
                        logger.info(
                            "browser %d expired (TTL=%ds), recycling",
                            instance.browser_id,
                            self._browser_ttl,
                        )
                        await self.mark_unhealthy(instance)

        self._health_check_task = asyncio.create_task(_loop(), name="browser-health-check-loop")

    async def _create_browser(self, browser_id: int) -> None:
        """Create a new browser instance."""
        failed_playwright = self._playwright
        try:
            instance = await self._launch_browser_instance(browser_id)
        except Exception as exc:
            if not await self._recover_transport_if_needed(failed_playwright, exc):
                raise
            return
        async with self._lock:
            # Replace old instance if it exists
            old_indices = [i for i, b in enumerate(self._instances) if b.browser_id == browser_id]
            if old_indices:
                idx = old_indices[0]
                try:
                    await self._instances[idx].browser.close()
                except Exception:
                    logger.exception("failed to close old browser %d", browser_id)
                self._instances[idx] = instance
            else:
                self._instances.append(instance)
        logger.info("browser %d created", browser_id)

    async def _recreate_browser(self, browser_id: int) -> None:
        """Recreate a specific browser instance with retry on failure."""
        try:
            await self._create_browser(browser_id)
            logger.info("browser %d recreated successfully", browser_id)
        except Exception as e:
            logger.error("failed to recreate browser %d: %s", browser_id, e)
            # Schedule retry after 10 seconds
            loop = asyncio.get_event_loop()
            loop.call_later(
                10,
                lambda: asyncio.create_task(
                    self._recreate_browser(browser_id),
                    name=f"recreate-browser-{browser_id}-retry",
                ),
            )

    async def _launch_browser_instance(self, browser_id: int) -> BrowserInstance:
        browser = await self._playwright.chromium.launch(
            headless=True,
            args=self._launch_args,
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            service_workers="block",
        )
        return BrowserInstance(browser, context, browser_id, self._max_concurrent)

    async def _recover_transport_if_needed(self, failed_playwright: Playwright, exc: Exception) -> bool:
        if not self._is_transport_closed_error(exc):
            return False
        if self._playwright_factory is None:
            return False
        if self._playwright is not failed_playwright:
            return True

        async with self._recovery_lock:
            if self._playwright is not failed_playwright:
                return True

            logger.warning("playwright transport closed, rebuilding browser pool: %s", exc)
            old_instances = list(self._instances)
            async with self._lock:
                self._instances = []

            for instance in old_instances:
                try:
                    await instance.browser.close()
                except Exception:
                    logger.exception(
                        "failed to close browser %d during transport recovery",
                        instance.browser_id,
                    )

            if self._playwright_shutdown is not None:
                try:
                    await self._playwright_shutdown(failed_playwright)
                except Exception:
                    logger.exception("failed to stop stale playwright during recovery")

            self._playwright = await self._playwright_factory()

            new_instances: list[BrowserInstance] = []
            for browser_id in range(self._pool_size):
                new_instances.append(await self._launch_browser_instance(browser_id))

            async with self._lock:
                self._instances = new_instances

            logger.info("browser pool recovered after playwright transport restart")
            return True

    @staticmethod
    def _is_transport_closed_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "handler is closed" in message
            or "transport closed" in message
            or "target page, context or browser has been closed" in message
            or "connection closed while reading from the driver" in message
        )
