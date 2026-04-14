"""
Migration script: Migrate skills from JSON to SQLite.

Usage:
    python scripts/migrate_skills_to_sqlite.py

This script:
1. Reads existing skills from rules/learned_skills.json
2. Migrates them to data/skills.db (SQLite)
3. Creates backup of original JSON file
"""

import os
import sys
import shutil
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from learning.skill_store import SkillStore


def main():
    print("=" * 50)
    print("Skill Migration: JSON → SQLite")
    print("=" * 50)

    # Paths
    json_path = os.path.join(project_root, "rules", "learned_skills.json")
    db_path = os.path.join(project_root, "data", "skills.db")
    backup_path = os.path.join(
        project_root,
        "rules",
        f"learned_skills_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

    # Check if JSON exists
    if not os.path.exists(json_path):
        print(f"[Error] JSON file not found: {json_path}")
        print("No skills to migrate.")
        return 0

    # Create backup
    print(f"\n[Step 1] Creating backup...")
    shutil.copy(json_path, backup_path)
    print(f"Backup saved: {backup_path}")

    # Initialize SQLite store
    print(f"\n[Step 2] Initializing SQLite database...")
    store = SkillStore(db_path)

    # Migrate
    print(f"\n[Step 3] Migrating skills...")
    migrated = store.migrate_from_json(json_path)

    # Verify
    print(f"\n[Step 4] Verifying migration...")
    stats = store.get_stats()
    print(f"Total skills in SQLite: {stats['total']}")
    print(f"  - Single skills: {stats['single_skills']}")
    print(f"  - Sequence skills: {stats['sequence_skills']}")
    print(f"  - Enabled: {stats['enabled']}")
    print(f"  - Disabled: {stats['disabled']}")

    print("\n" + "=" * 50)
    print("Migration complete!")
    print(f"  Migrated: {migrated} skills")
    print(f"  Database: {db_path}")
    print(f"  Backup:   {backup_path}")
    print("=" * 50)

    return migrated


if __name__ == "__main__":
    main()