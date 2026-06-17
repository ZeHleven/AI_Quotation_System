from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


SHEET_SIZE_RE = re.compile(r"^A[0-9]$", re.IGNORECASE)
CATALOG_SEQUENCE_RE = re.compile(r"^\d{1,3}$")
SHEET_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,8}[-－][A-Za-z0-9一-龥（）()]+$")
ANNOTATION_SOURCE_LIMIT_PER_FILE = 400

MATERIAL_KEYWORDS = (
    "材料说明",
    "材料名称",
    "玻化砖",
    "石膏板",
    "无机涂料",
    "乳胶漆",
    "聚氨酯",
    "聚合物水泥",
    "水泥砂浆",
    "细石混凝土",
    "轻集料混凝土",
    "轻钢龙骨",
    "主龙骨",
    "次龙骨",
    "阻燃板",
    "透光软膜",
    "铝镁合金",
    "踢脚线",
    "窗台板",
    "地砖",
    "墙砖",
    "腻子",
    "涂膜防水",
    "防火涂料",
    "木饰面",
    "铝板",
    "矿棉板",
    "吊件",
    "丝杆",
    "膨胀螺栓",
    "界面剂",
)

CONSTRUCTION_KEYWORDS = (
    "做法",
    "节点",
    "详图",
    "吊顶",
    "地面做法",
    "墙面做法",
    "天花",
    "灯槽",
    "窗帘盒",
    "窗台板",
    "收边",
    "龙骨",
    "铺装",
    "防水",
    "石膏板",
    "软膜",
)

NOISE_KEYWORDS = (
    "HONGFA",
    "CONSTRUCTION",
    "宏发建设",
    "设计资质",
    "地址：中国",
    "电话：",
    "传真：",
    "Co-operated",
    "出图专用章",
    "Project Name",
    "Drawing title",
    "ENTER NUMBER",
)

GENERIC_ANCHORS = {"材料名称", "做法详图", "序号", "图纸目录", "施工图目录表"}
ANNOTATION_NOISE_KEYWORDS = {
    "图纸目录",
    "施工图目录",
    "设计说明",
    "工程概况",
    "材料表",
    "材料名称",
    "图号",
    "图名",
    "比例",
    "日期",
    "设计",
    "审核",
    "项目名称",
}


def converge_table_fields(table_report: dict[str, Any]) -> dict[str, Any]:
    """Converge reconstructed table candidates into business-friendly fields."""
    catalog_rows = _build_drawing_catalog_rows(table_report)
    material_method_rows = _build_material_method_rows(table_report)
    material_counts = Counter(row["row_type"] for row in material_method_rows)
    drawing_counts = Counter(row["drawing_type"] for row in catalog_rows)
    source_summary = table_report.get("summary", {})
    return {
        "ok": True,
        "phase": "BIZ-2x-3-field-convergence",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_phase": table_report.get("phase", ""),
        "source_summary": source_summary,
        "summary": {
            "source_table_candidate_count": source_summary.get("table_candidate_count", 0),
            "drawing_catalog_row_count": len(catalog_rows),
            "material_method_row_count": len(material_method_rows),
            "material_method_type_counts": dict(material_counts.most_common()),
            "drawing_type_counts": dict(drawing_counts.most_common()),
        },
        "drawing_catalog_rows": catalog_rows,
        "material_method_rows": material_method_rows,
        "drawing_annotation_rows": [],
    }


def append_drawing_annotation_rows(
    field_report: dict[str, Any],
    parsed_files: list[Any],
) -> dict[str, Any]:
    rows = _build_drawing_annotation_rows(parsed_files)
    updated = dict(field_report)
    updated["drawing_annotation_rows"] = rows
    summary = dict(updated.get("summary") or {})
    summary["drawing_annotation_row_count"] = len(rows)
    updated["summary"] = summary
    return updated


