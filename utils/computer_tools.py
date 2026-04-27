"""
Computer interaction tools - Desktop GUI automation via pyautogui.
"""

import os
import sys
import time
import uuid
import pyautogui
import pyperclip
from PIL import Image
from pathlib import Path
pyautogui.FAILSAFE = False


class ComputerTools:
    """Cross-platform wrapper for desktop GUI automation via pyautogui."""

    def __init__(self):
           
        self.image_info = None
        self.template_matcher = None

    def _load_image_info(self, path):
        """Cache the width and height of the latest screenshot."""
        width, height = Image.open(path).size
        self.image_info = (width, height)

    def get_screenshot(self, image_path=None, retry_times=3):
        """
        Capture screenshot, save compressed JPEG, return image HTTP URL.
        """

        if os.path.exists(image_path):
            os.remove(image_path)

        os.makedirs(os.path.dirname(image_path), exist_ok=True)

        for _ in range(retry_times):
            screenshot = pyautogui.screenshot()

            # 压缩，降低 VLM 视觉编码开销
            img = screenshot.convert("RGB")
            # img.thumbnail((self.max_size, self.max_size))

            img.save(
                image_path,
                format="JPEG",
                # quality=self.jpeg_quality,
                optimize=True,
            )

            if os.path.exists(image_path):
                self._load_image_info(str(image_path))
                return True

            time.sleep(0.1)
        return False
    
    def get_screenshot_origin(self, image_path, retry_times=3):
        """
        Capture a desktop screenshot and save to *image_path*.
        Returns True on success, False after exhausting retries.
        """
        if os.path.exists(image_path):
            os.remove(image_path)

        for _ in range(retry_times):
            screenshot = pyautogui.screenshot()
            screenshot.save(image_path)
            if os.path.exists(image_path):
                self._load_image_info(image_path)
                return True
            time.sleep(0.1)
        return False

    def reset(self):
        """Minimize all windows and show the desktop."""
        pyautogui.hotkey("win", "d")

    def press_key(self, keys):
        """
        Press one or more keys. If multiple keys are given, they are
        pressed as a hotkey combination.
        """
        if isinstance(keys, list):
            cleaned = []
            for key in keys:
                if isinstance(key, str):
                    key = key.strip()
                    for prefix in ("keys=[", "['", '["'):
                        if key.startswith(prefix):
                            key = key[len(prefix):]
                    for suffix in ("]", "']", '"]'):
                        if key.endswith(suffix):
                            key = key[: -len(suffix)]
                    key = key.strip()

                    arrow_map = {
                        "arrowleft": "left",
                        "arrowright": "right",
                        "arrowup": "up",
                        "arrowdown": "down",
                        "print_screen": "printscreen",
                    }
                    key = arrow_map.get(key, key)
                    cleaned.append(key)
                else:
                    cleaned.append(key)
            keys = cleaned
        else:
            keys = [keys]

        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(keys[0])

    def type(self, text):
        """Type text by copying to clipboard and pasting."""
        pyperclip.copy(text)
        pyautogui.keyDown("ctrl")
        pyautogui.keyDown("v")
        pyautogui.keyUp("v")
        pyautogui.keyUp("ctrl")

    def open_app(self, app_name, wait=0.5):
        """Open an application by name using the OS search mechanism."""
        if app_name == "File Explorer":
            app_name = "文件资源管理器"

        if sys.platform == "win32":
            pyautogui.hotkey("winleft", "s")
            time.sleep(wait)
            pyperclip.copy(app_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
            pyautogui.press("enter")
            time.sleep(0.5)
        elif sys.platform == "darwin":
            pyautogui.hotkey("command", "space")
            time.sleep(wait)
            pyperclip.copy(app_name)
            pyautogui.hotkey("command", "v")
            time.sleep(0.3)
            pyautogui.press("enter")
        else:
            pyautogui.hotkey("alt", "f2")
            time.sleep(wait)
            pyperclip.copy(app_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
            pyautogui.press("enter")

    def mouse_move(self, x, y):
        """Move the mouse cursor to absolute coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.moveTo(x, y)

    def left_click(self, x, y):
        """Left-click at coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.click()

    def left_click_drag(self, x, y):
        """Click and drag from the current position to (x, y)."""
        pyautogui.dragTo(x, y, duration=0.5)
        pyautogui.moveTo(x, y)

    def right_click(self, x, y):
        """Right-click at coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.rightClick()

    def middle_click(self, x, y):
        """Middle-click at coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.middleClick()

    def double_click(self, x, y):
        """Double-click at coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.doubleClick()

    def triple_click(self, x, y):
        """Triple-click at coordinate (x, y)."""
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.tripleClick()

    def scroll(self, pixels):
        """Scroll the mouse wheel. Positive=up, Negative=down."""
        pyautogui.scroll(pixels)

