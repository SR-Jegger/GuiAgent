"""
LangGraph-based GUI Automation Agent

Architecture:
- State: TypedDict containing all runtime state
- Nodes: capture_node, reasoning_node, execution_node, error_handler_node
- Edges: Conditional routing based on execution status

Usage:
    python run_agent.py --instruction "Your task" --max_steps 50
"""

import os
import sys
import time
import json
import argparse
from typing import Annotated, Any, Optional, Literal
from dataclasses import dataclass, field

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from utils import (
    ComputerTools,
    StepPopup,
    annotate_screenshot,
    build_messages,
    extract_tool_calls,
    extract_template_request,
    extract_action,
    get_output_dir,
    sanitize_filename,
    smart_resize,
    GUIOwlWrapper,
    format_step_text,
    process_markdown_task
)
import re
from PIL import Image
from openai import OpenAI
from template_knowledge import TemplateKnowledgeBase

# ============================================================================
# State Definition
# ============================================================================

class AgentState(TypedDict, total=False):
    """
    Global state for the GUI automation agent.

    Attributes:
        instruction: The user's task instruction
        step_id: Current step number
        screenshot_path: Path to the current screenshot
        messages: LLM conversation history
        llm_response: Raw LLM response text
        actions: List of parsed action dicts
        action_index: Index of current action being executed
        execution_status: "pending" | "running" | "success" | "error"
        error_message: Error description if status is "error"
        retry_count: Number of retries for current step
        max_retries: Maximum retry attempts per step
        stop_flag: Whether to terminate the agent loop
        history: List of past step results
        output_dir: Directory for saving screenshots
        model: Model name for VLM
        base_url: VLM service base URL
        api_key: API key for VLM
        max_steps: Maximum number of steps before forced stop
        add_info: Optional supplementary knowledge
    """
    # Task configuration
    instruction: str
    max_steps: int
    add_info: Optional[str]

    # VLM configuration
    model: str
    base_url: str
    api_key: str

    # Runtime state
    task_name: str
    step_id: int
    screenshot_path: str
    messages: list[dict]
    llm_response: str
    actions: list[dict]
    action_index: int
    execution_status: str
    error_message: Optional[str]
    retry_count: int
    max_retries: int
    stop_flag: bool
    history: list[dict]
    judge_result: Optional[str]
    template_request: Optional[str]
    action_coordinate: Optional[tuple[int, int]]
    action_history: list[dict]

    # Output paths
    output_dir: str

    # Tools instance (ComputerTools for GUI automation)
    tools: Optional[Any]


# ============================================================================
# Node Implementations
# ============================================================================

def capture_node(state: AgentState) -> AgentState:
    task_name = state.get("task_name", "default")
    step_id = state.get("step_id", 0)
    max_steps = state.get("max_steps", 50)
    output_dir = state.get("output_dir", get_output_dir())
    
    # 1.先增加计数
    step_id = step_id + 1
    # 2. 【新增】检查是否超限
    if step_id > max_steps:
        print(f"\n[CAPTURE] Step limit reached: {step_id} > {max_steps}. Stopping.")
        state["stop_flag"] = True
        state["execution_status"] = "success" # 设为 success 以免触发 error_handler
        state["error_message"] = f"Max steps ({max_steps}) exceeded."
        # 直接返回更新，不再执行截图操作，路由会检测到 stop_flag 并结束
        return state
    
    # Generate screenshot path
    screenshot_path = os.path.join(output_dir, f"{task_name}_{step_id}.png")

    # Initialize tools if not in state
    if "tools" not in state:
        tools = ComputerTools()
        tools.reset()  # Minimize all windows
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
            "execution_status": "error",
            "error_message": f"Failed to capture screenshot at step {step_id}",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    print(f"[CAPTURE] Screenshot saved to: {screenshot_path}")

    return {
        "step_id": step_id,
        "screenshot_path": screenshot_path,
        "execution_status": "success",
        "error_message": None,
        "retry_count": 0,
        "tools": tools,  # Pass tools to next nodes
    }


