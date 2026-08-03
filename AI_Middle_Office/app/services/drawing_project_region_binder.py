from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


HIGH_SCORE = 7.0
MEDIUM_SCORE = 4.0
MEDIUM_READY_SCORE = 5.5
LEADING_SCORE_GAP = 1.5
MAX_CANDIDATES_PER_PROJECT = 8

BINDING_ROW_HEADERS = [
    "识别项目编号",
    "图纸项目名称",
    "项目名称",
    "单位",
    "推荐区域编号",
    "区域绑定状态",
    "区域绑定置信度",
    "候选区域数量",
    "区域面积",
    "区域周长",
    "区域类型建议",
    "区域文字证据",
    "工程量计算方式建议",
    "可进入专项算量",
    "绑定说明",
    "来源文件",
]

CANDIDATE_ROW_HEADERS = [
    "识别项目编号",
    "项目名称",
    "区域编号",
    "CAD面积",
    "CAD周长",
    "来源文件",
    "图层",
    "实体类型",
    "区域类型建议",
    "房间/空间标签",
    "项目标签",
    "区域文字证据",
    "绑定评分",
    "绑定置信度",
    "评分原因",
]

ROOM_KEYWORDS = (
    "餐厅",
    "食堂",
    "洗手间",
    "卫生间",
    "厨房",
    "包间",
    "大厅",
    "前厅",
    "走道",
    "过道",
    "库房",
    "更衣",
    "操作间",
    "备餐",
    "办公室",
    "会议室",
)

PROJECT_TERMS = (
    "防水",
    "踢脚",
    "踢脚线",
    "吊顶",
    "天棚",
    "天花",
    "顶面",
    "地面",
    "楼地面",
    "地砖",
    "地板",
    "墙面",
    "立面",
    "涂料",
    "乳胶漆",
    "腻子",
    "石膏板",
    "窗帘盒",
    "门",
    "窗",
)

NOISE_TERMS = (
    "图例",
    "图框",
    "图号",
    "目录",
    "说明",
    "材料表",
    "构造做法",
    "节点",
    "大样",
    "索引",
    "比例",
    "日期",
    "设计",
    "审核",
    "审定",
    "家具",
    "furniture",
    "scale",
    "门洞砌墙示意图",
    "门槛石",
)

NON_CONSTRUCTION_SOURCE_TERMS = (
    "门表",
    "图框",
    "目录",
    "前言",
    "说明",
    "材料表",
    "构造做法",
    "通用节点",
    "节点",
    "大样",
)

NON_CONSTRUCTION_LAYER_TERMS = (
    "图框",
    "门表",
    "立面造型线",
    "elevation",
    "索引",
    "标注",
    "家具",
    "furniture",
)


def build_project_region_binding_report(
    *,
    project_report: dict[str, Any],
    region_label_report: dict[str, Any],
) -> dict[str, Any]:
    projects = list(project_report.get("project_rows") or [])
    regions = _collect_region_candidates(region_label_report)

    binding_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for project in projects:
        scored = sorted(
            (_score_region_candidate(project, region) for region in regions),
            key=lambda item: item["score"],
            reverse=True,
        )
        selected = [item for item in scored if item["score"] >= MEDIUM_SCORE][:MAX_CANDIDATES_PER_PROJECT]
        binding_rows.append(_binding_row(project, selected))
        for item in selected:
            candidate_rows.append(_candidate_row(project, item))

    status_counts = Counter(row["区域绑定状态"] for row in binding_rows)
    return {
        "ok": True,
        "phase": "BIZ-2x-project-region-binding",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "safe_for_final_quantity_list": False,
        "summary": {
            "recognized_project_count": len(projects),
            "region_candidate_count": len(regions),
            "binding_ready_project_count": sum(1 for row in binding_rows if row["区域绑定状态"] == "建议绑定区域，需复核"),
            "ambiguous_project_count": sum(1 for row in binding_rows if row["区域绑定状态"] == "多个区域候选需选择"),
            "unbound_project_count": sum(1 for row in binding_rows if row["区域绑定状态"] == "未找到可绑定区域"),
            "candidate_option_count": len(candidate_rows),
            "binding_status_counts": dict(status_counts.most_common()),
            "final_generation_status": "blocked_until_standard_quantity_calculator",
            "next_step": "run_room_boundary_and_special_quantity_calculators",
        },
        "binding_rows": binding_rows,
        "candidate_rows": candidate_rows,
        "notes": [
            "本报告解决项目与 CAD 区域/房间/文字证据的对应关系，不直接生成最终工程量。",
            "区域面积、区域周长只是专项算量的输入，后续必须继续按 GB/T 标准库工程量计算规则处理扣减、并入和复用关系。",
            "未绑定或多候选项目不得写入最终四字段工程量 Excel。",
        ],
    }


