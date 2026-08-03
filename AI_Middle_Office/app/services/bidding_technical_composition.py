from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bidding import BidMaterialRequirement, BidMaterialRequirementEvent, BidParseRun, BidProject, BidProjectFile
from app.models.enterprise_profile import ENTERPRISE_PROFILE_CATEGORY_VALUES, EnterpriseProfileItem
from app.models.user import User
from app.services.bidding_llm_review import bidding_llm_model
from app.services.bidding_parser import dumps_json, loads_json
from app.services.enterprise_profile import list_active_profile_candidates, serialize_item
from app.services.model_gateway import post_json_via_gateway


BID_TECHNICAL_COMPOSITION_VERSION = "biz4c_technical_composition_v1.0"
BID_TECHNICAL_COMPOSITION_MIN_PROFILE_SCORE = 0.30
BID_TECHNICAL_COMPOSITION_CATEGORY_OVERRIDE_SCORE = 0.42


class BidTechnicalCompositionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def get_bid_technical_composition_plan(db: Session, project: BidProject, run: BidParseRun) -> dict[str, Any]:
    summary = loads_json(run.summary_json, {}) or {}
    cached = summary.get("technical_composition_plan")
    if isinstance(cached, dict):
        return _with_current_requirement_rows(db, run, cached)
    return {
        "version": BID_TECHNICAL_COMPOSITION_VERSION,
        "status": "not_generated",
        "run_uuid": run.run_uuid,
        "project_uuid": project.project_uuid,
        "components": [],
        "summary": {
            "component_count": 0,
            "enterprise_profile_need_count": 0,
            "enterprise_profile_material_item_count": 0,
            "tender_document_need_count": 0,
            "manual_requirement_count": 0,
            "auto_matched_profile_count": 0,
        },
        "requirements": [],
        "warnings": [{"code": "not_generated", "message": "尚未生成技术标投标文件组成识别结果。"}],
    }


async def generate_bid_technical_composition_plan(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    *,
    user: User,
) -> dict[str, Any]:
    files = _files_for_run(db, project, run)
    if not files:
        raise BidTechnicalCompositionError("NO_PARSED_BID_FILES")
    document_context = _build_document_context(files)
    if not document_context["segments"]:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_EMPTY_TEXT")

    raw_payload: dict[str, Any] = {}
    primary_error: Exception | None = None
    try:
        raw_payload = await _call_technical_composition_llm(project, run, document_context, username=user.username)
    except Exception as exc:
        if isinstance(exc, BidTechnicalCompositionError) and exc.code in {
            "BID_TECHNICAL_COMPOSITION_LLM_PROVIDER_NOT_SUPPORTED",
            "BID_TECHNICAL_COMPOSITION_LLM_NOT_CONFIGURED",
        }:
            raise
        primary_error = exc

    plan = (
        _normalize_llm_payload(raw_payload, project=project, run=run, document_context=document_context, repair_missing=False)
        if not primary_error
        else _empty_technical_composition_plan(project, run, document_context)
    )
    if primary_error or _should_retry_source_item_classification(plan):
        try:
            retry_payload = await _call_technical_composition_source_item_llm(
                project,
                run,
                document_context,
                raw_payload=raw_payload,
                username=user.username,
            )
            plan = _normalize_llm_payload(retry_payload, project=project, run=run, document_context=document_context)
            retry_reason = (
                f"首次组成识别外部调用失败，已基于原文组成项清单重新调用 LLM 分类：{_technical_composition_error_detail(primary_error)}"
                if primary_error
                else "首次组成识别未返回可用组成项，已基于原文组成项清单重新调用 LLM 分类。"
            )
            plan["warnings"] = _normalize_warnings([*plan.get("warnings", []), {"code": "source_item_llm_retry", "message": retry_reason}])
        except Exception as exc:
            if primary_error:
                raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_TRANSIENT_FAILURE") from exc
            plan = _normalize_llm_payload(raw_payload, project=project, run=run, document_context=document_context)
            plan["warnings"] = _normalize_warnings(
                [
                    *plan.get("warnings", []),
                    {
                        "code": "source_item_llm_retry_failed",
                        "message": f"原文组成项二次 LLM 分类失败，已保留人工复核兜底：{getattr(exc, 'code', None) or str(exc)[:120]}",
                    },
                ]
            )
    else:
        plan = _normalize_llm_payload(raw_payload, project=project, run=run, document_context=document_context)
    requirement_result = _sync_material_requirements_from_plan(db, project, run, plan, user=user)
    plan["requirement_sync"] = requirement_result
    plan["requirements"] = requirement_result["rows"]
    plan["summary"] = _build_plan_summary(plan)
    plan["generated_at"] = datetime.now(timezone.utc).isoformat()

    run_summary = loads_json(run.summary_json, {}) or {}
    run_summary["technical_composition_plan"] = plan
    run.summary_json = dumps_json(run_summary)
    return plan


def _files_for_run(db: Session, project: BidProject, run: BidParseRun) -> list[BidProjectFile]:
    input_file_uuids = loads_json(run.input_file_ids_json, []) or []
    query = db.query(BidProjectFile).filter(BidProjectFile.project_id == project.id, BidProjectFile.parser_status == "parsed")
    if input_file_uuids:
        query = query.filter(BidProjectFile.file_uuid.in_(input_file_uuids))
    files = query.order_by(BidProjectFile.id.asc()).all()
    if files:
        return files
    return (
        db.query(BidProjectFile)
        .filter(BidProjectFile.project_id == project.id, BidProjectFile.parser_status == "parsed")
        .order_by(BidProjectFile.id.asc())
        .all()
    )


def _build_document_context(files: list[BidProjectFile]) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    composition_scan_segments: list[dict[str, Any]] = []
    for file_obj in files:
        file_segments = loads_json(file_obj.segments_json, []) or []
        if file_segments:
            for index, segment in enumerate(file_segments):
                text = _clean_text(segment.get("text") or segment.get("content") or segment.get("original_text"))
                if not text:
                    continue
                segments.append(
                    {
                        "segment_id": f"S{len(segments) + 1}",
                        "source_file": segment.get("source_file") or file_obj.original_filename,
                        "source_location": segment.get("source_location") or segment.get("location") or f"片段{index + 1}",
                        "text": text,
                    }
                )
            if file_obj.extracted_text:
                composition_scan_segments.append(
                    {
                        "segment_id": f"F{file_obj.id}:full_text",
                        "source_file": file_obj.original_filename,
                        "source_location": "全文",
                        "text": file_obj.extracted_text,
                    }
                )
        elif file_obj.extracted_text:
            for index, text in enumerate(_split_long_text(file_obj.extracted_text)):
                segments.append(
                    {
                        "segment_id": f"S{len(segments) + 1}",
                        "source_file": file_obj.original_filename,
                        "source_location": f"文本片段{index + 1}",
                        "text": text,
                    }
                )
            composition_scan_segments.append(
                {
                    "segment_id": f"F{file_obj.id}:full_text",
                    "source_file": file_obj.original_filename,
                    "source_location": "全文",
                    "text": file_obj.extracted_text,
                }
            )
    composition_sections = _extract_technical_composition_sections([*segments, *composition_scan_segments])
    selected = _select_llm_context_segments(segments, composition_sections=composition_sections)
    return {
        "files": [{"file_uuid": item.file_uuid, "filename": item.original_filename, "file_type": item.file_type} for item in files],
        "segments": selected,
        "composition_sections": composition_sections,
        "composition_source_item_count": len(_source_items_from_sections(composition_sections)),
        "source_segment_count": len(segments),
    }


def _split_long_text(text: str, *, chunk_size: int = 1800) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]
    chunks = []
    for start in range(0, len(cleaned), chunk_size):
        chunks.append(cleaned[start : start + chunk_size])
    return chunks[:120]


