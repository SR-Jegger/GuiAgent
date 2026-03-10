#!/usr/bin/env python3
"""
模板管理工具

用于裁剪、注册和管理 UI 模板

使用方法:
    # 从截图裁剪模板
    python template_manager.py crop --screenshot screen.png --x 100 --y 200 --w 50 --h 50 --name buttons/close.png
    
    # 列出所有模板
    python template_manager.py list
    
    # 测试模板匹配
    python template_manager.py test --template buttons/close.png --screenshot screen.png
"""

import argparse
import os
import sys

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import TemplateMatcher, CV2_AVAILABLE

if not CV2_AVAILABLE:
    print("[ERROR] OpenCV (cv2) not installed!")
    print("        Install with: pip install opencv-python")
    sys.exit(1)


def cmd_crop(args):
    """从截图裁剪并注册模板"""
    matcher = TemplateMatcher(args.template_dir)
    
    success = matcher.register_template(
        name=args.name,
        screenshot_path=args.screenshot,
        x=args.x,
        y=args.y,
        w=args.w,
        h=args.h
    )
    
    if success:
        print(f"✓ 模板已保存：{args.name}")
        print(f"  位置：{os.path.join(args.template_dir, args.name)}")
        print(f"  尺寸：{args.w}x{args.h}")
    else:
        print("✗ 模板保存失败")
        sys.exit(1)


def cmd_list(args):
    """列出所有模板"""
    matcher = TemplateMatcher(args.template_dir)
    templates = matcher.list_templates()
    
    if not templates:
        print("暂无模板")
        return
    
    print(f"找到 {len(templates)} 个模板:\n")
    for t in sorted(templates):
        print(f"  - {t}")


def cmd_test(args):
    """测试模板匹配"""
    matcher = TemplateMatcher(args.template_dir, threshold=args.threshold)
    
    print(f"正在匹配模板：{args.template}")
    print(f"截图：{args.screenshot}")
    print(f"阈值：{args.threshold}\n")
    
    if args.all:
        # 匹配所有模板
        results = matcher.find_all(args.screenshot)
        if not results:
            print("未找到任何匹配")
            return
        
        print(f"找到 {len(results)} 个匹配:\n")
        for name, coord in sorted(results.items()):
            print(f"  {name}: {coord}")
    else:
        # 匹配单个模板
        coord = matcher.find(args.template, args.screenshot, multiple=args.multiple)
        
        if coord is None:
            print("✗ 未找到匹配")
            sys.exit(1)
        
        if args.multiple and isinstance(coord, list):
            print(f"✓ 找到 {len(coord)} 个匹配:")
            for i, c in enumerate(coord, 1):
                print(f"  [{i}] {c}")
        else:
            print(f"✓ 匹配成功，坐标：{coord}")


def cmd_info(args):
    """显示模板信息"""
    matcher = TemplateMatcher(args.template_dir)
    template_path = os.path.join(args.template_dir, args.template)
    
    if not os.path.exists(template_path):
        print(f"✗ 模板不存在：{args.template}")
        sys.exit(1)
    
    import cv2
    template = cv2.imread(template_path)
    h, w = template.shape[:2]
    
    print(f"模板：{args.template}")
    print(f"路径：{template_path}")
    print(f"尺寸：{w}x{h}")
    print(f"通道：{template.shape[2] if len(template.shape) > 2 else 1}")


def main():
    parser = argparse.ArgumentParser(
        description="GuiAgent 模板管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--template-dir",
        default="./templates",
        help="模板库目录 (默认：./templates)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # crop 命令
    crop_parser = subparsers.add_parser("crop", help="从截图裁剪模板")
    crop_parser.add_argument("--screenshot", required=True, help="源截图路径")
    crop_parser.add_argument("--x", type=int, required=True, help="裁剪区域 X 坐标")
    crop_parser.add_argument("--y", type=int, required=True, help="裁剪区域 Y 坐标")
    crop_parser.add_argument("--w", type=int, required=True, help="裁剪宽度")
    crop_parser.add_argument("--h", type=int, required=True, help="裁剪高度")
    crop_parser.add_argument("--name", required=True, help="模板文件名 (如 buttons/close.png)")
    crop_parser.set_defaults(func=cmd_crop)
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出所有模板")
    list_parser.set_defaults(func=cmd_list)
    
    # test 命令
    test_parser = subparsers.add_parser("test", help="测试模板匹配")
    test_parser.add_argument("--template", required=True, help="模板文件名")
    test_parser.add_argument("--screenshot", required=True, help="测试截图路径")
    test_parser.add_argument("--threshold", type=float, default=0.8, help="匹配置信度阈值 (默认：0.8)")
    test_parser.add_argument("--multiple", action="store_true", help="返回所有匹配（不只最佳）")
    test_parser.add_argument("--all", action="store_true", help="匹配所有模板")
    test_parser.set_defaults(func=cmd_test)
    
    # info 命令
    info_parser = subparsers.add_parser("info", help="显示模板信息")
    info_parser.add_argument("--template", required=True, help="模板文件名")
    info_parser.set_defaults(func=cmd_info)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
