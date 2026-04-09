"""
LLM Cluster Engine - Semantic clustering using embeddings and LLM.

This module replaces the rule-based clustering with semantic understanding:
- Uses sentence embeddings for semantic similarity
- Supports Chinese and multilingual instructions
- DBSCAN clustering for automatic cluster number detection
- LLM-assisted pattern extraction for better generalization
- Sequence clustering for multi-step operations

Usage:
    from learning.llm_cluster_engine import LLMClusterEngine

    engine = LLMClusterEngine()
    logs = [...]  # Load operation logs

    # Single-operation clustering
    clusters = engine.cluster_operations(logs)

    # Sequence clustering (for multi-step tasks)
    sequence_clusters = engine.cluster_sequences(logs)
"""

import os
import json
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
import numpy as np

# Set cache directory to project folder (avoid C drive space issues)
PROJECT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".hf_cache"
)
os.environ["HF_HOME"] = PROJECT_CACHE_DIR
os.environ["SENTENCE_TRANSFORMERS_HOME"] = PROJECT_CACHE_DIR

from learning.llm_client import LLMClient


# ============================================================================
# Embedding Model Wrapper
# ============================================================================

class EmbeddingModel:
    """
    Wrapper for sentence transformer embedding models.

    Supports multilingual models for Chinese instruction understanding.
    Can load models from local path or model name.
    """

    def __init__(self, model_name_or_path: str = "gte-multilingual-base"):
        """
        Initialize the embedding model.

        Args:
            model_name_or_path: Model name or local path to model directory.
                               If path exists, loads from local directory.
        """
        self.model_name_or_path = model_name_or_path
        self._model = None
        self._cache = {}

        # Check if it's a local path
        self._is_local_path = os.path.exists(model_name_or_path)
        if self._is_local_path:
            print(f"[EmbeddingModel] Will load from local path: {model_name_or_path}")

    @property
    def model(self):
        """Lazy load the embedding model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                if self._is_local_path:
                    # Load from local path - allow downloading missing configs from HF
                    # local_files_only=False allows fetching missing config files
                    self._model = SentenceTransformer(
                        self.model_name_or_path,
                        trust_remote_code=True,
                        local_files_only=False
                    )
                    print(f"[EmbeddingModel] Loaded from local path: {self.model_name_or_path}")
                else:
                    # Load from model name (will use HF_HOME cache)
                    self._model = SentenceTransformer(self.model_name_or_path, local_files_only=True)
                    print(f"[EmbeddingModel] Loaded: {self.model_name_or_path}")
            except ImportError:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                )
        return self._model

    def encode(self, text: str) -> np.ndarray:
        """
        Encode text to embedding vector.

        Args:
            text: Input text

        Returns:
            Embedding vector as numpy array
        """
        if text not in self._cache:
            self._cache[text] = self.model.encode(text, normalize_embeddings=True)
        return self._cache[text]

    def encode_batch(self, texts: List[str], show_progress: bool = False) -> np.ndarray:
        """
        Encode multiple texts to embedding vectors.

        Args:
            texts: List of input texts
            show_progress: Show progress bar

        Returns:
            Embedding matrix (n_texts x embedding_dim)
        """
        # Check cache first
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            if text not in self._cache:
                uncached_texts.append(text)
                uncached_indices.append(i)

        # Encode uncached texts
        if uncached_texts:
            embeddings = self.model.encode(
                uncached_texts,
                normalize_embeddings=True,
                show_progress_bar=show_progress
            )
            for i, text in zip(uncached_indices, uncached_texts):
                self._cache[text] = embeddings[i - uncached_indices[0]]

        # Return cached embeddings
        return np.array([self._cache[text] for text in texts])

    def similarity(self, text1: str, text2: str) -> float:
        """
        Compute cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Cosine similarity (0-1)
        """
        emb1 = self.encode(text1)
        emb2 = self.encode(text2)
        return float(np.dot(emb1, emb2))

    def clear_cache(self):
        """Clear the embedding cache."""
        self._cache = {}


# ============================================================================
# LLM Cluster Engine
# ============================================================================

class LLMClusterEngine:
    """
    Clusters similar operations using semantic embeddings and LLM.

    Features:
    - Semantic similarity using sentence embeddings
    - DBSCAN clustering for automatic cluster detection
    - LLM-assisted pattern extraction
    - Incremental clustering support
    - Sequence clustering for multi-step operations
    """

    def __init__(
        self,
        embedding_model: str = None,
        llm_client: Optional[LLMClient] = None,
        similarity_threshold: float = 0.75,
        min_cluster_size: int = 3,
        use_llm_pattern: bool = False,  # Default to False - use heuristic instead
    ):
        """
        Initialize the LLM cluster engine.

        Args:
            embedding_model: Model name or local path. If None, uses local cache path.
            llm_client: LLM client for pattern extraction
            similarity_threshold: Minimum semantic similarity for clustering
            min_cluster_size: Minimum operations to form a cluster
        """
        # Default to local model path if available
        if embedding_model is None:
            print(f"[LLMClusterEngine] No embedding model specified, checking local cache...")

            # Use gte-multilingual-base
            local_model_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "gte_multilingual_base",
            )

            if os.path.exists(local_model_path):
                embedding_model = local_model_path
                print(f"[LLMClusterEngine] Using local model: {local_model_path}")
            else:
                print(f"[LLMClusterEngine] Local embedding model not found, using model name")
                embedding_model = "Alibaba-NLP/gte-multilingual-base"

        self.embedding_model = EmbeddingModel(embedding_model)
        self.llm_client = llm_client
        self.similarity_threshold = similarity_threshold
        self.min_cluster_size = min_cluster_size
        self.use_llm_pattern = use_llm_pattern

        # DBSCAN eps parameter (derived from similarity threshold)
        # Higher similarity = lower eps
        self.dbscan_eps = 1.0 - similarity_threshold

    def _build_semantic_text(self, log: Dict) -> str:
        """
        Build a semantic text representation for a log entry.

        Combines instruction, app context, and action structure into
        a meaningful text for embedding.

        Args:
            log: Operation log dict

        Returns:
            Semantic text string
        """
        instruction = log.get("instruction", "")
        app_context = log.get("app_context", {})
        app_name = app_context.get("active_window", "") or app_context.get("window_title", "")
        action_structure = log.get("action_structure", [])
        actions_str = " -> ".join(action_structure) if action_structure else "无"

        # Build semantic text that captures the full meaning
        return f"在{app_name}中{instruction}，执行动作：{actions_str}"

    def compute_similarities(
        self,
        logs: List[Dict]
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Compute semantic embeddings for all logs.

        Args:
            logs: List of operation logs

        Returns:
            Tuple of (embedding_matrix, semantic_texts)
        """
        semantic_texts = [self._build_semantic_text(log) for log in logs]
        embeddings = self.embedding_model.encode_batch(semantic_texts)
        return embeddings, semantic_texts

    def _extract_pattern(self, instructions: List[str]) -> str:
        """
        Extract a regex pattern from similar instructions.

        Uses LLM if enabled and available, falls back to simple heuristic.

        Args:
            instructions: List of similar instructions

        Returns:
            Regex pattern string
        """
        if not instructions:
            return ".*"

        if len(set(instructions)) == 1:
            # All instructions are identical
            import re
            return re.escape(instructions[0])

        # Try LLM extraction only if explicitly enabled
        if self.use_llm_pattern and self.llm_client:
            try:
                print(f"[LLMClusterEngine] Attempting LLM pattern extraction for {len(instructions)} instructions...")
                from learning.llm_pattern_extractor import extract_pattern_with_llm
                pattern_result = extract_pattern_with_llm(instructions, self.llm_client)
                return pattern_result.get("regex_pattern", ".*")
            except Exception as e:
                print(f"[LLMClusterEngine] LLM pattern extraction failed: {e}, using fallback")

        # Fallback to simple heuristic (fast and reliable)
        from learning.similarity import extract_pattern_from_instructions
        return extract_pattern_from_instructions(instructions)

    def _collect_sample_actions(self, logs: List[Dict]) -> List[Dict]:
        """
        Collect sample actions from logs.

        Args:
            logs: List of operation logs

        Returns:
            List of sample actions
        """
        actions = []
        for log in logs:
            actions.extend(log.get("actions", []))
            if len(actions) >= 10:
                break
        return actions[:10]


