"""
Fast path node for GUI automation agent.

Responsible for rule-based quick matching before VLM reasoning.
"""

import time
from typing import TYPE_CHECKING

from nodes.types import AgentState
from utils.computer_tools import ComputerTools
from rule_matcher import RuleMatcher


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
        vlm_actions = []

        for action in rule_actions:
            action_type = action.get("type", "")

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
            elif action_type == "type" and "text" in action:
                vlm_action["arguments"]["text"] = action["text"]
            elif action_type in ("key",) and "keys" in action:
                vlm_action["arguments"]["keys"] = action["keys"]

            vlm_actions.append(vlm_action)

        source_label = "LEARNED" if rule_source == "learned" else "MANUAL"
        print(f"[FAST_PATH] Matched rule: {result['rule_name']} ({result['rule_id']}) [{source_label}]")
        print(f"[FAST_PATH] Converted {len(vlm_actions)} action(s) to VLM format (confidence: {rule_confidence})")

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
