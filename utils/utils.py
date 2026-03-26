import ast
import json
import math
import os
import re
import sys
import textwrap
import time
import abc
import base64
import numpy as np
from io import BytesIO
from openai import OpenAI
from typing import Any, Optional

import pyautogui
import pyperclip
from PIL import Image, ImageDraw
from prompt import SYSTEM_PROMPT_310 as SYSTEM_PROMPT

# OpenCV for template matching (optional dependency)
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[WARN] OpenCV (cv2) not installed. Template matching disabled.")
    print("       Install with: pip install opencv-python")

pyautogui.FAILSAFE = False
# ---------------------------------------------------------------------------
# Computer interaction tools
# ---------------------------------------------------------------------------

class ComputerTools:
    """Cross-platform wrapper for desktop GUI automation via pyautogui."""

    def __init__(self):
        self.image_info = None
        self.template_matcher = None

    def _load_image_info(self, path):
        """Cache the width and height of the latest screenshot."""
        width, height = Image.open(path).size
        self.image_info = (width, height)

    # -- screenshot -------------------------------------------------------

    def get_screenshot(self, image_path, retry_times=3):
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

    # -- window management ------------------------------------------------

    def reset(self):
        """Minimize all windows and show the desktop."""
        pyautogui.hotkey("win", "d")

    # -- keyboard actions -------------------------------------------------

    def press_key(self, keys):
        """
        Press one or more keys. If multiple keys are given, they are
        pressed as a hotkey combination.

        Args:
            keys: A single key string or a list of key strings.
        """
        if isinstance(keys, list):
            cleaned = []
            for key in keys:
                if isinstance(key, str):
                    # Strip any wrapper artifacts like "keys=[" or trailing "]"
                    key = key.strip()
                    for prefix in ("keys=[", "['", '["'):
                        if key.startswith(prefix):
                            key = key[len(prefix):]
                    for suffix in ("]", "']", '"]'):
                        if key.endswith(suffix):
                            key = key[: -len(suffix)]
                    key = key.strip()

                    # Normalize arrow key names
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
        """
        Type text by copying to clipboard and pasting.
        This approach supports CJK and special characters.
        """
        pyperclip.copy(text)
        pyautogui.keyDown("ctrl")
        pyautogui.keyDown("v")
        pyautogui.keyUp("v")
        pyautogui.keyUp("ctrl")

    # -- app launching ----------------------------------------------------

    def open_app(self, app_name, wait=0.5):
        """
        Open an application by name using the OS search mechanism.
        Supports Windows, macOS, and Linux.
        """
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
            # Linux — attempt Alt+F2 run dialog
            pyautogui.hotkey("alt", "f2")
            time.sleep(wait)
            pyperclip.copy(app_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
            pyautogui.press("enter")

    # -- mouse actions ----------------------------------------------------

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
        """
        Scroll the mouse wheel.
        Positive values scroll up, negative values scroll down.
        """
        pyautogui.scroll(pixels)

    # -- template matching ------------------------------------------------

    def set_template_dir(self, template_dir):
        """
        设置模板目录并初始化匹配器
        
        Args:
            template_dir: 模板库目录路径
        """
        if CV2_AVAILABLE:
            self.template_matcher = TemplateMatcher(template_dir)
            print(f"[INFO] Template matcher initialized: {template_dir}")
        else:
            print("[WARN] Template matching disabled (OpenCV not available)")

    def find_and_click(self, template_name, screenshot_path=None):
        """
        查找模板并点击（如果找到）
        
        Args:
            template_name: 模板文件名（相对于 template_dir）
            screenshot_path: 截图路径（如为 None 则自动截图）
        
        Returns:
            bool: 是否成功点击
        """
        if self.template_matcher is None:
            print("[ERROR] Template matcher not initialized")
            return False
        
        if screenshot_path is None:
            screenshot_path = "/tmp/template_search.png"
            self.get_screenshot(screenshot_path)
        
        coord = self.template_matcher.find(template_name, screenshot_path)
        if coord:
            print(f"[INFO] Found '{template_name}' at {coord}, clicking...")
            self.left_click(coord[0], coord[1])
            return True
        else:
            print(f"[WARN] Template '{template_name}' not found")
            return False

    def find_template(self, template_name, screenshot_path=None):
        """
        查找模板位置（不点击）
        
        Args:
            template_name: 模板文件名
            screenshot_path: 截图路径（如为 None 则自动截图）
        
        Returns:
            tuple (x, y) or None: 模板中心坐标，未找到返回 None
        """
        if self.template_matcher is None:
            print("[ERROR] Template matcher not initialized")
            return None
        
        if screenshot_path is None:
            screenshot_path = "/tmp/template_search.png"
            self.get_screenshot(screenshot_path)
        
        return self.template_matcher.find(template_name, screenshot_path)


# ---------------------------------------------------------------------------
# Step popup (blocking, with countdown)
# ---------------------------------------------------------------------------

class StepPopup:
    """Topmost popup window for displaying step information."""

    @staticmethod
    def show_blocking(
        title,
        text,
        image_path=None,
        timeout_sec=5,
        width=960,
        height=540,
        pos=None,
        image_ratio=0.55,
    ):
        """
        Show a blocking, always-on-top popup with an image on top
        and scrollable text below.

        Args:
            title:       Window title.
            text:        Body text to display.
            image_path:  Optional path to an image to show.
            timeout_sec: Auto-close after this many seconds.
            width:       Window width in pixels.
            height:      Window height in pixels.
            pos:         (x, y) position tuple, or None for centered.
            image_ratio: Fraction of content area used for the image (0.4–0.75).
        """
        import tkinter as tk
        from PIL import ImageTk

        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.resizable(False, False)

        # Window positioning
        if pos is None:
            root.update_idletasks()
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = int((sw - width) / 2)
            y = int(sh * 0.12)
        else:
            x, y = pos
        root.geometry(f"{width}x{height}+{x}+{y}")

        # Main container
        frm = tk.Frame(root, bg="#1f1f1f")
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # Title label
        lbl_title = tk.Label(
            frm, text=title, bg="#1f1f1f", fg="#ffffff",
            font=("Segoe UI", 12, "bold"), anchor="w",
        )
        lbl_title.pack(fill="x", pady=(0, 6))

        # Compute available heights for image and text areas
        content_h = height - 90
        image_h = max(80, int(content_h * image_ratio))
        text_h = max(60, content_h - image_h)

        # Image area (fixed height, scaled to fit)
        image_frame = tk.Frame(frm, bg="#1f1f1f", height=image_h)
        image_frame.pack(fill="x")
        image_frame.pack_propagate(False)

        img_label = tk.Label(image_frame, bg="#1f1f1f")
        img_label.pack(fill="both", expand=True)

        photo_ref = {"img": None}  # prevent garbage collection

        def render_image():
            if not image_path:
                img_label.config(text="(No image)", fg="#bbbbbb")
                return
            try:
                with Image.open(image_path) as im_src:
                    img = im_src.convert("RGB")
                avail_w = width - 24
                avail_h = image_h - 10
                iw, ih = img.size
                ratio = min(avail_w / iw, avail_h / ih)
                new_w = max(1, int(iw * ratio))
                new_h = max(1, int(ih * ratio))
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img_resized)
                img_label.config(image=photo)
                photo_ref["img"] = photo
            except Exception as e:
                img_label.config(text=f"Image load failed: {e}", fg="#ff6666")

        render_image()

        # Text area (scrollable)
        text_frame = tk.Frame(frm, bg="#1f1f1f", height=text_h)
        text_frame.pack(fill="both", expand=True, pady=(6, 0))
        text_frame.pack_propagate(False)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")
        txt = tk.Text(
            text_frame, wrap="word", bg="#262626", fg="#e8e8e8",
            insertbackground="#e8e8e8", relief="flat",
        )
        txt.pack(side="left", fill="both", expand=True)
        txt.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=txt.yview)

        txt.insert("1.0", text or "")
        txt.config(state="disabled")

        # Bottom bar: countdown + close button
        bottom = tk.Frame(frm, bg="#1f1f1f")
        bottom.pack(fill="x", pady=(6, 0))
        countdown_var = tk.StringVar()

        def close():
            try:
                root.destroy()
            except Exception:
                pass

        def on_key(event):
            if event.keysym in ("Escape", "Return"):
                close()

        root.bind("<Escape>", on_key)
        root.bind("<Return>", on_key)

        lbl_count = tk.Label(
            bottom, textvariable=countdown_var,
            bg="#1f1f1f", fg="#bbbbbb", font=("Segoe UI", 10),
        )
        lbl_count.pack(side="left")

        btn = tk.Button(bottom, text="Close", command=close)
        btn.pack(side="right")

        remaining = [timeout_sec]

        def tick():
            remaining[0] -= 1
            if remaining[0] <= 0:
                close()
            else:
                countdown_var.set(
                    f"Auto-close in {remaining[0]}s (Esc/Enter to dismiss)"
                )
                root.after(1000, tick)

        countdown_var.set(
            f"Auto-close in {timeout_sec}s (Esc/Enter to dismiss)"
        )
        root.after(1000, tick)

        root.mainloop()


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------

