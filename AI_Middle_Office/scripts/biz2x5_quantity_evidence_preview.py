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

from app.services.drawing_quantity_evidence import (  # noqa: E402
    extract_quantity_evidence_for_standard_matches,
    write_quantity_evidence_outputs,
)
from app.services.dxf_text_extractor import (  # noqa: E402
    DxfTextExtractionError,
    collect_dxf_files,
    parse_dxf_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x-5 工程量证据提取与标准规则判断预览")
    parser.add_argument("--standard-match-report", required=True, help="BIZ-2x-4 标准项目候选匹配 JSON 报告")
    parser.add_argument("--dxf-text-report", default="", help="BIZ-2x-3 DXF 文本提取 JSON 报告；仅使用报告内保存的文字样例")
    parser.add_argument("--dxf-dir", default="", help="DXF 文件目录；传入后会重新读取全量文字记录用于工程量证据提取")
    parser.add_argument("--dxf-file", action="append", default=[], help="可重复传入单个 DXF 文件")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2x5"),
        help="报告输出目录，默认 outputs/biz2x5",
    )
    parser.add_argument("--max-evidence-per-candidate", type=int, default=8, help="每个标准候选最多保留多少条工程量证据")
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写报告")
    parser.add_argument("--print-summary-only", action="store_true", help="只打印 summary 和输出文件路径")
    args = parser.parse_args()

    try:
        standard_match_path = Path(args.standard_match_report)
        standard_match_report = json.loads(standard_match_path.read_text(encoding="utf-8"))
        text_records = _load_text_records(args)
        report = extract_quantity_evidence_for_standard_matches(
            standard_match_report,
            text_records,
            max_evidence_per_candidate=args.max_evidence_per_candidate,
        )
        report["inputs"] = {
            "standard_match_report": str(standard_match_path),
            "dxf_text_report": args.dxf_text_report,
            "dxf_dir": args.dxf_dir,
            "dxf_file": list(args.dxf_file),
        }
    except (OSError, json.JSONDecodeError, DxfTextExtractionError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BIZ2X5_QUANTITY_EVIDENCE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.no_write:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        outputs = write_quantity_evidence_outputs(
            report,
            args.output_dir,
            stem=f"BIZ2x5_工程量证据提取_{timestamp}",
        )
        report["outputs"] = outputs

    if args.print_summary_only:
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "phase": report["phase"],
                    "generated_at": report["generated_at"],
                    "safe_for_final_quantity_list": report["safe_for_final_quantity_list"],
                    "summary": report["summary"],
                    "outputs": report.get("outputs", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _load_text_records(args: argparse.Namespace) -> list[dict[str, object]]:
    if args.dxf_dir or args.dxf_file:
        paths = collect_dxf_files(args.dxf_dir or None, args.dxf_file)
        if not paths:
            raise DxfTextExtractionError("未找到 DXF 文件，请传入 --dxf-dir 或 --dxf-file")
        records: list[dict[str, object]] = []
        for path in paths:
            parsed = parse_dxf_file(path)
            records.extend(record.as_dict() for record in parsed.text_records)
        return records
    if args.dxf_text_report:
        report = json.loads(Path(args.dxf_text_report).read_text(encoding="utf-8"))
        records = []
        seen: set[tuple[str, int, str]] = set()
        for file_item in report.get("files", []):
            for key in ("important_texts", "text_samples"):
                for record in file_item.get(key, []):
                    dedupe = (
                        str(record.get("source_file", "")),
                        int(record.get("line_number") or 0),
                        str(record.get("text", "")),
                    )
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    records.append(record)
        return records
    return []


if __name__ == "__main__":
    raise SystemExit(main())
