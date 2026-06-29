from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_quantity_list_draft_v1"

DEFAULT_PROCESSED_CANDIDATES_JSON = Path(
    "outputs/biz2x_trial/quote_candidates/20260626_stage4_quote_candidates_full2624_mvp/"
    "quote_candidates_system_processed.json"
)

FOUR_FIELD_HEADERS = ["项目名称", "项目特征", "单位", "工程量"]
REVIEW_HEADERS = [
    "清单ID",
    "候选ID",
    "生成状态",
    "项目名称",
    "项目特征",
    "单位",
    "工程量",
    "单位判断原因",
    "工程量判断原因",
    "项目名称质量",
    "项目名称判断原因",
    "候选类型",
    "下一步归口",
    "主证据ID",
    "关联规格",
    "关联工程量线索",
    "关联材料代号/索引",
    "截图文件",
]

QUANTITY_VALUE_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(m²|㎡|m2|m|米|个|套|樘|盏|只|处|项|kg|t)")


def read_system_processed_candidates_json(path: str | Path) -> list[dict[str, Any]]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        return []
    candidates = parsed.get("quote_candidates")
    if not isinstance(candidates, list):
        return []
    return [dict(item) for item in candidates if isinstance(item, Mapping)]


def build_quantity_list_draft(
    *,
    candidates: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    include_decisions: Sequence[str] = ("确认有效",),
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    selected = [
        dict(candidate)
        for candidate in candidates
        if _text(candidate.get("system_decision_cn") or candidate.get("manual_confirmation")) in include_decisions
    ]
    rows = [_draft_row(index + 1, candidate) for index, candidate in enumerate(selected)]
    summary = _summary(candidates, selected, rows)
    outputs = _write_outputs(directory, rows, summary)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_text(),
        "summary": summary,
        "outputs": outputs,
        "quantity_list_draft_rows": rows,
    }


def _draft_row(index: int, candidate: Mapping[str, Any]) -> dict[str, Any]:
    item_name = _clean_item_name(candidate)
    feature = _build_feature(candidate)
    unit, unit_reason, unit_confidence = _infer_unit(candidate)
    quantity, quantity_reason, quantity_status = _extract_quantity(candidate)
    name_quality, name_reason = _name_quality(item_name)
    status = _row_status(unit_confidence, quantity_status, name_quality)
    return {
        "schema_version": SCHEMA_VERSION,
        "list_item_id": f"QL{index:04d}",
        "candidate_id": _text(candidate.get("candidate_id")),
        "candidate_type": _text(candidate.get("candidate_type")),
        "source_decision": _text(candidate.get("system_decision_cn") or candidate.get("manual_confirmation")),
        "next_stage_bucket": _text(candidate.get("system_next_stage_bucket_cn")),
        "项目名称": item_name,
        "项目特征": feature,
        "单位": unit,
        "工程量": quantity,
        "generation_status_cn": status,
        "unit_reason_cn": unit_reason,
        "quantity_reason_cn": quantity_reason,
        "quantity_status_cn": quantity_status,
        "unit_confidence_cn": unit_confidence,
        "name_quality_cn": name_quality,
        "name_reason_cn": name_reason,
        "primary_evidence_ids": _string_list(candidate.get("primary_evidence_ids")),
        "attached_specs": _string_list(candidate.get("attached_specs")),
        "attached_quantity_clues": _string_list(candidate.get("attached_quantity_clues")),
        "attached_codes": _string_list(candidate.get("attached_codes")),
        "image_files": _string_list(candidate.get("image_files")),
    }


def _clean_item_name(candidate: Mapping[str, Any]) -> str:
    text = _text(candidate.get("draft_item_name") or candidate.get("representative_text"))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[：:；;、,，.。]+", "", text).strip()
    return text or "待确认项目名称"


def _build_feature(candidate: Mapping[str, Any]) -> str:
    parts: list[str] = []
    name = _clean_item_name(candidate)
    raw_feature = _text(candidate.get("draft_item_feature"))
    if raw_feature and raw_feature != name:
        parts.extend(_split_feature(raw_feature))
    for value in _string_list(candidate.get("attached_specs")):
        if _looks_feature_value(value):
            parts.append(value)
    for value in _string_list(candidate.get("attached_codes"))[:4]:
        if value:
            parts.append(value)
    if not parts:
        parts.append(name)
    return "；".join(_dedupe(parts))


