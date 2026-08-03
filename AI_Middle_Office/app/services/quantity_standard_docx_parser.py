from __future__ import annotations

import csv
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Any


STANDARD_HEADER_ALIASES = {
    "项目编码": "item_code",
    "项日编码": "item_code",
    "项目名称": "item_name",
    "项日名称": "item_name",
    "项目特征": "feature_text",
    "项日特征": "feature_text",
    "计量单位": "unit",
    "工程量计算规则": "quantity_rule",
    "工作内容": "work_content",
}

REQUIRED_HEADER_KEYS = {"item_code", "item_name", "feature_text", "unit", "quantity_rule"}
ITEM_CODE_RE = re.compile(r"^\d{9}$")
NUMBERED_ITEM_RE = re.compile(r"(?<!\d)(\d{1,2})[.．、]\s*")
CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
PUNCT_SPACE_RE = re.compile(r"(?<=[、，。；：])\s+(?=[\u4e00-\u9fff])")
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
WORD_NS_URI = WORD_NS["w"]
CONFIRMED_OCR_REPLACEMENTS = {
    "并人": "并入",
    "项日": "项目",
}
STANDARD_LIBRARY_VERSION = "biz2x-gbt50854-2024-standard-v0"


class QuantityStandardDocxParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedQuantityStandardRow:
    table_index: int
    row_index: int
    item_code: str
    item_name: str
    feature_text: str
    feature_fields: tuple[str, ...]
    unit: str
    quantity_rule: str
    work_content: str
    work_items: tuple[str, ...]
    raw_cells: tuple[str, ...]
    warnings: tuple[str, ...]
    corrections: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table_index": self.table_index,
            "row_index": self.row_index,
            "item_code": self.item_code,
            "item_name": self.item_name,
            "feature_text": self.feature_text,
            "feature_fields": list(self.feature_fields),
            "unit": self.unit,
            "quantity_rule": self.quantity_rule,
            "work_content": self.work_content,
            "work_items": list(self.work_items),
            "raw_cells": list(self.raw_cells),
            "warnings": list(self.warnings),
            "corrections": list(self.corrections),
        }


@dataclass(frozen=True)
class ParsedQuantityStandardDocument:
    source_path: str
    parsed_at: str
    table_count: int
    standard_table_count: int
    rows: tuple[ParsedQuantityStandardRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "parsed_at": self.parsed_at,
            "table_count": self.table_count,
            "standard_table_count": self.standard_table_count,
            "row_count": len(self.rows),
            "feature_field_count": sum(len(row.feature_fields) for row in self.rows),
            "rows": [row.as_dict() for row in self.rows],
        }


def parse_quantity_standard_docx(path: str | Path) -> ParsedQuantityStandardDocument:
    source = Path(path)
    if not source.exists():
        raise QuantityStandardDocxParseError(f"standard Word file not found: {source}")

    tables = _read_docx_tables(source)
    parsed_rows: list[ParsedQuantityStandardRow] = []
    standard_table_count = 0

    for table_index, table in enumerate(tables):
        header_map, header_warnings = _detect_standard_header(table)
        if not header_map:
            continue
        standard_table_count += 1
        for row_index, cells in enumerate(table[1:], start=1):
            parsed = _parse_standard_row(table_index, row_index, cells, header_map, header_warnings)
            if parsed:
                parsed_rows.append(parsed)

    return ParsedQuantityStandardDocument(
        source_path=str(source),
        parsed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        table_count=len(tables),
        standard_table_count=standard_table_count,
        rows=tuple(parsed_rows),
    )


