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

from app.services.drawing_floor_layer_rescanner import (  # noqa: E402
    build_floor_layer_rescan_report,
    write_floor_layer_rescan_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R3-3b 地面图层定向重扫")
    parser.add_argument("--floor-paving-report", default="", help="R3-3 地面铺装有效区域定位 JSON")
    parser.add_argument("--conversion-report", default="", help="DWG 转 DXF JSON")
    parser.add_argument("--dxf-dir", default="", help="DXF 目录；为空时从 conversion-report 读取 output_files")
    parser.add_argument("--output-dir", default="../outputs/biz2x_trial", help="报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    parser.add_argument("--search-radius", type=float, default=800.0, help="材料文字附近地面线段搜索半径，CAD 单位")
    parser.add_argument("--unit-to-meter-factor", type=float, default=0.001, help="CAD 长度单位转米系数")
    parser.add_argument("--area-to-square-meter-factor", type=float, default=0.000001, help="CAD 面积单位转平方米系数")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    floor_paving_path = _resolve_input(
        args.floor_paving_report,
        output_dir,
        ["*地面铺装有效区域定位*.json"],
        "R3-3 地面铺装有效区域定位 JSON",
    )
    conversion_path = _resolve_input(
        args.conversion_report,
        output_dir,
        ["*DWG*DXF*.json"],
        "DWG 转 DXF JSON",
    )
    dxf_files = _resolve_dxf_files(args.dxf_dir, conversion_path)
    unit_conversion = {
        "unit_to_meter_factor": args.unit_to_meter_factor,
        "area_to_square_meter_factor": args.area_to_square_meter_factor,
        "source": "biz2x_floor_layer_rescan_script",
    }
    report = build_floor_layer_rescan_report(
        dxf_files=dxf_files,
        floor_paving_locator_report=_load_json(floor_paving_path),
        unit_conversion=unit_conversion,
        search_radius=args.search_radius,
    )
    report["inputs"] = {
        "floor_paving_report": str(floor_paving_path.resolve()),
        "conversion_report": str(conversion_path.resolve()),
        "dxf_files": [str(Path(path).resolve()) for path in dxf_files],
        "unit_conversion": unit_conversion,
        "search_radius": args.search_radius,
    }
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_floor_layer_rescan_outputs(
        report,
        output_dir,
        stem=f"BIZ2x_R3_地面图层定向重扫_{timestamp}",
    )
    report["outputs"] = outputs
    Path(outputs["json"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "inputs": report["inputs"], "outputs": outputs}, ensure_ascii=False, indent=2))


def _resolve_dxf_files(dxf_dir: str, conversion_path: Path) -> list[Path]:
    if dxf_dir:
        directory = Path(dxf_dir)
        if not directory.exists() or not directory.is_dir():
            raise SystemExit(f"DXF 目录不存在：{directory}")
        return sorted([*directory.glob("*.dxf"), *directory.glob("*.DXF")])
    conversion = _load_json(conversion_path)
    return [Path(path) for path in conversion.get("output_files") or []]


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