def _infer_unit(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    candidate_type = _text(candidate.get("candidate_type"))
    name_text = _clean_item_name(candidate)
    feature_text = _text(candidate.get("draft_item_feature"))
    text = f"{name_text} {feature_text}"
    normalized = name_text.lower()

    if candidate_type == "拆除项":
        if any(keyword in name_text for keyword in ["门", "窗"]):
            return "樘", "拆除项命中门窗类关键词，暂按樘计量。", "中"
        if any(keyword in name_text for keyword in ["灯盘", "筒灯", "LED", "led", "灯具", "灯"]):
            return "套", "拆除项命中灯具类关键词，暂按套计量。", "中"
        if "管线" in name_text or "、" in name_text:
            return "项", "拆除项包含管线或多个拆除对象，先按项暂列。", "低"
        if _has_surface_keyword(name_text):
            return "㎡", "拆除项命中墙地顶或面层关键词，暂按面积计量。", "中"
        return "项", "拆除项未命中更明确计量关键词，先按项暂列。", "低"

    if any(keyword in name_text for keyword in ["踢脚线", "线条", "收边条", "压条"]):
        return "m", "命中线性构件关键词，按长度计量。", "高"
    if any(keyword in name_text for keyword in ["线型灯"]):
        return "m", "命中线型灯关键词，暂按长度计量。", "中"
    if any(keyword in name_text for keyword in ["门", "门套", "窗"]):
        return "樘", "命中门窗类关键词，暂按樘计量。", "中"
    if any(keyword in name_text for keyword in ["灯盘", "筒灯", "LED", "led", "灯具"]):
        return "套", "命中灯具类关键词，暂按套计量。", "中"
    if any(keyword in name_text for keyword in ["吊杆", "龙骨", "方通", "扁通"]):
        return "m", "命中杆件/龙骨/方通类关键词，暂按长度计量。", "中"
    if _has_surface_keyword(name_text):
        return "㎡", "命中面层/墙地顶/板材类关键词，按面积计量。", "中"
    if "kg" in normalized:
        return "kg", "文本中出现 kg 计量线索。", "中"
    if _has_surface_keyword(feature_text):
        return "㎡", "项目特征中命中面层/板材类关键词，但项目名称不充分，单位需复核。", "低"
    return "待确认", "未命中稳定单位规则，需要人工或后续规则确认。", "低"


def _extract_quantity(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    clues = _string_list(candidate.get("attached_quantity_clues"))
    matches: list[str] = []
    for clue in clues:
        for match in QUANTITY_VALUE_RE.finditer(clue):
            value, unit = match.groups()
            unit = "㎡" if unit.lower() in {"m²", "m2"} else unit
            unit = "m" if unit == "米" else unit
            matches.append(f"{value}{unit}")
    matches = _dedupe(matches)
    if len(matches) == 1:
        return matches[0], f"从关联工程量线索中抽取：{matches[0]}。", "已抽取"
    if len(matches) > 1:
        return "", f"存在多个工程量候选：{'；'.join(matches)}，暂不自动选择。", "待确认"
    if clues:
        return "", f"存在工程量线索但未识别出明确数值单位：{'；'.join(clues[:4])}。", "待确认"
    return "", "候选未挂接明确工程量线索，工程量留空，后续由图形计算/VLM/人工补量。", "待计算"


def _row_status(unit_confidence: str, quantity_status: str, name_quality: str) -> str:
    if name_quality != "正常":
        return "项目名称待确认"
    if quantity_status == "已抽取" and unit_confidence in {"高", "中"}:
        return "可用草案"
    if quantity_status == "已抽取":
        return "单位待确认"
    if quantity_status == "待确认":
        return "工程量待确认"
    if unit_confidence == "低":
        return "缺工程量且单位待确认"
    return "缺工程量"


def _name_quality(name: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", name)
    if not compact or compact == "待确认项目名称":
        return "待确认", "项目名称为空或无法从候选中稳定生成。"
    if len(compact) <= 1:
        return "待确认", "项目名称过短。"
    if re.match(r"^(注|说明|备注|详见|图例)", compact):
        return "待确认", "更像图纸说明或备注，不宜直接作为清单项目。"
    if any(keyword in compact for keyword in ["所有", "均置顶", "参见", "详图", "索引"]):
        return "待确认", "包含说明性词语，需人工确认是否为真实列项。"
    return "正常", "项目名称来自确认有效候选。"


def _has_surface_keyword(text: str) -> bool:
    return any(
        keyword in text
        for keyword in [
            "墙面",
            "地面",
            "天花",
            "吊顶",
            "墙砖",
            "地砖",
            "面砖",
            "瓷砖",
            "石材",
            "大理石",
            "石膏板",
            "木饰面",
            "墙布",
            "硬包",
            "涂料",
            "防水",
            "玻璃",
            "隔墙",
            "砌块墙",
            "砖墙",
            "美缝",
            "铝扣板",
            "回填",
        ]
    )


def _write_outputs(directory: Path, rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, str]:
    draft_json = directory / "quantity_list_draft.json"
    draft_csv = directory / "quantity_list_draft.csv"
    four_field_csv = directory / "quantity_list_four_fields.csv"
    review_md = directory / "quantity_list_draft_review.md"
    summary_json = directory / "quantity_list_draft_summary.json"
    _write_json(
        draft_json,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_text(),
            "summary": summary,
            "quantity_list_draft_rows": list(rows),
        },
    )
    _write_review_csv(draft_csv, rows)
    _write_four_field_csv(four_field_csv, rows)
    review_md.write_text(build_quantity_list_draft_markdown(rows, summary), encoding="utf-8")
    _write_json(summary_json, summary)
    return {
        "quantity_list_draft_json": str(draft_json.resolve()),
        "quantity_list_draft_csv": str(draft_csv.resolve()),
        "quantity_list_four_fields_csv": str(four_field_csv.resolve()),
        "quantity_list_draft_review_md": str(review_md.resolve()),
        "quantity_list_draft_summary_json": str(summary_json.resolve()),
    }


def build_quantity_list_draft_markdown(rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> str:
    lines = [
        "# 阶段 5 列项清单四字段草案",
        "",
        f"- 输入确认有效候选：{summary.get('selected_candidate_count', 0)}",
        f"- 输出清单草案：{summary.get('draft_item_count', 0)}",
        f"- 可用草案：{summary.get('usable_draft_count', 0)}",
        f"- 缺工程量：{summary.get('missing_quantity_count', 0)}",
        f"- 工程量待确认：{summary.get('quantity_pending_count', 0)}",
        f"- 单位待确认：{summary.get('unit_pending_count', 0)}",
        f"- 项目名称待确认：{summary.get('name_pending_count', 0)}",
        "",
        "## 状态统计",
        "",
        "| 生成状态 | 数量 |",
        "|---|---:|",
    ]
    for status, count in (summary.get("generation_status_counts") or {}).items():
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## 前 40 条四字段草案", "", "| 清单ID | 项目名称 | 项目特征 | 单位 | 工程量 | 生成状态 |", "|---|---|---|---|---|---|"])
    for row in list(rows)[:40]:
        lines.append(
            "| {id} | {name} | {feature} | {unit} | {qty} | {status} |".format(
                id=row.get("list_item_id", ""),
                name=_md(row.get("项目名称")),
                feature=_md(_text(row.get("项目特征"))[:120]),
                unit=_md(row.get("单位")),
                qty=_md(row.get("工程量") or "待计算"),
                status=_md(row.get("generation_status_cn")),
            )
        )
    return "\n".join(lines) + "\n"


def _write_review_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=REVIEW_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "清单ID": row.get("list_item_id"),
                    "候选ID": row.get("candidate_id"),
                    "生成状态": row.get("generation_status_cn"),
                    "项目名称": row.get("项目名称"),
                    "项目特征": row.get("项目特征"),
                    "单位": row.get("单位"),
                    "工程量": row.get("工程量"),
                    "单位判断原因": row.get("unit_reason_cn"),
                    "工程量判断原因": row.get("quantity_reason_cn"),
                    "项目名称质量": row.get("name_quality_cn"),
                    "项目名称判断原因": row.get("name_reason_cn"),
                    "候选类型": row.get("candidate_type"),
                    "下一步归口": row.get("next_stage_bucket"),
                    "主证据ID": "；".join(row.get("primary_evidence_ids") or []),
                    "关联规格": "；".join(row.get("attached_specs") or []),
                    "关联工程量线索": "；".join(row.get("attached_quantity_clues") or []),
                    "关联材料代号/索引": "；".join(row.get("attached_codes") or []),
                    "截图文件": "；".join(row.get("image_files") or []),
                }
            )


def _write_four_field_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FOUR_FIELD_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in FOUR_FIELD_HEADERS})


