"""
Similarity calculation utilities for operation clustering.

Provides semantic similarity functions to compare instructions and
identify similar operations for skill learning.
"""

import hashlib
from datetime import datetime
from typing import Optional, List, Dict


def compute_instruction_hash(instruction: str) -> str:
    """
    Compute a hash for an instruction string.

    Args:
        instruction: The instruction text

    Returns:
        MD5 hash (first 8 characters)
    """
    return hashlib.md5(instruction.encode()).hexdigest()[:8]


def jaccard_similarity(set1: set, set2: set) -> float:
    """
    Compute Jaccard similarity between two sets.

    Args:
        set1: First set
        set2: Second set

    Returns:
        Similarity score between 0 and 1
    """
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return intersection / union if union > 0 else 0.0


def tokenize_instruction(instruction: str) -> set:
    """
    Tokenize an instruction string into words.

    Args:
        instruction: The instruction text

    Returns:
        Set of tokens (lowercase words)
    """
    # Simple tokenization: split by whitespace and punctuation
    import re

    # Remove punctuation and convert to lowercase
    cleaned = re.sub(r"[^\w\s]", " ", instruction.lower())
    tokens = set(cleaned.split())

    # Remove very short tokens
    tokens = {t for t in tokens if len(t) > 1}

    return tokens


def instruction_similarity(instr1: str, instr2: str) -> float:
    """
    Compute similarity between two instructions.

    Uses Jaccard similarity on token sets.

    Args:
        instr1: First instruction
        instr2: Second instruction

    Returns:
        Similarity score between 0 and 1
    """
    tokens1 = tokenize_instruction(instr1)
    tokens2 = tokenize_instruction(instr2)

    return jaccard_similarity(tokens1, tokens2)


def action_structure_similarity(structure1: list, structure2: list) -> float:
    """
    Compute similarity between two action structures.

    Args:
        structure1: First action structure (list of action types)
        structure2: Second action structure

    Returns:
        Similarity score between 0 and 1
    """
    if not structure1 and not structure2:
        return 1.0
    if not structure1 or not structure2:
        return 0.0

    # Exact match gives highest score
    if structure1 == structure2:
        return 1.0

    # Jaccard on action types
    set1 = set(structure1)
    set2 = set(structure2)

    return jaccard_similarity(set1, set2)


def is_same_operation(
    op1: dict,
    op2: dict,
    instruction_threshold: float = 0.6,
    structure_threshold: float = 0.8,
) -> bool:
    """
    Determine if two operations are the "same" for clustering purposes.

    Two operations are considered the same if:
    1. They have similar instructions (semantic similarity)
    2. They have similar action structures
    3. They operate in the same application context

    Args:
        op1: First operation dict
        op2: Second operation dict
        instruction_threshold: Minimum instruction similarity
        structure_threshold: Minimum action structure similarity

    Returns:
        True if operations are considered the same
    """
    # Check app context match
    app1 = op1.get("app_context", {})
    app2 = op2.get("app_context", {})

    # If both have app context, check if they match
    if app1 and app2:
        # Check window title or process name
        title1 = app1.get("window_title", "") or app1.get("active_window", "")
        title2 = app2.get("window_title", "") or app2.get("active_window", "")

        # Simple check: if both have titles, they should share significant words
        if title1 and title2:
            title_sim = instruction_similarity(title1, title2)
            if title_sim < 0.3:  # Different apps
                return False

    # Check instruction similarity
    instr1 = op1.get("instruction", "")
    instr2 = op2.get("instruction", "")

    if instr1 and instr2:
        instr_sim = instruction_similarity(instr1, instr2)
        if instr_sim < instruction_threshold:
            return False

    # Check action structure similarity
    struct1 = op1.get("action_structure", [])
    struct2 = op2.get("action_structure", [])

    if struct1 and struct2:
        struct_sim = action_structure_similarity(struct1, struct2)
        if struct_sim < structure_threshold:
            return False

    return True


