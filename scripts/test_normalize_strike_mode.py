#!/usr/bin/env python3
# encoding: utf-8
r"""normalize_strike_mode 单元测试

验证 ASR 把打击方式念成别名时，能正确归一化回页面字面（金地打击/KVD打击/远火打击），
让下游 _resolve_strike_mode（task_decomposer_node.py）能识别指令。

运行：
    python -m pytest scripts/test_normalize_strike_mode.py -v
或直接：
    python scripts/test_normalize_strike_mode.py
"""

import os
import sys

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.semantic.semantic_matcher import normalize_strike_mode


# ============================================================================
# normalize_strike_mode 核心用例
# ============================================================================

def test_jindijia_variants():
    """金地打击同义词覆盖：2 种念法都归一化到 金地打击"""
    assert normalize_strike_mode("金地打击") == "金地打击"
    assert normalize_strike_mode("金地自杀") == "金地打击"


def test_kvd_variants():
    """KVD打击同义词覆盖：2 种念法都归一化到 KVD打击"""
    assert normalize_strike_mode("KVD打击") == "KVD打击"
    assert normalize_strike_mode("KVD") == "KVD打击"


def test_yuanhuo_variants():
    """远火打击同义词覆盖"""
    assert normalize_strike_mode("远火剑发8686") == "远火打击剑发8686"
    assert normalize_strike_mode("远程火箭打击") == "远火打击"  # 长 alias 整体替换
    assert normalize_strike_mode("远程火箭") == "远火打击"


# ============================================================================
# 长同义词优先（关键边界 case）
# ============================================================================

def test_long_alias_priority():
    """长同义词优先：'远程火箭打击' 整体替换为 '远火打击'，不被 '远程火箭' 拆成 '远火打击打击'。

    如果短优先，'远程火箭打击' 会先匹配 '远程火箭' -> '远火打击'，剩 '打击'，
    结果变成 '远火打击打击'。长优先确保整体替换。
    """
    assert normalize_strike_mode("远程火箭打击") == "远火打击"


def test_long_before_short_in_same_text():
    """同一文本里长同义词和短同义词都出现时，长先替换，避免误吃。

    '远程火箭打击和远程火箭' -> '远火打击和远火打击'（长 '远程火箭打击' 先吃，再 '远程火箭' -> '远火打击'）
    """
    assert normalize_strike_mode("远程火箭打击和远程火箭") == "远火打击和远火打击"


# ============================================================================
# 幂等性
# ============================================================================

def test_idempotent_canonical():
    """已是页面字面的再调一次不变"""
    assert normalize_strike_mode("金地打击") == "金地打击"
    assert normalize_strike_mode("KVD打击") == "KVD打击"
    assert normalize_strike_mode("远火打击") == "远火打击"
    assert normalize_strike_mode("KVD打击剑发8686") == "KVD打击剑发8686"


def test_idempotent_double_call():
    """连续调两次结果不变"""
    once = normalize_strike_mode("金地自杀剑发8686")
    twice = normalize_strike_mode(once)
    assert once == twice == "金地打击剑发8686"


# ============================================================================
# 多匹配与无匹配
# ============================================================================

def test_multiple_matches_in_one_instruction():
    """一条指令里出现多个打击方式"""
    assert normalize_strike_mode("金地自杀和KVD") == "金地打击和KVD打击"


def test_no_strike_keyword():
    """无打击方式关键词的文本不变"""
    assert normalize_strike_mode("打击剑发8686") == "打击剑发8686"
    assert normalize_strike_mode("确认206") == "确认206"
    assert normalize_strike_mode("打击目标") == "打击目标"


# ============================================================================
# 边界 case
# ============================================================================

def test_empty_string():
    """空字符串"""
    assert normalize_strike_mode("") == ""


def test_none():
    """None 输入（与 normalize_chinese_numerals 行为一致）"""
    assert normalize_strike_mode(None) is None


# ============================================================================
# 集成场景：与 _resolve_strike_mode 配合的预期输入
# ============================================================================

def test_integration_jindijia_instruction():
    """归一化后能被 _resolve_strike_mode 识别为金地打击指令。

    _resolve_strike_mode 在 instruction 里找 '金地打击'，
    归一化后 '金地打击剑发8686' 含 '金地打击'，命中。
    """
    normalized = normalize_strike_mode("金地自杀剑发8686")
    assert "金地打击" in normalized


def test_integration_kvd_instruction():
    """归一化后能被 _resolve_strike_mode 识别为 KVD打击指令"""
    normalized = normalize_strike_mode("KVD剑发8686")
    assert "KVD打击" in normalized


def test_integration_yuanhuo_instruction():
    """归一化后能被 _resolve_strike_mode 识别为远火打击指令"""
    normalized = normalize_strike_mode("远火剑发8686")
    assert "远火" in normalized


# ============================================================================
# 主入口
# ============================================================================

if __name__ == "__main__":
    # 直接运行时逐个调用测试函数
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for func in test_funcs:
        try:
            func()
            print(f"  PASS  {func.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {func.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
