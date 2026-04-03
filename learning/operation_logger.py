"""
Operation Logger - Records VLM operations for skill learning.

This module automatically logs successful VLM operations to enable
the system to learn and generate reusable skill rules.

Usage:
    from learning import OperationLogger

    logger = OperationLogger()
    logger.log(state, actions, success=True)
"""

import os
import json
import uuid
import hashlib
from datetime import datetime
from typing import Any, Optional


class OperationLogger:
    """
    Records VLM operations to JSONL files for later analysis and skill learning.

    Features:
    - Automatic logging of successful VLM operations
    - Captures instruction, context, actions, and results
    - Appends to JSONL file for efficient streaming
    """

    def __init__(self, log_dir: str = "data/logs"):
        """
        Initialize the operation logger.

        Args:
            log_dir: Directory to store log files
        """
        self.log_dir = log_dir
        self.log_file = os.path.join(log_dir, "operation_logs.jsonl")

        # Ensure directory exists
        os.makedirs(log_dir, exist_ok=True)

    def log(
        self,
        instruction: str,
        actions: list[dict],
        success: bool,
        source: str = "vlm",
        task_name: str = "",
        step_id: int = 0,
        app_context: Optional[dict] = None,
        screenshot_path: str = "",
    ) -> str:
        """
        Log a single operation.

        Args:
            instruction: The user instruction that triggered this operation
            actions: List of executed actions
            success: Whether the operation succeeded
            source: Source of the action ("vlm" or "fast_path")
            task_name: Name of the task
            step_id: Step number in the task
            app_context: Application context (window title, process name, etc.)
            screenshot_path: Path to the screenshot (optional)

        Returns:
            log_id: UUID of the logged entry
        """
        log_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        # Generate instruction hash for quick comparison
        instruction_hash = hashlib.md5(instruction.encode()).hexdigest()[:8]

        # Extract action structure (sequence of action types)
        action_structure = self._extract_action_structure(actions)

        # Build the log entry
        entry = {
            "log_id": log_id,
            "timestamp": timestamp,
            # Instruction info
            "instruction": instruction,
            "instruction_hash": instruction_hash,
            "task_name": task_name,
            # Context
            "app_context": app_context or {},
            # Actions
            "action_structure": action_structure,
            "actions": self._sanitize_actions(actions),
            # Result
            "success": success,
            "source": source,
            "step_id": step_id,
        }

        # Add screenshot path if provided
        if screenshot_path:
            entry["screenshot_path"] = screenshot_path

        # Append to log file
        self._append_log(entry)

        print(f"[OperationLogger] Logged operation: {log_id[:8]}... "
              f"(instruction_hash={instruction_hash}, actions={len(actions)})")

        return log_id

    def log_from_state(
        self,
        state: dict,
        actions: list[dict],
        success: bool,
        source: str = "vlm",
    ) -> Optional[str]:
        """
        Log an operation from agent state.

        This is a convenience method that extracts relevant info from state.

        Args:
            state: Agent state dict
            actions: List of executed actions
            success: Whether the operation succeeded
            source: Source of the action ("vlm" or "fast_path")

        Returns:
            log_id: UUID of the logged entry, or None if skipped
        """
        # Skip Fast Path actions (they're already rules)
        if source == "fast_path":
            print("[OperationLogger] Skipping Fast Path action (already a rule)")
            return None

        # Skip if no actions
        if not actions:
            print("[OperationLogger] Skipping empty action list")
            return None

        # Extract context from state
        sub_steps = state.get("sub_steps", [])
        current_step_index = state.get("current_step_index", 0)

        # Get the instruction that was executed
        if sub_steps and current_step_index < len(sub_steps):
            instruction = sub_steps[current_step_index].get("description", "")
        else:
            instruction = state.get("instruction", "")

        # Try to get app context
        app_context = self._get_app_context()

        return self.log(
            instruction=instruction,
            actions=actions,
            success=success,
            source=source,
            task_name=state.get("task_name", ""),
            step_id=state.get("step_id", 0),
            app_context=app_context,
            screenshot_path=state.get("screenshot_path", ""),
        )

    def _extract_action_structure(self, actions: list[dict]) -> list[str]:
        """
        Extract the sequence of action types.

        Example: [{"type": "click"}, {"type": "type"}] -> ["click", "type"]
        """
        structure = []
        for action in actions:
            action_type = action.get("type") or action.get("action", "unknown")
            structure.append(action_type)
        return structure

    def _sanitize_actions(self, actions: list[dict]) -> list[dict]:
        """
        Sanitize actions for logging.

        Removes sensitive data and ensures serializability.
        """
        sanitized = []
        for action in actions:
            # Deep copy the action
            action_copy = json.loads(json.dumps(action))

            # Remove potentially large data
            if "screenshot" in action_copy:
                del action_copy["screenshot"]

            sanitized.append(action_copy)

        return sanitized

    def _get_app_context(self) -> dict:
        """
        Get current application context.

        Tries to get active window info using pywinctl.
        """
        try:
            import pywinctl

            window = pywinctl.getActiveWindow()
            if window:
                return {
                    "active_window": window.title,
                    "window_title": window.title,
                }
        except ImportError:
            print("[OperationLogger] pywinctl not installed, skipping app context")
        except Exception as e:
            print(f"[OperationLogger] Could not get app context: {e}")

        return {}

    def _append_log(self, entry: dict) -> None:
        """
        Append an entry to the JSONL log file.

        Each entry is written as a single JSON line.
        """
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[OperationLogger] Error writing log: {e}")

    def load_logs(self, limit: int = 1000) -> list[dict]:
        """
        Load recent logs from the log file.

        Args:
            limit: Maximum number of logs to load

        Returns:
            List of log entries (newest first)
        """
        logs = []
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            logs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            # Return newest first
            logs.reverse()
            return logs[:limit]
        except FileNotFoundError:
            return []

    def load_logs_after(self, after_log_id: str = "", limit: int = 1000) -> list[dict]:
        """
        Load logs that were recorded after a specific log_id.

        This enables incremental clustering - only processing new logs.

        Args:
            after_log_id: Log ID to start from (empty string = load all)
            limit: Maximum number of logs to load

        Returns:
            List of log entries (newest first)
        """
        if not after_log_id:
            return self.load_logs(limit)

        logs = []
        found_start = False
        try:
            # Read from end of file (newest first)
            with open(self.log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()

            # Process from end to start
            for line in reversed(all_lines):
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        if not found_start:
                            if entry.get("log_id") == after_log_id:
                                found_start = True
                                continue  # Skip the starting log itself
                            else:
                                logs.append(entry)
                    except json.JSONDecodeError:
                        continue

                if len(logs) >= limit:
                    break

            return logs
        except FileNotFoundError:
            return []

    def get_latest_log_id(self) -> str:
        """
        Get the most recent log_id from the log file.

        Returns:
            Latest log_id, or empty string if no logs exist
        """
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                # Read last non-empty line
                last_line = ""
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line

                if last_line:
                    entry = json.loads(last_line)
                    return entry.get("log_id", "")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        return ""

    def get_stats(self) -> dict:
        """
        Get statistics about logged operations.

        Returns:
            Dict with total count, success rate, etc.
        """
        logs = self.load_logs(limit=10000)

        if not logs:
            return {"total": 0}

        total = len(logs)
        successful = sum(1 for log in logs if log.get("success"))
        vlm_count = sum(1 for log in logs if log.get("source") == "vlm")
        fast_path_count = sum(1 for log in logs if log.get("source") == "fast_path")

        # Count unique instructions
        unique_instructions = len(set(log.get("instruction_hash") for log in logs))

        return {
            "total": total,
            "successful": successful,
            "success_rate": successful / total if total > 0 else 0,
            "vlm_count": vlm_count,
            "fast_path_count": fast_path_count,
            "unique_instructions": unique_instructions,
        }