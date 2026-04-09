"""
Learning module for GUI Agent skill acquisition.

This module provides:
- OperationLogger: Record VLM operations for learning
- ClusterEngine: Identify repeated operation patterns (rule-based)
- LLMClusterEngine: Identify repeated operation patterns (LLM semantic)
- SkillGenerator: Generate reusable skill rules
- LLMReviewer: Automatic skill candidate review using LLM
- Similarity utilities: Compare operations for clustering
"""

from learning.operation_logger import OperationLogger
from learning.cluster_engine import ClusterEngine
from learning.skill_generator import SkillGenerator
from learning.similarity import (
    is_same_operation,
    instruction_similarity,
    semantic_similarity,
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
    "is_same_operation",
    "instruction_similarity",
    "semantic_similarity",
    # LLM-enhanced components
    "LLMClient",
    "create_llm_client",
    "LLMClusterEngine",
    "extract_pattern_with_llm",
    "LLMReviewer",
    "create_reviewer",
]