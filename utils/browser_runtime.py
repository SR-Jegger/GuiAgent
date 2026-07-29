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
    browser_if            -> conditional branch on element text/attribute/count/value
    browser_extract       -> extract value from element, store as {{name}} variable

Click-action optional fields (browser_click / browser_click_text / browser_click_role):
    button:       "left" (default) | "right" | "middle"
    click_count:  1 (default) | 2 (double-click) | 3 (triple-click)
    modifiers:    list of keys to hold, e.g. ["Shift", "Control", "Alt", "Meta"]

browser_if - conditional branch:
    {
      "action": "browser_if",
      "condition": {
        "selector": "div.confidence",     # CSS / Playwright selector
        "field": "text",                   # text | value | count | attribute:<name>
        "op": ">",                         # see below
        "value": 0.8,                      # expected value (optional for "exists")
        "timeout_ms": 2000,                # optional, default uses page timeout
        "selector_filter": "0\\.\\d+"       # optional regex to pick element from
                                          # multiple matches by text content
      },
      "then": [ <browser_step>, ... ],     # run if condition is True
      "else": [ <browser_step>, ... ]      # run if condition is False (optional)
    }

    Fields:
      text              element inner_text (default)
      value             input/select current value
      count             number of matching elements
      attribute:<name>  element attribute, e.g. attribute:aria-label

    selector_filter:
      Optional Python regex (string form). When `selector` matches multiple
      elements, the runtime iterates them and picks the first whose inner_text
      matches this regex. Useful when the target element (e.g. a confidence
      value "0.901") is among several sibling divs. The regex is applied via
      re.search, so partial match is sufficient.

    Operators:
      exists            element exists/visible (value ignored)
      contains          substring match (string)
      equals           exact equality (string)
      matches          regex match against value (string)
      > >= < <= == !=  numeric comparison (auto-extracts first number from field)