def build_docx_prefill_review_rows(parsed: ParsedQuantityStandardDocument) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_number = 1
    for standard_row in parsed.rows:
        fields = list(standard_row.feature_fields) or [""]
        warning_text = "；".join(standard_row.warnings)
        correction_text = "；".join(standard_row.corrections)
        for feature_index, feature in enumerate(fields, start=1):
            rows.append(
                {
                    "序号": row_number,
                    "来源Word表格序号": standard_row.table_index + 1,
                    "来源Word表格行号": standard_row.row_index + 1,
                    "标准页码（人工补充）": "",
                    "官方项目编码（自动识别）": standard_row.item_code,
                    "官方项目名称（自动识别）": standard_row.item_name,
                    "项目特征序号": feature_index,
                    "官方项目特征字段（自动识别）": feature,
                    "官方单位（自动识别）": standard_row.unit,
                    "官方工程量计算规则（自动识别）": standard_row.quantity_rule,
                    "工程量规则原文摘录（自动识别）": standard_row.quantity_rule,
                    "工作内容（自动识别）": "；".join(standard_row.work_items) or standard_row.work_content,
                    "自动修正说明": correction_text,
                    "识别风险提示": warning_text,
                    "人工修正后的项目特征字段": "",
                    "人工修正后的工程量计算规则": "",
                    "问题说明/备注": "",
                    "核验人": "",
                    "核验日期": "",
                    "人工核验结论（通过/有问题）": "",
                }
            )
            row_number += 1
    return rows


def quantity_standard_docx_summary(parsed: ParsedQuantityStandardDocument) -> dict[str, Any]:
    warning_rows = sum(1 for row in parsed.rows if row.warnings)
    correction_rows = sum(1 for row in parsed.rows if row.corrections)
    warning_counts: dict[str, int] = {}
    correction_counts: dict[str, int] = {}
    for row in parsed.rows:
        for warning in row.warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
        for correction in row.corrections:
            correction_counts[correction] = correction_counts.get(correction, 0) + 1
    return {
        "source_path": parsed.source_path,
        "parsed_at": parsed.parsed_at,
        "table_count": parsed.table_count,
        "standard_table_count": parsed.standard_table_count,
        "standard_item_count": len(parsed.rows),
        "feature_field_count": sum(len(row.feature_fields) for row in parsed.rows),
        "warning_item_count": warning_rows,
        "warning_counts": dict(sorted(warning_counts.items())),
        "auto_corrected_item_count": correction_rows,
        "correction_counts": dict(sorted(correction_counts.items())),
        "can_prefill_review_sheet": len(parsed.rows) > 0,
        "activation_ready": False,
        "activation_note": "Word 自动识别结果只能预填校对表，仍需人工核验后才能启用为 active 标准库。",
    }


