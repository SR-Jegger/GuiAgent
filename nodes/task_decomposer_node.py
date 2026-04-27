"""
Task Decomposer Node for GUI automation agent.

Responsible for:
1. Intent mapping: Match business intent to predefined operation steps
2. Text parsing: Fallback to text-based decomposition when no mapping matches
3. Initializing sub-step state for iterative execution
4. Passing global task instruction as context to each sub-step
"""

import json
import os
import re
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from nodes.types import AgentState


# ============================================================================
# Intent Mapping Configuration
# ============================================================================

class IntentMappingConfig:
    """
    Load and manage intent-to-steps mappings from JSON config.

    Features:
    - Keyword-based intent matching
    - Dynamic parameter extraction (match_groups)
    - Parameter substitution in sub_steps ({{match_group_1}}, etc.)
    """

    def __init__(self, config_path: str = "data/intent_mappings.json"):
        """
        Initialize the intent mapping config.

        Args:
            config_path: Path to the JSON mapping file
        """
        self.config_path = config_path
        self.mappings: List[Dict] = []
        self._load_config()

    def _load_config(self) -> bool:
        """Load mappings from JSON file."""
        if not os.path.exists(self.config_path):
            print(f"[INTENT_MAPPING] Config file not found: {self.config_path}")
            return False

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.mappings = data.get("mappings", [])
            # Filter enabled mappings
            self.mappings = [m for m in self.mappings if m.get("enabled", True)]

            print(f"[INTENT_MAPPING] Loaded {len(self.mappings)} enabled mappings from {self.config_path}")
            return True
        except Exception as e:
            print(f"[INTENT_MAPPING] Error loading config: {e}")
            return False

    def match(self, instruction: str) -> Optional[Tuple[Dict, Tuple]]:
        """
        Match instruction against mappings using keyword + match_groups.

        Matching logic:
        1. Check if instruction contains ALL keywords (order-independent)
        2. If match_groups defined, extract them using regex
        3. Return (mapping_dict, extracted_groups) if matched

        Args:
            instruction: User instruction text

        Returns:
            Tuple of (mapping_dict, match_groups) if matched, None otherwise
        """
        for mapping in self.mappings:
            keywords = mapping.get("keywords", [])
            match_group_patterns = mapping.get("match_groups", [])

            # Step 1: Check keywords match (ALL keywords must be present)
            keywords_matched = all(kw in instruction for kw in keywords)

            if not keywords_matched:
                continue

            # Step 2: Extract match_groups if defined
            extracted_groups = []
            if match_group_patterns:
                for pattern in match_group_patterns:
                    match = re.search(pattern, instruction)
                    if match:
                        # Use captured group if available, otherwise use full match
                        if match.groups():
                            extracted_groups.append(match.group(1))
                        else:
                            extracted_groups.append(match.group(0))
                    else:
                        # Pattern not matched, skip this mapping
                        print(f"[INTENT_MAPPING] Keyword matched but pattern '{pattern}' not found in '{instruction}'")
                        break

                # If not all patterns matched, skip this mapping
                if len(extracted_groups) != len(match_group_patterns):
                    continue

            # Step 3: Return matched mapping with extracted groups
            print(f"[INTENT_MAPPING] Matched intent: '{mapping.get('intent', 'unknown')}'")
            print(f"[INTENT_MAPPING] Keywords: {keywords}")
            print(f"[INTENT_MAPPING] Extracted groups: {extracted_groups}")

            return mapping, tuple(extracted_groups)

        # No mapping matched
        return None

    def substitute_steps(self, sub_steps: List[str], match_groups: Tuple) -> List[str]:
        """
        Substitute placeholders in sub_steps with extracted match_groups.

        Placeholder format: {{match_group_1}}, {{match_group_2}}, etc.

        Args:
            sub_steps: List of step descriptions with placeholders
            match_groups: Tuple of extracted values

        Returns:
            List of substituted step descriptions
        """
        result = []
        for step in sub_steps:
            substituted = step
            for i, group_value in enumerate(match_groups, 1):
                substituted = substituted.replace(f"{{{{match_group_{i}}}}}", group_value)
            result.append(substituted)
            print(f"[INTENT_MAPPING] Substituted: '{step}' → '{substituted}'")

        return result


