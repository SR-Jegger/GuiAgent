"""
Skill Learning Service Layer for GUI Agent

This module provides high-level business logic for skill learning,
decoupled from FastAPI routing concerns.

Services:
- SkillLearningService: Skill clustering, review, approval, and management
"""

import os
import json
from typing import Optional, Dict, Any, List
from pathlib import Path


class SkillLearningService:
    """
    Application service for LLM skill learning.

    Provides high-level business logic for:
    - Candidate skill discovery and clustering
    - LLM-based skill review
    - Skill approval/rejection workflow
    - Skill storage management

    Usage:
        service = SkillLearningService()
        await service.initialize()
        candidates = await service.list_candidate_skills()
        result = await service.review_candidate(cluster_id)
    """

    def __init__(self):
        # Lazy-initialized components
        self._cluster_engine = None
        self._llm_cluster_engine = None
        self._llm_reviewer = None
        self._llm_client = None

        # Component availability flags
        self._learning_available = False
        self._llm_available = False

    async def initialize(self):
        """
        Initialize service components.
        Call this once at application startup.
        """
        # Try to import learning modules
        try:
            from learning import ClusterEngine, OperationLogger
            from learning import LLMClusterEngine, LLMReviewer, create_llm_client

            self._learning_available = True
            self._llm_available = True

            # Initialize rule-based cluster engine
            self._cluster_engine = ClusterEngine()

            # Initialize LLM client and engines
            self._llm_client = create_llm_client()
            self._llm_cluster_engine = LLMClusterEngine(llm_client=self._llm_client)
            self._llm_reviewer = LLMReviewer(llm_client=self._llm_client)

            print("[SkillLearningService] Initialized with LLM support")

        except ImportError as e:
            self._learning_available = False
            self._llm_available = False
            print(f"[SkillLearningService] Learning modules not available: {e}")

    def _check_learning_available(self) -> bool:
        """Check if learning modules are available"""
        if not self._learning_available:
            print("[SkillLearningService] Learning modules not available")
        return self._learning_available

    def _check_llm_available(self) -> bool:
        """Check if LLM components are available"""
        if not self._llm_available:
            print("[SkillLearningService] LLM components not available")
        return self._llm_available

    # =========================================================================
    # Candidate Skills Management
    # =========================================================================

    async def list_candidate_skills(self) -> Dict:
        """
        List all candidate skills awaiting approval.

        Returns:
            Dict with total count and candidates list
        """
        if not self._check_learning_available():
            return {"total": 0, "candidates": []}

        candidates = self._cluster_engine.get_candidates()
        return {
            "total": len(candidates),
            "candidates": [
                {
                    "cluster_id": c["cluster_id"],
                    "pattern": c["pattern"],
                    "count": c["count"],
                    "sample_instructions": c.get("sample_instructions", []),
                    "created_at": c.get("created_at"),
                }
                for c in candidates
            ],
        }

    async def get_candidate_skill(self, cluster_id: str) -> Optional[Dict]:
        """
        Get details of a specific candidate skill.

        Args:
            cluster_id: The candidate cluster ID

        Returns:
            Cluster details or None if not found
        """
        if not self._check_learning_available():
            return None

        cluster = self._cluster_engine.get_cluster(cluster_id)
        return cluster

    async def approve_candidate_skill(
        self,
        cluster_id: str,
        modifications: Optional[Dict] = None,
    ) -> Dict:
        """
        Approve a candidate skill and convert to a rule.

        Args:
            cluster_id: The candidate cluster ID
            modifications: Optional modifications to apply

        Returns:
            Result with success status and skill_id
        """
        if not self._check_learning_available():
            return {"success": False, "error": "Learning modules not available"}

        # Get the cluster
        cluster = self._cluster_engine.get_cluster(cluster_id)
        if not cluster:
            return {"success": False, "error": "Cluster not found"}

        if cluster.get("status") != "candidate":
            return {
                "success": False,
                "error": f"Cluster is not a candidate (status: {cluster.get('status')})"
            }

        # Approve the cluster
        success = self._cluster_engine.approve_cluster(
            cluster_id,
            modifications=modifications
        )

        if not success:
            return {"success": False, "error": "Failed to approve cluster"}

        # Generate skill rule
        try:
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()
            skill = generator.generate_skill(cluster)
            generator.save_skill(skill)

            return {
                "success": True,
                "cluster_id": cluster_id,
                "skill_id": skill.get("id"),
                "message": "Skill approved and added to library",
            }
        except Exception as e:
            print(f"[SkillLearningService] Warning: Could not generate skill: {e}")
            return {
                "success": True,
                "cluster_id": cluster_id,
                "message": "Cluster approved but skill generation failed",
                "error": str(e),
            }

    async def reject_candidate_skill(
        self,
        cluster_id: str,
        reason: str = "",
    ) -> Dict:
        """
        Reject a candidate skill.

        Args:
            cluster_id: The candidate cluster ID
            reason: Optional rejection reason

        Returns:
            Result with success status
        """
        if not self._check_learning_available():
            return {"success": False, "error": "Learning modules not available"}

        success = self._cluster_engine.reject_cluster(cluster_id, reason=reason)

        if not success:
            return {"success": False, "error": "Cluster not found"}

        return {"success": True, "cluster_id": cluster_id}

    # =========================================================================
    # Clustering Operations
    # =========================================================================

    async def trigger_clustering(
        self,
        min_cluster_size: int = 3,
        full_scan: bool = False,
    ) -> Dict:
        """
        Manually trigger the clustering process.

        Args:
            min_cluster_size: Minimum operations to form a cluster
            full_scan: If True, scan all logs instead of incremental

        Returns:
            Clustering result with new clusters
        """
        if not self._check_learning_available():
            return {"success": False, "error": "Learning modules not available"}

        try:
            new_clusters = self._cluster_engine.scan_and_cluster(
                min_cluster_size, full_scan
            )
            return {
                "success": True,
                "new_clusters": len(new_clusters),
                "scan_type": "full" if full_scan else "incremental",
                "clusters": [
                    {
                        "cluster_id": c["cluster_id"],
                        "pattern": c["pattern"],
                        "count": c["count"],
                    }
                    for c in new_clusters
                ],
            }
        except Exception as e:
            import traceback
            error_detail = f"Clustering failed: {e}\n{traceback.format_exc()}"
            print(f"[SkillLearningService] {error_detail}")
            return {"success": False, "error": error_detail}

    async def trigger_llm_sequence_clustering(
        self,
        similarity_threshold: float = 0.75,
        min_cluster_size: int = 2,
        embedding_model: str = None,
        use_llm_pattern: bool = True,
        llm_model: str = "local_qwen8b",
    ) -> Dict:
        """
        Trigger LLM-enhanced sequence clustering.

        Args:
            similarity_threshold: Minimum semantic similarity (0-1)
            min_cluster_size: Minimum sequences to form a cluster
            embedding_model: Model name or local path
            use_llm_pattern: Use LLM for pattern extraction
            llm_model: LLM model name

        Returns:
            Clustering result with new sequence clusters
        """
        if not self._check_llm_available():
            return {
                "success": False,
                "error": "LLM components not available",
            }

        try:
            from learning import OperationLogger

            logger = OperationLogger()
            logs = logger.load_logs(limit=1000)

            # Create engine with LLM pattern extraction
            llm_client = create_llm_client(model_name=llm_model) if use_llm_pattern else None
            engine = LLMClusterEngine(
                llm_client=llm_client,
                use_llm_pattern=use_llm_pattern
            )
            engine.similarity_threshold = similarity_threshold
            engine.min_cluster_size = min_cluster_size
            engine.embedding_model.clear_cache()

            clusters = engine.cluster_sequences(logs, min_cluster_size=min_cluster_size)

            # Save clusters to file
            self._save_llm_clusters(clusters)

            return {
                "success": True,
                "new_clusters": len(clusters),
                "parameters": {
                    "similarity_threshold": similarity_threshold,
                    "min_cluster_size": min_cluster_size,
                    "embedding_model": engine.embedding_model.model_name_or_path,
                    "use_llm_pattern": use_llm_pattern,
                    "llm_model": llm_model if use_llm_pattern else None,
                },
                "clusters": [
                    {
                        "cluster_id": c["cluster_id"],
                        "cluster_type": c.get("cluster_type", "sequence_llm"),
                        "pattern": c["pattern"],
                        "count": c["count"],
                        "sample_instructions": c.get("sample_instructions", [])[:3],
                        "sample_sequences": c.get("sample_sequences", [])[:2],
                    }
                    for c in clusters
                ],
            }
        except Exception as e:
            import traceback
            error_detail = f"LLM sequence clustering failed: {e}\n{traceback.format_exc()}"
            print(f"[SkillLearningService] {error_detail}")
            return {"success": False, "error": error_detail}

    # =========================================================================
    # LLM Review Operations
    # =========================================================================

    async def review_candidate(self, cluster_id: str) -> Dict:
        """
        Review a candidate skill using LLM.

        Args:
            cluster_id: The candidate cluster ID

        Returns:
            Review result with decision and scores
        """
        if not self._check_llm_available():
            return {
                "success": False,
                "error": "LLM components not available",
            }

        # Get the cluster
        cluster = self._cluster_engine.get_cluster(cluster_id)
        if not cluster:
            return {"success": False, "error": "Cluster not found"}

        # Review with LLM
        review_result = self._llm_reviewer.review_candidate(cluster)

        # Save review result
        self._save_review_result(cluster_id, review_result)

        return {
            "success": True,
            "cluster_id": cluster_id,
            "review": review_result,
        }

    async def auto_approve_with_llm(self, cluster_id: str) -> Dict:
        """
        Auto-approve a candidate skill using LLM review.

        Args:
            cluster_id: The candidate cluster ID

        Returns:
            Result with approval decision
        """
        if not self._check_llm_available():
            return {
                "success": False,
                "error": "LLM components not available",
            }

        # Get the cluster
        cluster = self._cluster_engine.get_cluster(cluster_id)
        if not cluster:
            return {"success": False, "error": "Cluster not found"}

        if cluster.get("status") != "candidate":
            return {
                "success": False,
                "error": f"Cluster is not a candidate (status: {cluster.get('status')})"
            }

        # Review with LLM
        review_result = self._llm_reviewer.review_candidate(cluster)
        decision = review_result.get("decision", "requires_human_review")

        if decision == "auto_approved":
            # Auto-approve the cluster
            self._cluster_engine.approve_cluster(cluster_id)

            # Generate and save skill
            try:
                from learning.skill_generator import SkillGenerator
                generator = SkillGenerator()
                skill = generator.generate_skill(cluster)
                generator.save_skill(skill)

                return {
                    "success": True,
                    "decision": "auto_approved",
                    "cluster_id": cluster_id,
                    "skill_id": skill.get("id"),
                    "review": review_result,
                }
            except Exception as e:
                return {
                    "success": False,
                    "decision": "approved_but_generation_failed",
                    "cluster_id": cluster_id,
                    "error": str(e),
                    "review": review_result,
                }
        else:
            # Not approved, save review result for human
            self._save_review_result(cluster_id, review_result)

            return {
                "success": True,
                "decision": decision,
                "cluster_id": cluster_id,
                "reason": review_result.get("recommendation", {}).get("reason", ""),
                "review": review_result,
            }

    async def get_review_queue(self) -> Dict:
        """
        Get candidates that require human review.

        Returns:
            Review queue with candidates and their review status
        """
        if not self._check_learning_available():
            return {"total": 0, "queue": []}

        candidates = self._cluster_engine.get_candidates()
        review_results = self._load_review_results()

        # Filter to those requiring human review
        human_review_queue = []
        for c in candidates:
            cluster_id = c.get("cluster_id")
            review = review_results.get(cluster_id, {})

            # Include if:
            # 1. Has been reviewed and flagged for human review
            # 2. Has not been reviewed yet
            if review.get("decision") == "requires_human_review" or not review:
                human_review_queue.append({
                    "cluster": c,
                    "review": review if review else None,
                })

        return {
            "total": len(human_review_queue),
            "queue": human_review_queue,
        }

    # =========================================================================
    # Skill Management
    # =========================================================================

    async def list_skills(self, cluster_type: str = None) -> Dict:
        """
        List all approved skills.

        Args:
            cluster_type: Optional cluster type filter

        Returns:
            List of skills
        """
        if not self._check_learning_available():
            return {"total": 0, "skills": []}

        try:
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()
            skills = generator.list_skills(cluster_type=cluster_type)
            return {
                "total": len(skills),
                "skills": skills,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to load skills: {e}"}

    async def get_skill(self, skill_id: str) -> Optional[Dict]:
        """
        Get a specific skill by ID.

        Args:
            skill_id: The skill ID

        Returns:
            Skill details or None
        """
        if not self._check_learning_available():
            return None

        try:
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()
            return generator.get_skill(skill_id)
        except Exception:
            return None

    async def update_skill(
        self,
        skill_id: str,
        enabled: Optional[bool] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        trigger_patterns: Optional[List[str]] = None,
        app_context: Optional[List[str]] = None,
        actions: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Update a skill's properties.

        Args:
            skill_id: The skill ID to update
            enabled: Enable/disable the skill
            name: Skill name
            description: Skill description
            trigger_patterns: Regex patterns for matching
            app_context: Application context filters
            actions: Action sequence

        Returns:
            Updated skill info
        """
        if not self._check_learning_available():
            return {"success": False, "error": "Learning modules not available"}

        try:
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()

            # Get existing skill
            skill = generator.get_skill(skill_id)
            if not skill:
                return {"success": False, "error": f"Skill not found: {skill_id}"}

            # Update fields
            updated_fields = []

            if enabled is not None:
                generator.update_skill_enabled(skill_id, enabled)
                skill["enabled"] = enabled
                updated_fields.append("enabled")

            if name is not None:
                skill["name"] = name
                updated_fields.append("name")

            if description is not None:
                skill["description"] = description
                updated_fields.append("description")

            if trigger_patterns is not None:
                skill["trigger"]["patterns"] = trigger_patterns
                updated_fields.append("trigger_patterns")

            if app_context is not None:
                skill["trigger"]["app_context"] = app_context
                updated_fields.append("app_context")

            if actions is not None:
                skill["actions"] = actions
                updated_fields.append("actions")

            # Save updated skill
            if updated_fields:
                generator.save_skill(skill)

            return {
                "success": True,
                "skill_id": skill_id,
                "updated_fields": updated_fields,
                "skill": skill
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to update skill: {e}"}

    async def delete_skill(self, skill_id: str) -> Dict:
        """
        Delete a skill from storage.

        Args:
            skill_id: The skill ID to delete

        Returns:
            Success status
        """
        if not self._check_learning_available():
            return {"success": False, "error": "Learning modules not available"}

        try:
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()
            success = generator.delete_skill(skill_id)

            if not success:
                return {"success": False, "error": f"Skill not found: {skill_id}"}

            return {"success": True, "skill_id": skill_id}

        except Exception as e:
            return {"success": False, "error": f"Failed to delete skill: {e}"}

    # =========================================================================
    # Statistics
    # =========================================================================

    async def get_skill_stats(self) -> Dict:
        """Get statistics about skill learning"""
        if not self._check_learning_available():
            return {"available": False}

        from learning import OperationLogger
        from learning.skill_generator import SkillGenerator

        cluster_stats = self._cluster_engine.get_stats()
        logger = OperationLogger()
        log_stats = logger.get_stats()
        generator = SkillGenerator()
        skill_stats = generator.get_stats()

        return {
            "clusters": cluster_stats,
            "operations": log_stats,
            "skills": skill_stats,
        }

    async def get_llm_skill_stats(self) -> Dict:
        """Get statistics about LLM-enhanced skill learning"""
        if not self._check_llm_available():
            return {"available": False}

        review_stats = self._llm_reviewer.get_review_stats() if self._llm_reviewer else {}

        return {
            "available": True,
            "review_stats": review_stats,
        }

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _save_llm_clusters(self, clusters: List[Dict]) -> None:
        """Save LLM-generated clusters to file"""
        clusters_dir = "data/clusters"
        os.makedirs(clusters_dir, exist_ok=True)

        # Load existing clusters
        existing_file = os.path.join(clusters_dir, "operation_clusters.json")
        if os.path.exists(existing_file):
            with open(existing_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"clusters": [], "last_scan": None, "last_scan_log_id": ""}

        # Add new clusters (avoid duplicates by cluster_id)
        existing_ids = {c.get("cluster_id") for c in data.get("clusters", [])}
        for cluster in clusters:
            if cluster.get("cluster_id") not in existing_ids:
                data["clusters"].append(cluster)

        # Save
        with open(existing_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_review_result(self, cluster_id: str, review_result: Dict) -> None:
        """Save LLM review result to file"""
        review_dir = "data/reviews"
        os.makedirs(review_dir, exist_ok=True)

        review_file = os.path.join(review_dir, f"{cluster_id}.json")
        with open(review_file, "w", encoding="utf-8") as f:
            json.dump(review_result, f, ensure_ascii=False, indent=2)

    def _load_review_results(self) -> Dict[str, Dict]:
        """Load all review results"""
        review_dir = "data/reviews"
        results = {}

        if os.path.exists(review_dir):
            for filename in os.listdir(review_dir):
                if filename.endswith(".json"):
                    cluster_id = filename[:-5]
                    with open(os.path.join(review_dir, filename), "r", encoding="utf-8") as f:
                        results[cluster_id] = json.load(f)

        return results