def format_step_text(thought, action_list, explanation, max_width=88):
    """Format step details (thought / actions / explanation) for display."""

    def wrap(s):
        if isinstance(s, str):
            return "\n".join(textwrap.wrap(s, width=max_width))
        return str(s)

    parts = [f"Thought:\n{wrap(thought or '')}"]
    parts.append("\nActions:")
    if isinstance(action_list, list):
        for i, a in enumerate(action_list, 1):
            parts.append(f"  {i}. {json.dumps(a, ensure_ascii=False)}")
    else:
        parts.append(f"  {wrap(str(action_list))}")
    parts.append(f"\nExplanation:\n{wrap(explanation or '')}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Smart image resize (Qwen-VL style)
# ---------------------------------------------------------------------------

def smart_resize(
    height, width,
    factor=28,
    min_pixels=56 * 56,
    max_pixels=14 * 14 * 4 * 1280,
    max_long_side=8192,
):
    """
    Rescale dimensions so that:
      1. Both are divisible by *factor*.
      2. Total pixels is within [min_pixels, max_pixels].
      3. Longest side does not exceed *max_long_side*.
      4. Aspect ratio is preserved as closely as possible.
    """

    def _round(n):
        return round(n / factor) * factor

    def _floor(n):
        return math.floor(n / factor) * factor

    def _ceil(n):
        return math.ceil(n / factor) * factor

    if height < 2 or width < 2:
        raise ValueError(
            f"height ({height}) and width ({width}) must be >= 2"
        )
    if max(height, width) / min(height, width) > 200:
        raise ValueError(
            f"Aspect ratio must be < 200, "
            f"got {max(height, width) / min(height, width)}"
        )

    # Clamp longest side
    if max(height, width) > max_long_side:
        beta = max(height, width) / max_long_side
        height = int(height / beta)
        width = int(width / beta)

    h_bar = _round(height)
    w_bar = _round(width)

    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = _floor(height / beta)
        w_bar = _floor(width / beta)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = _ceil(height * beta)
        w_bar = _ceil(width * beta)

    return h_bar, w_bar


# ---------------------------------------------------------------------------
# Screenshot annotation
# ---------------------------------------------------------------------------

def annotate_screenshot(image_path, action_parameter, save_path="screenshot_anno.png"):
    """
    Draw action annotations (click dot / drag arrow) on a screenshot
    and save the result to *save_path*.

    Handles two cases:
      - 'coordinate' only: draws a red dot (click).
      - 'coordinate1' + 'coordinate2': draws a red arrow (drag/swipe).

    Returns the save path on success, or None if no coordinates found.
    """
    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)

    if "coordinate" in action_parameter:
        # Single-point action (click)
        radius = 15
        cx, cy = action_parameter["coordinate"]
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill="red",
            outline="red",
        )
    elif "coordinate1" in action_parameter and "coordinate2" in action_parameter:
        # Two-point action (drag / swipe)
        x1, y1 = action_parameter["coordinate1"]
        x2, y2 = action_parameter["coordinate2"]
        color = "red"
        arrow_size = 10

        # Draw the line
        draw.line((x1, y1, x2, y2), fill=color, width=2)

        # Compute and draw arrowhead
        angle = math.atan2(y2 - y1, x2 - x1)
        ax1 = x2 - arrow_size * math.cos(angle - math.pi / 6)
        ay1 = y2 - arrow_size * math.sin(angle - math.pi / 6)
        ax2 = x2 - arrow_size * math.cos(angle + math.pi / 6)
        ay2 = y2 - arrow_size * math.sin(angle + math.pi / 6)
        draw.polygon([(x2, y2), (ax1, ay1), (ax2, ay2)], fill=color)
    else:
        return None

    image.save(save_path)
    return save_path


