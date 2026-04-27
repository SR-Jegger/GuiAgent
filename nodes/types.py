"""
Shared types and utilities for nodes.

This module exports the AgentState type and common imports used across nodes.
"""

from typing import Annotated, Any, Optional, Literal
from typing_extensions import TypedDict
import asyncio


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
        judge_result: Optional[str]
        template_request: Optional[str]
        action_coordinate: Optional[tuple[int, int]]
        action_history: list[dict]
        fast_path_matched: bool

        # Task configuration
        instruction: str
        max_steps: int
        add_info: Optional[str]
        rules_dir: str

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

        # Output paths
        output_dir: str

        # Tools instance
        tools: Optional[Any]
    """
    # Task configuration
    instruction: str
    max_steps: int
    add_info: Optional[str]
    rules_dir: str

    # VLM and LLM configuration
    MODEL_CONFIG: Optional[Any]

    # Runtime state
    task_name: str
    step_id: int
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
    fast_path_matched: bool

    # Output paths
    output_dir: str
    screenshot_path: str
    image_base_url: str
    screenshot_url: str

    # Tools instance
    tools: Optional[Any]

    # Task decomposition (for multi-step tasks)
    sub_steps: list[dict]  # List of sub-step dicts: [{"step_id": 1, "description": "...", "status": "pending"}, ...]
    current_step_index: int  # Index of current sub-step being executed
    continue_substep_flag: bool  # Whether continue the current sub_step task  or next  sub_step

    # Intent mapping configuration
    use_intent_mapping: bool  # Whether to use intent mapping for task decomposition
    intent_mapping_config_path: Optional[str]  # Path to intent mapping config file

    # Cancellation support (asyncio.Event is not serializable but works for runtime)
    stop_event: Optional[Any]  # asyncio.Event for task cancellation