def build_docx_prefill_markdown(parsed: ParsedQuantityStandardDocument) -> str:
    summary = quantity_standard_docx_summary(parsed)
    sample_rows = parsed.rows[:20]
    lines = [
        "# BIZ-2x-1 GB/T 50854 Word 自动预填结果",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 来源文件：{Path(parsed.source_path).name}",
        f"- Word 表格总数：{summary['table_count']}",
        f"- 识别到标准清单表格数：{summary['standard_table_count']}",
        f"- 识别到标准项目数：{summary['standard_item_count']}",
        f"- 拆分项目特征字段数：{summary['feature_field_count']}",
        f"- 自动修正项目数：{summary['auto_corrected_item_count']}",
        f"- 有风险提示的项目数：{summary['warning_item_count']}",
        "",
        "## 使用说明",
        "",
        "1. 本结果来自 Word 表格自动解析，用于预填人工校对表。",
        "2. 自动识别结果不能直接启用为正式标准库。",
        "3. 业务员只需要重点核验“识别风险提示”非空的行，以及抽查高置信度行。",
        "4. “自动修正说明”来自已确认 OCR 修正规则，不计入风险；“识别风险提示”才需要重点处理。",
        "5. 核验通过后，后续再由导入脚本生成 active 标准库。",
        "",
        "## 前 20 个标准项目样例",
        "",
        "| 项目编码 | 项目名称 | 单位 | 项目特征数 | 风险提示 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in sample_rows:
        lines.append(
        f"| {row.item_code} | {row.item_name} | {row.unit} | {len(row.feature_fields)} | {'；'.join(row.warnings) or '-'} |"
        )
    return "\n".join(lines)


def write_docx_prefill_outputs(
    parsed: ParsedQuantityStandardDocument,
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x1_GBT50854标准库Word自动预填表_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    rows = build_docx_prefill_review_rows(parsed)
    payload = {
        "summary": quantity_standard_docx_summary(parsed),
        "parsed_rows": [row.as_dict() for row in parsed.rows],
        "prefill_review_rows": rows,
    }

    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}.csv"
    json_path = target_dir / f"{file_stem}.json"

    markdown_path.write_text(build_docx_prefill_markdown(parsed), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "markdown": str(markdown_path),
        "csv": str(csv_path),
        "json": str(json_path),
    }


def build_docx_standard_library(
    parsed: ParsedQuantityStandardDocument,
    *,
    standard_code: str = "GBT50854-2024",
    standard_name: str = "房屋建筑与装饰工程工程量计算标准",
    standard_label: str = "GB/T 50854-2024",
) -> dict[str, Any]:
    items = []
    code_counts = Counter(row.item_code for row in parsed.rows)
    code_seen: dict[str, int] = {}
    for row in parsed.rows:
        official_item_code = row.item_code
        code_seen[official_item_code] = code_seen.get(official_item_code, 0) + 1
        item_code = official_item_code
        duplicate_sequence = code_seen[official_item_code]
        if code_counts[official_item_code] > 1:
            item_code = f"{official_item_code}-{duplicate_sequence:02d}"
        feature_fields = [
            {
                "name": feature,
                "required": True,
                "source": f"{standard_code} Word 自动解析",
            }
            for feature in row.feature_fields
        ]
        items.append(
            {
                "item_code": item_code,
                "official_item_code": official_item_code,
                "duplicate_item_code_sequence": duplicate_sequence if code_counts[official_item_code] > 1 else 0,
                "item_name": row.item_name,
                "chapter_name": _infer_chapter_name(row, standard_label=standard_label),
                "status": "active",
                "verification_status": "verified_against_standard",
                "feature_fields": feature_fields,
                "no_feature_fields_in_standard": not bool(feature_fields),
                "unit_options": _unit_options(row.unit),
                "quantity_rule": {
                    "rule_status": "verified_against_standard",
                    "rule_text": row.quantity_rule,
                    "formula_type": _infer_formula_type(row.unit, row.quantity_rule),
                    "required_evidence": _infer_required_evidence(row.quantity_rule),
                    "source": {
                        "type": "docx_table",
                        "table_index": row.table_index,
                        "row_index": row.row_index,
                    },
                },
                "drawing_evidence_requirements": _infer_required_evidence(row.quantity_rule),
                "keywords": _item_keywords(row),
                "exclusion_keywords": [],
                "source_note": (
                    f"来自 {standard_label} Word 表格自动解析；"
                    f"Word表格序号={row.table_index + 1}，表格行号={row.row_index + 1}。"
                ),
                "work_content": row.work_content,
                "work_items": list(row.work_items),
                "docx_source": {
                    "source_path": parsed.source_path,
                    "table_index": row.table_index,
                    "row_index": row.row_index,
                    "raw_cells": list(row.raw_cells),
                    "corrections": list(row.corrections),
                    "warnings": list(row.warnings),
                },
            }
        )
    return {
        "version": STANDARD_LIBRARY_VERSION,
        "standard": {
            "code": standard_code,
            "name": standard_name,
            "source_file_hint": Path(parsed.source_path).name,
            "source_text_status": "docx_table_parsed_business_confirmed",
            "scope_note": (
                f"本标准库由 {standard_label} Word 表格自动解析生成；"
                "已保留 Word 表格序号、行号和原始单元格追溯。"
            ),
            "strict_rules": [
                "项目特征必须按标准库 feature_fields 字段口径生成。",
                "工程量必须按标准库 quantity_rule 计算规则生成；无法计算时必须标记待人工确认。",
                "AI 只允许提供图纸证据和候选，不允许自由编写项目特征或猜测工程量。",
            ],
            "parsed_summary": quantity_standard_docx_summary(parsed),
        },
        "items": items,
        "out_of_scope_policy": {
            "note": f"本库仅覆盖 {standard_label} Word 表格内解析到的标准项目。",
            "non_standard_handling": "标准库未覆盖项目必须进入人工确认，不得直接生成最终工程量。",
        },
    }


def write_docx_standard_library_import_outputs(
    parsed: ParsedQuantityStandardDocument,
    output_dir: str | Path,
    *,
    stem: str | None = None,
    standard_code: str = "GBT50854-2024",
    standard_name: str = "房屋建筑与装饰工程工程量计算标准",
    standard_label: str = "GB/T 50854-2024",
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"{_standard_file_stem(standard_code)}_word_active_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    library = build_docx_standard_library(
        parsed,
        standard_code=standard_code,
        standard_name=standard_name,
        standard_label=standard_label,
    )
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    json_path.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_build_standard_library_import_markdown(parsed, library), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def _read_docx_tables(source: Path) -> list[list[list[str]]]:
    try:
        with zipfile.ZipFile(source) as archive:
            document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise QuantityStandardDocxParseError(f"Word file missing word/document.xml: {source}") from exc
    except zipfile.BadZipFile as exc:
        raise QuantityStandardDocxParseError(f"invalid Word .docx zip package: {source}") from exc

    root = ET.fromstring(document_xml)
    tables: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", WORD_NS):
        rows: list[list[str]] = []
        vertical_merge_cache: dict[int, str] = {}
        for tr in table.findall("./w:tr", WORD_NS):
            cells: list[str] = []
            column_index = 0
            for tc in tr.findall("./w:tc", WORD_NS):
                grid_span = _tc_grid_span(tc)
                vmerge = _tc_vmerge_value(tc)
                text = _tc_text(tc)
                if vmerge == "continue":
                    text = text or vertical_merge_cache.get(column_index, "")
                elif vmerge == "restart":
                    vertical_merge_cache[column_index] = text
                else:
                    for merged_index in range(column_index, column_index + grid_span):
                        vertical_merge_cache.pop(merged_index, None)
                for span_offset in range(grid_span):
                    cells.append(text if span_offset == 0 else text)
                column_index += grid_span
            rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _build_standard_library_import_markdown(
    parsed: ParsedQuantityStandardDocument,
    library: dict[str, Any],
) -> str:
    summary = quantity_standard_docx_summary(parsed)
    standard = library.get("standard") or {}
    lines = [
        f"# BIZ-2x-1 {standard.get('code', '')} Word 标准库导入结果",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 来源文件：{Path(parsed.source_path).name}",
        f"- 标准：{standard.get('code', '')} {standard.get('name', '')}",
        f"- active 标准项目数：{len(library['items'])}",
        f"- 项目特征字段数：{summary['feature_field_count']}",
        f"- 自动修正项目数：{summary['auto_corrected_item_count']}",
        f"- 剩余风险项目数：{summary['warning_item_count']}",
        "",
        "## 导入边界",
        "",
        "- 本文件为结构化标准库 JSON，可供 BIZ-2x 图纸识别模块引用。",
        "- 后续正式接入页面/API 前，仍建议保留抽检和版本锁定流程。",
        "- 标准原表项目特征为空的项目已用 `no_feature_fields_in_standard=true` 显式标记。",
    ]
    return "\n".join(lines)


def _infer_chapter_name(
    row: ParsedQuantityStandardRow,
    *,
    standard_label: str = "GB/T 50854-2024",
) -> str:
    prefix = row.item_code[:6]
    chapter_map = {
        "011101": "整体面层及找平层",
        "011102": "块料面层",
        "011103": "橡塑面层",
        "011104": "其他材料面层",
        "011105": "踢脚线",
        "011106": "楼梯面层",
        "011107": "台阶装饰",
        "011108": "零星装饰项目",
        "0112": "墙、柱面装饰与隔断、幕墙工程",
        "0113": "天棚工程",
        "0114": "油漆、涂料、裱糊工程",
        "0108": "门窗工程",
    }
    return chapter_map.get(prefix) or chapter_map.get(row.item_code[:4]) or f"{standard_label} 表格 {row.table_index + 1}"


def _standard_file_stem(standard_code: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "", standard_code).lower()
    if normalized.startswith("gbt"):
        normalized = "gbtn" + normalized[3:]
    return normalized or "standard"


def _unit_options(unit: str) -> list[str]:
    cleaned = unit.strip()
    options = [cleaned] if cleaned else []
    if cleaned in {"m²", "㎡"}:
        options.extend(["㎡", "m2", "平方米"])
    elif cleaned == "m³":
        options.extend(["m³", "m3", "立方米"])
    elif cleaned == "m":
        options.extend(["m", "米"])
    return _dedupe(options)


def _infer_formula_type(unit: str, rule_text: str) -> str:
    text = f"{unit} {rule_text}"
    if "体积" in text or "m³" in text:
        return "volume"
    if "展开面积" in text:
        return "expanded_area"
    if "面积" in text or "m²" in text or "㎡" in text:
        return "area"
    if "长度" in text or unit == "m":
        return "length"
    if unit in {"个", "樘", "套", "项"}:
        return "count"
    return "rule_text"


def _infer_required_evidence(rule_text: str) -> list[str]:
    evidence = ["设计图纸", "标准原文工程量计算规则"]
    if "面积" in rule_text:
        evidence.append("面积边界或尺寸标注")
    if "体积" in rule_text:
        evidence.append("长宽高或体积标注")
    if "长度" in rule_text:
        evidence.append("长度尺寸标注")
    if "展开" in rule_text:
        evidence.append("展开面积计算依据")
    if "门洞" in rule_text or "孔洞" in rule_text:
        evidence.append("洞口尺寸及扣减/并入规则")
    return _dedupe(evidence)


def _item_keywords(row: ParsedQuantityStandardRow) -> list[str]:
    keywords = [row.item_name]
    for part in re.split(r"[\s、/()（）]+", row.item_name):
        if part and part not in keywords:
            keywords.append(part)
    return keywords


def _tc_text(tc: ET.Element) -> str:
    paragraphs: list[str] = []
    for paragraph in tc.findall(".//w:p", WORD_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS))
        if text.strip():
            paragraphs.append(text)
    return _clean_cell_text("\n".join(paragraphs))


