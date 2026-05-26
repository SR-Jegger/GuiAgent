#!/usr/bin/env python3
"""
从 JSON 导入技能脚本

功能：
- 读取用户提供的 JSON 文件（不含 ID）
- 自动生成 manual_xxxxxxxx 格式的唯一 ID
- 自动归一化坐标（像素坐标 → 0-1000 范围）
- 保存到 SQLite 数据库
- 同步导出到本地 JSON (rules/manual_skills.json)

用法：
    python scripts/import_skill_from_json.py skills/my_skill.json
    python scripts/import_skill_from_json.py --inline '{"name": "测试技能", ...}'

坐标归一化：
    - 如果 coordinate_normalized=true，坐标已归一化，直接保存
    - 如果坐标值 > 1000，自动归一化
    - 使用 icon_data.recorded_resolution 或当前屏幕分辨率计算
"""

import os
import sys
import json
import uuid
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from learning.skill_store import SkillStore
from learning.icon_matcher import get_screen_resolution


def normalize_coordinate(x: int, y: int, width: int, height: int) -> List[int]:
    """
    归一化坐标（像素 → 0-1000 范围）

    Args:
        x, y: 像素坐标
        width, height: 屏幕分辨率

    Returns:
        归一化坐标 [x_norm, y_norm]
    """
    x_norm = int(x / width * 1000)
    y_norm = int(y / height * 1000)
    return [x_norm, y_norm]


def is_coordinate_normalized(coord: List[int]) -> bool:
    """
    判断坐标是否已归一化

    Args:
        coord: 坐标 [x, y]

    Returns:
        True 如果坐标值 <= 1000（已归一化）
    """
    if not coord or len(coord) < 2:
        return False
    return coord[0] <= 1000 and coord[1] <= 1000


def normalize_actions(
    actions: List[Dict],
    resolution: Optional[List[int]] = None
) -> List[Dict]:
    """
    归一化所有动作的坐标

    Args:
        actions: 动作列表
        resolution: 屏幕分辨率 [width, height]，如果 None 则自动获取

    Returns:
        归一化后的动作列表
    """
    if not actions:
        return actions

    # 获取分辨率
    if resolution and len(resolution) >= 2:
        width, height = resolution[0], resolution[1]
    else:
        width, height = get_screen_resolution()

    click_types = ("click", "left_click", "right_click", "double_click", "middle_click")

    normalized_actions = []
    for action in actions:
        result = dict(action)
        action_type = action.get("type", "")

        # 处理点击类型动作
        if action_type in click_types and "coordinate" in action:
            coord = action["coordinate"]

            # 如果未标记归一化且坐标值 > 1000，则归一化
            if not action.get("coordinate_normalized") and not is_coordinate_normalized(coord):
                old_coord = coord.copy()
                result["coordinate"] = normalize_coordinate(coord[0], coord[1], width, height)
                result["coordinate_normalized"] = True
                print(f"    归一化坐标: {old_coord} → {result['coordinate']} (分辨率: {width}x{height})")
            elif not action.get("coordinate_normalized"):
                # 坐标已归一化但缺少标记
                result["coordinate_normalized"] = True

        # 处理拖拽动作
        if action_type in ("drag", "mouse_drag"):
            if "coordinate1" in action:
                coord1 = action["coordinate1"]
                if not action.get("coordinate_normalized") and not is_coordinate_normalized(coord1):
                    result["coordinate1"] = normalize_coordinate(coord1[0], coord1[1], width, height)
                    print(f"    归一化坐标1: {coord1} → {result['coordinate1']}")
            if "coordinate2" in action:
                coord2 = action["coordinate2"]
                if not action.get("coordinate_normalized") and not is_coordinate_normalized(coord2):
                    result["coordinate2"] = normalize_coordinate(coord2[0], coord2[1], width, height)
                    print(f"    归一化坐标2: {coord2} → {result['coordinate2']}")
            result["coordinate_normalized"] = True

        normalized_actions.append(result)

    return normalized_actions


def normalize_icon_data(
    icon_data: Optional[Dict],
    resolution: Optional[List[int]] = None
) -> Optional[Dict]:
    """
    归一化 icon_data 中的 fallback_coord

    Args:
        icon_data: 图标数据
        resolution: 屏幕分辨率

    Returns:
        归一化后的图标数据
    """
    if not icon_data:
        return icon_data

    result = dict(icon_data)

    # 获取分辨率
    if resolution and len(resolution) >= 2:
        width, height = resolution[0], resolution[1]
    elif icon_data.get("recorded_resolution"):
        width, height = icon_data["recorded_resolution"][0], icon_data["recorded_resolution"][1]
    else:
        width, height = get_screen_resolution()

    # 归一化 fallback_coord
    if "fallback_coord" in icon_data:
        fallback = icon_data["fallback_coord"]
        if not is_coordinate_normalized(fallback):
            result["fallback_coord"] = normalize_coordinate(fallback[0], fallback[1], width, height)
            print(f"    归一化 fallback_coord: {fallback} → {result['fallback_coord']}")

    return result


