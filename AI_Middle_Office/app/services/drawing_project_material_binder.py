from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


MATERIAL_CODE_RE = re.compile(
    r"(?<![A-Z0-9])((?:CT|ST|MT|MR|PM|PB|PT|WD|SS|DS|JD)\s*[-－]\s*\d{1,3})(?![A-Z0-9])",
    re.IGNORECASE,
)
MATERIAL_PREFIXES = {"CT", "ST", "MT", "MR", "PM", "PB", "PT", "WD", "SS", "DS", "JD"}
MIN_AREA_SQM = 0.5
MIN_LENGTH_M = 0.2
INHERITANCE_REGION_SCORE_THRESHOLD = 4.0

EXACT_BINDING_STATUS = "材料编号已绑定 CAD 证据，待 R4 规则计算"
INHERITED_BINDING_STATUS = "项目材料编号已生成区域/房间继承候选，待人工确认/R4规则校验"
NO_MATERIAL_CODE_STATUS = "未识别材料编号，待 R3 继续从材料表/做法表补证据"
MATERIAL_TABLE_ONLY_STATUS = "项目材料编号已命中材料表，但未绑定 CAD 区域/几何"
MATERIAL_CODE_ONLY_STATUS = "项目有材料编号，但未找到同编号 CAD 区域/几何候选"

PROJECT_BINDING_HEADERS = [
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料表证据",
    "材料绑定状态",
    "推荐区域编号",
    "区域面积",
    "区域周长",
    "推荐CAD候选编号",
    "建议工程量",
    "建议单位",
    "证据等级",
    "绑定说明",
    "来源文件",
]

MATERIAL_INDEX_HEADERS = [
    "材料编号",
    "材料表命中数",
    "项目命中数",
    "区域命中数",
    "CAD候选命中数",
    "材料表证据",
    "项目证据",
    "区域证据",
    "CAD证据",
]

MATERIAL_TABLE_HEADERS = [
    "材料编号",
    "材料名称",
    "规格",
    "来源文件",
    "来源分组",
    "源行号",
    "证据文本",
]

MATERIAL_INHERITANCE_HEADERS = [
    "识别项目编号",
    "项目名称",
    "单位",
    "材料编号",
    "材料名称",
    "规格",
    "候选类型",
    "候选状态",
    "候选区域编号",
    "区域面积",
    "区域周长",
    "候选CAD候选编号",
    "建议工程量",
    "建议单位",
    "继承得分",
    "证据等级",
    "继承规则",
    "继承证据",
    "来源文件",
]


def build_project_material_binding_report(
    *,
    project_report: dict[str, Any],
    region_label_report: dict[str, Any],
    geometry_report: dict[str, Any] | None = None,
    unit_conversion: dict[str, Any] | None = None,
    field_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    projects = list(project_report.get("project_rows") or [])
    regions = _collect_region_rows(region_label_report)
    geometry_candidates = _collect_geometry_candidates(
        geometry_report or {},
        region_label_report=region_label_report,
        unit_conversion=unit_conversion or {},
    )
    material_table_rows = _collect_material_table_rows(field_report or {})
    material_index = _build_material_index(projects, regions, geometry_candidates, material_table_rows)
    material_inheritance_rows = _build_material_inheritance_rows(
        projects,
        regions,
        geometry_candidates,
        material_table_rows,
        field_report or {},
    )

    binding_rows = [
        _project_binding_row(project, regions, geometry_candidates, material_table_rows, material_inheritance_rows)
        for project in projects
    ]
    material_rows = _material_index_rows(material_index)
    status_counts = Counter(row["材料绑定状态"] for row in binding_rows)
    inherited_project_ids = {
        row["识别项目编号"]
        for row in binding_rows
        if row["材料绑定状态"] == INHERITED_BINDING_STATUS
    }
    return {
        "ok": True,
        "phase": "BIZ-2x-R3-2-project-material-room-region-inheritance",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "recognized_project_count": len(projects),
            "project_with_material_code_count": sum(1 for row in binding_rows if row["材料编号"]),
            "material_code_count": len(material_rows),
            "material_table_entry_count": len(material_table_rows),
            "project_material_table_bound_count": sum(1 for row in binding_rows if row["材料表证据"]),
            "region_with_material_code_count": sum(1 for region in regions if region["material_codes"]),
            "geometry_with_material_code_count": sum(1 for candidate in geometry_candidates if candidate["material_codes"]),
            "material_region_bound_project_count": sum(1 for row in binding_rows if row["推荐区域编号"]),
            "material_geometry_bound_project_count": sum(1 for row in binding_rows if row["推荐CAD候选编号"]),
            "exact_material_region_bound_project_count": sum(1 for row in binding_rows if row["材料绑定状态"] == EXACT_BINDING_STATUS and row["推荐区域编号"]),
            "exact_material_geometry_bound_project_count": sum(1 for row in binding_rows if row["材料绑定状态"] == EXACT_BINDING_STATUS and row["推荐CAD候选编号"]),
            "material_inheritance_candidate_count": len(material_inheritance_rows),
            "material_inheritance_project_count": len({row["识别项目编号"] for row in material_inheritance_rows}),
            "material_inherited_region_candidate_project_count": len(inherited_project_ids),
            "material_name_evidence_candidate_count": sum(1 for row in material_inheritance_rows if row["候选类型"] == "材料名称/规格文字证据"),
            "material_region_inheritance_candidate_count": sum(1 for row in material_inheritance_rows if row["候选区域编号"]),
            "material_legend_risk_candidate_count": sum(1 for row in material_inheritance_rows if "图例" in row["候选状态"]),
            "unbound_project_count": sum(1 for row in binding_rows if row["材料绑定状态"] != EXACT_BINDING_STATUS),
            "binding_status_counts": dict(status_counts.most_common()),
            "final_generation_status": "blocked_until_inherited_region_candidates_are_reviewed_and_standard_rule_calculation_is_applied",
            "next_step": "review_inherited_material_region_candidates_then_use_R4_standard_quantity_calculation",
        },
        "project_binding_rows": binding_rows,
        "material_index_rows": material_rows,
        "material_table_rows": material_table_rows,
        "material_inheritance_rows": material_inheritance_rows,
        "notes": [
            "R3-2 在 R3-1 同编号绑定之外，新增材料表编号到材料名称/规格、区域类别文字、房间文字的继承候选，不直接生成最终工程量。",
            "材料编号已绑定 CAD 证据的项目仍需 R4 按 GB/T 或补充清单工程量规则计算，并保留扣减/并入 trace。",
            "继承候选尤其需要区分真实铺装区域和图例/节点小块，人工确认前不得写入四字段最终工程量。",
            "未识别材料编号的项目需要继续从材料表、做法表、房间文字或人工答案补充项目-材料关系。",
        ],
    }


