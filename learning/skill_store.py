"""
Skill Store - SQLite-based storage for learned skills.

Provides:
- CRUD operations for skills
- Pattern matching queries
- Migration from JSON
"""

import sqlite3
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional


class SkillStore:
    """SQLite storage for learned skills."""

    def __init__(self, db_path: str = "data/skills.db"):
        """
        Initialize the skill store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database and create tables."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                source TEXT DEFAULT 'learned',
                cluster_id TEXT,
                cluster_type TEXT DEFAULT 'single',
                trigger_patterns TEXT,
                app_context TEXT,
                actions TEXT,
                parameters TEXT,
                confidence REAL,
                enabled INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # Indexes for query optimization
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_app_context ON skills(app_context)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_enabled ON skills(enabled)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cluster_type ON skills(cluster_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cluster_id ON skills(cluster_id)")

        conn.commit()
        conn.close()
        print(f"[SkillStore] Database initialized: {self.db_path}")

    def save(self, skill: Dict) -> bool:
        """
        Insert or update a skill.

        Args:
            skill: Skill dict to save

        Returns:
            True if saved successfully
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            trigger = skill.get("trigger", {})
            patterns = json.dumps(trigger.get("patterns", []))
            app_context = json.dumps(trigger.get("app_context", []))  # Store as JSON array

            cursor.execute("""
                INSERT OR REPLACE INTO skills (
                    id, name, description, source, cluster_id, cluster_type,
                    trigger_patterns, app_context, actions, parameters,
                    confidence, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                skill.get("id"),
                skill.get("name"),
                skill.get("description"),
                skill.get("source", "learned"),
                skill.get("cluster_id"),
                skill.get("cluster_type", "single"),
                patterns,
                app_context,
                json.dumps(skill.get("actions", [])),
                json.dumps(skill.get("parameters")) if skill.get("parameters") else None,
                skill.get("confidence", 0.5),
                int(skill.get("enabled", True)),
                skill.get("created_at", datetime.now().isoformat()),
                datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()
            print(f"[SkillStore] Saved skill: {skill.get('id')} - {skill.get('name')}")
            return True
        except Exception as e:
            print(f"[SkillStore] Error saving skill: {e}")
            return False

    def delete(self, skill_id: str) -> bool:
        """
        Delete a skill by ID.

        Args:
            skill_id: Skill ID to delete

        Returns:
            True if deleted successfully
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM skills WHERE id = ?", (skill_id,))

            success = cursor.rowcount > 0
            conn.commit()
            conn.close()

            if success:
                print(f"[SkillStore] Deleted skill: {skill_id}")
            else:
                print(f"[SkillStore] Skill not found: {skill_id}")

            return success
        except Exception as e:
            print(f"[SkillStore] Error deleting skill: {e}")
            return False

    def get(self, skill_id: str) -> Optional[Dict]:
        """
        Get a skill by ID.

        Args:
            skill_id: Skill ID

        Returns:
            Skill dict or None if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return self._row_to_dict(row)
        return None

    def list_all(
        self,
        cluster_type: Optional[str] = None,
        enabled_only: bool = False
    ) -> List[Dict]:
        """
        List all skills, optionally filtered.

        Args:
            cluster_type: Filter by 'single' or 'sequence'
            enabled_only: Only return enabled skills

        Returns:
            List of skill dicts
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = "SELECT * FROM skills"
        conditions = []
        params = []

        if cluster_type:
            conditions.append("cluster_type = ?")
            params.append(cluster_type)

        if enabled_only:
            conditions.append("enabled = 1")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        # Parse each row, skip malformed records
        skills = []
        for row in rows:
            try:
                skills.append(self._row_to_dict(row))
            except Exception as e:
                print(f"[SkillStore] Error parsing skill row: {e}, row[0]={row[0] if row else 'N/A'}")
                continue

        return skills

    def match(self, instruction: str, app: Optional[str] = None) -> Optional[Dict]:
        """
        Find a skill matching instruction and app context.

        Args:
            instruction: User instruction text
            app: Current app context (window title)

        Returns:
            Matching skill dict or None
        """
        skills = self.list_all(enabled_only=True)

        for skill in skills:
            trigger = skill.get("trigger", {})
            app_contexts = trigger.get("app_context", [])

            # Check app context first (fast filter)
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
                    if re.search(pattern, instruction):
                        return skill
                except re.error:
                    continue

        return None

    def update_enabled(self, skill_id: str, enabled: bool) -> bool:
        """
        Update skill enabled status.

        Args:
            skill_id: Skill ID
            enabled: New enabled status

        Returns:
            True if updated successfully
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE skills SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), datetime.now().isoformat(), skill_id)
            )

            success = cursor.rowcount > 0
            conn.commit()
            conn.close()

            if success:
                status = "enabled" if enabled else "disabled"
                print(f"[SkillStore] {status} skill: {skill_id}")

            return success
        except Exception as e:
            print(f"[SkillStore] Error updating skill: {e}")
            return False

    def _row_to_dict(self, row: tuple) -> Dict:
        """
        Convert database row to skill dict.

        Args:
            row: Database row tuple

        Returns:
            Skill dict
        """
        parameters = None
        if row[9]:
            parameters = json.loads(row[9])

        # Parse patterns safely
        patterns = []
        if row[6]:
            try:
                patterns = json.loads(row[6])
            except json.JSONDecodeError:
                patterns = []

        # Parse app_context safely - handle both old string format and new JSON array
        app_context = []
        if row[7]:
            try:
                app_context = json.loads(row[7])
                # If it's a string, convert to list
                if isinstance(app_context, str):
                    app_context = [app_context] if app_context else []
            except json.JSONDecodeError:
                # Old format: single string
                app_context = [row[7]] if row[7] else []

        # Parse actions safely
        actions = []
        if row[8]:
            try:
                actions = json.loads(row[8])
            except json.JSONDecodeError:
                actions = []

        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "source": row[3],
            "cluster_id": row[4],
            "cluster_type": row[5],
            "trigger": {
                "patterns": patterns,
                "app_context": app_context
            },
            "actions": actions,
            "parameters": parameters,
            "confidence": row[10],
            "enabled": bool(row[11]),
            "created_at": row[12],
            "updated_at": row[13]
        }

    def migrate_from_json(self, json_path: str = "rules/learned_skills.json") -> int:
        """
        Migrate skills from JSON file to SQLite.

        Args:
            json_path: Path to learned_skills.json

        Returns:
            Number of skills migrated
        """
        if not os.path.exists(json_path):
            print(f"[SkillStore] JSON file not found: {json_path}")
            return 0

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            skills = data.get("rules", [])
            migrated = 0

            for skill in skills:
                # Add cluster_type if missing
                if "cluster_type" not in skill:
                    # Infer from cluster_id or presence of parameters
                    cluster_id = skill.get("cluster_id", "")
                    if "sequence" in cluster_id.lower() or skill.get("parameters"):
                        skill["cluster_type"] = "sequence"
                    else:
                        skill["cluster_type"] = "single"

                if self.save(skill):
                    migrated += 1

            print(f"[SkillStore] Migrated {migrated} skills from JSON to SQLite")
            return migrated
        except Exception as e:
            print(f"[SkillStore] Migration error: {e}")
            return 0

    def export_to_json(self, json_path: str = "rules/learned_skills.json") -> bool:
        """
        Export skills to JSON file (for backup).

        Args:
            json_path: Output JSON file path

        Returns:
            True if exported successfully
        """
        try:
            skills = self.list_all()

            data = {
                "version": "1.0",
                "description": "Learned skills - exported from SQLite",
                "rules": skills
            }

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"[SkillStore] Exported {len(skills)} skills to JSON: {json_path}")
            return True
        except Exception as e:
            print(f"[SkillStore] Export error: {e}")
            return False

    def get_stats(self) -> Dict:
        """
        Get statistics about stored skills.

        Returns:
            Dict with skill statistics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM skills")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM skills WHERE enabled = 1")
        enabled = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM skills WHERE cluster_type = 'single'")
        single_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM skills WHERE cluster_type = 'sequence'")
        sequence_count = cursor.fetchone()[0]

        conn.close()

        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "single_skills": single_count,
            "sequence_skills": sequence_count,
        }