from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_quote_candidate_v1"
SYSTEM_PROCESSED_SCHEMA_VERSION = "drawing_quote_candidate_system_processed_v1"

MAIN_CATEGORIES = ["材料/做法", "拆除项", "新建/安装项", "设备/构件"]
ATTACHMENT_CATEGORIES = ["规格尺寸", "工程量/数量线索", "图名/标题", "轴号/索引/编号"]
NON_QUOTE_CATEGORIES = ["公司/人名/图签信息", "噪声", "不确定"]

DEFAULT_CABINET_JSON = Path(
    "outputs/biz2x_trial/ocr_cabinet/20260626_stage3_ocr_cabinet_deepseek_full2624/classified_ocr_cabinet.json"
)

ENUM_PREFIX_RE = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+[、.．]|[（(]?[一二三四五六七八九十\d]+[）)]?)\s*")
DIMENSION_PHRASE_RE = re.compile(
    r"(?i)(?:高度|宽度|厚度|长度|直径|半径)?\s*\d+(?:\.\d+)?\s*(?:x|X|\*|×)?\s*\d*(?:\.\d+)?\s*(?:mm|cm|m|米|毫米|公分|㎡|m²)?"
)
SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[\s,，。；;:：、/\\|_+=~^()[\]{}<>《》【】\"'`]+")
MATERIAL_CODE_RE = re.compile(r"(?i)\b(?:CT|ST|MT|PT|GL|WD|PB|AL|AP|AT|DN|DE|SC|JDG|BV|BYJ|YJV|MR)\s*-?\s*\d{1,4}\b")
QUANTITY_RE = re.compile(r"(?i)\d+(?:\.\d+)?\s*(?:m²|㎡|m2|m|米|个|套|樘|盏|只|处|项|kg|t)")


def build_quote_candidates(
    *,
    classifications: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    max_attachments_per_type: int = 8,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    rows = [_normalize_row(row) for row in classifications]
    by_id = {row["text_id"]: row for row in rows if row["text_id"]}
    main_rows = [row for row in rows if row["primary_category"] in MAIN_CATEGORIES and row["is_effective"]]
    attachment_rows = [row for row in rows if row["primary_category"] in ATTACHMENT_CATEGORIES and row["is_effective"]]

    groups = _group_main_rows(main_rows)
    candidates = [
        _candidate_from_group(index + 1, group, attachment_rows, by_id, max_attachments_per_type=max_attachments_per_type)
        for index, group in enumerate(groups)
    ]
    vlm_tasks = _build_vlm_tasks(candidates, rows)

    summary = _summary(rows, candidates, vlm_tasks)
    outputs = _write_outputs(directory, candidates, vlm_tasks, summary)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_text(),
        "summary": summary,
        "outputs": outputs,
        "quote_candidates": candidates,
        "vlm_review_tasks": vlm_tasks,
    }


def read_classified_cabinet_json(path: str | Path) -> list[dict[str, Any]]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = parsed.get("classifications") if isinstance(parsed, Mapping) else []
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def read_quote_candidates_json(path: str | Path) -> dict[str, Any]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        return {"quote_candidates": [], "vlm_review_tasks": [], "summary": {}}
    candidates = parsed.get("quote_candidates")
    tasks = parsed.get("vlm_review_tasks")
    return {
        "quote_candidates": [dict(row) for row in candidates if isinstance(row, Mapping)] if isinstance(candidates, list) else [],
        "vlm_review_tasks": [dict(row) for row in tasks if isinstance(row, Mapping)] if isinstance(tasks, list) else [],
        "summary": dict(parsed.get("summary") or {}) if isinstance(parsed.get("summary"), Mapping) else {},
    }


