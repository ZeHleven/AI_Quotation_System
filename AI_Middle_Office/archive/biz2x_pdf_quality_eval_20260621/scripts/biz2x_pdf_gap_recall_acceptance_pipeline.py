from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_pdf_gap_recall_eval import (  # noqa: E402
    build_gap_recall_v2_evaluation,
    write_gap_recall_v2_evaluation_outputs,
)
from app.services.drawing_pdf_three_field_review import (  # noqa: E402
    build_three_field_human_review_report,
    write_three_field_human_review_outputs,
)
from app.services.drawing_pdf_three_field_gate import (  # noqa: E402
    build_three_field_quality_gate,
    write_three_field_quality_gate_outputs,
)
from app.services.drawing_pdf_standard_bill_export import (  # noqa: E402
    build_standard_bill_preview_report,
    write_standard_bill_preview_outputs,
)
from app.services.drawing_pdf_quantity_stage_placeholder import (  # noqa: E402
    build_quantity_stage_placeholder_report,
    write_quantity_stage_placeholder_outputs,
)
from app.services.drawing_pdf_object_recall_pack import (  # noqa: E402
    build_object_recall_pack,
    write_object_recall_pack_outputs,
)
from app.services.drawing_pdf_object_recall_workbench import (  # noqa: E402
    build_object_recall_workbench,
    write_object_recall_workbench_outputs,
)
from app.services.drawing_pdf_object_recall_capture_pack import (  # noqa: E402
    build_object_recall_capture_pack,
    write_object_recall_capture_pack_outputs,
)
from app.services.drawing_pdf_closed_loop_stage_report import (  # noqa: E402
    build_closed_loop_stage_report,
    write_closed_loop_stage_report_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local BIZ-2x PDF gap-recall evaluation and export the three-field review pack"
    )
    parser.add_argument("--base-v2-json", required=True, help="Base PDF V2 takeoff JSON")
    parser.add_argument("--recall-run-json", required=True, help="Gap recall run JSON")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "gap_recall_acceptance_pipeline"),
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem-prefix", default="BIZ2x_PDF_gap_recall_acceptance")
    parser.add_argument("--image-root", action="append", default=[], help="Optional image root for object recall workbench links")
    args = parser.parse_args()

    base_v2_report = json.loads(Path(args.base_v2_json).read_text(encoding="utf-8"))
    recall_run_report = json.loads(Path(args.recall_run_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem_base = _safe_stem(f"{args.stem_prefix}_{timestamp}")
    output_dir = Path(args.output_dir)
    eval_dir = output_dir / "eval"
    review_dir = output_dir / "three_field_review"
    object_recall_dir = output_dir / "object_recall_pack"
    object_workbench_dir = output_dir / "object_recall_workbench"
    object_capture_dir = output_dir / "object_recall_capture_pack"
    gate_dir = output_dir / "three_field_gate"
    standard_bill_dir = output_dir / "standard_bill_preview"
    quantity_dir = output_dir / "quantity_stage_placeholder"
    closed_loop_dir = output_dir / "closed_loop_stage_report"
    for directory in (
        eval_dir,
        review_dir,
        object_recall_dir,
        object_workbench_dir,
        object_capture_dir,
        gate_dir,
        standard_bill_dir,
        quantity_dir,
        closed_loop_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    evaluation = build_gap_recall_v2_evaluation(
        base_v2_report,
        recall_run_report,
        style_prompt_text="gap_recall_acceptance_pipeline",
    )
    eval_outputs = write_gap_recall_v2_evaluation_outputs(
        evaluation,
        eval_dir,
        stem=f"{stem_base}_eval",
    )
    review_report = build_three_field_human_review_report(evaluation["augmented_v2_report"])
    review_outputs = write_three_field_human_review_outputs(
        review_report,
        review_dir,
        stem=f"{stem_base}_three_field_review",
    )
    object_recall_pack = build_object_recall_pack(review_report, statuses=["missing_candidate"])
    object_recall_outputs = write_object_recall_pack_outputs(
        object_recall_pack,
        object_recall_dir,
        stem=f"{stem_base}_object_recall",
    )
    object_workbench_report = build_object_recall_workbench(
        object_recall_pack,
        image_roots=args.image_root,
    )
    object_workbench_outputs = write_object_recall_workbench_outputs(
        object_workbench_report,
        object_workbench_dir,
        stem=f"{stem_base}_object_workbench",
    )
    object_capture_report = build_object_recall_capture_pack(
        object_workbench_report.get("workbench_rows") or [],
    )
    object_capture_outputs = write_object_recall_capture_pack_outputs(
        object_capture_report,
        object_capture_dir,
        stem=f"{stem_base}_object_capture",
    )
    gate_report = build_three_field_quality_gate(review_report)
    gate_outputs = write_three_field_quality_gate_outputs(
        gate_report,
        gate_dir,
        stem=f"{stem_base}_three_field_gate",
    )
    standard_bill_report = build_standard_bill_preview_report(
        evaluation["augmented_v2_report"],
        gate_report=gate_report,
    )
    standard_bill_outputs = write_standard_bill_preview_outputs(
        standard_bill_report,
        standard_bill_dir,
        stem=f"{stem_base}_stdbill",
    )
    quantity_report = build_quantity_stage_placeholder_report(
        standard_bill_report,
        v2_report=evaluation["augmented_v2_report"],
        gate_report=gate_report,
    )
    quantity_outputs = write_quantity_stage_placeholder_outputs(
        quantity_report,
        quantity_dir,
        stem=f"{stem_base}_qty",
    )
    closed_loop_report = build_closed_loop_stage_report(
        v2_report=evaluation["augmented_v2_report"],
        evaluation_report=evaluation,
        review_report=review_report,
        object_recall_report=object_recall_pack,
        object_workbench_report=object_workbench_report,
        gate_report=gate_report,
        standard_bill_report=standard_bill_report,
        quantity_report=quantity_report,
        artifacts=_artifact_map(
            eval_outputs=eval_outputs,
            review_outputs=review_outputs,
            object_recall_outputs=object_recall_outputs,
            object_workbench_outputs=object_workbench_outputs,
            object_capture_outputs=object_capture_outputs,
            gate_outputs=gate_outputs,
            standard_bill_outputs=standard_bill_outputs,
            quantity_placeholder_outputs=quantity_outputs,
        ),
    )
    closed_loop_outputs = write_closed_loop_stage_report_outputs(
        closed_loop_report,
        closed_loop_dir,
        stem=f"{stem_base}_closed_loop",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "phase": "BIZ-2x-pdf-gap-recall-acceptance-pipeline",
                "evaluation_summary": evaluation["summary"],
                "review_summary": review_report["summary"],
                "object_recall_summary": object_recall_pack["summary"],
                "object_workbench_summary": object_workbench_report["summary"],
                "object_capture_summary": object_capture_report["summary"],
                "gate_summary": gate_report["summary"],
                "standard_bill_summary": standard_bill_report["summary"],
                "quantity_placeholder_summary": quantity_report["summary"],
                "closed_loop_summary": closed_loop_report["summary"],
                "can_enable_quantity": gate_report["can_enable_quantity"],
                "eval_outputs": eval_outputs,
                "review_outputs": review_outputs,
                "object_recall_outputs": object_recall_outputs,
                "object_workbench_outputs": object_workbench_outputs,
                "object_capture_outputs": object_capture_outputs,
                "gate_outputs": gate_outputs,
                "standard_bill_outputs": standard_bill_outputs,
                "quantity_placeholder_outputs": quantity_outputs,
                "closed_loop_outputs": closed_loop_outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _safe_stem(value: str, *, max_length: int = 56) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    if not cleaned:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{cleaned[: max_length - 9].rstrip('._')}_{digest}"


def _artifact_map(**groups: dict[str, str]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for group_name, outputs in groups.items():
        for kind, path in (outputs or {}).items():
            artifacts[f"{group_name}.{kind}"] = str(path)
    return artifacts


if __name__ == "__main__":
    raise SystemExit(main())
