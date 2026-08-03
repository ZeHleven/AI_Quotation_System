from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


PRICING_RULE_LIBRARY_VERSION = "biz2x-pricing-rule-standard-v0"
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.+)$")
SECTION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s+(.+)$")


class PricingRuleDocxParseError(ValueError):
    pass


def build_pricing_rule_library(
    path: str | Path,
    *,
    standard_code: str = "GBT50500-2024",
    standard_name: str = "建设工程工程量清单计价标准",
    standard_label: str = "GB/T 50500-2024",
) -> dict[str, Any]:
    source = Path(path)
    paragraphs = _read_docx_paragraphs(source)
    tables = _read_docx_tables(source)
    rules = _extract_rules(paragraphs, standard_code=standard_code)
    table_payloads = _build_table_payloads(tables, standard_code=standard_code)
    return {
        "version": PRICING_RULE_LIBRARY_VERSION,
        "standard": {
            "code": standard_code,
            "name": standard_name,
            "label": standard_label,
            "source_file_hint": source.name,
            "source_text_status": "docx_paragraph_and_table_parsed",
            "scope_note": (
                f"本规则库由 {standard_label} Word 条文和表式自动解析生成；"
                "用于清单编制、计价、补充项目、合同计量与价款规则引用。"
            ),
            "strict_rules": [
                "本库不作为工程项目清单项目库使用。",
                "工程项目编码、项目名称、项目特征、单位和工程量规则仍应引用对应专业工程量计算标准。",
                "清单编制、补充项目、计价、合同计量和价款调整口径可引用本规则库。",
            ],
        },
        "summary": {
            "paragraph_count": len(paragraphs),
            "rule_count": len(rules),
            "table_count": len(table_payloads),
            "table_row_count": sum(table["row_count"] for table in table_payloads),
            "category_counts": _category_counts(rules),
            "parsed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "paragraphs": paragraphs,
        "rules": rules,
        "tables": table_payloads,
    }


def write_pricing_rule_library_outputs(
    path: str | Path,
    output_dir: str | Path,
    *,
    stem: str | None = None,
    standard_code: str = "GBT50500-2024",
    standard_name: str = "建设工程工程量清单计价标准",
    standard_label: str = "GB/T 50500-2024",
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    library = build_pricing_rule_library(
        path,
        standard_code=standard_code,
        standard_name=standard_name,
        standard_label=standard_label,
    )
    file_stem = stem or f"{_standard_file_stem(standard_code)}_rule_active_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    json_path.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_build_pricing_rule_markdown(library), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def _read_docx_paragraphs(source: Path) -> list[dict[str, Any]]:
    root = _read_document_root(source)
    paragraphs: list[dict[str, Any]] = []
    for index, paragraph in enumerate(root.findall(".//w:p", WORD_NS), start=1):
        text = _clean_text("".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)))
        if not text:
            continue
        paragraphs.append(
            {
                "paragraph_index": index,
                "text": text,
                "kind": _paragraph_kind(text),
            }
        )
    return paragraphs


def _read_docx_tables(source: Path) -> list[list[list[str]]]:
    root = _read_document_root(source)
    tables: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", WORD_NS):
        rows: list[list[str]] = []
        for tr in table.findall("./w:tr", WORD_NS):
            cells = [_clean_text("".join(node.text or "" for node in tc.findall(".//w:t", WORD_NS))) for tc in tr.findall("./w:tc", WORD_NS)]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _read_document_root(source: Path) -> ET.Element:
    if not source.exists():
        raise PricingRuleDocxParseError(f"pricing standard Word file not found: {source}")
    try:
        with zipfile.ZipFile(source) as archive:
            return ET.fromstring(archive.read("word/document.xml"))
    except KeyError as exc:
        raise PricingRuleDocxParseError(f"Word file missing word/document.xml: {source}") from exc
    except zipfile.BadZipFile as exc:
        raise PricingRuleDocxParseError(f"invalid Word .docx zip package: {source}") from exc