def generate_unique_id(store: SkillStore, prefix: str = "manual") -> str:
    """生成不重复的技能 ID"""
    for _ in range(100):
        skill_id = f"{prefix}_{uuid.uuid4().hex[:8]}"
        if store.get(skill_id) is None:
            return skill_id
    raise RuntimeError("无法生成唯一 ID")


def import_skill(skill_data: dict, store: SkillStore) -> dict:
    """
    导入技能数据，自动生成 ID、归一化坐标并保存

    Args:
        skill_data: 用户提供的技能数据（不含 ID）
        store: SkillStore 实例

    Returns:
        完整的技能数据（含 ID，坐标已归一化）
    """
    # 生成唯一 ID
    skill_id = generate_unique_id(store, "manual")

    # 获取录制分辨率（用于归一化）
    icon_data = skill_data.get("icon_data")
    resolution = icon_data.get("recorded_resolution") if icon_data else None

    # 归一化坐标
    print("  处理坐标归一化...")
    normalized_actions = normalize_actions(skill_data.get("actions", []), resolution)
    normalized_icon_data = normalize_icon_data(icon_data, resolution)

    # 补充必要字段
    full_skill = {
        "id": skill_id,
        "name": skill_data.get("name", f"手动技能_{datetime.now().strftime('%H%M%S')}"),
        "description": skill_data.get("description", ""),
        "source": "manual",
        "cluster_type": skill_data.get("cluster_type", "single"),
        "trigger": skill_data.get("trigger", {"patterns": [], "app_context": []}),
        "actions": normalized_actions,
        "icon_data": normalized_icon_data,
        "parameters": skill_data.get("parameters"),
        "enabled": skill_data.get("enabled", True),
        "confidence": skill_data.get("confidence", 0.9),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }

    # 保存到数据库
    success = store.save(full_skill)

    if success:
        print(f"✓ 已保存到数据库: {skill_id}")
        return full_skill
    else:
        raise RuntimeError("保存到数据库失败")


def sync_to_json(skill: dict, output_path: str) -> bool:
    """
    同步技能到本地 JSON 文件

    Args:
        skill: 技能数据
        output_path: JSON 文件路径

    Returns:
        是否成功
    """
    # 读取现有数据（如果文件存在）
    data = {"version": "1.0", "description": "手动添加的技能", "rules": []}

    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  警告: 读取现有 JSON 失败: {e}")

    # 检查是否已存在（按 ID）
    existing_ids = {r.get("id") for r in data.get("rules", [])}
    if skill["id"] in existing_ids:
        # 更新现有技能
        data["rules"] = [skill if r.get("id") == skill["id"] else r for r in data["rules"]]
    else:
        # 添加新技能
        data["rules"].append(skill)

    # 保存
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✓ 已同步到 JSON: {output_path}")
        return True
    except Exception as e:
        print(f"✗ 同步 JSON 失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="从 JSON 导入技能")
    parser.add_argument("json_file", nargs="?", help="JSON 文件路径")
    parser.add_argument("--inline", type=str, help="直接传入 JSON 字符串")
    parser.add_argument("--output", type=str, default="rules/manual_skills_normalized.json", help="输出的 JSON 文件")
    parser.add_argument("--list", action="store_true", help="列出所有现有技能")
    args = parser.parse_args()

    # 初始化存储
    store = SkillStore()

    # 列出现有技能
    if args.list:
        print("\n现有技能列表:")
        print("-" * 50)
        skills = store.list_all()
        for s in skills:
            print(f"  {s.get('id')}: {s.get('name')} ({s.get('source')})")
        print(f"\n总数: {len(skills)}")
        return

    # 获取技能数据
    skill_data = None

    if args.inline:
        # 从命令行参数读取
        try:
            skill_data = json.loads(args.inline)
            print("从命令行读取 JSON")
        except json.JSONDecodeError as e:
            print(f"✗ JSON 解析错误: {e}")
            return

    elif args.json_file:
        # 从文件读取
        json_path = args.json_file
        if not os.path.isabs(json_path):
            json_path = os.path.join(project_root, json_path)

        if not os.path.exists(json_path):
            print(f"✗ 文件不存在: {json_path}")
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                skill_data = json.load(f)
            print(f"从文件读取: {json_path}")
        except Exception as e:
            print(f"✗ 读取文件失败: {e}")
            return

    else:
        print("请提供 JSON 文件路径或使用 --inline 参数")
        print("\n示例用法:")
        print("  python scripts/import_skill_from_json.py skills/my_skill.json")
        print("  python scripts/import_skill_from_json.py --inline '{\"name\": \"测试\"}'")
        return

    # 导入技能
    print("\n" + "=" * 50)
    print("导入技能")
    print("=" * 50)

    try:
        skill = import_skill(skill_data, store)

        print(f"\n技能信息:")
        print(f"  ID: {skill['id']}")
        print(f"  名称: {skill['name']}")
        print(f"  触发词: {skill['trigger'].get('patterns', [])}")
        print(f"  动作数: {len(skill['actions'])}")

        # 同步到 JSON
        output_path = os.path.join(project_root, args.output)
        sync_to_json(skill, output_path)

        print("\n" + "=" * 50)
        print("完成!")
        print(f"  数据库: {store.db_path}")
        print(f"  JSON: {args.output}")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ 导入失败: {e}")


if __name__ == "__main__":
    main()