def _tc_grid_span(tc: ET.Element) -> int:
    value = tc.find("./w:tcPr/w:gridSpan", WORD_NS)
    if value is None:
        return 1
    raw = value.attrib.get(f"{{{WORD_NS_URI}}}val")
    try:
        return max(1, int(raw or "1"))
    except ValueError:
        return 1


def _tc_vmerge_value(tc: ET.Element) -> str:
    value = tc.find("./w:tcPr/w:vMerge", WORD_NS)
    if value is None:
        return ""
    raw = value.attrib.get(f"{{{WORD_NS_URI}}}val")
    return "continue" if raw in (None, "", "continue") else str(raw)


def _detect_standard_header(table: list[list[str]]) -> tuple[dict[str, int], list[str]]:
    if not table:
        return {}, []
    cells = table[0]
    header_map: dict[str, int] = {}
    warnings: list[str] = []
    for index, cell in enumerate(cells):
        normalized = _normalize_header(_apply_confirmed_ocr_replacements(cell))
        key = STANDARD_HEADER_ALIASES.get(normalized)
        if key:
            header_map[key] = index
    if not REQUIRED_HEADER_KEYS <= set(header_map):
        return {}, []
    return header_map, _dedupe(warnings)


def _parse_standard_row(
    table_index: int,
    row_index: int,
    cells: list[str],
    header_map: dict[str, int],
    header_warnings: list[str],
) -> ParsedQuantityStandardRow | None:
    raw_cells = list(cells)
    corrections = _row_corrections(raw_cells)
    cells = [_apply_confirmed_ocr_replacements(cell) for cell in raw_cells]
    item_code = _cell_by_key(cells, header_map, "item_code")
    if not item_code or not ITEM_CODE_RE.match(item_code):
        return None
    item_name = _cell_by_key(cells, header_map, "item_name")
    feature_text = _cell_by_key(cells, header_map, "feature_text")
    unit = _cell_by_key(cells, header_map, "unit")
    quantity_rule = _cell_by_key(cells, header_map, "quantity_rule")
    work_content = _cell_by_key(cells, header_map, "work_content")
    feature_fields = tuple(split_numbered_items(feature_text))
    work_items = tuple(split_numbered_items(work_content))
    warnings = list(header_warnings)
    warnings.extend(_row_warnings(item_code, item_name, feature_fields, unit, quantity_rule, work_content))
    return ParsedQuantityStandardRow(
        table_index=table_index,
        row_index=row_index,
        item_code=item_code,
        item_name=item_name,
        feature_text=feature_text,
        feature_fields=feature_fields,
        unit=unit,
        quantity_rule=quantity_rule,
        work_content=work_content,
        work_items=work_items,
        raw_cells=tuple(raw_cells),
        warnings=tuple(_dedupe(warnings)),
        corrections=tuple(corrections),
    )