def extract_pattern_from_instructions(instructions: list[str]) -> str:
    """
    Extract a regex pattern from a list of similar instructions.

    Strategy: Find longest common prefix and identify variable suffix.
    This preserves meaningful context like "任务名称一栏输入" instead of
    over-generalizing to ".*输入.*".

    Examples:
        ["任务名称一栏输入侦察任务", "任务名称一栏输入测试内容"]
        → "任务名称一栏输入.*"

        ["点击确认按钮", "点击取消按钮"]
        → "点击.*按钮"

    Args:
        instructions: List of similar instructions

    Returns:
        Regex pattern string
    """
    import re

    if not instructions:
        return ".*"

    # Remove duplicates
    unique_instructions = list(set(instructions))

    if len(unique_instructions) == 1:
        return _escape_for_regex(unique_instructions[0])

    # Find longest common prefix
    prefix = unique_instructions[0]
    for instr in unique_instructions[1:]:
        while not instr.startswith(prefix) and prefix:
            prefix = prefix[:-1]

    # Find longest common suffix (only if meaningful)
    suffix = unique_instructions[0]
    for instr in unique_instructions[1:]:
        while not instr.endswith(suffix) and suffix:
            suffix = suffix[1:]

    # Decide pattern strategy based on prefix/suffix relationship
    # Add capture groups for variable extraction
    if prefix and suffix:
        # Check for overlap
        if prefix == suffix:
            return _escape_for_regex(prefix)

        # Check if they overlap
        combined_len = len(prefix) + len(suffix)
        min_instr_len = min(len(instr) for instr in unique_instructions)

        if combined_len > min_instr_len:
            # Overlap - prefer the longer one
            if len(prefix) >= len(suffix):
                return f"{_escape_for_regex(prefix)}(.*)"
            else:
                return f"(.*){_escape_for_regex(suffix)}"

        # Check if prefix ends with meaningful content
        # Prefer prefix-based pattern if prefix is substantial
        if len(prefix) >= 4:
            return f"{_escape_for_regex(prefix)}(.*)"

        # Otherwise use both prefix and suffix
        return f"{_escape_for_regex(prefix)}(.*){_escape_for_regex(suffix)}"

    elif prefix:
        return f"{_escape_for_regex(prefix)}(.*)"
    elif suffix:
        return f"(.*){_escape_for_regex(suffix)}"
    else:
        return "(.*)"


def _escape_for_regex(text: str) -> str:
    """
    Escape text for use in regex pattern.

    Unlike re.escape(), this does NOT escape spaces or common punctuation
    that are safe in typical Chinese instructions.

    Only escapes characters that have special meaning in regex:
    . * + ? ^ $ { } ( ) | [ ] \

    Args:
        text: Text to escape

    Returns:
        Escaped text safe for regex
    """
    # Characters that need escaping in regex
    special_chars = r'.*+?^${}()|[]\\'

    result = []
    for char in text:
        if char in special_chars:
            result.append('\\' + char)
        else:
            result.append(char)

    return ''.join(result)


def extract_pattern_with_prefix_heuristic(instructions: list[str]) -> dict:
    """
    Extract pattern with additional metadata for better skill generation.

    This function returns more information about the pattern, including
    identified variable slots.

    Args:
        instructions: List of similar instructions

    Returns:
        Dict with:
        - regex_pattern: The regex pattern
        - prefix: Common prefix
        - suffix: Common suffix (if meaningful)
        - variable_examples: Examples of the variable part
    """
    import re

    if not instructions:
        return {"regex_pattern": ".*", "prefix": "", "suffix": "", "variable_examples": []}

    unique_instructions = list(set(instructions))

    if len(unique_instructions) == 1:
        return {
            "regex_pattern": _escape_for_regex(unique_instructions[0]),
            "prefix": unique_instructions[0],
            "suffix": "",
            "variable_examples": []
        }

    # Find prefix
    prefix = unique_instructions[0]
    for instr in unique_instructions[1:]:
        while not instr.startswith(prefix) and prefix:
            prefix = prefix[:-1]

    # Find suffix
    suffix = unique_instructions[0]
    for instr in unique_instructions[1:]:
        while not instr.endswith(suffix) and suffix:
            suffix = suffix[1:]

    # Extract variable parts
    variable_examples = []
    for instr in unique_instructions:
        if prefix and instr.startswith(prefix):
            remaining = instr[len(prefix):]
            if suffix and remaining.endswith(suffix):
                remaining = remaining[:-len(suffix)]
            if remaining:
                variable_examples.append(remaining)

    # Build pattern with capture group for variable part
    if prefix and suffix and not (len(prefix) + len(suffix) > len(min(unique_instructions, key=len))):
        # prefix + variable + suffix
        pattern = f"{_escape_for_regex(prefix)}(.*){_escape_for_regex(suffix)}"
    elif prefix:
        # prefix + variable
        pattern = f"{_escape_for_regex(prefix)}(.*)"
    elif suffix:
        # variable + suffix
        pattern = f"(.*){_escape_for_regex(suffix)}"
    else:
        pattern = "(.*)"

    return {
        "regex_pattern": pattern,
        "prefix": prefix,
        "suffix": suffix if suffix != prefix else "",
        "variable_examples": variable_examples[:5]  # Keep up to 5 examples
    }


