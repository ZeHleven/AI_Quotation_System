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

from app.services.drawing_quantity_confirmation import (  # noqa: E402
    build_drawing_confirmation_pack,
    read_confirmation_workbook,
    validate_confirmation_rows,
    write_confirmation_outputs,
    write_validation_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-6 图纸识别人工确认补量包与四字段导出")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="根据 BIZ-2x-4/5 报告生成业务员确认补量工作簿")
    build_parser.add_argument("--standard-match-report", required=True, help="BIZ-2x-4 标准项目候选匹配 JSON")
    build_parser.add_argument("--quantity-evidence-report", required=True, help="BIZ-2x-5 工程量证据 JSON")
    build_parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x6"),
        help="输出目录，默认 outputs/biz2x6",
    )
    build_parser.add_argument("--print-summary-only", action="store_true", help="只打印 summary 和输出路径")

    export_parser = subparsers.add_parser("export-final", help="读取业务员填完的确认表，校验并导出最终四字段 Excel")
    export_parser.add_argument("--confirmation-workbook", required=True, help="业务员填完的 BIZ-2x-6 确认工作簿")
    export_parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x6"),
        help="输出目录，默认 outputs/biz2x6",
    )
    export_parser.add_argument("--print-summary-only", action="store_true", help="只打印 summary 和输出路径")
    args = parser.parse_args()

    try:
        if args.command == "build":
            result = _run_build(args)
        else:
            result = _run_export_final(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BIZ2X6_CONFIRMATION_PACK_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if getattr(args, "print_summary_only", False):
        print(
            json.dumps(
                {
                    "ok": result.get("ok"),
                    "phase": result.get("phase"),
                    "generated_at": result.get("generated_at"),
                    "summary": result.get("summary"),
                    "outputs": result.get("outputs", {}),
                    "issues": result.get("issues", [])[:10],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_build(args: argparse.Namespace) -> dict[str, object]:
    standard_match_path = Path(args.standard_match_report)
    quantity_evidence_path = Path(args.quantity_evidence_report)
    standard_match_report = json.loads(standard_match_path.read_text(encoding="utf-8"))
    quantity_evidence_report = json.loads(quantity_evidence_path.read_text(encoding="utf-8"))
    pack = build_drawing_confirmation_pack(standard_match_report, quantity_evidence_report)
    pack["inputs"] = {
        "standard_match_report": str(standard_match_path),
        "quantity_evidence_report": str(quantity_evidence_path),
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_confirmation_outputs(
        pack,
        args.output_dir,
        stem=f"BIZ2x6_图纸识别人工确认补量包_{timestamp}",
    )
    pack["outputs"] = outputs
    return pack


def _run_export_final(args: argparse.Namespace) -> dict[str, object]:
    confirmation_path = Path(args.confirmation_workbook)
    rows = read_confirmation_workbook(confirmation_path)
    validation = validate_confirmation_rows(rows)
    validation["phase"] = "BIZ-2x-6-final-four-field-export"
    validation["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    validation["inputs"] = {"confirmation_workbook": str(confirmation_path)}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_validation_report(
        validation,
        args.output_dir,
        stem=f"BIZ2x6_确认表校验_{timestamp}",
    )
    validation["outputs"] = outputs
    return validation


if __name__ == "__main__":
    raise SystemExit(main())