def build_field_convergence_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-3 DXF 表格字段级收敛报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 来源表格候选数：{summary['source_table_candidate_count']}",
        f"- 图纸目录字段行：{summary['drawing_catalog_row_count']}",
        f"- 材料/做法字段行：{summary['material_method_row_count']}",
        f"- 图纸类型统计：{json.dumps(summary['drawing_type_counts'], ensure_ascii=False)}",
        f"- 材料/做法类型统计：{json.dumps(summary['material_method_type_counts'], ensure_ascii=False)}",
        "",
        "## 图纸目录字段",
        "",
        "| 来源文件 | 序号 | 图纸名称 | 图纸编号 | 图幅 | 类型 | 置信度 |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in report["drawing_catalog_rows"][:100]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["source_file"]),
                    _md(row["sequence"]),
                    _md(row["drawing_name"]),
                    _md(row["drawing_code"]),
                    _md(row["sheet_size"]),
                    _md(row["drawing_type_label"]),
                    f"{row['confidence']:.2f}",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## 材料/做法字段",
        "",
        "| 来源文件 | 类型 | 名称 | 规格或做法 | 置信度 |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for row in report["material_method_rows"][:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["source_file"]),
                    _md(row["row_type_label"]),
                    _md(row["material_or_method_name"]),
                    _md(row["spec_or_method"]),
                    f"{row['confidence']:.2f}",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## 边界说明",
        "",
        "- 本报告仍是图纸识别中间结果，不生成最终工程量清单。",
        "- 材料和做法字段仅作为后续标准库匹配线索，不能直接写入项目特征或工程量。",
        "- 后续 BIZ-2x-4 必须用 GB/T 标准库项目特征字段和工程量计算规则二次约束。",
    ]
    return "\n".join(lines) + "\n"


def build_drawing_catalog_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "来源文件": row["source_file"],
            "来源表格锚点": row["source_table_anchor"],
            "来源行号": row["source_row_number"],
            "序号": row["sequence"],
            "图纸名称": row["drawing_name"],
            "图纸编号": row["drawing_code"],
            "图幅": row["sheet_size"],
            "图纸类型": row["drawing_type_label"],
            "置信度": f"{row['confidence']:.2f}",
            "原始行文本": row["raw_row_text"],
        }
        for row in report["drawing_catalog_rows"]
    ]


def build_material_method_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "来源文件": row["source_file"],
            "来源表格锚点": row["source_table_anchor"],
            "来源行号": row["source_row_number"],
            "类型": row["row_type_label"],
            "名称": row["material_or_method_name"],
            "规格或做法": row["spec_or_method"],
            "备注": row["remark"],
            "置信度": f"{row['confidence']:.2f}",
            "原始行文本": row["raw_row_text"],
        }
        for row in report["material_method_rows"]
    ]


def build_drawing_annotation_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "来源文件": row["source_file"],
            "来源行号": row["source_row_number"],
            "类型": row["row_type_label"],
            "识别文字": row["material_or_method_name"],
            "完整标注": row["spec_or_method"],
            "图层": row.get("layer", ""),
            "布局": row.get("layout", ""),
            "X": row.get("x", ""),
            "Y": row.get("y", ""),
            "置信度": f"{row['confidence']:.2f}",
            "原始行文本": row["raw_row_text"],
        }
        for row in report.get("drawing_annotation_rows", [])
    ]


def write_field_convergence_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_stem = stem or "BIZ2x3_DXF表格字段收敛"
    json_path = directory / f"{file_stem}.json"
    md_path = directory / f"{file_stem}.md"
    catalog_csv_path = directory / f"{file_stem}_图纸目录字段.csv"
    material_csv_path = directory / f"{file_stem}_材料做法字段.csv"
    annotation_csv_path = directory / f"{file_stem}_图纸文字标注字段.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_field_convergence_markdown(report), encoding="utf-8")
    _write_csv(catalog_csv_path, build_drawing_catalog_csv_rows(report))
    _write_csv(material_csv_path, build_material_method_csv_rows(report))
    _write_csv(annotation_csv_path, build_drawing_annotation_csv_rows(report))
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "drawing_catalog_csv": str(catalog_csv_path),
        "material_method_csv": str(material_csv_path),
        "drawing_annotation_csv": str(annotation_csv_path),
    }


