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

from app.services.drawing_project_material_binder import (
    build_project_material_binding_report,
    write_project_material_binding_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="BIZ-2x R3-2 项目材料编号区域/房间继承候选")
    parser.add_argument("--project-report", default="", help="图纸项目识别 JSON")
    parser.add_argument("--region-label-report", default="", help="CAD 区域文字绑定 JSON")
    parser.add_argument("--geometry-report", default="", help="CAD 几何图元探测 JSON")
    parser.add_argument("--field-report", default="", help="DXF 字段收敛 JSON")
    parser.add_argument("--output-dir", default="../outputs/biz2x_trial", help="报告输出目录")
    parser.add_argument("--timestamp", default="", help="输出时间戳")
    parser.add_argument("--unit-to-meter-factor", type=float, default=0.001, help="CAD 长度单位转米系数")
    parser.add_argument("--area-to-square-meter-factor", type=float, default=0.000001, help="CAD 面积单位转平方米系数")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    project_report_path = _resolve_input(
        args.project_report,
        output_dir,
        ["*图纸项目识别*R1_3.json", "*图纸项目识别*.json"],
        "图纸项目识别 JSON",
    )
    region_label_report_path = _resolve_input(
        args.region_label_report,
        output_dir,
        ["*CAD区域文字绑定*.json"],
        "CAD 区域文字绑定 JSON",
    )
    geometry_report_path = _resolve_input(
        args.geometry_report,
        output_dir,
        ["*CAD几何图元探测*.json"],
        "CAD 几何图元探测 JSON",
    )
    field_report_path = _resolve_input(
        args.field_report,
        output_dir,
        ["*DXF字段收敛*R1_2.json", "*DXF字段收敛*.json"],
        "DXF 字段收敛 JSON",
    )
    unit_conversion = {
        "unit_to_meter_factor": args.unit_to_meter_factor,
        "area_to_square_meter_factor": args.area_to_square_meter_factor,
        "source": "biz2x_project_material_binding_script",
    }

    report = build_project_material_binding_report(
        project_report=_load_json(project_report_path),
        region_label_report=_load_json(region_label_report_path),
        geometry_report=_load_json(geometry_report_path),
        unit_conversion=unit_conversion,
        field_report=_load_json(field_report_path),
    )
    report["inputs"] = {
        "project_report": str(project_report_path.resolve()),
        "region_label_report": str(region_label_report_path.resolve()),
        "geometry_report": str(geometry_report_path.resolve()),
        "field_report": str(field_report_path.resolve()),
        "unit_conversion": unit_conversion,
    }
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    outputs = write_project_material_binding_outputs(
        report,
        output_dir,
        stem=f"BIZ2x_R3_材料编号区域房间继承候选_{timestamp}",
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
        pattern_text = " / ".join(patterns)
        raise SystemExit(f"未在 {output_dir} 找到 {label}，匹配规则：{pattern_text}")
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
