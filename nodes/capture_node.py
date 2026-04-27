"""
Capture node for GUI automation agent.

Responsible for capturing screenshots at each step.
"""

import os
from typing import TYPE_CHECKING

import cv2

from nodes.types import AgentState
from utils.computer_tools import ComputerTools
from utils.utils import get_output_dir


def capture_node(state: AgentState) -> AgentState:
    """
    Capture a screenshot of the current screen.

    This node:
    1. Increments the step counter
    2. Checks if max steps limit is reached
    3. Captures a screenshot and saves it to the output directory
    4. Returns updated state with screenshot path

    Args:
        state: Current agent state

    Returns:
        Updated state with screenshot information
    """
    # Check for cancellation BEFORE starting any work
    stop_event = state.get("stop_event")
    if stop_event and stop_event.is_set():
        print("\n[CAPTURE] Task cancelled - exiting early")
        return {
            "step_id": state.get("step_id", 0),
            "screenshot_path": "",
            "execution_status": "error",
            "error_message": "Task cancelled",
            "stop_flag": True,
            "retry_count": 0,
        }

    task_name = state.get("task_name", "default")
    step_id = state.get("step_id", 0)
    max_steps = state.get("max_steps", 50)
    output_dir = state.get("output_dir", get_output_dir())
    
    image_base_url = state.get(
        "image_base_url",
        "http://192.168.137.1:8000/images"
    ).rstrip("/")

    # 1. Increment step counter
    step_id = step_id + 1

    # 2. Check if step limit is reached
    if step_id > max_steps:
        print(f"\n[CAPTURE] Step limit reached: {step_id} > {max_steps}. Stopping.")
        state["stop_flag"] = True
        state["execution_status"] = "success"  # Set to success to avoid triggering error_handler
        state["error_message"] = f"Max steps ({max_steps}) exceeded."
        # Return state updates without executing screenshot
        # Routing will detect stop_flag and terminate
        return {
            "step_id": step_id,
            "stop_flag": True,
            "execution_status": "success",
            "error_message": f"Max steps ({max_steps}) exceeded.",
        }

    # Generate screenshot path
    filename = f"{task_name}_{step_id}.jpg"
    screenshot_path = os.path.join(output_dir, filename)
    screenshot_url = f"{image_base_url}/{filename}"
    print(f"\n[CAPTURE] {screenshot_url}...")
    # Initialize tools if not in state
    if "tools" not in state:
        tools = ComputerTools()
        # tools.reset()  # Minimize all windows
        state["tools"] = tools
    else:
        tools = state["tools"]

    print(f"\n[CAPTURE] Step {step_id}: Capturing screenshot...")

    # Attempt screenshot capture
    success = tools.get_screenshot(screenshot_path, retry_times=3)

    if not success:
        return {
            "step_id": step_id,
            "screenshot_path": "",
            "screenshot_url": "",
            "execution_status": "error",
            "error_message": f"Failed to capture screenshot at step {step_id}",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    print(f"[CAPTURE] Screenshot saved to: {screenshot_path}")
    print(f"[CAPTURE] Screenshot URL: {screenshot_url}")
    return {
        "step_id": step_id,
        "screenshot_path": screenshot_path,
        "screenshot_url": screenshot_url,
        "execution_status": "success",
        "error_message": None,
        "retry_count": 0,
        "tools": tools,
    }