# ============================================================================
# Sequence Clustering
# ============================================================================

    def cluster_sequences(
        self,
        logs: List[Dict],
        min_cluster_size: int = 3,
        full_scan: bool = False,
    ) -> List[Dict]:
        """
        Cluster operation sequences using semantic embeddings.

        This method:
        1. Groups logs into sequences by instruction_hash + task_name
        2. Builds semantic embeddings for each sequence
        3. Clusters similar sequences using DBSCAN

        Args:
            logs: List of operation logs
            min_cluster_size: Minimum sequences to form a cluster
            full_scan: If True, re-cluster all logs

        Returns:
            List of sequence cluster dicts
        """
        print(f"[LLMClusterEngine] Starting sequence clustering for {len(logs)} operations...")

        # Filter to successful VLM operations
        vlm_logs = [
            log for log in logs
            if log.get("success") and log.get("source") == "vlm"
        ]
        print(f"[LLMClusterEngine] {len(vlm_logs)} VLM operations to cluster sequences")

        # Step 1: Group logs into sequences
        sequences = self._group_logs_into_sequences(vlm_logs)
        print(f"[LLMClusterEngine] Formed {len(sequences)} sequences")

        if len(sequences) < min_cluster_size:
            print(f"[LLMClusterEngine] Not enough sequences (need {min_cluster_size})")
            return []

        # Step 2: Build semantic embeddings for sequences
        sequence_embeddings, sequence_texts = self._encode_sequences(sequences)

        # Step 3: Run DBSCAN clustering
        from sklearn.cluster import DBSCAN

        clustering = DBSCAN(
            eps=self.dbscan_eps,
            min_samples=min_cluster_size,
            metric='cosine'
        )
        labels = clustering.fit_predict(sequence_embeddings)

        # Step 4: Aggregate sequences by cluster label
        cluster_dict: Dict[int, List[Dict]] = {}
        for i, label in enumerate(labels):
            if label == -1:  # Noise point
                continue
            if label not in cluster_dict:
                cluster_dict[label] = []
            cluster_dict[label].append(sequences[i])

        print(f"[LLMClusterEngine] Found {len(cluster_dict)} sequence clusters")

        # Step 5: Validate and convert to cluster format
        clusters = []
        for label, cluster_sequences in cluster_dict.items():
            # Validate: all sequences in a cluster should have similar instruction_hash
            # If instruction hashes vary too much, split the cluster
            validated_clusters = self._validate_sequence_cluster(cluster_sequences)
            clusters.extend(validated_clusters)

        return clusters

    def _group_logs_into_sequences(self, logs: List[Dict]) -> List[Dict]:
        """
        Group individual operation logs into sequences by instruction.

        Uses instruction_hash + task_name to identify operations belonging
        to the same instruction execution.

        Args:
            logs: List of operation logs

        Returns:
            List of sequence dicts
        """
        from learning.similarity import compute_instruction_hash, is_sequence_operation

        # Group by (instruction_hash, task_name)
        groups = {}
        for log in logs:
            key = (log.get("instruction_hash", ""), log.get("task_name", ""))
            if key not in groups:
                groups[key] = []
            groups[key].append(log)

        sequences = []

        for (instr_hash, task_name), group_logs in groups.items():
            # Sort by timestamp
            sorted_logs = sorted(group_logs, key=lambda x: x.get("timestamp", ""))

            # Get instruction (should be same for all)
            instruction = sorted_logs[0].get("instruction", "") if sorted_logs else ""

            # Determine if this is a SEQUENCE operation
            if not is_sequence_operation(sorted_logs):
                # Single operation - skip
                continue

            # Split into execution instances
            execution_instances = self._split_into_instances(sorted_logs)

            # Create a sequence for each execution instance
            for instance_logs in execution_instances:
                if len(instance_logs) < 2:  # At least 2 steps to be a sequence
                    continue

                # Sort by step_id within instance
                instance_logs = sorted(instance_logs, key=lambda x: x.get("step_id", 0))

                # Build sequence of actions
                actions = []
                action_structure = []
                app_contexts = []
                log_ids = []

                for log in instance_logs:
                    actions.extend(log.get("actions", []))
                    action_structure.extend(log.get("action_structure", []))
                    app_contexts.append(log.get("app_context", {}))
                    log_ids.append(log.get("log_id", ""))

                # Create sequence ID
                sequence_id = f"seq_{uuid.uuid4().hex[:8]}"

                # Get most common app context
                app_counts = {}
                for ctx in app_contexts:
                    app = ctx.get("active_window", "")
                    if app:
                        app_counts[app] = app_counts.get(app, 0) + 1

                most_common_app = max(app_counts.items(), key=lambda x: x[1])[0] if app_counts else ""

                sequences.append({
                    "sequence_id": sequence_id,
                    "instruction": instruction,
                    "instruction_hash": instr_hash,
                    "task_name": task_name,
                    "actions": actions,
                    "action_structure": action_structure,
                    "app_context": most_common_app,
                    "log_ids": log_ids,
                    "log_count": len(instance_logs),
                })

        return sequences

    def _split_into_instances(self, sorted_logs: List[Dict]) -> List[List[Dict]]:
        """
        Split sorted logs into separate execution instances.

        Uses step_id reset and time gaps to identify instance boundaries.

        Args:
            sorted_logs: Logs sorted by timestamp

        Returns:
            List of execution instances (each is a list of logs)
        """
        # Use same threshold as _is_sequence_operation for consistency
        MAX_SEQUENCE_GAP_SECONDS = 30

        execution_instances = []
        current_instance = []

        for log in sorted_logs:
            step_id = log.get("step_id", 0)
            timestamp = log.get("timestamp", "")

            if current_instance:
                last_log = current_instance[-1]
                last_step = last_log.get("step_id", 0)
                last_timestamp = last_log.get("timestamp", "")

                # Calculate time gap
                try:
                    time1 = datetime.fromisoformat(last_timestamp)
                    time2 = datetime.fromisoformat(timestamp)
                    time_gap = abs((time2 - time1).total_seconds())
                except:
                    time_gap = 0

                # Determine if this is a new execution instance
                is_new_instance = False

                # Case 1: step_id decreased (clear reset)
                if step_id < last_step:
                    is_new_instance = True

                # Case 2: Large time gap + step_id not consecutive
                elif time_gap > MAX_SEQUENCE_GAP_SECONDS and step_id <= last_step + 1:
                    is_new_instance = True

                if is_new_instance:
                    if len(current_instance) >= 1:
                        execution_instances.append(current_instance)
                    current_instance = [log]
                else:
                    current_instance.append(log)
            else:
                current_instance.append(log)

        # Don't forget the last instance
        if current_instance:
            execution_instances.append(current_instance)

        return execution_instances

    def _encode_sequences(self, sequences: List[Dict]) -> Tuple[np.ndarray, List[str]]:
        """
        Build semantic embeddings for sequences.

        The semantic text includes:
        - Instruction
        - Action structure
        - App context

        Args:
            sequences: List of sequence dicts

        Returns:
            Tuple of (embedding_matrix, semantic_texts)
        """
        semantic_texts = []
        for seq in sequences:
            text = self._build_sequence_semantic_text(seq)
            semantic_texts.append(text)

        embeddings = self.embedding_model.encode_batch(semantic_texts)
        return embeddings, semantic_texts

    def _build_sequence_semantic_text(self, seq: Dict) -> str:
        """
        Build a semantic text representation for a sequence.

        Args:
            seq: Sequence dict

        Returns:
            Semantic text string
        """
        instruction = seq.get("instruction", "")
        app_context = seq.get("app_context", "")
        action_structure = seq.get("action_structure", [])
        actions_str = " -> ".join(action_structure) if action_structure else "无"

        # Build semantic text that captures the full sequence meaning
        return f"在{app_context}中{instruction}，执行动作序列：{actions_str}"

    def _create_sequence_cluster(self, sequences: List[Dict]) -> Dict:
        """
        Create a cluster dict from a list of similar sequences.

        Args:
            sequences: List of similar sequence dicts

        Returns:
            Cluster dict
        """
        cluster_id = f"cluster_seq_llm_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()

        print(f"[LLMClusterEngine._create_sequence_cluster] Creating cluster for {len(sequences)} sequences...")

        # Extract instructions for pattern
        instructions = [seq.get("instruction", "") for seq in sequences]
        print(f"[LLMClusterEngine._create_sequence_cluster] Extracting pattern from {len(instructions)} instructions...")

        # Use heuristic pattern extraction (fast)
        instruction_pattern = self._extract_pattern(instructions)
        print(f"[LLMClusterEngine._create_sequence_cluster] Pattern: {instruction_pattern[:50]}...")

        # Get the most common action structure
        structure_counts = {}
        for seq in sequences:
            struct = tuple(seq.get("action_structure", []))
            structure_counts[struct] = structure_counts.get(struct, 0) + 1

        most_common_structure = max(structure_counts.items(), key=lambda x: x[1])[0] if structure_counts else ()

        # Get most common app context
        app_counts = {}
        for seq in sequences:
            app = seq.get("app_context", "")
            if app:
                app_counts[app] = app_counts.get(app, 0) + 1

        most_common_app = max(app_counts.items(), key=lambda x: x[1])[0] if app_counts else ""

        # Collect sample actions from all sequences
        sample_actions = []
        for seq in sequences[:3]:  # First 3 sequences
            sample_actions.extend(seq.get("actions", []))
        sample_actions = sample_actions[:15]  # Limit

        print(f"[LLMClusterEngine._create_sequence_cluster] Cluster created: {cluster_id}")

        return {
            "cluster_id": cluster_id,
            "cluster_type": "sequence_llm",  # Mark as LLM-based sequence cluster
            "pattern": {
                "instruction_pattern": instruction_pattern,
                "app_context": most_common_app,
                "action_structure": list(most_common_structure),
            },
            "members": [seq.get("sequence_id") for seq in sequences],
            "sequence_ids": [seq.get("sequence_id") for seq in sequences],
            "sample_instructions": instructions[:5],
            "sample_actions": sample_actions,
            "sample_sequences": [
                {
                    "sequence_id": seq.get("sequence_id"),
                    "instruction": seq.get("instruction"),
                    "instruction_hash": seq.get("instruction_hash"),
                    "actions": seq.get("actions", []),
                    "action_structure": seq.get("action_structure", []),
                }
                for seq in sequences[:3]
            ],
            "count": len(sequences),
            "status": "candidate",
            "created_at": now,
            "updated_at": now,
        }

    def _validate_sequence_cluster(self, sequences: List[Dict]) -> List[Dict]:
        """
        Validate a sequence cluster to ensure sequences are truly related.

        DBSCAN may cluster sequences with similar semantics but different intents.
        This method validates by checking:
        1. Action structure consistency
        2. Instruction structure similarity (prefix-based grouping)

        Note: We do NOT require same instruction_hash because the purpose of
        semantic clustering is to group sequences with similar intent but
        slightly different text (e.g., "输入网址访问百度" vs "输入网址访问Google").

        Args:
            sequences: List of sequence dicts from DBSCAN clustering

        Returns:
            List of validated cluster dicts
        """
        if not sequences:
            return []

        print(f"[LLMClusterEngine._validate] Validating cluster with {len(sequences)} sequences")

        # Step 1: Group by action structure
        structure_groups: Dict[tuple, List[Dict]] = {}
        for seq in sequences:
            struct = tuple(seq.get("action_structure", []))
            if struct not in structure_groups:
                structure_groups[struct] = []
            structure_groups[struct].append(seq)

        print(f"[LLMClusterEngine._validate] Found {len(structure_groups)} action structures: {list(structure_groups.keys())}")

        # Step 2: For each action structure group, further split by instruction structure
        clusters = []
        for struct, struct_sequences in structure_groups.items():
            if len(struct_sequences) < self.min_cluster_size:
                print(f"[LLMClusterEngine._validate] Skipping structure {struct}: count={len(struct_sequences)} < min_cluster_size")
                continue

            # Extract instruction prefixes to identify different operation types
            prefix_groups = self._group_by_instruction_prefix(struct_sequences)
            print(f"[LLMClusterEngine._validate] Structure {struct} split into {len(prefix_groups)} instruction prefix groups")

            for prefix, prefix_sequences in prefix_groups.items():
                if len(prefix_sequences) >= self.min_cluster_size:
                    print(f"[LLMClusterEngine._validate] Creating cluster for prefix '{prefix[:30]}...' with {len(prefix_sequences)} sequences")
                    cluster = self._create_sequence_cluster(prefix_sequences)
                    clusters.append(cluster)
                else:
                    print(f"[LLMClusterEngine._validate] Skipping prefix '{prefix[:30]}...': count={len(prefix_sequences)} < min_cluster_size")

        return clusters

    def _group_by_instruction_prefix(self, sequences: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group sequences by their instruction prefix (structure template).

        Two-stage verification:
        1. Extract individual prefixes and group
        2. Merge prefix groups with semantically similar prefixes

        This handles cases like:
        - "打击/侦察目标一栏输入xxx" (prefix: "打击/侦察目标一栏输入")
        - "打击/侦察目标中输入xxx" (prefix: "打击/侦察目标中输入")
        → Prefix similarity > 0.75 → merged into one group

        But keeps separate:
        - "任务名称一栏输入xxx" vs "打击/侦察目标一栏输入xxx"
        → Prefix similarity < 0.75 → separate groups

        Args:
            sequences: List of sequences with same action structure

        Returns:
            Dict mapping prefix to list of sequences
        """
        # Step 1: Extract individual prefixes and initial grouping
        initial_groups: Dict[str, List[Dict]] = {}

        for seq in sequences:
            instr = seq.get("instruction", "")
            individual_prefix = self._extract_instruction_prefix(instr)

            if individual_prefix not in initial_groups:
                initial_groups[individual_prefix] = []
            initial_groups[individual_prefix].append(seq)

        # If only one group, return directly
        if len(initial_groups) <= 1:
            return initial_groups

        # Step 2: Merge prefix groups with semantically similar prefixes
        merged_groups = self._merge_similar_prefix_groups(initial_groups)

        return merged_groups

    def _merge_similar_prefix_groups(
        self,
        prefix_groups: Dict[str, List[Dict]],
        threshold: float = 0.75
    ) -> Dict[str, List[Dict]]:
        """
        Merge prefix groups with semantically similar prefixes.

        Args:
            prefix_groups: Dict mapping prefix to list of sequences
            threshold: Minimum similarity to merge (default 0.75)

        Returns:
            Dict with merged prefix groups
        """
        prefixes = list(prefix_groups.keys())

        if len(prefixes) <= 1:
            return prefix_groups

        # Calculate pairwise prefix similarities
        print(f"[LLMClusterEngine._merge_prefixes] Checking similarity for {len(prefixes)} prefixes")

        # Find which prefixes should be merged
        merge_pairs = []
        for i, prefix1 in enumerate(prefixes):
            for j, prefix2 in enumerate(prefixes[i+1:], i+1):
                sim = self.embedding_model.similarity(prefix1, prefix2)
                print(f"  '{prefix1[:20]}...' vs '{prefix2[:20]}...': sim={sim:.4f}")

                if sim >= threshold:
                    merge_pairs.append((prefix1, prefix2, sim))

        # If no merges needed, return original
        if not merge_pairs:
            print(f"[LLMClusterEngine._merge_prefixes] No similar prefixes found, keeping {len(prefixes)} groups")
            return prefix_groups

        # Build merge graph and find connected components
        # Use Union-Find to group prefixes
        parent = {p: p for p in prefixes}

        def find(x):
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Union all similar prefixes
        for p1, p2, sim in merge_pairs:
            print(f"[LLMClusterEngine._merge_prefixes] Merging '{p1[:20]}...' and '{p2[:20]}...' (sim={sim:.4f})")
            union(p1, p2)

        # Group by root
        merged: Dict[str, List[Dict]] = {}
        for prefix, sequences in prefix_groups.items():
            root = find(prefix)
            if root not in merged:
                merged[root] = []
            merged[root].extend(sequences)

        print(f"[LLMClusterEngine._merge_prefixes] Merged {len(prefixes)} prefixes into {len(merged)} groups")
        return merged

    def _extract_instruction_prefix(self, instruction: str) -> str:
        """
        Extract the structural prefix from an instruction.

        Uses the last action verb position to determine the prefix boundary.
        This keeps the natural structure of the instruction.

        Examples:
            "打击/侦察目标一栏输入台北市政府" → "打击/侦察目标一栏输入"
            "任务名称一栏输入打击任务-C1" → "任务名称一栏输入"
            "输入http://localhost:3000/" → "输入"

        Args:
            instruction: Full instruction text

        Returns:
            Structural prefix
        """
        # Common action verbs that indicate the end of the prefix
        action_verbs = ["输入", "点击", "选择", "打开", "关闭", "确认", "取消", "执行", "设为", "填写"]

        # Find the last action verb position
        last_verb_pos = -1
        for verb in action_verbs:
            pos = instruction.rfind(verb)
            if pos > last_verb_pos:
                last_verb_pos = pos + len(verb)

        if last_verb_pos > 0:
            return instruction[:last_verb_pos]
        else:
            # Fallback: use the first half or first 20 chars
            return instruction[:max(len(instruction) // 2, 20)]
