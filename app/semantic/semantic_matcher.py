"""统一语义匹配器

将语音转写的文本通过 LLM 或规则与意图映射匹配，直接生成执行步骤。
支持参数化指令：从语音文本中提取参数并注入到步骤模板。
"""

import json
import re
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# ============================================================================
# 汉字数字转换
# ============================================================================

CHINESE_NUM_MAP = {
    "零": 0, "一": 1, "幺": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000, "万": 10000,
}


def chinese_to_number(chinese_num: str) -> Optional[int]:
    """
    将汉字数字转换为阿拉伯数字。

    支持格式：
    - 单个数字: "一" -> 1, "二" -> 2
    - 组合数字: "二零六" -> 206, "一百零一" -> 101
    - 特殊格式: "二十" -> 20, "三百零五" -> 305

    Args:
        chinese_num: 汉字数字字符串

    Returns:
        阿拉伯数字，转换失败返回 None
    """
    if not chinese_num:
        return None

    # 检查是否全是汉字数字
    valid_chars = set(CHINESE_NUM_MAP.keys())
    if not all(c in valid_chars for c in chinese_num):
        return None

    # 特殊情况：单个数字
    if len(chinese_num) == 1:
        return CHINESE_NUM_MAP.get(chinese_num)

    # 判断类型：是否有单位字符
    has_unit = any(c in ["十", "百", "千", "万"] for c in chinese_num)

    if not has_unit:
        # 纯数字组合（如"二零六"）- 逐位拼接
        result = 0
        for c in chinese_num:
            result = result * 10 + CHINESE_NUM_MAP[c]
        return result

    # 复合数字解析（如"一百零一", "二十三", "三百零五"）
    result = 0
    temp = 0

    for i, char in enumerate(chinese_num):
        val = CHINESE_NUM_MAP[char]

        if char in ["万", "千", "百", "十"]:
            if temp == 0:
                temp = 1
            temp *= val
            result += temp
            temp = 0
        else:
            temp = val

    if temp > 0:
        result += temp

    return result


def extract_number_from_text(text: str, patterns: str) -> Optional[str]:
    """
    从文本中提取数字（支持阿拉伯数字和汉字数字）。

    Args:
        text: 输入文本
        patterns: 正则表达式模式（多个用 | 分隔）

    Returns:
        提取的数字字符串（阿拉伯数字格式）
    """
    pattern_list = patterns.split("|")

    for pattern in pattern_list:
        try:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                for group in match.groups():
                    if group:
                        if all(c in CHINESE_NUM_MAP.keys() for c in group):
                            converted = chinese_to_number(group)
                            if converted is not None:
                                return str(converted)
                        return group
        except re.error:
            continue

    return None


# ============================================================================
# 统一配置加载
# ============================================================================

def load_intent_mappings(config_path: str = "data/intent_mappings.json") -> Dict[str, Dict]:
    """
    加载统一的意图映射配置（包含语义信息和执行步骤）。

    Args:
        config_path: 配置文件路径

    Returns:
        Dict[id, mapping] - 以 id 为键的映射字典
    """
    if not os.path.exists(config_path):
        print(f"[SemanticMatcher] 配置文件不存在: {config_path}")
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mappings = data.get("mappings", [])
        # 只加载启用的映射
        enabled_mappings = {m["id"]: m for m in mappings if m.get("enabled", True)}

        print(f"[SemanticMatcher] 加载了 {len(enabled_mappings)} 个启用的意图映射")
        return enabled_mappings

    except Exception as e:
        print(f"[SemanticMatcher] 加载配置失败: {e}")
        return {}


def get_mappings_text_for_llm(mappings: Dict[str, Dict]) -> str:
    """
    生成用于 LLM Prompt 的映射条目文本。

    Args:
        mappings: 映射字典

    Returns:
        格式化的文本
    """
    lines = []
    for id, mapping in mappings.items():
        aliases_str = ", ".join(mapping.get("aliases", []))
        desc = mapping.get("description", "")
        lines.append(
            f"- [{id}] {desc}\n"
            f"  别名: {aliases_str}"
        )
    return "\n".join(lines)


# ============================================================================
# 匹配结果数据结构
# ============================================================================

