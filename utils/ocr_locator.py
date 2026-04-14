"""OCR Locator module for text-based element positioning.

This module provides OCR-based element location using PaddleOCR,
supporting exact, partial, and fuzzy text matching.
"""
import logging
from typing import Optional, Tuple, List, Any

import numpy as np

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
            self.ocr = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                use_gpu=use_gpu,
                show_log=False
            )
            logger.info(f"OCRLocator initialized (use_gpu={use_gpu})")
        except ImportError as e:
            logger.error("PaddleOCR not installed. Install with: pip install paddleocr")
            raise ImportError(
                "PaddleOCR is required for OCR-based element location. "
                "Install with: pip install paddleocr"
            ) from e

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

        try:
            # Run OCR recognition
            result = self.ocr.ocr(screenshot, cls=True)

            if result is None or len(result) == 0 or result[0] is None:
                logger.debug("No text detected in screenshot")
                return None

            # Search for matching text
            for line in result[0]:
                if line is None:
                    continue

                box = line[0]  # Bounding box coordinates
                text_info = line[1]  # (text, confidence)

                if len(text_info) != 2:
                    continue

                recognized_text, confidence = text_info

                if self._text_matches(target_text, recognized_text, threshold):
                    # Calculate center of bounding box
                    # box format: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
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
            result = self.ocr.ocr(screenshot, cls=True)

            if result is None or len(result) == 0 or result[0] is None:
                return []

            coordinates = []
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
            result = self.ocr.ocr(screenshot, cls=True)

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

        Supports three matching strategies:
        1. Exact match (threshold == 1.0)
        2. Contains match (target in recognized)
        3. Fuzzy similarity match (using similarity ratio)

        Args:
            target: The target text to match.
            recognized: The recognized text from OCR.
            threshold: Matching threshold (0.0-1.0).

        Returns:
            True if the texts match according to threshold criteria.
        """
        if not target or not recognized:
            return False

        # Normalize texts for comparison
        target_normalized = target.strip().lower()
        recognized_normalized = recognized.strip().lower()

        # Exact match
        if threshold >= 1.0:
            return target_normalized == recognized_normalized

        # Contains match (target is substring of recognized)
        if target_normalized in recognized_normalized:
            return True

        # Reverse contains match (recognized is substring of target)
        if recognized_normalized in target_normalized:
            return True

        # Fuzzy similarity match using character overlap ratio
        similarity = OCRLocator._calculate_similarity(target_normalized, recognized_normalized)
        return similarity >= threshold

    @staticmethod
    def _calculate_similarity(text1: str, text2: str) -> float:
        """Calculate character-level similarity between two texts.

        Uses a simple character overlap ratio with position consideration.

        Args:
            text1: First text.
            text2: Second text.

        Returns:
            Similarity ratio (0.0-1.0).
        """
        if not text1 or not text2:
            return 0.0

        # Use longest common subsequence ratio
        len1 = len(text1)
        len2 = len(text2)

        # Simple character set overlap for quick matching
        set1 = set(text1)
        set2 = set(text2)

        if not set1 or not set2:
            return 0.0

        # Calculate intersection ratio
        intersection = len(set1 & set2)
        union = len(set1 | set2)

        if union == 0:
            return 0.0

        # Jaccard similarity
        jaccard = intersection / union

        # Also consider length similarity
        length_ratio = min(len1, len2) / max(len1, len2)

        # Combined score
        return (jaccard * 0.6 + length_ratio * 0.4)