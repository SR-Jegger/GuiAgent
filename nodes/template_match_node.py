"""
Template match node for GUI automation agent.

Responsible for template-based fallback when VLM cannot determine coordinates.
"""

import json
import os
import re

from nodes.types import AgentState
from template_knowledge import TemplateKnowledgeBase
from utils.parsers import extract_template_request


def template_match_node(state: AgentState) -> AgentState:
    """
    Template matching fallback node.

    Used when VLM cannot determine action coordinates.
    Expects template_request in format:
    <template_request> {"target": {...}, "description": {...}, "expected_action": {...}}</template_request>

    Args:
        state: Current agent state

    Returns:
        Updated state with action_coordinate and modified llm_response
    """
    step_id = state.get("step_id", 0)
    screenshot_path = state.get("screenshot_path", "")
    llm_response = state.get("llm_response", "")
    instruction = state.get("instruction", "")
    template_dir = state.get("template_dir", "./templates")
    template_request = state.get("template_request", "")

    print(f"\n[TEMPLATE_MATCH] Step {step_id}: Starting template matching...")

    if not screenshot_path or not os.path.exists(screenshot_path):
        return {
            "execution_status": "error",
            "error_message": "Screenshot not available",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    # =========================
    # 1. Parse template_request
    # =========================
    request = extract_template_request(template_request)
    print(f"[TEMPLATE_MATCH] Extracted template request: {request}")

    if not request:
        print("[TEMPLATE_MATCH] No template_request found")
        return {
            "execution_status": "error",
            "error_message": "template_request not found",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    target = request.get("target", "")
    description = request.get("description", "")
    expected_action = request.get("expected_action", "left_click")

    print(f"[TEMPLATE_MATCH] Target: {target}")
    print(f"[TEMPLATE_MATCH] Description: {description}")

    # 2. Construct search query
    query = target if target else description

    # 3. Initialize template library
    kb = TemplateKnowledgeBase(template_dir=template_dir)

    # 4. Execute template matching
    coord = kb.find_and_locate(query, screenshot_path)

    if not coord:
        print("[TEMPLATE_MATCH] Template match failed")
        return {
            "execution_status": "success",  # Template match failure is not an execution error, continue flow
            "error_message": f"Template match failed for {target}",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    x, y = coord
    print(f"[TEMPLATE_MATCH] Found coordinate: ({x}, {y})")

    # 5. Generate tool_call
    tool_call = {
        "name": "computer_use",
        "arguments": {
            "action": expected_action,
            "coordinate": [x, y]
        }
    }
    tool_call_str = f"""
    Observation: Template matched for target '{target}' at coordinates ({x}, {y}).
    Thought: The VLM could not determine the action coordinates, but we successfully found the target using template matching. We will execute the expected action at the matched coordinates.
    Action: {"已成功执行 {target} 操作/命令，请继续下一步"}
    {json.dumps(tool_call)}
    """
    print(f"[TEMPLATE_MATCH] Generated tool call: {tool_call_str}")

    return {
        "action_coordinate": (x, y),
        "llm_response": tool_call_str,
        "execution_status": "success",
        "retry_count": 0,
    }