def apply_system_suggestions_to_candidates(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    processed: list[dict[str, Any]] = []
    decision_counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    for candidate in candidates:
        advice, decision, bucket, action = _system_processing_decision(candidate)
        item = dict(candidate)
        item["system_advice_cn"] = advice
        item["system_decision_cn"] = decision
        item["system_next_stage_bucket_cn"] = bucket
        item["system_action_cn"] = action
        item["manual_confirmation"] = decision
        item["review_status"] = decision
        processed.append(item)
        decision_counts[decision] += 1
        bucket_counts[bucket] += 1

    summary = {
        "schema_version": SYSTEM_PROCESSED_SCHEMA_VERSION,
        "candidate_count": len(processed),
        "confirm_effective_count": decision_counts.get("确认有效", 0),
        "pending_vlm_count": decision_counts.get("待VLM", 0),
        "hold_count": decision_counts.get("暂缓", 0),
        "system_decision_counts": dict(decision_counts),
        "next_stage_bucket_counts": dict(bucket_counts),
    }
    return {
        "schema_version": SYSTEM_PROCESSED_SCHEMA_VERSION,
        "generated_at": _now_text(),
        "summary": summary,
        "quote_candidates": processed,
    }


def write_system_processed_candidates(output_dir: str | Path, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = apply_system_suggestions_to_candidates(candidates)
    processed_json = directory / "quote_candidates_system_processed.json"
    processed_csv = directory / "quote_candidates_system_processed.csv"
    processed_md = directory / "quote_candidates_system_processed_review.md"
    summary_json = directory / "quote_candidates_system_processed_summary.json"

    _write_json(processed_json, report)
    write_system_processed_candidates_csv(processed_csv, report["quote_candidates"])
    processed_md.write_text(build_system_processed_markdown(report["quote_candidates"], report["summary"]), encoding="utf-8")
    _write_json(summary_json, report["summary"])
    return {
        "summary": report["summary"],
        "outputs": {
            "quote_candidates_system_processed_json": str(processed_json.resolve()),
            "quote_candidates_system_processed_csv": str(processed_csv.resolve()),
            "quote_candidates_system_processed_review_md": str(processed_md.resolve()),
            "quote_candidates_system_processed_summary_json": str(summary_json.resolve()),
        },
    }


def _group_main_rows(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    exact_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _candidate_key(row)
        if not key:
            key = row["text_id"]
        exact_groups[(row["primary_category"], key)].append(row)

    groups = list(exact_groups.values())
    groups.sort(key=lambda group: (_category_order(group[0]["primary_category"]), -_group_quality(group), _representative_text(group)))

    consumed: set[int] = set()
    merged_groups: list[list[dict[str, Any]]] = []
    for index, group in enumerate(groups):
        if index in consumed:
            continue
        base_category = group[0]["primary_category"]
        base_text = _representative_text(group)
        base_norm = _normalize_key_text(base_text)
        merged = list(group)
        consumed.add(index)
        for other_index, other in enumerate(groups):
            if other_index in consumed or other_index == index:
                continue
            if other[0]["primary_category"] != base_category:
                continue
            other_text = _representative_text(other)
            other_norm = _normalize_key_text(other_text)
            if _looks_like_truncated_duplicate(base_norm, other_norm):
                merged.extend(other)
                consumed.add(other_index)
        merged_groups.append(merged)

    merged_groups.sort(key=lambda group: (_category_order(group[0]["primary_category"]), -_group_quality(group), _representative_text(group)))
    return merged_groups


def _candidate_from_group(
    index: int,
    group: Sequence[dict[str, Any]],
    attachment_rows: Sequence[dict[str, Any]],
    by_id: Mapping[str, dict[str, Any]],
    *,
    max_attachments_per_type: int,
) -> dict[str, Any]:
    representative = _representative_row(group)
    candidate_id = f"QC{index:04d}"
    attachments = _attach_evidence(group, attachment_rows, by_id, max_attachments_per_type=max_attachments_per_type)
    spec_texts = _unique_texts(item["text"] for item in attachments if item["category"] == "规格尺寸")
    quantity_texts = _unique_texts(item["text"] for item in attachments if item["category"] == "工程量/数量线索")
    title_texts = _unique_texts(item["text"] for item in attachments if item["category"] == "图名/标题")
    code_texts = _unique_texts(item["text"] for item in attachments if item["category"] == "轴号/索引/编号")
    candidate_type = representative["primary_category"]
    item_name = _draft_item_name(candidate_type, representative["current_text"])
    feature_parts = _feature_parts(representative["current_text"], spec_texts, code_texts)

    source_needs_vlm = any(row["needs_vlm_review"] for row in group)
    attachment_needs_vlm = any(item["needs_vlm_review"] and item["relation_strength"] != "弱关联" for item in attachments)
    weak_spec_count = sum(1 for item in attachments if item["category"] == "规格尺寸" and item["relation_strength"] == "弱关联")
    needs_vlm = bool(source_needs_vlm or attachment_needs_vlm)

    confidence = min(1.0, max(0.0, sum(row["confidence"] for row in group) / max(len(group), 1)))
    if weak_spec_count:
        confidence = max(0.0, confidence - min(0.2, weak_spec_count * 0.03))

    evidence_ids = [row["text_id"] for row in group if row["text_id"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "candidate_key": _candidate_key(representative),
        "draft_item_name": item_name,
        "draft_item_feature": "；".join(feature_parts),
        "representative_text": representative["current_text"],
        "primary_evidence_ids": evidence_ids,
        "source_evidence_count": len(evidence_ids),
        "source_texts": _unique_texts(row["current_text"] for row in group),
        "related_spec_evidence_ids": [item["text_id"] for item in attachments if item["category"] == "规格尺寸"],
        "related_quantity_evidence_ids": [item["text_id"] for item in attachments if item["category"] == "工程量/数量线索"],
        "related_title_evidence_ids": [item["text_id"] for item in attachments if item["category"] == "图名/标题"],
        "related_code_evidence_ids": [item["text_id"] for item in attachments if item["category"] == "轴号/索引/编号"],
        "attached_specs": spec_texts,
        "attached_quantity_clues": quantity_texts,
        "attached_titles": title_texts,
        "attached_codes": code_texts,
        "attached_evidence": attachments,
        "image_files": _unique_texts(Path(row["image_path"]).name for row in group if row["image_path"]),
        "pages": sorted({row["page"] for row in group if row["page"] is not None}),
        "tile_ids": _unique_texts(row["tile_id"] for row in group if row["tile_id"]),
        "confidence": round(confidence, 4),
        "needs_vlm_review": needs_vlm,
        "vlm_review_reason": _candidate_vlm_reason(source_needs_vlm, attachment_needs_vlm, weak_spec_count),
        "merge_reason_cn": _merge_reason(group, attachments),
        "review_status": "待人工确认",
    }


def _attach_evidence(
    group: Sequence[dict[str, Any]],
    attachment_rows: Sequence[dict[str, Any]],
    by_id: Mapping[str, dict[str, Any]],
    *,
    max_attachments_per_type: int,
) -> list[dict[str, Any]]:
    candidate_ids = {row["text_id"] for row in group}
    related_ids = {
        text_id
        for row in group
        for text_id in row["related_text_ids"]
        if text_id and text_id not in candidate_ids
    }
    related_rows = [by_id[text_id] for text_id in related_ids if text_id in by_id and by_id[text_id]["primary_category"] in ATTACHMENT_CATEGORIES]
    all_attachment_rows = list(attachment_rows)
    scored: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for attachment in [*related_rows, *all_attachment_rows]:
        text_id = attachment["text_id"]
        if not text_id or text_id in seen_ids or text_id in candidate_ids:
            continue
        score, reason = _attachment_score(group, attachment, related_ids)
        if score <= 0:
            continue
        seen_ids.add(text_id)
        scored.append(
            {
                "text_id": text_id,
                "text": attachment["current_text"],
                "category": attachment["primary_category"],
                "score": score,
                "relation_strength": _relation_strength(score),
                "reason_cn": reason,
                "needs_vlm_review": attachment["needs_vlm_review"],
                "image_file": Path(attachment["image_path"]).name if attachment["image_path"] else "",
            }
        )

    scored.sort(key=lambda item: (-item["score"], _attachment_category_order(item["category"]), item["text_id"]))
    limited: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    for item in scored:
        category = item["category"]
        if category_counts[category] >= max_attachments_per_type:
            continue
        category_counts[category] += 1
        limited.append(item)
    return limited


def _attachment_score(group: Sequence[dict[str, Any]], attachment: dict[str, Any], related_ids: set[str]) -> tuple[float, str]:
    if attachment["text_id"] in related_ids and _allow_direct_attach_by_text(attachment["primary_category"], attachment["current_text"]):
        return 100.0, "LLM 关联证据ID直接指向"

    attachment_text = attachment["current_text"]
    category = attachment["primary_category"]
    nearby_texts = {text for row in group for text in row["nearby_texts"]}
    if attachment_text and attachment_text in nearby_texts and _allow_auto_attach_by_text(category, attachment_text):
        return 92.0, "出现在主候选周边文字中"

    if not _allow_auto_attach_by_text(category, attachment_text):
        return 0.0, ""

    best_score = 0.0
    best_reason = ""
    for row in group:
        if row["page"] is not None and row["page"] != attachment["page"]:
            continue
        if row["tile_id"] and row["tile_id"] == attachment["tile_id"]:
            score = 86.0
            reason = "同页同 tile"
        else:
            tile_distance = _tile_distance(row["tile_id"], attachment["tile_id"])
            if tile_distance is not None and tile_distance <= 1:
                score = 76.0
                reason = "同页相邻 tile"
            else:
                bbox_distance = _bbox_distance(row["bbox_ratio"], attachment["bbox_ratio"])
                if bbox_distance is not None and bbox_distance <= 0.03:
                    score = 68.0
                    reason = "页面坐标邻近"
                else:
                    continue
        score += _semantic_attachment_boost(row, attachment)
        if score > best_score:
            best_score = min(100.0, score)
            best_reason = reason
    if best_score < 60.0:
        return 0.0, ""
    return best_score, best_reason


def _semantic_attachment_boost(row: dict[str, Any], attachment: dict[str, Any]) -> float:
    text = f"{row['current_text']} {attachment['current_text']}"
    category = attachment["primary_category"]
    if category == "规格尺寸":
        if any(word in text for word in ["门", "窗", "地砖", "墙砖", "灯", "踢脚线", "隔墙", "玻璃"]):
            return 6.0
    if category == "工程量/数量线索" and QUANTITY_RE.search(attachment["current_text"]):
        return 5.0
    if category == "轴号/索引/编号" and MATERIAL_CODE_RE.search(attachment["current_text"]):
        return 8.0
    return 0.0


def _allow_auto_attach_by_text(category: str, text: str) -> bool:
    compact = _normalize_key_text(text)
    if not compact:
        return False
    if category == "规格尺寸":
        return _is_explicit_spec_text(text)
    if category == "轴号/索引/编号":
        return bool(MATERIAL_CODE_RE.search(text))
    return True


def _allow_direct_attach_by_text(category: str, text: str) -> bool:
    if category in {"规格尺寸", "轴号/索引/编号"}:
        return _allow_auto_attach_by_text(category, text)
    return True


def _is_explicit_spec_text(text: str) -> bool:
    compact = _normalize_key_text(text)
    if not compact:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", compact):
        return False
    if compact in {"X", "*", "×", "Φ", "φ"}:
        return False
    if re.search(r"(?i)\d+(?:\.\d+)?\s*(?:mm|cm|m²|㎡|m2|米|毫米|公分)", text):
        return True
    if re.search(r"\d+(?:\.\d+)?\s*(?:x|X|×|\*)\s*\d+(?:\.\d+)?", text):
        return True
    if re.search(r"(?:Φ|φ)\s*\d+(?:\.\d+)?", text):
        return True
    if re.search(r"(?:厚|宽|高|长|直径|半径|规格)[^0-9一二三四五六七八九十]*\d+", text):
        return True
    return False


def _build_vlm_tasks(candidates: Sequence[Mapping[str, Any]], rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    task_seen: set[tuple[str, str]] = set()
    candidate_by_text_id: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        for text_id in candidate.get("primary_evidence_ids") or []:
            candidate_by_text_id[str(text_id)] = candidate
        for item in candidate.get("attached_evidence") or []:
            candidate_by_text_id[str(item.get("text_id"))] = candidate

    for row in rows:
        if not row["needs_vlm_review"]:
            continue
        candidate = candidate_by_text_id.get(row["text_id"])
        priority, task_type = _vlm_priority(row, candidate)
        key = (row["text_id"], candidate.get("candidate_id") if candidate else "")
        if key in task_seen:
            continue
        task_seen.add(key)
        task_id = f"VLM{len(tasks) + 1:04d}"
        tasks.append(
            {
                "schema_version": SCHEMA_VERSION,
                "vlm_task_id": task_id,
                "priority": priority,
                "task_type": task_type,
                "question": _vlm_question(row, candidate, task_type),
                "candidate_id": candidate.get("candidate_id") if candidate else "",
                "candidate_text": candidate.get("representative_text") if candidate else "",
                "text_id": row["text_id"],
                "current_text": row["current_text"],
                "primary_category": row["primary_category"],
                "image_path": row["image_path"],
                "image_file": Path(row["image_path"]).name if row["image_path"] else "",
                "nearby_texts": row["nearby_texts"],
                "expected_output": "确认关联/否定关联/无法判断，并给出中文原因。",
            }
        )
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    tasks.sort(key=lambda item: (priority_order.get(item["priority"], 9), item["vlm_task_id"]))
    for index, task in enumerate(tasks, start=1):
        task["vlm_task_id"] = f"VLM{index:04d}"
    return tasks


def _vlm_priority(row: dict[str, Any], candidate: Mapping[str, Any] | None) -> tuple[str, str]:
    if candidate and row["primary_category"] in MAIN_CATEGORIES:
        return "P0", "主候选确认"
    if candidate and row["primary_category"] in {"规格尺寸", "工程量/数量线索"}:
        return "P0", "规格工程量归属确认"
    if candidate and row["primary_category"] == "轴号/索引/编号":
        return "P1", "材料代号或索引确认"
    if row["primary_category"] == "不确定" and _looks_quote_like(row["current_text"]):
        return "P1", "疑似报价证据确认"
    return "P2", "暂缓视觉复核"


def _vlm_question(row: dict[str, Any], candidate: Mapping[str, Any] | None, task_type: str) -> str:
    text = row["current_text"]
    if candidate:
        candidate_text = candidate.get("representative_text") or candidate.get("draft_item_name") or ""
        if task_type == "规格工程量归属确认":
            return f"请确认“{text}”是否属于候选“{candidate_text}”的规格、尺寸或工程量证据。"
        if task_type == "材料代号或索引确认":
            return f"请确认“{text}”是否是候选“{candidate_text}”相关的材料代号、图例编号或索引。"
        return f"请确认 OCR 文字“{text}”是否能支持候选“{candidate_text}”。"
    return f"请查看图纸确认“{text}”是否为报价相关证据，或只是轴号、尺寸、图签、符号碎片。"


def _write_outputs(
    directory: Path,
    candidates: Sequence[Mapping[str, Any]],
    vlm_tasks: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    candidates_json = directory / "quote_candidates.json"
    candidates_csv = directory / "quote_candidates.csv"
    review_md = directory / "quote_candidates_review.md"
    vlm_jsonl = directory / "vlm_review_tasks.jsonl"
    summary_json = directory / "stage4_quote_candidate_summary.json"

    _write_json(
        candidates_json,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_text(),
            "summary": summary,
            "quote_candidates": list(candidates),
            "vlm_review_tasks": list(vlm_tasks),
        },
    )
    write_quote_candidates_csv(candidates_csv, candidates)
    review_md.write_text(build_review_markdown(candidates, vlm_tasks, summary), encoding="utf-8")
    vlm_jsonl.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in vlm_tasks) + ("\n" if vlm_tasks else ""), encoding="utf-8")
    _write_json(summary_json, summary)
    return {
        "quote_candidates_json": str(candidates_json.resolve()),
        "quote_candidates_csv": str(candidates_csv.resolve()),
        "quote_candidates_review_md": str(review_md.resolve()),
        "vlm_review_tasks_jsonl": str(vlm_jsonl.resolve()),
        "stage4_quote_candidate_summary_json": str(summary_json.resolve()),
    }


def write_quote_candidates_csv(path: str | Path, candidates: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "candidate_id",
        "候选类型",
        "项目名称草案",
        "项目特征草案",
        "主证据ID",
        "主证据数量",
        "关联规格",
        "关联工程量线索",
        "关联图名",
        "关联编号/代号",
        "置信度",
        "是否需要VLM复核",
        "VLM复核原因",
        "归并原因",
        "截图文件",
        "人工确认",
    ]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "候选类型": candidate.get("candidate_type"),
                    "项目名称草案": candidate.get("draft_item_name"),
                    "项目特征草案": candidate.get("draft_item_feature"),
                    "主证据ID": "；".join(candidate.get("primary_evidence_ids") or []),
                    "主证据数量": candidate.get("source_evidence_count"),
                    "关联规格": "；".join(candidate.get("attached_specs") or []),
                    "关联工程量线索": "；".join(candidate.get("attached_quantity_clues") or []),
                    "关联图名": "；".join(candidate.get("attached_titles") or []),
                    "关联编号/代号": "；".join(candidate.get("attached_codes") or []),
                    "置信度": candidate.get("confidence"),
                    "是否需要VLM复核": "是" if candidate.get("needs_vlm_review") else "否",
                    "VLM复核原因": candidate.get("vlm_review_reason"),
                    "归并原因": candidate.get("merge_reason_cn"),
                    "截图文件": "；".join(candidate.get("image_files") or []),
                    "人工确认": candidate.get("manual_confirmation") or "",
                }
            )


def write_system_processed_candidates_csv(path: str | Path, candidates: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "candidate_id",
        "系统建议",
        "处理结果",
        "下一步归口",
        "处理动作",
        "候选类型",
        "项目名称草案",
        "项目特征草案",
        "主证据数量",
        "关联规格",
        "关联工程量线索",
        "关联图名",
        "关联编号/代号",
        "置信度",
        "是否需要VLM复核",
        "VLM复核原因",
        "主证据ID",
        "截图文件",
    ]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "系统建议": candidate.get("system_advice_cn"),
                    "处理结果": candidate.get("system_decision_cn"),
                    "下一步归口": candidate.get("system_next_stage_bucket_cn"),
                    "处理动作": candidate.get("system_action_cn"),
                    "候选类型": candidate.get("candidate_type"),
                    "项目名称草案": candidate.get("draft_item_name"),
                    "项目特征草案": candidate.get("draft_item_feature"),
                    "主证据数量": candidate.get("source_evidence_count"),
                    "关联规格": "；".join(candidate.get("attached_specs") or []),
                    "关联工程量线索": "；".join(candidate.get("attached_quantity_clues") or []),
                    "关联图名": "；".join(candidate.get("attached_titles") or []),
                    "关联编号/代号": "；".join(candidate.get("attached_codes") or []),
                    "置信度": candidate.get("confidence"),
                    "是否需要VLM复核": "是" if candidate.get("needs_vlm_review") else "否",
                    "VLM复核原因": candidate.get("vlm_review_reason"),
                    "主证据ID": "；".join(candidate.get("primary_evidence_ids") or []),
                    "截图文件": "；".join(candidate.get("image_files") or []),
                }
            )


