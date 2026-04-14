"""
Skill Generator - Generates reusable skill rules from operation clusters.

This module takes approved clusters and generates skill rules that can be
used by the Fast Path matching engine.
"""

import os
import json
import uuid
import re
from datetime import datetime
from typing import Any, Optional


class SkillGenerator:
    """
    Generates skill rules from operation clusters.

    Features:
    - Extracts action patterns from clustered operations
    - Generalizes coordinates (relative positioning)
    - Generates trigger patterns from instructions
    - Creates parameter slots for variable parts
    - Supports both SQLite and JSON storage
    """

    def __init__(self, rules_dir: str = "rules", use_sqlite: bool = True):
        """
        Initialize the skill generator.

        Args:
            rules_dir: Directory to store generated skills (for JSON mode)
            use_sqlite: If True, use SQLite storage; if False, use JSON file
        """
        self.rules_dir = rules_dir
        self.use_sqlite = use_sqlite

        if use_sqlite:
            from learning.skill_store import SkillStore
            self.store = SkillStore()
            self.learned_skills_file = None
        else:
            self.learned_skills_file = os.path.join(rules_dir, "learned_skills.json")
            self.store = None
            # Ensure rules directory exists
            os.makedirs(rules_dir, exist_ok=True)

    def generate_skill(self, cluster: dict) -> dict:
        """
        Generate a skill rule from an approved cluster.

        Automatically detects cluster type and uses appropriate generation:
        - Sequence clusters (any type containing 'sequence'): generate_sequence_skill()
        - Operation clusters: generate_operation_skill()

        Args:
            cluster: The approved cluster dict

        Returns:
            Generated skill rule dict
        """
        cluster_type = cluster.get("cluster_type", "single")

        # Check if this is a sequence cluster (including 'sequence_llm', 'sequence', etc.)
        is_sequence = "sequence" in cluster_type.lower()

        if is_sequence:
            return self.generate_sequence_skill(cluster)
        else:
            return self.generate_operation_skill(cluster)

    def generate_operation_skill(self, cluster: dict) -> dict:
        """
        Generate a skill rule from an operation cluster (original behavior).

        Args:
            cluster: The approved cluster dict

        Returns:
            Generated skill rule dict
        """
        pattern = cluster.get("pattern", {})
        sample_actions = cluster.get("sample_actions", [])
        sample_instructions = cluster.get("sample_instructions", [])

        # Generate unique skill ID
        skill_id = f"learned_{uuid.uuid4().hex[:8]}"

        # Generate name from instruction
        app_context = pattern.get("app_context", "")
        action_structure = pattern.get("action_structure", [])
        name = self._generate_name(app_context, action_structure, sample_instructions)

        # Generate trigger patterns
        trigger_patterns = self._generate_trigger_patterns(sample_instructions)

        # For single-operation clusters, only keep ONE representative action
        # Each operation in the cluster has the same action structure
        # Use the first action as template, optionally average coordinates
        actions = self._generate_single_operation_action(sample_actions)

        # Build skill rule
        skill = {
            "id": skill_id,
            "name": name,
            "description": f"Auto-learned skill from {cluster.get('count', 0)} operations",
            "source": "learned",
            "cluster_id": cluster.get("cluster_id"),
            "cluster_type": "single",  # Single-operation skill
            "created_at": datetime.now().isoformat(),
            "trigger": {
                "patterns": trigger_patterns,
            },
            "actions": actions,
            "enabled": True,
            "confidence": self._calculate_confidence(cluster),
        }

        # Add app context if available
        if app_context:
            skill["trigger"]["app_context"] = [app_context]

        return skill

    def generate_sequence_skill(self, cluster: dict) -> dict:
        """
        Generate a skill rule from a sequence cluster.

        Sequence skills preserve the full operation sequence (e.g., click + type)
        and extract parameters from variable parts.

        Args:
            cluster: The approved sequence cluster dict

        Returns:
            Generated skill rule dict
        """
        from learning.similarity import extract_pattern_with_prefix_heuristic

        pattern = cluster.get("pattern", {})
        sample_instructions = cluster.get("sample_instructions", [])
        sample_sequences = cluster.get("sample_sequences", [])
        action_structure = pattern.get("action_structure", [])

        # Generate unique skill ID
        skill_id = f"learned_{uuid.uuid4().hex[:8]}"

        # Generate name from instruction
        app_context = pattern.get("app_context", "")
        name = self._generate_sequence_name(app_context, action_structure, sample_instructions)

        # Generate trigger patterns with better extraction
        pattern_info = extract_pattern_with_prefix_heuristic(sample_instructions)
        trigger_patterns = [pattern_info.get("regex_pattern", ".*")]

        # Generate actions from sample sequences
        actions, parameters = self._generate_sequence_actions(
            sample_sequences,
            pattern_info.get("variable_examples", [])
        )

        # Build skill rule
        skill = {
            "id": skill_id,
            "name": name,
            "description": f"Auto-learned sequence skill from {cluster.get('count', 0)} executions",
            "source": "learned",
            "cluster_id": cluster.get("cluster_id"),
            "cluster_type": "sequence",
            "created_at": datetime.now().isoformat(),
            "trigger": {
                "patterns": trigger_patterns,
            },
            "actions": actions,
            "parameters": parameters if parameters else None,
            "enabled": True,
            "confidence": self._calculate_confidence(cluster),
        }

        # Remove None parameters
        if skill["parameters"] is None:
            del skill["parameters"]

        # Add app context if available
        if app_context:
            skill["trigger"]["app_context"] = [app_context]

        return skill

    def _generate_sequence_name(self, app_context: str, action_structure: list, sample_instructions: list = None) -> str:
        """
        Generate a descriptive name for a sequence skill.

        Priority:
        1. Extract from user instruction (shows the overall task goal)
        2. Fallback to action sequence description

        Args:
            app_context: Application context
            action_structure: List of action types in sequence
            sample_instructions: List of user instructions (preferred source)

        Returns:
            Descriptive skill name
        """
        # Try to extract from user instruction first
        if sample_instructions and len(sample_instructions) > 0:
            extracted_name = self._extract_name_from_instruction(sample_instructions[0])
            if extracted_name:
                return extracted_name

        # Fallback: build from action sequence
        action_names = {
            "click": "点击",
            "left_click": "点击",
            "right_click": "右键",
            "double_click": "双击",
            "type": "输入",
            "hotkey": "快捷键",
            "key": "按键",
            "scroll": "滚动",
            "drag": "拖拽",
        }

        action_summary = []
        for action_type in action_structure[:4]:
            name = action_names.get(action_type, action_type)
            if name not in action_summary:
                action_summary.append(name)

        # Build name
        if app_context:
            app_name = app_context.split("-")[0].strip()[:10]
            return f"{app_name}_{'_'.join(action_summary)}"
        else:
            return f"序列技能_{'_'.join(action_summary)}"

    def _generate_sequence_actions(
        self,
        sample_sequences: list[dict],
        variable_examples: list[str]
    ) -> tuple[list[dict], list[dict]]:
        """
        Generate action sequence from sample sequences.

        Identifies variable parts and creates parameter slots.

        Args:
            sample_sequences: List of sample sequence dicts
            variable_examples: Examples of variable text parts

        Returns:
            Tuple of (actions list, parameters list)
        """
        if not sample_sequences:
            return [], []

        # Use first sequence as template
        template = sample_sequences[0]
        actions = template.get("actions", [])

        # Detect if there's a type action that needs parameterization
        parameters = []
        param_index = 0

        generalized_actions = []
        for action in actions:
            action_type = action.get("type") or action.get("action", "")
            generalized = {"type": action_type}

            if action_type in ("click", "left_click", "right_click", "double_click", "middle_click"):
                # Keep coordinates, but could average across samples
                if "coordinate" in action:
                    generalized["coordinate"] = self._average_coordinates(
                        sample_sequences,
                        len(generalized_actions)
                    )
                    generalized["coordinate_normalized"] = True  # Mark as normalized (0-1000)

            elif action_type == "type":
                # Check if type text is variable or fixed across samples
                if "text" in action:
                    # Collect all type texts from same position across samples
                    texts = []
                    for seq in sample_sequences:
                        seq_actions = seq.get("actions", [])
                        if len(generalized_actions) < len(seq_actions):
                            seq_action = seq_actions[len(generalized_actions)]
                            if (seq_action.get("type") or seq_action.get("action")) == "type":
                                if "text" in seq_action:
                                    texts.append(seq_action["text"])

                    # Determine if text is fixed or variable
                    unique_texts = list(set(texts))
                    if len(unique_texts) == 1 and not variable_examples:
                        # All same, use fixed value
                        generalized["text"] = unique_texts[0]
                    else:
                        # Variable, use parameter
                        generalized["text"] = "{{match_group_1}}"
                        generalized["param_name"] = "content"

                        # Add to parameters (metadata, not used in execution)
                        parameters.append({
                            "name": "content",
                            "type": "string",
                            "required": True,
                            "examples": variable_examples[:3] if variable_examples else unique_texts[:3]
                        })

            elif action_type in ("hotkey", "key"):
                if "keys" in action:
                    generalized["keys"] = action["keys"]

            elif action_type == "scroll":
                if "pixels" in action:
                    generalized["pixels"] = action["pixels"]

            else:
                # Copy all parameters
                for key, value in action.items():
                    if key not in ("type", "action"):
                        generalized[key] = value

            generalized_actions.append(generalized)

        return generalized_actions, parameters

    def _average_coordinates(self, sequences: list[dict], action_index: int) -> list:
        """
        Calculate average coordinates across sequences for an action.

        Args:
            sequences: List of sample sequences
            action_index: Index of the action to average

        Returns:
            Average coordinate [x, y]
        """
        coords = []
        for seq in sequences:
            actions = seq.get("actions", [])
            if action_index < len(actions):
                coord = actions[action_index].get("coordinate")
                if coord and len(coord) == 2:
                    coords.append(coord)

        if not coords:
            return [0, 0]

        avg_x = sum(c[0] for c in coords) // len(coords)
        avg_y = sum(c[1] for c in coords) // len(coords)

        return [avg_x, avg_y]

    def _generate_name(self, app_context: str, action_structure: list, sample_instructions: list = None) -> str:
        """
        Generate a descriptive name for the skill.

        Priority:
        1. Extract from user instruction (most intuitive)
        2. Fallback to action structure + app context

        Args:
            app_context: Application context (e.g., "Edge")
            action_structure: List of action types (e.g., ["click"])
            sample_instructions: List of user instructions (preferred source)

        Returns:
            Descriptive skill name
        """
        # Try to extract from user instruction first
        if sample_instructions and len(sample_instructions) > 0:
            extracted_name = self._extract_name_from_instruction(sample_instructions[0])
            if extracted_name:
                return extracted_name

        # Fallback: build from action structure
        action_names = {
            "click": "点击",
            "left_click": "点击",
            "right_click": "右键点击",
            "double_click": "双击",
            "type": "输入",
            "hotkey": "快捷键",
            "scroll": "滚动",
            "drag": "拖拽",
        }

        action_summary = []
        for action_type in action_structure[:3]:
            name = action_names.get(action_type, action_type)
            if name not in action_summary:
                action_summary.append(name)

        if app_context:
            app_name = app_context.split("-")[0].strip()[:10]
            return f"{app_name}_{'_'.join(action_summary)}"
        else:
            return f"自动技能_{'_'.join(action_summary)}"

    def _extract_name_from_instruction(self, instruction: str) -> str:
        """
        Extract a concise, meaningful name from user instruction.

        Examples:
            "双击打开Edge浏览器" → "打开Edge浏览器"
            "在任务名称一栏输入侦察任务" → "任务名称栏输入"
            "点击地图右侧位置执行指派" → "地图点击指派"
            "按Ctrl+S保存" → "保存文件"

        Args:
            instruction: User instruction text

        Returns:
            Extracted name (max 12 chars), or empty if failed
        """
        if not instruction or len(instruction.strip()) == 0:
            return ""

        text = instruction.strip()

        # Remove common prefix modifiers (noise words)
        prefixes_to_remove = [
            "然后", "接着", "请", "帮我", "帮忙", "让", "需要",
            "随机", "任意", "随便", "适当", "合适"
        ]
        for prefix in prefixes_to_remove:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Remove "在...上/里" pattern (keep the location but simplify)
        # "在任务名称一栏输入" → "任务名称栏输入"
        import re
        text = re.sub(r"^在(.+?)(上|里|中|一栏|区域)", lambda m: m.group(1).replace("一栏", "栏"), text)

        # Key verbs to anchor extraction
        key_verbs = [
            "打开", "关闭", "保存", "删除", "复制", "粘贴", "刷新",
            "输入", "填写", "搜索", "查找", "选择", "点击", "双击",
            "右键", "拖拽", "滚动", "按下", "按", "执行", "确认", "取消",
            "新建", "创建", "编辑", "修改", "发送", "提交", "上传", "下载"
        ]

        # Find key verb position
        verb_pos = -1
        found_verb = ""
        for verb in key_verbs:
            pos = text.find(verb)
            if pos >= 0 and (verb_pos < 0 or pos < verb_pos):
                verb_pos = pos
                found_verb = verb

        # If verb found, extract from verb onwards (include verb + object)
        if verb_pos >= 0:
            # Get text starting from verb
            extracted = text[verb_pos:]

            # Limit length (keep it concise)
            if len(extracted) <= 12:
                return extracted
            else:
                # Try to truncate at natural boundary (space, punctuation)
                # or just keep first 12 chars
                return extracted[:12]

        # No key verb found - use whole text (limited)
        if len(text) <= 12:
            return text
        else:
            return text[:12]

    def _generate_trigger_patterns(self, sample_instructions: list) -> list:
        """
        Generate regex patterns from sample instructions.

        Args:
            sample_instructions: List of similar instructions

        Returns:
            List of regex patterns
        """
        if not sample_instructions:
            return [".*"]

        patterns = []

        for instr in sample_instructions[:3]:  # Use first 3 samples
            # Convert to pattern
            pattern = self._instruction_to_pattern(instr)
            if pattern and pattern not in patterns:
                patterns.append(pattern)

        # If no valid patterns, use a generic one
        if not patterns:
            patterns = [".*"]

        return patterns

    def _instruction_to_pattern(self, instruction: str) -> str:
        """
        Convert an instruction to a regex pattern.

        This is a heuristic approach that tries to identify
        variable parts and create capture groups.
        """
        from learning.similarity import _escape_for_regex

        if not instruction:
            return ".*"

        # Escape special regex characters (but NOT spaces)
        pattern = _escape_for_regex(instruction)

        # Try to identify variable parts (numbers, specific names, etc.)
        # Replace numbers with \d+ pattern
        pattern = re.sub(r"\d+", r"\\d+", pattern)

        # Try to identify quoted strings and make them variable
        pattern = re.sub(r'"[^"]*"', r'".*?"', pattern)
        pattern = re.sub(r"'[^']*'", r"'.*?'", pattern)

        # Make the pattern more flexible - allow extra whitespace
        # Replace actual space characters with \s*
        pattern = pattern.replace(" ", r"\s*")

        return pattern

    def _generate_single_operation_action(self, sample_actions: list) -> list:
        """
        Generate ONE representative action for single-operation clusters.

        For single-operation clusters, each operation has the same action structure.
        We only need ONE action, but can average coordinates across all samples.

        Coordinates are stored as normalized (0-1000 range) for resolution independence.

        Args:
            sample_actions: List of sample actions (from multiple operations)

        Returns:
            List with single representative action
        """
        if not sample_actions:
            return []

        # Take first action as template
        template_action = sample_actions[0]
        action_type = template_action.get("type") or template_action.get("action", "")

        result = {"type": action_type}

        # For click actions, average the coordinates across all samples
        if action_type in ("click", "left_click", "right_click", "double_click", "middle_click"):
            # Collect all coordinates from same action type
            coords = []
            for action in sample_actions:
                if action.get("type") == action_type or action.get("action") == action_type:
                    if "coordinate" in action:
                        coords.append(action["coordinate"])

            if coords:
                # Average coordinates (already normalized 0-1000)
                avg_x = sum(c[0] for c in coords) / len(coords)
                avg_y = sum(c[1] for c in coords) / len(coords)
                result["coordinate"] = [int(avg_x), int(avg_y)]
                result["coordinate_normalized"] = True  # Mark as normalized (0-1000)
            elif "coordinate" in template_action:
                result["coordinate"] = template_action["coordinate"]
                result["coordinate_normalized"] = True

        elif action_type == "type":
            # For typing, the text is likely variable - use parameter
            if "text" in template_action:
                text = template_action["text"]
                if len(text) < 10:
                    result["text"] = text
                else:
                    result["text"] = "{{text_param}}"
                    result["param_name"] = "text_param"

        elif action_type in ("hotkey", "key"):
            if "keys" in template_action:
                result["keys"] = template_action["keys"]

        elif action_type == "scroll":
            if "pixels" in template_action:
                result["pixels"] = template_action["pixels"]

        else:
            # Copy all parameters from template
            for key, value in template_action.items():
                if key not in ("type", "action"):
                    result[key] = value

        return [result]

    def _calculate_confidence(self, cluster: dict) -> float:
        """
        Calculate confidence score for the generated skill.

        Higher count and more consistent patterns = higher confidence.

        Args:
            cluster: The cluster dict

        Returns:
            Confidence score between 0 and 1
        """
        count = cluster.get("count", 0)

        # Base confidence from count
        # 3 operations = 0.5, 10+ = 1.0
        count_score = min(count / 10, 1.0) * 0.5 + 0.5

        return round(count_score, 2)

    def save_skill(self, skill: dict) -> bool:
        """
        Save a skill to storage.

        Args:
            skill: The skill rule dict

        Returns:
            True if saved successfully
        """
        if self.use_sqlite:
            return self.store.save(skill)

        # JSON mode (original behavior)
        try:
            # Load existing skills
            data = {"version": "1.0", "rules": []}
            if os.path.exists(self.learned_skills_file):
                with open(self.learned_skills_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

            # Check for duplicate ID
            existing_ids = [r.get("id") for r in data.get("rules", [])]
            if skill.get("id") in existing_ids:
                print(f"[SkillGenerator] Skill already exists: {skill.get('id')}")
                return False

            # Add new skill
            data.setdefault("rules", []).append(skill)

            # Save
            with open(self.learned_skills_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"[SkillGenerator] Saved skill: {skill.get('id')} - {skill.get('name')}")
            return True

        except Exception as e:
            print(f"[SkillGenerator] Error saving skill: {e}")
            return False

    def delete_skill(self, skill_id: str) -> bool:
        """
        Delete a skill from storage.

        Args:
            skill_id: The skill ID to delete

        Returns:
            True if deleted successfully
        """
        if self.use_sqlite:
            return self.store.delete(skill_id)

        # JSON mode (original behavior)
        try:
            if not os.path.exists(self.learned_skills_file):
                return False

            with open(self.learned_skills_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            original_count = len(data.get("rules", []))
            data["rules"] = [r for r in data.get("rules", []) if r.get("id") != skill_id]

            if len(data["rules"]) == original_count:
                return False  # Not found

            with open(self.learned_skills_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"[SkillGenerator] Deleted skill: {skill_id}")
            return True

        except Exception as e:
            print(f"[SkillGenerator] Error deleting skill: {e}")
            return False

    def get_skill(self, skill_id: str) -> Optional[dict]:
        """
        Get a skill by ID.

        Args:
            skill_id: The skill ID

        Returns:
            Skill dict or None if not found
        """
        if self.use_sqlite:
            return self.store.get(skill_id)

        # JSON mode
        skills = self.list_skills()
        for skill in skills:
            if skill.get("id") == skill_id:
                return skill
        return None

    def list_skills(self, cluster_type: str = None) -> list:
        """
        List all learned skills.

        Args:
            cluster_type: Filter by 'single' or 'sequence' (None for all)

        Returns:
            List of skill dicts
        """
        if self.use_sqlite:
            return self.store.list_all(cluster_type=cluster_type)

        # JSON mode (original behavior)
        try:
            if not os.path.exists(self.learned_skills_file):
                return []

            with open(self.learned_skills_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            skills = data.get("rules", [])

            # Filter by cluster_type if specified
            if cluster_type:
                skills = [s for s in skills if s.get("cluster_type", "single") == cluster_type]

            return skills

        except Exception as e:
            print(f"[SkillGenerator] Error listing skills: {e}")
            return []

    def match_skill(self, instruction: str, app: str = None) -> Optional[dict]:
        """
        Find a matching skill for execution.

        Args:
            instruction: User instruction text
            app: Current app context (window title)

        Returns:
            Matching skill dict or None
        """
        if self.use_sqlite:
            return self.store.match(instruction, app)

        # JSON mode - manual matching
        skills = self.list_skills()
        enabled_skills = [s for s in skills if s.get("enabled", True)]

        for skill in enabled_skills:
            trigger = skill.get("trigger", {})
            app_contexts = trigger.get("app_context", [])

            # Check app context first
            if app and app_contexts:
                matched = False
                for ctx in app_contexts:
                    if ctx and ctx in app:
                        matched = True
                        break
                if not matched:
                    continue

            # Check pattern match
            patterns = trigger.get("patterns", [])
            for pattern in patterns:
                try:
                    import re
                    if re.search(pattern, instruction):
                        return skill
                except re.error:
                    continue

        return None

    def update_skill_enabled(self, skill_id: str, enabled: bool) -> bool:
        """
        Update skill enabled status.

        Args:
            skill_id: Skill ID
            enabled: New enabled status

        Returns:
            True if updated successfully
        """
        if self.use_sqlite:
            return self.store.update_enabled(skill_id, enabled)

        # JSON mode - load, modify, save
        try:
            if not os.path.exists(self.learned_skills_file):
                return False

            with open(self.learned_skills_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Find and update skill
            for skill in data.get("rules", []):
                if skill.get("id") == skill_id:
                    skill["enabled"] = enabled
                    skill["updated_at"] = datetime.now().isoformat()

                    with open(self.learned_skills_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                    status = "enabled" if enabled else "disabled"
                    print(f"[SkillGenerator] {status} skill: {skill_id}")
                    return True

            return False  # Not found

        except Exception as e:
            print(f"[SkillGenerator] Error updating skill: {e}")
            return False

    def migrate_from_json(self, json_path: str = "rules/learned_skills.json") -> int:
        """
        Migrate skills from JSON file to SQLite.

        Only works in SQLite mode.

        Args:
            json_path: Path to learned_skills.json

        Returns:
            Number of skills migrated
        """
        if not self.use_sqlite:
            print("[SkillGenerator] migrate_from_json only works in SQLite mode")
            return 0

        return self.store.migrate_from_json(json_path)

    def export_to_json(self, json_path: str = "rules/learned_skills.json") -> bool:
        """
        Export skills to JSON file (for backup).

        Only works in SQLite mode.

        Args:
            json_path: Output JSON file path

        Returns:
            True if exported successfully
        """
        if not self.use_sqlite:
            print("[SkillGenerator] export_to_json only works in SQLite mode")
            return False

        return self.store.export_to_json(json_path)

    def get_stats(self) -> dict:
        """
        Get statistics about stored skills.

        Returns:
            Dict with skill statistics
        """
        if self.use_sqlite:
            return self.store.get_stats()

        # JSON mode - manual counting
        skills = self.list_skills()
        return {
            "total": len(skills),
            "enabled": sum(1 for s in skills if s.get("enabled", True)),
            "disabled": sum(1 for s in skills if not s.get("enabled", True)),
            "single_skills": sum(1 for s in skills if s.get("cluster_type", "single") == "single"),
            "sequence_skills": sum(1 for s in skills if s.get("cluster_type") == "sequence"),
        }