"""
Browser runtime helpers - shared between browser_pre_step_node (entry-time
pre-steps) and execution_node (in-loop browser actions).

This module centralises:
- ensure_browser_tools(): lazy-create / attach a BrowserTools instance from state
- execute_browser_step(): dispatch one declarative browser step to BrowserTools
- _ACTION_DISPATCH: 14 browser_* action types -> handler functions

Design goal: allow browser actions to run at ANY sub-step position (not just
the entry pre-step), enabling desktop <-> browser interleaving inside one task.

Supported action types and their BrowserTools method mapping:

    browser_navigate      -> goto(url, wait_until)
    browser_click         -> click_selector(selector, button?, click_count?, modifiers?)
    browser_fill          -> fill_selector(selector, value|value_env)
    browser_type          -> page.type(selector, text)
    browser_wait_selector -> wait_for_selector(selector, state)
    browser_wait_time     -> asyncio.sleep(ms / 1000)
    browser_press         -> press(keys)
    browser_scroll        -> scroll(pixels)
    browser_screenshot    -> screenshot(path, full_page)
    browser_wait_idle     -> wait_for_idle(timeout_ms)
    browser_click_text    -> click_by_text(text, exact?, button?, click_count?, modifiers?)
    browser_click_role    -> click_by_role(role, name, exact?, button?, click_count?, modifiers?)
    browser_wait_text     -> wait_for_text(text, timeout_ms)
    browser_wait_role     -> wait_for_role(role, name, timeout_ms)

Click-action optional fields (browser_click / browser_click_text / browser_click_role):
    button:       "left" (default) | "right" | "middle"
    click_count:  1 (default) | 2 (double-click) | 3 (triple-click)
    modifiers:    list of keys to hold, e.g. ["Shift", "Control", "Alt", "Meta"]
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from utils.browser_tools import BrowserTools, PLAYWRIGHT_AVAILABLE

if TYPE_CHECKING:
    # Avoid runtime circular import: nodes.types <-> utils.browser_runtime
    from nodes.types import AgentState  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_STATE = "data/storage_state.json"
DEFAULT_TIMEOUT_MS = 15000


async def ensure_browser_tools(state: "AgentState") -> BrowserTools:
    """Return a started BrowserTools, creating it lazily if needed.

    Looks up state["browser_tools"]; if missing or not started, builds one from
    the browser_* fields on state and starts it (optionally navigating to
    target_url first).

    Mirrors BrowserAgent/nodes/runtime.py:ensure_browser_tools, adapted to
    GuiAgent's state field names (cdp_endpoint / browser_headless / ...).
    """
    tools = state.get("browser_tools")
    if tools is not None and getattr(tools, "_started", False):
        return tools

    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright not available. Install with: "
            "pip install playwright && playwright install chromium"
        )

    cdp_endpoint = state.get("cdp_endpoint")
    target_url = state.get("target_url")

    tools = BrowserTools(
        headless=state.get("browser_headless", False),
        user_data_dir=state.get("browser_user_data_dir"),
        storage_state_path=state.get("browser_storage_state", DEFAULT_STORAGE_STATE),
        default_timeout_ms=DEFAULT_TIMEOUT_MS,
        cdp_endpoint=cdp_endpoint,
    )

    if cdp_endpoint:
        logger.info("[BROWSER_RUNTIME] Connecting via CDP to %s", cdp_endpoint)
    else:
        logger.info("[BROWSER_RUNTIME] Launching BrowserTools ...")
    await tools.start()

    if target_url:
        await tools.goto(target_url)

    return tools


# ---------------------------------------------------------------------------
# Individual step handlers - one per browser_* action type.
# Each takes (tools, step) and is awaited by execute_browser_step().
# ---------------------------------------------------------------------------

async def _browser_navigate(tools: BrowserTools, step: dict) -> None:
    await tools.goto(step["url"], wait_until=step.get("wait_until", "domcontentloaded"))


async def _browser_click(tools: BrowserTools, step: dict) -> None:
    await tools.click_selector(
        step["selector"],
        button=step.get("button", "left"),
        click_count=step.get("click_count", 1),
        modifiers=step.get("modifiers"),
    )


async def _browser_fill(tools: BrowserTools, step: dict) -> None:
    await tools.fill_selector(step["selector"], _resolve_value(step))


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


async def _browser_click_text(tools: BrowserTools, step: dict) -> None:
    await tools.click_by_text(
        step["text"],
        exact=step.get("exact", False),
        button=step.get("button", "left"),
        click_count=step.get("click_count", 1),
        modifiers=step.get("modifiers"),
    )


async def _browser_click_role(tools: BrowserTools, step: dict) -> None:
    await tools.click_by_role(
        step["role"],
        name=step.get("name", ""),
        exact=step.get("exact", False),
        button=step.get("button", "left"),
        click_count=step.get("click_count", 1),
        modifiers=step.get("modifiers"),
    )


async def _browser_wait_text(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_text(step["text"], timeout_ms=step.get("timeout_ms"))


async def _browser_wait_role(tools: BrowserTools, step: dict) -> None:
    await tools.wait_for_role(step["role"], name=step.get("name", ""),
                              timeout_ms=step.get("timeout_ms"))


_ACTION_DISPATCH: dict[str, Any] = {
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


def _resolve_value(step: dict) -> str:
    """Resolve a fill value: literal `value`, or env var via `value_env`.

    Mirrors BrowserAgent/nodes/skill_matcher.py:_resolve_value - credentials
    never live in JSON, only env.
    """
    if "value_env" in step:
        name = step["value_env"]
        value = os.environ.get(name)
        if value is None:
            raise RuntimeError(
                f"Required env var '{name}' is not set for browser step"
            )
        return value
    return step.get("value", "")


async def execute_browser_step(tools: BrowserTools, step: dict) -> None:
    """Dispatch a single declarative browser step to BrowserTools.

    The step dict must have an "action" key whose value starts with "browser_".
    Unknown action types raise ValueError.
    """
    action_type = step.get("action", "")
    handler = _ACTION_DISPATCH.get(action_type)
    if handler is None:
        raise ValueError(f"Unsupported browser action: {action_type}")
    print(f"  [BROWSER_RUNTIME] {action_type}: {_summarize_step(step)}")
    await handler(tools, step)


def is_browser_action(action_or_step: dict) -> bool:
    """True if the given action dict's type/action starts with browser_.

    Accepts both desktop action format ({"type": "..."}) and browser step
    format ({"action": "browser_*"}), so callers can pass either.
    """
    action_type = action_or_step.get("action") or action_or_step.get("type", "")
    return isinstance(action_type, str) and action_type.startswith("browser_")


def _summarize_step(step: dict) -> str:
    """One-line summary of a browser step for logging."""
    action = step.get("action", "")
    if action == "browser_navigate":
        return step.get("url", "?")
    if action in ("browser_click", "browser_fill", "browser_type",
                  "browser_wait_selector"):
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