# Optional: Semantic similarity using embeddings
_embedding_model = None


def get_embedding_model():
    """
    Get or initialize the sentence embedding model.

    Returns:
        Sentence transformer model or None if not available
    """
    global _embedding_model

    if _embedding_model is not None:
        return _embedding_model

    try:
        from sentence_transformers import SentenceTransformer

        # Use a small, fast model
        _embedding_model = SentenceTransformer("paraphrase-MiniLM-L3-v2")
        return _embedding_model
    except ImportError:
        print("[Similarity] sentence-transformers not installed, "
              "using token-based similarity")
        return None
    except Exception as e:
        print(f"[Similarity] Could not load embedding model: {e}")
        return None


def semantic_similarity(text1: str, text2: str) -> float:
    """
    Compute semantic similarity using sentence embeddings.

    Falls back to token-based similarity if embeddings not available.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity score between 0 and 1
    """
    model = get_embedding_model()

    if model is None:
        return instruction_similarity(text1, text2)

    try:
        import numpy as np

        embeddings = model.encode([text1, text2])
        similarity = np.dot(embeddings[0], embeddings[1]) / (
            np.linalg.norm(embeddings[0]) * np.linalg.norm(embeddings[1])
        )
        return float(similarity)
    except Exception as e:
        print(f"[Similarity] Embedding error: {e}, falling back to token-based")
        return instruction_similarity(text1, text2)


def is_sequence_operation(logs: List[Dict]) -> bool:
    """
    Determine if logs represent a SEQUENCE operation or SINGLE operation.

    Sequence operation: One execution produces multiple logs with consecutive step_ids
        Example: "输入网址进入系统" → logs with step_id 4,5,6 (click, type, key)

    Single operation: Each execution produces one log, may be executed multiple times
        Example: "双击打开Edge" → 3 logs, each with step_id=1 (just double_click)

    Args:
        logs: List of logs with same instruction_hash + task_name

    Returns:
        True if this is a sequence operation, False if single operation
    """
    if not logs:
        return False

    # Sort by timestamp
    sorted_logs = sorted(logs, key=lambda x: x.get("timestamp", ""))

    # Check 1: If only 1 log, definitely single operation
    if len(sorted_logs) == 1:
        return False

    # Check 2: Check if there are consecutive step_ids within short time gaps
    MAX_SEQUENCE_GAP_SECONDS = 30

    for i in range(len(sorted_logs) - 1):
        log1 = sorted_logs[i]
        log2 = sorted_logs[i + 1]

        step1 = log1.get("step_id", 0)
        step2 = log2.get("step_id", 0)

        # Calculate time gap
        try:
            time1 = datetime.fromisoformat(log1.get("timestamp", ""))
            time2 = datetime.fromisoformat(log2.get("timestamp", ""))
            time_gap = abs((time2 - time1).total_seconds())
        except:
            time_gap = 999

        # If step_id is consecutive AND time gap is small → part of same sequence
        if step2 == step1 + 1 and time_gap < MAX_SEQUENCE_GAP_SECONDS:
            return True

    # Check 3: Check if step_ids span multiple values
    step_ids = [log.get("step_id", 0) for log in sorted_logs]
    unique_steps = sorted(set(step_ids))

    if len(unique_steps) == 1:
        return False

    min_step = min(unique_steps)
    max_step = max(unique_steps)

    if max_step - min_step >= 2:
        step_range = set(range(min_step, max_step + 1))
        coverage = len(unique_steps) / len(step_range) if step_range else 0
        if coverage >= 0.5:
            return True

    return False