def write_project_region_binding_outputs(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x_项目区域绑定_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    binding_csv_path = target_dir / f"{file_stem}_项目区域绑定状态.csv"
    candidate_csv_path = target_dir / f"{file_stem}_项目区域候选明细.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_project_region_binding_markdown(report), encoding="utf-8")
    _write_csv(binding_csv_path, report.get("binding_rows") or [], BINDING_ROW_HEADERS)
    _write_csv(candidate_csv_path, report.get("candidate_rows") or [], CANDIDATE_ROW_HEADERS)
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "binding_csv": str(binding_csv_path),
        "candidate_csv": str(candidate_csv_path),
    }


def build_project_region_binding_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x 图纸项目与 CAD 区域绑定报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 图纸项目数：{summary.get('recognized_project_count', 0)}",
        f"- CAD 区域候选数：{summary.get('region_candidate_count', 0)}",
        f"- 建议绑定项目：{summary.get('binding_ready_project_count', 0)}",
        f"- 多候选项目：{summary.get('ambiguous_project_count', 0)}",
        f"- 未绑定项目：{summary.get('unbound_project_count', 0)}",
        "",
        "## 项目-区域绑定状态",
        "",
        "| 项目编号 | 项目名称 | 状态 | 推荐区域 | 面积 | 周长 | 算量方式 | 说明 |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in (report.get("binding_rows") or [])[:120]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("识别项目编号")),
                    _md(row.get("项目名称")),
                    _md(row.get("区域绑定状态")),
                    _md(row.get("推荐区域编号")),
                    _md(row.get("区域面积")),
                    _md(row.get("区域周长")),
                    _md(row.get("工程量计算方式建议")),
                    _md(row.get("绑定说明")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本报告只把项目绑定到 CAD 区域，不把区域面积/周长直接作为最终工程量。",
            "- 后续需要 A3/A4 计算器按标准规则处理净周长、墙面高度、门洞扣减、面层复用等问题。",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_region_candidates(region_label_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = region_label_report.get("region_rows") or region_label_report.get("region_index_rows") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        area = _float_or_none(row.get("CAD面积"))
        perimeter = _float_or_none(row.get("CAD周长"))
        if area is None:
            continue
        evidence_text = _region_evidence_text(row)
        result.append(
            {
                "region_id": str(row.get("区域编号") or ""),
                "source_file": str(row.get("来源文件") or ""),
                "area": round(area, 4),
                "perimeter": round(perimeter or 0.0, 4),
                "layer": str(row.get("图层") or ""),
                "entity_type": str(row.get("实体类型") or ""),
                "line_number": row.get("源行号", ""),
                "room_labels": str(row.get("房间/空间标签") or ""),
                "project_labels": str(row.get("项目标签") or ""),
                "region_type": str(row.get("区域类型建议") or ""),
                "status": str(row.get("绑定状态") or ""),
                "evidence_text": evidence_text,
            }
        )
    return result


def _binding_row(project: dict[str, Any], selected: list[dict[str, Any]]) -> dict[str, Any]:
    high = [item for item in selected if item["score"] >= HIGH_SCORE]
    best_gap = _top_score_gap(selected)
    if len(high) == 1:
        best = high[0]
        status = "建议绑定区域，需复核"
        explanation = "；".join(best["reasons"][:4])
    elif len(high) > 1 and best_gap >= LEADING_SCORE_GAP:
        best = high[0]
        status = "建议绑定区域，需复核"
        explanation = "最高分区域明显领先其他高分候选；" + "；".join(best["reasons"][:4])
    elif len(high) > 1:
        best = high[0]
        status = "多个区域候选需选择"
        explanation = f"找到 {len(high)} 个高分区域候选，需要确认施工范围"
    elif selected and len(selected) == 1 and selected[0]["score"] >= MEDIUM_READY_SCORE:
        best = selected[0]
        status = "建议绑定区域，需复核"
        explanation = "只有 1 个中高分区域候选；" + "；".join(best["reasons"][:4])
    elif selected and selected[0]["score"] >= MEDIUM_READY_SCORE and best_gap >= LEADING_SCORE_GAP:
        best = selected[0]
        status = "建议绑定区域，需复核"
        explanation = "最高分区域明显领先其他候选；" + "；".join(best["reasons"][:4])
    elif selected:
        best = selected[0]
        status = "多个区域候选需选择"
        explanation = "存在区域候选但证据不够唯一，需要结合图纸视口、材料编号或人工确认"
    else:
        best = None
        status = "未找到可绑定区域"
        explanation = "未找到房间/区域文字、项目标签和来源文件均足够匹配的 CAD 区域"

    method = _calculation_method(project, best["region"] if best else None)
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "图纸项目名称": project.get("图纸项目名称", ""),
        "项目名称": project.get("项目名称", ""),
        "单位": project.get("单位", ""),
        "推荐区域编号": best["region"]["region_id"] if best else "",
        "区域绑定状态": status,
        "区域绑定置信度": _confidence(best["score"]) if best else "",
        "候选区域数量": len(selected),
        "区域面积": best["region"]["area"] if best else "",
        "区域周长": best["region"]["perimeter"] if best else "",
        "区域类型建议": best["region"]["region_type"] if best else "",
        "区域文字证据": best["region"]["evidence_text"] if best else "",
        "工程量计算方式建议": method,
        "可进入专项算量": "是，需继续按标准规则计算" if best else "否",
        "绑定说明": explanation,
        "来源文件": project.get("来源文件", ""),
    }


def _candidate_row(project: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    region = scored["region"]
    return {
        "识别项目编号": project.get("识别项目编号", ""),
        "项目名称": project.get("项目名称", ""),
        "区域编号": region["region_id"],
        "CAD面积": region["area"],
        "CAD周长": region["perimeter"],
        "来源文件": region["source_file"],
        "图层": region["layer"],
        "实体类型": region["entity_type"],
        "区域类型建议": region["region_type"],
        "房间/空间标签": region["room_labels"],
        "项目标签": region["project_labels"],
        "区域文字证据": region["evidence_text"],
        "绑定评分": scored["score"],
        "绑定置信度": _confidence(scored["score"]),
        "评分原因": "；".join(scored["reasons"]),
    }


def _score_region_candidate(project: dict[str, Any], region: dict[str, Any]) -> dict[str, Any]:
    project_text = _project_text(project)
    project_source = _normalize(str(project.get("来源文件") or ""))
    region_file = _normalize(region["source_file"])
    region_text = _normalize(
        " ".join(
            [
                region["source_file"],
                region["layer"],
                region["room_labels"],
                region["project_labels"],
                region["region_type"],
                region["evidence_text"],
            ]
        )
    )
    score = 0.0
    reasons: list[str] = []

    if region_file and region_file in project_source:
        score += 2.5
        reasons.append("来源文件一致")

    room_terms = [term for term in ROOM_KEYWORDS if term in project_text and term in region_text]
    if room_terms:
        score += min(4.0, len(room_terms) * 2.5)
        reasons.append("房间/空间名称一致：" + "、".join(room_terms[:3]))

    project_terms = [term for term in PROJECT_TERMS if term in project_text and term in region_text]
    if project_terms:
        score += min(5.0, len(project_terms) * 1.4 + 1.0)
        reasons.append("项目/做法关键词一致：" + "、".join(project_terms[:4]))

    category_score, category_reason = _category_compatibility(project_text, region_text, region)
    if category_score:
        score += category_score
        reasons.append(category_reason)

    if region["evidence_text"]:
        score += 1.0
        reasons.append("区域存在文字证据")
    if region["status"] and "未绑定" not in region["status"]:
        score += 0.8
        reasons.append("区域已完成文字绑定")

    if _is_noise_region(region_text):
        score -= 3.0
        reasons.append("区域文字疑似图例/节点/说明噪声")
    source_penalty, source_reason = _non_construction_region_penalty(region)
    if source_penalty:
        score -= source_penalty
        reasons.append(source_reason)
    if "平面" in region_file:
        score += 1.2
        reasons.append("区域来源为平面图，优先作为施工范围候选")
    if float(region.get("area") or 0) < 0.8:
        score -= 1.0
        reasons.append("区域面积偏小，需防止局部碎片")
    if not reasons:
        reasons.append("仅有弱区域证据，不能自动绑定")
    return {"region": region, "score": round(max(0.0, score), 2), "reasons": reasons}


def _category_compatibility(project_text: str, region_text: str, region: dict[str, Any]) -> tuple[float, str]:
    if "防水" in project_text:
        if "防水" in region_text or any(term in region_text for term in ("洗手间", "卫生间", "厨房", "湿区")):
            return 3.0, "防水项目与湿区/防水区域兼容"
        return 0.8, "防水项目可复用房间区域，仍需确认湿区范围"
    if "踢脚" in project_text:
        if region["room_labels"] or any(term in region_text for term in ROOM_KEYWORDS):
            return 2.5, "踢脚线可复用房间周长候选"
    if any(term in project_text for term in ("吊顶", "天棚", "天花")):
        if any(term in region_text for term in ("吊顶", "天棚", "天花", "顶面", "石膏板")):
            return 3.0, "吊顶/天棚项目与顶面区域兼容"
    if any(term in project_text for term in ("涂料", "乳胶漆", "腻子")):
        if any(term in region_text for term in ("吊顶", "天棚", "天花", "墙面", "立面", "涂料", "乳胶漆")):
            return 2.5, "涂料/乳胶漆项目可复用对应面层区域"
    if any(term in project_text for term in ("地面", "楼地面", "地砖", "地板")):
        if any(term in region_text for term in ("地面", "楼地面", "地砖", "地板", "铺装")):
            return 3.0, "地面项目与地面区域兼容"
    return 0.0, ""


def _calculation_method(project: dict[str, Any], region: dict[str, Any] | None) -> str:
    text = _project_text(project)
    height = _extract_height_m(text)
    if "防水" in text:
        height_note = f"防水高度 {height:g}m" if height else "待识别/确认防水高度"
        return f"墙面防水：区域周长 × {height_note}，后续按标准规则扣除门洞/开口"
    if "踢脚" in text:
        return "踢脚线：复用区域周长，后续按净周长和门洞扣减规则计算"
    if any(term in text for term in ("吊顶", "天棚", "天花")):
        return "吊顶/天棚：复用绑定区域水平投影面积，后续按标准扣减/并入规则计算"
    if any(term in text for term in ("涂料", "乳胶漆", "腻子")):
        if any(term in text for term in ("天棚", "天花", "顶面")):
            return "天棚喷刷涂料：复用天棚/吊顶区域面积，后续按标准规则计算"
        return "墙面/顶面涂料：需结合对应区域面积或墙面展开面积，后续专项算量"
    if any(term in text for term in ("地面", "楼地面", "地砖", "地板")):
        return "地面：复用闭合区域面积，后续按标准扣减/并入规则计算"
    if region:
        return "已找到区域证据，后续按标准库工程量计算规则选择面积/周长/数量模板"
    return "未找到区域证据，暂不能进入标准规则算量"


def _extract_height_m(text: str) -> float | None:
    patterns = (
        r"(?:高度|高|h)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)?",
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)\s*(?:高|高度)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _float_or_none(match.group(1))
        unit = (match.group(2) if len(match.groups()) >= 2 else "") or ""
        if value is None:
            continue
        if unit in {"m", "米"}:
            return value
        if value > 20:
            return value / 1000
        return value
    return None


def _project_text(project: dict[str, Any]) -> str:
    return _normalize(
        " ".join(
            [
                str(project.get("图纸项目名称") or ""),
                str(project.get("项目名称") or ""),
                str(project.get("项目特征") or ""),
                str(project.get("识别证据") or ""),
            ]
        )
    )


def _region_evidence_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("区域内文字") or ""),
        str(row.get("附近文字") or ""),
        str(row.get("房间/空间标签") or ""),
        str(row.get("项目标签") or ""),
        str(row.get("区域类型建议") or ""),
    ]
    return "；".join(part for part in parts if part)


def _is_noise_region(text: str) -> bool:
    return any(term in text for term in NOISE_TERMS)


def _non_construction_region_penalty(region: dict[str, Any]) -> tuple[float, str]:
    source_file = _normalize(str(region.get("source_file") or ""))
    layer = _normalize(str(region.get("layer") or ""))
    if any(term in source_file for term in NON_CONSTRUCTION_SOURCE_TERMS):
        return 5.0, "区域来源疑似门表/大样/节点/说明，不作为平面施工范围优先候选"
    if any(term in layer for term in NON_CONSTRUCTION_LAYER_TERMS):
        return 4.0, "区域图层疑似图框/门表/立面/标注，不作为平面施工范围优先候选"
    return 0.0, ""


def _top_score_gap(selected: list[dict[str, Any]]) -> float:
    if not selected:
        return 0.0
    if len(selected) == 1:
        return selected[0]["score"]
    return round(selected[0]["score"] - selected[1]["score"], 2)


def _confidence(score: float) -> str:
    if score >= HIGH_SCORE:
        return f"高({score})"
    if score >= MEDIUM_SCORE:
        return f"中({score})"
    if score > 0:
        return f"低({score})"
    return "无"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{header: row.get(header, "") for header in headers} for row in rows])
