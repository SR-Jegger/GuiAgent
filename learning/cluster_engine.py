"""
Cluster Engine - Identifies repeated operation patterns from logs.

This module scans operation logs and clusters similar operations together,
identifying patterns that could become reusable skills.
"""

import os
import json
import uuid
from datetime import datetime
from typing import Any, Optional

from learning.operation_logger import OperationLogger
from learning.similarity import (
    is_same_operation,
    extract_pattern_from_instructions,
    instruction_similarity,
)


class ClusterEngine:
    """
    Clusters similar operations to identify potential skills.

    Features:
    - Loads operation logs
    - Clusters by instruction similarity + action structure
    - Identifies candidate skills (repeated operations)
    - Manages cluster lifecycle
    """

    def __init__(self, data_dir: str = "data"):
        """
        Initialize the cluster engine.

        Args:
            data_dir: Directory containing logs and clusters data
        """
        self.data_dir = data_dir
        self.logger = OperationLogger(os.path.join(data_dir, "logs"))
        self.clusters_file = os.path.join(data_dir, "clusters", "operation_clusters.json")

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.clusters_file), exist_ok=True)

        # Load existing clusters
        self.clusters = self._load_clusters()

    def _load_clusters(self) -> dict:
        """Load existing clusters from file."""
        if os.path.exists(self.clusters_file):
            try:
                with open(self.clusters_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ClusterEngine] Error loading clusters: {e}")

        return {"clusters": [], "last_scan": None, "last_scan_log_id": ""}

    def _save_clusters(self) -> None:
        """Save clusters to file."""
        try:
            with open(self.clusters_file, "w", encoding="utf-8") as f:
                json.dump(self.clusters, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ClusterEngine] Error saving clusters: {e}")

    def scan_and_cluster(self, min_cluster_size: int = 3, full_scan: bool = False) -> list[dict]:
        """
        Scan logs and cluster similar operations.

        This method:
        1. Loads logs incrementally (only new logs since last scan)
        2. Updates existing clusters with matching new logs
        3. Creates new clusters from (new logs + previously unclustered logs)

        Args:
            min_cluster_size: Minimum operations to form a candidate cluster
            full_scan: If True, scan all logs (ignore incremental marker)

        Returns:
            List of new candidate clusters
        """
        print(f"[ClusterEngine] Starting {'full' if full_scan else 'incremental'} scan...")

        # Get the last scan log_id for incremental loading
        last_scan_log_id = self.clusters.get("last_scan_log_id", "")

        # Load logs (incremental or full)
        if full_scan or not last_scan_log_id:
            # Full scan - load all logs
            all_logs = self.logger.load_logs(limit=10000)
            new_logs = all_logs  # Treat all as "new" for full scan
            print(f"[ClusterEngine] Loaded {len(all_logs)} logs (full scan)")
        else:
            # Incremental scan - load new logs
            new_logs = self.logger.load_logs_after(after_log_id=last_scan_log_id, limit=1000)
            print(f"[ClusterEngine] Loaded {len(new_logs)} new logs (after {last_scan_log_id[:8]}...)")
            # Also load all logs to find unclustered ones (for creating new clusters)
            all_logs = self.logger.load_logs(limit=10000)

        # Filter to successful VLM operations only
        vlm_new_logs = [
            log for log in new_logs
            if log.get("success") and log.get("source") == "vlm"
        ]
        vlm_all_logs = [
            log for log in all_logs
            if log.get("success") and log.get("source") == "vlm"
        ]
        print(f"[ClusterEngine] {len(vlm_new_logs)} new VLM operations, {len(vlm_all_logs)} total VLM operations")

        # Filter out sequence operations (handled by LLM sequence clustering)
        from learning.similarity import is_sequence_operation
        sequence_log_ids = self._identify_sequence_operations(vlm_all_logs, is_sequence_operation)
        print(f"[ClusterEngine] {len(sequence_log_ids)} logs belong to sequence operations (will be skipped)")

        # Exclude sequence operation logs from single-operation clustering
        vlm_all_logs = [log for log in vlm_all_logs if log.get("log_id") not in sequence_log_ids]
        vlm_new_logs = [log for log in vlm_new_logs if log.get("log_id") not in sequence_log_ids]
        print(f"[ClusterEngine] {len(vlm_all_logs)} single-operation logs to cluster")

        # Track which logs are already in clusters
        clustered_log_ids = set()
        for cluster in self.clusters.get("clusters", []):
            clustered_log_ids.update(cluster.get("members", []))

        # Find unclustered logs from ALL logs (not just new)
        unclustered = [
            log for log in vlm_all_logs
            if log.get("log_id") not in clustered_log_ids
        ]
        print(f"[ClusterEngine] {len(unclustered)} unclustered operations (total)")

        # Step 1: Try to add NEW logs to existing clusters (incremental update)
        added_to_existing = 0
        for cluster in self.clusters.get("clusters", []):
            cluster_pattern = cluster.get("pattern", {})
            cluster_members = cluster.get("members", [])

            # Only try to add NEW logs to existing clusters
            for log in vlm_new_logs:
                if log.get("log_id") in clustered_log_ids:
                    continue

                # Check if this log matches the cluster pattern
                if self._matches_cluster_pattern(log, cluster_pattern):
                    # Add to cluster
                    cluster_members.append(log.get("log_id"))
                    clustered_log_ids.add(log.get("log_id"))
                    added_to_existing += 1

            # Update cluster count
            cluster["count"] = len(cluster_members)
            cluster["members"] = cluster_members
            cluster["updated_at"] = datetime.now().isoformat()

            # Update sample instructions/actions (from newly added)
            if added_to_existing > 0:
                instructions = cluster.get("sample_instructions", [])
                # Add instruction from the last added log
                for log in vlm_new_logs:
                    if log.get("log_id") in cluster_members:
                        instructions.append(log.get("instruction", ""))
                        break
                cluster["sample_instructions"] = instructions[-5:]  # Keep last 5

        if added_to_existing > 0:
            print(f"[ClusterEngine] Added {added_to_existing} new logs to existing clusters")
            self._save_clusters()

        # Refresh unclustered list after adding to existing clusters
        unclustered = [
            log for log in vlm_all_logs
            if log.get("log_id") not in clustered_log_ids
        ]
        print(f"[ClusterEngine] {len(unclustered)} remaining unclustered operations (for new clusters)")

        # Step 2: Create new clusters from remaining unclustered logs
        new_clusters = []
        used_log_ids = set()

        for i, log1 in enumerate(unclustered):
            if log1.get("log_id") in used_log_ids:
                continue

            # Find all similar operations
            similar = [log1]
            similar_ids = {log1.get("log_id")}

            for log2 in unclustered[i + 1:]:
                if log2.get("log_id") in used_log_ids:
                    continue

                if is_same_operation(log1, log2):
                    similar.append(log2)
                    similar_ids.add(log2.get("log_id"))

            # If enough similar operations, create a cluster
            if len(similar) >= min_cluster_size:
                cluster = self._create_cluster(similar)
                new_clusters.append(cluster)

                # Mark these logs as used
                used_log_ids.update(similar_ids)

        # Add new clusters
        if new_clusters:
            self.clusters.setdefault("clusters", []).extend(new_clusters)
            self.clusters["last_scan"] = datetime.now().isoformat()
            self._save_clusters()
            print(f"[ClusterEngine] Created {len(new_clusters)} new clusters")
        else:
            print(f"[ClusterEngine] No new clusters found")

        # Update last scan log_id for incremental loading
        latest_log_id = self.logger.get_latest_log_id()
        if latest_log_id:
            self.clusters["last_scan_log_id"] = latest_log_id

        self.clusters["last_scan"] = datetime.now().isoformat()
        self._save_clusters()

        return new_clusters

    def _matches_cluster_pattern(self, log: dict, pattern: dict) -> bool:
        """
        Check if a log matches a cluster's pattern.

        Args:
            log: Operation log dict
            pattern: Cluster pattern dict

        Returns:
            True if the log matches the pattern
        """
        # Check action structure match
        log_structure = log.get("action_structure", [])
        pattern_structure = pattern.get("action_structure", [])

        if log_structure and pattern_structure:
            if log_structure != pattern_structure:
                return False

        # Check app context match (if pattern has one)
        pattern_app = pattern.get("app_context", "")
        if pattern_app:
            log_app = log.get("app_context", {}).get("active_window", "")
            if log_app and pattern_app not in log_app:
                return False

        # Check instruction pattern match
        import re
        instruction_pattern = pattern.get("instruction_pattern", ".*")
        log_instruction = log.get("instruction", "")

        try:
            if not re.search(instruction_pattern, log_instruction):
                return False
        except re.error:
            # Invalid regex, use similarity check instead
            from learning.similarity import instruction_similarity
            # Compare with sample instructions
            samples = pattern.get("sample_instructions", [])
            if samples:
                max_sim = max(instruction_similarity(log_instruction, s) for s in samples)
                if max_sim < 0.5:
                    return False

        return True

    def _create_cluster(self, logs: list[dict]) -> dict:
        """
        Create a cluster from a list of similar operations.

        Args:
            logs: List of similar operation logs

        Returns:
            Cluster dict
        """
        cluster_id = f"cluster_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()

        # Extract common pattern
        instructions = [log.get("instruction", "") for log in logs]
        instruction_pattern = extract_pattern_from_instructions(instructions)

        # Get action structure (should be same/similar for all)
        action_structures = [log.get("action_structure", []) for log in logs]
        # Use the most common structure
        structure_counts = {}
        for struct in action_structures:
            struct_tuple = tuple(struct)
            structure_counts[struct_tuple] = structure_counts.get(struct_tuple, 0) + 1

        # Handle empty structure_counts
        if structure_counts:
            most_common_structure = max(structure_counts.items(), key=lambda x: x[1])[0]
        else:
            most_common_structure = ()

        # Get app context (most common)
        app_contexts = [log.get("app_context", {}) for log in logs]
        app_context = {}
        if app_contexts:
            # Get most common app
            apps = [ctx.get("active_window", "") or ctx.get("window_title", "")
                    for ctx in app_contexts]
            if apps:
                # Find most common (only non-empty apps)
                app_counts = {}
                for app in apps:
                    if app:
                        app_counts[app] = app_counts.get(app, 0) + 1
                if app_counts:
                    app_context["active_window"] = max(app_counts.items(),
                                                       key=lambda x: x[1])[0]

        # Collect all actions for later analysis
        all_actions = []
        for log in logs:
            all_actions.extend(log.get("actions", []))

        return {
            "cluster_id": cluster_id,
            "pattern": {
                "instruction_pattern": instruction_pattern,
                "app_context": app_context.get("active_window", ""),
                "action_structure": list(most_common_structure),
            },
            "members": [log.get("log_id") for log in logs],
            "sample_instructions": instructions[:5],  # Keep some samples
            "sample_actions": all_actions[:10],  # Keep some sample actions
            "count": len(logs),
            "status": "candidate",  # candidate | approved | rejected
            "created_at": now,
            "updated_at": now,
        }

    def _identify_sequence_operations(
        self,
        logs: list[dict],
        is_sequence_fn
    ) -> set:
        """
        Identify logs that belong to sequence operations.

        Groups logs by (instruction_hash, task_name) and determines if
        each group represents a sequence operation.

        Args:
            logs: List of operation logs
            is_sequence_fn: Function to check if a group is a sequence operation

        Returns:
            Set of log_ids that belong to sequence operations
        """
        # Group by (instruction_hash, task_name)
        groups = {}
        for log in logs:
            key = (log.get("instruction_hash", ""), log.get("task_name", ""))
            if key not in groups:
                groups[key] = []
            groups[key].append(log)

        # Identify sequence operation logs
        sequence_log_ids = set()
        for key, group_logs in groups.items():
            if is_sequence_fn(group_logs):
                # All logs in this group belong to a sequence operation
                for log in group_logs:
                    sequence_log_ids.add(log.get("log_id"))

        return sequence_log_ids

    def get_candidates(self) -> list[dict]:
        """
        Get all candidate clusters awaiting approval.

        Reloads from file to ensure sync with external changes.

        Returns:
            List of candidate clusters
        """
        # Reload from file to sync with any external changes
        self.clusters = self._load_clusters()

        return [
            c for c in self.clusters.get("clusters", [])
            if c.get("status") == "candidate"
        ]

    def get_cluster(self, cluster_id: str) -> Optional[dict]:
        """
        Get a specific cluster by ID.

        Reloads from file to ensure sync with external changes.

        Args:
            cluster_id: Cluster ID

        Returns:
            Cluster dict or None if not found
        """
        # Reload from file to sync with any external changes
        self.clusters = self._load_clusters()

        for cluster in self.clusters.get("clusters", []):
            if cluster.get("cluster_id") == cluster_id:
                return cluster
        return None

    def approve_cluster(self, cluster_id: str, modifications: dict = None) -> bool:
        """
        Approve a candidate cluster.

        Args:
            cluster_id: Cluster ID
            modifications: Optional modifications to apply

        Returns:
            True if approved successfully
        """
        cluster = self.get_cluster(cluster_id)
        if not cluster:
            print(f"[ClusterEngine] Cluster not found: {cluster_id}")
            return False

        if cluster.get("status") != "candidate":
            print(f"[ClusterEngine] Cluster is not a candidate: {cluster_id}")
            return False

        # Apply modifications if provided
        if modifications:
            cluster.update(modifications)

        cluster["status"] = "approved"
        cluster["updated_at"] = datetime.now().isoformat()

        self._save_clusters()
        print(f"[ClusterEngine] Approved cluster: {cluster_id}")

        return True

    def reject_cluster(self, cluster_id: str, reason: str = "") -> bool:
        """
        Reject a candidate cluster by removing it from the clusters list.

        Unlike the previous implementation that marked clusters as "rejected",
        this directly deletes them to avoid accumulating zombie data.

        Args:
            cluster_id: Cluster ID
            reason: Reason for rejection (logged but not stored)

        Returns:
            True if rejected successfully
        """
        cluster = self.get_cluster(cluster_id)
        if not cluster:
            print(f"[ClusterEngine] Cluster not found: {cluster_id}")
            return False

        # Log the rejection reason
        if reason:
            print(f"[ClusterEngine] Rejected cluster {cluster_id}: {reason}")
        else:
            print(f"[ClusterEngine] Rejected cluster: {cluster_id}")

        # Remove the cluster entirely (don't keep rejected status)
        self.clusters["clusters"] = [
            c for c in self.clusters.get("clusters", [])
            if c.get("cluster_id") != cluster_id
        ]

        self._save_clusters()

        return True

    def get_stats(self) -> dict:
        """
        Get cluster statistics.

        Returns:
            Dict with cluster stats
        """
        clusters = self.clusters.get("clusters", [])

        return {
            "total_clusters": len(clusters),
            "candidates": sum(1 for c in clusters if c.get("status") == "candidate"),
            "approved": sum(1 for c in clusters if c.get("status") == "approved"),
            "last_scan": self.clusters.get("last_scan"),
        }