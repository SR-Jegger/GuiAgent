"""
Execution node for GUI automation agent.

Responsible for parsing and executing actions from LLM response.
"""

import json
import os
import time
from typing import TYPE_CHECKING

from PIL import Image

from nodes.types import AgentState
from utils.computer_tools import ComputerTools
from utils.parsers import extract_tool_calls

from utils.utils import (
    annotate_screenshot,
    get_output_dir,
    smart_resize,
)


def execution_node(state: AgentState) -> AgentState:
    """
    Execute actions from LLM response.

    This node:
    1. Parses actions from LLM response (or uses Fast Path actions)
    2. Rescales coordinates if needed
    3. Executes each action
    4. Annotates screenshots for debugging
    5. Updates history

    Args:
        state: Current agent state

    Returns:
        Updated state with execution results
    """
    step_id = state.get("step_id", 0)
    llm_response = state.get("llm_response", "")
    screenshot_path = state.get("screenshot_path", "")
    history = state.get("history", [])
    output_dir = state.get("output_dir", get_output_dir())
    tools = state.get("tools")
    fast_path_matched = state.get("fast_path_matched", False)

    # Fast Path: use actions directly (already in VLM format)
    if fast_path_matched:
        action_list = state.get("actions", [])
        if not action_list:
            return {
                "actions": [],
                "execution_status": "error",
                "error_message": "Fast path matched but no actions provided",
                "retry_count": state.get("retry_count", 0) + 1,
            }
        print(f"[EXECUTION] Using Fast Path actions: {len(action_list)} action(s)")
        resized_width, resized_height = 1920, 1080
    else:
        # Normal VLM flow: extract actions from llm_response
        if not llm_response:
            return {
                "actions": [],
                "execution_status": "error",
                "error_message": "No LLM response to execute",
                "retry_count": state.get("retry_count", 0) + 1,
            }
        print(f"\n[EXECUTION] Step {step_id}: Parsing and executing actions...")
        action_list = extract_tool_calls(llm_response)
        if not action_list:
            print("[EXECUTION] Warning: No actions parsed from LLM response")
            return {
                "actions": [],
                "execution_status": "error",
                "error_message": "LLM did not return valid action JSON",
                "retry_count": state.get("retry_count", 0) + 1,
            }

        # Get screenshot dimensions for coordinate rescaling
        try:
            dummy_image = Image.open(screenshot_path)
            resized_height, resized_width = smart_resize(
                dummy_image.height,
                dummy_image.width,
                factor=16,
                min_pixels=3136,
                max_pixels=1003520 * 200,
            )
            dummy_image.close()
        except Exception as e:
            print(f"[EXECUTION] Warning: Could not get image dimensions: {e}")
            resized_width, resized_height = 1920, 1080  # Default

    # Execute each action
    stop_flag = False
    executed_actions = []
    
    # 从llm_response 或 fastrule节点 中提取的动作列表action_list
    try:
        for action_id, action in enumerate(action_list):
            action_parameter = action.get("arguments", {})
            action_type = action_parameter.get("action", "")
            print(f"  [EXECUTION] Action {action_id + 1}: {action_type}")

            # Rescale coordinates (skip for Fast Path if no coordinate in action)
            if not fast_path_matched and state.get("action_coordinate") is None:
                print("  [EXECUTION] !!Rescaling coordinates...")
                rescale_coordinates(action_parameter, resized_width, resized_height)

            # Execute action and check for stop signal
            print(f"  [EXECUTION] Parameters: {action_parameter}")
            should_stop = execute_action(tools, action_parameter)
            print(f"  [EXECUTION] Action {action_id + 1} executed successfully")
            if should_stop:
                stop_flag = True
                print("[EXECUTION] Stop signal received")
                break

            executed_actions.append(action_parameter)

            # Annotate screenshot for debugging (skip for Fast Path)
            if not fast_path_matched and screenshot_path:
                anno_path = annotate_screenshot(
                    screenshot_path,
                    action_parameter,
                    os.path.join(output_dir, f"anno_{step_id}_{action_id}.png"),
                )
                if anno_path:
                    print(f"  [EXECUTION] Annotation saved: {anno_path}")

    except Exception as e:
        print(f"[EXECUTION] Error executing action: {e}")
        return {
            "actions": executed_actions,
            "execution_status": "error",
            "error_message": f"Action execution failed: {str(e)}",
            "retry_count": state.get("retry_count", 0) + 1,
            "stop_flag": stop_flag,
        }

    # Update history (skip for Fast Path)
    if not fast_path_matched:
        history_entry = {
            "step": step_id,
            "output": llm_response,
            "image": screenshot_path,
            "actions": executed_actions,
        }
        history.append(history_entry)

        # Small delay to allow UI to update
        time.sleep(2)

    return {
        "actions": executed_actions,
        "execution_status": "success",
        "fast_path_matched": False,  # Reset fast path flag after execution
        "error_message": None,
        "retry_count": 0,
        "sub_flag": state.get("sub_flag", True),  # Pass through sub
        "stop_flag": stop_flag,
        "history": history if not fast_path_matched else state.get("history", []),
    }


