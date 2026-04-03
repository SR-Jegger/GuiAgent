"""
Learning module for GUI Agent skill acquisition.

This module provides:
- OperationLogger: Record VLM operations for learning
- ClusterEngine: Identify repeated operation patterns
- SkillGenerator: Generate reusable skill rules
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

__all__ = [
    "OperationLogger",
    "ClusterEngine",
    "SkillGenerator",
    "is_same_operation",
    "instruction_similarity",
    "semantic_similarity",
]