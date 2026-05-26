"""
Skill Store - SQLite-based storage for learned skills.

Provides:
- CRUD operations for skills
- Pattern matching queries
- Migration from JSON
- Icon-based coordinate resolution (Phase 1)
"""

import sqlite3
import json
import os
import re
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# Import icon matcher for image-based coordinate resolution
try:
    from learning.icon_matcher import IconMatcher, IconData, resolve_coordinate_with_icon
    ICON_MATCHING_AVAILABLE = True
except ImportError:
    ICON_MATCHING_AVAILABLE = False
    print("[SkillStore] Icon matching not available (cv2 not installed)")

logger = logging.getLogger(__name__)


class SkillStore:
    """SQLite storage for learned skills."""

    def __init__(self, db_path: str = "data/skills.db", screenshots_dir: str = "data/screenshots"):
        """
        Initialize the skill store.

        Args:
            db_path: Path to SQLite database file
            screenshots_dir: Directory for skill icon screenshots
        """
        self.db_path = db_path
        self.screenshots_dir = screenshots_dir
        self._init_db()

        # Initialize icon matcher if available
        self.icon_matcher = None
        if ICON_MATCHING_AVAILABLE:
            self.icon_matcher = IconMatcher(screenshots_dir=screenshots_dir)
            logger.info(f"[SkillStore] Icon matcher initialized with {screenshots_dir}")

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
                icon_data TEXT,
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

        # Migration: Add icon_data column if not exists (Phase 1)
        try:
            cursor.execute("SELECT icon_data FROM skills LIMIT 1")
        except sqlite3.OperationalError:
            # Column doesn't exist, add it
            cursor.execute("ALTER TABLE skills ADD COLUMN icon_data TEXT")
            conn.commit()
            logger.info("[SkillStore] Added icon_data column (migration)")

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
                    confidence, enabled, created_at, updated_at, icon_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.now().isoformat(),
                json.dumps(skill.get("icon_data")) if skill.get("icon_data") else None
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

    def resolve_action_coordinate(
        self,
        skill: Dict,
        screenshot: any,
        screen_size: Optional[Tuple[int, int]] = None,
        action_index: int = 0,
        current_dpi: Optional[int] = None,
        use_adaptive: bool = True
    ) -> Optional[Tuple[int, int]]:
        """
        Resolve action coordinate using icon matching with fallback.

        Phase 2: 支持自适应匹配（方案A+B）+ 自动获取屏幕分辨率

        Priority:
        1. Adaptive icon matching (if icon_data available with resolution metadata)
        2. Standard icon matching (if icon_data available without metadata)
        3. Fallback coordinate (from skill actions)
        4. Recorded coordinate (from skill actions)

        Args:
            skill: Skill dict with icon_data and actions
            screenshot: Current screenshot (numpy array or path)
            screen_size: Screen size (width, height). If None, auto-detect.
            action_index: Index of action to resolve (default 0, first action)
            current_dpi: Current screen DPI. If None, auto-detect.
            use_adaptive: Use adaptive scaling if resolution metadata available

        Returns:
            Coordinate (x, y) in pixel format, or None if cannot resolve
        """
        # Import auto-detection functions
        from learning.icon_matcher import get_screen_resolution, get_screen_dpi

        # Auto-detect screen resolution if not provided
        if screen_size is None:
            screen_size = get_screen_resolution()

        # Auto-detect DPI if not provided
        if current_dpi is None:
            current_dpi = get_screen_dpi()

        # Get actions
        actions = skill.get("actions", [])
        if not actions or action_index >= len(actions):
            logger.warning(f"No action at index {action_index}")
            return None

        action = actions[action_index]

        # Try icon matching first
        icon_data_dict = skill.get("icon_data")
        if icon_data_dict and self.icon_matcher:
            icon_data = IconData.from_dict(icon_data_dict)

            if icon_data.has_icon():
                coord = resolve_coordinate_with_icon(
                    self.icon_matcher,
                    icon_data,
                    screenshot,
                    screen_size,
                    current_dpi=current_dpi,
                    use_normalized=False,  # Return pixel coordinates for execution
                    use_adaptive=use_adaptive
                )

                if coord:
                    logger.info(f"[SkillStore] Coordinate resolved via icon matching: {coord}")
                    return coord

                logger.warning("[SkillStore] Icon matching failed, falling back to recorded coordinate")

        # Fallback to recorded coordinate in action
        coord_expr = action.get("coordinate")
        if coord_expr:
            # Handle different coordinate formats
            if isinstance(coord_expr, (list, tuple)) and len(coord_expr) >= 2:
                # Normalize if needed
                x, y = int(coord_expr[0]), int(coord_expr[1])

                # Check if normalized (0-1000 range)
                if x <= 1000 and y <= 1000:
                    # Denormalize to pixel coordinates
                    if self.icon_matcher:
                        return self.icon_matcher.denormalize_coordinate(x, y, screen_size)
                    else:
                        # Simple denormalization without icon_matcher
                        width, height = screen_size
                        return (int(x * width / 1000), int(y * height / 1000))

                return (x, y)

        logger.warning(f"[SkillStore] No coordinate available for action {action_index}")
        return None

    def update_icon_data(
        self,
        skill_id: str,
        icon_data: Dict
    ) -> bool:
        """
        Update skill's icon_data field.

        Args:
            skill_id: Skill ID
            icon_data: Icon data dict with icon_path, threshold, etc.

        Returns:
            True if updated successfully
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE skills SET icon_data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(icon_data), datetime.now().isoformat(), skill_id)
            )

            success = cursor.rowcount > 0
            conn.commit()
            conn.close()

            if success:
                logger.info(f"[SkillStore] Updated icon_data for skill: {skill_id}")

            return success
        except Exception as e:
            logger.error(f"[SkillStore] Error updating icon_data: {e}")
            return False

    def _row_to_dict(self, row: tuple) -> Dict:
        """
        Convert database row to skill dict.

        Args:
            row: Database row tuple

        Returns:
            Skill dict
        """
        # SQLite ALTER TABLE adds new columns at the END, not in CREATE TABLE order
        # Actual order: id, name, desc, source, cluster_id, cluster_type,
        #                patterns, app_ctx, actions, params, confidence, enabled, created, updated, icon_data
        # icon_data is at index 14 (last column)

        row_len = len(row)

        # Parse parameters (row[9])
        parameters = None
        if row[9]:
            try:
                parameters = json.loads(row[9])
            except (json.JSONDecodeError, TypeError):
                parameters = None

        # Parse icon_data (row[14] - last column added via ALTER TABLE)
        icon_data = None
        if row_len == 15 and row[14]:  # New schema with icon_data at end
            try:
                icon_data = json.loads(row[14])
            except json.JSONDecodeError:
                icon_data = None

        # Fixed indices (icon_data doesn't affect other columns' positions)
        confidence_idx = 10
        enabled_idx = 11
        created_idx = 12
        updated_idx = 13

        # Parse patterns safely
        patterns = []
        if row[6]:
            try:
                patterns = json.loads(row[6])
            except json.JSONDecodeError:
                patterns = []

        # Parse app_context safely
        app_context = []
        if row[7]:
            try:
                app_context = json.loads(row[7])
                if isinstance(app_context, str):
                    app_context = [app_context] if app_context else []
            except json.JSONDecodeError:
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
            "icon_data": icon_data,
            "confidence": row[confidence_idx],
            "enabled": bool(row[enabled_idx]),
            "created_at": row[created_idx],
            "updated_at": row[updated_idx]
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