def reasoning_node(state: AgentState) -> AgentState:
    step_id = state.get("step_id", 0)
    screenshot_path = state.get("screenshot_path", "")
    instruction = state.get("instruction", "")
    history = state.get("history", [])
    model = state.get("model", "/mnt/automl/Bigdata/model/GUI-Owl-1.5-8B-Instruct")
    base_url = state.get("base_url", "http://192.168.137.2:4040/v1")
    api_key = state.get("api_key", "EMPTY")
    tools = state.get("tools")

    FALG = False
    if not screenshot_path or not os.path.exists(screenshot_path):
        return {
            "llm_response": "",
            "execution_status": "error",
            "error_message": "Screenshot not available for reasoning",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    print(f"\n[REASONING] Step {step_id}: Calling VLM...")

    try:
        # Build messages with screenshot and history
        messages = build_messages(screenshot_path, instruction, history, model)

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
        vllm = GUIOwlWrapper(api_key, base_url, model)

        # Call VLM
        output_text, raw_messages, raw_response = vllm.predict_mm(messages)

        # if output_text == "Error calling LLM":
        #     return {
        #         "llm_response": "",
        #         "messages": raw_messages if raw_messages else messages,
        #         "execution_status": "error",
        #         "error_message": "VLM API call failed after retries",
        #         "retry_count": state.get("retry_count", 0) + 1,
        #     }

        # # Parse response for reasoning content
        llm_response = output_text

        # Also get response via direct OpenAI client (fallback path from original)
        try:
            client = OpenAI(base_url=base_url, api_key="EMPTY")
            # client = OpenAI(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key="sk...")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            # Prepend reasoning content if present
            llm_response = response.choices[0].message.content
            thought = getattr(response.choices[0].message, "reasoning_content", None)
            if thought:
                llm_response = f"<thinking>\n{thought}\n</thinking>{llm_response}"
        except Exception as e:
            print(f"[REASONING] Warning: Could not get reasoning content: {e}")

        print(f"\n[REASONING] Received response from VLM :")
        # Print truncated response for debugging
        
        print(llm_response)
        action_list = extract_tool_calls(llm_response)
        for reason_action in action_list:
            action_type = reason_action["arguments"]["action"]
            if action_type in ("stop", "terminate", "done"):
                # status = reason_action["arguments"].get("status", "success")
                StepPopup.show_blocking(
                    "Task Completed",
                    f"Task completed with status: success",
                    image_path="",
                    timeout_sec=120,
                    width=960,
                    height=540,
                )
                FALG = True  # signal to stop
                print("[REASONING] Stop signal received")
                break

        return {
            "llm_response": llm_response,
            "messages": messages,
            "execution_status": "success",
            "stop_flag": FALG,
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


def judge_node(state: AgentState) -> AgentState:
    step_id = state.get("step_id", 0)
    llm_response = state.get("llm_response", "")
    task = state.get("instruction", "")

    print(f"\n[JUDGE] Step {step_id}: Evaluating if task need match...")

    pattern = r"Observation:(.*?)Thought:(.*?)(?:Action:|<tool_call>)"
    match = re.search(pattern, llm_response, re.DOTALL)
    observation  = match.group(1).strip() if match else ""
    thought = match.group(2).strip() if match else ""

    from prompt_llm import JUDGE_PROMPT_2 as JUDGE_PROMPT
    print("测试!!!!",observation, thought)
    prompt = JUDGE_PROMPT.format(observation=observation, agent_thought=thought)
    message_for_judge = [{
        "role": "user",
        "content": [{"type": "text", "text": prompt}]
    }]

    try:
        llm_url = "http://192.168.137.2:4050/v1"
        client = OpenAI(base_url=llm_url, api_key="EMPTY")
        response = client.chat.completions.create(
            model="/mnt/automl/Bigdata/model/qwen3_8b",
            messages=message_for_judge,
        )
        print(f"\n[JUDGE] Received response from Judge LLM :")
        print(response.choices[0].message.content)
        if "<template_match>" in response.choices[0].message.content.lower():
            decision = "template_match"
        # elif "template match" in response.choices[0].message.content.lower():
        #     decision = "template_match"
        else:
            decision = "execute"
        state["judge_result"] = decision
        state["template_request"] = response.choices[0].message.content
        state["execution_status"] = "success"
        print(f"[JUDGE] Decision: {decision}")
        return state
    except Exception as e:
        print(f"[JUDGE] Warning: Could not get Judge content: {e}")
        return state

  


def execution_node(state: AgentState) -> AgentState:   
    step_id = state.get("step_id", 0)
    llm_response = state.get("llm_response", "")
    screenshot_path = state.get("screenshot_path", "")
    history = state.get("history", [])
    output_dir = state.get("output_dir", get_output_dir())
    tools = state.get("tools")

    if not llm_response:
        return {
            "actions": [],
            "execution_status": "error",
            "error_message": "No LLM response to execute",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    print(f"\n[EXECUTION] Step {step_id}: Parsing and executing actions...")

    try:
        # Extract actions from LLM response
        action_list = extract_tool_calls(llm_response)

        if not action_list:
            # Try to extract as raw JSON if no tool call blocks found
            print("[EXECUTION] Warning: No actions parsed from LLM response")
            return {
                "actions": [],
                "execution_status": "error",
                "error_message": "LLM did not return valid action JSON",
                "retry_count": state.get("retry_count", 0) + 1,
            }

        print(f"[EXECUTION] Found {len(action_list)} action(s)")

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

        for action_id, action in enumerate(action_list):
            action_parameter = action.get("arguments", {})
            action_type = action_parameter.get("action", "")

            print(f"  [EXECUTION] Action {action_id + 1}: {action_type}")

            # Rescale coordinates
            if state.get("action_coordinate") is None:
                print("  [EXECUTION] !!Rescaling coordinates...")
                rescale_coordinates(action_parameter, resized_width, resized_height)

            # Execute action and check for stop signal
            try:
                print(f"  [EXECUTION] Rescaled parameters: {action_parameter}")
                should_stop = execute_action(tools, action_parameter)
                print(f"  [EXECUTION] Action {action_id + 1} executed successfully")
                if should_stop:
                    stop_flag = True
                    print("[EXECUTION] Stop signal received")
                    break

                executed_actions.append(action_parameter)

                # Annotate screenshot for debugging
                anno_path = annotate_screenshot(
                    screenshot_path,
                    action_parameter,
                    os.path.join(output_dir, f"anno_{step_id}_{action_id}.png"),
                )
                if anno_path:
                    print(f"  [EXECUTION] Annotation saved: {anno_path}")

            except Exception as e:
                print(f"[EXECUTION] Error executing action {action_id}: {e}")
                return {
                    "actions": executed_actions,
                    "execution_status": "error",
                    "error_message": f"Action execution failed: {str(e)}",
                    "retry_count": state.get("retry_count", 0) + 1,
                    "stop_flag": stop_flag,
                }

        # Update history   "output": extract_action(llm_response),
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
            "error_message": None,
            "retry_count": 0,
            "stop_flag": stop_flag,
            "history": history,
        }

    except Exception as e:
        print(f"[EXECUTION] Error: {e}")
        return {
            "actions": [],
            "execution_status": "error",
            "error_message": f"Execution node error: {str(e)}",
            "retry_count": state.get("retry_count", 0) + 1,
        }


def error_handler_node(state: AgentState) -> AgentState:
    """
    Node: Handle execution errors and decide recovery strategy.

    Responsibilities:
    - Log error details
    - Check if retry count exceeds limit
    - Decide whether to retry or terminate
    """
    step_id = state.get("step_id", 0)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)
    error_message = state.get("error_message", "Unknown error")

    print(f"\n[ERROR] Step {step_id}: {error_message}")
    print(f"[ERROR] Retry count: {retry_count}/{max_retries}")

    if retry_count >= max_retries:
        print(f"[ERROR] Max retries exceeded for step {step_id}, terminating...")
        return {
            "execution_status": "error",
            "stop_flag": True,
            "error_message": f"Max retries ({max_retries}) exceeded: {error_message}",
        }

    print(f"[ERROR] Will retry step {step_id}...")

    return {
        "execution_status": "pending",
        "error_message": None,
    }

def template_match_node(state: AgentState) -> AgentState:
    """
    Template matching fallback node.
    Used when VLM cannot determine action coordinates.
    
    <template_request> {"target": {...}, "description": {...}, "expected_action": {...}}</template_request>
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
    # 1 解析 template_request
    # =========================
    request = extract_template_request(template_request)
    print(f"[TEMPLATE_MATCH] Extracted template request: {request}")
    # request = re.sub(r'\{\s*"([^"]+)"\s*\}', r'"\1"', request)
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

    # 2 构造搜索 query
    query = target if target else description
    # query = description if description else target

    # 3 初始化模板库
    kb = TemplateKnowledgeBase(template_dir=template_dir)

    # 4 执行模板匹配
    coord = kb.find_and_locate(query, screenshot_path)

    if not coord:
        print("[TEMPLATE_MATCH] Template match failed")
        return {
            "execution_status": "error",
            "error_message": f"Template match failed for {target}",
            "retry_count": state.get("retry_count", 0) + 1,
        }
    x, y = coord

    print(f"[TEMPLATE_MATCH] Found coordinate: ({x}, {y})")
    # 5 生成 tool_call
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
    Action: {f"已成功执行 {target} 操作/命令, 请继续下一步"}
    <tool_call>{json.dumps(tool_call)}</tool_call>
    """
    print(f"[TEMPLATE_MATCH] Generated tool call: {tool_call_str}")
    return {
        "action_coordinate": (x, y),
        "llm_response": tool_call_str,
        "execution_status": "success",
        "retry_count": 0,
    }
# ============================================================================
# Helper Functions
# ============================================================================

def rescale_coordinates(action_parameter: dict, resized_width: int, resized_height: int) -> None:
    """
    Convert normalized coordinates (0-1000 range) to actual pixel
    coordinates based on the resized image dimensions.

    Modifies action_parameter in place.
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


# ============================================================================
# Graph Construction
# ============================================================================

def build_agent_graph() -> StateGraph:
    """
    Build and return the LangGraph StateGraph for the GUI automation agent.

    Graph structure:
        START -> capture_node -> reasoning_node -> execution_node -> END

    Conditional edges:
        - After capture: success -> reasoning, error -> error_handler
        - After reasoning: success -> execution, error -> error_handler
        - After execution: success/stop -> END, error -> error_handler
        - After error_handler: retry -> capture, terminate -> END
    """
    # Create the graph builder
    builder = StateGraph(AgentState)

    # Add nodes
    builder.add_node("capture", capture_node)
    builder.add_node("reasoning", reasoning_node)
    builder.add_node("judge", judge_node)
    builder.add_node("template_match", template_match_node)
    builder.add_node("execution", execution_node)
    builder.add_node("error_handler", error_handler_node)

    # Set entry point
    builder.set_entry_point("capture")

    # Add conditional edges
    def capture_router(state: AgentState) -> Literal["reasoning", "error_handler"]:
        if state.get("execution_status") == "success":
            return "reasoning"
        return "error_handler"

    # def reasoning_router(state: AgentState) -> Literal["execution", "template_match", "error_handler"]:
    #     if "<template_request>" in state.get("llm_response", ""):
    #         return "template_match"
    #     if "<tool_call>" in state.get("llm_response", "") or state.get("execution_status") == "success":
    #         return "execution"
    #     return "error_handler"

    def reasoning_router(state: AgentState) -> Literal["execution", "judge", "error_handler"]:
        if state.get("stop_flag"):
            return "END"
        elif state.get("execution_status") == "success":
            return "judge"
        return "error_handler"
    
    def judge_router(state: AgentState) -> Literal["execution", "template_match", "error_handler"]:
        judge_result = state.get("judge_result", "execute")
        if judge_result == "template_match":
            return "template_match"
        elif judge_result == "execute":
            return "execution"
        else:
            return "error_handler"
        
    def template_router(state: AgentState) -> Literal["execution", "error_handler"]:
        if state.get("execution_status") == "success":
            return "execution"
        return "error_handler"

    def execution_router(state: AgentState) -> Literal["continue", "error_handler", "end"]:
        if state.get("stop_flag"):
            return "end"
        if state.get("execution_status") == "success":
            return "continue"
        return "error_handler"

    def error_router(state: AgentState) -> Literal["capture", "end"]:
        if state.get("stop_flag"):
            return "end"
        return "capture"

    # Capture -> Reasoning or Error Handler
    builder.add_conditional_edges(
        "capture",
        capture_router,
        {
            "reasoning": "reasoning",
            "error_handler": "error_handler",
        },
    )
    builder.add_conditional_edges(
        "reasoning",
        reasoning_router,
        {
            "END": END,
            "judge": "judge",
            "error_handler": "error_handler",
        },
    )
    
    builder.add_conditional_edges(
        "judge",
        judge_router,
        {
            "execution": "execution",
            "template_match": "template_match",
            "error_handler": "error_handler",
        },
    )

    # Execution -> Continue (loop back) or Error Handler or End
    builder.add_conditional_edges(
        "execution",
        execution_router,
        {
            "continue": "capture",  # Loop back to capture for next step
            "error_handler": "error_handler",
            "end": END,
        },
    )

    builder.add_conditional_edges(
        "template_match",
        template_router,
        {
            "execution": "execution",
            "error_handler": "error_handler",
        },
    )
    
    # Error Handler -> Retry or End
    builder.add_conditional_edges(
        "error_handler",
        error_router,
        {
            "capture": "capture",
            "end": END,
        },
    )

    return builder


def run_agent(
    task_name: str,
    instruction: str,
    model: str = "/mnt/automl/Bigdata/model/GUI-Owl-1.5-8B-Instruct",
    base_url: str = "http://192.168.137.2:4040/v1",
    api_key: str = "EMPTY",
    max_steps: int = 50,
    max_retries: int = 3,
    add_info: Optional[str] = None,
    mdpath: Optional[str] = None,
) -> dict:
    """
    Run the GUI automation agent.

    Returns:
        Final agent state
    """

    print("=" * 60)
    print("GUI Automation Agent (LangGraph)")
    print("=" * 60)
    print(f"Instruction: {instruction[:100]}{'...' if len(instruction) > 100 else ''}")
    print(f"Model: {model}")
    print(f"Max Steps: {max_steps}")
    print(f"Max Retries per Step: {max_retries}")
    print("=" * 60)

    # Build and compile the agent graph
    builder = build_agent_graph()
    agent = builder.compile()

    # Initialize state
    state: AgentState = {
        "task_name": task_name,
        "instruction": instruction,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "max_steps": max_steps,
        "max_retries": max_retries,
        "add_info": add_info,
        "step_id": 0,
        "screenshot_path": "",
        "messages": [],
        "llm_response": "",
        "actions": [],
        "action_index": 0,
        "execution_status": "pending",
        "error_message": None,
        "retry_count": 0,
        "stop_flag": False,
        "history": [],
        "output_dir": get_output_dir(),
    }

    # Main execution loop with step counter
    final_state = state
    config = {"recursion_limit": 500}

    try:
        # Stream through the graph
        for event in agent.stream(state, config=config):
            for node_name, node_output in event.items():
                print(f"  [Node: {node_name}] completed")
                # Update state with node output
                state.update(node_output)
            if state.get("stop_flag"):
                print(f"\n[AGENT] Task completed at step {state.get('step_id', 0)}")

        final_state = state

        # Check for termination conditions
        # if state.get("stop_flag"):
        #     print(f"\n[AGENT] Task completed at step {state.get('step_id', 0)}")

        if state.get("execution_status") == "error" and state.get("stop_flag"):
            print(f"\n[AGENT] Terminating due to errors")

    except Exception as e:
        print(f"\n[AGENT] Graph execution error: {e}")
        final_state = state
        # break

    # Final summary
    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Total Steps Executed: {final_state.get('step_id', 0)}")
    print(f"Stop Flag: {final_state.get('stop_flag', False)}")
    print(f"Final Status: {final_state.get('execution_status', 'unknown')}")
    if final_state.get("error_message"):
        print(f"Error: {final_state['error_message']}")
    print(f"Output Directory: {final_state.get('output_dir', 'N/A')}")
    print("=" * 60)

    return final_state


# ============================================================================
# CLI Entry Point
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LangGraph-based GUI Automation Agent"
    )
    parser.add_argument(
        "--api_key",
        default="EMPTY",
        type=str,
        required=False,
        help="API key for VLM service",
    )
    parser.add_argument(
        "--base_url",
        default="http://192.168.137.2:4040/v1",
        type=str,
        required=False,
        help="Base URL for the VLM service",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        required=False,
        help="The task instruction for the agent to complete",
    )
    parser.add_argument(
        "--mdpath",
        type=str,
        default="test_md/test_ui1.md",
        required=False,
        help="Path to markdown file containing the task instruction",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="/mnt/automl/Bigdata/model/GUI-Owl-1.5-8B-Instruct",
        help="Model name for the VLM service",
    )
    parser.add_argument(
        "--add_info",
        type=str,
        default="",
        help="Optional supplementary knowledge for the task",
    )
    parser.add_argument(
        "--max_steps",
        type=int,
        default=50,
        help="Maximum number of interaction steps (default: 50)",
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=3,
        help="Maximum retry attempts per step (default: 3)",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    # Determine instruction source
    instruction = args.instruction or ""
    mdpath = args.mdpath if not instruction else None

    # if not instruction and mdpath and os.path.exists(mdpath):
    #     with open(mdpath, "r", encoding="utf-8") as f:
    #         instruction = f.read().strip()
    read_markdown = process_markdown_task(mdpath) # if mdpath and os.path.exists(mdpath) else None
    task_name = "default_task"
    if read_markdown:
        task_name = read_markdown["extracted_title"]
        instruction = read_markdown["prompt_for_llm"]
    
    if not instruction:
        print("[ERROR] No instruction provided. Use --instruction or --mdpath")
        sys.exit(1)

    # Run the agent
    final_state = run_agent(
        task_name= task_name,
        instruction=instruction,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
        add_info=args.add_info or None,
        mdpath=mdpath if not args.instruction else None,
    )

    # Return exit code based on final status
    if final_state.get("stop_flag") and final_state.get("execution_status") != "error":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()