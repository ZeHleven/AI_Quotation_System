from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_pdf_feature_precision_capture_pack import (  # noqa: E402
    build_feature_precision_capture_pack,
    write_feature_precision_capture_pack_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an answer-blind GLM-4V capture pack for precise feature/spec evidence"
    )
    parser.add_argument("--defect-router", required=True, help="Three-field defect router JSON or CSV")
    parser.add_argument("--recall-plan-json", action="append", default=[], help="Optional recall plan JSON; repeatable")
    parser.add_argument("--image-root", action="append", default=[], help="Optional rendered evidence image root; repeatable")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "feature_precision_capture_pack"),
    )
    parser.add_argument(
        "--routes",
        action="append",
        default=[],
        help="Repair route to include; repeatable or comma-separated. Defaults to feature_enrichment and split_variant_review.",
    )
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--max-tasks-per-call", type=int, default=8)
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    defect_router_report = load_defect_router_report(args.defect_router)
    recall_plans = [load_json(path) for path in args.recall_plan_json]
    routes = _parse_routes(args.routes)
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_feature_precision_capture_pack_{timestamp}"
    report = build_feature_precision_capture_pack(
        defect_router_report,
        recall_plans=recall_plans,
        image_roots=args.image_root,
        routes=routes or None,
        max_rows=args.max_rows,
        max_tasks_per_call=args.max_tasks_per_call,
    )
    outputs = write_feature_precision_capture_pack_outputs(report, args.output_dir, stem=stem)
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


def load_defect_router_report(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        payload = load_json(source)
        if isinstance(payload.get("defect_rows"), list):
            return payload
        for key in ("rows", "review_rows"):
            if isinstance(payload.get(key), list):
                return {"defect_rows": payload[key]}
        return {"defect_rows": []}
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            return {"defect_rows": [dict(row) for row in csv.DictReader(handle)]}
    raise ValueError(f"Unsupported defect router file type: {source.suffix}")


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        return dict(payload)
    raise ValueError(f"Expected JSON object: {path}")


def _parse_routes(values: list[str]) -> list[str]:
    routes: list[str] = []
    for value in values:
        for route in str(value or "").split(","):
            route = route.strip()
            if route and route not in routes:
                routes.append(route)
    return routes


if __name__ == "__main__":
    raise SystemExit(main())
