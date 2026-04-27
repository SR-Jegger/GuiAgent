"""
脚本：归一化技能坐标并存储到 SQLite 数据库

用法：
    python scripts/normalize_and_store_skills.py [--width 1920] [--height 1080] [--backup]

参数：
    --width: 原始屏幕宽度（默认1920）
    --height: 原始屏幕高度（默认1080）
    --backup: 是否保存归一化后的 JSON 备份
    --overwrite: 是否覆盖数据库中已存在的技能

功能：
    1. 读取 rules/learned_skills.json
    2. 归一化所有坐标（转换为 0-1000 范围）
    3. 存储到 SQLite 数据库
    4. 可选保存 JSON 备份
"""

import json
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any


def normalize_coordinate(coord: list[int], width: int, height: int) -> list[int]:
    """将像素坐标转换为归一化坐标 (0-1000 范围)"""
    x_norm = int(coord[0] / width * 1000)
    y_norm = int(coord[1] / height * 1000)
    return [x_norm, y_norm]


def process_action(action: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """处理单个动作，归一化其坐标并添加标记"""
    result = dict(action)

    action_type = action.get("type", "")
    click_types = ("click", "left_click", "right_click", "double_click", "middle_click")

    if action_type in click_types and "coordinate" in action:
        result["coordinate"] = normalize_coordinate(action["coordinate"], width, height)
        result["coordinate_normalized"] = True
        print(f"    归一化坐标: {action['coordinate']} → {result['coordinate']}")

    if action_type in ("drag", "mouse_drag"):
        if "coordinate1" in action:
            result["coordinate1"] = normalize_coordinate(action["coordinate1"], width, height)
            print(f"    归一化坐标1: {action['coordinate1']} → {result['coordinate1']}")
        if "coordinate2" in action:
            result["coordinate2"] = normalize_coordinate(action["coordinate2"], width, height)
            print(f"    归一化坐标2: {action['coordinate2']} → {result['coordinate2']}")
        result["coordinate_normalized"] = True

    return result


def process_skill(skill: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """处理单个技能，归一化所有动作的坐标"""
    result = dict(skill)

    if "actions" in skill:
        print(f"  处理技能: {skill.get('name', skill.get('id', 'unknown'))}")
        result["actions"] = [
            process_action(action, width, height)
            for action in skill["actions"]
        ]

    return result


def main():
    parser = argparse.ArgumentParser(description="归一化技能坐标并存储到数据库")
    parser.add_argument("--width", type=int, default=1920, help="原始屏幕宽度")
    parser.add_argument("--height", type=int, default=1080, help="原始屏幕高度")
    parser.add_argument("--backup", action="store_true", help="保存归一化后的 JSON 备份")
    parser.add_argument("--overwrite", action="store_true", help="覆盖数据库中已存在的技能")
    parser.add_argument("--input", type=str, default="rules/learned_skills.json", help="输入文件路径")
    args = parser.parse_args()

    # 获取项目根目录
    project_root = Path(__file__).parent.parent
    input_path = project_root / args.input

    print("=" * 60)
    print("归一化技能坐标并存储到 SQLite")
    print("=" * 60)
    print(f"原始屏幕分辨率: {args.width}x{args.height}")
    print(f"输入文件: {input_path}")

    # 添加项目根目录到 Python 路径
    sys.path.insert(0, str(project_root))

    from learning.skill_store import SkillStore

    # 初始化数据库存储
    db_path = project_root / "data" / "skills.db"
    store = SkillStore(db_path)

    # 读取原始数据
    print("\n[Step 1] 读取技能数据...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 处理所有技能（归一化坐标）
    print("\n[Step 2] 归一化坐标...")
    processed_skills = []
    if "rules" in data:
        processed_skills = [
            process_skill(skill, args.width, args.height)
            for skill in data["rules"]
        ]

    # 添加全局归一化标记
    data["coordinate_normalized"] = True
    data["original_resolution"] = f"{args.width}x{args.height}"
    data["rules"] = processed_skills

    # 存储到数据库
    print("\n[Step 3] 存储到 SQLite 数据库...")
    for skill in processed_skills:
        skill_id = skill.get("id")
        existing = store.get(skill_id)

        if existing and not args.overwrite:
            print(f"    跳过已存在技能: {skill_id}")
            continue

        store.save(skill)
        print(f"    存储技能: {skill.get('name', skill_id)}")

    # 获取统计信息
    print("\n[Step 4] 验证存储...")
    stats = store.get_stats()
    print(f"  数据库总技能数: {stats['total']}")
    print(f"  已启用技能数: {stats['enabled']}")
    print(f"  单步技能数: {stats['single_skills']}")
    print(f"  序列技能数: {stats['sequence_skills']}")

    # 可选：续写到 manual_skills_normalized.json
    if args.backup:
        print("\n[Step 5] 续写 JSON 备份...")
        backup_path = project_root / "rules" / "manual_skills_normalized.json"

        # 如果文件已存在，读取并追加；否则创建新文件
        if backup_path.exists():
            with open(backup_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_rules = existing_data.get("rules", [])
            # 合并：按技能ID去重，新数据覆盖旧数据
            existing_ids = {r.get("id") for r in existing_rules}
            merged_rules = existing_rules.copy()
            for skill in processed_skills:
                if skill.get("id") in existing_ids:
                    # 替换旧数据
                    merged_rules = [s if s.get("id") != skill.get("id") else skill for s in merged_rules]
                else:
                    # 追加新数据
                    merged_rules.append(skill)
            data["rules"] = merged_rules
        else:
            data["rules"] = processed_skills
            data["version"] = "1.0"
            data["description"] = "手动维护的归一化技能备份"

        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  已续写保存到: {backup_path}")

    print("\n" + "=" * 60)
    print("完成！")
    print(f"  处理技能数: {len(processed_skills)}")
    print(f"  数据库路径: {db_path}")
    if args.backup:
        print(f"  JSON 备份: {backup_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()