def _select_llm_context_segments(
    segments: list[dict[str, Any]],
    *,
    composition_sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    section_segment_ids = {
        segment_id
        for section in composition_sections or []
        for segment_id in (section.get("segment_ids") or [])
    }
    anchors = ("投标文件", "文件组成", "组成", "技术标", "施工组织设计", "投标格式", "格式")
    for index, segment in enumerate(segments):
        text = segment.get("text") or ""
        score = sum(1 for anchor in anchors if anchor in text)
        if segment.get("segment_id") in section_segment_ids:
            score += 12
        if "投标文件的组成" in text or "投标文件组成" in text:
            score += 5
        scored.append((score, index, segment))
    selected = [item for score, _, item in sorted(scored, key=lambda item: (-item[0], item[1]))[:36] if score > 0]
    if len(selected) < 8:
        seen = {id(item) for item in selected}
        for _, _, segment in scored[: 8 - len(selected)]:
            if id(segment) not in seen:
                selected.append(segment)
    return [{**item, "text": _clip(item.get("text"), 2200)} for item in selected]


def _extract_technical_composition_sections(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    heading_pattern = re.compile(r"(?P<section_no>\d+(?:\.\d+)*)\s*[^\n]{0,50}技术标[^\n]{0,50}要求")
    for index, segment in enumerate(segments):
        text = segment.get("text") or ""
        match = heading_pattern.search(text)
        if not match:
            continue
        section_no = match.group("section_no")
        collected_parts: list[str] = []
        segment_ids: list[str] = []
        stopped = False
        for next_segment in segments[index : index + 12]:
            part = next_segment.get("text") or ""
            if next_segment is segment:
                part = part[match.start() :]
            stop_at = _technical_section_stop_index(part, section_no)
            if stop_at is not None:
                part = part[:stop_at]
                stopped = True
            if part.strip():
                collected_parts.append(part)
                if next_segment.get("segment_id"):
                    segment_ids.append(next_segment["segment_id"])
            if stopped:
                break
        section_text = "\n".join(collected_parts).strip()
        raw_items = _extract_numbered_source_items(section_text)
        if not raw_items:
            continue
        first_item = raw_items[0]
        sections.append(
            {
                "section_no": section_no,
                "section_title": _clean_text(section_text.splitlines()[0] if section_text else f"{section_no} 技术标部分要求", 180),
                "source_file": segment.get("source_file") or "招标文件",
                "source_location": segment.get("source_location") or "-",
                "segment_ids": segment_ids,
                "raw_item_count": len(raw_items),
                "raw_items": raw_items,
                "source_text": _clip(section_text, 9000),
                "first_item_no": first_item.get("item_no"),
            }
        )
    best_by_section: dict[str, dict[str, Any]] = {}
    for section in sections:
        key = section.get("section_no") or section.get("section_title") or str(len(best_by_section))
        previous = best_by_section.get(key)
        if not previous or int(section.get("raw_item_count") or 0) > int(previous.get("raw_item_count") or 0):
            best_by_section[key] = section
    return sorted(best_by_section.values(), key=lambda item: (str(item.get("section_no") or ""), -int(item.get("raw_item_count") or 0)))[:3]


def _technical_section_stop_index(text: str, section_no: str) -> int | None:
    parts = [int(part) for part in section_no.split(".") if part.isdigit()]
    stop_numbers: list[str] = []
    if len(parts) >= 2:
        sibling = ".".join([*(str(part) for part in parts[:-1]), str(parts[-1] + 1)])
        stop_numbers.append(re.escape(sibling))
    if parts:
        stop_numbers.append(re.escape(f"{parts[0] + 1}."))
    if not stop_numbers:
        return None
    pattern = re.compile(rf"(?m)^\s*(?:{'|'.join(stop_numbers)})(?:\s+|[：:])")
    match = pattern.search(text)
    return match.start() if match else None


def _extract_numbered_source_items(section_text: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"(?s)(?P<item_no>\d+\.\d+\.\d+)\s+(?P<body>.*?)(?=\s+\d+\.\d+\.\d+\s+|\s+\d+\.\d+\s+|\s+\d+\.\s+|\Z)"
    )
    items: list[dict[str, Any]] = []
    for index, match in enumerate(pattern.finditer(section_text)):
        body = _clean_source_item_body(match.group("body"))
        if not body:
            continue
        item_no = match.group("item_no")
        items.append(
            {
                "item_no": item_no,
                "order_index": index + 1,
                "item_title": body[:180],
                "original_text": f"{item_no} {body}",
            }
        )
    return items


def _clean_source_item_body(value: str) -> str:
    lines = [line.strip() for line in str(value or "").splitlines()]
    text = " ".join(line for line in lines if line)
    text = re.sub(r"\s+", " ", text).strip(" ；;")
    return text


async def _call_technical_composition_llm(
    project: BidProject,
    run: BidParseRun,
    document_context: dict[str, Any],
    *,
    username: str | None,
) -> dict[str, Any]:
    provider = (settings.bidding_llm_provider or "deepseek").strip().lower()
    if provider != "deepseek":
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_PROVIDER_NOT_SUPPORTED")
    if not (settings.deepseek_api_key or "").strip():
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_NOT_CONFIGURED")
    model = bidding_llm_model()
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是装饰工程投标文件组成分析助手。你的任务是从招标文件证据中识别技术标投标文件组成，"
                    "并判断每个组成项的信息来源。不要根据固定关键词模板推断，不要编造招标文件中没有的组成项。"
                    "对于固定企业资料，给出用于企业资料库检索的名称；对于项目相关内容，从招标文件证据中抽取并润色为可写入投标文件的表达。"
                    "输出必须是严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "project": {
                            "project_uuid": project.project_uuid,
                            "project_name": project.project_name,
                            "tenderer_name": project.tenderer_name,
                            "project_location": project.project_location,
                            "project_type": project.project_type,
                        },
                        "task": [
                            "先定位招标文件中的“投标文件组成/技术标组成/投标文件格式/施工组织设计”等证据。",
                            "如果 document_context.composition_sections 非空，必须以其中 raw_items 为唯一基准逐项输出，不得抽样、合并、改写成大类或遗漏。",
                            "每个 raw_item 必须对应一个 components 项或 excluded_components 项；components.source_item_no 必须填写原 item_no，例如 7.3.1。",
                            "只输出技术标链路需要处理的组成项；商务标、报价表、纯商务函件不放入技术标，除非证据明确要求放在技术标分册。",
                            "无法判断资料来源的 raw_item 也必须输出为 components，source_type 填 manual_input，并说明需要人工复核，不得省略。",
                            "把组成项拆成 information_needs：enterprise_profile 表示靠固定企业资料库；tender_document 表示需要从本招标文件抽取并润色；manual_input 表示既非企业资料也无法从招标文件稳定抽取，需要人工补充。",
                            "enterprise_profile 的 query 必须是最适合企业资料库检索的资料名称。",
                            "tender_document 的 polished_text 应写成可直接放入技术标草案的正式表述，但必须来源于给定证据。",
                            "输出 coverage_check，写明 source_item_count、component_count、excluded_count、missing_source_item_nos。",
                        ],
                        "allowed_profile_categories": sorted(ENTERPRISE_PROFILE_CATEGORY_VALUES),
                        "output_schema": {
                            "components": [
                                {
                                    "component_key": "稳定英文或拼音短键",
                                    "component_title": "组成项名称",
                                    "source_item_no": "对应 raw_items.item_no，例如 7.3.1",
                                    "package_key": "technical",
                                    "order_index": 1,
                                    "classification": "fixed_enterprise_material | tender_extracted_content | mixed | manual_input",
                                    "classification_reason": "来源判断理由",
                                    "source_evidence": [
                                        {"source_file": "文件名", "source_location": "页码/章节/片段", "original_text": "原文短摘"}
                                    ],
                                    "information_needs": [
                                        {
                                            "need_key": "稳定短键",
                                            "need_title": "所需资料或信息",
                                            "source_type": "enterprise_profile | tender_document | manual_input",
                                            "profile_category": "certificate/personnel/project_performance/technical_solution/other 等，可为空",
                                            "query": "企业资料库检索名或招标文件抽取查询",
                                            "reason": "为什么需要该来源",
                                            "polished_text": "source_type=tender_document 时填写，其他可为空",
                                            "source_evidence": [
                                                {"source_file": "文件名", "source_location": "页码/章节/片段", "original_text": "原文短摘"}
                                            ],
                                        }
                                    ],
                                    "draft_instruction": "后续生成技术标草案时如何使用这些信息",
                                }
                            ],
                            "excluded_components": [
                                {
                                    "source_item_no": "被排除的 raw_items.item_no",
                                    "component_title": "被排除项名称",
                                    "reason": "为什么不属于技术标草案链路",
                                }
                            ],
                            "coverage_check": {
                                "source_item_count": "raw_items 总数",
                                "component_count": "components 数量",
                                "excluded_count": "excluded_components 数量",
                                "missing_source_item_nos": ["未覆盖的 item_no"],
                            },
                            "warnings": [{"code": "可选", "message": "可选"}],
                        },
                        "document_context": document_context,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="bidding_technical_composition",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"},
        timeout=settings.bidding_llm_timeout_seconds,
        username=username,
        trace_id=f"bid-tech-composition:{run.run_uuid}",
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_FAILED")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_BAD_RESPONSE") from exc
    return _extract_json_object(content)


