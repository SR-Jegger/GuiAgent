"""
Test script for debugging OCR issues.
Uses a single predict() call for all tests to ensure consistent data.

Usage:
    python scripts/test_ocr.py [--screenshot PATH]
"""
import os
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 禁用模型源检查
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

import numpy as np
import pyautogui
from utils.ocr_locator import OCRLocator


def capture_screenshot(save_path: str = None) -> np.ndarray:
    """截取当前屏幕并保存（可选）"""
    screenshot = pyautogui.screenshot()
    arr = np.array(screenshot)

    if save_path:
        from PIL import Image
        Image.fromarray(arr).save(save_path)
        print(f"Screenshot saved to: {save_path}")

    # 转换为 BGR 格式（PaddleOCR 期望的格式）
    return arr[:, :, ::-1].copy()


def test_ocr(target_text: str = "F35-301"):
    """测试 OCR 识别"""
    print("=" * 60)
    print("OCR Debug Test")
    print("=" * 60)

    # 1. 截图
    print("\n[1] Capturing screenshot...")
    screenshot_bgr = capture_screenshot(save_path=str(PROJECT_ROOT / "test_screenshot.png"))
    print(f"    Screenshot shape: {screenshot_bgr.shape}")

    # 2. 初始化 OCR
    print("\n[2] Initializing OCRLocator...")
    locator = OCRLocator()

    # 3. 获取所有识别结果 - 只调用一次 predict()
    print("\n[3] Running OCR on full screenshot...")
    result_list = locator.ocr.predict(screenshot_bgr)

    if not result_list or len(result_list) == 0:
        print("    No results from predict()")
        return [], None

    result = result_list[0]
    rec_texts = result.get('rec_texts', [])
    rec_polys = result.get('rec_polys', [])
    dt_polys = result.get('dt_polys', [])
    rec_scores = result.get('rec_scores', [])

    print(f"    rec_texts count: {len(rec_texts)}")
    print(f"    rec_polys count: {len(rec_polys)}")
    print(f"    dt_polys count: {len(dt_polys)}")

    # 使用 rec_polys（识别多边形）构建结果列表
    all_text = []
    for i, (text, box, score) in enumerate(zip(rec_texts, rec_polys, rec_scores)):
        x = int(box[:, 0].min())
        y = int(box[:, 1].min())
        w = int(box[:, 0].max() - box[:, 0].min())
        h = int(box[:, 1].max() - box[:, 1].min())
        all_text.append((text, (x, y, w, h), float(score)))

    # 查找包含目标文本的结果
    print(f"\n    Searching for '{target_text}':")
    for i, (text, box, score) in enumerate(zip(rec_texts, rec_polys, rec_scores)):
        # 跳过空字符串或太短的文本
        if not text or not text.strip() or len(text.strip()) < 2:
            continue
        # 使用更严格的匹配逻辑
        text_lower = text.lower().strip()
        target_lower = target_text.lower().strip()
        # 至少 3 个字符才进行子串匹配
        if (len(target_lower) >= 3 and target_lower in text_lower) or \
           (len(text_lower) >= 3 and text_lower in target_lower):
            x_min, x_max = box[:, 0].min(), box[:, 0].max()
            y_min, y_max = box[:, 1].min(), box[:, 1].max()
            center_x = int((x_min + x_max) / 2)
            center_y = int((y_min + y_max) / 2)
            print(f"      [{i}] MATCH: '{text}' @ ({center_x},{center_y}) score={score:.2f}")

    # 显示前 20 个识别结果
    print(f"\n    First 20 text regions (using rec_polys):")
    print("-" * 60)
    for i, (text, bbox, conf) in enumerate(all_text[:20]):
        x, y, w, h = bbox
        print(f"    [{i}] '{text}' @ ({x},{y}) [{w}x{h}] conf={conf:.2f}")
    print("-" * 60)

    # 4. 使用 locate_element 定位
    print(f"\n[4] Trying to locate '{target_text}' with locate_element()...")
    coord = locator.locate_element(target_text, screenshot_bgr)

    if coord:
        print(f"    [OK] Found at: {coord}")
    else:
        print(f"    [FAIL] NOT FOUND")

        # 手动检查匹配
        print("\n    Manual matching check:")
        for i, (text, bbox, conf) in enumerate(all_text):
            if target_text.lower() in text.lower() or text.lower() in target_text.lower():
                x, y, w, h = bbox
                center_x = x + w // 2
                center_y = y + h // 2
                print(f"      [{i}] '{text}' @ ({center_x},{center_y})")

    # 5. 诊断信息
    print("\n[5] Diagnostics:")
    model_dir = PROJECT_ROOT / "models" / "paddleocr"
    if model_dir.exists():
        print(f"    [OK] Local model directory exists: {model_dir}")
    else:
        print(f"    [FAIL] Model directory NOT found: {model_dir}")

    return all_text, coord


def test_from_screenshot(screenshot_path: str, target_text: str = "F35-301"):
    """从已有截图文件测试 OCR"""
    print("=" * 60)
    print("OCR Debug Test (from file)")
    print("=" * 60)

    from PIL import Image

    # 1. 加载截图
    print(f"\n[1] Loading screenshot: {screenshot_path}")
    img = Image.open(screenshot_path)
    screenshot_bgr = np.array(img)[:, :, ::-1].copy()
    print(f"    Image shape: {screenshot_bgr.shape}")

    # 2. 初始化 OCR
    print("\n[2] Initializing OCRLocator...")
    locator = OCRLocator()

    # 3. 获取所有识别结果
    print("\n[3] Running OCR on screenshot...")
    result_list = locator.ocr.predict(screenshot_bgr)

    if not result_list or len(result_list) == 0:
        print("    No results from predict()")
        return [], None

    result = result_list[0]
    rec_texts = result.get('rec_texts', [])
    rec_polys = result.get('rec_polys', [])
    rec_scores = result.get('rec_scores', [])

    print(f"    rec_texts count: {len(rec_texts)}")

    # 使用 rec_polys 构建结果
    all_text = []
    for i, (text, box, score) in enumerate(zip(rec_texts, rec_polys, rec_scores)):
        x = int(box[:, 0].min())
        y = int(box[:, 1].min())
        w = int(box[:, 0].max() - box[:, 0].min())
        h = int(box[:, 1].max() - box[:, 1].min())
        all_text.append((text, (x, y, w, h), float(score)))

    print(f"\n    Found {len(all_text)} text regions:")
    print("-" * 60)
    for i, (text, bbox, conf) in enumerate(all_text[:50]):
        x, y, w, h = bbox
        print(f"    [{i}] '{text}' @ ({x},{y}) [{w}x{h}] conf={conf:.2f}")
    print("-" * 60)

    # 4. 尝试定位目标文本
    print(f"\n[4] Trying to locate '{target_text}'...")
    coord = locator.locate_element(target_text, screenshot_bgr)

    if coord:
        print(f"    [OK] Found at: {coord}")
    else:
        print(f"    [FAIL] NOT FOUND")

    return all_text, coord


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--screenshot":
        if len(sys.argv) > 2:
            test_from_screenshot(sys.argv[2])
        else:
            print("Usage: python test_ocr.py --screenshot <path>")
    else:
        test_ocr()



