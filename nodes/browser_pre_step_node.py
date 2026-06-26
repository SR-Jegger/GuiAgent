"""
Browser pre-step node -- runs declarative Playwright actions ONCE before the
desktop pipeline takes over.

When `browser_pre_steps` is empty, this node is a no-op pass-through, so
existing desktop-only tasks are completely unaffected.

Supported action types and their BrowserTools method mapping:

    browser_navigate      -> goto(url, wait_until)
    browser_click         -> click_selector(selector)
    browser_fill          -> fill_selector(selector, value)
    browser_type          -> page.type(selector, text)
    browser_wait_selector -> wait_for_selector(selector, state)
    browser_wait_time     -> asyncio.sleep(ms / 1000)
    browser_press         -> press(keys)
    browser_scroll        -> scroll(pixels)
    browser_screenshot    -> screenshot(path, full_page)
    browser_wait_idle     -> wait_for_idle(timeout_ms)
    browser_click_text    -> click_by_text(text, exact)      # get_by_text equivalent
    browser_click_role    -> click_by_role(role, name, exact) # get_by_role equivalent
    browser_wait_text     -> wait_for_text(text, timeout_ms)
    browser_wait_role     -> wait_for_role(role, name, timeout_ms)
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from nodes.types import AgentState
from utils.browser_tools import BrowserTools, PLAYWRIGHT_AVAILABLE

logger = logging.getLogger(__name__)


async def _browser_navigate(tools: BrowserTools, step: dict) -> None:
    await tools.goto(step["url"], wait_until=step.get("wait_until", "domcontentloaded"))


async def _browser_click(tools: BrowserTools, step: dict) -> None:
    await tools.click_selector(step["selector"])


async def _browser_fill(tools: BrowserTools, step: dict) -> None:
    await tools.fill_selector(step["selector"], step["value"])


async def _browser_type(tools: BrowserTools, step: dict) -> None:
    await tools.page.type(step["selector"], step["text"])


async def _browser_wait_selector(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_selector(step["selector"], state=step.get("state", "visible"))


async def _browser_wait_time(tools: BrowserTools, step: dict) -> None:
    await asyncio.sleep(step.get("ms", 1000) / 1000)


async def _browser_press(tools: BrowserTools, step: dict) -> None:
    await tools.press(step["keys"])


async def _browser_scroll(tools: BrowserTools, step: dict) -> None:
    await tools.scroll(step["pixels"])


async def _browser_screenshot(tools: BrowserTools, step: dict) -> None:
    await tools.screenshot(step["path"], full_page=step.get("full_page", False))


async def _browser_wait_idle(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_idle(timeout_ms=step.get("timeout_ms"))


# ---- Text / Role locators (get_by_text / get_by_role equivalents) ----

async def _browser_click_text(tools: BrowserTools, step: dict) -> None:
    await tools.click_by_text(step["text"], exact=step.get("exact", False))


async def _browser_click_role(tools: BrowserTools, step: dict) -> None:
    await tools.click_by_role(step["role"], name=step.get("name", ""),
                              exact=step.get("exact", False))


async def _browser_wait_text(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_text(step["text"], timeout_ms=step.get("timeout_ms"))


async def _browser_wait_role(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_role(step["role"], name=step.get("name", ""),
                              timeout_ms=step.get("timeout_ms"))


_ACTION_DISPATCH = {
    "browser_navigate": _browser_navigate,
    "browser_click": _browser_click,
    "browser_fill": _browser_fill,
    "browser_type": _browser_type,
    "browser_wait_selector": _browser_wait_selector,
    "browser_wait_time": _browser_wait_time,
    "browser_press": _browser_press,
    "browser_scroll": _browser_scroll,
    "browser_screenshot": _browser_screenshot,
    "browser_wait_idle": _browser_wait_idle,
    "browser_click_text": _browser_click_text,
    "browser_click_role": _browser_click_role,
    "browser_wait_text": _browser_wait_text,
    "browser_wait_role": _browser_wait_role,
}


async def browser_pre_step_node(state: AgentState) -> AgentState:
    """
    Execute declarative browser pre-steps before the desktop pipeline.

    Reads `browser_pre_steps` from state. If empty, returns immediately.
    Otherwise initializes BrowserTools, executes each step, and leaves the
    browser open on state so the desktop pipeline can capture its window.
    """
    pre_steps = state.get("browser_pre_steps", [])
    if not pre_steps:
        return {"execution_status": "success"}

    if not PLAYWRIGHT_AVAILABLE:
        return {
            "execution_status": "error",
            "error_message": "Playwright not installed. Install with: pip install playwright && playwright install chromium",
            "stop_flag": True,
        }

    browser_tools = state.get("browser_tools")
    if browser_tools is None:
        cdp_endpoint = state.get("cdp_endpoint")
        if cdp_endpoint:
            logger.info("[BROWSER_PRE_STEP] Connecting via CDP to %s", cdp_endpoint)
        else:
            logger.info("[BROWSER_PRE_STEP] Initializing BrowserTools ...")
        browser_tools = BrowserTools(
            headless=state.get("browser_headless", False),
            storage_state_path=state.get("browser_storage_state"),
            user_data_dir=state.get("browser_user_data_dir"),
            cdp_endpoint=cdp_endpoint,
        )
        await browser_tools.start()

    print(f"\n[BROWSER_PRE_STEP] Executing {len(pre_steps)} pre-step(s)...")

    for i, step in enumerate(pre_steps):
        action_type = step.get("action", "")
        handler = _ACTION_DISPATCH.get(action_type)
        if handler is None:
            print(f"[BROWSER_PRE_STEP] Unknown action type: {action_type}")
            return {
                "execution_status": "error",
                "error_message": f"Unknown browser pre-step action: {action_type}",
                "browser_tools": browser_tools,
            }
        try:
            print(f"  [{i + 1}/{len(pre_steps)}] {action_type}: {_summarize_step(step)}")
            await handler(browser_tools, step)
        except Exception as e:
            logger.error("[BROWSER_PRE_STEP] Step %d (%s) failed: %s", i + 1, action_type, e)
            return {
                "execution_status": "error",
                "error_message": f"Browser pre-step '{action_type}' failed: {e}",
                "browser_tools": browser_tools,
            }

    print("[BROWSER_PRE_STEP] All pre-steps completed, handing off to desktop pipeline\n")
    return {
        "execution_status": "success",
        "browser_tools": browser_tools,
        "mode": "browser",
    }


def _summarize_step(step: dict) -> str:
    """One-line summary of a pre-step for logging."""
    action = step.get("action", "")
    if action == "browser_navigate":
        return step.get("url", "?")
    if action in ("browser_click", "browser_fill", "browser_type", "browser_wait_selector"):
        return step.get("selector", "?")
    if action in ("browser_click_text", "browser_wait_text"):
        return step.get("text", "?")
    if action in ("browser_click_role", "browser_wait_role"):
        return f"role={step.get('role', '?')} name={step.get('name', '')}"
    if action == "browser_wait_time":
        return f"{step.get('ms', 1000)}ms"
    if action == "browser_press":
        return step.get("keys", "?")
    if action == "browser_scroll":
        return str(step.get("pixels", 0))
    return ""
