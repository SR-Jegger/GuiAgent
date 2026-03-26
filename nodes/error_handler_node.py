"""
Error handler node for GUI automation agent.

Responsible for handling execution errors and deciding recovery strategy.
"""

from typing import TYPE_CHECKING

from nodes.types import AgentState


def error_handler_node(state: AgentState) -> AgentState:
    """
    Handle execution errors and decide recovery strategy.

    This node:
    1. Logs error details
    2. Checks if retry count exceeds limit
    3. Decides whether to retry or terminate

    Args:
        state: Current agent state

    Returns:
        Updated state with retry decision
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
