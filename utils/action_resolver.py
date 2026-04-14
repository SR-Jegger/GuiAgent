"""Action Resolver module for placeholder resolution.

This module provides placeholder resolution for dynamic coordinates,
supporting OCR-based location, regex capture groups, and coordinate offsets.

Supported placeholders:
    {{ocr:text}}         - OCR locate text coordinates
    {{match_group_n}}    - Regex capture group n content
    {{prev_x}}           - Previous step X coordinate
    {{prev_y}}           - Previous step Y coordinate
    {{prev_x+n}}         - X coordinate with positive offset
    {{prev_y+n}}         - Y coordinate with positive offset
    {{prev_x-n}}         - X coordinate with negative offset
    {{prev_y-n}}         - Y coordinate with negative offset
"""
import logging
import re
from typing import Optional, Tuple, List, Dict, Any, Union

import numpy as np

logger = logging.getLogger(__name__)

# Placeholder patterns
PLACEHOLDER_PATTERN = re.compile(r'\{\{([^}]+)\}\}')
OCR_PLACEHOLDER_PATTERN = re.compile(r'\{\{ocr:([^}]+)\}\}')
MATCH_GROUP_PATTERN = re.compile(r'\{\{match_group_(\d+)\}\}')
OFFSET_PATTERN = re.compile(r'\{\{prev_(x|y)([+-]\d+)?\}\}')