def build_review_markdown(
    candidates: Sequence[Mapping[str, Any]],
    vlm_tasks: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> str:
    lines = [
        "# 阶段 4 报价候选归并与证据挂接报告",
        "",
        f"- 候选数：{summary['candidate_count']}",
        f"- 主证据数：{summary['main_evidence_count']}",
        f"- 需要 VLM 复核的候选：{summary['candidate_needs_vlm_count']}",
        f"- VLM 任务数：{summary['vlm_task_count']}（P0={summary['vlm_priority_counts'].get('P0', 0)}，P1={summary['vlm_priority_counts'].get('P1', 0)}，P2={summary['vlm_priority_counts'].get('P2', 0)}）",
        "",
        "## 候选类型统计",
        "",
        "| 候选类型 | 数量 |",
        "|---|---:|",
    ]
    for category in MAIN_CATEGORIES:
        lines.append(f"| {category} | {summary['candidate_type_counts'].get(category, 0)} |")

    lines.extend(["", "## 前 30 个候选", "", "| ID | 类型 | 项目名称草案 | 关联规格 | 需要VLM |", "|---|---|---|---|---|"])
    for candidate in list(candidates)[:30]:
        lines.append(
            "| {id} | {typ} | {name} | {spec} | {vlm} |".format(
                id=candidate.get("candidate_id", ""),
                typ=candidate.get("candidate_type", ""),
                name=_md(candidate.get("draft_item_name")),
                spec=_md("；".join(candidate.get("attached_specs") or [])[:120]),
                vlm="是" if candidate.get("needs_vlm_review") else "否",
            )
        )

    lines.extend(["", "## P0/P1 VLM 任务预览", "", "| 任务ID | 优先级 | 类型 | 问题 |", "|---|---|---|---|"])
    for task in [item for item in vlm_tasks if item.get("priority") in {"P0", "P1"}][:40]:
        lines.append(
            f"| {task.get('vlm_task_id')} | {task.get('priority')} | {task.get('task_type')} | {_md(task.get('question'))} |"
        )
    return "\n".join(lines) + "\n"


def build_system_processed_markdown(candidates: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> str:
    bucket_counts = summary.get("next_stage_bucket_counts") or {}
    decision_counts = summary.get("system_decision_counts") or {}
    lines = [
        "# 阶段 4 报价候选系统建议处理结果",
        "",
        f"- 候选总数：{summary.get('candidate_count', 0)}",
        f"- 确认有效：{summary.get('confirm_effective_count', 0)}",
        f"- 待 VLM/人工确认：{summary.get('pending_vlm_count', 0)}",
        f"- 暂缓补规格/工程量：{summary.get('hold_count', 0)}",
        "",
        "## 处理结果统计",
        "",
        "| 处理结果 | 数量 |",
        "|---|---:|",
    ]
    for decision, count in decision_counts.items():
        lines.append(f"| {decision} | {count} |")
    lines.extend(["", "## 下一步归口统计", "", "| 下一步归口 | 数量 |", "|---|---:|"])
    for bucket, count in bucket_counts.items():
        lines.append(f"| {bucket} | {count} |")
    lines.extend(["", "## 前 40 个确认有效候选", "", "| ID | 类型 | 项目名称草案 | 下一步归口 |", "|---|---|---|---|"])
    for candidate in [item for item in candidates if item.get("system_decision_cn") == "确认有效"][:40]:
        lines.append(
            "| {id} | {typ} | {name} | {bucket} |".format(
                id=candidate.get("candidate_id", ""),
                typ=candidate.get("candidate_type", ""),
                name=_md(candidate.get("draft_item_name")),
                bucket=_md(candidate.get("system_next_stage_bucket_cn")),
            )
        )
    return "\n".join(lines) + "\n"


def _summary(
    rows: Sequence[dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    vlm_tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_type_counts = Counter(candidate.get("candidate_type") for candidate in candidates)
    vlm_priority_counts = Counter(task.get("priority") for task in vlm_tasks)
    return {
        "schema_version": SCHEMA_VERSION,
        "input_classification_count": len(rows),
        "main_evidence_count": sum(1 for row in rows if row["primary_category"] in MAIN_CATEGORIES and row["is_effective"]),
        "attachment_evidence_count": sum(1 for row in rows if row["primary_category"] in ATTACHMENT_CATEGORIES and row["is_effective"]),
        "candidate_count": len(candidates),
        "candidate_type_counts": {category: candidate_type_counts.get(category, 0) for category in MAIN_CATEGORIES},
        "candidate_needs_vlm_count": sum(1 for candidate in candidates if candidate.get("needs_vlm_review")),
        "candidate_with_spec_count": sum(1 for candidate in candidates if candidate.get("attached_specs")),
        "candidate_with_quantity_count": sum(1 for candidate in candidates if candidate.get("attached_quantity_clues")),
        "vlm_task_count": len(vlm_tasks),
        "vlm_priority_counts": dict(vlm_priority_counts),
    }


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text_id": _text(row.get("text_id")),
        "current_text": _text(row.get("current_text")),
        "primary_category": _text(row.get("primary_category")),
        "secondary_category": _text(row.get("secondary_category")),
        "is_effective": bool(row.get("is_effective")),
        "confidence": _float(row.get("confidence")),
        "reason": _text(row.get("reason")),
        "related_text_ids": _string_list(row.get("related_text_ids")),
        "needs_vlm_review": bool(row.get("needs_vlm_review")),
        "vlm_review_reason": _text(row.get("vlm_review_reason")),
        "noise_reason": _text(row.get("noise_reason")),
        "suggested_usage": _string_list(row.get("suggested_usage")),
        "nearby_texts": _string_list(row.get("nearby_texts")),
        "page": row.get("page"),
        "tile_id": _text(row.get("tile_id")),
        "image_path": _text(row.get("image_path")),
        "bbox_ratio": _number_list(row.get("bbox_ratio")),
    }


def _candidate_key(row: Mapping[str, Any]) -> str:
    text = _clean_candidate_text(_text(row.get("current_text")))
    return _normalize_key_text(text)


def _clean_candidate_text(text: str) -> str:
    cleaned = ENUM_PREFIX_RE.sub("", text).strip()
    if "拆除" in cleaned:
        return cleaned
    # Keep dimensions inside demolition/new-build texts, but trim pure trailing dimension fragments from material names.
    if len(cleaned) > 12:
        cleaned = re.sub(r"[，,、;；]?\s*(?:高度|宽度|厚度|长度)\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|米)?", "", cleaned)
    return cleaned.strip()


def _draft_item_name(category: str, text: str) -> str:
    cleaned = _clean_candidate_text(text)
    if category == "拆除项":
        if "拆除" in cleaned:
            return cleaned.split("，")[0].split(",")[0].strip()
        return f"拆除{cleaned}".strip()
    if category == "新建/安装项":
        return cleaned
    if category == "设备/构件":
        return cleaned
    return cleaned


def _feature_parts(text: str, specs: Sequence[str], codes: Sequence[str]) -> list[str]:
    parts = []
    cleaned = _clean_candidate_text(text)
    if cleaned:
        parts.append(cleaned)
    for spec in specs[:4]:
        if spec and spec not in parts:
            parts.append(spec)
    for code in codes[:3]:
        if code and code not in parts and MATERIAL_CODE_RE.search(code):
            parts.append(code)
    return parts


def _representative_row(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return sorted(group, key=lambda row: (-len(row["current_text"]), -row["confidence"], row["text_id"]))[0]


def _representative_text(group: Sequence[dict[str, Any]]) -> str:
    return _representative_row(group)["current_text"]


def _group_quality(group: Sequence[dict[str, Any]]) -> float:
    return len(group) * 0.1 + max((row["confidence"] for row in group), default=0.0) + len(_representative_text(group)) * 0.001


def _looks_like_truncated_duplicate(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 5 or len(long) < 8:
        return False
    if short in long and len(short) / len(long) >= 0.45:
        return True
    return False


def _candidate_vlm_reason(source_needs_vlm: bool, attachment_needs_vlm: bool, weak_spec_count: int) -> str:
    reasons = []
    if source_needs_vlm:
        reasons.append("主候选自身存在需要视觉确认的证据")
    if attachment_needs_vlm:
        reasons.append("挂接证据需要视觉确认")
    if weak_spec_count:
        reasons.append(f"存在 {weak_spec_count} 条弱关联规格，暂不直接写死归属")
    return "；".join(reasons)


def _system_processing_decision(candidate: Mapping[str, Any]) -> tuple[str, str, str, str]:
    candidate_type = _text(candidate.get("candidate_type"))
    has_supporting_measure = bool(candidate.get("attached_specs") or candidate.get("attached_quantity_clues"))
    if candidate.get("needs_vlm_review"):
        return (
            "先确认挂接证据",
            "待VLM",
            "VLM/人工确认",
            "暂不进入自动归并，先确认规格、工程量或代号是否挂错。",
        )
    if not has_supporting_measure:
        return (
            "可先保留项目名，后续补规格/工程量",
            "暂缓",
            "暂缓补证据",
            "保留候选项目名，不删除；等待后续补充规格、工程量或人工确认后再归并。",
        )
    if candidate_type == "拆除项":
        return ("可进入拆除项归并", "确认有效", "拆除项归并", "进入拆除项候选归并。")
    if candidate_type == "新建/安装项":
        return ("可进入新建/安装项归并", "确认有效", "新建/安装项归并", "进入新建/安装项候选归并。")
    if candidate_type == "设备/构件":
        return ("可进入构件/设备归并", "确认有效", "构件/设备归并", "进入构件/设备候选归并。")
    return ("可进入材料/做法归并", "确认有效", "材料/做法归并", "进入材料/做法候选归并。")


def _merge_reason(group: Sequence[dict[str, Any]], attachments: Sequence[Mapping[str, Any]]) -> str:
    if len(group) == 1:
        base = "单条主证据形成候选"
    else:
        base = f"{len(group)} 条同类/近似 OCR 主证据归并为一个候选"
    if attachments:
        return f"{base}；已挂接 {len(attachments)} 条规格/工程量/图名/编号证据"
    return f"{base}；暂未挂接规格或工程量证据"


def _relation_strength(score: float) -> str:
    if score >= 90:
        return "强关联"
    if score >= 75:
        return "中关联"
    return "弱关联"


def _tile_distance(left: str, right: str) -> int | None:
    l = _tile_rc(left)
    r = _tile_rc(right)
    if l is None or r is None:
        return None
    return max(abs(l[0] - r[0]), abs(l[1] - r[1]))


def _tile_rc(tile_id: str) -> tuple[int, int] | None:
    match = re.search(r"_r(\d+)_c(\d+)", tile_id or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _bbox_distance(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 4 or len(right) < 4:
        return None
    lx = (left[0] + left[2]) / 2
    ly = (left[1] + left[3]) / 2
    rx = (right[0] + right[2]) / 2
    ry = (right[1] + right[3]) / 2
    return math.hypot(lx - rx, ly - ry)


def _looks_quote_like(text: str) -> bool:
    return any(keyword in text for keyword in ["拆", "除", "新建", "安装", "地砖", "墙砖", "涂料", "门", "灯", "玻璃", "踢脚线", "石膏板", "龙骨"])


def _category_order(category: str) -> int:
    try:
        return MAIN_CATEGORIES.index(category)
    except ValueError:
        return 99


def _attachment_category_order(category: str) -> int:
    try:
        return ATTACHMENT_CATEGORIES.index(category)
    except ValueError:
        return 99


def _normalize_key_text(text: str) -> str:
    return PUNCT_RE.sub("", SPACE_RE.sub("", text)).upper()


def _unique_texts(values: Sequence[Any] | Any) -> list[str]:
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


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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


def _number_list(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [_float(item) for item in value]
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_float(item) for item in parsed]


def _md(value: Any) -> str:
    return _text(value).replace("|", "｜").replace("\n", " ") or "-"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