async def _call_technical_composition_source_item_llm(
    project: BidProject,
    run: BidParseRun,
    document_context: dict[str, Any],
    *,
    raw_payload: dict[str, Any],
    username: str | None,
) -> dict[str, Any]:
    provider = (settings.bidding_llm_provider or "deepseek").strip().lower()
    if provider != "deepseek":
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_PROVIDER_NOT_SUPPORTED")
    if not (settings.deepseek_api_key or "").strip():
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_NOT_CONFIGURED")
    source_items = _source_items_from_sections(document_context.get("composition_sections") or [])
    if not source_items:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_NO_SOURCE_ITEMS")
    model = bidding_llm_model()
    payload = {
        "model": model,
        "temperature": 0.05,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是技术标投标文件组成分类助手。现在只处理用户提供的 source_items 原文清单。"
                    "必须为每一个 source_item 输出一个 components 项，不得省略、合并或改写条款号。"
                    "你只能根据原文含义判断信息来源：固定企业资料、招标文件抽取、混合、人工补充。"
                    "输出必须是严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "project": {
                            "project_uuid": project.project_uuid,
                            "project_name": project.project_name,
                            "tenderer_name": project.tenderer_name,
                            "project_location": project.project_location,
                            "project_type": project.project_type,
                        },
                        "source_items": source_items,
                        "failed_primary_payload_keys": sorted(raw_payload.keys()) if isinstance(raw_payload, dict) else [],
                        "allowed_profile_categories": sorted(ENTERPRISE_PROFILE_CATEGORY_VALUES),
                        "classification_values": [
                            "fixed_enterprise_material",
                            "tender_extracted_content",
                            "mixed",
                            "manual_input",
                        ],
                        "source_type_values": ["enterprise_profile", "tender_document", "manual_input"],
                        "requirements": [
                            "每个 source_items[i] 必须对应 components[i] 或同一 source_item_no 的 components 项。",
                            "固定证照、授权书、人员证书、企业业绩、管理人员简历等已存在或应沉淀的固定材料，source_type 使用 enterprise_profile。",
                            "质量工期承诺、进度计划、施工组织设计、安全文明、材料采购、项目重难点等需要结合本项目招标文件生成正文的内容，source_type 使用 tender_document 或 mixed。",
                            "只有原文无法判断、且既不是固定企业资料也无法从招标文件稳定抽取时，才使用 manual_input。",
                            "enterprise_profile 的 query 必须写成最适合企业资料库检索的资料名称。",
                            "tender_document 的 polished_text 必须写成可进入技术标草案的正式表达，并依据 source_items 原文。",
                        ],
                        "output_schema": {
                            "components": [
                                {
                                    "component_key": "stable_short_key",
                                    "component_title": "source item title",
                                    "source_item_no": "7.3.x",
                                    "package_key": "technical",
                                    "order_index": 1,
                                    "classification": "fixed_enterprise_material|tender_extracted_content|mixed|manual_input",
                                    "classification_reason": "why",
                                    "source_evidence": [
                                        {"source_file": "file", "source_location": "location", "original_text": "quote"}
                                    ],
                                    "information_needs": [
                                        {
                                            "need_key": "stable_need_key",
                                            "need_title": "material or information title",
                                            "source_type": "enterprise_profile|tender_document|manual_input",
                                            "profile_category": "certificate/personnel/project_performance/technical_solution/basic_info/other/null",
                                            "query": "search query",
                                            "reason": "why",
                                            "polished_text": "formal tender text when source_type=tender_document",
                                            "source_evidence": [
                                                {"source_file": "file", "source_location": "location", "original_text": "quote"}
                                            ],
                                        }
                                    ],
                                    "draft_instruction": "how to use this item in draft generation",
                                }
                            ],
                            "coverage_check": {
                                "source_item_count": len(source_items),
                                "component_count": len(source_items),
                                "excluded_count": 0,
                                "missing_source_item_nos": [],
                            },
                            "warnings": [],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    response = await post_json_via_gateway(
        provider="deepseek",
        model=model,
        endpoint_type="bidding_technical_composition_source_item_retry",
        url=settings.deepseek_chat_url,
        json_payload=payload,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key.strip()}"},
        timeout=settings.bidding_llm_timeout_seconds,
        username=username,
        trace_id=f"bid-tech-composition-retry:{run.run_uuid}",
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_FAILED")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_BAD_RESPONSE") from exc
    return _extract_json_object(content)


def _normalize_llm_payload(
    payload: dict[str, Any],
    *,
    project: BidProject,
    run: BidParseRun,
    document_context: dict[str, Any],
    repair_missing: bool = True,
) -> dict[str, Any]:
    raw_components = _payload_component_items(payload)
    components: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_components if isinstance(raw_components, list) else []):
        if not isinstance(raw, dict):
            continue
        title = _clean_text(_first_present(raw, "component_title", "title", "item_title", "name", "组成项", "组成项名称", "标题"), 160)
        if not title:
            continue
        component_key = _stable_key(_first_present(raw, "component_key", "key", "item_key") or title, prefix="component", index=index)
        needs = _normalize_needs(_first_present(raw, "information_needs", "needs", "requirements", "资料需求", "信息需求"), component_key=component_key)
        evidence = _normalize_evidence(_first_present(raw, "source_evidence", "evidence", "依据", "证据"))
        components.append(
            {
                "component_key": component_key,
                "component_title": title,
                "source_item_no": _clean_text(_first_present(raw, "source_item_no", "item_no", "clause_no", "条款号", "原文条款号"), 40),
                "package_key": "technical",
                "order_index": _safe_int(_first_present(raw, "order_index", "order", "index", "序号"), index + 1),
                "classification": _normalize_classification(_first_present(raw, "classification", "category", "来源分类", "分类")),
                "classification_reason": _clip(_first_present(raw, "classification_reason", "reason", "分类理由"), 500),
                "source_evidence": evidence,
                "information_needs": needs,
                "draft_instruction": _clip(_first_present(raw, "draft_instruction", "instruction", "生成说明", "草案说明"), 600),
            }
        )
    source_sections = document_context.get("composition_sections") if isinstance(document_context, dict) else []
    source_items = _source_items_from_sections(source_sections)
    _assign_component_source_item_numbers(components, source_items)
    _postprocess_component_information_needs(components, source_items)
    excluded_components = _normalize_excluded_components(_payload_excluded_items(payload))
    components.sort(key=lambda item: (item.get("order_index") or 9999, item.get("component_title") or ""))
    warnings = _normalize_warnings(_first_present(payload, "warnings", "警告") if isinstance(payload, dict) else [])
    llm_component_count = len(components)
    coverage = _build_coverage_check(source_items, components, excluded_components)
    missing_before_repair = list(coverage.get("missing_source_item_nos") or [])
    if repair_missing and coverage.get("missing_source_item_nos"):
        _append_missing_source_item_components(components, source_items, coverage["missing_source_item_nos"])
        coverage = _build_coverage_check(source_items, components, excluded_components)
        coverage["missing_source_item_nos_before_repair"] = missing_before_repair
        coverage["repaired_missing_count"] = len(missing_before_repair)
        warnings.append(
            {
                "code": "source_item_repair",
                "message": "LLM 未覆盖全部技术标原文组成项，系统已将漏项补为待人工复核项。",
            }
        )
    coverage["llm_component_count"] = llm_component_count
    if source_items and coverage.get("coverage_status") != "complete":
        warnings.append(
            {
                "code": "composition_coverage_incomplete",
                "message": f"原文组成项 {coverage.get('source_item_count', 0)} 项，当前输出覆盖 {coverage.get('covered_source_item_count', 0)} 项。",
            }
        )
    if not components:
        warnings.append({"code": "empty_components", "message": "LLM 未返回可用的技术标组成项。"})
    return {
        "version": BID_TECHNICAL_COMPOSITION_VERSION,
        "status": "generated",
        "project_uuid": project.project_uuid,
        "run_uuid": run.run_uuid,
        "llm": {"provider": settings.bidding_llm_provider, "model": bidding_llm_model()},
        "source_sections": source_sections or [],
        "source_items": source_items,
        "coverage_check": coverage,
        "components": components,
        "excluded_components": excluded_components,
        "warnings": warnings,
    }


def _should_retry_source_item_classification(plan: dict[str, Any]) -> bool:
    coverage = plan.get("coverage_check") if isinstance(plan.get("coverage_check"), dict) else {}
    source_item_count = _safe_int(coverage.get("source_item_count"), 0)
    llm_component_count = _safe_int(coverage.get("llm_component_count"), 0)
    if source_item_count <= 0:
        return False
    return llm_component_count == 0


def _empty_technical_composition_plan(
    project: BidProject,
    run: BidParseRun,
    document_context: dict[str, Any],
) -> dict[str, Any]:
    source_sections = document_context.get("composition_sections") if isinstance(document_context, dict) else []
    source_items = _source_items_from_sections(source_sections)
    return {
        "version": BID_TECHNICAL_COMPOSITION_VERSION,
        "status": "generated",
        "project_uuid": project.project_uuid,
        "run_uuid": run.run_uuid,
        "llm": {"provider": settings.bidding_llm_provider, "model": bidding_llm_model()},
        "source_sections": source_sections or [],
        "source_items": source_items,
        "coverage_check": {
            "source_item_count": len(source_items),
            "component_count": 0,
            "excluded_count": 0,
            "covered_source_item_count": 0,
            "missing_source_item_nos": [item["item_no"] for item in source_items if item.get("item_no")],
            "duplicate_component_source_item_nos": [],
            "coverage_status": "incomplete" if source_items else "not_available",
            "llm_component_count": 0,
        },
        "components": [],
        "excluded_components": [],
        "warnings": [],
    }


def _technical_composition_error_detail(exc: Exception | None) -> str:
    if not exc:
        return ""
    return _clip(getattr(exc, "code", None) or str(exc), 160)


def _payload_component_items(payload: Any) -> list[Any]:
    direct = _find_payload_list(
        payload,
        {
            "components",
            "technical_components",
            "technical_bid_components",
            "composition_components",
            "composition_items",
            "bid_components",
            "items",
            "组成项",
            "技术标组成",
            "技术标组成项",
        },
        excluded_keys={"source_items", "raw_items", "requirements", "information_needs"},
    )
    return direct if isinstance(direct, list) else []