def _build_drawing_catalog_rows(table_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in table_report.get("table_candidates", []):
        if candidate.get("table_type") != "drawing_catalog":
            continue
        source_file = str(candidate.get("source_file") or "")
        anchor_text = _clean_text(str(candidate.get("anchor_text") or ""))
        for row_number, row in enumerate(candidate.get("rows") or [], start=1):
            row_text = _clean_text(str(row.get("row_text") or ""))
            tokens = [_clean_text(str(cell.get("text") or "")) for cell in row.get("cells") or []]
            tokens = [token for token in tokens if token]
            for item in _iter_catalog_groups(tokens):
                key = (
                    source_file,
                    item["sequence"],
                    item["drawing_code"],
                    _normalize_key(item["drawing_name"]),
                )
                if key in seen:
                    continue
                seen.add(key)
                drawing_type, drawing_type_label = _infer_drawing_type(item["drawing_name"], item["drawing_code"])
                rows.append(
                    {
                        "source_file": source_file,
                        "source_table_anchor": anchor_text,
                        "source_row_number": row_number,
                        "sequence": item["sequence"],
                        "drawing_name": item["drawing_name"],
                        "drawing_code": item["drawing_code"],
                        "sheet_size": item["sheet_size"],
                        "drawing_type": drawing_type,
                        "drawing_type_label": drawing_type_label,
                        "confidence": item["confidence"],
                        "raw_row_text": row_text,
                    }
                )
    rows.sort(key=lambda item: (item["source_file"], _sequence_sort_key(item["sequence"]), item["drawing_code"]))
    return rows


def _build_drawing_annotation_rows(parsed_files: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for parsed in parsed_files:
        file_rows = 0
        for record in getattr(parsed, "text_records", ()):
            if file_rows >= ANNOTATION_SOURCE_LIMIT_PER_FILE:
                break
            text = _clean_text(str(getattr(record, "text", "") or ""))
            if not _looks_like_drawing_annotation_text(text):
                continue
            source_file = str(getattr(record, "source_file", "") or getattr(parsed, "file_name", "") or "")
            key = (source_file, _normalize_key(text), str(getattr(record, "layer", "") or ""))
            if key in seen:
                continue
            seen.add(key)
            name, spec_or_method = _split_annotation_text(text)
            rows.append(
                {
                    "source_file": source_file,
                    "source_table_anchor": "图纸文字标注",
                    "source_row_number": int(getattr(record, "line_number", 0) or 0),
                    "row_type": "drawing_annotation",
                    "row_type_label": "平面/立面文字标注",
                    "material_or_method_name": name,
                    "spec_or_method": spec_or_method,
                    "remark": "",
                    "confidence": _annotation_confidence(text, record),
                    "raw_row_text": text,
                    "layer": str(getattr(record, "layer", "") or ""),
                    "layout": str(getattr(record, "layout", "") or ""),
                    "x": getattr(record, "x", ""),
                    "y": getattr(record, "y", ""),
                }
            )
            file_rows += 1
    rows.sort(key=lambda item: (item["source_file"], -item["confidence"], item["source_row_number"], item["material_or_method_name"]))
    return rows


def _looks_like_drawing_annotation_text(text: str) -> bool:
    if not text:
        return False
    if len(text) < 3 or len(text) > 160:
        return False
    compact = text.replace(" ", "")
    if compact.isdigit() or re.fullmatch(r"[-+]?(\d+(\.\d+)?)(mm|m|㎡|m2)?", compact, re.IGNORECASE):
        return False
    if any(keyword in compact for keyword in ANNOTATION_NOISE_KEYWORDS):
        return False
    return any(keyword in compact for keyword in MATERIAL_KEYWORDS + CONSTRUCTION_KEYWORDS)


def _split_annotation_text(text: str) -> tuple[str, str]:
    normalized = _clean_text(text.replace("\n", "；"))
    parts = [part.strip() for part in re.split(r"[；;。]", normalized) if part.strip()]
    first = parts[0] if parts else normalized
    if len(first) > 36:
        first = _shorten_name(first)
    return first[:80], normalized[:500]


def _annotation_confidence(text: str, record: Any) -> float:
    confidence = 0.58
    role_tags = set(getattr(record, "role_tags", ()) or ())
    if role_tags.intersection({"plan", "elevation"}):
        confidence += 0.18
    if role_tags.intersection({"detail", "construction_method"}):
        confidence += 0.08
    if any(keyword in text for keyword in ("地面", "墙面", "吊顶", "天花", "踢脚线", "窗帘盒")):
        confidence += 0.1
    if any(keyword in text for keyword in ("做法", "材料", "说明")):
        confidence -= 0.06
    return round(min(max(confidence, 0.45), 0.9), 2)


def _iter_catalog_groups(tokens: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not _looks_like_catalog_sequence(token):
            i += 1
            continue
        matched = False
        for code_index in range(i + 2, min(i + 6, len(tokens))):
            if not _looks_like_sheet_code(tokens[code_index]):
                continue
            name_tokens = tokens[i + 1 : code_index]
            name = _clean_catalog_name("".join(name_tokens))
            if _should_skip_catalog_name(name):
                continue
            sheet_size = ""
            next_index = code_index + 1
            if next_index < len(tokens) and _looks_like_sheet_size(tokens[next_index]):
                sheet_size = tokens[next_index].upper()
                next_index += 1
            confidence = 0.88
            if sheet_size:
                confidence += 0.05
            if len(name) >= 4:
                confidence += 0.03
            items.append(
                {
                    "sequence": token.zfill(3) if token.isdigit() and len(token) < 3 else token,
                    "drawing_name": name,
                    "drawing_code": tokens[code_index],
                    "sheet_size": sheet_size,
                    "confidence": min(confidence, 0.98),
                }
            )
            i = next_index
            matched = True
            break
        if not matched:
            i += 1
    return items


def _build_material_method_rows(table_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in table_report.get("table_candidates", []):
        table_type = candidate.get("table_type")
        if table_type not in {"material_table", "construction_method"}:
            continue
        source_file = str(candidate.get("source_file") or "")
        anchor_text = _clean_text(str(candidate.get("anchor_text") or ""))
        row_type = "construction_method" if table_type == "construction_method" else "material"
        row_type_label = "构造做法" if row_type == "construction_method" else "材料"

        anchor_row = _candidate_from_text(
            anchor_text,
            source_file=source_file,
            anchor_text=anchor_text,
            row_number=0,
            raw_row_text=anchor_text,
            row_type=row_type,
            row_type_label=row_type_label,
            from_anchor=True,
        )
        if anchor_row:
            _append_unique_material_row(rows, seen, anchor_row)

        for row_number, row in enumerate(candidate.get("rows") or [], start=1):
            raw_row_text = _clean_text(str(row.get("row_text") or ""))
            for cell in row.get("cells") or []:
                text = _clean_text(str(cell.get("text") or ""))
                item = _candidate_from_text(
                    text,
                    source_file=source_file,
                    anchor_text=anchor_text,
                    row_number=row_number,
                    raw_row_text=raw_row_text,
                    row_type=row_type,
                    row_type_label=row_type_label,
                    from_anchor=False,
                )
                if item:
                    _append_unique_material_row(rows, seen, item)
    rows.sort(key=lambda item: (item["source_file"], item["row_type"], -item["confidence"], item["material_or_method_name"]))
    return rows


def _candidate_from_text(
    text: str,
    *,
    source_file: str,
    anchor_text: str,
    row_number: int,
    raw_row_text: str,
    row_type: str,
    row_type_label: str,
    from_anchor: bool,
) -> dict[str, Any] | None:
    if not _looks_like_material_or_method_text(text, row_type, from_anchor=from_anchor):
        return None
    name, spec_or_method, remark = _split_material_method_text(text, row_type)
    if not name:
        return None
    confidence = 0.72 if from_anchor else 0.62
    if any(keyword in text for keyword in ("材料说明", "做法", "详图", "地面做法", "吊顶")):
        confidence += 0.12
    if spec_or_method:
        confidence += 0.06
    return {
        "source_file": source_file,
        "source_table_anchor": anchor_text,
        "source_row_number": row_number,
        "row_type": row_type,
        "row_type_label": row_type_label,
        "material_or_method_name": name,
        "spec_or_method": spec_or_method,
        "remark": remark,
        "confidence": min(confidence, 0.94),
        "raw_row_text": raw_row_text,
    }


def _split_material_method_text(text: str, row_type: str) -> tuple[str, str, str]:
    normalized = _clean_text(text.replace("\n", "；"))
    normalized = re.sub(r"^\d+[).、]\s*", "", normalized)
    normalized = re.sub(r"^[-—]+\s*", "", normalized)
    if not normalized:
        return "", "", ""
    if "材料说明" in normalized and len(normalized) <= 80:
        return normalized, "", ""
    if row_type == "construction_method" and ("做法" in normalized or "详图" in normalized) and len(normalized) <= 80:
        return normalized, "", ""
    parts = [part.strip() for part in re.split(r"[；;。]", normalized) if part.strip()]
    first = parts[0] if parts else normalized
    if len(first) > 36:
        first = _shorten_name(first)
    spec = normalized if normalized != first else ""
    return first[:80], spec[:500], ""


def _append_unique_material_row(
    rows: list[dict[str, Any]],
    seen: set[tuple[str, str, str, str]],
    item: dict[str, Any],
) -> None:
    key = (
        item["source_file"],
        item["row_type"],
        _normalize_key(item["material_or_method_name"]),
        _normalize_key(item["spec_or_method"])[:80],
    )
    if key in seen:
        return
    seen.add(key)
    rows.append(item)


def _looks_like_material_or_method_text(text: str, row_type: str, *, from_anchor: bool) -> bool:
    if not text:
        return False
    if len(text) < 3 or len(text) > 700:
        return False
    if text in GENERIC_ANCHORS and not from_anchor:
        return False
    compact = text.replace(" ", "")
    if compact.isdigit() or re.fullmatch(r"[A-Z](\|[A-Z])+", compact):
        return False
    if any(keyword in text for keyword in NOISE_KEYWORDS):
        return False
    keywords = CONSTRUCTION_KEYWORDS if row_type == "construction_method" else MATERIAL_KEYWORDS
    if any(keyword in text for keyword in keywords):
        return True
    if row_type == "material" and from_anchor and text not in GENERIC_ANCHORS:
        return any(keyword in text for keyword in MATERIAL_KEYWORDS + CONSTRUCTION_KEYWORDS)
    return False


def _looks_like_catalog_sequence(text: str) -> bool:
    return bool(CATALOG_SEQUENCE_RE.match(text.strip()))


def _looks_like_sheet_code(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 24:
        return False
    if not SHEET_CODE_RE.match(stripped):
        return False
    return not stripped.startswith("-")


def _looks_like_sheet_size(text: str) -> bool:
    return bool(SHEET_SIZE_RE.match(text.strip()))


def _clean_catalog_name(text: str) -> str:
    return re.sub(r"\s+", "", text.strip(" |"))


def _should_skip_catalog_name(text: str) -> bool:
    if not text or len(text) < 2 or len(text) > 60:
        return True
    if text in {"图纸名称", "图纸编号", "图幅", "序号"}:
        return True
    if "GB" in text or "《" in text or "规范" in text:
        return True
    if any(keyword in text for keyword in NOISE_KEYWORDS):
        return True
    return False


def _infer_drawing_type(name: str, code: str) -> tuple[str, str]:
    compact = f"{name}{code}".replace(" ", "")
    mapping = (
        ("drawing_catalog", "图纸目录", ("目录",)),
        ("material_table", "材料表", ("材料表", "图例与材料", "材料说明")),
        ("design_note", "设计说明", ("设计说明", "工程概况", "说明")),
        ("construction_method", "构造做法/通用节点", ("通用节点", "做法", "节点")),
        ("elevation", "立面图", ("立面",)),
        ("detail", "大样/详图", ("大样", "详图", "剖面")),
        ("plan", "平面图", ("平面", "布置图", "天花", "地面", "铺装", "灯具", "插座")),
    )
    for value, label, keywords in mapping:
        if any(keyword in compact for keyword in keywords):
            return value, label
    return "other", "其他"


def _sequence_sort_key(value: str) -> tuple[int, str]:
    if value.isdigit():
        return int(value), value
    return 9999, value


def _shorten_name(text: str) -> str:
    for keyword in MATERIAL_KEYWORDS + CONSTRUCTION_KEYWORDS:
        if keyword in text:
            start = max(text.find(keyword) - 12, 0)
            return text[start : start + 36].strip("，,；;。 ")
    return text[:36].strip("，,；;。 ")


def _clean_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
