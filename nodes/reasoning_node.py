"""
Reasoning node for GUI automation agent.

Responsible for calling VLM to analyze screenshots and generate action plans.
"""

import os
from typing import TYPE_CHECKING

from openai import OpenAI
from PIL import Image

from nodes.types import AgentState
from utils.parsers import extract_tool_calls
from utils.popup import StepPopup
from utils.utils import (
    build_messages,
    GUIOwlWrapper,
    smart_resize,
)


def reasoning_node(state: AgentState) -> AgentState:
    """
    Call VLM to analyze the screenshot and generate action plan.

    This node:
    1. Builds messages with screenshot and history
    2. Calls VLM for reasoning
    3. Extracts tool calls from response
    4. Checks for stop signals

    For multi-step tasks:
    - Uses current sub-step description as the primary instruction
    - Includes global_task_instruction as context for better understanding

    Args:
        state: Current agent state

    Returns:
        Updated state with LLM response and parsed actions
    """
    # Check for cancellation BEFORE starting any work
    stop_event = state.get("stop_event")
    if stop_event and stop_event.is_set():
        print("\n[REASONING] Task cancelled - exiting early")
        return {
            "llm_response": "",
            "execution_status": "error",
            "error_message": "Task cancelled",
            "stop_flag": True,
            "retry_count": 0,
        }

    step_id = state.get("step_id", 0)
    screenshot_path = state.get("screenshot_path", "")

    # For multi-step tasks, use current sub-step description
    sub_steps = state.get("sub_steps", [])
    current_step_index = state.get("current_step_index", 0)
    task_instruction = state.get("instruction", "")

    if sub_steps and current_step_index < len(sub_steps):
        # Multi-step task: use current sub-step as primary instruction
        current_step = sub_steps[current_step_index]
        current_instruction = current_step.get("description", "")
        print(f"\n[REASONING] Executing sub-step {current_step_index + 1}/{len(sub_steps)}")
        print(f"[REASONING] Sub-step: {current_instruction}")
    else:
        # Single-step task
        current_instruction = state.get("instruction", "")

    history = state.get("history", [])

    MODEL_CONFIG = state.get("MODEL_CONFIG", None)
    vlm_config = MODEL_CONFIG["models"]["GUI-Owl-1.5-8B"]
    model = vlm_config["model"]
    base_url = vlm_config["base_url"]
    api_key = vlm_config["api_key"]
    continue_substep_flag = True  # signal to continue to next sub-step by default

    if not screenshot_path or not os.path.exists(screenshot_path):
        return {
            "llm_response": "",
            "execution_status": "error",
            "error_message": "Screenshot not available for reasoning",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    print(f"\n[REASONING] Step {step_id}: Calling VLM...")
    # if task_instruction and task_instruction != current_instruction:
    #     print(f"[REASONING] Task context: {task_instruction[:80]}...")

    try:
        # Build messages with screenshot and history
        print(f"[REASONING] current_instruction: {current_instruction}")
        messages = build_messages(screenshot_path, current_instruction, history, model)

        # Add supplementary info if provided
        if state.get("add_info"):
            # Inject into system prompt or as additional instruction
            add_info = state["add_info"]
            # Find and modify the instruction in the last user message
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    for item in content:
                        if item.get("type") == "text":
                            item["text"] += f"\n\nAdditional Info: {add_info}"
                    break

        # Initialize VLM wrapper
        # vllm = GUIOwlWrapper(api_key, base_url, model)
        # Call VLM
        # output_text, raw_messages, raw_response = vllm.predict_mm(messages)
        # Parse response for reasoning content
        # llm_response = output_text

        # Also get response via direct OpenAI client (fallback path)
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            # Check for cancellation after LLM call
            if stop_event and stop_event.is_set():
                print("\n[REASONING] Task cancelled after LLM call")
                return {
                    "llm_response": "",
                    "execution_status": "error",
                    "error_message": "Task cancelled",
                    "stop_flag": True,
                    "retry_count": 0,
                }
            # Prepend reasoning content if present
            llm_response = response.choices[0].message.content
            thought = getattr(response.choices[0].message, "reasoning_content", None)
            if thought:
                llm_response = f"<thinking>\n{thought}\n</thinking>{llm_response}"
        except Exception as e:
            print(f"[REASONING] Warning: Could not get reasoning content: {e}")

        print(f"\n[REASONING] Received response from VLM:")
        print(llm_response)

        action_list = extract_tool_calls(llm_response)
        for reason_action in action_list:
            action_type = reason_action["arguments"]["action"]
            if action_type in ("stop", "terminate", "done"):
                # StepPopup.show_blocking(
                #     "Task Completed",
                #     f"Task completed with status: success",
                #     image_path="",
                #     timeout_sec=120,
                #     width=960,
                #     height=540,
                # )
                
                continue_substep_flag = False  # signal to not continue to current sub-step
                print("[REASONING] Stop signal received")
                break

        return {
            "llm_response": llm_response,
            "messages": messages,
            "execution_status": "success",
            "continue_substep_flag": continue_substep_flag,
            "error_message": None,
            "retry_count": 0,
        }

    except Exception as e:
        print(f"[REASONING] Error: {e}")
        return {
            "llm_response": "",
            "execution_status": "error",
            "error_message": f"Reasoning node error: {str(e)}",
            "retry_count": state.get("retry_count", 0) + 1,
        }
