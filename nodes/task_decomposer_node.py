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

    def __init__(self, config_path: str = "data/mappings"):
        """
        Initialize the intent mapping config.

        Args:
            config_path: Path to the JSON mapping file or directory
        """
        self.config_path = config_path
        self.mappings: List[Dict] = []
        self.mappings_by_id: Dict[str, Dict] = {}  # ID -> mapping dict
        self._load_config()

    def _load_config(self) -> bool:
        """Load mappings from JSON file or directory."""
        if not os.path.exists(self.config_path):
            print(f"[INTENT_MAPPING] Config path not found: {self.config_path}")
            return False

        try:
            items: List[tuple] = []  # [(mapping_dict, source_file), ...]
            file_count = 0

            if os.path.isdir(self.config_path):
                json_files = sorted(
                    f for f in os.listdir(self.config_path) if f.endswith(".json")
                )
                for fname in json_files:
                    fpath = os.path.join(self.config_path, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception as e:
                        print(f"[INTENT_MAPPING] Failed to load {fname}: {e}")
                        continue
                    file_count += 1
                    for m in data.get("mappings", []):
                        items.append((m, fname))
                if not items:
                    print(f"[INTENT_MAPPING] No usable .json in dir: {self.config_path}")
                    return False
            else:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                file_count = 1
                source_file = os.path.basename(self.config_path)
                for m in data.get("mappings", []):
                    items.append((m, source_file))

            # Filter enabled + dedup by id (first-wins + WARNING, 低数字文件优先)
            self.mappings = []
            self.mappings_by_id = {}
            duplicates = 0
            for mapping, source in items:
                if not mapping.get("enabled", True):
                    continue
                mapping_id = mapping.get("id")
                if not mapping_id:
                    continue
                if mapping_id in self.mappings_by_id:
                    prev_source = self.mappings_by_id[mapping_id].get("_source_file", "?")
                    print(
                        f"[INTENT_MAPPING] WARNING: Duplicate id {mapping_id!r} in {source} ignored (already loaded from {prev_source})"
                    )
                    duplicates += 1
                    continue
                mapping["_source_file"] = source
                self.mappings.append(mapping)
                self.mappings_by_id[mapping_id] = mapping

            print(
                f"[INTENT_MAPPING] Loaded {len(self.mappings_by_id)} enabled mappings "
                f"from {file_count} file(s) ({duplicates} duplicates overridden)"
            )
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
        return [self._substitute_step(step, parameters) for step in sub_steps]

    def _substitute_step(self, step, parameters: Dict):
        if isinstance(step, dict):
            substituted = {}
            for key, value in step.items():
                if isinstance(value, str):
                    for param_name, param_value in parameters.items():
                        value = value.replace(f"{{{{{param_name}}}}}", str(param_value))
                    substituted[key] = value
                elif isinstance(value, list):
                    substituted[key] = [self._substitute_step(s, parameters) for s in value]
                elif isinstance(value, dict):
                    substituted[key] = self._substitute_step(value, parameters)
                else:
                    substituted[key] = value
            return substituted
        elif isinstance(step, str):
            substituted = step
            for param_name, param_value in parameters.items():
                substituted = substituted.replace(f"{{{{{param_name}}}}}", str(param_value))
            return substituted
        else:
            return step

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
        - "any_number": 抓第一个阿拉伯数字（汉字数字由 normalize_chinese_numerals 在入口预处理）
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
        """提取单个参数。逻辑搬自 app.semantic.semantic_matcher._extract_single_param。

        前置条件：text 已由 normalize_chinese_numerals 预处理，汉字数字已转阿拉伯。
        """
        pattern = param_def.get("extract_pattern", "")
        if not pattern:
            return None

        if pattern == "any_number":
            match = re.search(r'\d+', text)
            return match.group(0) if match else None

        # 正则模式(可能含 | 多模式)
        for sub_pattern in pattern.split("|"):
            try:
                match = re.search(sub_pattern, text, re.IGNORECASE)
                if match:
                    # 优先取第一个非空捕获组
                    for group in match.groups():
                        if group:
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
        return [self._substitute_step_groups(step, match_groups) for step in sub_steps]

    def _substitute_step_groups(self, step, match_groups: Tuple):
        if isinstance(step, dict):
            substituted = {}
            for key, value in step.items():
                if isinstance(value, str):
                    for i, group_value in enumerate(match_groups, 1):
                        value = value.replace(f"{{{{match_group_{i}}}}}", group_value)
                        value = value.replace(f"{{{i - 1}}}", group_value)
                    substituted[key] = value
                elif isinstance(value, list):
                    substituted[key] = [self._substitute_step_groups(s, match_groups) for s in value]
                elif isinstance(value, dict):
                    substituted[key] = self._substitute_step_groups(value, match_groups)
                else:
                    substituted[key] = value
            return substituted
        elif isinstance(step, str):
            substituted = step
            for i, group_value in enumerate(match_groups, 1):
                substituted = substituted.replace(f"{{{{match_group_{i}}}}}", group_value)
                substituted = substituted.replace(f"{{{i - 1}}}", group_value)
            return substituted
        else:
            return step


# Global config instance (lazy loaded)
intent_mapping_config: Optional[IntentMappingConfig] = None


def get_intent_mapping_config(config_path: str = "data/mappings") -> IntentMappingConfig:
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

def match_intent_to_steps(instruction: str, config_path: str = "data/mappings") -> Optional[List[Dict]]:
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
# Kill Chain Dispatcher Rewrite
# ============================================================================

def _rewrite_kill_chain_dispatch(params: Dict, instruction: str) -> List | None:
    """confirm_kill_chain_by_target 命中时，查杀伤链缓存拿阶段，返回对应阶段 sub_steps。

    返回值是 raw sub_steps list（dict/str 混合），交给现有 wrapping loop 包装。
    缓存未就绪/未找到链/未找到平台/阶段不可解析时返回单条 NL sub_step 兜底。
    """
    from utils.kill_chain_cache import get_kill_chain_cache, KillChainCache, find_chain_by_number

    target_id = params.get("target_id")
    if not target_id:
        return ["未识别到目标标识，请明确目标"]

    cache = get_kill_chain_cache()
    if not cache.chains:
        return ["缓存未就绪，请稍后重试"]

    # 优先按 platform_id 精确匹配（用户说了具体平台号），否则按优先级自动选
    result = cache.resolve_platform(instruction)
    if result is not None:
        chain, platform = result
    else:
        chain = cache.resolve_first(target_id)
        if chain is None:
            # target_id 抽取可能漏了真实汉字前缀（如"打击目标0082"抽成"目标0082"），
            # 用 instruction 里的数字兜底找链
            chain = find_chain_by_number(instruction)
        platform = chain.pick_platform_by_priority() if chain else None
        if platform is None:
            return ["未找到对应平台，请指定平台编号"]

    stage = platform.stage or KillChainCache.parse_stage(platform.status_img_src)
    if not stage:
        return ["未识别到当前阶段，请确认后重试"]

    strike_mode = _resolve_strike_mode(instruction, platform.platform_id) if stage == "target" else "远火打击"
    if stage == "target":
        print(f"[TASK_DECOMPOSER] target 阶段打击方式: {strike_mode!r} (platform_id={platform.platform_id!r}, instruction={instruction!r})")
    return _build_stage_steps(stage, chain.target_id, platform.platform_id, strike_mode)


def _resolve_strike_mode(instruction: str, platform_id: str) -> str:
    """target 阶段打击方式选择：指令优先，平台次之。

    Returns: "远火打击" | "金地打击" | "KVD打击"

    决策顺序：
    1. 指令明说（原文匹配"金地打击"/"KVD打击"/"远火"）-> 听指令，
       即使与平台默认规则冲突也听指令（指挥官意图优先，平台兼容性由人把关）。
    2. 平台前缀默认：20x 系列 -> 金地打击；10x 系列 -> 远火打击。
    3. 兜底：远火打击。
    """
    upper = (instruction or "").upper()
    if "KVD打击" in (instruction or "") or "KVD" in upper:
        return "KVD打击"
    if "金地打击" in (instruction or ""):
        return "金地打击"
    if "远火" in (instruction or ""):
        return "远火打击"
    pid = (platform_id or "").strip()
    if pid.startswith("2"):  # 20x 系列
        return "金地打击"
    if pid.startswith("1"):  # 10x 系列
        return "远火打击"
    return "远火打击"


def _build_stage_steps(stage: str, target_id: str, platform_id: str, strike_mode: str = "远火打击") -> List:
    """按阶段生成 sub_steps，selector 用 cache 的真实 DOM 结构动态拼。

    DOM 结构（来源 srj_data/intent_mappings.json 的 platform_*_confirm）：
      div.kill_chain_card_grops.target_outer:has-text("target_id")  <- chain_root
        ├─ div.target_info_container
        │    └─ div:has-text("platform_id")          <- 目标卡片
        │         └─ div.wrap_each_mini_item
        │              └─ div:has-text("platform_id")
        │                   ├─ div.wrap_num_img > div.wrap_img
        │                   ├─ div.wrap_num_img
        │                   └─ div.wrap_checkbox > img
        ├─ div.wrap_right_click > div.next_stage.wrap_common > div  <- 直接子元素
        └─ div.sure_box > div.wrap_buttons > div.yes                <- 直接子元素
    """
    chain_root_sel = f'div.kill_chain_card_grops.target_outer:has-text("{target_id}")'
    # stage -> img src 关键词（与 kill_chain_cache.parse_stage 对应）。
    # 同链下两个平台 platform_id 相同/子串重叠时，has-text(platform_id) 命中 DOM 第一个，
    # 可能不是目标 stage。用 stage 对应的 img src 关键词二次限定平台卡片，确保命中正确阶段。
    stage_img_kw = {
        "fix": "ding_wei", "track": "gen_zong", "target": "miao_zhun",
        "engage": "jiao_zhan", "assess": "hui_ping",
    }.get(stage, "")
    stage_filter = f':has(img[src*="{stage_img_kw}"])' if stage_img_kw else ""
    plat_card = (
        f'{chain_root_sel} > div.target_info_container > '
        f'div:has-text("{platform_id}")'
    )
    plat_item = (
        f'{plat_card} > div.wrap_each_mini_item > div:has-text("{platform_id}"){stage_filter}'
    )
    icon = f'{plat_item} > div.wrap_num_img > div.wrap_img'
    num_img = f'{plat_item} > div.wrap_num_img'
    checkbox = f'{plat_item} > div.wrap_checkbox > img'
    next_stage = f'{chain_root_sel} > div.wrap_right_click > div.next_stage.wrap_common > div'
    sure_yes = f'{chain_root_sel} > div.sure_box > div.wrap_buttons > div.yes'
    close = '#wrap_center > div.top > div.close > i'

    if stage == "fix":
        return [
            {"action": "browser_click", "selector": icon},
            {"action": "browser_wait_time", "ms": 3000},
            "进行定位确认",
            {"action": "browser_click", "selector": close},
        ]
    if stage == "track":
        return [
            {"action": "browser_click", "selector": num_img, "button": "right"},
            {"action": "browser_click", "selector": next_stage},
            {"action": "browser_click", "selector": sure_yes},
        ]
    if stage == "target":
        return [
            {"action": "browser_click", "selector": icon},
            {"action": "browser_click", "selector": "#wrap_bottom > div.wrap_position_access > div.end_select > div > div"},
            {"action": "browser_click", "selector": f"[role=option]:has-text(\"{strike_mode}\")"},
            "进行定位确认",
            {"action": "browser_click", "selector": close},
        ]
    if stage == "engage":
        return [
            {"action": "browser_click", "selector": checkbox},
            {"action": "browser_click", "selector": num_img, "button": "right"},
            {"action": "browser_click", "selector": next_stage},
            {"action": "browser_click", "selector": sure_yes},
        ]
    if stage == "assess":
        return [
            {"action": "browser_click", "selector": icon},
            "进行毁伤评估确认",
            {"action": "browser_click", "selector": close},
        ]
    return ["未识别到当前阶段，请确认后重试"]


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
    intent_mapping_config_path = state.get("intent_mapping_config_path", "data/mappings")

    # Semantic matching result (from voice input)
    semantic_matched_id = state.get("semantic_matched_id")
    semantic_parameters = state.get("semantic_parameters", {})

    # @path 逐行匹配结果（每项: matched_id/is_matched/parameters/instruction/original_text）
    semantic_matches = state.get("semantic_matches")

    print("\n" + "=" * 60)
    print(f"[TASK_DECOMPOSER] Processing task: {task_name}")
    print(f"[TASK_DECOMPOSER] Intent mapping mode: {use_intent_mapping}")
    print(f"[TASK_DECOMPOSER] Semantic matched_id: {semantic_matched_id}")
    print(f"[TASK_DECOMPOSER] Semantic parameters: {semantic_parameters}")
    print(f"[TASK_DECOMPOSER] Semantic matches (per-line): {len(semantic_matches) if semantic_matches else 0}")
    print("=" * 60)

    # Parse task into sub-steps
    sub_steps = []

    # === 优先路径 1：单值语义匹配（直接输入/语音） ===
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

            # dispatcher rewrite: confirm_kill_chain_by_target 命中时，
            # 查杀伤链缓存拿阶段，直接生成对应阶段 sub_steps（覆盖占位模板）
            if semantic_matched_id == "confirm_kill_chain_by_target":
                rewritten = _rewrite_kill_chain_dispatch(effective_params, instruction)
                if rewritten is not None:
                    sub_steps_desc = rewritten

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

    # === 优先路径 2：@path 逐行语义匹配 ===
    # 每行独立匹配：命中则展开为该 mapping 的 sub_steps（一条 md 行可能展开成多步），
    # 未命中则作为文本步。与直接输入走同一个 HybridMatcher，能力等价。
    if not sub_steps and semantic_matches:
        config = get_intent_mapping_config(intent_mapping_config_path)
        step_id_counter = 0
        for match in semantic_matches:
            original_text = match.get("original_text", "")
            matched_id = match.get("matched_id")
            is_matched = match.get("is_matched", False)
            parameters = match.get("parameters", {}) or {}

            if is_matched and matched_id:
                mapping = config.get_mapping_by_id(matched_id)
                if mapping:
                    sub_steps_template = mapping.get("sub_steps", [])
                    # 命中行的参数已在 service 层抽好，直接注入
                    sub_steps_desc = config.substitute_params(sub_steps_template, parameters)
                    for item in sub_steps_desc:
                        step_id_counter += 1
                        if isinstance(item, dict):
                            action_type = item.get("action", "browser_action")
                            sub_steps.append({
                                "step_id": step_id_counter,
                                "description": f"[browser] {action_type}",
                                "status": "pending",
                                "is_browser": True,
                                "browser_step": item,
                            })
                        else:
                            sub_steps.append({
                                "step_id": step_id_counter,
                                "description": item,
                                "status": "pending",
                            })
                    print(f"[TASK_DECOMPOSER] 行 '{original_text}' → matched_id={matched_id}, "
                          f"展开 {len(sub_steps_desc)} 步")
                    continue
                else:
                    print(f"[TASK_DECOMPOSER] 行 '{original_text}' matched_id={matched_id} "
                          f"在 intent_mappings 中找不到，作为文本步")

            # 未命中或 mapping 不存在：作为文本步
            step_id_counter += 1
            sub_steps.append({
                "step_id": step_id_counter,
                "description": original_text,
                "status": "pending",
            })

        print(f"[TASK_DECOMPOSER] @path 逐行匹配生成 {len(sub_steps)} 步")

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