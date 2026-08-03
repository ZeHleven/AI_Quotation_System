from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


PHASE = "BIZ-2x-pdf-dxf-item-fusion"

FOUR_FIELD_HEADERS = ["项目名称", "项目特征", "单位", "工程量"]

FUSION_DETAIL_HEADERS = [
    "融合行号",
    "项目名称",
    "项目特征",
    "单位",
    "工程量",
    "融合来源",
    "标准项目编码",
    "标准项目名称",
    "具体项目名称",
    "PDF识别编号",
    "DWG识别项目编号",
    "去重键",
    "合并说明",
    "风险提示",
]


def build_pdf_dxf_item_fusion_report(
    *,
    dwg_quantity_list_rows: list[Mapping[str, Any]],
    dwg_project_rows: list[Mapping[str, Any]] | None = None,
    pdf_direct_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse PDF direct itemization rows with DWG/DXF rows for business-facing four fields.

    PDF rows are preferred for item completeness and concrete business names.
    DWG/DXF rows remain the safer source for quantities; any ready quantity from DWG
    is carried into the fused row when the row is merged.
    """

    dwg_sources = _dwg_sources(dwg_quantity_list_rows, dwg_project_rows or [])
    pdf_sources = _pdf_sources(pdf_direct_report or {})

    merged: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for source in [*pdf_sources, *dwg_sources]:
        key = source["去重键"]
        if not key:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = dict(source)
            continue
        duplicate_count += 1
        _merge_source_into(current, source)

    detail_rows = list(merged.values())
    detail_rows.sort(key=lambda row: (_source_order(row.get("融合来源")), row.get("融合行号", "")))
    for index, row in enumerate(detail_rows, start=1):
        row["融合行号"] = f"FUSE-{index:06d}"

    quantity_list_rows = [
        {header: row.get(header, "") for header in FOUR_FIELD_HEADERS}
        for row in detail_rows
    ]
    source_counts = Counter(row.get("融合来源", "") for row in detail_rows)
    status_counts = Counter(_quantity_status(row.get("工程量")) for row in detail_rows)
    return {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "dwg_quantity_list_count": len(dwg_sources),
            "pdf_direct_quantity_list_count": len(pdf_sources),
            "fused_quantity_list_count": len(quantity_list_rows),
            "fusion_duplicate_suppressed_count": duplicate_count,
            "pdf_dxf_fusion_source_counts": dict(source_counts.most_common()),
            "pdf_dxf_fusion_quantity_status_counts": dict(status_counts.most_common()),
            "pdf_direct_used_as_primary": bool(pdf_sources),
            "quantity_policy": "PDF负责列项完整性，DWG/DXF负责可信算量；无可靠算量证据则保留待算量",
        },
        "quantity_list_rows": quantity_list_rows,
        "fusion_rows": detail_rows,
    }


def write_pdf_dxf_item_fusion_outputs(
    report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    stem: str,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    csv_path = directory / f"{stem}_融合明细.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown(report), encoding="utf-8")
    _write_csv(csv_path, list(report.get("fusion_rows") or []), FUSION_DETAIL_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "fusion_csv": str(csv_path),
    }


def enrich_summary_with_pdf_dxf_fusion(
    summary: Mapping[str, Any],
    fusion_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    updated = dict(summary)
    fusion_summary = (fusion_report or {}).get("summary") or {}
    if fusion_summary:
        updated.update(
            {
                "dwg_quantity_list_count": fusion_summary.get("dwg_quantity_list_count", 0),
                "pdf_direct_quantity_list_count": fusion_summary.get("pdf_direct_quantity_list_count", 0),
                "fused_quantity_list_count": fusion_summary.get("fused_quantity_list_count", 0),
                "fusion_duplicate_suppressed_count": fusion_summary.get("fusion_duplicate_suppressed_count", 0),
                "pdf_dxf_fusion_source_counts": fusion_summary.get("pdf_dxf_fusion_source_counts", {}),
                "pdf_dxf_fusion_quantity_status_counts": fusion_summary.get("pdf_dxf_fusion_quantity_status_counts", {}),
                "pdf_direct_used_as_primary": fusion_summary.get("pdf_direct_used_as_primary", False),
                "final_generation_status": (
                    "pdf_direct_itemization_primary_quantity_pending"
                    if fusion_summary.get("pdf_direct_used_as_primary")
                    else updated.get("final_generation_status", "")
                ),
            }
        )
    return updated


def _dwg_sources(
    quantity_rows: list[Mapping[str, Any]],
    project_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    project_by_index = {
        index: project
        for index, project in enumerate(project_rows)
    }
    sources: list[dict[str, Any]] = []
    for index, row in enumerate(quantity_rows):
        project = project_by_index.get(index, {})
        concrete_name = _clean_text(project.get("图纸项目名称"))
        standard_name = _clean_text(project.get("项目名称")) or _extract_parenthetical_standard(row.get("项目名称"))
        display_name = build_specific_standard_display_name(
            concrete_name=concrete_name or _strip_parenthetical_standard(row.get("项目名称")),
            standard_name=standard_name or row.get("项目名称"),
        )
        quantity = _clean_text(row.get("工程量")) or "待算量"
        sources.append(
            {
                "融合行号": f"DWG-{index + 1:06d}",
                "项目名称": display_name,
                "项目特征": _clean_text(row.get("项目特征")),
                "单位": _clean_text(row.get("单位")),
                "工程量": quantity,
                "融合来源": "DWG/DXF",
                "标准项目编码": _clean_text(project.get("标准项目编码")),
                "标准项目名称": standard_name,
                "具体项目名称": concrete_name or _strip_parenthetical_standard(row.get("项目名称")),
                "PDF识别编号": "",
                "DWG识别项目编号": _clean_text(project.get("识别项目编号")),
                "去重键": _dedupe_key(
                    standard_code=project.get("标准项目编码"),
                    standard_name=standard_name,
                    concrete_name=concrete_name or row.get("项目名称"),
                    feature=row.get("项目特征"),
                ),
                "合并说明": "来自 DWG/DXF 结构化识别",
                "风险提示": "工程量必须来自可信 CAD trace 或人工确认" if _quantity_status(quantity) != "已算量" else "",
            }
        )
    return sources


def _pdf_sources(pdf_direct_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    mapping_rows = list(pdf_direct_report.get("standard_mapping_rows") or [])
    if mapping_rows:
        for index, row in enumerate(mapping_rows, start=1):
            source_item = row.get("source_item") if isinstance(row.get("source_item"), Mapping) else {}
            concrete_name = _clean_text(source_item.get("图纸项目名称"))
            standard_name = _clean_text(row.get("标准项目名称"))
            display_name = build_specific_standard_display_name(
                concrete_name=concrete_name,
                standard_name=standard_name,
            )
            quantity = _clean_text(row.get("工程量")) or "待算量"
            rows.append(
                {
                    "融合行号": f"PDF-{index:06d}",
                    "项目名称": display_name,
                    "项目特征": _clean_text(row.get("项目特征")),
                    "单位": _clean_text(row.get("标准单位")),
                    "工程量": quantity,
                    "融合来源": "PDF直接识图",
                    "标准项目编码": _clean_text(row.get("标准项目编码")),
                    "标准项目名称": standard_name,
                    "具体项目名称": concrete_name,
                    "PDF识别编号": _clean_text(row.get("识别编号")),
                    "DWG识别项目编号": "",
                    "去重键": _dedupe_key(
                        standard_code=row.get("标准项目编码"),
                        standard_name=standard_name,
                        concrete_name=concrete_name or display_name,
                        feature=row.get("项目特征"),
                    ),
                    "合并说明": "来自 PDF 直接视觉列项，作为 DWG+PDF 联合清单主列项候选",
                    "风险提示": "PDF 仅用于列项完整性；工程量不由视觉模型估算",
                }
            )
        return rows

    for index, row in enumerate(pdf_direct_report.get("quantity_list_rows") or [], start=1):
        quantity = _clean_text(row.get("工程量")) or "待算量"
        name = _clean_text(row.get("项目名称"))
        rows.append(
            {
                "融合行号": f"PDF-{index:06d}",
                "项目名称": name,
                "项目特征": _clean_text(row.get("项目特征")),
                "单位": _clean_text(row.get("单位")),
                "工程量": quantity,
                "融合来源": "PDF直接识图",
                "标准项目编码": "",
                "标准项目名称": _extract_parenthetical_standard(name),
                "具体项目名称": _strip_parenthetical_standard(name),
                "PDF识别编号": "",
                "DWG识别项目编号": "",
                "去重键": _dedupe_key(
                    standard_code="",
                    standard_name=_extract_parenthetical_standard(name),
                    concrete_name=name,
                    feature=row.get("项目特征"),
                ),
                "合并说明": "来自 PDF 直接视觉列项",
                "风险提示": "PDF 仅用于列项完整性；工程量不由视觉模型估算",
            }
        )
    return rows


def _merge_source_into(current: dict[str, Any], source: Mapping[str, Any]) -> None:
    current_source = _clean_text(current.get("融合来源"))
    incoming_source = _clean_text(source.get("融合来源"))
    if incoming_source and incoming_source not in current_source.split("+"):
        current["融合来源"] = "+".join([part for part in [current_source, incoming_source] if part])

    current_quantity = _clean_text(current.get("工程量"))
    incoming_quantity = _clean_text(source.get("工程量"))
    if _quantity_status(current_quantity) != "已算量" and _quantity_status(incoming_quantity) == "已算量":
        current["工程量"] = incoming_quantity
        if source.get("单位"):
            current["单位"] = source.get("单位")

    current["项目特征"] = _join_unique([current.get("项目特征"), source.get("项目特征")])
    current["PDF识别编号"] = _join_unique([current.get("PDF识别编号"), source.get("PDF识别编号")], separator=",")
    current["DWG识别项目编号"] = _join_unique([current.get("DWG识别项目编号"), source.get("DWG识别项目编号")], separator=",")
    current["合并说明"] = _join_unique([current.get("合并说明"), source.get("合并说明")])
    current["风险提示"] = _join_unique([current.get("风险提示"), source.get("风险提示")])


def build_specific_standard_display_name(*, concrete_name: Any, standard_name: Any) -> str:
    concrete = _clean_text(concrete_name)
    standard = _clean_text(standard_name)
    if not concrete:
        return standard
    if not standard or _normalize(concrete) == _normalize(standard):
        return concrete
    if concrete.endswith(f"（{standard}）"):
        return concrete
    return f"{concrete}（{standard}）"


def _dedupe_key(*, standard_code: Any, standard_name: Any, concrete_name: Any, feature: Any) -> str:
    code = _normalize(standard_code)
    standard = _normalize(standard_name)
    concrete = _normalize(_strip_parenthetical_standard(concrete_name))
    parts = [code or standard, concrete]
    if not concrete or concrete == standard or len(concrete) <= 3:
        parts.append(_feature_key(feature))
    return "|".join(part for part in parts if part)


def _feature_key(value: Any) -> str:
    text = _normalize(value)
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", text)
    meaningful = [token for token in tokens if token not in {"待补充", "待算量", "图纸识别"}]
    return "".join(meaningful[:8])


def _source_order(value: Any) -> int:
    text = _clean_text(value)
    if text.startswith("PDF"):
        return 0
    if "PDF" in text and "DWG" in text:
        return 1
    return 2


def _quantity_status(value: Any) -> str:
    text = _clean_text(value)
    if not text or text == "待算量":
        return "待算量"
    if "AI建议" in text or "待确认" in text:
        return "AI候选量待确认"
    return "已算量"


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _normalize(value: Any) -> str:
    text = _clean_text(value).lower()
    text = text.replace("㎡", "m2").replace("ｍ２", "m2")
    return re.sub(r"[\s,，;；:：|｜/\\()（）【】\[\]{}<>《》\"'“”‘’._\-+]+", "", text)


def _extract_parenthetical_standard(value: Any) -> str:
    text = _clean_text(value)
    match = re.search(r"（([^（）]+)）\s*$", text)
    return _clean_text(match.group(1)) if match else ""


def _strip_parenthetical_standard(value: Any) -> str:
    text = _clean_text(value)
    return _clean_text(re.sub(r"（[^（）]+）\s*$", "", text))


def _join_unique(values: list[Any], *, separator: str = "；") -> str:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        for part in text.split(separator):
            part = _clean_text(part)
            key = _normalize(part)
            if part and key not in seen:
                seen.add(key)
                result.append(part)
    return separator.join(result)


def _write_csv(path: Path, rows: list[Mapping[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _build_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# PDF + DXF 列项融合报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DWG/DXF 四字段行：{summary.get('dwg_quantity_list_count', 0)}",
        f"- PDF 直接列项行：{summary.get('pdf_direct_quantity_list_count', 0)}",
        f"- 融合后四字段行：{summary.get('fused_quantity_list_count', 0)}",
        f"- 去重合并行：{summary.get('fusion_duplicate_suppressed_count', 0)}",
        "",
        "## 融合口径",
        "",
        "- PDF 直接识图负责列项完整性和具体项目名称。",
        "- DWG/DXF 结构化结果负责材料、标准映射、CAD trace 和可信工程量。",
        "- 无可靠工程量证据时，工程量保留为待算量。",
        "",
        "## 前 80 行",
        "",
        "| 行号 | 项目名称 | 单位 | 工程量 | 来源 | 风险 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in list(report.get("fusion_rows") or [])[:80]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("融合行号")),
                    _md(row.get("项目名称")),
                    _md(row.get("单位")),
                    _md(row.get("工程量")),
                    _md(row.get("融合来源")),
                    _md(row.get("风险提示")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    return _clean_text(value).replace("|", "\\|").replace("\n", "<br>")
