from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.drawing_floor_region_reconstructor import (  # noqa: E402
    build_floor_region_reconstruction_report,
    write_floor_region_reconstruction_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R3-3c 地面线段闭合区域重构")
    parser.add_argument("--floor-layer-report", default="", help="R3-3b 地面图层定向重扫 JSON")
    parser.add_argument("--output-dir", default="../outputs/biz2x_trial", help="报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    parser.add_argument("--snap-tolerance", type=float, default=2.0, help="端点/轴线吸附容差，CAD 单位")
    parser.add_argument(
        "--area-to-square-meter-factor",
        type=float,
        default=0.0,
        help="CAD 面积单位转平方米系数；为 0 时优先读取 R3-3b 输入",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    floor_layer_path = _resolve_input(
        args.floor_layer_report,
        output_dir,
        ["*地面图层定向重扫*.json"],
        "R3-3b 地面图层定向重扫 JSON",
    )
    floor_layer_report = _load_json(floor_layer_path)
    area_factor = args.area_to_square_meter_factor if args.area_to_square_meter_factor > 0 else None
    report = build_floor_region_reconstruction_report(
        floor_layer_rescan_report=floor_layer_report,
        area_to_square_meter_factor=area_factor,
        snap_tolerance=args.snap_tolerance,
    )
    report["inputs"] = {
        "floor_layer_report": str(floor_layer_path.resolve()),
        "snap_tolerance": args.snap_tolerance,
        "area_to_square_meter_factor": area_factor,
    }
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_floor_region_reconstruction_outputs(
        report,
        output_dir,
        stem=f"BIZ2x_R3_地面线段闭合区域重构_{timestamp}",
    )
    report["outputs"] = outputs
    Path(outputs["json"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "inputs": report["inputs"], "outputs": outputs}, ensure_ascii=False, indent=2))


def _resolve_input(value: str, output_dir: Path, patterns: list[str], label: str) -> Path:
    if value:
        path = Path(value)
        if not path.exists():
            raise SystemExit(f"{label} 不存在：{path}")
        return path
    latest = _latest_json(output_dir, patterns)
    if latest is None:
        raise SystemExit(f"未在 {output_dir} 找到 {label}，匹配规则：{' / '.join(patterns)}")
    return latest


def _latest_json(output_dir: Path, patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(output_dir.glob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