@dataclass
class MatchResult:
    """语义匹配结果"""
    matched_id: Optional[str]      # 匹配的条目ID
    confidence: float              # 置信度 0-1
    corrected_intent: str          # 修正后的意图
    instruction: str               # 最终执行指令（已填充参数）
    original_text: str             # 原始转写文本
    is_matched: bool               # 是否成功匹配
    parameters: Dict[str, Any] = field(default_factory=dict)  # 提取的参数
    missing_params: list = field(default_factory=list)  # 缺失的必填参数
    sub_steps: List[str] = field(default_factory=list)  # 执行步骤（已填充参数）


# ============================================================================
# LLM 语义匹配器
# ============================================================================

SEMANTIC_MATCH_PROMPT = """你是一个语义匹配助手。用户输入的是语音转写文本，可能存在识别错误（如同音字、表述不完整等）。

请从以下意图映射中找到最匹配的一项，并返回匹配结果。

【意图映射】
{mappings_text}

【用户输入】
{user_text}

请分析用户的真实意图，判断最匹配哪个意图映射。

输出要求：
1. 如果有匹配项，返回 matched_id（条目ID）、confidence（置信度）、corrected_intent（修正意图）
2. 如果没有匹配项，matched_id 设为 "none"，confidence 设为 0
3. confidence 高于 0.7 表示高置信度匹配，0.4-0.7 表示部分匹配，低于 0.4 表示无匹配

请直接输出 JSON 格式结果（不要有其他内容）：
"""