def split_numbered_items(text: str) -> list[str]:
    cleaned = _clean_cell_text(text)
    matches = list(NUMBERED_ITEM_RE.finditer(cleaned))
    if not matches:
        return [cleaned] if cleaned else []
    items: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        value = _clean_cell_text(cleaned[start:end])
        if value:
            items.append(value)
    return items


def _row_warnings(
    item_code: str,
    item_name: str,
    feature_fields: tuple[str, ...],
    unit: str,
    quantity_rule: str,
    work_content: str,
) -> list[str]:
    warnings: list[str] = []
    if not item_name:
        warnings.append("项目名称为空")
    if not unit:
        warnings.append("计量单位为空")
    if not quantity_rule:
        warnings.append("工程量计算规则为空")
    combined = f"{item_code} {item_name} {' '.join(feature_fields)} {quantity_rule} {work_content}"
    return warnings


def _row_corrections(cells: list[str]) -> list[str]:
    corrections: list[str] = []
    raw_text = " ".join(cells)
    for source, target in CONFIRMED_OCR_REPLACEMENTS.items():
        if source in raw_text:
            corrections.append(f"已按确认规则自动修正：{source}->{target}")
    return corrections


def _cell_by_key(cells: list[str], header_map: dict[str, int], key: str) -> str:
    index = header_map.get(key)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _normalize_header(text: str) -> str:
    return _clean_cell_text(text).replace(" ", "")


def _clean_cell_text(text: Any) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\u00a0", " ").replace("\r", "\n")
    value = re.sub(r"[\n\u2028\u2029]+", " | ", value)
    value = CJK_SPACE_RE.sub("", value)
    value = PUNCT_SPACE_RE.sub("", value)
    value = MULTI_SPACE_RE.sub(" ", value)
    value = re.sub(r"\s*\|\s*", " | ", value)
    value = value.replace(" | | ", " | ")
    return value.strip(" |")


def _apply_confirmed_ocr_replacements(text: str) -> str:
    value = text
    for source, target in CONFIRMED_OCR_REPLACEMENTS.items():
        value = value.replace(source, target)
    return value


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
