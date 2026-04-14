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
import asyncio
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

    【流程变更】Graph structure for multi-step tasks (Capture前置为Fast Path提供截图):
        START -> task_decomposer -> capture -> fast_path -> (matched: execution | not matched: reasoning) -> judge -> (execution | template_match)
                                                                                                           -> (next step | END)

    Nodes:
        - task_decomposer: Parse multi-step tasks into sub-steps
        - capture: Screenshot capture (前置为Fast Path提供截图)
        - fast_path: Rule-based quick matching (使用Capture提供的截图进行匹配)
        - reasoning: VLM-based action planning
        - judge: Template match decision
        - template_match: Template-based fallback
        - execution: Action execution
        - error_handler: Error recovery

    Conditional edges:
        - After task_decomposer: -> capture (前置)
        - After capture: success -> fast_path, error -> error_handler
        - After fast_path: matched -> execution, not matched -> reasoning
        - After reasoning: success -> judge, stop -> END, error -> error_handler
        - After judge: template_match -> template_match, execute -> execution
        - After template_match: success -> execution, error -> error_handler
        - After execution: continue -> capture (next step), stop -> END, error -> error_handler
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
    # 【流程变更】Fast Path Router：匹配成功执行，失败进入Reasoning（而非Capture）
    def fast_path_router(state: AgentState) -> Literal["execution", "reasoning"]:
        """Fast Path 路由：匹配成功直接执行，失败则进入 Reasoning 流程（Capture已前置，无需再走Capture）"""
        if state.get("fast_path_matched", False):
            return "execution"
        return "reasoning"  # 【变更】capture -> reasoning

    # 【流程变更】Capture Router：成功时进入Fast Path而非Reasoning
    def capture_router(state: AgentState) -> Literal["END", "fast_path", "error_handler"]:
        """Capture 路由：检查 stop_flag 或 execution_status，成功时进入Fast Path"""
        if state.get("stop_flag"):
            return "END"
        if state.get("execution_status") == "success":
            return "fast_path"  # 【变更】reasoning -> fast_path（Capture前置为Fast Path提供截图）
        return "error_handler"

    def reasoning_router(state: AgentState) -> Literal["sub_end", "judge", "error_handler"]:
        # if state.get("stop_flag"):
        #     return "END"
        if not state.get("sub_flag") and state.get("execution_status") == "success":
            return "sub_end"
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

    # 【流程变更】Continue Handler -> Capture（而非Fast Path，因为Capture已前置）
    def continue_handler_router(state: AgentState) -> Literal["capture", "END"]:
        sub_steps = state.get("sub_steps", [])
        current_step_index = state.get("current_step_index", 0)
        if sub_steps and current_step_index < len(sub_steps):
            # 【变更】有下一个子步骤时进入Capture（而非Fast Path），Capture为Fast Path提供截图
            return "capture"
        return "END"

    def error_router(state: AgentState) -> Literal["capture", "end"]:
        if state.get("stop_flag"):
            return "end"
        return "capture"

    # 【流程变更】Task Decomposer -> Capture（不再直接到Fast Path，capture前置为fast_path提供截图）
    builder.add_edge("task_decomposer", "capture")

    # 【流程变更】Fast Path -> Execution or Reasoning（匹配失败时进入Reasoning而非Capture，因为Capture已前置）
    builder.add_conditional_edges(
        "fast_path",
        fast_path_router,
        {
            "execution": "execution",
            "reasoning": "reasoning",  # 【变更】capture -> reasoning
        },
    )

    # 【流程变更】Capture -> Fast Path or Error Handler or END（成功时进入Fast Path而非Reasoning）
    builder.add_conditional_edges(
        "capture",
        capture_router,
        {
            "END": END,
            "fast_path": "fast_path",  # 【变更】reasoning -> fast_path
            "error_handler": "error_handler",
        },
    )

    # Reasoning -> Judge or END or Error Handler
    builder.add_conditional_edges(
        "reasoning",
        reasoning_router,
        {
            "sub_end": "continue_handler", # Sub-step end
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
            "capture": "capture",  # 【变更】fast_path -> capture（Continue Handler进入Capture而非Fast Path）
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

    【流程变更】Graph structure for multi-step tasks (Capture前置为Fast Path提供截图):
        START -> task_decomposer -> capture -> fast_path -> (matched: execution | not matched: reasoning)
                                                                           -> (next step | END)

    Nodes:
        - task_decomposer: Parse multi-step tasks into sub-steps
        - capture: Screenshot capture (前置为Fast Path提供截图)
        - fast_path: Rule-based quick matching (使用Capture提供的截图进行匹配)
        - reasoning: VLM-based action planning
        - x judge: Template match decision
        - x template_match: Template-based fallback
        - execution: Action execution
        - error_handler: Error recovery

    Conditional edges:
        - After task_decomposer: -> capture (前置)
        - After capture: success -> fast_path, error -> error_handler
        - After fast_path: matched -> execution, not matched -> reasoning
        - After reasoning: success -> execution, stop -> END, error -> error_handler
        - x After judge: template_match -> template_match, execute -> execution
        - x After template_match: success -> execution, error -> error_handler
        - After execution: continue -> capture (next step), stop -> END, error -> error_handler
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
    # 【流程变更】Fast Path Router：匹配成功执行，失败进入Reasoning（而非Capture）
    def fast_path_router(state: AgentState) -> Literal["execution", "reasoning"]:
        """Fast Path 路由：匹配成功直接执行，失败则进入 Reasoning 流程（Capture已前置，无需再走Capture）"""
        if state.get("fast_path_matched", False):
            return "execution"
        return "reasoning"  # 【变更】capture -> reasoning

    # 【流程变更】Capture Router：成功时进入Fast Path而非Reasoning
    def capture_router(state: AgentState) -> Literal["END", "fast_path", "error_handler"]:
        """Capture 路由：检查 stop_flag 或 execution_status，成功时进入Fast Path"""
        if state.get("stop_flag"):
            return "END"
        if state.get("execution_status") == "success":
            return "fast_path"  # 【变更】reasoning -> fast_path（Capture前置为Fast Path提供截图）
        return "error_handler"

    def reasoning_router(state: AgentState) -> Literal["sub_end", "execution", "error_handler"]:
        # if state.get("stop_flag"):
        #     return "END"
        if not state.get("sub_flag") and state.get("execution_status") == "success":
            return "sub_end"
        elif state.get("execution_status") == "success":
            return "execution"
        return "error_handler"

    def execution_router(state: AgentState) -> Literal["continue", "error_handler", "END"]:
        # 这个END指的是当前的子任务是否被判断为完成，而不是整个task的结束。continue_handler会根据sub_steps和current_step_index来决定是进入下一步的fast_path还是结束整个task。
        if state.get("stop_flag"):
            return "END"
        if not state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_next"
        if state.get("sub_flag") and state.get("execution_status") == "success":
            return "continue_current"
        return "error_handler"

    # 【流程变更】Continue Handler -> Capture（而非Fast Path，因为Capture已前置）
    def continue_handler_router(state: AgentState) -> Literal["capture", "END"]:
        sub_steps = state.get("sub_steps", [])
        current_step_index = state.get("current_step_index", 0)
        print(f"\n[CONTINUE_HANDLER_ROUTER] current_step_index: {current_step_index}, sub_steps: {len(sub_steps)}")
        if sub_steps and current_step_index < len(sub_steps):
            # 【变更】有下一个子步骤时进入Capture（而非Fast Path），Capture为Fast Path提供截图
            return "capture"
        return "END"

    def error_router(state: AgentState) -> Literal["capture", "end"]:
        if state.get("stop_flag"):
            return "end"
        return "capture"

    # 【流程变更】Task Decomposer -> Capture（不再直接到Fast Path，capture前置为fast_path提供截图）
    builder.add_edge("task_decomposer", "capture")

    # 【流程变更】Fast Path -> Execution or Reasoning（匹配失败时进入Reasoning而非Capture，因为Capture已前置）
    builder.add_conditional_edges(
        "fast_path",
        fast_path_router,
        {
            "execution": "execution",
            "reasoning": "reasoning",  # 【变更】capture -> reasoning
        },
    )

    # 【流程变更】Capture -> Fast Path or Error Handler or END（成功时进入Fast Path而非Reasoning）
    builder.add_conditional_edges(
        "capture",
        capture_router,
        {
            "END": END,
            "fast_path": "fast_path",  # 【变更】reasoning -> fast_path
            "error_handler": "error_handler",
        },
    )

    # Reasoning -> Judge or END or Error Handler
    builder.add_conditional_edges(
        "reasoning",
        reasoning_router,
        {
            "sub_end": "continue_handler", # END
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
            "capture": "capture",  # 【变更】fast_path -> capture（Continue Handler进入Capture而非Fast Path）
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
# Agent Runner - Async version for FastAPI integration
# ============================================================================

async def run_agent_async(
    task_name: str,
    instruction: str,
    MODEL_CONFIG: Optional[Any] = None,
    max_steps: int = 50,
    max_retries: int = 3,
    add_info: Optional[str] = None,
    rules_dir: str = "./rules",
    stop_event: Optional[asyncio.Event] = None,
    compiled_agent=None,  # Pre-compiled agent for hot-start
) -> dict:
    """
    Run the GUI automation agent asynchronously.

    Args:
        stop_event: Optional asyncio.Event to cancel the task
        compiled_agent: Optional pre-compiled agent graph (for hot-start)

    Returns:
        Final agent state
    """
    import asyncio

    print("=" * 60)
    print("GUI Automation Agent (LangGraph)")
    print("=" * 60)
    print(f"Instruction: {instruction[:100]}{'...' if len(instruction) > 100 else ''}")
    print(f"Max Steps: {max_steps}")
    print(f"Max Retries per Step: {max_retries}")
    print("=" * 60)

    # Use pre-compiled agent if provided (hot-start), otherwise compile now
    if compiled_agent is not None:
        agent = compiled_agent
        print("[INFO] Using pre-compiled agent (hot-start)")
    else:
        builder = build_agent_graph_simple()
        agent = builder.compile()
        print("[INFO] Compiled agent graph (cold-start)")

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
        "sub_flag": True,
        "history": [],
        "output_dir": get_output_dir(),
        "stop_event": stop_event,  # Pass stop_event to state for node-level cancellation check
    }
    final_state = state
    config = {"recursion_limit": 500}

    try:
        # Use async streaming to allow real-time cancellation checks
        # agent.astream() is async, so we can check stop_event between events
        async for event in agent.astream(state, config=config):
            # Check for cancellation BEFORE processing each event
            if stop_event and stop_event.is_set():
                print("\n[AGENT] Task cancelled by user")
                state["stop_flag"] = True
                state["execution_status"] = "error"
                state["error_message"] = "Task cancelled"
                break

            for node_name, node_output in event.items():
                print(f"  [Node: {node_name}] completed")
                state.update(node_output)

            # Also check after processing event
            if state.get("stop_flag"):
                print(f"\n[AGENT] Task completed at step {state.get('step_id', 0)}")
                # break

            # Check cancellation again after state update
            if stop_event and stop_event.is_set():
                print("\n[AGENT] Task cancelled by user (post-event check)")
                state["stop_flag"] = True
                state["execution_status"] = "error"
                state["error_message"] = "Task cancelled"
                break

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
    print(f"Total Steps Executed: {state.get('step_id', 0)}")
    print(f"Stop Flag: {state.get('stop_flag', False)}")
    print(f"Final Status: {state.get('execution_status', 'unknown')}")
    if state.get("error_message"):
        print(f"Error: {state['error_message']}")
    print(f"Output Directory: {state.get('output_dir', 'N/A')}")
    print("=" * 60)

    return final_state


def run_agent(
    task_name: str,
    instruction: str,
    MODEL_CONFIG: Optional[Any] = None,
    max_steps: int = 50,
    max_retries: int = 3,
    add_info: Optional[str] = None,
    rules_dir: str = "./rules",
) -> dict:
    """
    Synchronous wrapper for run_agent_async.

    For CLI usage. For API usage, use run_agent_async directly.
    """
    return asyncio.run(run_agent_async(
        task_name=task_name,
        instruction=instruction,
        MODEL_CONFIG=MODEL_CONFIG,
        max_steps=max_steps,
        max_retries=max_retries,
        add_info=add_info,
        rules_dir=rules_dir,
    ))
