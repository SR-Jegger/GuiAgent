"""
Image utilities - Screenshot annotation, smart resizing.
"""

import math
from PIL import Image, ImageDraw


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
        raise ValueError(f"height ({height}) and width ({width}) must be >= 2")
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


def annotate_screenshot(image_path, action_parameter, save_path="screenshot_anno.png"):
    """
    Draw action annotations (click dot / drag arrow) on a screenshot.

    Handles two cases:
    - 'coordinate' only: draws a red dot (click).
    - 'coordinate1' + 'coordinate2': draws a red arrow (drag/swipe).
    """
    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)

    if "coordinate" in action_parameter:
        radius = 15
        cx, cy = action_parameter["coordinate"]
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill="red",
            outline="red",
        )
    elif "coordinate1" in action_parameter and "coordinate2" in action_parameter:
        x1, y1 = action_parameter["coordinate1"]
        x2, y2 = action_parameter["coordinate2"]
        color = "red"
        arrow_size = 10

        draw.line((x1, y1, x2, y2), fill=color, width=2)

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
