"""Tests for Action Resolver module.

Tests placeholder resolution for dynamic coordinates:
- {{ocr:text}} - OCR locate text
- {{match_group_n}} - Regex capture group n
- {{prev_x}} / {{prev_y}} - Previous step coordinates
- {{prev_x+n}} / {{prev_y+n}} - Coordinate offsets
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
import re


# Test needs_resolution function
class TestNeedsResolution:
    """Test detection of placeholders in actions."""

    def test_no_placeholder(self):
        """Fixed coordinates should return False."""
        from utils.action_resolver import needs_resolution

        actions = [
            {"action": "click", "coordinate": [100, 200]},
            {"action": "type", "text": "hello"},
        ]
        assert not needs_resolution(actions)

    def test_ocr_placeholder(self):
        """OCR placeholder should be detected."""
        from utils.action_resolver import needs_resolution

        actions = [{"action": "click", "coordinate": "{{ocr:登录按钮}}"}]
        assert needs_resolution(actions)

    def test_match_group_placeholder(self):
        """Match group placeholder should be detected."""
        from utils.action_resolver import needs_resolution

        actions = [{"action": "type", "text": "{{match_group_1}}"}]
        assert needs_resolution(actions)

    def test_prev_coordinate_placeholder(self):
        """Previous coordinate placeholder should be detected."""
        from utils.action_resolver import needs_resolution

        actions = [{"action": "click", "coordinate": "{{prev_x+20}}"}]
        assert needs_resolution(actions)

    def test_mixed_actions(self):
        """Mixed actions with placeholder should be detected."""
        from utils.action_resolver import needs_resolution

        actions = [
            {"action": "click", "coordinate": [100, 200]},
            {"action": "click", "coordinate": "{{ocr:确定}}"},
        ]
        assert needs_resolution(actions)

    def test_empty_actions(self):
        """Empty actions should return False."""
        from utils.action_resolver import needs_resolution

        assert not needs_resolution([])

    def test_no_coordinate_field(self):
        """Actions without coordinate field should return False."""
        from utils.action_resolver import needs_resolution

        actions = [{"action": "scroll", "direction": "down"}]
        assert not needs_resolution(actions)


# Test _replace_match_groups function
class TestReplaceMatchGroups:
    """Test regex capture group replacement."""

    def test_single_match_group(self):
        """Replace single match group."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._replace_match_groups(
            "{{match_group_1}}", {"1": "value1"}
        )
        assert result == "value1"

    def test_multiple_match_groups(self):
        """Replace multiple match groups."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._replace_match_groups(
            "{{match_group_1}} and {{match_group_2}}",
            {"1": "value1", "2": "value2"}
        )
        assert result == "value1 and value2"

    def test_missing_match_group(self):
        """Missing match group should return original expression."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._replace_match_groups(
            "{{match_group_3}}", {"1": "value1"}
        )
        assert result == "{{match_group_3}}"

    def test_empty_match_groups(self):
        """Empty match groups dict should return original."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._replace_match_groups(
            "{{match_group_1}}", {}
        )
        assert result == "{{match_group_1}}"

    def test_no_placeholders(self):
        """Expression without placeholders should return unchanged."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._replace_match_groups(
            "plain text", {"1": "value1"}
        )
        assert result == "plain text"




# Test _resolve_coordinate function
class TestResolveCoordinate:
    """Test coordinate resolution."""

    def test_fixed_coordinate_list(self):
        """Fixed coordinate list should return unchanged."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._resolve_coordinate([100, 200], {}, None)
        assert result == (100, 200)

    def test_fixed_coordinate_tuple(self):
        """Fixed coordinate tuple should return unchanged."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._resolve_coordinate((100, 200), {}, None)
        assert result == (100, 200)

    def test_match_group_in_coordinate(self):
        """Match group in coordinate string should be replaced."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        # Simulating coordinate extracted from match group
        result = resolver._resolve_coordinate(
            "{{match_group_1}},{{match_group_2}}",
            {"1": "100", "2": "200"},
            None
        )
        assert result == (100, 200)

    def test_prev_x_offset_with_context(self):
        """Previous X coordinate with offset."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        resolver.set_prev_coordinate(50, 100)
        result = resolver._resolve_coordinate("{{prev_x+20}}", {}, None)
        assert result == (70, 100)

    def test_prev_y_offset_with_context(self):
        """Previous Y coordinate with offset."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        resolver.set_prev_coordinate(50, 100)
        result = resolver._resolve_coordinate("{{prev_y+30}}", {}, None)
        assert result == (50, 130)

    def test_prev_xy_both_offsets(self):
        """Both prev_x and prev_y with offsets."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        resolver.set_prev_coordinate(100, 200)
        result = resolver._resolve_coordinate("{{prev_x+10}},{{prev_y-20}}", {}, None)
        assert result == (110, 180)


