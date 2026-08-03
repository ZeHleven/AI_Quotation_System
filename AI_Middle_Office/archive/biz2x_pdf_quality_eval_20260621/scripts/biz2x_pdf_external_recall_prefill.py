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

from app.services.drawing_pdf_external_recall_prefill import (  # noqa: E402
    build_external_recall_prefill_report,
    load_external_recall_template_rows,
    write_external_recall_prefill_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefill BIZ-2x PDF external recall template from local V2 evidence")
    parser.add_argument("--external-template", required=True, help="External recall template XLSX/XLSM/CSV/JSON")
    parser.add_argument("--v2-json", required=True, help="PDF V2 takeoff JSON containing local evidence_rows")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "pdf_v2_takeoff" / "external_recall_prefill"),
    )
    parser.add_argument("--match-mode", choices=["exact_tile", "source_page"], default="exact_tile")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing evidence fields in the template")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--stem", default="")
    args = parser.parse_args()

    template_rows = load_external_recall_template_rows(args.external_template)
    v2_report = json.loads(Path(args.v2_json).read_text(encoding="utf-8"))
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = args.stem or f"BIZ2x_PDF_external_recall_prefill_{timestamp}"
    report = build_external_recall_prefill_report(
        template_rows,
        v2_report,
        match_mode=args.match_mode,
        overwrite=args.overwrite,
    )
    outputs = write_external_recall_prefill_outputs(report, Path(args.output_dir), stem=stem)
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


if __name__ == "__main__":
    raise SystemExit(main())
