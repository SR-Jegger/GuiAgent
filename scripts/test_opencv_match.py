#!/usr/bin/env python3
"""
OpenCV Template Matching Test Script

Interactive tool for testing icon matching before integrating into skills.

Usage:
    # Test with existing images
    python scripts/test_opencv_match.py --template screenshots/icon.png --screenshot screenshots/screen.png

    # Capture template region first, then match
    python scripts/test_opencv_match.py --capture-template

    # Use current screen for matching
    python scripts/test_opencv_match.py --template screenshots/icon.png --capture-screen

    # Interactive: select region on screen to capture as template
    python scripts/test_opencv_match.py --interactive
"""

import os
import sys
import argparse
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[ERROR] OpenCV not installed. Run: pip install opencv-python")
    sys.exit(1)

try:
    import pyautogui
    from PIL import Image
    SCREENSHOT_AVAILABLE = True
except ImportError:
    SCREENSHOT_AVAILABLE = False
    print("[WARN] pyautogui/PIL not installed, cannot capture screenshots")

# Windows keyboard input
if os.name == 'nt':
    import msvcrt


def capture_screen(save_path: str = None) -> np.ndarray:
    """
    Capture current screen as numpy array.

    Args:
        save_path: Optional path to save screenshot

    Returns:
        Screenshot as numpy array (BGR format for cv2)
    """
    if not SCREENSHOT_AVAILABLE:
        print("[ERROR] Cannot capture screen without pyautogui")
        return None

    # Capture with pyautogui
    screenshot = pyautogui.screenshot()

    # Convert PIL Image to numpy array (RGB to BGR for cv2)
    img_array = np.array(screenshot)
    img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

    if save_path:
        cv2.imwrite(save_path, img_bgr)
        print(f"[INFO] Screenshot saved: {save_path}")

    return img_bgr


def capture_template_interactive() -> tuple:
    """
    Interactive template capture - user selects region on screen.

    Returns:
        Tuple of (template_image, x, y, w, h) or None if cancelled
    """
    if not SCREENSHOT_AVAILABLE:
        print("[ERROR] Cannot capture without pyautogui")
        return None

    print("\n" + "="*60)
    print("Interactive Template Capture")
    print("="*60)
    print("Instructions:")
    print("  1. Position your mouse over the TOP-LEFT corner of the icon")
    print("  2. Press ENTER to mark the start point")
    print("  3. Move mouse to BOTTOM-RIGHT corner")
    print("  4. Press ENTER to complete selection")
    print("  5. Press 'q' to cancel")
    print("="*60)

    # First capture screen for preview
    screen = capture_screen()
    screen_h, screen_w = screen.shape[:2]

    # Get start point
    print("\n[Step 1] Move mouse to TOP-LEFT corner, press ENTER...")
    start_pos = None

    while start_pos is None:
        x, y = pyautogui.position()
        print(f"  Current position: ({x}, {y}) - Press ENTER to confirm, 'q' to cancel")

        # Check for key press (Windows)
        if os.name == 'nt' and msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b'\r' or key == b'\n':  # Enter
                start_pos = (x, y)
                print(f"  Start point set: ({x}, {y})")
            elif key == b'q':  # Cancel
                print("[CANCEL] Selection cancelled")
                return None

        time.sleep(0.1)

    # Get end point
    print("\n[Step 2] Move mouse to BOTTOM-RIGHT corner, press ENTER...")
    end_pos = None

    while end_pos is None:
        x, y = pyautogui.position()
        w = x - start_pos[0]
        h = y - start_pos[1]
        print(f"  Current: ({x}, {y}) | Region size: {w}x{h} - Press ENTER to confirm, 'q' to cancel")

        if os.name == 'nt' and msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b'\r' or key == b'\n':  # Enter
                if w > 0 and h > 0:
                    end_pos = (x, y)
                    print(f"  End point set: ({x}, {y})")
                else:
                    print("  [WARN] Region must have positive width/height")
            elif key == b'q':  # Cancel
                print("[CANCEL] Selection cancelled")
                return None

        time.sleep(0.1)

    # Extract template
    x1, y1 = start_pos
    x2, y2 = end_pos
    template = screen[y1:y2, x1:x2]

    print(f"\n[SUCCESS] Template captured: {template.shape[1]}x{template.shape[0]} pixels")
    print(f"  Region: ({x1}, {y1}) to ({x2}, {y2})")

    return template, x1, y1, template.shape[1], template.shape[0]


