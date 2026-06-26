"""
Persistent browser process manager.

Launches a Chromium browser with --remote-debugging-port at server startup
so that Playwright tasks can connect via CDP without cold-launch overhead.
The browser survives across tasks and is torn down on server shutdown.
"""

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_HOST = "127.0.0.1"

# Places to look for a Chromium executable, in order of preference.
_CHROMIUM_CANDIDATES = [
    # Playwright-installed chromium (Windows)
    os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-*"),
    # Google Chrome (Windows)
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    # Microsoft Edge (Windows)
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Linux / macOS
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/snap/bin/chromium",
]


def _resolve_playwright_chromium() -> Optional[str]:
    """Walk the Playwright cache to find the most recent chromium binary."""
    base = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    if not os.path.isdir(base):
        base = os.path.expanduser("~/Library/Caches/ms-playwright")
    if not os.path.isdir(base):
        base = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(base):
        return None
    best = None
    best_ver = (0,)
    for name in os.listdir(base):
        if name.startswith("chromium-") or name.startswith("chromium_headless_"):
            ver_str = name.split("-", 1)[-1] if "-" in name else name.split("_", 2)[-1]
            try:
                ver = tuple(int(x) for x in ver_str.split("."))
            except ValueError:
                ver = (0,)
            if ver > best_ver:
                best_ver = ver
                if os.name == "nt":
                    candidate = os.path.join(base, name, "chrome-win", "chrome.exe")
                else:
                    candidate = os.path.join(base, name, "chrome-linux", "chrome")
                if os.path.isfile(candidate):
                    best = candidate
    return best


def find_chromium() -> str:
    """Locate a usable Chromium/Chrome executable on this machine."""
    # 1. Playwright cache (most reliable for our use case)
    pw = _resolve_playwright_chromium()
    if pw:
        return pw

    # 2. PATH lookup
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "chrome", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found

    # 3. Hard-coded candidate paths
    for candidate in _CHROMIUM_CANDIDATES:
        if "*" in candidate:
            continue  # glob not expanded here
        if os.path.isfile(candidate):
            return candidate

    raise FileNotFoundError(
        "No Chromium/Chrome executable found. "
        "Install Playwright browsers: playwright install chromium"
    )


class BrowserManager:
    """Manages a persistent Chromium browser process with remote debugging enabled."""

    def __init__(self, port: int = DEFAULT_CDP_PORT, host: str = DEFAULT_CDP_HOST):
        self.port = port
        self.host = host
        self._process: Optional[subprocess.Popen] = None

    @property
    def cdp_endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, headless: bool = False, user_data_dir: Optional[str] = None) -> None:
        """Launch Chromium with remote debugging enabled.

        Blocks until the CDP endpoint is accepting connections.
        """
        if self._process is not None:
            logger.info("BrowserManager: browser already running")
            return

        exe = find_chromium()
        args = [
            exe,
            f"--remote-debugging-port={self.port}",
            f"--remote-debugging-address={self.host}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-features=TranslateUI,DialMediaRouteProvider",
        ]
        if headless:
            args.append("--headless=new")
        if user_data_dir:
            args.append(f"--user-data-dir={user_data_dir}")
        else:
            # Use a temp dir so the persistent browser doesn't pollute user profiles
            import tempfile
            user_data_dir = os.path.join(tempfile.gettempdir(), "gui_agent_browser_profile")
            os.makedirs(user_data_dir, exist_ok=True)
            args.append(f"--user-data-dir={user_data_dir}")
            logger.info("BrowserManager: using profile dir %s", user_data_dir)

        logger.info("BrowserManager: launching %s", " ".join(args))
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        # Wait for the CDP endpoint to become available (up to 30s)
        self._wait_for_cdp(timeout_s=30)
        logger.info("BrowserManager: CDP endpoint ready at %s", self.cdp_endpoint)

    def _wait_for_cdp(self, timeout_s: float = 30) -> None:
        """Poll the CDP endpoint until it responds or timeout."""
        import urllib.request
        import urllib.error

        deadline = time.time() + timeout_s
        url = f"http://{self.host}:{self.port}/json/version"
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=2)
                return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise TimeoutError(
            f"Browser CDP endpoint {self.cdp_endpoint} not reachable after {timeout_s}s"
        )

    def stop(self) -> None:
        """Terminate the browser process."""
        if self._process is None:
            return
        logger.info("BrowserManager: stopping browser (pid=%s)", self._process.pid)
        try:
            self._process.terminate()
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("BrowserManager: kill browser (pid=%s)", self._process.pid)
            self._process.kill()
            self._process.wait(timeout=5)
        except Exception as exc:
            logger.warning("BrowserManager: error stopping browser: %s", exc)
        self._process = None