# ---------------------------------------------------------------------------
# VLM message construction
# 修改了 left_click 和 double_click, 并加入# Click Action Decision Rules
# 以加强区分单击和双击
# ---------------------------------------------------------------------------


def encode_image_to_base64(image_path):
    """将本地图片转换为 base64 编码"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def build_messages(image_path, instruction, history_output, model_name, history_n=4):
    """
    Construct the multi-turn message list for the VLM.

    Args:
        image_path:      Path to the current screenshot.
        instruction:     The user's task instruction.
        history_output:  List of dicts with keys 'output' and 'image'.
        model_name:      Model identifier (affects history summarization).
        history_n:       Number of recent history turns to include as images.

    Returns:
        A list of message dicts suitable for the DashScope API.
    """
    current_step = len(history_output)
    # history_start_idx = max(0, current_step - history_n)
    history_start_idx = max(0, current_step)
    
    # Summarize early actions (before the image-history window)
    previous_actions = []
    for i in range(history_start_idx):
        if i < len(history_output):
            text = history_output[i]["output"]
            if "Action:" in text and "<tool_call>" in text:
                # 从文本中提取 Action: 和 <tool_call> 之间的内容
                text = text.split("Action:")[1].split("<tool_call>")[0].strip()
            previous_actions.append(f"Step {i + 1}: {text}")

    previous_actions_str = "\n".join(previous_actions) if previous_actions else "None"

    instruction_prompt = (
        "Please generate the next move according to the UI screenshot, "
        "instruction and previous actions.\n\n"
        f"Instruction: {instruction}\n\n"
        f"Previous actions Have finished:\n{previous_actions_str}"
    )

    # Assemble messages
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": SYSTEM_PROMPT}],
        }
    ]

    history_len = min(history_n, len(history_output))
    
    if history_len > 0:
        for idx, item in enumerate(history_output[-history_n:]):
            # 将历史图片转换为 base64
            base64_image = encode_image_to_base64(item["image"])
            
            if idx == 0:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        },
                    ],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ],
                })
            messages.append({
                "role": "assistant",
                "content": [{"type": "text", "text": item["output"]}],
            })
        
        # 当前图片也转换为 base64
        base64_current = encode_image_to_base64(image_path)
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_current}"
                    }
                }
            ],
        })
    else:
        base64_image = encode_image_to_base64(image_path)
        # mime_type = get_image_mime_type(image_path)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": instruction_prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                },
            ],
        })
    
    return messages


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------

def extract_tool_calls(text):
    """
    Extract all JSON objects from <tool_call>...</tool_call> blocks.

    Returns a list of parsed dicts. Blocks that fail to parse are skipped
    with a warning.
    """
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
    blocks = pattern.findall(text)

    actions = []
    for blk in blocks:
        blk = blk.strip()
        try:
            actions.append(ast.literal_eval(blk))
        except (ValueError, SyntaxError) as e:
            print(f"[WARN] Failed to parse tool_call block: {e} | snippet: {blk[:80]}...")
    return actions


def extract_template_request(text: str):
    """
    Extract JSON from <template_match>...</template_match>
    """

    pattern = r"<template_match>(.*?)</template_match>"
    match = re.search(pattern, text, re.DOTALL)

    if not match:
        return None

    try:
        request_json = match.group(1).strip()
        return json.loads(request_json)
    except Exception as e:
        print(f"[TEMPLATE_MATCH] JSON parse error: {e}")
        return None
    
def extract_action(text: str):
    """
    Extract the action from the LLM response.
    """
    pattern = r'Action:.*'
    match = re.search(pattern, text)
    if match:
        result = match.group(0)
        print(result)  # 输出: Action:xxxxx <tool>xxx</tool>
    return result.strip() if match else None
# ---------------------------------------------------------------------------
# Output directory helper
# ---------------------------------------------------------------------------

def get_output_dir(subdir="anno"):
    """
    Create and return an output directory for annotated screenshots.
    Prefers ~/Desktop/<subdir>; falls back to ./<subdir>.
    """
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    base_dir = desktop if os.path.isdir(desktop) else os.getcwd()
    out_dir = os.path.join(base_dir, subdir)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def sanitize_filename(name):
    """Replace non-alphanumeric characters with underscores for safe filenames."""
    return "".join(
        c if c.isalnum() or c in (" ", "_", "-") else "_" for c in name
    ).strip()


ERROR_CALLING_LLM = 'Error calling LLM'

def pil_to_base64(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG") 
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def image_to_base64(image_path):
    dummy_image = Image.open(image_path)
    MIN_PIXELS=3136
    MAX_PIXELS=10035200
    resized_height, resized_width  = smart_resize(dummy_image.height,
        dummy_image.width,
        factor=28,
        min_pixels=MIN_PIXELS,
        max_pixels=MAX_PIXELS,)
    dummy_image = dummy_image.resize((resized_width, resized_height))
    return f"data:image/png;base64,{pil_to_base64(dummy_image)}"

class LlmWrapper(abc.ABC):
    """Abstract interface for (text only) LLM."""
    @abc.abstractmethod
    def predict(
        self,
        text_prompt: str,
    ) -> tuple[str, Optional[bool], Any]:
        """Calling multimodal LLM with a prompt and a list of images.

        Args:
        text_prompt: Text prompt.

        Returns:
        Text output, is_safe, and raw output.
        """

class MultimodalLlmWrapper(abc.ABC):
    """Abstract interface for Multimodal LLM."""
    @abc.abstractmethod
    def predict_mm(
        self, text_prompt: str, images: list[np.ndarray]
    ) -> tuple[str, Optional[bool], Any]:
        """Calling multimodal LLM with a prompt and a list of images.

        Args:
        text_prompt: Text prompt.
        images: List of images as numpy ndarray.

        Returns:
        Text output and raw output.
        """

class GUIOwlWrapper(LlmWrapper, MultimodalLlmWrapper):

    RETRY_WAITING_SECONDS = 20

    def __init__(
            self,
            api_key: str,
            base_url: str,
            model_name: str,
            max_retry: int = 10,
            temperature: float = 0.0,
    ):
        if max_retry <= 0:
            max_retry = 10
            print('Max_retry must be positive. Reset it to 3')
        self.max_retry = min(max_retry, 10)
        self.temperature = temperature
        self.model = model_name
        self.bot = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=30
        )

    def convert_messages_format_to_openaiurl(self, messages):
      converted_messages = []
      for message in messages:
          new_content = []
          for item in message['content']:
              if list(item.keys())[0] == 'text':
                  new_content.append({'type': 'text', 'text': item['text']})
              elif list(item.keys())[0] == 'image':
                new_content.append({'type': 'image_url', 'image_url': {'url': image_to_base64(item['image'])}})
          converted_messages.append({'role': message['role'], 'content': new_content})

      return converted_messages
    
    def predict(
            self,
            text_prompt: str,
    ) -> tuple[str, Optional[bool], Any]:
        return self.predict_mm(text_prompt, [])

    def predict_mm(
            self, messages = None
    ) -> tuple[str, Optional[bool], Any]:
        
        payload = messages
        payload = self.convert_messages_format_to_openaiurl(payload)

        counter = self.max_retry
        wait_seconds = self.RETRY_WAITING_SECONDS
        while counter > 0:
            try:
              chat_completion_from_url = self.bot.chat.completions.create(model=self.model, messages=payload, **{})
              return (chat_completion_from_url.choices[0].message.content, payload, chat_completion_from_url)
            except Exception as e:
                time.sleep(wait_seconds)
                wait_seconds *= 1
                counter -= 1
                print('Error calling LLM, will retry soon...')
                print(e)
        return ERROR_CALLING_LLM, None, None

def process_markdown_task(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines:
        raise ValueError("文件为空")

    # 1. 提取第一行作为标题
    first_line = lines[0].strip()
    
    # 清理标题：去除开头的 '#' 和空格，例如 "## 任务名称" -> "任务名称"
    # 使用正则去除开头所有的 # 和紧随的空格
    task_title = re.sub(r'^#+\s*', '', first_line).strip()
    
    #  sanitization: 文件名不能包含非法字符 (如 / \ : * ? " < > |)
    safe_filename = re.sub(r'[\\/:*?"<>|]', '_', task_title)

    # 2. 提取剩余内容作为任务指令
    # 从索引 1 开始截取，并过滤掉可能存在的空行（可选）
    task_content_lines = lines[1:]
    
    # 可选：如果第二行是空行，也去掉，让指令更紧凑
    if task_content_lines and task_content_lines[0].strip() == "":
        task_content_lines = task_content_lines[1:]
        
    task_prompt = "".join(task_content_lines)

    return {
        "original_file": file_path,
        "extracted_title": safe_filename,
        "prompt_for_llm": task_prompt
    }

# ============================================================================
# Template Matching
# ============================================================================

class TemplateMatcher:
    """
    模板匹配器：在截图中查找预定义的 UI 元素模板
    
    使用方法:
        matcher = TemplateMatcher("./templates")
        coord = matcher.find("buttons/close.png", "screenshot.png")
        if coord:
            print(f"找到元素，坐标：{coord}")
    """
    
    def __init__(self, template_dir="./templates", threshold=0.8):
        """
        Args:
            template_dir: 模板库目录
            threshold: 匹配置信度阈值 (0-1)，越高越严格
        """
        self.template_dir = template_dir
        self.threshold = threshold
        self.template_cache = {}  # 缓存已加载的模板
    
    def _load_template(self, template_name):
        """加载模板图（带缓存）"""
        if template_name in self.template_cache:
            return self.template_cache[template_name]
        
        template_path = os.path.join(self.template_dir, template_name)
        if not os.path.exists(template_path):
            print(f"[WARN] Template file does not exist: {template_path}")
            return None
        template_path = template_path.encode('gbk')
        template = cv2.imread(template_path)
        if template is not None:
            self.template_cache[template_name] = template
        return template
    
    def find(self, template_name, screenshot_path, multiple=False):
        """
        在截图中查找匹配的模板
        
        Args:
            template_name: 模板文件名（相对于 template_dir）
            screenshot_path: 截图路径
            multiple: 是否返回所有匹配结果（默认只返回最匹配的一个）
        
        Returns:
            单个坐标 (x, y) 或 坐标列表 [(x, y), ...]，未找到返回 None
        """
        if not CV2_AVAILABLE:
            print("[ERROR] OpenCV not available")
            return None
        
        template = self._load_template(template_name)
        if template is None:
            print(f"[WARN] Template not found: {template_name}")
            return None
        screenshot_path = screenshot_path.encode('gbk') 
        screenshot = cv2.imread(screenshot_path)
        if screenshot is None:
            print(f"[ERROR] Cannot load screenshot: {screenshot_path}")
            return None
        
        # 模板匹配
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        if max_val < self.threshold:
            return None
        
        h, w = template.shape[:2]
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2
        
        if multiple:
            # 查找所有匹配点
            locations = np.where(result >= self.threshold)
            points = list(zip(*locations[::-1]))
            # 去重（合并邻近点）
            unique_points = self._deduplicate_points(points, min_distance=w//2)
            return [(p[0] + w//2, p[1] + h//2) for p in unique_points]
        else:
            return (center_x, center_y)
    
    def find_all(self, screenshot_path):
        """
        在截图中查找所有已注册的模板
        
        Args:
            screenshot_path: 截图路径
        
        Returns:
            dict: {模板名：坐标}，只包含找到的
        """
        results = {}
        
        # 扫描模板目录
        for root, dirs, files in os.walk(self.template_dir):
            for file in files:
                if file.endswith('.png'):
                    rel_path = os.path.relpath(os.path.join(root, file), self.template_dir)
                    print(f"[INFO] Searching for template: {rel_path}")
                    coord = self.find(rel_path, screenshot_path)
                    if coord:
                        results[rel_path] = coord
        
        return results
    
    def _deduplicate_points(self, points, min_distance=10):
        """去重：合并距离过近的点"""
        if not points:
            return []
        
        unique = []
        for point in points:
            is_duplicate = False
            for existing in unique:
                dist = math.sqrt((point[0]-existing[0])**2 + (point[1]-existing[1])**2)
                if dist < min_distance:
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique.append(point)
        
        return unique
    
    def register_template(self, name, screenshot_path, x, y, w, h):
        """
        从截图中裁剪并注册新模板
        
        Args:
            name: 模板文件名（如 "buttons/close.png"）
            screenshot_path: 源截图路径
            x, y: 裁剪区域左上角坐标
            w, h: 裁剪区域宽高
        """
        if not CV2_AVAILABLE:
            return False
        screenshot_path = screenshot_path.encode('gbk') 
        screenshot = cv2.imread(screenshot_path)
        if screenshot is None:
            return False
        
        template = screenshot[y:y+h, x:x+w]
        output_path = os.path.join(self.template_dir, name)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        cv2.imwrite(output_path, template)
        
        # 清除缓存（下次重新加载）
        if name in self.template_cache:
            del self.template_cache[name]
        
        print(f"[INFO] Template registered: {name}")
        return True
    
    def list_templates(self):
        """列出所有可用模板"""
        templates = []
        for root, dirs, files in os.walk(self.template_dir):
            for file in files:
                if file.endswith('.png'):
                    rel_path = os.path.relpath(os.path.join(root, file), self.template_dir)
                    templates.append(rel_path)
        return templates
