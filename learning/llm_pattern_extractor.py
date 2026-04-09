"""
LLM Pattern Extractor - Extract trigger patterns from instructions using LLM.

This module uses LLM to:
- Identify common intent across similar instructions
- Extract invariant trigger keywords
- Identify variable slots (parameters)
- Generate robust regex patterns

Usage:
    from learning.llm_pattern_extractor import extract_pattern_with_llm

    instructions = [
        "打开 Chrome 浏览器",
        "启动 Chrome",
        "帮我开一下 Chrome 浏览器"
    ]
    pattern = extract_pattern_with_llm(instructions, llm_client)
"""

import json
from typing import List, Dict, Any, Optional
from learning.llm_client import LLMClient


# ============================================================================
# Prompts
# ============================================================================

PATTERN_EXTRACTION_PROMPT = """
你是一个 GUI 自动化技能学习系统的模式提取专家。请从以下相似操作样本中提取通用的触发模式。

## 输入样本
以下指令都被判定为语义相似的操作：
{sample_instructions}

## 任务
请分析这些指令，完成以下任务：

1. **识别共同意图**：这些指令的核心意图是什么？
2. **提取触发前缀**：找出指令开头的固定部分（如"任务名称一栏输入"、"打击/侦察目标一栏输入"）
3. **识别可变参数**：前缀之后的内容是可变参数
4. **生成正则表达式**：生成一个简洁的正则表达式

## 重要规则
- **优先宽松模式**：正则表达式应该尽量宽松，只保留关键触发前缀
- **只捕获一个可变部分**：使用 `(.*)` 捕获所有可变内容，不要过度细分
- **格式要求**：`前缀(.*)` 或 `前缀(.*?)后缀`（如有固定后缀）
- **示例**：
  - 输入: ["任务名称一栏输入打击任务01", "任务名称一栏输入侦察任务02"]
  - 正确输出: `任务名称一栏输入(.*)`
  - 错误输出: `任务名称一栏输入.*任务.*\\d+` （太严格）

## 输出格式（JSON）
```json
{{
    "intent": "指令的核心意图描述",
    "trigger_keywords": ["触发前缀中的关键词"],
    "variable_slots": [
        {{"name": "content", "type": "string", "examples": ["示例值1", "示例值2"]}}
    ],
    "regex_pattern": "触发前缀(.*)",
    "confidence": 0.85,
    "explanation": "简要说明模式提取的逻辑"
}}
```

## 注意事项
- 触发前缀是识别这个操作的关键，必须保留
- 可变部分全部用 `(.*)` 捕获，不要尝试匹配具体格式
- 置信度反映你对这个模式泛化能力的信心（0-1）
"""

PATTERN_EXTRACTION_PROMPT_EN = """
You are a pattern extraction expert for a GUI automation skill learning system.

## Input Samples
The following instructions are semantically similar:
{sample_instructions}

## Task
Analyze these instructions and:

1. **Identify common intent**: What is the core intent?
2. **Extract trigger prefix**: Find the fixed prefix at the start (e.g., "Open browser", "Enter URL")
3. **Identify variable parts**: The content after the prefix is the variable parameter
4. **Generate regex pattern**: Create a concise regex pattern

## Important Rules
- **Prefer loose patterns**: The regex should be as loose as possible, keeping only the key trigger prefix
- **Single capture group**: Use `(.*)` to capture all variable content, don't over-specify
- **Format**: `prefix(.*)` or `prefix(.*?)suffix` (if fixed suffix exists)
- **Example**:
  - Input: ["Enter task name Task01", "Enter task name Task02"]
  - Correct: `Enter task name(.*)`
  - Wrong: `Enter task name.*Task.*\\d+` (too strict)

## Output Format (JSON)
```json
{{
    "intent": "Core intent description",
    "trigger_keywords": ["keywords", "in", "prefix"],
    "variable_slots": [
        {{"name": "content", "type": "string", "examples": ["value1", "value2"]}}
    ],
    "regex_pattern": "trigger prefix(.*)",
    "confidence": 0.85,
    "explanation": "Brief explanation of the pattern logic"
}}
```

## Notes
- The trigger prefix is the key identifier for this operation
- Capture all variable parts with `(.*)`, don't try to match specific formats
- Confidence reflects your trust in this pattern's generalization ability (0-1)
"""


# ============================================================================
# Pattern Extraction Functions
# ============================================================================

