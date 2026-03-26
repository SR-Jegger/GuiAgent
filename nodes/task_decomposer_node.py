"""
Task Decomposer Node for GUI automation agent.

Responsible for:
1. Parsing complex multi-step tasks into structured sub-steps
2. Initializing sub-step state for iterative execution
3. Passing global task instruction as context to each sub-step
"""

import re
from typing import TYPE_CHECKING

from nodes.types import AgentState


def parse_task_into_steps(instruction: str) -> list[dict]:
    """
    Parse a complex task instruction into structured sub-steps.

    This function analyzes the task instruction and breaks it down into
    individual executable steps. Each step will be processed independently
    through the fast_path -> capture -> reasoning -> judge -> execution flow.

    Args:
        instruction: The full task instruction string

    Returns:
        List of sub-step dicts with structure:
        [
            {"step_id": 1, "description": "...", "status": "pending"},
            {"step_id": 2, "description": "...", "status": "pending"},
            ...
        ]
    """
    steps = []

    # Method 1: Split by newline - each non-empty line is a step
    lines = [line.strip() for line in instruction.split('\n') if line.strip()]

    if len(lines) > 1:
        # Multiple lines = multiple steps
        for i, line in enumerate(lines, 1):
            # Skip lines that look like headers or metadata
            if line.startswith('#') or line.startswith('"""'):
                continue
            steps.append({
                "step_id": i,
                "description": line,
                "status": "pending"
            })
    else:
        # Method 2: Single line task - treat as one step
        # Try to split by common step indicators
        single_line = lines[0] if lines else instruction

        # Check for numbered steps like "1. xxx 2. xxx"
        numbered_pattern = r'\d+[\.、]\s*[^,.]+?'
        numbered_matches = re.findall(numbered_pattern, single_line)

        if len(numbered_matches) > 1:
            for i, match in enumerate(numbered_matches, 1):
                # Remove the number prefix
                desc = re.sub(r'^\d+[\.、]\s*', '', match).strip()
                steps.append({
                    "step_id": i,
                    "description": desc,
                    "status": "pending"
                })
        else:
            # Single step task
            steps.append({
                "step_id": 1,
                "description": single_line,
                "status": "pending"
            })

    print(f"\n[TASK_DECOMPOSER] Parsed {len(steps)} sub-step(s):")
    for step in steps:
        print(f"  Step {step['step_id']}: {step['description'][:50]}...")

    return steps


def task_decomposer_node(state: AgentState) -> AgentState:
    """
    Task Decomposer Node.

    This node runs once at the beginning of task execution.
    It parses the task instruction into sub-steps and initializes
    the state for iterative step execution.

    Flow:
    1. Parse instruction into sub-steps
    2. Store global_task_instruction for context
    3. Initialize sub_steps list and current_step_index
    4. Pass control to fast_path for the first sub-step

    Args:
        state: Current agent state

    Returns:
        Updated state with sub_steps initialized
    """
    instruction = state.get("instruction", "")
    task_name = state.get("task_name", "unknown_task")

    print("\n" + "=" * 60)
    print(f"[TASK_DECOMPOSER] Processing task: {task_name}")
    print("=" * 60)

    # Parse task into sub-steps
    sub_steps = parse_task_into_steps(instruction)

    if len(sub_steps) == 1:
        print(f"[TASK_DECOMPOSER] Single-step task detected, no decomposition needed")
    else:
        print(f"[TASK_DECOMPOSER] Multi-step task detected, will execute {len(sub_steps)} steps")

    return {
        # Store the original full instruction as context
        "instruction": instruction,
        # Store parsed sub-steps
        "sub_steps": sub_steps,
        # Initialize current step index (0-based)
        "current_step_index": 0,
        # Set execution status to continue to next node
        "execution_status": "success",
    }