def _extract_rules(paragraphs: list[dict[str, Any]], *, standard_code: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    current_section = ""
    for paragraph in paragraphs:
        text = paragraph["text"]
        section_match = SECTION_RE.match(text)
        if section_match and text.count(".") <= 1:
            current_section = text
        clause_match = CLAUSE_RE.match(text)
        if not clause_match:
            continue
        clause_no = clause_match.group(1)
        rules.append(
            {
                "rule_id": f"{standard_code}-{clause_no}",
                "clause_no": clause_no,
                "section": current_section,
                "text": text,
                "category": _rule_category(text),
                "keywords": _rule_keywords(text),
                "source": {
                    "type": "docx_paragraph",
                    "paragraph_index": paragraph["paragraph_index"],
                },
            }
        )
    return rules


def _build_table_payloads(tables: list[list[list[str]]], *, standard_code: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, rows in enumerate(tables, start=1):
        payloads.append(
            {
                "table_id": f"{standard_code}-TABLE-{index:03d}",
                "table_index": index,
                "row_count": len(rows),
                "column_count": max((len(row) for row in rows), default=0),
                "header": rows[0] if rows else [],
                "rows": rows,
                "category": _table_category(rows),
            }
        )
    return payloads


def _paragraph_kind(text: str) -> str:
    if CLAUSE_RE.match(text):
        return "clause"
    if SECTION_RE.match(text):
        return "section_or_toc"
    return "paragraph"


def _rule_category(text: str) -> str:
    category_terms = [
        ("bill_compilation", ("工程量清单编制", "编制工程量清单", "项目特征", "补充项目")),
        ("pricing", ("计价", "综合单价", "最高投标限价", "投标报价")),
        ("contract_measurement", ("合同工程计量", "工程计量", "计量周期")),
        ("price_adjustment", ("合同价款调整", "价款调整", "价格调整")),
        ("payment_settlement", ("支付", "结算", "预付款", "进度款")),
        ("dispute_archive", ("争议", "档案", "成果文件")),
    ]
    for category, terms in category_terms:
        if any(term in text for term in terms):
            return category
    return "general"


def _rule_keywords(text: str) -> list[str]:
    candidates = [
        "工程量清单",
        "项目编码",
        "项目名称",
        "项目特征",
        "计量单位",
        "工程量",
        "补充项目",
        "综合单价",
        "最高投标限价",
        "投标报价",
        "合同工程计量",
        "合同价款",
        "价款调整",
        "结算",
        "支付",
    ]
    return [keyword for keyword in candidates if keyword in text]


def _table_category(rows: list[list[str]]) -> str:
    header = " ".join(rows[0]) if rows else ""
    if "项目编码" in header and "项目特征" in header:
        return "bill_item_form"
    if "措施项目" in header:
        return "measure_form"
    if "计日工" in header:
        return "daywork_form"
    if "支付" in header or "结算" in header:
        return "payment_settlement_form"
    if "材料" in header:
        return "material_form"
    return "pricing_form"


def _category_counts(rules: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rule in rules:
        category = str(rule.get("category") or "general")
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _build_pricing_rule_markdown(library: dict[str, Any]) -> str:
    standard = library["standard"]
    summary = library["summary"]
    lines = [
        f"# {standard['code']} 规则库导入结果",
        "",
        f"- 标准：{standard['code']} {standard['name']}",
        f"- 段落数：{summary['paragraph_count']}",
        f"- 条款规则数：{summary['rule_count']}",
        f"- 表式数：{summary['table_count']}",
        f"- 表式总行数：{summary['table_row_count']}",
        "",
        "## 规则分类",
        "",
    ]
    for category, count in summary["category_counts"].items():
        lines.append(f"- {category}: {count}")
    lines.extend(["", "## 使用边界", ""])
    for rule in standard["strict_rules"]:
        lines.append(f"- {rule}")
    return "\n".join(lines) + "\n"


def _standard_file_stem(standard_code: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "", standard_code).lower()
    if normalized.startswith("gbt"):
        normalized = "gbtn" + normalized[3:]
    return normalized or "pricing_rule"


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
