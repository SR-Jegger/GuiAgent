"""
Phase 0 DOM probe.

Opens a target URL in a Playwright browser and dumps the indexed list of
interactable elements the agent would see. Use this to decide the selector
strategy for fixed internal sites (the "selector stability unknown" question).

Usage:
    python -m scripts.browser_probe --url https://portal.example.com
    python -m scripts.browser_probe --url https://portal.example.com \
        --storage-state data/browser/storage_state.json   # logged-in session
    python -m scripts.browser_probe --url http://localhost:3000 \
        --user-data-dir data/browser/profile               # persistent profile

Notes:
- For login-walled pages, first establish a session (run the login skill, or log
  in manually with --user-data-dir) so the probe sees the authenticated page.
- Output is printed and also written to data/browser/probe_<host>.json.
"""

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse

from utils.browser_tools import BrowserTools
from browser.dom_extractor import format_elements_for_llm


async def probe(url: str, storage_state: str | None, user_data_dir: str | None, headless: bool) -> None:
    tools = BrowserTools(
        headless=headless,
        user_data_dir=user_data_dir,
        storage_state_path=storage_state,
    )
    await tools.start()
    try:
        await tools.goto(url)
        await tools.wait_for_idle()
        elements = await tools.snapshot()

        print(f"\n=== {len(elements)} interactable elements on {url} ===\n")
        print(format_elements_for_llm(elements, max_elements=500))

        host = urlparse(url).netloc.replace(":", "_") or "page"
        out_path = os.path.join("data", "browser", f"probe_{host}.json")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(elements, f, ensure_ascii=False, indent=2)
        print(f"\n[probe] Full element metadata written to: {out_path}")
    finally:
        await tools.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a page's interactable DOM elements")
    parser.add_argument("--url", required=True, help="Target page URL")
    parser.add_argument("--storage-state", default=None, help="Playwright storageState JSON path")
    parser.add_argument("--user-data-dir", default=None, help="Persistent browser profile dir")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()

    asyncio.run(probe(args.url, args.storage_state, args.user_data_dir, args.headless))


if __name__ == "__main__":
    main()
