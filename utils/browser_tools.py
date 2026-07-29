"""
Browser interaction tools - web automation via async Playwright.

Parallel to ComputerTools (desktop / pyautogui). Where ComputerTools clicks
pixel coordinates, BrowserTools resolves real DOM elements (auto-wait,
scroll-into-view, actionability checks come for free from Playwright).

Design notes:
- Async API: the LangGraph runs via astream(), so browser nodes are async and
  call these coroutines directly. (The Playwright *sync* API cannot run inside
  a running asyncio loop, hence async here.)
- Session reuse: a single browser context lives for the whole task and is
  stored on AgentState["browser_tools"]. storage_state persistence lets a
  logged-in session survive across runs.
- Element resolution: snapshot() tags elements with data-agent-id=N; click(id)
  / type_text(id) resolve via that attribute. Deterministic skills instead pass
  an explicit selector.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - environment guard
    PLAYWRIGHT_AVAILABLE = False
    logger.warning(
        "Playwright not installed. Browser mode disabled. "
        "Install with: pip install playwright && playwright install chromium"
    )

from browser.dom_extractor import EXTRACT_INTERACTABLES_JS, AGENT_ID_ATTR

DEFAULT_TIMEOUT_MS = 15000


class BrowserTools:
    """Async Playwright wrapper for browser-page automation."""

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        storage_state_path: Optional[str] = None,
        default_timeout_ms: int = DEFAULT_TIMEOUT_MS,
        cdp_endpoint: Optional[str] = None,
    ) -> None:
        """
        Args:
            headless: Run without a visible window.
            user_data_dir: If set, launch a *persistent* context (cookies, logins
                survive on disk). Mutually preferred over storage_state for SSO.
            storage_state_path: Path to a Playwright storageState JSON. Loaded on
                start if it exists; saved via save_storage_state().
            default_timeout_ms: Default action/navigation timeout.
            cdp_endpoint: If set, connect to an already-running browser via CDP
                (e.g. "http://localhost:9222") instead of launching a new one.
                The browser is shared; each BrowserTools instance gets its own
                browser context for isolation.
        """
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.storage_state_path = storage_state_path
        self.default_timeout_ms = default_timeout_ms
        self.cdp_endpoint = cdp_endpoint

        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._started = False
        self._owns_browser = False  # True when we launched the browser ourselves
        self._owns_page = False     # True when we created the page ourselves
                                    # (False when reusing an existing tab via CDP)

    async def start(self) -> None:
        """Launch the browser and open the first page.

        When cdp_endpoint is set, connects to an already-running browser via
        Chrome DevTools Protocol instead of launching a new one. The shared
        browser stays alive after close().
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright not available. Run: pip install playwright && playwright install chromium"
            )
        if self._started:
            return

        self._pw = await async_playwright().start()

        if self.cdp_endpoint:
            # Connect to an already-running browser via CDP.
            # Reuse the default context AND its existing tab (if any) so we
            # operate on the page the user already has open instead of
            # opening a fresh tab every task.
            self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_endpoint)
            self._owns_browser = False  # Shared browser, don't close it
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
            else:
                self._context = await self._browser.new_context(no_viewport=True)
            existing_pages = self._context.pages
            if existing_pages:
                # Reuse the first existing tab - don't open a new one.
                self.page = existing_pages[0]
                self._owns_page = False
                logger.info(
                    "BrowserTools reused existing tab #%d via CDP (url=%s)",
                    0,
                    self.page.url if not self.page.is_closed() else "(closed)",
                )
            else:
                self.page = await self._context.new_page()
                self._owns_page = True
                logger.info("BrowserTools opened a new tab via CDP (no existing tab to reuse)")
            logger.info("BrowserTools connected via CDP to %s", self.cdp_endpoint)

        elif self.user_data_dir:
            # Persistent context: best for SSO / long-lived logins.
            self._context = await self._pw.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=self.headless,
            )
            self._owns_browser = True
            if self._context.pages:
                self.page = self._context.pages[0]
                self._owns_page = False
            else:
                self.page = await self._context.new_page()
                self._owns_page = True

        else:
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            self._owns_browser = True
            storage_state = (
                self.storage_state_path
                if self.storage_state_path and os.path.exists(self.storage_state_path)
                else None
            )
            self._context = await self._browser.new_context(storage_state=storage_state)
            self.page = await self._context.new_page()
            self._owns_page = True

        self._context.set_default_timeout(self.default_timeout_ms)
        self._started = True
        logger.info("BrowserTools started (headless=%s)", self.headless)

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate to a URL."""
        await self.page.goto(url, wait_until=wait_until)

    async def screenshot(self, path: str, full_page: bool = False) -> bool:
        """Save a screenshot of the current page. Returns True on success."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await self.page.screenshot(path=path, full_page=full_page)
            return True
        except Exception as e:  # pragma: no cover - runtime guard
            logger.error("Screenshot failed: %s", e)
            return False

    async def snapshot(self) -> list[dict[str, Any]]:
        """
        Extract the indexed interactable-element list for the current page.

        Side effect: tags elements with data-agent-id so click()/type_text() can
        resolve them. Returns the metadata list (see dom_extractor).
        """
        elements = await self.page.evaluate(EXTRACT_INTERACTABLES_JS)
        return elements or []

    def _agent_selector(self, agent_id: int) -> str:
        return f'[{AGENT_ID_ATTR}="{agent_id}"]'

    async def click(self, agent_id: int) -> None:
        """Click an element previously tagged by snapshot()."""
        await self.page.click(self._agent_selector(agent_id))

    async def type_text(self, agent_id: int, text: str, clear: bool = True) -> None:
        """Fill text into an element previously tagged by snapshot()."""
        selector = self._agent_selector(agent_id)
        if clear:
            await self.page.fill(selector, text)
        else:
            await self.page.type(selector, text)

    # ---- Deterministic-skill helpers: act on explicit author-provided selectors ----

    async def click_selector(
        self,
        selector: str,
        button: str = "left",
        click_count: int = 1,
        modifiers: Optional[list] = None,
    ) -> None:
        """Click an element by CSS selector.

        Args:
            selector: CSS / Playwright selector.
            button: "left" (default), "right", or "middle".
            click_count: 1 (default), 2 for double-click, 3 for triple.
            modifiers: Optional list of keys to hold, e.g. ["Shift", "Control"].
        """
        kwargs = {"button": button, "click_count": click_count}
        if modifiers:
            kwargs["modifiers"] = modifiers
        await self.page.click(selector, **kwargs)

    async def fill_selector(self, selector: str, value: str) -> None:
        await self.page.fill(selector, value)

    async def wait_for_selector(self, selector: str, state: str = "visible") -> None:
        await self.page.wait_for_selector(selector, state=state)

    # ---- Text-based locators (by_role / get_by_text equivalents) ----

    async def click_by_text(
        self,
        text: str,
        exact: bool = False,
        button: str = "left",
        click_count: int = 1,
        modifiers: Optional[list] = None,
    ) -> None:
        """Click an element by its visible text. Equivalent to page.get_by_text().

        Args:
            text: Visible text to locate.
            exact: True for exact match, False for substring.
            button: "left" (default), "right", or "middle".
            click_count: 1 (default), 2 for double-click, 3 for triple.
            modifiers: Optional list of keys to hold, e.g. ["Shift", "Control"].
        """
        if exact:
            locator = self.page.get_by_text(text, exact=True)
        else:
            locator = self.page.get_by_text(text)
        kwargs = {"button": button, "click_count": click_count}
        if modifiers:
            kwargs["modifiers"] = modifiers
        await locator.first.click(**kwargs)

    async def click_by_role(
        self,
        role: str,
        name: str = "",
        exact: bool = False,
        button: str = "left",
        click_count: int = 1,
        modifiers: Optional[list] = None,
    ) -> None:
        """Click an element by ARIA role and accessible name.
        Common roles: button, link, menuitem, tab, textbox, combobox, etc.

        Args:
            role: ARIA role (button, link, menuitem, ...).
            name: Accessible name (label text).
            exact: True for exact name match.
            button: "left" (default), "right", or "middle".
            click_count: 1 (default), 2 for double-click, 3 for triple.
            modifiers: Optional list of keys to hold, e.g. ["Shift", "Control"].
        """
        locator = self.page.get_by_role(role, name=name, exact=exact)
        kwargs = {"button": button, "click_count": click_count}
        if modifiers:
            kwargs["modifiers"] = modifiers
        await locator.first.click(**kwargs)

    async def wait_for_text(self, text: str, timeout_ms: Optional[int] = None) -> None:
        """Wait until text appears on the page (any element)."""
        await self.page.get_by_text(text).first.wait_for(
            state="visible", timeout=timeout_ms or self.default_timeout_ms
        )

    async def wait_for_role(self, role: str, name: str = "",
                            timeout_ms: Optional[int] = None) -> None:
        """Wait until an element with given role and name is visible."""
        await self.page.get_by_role(role, name=name).first.wait_for(
            state="visible", timeout=timeout_ms or self.default_timeout_ms
        )

    # ---- Generic actions ----

    async def press(self, keys: str) -> None:
        """Press a key / key combo, e.g. 'Enter' or 'Control+A'."""
        await self.page.keyboard.press(keys)

    async def scroll(self, pixels: int) -> None:
        """Scroll vertically. Positive = down, negative = up."""
        await self.page.mouse.wheel(0, pixels)

    async def click_coordinate(self, x: int, y: int) -> None:
        """Fallback: click raw page coordinates (for canvas / non-DOM content)."""
        await self.page.mouse.click(x, y)

    async def wait_for_idle(self, timeout_ms: Optional[int] = None) -> None:
        """Wait for the network to go idle (best-effort)."""
        try:
            await self.page.wait_for_load_state(
                "networkidle", timeout=timeout_ms or self.default_timeout_ms
            )
        except Exception:
            logger.debug("wait_for_idle timed out (continuing)")

    async def pick_kill_chain_page(
        self, selector: str = "div.kill_chain_card_grops.target_outer"
    ) -> Optional[Any]:
        """从当前所有标签页里挑一个有杀伤链 DOM 的 page。

        kill_chain_cache 轮询用：用户可能关掉启动时拿到的标签页、或在
        别的标签页里操作指控页，固定持有 page 引用会失效（报 "Target
        page has been closed"）。每次轮询前调这个方法动态定位当前
        指控页。

        Args:
            selector: 识别指控页的 CSS selector（页面含该元素即视为指控页）

        Returns:
            第一个匹配 selector 的 Page；都没有则返回 None。
            context 未初始化或访问出错也返回 None。
        """
        if not self._context:
            return None
        try:
            pages = list(self._context.pages)
        except Exception:
            return None
        for page in pages:
            try:
                if page.is_closed():
                    continue
                if await page.locator(selector).count() > 0:
                    return page
            except Exception:
                continue
        return None

    async def save_storage_state(self, path: Optional[str] = None) -> None:
        """Persist cookies/localStorage so the next run starts logged in."""
        target = path or self.storage_state_path
        if not target:
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        await self._context.storage_state(path=target)
        logger.info("Saved storage state to %s", target)

    async def close(self) -> None:
        """Tear down the browser context (and browser if we own it).

        In CDP mode, only pages we created ourselves are closed; tabs we
        reused from the existing browser window are left intact so the
        user doesn't lose their tab. The shared browser itself always
        survives for the next task.
        """
        try:
            if self.cdp_endpoint:
                # Only close pages we created; leave reused user tabs alone.
                if self._owns_page and self.page and not self.page.is_closed():
                    await self.page.close()
            else:
                if self._context:
                    await self._context.close()
                if self._browser and self._owns_browser:
                    await self._browser.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._started = False
            logger.info("BrowserTools closed (owns_page=%s)", self._owns_page)
