"""
LangGraph-based GUI Automation Agent

Architecture:
- State: TypedDict containing all runtime state (defined in nodes/types.py)
- Nodes: Imported from nodes/ package
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

# Import nodes from the nodes package
from nodes import (
    capture_node,
    reasoning_node,
    judge_node,
    execution_node,
    error_handler_node,
    fast_path_node,
    template_match_node,
)
from nodes.task_decomposer_node import task_decomposer_node

# Import utilities
from utils.utils import (
    get_output_dir,
    process_markdown_task,
)

# Import state type from nodes
from nodes.types import AgentState


import logging

# logging.basicConfig(
#     level=logging.DEBUG,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     handlers=[
#         logging.StreamHandler(),           # 终端输出
#         logging.FileHandler('app.log')     # 文件输出
#     ]
# )

# logger = logging.getLogger(__name__)



# Add a pseudo-node to handle continue logic (sub-step iteration)
def continue_handler(state: AgentState) -> AgentState:
    """
    Handle continue logic: increment sub-step index if multi-step task
    """
    sub_steps = state.get("sub_steps", [])
    current_step_index = state.get("current_step_index", 0)

    if sub_steps and current_step_index < len(sub_steps) - 1:
        # Mark current step as completed
        sub_steps[current_step_index]["status"] = "completed"
        # Increment step index
        new_index = current_step_index + 1
        print(f"\n[CONTINUE_HANDLER] Completed step {current_step_index + 1}, moving to step {new_index + 1}")
        return {
            "current_step_index": new_index,
            "sub_steps": sub_steps,
            "step_id": state.get("step_id", 0) + 1,
        }
    elif sub_steps and current_step_index == len(sub_steps) - 1:
        # Last sub-step completed
        sub_steps[current_step_index]["status"] = "completed"
        print(f"\n[CONTINUE_HANDLER] Completed final step {current_step_index + 1}, no more sub-steps")
        return {
            "sub_steps": sub_steps,
            "current_step_index": current_step_index + 1,
            "step_id": state.get("step_id", 0) + 1,
            "stop_flag": True,  # Signal to stop after last step
        }
    else:
        # Single step task, just increment step_id
        print("\n[CONTINUE_HANDLER] Step completed, no more sub-steps")
        return {
            "step_id": state.get("step_id", 0) + 1,
        }

# ============================================================================
# Graph Construction
# ============================================================================

def build_agent_graph() -> StateGraph:
    """
    Build and return the LangGraph StateGraph for the GUI automation agent.

    Graph structure for multi-step tasks:
        START -> task_decomposer -> fast_path -> (execution | capture) -> reasoning -> judge -> (execution | template_match)
                                                                                        -> (next step | END)

    Nodes:
        - task_decomposer: Parse multi-step tasks into sub-steps
        - fast_path: Rule-based quick matching
        - capture: Screenshot capture
        - reasoning: VLM-based action planning
        - judge: Template match decision
        - template_match: Template-based fallback
        - execution: Action execution
        - error_handler: Error recovery

    Conditional edges:
        - After task_decomposer: -> fast_path
        - After fast_path: matched -> execution, not matched -> capture
        - After capture: success -> reasoning, error -> error_handler
        - After reasoning: success -> judge, stop -> END, error -> error_handler
        - After judge: template_match -> template_match, execute -> execution
        - After template_match: success -> execution, error -> error_handler
        - After execution: continue -> (next step or capture), stop -> END, error -> error_handler
        - After error_handler: retry -> capture, max retries -> END
    """
    # Create the graph builder
    builder = StateGraph(AgentState)

    # Add nodes
    builder.add_node("task_decomposer", task_decomposer_node)
    builder.add_node("fast_path", fast_path_node)
    builder.add_node("capture", capture_node)
    builder.add_node("reasoning", reasoning_node)
    builder.add_node("judge", judge_node)
    builder.add_node("template_match", template_match_node)
    builder.add_node("execution", execution_node)
    builder.add_node("continue_handler", continue_handler)
    builder.add_node("error_handler", error_handler_node)

    # Set entry point to task_decomposer (parse multi-step tasks first)
    builder.set_entry_point("task_decomposer")

    # Add conditional edges
    def fast_path_router(state: AgentState) -> Literal["execution", "capture"]:
        """Fast Path 路由：匹配成功直接执行，失败则进入 capture->reasoning 流程"""
        if state.get("fast_path_matched", False):
            return "execution"
        return "capture"

    def capture_router(state: AgentState) -> Literal["reasoning", "error_handler"]:
        if state.get("execution_status") == "success":
            return "reasoning"
        return "error_handler"

    def reasoning_router(state: AgentState) -> Literal["END", "judge", "error_handler"]:
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

    def execution_router(state: AgentState) -> Literal["continue", "error_handler", "END"]:
        if state.get("stop_flag"):
            return "END"
        if not state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_next"
        if state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_current"
        return "error_handler"

    # Continue Handler -> Fast Path (next sub-step) or Capture (normal flow)
    def continue_handler_router(state: AgentState) -> Literal["fast_path", "capture"]:
        sub_steps = state.get("sub_steps", [])
        current_step_index = state.get("current_step_index", 0)
        if sub_steps and current_step_index < len(sub_steps):
            # Has next sub-step, go to fast_path
            return "fast_path"
        return "END"

    def error_router(state: AgentState) -> Literal["capture", "end"]:
        if state.get("stop_flag"):
            return "end"
        return "capture"

    # Task Decomposer -> Fast Path
    builder.add_edge("task_decomposer", "fast_path")

    # Fast Path -> Execution or Capture (fallback)
    builder.add_conditional_edges(
        "fast_path",
        fast_path_router,
        {
            "execution": "execution",
            "capture": "capture",
        },
    )

    # Capture -> Reasoning or Error Handler
    builder.add_conditional_edges(
        "capture",
        capture_router,
        {
            "reasoning": "reasoning",
            "error_handler": "error_handler",
        },
    )

    # Reasoning -> Judge or END or Error Handler
    builder.add_conditional_edges(
        "reasoning",
        reasoning_router,
        {
            "END": "continue_handler", # END
            "judge": "judge",
            "error_handler": "error_handler",
        },
    )

    # Judge -> Execution or Template Match or Error Handler
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
            "continue_next": "continue_handler",
            "continue_current": "capture",
            "error_handler": "error_handler",
            "END": "continue_handler", # If stop_flag is set, we will route to END which is handled by continue_handler to finalize the task
        },
    )

    builder.add_conditional_edges(
        "continue_handler",
        continue_handler_router,
        {
            "fast_path": "fast_path",
            "END": END,
        },
    )

    # Template Match -> Execution or Error Handler
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


# ===========================================================================
# Graph without judge and template match (for simpler tasks)
# ============================================================================
def build_agent_graph_simple() -> StateGraph:
    """
    Build and return the LangGraph StateGraph for the GUI automation agent.

    Graph structure for multi-step tasks:
        START -> task_decomposer -> fast_path -> (execution | capture) -> reasoning -> judge -> (execution | template_match)
                                                                                        -> (next step | END)
    Nodes:
        - task_decomposer: Parse multi-step tasks into sub-steps
        - fast_path: Rule-based quick matching
        - capture: Screenshot capture
        - reasoning: VLM-based action planning
        - x judge: Template match decision
        - x template_match: Template-based fallback
        - execution: Action execution
        - error_handler: Error recovery
    Conditional edges:
        - After task_decomposer: -> fast_path
        - After fast_path: matched -> execution, not matched -> capture
        - After capture: success -> reasoning, error -> error_handler
        - After reasoning: success -> judge, stop -> END, error -> error_handler
        - x After judge: template_match -> template_match, execute -> execution
        - x After template_match: success -> execution, error -> error_handler
        - After execution: continue -> (next step or capture), stop -> END, error -> error_handler
        - After error_handler: retry -> capture, max retries -> END
    """
    # Create the graph builder
    builder = StateGraph(AgentState)

    # Add nodes
    builder.add_node("task_decomposer", task_decomposer_node)
    builder.add_node("fast_path", fast_path_node)
    builder.add_node("capture", capture_node)
    builder.add_node("reasoning", reasoning_node)
    builder.add_node("execution", execution_node)
    builder.add_node("continue_handler", continue_handler)
    builder.add_node("error_handler", error_handler_node)

    # Set entry point to task_decomposer (parse multi-step tasks first)
    builder.set_entry_point("task_decomposer")

    # Add conditional edges
    def fast_path_router(state: AgentState) -> Literal["execution", "capture"]:
        """Fast Path 路由：匹配成功直接执行，失败则进入 capture->reasoning 流程"""
        if state.get("fast_path_matched", False):
            return "execution"
        return "capture"

    def capture_router(state: AgentState) -> Literal["reasoning", "error_handler"]:
        if state.get("execution_status") == "success":
            return "reasoning"
        return "error_handler"

    def reasoning_router(state: AgentState) -> Literal["END", "execution", "error_handler"]:
        if state.get("stop_flag"):
            return "END"
        elif state.get("execution_status") == "success":
            return "execution"
        return "error_handler"

    def execution_router(state: AgentState) -> Literal["continue", "error_handler", "END"]:
        if state.get("stop_flag"):
            return "END"
        if not state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_next"
        if state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_current"
        return "error_handler"

    # Continue Handler -> Fast Path (next sub-step) or Capture (normal flow)
    def continue_handler_router(state: AgentState) -> Literal["fast_path", "capture"]:
        sub_steps = state.get("sub_steps", [])
        current_step_index = state.get("current_step_index", 0)
        if sub_steps and current_step_index < len(sub_steps):
            # Has next sub-step, go to fast_path
            return "fast_path"
        return "END"

    def error_router(state: AgentState) -> Literal["capture", "end"]:
        if state.get("stop_flag"):
            return "end"
        return "capture"

    # Task Decomposer -> Fast Path
    builder.add_edge("task_decomposer", "fast_path")

    # Fast Path -> Execution or Capture (fallback)
    builder.add_conditional_edges(
        "fast_path",
        fast_path_router,
        {
            "execution": "execution",
            "capture": "capture",
        },
    )

    # Capture -> Reasoning or Error Handler
    builder.add_conditional_edges(
        "capture",
        capture_router,
        {
            "reasoning": "reasoning",
            "error_handler": "error_handler",
        },
    )

    # Reasoning -> Judge or END or Error Handler
    builder.add_conditional_edges(
        "reasoning",
        reasoning_router,
        {
            "END": "continue_handler", # END
            "execution": "execution", # Skip judge and template match for simpler tasks
            "error_handler": "error_handler",
        },
    )

    # Execution -> Continue (loop back) or Error Handler or End
    builder.add_conditional_edges(
        "execution",
        execution_router,
        {
            "continue_next": "continue_handler",
            "continue_current": "capture",
            "error_handler": "error_handler",
            "END": "continue_handler", # If stop_flag is set, we will route to END which is handled by continue_handler to finalize the task
        },
    )

    builder.add_conditional_edges(
        "continue_handler",
        continue_handler_router,
        {
            "fast_path": "fast_path",
            "END": END,
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
# ============================================================================
# Agent Runner
# ============================================================================

def run_agent(
    task_name: str,
    instruction: str,
    MODEL_CONFIG: Optional[Any] = None,
    max_steps: int = 50,
    max_retries: int = 3,
    add_info: Optional[str] = None,
    mdpath: Optional[str] = None,
    rules_dir: str = "./rules",
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
    print(f"Max Steps: {max_steps}")
    print(f"Max Retries per Step: {max_retries}")
    print("=" * 60)

    # Build and compile the agent graph
    # builder = build_agent_graph()
    builder = build_agent_graph_simple()  # For simpler tasks without judge/template match
    agent = builder.compile()

    # Initialize state
    state: AgentState = {
        "task_name": task_name,
        "instruction": instruction,
        "MODEL_CONFIG": MODEL_CONFIG,
        "max_steps": max_steps,
        "max_retries": max_retries,
        "add_info": add_info,
        "rules_dir": rules_dir,
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
        "sub_flag": True,  # Whether to continue current sub-step or move to next sub-step
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
        if state.get("execution_status") == "error" and state.get("stop_flag"):
            print(f"\n[AGENT] Terminating due to errors")

    except Exception as e:
        print(f"\n[AGENT] Graph execution error: {e}")
        final_state = state

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
    parser.add_argument(
        "--rules_dir",
        type=str,
        default="./rules",
        help="Directory containing rule JSON files (default: ./rules)",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    # Determine instruction source
    instruction = args.instruction or ""
    mdpath = args.mdpath if not instruction else None

    read_markdown = process_markdown_task(mdpath)
    task_name = "default_task"
    if read_markdown:
        task_name = read_markdown["extracted_title"]
        instruction = read_markdown["prompt_for_llm"]

    if not instruction:
        print("[ERROR] No instruction provided. Use --instruction or --mdpath")
        sys.exit(1)

    modelconfig = json.load(open("nodes/model_config.json"))
    # Run the agent
    final_state = run_agent(
        task_name=task_name,
        instruction=instruction,
        MODEL_CONFIG=modelconfig,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
        add_info=args.add_info or None,
        mdpath=mdpath if not args.instruction else None,
        rules_dir=args.rules_dir,
    )

    # Return exit code based on final status
    if final_state.get("stop_flag") and final_state.get("execution_status") != "error":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
