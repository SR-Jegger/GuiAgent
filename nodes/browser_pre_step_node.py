"""
Browser pre-step node -- runs declarative Playwright actions ONCE before the
desktop pipeline takes over.

When `browser_pre_steps` is empty, this node is a no-op pass-through, so
existing desktop-only tasks are completely unaffected.

Delegates to utils.browser_runtime.execute_browser_step for dispatch, so all
browser_* action types are supported including browser_if (conditional branch).
Note: in the pre-step phase, browser_if's then/else can only contain browser_*
actions - desktop actions require the desktop pipeline (computer_tools) which
is not yet initialized here.
"""

import logging
from typing import TYPE_CHECKING

from nodes.types import AgentState
from utils.browser_tools import BrowserTools, PLAYWRIGHT_AVAILABLE
from utils.browser_runtime import execute_browser_step

logger = logging.getLogger(__name__)


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
        try:
            await execute_browser_step(browser_tools, step)
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