def write_project_material_binding_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_R3_材料编号CAD证据绑定_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    project_csv_path = target_dir / f"{file_stem}_项目材料CAD绑定.csv"
    material_csv_path = target_dir / f"{file_stem}_材料编号索引.csv"
    material_table_csv_path = target_dir / f"{file_stem}_材料表编号证据.csv"
    material_inheritance_csv_path = target_dir / f"{file_stem}_材料继承候选.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_project_material_binding_markdown(report), encoding="utf-8")
    _write_csv(project_csv_path, report.get("project_binding_rows") or [], PROJECT_BINDING_HEADERS)
    _write_csv(material_csv_path, report.get("material_index_rows") or [], MATERIAL_INDEX_HEADERS)
    _write_csv(material_table_csv_path, report.get("material_table_rows") or [], MATERIAL_TABLE_HEADERS)
    _write_csv(material_inheritance_csv_path, report.get("material_inheritance_rows") or [], MATERIAL_INHERITANCE_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "project_binding_csv": str(project_csv_path),
        "material_index_csv": str(material_csv_path),
        "material_table_csv": str(material_table_csv_path),
        "material_inheritance_csv": str(material_inheritance_csv_path),
    }


def build_project_material_binding_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x R3-2 材料编号区域/房间继承候选报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 项目候选数：{summary.get('recognized_project_count', 0)}",
        f"- 带材料编号项目数：{summary.get('project_with_material_code_count', 0)}",
        f"- 材料编号数：{summary.get('material_code_count', 0)}",
        f"- 材料表编号证据数：{summary.get('material_table_entry_count', 0)}",
        f"- 项目命中材料表证据数：{summary.get('project_material_table_bound_count', 0)}",
        f"- 材料-区域绑定项目数：{summary.get('material_region_bound_project_count', 0)}",
        f"- 材料-CAD 候选绑定项目数：{summary.get('material_geometry_bound_project_count', 0)}",
        f"- 材料继承候选数：{summary.get('material_inheritance_candidate_count', 0)}",
        f"- 带区域的继承候选数：{summary.get('material_region_inheritance_candidate_count', 0)}",
        f"- 疑似图例/节点小块候选数：{summary.get('material_legend_risk_candidate_count', 0)}",
        f"- 绑定状态分布：{summary.get('binding_status_counts', {})}",
        "",
        "## 项目材料绑定",
        "",
        "| 项目编号 | 项目 | 材料编号 | 材料表证据 | 状态 | 推荐区域 | CAD候选 | 建议量 | 说明 |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in (report.get("project_binding_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("材料编号")),
                    _md(row.get("材料表证据")),
                    _md(row.get("材料绑定状态")),
                    _md(row.get("推荐区域编号")),
                    _md(row.get("推荐CAD候选编号")),
                    _md(f"{row.get('建议工程量')}{row.get('建议单位')}".strip()),
                    _md(row.get("绑定说明")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 材料继承候选",
            "",
            "| 项目编号 | 项目 | 材料编号 | 材料名称 | 候选类型 | 状态 | 区域 | CAD候选 | 建议量 | 规则 | 证据 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in (report.get("material_inheritance_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("材料编号")),
                    _md(row.get("材料名称")),
                    _md(row.get("候选类型")),
                    _md(row.get("候选状态")),
                    _md(row.get("候选区域编号")),
                    _md(row.get("候选CAD候选编号")),
                    _md(f"{row.get('建议工程量')}{row.get('建议单位')}".strip()),
                    _md(row.get("继承规则")),
                    _md(row.get("继承证据")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告不是最终工程量清单。",
            "- 建议工程量来自 CAD 候选或区域几何，只能作为 R4 标准规则计算输入。",
            "- 继承候选只解决材料编号与区域/房间文字之间的证据桥，人工确认前不得直接进入最终四字段 Excel。",
            "- 缺材料编号不代表项目不存在，只代表当前还缺少稳定的项目-材料-CAD 证据桥。",
        ]
    )
    return "\n".join(lines) + "\n"


