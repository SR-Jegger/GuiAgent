#!/usr/bin/env python3
# encoding: utf-8
"""
测试规则匹配功能

使用方法:
    python test_rule_matcher.py
"""

import os
import sys
import io

# 设置标准输出为 UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning.rule_matcher import RuleMatcher


def test_basic_match():
    """测试基本匹配功能"""
    print("\n" + "=" * 60)
    print("测试 1: 基本规则匹配")
    print("=" * 60)

    matcher = RuleMatcher("./rules")

    test_cases = [
        ("关闭窗口", True),
        ("退出当前窗口", True),
        ("向下滚动", True),
        ("往上翻一页", True),
        ("复制这段文字", True),
        ("粘贴内容", True),
        ("打开浏览器", False),  # 应该不匹配
        ("点击提交按钮", False),  # 应该不匹配
    ]

    for instruction, should_match in test_cases:
        result = matcher.match(instruction)
        matched = result is not None
        status = "[OK]" if matched == should_match else "[FAIL]"
        match_str = "匹配" if matched else "不匹配"
        expect_str = "匹配" if should_match else "不匹配"
        print(f"{status} \"{instruction}\" -> {match_str} (期望：{expect_str})")
        if result and matched:
            print(f"   规则：{result['rule_name']}")
            print(f"   动作：{result['actions']}")


def test_regex_capture():
    """测试正则捕获组"""
    print("\n" + "=" * 60)
    print("测试 2: 正则捕获组 (需要添加带捕获组的规则)")
    print("=" * 60)

    # 添加一个带捕获组的规则
    matcher = RuleMatcher("./rules", auto_load=False)
    matcher.add_rule({
        "id": "search_in_bilibili",
        "name": "在 B 站搜索",
        "description": "在 B ilibili 网站搜索视频",
        "trigger": {
            "patterns": [
                r"在 (?:B 站|bilibili) 搜索 (.+)",
                r"在 B 站搜 (.+)"
            ]
        },
        "actions": [
            {
                "type": "click",
                "target": {"type": "template", "path": "search_box.png"}
            },
            {
                "type": "type",
                "text": "{{match_group_1}}"
            },
            {
                "type": "key",
                "keys": ["enter"]
            }
        ],
        "enabled": True
    })

    test_cases = [
        ("在 B 站搜索 发射邓总", "发射邓总"),
        ("在 bilibili 搜索 罗翔说刑法", "罗翔说刑法"),
        ("在 B 站搜 张三法考", "张三法考"),
    ]

    for instruction, expected_term in test_cases:
        result = matcher.match(instruction)
        if result:
            # 检查 type 为 type 的动作中的 text 字段
            for action in result['actions']:
                if action.get('type') == 'type':
                    text = action.get('text', '')
                    if expected_term in text:
                        print(f"[OK] \"{instruction}\" -> 捕获组：{expected_term}")
                    else:
                        print(f"[FAIL] \"{instruction}\" -> 捕获组：{text} (期望包含：{expected_term})")
        else:
            print(f"[FAIL] \"{instruction}\" -> 无匹配")


def test_action_chain():
    """测试动作链执行"""
    print("\n" + "=" * 60)
    print("测试 3: 动作链结构")
    print("=" * 60)

    matcher = RuleMatcher("./rules")

    result = matcher.match("关闭窗口")
    if result:
        print(f"规则：{result['rule_name']}")
        print(f"动作数量：{len(result['actions'])}")
        for i, action in enumerate(result['actions'], 1):
            print(f"  {i}. {action}")


def test_list_rules():
    """列出所有规则"""
    print("\n" + "=" * 60)
    print("测试 4: 列出所有规则")
    print("=" * 60)

    matcher = RuleMatcher("./rules")
    rules = matcher.list_rules()

    print(f"共 {len(rules)} 条规则:\n")
    for rule in rules:
        print(f"  [{rule['id']}] {rule['name']}")
        if rule.get('description'):
            print(f"       {rule['description']}")


def test_stats():
    """显示统计信息"""
    print("\n" + "=" * 60)
    print("测试 5: 统计信息")
    print("=" * 60)

    matcher = RuleMatcher("./rules")
    stats = matcher.get_stats()

    print(f"总规则数：{stats['total_rules']}")
    for file, count in stats.get('rules_by_file', {}).items():
        print(f"  {file}: {count} 条")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("规则匹配引擎测试")
    print("=" * 60)

    test_basic_match()
    test_regex_capture()
    test_action_chain()
    test_list_rules()
    test_stats()

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)
