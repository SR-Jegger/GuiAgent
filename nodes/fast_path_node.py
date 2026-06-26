"""
Fast path node for GUI automation agent.

Responsible for rule-based quick matching before VLM reasoning.
"""

import time
import threading
from typing import TYPE_CHECKING

from nodes.types import AgentState
from utils.computer_tools import ComputerTools
from learning.rule_matcher import RuleMatcher
from utils.action_resolver import ActionResolver, needs_resolution
from utils.ocr_locator import OCRLocator

# 全局单例 OCRLocator，避免每次冷启动加载模型
_ocr_locator_instance: OCRLocator = None
_ocr_lock = threading.Lock()  # OCR 调用锁，防止并发崩溃


def get_ocr_locator() -> OCRLocator:
    """获取全局 OCRLocator 单例实例，避免重复初始化模型"""
    global _ocr_locator_instance
    if _ocr_locator_instance is None:
        print("[FAST_PATH] 初始化 OCRLocator 单例...")
        _ocr_locator_instance = OCRLocator()
    return _ocr_locator_instance


def resolve_icon_coordinates(
    rule_result: dict,
    screenshot_array: any,
    screen_size: tuple = None
) -> list:
    """
    使用图像匹配解析技能的坐标

    Args:
        rule_result: 规则匹配结果（包含技能信息）
        screenshot_array: 当前截图
        screen_size: 屏幕分辨率

    Returns:
        解析后的 actions（坐标已通过图像匹配更新）
    """
    try:
        from learning.skill_store import SkillStore
        from learning.icon_matcher import get_screen_resolution

        # 获取完整技能信息（包含 icon_data）
        store = SkillStore()
        skill_id = rule_result.get("rule_id")
        skill = store.get(skill_id)

        if not skill:
            print(f"[FAST_PATH] 技能不存在: {skill_id}")
            return rule_result.get("actions", [])

        icon_data = skill.get("icon_data")
        if not icon_data:
            print(f"[FAST_PATH] 技能无 icon_data，使用原始坐标")
            return rule_result.get("actions", [])

        print(f"[FAST_PATH] 检测到 icon_data，进行图像匹配...")

        # 自动获取屏幕分辨率
        if screen_size is None:
            screen_size = get_screen_resolution()

        # 调用图像匹配解析坐标
        actions = skill.get("actions", [])
        resolved_actions = []

        for i, action in enumerate(actions):
            action_type = action.get("type", "")

            # 只处理点击类型动作
            if action_type in ("click", "left_click", "right_click", "middle_click", "double_click"):
                coord = store.resolve_action_coordinate(
                    skill,
                    screenshot_array,
                    screen_size,
                    action_index=i,
                    use_adaptive=True
                )

                if coord:
                    # 图像匹配成功，更新坐标
                    # 注意：resolve_action_coordinate 返回的是像素坐标，不需要归一化标记
                    print(f"[FAST_PATH] 图像匹配成功: {coord} (像素坐标)")
                    resolved_action = dict(action)
                    resolved_action["coordinate"] = list(coord)
                    # 删除归一化标记，因为图像匹配返回的是像素坐标
                    resolved_action.pop("coordinate_normalized", None)
                    resolved_actions.append(resolved_action)
                else:
                    # 图像匹配失败，使用 fallback
                    print(f"[FAST_PATH] 图像匹配失败，使用原始坐标")
                    resolved_actions.append(action)
            else:
                # 非点击动作，直接保留
                resolved_actions.append(action)

        return resolved_actions

    except Exception as e:
        print(f"[FAST_PATH] 图像匹配错误: {e}")
        import traceback
        traceback.print_exc()
        return rule_result.get("actions", [])


