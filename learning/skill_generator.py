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
    """

    def __init__(self, rules_dir: str = "rules"):
        """
        Initialize the skill generator.

        Args:
            rules_dir: Directory to store generated skills
        """
        self.rules_dir = rules_dir
        self.learned_skills_file = os.path.join(rules_dir, "learned_skills.json")

        # Ensure rules directory exists
        os.makedirs(rules_dir, exist_ok=True)

    def generate_skill(self, cluster: dict) -> dict:
        """
        Generate a skill rule from an approved cluster.

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

        # Generate name from pattern
        app_context = pattern.get("app_context", "")
        action_structure = pattern.get("action_structure", [])
        name = self._generate_name(app_context, action_structure)

        # Generate trigger patterns
        trigger_patterns = self._generate_trigger_patterns(sample_instructions)

        # Generate actions
        actions = self._generate_actions(sample_actions)

        # Build skill rule
        skill = {
            "id": skill_id,
            "name": name,
            "description": f"Auto-learned skill from {cluster.get('count', 0)} operations",
            "source": "learned",
            "cluster_id": cluster.get("cluster_id"),
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

    def _generate_name(self, app_context: str, action_structure: list) -> str:
        """Generate a descriptive name for the skill."""
        # Build name from action structure
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

        # Get action summary
        action_summary = []
        for action_type in action_structure[:3]:  # Limit to first 3
            name = action_names.get(action_type, action_type)
            if name not in action_summary:
                action_summary.append(name)

        # Build name
        if app_context:
            app_name = app_context.split("-")[0].strip()[:10]
            return f"{app_name}_{'_'.join(action_summary)}"
        else:
            return f"自动技能_{'_'.join(action_summary)}"

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
        if not instruction:
            return ".*"

        # Escape special regex characters
        pattern = re.escape(instruction)

        # Try to identify variable parts (numbers, specific names, etc.)
        # Replace numbers with \d+ pattern
        pattern = re.sub(r"\\d+", r"\\d+", pattern)

        # Try to identify quoted strings and make them variable
        pattern = re.sub(r'"[^"]*"', r'".*?"', pattern)
        pattern = re.sub(r"'[^']*'", r"'.*?'", pattern)

        # Make the pattern more flexible
        # Allow extra whitespace
        pattern = pattern.replace("\\ ", r"\s*")

        return pattern

    def _generate_actions(self, sample_actions: list) -> list:
        """
        Generate action sequence from sample actions.

        This tries to generalize coordinates and extract parameters.

        Args:
            sample_actions: List of sample actions

        Returns:
            List of generalized actions
        """
        if not sample_actions:
            return []

        actions = []

        for action in sample_actions:
            action_type = action.get("type") or action.get("action", "")

            generalized = {"type": action_type}

            # Handle different action types
            if action_type in ("click", "left_click", "right_click", "double_click",
                               "middle_click"):
                # Keep coordinates (they're specific to the screen)
                # In future: could be made relative to window
                if "coordinate" in action:
                    generalized["coordinate"] = action["coordinate"]

            elif action_type == "type":
                # For typing actions, the text is often variable
                # Keep as parameter
                if "text" in action:
                    text = action["text"]
                    # If text is short, it might be a fixed value
                    # If longer, it's probably variable
                    if len(text) < 10:
                        generalized["text"] = text
                    else:
                        # Mark as parameter slot
                        generalized["text"] = "{{text_param}}"
                        generalized["param_name"] = "text_param"

            elif action_type in ("hotkey", "key"):
                if "keys" in action:
                    generalized["keys"] = action["keys"]

            elif action_type == "scroll":
                if "pixels" in action:
                    generalized["pixels"] = action["pixels"]

            elif action_type == "drag":
                if "coordinate" in action:
                    generalized["coordinate"] = action["coordinate"]

            else:
                # Copy all parameters for unknown types
                for key, value in action.items():
                    if key not in ("type", "action"):
                        generalized[key] = value

            actions.append(generalized)

        return actions

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
        Save a skill to the learned skills file.

        Args:
            skill: The skill rule dict

        Returns:
            True if saved successfully
        """
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
        Delete a skill from the learned skills file.

        Args:
            skill_id: The skill ID to delete

        Returns:
            True if deleted successfully
        """
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

    def list_skills(self) -> list:
        """
        List all learned skills.

        Returns:
            List of skill dicts
        """
        try:
            if not os.path.exists(self.learned_skills_file):
                return []

            with open(self.learned_skills_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return data.get("rules", [])

        except Exception as e:
            print(f"[SkillGenerator] Error listing skills: {e}")
            return []