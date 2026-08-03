from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
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
from app.services.drawing_pdf_gap_recall_importer import (  # noqa: E402
    build_gap_recall_external_import_report,
    load_external_recall_results,
    write_gap_recall_external_import_outputs,
)
from app.services.drawing_pdf_external_recall_template_status import (  # noqa: E402
    build_external_recall_template_status,
    write_external_recall_template_status_outputs,
)
from app.services.drawing_pdf_external_evidence_quality import (  # noqa: E402
    build_external_evidence_quality_report,
    write_external_evidence_quality_outputs,
)
from app.services.drawing_pdf_standard_bill_export import (  # noqa: E402
    build_standard_bill_preview_report,
    write_standard_bill_preview_outputs,
)
from app.services.drawing_pdf_quantity_stage_placeholder import (  # noqa: E402
    build_quantity_stage_placeholder_report,
    write_quantity_stage_placeholder_outputs,
)
from app.services.drawing_pdf_three_field_gate import (  # noqa: E402
    build_three_field_quality_gate,
    write_three_field_quality_gate_outputs,
)
from app.services.drawing_pdf_three_field_review import (  # noqa: E402
    build_three_field_human_review_report,
    write_three_field_human_review_outputs,
)
from app.services.drawing_pdf_three_field_defect_router import (  # noqa: E402
    build_three_field_defect_router_report,
    write_three_field_defect_router_outputs,
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
        description="Import external BIZ-2x PDF recall evidence, rebuild V2, export review workbook, and run the quality gate"
    )
    parser.add_argument("--base-v2-json", required=True, help="Base PDF V2 takeoff JSON")
    parser.add_argument(
        "--external-results",
        action="append",
        required=True,
        help="External recall result JSON/CSV/XLSX; may repeat to merge multiple evidence sources",
    )
    parser.add_argument("--recall-plan-json", default="", help="Optional gap recall plan JSON for call matching")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "external_recall_acceptance_pipeline"),
    )
    parser.add_argument("--source-name", default="external_recall")
    parser.add_argument("--image-root", action="append", default=[], help="Optional image root for object recall workbench links")
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
        help="Optional task-specific image in task_no=path form for object recall workbench; may repeat",
    )
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem-prefix", default="BIZ2x_PDF_external_recall_acceptance")
    parser.add_argument(
        "--require-importable",
        action="store_true",
        help="Stop after template-status output when the external template has no importable evidence rows",
    )
    parser.add_argument(
        "--quality-filter",
        action="store_true",
        help="Score external evidence and import only quality-accepted rows before rebuilding V2",
    )
    parser.add_argument(
        "--quality-include-review",
        action="store_true",
        help="With --quality-filter, also import review-quality rows instead of accepted rows only",
    )
    args = parser.parse_args()

    base_v2_report = json.loads(Path(args.base_v2_json).read_text(encoding="utf-8"))
    external_results = _merge_external_results(
        [(path, load_external_recall_results(path)) for path in args.external_results]
    )
    recall_plan = {}
    if args.recall_plan_json:
        recall_plan = json.loads(Path(args.recall_plan_json).read_text(encoding="utf-8"))

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem_base = _safe_stem(f"{args.stem_prefix}_{timestamp}")
    output_dir = Path(args.output_dir)
    status_dir = output_dir / "template_status"
    quality_dir = output_dir / "evidence_quality"
    import_dir = output_dir / "import"
    eval_dir = output_dir / "eval"
    review_dir = output_dir / "three_field_review"
    defect_router_dir = output_dir / "three_field_defect_router"
    object_recall_dir = output_dir / "object_recall_pack"
    object_workbench_dir = output_dir / "object_recall_workbench"
    object_capture_dir = output_dir / "object_recall_capture_pack"
    gate_dir = output_dir / "three_field_gate"
    standard_bill_dir = output_dir / "standard_bill_preview"
    quantity_dir = output_dir / "quantity_stage_placeholder"
    closed_loop_dir = output_dir / "closed_loop_stage_report"
    for directory in (
        status_dir,
        quality_dir,
        import_dir,
        eval_dir,
        review_dir,
        defect_router_dir,
        object_recall_dir,
        object_workbench_dir,
        object_capture_dir,
        gate_dir,
        standard_bill_dir,
        quantity_dir,
        closed_loop_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    status_report = build_external_recall_template_status(
        external_results,
        source_path=";".join(args.external_results),
    )
    status_outputs = write_external_recall_template_status_outputs(
        status_report,
        status_dir,
        stem=f"{stem_base}_template_status",
    )
    if args.require_importable and not status_report["summary"].get("ready_for_external_import"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "phase": "BIZ-2x-pdf-external-recall-acceptance-pipeline",
                    "stopped_at": "template_status",
                    "reason": "no_importable_external_recall_rows",
                    "template_status_summary": status_report["summary"],
                    "template_status_outputs": status_outputs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    quality_report: dict[str, object] = {
        "ok": True,
        "phase": "BIZ-2x-pdf-external-evidence-quality",
        "summary": {
            "enabled": False,
            "input_row_count": status_report["summary"].get("input_row_count", 0),
            "filtered_importable_row_count": status_report["summary"].get("importable_row_count", 0),
            "include_review": False,
        },
        "quality_rows": [],
        "filtered_external_results": external_results,
    }
    quality_outputs: dict[str, str] = {}
    external_results_for_import = external_results
    if args.quality_filter:
        quality_report = build_external_evidence_quality_report(
            external_results,
            source_path=";".join(args.external_results),
            include_review=args.quality_include_review,
        )
        quality_outputs = write_external_evidence_quality_outputs(
            quality_report,
            quality_dir,
            stem=f"{stem_base}_evidence_quality",
        )
        external_results_for_import = dict(quality_report.get("filtered_external_results") or {"evidence_rows": []})
        if args.require_importable and not quality_report["summary"].get("filtered_importable_row_count"):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "phase": "BIZ-2x-pdf-external-recall-acceptance-pipeline",
                        "stopped_at": "evidence_quality",
                        "reason": "no_quality_importable_external_recall_rows",
                        "template_status_summary": status_report["summary"],
                        "quality_summary": quality_report["summary"],
                        "template_status_outputs": status_outputs,
                        "quality_outputs": quality_outputs,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

    import_report = build_gap_recall_external_import_report(
        external_results_for_import,
        recall_plan=recall_plan,
        source_name=args.source_name,
    )
    import_outputs = write_gap_recall_external_import_outputs(
        import_report,
        import_dir,
        stem=f"{stem_base}_import",
    )

    evaluation = build_gap_recall_v2_evaluation(
        base_v2_report,
        import_report,
        style_prompt_text="external_recall_acceptance_pipeline",
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
    defect_router_report = build_three_field_defect_router_report(review_report)
    defect_router_outputs = write_three_field_defect_router_outputs(
        defect_router_report,
        defect_router_dir,
        stem=f"{stem_base}_defect_router",
    )
    object_recall_pack = build_object_recall_pack(review_report, statuses=["missing_candidate"])
    object_recall_outputs = write_object_recall_pack_outputs(
        object_recall_pack,
        object_recall_dir,
        stem=f"{stem_base}_object_recall",
    )
    object_workbench_report = build_object_recall_workbench(
        object_recall_pack,
        recall_plans=[recall_plan] if recall_plan else [],
        image_roots=args.image_root,
        fallback_images=_parse_fallback_images(args.fallback_image),
        task_images=_parse_key_path_pairs(args.task_image, option_name="--task-image"),
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
        template_status_report=status_report,
        import_report=import_report,
        evaluation_report=evaluation,
        review_report=review_report,
        object_recall_report=object_recall_pack,
        object_workbench_report=object_workbench_report,
        gate_report=gate_report,
        standard_bill_report=standard_bill_report,
        quantity_report=quantity_report,
        artifacts=_artifact_map(
            template_status_outputs=status_outputs,
            import_outputs=import_outputs,
            quality_outputs=quality_outputs,
            eval_outputs=eval_outputs,
            review_outputs=review_outputs,
            defect_router_outputs=defect_router_outputs,
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
                "phase": "BIZ-2x-pdf-external-recall-acceptance-pipeline",
                "template_status_summary": status_report["summary"],
                "quality_summary": quality_report["summary"],
                "import_summary": import_report["summary"],
                "evaluation_summary": evaluation["summary"],
                "review_summary": review_report["summary"],
                "defect_router_summary": defect_router_report["summary"],
                "object_recall_summary": object_recall_pack["summary"],
                "object_workbench_summary": object_workbench_report["summary"],
                "object_capture_summary": object_capture_report["summary"],
                "gate_summary": gate_report["summary"],
                "standard_bill_summary": standard_bill_report["summary"],
                "quantity_placeholder_summary": quantity_report["summary"],
                "closed_loop_summary": closed_loop_report["summary"],
                "can_enable_quantity": gate_report["can_enable_quantity"],
                "template_status_outputs": status_outputs,
                "quality_outputs": quality_outputs,
                "import_outputs": import_outputs,
                "eval_outputs": eval_outputs,
                "review_outputs": review_outputs,
                "defect_router_outputs": defect_router_outputs,
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


def _merge_external_results(sources: list[tuple[str, dict[str, object]]]) -> dict[str, object]:
    evidence_rows: list[dict[str, object]] = []
    for index, (source_path, payload) in enumerate(sources, start=1):
        source_tag = _safe_stem(Path(source_path).stem or f"external_{index}", max_length=28)
        for row in _external_rows(payload):
            merged = dict(row)
            merged["external_source_path"] = source_path
            merged["external_source_tag"] = source_tag
            evidence_rows.append(merged)
    duplicate_ids = Counter(str(row.get("evidence_id") or "") for row in evidence_rows if row.get("evidence_id"))
    for row in evidence_rows:
        evidence_id = str(row.get("evidence_id") or "")
        if evidence_id and duplicate_ids[evidence_id] > 1:
            row["source_evidence_id"] = evidence_id
            row["evidence_id"] = f"{row.get('external_source_tag')}__{evidence_id}"
    return {"evidence_rows": evidence_rows}


def _external_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    for key in ("evidence_rows", "recall_evidence", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
    visual_report = payload.get("visual_evidence_report")
    if isinstance(visual_report, dict) and isinstance(visual_report.get("evidence_rows"), list):
        return [dict(row) for row in visual_report.get("evidence_rows") or [] if isinstance(row, dict)]
    source_rows: list[dict[str, object]] = []
    call_groups: list[dict[str, object]] = []
    for key in ("call_results", "calls", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            call_groups.extend(dict(row) for row in value if isinstance(row, dict))
    if not call_groups and isinstance(payload.get("evidence_items"), list):
        call_groups.append(payload)
    for call in call_groups:
        items = (
            call.get("evidence_rows")
            or call.get("evidence_items")
            or call.get("items")
            or call.get("drawing_items")
            or []
        )
        if not isinstance(items, list):
            continue
        call_meta = {
            "call_no": call.get("call_no"),
            "source_file": _first(call, "source_file", "PDF文件"),
            "page": _first(call, "page", "页码"),
            "tile_id": call.get("tile_id"),
            "vision_pass": _first(call, "vision_pass", "recommended_pass", "prompt_mode"),
            "model": call.get("model"),
        }
        for item in items:
            if isinstance(item, dict):
                source_rows.append({**call_meta, **dict(item)})
    if source_rows:
        return source_rows
    return []


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


def _first(row: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
