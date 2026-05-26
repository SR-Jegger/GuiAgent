"""
Icon Matcher - Image-based skill icon matching for skill execution.

Provides:
- Multi-scale template matching for different resolutions/DPI
- Coordinate normalization and denormalization
- ROI region optimization for faster matching
- Fallback to recorded coordinates when image match fails
- Auto-detection of screen resolution (Phase 2 enhancement)

Phase 1 implementation: Basic OpenCV template matching with multi-scale support.
Phase 2 (A+B): Resolution-aware adaptive matching with dynamic scaling.
"""

import os
import math
import logging
from typing import Optional, Tuple, List, Dict, Any

# OpenCV for template matching (optional dependency)
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("[WARN] OpenCV (cv2) not installed. Icon matching disabled.")
    print("       Install with: pip install opencv-python")

# pyautogui for auto screen resolution detection
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    print("[WARN] pyautogui not installed. Cannot auto-detect screen resolution.")

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_MATCH_THRESHOLD = 0.85
DEFAULT_SCALE_RANGE = [0.8, 0.9, 1.0, 1.1, 1.2]
DEFAULT_SCREEN_DIAGONAL = 1000  # Normalized coordinate range
DEFAULT_SCREEN_SIZE = (1920, 1080)  # Fallback if auto-detection fails


def get_screen_resolution() -> Tuple[int, int]:
    """
    Auto-detect current screen resolution.

    Returns:
        Tuple of (width, height) in pixels
    """
    if PYAUTOGUI_AVAILABLE:
        try:
            width, height = pyautogui.size()
            logger.debug(f"Auto-detected screen resolution: {width}x{height}")
            return (width, height)
        except Exception as e:
            logger.warning(f"Failed to auto-detect screen resolution: {e}")

    # Fallback to default
    logger.warning(f"Using default screen resolution: {DEFAULT_SCREEN_SIZE}")
    return DEFAULT_SCREEN_SIZE


