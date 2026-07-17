from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.project_cost_import import (  # noqa: E402
    ProjectCostImportError,
    create_project_cost_import_batch,
    parse_project_purchase_directory,
    serialize_project_cost_import_batch,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview or import a local project purchase folder into the cost import review layer.")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--output-dir", default=str(BACKEND_ROOT / "outputs" / "project_cost_import_mvp"))
    parser.add_argument("--commit", action="store_true", help="Persist observations and candidates. Default is read-only preview.")
    parser.add_argument("--actor-user-id", type=int, help="Required with --commit.")
    return parser.parse_args()


def main() -> int:
    args = _args()
    source_dir = Path(args.source_dir).expanduser().resolve()
    try:
        parsed = parse_project_purchase_directory(source_dir)
        result = {
            "mode": "preview",
            "project_name": args.project_name,
            "source_dir": str(source_dir),
            **parsed,
        }
        if args.commit:
            if not args.actor_user_id:
                raise ProjectCostImportError("ACTOR_USER_ID_REQUIRED_FOR_COMMIT")
            paths = sorted(path for path in source_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".xlsx", ".xlsm"})
            db = SessionLocal()
            try:
                batch = create_project_cost_import_batch(
                    db,
                    project_name=args.project_name,
                    source_name=source_dir.name,
                    files=((str(path.relative_to(source_dir)), path.read_bytes()) for path in paths),
                    actor_user_id=args.actor_user_id,
                    max_total_bytes=None,
                )
                db.commit()
                db.refresh(batch)
                result = {"mode": "commit", "batch": serialize_project_cost_import_batch(batch)}
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
    except ProjectCostImportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"project_cost_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "output_path": str(output_path), "summary": result.get("summary") or result.get("batch")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
