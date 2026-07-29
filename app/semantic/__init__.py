"""语义匹配模块

提供语音转写文本的语义匹配功能，直接从统一配置文件加载。
"""

from app.semantic.semantic_matcher import (
    SemanticMatcher,
    RuleBasedMatcher,
    HybridMatcher,
    MatchResult,
    load_intent_mappings,
    chinese_to_number,
    normalize_chinese_numerals,
    normalize_strike_mode,
)

__all__ = [
    "SemanticMatcher",
    "RuleBasedMatcher",
    "HybridMatcher",
    "MatchResult",
    "load_intent_mappings",
    "chinese_to_number",
    "normalize_chinese_numerals",
    "normalize_strike_mode",
]