def extract_pattern_with_llm(
    instructions: List[str],
    llm_client: LLMClient,
    language: Optional[str] = None,
    max_tokens: int = 1000,
    temperature: float = 0.1,
) -> Dict[str, Any]:
    """
    Extract a trigger pattern from similar instructions using LLM.

    Args:
        instructions: List of similar instructions
        llm_client: LLM client for pattern extraction
        language: Language hint ("zh" or "en", auto-detected if None)
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature

    Returns:
        Pattern extraction result dict with:
        - intent: Core intent description
        - trigger_keywords: List of invariant keywords
        - variable_slots: List of variable parameter slots
        - regex_pattern: Generated regex pattern
        - confidence: Confidence score (0-1)
        - explanation: Explanation of the pattern logic
    """
    if not instructions:
        return {
            "intent": "",
            "trigger_keywords": [],
            "variable_slots": [],
            "regex_pattern": ".*",
            "confidence": 0.0,
            "explanation": "No instructions provided"
        }

    # Auto-detect language
    if language is None:
        # Simple heuristic: check if any Chinese characters
        has_chinese = any('\u4e00' <= c <= '\u9fff' for instr in instructions for c in instr)
        language = "zh" if has_chinese else "en"

    # Select prompt based on language
    prompt_template = PATTERN_EXTRACTION_PROMPT if language == "zh" else PATTERN_EXTRACTION_PROMPT_EN

    # Format instructions
    formatted_instructions = "\n".join(f"- {instr}" for instr in instructions)
    prompt = prompt_template.format(sample_instructions=formatted_instructions)

    # Call LLM
    messages = [{"role": "user", "content": prompt}]

    try:
        result = llm_client.chat_json(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Validate result
        if "regex_pattern" not in result:
            print(f"[LLMPatternExtractor] Missing regex_pattern in response: {result}")
            result["regex_pattern"] = ".*"

        if "confidence" not in result:
            result["confidence"] = 0.5

        print(f"[LLMPatternExtractor] Extracted pattern: {result.get('regex_pattern')} "
              f"(confidence: {result.get('confidence')})")

        return result

    except Exception as e:
        print(f"[LLMPatternExtractor] Pattern extraction failed: {e}")
        # Fallback to simple pattern
        from learning.similarity import extract_pattern_from_instructions
        fallback_pattern = extract_pattern_from_instructions(instructions)
        return {
            "intent": "Unknown (extraction failed)",
            "trigger_keywords": [],
            "variable_slots": [],
            "regex_pattern": fallback_pattern,
            "confidence": 0.3,
            "explanation": f"Fallback pattern due to extraction error: {e}"
        }


def refine_pattern_with_llm(
    pattern: str,
    sample_instructions: List[str],
    llm_client: LLMClient,
) -> Dict[str, Any]:
    """
    Refine an existing pattern based on feedback or additional samples.

    Args:
        pattern: Existing regex pattern
        sample_instructions: Sample instructions to validate against
        llm_client: LLM client

    Returns:
        Refined pattern result
    """
    prompt = f"""
你是一个正则表达式优化专家。请优化以下触发模式，使其能更好地匹配给定的指令样本。

## 当前模式
```
{pattern}
```

## 指令样本
{chr(10).join(f"- {instr}" for instr in sample_instructions)}

## 任务
1. 分析当前模式是否能匹配所有样本
2. 如果不能，指出问题所在
3. 生成一个优化后的模式

## 输出格式（JSON）
```json
{{
    "analysis": "当前模式的分析",
    "issues": ["问题列表"],
    "optimized_pattern": "优化后的正则表达式",
    "confidence": 0.9,
    "explanation": "优化说明"
}}
```
"""

    messages = [{"role": "user", "content": prompt}]

    try:
        result = llm_client.chat_json(messages=messages)
        return result
    except Exception as e:
        print(f"[LLMPatternExtractor] Pattern refinement failed: {e}")
        return {
            "analysis": "Refinement failed",
            "issues": [str(e)],
            "optimized_pattern": pattern,
            "confidence": 0.5,
            "explanation": "Kept original pattern due to error"
        }


def validate_pattern_with_llm(
    pattern: str,
    positive_samples: List[str],
    negative_samples: Optional[List[str]] = None,
    llm_client: Optional[LLMClient] = None,
) -> Dict[str, Any]:
    """
    Validate a regex pattern against positive and negative samples.

    Args:
        pattern: Regex pattern to validate
        positive_samples: Instructions that should match
        negative_samples: Instructions that should NOT match
        llm_client: LLM client (optional, uses simple matching if not provided)

    Returns:
        Validation result with:
        - matches_positive: Number of positive samples matched
        - matches_negative: Number of negative samples matched (should be 0)
        - valid: True if pattern is valid and doesn't match negatives
        - issues: List of issues found
    """
    import re

    try:
        compiled_pattern = re.compile(pattern)
    except re.error as e:
        return {
            "valid": False,
            "issues": [f"Invalid regex: {e}"],
            "matches_positive": 0,
            "matches_negative": 0,
        }

    # Test against positive samples
    positive_matches = sum(1 for s in positive_samples if compiled_pattern.search(s))

    # Test against negative samples
    negative_matches = 0
    false_positives = []
    if negative_samples:
        for s in negative_samples:
            if compiled_pattern.search(s):
                negative_matches += 1
                false_positives.append(s)

    result = {
        "valid": negative_matches == 0 and positive_matches > 0,
        "issues": [],
        "matches_positive": positive_matches,
        "matches_negative": negative_matches,
        "positive_coverage": positive_matches / len(positive_samples) if positive_samples else 0,
    }

    if positive_matches == 0:
        result["issues"].append("Pattern does not match any positive samples")

    if negative_matches > 0:
        result["issues"].append(
            f"Pattern matches {negative_matches} negative samples: {false_positives[:3]}"
        )

    return result
