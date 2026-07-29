#!/usr/bin/env python3
# encoding: utf-8
r"""normalize_chinese_numerals 单元测试

验证 ASR 同音汉字数字能正确转回阿拉伯数字，让下游 \d 正则、
target_id 子串匹配、kill_chain 缓存命中正常工作。

运行：
    python -m pytest scripts/test_normalize_chinese_numerals.py -v
或直接：
    python scripts/test_normalize_chinese_numerals.py
"""

import os
import sys

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.semantic.semantic_matcher import (
    normalize_chinese_numerals,
    chinese_to_number,
    extract_number_from_text,
)


# ============================================================================
# normalize_chinese_numerals 核心用例
# ============================================================================

def test_digit_by_digit_reading():
    """ASR 逐位读法：'八六八六' -> '8686'（用户原始问题场景）"""
    assert normalize_chinese_numerals("打击剑发八六八六") == "打击剑发8686"


def test_composite_reading():
    """复合读法：'二百零六' -> '206'"""
    assert normalize_chinese_numerals("执行二百零六平台") == "执行206平台"


def test_composite_yi_bai_ling_yi():
    """'一百零一' -> '101'"""
    assert normalize_chinese_numerals("确认一百零一") == "确认101"


def test_composite_er_shi_san():
    """'二十三' -> '23'"""
    assert normalize_chinese_numerals("二十三") == "23"


def test_yao_as_one():
    """'幺' 在通信/编号场景读作 1：'幺两零六' -> '1206'"""
    assert normalize_chinese_numerals("幺两零六") == "1206"


def test_liang_as_two():
    """'两' -> 2（原 CHINESE_NUM_MAP 缺这个，是本次修复的 bug）"""
    assert normalize_chinese_numerals("两零六") == "206"


def test_liang_in_composite():
    """'两百零六' -> '206'（复合读法里的'两'）"""
    assert normalize_chinese_numerals("两百零六") == "206"


def test_decimal_coordinate():
    """坐标小数：'九十点五九四' -> '90.594'"""
    assert normalize_chinese_numerals("经度九十点五九四") == "经度90.594"


def test_decimal_with_leading_zero():
    """'零点五' -> '0.5'"""
    assert normalize_chinese_numerals("零点五") == "0.5"


def test_decimal_only_fraction():
    r"""'点五' -> '.5'（首段空，下游 [\d.]+ 仍可命中）"""
    assert normalize_chinese_numerals("点五") == ".5"


def test_mixed_multiple_numerals():
    """一条指令里多个汉字数字子串"""
    assert (
        normalize_chinese_numerals("向八六平台发送经度九十点五纬度三十点二")
        == "向86平台发送经度90.5纬度30.2"
    )


def test_preserve_non_numeral_chinese():
    """非数字汉字不受影响"""
    assert normalize_chinese_numerals("打击目标") == "打击目标"
    assert normalize_chinese_numerals("确认杀伤链") == "确认杀伤链"


def test_already_arabic_unchanged():
    """已经是阿拉伯数字的不变"""
    assert normalize_chinese_numerals("打击目标剑发8686") == "打击目标剑发8686"


def test_empty_string():
    """空串安全返回"""
    assert normalize_chinese_numerals("") == ""


def test_none_safe():
    """None 入参不抛异常（部分代码路径可能传 None）"""
    # normalize_chinese_numerals 对 None 不做特殊处理，但 falsy 值直接返回
    assert normalize_chinese_numerals(None) is None


def test_single_digit():
    """单个汉字数字"""
    assert normalize_chinese_numerals("五") == "5"


def test_shi_alone():
    """'十' 单独出现 -> 10"""
    assert normalize_chinese_numerals("十") == "10"


def test_idempotent():
    """归一化结果再归一化不变（幂等）"""
    once = normalize_chinese_numerals("打击剑发八六八六")
    twice = normalize_chinese_numerals(once)
    assert once == twice


def test_non_numeral_chars_break_runs():
    """非数字汉字（不在扫描集）打断 run，各段独立转换。

    '八六abc六' -> '八六' 和 '六' 两个独立 run，'abc' 原样保留。
    """
    assert normalize_chinese_numerals("八六abc六") == "86abc6"


def test_dian_alone_preserved():
    """'点' 单独出现（无相邻数字）不转换为 '.'，避免破坏'打点击'这类词。"""
    assert normalize_chinese_numerals("打点击") == "打点击"
    assert normalize_chinese_numerals("点") == "点"
    assert normalize_chinese_numerals("点点") == "点点"


def test_dian_with_adjacent_digit_converts():
    """'点' 与数字字符相邻时正常作为小数分隔"""
    assert normalize_chinese_numerals("八点五") == "8.5"
    assert normalize_chinese_numerals("点五") == ".5"


# ============================================================================
# chinese_to_number 直接验证（normalize 的 building block）
# ============================================================================

def test_chinese_to_number_digit_by_digit():
    assert chinese_to_number("八六八六") == 8686


def test_chinese_to_number_composite():
    assert chinese_to_number("二百零六") == 206
    assert chinese_to_number("一百零一") == 101
    assert chinese_to_number("二十三") == 23


def test_chinese_to_number_with_liang():
    assert chinese_to_number("两百零六") == 206
    assert chinese_to_number("两零六") == 206


def test_chinese_to_number_invalid():
    assert chinese_to_number("abc") is None
    assert chinese_to_number("") is None


# ============================================================================
# 下游集成：归一化后 extract_number_from_text 能正确抽参数
# ============================================================================

def test_extract_after_normalize_kill_chain_target():
    r"""归一化后，杀伤链 target_id 的正则能命中。

    用户原始问题：'打击剑发八六八六' 直接喂 pattern 会 miss，
    归一化后 '打击剑发8686' 能被 ([一-龥]{2}-?\d{4,}) 抓到。
    """
    text = normalize_chinese_numerals("打击剑发八六八六")
    pattern = "([\\u4e00-\\u9fa5]{2}-?\\d{4,})"
    assert extract_number_from_text(text, pattern) == "剑发8686"


def test_extract_after_normalize_platform():
    """归一化后，'执行([0-9]+)' 能从 '执行八六' 抽出 86"""
    text = normalize_chinese_numerals("执行八六平台")
    assert extract_number_from_text(text, "执行([0-9]+)") == "86"


def test_extract_after_normalize_coordinate():
    """归一化后，'经度([\\d.]+)' 能抽 '90.594'"""
    text = normalize_chinese_numerals("经度九十点五九四")
    assert extract_number_from_text(text, "经度([\\d.]+)") == "90.594"


# ============================================================================
# 手动跑（不依赖 pytest）
# ============================================================================

def _run_all_manually():
    """没有 pytest 时手动跑所有 test_ 函数"""
    failures = 0
    passed = 0
    g = globals()
    for name in sorted(g.keys()):
        if name.startswith("test_") and callable(g[name]):
            try:
                g[name]()
                print(f"  [OK] {name}")
                passed += 1
            except AssertionError as e:
                print(f"  [FAIL] {name}: {e}")
                failures += 1
            except Exception as e:
                print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
                failures += 1
    print(f"\n=== {passed} passed, {failures} failed ===")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all_manually() else 0)