class SemanticMatcher:
    """基于 LLM 的语义匹配器"""

    def __init__(self, llm_client=None, model_config: dict = None, config_path: str = "data/intent_mappings.json"):
        """
        Args:
            llm_client: LLM 客户端（兼容 LangChain 或自定义）
            model_config: 模型配置
            config_path: 意图映射配置文件路径
        """
        self.llm_client = llm_client
        self.model_config = model_config or {}
        self.config_path = config_path
        self._mappings = load_intent_mappings(config_path)
        self._mappings_text = get_mappings_text_for_llm(self._mappings)

    async def match(self, user_text: str) -> MatchResult:
        """
        匹配用户文本到意图映射

        Args:
            user_text: ASR 转写文本

        Returns:
            MatchResult: 匹配结果
        """
        prompt = SEMANTIC_MATCH_PROMPT.format(
            mappings_text=self._mappings_text,
            user_text=user_text
        )

        try:
            response = await self._call_llm(prompt)
            result = self._parse_response(response, user_text)
            return result
        except Exception as e:
            print(f"[SemanticMatcher] LLM调用失败: {e}")
            return MatchResult(
                matched_id=None,
                confidence=0.0,
                corrected_intent=user_text,
                instruction=user_text,
                original_text=user_text,
                is_matched=False,
            )

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        if self.llm_client:
            return await self.llm_client(prompt)

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=self.model_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            api_key=self.model_config.get("api_key", ""),
        )

        response = await client.chat.completions.create(
            model=self.model_config.get("model", "qwen-plus"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        return response.choices[0].message.content

    def _parse_response(self, response: str, original_text: str) -> MatchResult:
        """解析 LLM 返回的 JSON"""
        try:
            json_str = response.strip()

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                start = json_str.find("{")
                end = json_str.rfind("}") + 1
                if start >= 0 and end > start:
                    json_str = json_str[start:end]
                    data = json.loads(json_str)
                else:
                    raise

            matched_id = data.get("matched_id", "none")
            confidence = float(data.get("confidence", 0.0))
            corrected_intent = data.get("corrected_intent", original_text)

            if matched_id and matched_id != "none":
                mapping = self._mappings.get(matched_id)
                if mapping:
                    # 提取参数
                    parameters = self._extract_parameters(original_text, mapping)
                    missing_params = self._get_missing_params(parameters, mapping)

                    # 填充步骤模板
                    sub_steps = self._fill_steps(mapping.get("sub_steps", []), parameters, missing_params)
                    instruction = self._get_instruction(mapping, parameters, missing_params)

                    return MatchResult(
                        matched_id=matched_id,
                        confidence=confidence,
                        corrected_intent=corrected_intent,
                        instruction=instruction,
                        original_text=original_text,
                        is_matched=True,
                        parameters=parameters,
                        missing_params=missing_params,
                        sub_steps=sub_steps,
                    )

            return MatchResult(
                matched_id=None,
                confidence=confidence,
                corrected_intent=corrected_intent,
                instruction=original_text,
                original_text=original_text,
                is_matched=False,
            )

        except Exception as e:
            print(f"[SemanticMatcher] 解析失败: {e}, response={response}")
            return MatchResult(
                matched_id=None,
                confidence=0.0,
                corrected_intent=original_text,
                instruction=original_text,
                original_text=original_text,
                is_matched=False,
            )

    def _extract_parameters(self, text: str, mapping: Dict) -> Dict[str, str]:
        """从文本中提取参数"""
        parameters = {}
        for param in mapping.get("parameters", []):
            param_name = param["name"]
            param_value = self._extract_single_param(text, param)
            if param_value:
                parameters[param_name] = param_value
        return parameters

    def _extract_single_param(self, text: str, param_def: Dict) -> Optional[str]:
        """提取单个参数"""
        pattern = param_def.get("extract_pattern", "")
        if not pattern:
            return None

        if pattern == "any_number":
            return self._extract_any_number(text)

        return extract_number_from_text(text, pattern)

    def _extract_any_number(self, text: str) -> Optional[str]:
        """从文本中提取第一个数字"""
        # 优先提取阿拉伯数字
        match = re.search(r'\d+', text)
        if match:
            return match.group(0)

        # 提取汉字数字
        chinese_chars = "零一幺二三四五六七八九十百千万"
        match = re.search(f'[{chinese_chars}]+', text)
        if match:
            converted = chinese_to_number(match.group(0))
            if converted is not None:
                return str(converted)

        return None

    def _get_missing_params(self, parameters: Dict, mapping: Dict) -> List[str]:
        """获取缺失的必填参数"""
        missing = []
        for param in mapping.get("parameters", []):
            if param.get("required", False) and param["name"] not in parameters:
                missing.append(param["name"])
        return missing

    def _fill_steps(self, steps: List[str], parameters: Dict, missing_params: List[str]) -> List[str]:
        """填充步骤模板中的参数"""
        result = []
        for step in steps:
            substituted = step
            for name, value in parameters.items():
                substituted = substituted.replace(f"{{{{{name}}}}}", str(value))
            for name in missing_params:
                substituted = substituted.replace(f"{{{{{name}}}}}", f"[缺失{name}]")
            result.append(substituted)
        return result

    def _get_instruction(self, mapping: Dict, parameters: Dict, missing_params: List[str]) -> str:
        """生成指令描述"""
        desc = mapping.get("description", "")
        if parameters:
            params_str = ", ".join(f"{k}={v}" for k, v in parameters.items())
            return f"{desc} ({params_str})"
        return desc


# ============================================================================
# 规则匹配器（快速、无LLM调用）
# ============================================================================

class RuleBasedMatcher:
    """基于规则的语义匹配器（快速、无LLM调用）

    支持：
    - 关键词/别名匹配
    - 参数提取（正则表达式）
    - 参数注入到步骤模板
    """

    def __init__(self, config_path: str = "data/intent_mappings.json"):
        """
        Args:
            config_path: 意图映射配置文件路径
        """
        self.config_path = config_path
        self._mappings = load_intent_mappings(config_path)

    def match(self, user_text: str) -> MatchResult:
        """基于关键词和别名的快速匹配，并提取参数"""
        user_text_lower = user_text.lower()

        best_match = None
        best_score = 0.0

        for id, mapping in self._mappings.items():
            score = 0.0

            # 检查别名精确匹配
            for alias in mapping.get("aliases", []):
                if alias.lower() in user_text_lower:
                    score += 0.8

            # 检查关键词匹配
            matched_keywords = 0
            for keyword in mapping.get("keywords", []):
                if keyword.lower() in user_text_lower:
                    matched_keywords += 1

            if matched_keywords > 0:
                keyword_score = min(0.6, matched_keywords * 0.2)
                score += keyword_score

            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_match = mapping

        # 无匹配
        if not best_match or best_score < 0.4:
            return MatchResult(
                matched_id=None,
                confidence=0.0,
                corrected_intent=user_text,
                instruction=user_text,
                original_text=user_text,
                is_matched=False,
            )

        # 有匹配：提取参数
        parameters = {}
        missing_params = []

        for param in best_match.get("parameters", []):
            param_name = param["name"]
            param_value = self._extract_parameter(user_text, param)

            if param_value:
                parameters[param_name] = param_value
            elif param.get("required", False):
                missing_params.append(param_name)

        # 填充步骤模板
        sub_steps = self._fill_steps(
            best_match.get("sub_steps", []),
            parameters,
            missing_params
        )

        # 生成指令描述
        instruction = self._get_instruction(best_match, parameters, missing_params)

        # 如果有缺失的必填参数，降低置信度
        final_confidence = best_score
        if missing_params:
            final_confidence *= 0.5

        return MatchResult(
            matched_id=best_match["id"],
            confidence=final_confidence,
            corrected_intent=best_match.get("description", ""),
            instruction=instruction,
            original_text=user_text,
            is_matched=final_confidence >= 0.4,
            parameters=parameters,
            missing_params=missing_params,
            sub_steps=sub_steps,
        )

    def _extract_parameter(self, text: str, param_def: dict) -> Optional[str]:
        """从文本中提取参数值"""
        pattern = param_def.get("extract_pattern", "")
        if not pattern:
            return None

        if pattern == "any_number":
            return self._extract_any_number(text)

        return extract_number_from_text(text, pattern)

    def _extract_any_number(self, text: str) -> Optional[str]:
        """从文本中提取第一个数字"""
        match = re.search(r'\d+', text)
        if match:
            return match.group(0)

        chinese_chars = "零一幺二三四五六七八九十百千万"
        match = re.search(f'[{chinese_chars}]+', text)
        if match:
            converted = chinese_to_number(match.group(0))
            if converted is not None:
                return str(converted)

        return None

    def _fill_steps(self, steps: List[str], parameters: Dict, missing_params: list) -> List[str]:
        """填充步骤模板"""
        result = []
        for step in steps:
            substituted = step
            for name, value in parameters.items():
                substituted = substituted.replace(f"{{{{{name}}}}}", str(value))
            for name in missing_params:
                substituted = substituted.replace(f"{{{{{name}}}}}", f"[缺失{name}]")
            result.append(substituted)
        return result

    def _get_instruction(self, mapping: Dict, parameters: Dict, missing_params: List[str]) -> str:
        """生成指令描述"""
        desc = mapping.get("description", "")
        if parameters:
            params_str = ", ".join(f"{k}={v}" for k, v in parameters.items())
            return f"{desc} ({params_str})"
        elif missing_params:
            return f"{desc} [缺失{missing_params[0]}]"
        return desc


# ============================================================================
# 混合匹配器（规则 + LLM 结合）
# ============================================================================

class HybridMatcher:
    """混合匹配器：结合规则匹配和 LLM 匹配

    策略：
    1. 先用规则匹配器快速匹配（RuleBasedMatcher）
    2. 如果规则匹配置信度 >= 0.8，直接返回（高置信度）
    3. 如果规则匹配置信度 < 0.8，调用 LLM 匹配器（SemanticMatcher）
    4. LLM 结果必须通过规则验证：置信度 > 0.4 才算有效
    5. 最终返回两者中置信度更高的结果

    优点：
    - 高置信度匹配快速返回（无 LLM 调用延迟）
    - LLM 匹配有规则验证兜底，避免误判
    - 兼顾速度和准确性
    """

    # 规则匹配置信度阈值（>= 此值直接返回，不调用 LLM）
    RULE_HIGH_CONFIDENCE_THRESHOLD = 0.8

    # 规则验证阈值（LLM 结果必须通过此验证才有效）
    RULE_VALIDATION_THRESHOLD = 0.4

    def __init__(
        self,
        llm_client=None,
        model_config: dict = None,
        config_path: str = "data/intent_mappings.json"
    ):
        """
        Args:
            llm_client: LLM 客户端（用于 SemanticMatcher）
            model_config: 模型配置
            config_path: 意图映射配置文件路径
        """
        self.rule_matcher = RuleBasedMatcher(config_path)
        self.llm_matcher = SemanticMatcher(llm_client, model_config, config_path)

    async def match(self, user_text: str) -> MatchResult:
        """
        混合匹配策略

        Args:
            user_text: ASR 转写文本

        Returns:
            MatchResult: 最佳匹配结果
        """
        # 1. 先用规则匹配器快速匹配
        rule_result = self.rule_matcher.match(user_text)

        print(f"[HybridMatcher] 规则匹配置信度: {rule_result.confidence:.2f}")

        # 2. 规则匹配置信度 >= 0.8，直接返回（高置信度，无需 LLM）
        if rule_result.confidence >= self.RULE_HIGH_CONFIDENCE_THRESHOLD:
            print(f"[HybridMatcher] 规则高置信度匹配，直接返回: {rule_result.matched_id}")
            return rule_result

        # 3. 规则匹配置信度低，调用 LLM 匹配器
        print(f"[HybridMatcher] 规则置信度低，调用 LLM 匹配...")
        llm_result = await self.llm_matcher.match(user_text)

        print(f"[HybridMatcher] LLM 匹配结果: {llm_result.matched_id}, 置信度: {llm_result.confidence:.2f}")

        # 4. LLM 未匹配，返回规则结果
        if not llm_result.is_matched:
            print(f"[HybridMatcher] LLM 未匹配，返回规则结果")
            return rule_result

        # 5. LLM 匹配成功，需要规则验证
        # 用规则匹配器验证 LLM 的 matched_id
        validation_result = self._validate_llm_result(llm_result.matched_id, user_text)

        print(f"[HybridMatcher] 规则验证置信度: {validation_result.confidence:.2f}")

        # 6. 规则验证置信度 > 0.4，LLM 结果有效
        if validation_result.confidence > self.RULE_VALIDATION_THRESHOLD:
            print(f"[HybridMatcher] LLM 结果通过规则验证，返回 LLM 结果")
            return llm_result

        # 7. 规则验证失败，返回规则匹配结果（或无匹配）
        if rule_result.is_matched:
            print(f"[HybridMatcher] LLM 验证失败，返回规则匹配结果")
            return rule_result
        else:
            print(f"[HybridMatcher] LLM 验证失败，规则未匹配，返回无匹配")
            return MatchResult(
                matched_id=None,
                confidence=0.0,
                corrected_intent=user_text,
                instruction=user_text,
                original_text=user_text,
                is_matched=False,
            )

    def _validate_llm_result(self, matched_id: str, user_text: str) -> MatchResult:
        """
        用规则匹配器验证 LLM 的匹配结果

        检查 LLM 返回的 matched_id 是否与规则匹配器对该 ID 的置信度一致

        Args:
            matched_id: LLM 返回的匹配 ID
            user_text: 原始用户文本

        Returns:
            规则匹配器对该 ID 的验证结果
        """
        # 找到 LLM 匹配的 mapping
        mapping = self.rule_matcher._mappings.get(matched_id)

        if not mapping:
            # LLM 匹配的 ID 不存在，验证失败
            return MatchResult(
                matched_id=None,
                confidence=0.0,
                corrected_intent=user_text,
                instruction=user_text,
                original_text=user_text,
                is_matched=False,
            )

        # 计算规则匹配置信度（只针对该 mapping）
        user_text_lower = user_text.lower()
        score = 0.0

        # 检查别名匹配
        for alias in mapping.get("aliases", []):
            if alias.lower() in user_text_lower:
                score += 0.8

        # 检查关键词匹配
        matched_keywords = 0
        for keyword in mapping.get("keywords", []):
            if keyword.lower() in user_text_lower:
                matched_keywords += 1

        if matched_keywords > 0:
            keyword_score = min(0.6, matched_keywords * 0.2)
            score += keyword_score

        # 返回验证结果
        return MatchResult(
            matched_id=matched_id if score > 0 else None,
            confidence=score,
            corrected_intent=mapping.get("description", ""),
            instruction=mapping.get("description", ""),
            original_text=user_text,
            is_matched=score > 0,
        )