# Global config instance (lazy loaded)
intent_mapping_config: Optional[IntentMappingConfig] = None


def get_intent_mapping_config(config_path: str = "data/intent_mappings.json") -> IntentMappingConfig:
    """
    Get or create the global intent mapping config instance.

    Args:
        config_path: Path to the JSON mapping file

    Returns:
        IntentMappingConfig instance
    """
    global intent_mapping_config
    if intent_mapping_config is None:
        intent_mapping_config = IntentMappingConfig(config_path)
    return intent_mapping_config


# ============================================================================
# Intent Mapping Match Function
# ============================================================================

def match_intent_to_steps(instruction: str, config_path: str = "data/intent_mappings.json") -> Optional[List[Dict]]:
    """
    Match user instruction to predefined operation steps.

    This is the main entry point for intent mapping.

    Args:
        instruction: User instruction text
        config_path: Path to the JSON mapping file

    Returns:
        List of sub-step dicts if matched, None otherwise
        [
            {"step_id": 1, "description": "...", "status": "pending"},
            {"step_id": 2, "description": "...", "status": "pending"},
            ...
        ]
    """
    config = get_intent_mapping_config(config_path)
    match_result = config.match(instruction)

    if match_result is None:
        return None

    mapping, match_groups = match_result

    # Get sub_steps template
    sub_steps_template = mapping.get("sub_steps", [])

    # Substitute placeholders with extracted groups
    substituted_steps = config.substitute_steps(sub_steps_template, match_groups)

    # Convert to step dict format
    steps = []
    for i, desc in enumerate(substituted_steps, 1):
        steps.append({
            "step_id": i,
            "description": desc,
            "status": "pending"
        })

    print(f"[INTENT_MAPPING] Generated {len(steps)} sub-steps from mapping")

    return steps


# ============================================================================
# Original Text Parsing (Fallback)
# ============================================================================

def parse_task_into_steps(instruction: str) -> list[dict]:
    """
    Parse a complex task instruction into structured sub-steps (fallback method).

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

    print(f"\n[TASK_DECOMPOSER] Parsed {len(steps)} sub-step(s) (text fallback):")
    for step in steps:
        print(f"  Step {step['step_id']}: {step['description'][:50]}...")

    return steps


# ============================================================================
# Main Node Function
# ============================================================================

def task_decomposer_node(state: AgentState) -> AgentState:
    """
    Task Decomposer Node.

    This node runs once at the beginning of task execution.
    It parses the task instruction into sub-steps and initializes
    the state for iterative step execution.

    Flow:
    1. Check use_intent_mapping flag to determine decomposition mode
    2. If enabled: Try intent mapping first, fallback to text parsing
    3. If disabled: Use text parsing directly
    4. Store global_task_instruction for context
    5. Initialize sub_steps list and current_step_index
    6. Pass control to fast_path for the first sub-step

    Args:
        state: Current agent state

    Returns:
        Updated state with sub_steps initialized
    """
    instruction = state.get("instruction", "")
    task_name = state.get("task_name", "unknown_task")

    # Control parameter: use intent mapping or text parsing
    use_intent_mapping = state.get("use_intent_mapping", False)  # Default: enabled
    intent_mapping_config_path = state.get("intent_mapping_config_path", "data/intent_mappings.json")

    print("\n" + "=" * 60)
    print(f"[TASK_DECOMPOSER] Processing task: {task_name}")
    print(f"[TASK_DECOMPOSER] Intent mapping mode: {use_intent_mapping}")
    print("=" * 60)

    # Parse task into sub-steps
    sub_steps = []

    if use_intent_mapping:
        # Try intent mapping first
        mapped_steps = match_intent_to_steps(instruction, intent_mapping_config_path)

        if mapped_steps is not None:
            sub_steps = mapped_steps
            print(f"[TASK_DECOMPOSER] Intent mapping matched, using mapped sub-steps")
        else:
            print(f"[TASK_DECOMPOSER] No intent mapping matched, falling back to text parsing")

    # Fallback to text parsing if:
    # 1. use_intent_mapping is False
    # 2. use_intent_mapping is True but no mapping matched
    if not sub_steps:
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