def match_template(
    template: np.ndarray,
    screenshot: np.ndarray,
    threshold: float = 0.8,
    multi_scale: bool = True,
    scales: list = [0.8, 0.9, 1.0, 1.1, 1.2]
) -> dict:
    """
    Perform template matching with optional multi-scale support.

    Args:
        template: Template image (numpy array)
        screenshot: Screenshot to search in (numpy array)
        threshold: Match confidence threshold (0-1)
        multi_scale: Whether to try multiple scales
        scales: List of scale factors to try

    Returns:
        Dict with match results:
            - found: bool
            - coordinate: (x, y) or None
            - score: float
            - scale: float
            - all_matches: list of (score, coord, scale)
    """
    results = {
        "found": False,
        "coordinate": None,
        "score": 0.0,
        "scale": 1.0,
        "all_matches": []
    }

    if template is None or screenshot is None:
        return results

    h_t, w_t = template.shape[:2]
    h_s, w_s = screenshot.shape[:2]

    # Check template size
    if h_t > h_s or w_t > w_s:
        print(f"[WARN] Template ({w_t}x{h_t}) larger than screenshot ({w_s}x{h_s})")
        return results

    # Try each scale
    for scale in scales:
        # Resize template
        if scale != 1.0:
            resized = cv2.resize(
                template,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            )
        else:
            resized = template

        h_r, w_r = resized.shape[:2]

        # Skip if resized template larger than screenshot
        if h_r > h_s or w_r > w_s:
            continue

        # Template matching
        result = cv2.matchTemplate(screenshot, resized, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # Calculate center coordinate
        center_x = max_loc[0] + w_r // 2
        center_y = max_loc[1] + h_r // 2

        results["all_matches"].append({
            "score": max_val,
            "coordinate": (center_x, center_y),
            "scale": scale,
            "top_left": max_loc,
            "size": (w_r, h_r)
        })

    # Sort by score and find best match
    if results["all_matches"]:
        results["all_matches"].sort(key=lambda x: x["score"], reverse=True)
        best = results["all_matches"][0]

        results["score"] = best["score"]
        results["coordinate"] = best["coordinate"]
        results["scale"] = best["scale"]
        results["found"] = best["score"] >= threshold

    return results


def visualize_match(
    screenshot: np.ndarray,
    match_result: dict,
    save_path: str = None,
    show: bool = True
) -> np.ndarray:
    """
    Draw match result on screenshot for visualization.

    Args:
        screenshot: Original screenshot
        match_result: Match result dict from match_template
        save_path: Path to save annotated image
        show: Whether to display image in window

    Returns:
        Annotated screenshot image
    """
    annotated = screenshot.copy()

    if match_result["found"]:
        # Get best match details
        best = match_result["all_matches"][0]
        x, y = best["coordinate"]
        scale = best["scale"]

        # Draw all matches (lighter color)
        for match in match_result["all_matches"][1:5]:  # Show top 5
            mx, my = match["coordinate"]
            cv2.circle(annotated, (mx, my), 10, (100, 100, 255), 2)

        # Draw best match (bright green)
        cv2.circle(annotated, (x, y), 20, (0, 255, 0), 3)
        cv2.circle(annotated, (x, y), 5, (0, 255, 0), -1)

        # Draw rectangle showing template region
        top_left = best["top_left"]
        w, h = best["size"]
        cv2.rectangle(annotated, top_left, (top_left[0] + w, top_left[1] + h), (0, 255, 0), 2)

        # Add text
        text = f"Best match: ({x}, {y}) score={match_result['score']:.3f} scale={scale:.2f}"
        cv2.putText(annotated, text, (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

        # Add scale info
        cv2.putText(annotated, f"Scale: {scale:.2f}", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1)

    else:
        # No match found
        cv2.putText(annotated, f"No match (score={match_result['score']:.3f})", (50, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)

    if save_path:
        cv2.imwrite(save_path, annotated)
        print(f"[INFO] Annotated image saved: {save_path}")

    if show:
        # Resize for display if too large
        display_h, display_w = annotated.shape[:2]
        max_display = 1000
        if display_h > max_display or display_w > max_display:
            scale = max_display / max(display_h, display_w)
            annotated = cv2.resize(annotated, None, fx=scale, fy=scale)

        cv2.imshow("Template Match Result", annotated)
        print("[INFO] Press any key to close the display window...")
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return annotated


def load_image(path: str) -> np.ndarray:
    """Load image from file path."""
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}")
        return None

    # Handle Windows encoding
    try:
        img = cv2.imread(path)
    except Exception:
        img = cv2.imread(path.encode('gbk') if os.name == 'nt' else path)

    if img is None:
        print(f"[ERROR] Could not load image: {path}")
        return None

    return img


def test_single_match(
    template_path: str,
    screenshot_path: str = None,
    threshold: float = 0.8,
    save_result: str = None
):
    """
    Test template matching with provided images.

    Args:
        template_path: Path to template image
        screenshot_path: Path to screenshot (or None to capture current screen)
        threshold: Match threshold
        save_result: Path to save annotated result
    """
    print("\n" + "="*60)
    print("Template Matching Test")
    print("="*60)

    # Auto-detect screen resolution
    from learning.icon_matcher import get_screen_resolution, get_screen_dpi
    screen_res = get_screen_resolution()
    screen_dpi = get_screen_dpi()
    print(f"\n[Auto-Detected] Screen: {screen_res[0]}x{screen_res[1]} @ {screen_dpi} DPI")

    # Load template
    print(f"\n[Step 1] Loading template: {template_path}")
    template = load_image(template_path)
    if template is None:
        return

    print(f"  Template size: {template.shape[1]}x{template.shape[0]} pixels")

    # Get screenshot
    if screenshot_path:
        print(f"\n[Step 2] Loading screenshot: {screenshot_path}")
        screenshot = load_image(screenshot_path)
    else:
        print(f"\n[Step 2] Capturing current screen...")
        screenshot = capture_screen()

    if screenshot is None:
        return

    print(f"  Screenshot size: {screenshot.shape[1]}x{screenshot.shape[0]} pixels")

    # Perform matching
    print(f"\n[Step 3] Performing template matching (threshold={threshold})...")
    result = match_template(template, screenshot, threshold=threshold, multi_scale=True)

    # Report results
    print("\n" + "-"*60)
    print("Matching Results:")
    print("-"*60)

    if result["found"]:
        print(f"  [SUCCESS] Match found!")
        print(f"  Position: {result['coordinate']}")
        print(f"  Confidence: {result['score']:.4f}")
        print(f"  Scale: {result['scale']:.2f}")
    else:
        print(f"  [FAILED] No match above threshold {threshold}")
        print(f"  Best score: {result['score']:.4f}")

    # Show all scale attempts
    print(f"\n  All scale attempts:")
    for match in result["all_matches"]:
        status = "PASS" if match["score"] >= threshold else "FAIL"
        print(f"    Scale {match['scale']:.2f}: score={match['score']:.3f} @ {match['coordinate']} [{status}]")

    # Visualize
    print("\n[Step 4] Visualizing result...")
    if save_result:
        visualize_match(screenshot, result, save_path=save_result, show=True)
    else:
        default_save = os.path.join("data/screenshots", "match_result.png")
        visualize_match(screenshot, result, save_path=default_save, show=True)


def interactive_test():
    """
    Full interactive test: capture template, capture screen, match.
    """
    print("\n" + "="*60)
    print("Interactive Template Matching Test")
    print("="*60)

    # Step 1: Capture template
    print("\n[Phase 1] Capture template from screen")
    result = capture_template_interactive()

    if result is None:
        return

    template, x1, y1, w, h = result

    # Save template
    template_path = os.path.join("data/screenshots", "captured_template.png")
    cv2.imwrite(template_path, template)
    print(f"  Template saved: {template_path}")

    # Step 2: Wait and capture screen (user can change context)
    print("\n[Phase 2] Prepare screen for matching")
    print("  You have 3 seconds to switch windows or change context...")
    time.sleep(3)

    print("  Capturing screen now...")
    screenshot = capture_screen()
    screenshot_path = os.path.join("data/screenshots", "captured_screen.png")

    # Step 3: Match
    print("\n[Phase 3] Matching template in screenshot")
    match_result = match_template(template, screenshot, threshold=0.8, multi_scale=True)

    # Step 4: Visualize
    print("\n[Phase 4] Visualizing results")
    visualize_match(screenshot, match_result,
                    save_path=os.path.join("data/screenshots", "interactive_result.png"),
                    show=True)


def main():
    parser = argparse.ArgumentParser(
        description="Test OpenCV template matching for skill icons",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with existing images
  python scripts/test_opencv_match.py --template icon.png --screenshot screen.png

  # Capture template interactively, then match on current screen
  python scripts/test_opencv_match.py --interactive

  # Use existing template on current screen
  python scripts/test_opencv_match.py --template icon.png --capture-screen

  # Adjust threshold
  python scripts/test_opencv_match.py --template icon.png --screenshot screen.png --threshold 0.85
        """
    )

    parser.add_argument("--template", help="Path to template image")
    parser.add_argument("--screenshot", help="Path to screenshot image")
    parser.add_argument("--threshold", type=float, default=0.8, help="Match threshold (default: 0.8)")
    parser.add_argument("--capture-screen", action="store_true", help="Capture current screen instead of loading")
    parser.add_argument("--interactive", action="store_true", help="Interactive mode: capture template from screen")
    parser.add_argument("--save-result", help="Path to save annotated result image")
    parser.add_argument("--no-display", action="store_true", help="Don't display result window")

    args = parser.parse_args()

    # Ensure screenshots directory exists
    os.makedirs("data/screenshots", exist_ok=True)

    if args.interactive:
        interactive_test()
    elif args.template:
        test_single_match(
            args.template,
            args.screenshot if not args.capture_screen else None,
            args.threshold,
            args.save_result
        )
    else:
        parser.print_help()
        print("\n[ERROR] Please provide --template or use --interactive mode")


if __name__ == "__main__":
    main()