def rescale_coordinates(action_parameter: dict, resized_width: int, resized_height: int) -> None:
    """
    Convert normalized coordinates (0-1000 range) to actual pixel coordinates.

    Modifies action_parameter in place.

    Args:
        action_parameter: Action parameters dict (modified in place)
        resized_width: Target width in pixels
        resized_height: Target height in pixels
    """
    for key in ("coordinate", "coordinate1", "coordinate2"):
        if key in action_parameter:
            action_parameter[key][0] = int(
                action_parameter[key][0] / 1000 * resized_width
            )
            action_parameter[key][1] = int(
                action_parameter[key][1] / 1000 * resized_height
            )


def execute_action(computer_tools: ComputerTools, action_parameter: dict) -> bool:
    """
    Execute a single action on the desktop.

    Args:
        computer_tools: ComputerTools instance
        action_parameter: Dict with 'action' type and parameters

    Returns:
        stop (bool): True if the agent should terminate
    """
    action_type = action_parameter.get("action", "")

    if action_type in ("click", "left_click"):
        computer_tools.left_click(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "mouse_move":
        computer_tools.mouse_move(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "middle_click":
        computer_tools.middle_click(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type in ("right click", "right_click"):
        computer_tools.right_click(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "open app":
        computer_tools.open_app(action_parameter["app_name"])

    elif action_type in ("key", "hotkey"):
        computer_tools.press_key(action_parameter["keys"])

    elif action_type == "type":
        computer_tools.type(action_parameter["text"])

    elif action_type == "drag":
        computer_tools.left_click_drag(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "scroll":
        if "coordinate" in action_parameter:
            computer_tools.mouse_move(
                action_parameter["coordinate"][0],
                action_parameter["coordinate"][1],
            )
        computer_tools.scroll(action_parameter.get("pixels", 1))

    elif action_type in ("computer_double_click", "double_click"):
        print("  [EXECUTION] Performing double click")
        computer_tools.double_click(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "triple_click":
        computer_tools.triple_click(
            action_parameter["coordinate"][0],
            action_parameter["coordinate"][1],
        )

    elif action_type == "call_user":
        from utils import StepPopup
        StepPopup.show_blocking(
            "User Interaction Required",
            "Please perform the requested manual operation.",
            image_path="",
            timeout_sec=120,
            width=960,
            height=540,
        )
        print("Manual action completed, resuming...")

    elif action_type == "wait":
        time_value = action_parameter.get("time", 2)
        try:
            wait_time = int(time_value)
        except (ValueError, TypeError):
            wait_time = 2
        time.sleep(wait_time)

    elif action_type == "answer":
        from utils import StepPopup
        StepPopup.show_blocking(
            "Task Finished",
            action_parameter["text"],
            image_path="",
            timeout_sec=120,
            width=960,
            height=540,
        )
        return True  # signal to stop

    elif action_type in ("stop", "terminate", "done"):
        from utils import StepPopup
        status = action_parameter.get("status", "success")
        StepPopup.show_blocking(
            "Task Completed",
            f"Task completed with status: {status}",
            image_path="",
            timeout_sec=120,
            width=960,
            height=540,
        )
        return True  # signal to stop

    elif action_type == "interact":
        from utils import StepPopup
        StepPopup.show_blocking(
            "User Interaction Required",
            action_parameter.get("text", "Please interact with the dialog."),
            image_path="",
            timeout_sec=120,
            width=960,
            height=540,
        )
        print("User interaction completed, resuming...")

    else:
        raise ValueError(f"Unsupported action type: {action_type}")

    return False  # continue execution
