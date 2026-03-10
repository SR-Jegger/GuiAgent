#!/usr/bin/env python3
"""
智能执行器：结合 VLM + 知识库 + 模板匹配

使用示例:
    executor = SmartExecutor("./templates")
    executor.execute("click", {"template_query": "关闭按钮"}, "screenshot.png")
"""

import os
import sys
from typing import Dict, Any, Optional, List

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from template_knowledge import TemplateKnowledgeBase
from utils import ComputerTools, CV2_AVAILABLE


class SmartExecutor:
    """智能动作执行器"""
    
    def __init__(self, template_dir="./templates"):
        """
        Args:
            template_dir: 模板库目录
        """
        self.template_dir = template_dir
        self.tools = ComputerTools()
        self.kb = TemplateKnowledgeBase(template_dir, auto_load=True)
        
        if CV2_AVAILABLE:
            self.tools.set_template_dir(template_dir)
            print(f"[INFO] SmartExecutor initialized with {len(self.kb.templates)} templates")
        else:
            print("[WARN] OpenCV not available, template matching disabled")
    
    def execute(self, action_type: str, params: Dict[str, Any], 
                screenshot_path: Optional[str] = None) -> bool:
        """
        智能执行动作
        
        优先级：
        1. 知识库语义搜索
        2. 直接模板匹配
        3. 坐标点击（兜底）
        
        Args:
            action_type: 动作类型 (click, double_click, etc.)
            params: 动作参数
            screenshot_path: 当前截图路径
        
        Returns:
            bool: 是否成功执行
        """
        if action_type not in ("click", "left_click", "double_click", "right_click"):
            # 非点击动作，直接交给 ComputerTools
            return self._execute_basic(action_type, params)
        
        # 1. 知识库语义搜索（最高优先级）
        if "template_query" in params:
            coord = self.kb.find_and_locate(
                params["template_query"], 
                screenshot_path or self._get_screenshot()
            )
            if coord:
                print(f"[INFO] KB match: {params['template_query']} → {coord}")
                return self._click(coord, action_type)
        
        # 2. 直接模板匹配
        if "template" in params:
            coord = self.tools.find_template(
                params["template"],
                screenshot_path or self._get_screenshot()
            )
            if coord:
                print(f"[INFO] Template match: {params['template']} → {coord}")
                return self._click(coord, action_type)
        
        # 3. 坐标点击（兜底）
        if "coordinate" in params:
            coord = params["coordinate"]
            print(f"[INFO] Coordinate click: {coord}")
            return self._click(coord, action_type)
        
        print(f"[WARN] No valid target for {action_type}")
        return False
    
    def _click(self, coord: tuple, action_type: str) -> bool:
        """执行点击"""
        x, y = coord
        
        if action_type == "double_click":
            self.tools.double_click(x, y)
        elif action_type == "right_click":
            self.tools.right_click(x, y)
        else:
            self.tools.left_click(x, y)
        
        return True
    
    def _execute_basic(self, action_type: str, params: Dict[str, Any]) -> bool:
        """执行基础动作"""
        try:
            if action_type == "type":
                self.tools.type(params.get("text", ""))
            elif action_type == "key":
                self.tools.press_key(params.get("keys", []))
            elif action_type == "wait":
                import time
                time.sleep(params.get("time", 1))
            elif action_type == "scroll":
                self.tools.scroll(params.get("pixels", 100))
            else:
                print(f"[WARN] Unknown action type: {action_type}")
                return False
            return True
        except Exception as e:
            print(f"[ERROR] Execute failed: {e}")
            return False
    
    def _get_screenshot(self) -> str:
        """获取截图"""
        path = "/tmp/smart_executor_screenshot.png"
        self.tools.get_screenshot(path)
        return path
    
    def list_templates(self, category: Optional[str] = None) -> List[Dict]:
        """列出可用模板"""
        return self.kb.list_templates(category)
    
    def search_templates(self, query: str, limit: int = 10) -> List[str]:
        """搜索模板"""
        return self.kb.search(query, limit)
    
    def add_template(self, file: str, name: str, description: str = "",
                     tags: List[str] = None, category: str = "") -> bool:
        """添加新模板"""
        return self.kb.add_template(file, name, description, tags, category)


# ============================================================================
# 命令行测试
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="智能执行器测试工具")
    parser.add_argument("--template-dir", default="./templates", help="模板目录")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # test 命令
    test_parser = subparsers.add_parser("test", help="测试执行")
    test_parser.add_argument("--action", default="click", help="动作类型")
    test_parser.add_argument("--query", help="搜索词（用于知识库）")
    test_parser.add_argument("--template", help="模板文件名")
    test_parser.add_argument("--x", type=int, help="X 坐标")
    test_parser.add_argument("--y", type=int, help="Y 坐标")
    test_parser.add_argument("--screenshot", help="截图路径")
    test_parser.set_defaults(func=cmd_test)
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出模板")
    list_parser.set_defaults(func=cmd_list)
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="搜索模板")
    search_parser.add_argument("query", help="搜索词")
    search_parser.set_defaults(func=cmd_search)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    executor = SmartExecutor(args.template_dir)
    args.func(args, executor)


def cmd_test(args, executor):
    """测试执行"""
    params = {}
    
    if args.query:
        params["template_query"] = args.query
    elif args.template:
        params["template"] = args.template
    elif args.x and args.y:
        params["coordinate"] = [args.x, args.y]
    else:
        print("请指定 --query, --template, 或 --x --y")
        return
    
    success = executor.execute(args.action, params, args.screenshot)
    print(f"执行{'成功' if success else '失败'}")


def cmd_list(args, executor):
    """列出模板"""
    templates = executor.list_templates()
    
    if not templates:
        print("暂无模板")
        return
    
    print(f"共 {len(templates)} 个模板:\n")
    for tmpl in templates:
        print(f"  {tmpl['file']}")
        print(f"    名称：{tmpl['name']}")
        if tmpl.get("tags"):
            print(f"    标签：{', '.join(tmpl['tags'])}")
        print()


def cmd_search(args, executor):
    """搜索模板"""
    results = executor.search_templates(args.query)
    
    if not results:
        print("未找到匹配")
        return
    
    print(f"找到 {len(results)} 个匹配:\n")
    for file in results:
        tmpl = executor.kb.get_template(file)
        print(f"  - {tmpl['name']} ({file})")


if __name__ == "__main__":
    main()
