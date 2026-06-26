"""
Browser automation support package (Playwright-based).

Provides DOM extraction utilities that turn a live web page into an indexed
list of interactable elements, so the agent can pick elements by number
instead of guessing pixel coordinates.
"""

from browser.dom_extractor import (
    EXTRACT_INTERACTABLES_JS,
    AGENT_ID_ATTR,
    format_elements_for_llm,
)

__all__ = [
    "EXTRACT_INTERACTABLES_JS",
    "AGENT_ID_ATTR",
    "format_elements_for_llm",
]
