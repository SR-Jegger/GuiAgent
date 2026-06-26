"""
Learning module for GUI Agent skill acquisition.

This module provides:
- OperationLogger: Record VLM operations for learning
- ClusterEngine: Identify repeated operation patterns (rule-based)
- LLMClusterEngine: Identify repeated operation patterns (LLM semantic)
- SkillGenerator: Generate reusable skill rules
- SkillStore: SQLite-based storage for learned skills
- LLMReviewer: Automatic skill candidate review using LLM
- Similarity utilities: Compare operations for clustering
- IconMatcher: Image-based skill matching with resolution awareness (Phase 2)
"""

from learning.operation_logger import OperationLogger
from learning.cluster_engine import ClusterEngine
from learning.skill_generator import SkillGenerator
from learning.skill_store import SkillStore
from learning.similarity import (
    is_same_operation,
    instruction_similarity,
    semantic_similarity,
)

# Icon matching (Phase 2 A+B)
from learning.icon_matcher import (
    IconMatcher,
    IconData,
    get_screen_resolution,
    get_screen_dpi,
    resolve_coordinate_with_icon,
    apply_anchor,
    ANCHOR_MAP,
)

# LLM-enhanced components
from learning.llm_client import LLMClient, create_llm_client
from learning.llm_cluster_engine import LLMClusterEngine
from learning.llm_pattern_extractor import extract_pattern_with_llm
from learning.llm_reviewer import LLMReviewer, create_reviewer

__all__ = [
    # Original components
    "OperationLogger",
    "ClusterEngine",
    "SkillGenerator",
    "SkillStore",
    "is_same_operation",
    "instruction_similarity",
    "semantic_similarity",
    # Icon matching (Phase 2)
    "IconMatcher",
    "IconData",
    "get_screen_resolution",
    "get_screen_dpi",
    "resolve_coordinate_with_icon",
    "apply_anchor",
    "ANCHOR_MAP",
    # LLM-enhanced components
    "LLMClient",
    "create_llm_client",
    "LLMClusterEngine",
    "extract_pattern_with_llm",
    "LLMReviewer",
    "create_reviewer",
]