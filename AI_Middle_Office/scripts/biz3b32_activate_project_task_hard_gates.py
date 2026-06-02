from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.project_progress import activate_project_task_hard_gates  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Activate BIZ-3b-3-2 complete_required hard gates for project tasks.")
    parser.add_argument("--apply", action="store_true", help="Write changes. Omit for dry-run summary only.")
    parser.add_argument("--project-id", type=int, default=None, help="Limit activation to one project. Omit for all tasks.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = activate_project_task_hard_gates(db, apply=args.apply, project_id=args.project_id)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
