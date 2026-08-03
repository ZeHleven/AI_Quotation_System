from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.requirement_standardizer import (  # noqa: E402
    RequirementStandardizationError,
    standardize_requirement_excel_path,
    write_standardization_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BIZ-2l-1 甲方需求单只读标准化预览")
    parser.add_argument("excel_file", help="待解析的 .xlsx/.xlsm 需求单")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT.parent / "outputs" / "biz2l"),
        help="预览报告输出目录，默认 outputs/biz2l",
    )
    args = parser.parse_args()

    excel_path = Path(args.excel_file)
    try:
        path_exists = excel_path.exists()
        path_is_file = excel_path.is_file() if path_exists else False
    except PermissionError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "PERMISSION_DENIED",
                    "message": f"没有权限读取需求单文件：{excel_path}",
                    "usage": "请先把文件复制到项目目录下，例如 AI_Middle_Office\\biz2l_reports\\，再重新运行预览脚本。",
                    "detail": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not path_exists:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "FILE_NOT_FOUND",
                    "message": f"未找到需求单文件：{excel_path}",
                    "usage": "请把命令里的“你的需求单.xlsx”替换成真实文件名或完整路径，例如：.\\scripts\\biz2l_requirement_standardization_preview.py \"C:\\\\path\\\\需求单.xlsx\"",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not path_is_file:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "NOT_A_FILE",
                    "message": f"路径不是文件：{excel_path}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        result = standardize_requirement_excel_path(excel_path)
    except RequirementStandardizationError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "STANDARDIZATION_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"requirement_standardization_{excel_path.stem}_{timestamp}"
    output_dir = Path(args.output_dir)
    try:
        paths = write_standardization_outputs(result, output_dir, stem=stem)
    except OSError as exc:
        fallback_dir = BACKEND_ROOT / "biz2l_reports"
        try:
            paths = write_standardization_outputs(result, fallback_dir, stem=stem)
        except OSError:
            raise
        paths["fallback_reason"] = f"默认输出目录不可写，已回退到 {fallback_dir}: {exc}"

    print(
        json.dumps(
            {
                "ok": True,
                "summary": result["summary"],
                "outputs": paths,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