def fast_path_node(state: AgentState) -> AgentState:
    """
    Fast Path rule matching node.

    This node matches pre-defined rules before VLM reasoning.
    If a rule matches, actions are executed directly.
    If no rule matches, fallback to capture -> reasoning flow.

    For multi-step tasks:
    - Uses current sub-step's description as the instruction to match
    - Global task instruction is available as context

    Flow:
    1. Use RuleMatcher to match current sub-step instruction
    2. If matched: convert rule actions to VLM format, set fast_path_matched=True
    3. If not matched: set fast_path_matched=False, fallback to normal flow

    Args:
        state: Current agent state

    Returns:
        Updated state with fast_path_matched flag and actions (if matched)
    """
    time.sleep(0.5)  # Reduced delay

    if "tools" not in state:
        tools = ComputerTools()
        # tools.reset()  # Minimize all windows
        state["tools"] = tools
    else:
        tools = state.get("tools")

    # For multi-step tasks, use current sub-step's description
    sub_steps = state.get("sub_steps", [])
    current_step_index = state.get("current_step_index", 0)

    if sub_steps and current_step_index < len(sub_steps):
        # Multi-step task: use current sub-step description
        current_step = sub_steps[current_step_index]
        current_instruction = current_step.get("description", "")
        print(f"\n[FAST_PATH] Executing sub-step {current_step_index + 1}/{len(sub_steps)}")
        print(f"[FAST_PATH] Sub-step instruction: {current_instruction}")

        # Browser sub-step short-circuit: the sub_step is a structured dict
        # ({"action": "browser_*", "selector": "...", ...}) emitted by
        # task_decomposer_node from intent_mappings.json. Skip RuleMatcher,
        # OCR, and icon matching entirely - hand it straight to execution_node
        # as a browser_use action.
        if current_step.get("is_browser"):
            browser_step = current_step.get("browser_step", {})
            action_type = browser_step.get("action", "browser_action")
            print(f"[FAST_PATH] Browser sub-step detected, short-circuiting: {action_type}")
            return {
                "fast_path_matched": True,
                "actions": [{
                    "name": "browser_use",
                    "arguments": dict(browser_step),
                }],
                "execution_status": "success",
                "tools": tools,
                "browser_skill_matched": True,
            }
    else:
        # Single-step task or fallback
        current_instruction = state.get("instruction", "")

    # Global instruction available as context
    task_instruction = state.get("instruction", "")
    if task_instruction and task_instruction != current_instruction:
        print(f"[FAST_PATH] Global task context: {task_instruction[:50]}...")

    rules_dir = state.get("rules_dir", "./rules")
    use_sqlite = state.get("use_sqlite", True)  # Default: use SQLite for learned skills

    print(f"\n[FAST_PATH] Matching instruction: {current_instruction[:50]}...")

    # Initialize rule matcher (with SQLite support)
    matcher = RuleMatcher(rules_dir, auto_load=True, use_sqlite=use_sqlite)

    # Execute matching
    result = matcher.match(current_instruction)

    if result:
        # Matched: convert rule actions to VLM tool_call format
        rule_actions = result.get("actions", [])
        rule_source = result.get("source", "manual")
        rule_confidence = result.get("confidence", 1.0)

        # =====================================================
        # Step 1: 图像匹配（如果技能有 icon_data）
        # =====================================================
        # 检查是否有 icon_data（需要截图进行匹配）
        has_icon_data = False
        try:
            from learning.skill_store import SkillStore
            store = SkillStore()
            skill = store.get(result.get("rule_id"))
            if skill and skill.get("icon_data"):
                has_icon_data = True
        except Exception:
            pass

        if has_icon_data:
            print(f"[FAST_PATH] 技能包含 icon_data，准备截图进行图像匹配...")

            # 等待界面加载
            wait_time = state.get("ocr_wait_time", 1.0)
            time.sleep(wait_time)

            # 截图
            ocr_locator = get_ocr_locator()
            screenshot_array = ocr_locator.capture_screenshot()

            if screenshot_array is None:
                print(f"[FAST_PATH] 截图失败，使用原始坐标")
            else:
                # 调用图像匹配
                rule_actions = resolve_icon_coordinates(result, screenshot_array)

        # =====================================================
        # Step 2: OCR 占位符解析（如果需要）
        # =====================================================

        # 检测是否需要占位符解析
        if needs_resolution(rule_actions):
            print(f"[FAST_PATH] 检测到占位符，需要解析动态坐标")

            try:
                # 等待界面加载完成（特别是导航类动作后需要等待）
                wait_time = state.get("ocr_wait_time", 2.0)  # 默认等待2秒
                print(f"[FAST_PATH] 等待 {wait_time} 秒让界面加载完成...")
                time.sleep(wait_time)

                # OCR自己截图，不依赖capture_node
                # 使用单例 OCRLocator，避免每次冷启动
                ocr_locator = get_ocr_locator()
                screenshot_array = ocr_locator.capture_screenshot()
                if screenshot_array is None:
                    print(f"[FAST_PATH] 警告:OCR截图失败, 无法进行定位")
                    # 回退到 VLM reasoning
                    return {
                        "fast_path_matched": False,
                        "execution_status": "success",
                        "tools": tools,
                    }

                # 初始化解析器
                resolver = ActionResolver(ocr_locator)

                # 解析占位符
                match_groups = result.get("match_groups", ())
                resolved_actions = resolver.resolve_actions(
                    rule_actions,
                    match_groups,
                    screenshot_array
                )

                print(f"[FAST_PATH] 占位符解析完成，生成 {len(resolved_actions)} 个动作")
                rule_actions = resolved_actions  # 使用解析后的动作

            except Exception as e:
                print(f"[FAST_PATH] 错误：OCR解析失败: {e}")
                import traceback
                traceback.print_exc()
                # 回退到 VLM reasoning
                return {
                    "fast_path_matched": False,
                    "execution_status": "success",
                    "tools": tools,
                }

        vlm_actions = []

        for action in rule_actions:
            # Action type may live under "type" (desktop convention) or "action"
            # (browser convention, matching BrowserAgent/skills/browser_skills.json).
            action_type = action.get("type", "") or action.get("action", "")
            coordinate = action.get("coordinate")

            # Browser actions: preserve the full step dict so execute_browser_step
            # in execution_node can read selector/url/text/value/exact/role/etc.
            # Browser steps skip coordinate rescaling and ComputerTools entirely.
            if action_type.startswith("browser_"):
                arguments = dict(action)
                arguments["action"] = action_type
                vlm_actions.append({
                    "name": "browser_use",
                    "arguments": arguments,
                })
                continue

            # 跳过无法解析坐标的点击动作并打印警告
            if action_type in ("click", "left_click", "right_click", "middle_click", "double_click"):
                if coordinate is None:
                    print(f"[FAST_PATH] 警告：动作 '{action_type}' 坐标解析失败，跳过该动作")
                    continue

            # Convert action format: {"type": "hotkey"} -> {"name": "computer_use", "arguments": {"action": "hotkey"}}
            vlm_action = {
                "name": "computer_use",
                "arguments": {"action": action_type}
            }

            # Copy parameter fields
            if action_type == "hotkey" and "keys" in action:
                vlm_action["arguments"]["keys"] = action["keys"]
            elif action_type == "scroll" and "pixels" in action:
                vlm_action["arguments"]["pixels"] = action["pixels"]
            elif action_type in ("click", "left_click", "right_click", "middle_click", "double_click") and "coordinate" in action:
                vlm_action["arguments"]["coordinate"] = action["coordinate"]
                # Copy coordinate_normalized flag for rescaling in execution_node
                if "coordinate_normalized" in action:
                    vlm_action["arguments"]["coordinate_normalized"] = action["coordinate_normalized"]
            elif action_type == "type" and "text" in action:
                vlm_action["arguments"]["text"] = action["text"]
            elif action_type in ("key",) and "keys" in action:
                vlm_action["arguments"]["keys"] = action["keys"]

            vlm_actions.append(vlm_action)

        source_label = "LEARNED" if rule_source == "learned" else "MANUAL"
        print(f"[FAST_PATH] Matched rule: {result['rule_name']} ({result['rule_id']}) [{source_label}]")
        print(f"[FAST_PATH] Converted {len(vlm_actions)} action(s) to VLM format (confidence: {rule_confidence})")

        # 如果所有动作都被跳过，回退到 VLM reasoning
        if len(vlm_actions) == 0:
            print("[FAST_PATH] 所有动作坐标解析失败，回退到 VLM reasoning")
            return {
                "fast_path_matched": False,
                "execution_status": "success",
                "tools": tools,
            }

        return {
            "fast_path_matched": True,
            "actions": vlm_actions,
            "rule_source": rule_source,  # Track where the rule came from
            "rule_confidence": rule_confidence,
            "execution_status": "success",
            "tools": tools,  # Pass tools for direct execution
        }
    else:
        # Not matched: fallback to normal VLM flow
        print("[FAST_PATH] No rule matched, falling back to VLM reasoning")

        return {
            "fast_path_matched": False,
            "execution_status": "success",
            "tools": tools,  # Pass tools for later nodes
        }
