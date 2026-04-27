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
# ---------------------------------------------------------------------------


def encode_image_to_base64(image_path):
    """Convert local image to base64 encoding."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def build_messages(image_path, instruction, history_output, model_name, history_n=4, image_url=None):
    """
    Construct the multi-turn message list for the VLM.

    Args:
        image_path:      Path to the current screenshot (fallback if image_url not provided).
        instruction:     The user's task instruction.
        history_output:  List of dicts with keys 'output', 'image', and optionally 'image_url'.
        model_name:      Model identifier (affects history summarization).
        history_n:       Number of recent history turns to include as images.
        image_url:       HTTP URL for the current screenshot (preferred over base64).

    Returns:
        A list of message dicts suitable for the OpenAI API.
    """
    current_step = len(history_output)
    history_start_idx = max(0, current_step - history_n)

    # Summarize early actions (before the image-history window)
    previous_actions = []
    for i in range(history_start_idx):
        if i < len(history_output):
            text = history_output[i]["output"]
            if "Action:" in text and "ॽ" in text:
                text = text.split("Action:")[1].split("ॽ")[0].strip()
            previous_actions.append(f"Step {i + 1}: {text}")

    previous_actions_str = "\n".join(previous_actions) if previous_actions else "None"

    instruction_prompt = (
        "Please generate the next move according to the UI screenshot, "
        "instruction and previous actions.\n\n"
        f"Instruction: {instruction}\n\n"
        f"Previous actions Have finished:\n{previous_actions_str}"
    )

    # Helper: get image content (URL or base64)
    def get_image_content(item_image_path, item_image_url=None):
        """Return image_url dict, preferring URL over base64."""
        if item_image_url:
            return {
                "type": "image_url",
                "image_url": {"url": item_image_url}
            }
        else:
            print(f"[WARN] No image URL provided, using base64 encoding for {item_image_path}")
            base64_image = encode_image_to_base64(item_image_path)
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            }

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
            # Get history image content (prefer URL)
            item_image_url = item.get("image_url")
            item_image_path = item.get("image")
            image_content = get_image_content(item_image_path, item_image_url)

            if idx == 0:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction_prompt},
                        image_content,
                    ],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": [image_content],
                })
            messages.append({
                "role": "assistant",
                "content": [{"type": "text", "text": item["output"]}],
            })

        # Current image (prefer image_url)
        current_image_content = get_image_content(image_path, image_url)
        messages.append({
            "role": "user",
            "content": [current_image_content],
        })
    else:
        # No history, send current image directly
        current_image_content = get_image_content(image_path, image_url)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": instruction_prompt},
                current_image_content,
            ],
        })

    return messages


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------

def extract_tool_calls(text):
    """
    Extract all JSON objects from alsex...alsex blocks.

    Returns a list of parsed dicts. Blocks that fail to parse are skipped
    with a warning.
    """
    pattern = re.compile(r"alsex(.*?)alsex", re.DOTALL | re.IGNORECASE)
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
        print(result)
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
        raise ValueError("File is empty")

    # 1. Extract first line as title
    first_line = lines[0].strip()

    # Clean title: remove leading '#' and spaces
    task_title = re.sub(r'^#+\s*', '', first_line).strip()

    # Sanitize: filename cannot contain illegal characters
    safe_filename = re.sub(r'[\\/:*?"<>|]', '_', task_title)

    # 2. Extract remaining content as task instruction
    task_content_lines = lines[1:]

    # Optional: remove leading empty line
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
    Template matcher: find predefined UI element templates in screenshots

    Usage:
        matcher = TemplateMatcher("./templates")
        coord = matcher.find("buttons/close.png", "screenshot.png")
        if coord:
            print(f"Found element at: {coord}")
    """

    def __init__(self, template_dir="./templates", threshold=0.8):
        """
        Args:
            template_dir: Template library directory
            threshold: Match confidence threshold (0-1), higher is stricter
        """
        self.template_dir = template_dir
        self.threshold = threshold
        self.template_cache = {}

    def _load_template(self, template_name):
        """Load template image (with cache)"""
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
        Find matching template in screenshot

        Args:
            template_name: Template file name (relative to template_dir)
            screenshot_path: Screenshot path
            multiple: Return all matches (default: only best match)

        Returns:
            Single coordinate (x, y) or list [(x, y), ...], None if not found
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

        # Template matching
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val < self.threshold:
            return None

        h, w = template.shape[:2]
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2

        if multiple:
            # Find all matching points
            locations = np.where(result >= self.threshold)
            points = list(zip(*locations[::-1]))
            # Deduplicate (merge nearby points)
            unique_points = self._deduplicate_points(points, min_distance=w//2)
            return [(p[0] + w//2, p[1] + h//2) for p in unique_points]
        else:
            return (center_x, center_y)

    def find_all(self, screenshot_path):
        """
        Find all registered templates in screenshot

        Args:
            screenshot_path: Screenshot path

        Returns:
            dict: {template_name: coordinate}, only found ones
        """
        results = {}

        # Scan template directory
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
        """Deduplicate: merge points too close together"""
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
        Crop and register new template from screenshot

        Args:
            name: Template file name (e.g. "buttons/close.png")
            screenshot_path: Source screenshot path
            x, y: Crop region top-left coordinate
            w, h: Crop region width and height
        """
        if not CV2_AVAILABLE:
            return False
        screenshot_path = screenshot_path.encode('gbk')
        screenshot = cv2.imread(screenshot_path)
        if screenshot is None:
            return False

        template = screenshot[y:y+h, x:x+w]
        output_path = os.path.join(self.template_dir, name)

        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cv2.imwrite(output_path, template)

        # Clear cache (reload next time)
        if name in self.template_cache:
            del self.template_cache[name]

        print(f"[INFO] Template registered: {name}")
        return True

    def list_templates(self):
        """List all available templates"""
        templates = []
        for root, dirs, files in os.walk(self.template_dir):
            for file in files:
                if file.endswith('.png'):
                    rel_path = os.path.relpath(os.path.join(root, file), self.template_dir)
                    templates.append(rel_path)
        return templates