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

from app.services.drawing_floor_paving_locator import (  # noqa: E402
    build_floor_paving_locator_report,
    write_floor_paving_locator_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R3-3 地面铺装有效区域定位")
    parser.add_argument("--project-material-report", default="", help="R3-2 项目材料继承候选 JSON")
    parser.add_argument("--field-report", default="", help="DXF 字段收敛 JSON")
    parser.add_argument("--geometry-report", default="", help="CAD 几何图元探测 JSON")
    parser.add_argument("--region-label-report", default="", help="CAD 区域文字绑定 JSON")
    parser.add_argument("--output-dir", default="../outputs/biz2x_trial", help="报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    parser.add_argument("--unit-to-meter-factor", type=float, default=0.001, help="CAD 长度单位转米系数")
    parser.add_argument("--area-to-square-meter-factor", type=float, default=0.000001, help="CAD 面积单位转平方米系数")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    project_material_path = _resolve_input(
        args.project_material_report,
        output_dir,
        ["*材料编号区域房间继承候选*.json", "*材料编号CAD证据绑定*.json"],
        "R3 项目材料报告 JSON",
    )
    field_report_path = _resolve_input(
        args.field_report,
        output_dir,
        ["*DXF字段收敛*R1_2.json", "*DXF字段收敛*.json"],
        "DXF 字段收敛 JSON",
    )
    geometry_report_path = _resolve_input(
        args.geometry_report,
        output_dir,
        ["*CAD几何图元探测*.json"],
        "CAD 几何图元探测 JSON",
    )
    region_label_path = _resolve_input(
        args.region_label_report,
        output_dir,
        ["*CAD区域文字绑定*.json"],
        "CAD 区域文字绑定 JSON",
    )
    unit_conversion = {
        "unit_to_meter_factor": args.unit_to_meter_factor,
        "area_to_square_meter_factor": args.area_to_square_meter_factor,
        "source": "biz2x_floor_paving_locator_script",
    }
    report = build_floor_paving_locator_report(
        project_material_binding_report=_load_json(project_material_path),
        field_report=_load_json(field_report_path),
        geometry_report=_load_json(geometry_report_path),
        region_label_report=_load_json(region_label_path),
        unit_conversion=unit_conversion,
    )
    report["inputs"] = {
        "project_material_report": str(project_material_path.resolve()),
        "field_report": str(field_report_path.resolve()),
        "geometry_report": str(geometry_report_path.resolve()),
        "region_label_report": str(region_label_path.resolve()),
        "unit_conversion": unit_conversion,
    }
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_floor_paving_locator_outputs(
        report,
        output_dir,
        stem=f"BIZ2x_R3_地面铺装有效区域定位_{timestamp}",
    )
    report["outputs"] = outputs
    Path(outputs["json"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "summary": report["summary"],
                "inputs": report["inputs"],
                "outputs": outputs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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
