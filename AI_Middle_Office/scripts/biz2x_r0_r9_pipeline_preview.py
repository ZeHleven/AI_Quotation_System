from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_dynamic_itemization import (  # noqa: E402
    build_dynamic_itemization_report,
    build_dynamic_itemization_report_with_llm,
    write_dynamic_itemization_outputs,
)
from app.services.quantity_standard_index import load_standard_library_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BIZ-2x R0-R9 DXF/PDF evidence + standard library + LLM-ready itemization preview"
    )
    parser.add_argument("--field-report", help="DXF field convergence JSON report")
    parser.add_argument("--signals-json", help="JSON file containing evidence_signals or a list of signals")
    parser.add_argument(
        "--signal",
        action="append",
        default=[],
        help="Ad-hoc evidence text. Can be repeated, for example: --signal 'CT-01 750x1500地砖地面'",
    )
    parser.add_argument("--llm-decisions", help="Optional constrained LLM decision JSON")
    parser.add_argument(
        "--standard-index",
        default=str(BACKEND_ROOT / "data" / "standards" / "standard_library_index.json"),
        help="Standard library index JSON",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x_r0_r9"),
        help="Output directory",
    )
    parser.add_argument("--no-write", action="store_true", help="Only print summary")
    parser.add_argument("--print-report", action="store_true", help="Print full report JSON")
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Call configured DeepSeek LLM through the model gateway for R4 decisions; falls back when unavailable",
    )
    args = parser.parse_args()

    try:
        evidence_source = _load_evidence_source(args)
        llm_decisions = _load_optional_json(args.llm_decisions)
        standard_index = load_standard_library_index(args.standard_index)
        if args.use_llm:
            report = asyncio.run(
                build_dynamic_itemization_report_with_llm(
                    evidence_source,
                    standard_index=standard_index,
                )
            )
        else:
            report = build_dynamic_itemization_report(
                evidence_source,
                standard_index=standard_index,
                llm_decisions=llm_decisions,
            )
        report["inputs"] = {
            "field_report": args.field_report or "",
            "signals_json": args.signals_json or "",
            "signal_count": len(args.signal or []),
            "llm_decisions": args.llm_decisions or "",
            "standard_index": args.standard_index,
        }
        if not args.no_write:
            outputs = write_dynamic_itemization_outputs(
                report,
                args.output_dir,
                stem=f"BIZ2x_R0_R9_DXF_PDF_国标_LLM动态列项_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            )
            report["outputs"] = outputs
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BIZ2X_R0_R9_PIPELINE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = report if args.print_report else {
        "ok": report["ok"],
        "phase": report["phase"],
        "generated_at": report["generated_at"],
        "safe_for_final_quantity_list": report["safe_for_final_quantity_list"],
        "summary": report["summary"],
        "outputs": report.get("outputs", {}),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _load_evidence_source(args: argparse.Namespace) -> dict[str, object] | list[dict[str, object]]:
    sources = [bool(args.field_report), bool(args.signals_json), bool(args.signal)]
    if sum(1 for value in sources if value) != 1:
        raise ValueError("provide exactly one of --field-report, --signals-json, or --signal")
    if args.field_report:
        return json.loads(Path(args.field_report).read_text(encoding="utf-8"))
    if args.signals_json:
        return json.loads(Path(args.signals_json).read_text(encoding="utf-8"))
    return [
        {
            "signal_id": f"SIG-{index:04d}",
            "source_kind": "ad_hoc_text",
            "source_name": text,
            "evidence_text": text,
            "evidence_source": "manual_preview",
        }
        for index, text in enumerate(args.signal, start=1)
    ]


def _load_optional_json(path: str | None) -> object | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
