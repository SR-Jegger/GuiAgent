"""Tests for OCR Locator module."""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
import sys


# Test static methods without requiring OCR instance
def test_text_matches_exact():
    """测试完全匹配"""
    from utils.ocr_locator import OCRLocator
    assert OCRLocator._text_matches("测试", "测试", 1.0)
    assert not OCRLocator._text_matches("测试", "其他", 1.0)


def test_text_matches_contains():
    """测试包含匹配"""
    from utils.ocr_locator import OCRLocator
    assert OCRLocator._text_matches("测试", "这是测试文字", 0.8)
    assert OCRLocator._text_matches("测试", "测试文字", 0.8)


def test_text_matches_fuzzy():
    """测试模糊匹配"""
    from utils.ocr_locator import OCRLocator
    # 相似度匹配
    assert OCRLocator._text_matches("测试", "测试", 0.7)


def test_text_matches_empty():
    """测试空文本匹配"""
    from utils.ocr_locator import OCRLocator
    assert not OCRLocator._text_matches("", "测试", 0.8)
    assert not OCRLocator._text_matches("测试", "", 0.8)
    assert not OCRLocator._text_matches("", "", 0.8)


def test_text_matches_case_insensitive():
    """测试大小写不敏感"""
    from utils.ocr_locator import OCRLocator
    assert OCRLocator._text_matches("TEST", "test", 1.0)
    assert OCRLocator._text_matches("Test", "TEST", 1.0)


def test_calculate_similarity():
    """测试相似度计算"""
    from utils.ocr_locator import OCRLocator
    # 相同文本
    assert OCRLocator._calculate_similarity("abc", "abc") > 0.9
    # 完全不同
    assert OCRLocator._calculate_similarity("abc", "xyz") < 0.5
    # 空文本
    assert OCRLocator._calculate_similarity("", "test") == 0.0


@pytest.fixture
def mock_paddleocr_module():
    """Mock paddleocr module for testing without actual installation."""
    mock_ocr_class = MagicMock()
    mock_ocr_instance = MagicMock()
    mock_ocr_class.return_value = mock_ocr_instance

    mock_module = MagicMock()
    mock_module.PaddleOCR = mock_ocr_class

    # Save original module if exists
    original_module = sys.modules.get('paddleocr', None)

    # Install mock module
    sys.modules['paddleocr'] = mock_module

    yield mock_ocr_instance

    # Restore original module
    if original_module is not None:
        sys.modules['paddleocr'] = original_module
    else:
        del sys.modules['paddleocr']


def test_ocr_locator_init_with_mock(mock_paddleocr_module):
    """测试OCRLocator初始化（使用mock）"""
    # Re-import to get fresh class with mocked PaddleOCR
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator
    locator = OCRLocator()
    assert locator.ocr is not None


def test_locate_element_returns_none_for_empty_image(mock_paddleocr_module):
    """测试空图像返回None"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator
    locator = OCRLocator()

    empty_img = np.zeros((100, 100, 3), dtype=np.uint8)
    result = locator.locate_element("测试文字", empty_img)
    assert result is None


def test_locate_element_returns_none_for_no_text(mock_paddleocr_module):
    """测试无文字图像返回None"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    # Mock OCR returning no text
    mock_paddleocr_module.ocr.return_value = [None]

    locator = OCRLocator()
    test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    result = locator.locate_element("test", test_img)
    assert result is None


def test_locate_element_returns_coordinates_for_matching_text(mock_paddleocr_module):
    """测试匹配文字返回坐标"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    # Mock OCR result with matching text
    # Format: [[[box, (text, confidence)], ...]]
    mock_result = [
        [
            [
                [[10, 10], [110, 10], [110, 50], [10, 50]],  # box coordinates
                ("测试文字", 0.95)  # text and confidence
            ]
        ]
    ]
    mock_paddleocr_module.ocr.return_value = mock_result

    locator = OCRLocator()
    test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    result = locator.locate_element("测试文字", test_img)

    # Should return center coordinates
    assert result is not None
    assert isinstance(result, tuple)
    assert len(result) == 2
    # Center of box (10,10) to (110,50) is (60, 30)
    assert result == (60, 30)


def test_locate_element_partial_match(mock_paddleocr_module):
    """测试部分匹配"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    mock_result = [
        [
            [
                [[10, 10], [110, 10], [110, 50], [10, 50]],
                ("这是测试文字内容", 0.95)
            ]
        ]
    ]
    mock_paddleocr_module.ocr.return_value = mock_result

    locator = OCRLocator()
    test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    # Search for partial text
    result = locator.locate_element("测试文字", test_img)
    assert result is not None


def test_locate_all_elements(mock_paddleocr_module):
    """测试定位所有匹配元素"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    # Mock multiple matching texts
    mock_result = [
        [
            [
                [[10, 10], [110, 10], [110, 50], [10, 50]],
                ("测试", 0.95)
            ],
            [
                [[200, 10], [300, 10], [300, 50], [200, 50]],
                ("测试", 0.90)
            ]
        ]
    ]
    mock_paddleocr_module.ocr.return_value = mock_result

    locator = OCRLocator()
    test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    results = locator.locate_all_elements("测试", test_img)

    assert len(results) == 2
    assert results[0] == (60, 30)
    assert results[1] == (250, 30)


def test_get_all_text(mock_paddleocr_module):
    """测试获取所有文字"""
    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    mock_result = [
        [
            [
                [[10, 10], [110, 10], [110, 50], [10, 50]],
                ("Hello", 0.95)
            ],
            [
                [[200, 100], [400, 100], [400, 150], [200, 150]],
                ("World", 0.90)
            ]
        ]
    ]
    mock_paddleocr_module.ocr.return_value = mock_result

    locator = OCRLocator()
    test_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    results = locator.get_all_text(test_img)

    assert len(results) == 2
    assert results[0][0] == "Hello"
    assert results[1][0] == "World"


def test_ocr_locator_init_without_paddleocr():
    """测试没有PaddleOCR时初始化失败"""
    # Remove paddleocr from sys.modules temporarily
    original_module = sys.modules.get('paddleocr', None)
    if 'paddleocr' in sys.modules:
        del sys.modules['paddleocr']

    import importlib
    import utils.ocr_locator
    importlib.reload(utils.ocr_locator)

    from utils.ocr_locator import OCRLocator

    with pytest.raises(ImportError) as exc_info:
        OCRLocator()
    assert "PaddleOCR is required" in str(exc_info.value)

    # Restore original module
    if original_module is not None:
        sys.modules['paddleocr'] = original_module