"""

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

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


# ---------------------------------------------------------------------------
# Conditional branch (browser_if)
# ---------------------------------------------------------------------------

# Numeric operators extracted from a field's text, e.g. "置信度: 0.85" -> 0.85
_NUMBER_RE = re.compile(r"-?\d+\.?\d*")

_NUMERIC_OPS = {
    ">":  lambda a, e: a >  e,
    ">=": lambda a, e: a >= e,
    "<":  lambda a, e: a <  e,
    "<=": lambda a, e: a <= e,
    "==": lambda a, e: a == e,
    "!=": lambda a, e: a != e,
}


async def _read_field(tools: BrowserTools, cond: dict) -> Any:
    """Read a value from the page for conditional comparison.

    Returns:
        int for count, str for text/value/attribute, or None on failure.
    Raises nothing - callers handle missing elements as False.
    """
    selector = cond["selector"]
    field = cond.get("field", "text")
    timeout_ms = cond.get("timeout_ms")
    selector_filter = cond.get("selector_filter")

    locator = tools.page.locator(selector)

    if field == "count":
        return await locator.count()

    kwargs = {"timeout": timeout_ms} if timeout_ms is not None else {}

    # If selector_filter regex is provided, iterate matching elements and return
    # the first one whose text matches the filter. Useful when `selector` matches
    # multiple elements and we need to pick the one with numeric/regex content.
    if selector_filter:
        try:
            pattern = re.compile(selector_filter)
        except re.error as e:
            logger.warning("[BROWSER_IF] invalid selector_filter regex: %s", e)
            return None

        count = await locator.count()
        for i in range(count):
            elem = locator.nth(i)
            try:
                text = await elem.inner_text(**kwargs)
            except Exception:
                continue
            if pattern.search(text or ""):
                # Found the matching element. Now return the requested field
                # from this specific element.
                if field.startswith("attribute:"):
                    attr_name = field.split(":", 1)[1]
                    return await elem.get_attribute(attr_name, **kwargs)
                if field == "value":
                    return await elem.input_value(**kwargs)
                return text  # default: text
        logger.warning(
            "[BROWSER_IF] selector_filter '%s' matched none of %d element(s)",
            selector_filter, count,
        )
        return None

    first = locator.first

    if field.startswith("attribute:"):
        attr_name = field.split(":", 1)[1]
        return await first.get_attribute(attr_name, **kwargs)
    if field == "value":
        return await first.input_value(**kwargs)
    # default: text
    return await first.inner_text(**kwargs)


async def _evaluate_condition(tools: BrowserTools, cond: dict) -> bool:
    """Evaluate a browser_if condition. Never raises - returns False on miss.

    See module docstring for supported fields and operators.
    """
    op = cond.get("op", "exists")
    expected = cond.get("value")

    try:
        actual = await _read_field(tools, cond)
    except Exception as e:
        logger.warning(
            "[BROWSER_IF] cannot read selector=%s field=%s: %s",
            cond.get("selector"), cond.get("field", "text"), e,
        )
        return False

    if op == "exists":
        return actual is not None and actual != ""

    actual_str = "" if actual is None else str(actual)
    expected_str = "" if expected is None else str(expected)

    if op == "contains":
        return expected_str in actual_str
    if op == "equals":
        return actual_str == expected_str
    if op == "matches":
        return re.search(expected_str, actual_str) is not None

    # Numeric operators
    if op in _NUMERIC_OPS:
        match = _NUMBER_RE.search(actual_str)
        if not match:
            logger.warning(
                "[BROWSER_IF] no number found in %r for op %s",
                actual_str, op,
            )
            return False
        try:
            actual_num = float(match.group())
            expected_num = float(expected)
        except (ValueError, TypeError):
            logger.warning(
                "[BROWSER_IF] cannot compare actual=%r expected=%r",
                actual_str, expected,
            )
            return False
        return _NUMERIC_OPS[op](actual_num, expected_num)

    logger.warning("[BROWSER_IF] unknown operator: %s", op)
    return False


async def _browser_if(
    tools: BrowserTools,
    step: dict,
    execute_sub: Optional[Any] = None,
    variables: Optional[Dict[str, str]] = None,
) -> None:
    """Conditional branch: run `then` or `else` sub-steps based on condition.

    Args:
        tools: BrowserTools for condition evaluation and browser_* sub-steps.
        step: The browser_if step dict (contains `condition`, `then`, `else`).
        execute_sub: Optional async callback `(sub_step_dict) -> None` used to
            execute each branch's sub-steps. When provided, it handles both
            browser_* and desktop actions (caller-defined). When None, only
            browser_* sub-steps are supported and a non-browser sub-step raises.
        variables: Optional task-scoped variable dict. Passed through to
            execute_browser_step for {{name}} substitution in branch sub-steps.
    """
    cond = step.get("condition")
    if not cond or "selector" not in cond:
        raise ValueError("browser_if requires a 'condition' with 'selector'")

    result = await _evaluate_condition(tools, cond)
    branch = "then" if result else "else"
    sub_steps = step.get(branch, [])

    print(f"  [BROWSER_IF] condition -> {branch} ({len(sub_steps)} step(s))")
    for sub in sub_steps:
        if execute_sub is not None:
            await execute_sub(sub)
        else:
            # Default: only browser_* sub-steps are supported.
            sub_type = sub.get("action", "") or sub.get("type", "")
            if not sub_type.startswith("browser_"):
                raise ValueError(
                    f"browser_if branch contains non-browser action "
                    f"'{sub_type}', but no desktop executor was provided. "
                    f"Pass execute_sub callback to support desktop actions "
                    f"in then/else branches."
                )
            await execute_browser_step(tools, sub, execute_sub=execute_sub, variables=variables)


# ---------------------------------------------------------------------------
# Variable extraction (browser_extract)
# ---------------------------------------------------------------------------

async def _browser_extract(
    tools: BrowserTools,
    step: dict,
    variables: Optional[Dict[str, str]] = None,
) -> None:
    """Extract a value from the page and store it as a task-scoped variable.

    Required fields:
        selector: CSS / Playwright selector to locate element(s).
        store_as: Variable name; subsequent steps reference via {{store_as}}.

    Optional fields:
        selector_filter: Regex (string form) to pick target element from
            multiple matches by inner_text. Default: take locator.first.
        extract_regex:  Regex with capture group to extract value from text.
            Default: use entire text (no extraction).
        regex_group:    Capture group index (1-based). Default: 1.
        field:          text | value | attribute:<name>. Default: text.
        timeout_ms:     Wait timeout for element lookup. Default: page default.

    Behavior on multiple matches:
        Takes the first matching element (after selector_filter applies) and
        logs a WARNING so the user knows multiple cards matched. Does NOT
        raise - this is by design for scenarios where occasional duplicates
        appear but the first is always the intended target.

    Behavior on no match / extraction failure:
        Raises ValueError so the task surfaces the failure rather than
        silently using an undefined variable downstream.
    """
    if variables is None:
        raise RuntimeError(
            "browser_extract requires a variables dict to store the extracted "
            "value. Ensure execution_node injects task-scoped variables."
        )

    selector = step.get("selector")
    store_as = step.get("store_as")
    if not selector or not store_as:
        raise ValueError("browser_extract requires 'selector' and 'store_as' fields")

    selector_filter = step.get("selector_filter")
    extract_regex = step.get("extract_regex")
    regex_group = step.get("regex_group", 1)
    field = step.get("field", "text")
    timeout_ms = step.get("timeout_ms")

    # Reuse _read_field for element lookup + selector_filter handling.
    # We pass a synthetic cond dict shaped like a browser_if condition.
    cond = {
        "selector": selector,
        "field": field,
        "timeout_ms": timeout_ms,
        "selector_filter": selector_filter,
    }
    actual = await _read_field(tools, cond)
    if actual is None:
        raise ValueError(
            f"browser_extract: no element matched selector='{selector}'"
            + (f" with selector_filter='{selector_filter}'" if selector_filter else "")
        )

    actual_str = str(actual)

    # Check if multiple elements matched (for WARNING).
    if selector_filter:
        try:
            pattern = re.compile(selector_filter)
            count = await tools.page.locator(selector).count()
            matched = 0
            for i in range(count):
                try:
                    text = await tools.page.locator(selector).nth(i).inner_text(
                        **({"timeout": timeout_ms} if timeout_ms is not None else {})
                    )
                    if pattern.search(text or ""):
                        matched += 1
                except Exception:
                    continue
            if matched > 1:
                logger.warning(
                    "[BROWSER_EXTRACT] %d element(s) matched selector_filter '%s', "
                    "using the first one (store_as='%s')",
                    matched, selector_filter, store_as,
                )
        except Exception as e:
            logger.warning("[BROWSER_EXTRACT] count check failed: %s", e)

    # Extract value via regex.
    if extract_regex:
        try:
            pattern = re.compile(extract_regex)
        except re.error as e:
            raise ValueError(f"browser_extract: invalid extract_regex: {e}") from e
        m = pattern.search(actual_str)
        if not m:
            raise ValueError(
                f"browser_extract: extract_regex '{extract_regex}' did not match "
                f"text '{actual_str}'"
            )
        try:
            value = m.group(regex_group)
        except IndexError:
            raise ValueError(
                f"browser_extract: regex_group {regex_group} out of range for "
                f"pattern '{extract_regex}' (has {m.lastindex} group(s))"
            )
        if value is None:
            raise ValueError(
                f"browser_extract: capture group {regex_group} matched None "
                f"in pattern '{extract_regex}'"
            )
    else:
        value = actual_str

    variables[store_as] = value
    print(f"  [BROWSER_EXTRACT] stored {store_as}='{value}' (from text='{actual_str[:40]}{'...' if len(actual_str) > 40 else ''}')")


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
    "browser_if": _browser_if,
    "browser_extract": _browser_extract,
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


async def execute_browser_step(
    tools: BrowserTools,
    step: dict,
    execute_sub: Optional[Any] = None,
    variables: Optional[Dict[str, str]] = None,
) -> None:
    """Dispatch a single declarative browser step to BrowserTools.

    The step dict must have an "action" key whose value starts with "browser_".
    Unknown action types raise ValueError.

    Args:
        tools: BrowserTools instance for browser_* actions.
        step: Step dict with "action" field.
        execute_sub: Optional async callback for browser_if's then/else sub-
            steps. When provided, supports both browser_* and desktop actions
            (caller-defined dispatch). When None, browser_if's then/else can
            only contain browser_* actions.
        variables: Optional dict of task-scoped variables for {{name}} placeholder
            substitution. Populated by browser_extract (store_as), consumed by
            subsequent steps referencing {{name}} in any string field. When None,
            no substitution is performed.
    """
    if variables is not None:
        step = _substitute_variables(step, variables)

    action_type = step.get("action", "")
    handler = _ACTION_DISPATCH.get(action_type)
    if handler is None:
        raise ValueError(f"Unsupported browser action: {action_type}")
    print(f"  [BROWSER_RUNTIME] {action_type}: {_summarize_step(step)}")
    if action_type == "browser_if":
        await _browser_if(tools, step, execute_sub=execute_sub, variables=variables)
    elif action_type == "browser_extract":
        await _browser_extract(tools, step, variables=variables)
    else:
        await handler(tools, step)


# ---------------------------------------------------------------------------
# Task-scoped variable substitution (for browser_extract + {{name}} reference)
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def _substitute_variables(step: dict, variables: Dict[str, str]) -> dict:
    """Replace {{name}} placeholders in all string fields of a step dict.

    Unknown placeholders (no matching variable) are left intact so that
    downstream parsing (e.g. {{match_group_1}} from task_decomposer) still works.
    """
    if not variables:
        return step

    def _replace(value):
        if isinstance(value, str):
            def _sub(m):
                key = m.group(1)
                return variables[key] if key in variables else m.group(0)
            return _VAR_RE.sub(_sub, value)
        if isinstance(value, dict):
            return {k: _replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_replace(v) for v in value]
        return value

    return _replace(step)


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
    if action == "browser_if":
        cond = step.get("condition", {})
        op = cond.get("op", "exists")
        if op == "exists":
            return f"{cond.get('selector', '?')} exists?"
        return f"{cond.get('selector', '?')}.{cond.get('field', 'text')} {op} {cond.get('value', '?')}"
    if action == "browser_extract":
        return f"{step.get('selector', '?')} -> {step.get('store_as', '?')}"
    return ""