def _payload_excluded_items(payload: Any) -> list[Any]:
    direct = _find_payload_list(
        payload,
        {
            "excluded_components",
            "excluded_items",
            "exclusions",
            "排除项",
            "不适用项",
        },
        excluded_keys=set(),
    )
    return direct if isinstance(direct, list) else []


def _find_payload_list(payload: Any, target_keys: set[str], *, excluded_keys: set[str]) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    queue: list[Any] = [payload]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        for key, value in current.items():
            normalized_key = str(key).strip()
            if normalized_key in excluded_keys:
                continue
            if normalized_key in target_keys and isinstance(value, list):
                return value
        for key, value in current.items():
            normalized_key = str(key).strip()
            if normalized_key in excluded_keys:
                continue
            if isinstance(value, dict):
                queue.append(value)
    return []


def _first_present(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_needs(value: Any, *, component_key: str) -> list[dict[str, Any]]:
    needs: list[dict[str, Any]] = []
    for index, raw in enumerate(value if isinstance(value, list) else []):
        if not isinstance(raw, dict):
            continue
        title = _clean_text(_first_present(raw, "need_title", "title", "name", "query", "资料名称", "信息名称", "需求名称"), 160)
        if not title:
            continue
        source_type = _normalize_source_type(_first_present(raw, "source_type", "source", "来源", "信息来源"))
        category = _clean_text(_first_present(raw, "profile_category", "category", "资料类别", "企业资料类别"), 64)
        if category not in ENTERPRISE_PROFILE_CATEGORY_VALUES:
            category = None
        need_key = _stable_key(_first_present(raw, "need_key", "key") or title, prefix=f"{component_key}_need", index=index)
        needs.append(
            {
                "need_key": need_key,
                "need_title": title,
                "source_type": source_type,
                "profile_category": category,
                "query": _clean_text(_first_present(raw, "query", "search_query", "检索词", "查询") or title, 220),
                "reason": _clip(_first_present(raw, "reason", "理由"), 500),
                "polished_text": _clip(_first_present(raw, "polished_text", "draft_text", "正式表述", "润色文本"), 1600),
                "source_evidence": _normalize_evidence(_first_present(raw, "source_evidence", "evidence", "依据", "证据")),
            }
        )
    return needs


def _source_items_from_sections(sections: Any) -> list[dict[str, Any]]:
    items_by_no: dict[str, dict[str, Any]] = {}
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        for raw in section.get("raw_items") or []:
            if not isinstance(raw, dict) or not raw.get("item_no"):
                continue
            item = {
                **raw,
                "source_file": section.get("source_file"),
                "source_location": section.get("source_location"),
                "section_no": section.get("section_no"),
            }
            previous = items_by_no.get(raw["item_no"])
            if not previous or len(str(item.get("original_text") or "")) >= len(str(previous.get("original_text") or "")):
                items_by_no[raw["item_no"]] = item
    return sorted(items_by_no.values(), key=lambda item: [_safe_int(part, 0) for part in str(item.get("item_no") or "").split(".")])


def _assign_component_source_item_numbers(components: list[dict[str, Any]], source_items: list[dict[str, Any]]) -> None:
    if not source_items:
        return
    used = {component.get("source_item_no") for component in components if component.get("source_item_no")}
    for component in components:
        if component.get("source_item_no"):
            continue
        best_item = None
        best_score = 0.0
        for item in source_items:
            if item.get("item_no") in used:
                continue
            score = _profile_match_score(str(component.get("component_title") or ""), _SourceItemMatch(item))
            if score > best_score:
                best_score = score
                best_item = item
        if best_item and best_score >= 0.32:
            component["source_item_no"] = best_item["item_no"]
            used.add(best_item["item_no"])


def _postprocess_component_information_needs(components: list[dict[str, Any]], source_items: list[dict[str, Any]]) -> None:
    source_by_no = {str(item.get("item_no") or ""): item for item in source_items if item.get("item_no")}
    for component in components:
        if not isinstance(component, dict):
            continue
        source_item = source_by_no.get(str(component.get("source_item_no") or ""))
        _expand_compound_enterprise_profile_needs(component, source_item)
        _promote_scheme_manual_needs(component, source_item)


def _expand_compound_enterprise_profile_needs(component: dict[str, Any], source_item: dict[str, Any] | None) -> None:
    needs = [item for item in component.get("information_needs") or [] if isinstance(item, dict)]
    if not any(need.get("source_type") == "enterprise_profile" for need in needs):
        return
    evidence = _component_default_evidence(component, source_item)
    expanded: list[dict[str, Any]] = []
    changed = False
    for need in needs:
        if need.get("source_type") != "enterprise_profile":
            expanded.append(need)
            continue
        need_text = _need_match_text(need)
        if _is_business_license_qualification_need(need_text):
            expanded.extend(
                [
                    _expanded_profile_need(
                        component,
                        need,
                        suffix="business_license",
                        title="营业执照",
                        category="certificate",
                        query="营业执照 企业营业执照 营业执照副本 统一社会信用代码",
                        reason="该组成项同时要求营业执照和资质证明，营业执照应作为独立企业资料匹配。",
                        evidence=evidence,
                    ),
                    _expanded_profile_need(
                        component,
                        need,
                        suffix="qualification_certificate",
                        title="企业资质证明文件",
                        category="qualification",
                        query="企业资质证书 建筑业企业资质证书 建筑装修装饰工程专业承包资质",
                        reason="该组成项同时要求营业执照和资质证明，企业资质证明应作为独立企业资料匹配。",
                        evidence=evidence,
                    ),
                ]
            )
            changed = True
            continue
        if _is_project_org_personnel_need(need_text):
            expanded.extend(
                [
                    _expanded_profile_need(
                        component,
                        need,
                        suffix="project_team_info",
                        title="项目经理及主要人员信息",
                        category="basic_info",
                        query="项目经理及主要人员信息 项目组织架构 主要管理人员简历 技术负责人 安全负责人",
                        reason="该组成项需要引用项目组织架构及主要管理人员信息，应优先匹配企业资料库中的人员信息资料。",
                        evidence=evidence,
                    )
                ]
            )
            changed = True
            continue
        if _is_project_personnel_bundle_need(need_text):
            expanded.extend(_project_personnel_profile_needs(component, need, evidence))
            changed = True
            continue
        expanded.append(need)
    if changed:
        component["information_needs"] = _dedupe_information_needs(expanded)


def _promote_scheme_manual_needs(component: dict[str, Any], source_item: dict[str, Any] | None) -> None:
    needs = [item for item in component.get("information_needs") or [] if isinstance(item, dict)]
    if not needs or not _composition_component_scheme_like(component, source_item):
        return
    changed = False
    evidence = _component_default_evidence(component, source_item)
    promoted: list[dict[str, Any]] = []
    for need in needs:
        if need.get("source_type") != "manual_input":
            promoted.append(need)
            continue
        promoted_need = dict(need)
        promoted_need["source_type"] = "tender_document"
        promoted_need["profile_category"] = None
        promoted_need["query"] = promoted_need.get("query") or component.get("component_title") or ""
        promoted_need["reason"] = (
            promoted_need.get("reason")
            or "该章节属于项目化技术方案内容，应基于招标文件、项目背景和企业资料生成正文；人工仅做复核和补充。"
        )
        promoted_need["polished_text"] = promoted_need.get("polished_text") or _source_item_polished_hint(component, source_item)
        promoted_need["source_evidence"] = promoted_need.get("source_evidence") or evidence
        promoted.append(promoted_need)
        changed = True
    if not changed:
        return
    component["information_needs"] = _dedupe_information_needs(promoted)
    if component.get("classification") == "manual_input":
        component["classification"] = "tender_extracted_content"
    elif component.get("classification") == "fixed_enterprise_material":
        component["classification"] = "mixed"
    component["classification_reason"] = component.get("classification_reason") or (
        "该组成项属于方案、计划、措施或项目化响应内容，系统按招标文件生成正文，人工复核。"
    )


def _project_personnel_profile_needs(
    component: dict[str, Any],
    base_need: dict[str, Any],
    evidence: list[dict[str, str]],
) -> list[dict[str, Any]]:
    return [
        _expanded_profile_need(
            component,
            base_need,
            suffix="project_team_info",
            title="项目经理及主要人员信息",
            category="basic_info",
            query="项目经理及主要人员信息 项目经理 技术负责人 安全负责人 主要管理人员简历",
            reason="该组成项要求主要管理人员简历，应优先引用企业资料库中的项目人员信息。",
            evidence=evidence,
        ),
        _expanded_profile_need(
            component,
            base_need,
            suffix="project_manager_constructor_certificate",
            title="项目经理一级建造师注册证书",
            category="personnel",
            query="项目经理 一级建造师注册证书 建造师注册证书",
            reason="该组成项要求项目经理资格证书，应匹配一级建造师注册证书。",
            evidence=evidence,
        ),
        _expanded_profile_need(
            component,
            base_need,
            suffix="project_manager_safety_b_certificate",
            title="项目经理安全生产考核合格证书",
            category="personnel",
            query="项目经理 安全生产考核合格证书 B证 粤建安B",
            reason="该组成项要求项目经理等人员资格证书，安全生产考核合格证书应单独匹配。",
            evidence=evidence,
        ),
        _expanded_profile_need(
            component,
            base_need,
            suffix="main_management_staff_certificates",
            title="主要管理人员岗位及资格证书",
            category="personnel",
            query="主要管理人员 技术负责人 安全负责人 施工员 质量员 材料员 资料员 职业培训合格证 资格证书",
            reason="该组成项要求技术负责人、安全负责人及其他主要管理人员资格证明，应作为独立资料项补齐。",
            evidence=evidence,
        ),
    ]


def _expanded_profile_need(
    component: dict[str, Any],
    base_need: dict[str, Any],
    *,
    suffix: str,
    title: str,
    category: str,
    query: str,
    reason: str,
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    component_key = str(component.get("component_key") or "component")
    return {
        "need_key": _stable_key(f"{component_key}_{suffix}", prefix=f"{component_key}_need", index=0),
        "need_title": title,
        "source_type": "enterprise_profile",
        "profile_category": category,
        "query": query,
        "reason": reason or base_need.get("reason") or "",
        "polished_text": "",
        "source_evidence": base_need.get("source_evidence") or evidence,
    }


def _dedupe_information_needs(needs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for need in needs:
        key = "|".join(
            [
                str(need.get("source_type") or ""),
                str(need.get("profile_category") or ""),
                _identity_text(need.get("need_title") or need.get("query") or need.get("need_key")),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(need)
    return result


def _need_match_text(need: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in [need.get("need_title"), need.get("query"), need.get("reason")]
    )


def _component_match_text(component: dict[str, Any], source_item: dict[str, Any] | None = None) -> str:
    values = [
        component.get("source_item_no"),
        component.get("component_title"),
        component.get("classification_reason"),
        component.get("draft_instruction"),
    ]
    if source_item:
        values.extend([source_item.get("item_title"), source_item.get("original_text")])
    for need in component.get("information_needs") or []:
        if isinstance(need, dict):
            values.extend([need.get("need_title"), need.get("query"), need.get("reason")])
    return " ".join(str(value or "") for value in values)


def _is_business_license_qualification_need(text: str) -> bool:
    normalized = _normalize_for_match(text)
    return _has_any(normalized, ["营业执照", "businesslicense"]) and _has_any(
        normalized,
        ["资质", "资格证明", "qualification"],
    )


def _is_project_personnel_bundle_need(text: str) -> bool:
    normalized = _normalize_for_match(text)
    return _has_any(normalized, ["主要管理人员", "技术负责人", "安全负责人", "人员简历"]) and _has_any(
        normalized,
        ["资格证书", "证书", "简历", "certificate", "resume"],
    )


def _is_project_org_personnel_need(text: str) -> bool:
    normalized = _normalize_for_match(text)
    return _has_any(normalized, ["组织架构", "组织机构", "项目组织", "项目管理架构"]) and _has_any(
        normalized,
        ["主要管理人员", "人员简历", "项目经理"],
    )


def _composition_component_scheme_like(component: dict[str, Any], source_item: dict[str, Any] | None = None) -> bool:
    text = _normalize_for_match(_component_match_text(component, source_item))
    return _has_any(
        text,
        [
            "方案",
            "措施",
            "施工组织设计",
            "施工总进度计划",
            "进度计划",
            "管理方案",
            "临时用电",
            "采购计划",
            "质量",
            "安全文明",
            "文明施工",
            "防火施工",
            "样板",
            "品牌表",
            "重难点",
            "竞争力",
            "工期",
            "承诺",
            "保证",
            "constructionplan",
            "methodstatement",
            "schedule",
            "quality",
            "safety",
        ],
    )


def _source_item_polished_hint(component: dict[str, Any], source_item: dict[str, Any] | None) -> str:
    title = component.get("component_title") or (source_item or {}).get("item_title") or "本章节"
    original = _clip((source_item or {}).get("original_text"), 500)
    if original:
        return f"本章节应响应招标文件原文要求：{original}"
    return f"本章节应围绕“{title}”结合招标文件、项目背景和企业履约能力编写技术标正文。"


def _component_default_evidence(component: dict[str, Any], source_item: dict[str, Any] | None) -> list[dict[str, str]]:
    evidence = component.get("source_evidence")
    if isinstance(evidence, list) and evidence:
        return _normalize_evidence(evidence)
    if source_item:
        return [
            {
                "source_file": str(source_item.get("source_file") or "招标文件"),
                "source_location": str(source_item.get("source_location") or source_item.get("section_no") or "-"),
                "original_text": _clip(source_item.get("original_text"), 500),
            }
        ]
    return []


def _has_any(text: str, tokens: list[str]) -> bool:
    if not text:
        return False
    return any(_normalize_for_match(token) in text for token in tokens if token)


def _normalize_excluded_components(value: Any) -> list[dict[str, Any]]:
    result = []
    for index, raw in enumerate(value if isinstance(value, list) else []):
        if not isinstance(raw, dict):
            continue
        source_item_no = _clean_text(raw.get("source_item_no") or raw.get("item_no"), 40)
        title = _clean_text(raw.get("component_title") or raw.get("title"), 180)
        if not source_item_no and not title:
            continue
        result.append(
            {
                "source_item_no": source_item_no,
                "component_title": title or source_item_no or f"排除项{index + 1}",
                "reason": _clip(raw.get("reason"), 500),
            }
        )
    return result


def _build_coverage_check(
    source_items: list[dict[str, Any]],
    components: list[dict[str, Any]],
    excluded_components: list[dict[str, Any]],
) -> dict[str, Any]:
    source_nos = [item["item_no"] for item in source_items if item.get("item_no")]
    component_nos = [component.get("source_item_no") for component in components if component.get("source_item_no")]
    excluded_nos = [component.get("source_item_no") for component in excluded_components if component.get("source_item_no")]
    covered = set(component_nos) | set(excluded_nos)
    missing = [item_no for item_no in source_nos if item_no not in covered]
    duplicate = sorted({item_no for item_no in component_nos if component_nos.count(item_no) > 1})
    return {
        "source_item_count": len(source_nos),
        "component_count": len(components),
        "excluded_count": len(excluded_components),
        "covered_source_item_count": len([item_no for item_no in source_nos if item_no in covered]),
        "missing_source_item_nos": missing,
        "duplicate_component_source_item_nos": duplicate,
        "coverage_status": "complete" if source_nos and not missing and not duplicate else ("not_available" if not source_nos else "incomplete"),
    }


def _append_missing_source_item_components(
    components: list[dict[str, Any]],
    source_items: list[dict[str, Any]],
    missing_source_item_nos: list[str],
) -> None:
    source_by_no = {item["item_no"]: item for item in source_items}
    next_order = max([_safe_int(component.get("order_index"), 0) for component in components] or [0]) + 1
    for offset, item_no in enumerate(missing_source_item_nos):
        item = source_by_no.get(item_no)
        if not item:
            continue
        title = _clean_text(item.get("item_title"), 180) or item_no
        evidence = [
            {
                "source_file": item.get("source_file") or "招标文件",
                "source_location": item.get("source_location") or item.get("section_no") or "-",
                "original_text": _clip(item.get("original_text"), 500),
            }
        ]
        component_key = _stable_key(f"missing_{item_no}_{title}", prefix="source_item", index=offset)
        components.append(
            {
                "component_key": component_key,
                "component_title": title,
                "source_item_no": item_no,
                "package_key": "technical",
                "order_index": next_order + offset,
                "classification": "manual_input",
                "classification_reason": "LLM 未覆盖该原文组成项，系统按原文补入并标记为待人工复核。",
                "source_evidence": evidence,
                "information_needs": [
                    {
                        "need_key": f"{component_key}_manual_review",
                        "need_title": title,
                        "source_type": "manual_input",
                        "profile_category": None,
                        "query": title,
                        "reason": "请人工确认该组成项应从企业资料库、招标文件抽取还是人工补充。",
                        "polished_text": "",
                        "source_evidence": evidence,
                    }
                ],
                "draft_instruction": "该组成项来自技术标原文清单，但 LLM 未完成来源分类，生成草案前需人工复核。",
                "coverage_repair": True,
            }
        )


class _SourceItemMatch:
    def __init__(self, item: dict[str, Any]):
        self.title = item.get("item_title") or ""
        self.summary = item.get("original_text") or ""
        self.profile_key = item.get("item_no") or ""
        self.subcategory = item.get("section_no") or ""
        self.tags_json = ""


def _sync_material_requirements_from_plan(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    plan: dict[str, Any],
    *,
    user: User,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    created_count = 0
    refreshed_count = 0
    auto_submitted_count = 0
    missing_count = 0
    valid_material_keys: set[str] = set()
    synced_row_ids: set[int] = set()

    existing_rows = db.query(BidMaterialRequirement).filter(BidMaterialRequirement.parse_run_id == run.id).all()
    existing_by_key = {row.material_key: row for row in existing_rows}
    existing_by_identity: dict[str, BidMaterialRequirement] = {}
    for row in existing_rows:
        for identity in _row_material_identities(row):
            existing_by_identity.setdefault(identity, row)
    for component in plan.get("components") or []:
        for need in component.get("information_needs") or []:
            if need.get("source_type") != "enterprise_profile":
                continue
            match = _match_enterprise_profile(db, user, need)
            selected_profile_item_uuid = match.get("selected", {}).get("item_uuid") if match.get("selected") else None
            material_identities = _material_identity_candidates(
                component,
                need,
                selected_profile_item_uuid=selected_profile_item_uuid,
            )
            material_identity = material_identities[0]
            material_key = _material_key(component, need, material_identity=material_identity)
            valid_material_keys.add(material_key)
            first_evidence = _first_evidence(need.get("source_evidence") or component.get("source_evidence") or [])
            row = existing_by_key.get(material_key) or next(
                (existing_by_identity[identity] for identity in material_identities if identity in existing_by_identity),
                None,
            )
            old_status = row.status if row else None
            if not row:
                row = BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_plan_id=None,
                    format_item_key=component["component_key"],
                    package_key="technical",
                    package_title="技术标",
                    section_key=_component_section_key(component),
                    item_title=component["component_title"],
                    requirement_type="profile",
                    profile_category=need.get("profile_category") or "other",
                    material_key=material_key,
                    title=need["need_title"],
                    fulfillment_mode="enterprise_profile",
                    status="missing",
                    priority="high",
                    owner_role="经营",
                    created_by=user.id,
                    updated_by=user.id,
                )
                db.add(row)
                db.flush()
                created_count += 1
            else:
                if row.material_key != material_key:
                    row.material_key = material_key
                refreshed_count += 1
            _apply_requirement_values(row, component, need, match, first_evidence, user=user, material_identity=material_identity)
            existing_by_key[material_key] = row
            existing_by_identity[material_identity] = row
            if row.status == "submitted" and match.get("selected"):
                auto_submitted_count += 1
            if row.status == "missing":
                missing_count += 1
            _record_material_event(
                db,
                row,
                event_type="technical_composition_sync",
                old_status=old_status,
                user=user,
                detail={
                    "component_key": component["component_key"],
                    "need_key": need["need_key"],
                    "match_score": match.get("score"),
                    "selected_profile_item_uuid": match.get("selected", {}).get("item_uuid") if match.get("selected") else None,
                },
            )
            if row.id not in synced_row_ids:
                rows.append(_serialize_requirement_row(row))
                synced_row_ids.add(row.id)
    stale_removed_count = _remove_stale_technical_composition_requirements(db, run, valid_material_keys)
    return {
        "created_count": created_count,
        "refreshed_count": refreshed_count,
        "stale_removed_count": stale_removed_count,
        "auto_submitted_count": auto_submitted_count,
        "missing_count": missing_count,
        "rows": rows,
    }


def _apply_requirement_values(
    row: BidMaterialRequirement,
    component: dict[str, Any],
    need: dict[str, Any],
    match: dict[str, Any],
    first_evidence: dict[str, Any],
    *,
    user: User,
    material_identity: str,
) -> None:
    selected = match.get("selected")
    preserve_existing_submission = _preserve_existing_material_submission(row)
    if preserve_existing_submission and selected and _should_refresh_preserved_profile_submission(row, selected):
        preserve_existing_submission = False
    row.format_item_key = component["component_key"]
    row.package_key = "technical"
    row.package_title = "技术标"
    row.section_key = _component_section_key(component)
    row.item_title = component["component_title"]
    row.requirement_type = "profile"
    row.profile_category = _profile_category_for_need(need)
    row.title = need["need_title"]
    row.description = need.get("reason") or f"来自技术标组成项：{component['component_title']}"
    row.source_file = first_evidence.get("source_file")
    row.source_location = first_evidence.get("source_location")
    row.source_text = first_evidence.get("original_text")
    row.fulfillment_mode = "enterprise_profile"
    row.priority = "high"
    row.owner_role = "经营"
    row.candidate_profile_item_uuid = selected.get("item_uuid") if selected else None
    row.normalized_json = dumps_json(
        {
            "version": BID_TECHNICAL_COMPOSITION_VERSION,
            "extractor": "llm_technical_composition",
            "component": {
                "component_key": component["component_key"],
                "component_title": component["component_title"],
                "source_item_no": component.get("source_item_no"),
                "classification": component.get("classification"),
                "classification_reason": component.get("classification_reason"),
            },
            "need": need,
            "material_identity": material_identity,
            "candidate_profile_item": selected,
            "candidates": match.get("candidates") or [],
            "match_score": match.get("score"),
            "manual_submission": _manual_submission(row),
        }
    )
    row.evidence_json = dumps_json(need.get("source_evidence") or component.get("source_evidence") or [])
    if selected and not preserve_existing_submission:
        row.submitted_profile_item_uuid = selected["item_uuid"]
        row.submitted_file_id = None
        row.submitted_value = None
        row.status = "submitted"
        row.notes = "LLM 识别为固定企业资料，系统按名称相近度从企业资料库自动匹配，待人工确认可用。"
        row.normalized_json = _with_manual_profile(row.normalized_json, selected["item_uuid"])
        row.reviewed_by = None
        row.reviewed_at = None
    elif not selected and not preserve_existing_submission:
        row.submitted_profile_item_uuid = None
        row.submitted_file_id = None
        row.submitted_value = None
        row.normalized_json = _without_manual_submission(row.normalized_json)
        row.status = "missing"
        row.notes = "LLM 识别为固定企业资料，但企业资料库未找到足够接近的 active 资料，请人工填写或上传。"
        row.reviewed_by = None
        row.reviewed_at = None
    row.updated_by = user.id


def _component_section_key(component: dict[str, Any]) -> str:
    source_item_no = _clean_text(component.get("source_item_no"), 40)
    if source_item_no:
        match = re.search(r"\d+(?:\.\d+)+", source_item_no)
        anchor = match.group(0) if match else source_item_no
        key = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", anchor).strip("_").lower()
        if key:
            return f"technical_composition:{key[:80]}"
    return f"technical_composition:{component['component_key']}"


def _preserve_existing_material_submission(row: BidMaterialRequirement) -> bool:
    if row.status in {"approved", "applied", "not_applicable"}:
        return True
    if not (row.submitted_profile_item_uuid or row.submitted_file_id or row.submitted_value or _manual_submission(row)):
        return False
    normalized = loads_json(row.normalized_json, {}) or {}
    if normalized.get("extractor") == "llm_technical_composition" and row.status == "submitted":
        return False
    return True


def _should_refresh_preserved_profile_submission(row: BidMaterialRequirement, selected: dict[str, Any]) -> bool:
    selected_uuid = selected.get("item_uuid") if isinstance(selected, dict) else None
    if not selected_uuid or row.submitted_profile_item_uuid == selected_uuid:
        return False
    normalized = loads_json(row.normalized_json, {}) or {}
    if normalized.get("extractor") != "llm_technical_composition":
        return False
    candidate = normalized.get("candidate_profile_item")
    candidate_uuid = candidate.get("item_uuid") if isinstance(candidate, dict) else None
    current_candidate_uuid = row.candidate_profile_item_uuid or candidate_uuid
    material_identity = str(normalized.get("material_identity") or "")
    if current_candidate_uuid == selected_uuid:
        return True
    if material_identity == f"profile:{selected_uuid}":
        return True
    return False


def _match_enterprise_profile(db: Session, user: User, need: dict[str, Any]) -> dict[str, Any]:
    if not settings.feature_enterprise_profile:
        return {"selected": None, "candidates": [], "score": 0.0, "enabled": False}
    query_text = " ".join(
        item
        for item in [
            need.get("query"),
            need.get("need_title"),
            need.get("reason"),
        ]
        if item
    )
    search_terms = _search_terms(query_text)
    category = _profile_category_for_need(need)
    candidates: dict[str, EnterpriseProfileItem] = {}
    for term in search_terms[:8]:
        for scoped_category in ([category, None] if category else [None]):
            for item in list_active_profile_candidates(db, user, category=scoped_category, keyword=term, limit=20):
                candidates[item.item_uuid] = item
    if not candidates:
        for item in list_active_profile_candidates(db, user, category=category, keyword=None, limit=80):
            candidates[item.item_uuid] = item
    serialized = _rank_profile_candidates(query_text, category, candidates.values())
    selected = next((item for item in serialized if item.get("match_eligible")), None)
    if not selected:
        for item in list_active_profile_candidates(db, user, category=category, keyword=None, limit=120):
            candidates[item.item_uuid] = item
        for item in list_active_profile_candidates(db, user, category=None, keyword=None, limit=200):
            candidates[item.item_uuid] = item
        serialized = _rank_profile_candidates(query_text, category, candidates.values())
        selected = next((item for item in serialized if item.get("match_eligible")), None)
    return {
        "selected": selected,
        "candidates": serialized,
        "score": selected["match_score"] if selected else (serialized[0]["match_score"] if serialized else 0.0),
        "enabled": True,
    }


def _rank_profile_candidates(
    query_text: str,
    category: str | None,
    candidates: Any,
) -> list[dict[str, Any]]:
    scored = sorted(
        (
            (_profile_match_score(query_text, item), item)
            for item in candidates
        ),
        key=lambda pair: (pair[0], pair[1].updated_at or pair[1].created_at),
        reverse=True,
    )
    serialized = []
    for score, item in scored[:5]:
        data = serialize_item(item)
        data["match_score"] = round(score, 4)
        quality = _profile_match_quality(query_text, category, item, score)
        data["match_eligible"] = bool(quality.get("eligible"))
        data["match_reject_reason"] = None if quality.get("eligible") else quality.get("reason")
        data["match_category_ok"] = bool(quality.get("category_match"))
        data["match_phrase_hits"] = quality.get("phrase_hits") or []
        serialized.append(data)
    return serialized


def _profile_category_for_need(need: dict[str, Any]) -> str:
    explicit_category = _clean_text(need.get("profile_category"), 64)
    if explicit_category in ENTERPRISE_PROFILE_CATEGORY_VALUES:
        return explicit_category
    text = " ".join(
        str(value or "")
        for value in (
            need.get("title"),
            need.get("need_title"),
            need.get("query"),
            need.get("reason"),
        )
    )
    if "营业执照" in text or "安全生产许可证" in text or "许可证" in text:
        return "certificate"
    if "授权委托" in text or "承诺" in text or "保修" in text:
        return "commitment_template"
    if "法定代表人" in text or "投标函" in text or "企业基本" in text:
        return "basic_info"
    if any(token in text for token in ("项目经理", "建造师", "人员", "技术负责人", "安全负责人", "资格证书", "注册证书")):
        return "personnel"
    if "类似" in text and ("业绩" in text or "经验" in text or "合同" in text):
        return "project_performance"
    if "业绩" in text and "合同" in text:
        return "project_performance"
    if "资质" in text or "资格证明" in text:
        return "qualification"
    return "other"


def _profile_match_score(query: str, item: EnterpriseProfileItem) -> float:
    haystack = " ".join(
        str(value or "")
        for value in [item.title, item.summary, item.profile_key, item.subcategory, item.tags_json]
    )
    q = _normalize_for_match(query)
    h = _normalize_for_match(haystack)
    if not q or not h:
        return 0.0
    char_overlap = len(set(q) & set(h)) / max(len(set(q)), 1)
    sequence = SequenceMatcher(None, q, h).ratio()
    substring = 0.18 if q in h or h[: min(len(h), 12)] in q else 0.0
    return min(1.0, char_overlap * 0.55 + sequence * 0.35 + substring)


def _profile_match_quality(query: str, category: str | None, item: EnterpriseProfileItem, score: float) -> dict[str, Any]:
    text = _normalize_for_match(_profile_item_match_text(item))
    phrases = _profile_match_required_phrases(query, category)
    phrase_hits = [phrase for phrase in phrases if _normalize_for_match(phrase) in text]
    category_match = bool(category and item.category == category)
    semantic_override = bool(phrase_hits) and (
        score >= BID_TECHNICAL_COMPOSITION_CATEGORY_OVERRIDE_SCORE
        or (len(phrase_hits) >= 3 and score >= BID_TECHNICAL_COMPOSITION_MIN_PROFILE_SCORE)
    )
    strong_category_keyword_match = category_match and bool(phrase_hits) and score >= 0.18
    if score < BID_TECHNICAL_COMPOSITION_MIN_PROFILE_SCORE and not strong_category_keyword_match:
        return {
            "eligible": False,
            "reason": "score_below_threshold",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    if phrases and not phrase_hits:
        return {
            "eligible": False,
            "reason": "required_phrase_missing",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    if category and not category_match and not semantic_override:
        return {
            "eligible": False,
            "reason": "category_mismatch",
            "category_match": category_match,
            "phrase_hits": phrase_hits,
        }
    return {
        "eligible": True,
        "reason": "category_match" if category_match else "semantic_override",
        "category_match": category_match,
        "phrase_hits": phrase_hits,
    }


def _profile_item_match_text(item: EnterpriseProfileItem) -> str:
    return " ".join(
        str(value or "")
        for value in [item.title, item.summary, item.profile_key, item.subcategory, item.tags_json]
    )


def _profile_match_required_phrases(query: str, category: str | None) -> list[str]:
    text = str(query or "")
    normalized = _normalize_for_match(text)
    phrases: list[str] = []
    if category == "project_performance" or any(token in normalized for token in ("类似", "业绩", "经验", "合同", "performance", "experience", "contract")):
        phrases.extend(["类似", "业绩", "经验", "合同", "performance", "experience", "contract"])
    if category == "personnel" or any(token in normalized for token in ("项目经理", "建造师", "人员", "证书", "资格证", "注册证", "personnel", "certificate")):
        phrases.extend(["项目经理", "建造师", "人员", "证书", "资格证", "注册证", "personnel", "certificate"])
    if category == "certificate" or any(token in normalized for token in ("营业执照", "安全生产许可证", "许可证", "license", "permit")):
        phrases.extend(["营业执照", "安全生产许可证", "许可证", "license", "permit"])
    if category == "qualification" or any(token in normalized for token in ("资质", "资格证明", "qualification")):
        phrases.extend(["资质", "资格证明", "qualification"])
    if category == "commitment_template" or any(token in normalized for token in ("授权委托", "承诺", "保修", "commitment", "authorization")):
        phrases.extend(["授权委托", "承诺", "保修", "commitment", "authorization"])
    if category == "basic_info" or any(token in normalized for token in ("法定代表人", "企业基本", "投标函", "basic")):
        phrases.extend(["法定代表人", "企业基本", "投标函", "basic"])
    if category == "technical_solution" or any(token in normalized for token in ("方案", "措施", "施工组织", "质量", "安全", "文明施工", "technical", "solution")):
        phrases.extend(["方案", "措施", "施工组织", "质量", "安全", "文明施工", "technical", "solution"])
    return _unique_text(phrases)


def _search_terms(text: str) -> list[str]:
    text = _clean_text(text, 300)
    if not text:
        return []
    parts = [text]
    parts.extend(re.split(r"[\s,，、；;：:/\\（）()《》【】\[\]及和与]+", text))
    result = []
    seen = set()
    for part in parts:
        cleaned = _clean_text(part, 80)
        if len(cleaned) < 2 or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _build_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    components = plan.get("components") or []
    needs = [need for component in components for need in component.get("information_needs") or []]
    req_sync = plan.get("requirement_sync") or {}
    coverage = plan.get("coverage_check") or {}
    return {
        "source_item_count": coverage.get("source_item_count", len(plan.get("source_items") or [])),
        "llm_component_count": coverage.get("llm_component_count", len(components)),
        "repaired_missing_count": coverage.get("repaired_missing_count", 0),
        "covered_source_item_count": coverage.get("covered_source_item_count", 0),
        "missing_source_item_count": len(coverage.get("missing_source_item_nos") or []),
        "coverage_status": coverage.get("coverage_status") or "not_available",
        "component_count": len(components),
        "enterprise_profile_need_count": sum(1 for item in needs if item.get("source_type") == "enterprise_profile"),
        "enterprise_profile_material_item_count": len(req_sync.get("rows") or []),
        "tender_document_need_count": sum(1 for item in needs if item.get("source_type") == "tender_document"),
        "manual_input_need_count": sum(1 for item in needs if item.get("source_type") == "manual_input"),
        "auto_matched_profile_count": req_sync.get("auto_submitted_count", 0),
        "manual_requirement_count": req_sync.get("missing_count", 0),
    }


def _with_current_requirement_rows(db: Session, run: BidParseRun, plan: dict[str, Any]) -> dict[str, Any]:
    copy = json.loads(json.dumps(plan, ensure_ascii=False))
    requirement_sync = plan.get("requirement_sync") or {}
    current_keys = {
        str(row.get("material_key"))
        for row in requirement_sync.get("rows") or []
        if isinstance(row, dict) and row.get("material_key")
    }
    query = db.query(BidMaterialRequirement).filter(
        BidMaterialRequirement.parse_run_id == run.id,
        BidMaterialRequirement.package_key == "technical",
        BidMaterialRequirement.section_key.like("technical_composition:%"),
    )
    if current_keys:
        query = query.filter(BidMaterialRequirement.material_key.in_(current_keys))
    rows = query.order_by(BidMaterialRequirement.id.asc()).all()
    copy["requirements"] = [_serialize_requirement_row(row) for row in rows]
    summary = copy.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        copy["summary"] = summary
    summary["enterprise_profile_material_item_count"] = len(rows)
    return copy


def _serialize_requirement_row(row: BidMaterialRequirement) -> dict[str, Any]:
    normalized = loads_json(row.normalized_json, {}) or {}
    return {
        "requirement_uuid": row.requirement_uuid,
        "material_key": row.material_key,
        "component_key": normalized.get("component", {}).get("component_key"),
        "need_key": normalized.get("need", {}).get("need_key"),
        "title": row.title,
        "status": row.status,
        "candidate_profile_item_uuid": row.candidate_profile_item_uuid,
        "submitted_profile_item_uuid": row.submitted_profile_item_uuid,
        "match_score": normalized.get("match_score"),
    }


def _record_material_event(
    db: Session,
    row: BidMaterialRequirement,
    *,
    event_type: str,
    old_status: str | None,
    user: User,
    detail: dict[str, Any],
) -> None:
    db.add(
        BidMaterialRequirementEvent(
            event_uuid=str(uuid.uuid4()),
            requirement_id=row.id,
            project_id=row.project_id,
            parse_run_id=row.parse_run_id,
            event_type=event_type,
            old_status=old_status,
            new_status=row.status,
            detail_json=dumps_json(detail),
            created_by=user.id,
        )
    )


def _remove_stale_technical_composition_requirements(db: Session, run: BidParseRun, valid_material_keys: set[str]) -> int:
    if not valid_material_keys:
        return 0
    removed = 0
    rows = (
        db.query(BidMaterialRequirement)
        .filter(
            BidMaterialRequirement.parse_run_id == run.id,
            BidMaterialRequirement.package_key == "technical",
            BidMaterialRequirement.section_key.like("technical_composition:%"),
        )
        .all()
    )
    for row in rows:
        if row.material_key in valid_material_keys:
            continue
        if row.submitted_file_id or row.submitted_value:
            continue
        normalized = loads_json(row.normalized_json, {}) or {}
        if normalized.get("extractor") != "llm_technical_composition":
            continue
        db.delete(row)
        removed += 1
    if removed:
        db.flush()
    return removed


def _material_key(component: dict[str, Any], need: dict[str, Any], *, material_identity: str | None = None) -> str:
    identity = material_identity or _material_identity(component, need)
    source_anchor = _stable_key(identity, prefix="material", index=0)
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex[:16]
    key = f"technical_composition:{source_anchor[:48]}:{suffix}"
    if len(key) <= 128:
        return key
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]
    return f"technical_composition:{source_anchor[:42]}:{suffix}"[:128]


def _material_identity(component: dict[str, Any], need: dict[str, Any]) -> str:
    return _material_identity_candidates(component, need)[0]


def _material_identity_candidates(
    component: dict[str, Any],
    need: dict[str, Any],
    *,
    selected_profile_item_uuid: str | None = None,
) -> list[str]:
    source_item_no = _clean_text(component.get("source_item_no"), 40)
    title_anchor = f"title:{_identity_text(component.get('component_title'))[:80]}"
    component_anchor = f"source:{source_item_no}" if source_item_no else title_anchor
    category = _profile_category_for_need(need)
    need_anchor = _identity_text(need.get("need_title") or need.get("query") or need.get("need_key"))[:80]
    identities = []
    if selected_profile_item_uuid:
        identities.append(f"{component_anchor}|category:{category}|need:{need_anchor}|profile:{selected_profile_item_uuid}")
    if need_anchor:
        identities.append(f"{component_anchor}|need:{need_anchor}")
    scoped_identity = f"{component_anchor}|category:{category}|need:{need_anchor}"
    if scoped_identity not in identities:
        identities.append(scoped_identity)
    title_identity = f"{title_anchor}|category:{category}|need:{need_anchor}"
    if title_identity not in identities:
        identities.append(title_identity)
    return identities


def _row_material_identities(row: BidMaterialRequirement) -> list[str]:
    normalized = loads_json(row.normalized_json, {}) or {}
    existing = _clean_text(normalized.get("material_identity"), 255)
    identities: list[str] = []
    is_auto_submitted = normalized.get("extractor") == "llm_technical_composition" and row.status == "submitted"
    if is_auto_submitted and row.submitted_profile_item_uuid:
        identities.append(f"profile:{row.submitted_profile_item_uuid}")
    preserved_profile_identity = (
        normalized.get("extractor") == "llm_technical_composition"
        and row.status in {"approved", "applied", "not_applicable"}
        and existing.startswith("profile:")
    )
    if existing and (is_auto_submitted or not existing.startswith("profile:")):
        identities.append(existing)
    if row.package_key != "technical" or not str(row.section_key or "").startswith("technical_composition:"):
        return identities
    category = row.profile_category or "other"
    component_data = normalized.get("component") if isinstance(normalized.get("component"), dict) else {}
    source_item_no = _clean_text(component_data.get("source_item_no"), 40)
    component_anchor = f"source:{source_item_no}" if source_item_no else f"title:{_identity_text(row.item_title)[:80]}"
    need_anchor = _identity_text(row.title)[:80]
    if normalized.get("extractor") == "llm_technical_composition" and need_anchor:
        profile_uuids = _unique_text(
            [
                row.submitted_profile_item_uuid,
                row.candidate_profile_item_uuid,
                (normalized.get("candidate_profile_item") or {}).get("item_uuid")
                if isinstance(normalized.get("candidate_profile_item"), dict)
                else None,
            ]
        )
        for item_uuid in profile_uuids:
            scoped_profile_identity = f"{component_anchor}|category:{category}|need:{need_anchor}|profile:{item_uuid}"
            if scoped_profile_identity not in identities:
                identities.append(scoped_profile_identity)
    if preserved_profile_identity:
        return identities
    need_identity = f"{component_anchor}|need:{need_anchor}"
    if need_anchor and need_identity not in identities:
        identities.append(need_identity)
    fallback = f"{component_anchor}|category:{category}|need:{need_anchor}"
    if need_anchor and fallback not in identities:
        identities.append(fallback)
    return identities


def _identity_text(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().lower())


def _manual_submission(row: BidMaterialRequirement) -> dict[str, Any]:
    normalized = loads_json(row.normalized_json, {}) or {}
    manual = normalized.get("manual_submission")
    return dict(manual) if isinstance(manual, dict) else {}


def _with_manual_profile(normalized_json: str | None, item_uuid: str) -> str:
    normalized = loads_json(normalized_json, {}) or {}
    manual = normalized.get("manual_submission")
    if not isinstance(manual, dict):
        manual = {}
    manual["profile_item_uuids"] = [item_uuid]
    normalized["manual_submission"] = manual
    return dumps_json(normalized)


def _without_manual_submission(normalized_json: str | None) -> str:
    normalized = loads_json(normalized_json, {}) or {}
    normalized["manual_submission"] = {}
    return dumps_json(normalized)


def _normalize_evidence(value: Any) -> list[dict[str, str]]:
    result = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        text = _clip(raw.get("original_text") or raw.get("text"), 500)
        if not text:
            continue
        result.append(
            {
                "source_file": _clean_text(raw.get("source_file"), 255) or "招标文件",
                "source_location": _clean_text(raw.get("source_location"), 255) or "-",
                "original_text": text,
            }
        )
    return result[:6]


def _first_evidence(items: list[dict[str, Any]]) -> dict[str, Any]:
    return items[0] if items else {}


def _normalize_warnings(value: Any) -> list[dict[str, str]]:
    warnings = []
    for raw in value if isinstance(value, list) else []:
        if isinstance(raw, dict):
            message = _clip(raw.get("message") or raw.get("text"), 300)
            if message:
                warnings.append({"code": _clean_text(raw.get("code"), 64) or "llm_warning", "message": message})
        elif raw:
            warnings.append({"code": "llm_warning", "message": _clip(raw, 300)})
    return warnings


def _normalize_classification(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"fixed_enterprise_material", "tender_extracted_content", "mixed", "manual_input"}:
        return text
    if any(token in text for token in ("企业资料", "固定资料", "证照", "资质", "证书", "授权", "业绩", "fixed", "enterprise")):
        return "fixed_enterprise_material"
    if any(token in text for token in ("招标文件", "项目提取", "项目内容", "tender", "extract")):
        return "tender_extracted_content"
    if any(token in text for token in ("混合", "mixed")):
        return "mixed"
    if any(token in text for token in ("人工", "手动", "manual")):
        return "manual_input"
    return "mixed"


def _normalize_source_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"enterprise_profile", "tender_document", "manual_input"}:
        return text
    if any(token in text for token in ("企业资料", "企业资料库", "固定资料", "证照", "资质", "证书", "授权", "业绩", "enterprise", "profile")):
        return "enterprise_profile"
    if any(token in text for token in ("招标文件", "招标资料", "本项目", "项目背景", "tender", "document")):
        return "tender_document"
    if any(token in text for token in ("人工", "手动", "补充", "manual")):
        return "manual_input"
    return "manual_input"


def _stable_key(value: Any, *, prefix: str, index: int) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", text).strip("_")
    if not text:
        text = f"{prefix}_{index + 1}"
    if re.search(r"[\u4e00-\u9fff]", text):
        text = f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, text).hex[:10]}"
    return text[:96]


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_BAD_JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise BidTechnicalCompositionError("BID_TECHNICAL_COMPOSITION_LLM_BAD_JSON")
    return payload


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_for_match(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower())


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _clip(value: Any, limit: int) -> str:
    return _clean_text(value, limit)
