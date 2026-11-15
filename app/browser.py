import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.config import settings

logger = logging.getLogger(__name__)

LAUNCH_ARGS = [
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
]

READY_EXPRESSION = "document.documentElement.dataset.ready === '1'"

# document.fonts.check() alone answers true for a family that has no @font-face at all,
# because the browser is happy to substitute. Pairing it with a loaded FontFace of the same
# family is what turns this into a real check.
FONT_PROBE = """
faces => faces.map(face => {
  const match = face.match(/"([^"]+)"/);
  if (!match) return false;
  const family = match[1];
  const loaded = Array.from(document.fonts).some(
    entry => entry.family.replace(/^["']|["']$/g, '') === family && entry.status === 'loaded'
  );
  return loaded && document.fonts.check(face);
})
"""

CONTEXT_CLOSE_TIMEOUT = 5.0

Output = Callable[[Page, int, int], Awaitable[bytes]]


async def _png(page: Page, width: int, height: int) -> bytes:
    return await page.screenshot(type="png", full_page=False, animations="disabled")


async def _pdf(page: Page, width: int, height: int) -> bytes:
    # Screen media and printed backgrounds, or the PDF loses every background colour.
    await page.emulate_media(media="screen")
    return await page.pdf(
        width=f"{width}px",
        height=f"{height}px",
        print_background=True,
        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        page_ranges="1",
    )


class RenderTimeout(Exception):
    pass


class RenderBusy(Exception):
    pass


class RenderUnavailable(Exception):
    pass


class FontNotLoaded(Exception):
    pass


class BrowserPool:
    def __init__(self, concurrency: int, render_timeout_ms: int, queue_timeout_ms: int):
        self._semaphore = asyncio.Semaphore(concurrency)
        self._render_timeout_ms = render_timeout_ms
        self._queue_timeout_ms = queue_timeout_ms
        self._playwright = None
        self._browser: Browser | None = None
        self._relaunch_lock = asyncio.Lock()
        self._closing = False

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        try:
            await self._launch()
        except BaseException:
            await self._stop_playwright()
            raise

    async def _launch(self) -> None:
        browser = await self._playwright.chromium.launch(headless=True, args=LAUNCH_ARGS)
        previous, self._browser = self._browser, browser
        logger.info("Chromium launched (version %s)", browser.version)
        if previous is not None and previous is not browser:
            try:
                await previous.close()
            except PlaywrightError:
                pass

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            playwright, self._playwright = self._playwright, None
            await playwright.stop()

    async def stop(self) -> None:
        self._closing = True
        try:
            if self._browser is not None:
                browser, self._browser = self._browser, None
                await browser.close()
        except PlaywrightError as exc:
            logger.warning("Closing Chromium failed: %s: %s", type(exc).__name__, exc)
        finally:
            await self._stop_playwright()

    @property
    def is_ready(self) -> bool:
        return not self._closing and self._browser is not None and self._browser.is_connected()

    async def _ensure_browser(self) -> Browser:
        if self._closing:
            raise RenderUnavailable()
        if self.is_ready:
            return self._browser
        async with self._relaunch_lock:
            if self._closing:
                raise RenderUnavailable()
            if not self.is_ready:
                logger.warning("Chromium is not connected, relaunching.")
                # Shielded so a caller giving up mid-launch cannot orphan the new browser.
                await asyncio.shield(self._launch())
        return self._browser

    async def render_png(
        self, html: str, width: int, height: int, font_checks: Sequence[str]
    ) -> bytes:
        return await self._render(html, width, height, font_checks, _png)

    async def render_pdf(
        self, html: str, width: int, height: int, font_checks: Sequence[str]
    ) -> bytes:
        return await self._render(html, width, height, font_checks, _pdf)

    async def _render(
        self, html: str, width: int, height: int, font_checks: Sequence[str], output: Output
    ) -> bytes:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._queue_timeout_ms / 1000)
        except TimeoutError as exc:
            raise RenderBusy() from exc

        try:
            # Launching is deliberately outside the render budget: a cold start would
            # otherwise be charged to whichever request happened to trigger it.
            browser = await self._ensure_browser()
            return await asyncio.wait_for(
                self._capture(browser, html, width, height, font_checks, output),
                timeout=self._render_timeout_ms / 1000,
            )
        except TimeoutError as exc:
            raise RenderTimeout() from exc
        finally:
            self._semaphore.release()

    async def _capture(
        self,
        browser: Browser,
        html: str,
        width: int,
        height: int,
        font_checks: Sequence[str],
        output: Output,
    ) -> bytes:
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            locale="de-DE",
        )
        try:
            page = await context.new_page()
            # Below the outer budget so Playwright's own error, which names the failing
            # step, wins the race against the anonymous outer timeout.
            page.set_default_timeout(self._render_timeout_ms * 0.8)
            await page.set_content(html, wait_until="load")
            await page.wait_for_function(READY_EXPRESSION)
            await self._verify_fonts(page, font_checks)
            return await output(page, width, height)
        except PlaywrightError as exc:
            if "Timeout" in type(exc).__name__:
                logger.error("Render step timed out: %s", exc)
                raise RenderTimeout() from exc
            logger.error("Playwright error: %s: %s", type(exc).__name__, exc)
            raise
        finally:
            # Shielded and bounded: an unresponsive browser must not park a render slot.
            try:
                await asyncio.wait_for(
                    asyncio.shield(context.close()), timeout=CONTEXT_CLOSE_TIMEOUT
                )
            except (TimeoutError, PlaywrightError) as exc:
                logger.warning("Closing browser context failed: %s: %s", type(exc).__name__, exc)

    async def _verify_fonts(self, page, font_checks: Sequence[str]) -> None:
        results = await page.evaluate(FONT_PROBE, list(font_checks))
        missing = [face for face, ok in zip(font_checks, results, strict=True) if not ok]
        if not missing:
            return
        logger.warning("Brand fonts not loaded: %s", ", ".join(missing))
        if settings.strict_fonts:
            raise FontNotLoaded(", ".join(missing))