# Test resolve_actions function
class TestResolveActions:
    """Test full action list resolution."""

    def test_fixed_coordinates(self):
        """Actions with fixed coordinates should pass unchanged (tuple format)."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        actions = [
            {"action": "click", "coordinate": [100, 200]},
            {"action": "type", "text": "hello"},
        ]
        result = resolver.resolve_actions(actions, {}, None)
        # Coordinates are converted to tuples (immutable)
        assert result[0]["coordinate"] == (100, 200)
        assert result[1]["text"] == "hello"

    def test_match_group_replacement(self):
        """Match groups should be replaced in actions."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        actions = [
            {"action": "type", "text": "{{match_group_1}}"},
        ]
        result = resolver.resolve_actions(actions, {"1": "hello"}, None)
        assert result[0]["text"] == "hello"

    def test_coordinate_resolution_sequence(self):
        """Coordinate resolution should update prev coordinate."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        actions = [
            {"action": "click", "coordinate": [100, 200]},
            {"action": "click", "coordinate": "{{prev_x+50}}"},
        ]
        result = resolver.resolve_actions(actions, {}, None)
        assert result[1]["coordinate"] == (150, 200)

    def test_empty_actions(self):
        """Empty actions should return empty list."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver.resolve_actions([], {}, None)
        assert result == []


# Test OCR placeholder resolution
class TestOCRPlaceholderResolution:
    """Test OCR-based coordinate resolution."""

    @pytest.fixture
    def mock_ocr_locator(self):
        """Create mock OCR locator."""
        mock_locator = MagicMock()
        mock_locator.locate_element.return_value = (150, 250)
        return mock_locator

    def test_ocr_placeholder_resolution(self, mock_ocr_locator):
        """OCR placeholder should resolve to located coordinates."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver(ocr_locator=mock_ocr_locator)
        actions = [
            {"action": "click", "coordinate": "{{ocr:登录按钮}}"},
        ]
        test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        result = resolver.resolve_actions(actions, {}, test_img)

        assert result[0]["coordinate"] == (150, 250)
        mock_ocr_locator.locate_element.assert_called_once_with(
            "登录按钮", test_img
        )

    def test_ocr_not_found_returns_none(self, mock_ocr_locator):
        """OCR not finding text should handle gracefully."""
        from utils.action_resolver import ActionResolver

        mock_ocr_locator.locate_element.return_value = None
        resolver = ActionResolver(ocr_locator=mock_ocr_locator)
        actions = [
            {"action": "click", "coordinate": "{{ocr:不存在文字}}"},
        ]
        test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        result = resolver.resolve_actions(actions, {}, test_img)

        # Should handle None gracefully, coordinate should be None or unchanged
        assert result[0]["coordinate"] is None

    def test_ocr_without_locator_raises_error(self):
        """OCR placeholder without OCR locator should raise error."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver(ocr_locator=None)
        actions = [
            {"action": "click", "coordinate": "{{ocr:按钮}}"},
        ]
        test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255

        with pytest.raises(ValueError) as exc_info:
            resolver.resolve_actions(actions, {}, test_img)
        assert "OCR locator" in str(exc_info.value)


# Test edge cases
class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_coordinate_string_parsing(self):
        """Coordinate string with comma separation should parse correctly."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._resolve_coordinate("100,200", {}, None)
        assert result == (100, 200)

    def test_coordinate_with_spaces(self):
        """Coordinate string with spaces should parse correctly."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._resolve_coordinate("100, 200", {}, None)
        assert result == (100, 200)

    def test_invalid_coordinate_string(self):
        """Invalid coordinate string should return None."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        result = resolver._resolve_coordinate("abc,def", {}, None)
        assert result is None

    def test_coordinate_out_of_bounds_negative(self):
        """Negative coordinate offset should be handled."""
        from utils.action_resolver import ActionResolver

        resolver = ActionResolver()
        resolver.set_prev_coordinate(10, 10)
        result = resolver._resolve_coordinate("{{prev_x-5}}", {}, None)
        # Negative result may be clamped or passed through
        assert result == (5, 10)

    def test_multiple_ocr_placeholders(self):
        """Multiple OCR placeholders should each be resolved."""
        from utils.action_resolver import ActionResolver

        mock_locator = MagicMock()
        mock_locator.locate_element.side_effect = [(100, 100), (200, 200)]
        resolver = ActionResolver(ocr_locator=mock_locator)
        actions = [
            {"action": "click", "coordinate": "{{ocr:按钮1}}"},
            {"action": "click", "coordinate": "{{ocr:按钮2}}"},
        ]
        test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        result = resolver.resolve_actions(actions, {}, test_img)

        assert result[0]["coordinate"] == (100, 100)
        assert result[1]["coordinate"] == (200, 200)