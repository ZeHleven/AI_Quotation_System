from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.bidding_technical_regression import (  # noqa: E402
    build_technical_bid_regression_report,
    write_technical_bid_regression_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-4c4 P5 技术标真实样本回归评估")
    parser.add_argument("--official", required=True, help="正式技术标样本，支持 .docx/.txt/.md")
    parser.add_argument("--generated", required=True, help="系统生成技术标样本，支持 .docx/.txt/.md")
    parser.add_argument("--out-dir", default="../output/biz4c4_p5", help="输出目录")
    parser.add_argument("--stem", default="", help="输出文件名前缀")
    parser.add_argument("--json-only", action="store_true", help="仅在 stdout 输出 JSON，不写文件")
    args = parser.parse_args()

    if args.json_only:
        report = build_technical_bid_regression_report(args.official, args.generated)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    outputs = write_technical_bid_regression_outputs(
        args.official,
        args.generated,
        args.out_dir,
        stem=args.stem or None,
    )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
