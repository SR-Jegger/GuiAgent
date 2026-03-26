#!/usr/bin/env python3
"""
规则匹配引擎 - 用于固定任务场景的快速定位

功能：
- 加载规则文件
- 正则匹配指令
- 执行预定义动作链

使用方法:
    from rule_matcher import RuleMatcher

    matcher = RuleMatcher("./rules")
    result = matcher.match("关闭窗口")
    if result:
        print(f"匹配规则：{result['rule_name']}")
        print(f"动作链：{result['actions']}")
"""

import os
import re
import json
from typing import List, Dict, Optional, Any


class RuleMatcher:
    """
    规则匹配器：匹配固定任务模式，直接执行动作
    """

    def __init__(self, rules_dir="./rules", auto_load=True):
        """
        Args:
            rules_dir: 规则文件目录
            auto_load: 是否自动加载规则文件
        """
        self.rules_dir = rules_dir
        self.rules = []
        self.rules_cache = {}  # 编译后的正则缓存

        if auto_load:
            self.load_all_rules()

    # =========================================================================
    # 规则加载
    # =========================================================================

    def load_all_rules(self) -> bool:
        """加载所有规则文件"""
        if not os.path.exists(self.rules_dir):
            print(f"[WARN] Rules directory not found: {self.rules_dir}")
            return False

        loaded_count = 0
        for filename in os.listdir(self.rules_dir):
            if filename.endswith(".json"):
                filepath = os.path.join(self.rules_dir, filename)
                if self.load_rules_file(filepath):
                    loaded_count += 1

        print(f"[INFO] Loaded {len(self.rules)} rules from {loaded_count} files")
        return True

    def load_rules_file(self, filepath: str) -> bool:
        """加载单个规则文件"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            rules = data.get("rules", [])
            enabled_rules = [r for r in rules if r.get("enabled", True)]

            for rule in enabled_rules:
                # 检查 ID 是否重复
                if any(r["id"] == rule["id"] for r in self.rules):
                    print(f"[WARN] Duplicate rule ID: {rule['id']}")
                    continue

                self.rules.append(rule)

                # 预编译正则
                patterns = rule.get("trigger", {}).get("patterns", [])
                self.rules_cache[rule["id"]] = [re.compile(p) for p in patterns]

            print(f"[INFO] Loaded {len(enabled_rules)} rules from {filepath}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load rules from {filepath}: {e}")
            return False

    def reload_rules(self) -> bool:
        """重新加载所有规则"""
        self.rules = []
        self.rules_cache = {}
        return self.load_all_rules()

    # =========================================================================
    # 规则匹配
    # =========================================================================

    def match(self, instruction: str, app_context: Optional[str] = None) -> Optional[Dict]:
        """
        匹配指令

        Args:
            instruction: 用户指令
            app_context: 应用上下文（可选）

        Returns:
            匹配结果字典，包含 rule_id, rule_name, actions, match_groups 等
            无匹配返回 None
        """
        for rule in self.rules:
            result = self._match_rule(rule, instruction, app_context)
            if result:
                print(f"[MATCH] Instruction matched rule: {rule['name']} ({rule['id']})")
                return result

        print(f"[NOMATCH] No rule matched instruction: {instruction[:50]}...")
        return None

    def _match_rule(self, rule: Dict, instruction: str, app_context: Optional[str]) -> Optional[Dict]:
        """匹配单个规则"""
        # 检查应用上下文
        if app_context:
            app_trigger = rule.get("trigger", {}).get("app_context", [])
            if app_trigger and "*" not in app_trigger:
                if not any(app in app_context.lower() for app in app_trigger):
                    return None

        # 正则匹配
        patterns = self.rules_cache.get(rule["id"], [])
        for pattern in patterns:
            match = pattern.search(instruction)
            if match:
                return self._build_match_result(rule, match)

        return None

    def _build_match_result(self, rule: Dict, match: re.Match) -> Dict:
        """构建匹配结果"""
        # 提取捕获组
        match_groups = match.groups() if match.groups() else []
        print(f"[DEBUG] Matched rule '{rule['name']}' with groups: {match_groups}")

        # 处理动作链中的变量替换
        actions = self._process_action_variables(
            rule.get("actions", []),
            match_groups,
            match.group(0)
        )

        return {
            "rule_id": rule["id"],
            "rule_name": rule.get("name", rule["id"]),
            "description": rule.get("description", ""),
            "actions": actions,
            "match_groups": match_groups,
            "matched_text": match.group(0),
        }

    def _process_action_variables(self, actions: List[Dict],
                                   match_groups: tuple,
                                   full_match: str) -> List[Dict]:
        """处理动作链中的变量替换

        支持的变量:
        - {{match_group_1}}, {{match_group_2}}... - 正则捕获组
        - {{full_match}} - 完整匹配文本
        """
        processed = []

        for action in actions:
            action_copy = json.loads(json.dumps(action))  # 深拷贝

            # 替换 text 字段中的变量
            if "text" in action_copy:
                text = action_copy["text"]
                for i, group in enumerate(match_groups, 1):
                    text = text.replace(f"{{{{match_group_{i}}}}}", group or "")
                text = text.replace("{{full_match}}", full_match)
                action_copy["text"] = text

            # 替换其他字符串字段
            for key, value in action_copy.items():
                if isinstance(value, str):
                    for i, group in enumerate(match_groups, 1):
                        value = value.replace(f"{{{{match_group_{i}}}}}", group or "")
                    value = value.replace("{{full_match}}", full_match)
                    action_copy[key] = value
            print(f"[DEBUG] Processed action: {action_copy}")
            processed.append(action_copy)

        return processed

    # =========================================================================
    # 规则管理
    # =========================================================================

    def list_rules(self) -> List[Dict]:
        """列出所有规则"""
        return [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "description": r.get("description", ""),
                "enabled": r.get("enabled", True),
            }
            for r in self.rules
        ]

    def get_rule(self, rule_id: str) -> Optional[Dict]:
        """获取单个规则"""
        for rule in self.rules:
            if rule["id"] == rule_id:
                return rule
        return None

    def add_rule(self, rule: Dict) -> bool:
        """添加规则"""
        if any(r["id"] == rule["id"] for r in self.rules):
            print(f"[WARN] Rule already exists: {rule['id']}")
            return False

        self.rules.append(rule)

        # 预编译正则
        patterns = rule.get("trigger", {}).get("patterns", [])
        self.rules_cache[rule["id"]] = [re.compile(p) for p in patterns]

        print(f"[INFO] Added rule: {rule['name']} ({rule['id']})")
        return True

    def remove_rule(self, rule_id: str) -> bool:
        """删除规则"""
        for i, rule in enumerate(self.rules):
            if rule["id"] == rule_id:
                self.rules.pop(i)
                self.rules_cache.pop(rule_id, None)
                print(f"[INFO] Removed rule: {rule_id}")
                return True
        print(f"[WARN] Rule not found: {rule_id}")
        return False

    def save_rules(self, filepath: Optional[str] = None) -> bool:
        """保存规则到文件"""
        if filepath is None:
            # 默认保存到第一个加载的规则文件
            filepath = os.path.join(self.rules_dir, "quick_actions.json")

        try:
            # 按文件分组
            rules_by_file = {"quick_actions.json": self.rules}

            for filename, rules in rules_by_file.items():
                filepath = os.path.join(self.rules_dir, filename)
                data = {
                    "version": "1.0",
                    "rules": rules
                }
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[INFO] Saved {len(rules)} rules to {filepath}")

            return True
        except Exception as e:
            print(f"[ERROR] Failed to save rules: {e}")
            return False

    # =========================================================================
    # 统计信息
    # =========================================================================

    def get_stats(self) -> Dict:
        """获取规则统计信息"""
        return {
            "total_rules": len(self.rules),
            "rules_by_file": self._count_rules_by_file(),
        }

    def _count_rules_by_file(self) -> Dict:
        """统计每个文件的规则数"""
        # 简单实现，可以扩展
        return {"quick_actions.json": len(self.rules)}


# ============================================================================
# 命令行工具
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="规则匹配引擎管理工具")
    parser.add_argument("--rules-dir", default="./rules", help="规则目录")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出所有规则")
    list_parser.set_defaults(func=lambda args: cmd_list(args, parser))

    # test 命令
    test_parser = subparsers.add_parser("test", help="测试规则匹配")
    test_parser.add_argument("instruction", help="测试指令")
    test_parser.set_defaults(func=lambda args: cmd_test(args, parser))

    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="显示统计信息")
    stats_parser.set_defaults(func=lambda args: cmd_stats(args, parser))

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    matcher = RuleMatcher(args.rules_dir)
    args.func(args)


def cmd_list(args, parser):
    """列出规则"""
    matcher = RuleMatcher(args.rules_dir)
    rules = matcher.list_rules()

    if not rules:
        print("暂无规则")
        return

    print(f"共 {len(rules)} 条规则:\n")
    for rule in rules:
        print(f"  [{rule['id']}] {rule['name']}")
        if rule.get('description'):
            print(f"       {rule['description']}")


def cmd_test(args, parser):
    """测试匹配"""
    matcher = RuleMatcher(args.rules_dir)
    result = matcher.match(args.instruction)

    if result:
        print(f"\n✓ 匹配成功!")
        print(f"  规则：{result['rule_name']} ({result['rule_id']})")
        print(f"  描述：{result.get('description', 'N/A')}")
        print(f"  匹配文本：{result['matched_text']}")
        if result['match_groups']:
            print(f"  捕获组：{result['match_groups']}")
        print(f"\n  动作链 ({len(result['actions'])} 个动作):")
        for i, action in enumerate(result['actions'], 1):
            print(f"    {i}. {action}")
    else:
        print(f"\n✗ 无匹配规则")


def cmd_stats(args, parser):
    """统计信息"""
    matcher = RuleMatcher(args.rules_dir)
    stats = matcher.get_stats()

    print("\n规则统计:")
    print(f"  总规则数：{stats['total_rules']}")
    for file, count in stats.get('rules_by_file', {}).items():
        print(f"  {file}: {count} 条")


if __name__ == "__main__":
    main()