def get_screen_dpi() -> int:
    """
    Get current screen DPI (cross-platform).

    Windows: Uses GetDpiForWindow API
    Linux: Uses Xrandr or defaults to 96

    Returns:
        DPI value (default 96 for Linux, 100 for fallback)
    """
    if os.name == 'nt':
        # Windows: Get DPI from system
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetDesktopWindow()
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                logger.debug(f"Detected DPI (Windows): {dpi}")
                return dpi
        except Exception as e:
            logger.warning(f"Failed to detect DPI (Windows): {e}")

    elif os.name == 'posix':
        # Linux: Try multiple methods
        try:
            # Method 1: Use xrandr (most reliable on X11)
            import subprocess
            result = subprocess.run(
                ['xrandr', '--query'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                # Parse xrandr output to find DPI
                import re
                # Look for "connected" line with DPI info
                for line in result.stdout.split('\n'):
                    if 'connected' in line:
                        # Pattern: "1920x1080 60.00*+ ... 96dpi"
                        dpi_match = re.search(r'(\d+)dpi', line)
                        if dpi_match:
                            dpi = int(dpi_match.group(1))
                            logger.debug(f"Detected DPI (Linux/xrandr): {dpi}")
                            return dpi

                # Alternative: calculate from physical dimensions
                # Pattern: "1920x1080 ... 160mm x 90mm"
                size_match = re.search(r'(\d+)mm x (\d+)mm', result.stdout)
                res_match = re.search(r'(\d+)x(\d+)', result.stdout)
                if size_match and res_match:
                    w_mm, h_mm = int(size_match.group(1)), int(size_match.group(2))
                    w_px, h_px = int(res_match.group(1)), int(res_match.group(2))
                    # DPI = pixels / inches, 1 inch = 25.4mm
                    dpi_w = int(w_px * 25.4 / w_mm)
                    dpi_h = int(h_px * 25.4 / h_mm)
                    dpi = (dpi_w + dpi_h) // 2
                    logger.debug(f"Calculated DPI (Linux/xrandr): {dpi}")
                    return dpi

        except Exception as e:
            logger.warning(f"Failed to detect DPI (Linux/xrandr): {e}")

        try:
            # Method 2: Use xrdb for X resources
            import subprocess
            result = subprocess.run(
                ['xrdb', '-query'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                import re
                dpi_match = re.search(r'Xft.dpi:\s*(\d+)', result.stdout)
                if dpi_match:
                    dpi = int(dpi_match.group(1))
                    logger.debug(f"Detected DPI (Linux/xrdb): {dpi}")
                    return dpi
        except Exception as e:
            logger.warning(f"Failed to detect DPI (Linux/xrdb): {e}")

        # Method 3: Default for Linux (96 is common)
        logger.debug(f"Using default DPI (Linux): 96")
        return 96

    # Fallback for unknown platforms
    logger.warning(f"Using default DPI: 100")
    return 100


class IconMatcher:
    """
    Icon matcher for skill execution with multi-scale template matching.

    Usage:
        matcher = IconMatcher()
        coord = matcher.find_icon("data/screenshots/edge_icon.png", screenshot)
        if coord:
            print(f"Found icon at: {coord}")
    """

    def __init__(
        self,
        screenshots_dir: str = "data/screenshots",
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        scale_range: List[float] = DEFAULT_SCALE_RANGE
    ):
        """
        Initialize icon matcher.

        Args:
            screenshots_dir: Directory storing skill icon screenshots
            match_threshold: Minimum confidence threshold (0-1)
            scale_range: List of scales to try for multi-resolution matching
        """
        self.screenshots_dir = screenshots_dir
        self.match_threshold = match_threshold
        self.scale_range = scale_range
        self.template_cache: Dict[str, Any] = {}

        os.makedirs(screenshots_dir, exist_ok=True)
        logger.debug(f"IconMatcher initialized with screenshots_dir={screenshots_dir}")

    def find_icon(
        self,
        icon_path: str,
        screenshot: Any,
        roi_region: Optional[Tuple[int, int, int, int]] = None,
        normalized_coords: bool = True,
        screen_size: Optional[Tuple[int, int]] = None
    ) -> Optional[Tuple[int, int]]:
        """
        Find icon location in screenshot using multi-scale template matching.

        Args:
            icon_path: Path to icon template image
            screenshot: Screenshot (numpy array or path to image file)
            roi_region: Optional search region (x, y, width, height) to speed up matching
            normalized_coords: If True, return normalized coordinates (0-1000 range)
            screen_size: Screen size (width, height) for coordinate normalization.
                        If None, auto-detect from current screen.

        Returns:
            Coordinate (x, y) in normalized or pixel format, or None if not found
        """
        if not CV2_AVAILABLE:
            logger.warning("OpenCV not available, cannot perform image matching")
            return None

        # Auto-detect screen resolution if not provided
        if screen_size is None:
            screen_size = get_screen_resolution()

        # Load template
        template = self._load_template(icon_path)
        if template is None:
            return None

        # Load screenshot if path provided
        if isinstance(screenshot, str):
            screenshot = self._load_screenshot(screenshot)
            if screenshot is None:
                return None

        # Apply ROI if specified
        search_region = screenshot
        offset_x, offset_y = 0, 0
        if roi_region:
            x, y, w, h = roi_region
            search_region = screenshot[y:y+h, x:x+w]
            offset_x, offset_y = x, y
            logger.debug(f"Using ROI region: ({x}, {y}, {w}, {h})")

        # Multi-scale matching
        best_match = None
        best_score = 0.0

        for scale in self.scale_range:
            result = self._match_at_scale(template, search_region, scale)
            if result is None:
                continue

            score, coord = result
            if score > best_score:
                best_score = score
                best_match = coord

        if best_match is None or best_score < self.match_threshold:
            logger.info(f"Icon match failed: {icon_path}, best_score={best_score:.2f}")
            return None

        # Convert to absolute coordinates
        abs_x = best_match[0] + offset_x
        abs_y = best_match[1] + offset_y

        logger.info(f"Icon matched: {icon_path} at ({abs_x}, {abs_y}), score={best_score:.2f}")

        # Normalize if requested
        if normalized_coords and screen_size:
            return self._normalize_coordinate(abs_x, abs_y, screen_size)

        return (abs_x, abs_y)

    def _load_template(self, icon_path: str) -> Optional[Any]:
        """Load template image with caching."""
        if icon_path in self.template_cache:
            return self.template_cache[icon_path]

        if not os.path.exists(icon_path):
            logger.warning(f"Icon template not found: {icon_path}")
            return None

        # Handle Windows path encoding
        try:
            template = cv2.imread(icon_path.encode('gbk') if os.name == 'nt' else icon_path)
        except Exception:
            template = cv2.imread(icon_path)

        if template is None:
            logger.warning(f"Failed to load template: {icon_path}")
            return None

        self.template_cache[icon_path] = template
        return template

    def _load_screenshot(self, screenshot_path: str) -> Optional[Any]:
        """Load screenshot image."""
        if not os.path.exists(screenshot_path):
            logger.warning(f"Screenshot not found: {screenshot_path}")
            return None

        try:
            screenshot = cv2.imread(screenshot_path.encode('gbk') if os.name == 'nt' else screenshot_path)
        except Exception:
            screenshot = cv2.imread(screenshot_path)

        if screenshot is None:
            logger.warning(f"Failed to load screenshot: {screenshot_path}")
            return None

        return screenshot

    def _match_at_scale(
        self,
        template: Any,
        screenshot: Any,
        scale: float
    ) -> Optional[Tuple[float, Tuple[int, int]]]:
        """
        Perform template matching at a specific scale.

        Args:
            template: Template image (numpy array)
            screenshot: Screenshot image (numpy array)
            scale: Scale factor for template

        Returns:
            Tuple of (score, coordinate) or None if no match
        """
        try:
            # Resize template
            if scale != 1.0:
                template_scaled = cv2.resize(
                    template,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                )
            else:
                template_scaled = template

            # Check if scaled template is larger than screenshot
            h_t, w_t = template_scaled.shape[:2]
            h_s, w_s = screenshot.shape[:2]

            if h_t > h_s or w_t > w_s:
                return None

            # Template matching
            result = cv2.matchTemplate(screenshot, template_scaled, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            # Calculate center coordinate
            center_x = max_loc[0] + w_t // 2
            center_y = max_loc[1] + h_t // 2

            return (max_val, (center_x, center_y))

        except Exception as e:
            logger.error(f"Template matching error at scale {scale}: {e}")
            return None

    def _normalize_coordinate(
        self,
        x: int,
        y: int,
        screen_size: Tuple[int, int]
    ) -> Tuple[int, int]:
        """
        Normalize pixel coordinate to 0-1000 range.

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels
            screen_size: Screen size (width, height)

        Returns:
            Normalized coordinate (x, y) in 0-1000 range
        """
        width, height = screen_size
        norm_x = int(x * DEFAULT_SCREEN_DIAGONAL / width)
        norm_y = int(y * DEFAULT_SCREEN_DIAGONAL / height)
        return (norm_x, norm_y)

    def denormalize_coordinate(
        self,
        norm_x: int,
        norm_y: int,
        screen_size: Tuple[int, int]
    ) -> Tuple[int, int]:
        """
        Convert normalized coordinate back to pixel coordinate.

        Args:
            norm_x: Normalized X coordinate (0-1000)
            norm_y: Normalized Y coordinate (0-1000)
            screen_size: Screen size (width, height)

        Returns:
            Pixel coordinate (x, y)
        """
        width, height = screen_size
        x = int(norm_x * width / DEFAULT_SCREEN_DIAGONAL)
        y = int(norm_y * height / DEFAULT_SCREEN_DIAGONAL)
        return (x, y)

    def capture_icon(
        self,
        screenshot_path: str,
        x: int,
        y: int,
        w: int,
        h: int,
        output_name: str
    ) -> Optional[str]:
        """
        Capture and save icon from screenshot.

        Args:
            screenshot_path: Path to source screenshot
            x, y: Top-left corner of icon region
            w, h: Width and height of icon region
            output_name: Output filename (relative to screenshots_dir)

        Returns:
            Path to saved icon, or None if failed
        """
        if not CV2_AVAILABLE:
            return None

        screenshot = self._load_screenshot(screenshot_path)
        if screenshot is None:
            return None

        # Crop icon region
        icon = screenshot[y:y+h, x:x+w]

        # Save icon
        output_path = os.path.join(self.screenshots_dir, output_name)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            # Handle Windows path encoding properly
            output_path_str = output_path if os.name != 'nt' else output_path
            cv2.imwrite(output_path_str, icon)
            logger.info(f"Icon captured: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Failed to save icon: {e}")
            return None

    def clear_cache(self):
        """Clear template cache."""
        self.template_cache.clear()
        logger.debug("Template cache cleared")

    # =========================================================================
    # Phase 2: Adaptive scaling (方案 A + B)
    # =========================================================================

    def calculate_adaptive_scales(
        self,
        base_scale: float,
        range_percent: float = 0.15,
        num_steps: int = 5
    ) -> List[float]:
        """
        Calculate adaptive scale range based on resolution difference.

        方案A: 动态缩放计算
        根据分辨率差异计算精准的缩放范围，而不是使用固定的 [0.8, 0.9, 1.0, 1.1, 1.2]

        Args:
            base_scale: Base scale factor from resolution difference
            range_percent: Range around base scale (e.g., 0.15 = ±15%)
            num_steps: Number of scale steps to generate

        Returns:
            List of adaptive scale factors

        Example:
            base_scale = 1.33 (2560x1440 vs 1920x1080)
            range_percent = 0.15
            → scales = [1.13, 1.20, 1.26, 1.33, 1.40, 1.46, 1.53]
        """
        min_scale = base_scale * (1 - range_percent)
        max_scale = base_scale * (1 + range_percent)

        # Generate evenly distributed scales
        step = (max_scale - min_scale) / (num_steps - 1)
        scales = [min_scale + i * step for i in range(num_steps)]

        # Always include base_scale and 1.0 for fallback
        if 1.0 not in scales:
            scales.append(1.0)
        if base_scale not in scales:
            scales.append(base_scale)

        # Sort scales
        scales.sort()

        logger.debug(f"Adaptive scales: base={base_scale:.2f}, range=[{min_scale:.2f}, {max_scale:.2f}]")
        logger.debug(f"Generated scales: {[f'{s:.2f}' for s in scales]}")

        return scales

    def find_icon_adaptive(
        self,
        icon_data: "IconData",
        screenshot: Any,
        current_resolution: Optional[Tuple[int, int]] = None,
        current_dpi: Optional[int] = None,
        roi_region: Optional[Tuple[int, int, int, int]] = None,
        normalized_coords: bool = True
    ) -> Optional[Tuple[int, int]]:
        """
        方案A+B: 自适应图像匹配

        根据icon_data中存储的分辨率信息动态计算缩放范围，
        而不是使用固定缩放。

        Args:
            icon_data: IconData with resolution metadata
            screenshot: Current screenshot
            current_resolution: Current screen resolution (width, height).
                                If None, auto-detect.
            current_dpi: Current DPI. If None, auto-detect.
            roi_region: Optional search region
            normalized_coords: Return normalized coordinates

        Returns:
            Coordinate (x, y) or None
        """
        if not CV2_AVAILABLE or not icon_data.has_icon():
            return None

        # Auto-detect screen resolution if not provided
        if current_resolution is None:
            current_resolution = get_screen_resolution()

        # Auto-detect DPI if not provided
        if current_dpi is None:
            current_dpi = get_screen_dpi()

        logger.info(f"Screen: {current_resolution[0]}x{current_resolution[1]} @ {current_dpi} DPI")

        # Load template
        template = self._load_template(icon_data.icon_path)
        if template is None:
            return None

        # Load screenshot if path
        if isinstance(screenshot, str):
            screenshot = self._load_screenshot(screenshot)
            if screenshot is None:
                return None

        # Calculate adaptive scales
        if icon_data.has_resolution_info():
            base_scale = icon_data.get_scale_factor(current_resolution, current_dpi)
            scales = self.calculate_adaptive_scales(base_scale, range_percent=0.15, num_steps=7)
            logger.info(f"Using adaptive scales (base={base_scale:.2f})")
        else:
            # Fallback to fixed scales if no resolution info
            scales = self.scale_range
            logger.info(f"Using fixed scales (no resolution metadata)")

        # Apply ROI
        search_region = screenshot
        offset_x, offset_y = 0, 0
        if roi_region:
            x, y, w, h = roi_region
            search_region = screenshot[y:y+h, x:x+w]
            offset_x, offset_y = x, y

        # Multi-scale matching
        best_match = None
        best_score = 0.0
        best_scale = 1.0
        all_matches = []

        for scale in scales:
            result = self._match_at_scale(template, search_region, scale)
            if result is None:
                continue

            score, coord = result
            all_matches.append((scale, score, coord))

            if score > best_score:
                best_score = score
                best_match = coord
                best_scale = scale

        # Log all attempts
        for scale, score, coord in all_matches:
            logger.debug(f"Scale {scale:.2f}: score={score:.3f} @ {coord}")

        threshold = icon_data.match_threshold

        if best_match is None or best_score < threshold:
            logger.warning(f"Adaptive match failed: {icon_data.icon_path}, best={best_score:.2f} < {threshold}")
            return None

        # Convert to absolute coordinates
        abs_x = best_match[0] + offset_x
        abs_y = best_match[1] + offset_y

        logger.info(f"Adaptive match success: {icon_data.icon_path} at ({abs_x}, {abs_y}), "
                   f"score={best_score:.2f}, scale={best_scale:.2f}")

        # Normalize if requested
        if normalized_coords:
            return self._normalize_coordinate(abs_x, abs_y, current_resolution)

        return (abs_x, abs_y)


class IconData:
    """
    Icon data structure for skill configuration.

    Phase 1: Basic icon matching
    Phase 2 (A+B): Resolution-aware matching with dynamic scaling

    Represents the icon_data field in skill definition:
    {
        "icon_path": "screenshots/edge_icon.png",
        "match_threshold": 0.85,
        "roi_region": [100, 200, 50, 50],
        "fallback_coord": [150, 225],
        "recorded_resolution": [1920, 1080],  // Phase 2: Screen resolution when recorded
        "recorded_dpi": 100,                  // Phase 2: DPI when recorded
        "recorded_window_size": [800, 600]    // Phase 2: Window size if applicable
    }
    """

    def __init__(
        self,
        icon_path: Optional[str] = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        roi_region: Optional[List[int]] = None,
        fallback_coord: Optional[List[int]] = None,
        recorded_resolution: Optional[List[int]] = None,
        recorded_dpi: int = 100,
        recorded_window_size: Optional[List[int]] = None
    ):
        """
        Initialize IconData with optional resolution metadata.

        Args:
            icon_path: Path to icon template image
            match_threshold: Match confidence threshold
            roi_region: Search region [x, y, w, h]
            fallback_coord: Fallback coordinate [x, y]
            recorded_resolution: Screen resolution when icon was recorded [w, h]
            recorded_dpi: DPI when icon was recorded (default 100)
            recorded_window_size: Window size if icon is window-relative [w, h]
        """
        self.icon_path = icon_path
        self.match_threshold = match_threshold
        self.roi_region = roi_region
        self.fallback_coord = fallback_coord
        self.recorded_resolution = recorded_resolution
        self.recorded_dpi = recorded_dpi
        self.recorded_window_size = recorded_window_size

    @classmethod
    def from_dict(cls, data: Dict) -> "IconData":
        """Create IconData from dictionary."""
        return cls(
            icon_path=data.get("icon_path"),
            match_threshold=data.get("match_threshold", DEFAULT_MATCH_THRESHOLD),
            roi_region=data.get("roi_region"),
            fallback_coord=data.get("fallback_coord"),
            recorded_resolution=data.get("recorded_resolution"),
            recorded_dpi=data.get("recorded_dpi", 100),
            recorded_window_size=data.get("recorded_window_size")
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        result = {
            "match_threshold": self.match_threshold
        }
        if self.icon_path:
            result["icon_path"] = self.icon_path
        if self.roi_region:
            result["roi_region"] = self.roi_region
        if self.fallback_coord:
            result["fallback_coord"] = self.fallback_coord
        # Phase 2: Add resolution metadata
        if self.recorded_resolution:
            result["recorded_resolution"] = self.recorded_resolution
        if self.recorded_dpi:
            result["recorded_dpi"] = self.recorded_dpi
        if self.recorded_window_size:
            result["recorded_window_size"] = self.recorded_window_size
        return result

    def has_icon(self) -> bool:
        """Check if icon data is available."""
        return self.icon_path is not None and os.path.exists(self.icon_path)

    def get_fallback_coordinate(self) -> Optional[Tuple[int, int]]:
        """Get fallback coordinate."""
        if self.fallback_coord and len(self.fallback_coord) >= 2:
            return (self.fallback_coord[0], self.fallback_coord[1])
        return None

    def has_resolution_info(self) -> bool:
        """Check if resolution metadata is available."""
        return self.recorded_resolution is not None

    def get_scale_factor(self, current_resolution: Tuple[int, int], current_dpi: int = 100) -> float:
        """
        Calculate scale factor between recorded and current resolution.

        Args:
            current_resolution: Current screen resolution (width, height)
            current_dpi: Current DPI

        Returns:
            Scale factor to apply to template
        """
        if not self.has_resolution_info():
            return 1.0

        recorded_w, recorded_h = self.recorded_resolution
        current_w, current_h = current_resolution

        # Resolution scale (area-based)
        res_scale = math.sqrt((current_w * current_h) / (recorded_w * recorded_h))

        # DPI scale
        dpi_scale = current_dpi / self.recorded_dpi

        return res_scale * dpi_scale


def resolve_coordinate_with_icon(
    matcher: IconMatcher,
    icon_data: IconData,
    screenshot: Any,
    screen_size: Optional[Tuple[int, int]] = None,
    current_dpi: Optional[int] = None,
    use_normalized: bool = True,
    use_adaptive: bool = True
) -> Optional[Tuple[int, int]]:
    """
    Resolve action coordinate using icon matching with fallback.

    Phase 2: 支持自适应匹配（方案A+B）+ 自动获取屏幕分辨率

    Priority:
    1. Adaptive icon matching (if icon available with resolution metadata)
    2. Standard icon matching (if icon available without metadata)
    3. Fallback coordinate (if icon match fails or no icon)

    Args:
        matcher: IconMatcher instance
        icon_data: IconData configuration
        screenshot: Current screenshot
        screen_size: Screen size (width, height). If None, auto-detect.
        current_dpi: Current DPI. If None, auto-detect.
        use_normalized: Whether to use normalized coordinates
        use_adaptive: Use adaptive scaling if resolution metadata available

    Returns:
        Coordinate (x, y) or None if cannot resolve
    """
    # Auto-detect screen resolution if not provided
    if screen_size is None:
        screen_size = get_screen_resolution()

    # Auto-detect DPI if not provided
    if current_dpi is None:
        current_dpi = get_screen_dpi()

    # Try icon matching first
    if icon_data.has_icon():
        roi = None
        if icon_data.roi_region:
            roi = tuple(icon_data.roi_region)

        # Use adaptive matching if resolution info available
        if use_adaptive and icon_data.has_resolution_info():
            coord = matcher.find_icon_adaptive(
                icon_data,
                screenshot,
                current_resolution=screen_size,
                current_dpi=current_dpi,
                roi_region=roi,
                normalized_coords=use_normalized
            )
        else:
            # Standard matching
            coord = matcher.find_icon(
                icon_data.icon_path,
                screenshot,
                roi_region=roi,
                normalized_coords=use_normalized,
                screen_size=screen_size
            )

        if coord:
            logger.info(f"Coordinate resolved via icon matching: {coord}")
            return coord

        logger.warning("Icon matching failed, using fallback")

    # Use fallback coordinate
    fallback = icon_data.get_fallback_coordinate()
    if fallback:
        if use_normalized:
            return fallback
        else:
            return matcher.denormalize_coordinate(
                fallback[0], fallback[1], screen_size
            )

    logger.warning("No coordinate available (icon match failed, no fallback)")
    return None