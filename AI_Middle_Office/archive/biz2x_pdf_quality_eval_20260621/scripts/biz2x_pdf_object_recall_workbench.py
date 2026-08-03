from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_pdf_object_recall_workbench import (  # noqa: E402
    build_object_recall_workbench,
    write_object_recall_workbench_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fillable object recall evidence workbench with image links")
    parser.add_argument("--object-recall-json", required=True, help="Object recall pack JSON")
    parser.add_argument("--recall-plan-json", action="append", default=[], help="Optional recall plan JSON; may repeat")
    parser.add_argument("--image-root", action="append", default=[], help="Optional image root for evidence-id image lookup")
    parser.add_argument(
        "--fallback-image",
        action="append",
        default=[],
        help="Optional fallback image in key=path form, where key is recommended_pass, object_class, or default",
    )
    parser.add_argument(
        "--task-image",
        action="append",
        default=[],
        help="Optional task-specific image in task_no=path form; may repeat",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "object_recall_workbench"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    object_pack = json.loads(Path(args.object_recall_json).read_text(encoding="utf-8"))
    recall_plans = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.recall_plan_json]
    report = build_object_recall_workbench(
        object_pack,
        recall_plans=recall_plans,
        image_roots=args.image_root,
        fallback_images=_parse_fallback_images(args.fallback_image),
        task_images=_parse_key_path_pairs(args.task_image, option_name="--task-image"),
    )
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_object_recall_workbench_{timestamp}"
    outputs = write_object_recall_workbench_outputs(report, args.output_dir, stem=stem)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "phase": report["phase"],
                "summary": report["summary"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_fallback_images(values: list[str]) -> dict[str, str]:
    return _parse_key_path_pairs(values, option_name="--fallback-image")


def _parse_key_path_pairs(values: list[str], *, option_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"{option_name} must use key=path form: {value}")
        key, path = value.split("=", 1)
        key = key.strip()
        path = path.strip()
        if not key or not path:
            raise SystemExit(f"{option_name} must use non-empty key=path form: {value}")
        result[key] = path
    return result


if __name__ == "__main__":
    raise SystemExit(main())
