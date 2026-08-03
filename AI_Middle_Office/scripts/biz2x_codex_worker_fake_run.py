from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.codex_worker_contract import run_codex_worker_contract  # noqa: E402
from app.services.codex_worker_fake import build_fake_codex_result as build_service_fake_codex_result  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2x Codex Worker POC fake contract runner")
    parser.add_argument("--input-json", default="", help="已有 codex_result.json；为空时生成 fake 样例")
    parser.add_argument("--job-id", default="", help="任务 ID；为空时自动生成")
    parser.add_argument(
        "--job-root",
        default=str(BACKEND_ROOT / "runtime" / "codex_worker" / "jobs"),
        help="任务根目录，默认 AI_Middle_Office/runtime/codex_worker/jobs",
    )
    parser.add_argument(
        "--sample",
        choices=["valid", "missing-field", "non-construction", "missing-evidence"],
        default="valid",
        help="未提供 input-json 时生成哪种 fake 样例",
    )
    args = parser.parse_args()

    job_id = args.job_id or f"fake_codex_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job_dir = Path(args.job_root) / job_id
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_json:
        codex_result_path = Path(args.input_json)
    else:
        codex_result_path = output_dir / "codex_result.json"
        codex_result_path.write_text(
            json.dumps(build_service_fake_codex_result(args.sample, job_id=job_id), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    result = run_codex_worker_contract(codex_result_path, output_dir, excel_stem="four_field")
    payload = {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "job_id": job_id,
        "job_dir": str(job_dir.resolve()),
        "codex_result_json": str(codex_result_path.resolve()),
        **result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1

if __name__ == "__main__":
    raise SystemExit(main())
