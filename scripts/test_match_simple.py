#!/usr/bin/env python3
"""
Simple OpenCV Template Matching Test - Mouse-based selection.

Usage:
    python scripts/test_match_simple.py

Steps:
    1. Script takes screenshot
    2. You click TOP-LEFT corner of the icon
    3. You click BOTTOM-RIGHT corner
    4. Script tests matching and shows result
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import cv2
    import numpy as np
    import pyautogui
    from PIL import Image
except ImportError as e:
    print(f"[ERROR] Missing library: {e}")
    print("Install: pip install opencv-python pyautogui pillow")
    sys.exit(1)


def capture_screen():
    """Capture screen and return as cv2 image."""
    screenshot = pyautogui.screenshot()
    img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
    return img


def save_image(img, path):
    """Save image to path."""
    cv2.imwrite(path, img)
    print(f"Saved: {path}")


def main():
    print("="*60)
    print("Simple Template Match Test")
    print("="*60)

    os.makedirs("data/screenshots", exist_ok=True)

    # Step 1: Capture screen
    print("\n[1] Taking screenshot...")
    screen = capture_screen()
    screen_path = "data/screenshots/test_screen.png"
    save_image(screen, screen_path)
    print(f"  Screen size: {screen.shape[1]}x{screen.shape[0]}")

    # Step 2: Select template region
    print("\n[2] Select template region")
    print("  Move mouse to TOP-LEFT corner of the icon")
    print("  Press ENTER when ready...")

    input()  # Wait for Enter
    x1, y1 = pyautogui.position()
    print(f"  Start: ({x1}, {y1})")

    print("\n  Move mouse to BOTTOM-RIGHT corner")
    print("  Press ENTER when ready...")

    input()  # Wait for Enter
    x2, y2 = pyautogui.position()
    print(f"  End: ({x2}, {y2})")

    # Validate
    if x2 <= x1 or y2 <= y1:
        print("[ERROR] Invalid region. Second point must be below-right of first.")
        return

    # Extract template
    template = screen[y1:y2, x1:x2]
    template_path = "data/screenshots/test_template.png"
    save_image(template, template_path)
    print(f"  Template size: {template.shape[1]}x{template.shape[0]}")

    # Step 3: Ask if user wants to change context
    print("\n[3] Want to test matching on a different screen?")
    print("  (e.g., open a different window or resize)")
    print("  Press ENTER to continue with current screen,")
    print("  or wait 5 seconds for new capture...")

    start = time.time()
    while time.time() - start < 5:
        # Check if Enter pressed (simplified - just wait)
        time.sleep(0.5)
        remaining = 5 - (time.time() - start)
        print(f"  Capturing new screen in {int(remaining)}s...")

    # Capture screen for matching
    print("\n  Capturing screen for matching...")
    match_screen = capture_screen()
    match_screen_path = "data/screenshots/test_match_screen.png"
    save_image(match_screen, match_screen_path)

    # Step 4: Perform matching
    print("\n[4] Template matching...")

    h_t, w_t = template.shape[:2]
    h_s, w_s = match_screen.shape[:2]

    if h_t > h_s or w_t > w_s:
        print("[ERROR] Template larger than screenshot!")
        return

    # Multi-scale matching
    scales = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]
    best_match = None
    best_score = 0
    best_scale = 1.0

    all_results = []

    for scale in scales:
        # Resize template
        if scale != 1.0:
            resized = cv2.resize(template, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        else:
            resized = template

        h_r, w_r = resized.shape[:2]

        if h_r > h_s or w_r > w_s:
            continue

        # Match
        result = cv2.matchTemplate(match_screen, resized, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        center_x = max_loc[0] + w_r // 2
        center_y = max_loc[1] + h_r // 2

        all_results.append({
            "scale": scale,
            "score": max_val,
            "center": (center_x, center_y),
            "top_left": max_loc,
            "size": (w_r, h_r)
        })

        if max_val > best_score:
            best_score = max_val
            best_match = max_loc
            best_scale = scale

    # Print results
    print("\n" + "-"*60)
    print("Matching Results:")
    print("-"*60)

    threshold = 0.8

    for r in all_results:
        status = "PASS" if r["score"] >= threshold else "FAIL"
        print(f"  Scale {r['scale']:.2f}: score={r['score']:.3f} @ {r['center']} [{status}]")

    print("-"*60)

    if best_score >= threshold:
        print(f"\n[SUCCESS] Match found!")
        print(f"  Best score: {best_score:.3f}")
        print(f"  Best scale: {best_scale:.2f}")
        print(f"  Position: {all_results[0]['center']}")
    else:
        print(f"\n[FAILED] No match above threshold {threshold}")
        print(f"  Best score: {best_score:.3f}")

    # Step 5: Visualize
    print("\n[5] Drawing result...")
    annotated = match_screen.copy()

    # Draw all attempts (lighter)
    for r in all_results[1:]:
        cx, cy = r["center"]
        cv2.circle(annotated, (cx, cy), 8, (100, 100, 255), 2)

    # Draw best match (bright green)
    if best_match:
        best_r = all_results[0]
        cx, cy = best_r["center"]
        cv2.circle(annotated, (cx, cy), 15, (0, 255, 0), 3)
        cv2.circle(annotated, (cx, cy), 4, (0, 255, 0), -1)

        # Draw rectangle
        tl = best_r["top_left"]
        w, h = best_r["size"]
        cv2.rectangle(annotated, tl, (tl[0]+w, tl[1]+h), (0, 255, 0), 2)

        # Text
        text = f"Score: {best_score:.3f} Scale: {best_scale:.2f}"
        cv2.putText(annotated, text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    result_path = "data/screenshots/test_result.png"
    save_image(annotated, result_path)

    # Show result
    print("\n[6] Showing result window...")
    print("  Press any key to close...")

    # Resize for display if needed
    display_h, display_w = annotated.shape[:2]
    if display_h > 800:
        scale = 800 / display_h
        annotated = cv2.resize(annotated, None, fx=scale, fy=scale)

    cv2.imshow("Template Match Result", annotated)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    print("\n[7] Test complete!")
    print(f"  Screen: {match_screen_path}")
    print(f"  Template: {template_path}")
    print(f"  Result: {result_path}")


if __name__ == "__main__":
    main()