class ActionResolver:
    """Resolver for action placeholders.

    Resolves dynamic placeholders in action coordinates and text fields,
    supporting OCR location, regex capture groups, and coordinate offsets.

    Attributes:
        ocr_locator: OCRLocator instance for OCR-based coordinate resolution.
        prev_x: Previous resolved X coordinate.
        prev_y: Previous resolved Y coordinate.
    """

    def __init__(self, ocr_locator: Optional[Any] = None):
        """Initialize ActionResolver.

        Args:
            ocr_locator: Optional OCRLocator instance for OCR placeholder resolution.
        """
        self.ocr_locator = ocr_locator
        self.prev_x: Optional[int] = None
        self.prev_y: Optional[int] = None
        logger.debug("ActionResolver initialized")

    def set_prev_coordinate(self, x: int, y: int) -> None:
        """Set the previous coordinate for offset calculations.

        Args:
            x: X coordinate.
            y: Y coordinate.
        """
        self.prev_x = x
        self.prev_y = y
        logger.debug(f"Set previous coordinate: ({x}, {y})")

    def resolve_actions(
        self,
        actions: List[Dict[str, Any]],
        match_groups: Dict[str, str],
        screenshot: Optional[np.ndarray]
    ) -> List[Dict[str, Any]]:
        """Resolve all placeholders in action list.

        Args:
            actions: List of action dictionaries with potential placeholders.
            match_groups: Dict mapping capture group numbers to their values.
            screenshot: Optional screenshot for OCR-based resolution.

        Returns:
            List of resolved action dictionaries with concrete coordinates.

        Raises:
            ValueError: If OCR placeholder used without OCR locator.
        """
        if not actions:
            return []

        resolved_actions = []
        for action in actions:
            resolved_action = action.copy()

            # Resolve coordinate field
            if "coordinate" in action:
                coord = action["coordinate"]
                resolved_coord = self._resolve_coordinate(
                    coord, match_groups, screenshot
                )
                resolved_action["coordinate"] = resolved_coord

                # Update previous coordinate for subsequent actions
                if resolved_coord is not None:
                    self.set_prev_coordinate(resolved_coord[0], resolved_coord[1])

            # Resolve text field (for match groups)
            if "text" in action and isinstance(action["text"], str):
                resolved_action["text"] = self._replace_match_groups(
                    action["text"], match_groups
                )

            resolved_actions.append(resolved_action)

        return resolved_actions

    def _resolve_coordinate(
        self,
        coord_expr: Union[List, Tuple, str],
        match_groups: Dict[str, str],
        screenshot: Optional[np.ndarray]
    ) -> Optional[Tuple[int, int]]:
        """Resolve a coordinate expression.

        Args:
            coord_expr: Coordinate expression (list, tuple, or string with placeholders).
            match_groups: Dict mapping capture group numbers to their values.
            screenshot: Optional screenshot for OCR-based resolution.

        Returns:
            Tuple of (x, y) coordinates if resolution successful, None otherwise.

        Raises:
            ValueError: If OCR placeholder used without OCR locator.
        """
        # Fixed coordinate list/tuple
        if isinstance(coord_expr, (list, tuple)):
            if len(coord_expr) >= 2:
                try:
                    return (int(coord_expr[0]), int(coord_expr[1]))
                except (ValueError, TypeError):
                    logger.warning(f"Invalid fixed coordinate: {coord_expr}")
                    return None
            return None

        # String coordinate expression
        if isinstance(coord_expr, str):
            # Check for OCR placeholder
            ocr_match = OCR_PLACEHOLDER_PATTERN.search(coord_expr)
            if ocr_match:
                target_text = ocr_match.group(1)
                return self._resolve_ocr_coordinate(target_text, screenshot)

            # Replace match groups first
            resolved_expr = self._replace_match_groups(coord_expr, match_groups)

            # Check for offset placeholders (prev_x/prev_y)
            offset_result = self._resolve_offset_expression(resolved_expr)
            if offset_result:
                return offset_result

            # Try parsing as numeric coordinate string
            return self._parse_coordinate_string(resolved_expr)

        return None

    def _resolve_ocr_coordinate(
        self,
        target_text: str,
        screenshot: Optional[np.ndarray]
    ) -> Optional[Tuple[int, int]]:
        """Resolve OCR-based coordinate.

        Args:
            target_text: Text to locate via OCR.
            screenshot: Screenshot for OCR recognition.

        Returns:
            Tuple of (x, y) coordinates if found, None otherwise.

        Raises:
            ValueError: If OCR locator not available.
        """
        if self.ocr_locator is None:
            raise ValueError(
                "OCR locator required for OCR placeholder resolution. "
                "Initialize ActionResolver with ocr_locator parameter."
            )

        if screenshot is None:
            logger.warning("Screenshot required for OCR placeholder resolution")
            return None

        try:
            result = self.ocr_locator.locate_element(target_text, screenshot)
            if result:
                logger.info(f"OCR located '{target_text}' at {result}")
                return result
            else:
                logger.warning(f"OCR could not locate '{target_text}'")
                return None
        except Exception as e:
            logger.error(f"OCR resolution failed for '{target_text}': {e}")
            return None

    def _resolve_offset_expression(
        self,
        expr: str
    ) -> Optional[Tuple[int, int]]:
        """Resolve coordinate expression with offset placeholders.

        Args:
            expr: Expression potentially containing prev_x/prev_y placeholders.

        Returns:
            Tuple of (x, y) coordinates if offset resolution successful, None otherwise.
        """
        # Check if expression contains offset placeholders
        matches = OFFSET_PATTERN.findall(expr)

        if not matches:
            return None

        # If prev_x or prev_y not set, cannot resolve
        if self.prev_x is None or self.prev_y is None:
            logger.warning(
                "Cannot resolve offset expression: no previous coordinate set"
            )
            return None

        # Handle single placeholder case (like "{{prev_x+20}}")
        if len(matches) == 1:
            axis, offset_str = matches[0]
            offset = int(offset_str) if offset_str else 0

            if axis == 'x':
                return (self.prev_x + offset, self.prev_y)
            else:
                return (self.prev_x, self.prev_y + offset)

        # Handle dual placeholder case (like "{{prev_x+10}},{{prev_y-20}}")
        x_offset = 0
        y_offset = 0

        for axis, offset_str in matches:
            offset = int(offset_str) if offset_str else 0
            if axis == 'x':
                x_offset = offset
            else:
                y_offset = offset

        return (self.prev_x + x_offset, self.prev_y + y_offset)

    def _replace_match_groups(
        self,
        expr: str,
        match_groups: Dict[str, str]
    ) -> str:
        """Replace match group placeholders in expression.

        Args:
            expr: Expression with potential {{match_group_n}} placeholders.
            match_groups: Dict mapping capture group numbers to their values.

        Returns:
            Expression with placeholders replaced by actual values.
        """
        if not expr:
            return expr

        result = expr
        for match in MATCH_GROUP_PATTERN.finditer(expr):
            group_num = match.group(1)
            placeholder = match.group(0)

            if group_num in match_groups:
                value = match_groups[group_num]
                result = result.replace(placeholder, value)
                logger.debug(f"Replaced {placeholder} with '{value}'")
            else:
                logger.debug(f"Match group {group_num} not found, leaving placeholder")

        return result

    def _parse_offset_expression(
        self,
        expr: str
    ) -> Optional[Tuple[str, int]]:
        """Parse offset expression to extract axis and offset value.

        Args:
            expr: Expression to parse (e.g., "{{prev_x+20}}").

        Returns:
            Tuple of (axis, offset) if valid offset expression, None otherwise.
        """
        match = OFFSET_PATTERN.match(expr)
        if match:
            axis = f"prev_{match.group(1)}"
            offset_str = match.group(2)
            offset = int(offset_str) if offset_str else 0
            return (axis, offset)
        return None

    def _parse_coordinate_string(
        self,
        expr: str
    ) -> Optional[Tuple[int, int]]:
        """Parse coordinate string to numeric values.

        Args:
            expr: Coordinate string (e.g., "100,200" or "100, 200").

        Returns:
            Tuple of (x, y) coordinates if valid, None otherwise.
        """
        # Remove any remaining placeholders (unresolved)
        if PLACEHOLDER_PATTERN.search(expr):
            logger.warning(f"Unresolved placeholder in coordinate: {expr}")
            return None

        # Try comma-separated format
        parts = expr.split(',')
        if len(parts) == 2:
            try:
                x = int(parts[0].strip())
                y = int(parts[1].strip())
                return (x, y)
            except ValueError:
                logger.warning(f"Invalid coordinate string: {expr}")
                return None

        return None


def needs_resolution(actions: List[Dict[str, Any]]) -> bool:
    """Check if action list contains any placeholders requiring resolution.

    Args:
        actions: List of action dictionaries.

    Returns:
        True if any action contains a placeholder, False otherwise.
    """
    if not actions:
        return False

    for action in actions:
        # Check coordinate field
        if "coordinate" in action:
            coord = action["coordinate"]
            if isinstance(coord, str) and PLACEHOLDER_PATTERN.search(coord):
                return True

        # Check text field for match groups
        if "text" in action and isinstance(action["text"], str):
            if MATCH_GROUP_PATTERN.search(action["text"]):
                return True

    return False