def _project_binding_row(
    project: dict[str, Any],
    regions: list[dict[str, Any]],
    geometry_candidates: list[dict[str, Any]],
    material_table_rows: list[dict[str, Any]],
    material_inheritance_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    material_codes = extract_material_codes(_project_text(project))
    material_table_evidence = _material_table_evidence(material_codes, material_table_rows)
    if not material_codes:
        return _empty_project_binding_row(project, NO_MATERIAL_CODE_STATUS)
    region_matches = sorted(
        (
            _score_region(project, region, material_codes)
            for region in regions
            if set(material_codes) & set(region["material_codes"])
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    geometry_matches = sorted(
        (
            _score_geometry(project, candidate, material_codes)
            for candidate in geometry_candidates
            if set(material_codes) & set(candidate["material_codes"])
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    best_region = region_matches[0] if region_matches else None
    best_geometry = geometry_matches[0] if geometry_matches else None
    if not best_region and not best_geometry:
        inherited = _best_inheritance_candidate(project, material_codes, material_inheritance_rows)
        if inherited and inherited.get("候选区域编号"):
            return _inherited_project_binding_row(project, material_codes, material_table_evidence, inherited)
        status = MATERIAL_TABLE_ONLY_STATUS if material_table_evidence else MATERIAL_CODE_ONLY_STATUS
        explanation = status
        if inherited:
            explanation = f"{status}；已找到材料名称/规格证据，但尚无可用区域：{inherited.get('继承证据', '')}"
        return _empty_project_binding_row(project, status, material_codes, material_table_evidence, explanation)

    code_text = "、".join(material_codes)
    explanation_parts = []
    if best_region:
        explanation_parts.append("材料编号命中区域文字：" + "；".join(best_region["reasons"][:3]))
    if best_geometry:
        explanation_parts.append("材料编号命中 CAD 候选：" + "；".join(best_geometry["reasons"][:3]))
    evidence_score = max(best_region["score"] if best_region else 0.0, best_geometry["score"] if best_geometry else 0.0)
    region = best_region["region"] if best_region else None
    geometry = best_geometry["candidate"] if best_geometry else None
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": code_text,
        "材料表证据": material_table_evidence,
        "材料绑定状态": EXACT_BINDING_STATUS,
        "推荐区域编号": region.get("region_id", "") if region else geometry.get("region_id", ""),
        "区域面积": region.get("area", "") if region else geometry.get("region_area", ""),
        "区域周长": region.get("perimeter", "") if region else geometry.get("region_perimeter", ""),
        "推荐CAD候选编号": geometry.get("candidate_id", "") if geometry else "",
        "建议工程量": geometry.get("suggested_quantity", "") if geometry else _region_quantity_for_project(project, region),
        "建议单位": geometry.get("suggested_unit", "") if geometry else _region_unit_for_project(project),
        "证据等级": _confidence(evidence_score),
        "绑定说明": "；".join(explanation_parts),
        "来源文件": project.get("来源文件", ""),
    }


def _empty_project_binding_row(
    project: dict[str, Any],
    status: str,
    material_codes: list[str] | None = None,
    material_table_evidence: str = "",
    explanation: str | None = None,
) -> dict[str, Any]:
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": "、".join(material_codes or []),
        "材料表证据": material_table_evidence,
        "材料绑定状态": status,
        "推荐区域编号": "",
        "区域面积": "",
        "区域周长": "",
        "推荐CAD候选编号": "",
        "建议工程量": "",
        "建议单位": "",
        "证据等级": "",
        "绑定说明": explanation or status,
        "来源文件": project.get("来源文件", ""),
    }


def _inherited_project_binding_row(
    project: dict[str, Any],
    material_codes: list[str],
    material_table_evidence: str,
    inherited: dict[str, Any],
) -> dict[str, Any]:
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": "、".join(material_codes),
        "材料表证据": material_table_evidence,
        "材料绑定状态": INHERITED_BINDING_STATUS,
        "推荐区域编号": inherited.get("候选区域编号", ""),
        "区域面积": inherited.get("区域面积", ""),
        "区域周长": inherited.get("区域周长", ""),
        "推荐CAD候选编号": inherited.get("候选CAD候选编号", ""),
        "建议工程量": inherited.get("建议工程量", ""),
        "建议单位": inherited.get("建议单位", ""),
        "证据等级": inherited.get("证据等级", ""),
        "绑定说明": f"继承候选，非最终工程量：{inherited.get('继承规则', '')}；{inherited.get('继承证据', '')}",
        "来源文件": project.get("来源文件", ""),
    }


def _collect_region_rows(region_label_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = region_label_report.get("region_index_rows") or region_label_report.get("region_rows") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        text = _region_text(row)
        codes = extract_material_codes(text)
        result.append(
            {
                "region_id": str(row.get("区域编号") or ""),
                "source_file": str(row.get("来源文件") or ""),
                "area": _float_or_empty(row.get("CAD面积")),
                "perimeter": _float_or_empty(row.get("CAD周长")),
                "layer": str(row.get("图层") or ""),
                "entity_type": str(row.get("实体类型") or ""),
                "line_number": row.get("源行号", ""),
                "text": text,
                "material_codes": codes,
                "geometry_key": str(row.get("_geometry_key") or row.get("几何键") or ""),
            }
        )
    return result


def _collect_geometry_candidates(
    geometry_report: dict[str, Any],
    *,
    region_label_report: dict[str, Any],
    unit_conversion: dict[str, Any],
) -> list[dict[str, Any]]:
    unit_to_meter_factor = float(unit_conversion.get("unit_to_meter_factor") or 0.001)
    area_to_square_meter_factor = float(unit_conversion.get("area_to_square_meter_factor") or unit_to_meter_factor * unit_to_meter_factor)
    region_index = {
        region["geometry_key"]: region
        for region in _collect_region_rows(region_label_report)
        if region["geometry_key"]
    }
    result: list[dict[str, Any]] = []
    sequence = 1
    for file_item in geometry_report.get("files") or []:
        source_file = str(file_item.get("file_name") or "")
        for raw in file_item.get("area_candidates") or []:
            raw_area = _float_or_none(raw.get("area"))
            if raw_area is None:
                continue
            quantity = round(raw_area * area_to_square_meter_factor, 4)
            if quantity < MIN_AREA_SQM:
                continue
            result.append(
                _geometry_candidate(sequence, source_file, raw, "area", quantity, "㎡", raw_area, region_index)
            )
            sequence += 1
        for raw in file_item.get("length_candidates") or []:
            raw_length = _float_or_none(raw.get("length"))
            if raw_length is None:
                continue
            quantity = round(raw_length * unit_to_meter_factor, 4)
            if quantity < MIN_LENGTH_M:
                continue
            result.append(
                _geometry_candidate(sequence, source_file, raw, "length", quantity, "m", raw_length, region_index)
            )
            sequence += 1
        for raw in file_item.get("count_candidates") or []:
            quantity = _float_or_none(raw.get("count")) or 1.0
            result.append(_geometry_candidate(sequence, source_file, raw, "count", quantity, "个", quantity, region_index))
            sequence += 1
    return result


def _geometry_candidate(
    sequence: int,
    source_file: str,
    raw: dict[str, Any],
    quantity_kind: str,
    suggested_quantity: float,
    suggested_unit: str,
    raw_quantity: float,
    region_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = "|".join([source_file, str(raw.get("entity_type") or ""), str(raw.get("line_number") or "")])
    region = region_index.get(key) or {}
    text = " ".join(
        [
            source_file,
            str(raw.get("layer") or ""),
            str(raw.get("block_name") or ""),
            str(raw.get("quantity_hint") or ""),
            str(region.get("text") or ""),
        ]
    )
    return {
        "candidate_id": f"BIZ2xM-G{sequence:05d}",
        "source_file": source_file,
        "quantity_kind": quantity_kind,
        "suggested_quantity": suggested_quantity,
        "suggested_unit": suggested_unit,
        "raw_quantity": raw_quantity,
        "layer": str(raw.get("layer") or ""),
        "block_name": str(raw.get("block_name") or ""),
        "entity_type": str(raw.get("entity_type") or ""),
        "line_number": raw.get("line_number", ""),
        "text": text,
        "material_codes": extract_material_codes(text),
        "region_id": str(region.get("region_id") or ""),
        "region_area": region.get("area", ""),
        "region_perimeter": region.get("perimeter", ""),
    }


def _collect_material_table_rows(field_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for bucket in ("drawing_catalog_rows", "material_method_rows", "drawing_annotation_rows"):
        for row in field_report.get(bucket) or []:
            for entry in _material_entries_from_field_row(row, bucket):
                key = (
                    entry["材料编号"],
                    entry["材料名称"],
                    entry["来源文件"],
                    str(entry["源行号"]),
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(entry)
    rows.sort(key=lambda item: (item["材料编号"], item["来源文件"], str(item["源行号"]), item["材料名称"]))
    return rows


def _material_entries_from_field_row(row: dict[str, Any], bucket: str) -> list[dict[str, Any]]:
    text = _field_row_text(row)
    if not text:
        return []
    tokens = _field_tokens(text)
    entries: list[dict[str, Any]] = []
    for code in extract_material_codes(text):
        entries.append(_material_table_entry(row, bucket, code, _name_near_explicit_code(tokens, code), text))
    for index in range(0, max(0, len(tokens) - 1)):
        prefix = tokens[index].upper()
        number = tokens[index + 1]
        if prefix not in MATERIAL_PREFIXES or not re.fullmatch(r"\d{1,3}", number or ""):
            continue
        code = f"{prefix}-{number.zfill(2) if len(number) == 1 else number}"
        material_name = _name_near_split_code(tokens, index)
        if material_name:
            entries.append(_material_table_entry(row, bucket, code, material_name, text))
    return _dedupe_material_entries(entries)


def _material_table_entry(
    row: dict[str, Any],
    bucket: str,
    code: str,
    material_name: str,
    evidence_text: str,
) -> dict[str, Any]:
    fallback_name = str(row.get("material_or_method_name") or row.get("drawing_name") or "").strip()
    name = material_name or fallback_name
    return {
        "材料编号": code,
        "材料名称": name,
        "规格": _guess_spec(name, evidence_text),
        "来源文件": str(row.get("source_file") or ""),
        "来源分组": bucket,
        "源行号": row.get("source_row_number", ""),
        "证据文本": _compact_text(evidence_text, 160),
    }


def _field_row_text(row: dict[str, Any]) -> str:
    return " | ".join(
        str(row.get(key) or "")
        for key in (
            "material_or_method_name",
            "spec_or_method",
            "remark",
            "drawing_name",
            "drawing_code",
            "raw_row_text",
        )
    )


def _field_tokens(text: str) -> list[str]:
    parts = re.split(r"[|；;，,\n\r\t]+", text or "")
    return [part.strip() for part in parts if part and part.strip()]


def _name_near_split_code(tokens: list[str], prefix_index: int) -> str:
    after = _first_useful_material_name(_until_next_code_token(tokens[prefix_index + 2 : prefix_index + 5]))
    if after:
        return after
    before = tokens[prefix_index - 1] if prefix_index > 0 else ""
    return before if _looks_like_material_name(before) else ""


def _until_next_code_token(tokens: list[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        text = token.strip()
        if text.upper() in MATERIAL_PREFIXES or extract_material_codes(text):
            break
        result.append(text)
    return result


def _name_near_explicit_code(tokens: list[str], code: str) -> str:
    normalized_code = code.upper()
    for index, token in enumerate(tokens):
        if normalized_code not in token.upper().replace("－", "-"):
            continue
        before = list(reversed(tokens[max(0, index - 4) : index]))
        after = tokens[index + 1 : index + 4]
        return _first_useful_material_name(before) or _first_useful_material_name(after)
    return ""


def _first_useful_material_name(tokens: list[str]) -> str:
    for token in tokens:
        text = token.strip()
        if _looks_like_material_name(text):
            return text
    return ""


def _looks_like_material_name(text: str) -> bool:
    if len(text) < 3 or len(text) > 48:
        return False
    upper = text.upper()
    if upper in MATERIAL_PREFIXES or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return False
    if any(noise in text for noise in ("页码", "轴号", "ENTER", "REFERENCE", "SCALE")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _guess_spec(name: str, evidence_text: str) -> str:
    text = f"{name} {evidence_text}"
    match = re.search(r"\d{2,4}\s*[xX*×]\s*\d{2,4}", text)
    return match.group(0).replace(" ", "") if match else ""


def _dedupe_material_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry["材料编号"], entry["材料名称"])
        if not entry["材料编号"] or key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _material_table_evidence(material_codes: list[str], material_table_rows: list[dict[str, Any]]) -> str:
    if not material_codes:
        return ""
    parts: list[str] = []
    for row in material_table_rows:
        if row["材料编号"] not in material_codes:
            continue
        name = row.get("材料名称") or ""
        spec = f" {row.get('规格')}" if row.get("规格") else ""
        source = f"{row.get('来源文件', '')}:{row.get('源行号', '')}".strip(":")
        parts.append(f"{row['材料编号']}={name}{spec}（{source}）")
    return "；".join(_dedupe(parts)[:5])


def _build_material_inheritance_rows(
    projects: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    geometry_candidates: list[dict[str, Any]],
    material_table_rows: list[dict[str, Any]],
    field_report: dict[str, Any],
) -> list[dict[str, Any]]:
    field_evidence_rows = _collect_field_evidence_rows(field_report)
    rows: list[dict[str, Any]] = []
    for project in projects:
        material_codes = extract_material_codes(_project_text(project))
        if not material_codes:
            continue
        for code in material_codes:
            material_entries = [row for row in material_table_rows if row["材料编号"] == code]
            if not material_entries:
                continue
            for entry in material_entries:
                rows.extend(_field_material_inheritance_candidates(project, entry, field_evidence_rows))
                rows.extend(_region_material_inheritance_candidates(project, entry, regions))
                rows.extend(_geometry_material_inheritance_candidates(project, entry, geometry_candidates))
    return _dedupe_inheritance_rows(rows)


def _collect_field_evidence_rows(field_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in ("drawing_catalog_rows", "material_method_rows", "drawing_annotation_rows"):
        for row in field_report.get(bucket) or []:
            text = _field_row_text(row)
            if not text:
                continue
            rows.append(
                {
                    "bucket": bucket,
                    "source_file": str(row.get("source_file") or ""),
                    "source_row_number": row.get("source_row_number", ""),
                    "text": text,
                    "material_or_method_name": str(row.get("material_or_method_name") or ""),
                    "raw_row_text": str(row.get("raw_row_text") or text),
                }
            )
    return rows


def _field_material_inheritance_candidates(
    project: dict[str, Any],
    material_entry: dict[str, Any],
    field_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field_row in field_rows:
        score, reasons = _score_material_text_match(material_entry, field_row["text"])
        if score < 4.5:
            continue
        if _same_source_file(project, field_row["source_file"]):
            score += 1.0
            reasons.append("来源文件一致")
        elif material_entry.get("来源文件") == field_row["source_file"]:
            score += 0.5
            reasons.append("材料表来源文件一致")
        score = round(score, 2)
        source = f"{field_row['source_file']}:{field_row['source_row_number']}".strip(":")
        rows.append(
            _inheritance_row(
                project,
                material_entry,
                candidate_type="材料名称/规格文字证据",
                status="材料名称/规格已在图纸文字中出现，尚未绑定区域",
                score=score,
                rule="材料表编号 -> 材料名称/规格 -> 图纸文字",
                evidence=f"{'；'.join(reasons[:4])}（{source}）",
                source_file=field_row["source_file"],
            )
        )
    return rows


def _region_material_inheritance_candidates(
    project: dict[str, Any],
    material_entry: dict[str, Any],
    regions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for region in regions:
        score, reasons = _score_region_material_inheritance(project, material_entry, region)
        if score < INHERITANCE_REGION_SCORE_THRESHOLD:
            continue
        score = round(score, 2)
        status = "区域/房间材料继承候选，需人工确认"
        risk_reasons = _region_inheritance_risks(project, region)
        if risk_reasons:
            status = "疑似图例/节点小块继承候选，需人工排除"
            reasons.extend(risk_reasons)
        rows.append(
            _inheritance_row(
                project,
                material_entry,
                candidate_type="区域/房间文字继承候选",
                status=status,
                score=score,
                rule="材料表编号 -> 材料名称/类别 -> CAD 区域/房间文字",
                evidence="；".join(reasons[:6]),
                source_file=region["source_file"],
                region=region,
            )
        )
    return rows


def _geometry_material_inheritance_candidates(
    project: dict[str, Any],
    material_entry: dict[str, Any],
    geometry_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected = _expected_quantity_kind(project)
    for candidate in geometry_candidates:
        if candidate["quantity_kind"] != expected:
            continue
        score, reasons = _score_material_text_match(material_entry, candidate["text"])
        if score < 4.0:
            continue
        if _same_source_file(project, candidate["source_file"]):
            score += 1.0
            reasons.append("来源文件一致")
        score += 1.0
        reasons.append("CAD 候选类型与项目单位兼容")
        score = round(score, 2)
        status = "CAD 几何继承候选，需人工确认"
        rows.append(
            _inheritance_row(
                project,
                material_entry,
                candidate_type="CAD几何文字继承候选",
                status=status,
                score=score,
                rule="材料表编号 -> 材料名称/类别 -> CAD 几何候选文字",
                evidence="；".join(reasons[:6]),
                source_file=candidate["source_file"],
                geometry=candidate,
            )
        )
    return rows


def _inheritance_row(
    project: dict[str, Any],
    material_entry: dict[str, Any],
    *,
    candidate_type: str,
    status: str,
    score: float,
    rule: str,
    evidence: str,
    source_file: str,
    region: dict[str, Any] | None = None,
    geometry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "材料编号": material_entry.get("材料编号", ""),
        "材料名称": material_entry.get("材料名称", ""),
        "规格": material_entry.get("规格", ""),
        "候选类型": candidate_type,
        "候选状态": status,
        "候选区域编号": region.get("region_id", "") if region else geometry.get("region_id", "") if geometry else "",
        "区域面积": region.get("area", "") if region else geometry.get("region_area", "") if geometry else "",
        "区域周长": region.get("perimeter", "") if region else geometry.get("region_perimeter", "") if geometry else "",
        "候选CAD候选编号": geometry.get("candidate_id", "") if geometry else "",
        "建议工程量": geometry.get("suggested_quantity", "") if geometry else _region_quantity_for_project(project, region),
        "建议单位": geometry.get("suggested_unit", "") if geometry else _region_unit_for_project(project) if region else "",
        "继承得分": score,
        "证据等级": _confidence(score),
        "继承规则": rule,
        "继承证据": evidence,
        "来源文件": source_file,
    }


def _score_region_material_inheritance(
    project: dict[str, Any],
    material_entry: dict[str, Any],
    region: dict[str, Any],
) -> tuple[float, list[str]]:
    score, reasons = _score_material_text_match(material_entry, region["text"])
    prefix = str(material_entry.get("材料编号") or "").split("-")[0]
    normalized_region = _normalize(region["text"])
    if prefix and prefix.lower() in normalized_region:
        score += 3.0
        reasons.append(f"区域文字出现材料类别 {prefix}")
    project_terms = _project_terms(project)
    term_hits = [term for term in project_terms if term and term in normalized_region]
    if term_hits:
        score += min(2.0, len(term_hits) * 0.8)
        reasons.append("区域文字命中项目关键词：" + "、".join(term_hits[:4]))
    if _same_source_file(project, region["source_file"]):
        score += 1.0
        reasons.append("来源文件一致")
    if _expected_quantity_kind(project) == "area" and region.get("area"):
        score += 0.5
        reasons.append("区域有面积候选")
    if _expected_quantity_kind(project) == "length" and region.get("perimeter"):
        score += 0.5
        reasons.append("区域有周长候选")
    return score, reasons


def _score_material_text_match(material_entry: dict[str, Any], text: str) -> tuple[float, list[str]]:
    normalized = _normalize(text)
    score = 0.0
    reasons: list[str] = []
    name = str(material_entry.get("材料名称") or "")
    spec = str(material_entry.get("规格") or "")
    normalized_name = _normalize(name)
    normalized_spec = _normalize(spec)
    if normalized_name and normalized_name in normalized:
        score += 5.0
        reasons.append("命中完整材料名称")
    if normalized_spec and normalized_spec in normalized:
        score += 3.0
        reasons.append("命中材料规格")
    family_terms = _material_family_terms(name)
    family_hits = [term for term in family_terms if term in normalized]
    if family_hits:
        score += min(2.0, len(family_hits) * 1.0)
        reasons.append("命中材料类别：" + "、".join(family_hits[:4]))
    color_hits = [term for term in _material_color_terms(name) if term in normalized]
    if color_hits:
        score += min(1.0, len(color_hits) * 0.5)
        reasons.append("命中材料颜色：" + "、".join(color_hits[:3]))
    compact_name_without_spec = normalized_name.replace(normalized_spec, "") if normalized_spec else normalized_name
    if compact_name_without_spec and compact_name_without_spec != normalized_name and compact_name_without_spec in normalized:
        score += 1.0
        reasons.append("命中去规格材料名称")
    return score, reasons


def _material_family_terms(name: str) -> list[str]:
    normalized = _normalize(name)
    mapping = [
        (("地砖", "玻化砖", "瓷砖"), ["地砖", "玻化砖", "瓷砖"]),
        (("墙面砖", "墙砖"), ["墙面砖", "墙砖", "瓷砖"]),
        (("踢脚线",), ["踢脚", "踢脚线"]),
        (("无机涂料", "涂料"), ["无机涂料", "涂料"]),
        (("石膏板",), ["石膏板"]),
        (("吊顶",), ["吊顶", "天棚"]),
        (("不锈钢",), ["不锈钢", "金属"]),
        (("铝板",), ["铝板", "金属"]),
    ]
    terms: list[str] = []
    for needles, values in mapping:
        if any(needle in normalized for needle in needles):
            terms.extend(values)
    return _dedupe(terms)


def _material_color_terms(name: str) -> list[str]:
    colors = ("灰色", "白色", "黑色", "铁灰色", "深咖", "素色")
    return [color for color in colors if color in name]


def _region_inheritance_risks(project: dict[str, Any], region: dict[str, Any]) -> list[str]:
    text = region["text"]
    area = _float_or_none(region.get("area"))
    risks: list[str] = []
    if any(token in text for token in ("图例", "页码", "序号", "ENTER NUMBER", "Ref ID", "轴号")):
        risks.append("区域文字含图例/页码/序号类干扰")
    if _expected_quantity_kind(project) == "area" and area is not None and area < 2.0:
        risks.append("面积小于 2㎡，疑似节点/图例小块")
    return risks


def _dedupe_inheritance_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for row in sorted(rows, key=lambda item: (-float(item.get("继承得分") or 0), item.get("识别项目编号", ""), item.get("材料编号", ""))):
        key = (
            str(row.get("识别项目编号", "")),
            str(row.get("材料编号", "")),
            str(row.get("候选类型", "")),
            str(row.get("候选区域编号", "")),
            str(row.get("候选CAD候选编号", "")),
            str(row.get("继承证据", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _best_inheritance_candidate(
    project: dict[str, Any],
    material_codes: list[str],
    material_inheritance_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    project_id = str(project.get("识别项目编号") or "")
    candidates = [
        row
        for row in material_inheritance_rows
        if row.get("识别项目编号") == project_id and row.get("材料编号") in material_codes
    ]
    if not candidates:
        return None
    region_candidates = [
        row
        for row in candidates
        if row.get("候选区域编号") and "图例" not in str(row.get("候选状态") or "")
    ]
    if region_candidates:
        return sorted(region_candidates, key=lambda item: float(item.get("继承得分") or 0), reverse=True)[0]
    return sorted(candidates, key=lambda item: float(item.get("继承得分") or 0), reverse=True)[0]


def _build_material_index(
    projects: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    geometry_candidates: list[dict[str, Any]],
    material_table_rows: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"project": [], "region": [], "geometry": [], "material_table": []})
    for row in material_table_rows:
        label = f"{row['材料名称']} {row['规格']} {row['来源文件']}:{row['源行号']}".strip()
        index[row["材料编号"]]["material_table"].append(label)
    for project in projects:
        label = f"{project.get('识别项目编号', '')} {project.get('项目名称', '')}"
        for code in extract_material_codes(_project_text(project)):
            index[code]["project"].append(label)
    for region in regions:
        label = f"{region['region_id']} {region['source_file']} {region['text'][:80]}"
        for code in region["material_codes"]:
            index[code]["region"].append(label)
    for candidate in geometry_candidates:
        label = f"{candidate['candidate_id']} {candidate['source_file']} {candidate['layer']}"
        for code in candidate["material_codes"]:
            index[code]["geometry"].append(label)
    return index


def _material_index_rows(index: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in sorted(index):
        buckets = index[code]
        rows.append(
            {
                "材料编号": code,
                "材料表命中数": len(_dedupe(buckets["material_table"])),
                "项目命中数": len(_dedupe(buckets["project"])),
                "区域命中数": len(_dedupe(buckets["region"])),
                "CAD候选命中数": len(_dedupe(buckets["geometry"])),
                "材料表证据": "；".join(_dedupe(buckets["material_table"])[:8]),
                "项目证据": "；".join(_dedupe(buckets["project"])[:8]),
                "区域证据": "；".join(_dedupe(buckets["region"])[:8]),
                "CAD证据": "；".join(_dedupe(buckets["geometry"])[:8]),
            }
        )
    return rows


def _score_region(project: dict[str, Any], region: dict[str, Any], material_codes: list[str]) -> dict[str, Any]:
    score = 6.0
    reasons = ["命中材料编号：" + "、".join(sorted(set(material_codes) & set(region["material_codes"])))]
    if _same_source_file(project, region["source_file"]):
        score += 2.0
        reasons.append("来源文件一致")
    project_terms = _project_terms(project)
    term_hits = [term for term in project_terms if term and term in _normalize(region["text"])]
    if term_hits:
        score += min(3.0, len(term_hits) * 1.0)
        reasons.append("区域文字命中项目关键词：" + "、".join(term_hits[:4]))
    return {"region": region, "score": round(score, 2), "reasons": reasons}


def _score_geometry(project: dict[str, Any], candidate: dict[str, Any], material_codes: list[str]) -> dict[str, Any]:
    score = 6.0
    reasons = ["命中材料编号：" + "、".join(sorted(set(material_codes) & set(candidate["material_codes"])))]
    if _same_source_file(project, candidate["source_file"]):
        score += 2.0
        reasons.append("来源文件一致")
    expected = _expected_quantity_kind(project)
    if expected == candidate["quantity_kind"]:
        score += 2.0
        reasons.append("CAD 候选类型与项目单位兼容")
    return {"candidate": candidate, "score": round(score, 2), "reasons": reasons}


def extract_material_codes(text: str) -> list[str]:
    values = []
    for match in MATERIAL_CODE_RE.finditer(text or ""):
        values.append(match.group(1).upper().replace("－", "-").replace(" ", ""))
    return _dedupe(values)


def _project_text(project: dict[str, Any]) -> str:
    return " ".join(
        str(project.get(key) or "")
        for key in ("图纸项目名称", "项目名称", "项目特征", "识别证据", "匹配理由")
    )


def _region_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("来源文件", "图层", "区域内文字", "附近文字", "房间/空间标签", "项目标签", "区域类型建议")
    )


def _project_terms(project: dict[str, Any]) -> list[str]:
    text = _normalize(_project_text(project))
    candidates = ("地砖", "地面", "石材", "吊顶", "天棚", "窗帘盒", "窗台", "踢脚", "防水", "涂料", "隔墙")
    return [term for term in candidates if term in text]


def _expected_quantity_kind(project: dict[str, Any]) -> str:
    unit = str(project.get("单位") or "")
    if unit in {"㎡", "m²", "m2", "平方米"}:
        return "area"
    if unit in {"m", "米"}:
        return "length"
    if unit in {"个", "套", "樘", "项"}:
        return "count"
    return "area"


def _region_quantity_for_project(project: dict[str, Any], region: dict[str, Any] | None) -> Any:
    if not region:
        return ""
    return region.get("perimeter") if _expected_quantity_kind(project) == "length" else region.get("area")


def _region_unit_for_project(project: dict[str, Any]) -> str:
    return "m" if _expected_quantity_kind(project) == "length" else "㎡"


def _same_source_file(project: dict[str, Any], source_file: str) -> bool:
    project_source = _normalize(str(project.get("来源文件") or ""))
    source = _normalize(source_file)
    return bool(source and source in project_source)


def _confidence(score: float) -> str:
    if score >= 9:
        return f"高({score})"
    if score >= 6:
        return f"中({score})"
    if score > 0:
        return f"低({score})"
    return ""


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_empty(value: Any) -> float | str:
    number = _float_or_none(value)
    return "" if number is None else number


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit]


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
