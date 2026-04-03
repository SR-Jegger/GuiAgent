"""
Application context detection utilities.

Provides functions to detect the current active window and application context.
Used by the operation logger to capture context for skill learning.
"""

import os
import subprocess
from typing import Optional


def get_active_window_info() -> dict:
    """
    Get information about the currently active window.

    Cross-platform support: Windows, macOS, Linux (X11)

    Returns:
        Dict with window title, process name, etc.
        Empty dict if unable to determine.
    """
    info = {}

    # Try pywinctl first (cross-platform: Windows, macOS, Linux-X11)
    try:
        import pywinctl

        window = pywinctl.getActiveWindow()
        if window:
            info["window_title"] = window.title
            info["active_window"] = window.title

            # Try to get process info
            try:
                # Get process ID (cross-platform method)
                pid = getattr(window, 'processId', None) or getattr(window, 'pid', None)
                if pid:
                    info["process_id"] = pid
                    process_name = get_process_name(pid)
                    if process_name:
                        info["process_name"] = process_name
            except Exception:
                pass

            return info
    except ImportError:
        print("[AppContext] pywinctl not installed, window detection unavailable")
    except Exception as e:
        # Linux without X11 will fail here
        print(f"[AppContext] Could not get active window: {e}")

    return info


def get_process_name(pid: int) -> Optional[str]:
    """
    Get the process name from a process ID.

    Args:
        pid: Process ID

    Returns:
        Process name or None if not found
    """
    try:
        if os.name == "nt":
            import psutil

            process = psutil.Process(pid)
            return process.name()
    except ImportError:
        pass
    except Exception:
        pass

    return None


def get_window_title() -> Optional[str]:
    """
    Get the title of the currently active window.

    Returns:
        Window title or None if unable to determine
    """
    info = get_active_window_info()
    return info.get("window_title")


def detect_app_type(window_title: str = None) -> str:
    """
    Detect the type of application from window title.

    This is a simple heuristic to categorize applications.

    Args:
        window_title: Window title (if None, will detect automatically)

    Returns:
        Application type string (e.g., "browser", "excel", "unknown")
    """
    if window_title is None:
        info = get_active_window_info()
        window_title = info.get("window_title", "")

    window_lower = window_title.lower()

    # Browser detection
    browser_keywords = ["chrome", "firefox", "edge", "safari", "browser"]
    if any(kw in window_lower for kw in browser_keywords):
        return "browser"

    # Office apps
    if "excel" in window_lower:
        return "excel"
    if "word" in window_lower:
        return "word"
    if "powerpoint" in window_lower:
        return "powerpoint"
    if "outlook" in window_lower:
        return "outlook"

    # Communication apps
    if "wechat" in window_lower or "微信" in window_title:
        return "wechat"
    if "qq" in window_lower:
        return "qq"
    if "slack" in window_lower:
        return "slack"
    if "teams" in window_lower:
        return "teams"

    # Development tools
    if "visual studio" in window_lower or "vscode" in window_lower:
        return "ide"
    if "pycharm" in window_lower:
        return "pycharm"

    return "unknown"


# For testing
if __name__ == "__main__":
    print("Active Window Info:")
    info = get_active_window_info()
    for key, value in info.items():
        print(f"  {key}: {value}")

    print(f"\nApp Type: {detect_app_type()}")