def _summary(
    all_candidates: Sequence[Mapping[str, Any]],
    selected_candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    status_counts = Counter(row.get("generation_status_cn") for row in rows)
    unit_counts = Counter(row.get("单位") for row in rows)
    type_counts = Counter(candidate.get("candidate_type") for candidate in selected_candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "input_candidate_count": len(all_candidates),
        "selected_candidate_count": len(selected_candidates),
        "draft_item_count": len(rows),
        "usable_draft_count": status_counts.get("可用草案", 0),
        "missing_quantity_count": sum(1 for row in rows if row.get("quantity_status_cn") == "待计算"),
        "quantity_pending_count": sum(1 for row in rows if row.get("quantity_status_cn") == "待确认"),
        "unit_pending_count": sum(1 for row in rows if row.get("单位") == "待确认" or row.get("unit_confidence_cn") == "低"),
        "name_pending_count": sum(1 for row in rows if row.get("name_quality_cn") != "正常"),
        "generation_status_counts": dict(status_counts),
        "unit_counts": dict(unit_counts),
        "candidate_type_counts": dict(type_counts),
    }


def _split_feature(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[；;\n]+", text) if item.strip()]


def _looks_feature_value(text: str) -> bool:
    clean = _text(text)
    if not clean:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", clean):
        return False
    return True


def _dedupe(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [item.strip() for item in re.split(r"[;；,，]", text) if item.strip()]
    if isinstance(parsed, list):
        return [_text(item) for item in parsed if _text(item)]
    return [text]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _md(value: Any) -> str:
    return _text(value).replace("|", "｜").replace("\n", " ") or "-"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
