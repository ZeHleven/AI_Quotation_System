from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.enterprise_quota_phase0 import (  # noqa: E402
    EnterpriseQuotaPhase0Error,
    preview_enterprise_quota_file,
    write_phase0_outputs,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BIZ-2x enterprise quota Phase 0 read-only parser preview.")
    parser.add_argument("quota_file", help="Enterprise quota workbook: .xls/.xlsx/.xlsm")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "outputs" / "biz2x_enterprise_quota_phase0"),
        help="Output directory for JSON/Markdown/CSV reports.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    quota_path = Path(args.quota_file)
    try:
        result = preview_enterprise_quota_file(quota_path)
    except EnterpriseQuotaPhase0Error as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "ENTERPRISE_QUOTA_PHASE0_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"enterprise_quota_phase0_{quota_path.stem}_{timestamp}"
    output_dir = Path(args.output_dir)
    try:
        outputs = write_phase0_outputs(result, output_dir, stem=stem)
    except OSError as exc:
        fallback_dir = BACKEND_ROOT / "biz2x_enterprise_quota_phase0_reports"
        outputs = write_phase0_outputs(result, fallback_dir, stem=stem)
        outputs["fallback_reason"] = f"Default output directory is not writable; used {fallback_dir}: {exc}"

    print(
        json.dumps(
            {
                "ok": result["ok"],
                "summary": result["summary"],
                "source": result["source"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
