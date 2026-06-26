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
    - ID-based matching (关联语义库的 matched_id)
    - Keyword-based intent matching (fallback)
    - Dynamic parameter extraction (match_groups)
    - Parameter substitution in sub_steps ({{param_name}}, {{match_group_1}}, etc.)
    """

    def __init__(self, config_path: str = "data/intent_mappings.json"):
        """
        Initialize the intent mapping config.

        Args:
            config_path: Path to the JSON mapping file
        """
        self.config_path = config_path
        self.mappings: List[Dict] = []
        self.mappings_by_id: Dict[str, Dict] = {}  # ID -> mapping dict
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

            # Build ID index
            for mapping in self.mappings:
                mapping_id = mapping.get("id")
                if mapping_id:
                    self.mappings_by_id[mapping_id] = mapping

            print(f"[INTENT_MAPPING] Loaded {len(self.mappings)} enabled mappings from {self.config_path}")
            print(f"[INTENT_MAPPING] Available IDs: {list(self.mappings_by_id.keys())}")
            return True
        except Exception as e:
            print(f"[INTENT_MAPPING] Error loading config: {e}")
            return False

    def get_mapping_by_id(self, mapping_id: str) -> Optional[Dict]:
        """
        Get mapping by ID (直接关联语义库的 matched_id).

        Args:
            mapping_id: The ID from semantic matcher result

        Returns:
            Mapping dict if found, None otherwise
        """
        return self.mappings_by_id.get(mapping_id)

    def substitute_params(self, sub_steps: List, parameters: Dict) -> List:
        """
        Substitute placeholders in sub_steps with parameters.

        Placeholder format: {{param_name}}

        sub_steps 元素可以是:
        - str: 文本描述(走桌面 fast_path)
        - dict: 浏览器结构化动作 {"action": "browser_*", "selector": "...", ...}
          占位符替换会作用于 dict 中所有字符串字段(selector/url/text/value 等)。

        Args:
            sub_steps: List of step descriptions (str) or browser step dicts
            parameters: Dict of param_name -> param_value

        Returns:
            List of substituted steps (preserving original element type)
        """
        result = []
        for step in sub_steps:
            if isinstance(step, dict):
                # Browser step dict: replace placeholders in every string field.
                substituted = {}
                for key, value in step.items():
                    if isinstance(value, str):
                        for param_name, param_value in parameters.items():
                            value = value.replace(f"{{{{{param_name}}}}}", str(param_value))
                        substituted[key] = value
                    else:
                        substituted[key] = value
                result.append(substituted)
                if substituted != step:
                    print(f"[INTENT_MAPPING] Substituted browser step: {step.get('action', '?')}")
            else:
                substituted = step
                for param_name, param_value in parameters.items():
                    substituted = substituted.replace(f"{{{{{param_name}}}}}", str(param_value))
                result.append(substituted)
                if substituted != step:
                    print(f"[INTENT_MAPPING] Substituted: '{step}' → '{substituted}'")

        return result

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

            # Step 2: Extract match_groups if defined.
            # match_groups 是附加约束 — 任一 pattern 没匹配就视为不命中,
            # 让其他 mapping 有机会。这是与原行为一致的契约:
            # 一旦声明了 match_groups,就要求全部能抽到。
            extracted_groups = []
            if match_group_patterns:
                all_matched = True
                for pattern in match_group_patterns:
                    match = re.search(pattern, instruction)
                    if match:
                        if match.groups():
                            extracted_groups.append(match.group(1))
                        else:
                            extracted_groups.append(match.group(0))
                    else:
                        print(f"[INTENT_MAPPING] Keyword matched but pattern '{pattern}' not found in '{instruction}'")
                        all_matched = False
                        break
                if not all_matched:
                    continue

            # Step 3: Return matched mapping with extracted groups
            print(f"[INTENT_MAPPING] Matched intent: '{mapping.get('intent', 'unknown')}'")
            print(f"[INTENT_MAPPING] Keywords: {keywords}")
            print(f"[INTENT_MAPPING] Extracted groups: {extracted_groups}")

            return mapping, tuple(extracted_groups)

        # No mapping matched
        return None

    def extract_parameters(self, instruction: str, mapping: Dict) -> Dict[str, str]:
        """
        从指令中按 mapping.parameters[].extract_pattern 提取命名参数。

        支持的 extract_pattern 值:
        - "any_number": 抓第一个阿拉伯数字(或汉字数字,自动转换)
        - 正则字符串: 如 "(\\d+)平台" — 取第一个非空捕获组;无捕获组时取整段匹配
        - 多模式(用 | 分隔): 按顺序尝试每个

        抽不到时,若该参数声明了 "default" 字段,则用 default 兜底;
        否则不放入 dict(required 参数会由调用方按 missing 处理)。

        与 semantic_matcher._extract_single_param 行为一致,以便 CLI 入口
        和卡片/语音入口产生相同结果。

        Args:
            instruction: 用户指令文本
            mapping: 单条 mapping dict

        Returns:
            Dict[param_name, param_value]。未提取到且无 default 的参数不放入 dict。
        """
        result: Dict[str, str] = {}
        for param in mapping.get("parameters", []):
            name = param.get("name")
            if not name:
                continue
            value = self._extract_single_param(instruction, param)
            if value is not None:
                result[name] = value
            elif param.get("default") is not None:
                result[name] = param["default"]
                print(f"[INTENT_MAPPING] Param '{name}' not found, using default '{param['default']}'")
            elif param.get("required", False):
                print(f"[INTENT_MAPPING] Required param '{name}' not found in '{instruction}'")
        return result

    def _extract_single_param(self, text: str, param_def: Dict) -> Optional[str]:
        """提取单个参数。逻辑搬自 app.semantic.semantic_matcher._extract_single_param。"""
        pattern = param_def.get("extract_pattern", "")
        if not pattern:
            return None

        if pattern == "any_number":
            # 优先阿拉伯数字,fallback 汉字数字
            match = re.search(r'\d+', text)
            if match:
                return match.group(0)
            chinese_chars = "零一幺二三四五六七八九十百千万"
            match = re.search(f'[{chinese_chars}]+', text)
            if match:
                try:
                    from app.semantic.semantic_matcher import chinese_to_number
                    converted = chinese_to_number(match.group(0))
                    if converted is not None:
                        return str(converted)
                except Exception:
                    return None
            return None

        # 正则模式(可能含 | 多模式)
        for sub_pattern in pattern.split("|"):
            try:
                match = re.search(sub_pattern, text, re.IGNORECASE)
                if match:
                    # 优先取第一个非空捕获组
                    for group in match.groups():
                        if group:
                            # 汉字数字自动转换
                            if all(c in "零一幺二三四五六七八九十百千万" for c in group):
                                try:
                                    from app.semantic.semantic_matcher import chinese_to_number
                                    converted = chinese_to_number(group)
                                    if converted is not None:
                                        return str(converted)
                                except Exception:
                                    pass
                            return group
                    # 无捕获组,返回整段匹配
                    return match.group(0)
            except re.error:
                continue
        return None

    def substitute_steps(self, sub_steps: List, match_groups: Tuple) -> List:
        """
        Substitute placeholders in sub_steps with extracted match_groups.

        Placeholder formats (both supported, can be mixed):
        - {{match_group_1}}, {{match_group_2}}... - GuiAgent 风格(一基索引)
        - {0}, {1}, ... - BrowserAgent 风格(零基索引)

        sub_steps 元素可以是 str(桌面描述)或 dict(浏览器结构化动作)。
        对 dict 元素,占位符替换作用于所有字符串字段。

        Args:
            sub_steps: List of step descriptions (str) or browser step dicts
            match_groups: Tuple of extracted values

        Returns:
            List of substituted steps (preserving original element type)
        """
        result = []
        for step in sub_steps:
            if isinstance(step, dict):
                # Browser step dict: replace placeholders in every string field.
                substituted = {}
                for key, value in step.items():
                    if isinstance(value, str):
                        for i, group_value in enumerate(match_groups, 1):
                            value = value.replace(f"{{{{match_group_{i}}}}}", group_value)
                            value = value.replace(f"{{{i - 1}}}", group_value)
                        substituted[key] = value
                    else:
                        substituted[key] = value
                result.append(substituted)
                print(f"[INTENT_MAPPING] Substituted browser step: {substituted.get('action', '?')}")
            else:
                substituted = step
                for i, group_value in enumerate(match_groups, 1):
                    substituted = substituted.replace(f"{{{{match_group_{i}}}}}", group_value)
                    substituted = substituted.replace(f"{{{i - 1}}}", group_value)
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

    # Substitute {{match_group_N}} / {N} placeholders (from match_groups field)
    substituted_steps = config.substitute_steps(sub_steps_template, match_groups)

    # Substitute {{param_name}} placeholders (from parameters[].extract_pattern).
    # This runs AFTER match_groups substitution so both styles can coexist:
    # e.g. a selector like "button:has-text('{{platform_id}}')" with
    # extract_pattern="any_number" on the platform_id parameter.
    extracted_params = config.extract_parameters(instruction, mapping)
    if extracted_params:
        print(f"[INTENT_MAPPING] Extracted parameters: {extracted_params}")
        substituted_steps = config.substitute_params(substituted_steps, extracted_params)

    # Convert to step dict format.
    # dict elements (browser actions) are wrapped with is_browser=True and the
    # original step dict preserved under "browser_step" for fast_path_node.
    steps = []
    for i, item in enumerate(substituted_steps, 1):
        if isinstance(item, dict):
            action_type = item.get("action", "browser_action")
            steps.append({
                "step_id": i,
                "description": f"[browser] {action_type}",
                "status": "pending",
                "is_browser": True,
                "browser_step": item,
            })
        else:
            steps.append({
                "step_id": i,
                "description": item,
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
    1. 优先检查语义匹配结果 (semantic_matched_id + semantic_parameters)
    2. 如果有 matched_id，直接用 ID 查找意图映射，注入参数生成步骤
    3. 如果没有 matched_id 或映射不存在，fallback 到关键词匹配或文本解析
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
    use_intent_mapping = state.get("use_intent_mapping", False)
    intent_mapping_config_path = state.get("intent_mapping_config_path", "data/intent_mappings.json")

    # Semantic matching result (from voice input)
    semantic_matched_id = state.get("semantic_matched_id")
    semantic_parameters = state.get("semantic_parameters", {})

    print("\n" + "=" * 60)
    print(f"[TASK_DECOMPOSER] Processing task: {task_name}")
    print(f"[TASK_DECOMPOSER] Intent mapping mode: {use_intent_mapping}")
    print(f"[TASK_DECOMPOSER] Semantic matched_id: {semantic_matched_id}")
    print(f"[TASK_DECOMPOSER] Semantic parameters: {semantic_parameters}")
    print("=" * 60)

    # Parse task into sub-steps
    sub_steps = []

    # === 优先路径：语义匹配结果直接关联意图映射 ===
    if semantic_matched_id:
        config = get_intent_mapping_config(intent_mapping_config_path)
        mapping = config.get_mapping_by_id(semantic_matched_id)

        if mapping:
            sub_steps_template = mapping.get("sub_steps", [])

            # 参数来源优先级:
            # 1. semantic_parameters(语音/卡片输入已抽出)
            # 2. 从 instruction 用 mapping.parameters[].extract_pattern 现抽
            #    (CLI 直接传 semantic_matched_id 但没传 semantic_parameters 时)
            effective_params = dict(semantic_parameters or {})
            if not effective_params and mapping.get("parameters"):
                extracted = config.extract_parameters(instruction, mapping)
                if extracted:
                    print(f"[TASK_DECOMPOSER] Extracted parameters from instruction: {extracted}")
                    effective_params = extracted

            # 用语义匹配提取的参数注入步骤模板
            sub_steps_desc = config.substitute_params(sub_steps_template, effective_params)

            # sub_steps 元素可以是 str(桌面描述)或 dict(浏览器动作)。
            # dict 元素包装为 is_browser=True 的 sub_step,供 fast_path_node 识别。
            for i, item in enumerate(sub_steps_desc, 1):
                if isinstance(item, dict):
                    action_type = item.get("action", "browser_action")
                    sub_steps.append({
                        "step_id": i,
                        "description": f"[browser] {action_type}",
                        "status": "pending",
                        "is_browser": True,
                        "browser_step": item,
                    })
                else:
                    sub_steps.append({
                        "step_id": i,
                        "description": item,
                        "status": "pending"
                    })

            print(f"[TASK_DECOMPOSER] Semantic ID matched, generated {len(sub_steps)} steps from intent mapping")
            print(f"[TASK_DECOMPOSER] Mapping ID: {semantic_matched_id}, Description: {mapping.get('description', '')}")
        else:
            print(f"[TASK_DECOMPOSER] Semantic matched_id '{semantic_matched_id}' not found in intent mappings")

    # === Fallback 1: 传统关键词匹配 ===
    if not sub_steps and use_intent_mapping:
        mapped_steps = match_intent_to_steps(instruction, intent_mapping_config_path)

        if mapped_steps is not None:
            sub_steps = mapped_steps
            print(f"[TASK_DECOMPOSER] Keyword-based intent mapping matched, using mapped sub-steps")
        else:
            print(f"[TASK_DECOMPOSER] No intent mapping matched, falling back to text parsing")

    # === Fallback 2: 文本解析 ===
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