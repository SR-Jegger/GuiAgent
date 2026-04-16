"""OCR Locator module for text-based element positioning.

This module provides OCR-based element location using PaddleOCR,
supporting exact, partial, and fuzzy text matching.

OCR自己截图,不依赖capture_node提供的截图。"""
import os
import logging
import pyautogui
from typing import Optional, Tuple, List, Any

import numpy as np

# 跳过PaddleOCR模型源连接检查
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

logger = logging.getLogger(__name__)


class OCRLocator:
    """OCR-based element locator using PaddleOCR.

    Locates UI elements by recognizing text on screen and returning
    the center coordinates of matching text regions.

    Attributes:
        ocr: PaddleOCR instance for text recognition.
        use_gpu: Whether to use GPU for OCR processing.
    """

    def __init__(self, use_gpu: bool = False):
        """Initialize OCRLocator with PaddleOCR.

        Args:
            use_gpu: Whether to use GPU acceleration. Defaults to False.

        Raises:
            ImportError: If PaddleOCR is not installed.
        """
        try:
            from paddleocr import PaddleOCR
            from pathlib import Path

            # 使用本地模型目录，避免重复下载
            project_root = Path(__file__).parent.parent
            model_dir = project_root / "models" / "paddleocr"

            # 检查模型目录是否存在
            if not model_dir.exists():
                print(f"[OCR] Warning: Model directory not found at {model_dir}")
                print(f"[OCR] Falling back to auto-download")
                self.ocr = PaddleOCR(use_angle_cls=False, lang='ch')
            else:
                # 使用本地模型 - 使用正确的参数名
                det_model_dir = model_dir / "PP-OCRv5_server_det"
                rec_model_dir = model_dir / "PP-OCRv5_server_rec"

                # PaddleOCR 新版本的参数名是 det_model_dir 和 rec_model_dir
                self.ocr = PaddleOCR(
                    text_detection_model_dir=str(det_model_dir),
                    text_recognition_model_dir=str(rec_model_dir),
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    # doc_unwarping_model_dir="UVDoc",
                    # doc_orientation_classify_model_dir="LCNet_x1_0_doc_ori",
                    # textline_orientation_model_dir="PP-LCNet_x1_0_textline_ori",
                    lang='ch',
                )
                print(f"[OCR] OCRLocator initialized with local models from {model_dir}")

            # 检测 OCR 版本和 API
            # 强制使用旧的 ocr() API，因为 predict() 在某些环境下会崩溃
            self._use_predict_api = True
            print(f"[OCR] Using ocr() API (legacy version, forced for stability)")
            # 注释：predict() API 在 oneDNN 环境下可能不稳定，使用 ocr() 更稳定
        except ImportError as e:
            print(f"[OCR] PaddleOCR not installed. Install with: pip install paddleocr")
            raise ImportError(
                "PaddleOCR is required for OCR-based element location. "
                "Install with: pip install paddleocr"
            ) from e

    def capture_screenshot(self) -> np.ndarray:
        """Capture current screen as numpy array for OCR.

        OCR自己截图, 不依赖capture_node提供的截图。
        Returns:
            Numpy array of screenshot (BGR format for PaddleOCR).
        """
        try:
            # 使用pyautogui截图
            screenshot = pyautogui.screenshot()
            # 转换为numpy array (RGB)
            screenshot_array = np.array(screenshot)
            # 转换为BGR格式（PaddleOCR期望的格式）
            # 使用 ascontiguousarray 确保数组内存布局正确，防止 C++ 层崩溃
            screenshot_bgr = np.ascontiguousarray(screenshot_array[:, :, ::-1])
            print(f"[OCR] Captured screenshot: shape={screenshot_bgr.shape}, dtype={screenshot_bgr.dtype}, contiguous={screenshot_bgr.flags['C_CONTIGUOUS']}")
            return screenshot_bgr
        except Exception as e:
            print(f"[OCR] Failed to capture screenshot: {e}")
            return None

    def locate_element(
        self,
        target_text: str,
        screenshot: np.ndarray,
        threshold: float = 0.8
    ) -> Optional[Tuple[int, int]]:
        """Locate an element by finding matching text in screenshot.

        Args:
            target_text: The text to search for in the screenshot.
            screenshot: Numpy array of the screenshot (BGR format).
            threshold: Matching threshold (0.0-1.0). Defaults to 0.8.

        Returns:
            Tuple of (center_x, center_y) coordinates if found, None otherwise.
        """
        if screenshot is None or screenshot.size == 0:
            logger.warning("Empty screenshot provided")
            return None

        if not target_text:
            logger.warning("Empty target text provided")
            return None

        print(f"[OCR] locate_element called: target='{target_text}', screenshot shape={screenshot.shape if screenshot is not None else 'None'}")

        try:
            # Run OCR recognition
            if self._use_predict_api:
                print(f"[OCR] Calling predict()...")
                # 新版本 API: predict() 返回 list，第一个元素是 dict
                # list[0] = {'dt_polys': ..., 'rec_texts': ..., 'rec_scores': ..., 'rec_polys': ...}
                result_list = self.ocr.predict(screenshot)
                print(f"[OCR] predict() returned: {type(result_list)}, len={len(result_list) if result_list else 0}")

                if not result_list or len(result_list) == 0:
                    logger.debug("No text detected in screenshot")
                    return None

                result = result_list[0]  # 取第一个元素（字典）
                print(f"[OCR] result keys: {result.keys() if hasattr(result, 'keys') else 'N/A'}")

                if 'rec_texts' not in result or 'rec_polys' not in result:
                    logger.debug("No text detected in screenshot")
                    return None

                # 使用 rec_polys（识别多边形）而非 dt_polys（检测多边形），因为 rec_polys 更准确
                polyps = result['rec_polys']
                rec_texts = result['rec_texts']
                rec_scores = result.get('rec_scores', [1.0] * len(rec_texts))

                print(f"[OCR] Found {len(rec_texts)} texts, searching for '{target_text}'")
                
                for i, (box, text) in enumerate(zip(polyps, rec_texts)):
                    confidence = rec_scores[i] if i < len(rec_scores) else 1.0

                    if self._text_matches(target_text, text, threshold):
                        # box shape: (4, 2) array [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                        center_x = int((box[:, 0].min() + box[:, 0].max()) / 2)
                        center_y = int((box[:, 1].min() + box[:, 1].max()) / 2)

                        logger.info(
                            f"Found '{text}' matching '{target_text}' "
                            f"at ({center_x}, {center_y}) with confidence {confidence:.2f}"
                        )
                        return (center_x, center_y)
            else:
                # 旧版本 API: ocr() 返回 [[[box], (text, conf)], ...]
                print(f"[OCR] Calling ocr()...")
                result = self.ocr.ocr(screenshot)
                print(f"[OCR] ocr() returned: {type(result)}")

                if result is None or len(result) == 0 or result[0] is None:
                    logger.debug("No text detected in screenshot")
                    return None

                print(f"[OCR] Found {len(result[0])} texts, searching for '{target_text}'")

                for line in result[0]:
                    if line is None:
                        continue

                    box = line[0]
                    text_info = line[1]

                    if len(text_info) != 2:
                        continue

                    recognized_text, confidence = text_info

                    if self._text_matches(target_text, recognized_text, threshold):
                        center_x = int((box[0][0] + box[2][0]) / 2)
                        center_y = int((box[0][1] + box[2][1]) / 2)

                        logger.info(
                            f"Found '{recognized_text}' matching '{target_text}' "
                            f"at ({center_x}, {center_y}) with confidence {confidence:.2f}"
                        )
                        return (center_x, center_y)

            logger.debug(f"No matching text found for '{target_text}'")
            return None

        except Exception as e:
            logger.error(f"OCR recognition failed: {e}")
            return None

    def locate_all_elements(
        self,
        target_text: str,
        screenshot: np.ndarray,
        threshold: float = 0.8
    ) -> List[Tuple[int, int]]:
        """Locate all elements matching the target text.

        Args:
            target_text: The text to search for.
            screenshot: Numpy array of the screenshot.
            threshold: Matching threshold. Defaults to 0.8.

        Returns:
            List of (center_x, center_y) coordinate tuples.
        """
        if screenshot is None or screenshot.size == 0:
            return []

        try:
            # 只用新版本 OCR API: predict() 返回 list[dict]
            result_list = self.ocr.predict(screenshot)
            if not result_list or len(result_list) == 0:
                return []

            result = result_list[0]
            if 'rec_texts' not in result or 'rec_polys' not in result:
                return []

            # 使用 rec_polys（识别多边形）而非 dt_polys（检测多边形）
            polyps = result['rec_polys']
            rec_texts = result['rec_texts']
            rec_scores = result.get('rec_scores', [1.0] * len(rec_texts))

            coordinates = []
            for i, (box, text) in enumerate(zip(polyps, rec_texts)):
                confidence = rec_scores[i] if i < len(rec_scores) else 1.0
                if self._text_matches(target_text, text, threshold):
                    center_x = int((box[:, 0].min() + box[:, 0].max()) / 2)
                    center_y = int((box[:, 1].min() + box[:, 1].max()) / 2)
                    coordinates.append((center_x, center_y))

            return coordinates
        except Exception as e:
            logger.error(f"OCR recognition failed: {e}")
            return []

    def get_all_text(
        self,
        screenshot: np.ndarray
    ) -> List[Tuple[str, Tuple[int, int, int, int], float]]:
        """Get all recognized text from screenshot with positions.

        Args:
            screenshot: Numpy array of the screenshot.

        Returns:
            List of (text, (x, y, width, height), confidence) tuples.
        """
        if screenshot is None or screenshot.size == 0:
            return []

        try:
            if self._use_predict_api:
                # 新版本 API: predict() 返回 list[dict]
                result_list = self.ocr.predict(screenshot)
                if not result_list or len(result_list) == 0:
                    return []

                result = result_list[0]
                if 'rec_texts' not in result or 'rec_polys' not in result:
                    return []

                # 使用 rec_polys（识别多边形）而非 dt_polys（检测多边形）
                polyps = result['rec_polys']
                rec_texts = result['rec_texts']
                rec_scores = result.get('rec_scores', [1.0] * len(rec_texts))

                text_list = []
                for i, (box, text) in enumerate(zip(polyps, rec_texts)):
                    confidence = rec_scores[i] if i < len(rec_scores) else 1.0
                    x = int(box[:, 0].min())
                    y = int(box[:, 1].min())
                    w = int(box[:, 0].max() - box[:, 0].min())
                    h = int(box[:, 1].max() - box[:, 1].min())
                    text_list.append((text, (x, y, w, h), confidence))

                return text_list
            else:
                # 旧版本 API
                result = self.ocr.ocr(screenshot)

                if result is None or len(result) == 0 or result[0] is None:
                    return []

                text_list = []
                for line in result[0]:
                    if line is None:
                        continue

                    box = line[0]
                    text_info = line[1]

                    if len(text_info) != 2:
                        continue

                    text, confidence = text_info

                    # Calculate bounding box dimensions
                    x = int(box[0][0])
                    y = int(box[0][1])
                    width = int(box[1][0] - box[0][0])
                    height = int(box[2][1] - box[0][1])

                    text_list.append((text, (x, y, width, height), confidence))

                return text_list

        except Exception as e:
            logger.error(f"OCR recognition failed: {e}")
            return []

    @staticmethod
    def _text_matches(
        target: str,
        recognized: str,
        threshold: float
    ) -> bool:
        """Check if recognized text matches target text.
        Uses exact match (case-insensitive).
        Args:
            target: The target text to match.
            recognized: The recognized text from OCR.
            threshold: Matching threshold (unused for exact match).
        Returns:
            True if the texts match exactly (case-insensitive).
        """
        if not target or not recognized:
            return False

        # Normalize texts for comparison
        target_normalized = target.strip().lower()
        recognized_normalized = recognized.strip().lower()

        # Exact match only
        return target_normalized == recognized_normalized
