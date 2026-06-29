from __future__ import annotations

import asyncio
import base64
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.config import settings
from app.services.cad_view_frame_detector import (
    CAD_VIEW_TILE_TYPE,
    augment_tile_report_with_cad_views,
    build_cad_view_frame_report,
)
from app.services.drawing_pdf_evidence_pipeline import (
    build_pdf_basic_parse_report,
    build_pdf_render_report,
    build_pdf_tile_report,
    collect_pdf_files,
)
from app.services.drawing_agent_runtime import (
    DrawingAgentRunTracker,
    create_pdf_agent_run_tracker,
    pdf_agent_completion_status,
)
from app.services.drawing_agent_ocr import build_pdf_agent_ocr_report
from app.services.drawing_cad_view_detail_planner import build_cad_view_detail_plan_report
from app.services.drawing_highres_region_renderer import build_highres_region_render_report
from app.services.drawing_layout_planner import build_pdf_layout_plan_report
from app.services.drawing_region_cropper import build_region_crop_report
from app.services.quantity_list_export import write_quantity_list_outputs
from app.services.quantity_standard_index import search_standard_index


PHASE = "BIZ-2x-pdf-agent-itemization"
PDF_AGENT_SOURCE_MODE = "pdf_agent_model_flow"
PDF_AGENT_STATUS_COMPLETED = "model_flow_completed"
WHOLE_PAGE_CONTEXT_ROLE = "whole_page_context"
PAGE_CONTEXT_ROLE = "page_context"
LOCAL_CAD_VIEW_ROLE = "local_cad_view"
LOCAL_GRID_VIEW_ROLE = "local_grid_view"
CONTEXT_SELECTION_ROLES = {WHOLE_PAGE_CONTEXT_ROLE, PAGE_CONTEXT_ROLE}

STANDARD_MAPPING_HEADERS = [
    "识别编号",
    "映射状态",
    "标准号",
    "标准项目编码",
    "标准项目名称",
    "标准章节",
    "标准单位",
    "匹配分数",
    "匹配原因",
    "候选数量",
    "列项判断",
    "列项处理",
    "列项判断原因",
    "具体做法名称",
    "来源视图",
    "项目特征",
    "工程量",
]

AgentEvidenceExtractor = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]
AgentBillSummarizer = Callable[[Mapping[str, Any]], Mapping[str, Any] | str]
LayoutPlanner = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]
CadViewDetailPlanner = Callable[[list[dict[str, Any]]], Mapping[str, Any] | str]
StandardSearch = Callable[..., list[dict[str, Any]]]


class PdfAgentItemizationError(ValueError):
    pass


def run_pdf_agent_itemization(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    render_dpi: int = 350,
    tile_grid_size: int = 3,
    max_views: int = 24,
    include_whole_page: bool = True,
    layout_planner: LayoutPlanner | None = None,
    cad_view_detail_planner: CadViewDetailPlanner | None = None,
    evidence_extractor: AgentEvidenceExtractor | None = None,
    bill_summarizer: AgentBillSummarizer | None = None,
    standard_search: StandardSearch | None = None,
    run_tracker: DrawingAgentRunTracker | None = None,
) -> dict[str, Any]:
    if evidence_extractor is None or bill_summarizer is None:
        raise PdfAgentItemizationError("PDF Agent 需要配置模型适配器；请使用 OpenAI/DashScope 入口或注入测试适配器。")

    run_timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    source_dir = Path(pdf_dir)
    target_dir = Path(output_dir)
    debug_dir = target_dir / "debug" / run_timestamp
    business_dir = target_dir / "business" / run_timestamp
    debug_dir.mkdir(parents=True, exist_ok=True)
    business_dir.mkdir(parents=True, exist_ok=True)
    tracker = run_tracker or create_pdf_agent_run_tracker(
        output_dir=target_dir,
        run_id=run_timestamp,
        input_dir=source_dir,
        provider="pdf_agent",
    )
    tracker.bind_artifact_dirs(debug_dir=debug_dir, business_dir=business_dir)

    tracker.update("rendering_pdf", progress=5, detail={"pdf_dir": str(source_dir), "render_dpi": render_dpi})
    pdf_files = collect_pdf_files(source_dir)
    if not pdf_files:
        raise PdfAgentItemizationError("没有找到可识别的 .pdf 文件")

    parse_report = build_pdf_basic_parse_report(pdf_files)
    render_report = build_pdf_render_report(
        parse_report,
        debug_dir / f"pages_{run_timestamp}",
        render_dpi=render_dpi,
    )
    tracker.update(
        "detecting_layout",
        progress=15,
        detail={
            "pdf_file_count": len(pdf_files),
            "rendered_page_count": len(render_report.get("render_rows") or []),
        },
    )
    tile_report = build_pdf_tile_report(
        parse_report=parse_report,
        render_report=render_report,
        tile_dir=debug_dir / f"tiles_{run_timestamp}",
        grid_size=tile_grid_size,
    )
    cad_view_report = build_cad_view_frame_report(
        parse_report=parse_report,
        render_report=render_report,
        view_dir=debug_dir / f"cad_views_{run_timestamp}",
    )
    tile_report = augment_tile_report_with_cad_views(tile_report, cad_view_report)
    tracker.update(
        "planning_views",
        progress=25,
        detail={
            "tile_count": len(tile_report.get("tile_rows") or []),
            "cad_view_count": len(cad_view_report.get("cad_view_rows") or []),
            "max_views": max_views,
        },
    )
    selected_views = select_agent_views(
        list(tile_report.get("tile_rows") or []),
        max_views=max_views,
        include_whole_page=include_whole_page,
    )
    if not selected_views:
        raise PdfAgentItemizationError("PDF Agent 没有选出可识别视图")

    tracker.update(
        "planning_regions",
        progress=30,
        detail={
            "layout_planner": "configured" if layout_planner is not None else "skipped",
            "max_pages": settings.drawing_layout_planner_max_pages,
        },
    )
    layout_plan_report = build_pdf_layout_plan_report(
        render_report=render_report,
        planner_dir=tracker.run_dir / "layout",
        layout_planner=layout_planner,
        max_pages=settings.drawing_layout_planner_max_pages,
    )
    region_crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=layout_plan_report,
        crop_dir=tracker.run_dir / "region_crops",
        max_regions=settings.drawing_region_crop_max_regions,
    )
    cad_view_detail_plan_report = build_cad_view_detail_plan_report(
        render_report=render_report,
        cad_view_report=cad_view_report,
        planner_dir=tracker.run_dir / "cad_view_detail_plan",
        view_region_planner=cad_view_detail_planner,
        max_views=settings.drawing_cad_view_detail_max_views,
    )
    cad_view_detail_crop_report = build_region_crop_report(
        render_report=render_report,
        layout_plan_report=cad_view_detail_plan_report,
        crop_dir=tracker.run_dir / "cad_view_detail_crops",
        max_regions=settings.drawing_cad_view_detail_max_regions,
        min_area_ratio=0.00002,
        iou_threshold=0.92,
    )
    cad_view_detail_highres_report: Mapping[str, Any] = {}
    if settings.drawing_highres_region_render_enabled:
        cad_view_detail_highres_report = build_highres_region_render_report(
            parse_report=parse_report,
            layout_plan_report=cad_view_detail_plan_report,
            output_dir=tracker.run_dir / "cad_view_detail_highres_crops",
            max_regions=settings.drawing_cad_view_detail_max_regions,
            default_scale=settings.drawing_highres_region_default_scale,
            max_scale=settings.drawing_highres_region_max_scale,
            max_pixels=settings.drawing_highres_region_max_pixels,
            min_width_px=settings.drawing_highres_region_min_width_px,
            min_height_px=settings.drawing_highres_region_min_height_px,
        )
    tracker.update(
        "planning_regions",
        progress=34,
        detail={
            **(layout_plan_report.get("summary") or {}),
            **(region_crop_report.get("summary") or {}),
            **(cad_view_detail_plan_report.get("summary") or {}),
            "cad_view_detail_crop_count": (cad_view_detail_crop_report.get("summary") or {}).get("region_crop_count", 0),
            "cad_view_detail_highres_crop_count": (cad_view_detail_highres_report.get("summary") or {}).get("highres_crop_count", 0),
        },
    )

    tracker.update(
        "running_ocr",
        progress=35,
        detail={"selected_view_count": len(selected_views), "engine": "paddleocr"},
    )
    cad_view_detail_ocr_crops = (
        (cad_view_detail_highres_report.get("crop_manifest") or [])
        if settings.drawing_highres_region_use_for_ocr and cad_view_detail_highres_report
        else (cad_view_detail_crop_report.get("crop_manifest") or [])
    )
    ocr_report = build_pdf_agent_ocr_report(
        render_report=render_report,
        crop_dir=tracker.run_dir / "crops",
        ocr_dir=tracker.run_dir / "ocr",
        context_dir=tracker.run_dir / "context",
        extra_crop_manifest=[
            *(region_crop_report.get("crop_manifest") or []),
            *cad_view_detail_ocr_crops,
        ],
    )
    tracker.update(
        "running_ocr",
        progress=45,
        detail=ocr_report.get("summary") or {},
    )

    report = build_agent_itemization_report_from_views(
        selected_views,
        evidence_extractor=evidence_extractor,
        bill_summarizer=bill_summarizer,
        standard_search=standard_search,
        run_tracker=tracker,
        ocr_report=ocr_report,
        layout_plan_report=layout_plan_report,
        region_crop_report=region_crop_report,
        cad_view_detail_plan_report=cad_view_detail_plan_report,
        cad_view_detail_crop_report=cad_view_detail_crop_report,
        cad_view_detail_highres_report=cad_view_detail_highres_report,
    )
    report.update(
        {
            "parse_report": parse_report,
            "render_report": render_report,
            "tile_report": tile_report,
            "cad_view_report": cad_view_report,
            "ocr_report": ocr_report,
            "layout_plan_report": layout_plan_report,
            "region_crop_report": region_crop_report,
            "cad_view_detail_plan_report": cad_view_detail_plan_report,
            "cad_view_detail_crop_report": cad_view_detail_crop_report,
            "cad_view_detail_highres_report": cad_view_detail_highres_report,
        }
    )
    tracker.update("exporting", progress=98, detail={"quantity_row_count": len(report.get("quantity_list_rows") or [])})
    report["outputs"] = write_pdf_agent_itemization_outputs(
        report,
        business_dir=business_dir,
        debug_dir=debug_dir,
        run_timestamp=run_timestamp,
    )
    report["outputs"].update(_ocr_output_paths(ocr_report))
    report["outputs"].update(_generic_output_paths(layout_plan_report))
    report["outputs"].update(_generic_output_paths(region_crop_report))
    report["outputs"].update(_generic_output_paths(cad_view_detail_plan_report))
    report["outputs"].update(_generic_output_paths(cad_view_detail_crop_report))
    report["outputs"].update(_generic_output_paths(cad_view_detail_highres_report))
    report["outputs"]["agent_run_state_json"] = str(tracker.state_path.resolve())
    report["outputs"]["agent_run_events_jsonl"] = str(tracker.events_path.resolve())
    report["agent_run"] = tracker.snapshot()
    completion_status = pdf_agent_completion_status(report)
    if completion_status == "failed_with_report":
        tracker.mark_report_failure(
            error_code="NO_VALID_AGENT_OUTPUT",
            message="PDF agent finished without usable quantity list",
            report=report,
        )
    else:
        tracker.complete(
            status=completion_status,
            summary=report.get("summary") or {},
            outputs=report.get("outputs") or {},
            issues=list(report.get("issues") or []),
        )
    report["agent_run"] = tracker.snapshot()
    return report


def run_pdf_agent_itemization_openai(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    render_dpi: int = 350,
    tile_grid_size: int = 3,
    max_views: int | None = None,
    include_whole_page: bool | None = None,
    username: str | None = None,
    trace_id: str | None = None,
    standard_search: StandardSearch | None = None,
    run_tracker: DrawingAgentRunTracker | None = None,
) -> dict[str, Any]:
    return run_pdf_agent_itemization(
        pdf_dir=pdf_dir,
        output_dir=output_dir,
        timestamp=timestamp,
        render_dpi=render_dpi,
        tile_grid_size=tile_grid_size,
        max_views=settings.openai_drawing_agent_max_views if max_views is None else int(max_views),
        include_whole_page=(
            settings.openai_drawing_agent_include_whole_page if include_whole_page is None else bool(include_whole_page)
        ),
        evidence_extractor=lambda view_manifest: call_openai_agent_evidence_extractor(
            view_manifest,
            username=username,
            trace_id=trace_id,
        ),
        bill_summarizer=lambda merged_evidence: call_openai_agent_bill_summarizer(
            merged_evidence,
            username=username,
            trace_id=trace_id,
        ),
        standard_search=standard_search,
        run_tracker=run_tracker,
    )


def run_pdf_agent_itemization_dashscope(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    render_dpi: int = 350,
    tile_grid_size: int = 3,
    max_views: int | None = None,
    include_whole_page: bool | None = None,
    username: str | None = None,
    trace_id: str | None = None,
    standard_search: StandardSearch | None = None,
    run_tracker: DrawingAgentRunTracker | None = None,
) -> dict[str, Any]:
    return run_pdf_agent_itemization(
        pdf_dir=pdf_dir,
        output_dir=output_dir,
        timestamp=timestamp,
        render_dpi=render_dpi,
        tile_grid_size=tile_grid_size,
        max_views=settings.dashscope_drawing_agent_max_views if max_views is None else int(max_views),
        include_whole_page=(
            settings.dashscope_drawing_agent_include_whole_page if include_whole_page is None else bool(include_whole_page)
        ),
        layout_planner=lambda page_manifest: call_dashscope_agent_layout_planner(
            page_manifest,
            username=username,
            trace_id=trace_id,
        ),
        cad_view_detail_planner=lambda view_manifest: call_dashscope_agent_cad_view_detail_planner(
            view_manifest,
            username=username,
            trace_id=trace_id,
        ),
        evidence_extractor=lambda view_manifest: call_dashscope_agent_evidence_extractor(
            view_manifest,
            username=username,
            trace_id=trace_id,
        ),
        bill_summarizer=lambda merged_evidence: call_dashscope_agent_bill_summarizer(
            merged_evidence,
            username=username,
            trace_id=trace_id,
        ),
        standard_search=standard_search,
        run_tracker=run_tracker,
    )


def call_openai_agent_evidence_extractor(
    view_manifest: list[dict[str, Any]],
    *,
    username: str | None = None,
    trace_id: str | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_openai_pdf_agent_evidence_extract

    prepared = prepare_openai_agent_view_payloads(view_manifest)
    if not prepared:
        return {"drawing_evidence": []}
    size = max(1, int(batch_size or settings.openai_drawing_agent_batch_size or len(prepared)))
    all_rows: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(_chunks(prepared, size), start=1):
        result = _run_async_model_call(
            call_openai_pdf_agent_evidence_extract(
                batch,
                username=username,
                trace_id=f"{trace_id}-evidence-b{batch_index}" if trace_id else None,
            )
        )
        rows = result.get("drawing_evidence") if isinstance(result, Mapping) else []
        if isinstance(rows, list):
            all_rows.extend([dict(row) for row in rows if isinstance(row, Mapping)])
    return {"drawing_evidence": all_rows}


def call_openai_agent_bill_summarizer(
    merged_evidence: Mapping[str, Any],
    *,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_openai_pdf_agent_bill_summarize

    return _run_async_model_call(
        call_openai_pdf_agent_bill_summarize(
            dict(merged_evidence),
            username=username,
            trace_id=f"{trace_id}-bill" if trace_id else None,
        )
    )


def call_dashscope_agent_layout_planner(
    page_manifest: list[dict[str, Any]],
    *,
    username: str | None = None,
    trace_id: str | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_dashscope_drawing_layout_plan

    prepared = prepare_openai_agent_view_payloads(page_manifest)
    if not prepared:
        return {"regions": []}
    return _run_async_model_call(
        call_dashscope_drawing_layout_plan(
            prepared,
            model_override=model_override,
            username=username,
            trace_id=f"{trace_id}-layout" if trace_id else None,
        )
    )


def call_dashscope_agent_cad_view_detail_planner(
    view_manifest: list[dict[str, Any]],
    *,
    username: str | None = None,
    trace_id: str | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_dashscope_cad_view_detail_plan

    prepared = prepare_openai_agent_view_payloads(view_manifest)
    if not prepared:
        return {"regions": []}
    return _run_async_model_call(
        call_dashscope_cad_view_detail_plan(
            prepared,
            model_override=model_override,
            username=username,
            trace_id=f"{trace_id}-cad-view-detail" if trace_id else None,
        )
    )


def call_dashscope_agent_evidence_extractor(
    view_manifest: list[dict[str, Any]],
    *,
    username: str | None = None,
    trace_id: str | None = None,
    batch_size: int | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_dashscope_pdf_agent_evidence_extract

    prepared = prepare_openai_agent_view_payloads(view_manifest)
    if not prepared:
        return {"drawing_evidence": []}
    size = max(1, int(batch_size or settings.dashscope_drawing_agent_batch_size or len(prepared)))
    all_rows: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(_chunks(prepared, size), start=1):
        result = _run_async_model_call(
            call_dashscope_pdf_agent_evidence_extract(
                batch,
                model_override=model_override,
                username=username,
                trace_id=f"{trace_id}-evidence-b{batch_index}" if trace_id else None,
            )
        )
        rows = result.get("drawing_evidence") if isinstance(result, Mapping) else []
        if isinstance(rows, list):
            all_rows.extend([dict(row) for row in rows if isinstance(row, Mapping)])
    return {"drawing_evidence": all_rows}


def call_dashscope_agent_bill_summarizer(
    merged_evidence: Mapping[str, Any],
    *,
    username: str | None = None,
    trace_id: str | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    from app.services.model_gateway import call_dashscope_pdf_agent_bill_summarize

    return _run_async_model_call(
        call_dashscope_pdf_agent_bill_summarize(
            dict(merged_evidence),
            model_override=model_override,
            username=username,
            trace_id=f"{trace_id}-bill" if trace_id else None,
        )
    )


def prepare_openai_agent_view_payloads(view_manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for view in view_manifest:
        image_path = Path(str(view.get("image_path") or ""))
        if not image_path.exists() or not image_path.is_file():
            continue
        payload = dict(view)
        payload["mime_type"] = _image_mime_type(image_path)
        payload["image_base64"] = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payloads.append(payload)
    return payloads


def build_agent_itemization_report_from_views(
    selected_views: list[dict[str, Any]],
    *,
    evidence_extractor: AgentEvidenceExtractor,
    bill_summarizer: AgentBillSummarizer,
    standard_search: StandardSearch | None = None,
    run_tracker: DrawingAgentRunTracker | None = None,
    ocr_report: Mapping[str, Any] | None = None,
    layout_plan_report: Mapping[str, Any] | None = None,
    region_crop_report: Mapping[str, Any] | None = None,
    cad_view_detail_plan_report: Mapping[str, Any] | None = None,
    cad_view_detail_crop_report: Mapping[str, Any] | None = None,
    cad_view_detail_highres_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    issues: list[dict[str, Any]] = []
    view_manifest = build_view_manifest(selected_views)

    evidence_rows: list[dict[str, Any]] = []
    if run_tracker is not None:
        run_tracker.update(
            "running_vision",
            progress=50,
            detail={
                "selected_view_count": len(view_manifest),
                "context_view_count": sum(1 for row in view_manifest if _is_context_manifest_view(row)),
            },
        )
    try:
        evidence_payload = evidence_extractor(view_manifest)
        evidence_rows = parse_agent_evidence_json(evidence_payload)
    except Exception as exc:  # noqa: BLE001 - model payloads need defensive capture
        issues.append({"级别": "warning", "说明": f"Agent 证据抽取失败：{exc}"})

    if not evidence_rows:
        issues.append(
            {
                "level": "warning",
                "code": "NO_AGENT_EVIDENCE",
                "message": "Agent evidence extraction returned no usable rows",
            }
        )
    if run_tracker is not None:
        run_tracker.update("building_context", progress=75, detail={"agent_evidence_count": len(evidence_rows)})

    merged_evidence = merge_agent_evidence(evidence_rows, view_manifest=view_manifest, ocr_report=ocr_report)
    bill_items: list[dict[str, Any]] = []
    if run_tracker is not None:
        run_tracker.update("generating_items", progress=82, detail={"evidence_count": len(evidence_rows)})
    try:
        bill_payload = bill_summarizer(merged_evidence)
        bill_items = parse_agent_bill_items_json(bill_payload)
    except Exception as exc:  # noqa: BLE001 - model payloads need defensive capture
        issues.append({"级别": "warning", "说明": f"Agent 清单归纳失败：{exc}"})

    if not bill_items:
        issues.append(
            {
                "level": "warning",
                "code": "NO_AGENT_BILL_ITEMS",
                "message": "Agent bill summarizer returned no usable items",
            }
        )
    if run_tracker is not None:
        run_tracker.update("mapping_standards", progress=90, detail={"agent_bill_item_count": len(bill_items)})

    itemizability_report = classify_agent_bill_items(bill_items)
    mappable_bill_items = list(itemizability_report.get("mappable_items") or [])
    mapping_rows = build_agent_standard_mapping_rows(
        mappable_bill_items,
        standard_search=standard_search,
    )
    quantity_rows = build_agent_four_field_rows(mapping_rows)
    if run_tracker is not None:
        run_tracker.update(
            "quality_review",
            progress=96,
            detail={
                "mappable_count": len(mappable_bill_items),
                "standard_mapping_count": len(mapping_rows),
                "quantity_list_row_count": len(quantity_rows),
                "issue_count": len(issues),
            },
        )
    return {
        "ok": not issues or bool(quantity_rows),
        "phase": PHASE,
        "generated_at": generated_at,
        "source_mode": PDF_AGENT_SOURCE_MODE,
        "summary": build_agent_summary(
            view_manifest=view_manifest,
            evidence_rows=evidence_rows,
            bill_items=bill_items,
            itemizability_report=itemizability_report,
            mapping_rows=mapping_rows,
            quantity_rows=quantity_rows,
            ocr_report=ocr_report,
            layout_plan_report=layout_plan_report,
            region_crop_report=region_crop_report,
            cad_view_detail_plan_report=cad_view_detail_plan_report,
            cad_view_detail_crop_report=cad_view_detail_crop_report,
            cad_view_detail_highres_report=cad_view_detail_highres_report,
        ),
        "view_manifest": view_manifest,
        "agent_evidence_rows": evidence_rows,
        "merged_evidence": merged_evidence,
        "agent_bill_items": bill_items,
        "agent_itemizability_rows": itemizability_report.get("all_items") or [],
        "agent_filtered_items": itemizability_report.get("filtered_items") or [],
        "agent_manual_review_items": itemizability_report.get("manual_review_items") or [],
        "standard_mapping_rows": mapping_rows,
        "quantity_list_rows": quantity_rows,
        "layout_plan_report": dict(layout_plan_report or {}),
        "region_crop_report": dict(region_crop_report or {}),
        "cad_view_detail_plan_report": dict(cad_view_detail_plan_report or {}),
        "cad_view_detail_crop_report": dict(cad_view_detail_crop_report or {}),
        "cad_view_detail_highres_report": dict(cad_view_detail_highres_report or {}),
        "ocr_report": dict(ocr_report or {}),
        "issues": issues,
        "outputs": {},
    }


def select_agent_views(
    tile_rows: list[dict[str, Any]],
    *,
    max_views: int,
    include_whole_page: bool = True,
) -> list[dict[str, Any]]:
    if max_views <= 0:
        return []
    rendered = [
        row
        for row in tile_rows
        if row.get("image_path")
        and Path(str(row.get("image_path"))).exists()
        and Path(str(row.get("image_path"))).is_file()
    ]
    whole_pages = [row for row in rendered if row.get("tile_type") == "whole_page_preview"]
    cad_views = [row for row in rendered if row.get("tile_type") == CAD_VIEW_TILE_TYPE]
    grid_tiles = [row for row in rendered if row.get("tile_type") == "grid"]

    whole_pages.sort(key=lambda row: (_int(row.get("page")), str(row.get("source_file") or ""), str(row.get("tile_id") or "")))
    cad_views.sort(key=lambda row: (_int(row.get("page")), str(row.get("source_file") or ""), str(row.get("tile_id") or "")))
    grid_tiles.sort(
        key=lambda row: (
            -_int(row.get("priority")),
            -_image_file_size(row.get("image_path")),
            _int(row.get("page")),
            str(row.get("source_file") or ""),
            str(row.get("tile_id") or ""),
        )
    )

    selected: list[dict[str, Any]] = []
    if include_whole_page:
        selected.extend(_tag_agent_view(row, WHOLE_PAGE_CONTEXT_ROLE) for row in whole_pages[:1])
    remaining = max_views - len(selected)
    if remaining <= 0:
        return selected[:max_views]

    selected_identities = {_agent_tile_identity(row) for row in selected}
    context_grid_limit = _context_grid_limit(
        max_views=max_views,
        selected_count=len(selected),
        cad_view_count=len(cad_views),
        grid_tile_count=len(grid_tiles),
    )
    if context_grid_limit:
        context_grid_tiles = [
            row for row in sorted(grid_tiles, key=_context_grid_sort_key) if _agent_tile_identity(row) not in selected_identities
        ][:context_grid_limit]
        selected.extend(_tag_agent_view(row, PAGE_CONTEXT_ROLE) for row in context_grid_tiles)
        selected_identities.update(_agent_tile_identity(row) for row in context_grid_tiles)
        remaining = max_views - len(selected)
        if remaining <= 0:
            return selected[:max_views]

    if cad_views:
        selected.extend(
            _tag_agent_view(row, LOCAL_CAD_VIEW_ROLE)
            for row in cad_views
            if _agent_tile_identity(row) not in selected_identities
        )
        return selected[:max_views]

    selected.extend(
        _tag_agent_view(row, LOCAL_GRID_VIEW_ROLE)
        for row in grid_tiles
        if _agent_tile_identity(row) not in selected_identities
    )
    return selected[:max_views]


def _tag_agent_view(row: Mapping[str, Any], selection_role: str) -> dict[str, Any]:
    tagged = dict(row)
    tagged["selection_role"] = selection_role
    return tagged


def _agent_tile_identity(row: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        _clean_text(row.get("source_file")),
        _int(row.get("page")),
        _clean_text(row.get("tile_id")),
    )


def _context_grid_limit(
    *,
    max_views: int,
    selected_count: int,
    cad_view_count: int,
    grid_tile_count: int,
) -> int:
    if max_views < 6 or cad_view_count <= 0 or grid_tile_count <= 0:
        return 0
    # Reserve a small context budget while keeping at least one local CAD view.
    available_after_context = max_views - selected_count
    if available_after_context <= 1:
        return 0
    return min(4, max(1, max_views // 5), grid_tile_count, available_after_context - 1)


def _context_grid_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    bbox = _bbox_numbers(row.get("bbox_pixel"))
    x1, y1, x2, y2 = bbox
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return (
        _int(row.get("page")),
        str(row.get("source_file") or ""),
        -x1,
        -y1,
        -area,
        str(row.get("tile_id") or ""),
    )


def _bbox_numbers(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return [0.0, 0.0, 0.0, 0.0]
    numbers = [_float(item, 0.0) for item in list(value)[:4]]
    while len(numbers) < 4:
        numbers.append(0.0)
    return numbers


def build_view_manifest(selected_views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for index, row in enumerate(selected_views, start=1):
        tile_id = _clean_text(row.get("tile_id")) or f"view{index:03d}"
        manifest.append(
            {
                "view_id": tile_id,
                "view_order": index,
                "source_file": _clean_text(row.get("source_file")),
                "page": _int(row.get("page")),
                "tile_type": _clean_text(row.get("tile_type")) or "unknown",
                "selection_role": _clean_text(row.get("selection_role")),
                "image_path": str(Path(str(row.get("image_path") or "")).resolve()),
                "bbox_pixel": list(row.get("bbox_pixel") or []),
                "bbox_pdf": list(row.get("bbox_pdf") or []),
                "priority": _int(row.get("priority")),
                "status": _clean_text(row.get("status")),
            }
        )
    return manifest


def parse_agent_evidence_json(payload: Mapping[str, Any] | str) -> list[dict[str, Any]]:
    data = _coerce_json_object(payload)
    raw_rows = data.get("drawing_evidence") if isinstance(data, Mapping) else []
    if isinstance(raw_rows, Mapping):
        raw_rows = [raw_rows]
    if not isinstance(raw_rows, list):
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        row = {
            "evidence_id": _clean_text(raw.get("evidence_id")) or f"PDFAGEV-{index:06d}",
            "view_id": _clean_text(raw.get("view_id")),
            "view_title": _clean_text(raw.get("view_title")),
            "view_type": _clean_text(raw.get("view_type")),
            "spaces": _clean_text_list(raw.get("spaces")),
            "visible_texts": _clean_text_list(raw.get("visible_texts")),
            "material_codes": _normalize_material_codes(raw.get("material_codes")),
            "objects": _normalize_objects(raw.get("objects")),
            "methods": _clean_text_list(raw.get("methods")),
            "quantity_clues": _normalize_quantity_clues(raw.get("quantity_clues")),
            "evidence_notes": _clean_text_list(raw.get("evidence_notes")),
            "confidence": _float(raw.get("confidence"), 0.0),
            "needs_manual_review": _bool(raw.get("needs_manual_review"), default=True),
        }
        rows.append(row)
    return rows


def merge_agent_evidence(
    evidence_rows: list[dict[str, Any]],
    *,
    view_manifest: list[dict[str, Any]] | None = None,
    ocr_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_views = _source_views_from_manifest(view_manifest or [])
    materials: dict[str, dict[str, Any]] = {}
    objects: dict[str, dict[str, Any]] = {}
    methods: dict[str, dict[str, Any]] = {}
    quantity_clues: list[dict[str, Any]] = []
    visible_texts: dict[str, dict[str, Any]] = {}

    for row in evidence_rows:
        view_id = _clean_text(row.get("view_id"))
        if view_id:
            current = source_views.setdefault(
                view_id,
                {
                    "view_id": view_id,
                    "view_title": "",
                    "view_type": "",
                    "spaces": [],
                    "evidence_count": 0,
                },
            )
            current["view_title"] = current.get("view_title") or _clean_text(row.get("view_title"))
            current["view_type"] = current.get("view_type") or _clean_text(row.get("view_type"))
            current["spaces"] = _merge_unique_lists(current.get("spaces"), row.get("spaces"))
            current["evidence_count"] = _int(current.get("evidence_count")) + 1

        for material in row.get("material_codes") or []:
            code = _clean_text(material.get("code")) if isinstance(material, Mapping) else ""
            key = _normalize(code or material.get("name_or_hint") if isinstance(material, Mapping) else "")
            if not key:
                continue
            current = materials.setdefault(
                key,
                {
                    "code": code,
                    "name_or_hint": "",
                    "spec_or_method": "",
                    "source_view_ids": [],
                    "confidence": 0.0,
                },
            )
            current["code"] = current.get("code") or code
            current["name_or_hint"] = _join_unique([current.get("name_or_hint"), material.get("name_or_hint")])
            current["spec_or_method"] = _join_unique([current.get("spec_or_method"), material.get("spec_or_method")])
            current["source_view_ids"] = _merge_unique_lists(current.get("source_view_ids"), [view_id])
            current["confidence"] = max(_float(current.get("confidence"), 0), _float(material.get("confidence"), 0))

        for obj in row.get("objects") or []:
            if not isinstance(obj, Mapping):
                continue
            name = _clean_text(obj.get("name"))
            if not name:
                continue
            key = "|".join([_normalize(name), _normalize(obj.get("space")), _normalize(obj.get("method"))])
            current = objects.setdefault(
                key,
                {
                    "name": name,
                    "space": "",
                    "method": "",
                    "unit_hint": "",
                    "source_view_ids": [],
                    "confidence": 0.0,
                },
            )
            current["space"] = _join_unique([current.get("space"), obj.get("space")])
            current["method"] = _join_unique([current.get("method"), obj.get("method")])
            current["unit_hint"] = current.get("unit_hint") or _clean_text(obj.get("unit_hint"))
            current["source_view_ids"] = _merge_unique_lists(current.get("source_view_ids"), [view_id])
            current["confidence"] = max(_float(current.get("confidence"), 0), _float(obj.get("confidence"), 0))

        for method in row.get("methods") or []:
            text = _clean_text(method)
            key = _normalize(text)
            if not key:
                continue
            current = methods.setdefault(
                key,
                {
                    "method": text,
                    "source_view_ids": [],
                    "evidence_count": 0,
                },
            )
            current["source_view_ids"] = _merge_unique_lists(current.get("source_view_ids"), [view_id])
            current["evidence_count"] = _int(current.get("evidence_count")) + 1

        for clue in row.get("quantity_clues") or []:
            if not isinstance(clue, Mapping):
                continue
            quantity_clues.append(
                {
                    "source_view_id": view_id,
                    "text": _clean_text(clue.get("text")),
                    "meaning": _clean_text(clue.get("meaning")),
                    "confidence": _float(clue.get("confidence"), 0),
                }
            )

        for text in row.get("visible_texts") or []:
            normalized = _normalize(text)
            if not normalized:
                continue
            current = visible_texts.setdefault(
                normalized,
                {"text": _clean_text(text), "source_view_ids": []},
            )
            current["source_view_ids"] = _merge_unique_lists(current.get("source_view_ids"), [view_id])

    ocr_report = ocr_report or {}
    for material in ocr_report.get("material_legend_candidates") or []:
        if not isinstance(material, Mapping):
            continue
        code = _clean_text(material.get("code"))
        key = _normalize(code or material.get("name_or_hint"))
        if not key:
            continue
        current = materials.setdefault(
            key,
            {
                "code": code,
                "name_or_hint": "",
                "spec_or_method": "",
                "source_view_ids": [],
                "source_crop_ids": [],
                "source_region_ids": [],
                "source_region_types": [],
                "source_texts": [],
                "confidence": 0.0,
            },
        )
        current["code"] = current.get("code") or code
        current["name_or_hint"] = _join_unique([current.get("name_or_hint"), material.get("name_or_hint")])
        current["spec_or_method"] = _join_unique([current.get("spec_or_method"), material.get("spec_or_method")])
        current["source_crop_ids"] = _merge_unique_lists(current.get("source_crop_ids"), material.get("source_crop_ids"))
        current["source_region_ids"] = _merge_unique_lists(current.get("source_region_ids"), material.get("source_region_ids"))
        current["source_region_types"] = _merge_unique_lists(
            current.get("source_region_types"),
            material.get("source_region_types"),
        )
        current["source_texts"] = _merge_unique_lists(current.get("source_texts"), material.get("source_texts"))
        current["confidence"] = max(_float(current.get("confidence"), 0), _float(material.get("confidence"), 0))

    for ocr_row in ocr_report.get("ocr_rows") or []:
        if not isinstance(ocr_row, Mapping):
            continue
        text = _clean_text(ocr_row.get("text"))
        normalized = _normalize(text)
        if not normalized:
            continue
        current = visible_texts.setdefault(
            normalized,
            {"text": text, "source_view_ids": [], "source_crop_ids": []},
        )
        current["source_crop_ids"] = _merge_unique_lists(current.get("source_crop_ids"), [ocr_row.get("crop_id")])

    return {
        "phase": "pdf-agent-evidence-merge",
        "view_count": len(source_views),
        "evidence_count": len(evidence_rows),
        "source_views": list(source_views.values()),
        "merged_materials": list(materials.values()),
        "merged_objects": list(objects.values()),
        "merged_methods": sorted(methods.values(), key=lambda item: (-_int(item.get("evidence_count")), item.get("method", ""))),
        "quantity_clues": quantity_clues,
        "visible_texts": list(visible_texts.values()),
        "global_context": _build_agent_global_context(evidence_rows, source_views, ocr_report=ocr_report),
    }


def parse_agent_bill_items_json(payload: Mapping[str, Any] | str) -> list[dict[str, Any]]:
    data = _coerce_json_object(payload)
    raw_rows = data.get("bill_items") if isinstance(data, Mapping) else []
    if isinstance(raw_rows, Mapping):
        raw_rows = [raw_rows]
    if not isinstance(raw_rows, list):
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "item_id": _clean_text(raw.get("item_id")) or f"PDFAGITEM-{index:06d}",
                "concrete_item_name": _clean_text(raw.get("concrete_item_name")),
                "feature": _clean_text(raw.get("feature")),
                "unit": _clean_text(raw.get("unit")),
                "rough_quantity": _clean_text(raw.get("rough_quantity")),
                "quantity_note": _clean_text(raw.get("quantity_note")),
                "source_view_ids": _clean_text_list(raw.get("source_view_ids")),
                "source_evidence": _clean_text_list(raw.get("source_evidence")),
                "confidence": _float(raw.get("confidence"), 0.0),
                "needs_manual_review": _bool(raw.get("needs_manual_review"), default=True),
                "reason": _clean_text(raw.get("reason")),
                "itemizability_status": _clean_text(
                    raw.get("itemizability_status")
                    or raw.get("itemizability")
                    or raw.get("listing_status")
                    or raw.get("billability_status")
                ),
                "itemizability_reason": _clean_text(
                    raw.get("itemizability_reason")
                    or raw.get("listing_reason")
                    or raw.get("billability_reason")
                ),
            }
        )
    return rows


def classify_agent_bill_items(bill_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    all_items: list[dict[str, Any]] = []
    mappable_items: list[dict[str, Any]] = []
    filtered_items: list[dict[str, Any]] = []
    manual_review_items: list[dict[str, Any]] = []
    for item in bill_items:
        classified = dict(item)
        decision = _classify_agent_bill_item(item)
        classified.update(decision)
        all_items.append(classified)
        action = classified.get("itemizability_action")
        if action == "standard_mapping":
            mappable_items.append(classified)
        elif action == "filtered_non_construction":
            filtered_items.append(classified)
        else:
            manual_review_items.append(classified)
    return {
        "all_items": all_items,
        "mappable_items": mappable_items,
        "filtered_items": filtered_items,
        "manual_review_items": manual_review_items,
    }


def _classify_agent_bill_item(item: Mapping[str, Any]) -> dict[str, str]:
    explicit_status = _normalize_itemizability_status(item.get("itemizability_status"))
    text = _agent_item_search_text(item)
    normalized_text = _normalize(text)
    if explicit_status:
        if explicit_status in {"施工项", "安装项", "定制项"}:
            return {
                "itemizability_status": explicit_status,
                "itemizability_action": "standard_mapping",
                "itemizability_reason": _clean_text(item.get("itemizability_reason")) or f"模型判断为{explicit_status}",
            }
        if explicit_status == "非施工项":
            return {
                "itemizability_status": explicit_status,
                "itemizability_action": "filtered_non_construction",
                "itemizability_reason": _clean_text(item.get("itemizability_reason")) or "模型判断为非施工项",
            }
        return {
            "itemizability_status": "待确认项",
            "itemizability_action": "manual_review_required",
            "itemizability_reason": _clean_text(item.get("itemizability_reason")) or "模型判断需要人工确认",
        }

    has_fixed_or_custom = any(
        token in text
        for token in (
            "固定",
            "定制",
            "制作",
            "制安",
            "采购安装",
            "成品柜",
            "吧台",
            "售卖台",
            "售卖口",
            "操作台",
            "台面",
        )
    )
    has_installation = any(token in text for token in ("安装", "门窗", "门套", "隔断", "洗手台", "洗脸盆", "洁具", "玻璃"))
    has_construction_method = any(
        token in text
        for token in (
            "铺贴",
            "湿贴",
            "干挂",
            "吊顶",
            "天棚",
            "天花",
            "涂料",
            "乳胶漆",
            "防水",
            "美缝",
            "拆除",
            "收边",
            "踢脚",
            "找平",
            "基层",
            "龙骨",
            "地面",
            "墙面",
            "石材",
            "瓷砖",
            "地砖",
            "块料",
        )
    )
    has_loose_furniture = any(
        token in text
        for token in (
            "餐桌",
            "餐椅",
            "桌椅",
            "沙发",
            "茶几",
            "活动家具",
            "摆放",
            "布置",
            "绿植",
            "摆件",
            "装饰画",
            "挂画",
        )
    )
    if has_loose_furniture and not has_fixed_or_custom and not has_installation and not has_construction_method:
        return {
            "itemizability_status": "非施工项",
            "itemizability_action": "filtered_non_construction",
            "itemizability_reason": "识别为活动家具或陈设物，进入非施工项过滤",
        }
    if has_fixed_or_custom:
        return {
            "itemizability_status": "定制项",
            "itemizability_action": "standard_mapping",
            "itemizability_reason": "包含固定、定制、制作或成品台柜类线索，进入国标匹配",
        }
    if has_installation:
        return {
            "itemizability_status": "安装项",
            "itemizability_action": "standard_mapping",
            "itemizability_reason": "包含安装、门窗、隔断、洁具或玻璃类线索，进入国标匹配",
        }
    if has_construction_method:
        return {
            "itemizability_status": "施工项",
            "itemizability_action": "standard_mapping",
            "itemizability_reason": "包含铺贴、吊顶、涂料、防水、拆除等施工做法线索，进入国标匹配",
        }
    if any(token in normalized_text for token in ("家具", "窗帘", "软装", "设备")):
        return {
            "itemizability_status": "待确认项",
            "itemizability_action": "manual_review_required",
            "itemizability_reason": "识别内容可能属于软装、设备或家具采购，需要人工确认是否进入工程清单",
        }
    return {
        "itemizability_status": "待确认项",
        "itemizability_action": "manual_review_required",
        "itemizability_reason": "缺少明确施工、安装或定制线索，保留为待确认项",
    }


def _normalize_itemizability_status(value: Any) -> str:
    text = _clean_text(value)
    normalized = _normalize(text)
    if not normalized:
        return ""
    if "非施工" in text or "不列" in text or "nonconstruction" in normalized or "exclude" in normalized:
        return "非施工项"
    if "待确认" in text or "人工确认" in text or "manualreview" in normalized:
        return "待确认项"
    if "定制" in text or "制作" in text or "custom" in normalized:
        return "定制项"
    if "安装" in text or "install" in normalized:
        return "安装项"
    if "施工" in text or "工程" in text or "construction" in normalized:
        return "施工项"
    return ""


def build_agent_standard_mapping_rows(
    bill_items: list[dict[str, Any]],
    *,
    standard_search: StandardSearch | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    searcher = standard_search or search_standard_index
    for index, item in enumerate(bill_items, start=1):
        query = _join_unique(
            [
                item.get("concrete_item_name"),
                item.get("feature"),
                " ".join(item.get("source_evidence") or []),
                " ".join(item.get("source_view_ids") or []),
                item.get("unit"),
            ],
            separator=" ",
        )
        candidates = _call_standard_search(searcher, query)
        selected, candidates = _select_agent_standard_candidate(
            searcher,
            candidates,
            item,
            fallback_query=query,
        )
        unit_options = list(selected.get("unit_options") or []) if isinstance(selected, Mapping) else []
        unit = unit_options[0] if unit_options else _clean_text(item.get("unit"))
        standard_name = _clean_text(selected.get("item_name")) if isinstance(selected, Mapping) else ""
        mapping_status = "standard_mapped" if standard_name else "manual_standard_mapping_required"
        rows.append(
            {
                "识别编号": item.get("item_id") or f"PDFAGITEM-{index:06d}",
                "映射状态": mapping_status,
                "标准号": selected.get("standard_code", "") if isinstance(selected, Mapping) else "",
                "标准项目编码": selected.get("item_code", "") if isinstance(selected, Mapping) else "",
                "标准项目名称": standard_name,
                "标准章节": selected.get("chapter_name", "") if isinstance(selected, Mapping) else "",
                "标准单位": unit,
                "匹配分数": selected.get("score", "") if isinstance(selected, Mapping) else "",
                "匹配原因": selected.get("match_reason", "") if isinstance(selected, Mapping) else "",
                "候选数量": len(candidates),
                "列项判断": item.get("itemizability_status", ""),
                "列项处理": item.get("itemizability_action", ""),
                "列项判断原因": item.get("itemizability_reason", ""),
                "具体做法名称": item.get("concrete_item_name", ""),
                "来源视图": ",".join(item.get("source_view_ids") or []),
                "项目特征": build_agent_project_feature_text(item),
                "工程量": build_agent_quantity_text(item),
                "source_item": item,
                "standard_candidates": candidates,
            }
        )
    return rows


def build_agent_four_field_rows(mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "项目名称": build_agent_display_project_name(row),
            "项目特征": row.get("项目特征", ""),
            "单位": row.get("标准单位", ""),
            "工程量": row.get("工程量", "待复核") or "待复核",
        }
        for row in mapping_rows
    ]


def build_agent_display_project_name(mapping_row: Mapping[str, Any]) -> str:
    concrete_name = _clean_text(mapping_row.get("具体做法名称"))
    standard_name = _clean_text(mapping_row.get("标准项目名称"))
    if not concrete_name:
        return standard_name
    if not standard_name or _normalize(concrete_name) == _normalize(standard_name):
        return concrete_name
    return f"{concrete_name}（{standard_name}）"


def build_agent_project_feature_text(item: Mapping[str, Any]) -> str:
    parts = []
    feature = _clean_text(item.get("feature"))
    if feature:
        parts.append(feature)
    source_views = _clean_text_list(item.get("source_view_ids"))
    if source_views:
        parts.append(f"来源视图：{','.join(source_views)}")
    evidence = _clean_text_list(item.get("source_evidence"))
    if evidence:
        parts.append(f"图纸证据：{'；'.join(evidence)}")
    reason = _clean_text(item.get("reason"))
    if reason:
        parts.append(f"列项原因：{reason}")
    itemizability_status = _clean_text(item.get("itemizability_status"))
    itemizability_reason = _clean_text(item.get("itemizability_reason"))
    if itemizability_status:
        itemizability_text = f"列项判断：{itemizability_status}"
        if itemizability_reason:
            itemizability_text = f"{itemizability_text}，{itemizability_reason}"
        parts.append(itemizability_text)
    if _bool(item.get("needs_manual_review"), default=True):
        parts.append("复核提示：AI识图草稿，需人工复核")
    return "；".join(dict.fromkeys(parts)) or "PDF Agent 识图列项，需人工补充项目特征"


def build_agent_quantity_text(item: Mapping[str, Any]) -> str:
    rough_quantity = _clean_text(item.get("rough_quantity"))
    quantity_note = _clean_text(item.get("quantity_note"))
    if not rough_quantity:
        return "待复核"
    if "待复核" in rough_quantity or "待确认" in rough_quantity:
        return rough_quantity
    if quantity_note and ("待复核" in quantity_note or "待确认" in quantity_note):
        return f"{rough_quantity}，待复核"
    return rough_quantity


def build_agent_summary(
    *,
    view_manifest: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    bill_items: list[dict[str, Any]],
    itemizability_report: Mapping[str, Any],
    mapping_rows: list[dict[str, Any]],
    quantity_rows: list[dict[str, Any]],
    ocr_report: Mapping[str, Any] | None = None,
    layout_plan_report: Mapping[str, Any] | None = None,
    region_crop_report: Mapping[str, Any] | None = None,
    cad_view_detail_plan_report: Mapping[str, Any] | None = None,
    cad_view_detail_crop_report: Mapping[str, Any] | None = None,
    cad_view_detail_highres_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tile_type_counts = Counter(row.get("tile_type") or "unknown" for row in view_manifest)
    mapped_count = sum(1 for row in mapping_rows if row.get("映射状态") == "standard_mapped")
    itemizability_report = itemizability_report or {}
    mappable_count = len(list(itemizability_report.get("mappable_items") or []))
    filtered_count = len(list(itemizability_report.get("filtered_items") or []))
    manual_review_count = len(list(itemizability_report.get("manual_review_items") or []))
    context_view_count = sum(1 for row in view_manifest if _is_context_manifest_view(row))
    ocr_summary = (ocr_report or {}).get("summary") if isinstance((ocr_report or {}).get("summary"), Mapping) else {}
    layout_summary = (layout_plan_report or {}).get("summary") if isinstance((layout_plan_report or {}).get("summary"), Mapping) else {}
    region_summary = (region_crop_report or {}).get("summary") if isinstance((region_crop_report or {}).get("summary"), Mapping) else {}
    cad_detail_summary = (
        (cad_view_detail_plan_report or {}).get("summary")
        if isinstance((cad_view_detail_plan_report or {}).get("summary"), Mapping)
        else {}
    )
    cad_detail_crop_summary = (
        (cad_view_detail_crop_report or {}).get("summary")
        if isinstance((cad_view_detail_crop_report or {}).get("summary"), Mapping)
        else {}
    )
    cad_detail_highres_summary = (
        (cad_view_detail_highres_report or {}).get("summary")
        if isinstance((cad_view_detail_highres_report or {}).get("summary"), Mapping)
        else {}
    )
    return {
        "agent_status": PDF_AGENT_STATUS_COMPLETED,
        "selected_view_count": len(view_manifest),
        "context_view_count": context_view_count,
        "local_view_count": max(0, len(view_manifest) - context_view_count),
        "layout_plan_status": _clean_text(layout_summary.get("layout_plan_status") or (layout_plan_report or {}).get("status") or "not_run"),
        "layout_plan_region_count": _int(layout_summary.get("layout_plan_region_count")),
        "layout_plan_high_priority_region_count": _int(layout_summary.get("layout_plan_high_priority_region_count")),
        "region_crop_status": _clean_text(region_summary.get("region_crop_status") or (region_crop_report or {}).get("status") or "not_run"),
        "region_crop_count": _int(region_summary.get("region_crop_count")),
        "cad_view_detail_plan_status": _clean_text(
            cad_detail_summary.get("cad_view_detail_plan_status")
            or (cad_view_detail_plan_report or {}).get("status")
            or "not_run"
        ),
        "cad_view_detail_region_count": _int(cad_detail_summary.get("cad_view_detail_region_count")),
        "cad_view_detail_right_bar_region_count": _int(cad_detail_summary.get("right_bar_region_count")),
        "cad_view_detail_bottom_note_region_count": _int(cad_detail_summary.get("bottom_note_region_count")),
        "cad_view_detail_crop_count": _int(cad_detail_crop_summary.get("region_crop_count")),
        "cad_view_detail_highres_status": _clean_text(
            cad_detail_highres_summary.get("highres_render_status")
            or (cad_view_detail_highres_report or {}).get("status")
            or "not_run"
        ),
        "cad_view_detail_highres_crop_count": _int(cad_detail_highres_summary.get("highres_crop_count")),
        "cad_view_detail_highres_quality_passed_count": _int(cad_detail_highres_summary.get("quality_passed_count")),
        "cad_view_detail_not_upscaled_from_lowres_count": _int(cad_detail_highres_summary.get("not_upscaled_from_lowres_count")),
        "ocr_status": _clean_text(ocr_summary.get("ocr_status") or (ocr_report or {}).get("ocr_status") or "not_run"),
        "ocr_crop_count": _int(ocr_summary.get("crop_count")),
        "ocr_region_crop_count": _int(ocr_summary.get("region_crop_count")),
        "ocr_text_line_count": _int(ocr_summary.get("ocr_text_line_count")),
        "region_ocr_text_line_count": _int(ocr_summary.get("region_ocr_text_line_count")),
        "ocr_material_legend_candidate_count": _int(ocr_summary.get("material_legend_candidate_count")),
        "region_material_legend_candidate_count": _int(ocr_summary.get("region_material_legend_candidate_count")),
        "view_tile_type_counts": dict(tile_type_counts),
        "agent_evidence_count": len(evidence_rows),
        "agent_bill_item_count": len(bill_items),
        "itemizability_mappable_count": mappable_count,
        "itemizability_filtered_non_construction_count": filtered_count,
        "itemizability_manual_review_count": manual_review_count,
        "standard_mapping_count": len(mapping_rows),
        "standard_mapped_count": mapped_count,
        "quantity_list_row_count": len(quantity_rows),
    }


def write_pdf_agent_itemization_outputs(
    report: Mapping[str, Any],
    *,
    business_dir: Path,
    debug_dir: Path,
    run_timestamp: str,
) -> dict[str, str]:
    business_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}

    quantity_outputs = write_quantity_list_outputs(
        list(report.get("quantity_list_rows") or []),
        business_dir,
        stem=f"BIZ2x_PDF_Agent四字段清单_{run_timestamp}",
    )
    outputs["quantity_list_xlsx"] = quantity_outputs["xlsx"]
    outputs["quantity_list_csv"] = quantity_outputs["csv"]

    output_specs = [
        ("view_manifest_json", f"BIZ2x_PDF_Agent图框清单_{run_timestamp}.json", report.get("view_manifest") or []),
        ("agent_evidence_json", f"BIZ2x_PDF_Agent识图证据_{run_timestamp}.json", report.get("agent_evidence_rows") or []),
        ("merged_evidence_json", f"BIZ2x_PDF_Agent合并证据_{run_timestamp}.json", report.get("merged_evidence") or {}),
        ("bill_items_raw_json", f"BIZ2x_PDF_Agent清单归纳_{run_timestamp}.json", report.get("agent_bill_items") or []),
        (
            "agent_itemizability_json",
            f"BIZ2x_PDF_Agent可列项性判断_{run_timestamp}.json",
            report.get("agent_itemizability_rows") or [],
        ),
        ("agent_report_json", f"BIZ2x_PDF_Agent运行报告_{run_timestamp}.json", _serializable_report(report)),
    ]
    for key, filename, payload in output_specs:
        path = debug_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    outputs.update(_ocr_output_paths(report.get("ocr_report") if isinstance(report.get("ocr_report"), Mapping) else {}))

    mapping_path = debug_dir / f"BIZ2x_PDF_Agent国标匹配_{run_timestamp}.csv"
    _write_csv(mapping_path, list(report.get("standard_mapping_rows") or []), STANDARD_MAPPING_HEADERS)
    outputs["standard_mapping_csv"] = str(mapping_path.resolve())

    markdown_path = debug_dir / f"BIZ2x_PDF_Agent运行报告_{run_timestamp}.md"
    markdown_path.write_text(_build_markdown(report), encoding="utf-8")
    outputs["agent_report_markdown"] = str(markdown_path.resolve())
    return outputs


def _call_standard_search(searcher: StandardSearch, query: str) -> list[dict[str, Any]]:
    try:
        result = searcher(query, limit=5)
    except TypeError:
        result = searcher(query)
    return [dict(item) for item in (result or []) if isinstance(item, Mapping)]


def _select_agent_standard_candidate(
    searcher: StandardSearch,
    candidates: list[dict[str, Any]],
    item: Mapping[str, Any],
    *,
    fallback_query: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    preferred = _preferred_standard_match_rules(item)
    all_candidates = list(candidates)
    for query, matcher in preferred:
        for candidate in candidates:
            if matcher(candidate):
                return candidate, all_candidates
        preferred_candidates = _call_standard_search(searcher, query)
        all_candidates = _merge_standard_candidates(all_candidates, preferred_candidates)
        for candidate in preferred_candidates:
            if matcher(candidate):
                return candidate, _merge_standard_candidates([candidate], all_candidates)
    return (candidates[0] if candidates else {}, all_candidates)


def _preferred_standard_match_rules(item: Mapping[str, Any]) -> list[tuple[str, Callable[[Mapping[str, Any]], bool]]]:
    text = _agent_item_search_text(item)
    rules: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = []
    has_wall = any(token in text for token in ("墙面", "墙砖", "墙、柱", "墙体"))
    has_floor = any(token in text for token in ("地面", "楼地面", "门槛石"))
    has_tile = any(token in text for token in ("瓷砖", "地砖", "块料", "CT", "ST", "大理石", "石材"))
    if any(token in text for token in ("洗手台", "洗脸盆", "台盆")):
        rules.append(("洗脸盆", lambda candidate: "洗脸盆" in _clean_text(candidate.get("item_name"))))
    if any(token in text for token in ("吊顶", "天花", "天棚")):
        ceiling_query = "艺术造型 吊顶天棚" if any(token in text for token in ("造型", "跌级", "圆形", "灯槽")) else "平面吊顶 天棚"
        rules.append(
            (
                ceiling_query,
                lambda candidate: "天棚" in _clean_text(candidate.get("chapter_name"))
                and ("吊顶" in _clean_text(candidate.get("item_name")) or "天棚" in _clean_text(candidate.get("item_name"))),
            )
        )
    if has_wall and has_tile:
        rules.append(("块料墙、柱面", lambda candidate: "块料墙" in _clean_text(candidate.get("item_name"))))
    elif has_floor and has_tile:
        rules.append(("块料楼地面", lambda candidate: "块料楼地面" in _clean_text(candidate.get("item_name"))))
    if "门套" in text:
        if any(token in text for token in ("木饰面", "MR")):
            query = "木门窗套"
        elif any(token in text for token in ("金属", "不锈钢", "MT")):
            query = "金属门窗套"
        elif any(token in text for token in ("石材", "大理石", "ST")):
            query = "石材门窗套"
        else:
            query = "木门窗套"
        rules.append((query, lambda candidate: "门窗套" in _clean_text(candidate.get("item_name"))))
    if "隔断" in text:
        rules.append((("成品隔断" if "成品" in text or "玻璃" in text else "轻质隔断"), lambda candidate: "隔断" in _clean_text(candidate.get("item_name"))))
    if any(token in text for token in ("台面", "操作台", "售卖口", "售卖窗口")):
        rules.append(("成品柜、架、台", lambda candidate: "成品柜" in _clean_text(candidate.get("item_name"))))
    return rules


def _agent_item_search_text(item: Mapping[str, Any]) -> str:
    return _join_unique(
        [
            item.get("concrete_item_name"),
            item.get("feature"),
            item.get("reason"),
            " ".join(item.get("source_evidence") or []),
        ],
        separator=" ",
    )


def _merge_standard_candidates(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in [*left, *right]:
        key = (_clean_text(candidate.get("standard_code")), _clean_text(candidate.get("item_code")))
        if not key[0] and not key[1]:
            key = (_clean_text(candidate.get("item_name")), _clean_text(candidate.get("chapter_name")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged


def _coerce_json_object(payload: Mapping[str, Any] | str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    text = str(payload or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PdfAgentItemizationError("Agent 返回内容不是合法 JSON") from exc
    if not isinstance(data, Mapping):
        return {}
    return data


def _normalize_material_codes(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = value if isinstance(value, list) else [value] if value else []
    for raw in raw_rows:
        if isinstance(raw, Mapping):
            code = _clean_text(raw.get("code"))
            name_or_hint = _clean_text(raw.get("name_or_hint"))
            spec_or_method = _clean_text(raw.get("spec_or_method"))
            if not code and not name_or_hint and not spec_or_method:
                continue
            rows.append(
                {
                    "code": code,
                    "name_or_hint": name_or_hint,
                    "spec_or_method": spec_or_method,
                    "confidence": _float(raw.get("confidence"), 0.0),
                }
            )
            continue
        text = _clean_text(raw)
        if text:
            rows.append({"code": text, "name_or_hint": "", "spec_or_method": "", "confidence": 0.0})
    return rows


def _normalize_objects(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = value if isinstance(value, list) else [value] if value else []
    for raw in raw_rows:
        if isinstance(raw, Mapping):
            name = _clean_text(raw.get("name"))
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "space": _clean_text(raw.get("space")),
                    "method": _clean_text(raw.get("method")),
                    "unit_hint": _clean_text(raw.get("unit_hint")),
                    "confidence": _float(raw.get("confidence"), 0.0),
                }
            )
            continue
        text = _clean_text(raw)
        if text:
            rows.append({"name": text, "space": "", "method": "", "unit_hint": "", "confidence": 0.0})
    return rows


def _normalize_quantity_clues(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = value if isinstance(value, list) else [value] if value else []
    for raw in raw_rows:
        if isinstance(raw, Mapping):
            text = _clean_text(raw.get("text"))
            meaning = _clean_text(raw.get("meaning"))
            if text or meaning:
                rows.append({"text": text, "meaning": meaning, "confidence": _float(raw.get("confidence"), 0.0)})
            continue
        text = _clean_text(raw)
        if text:
            rows.append({"text": text, "meaning": "", "confidence": 0.0})
    return rows


def _source_views_from_manifest(view_manifest: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _clean_text(row.get("view_id")): {
            "view_id": _clean_text(row.get("view_id")),
            "view_title": "",
            "view_type": "",
            "spaces": [],
            "evidence_count": 0,
            "source_file": _clean_text(row.get("source_file")),
            "page": _int(row.get("page")),
            "tile_type": _clean_text(row.get("tile_type")),
            "selection_role": _clean_text(row.get("selection_role")),
            "image_path": _clean_text(row.get("image_path")),
        }
        for row in view_manifest
        if _clean_text(row.get("view_id"))
    }


def _is_context_manifest_view(row: Mapping[str, Any]) -> bool:
    selection_role = _clean_text(row.get("selection_role"))
    return selection_role in CONTEXT_SELECTION_ROLES or _clean_text(row.get("tile_type")) == "whole_page_preview"


def _build_agent_global_context(
    evidence_rows: list[dict[str, Any]],
    source_views: Mapping[str, Mapping[str, Any]],
    *,
    ocr_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context_view_ids = [
        view_id for view_id, view in source_views.items() if _is_context_manifest_view(view)
    ]
    context_view_id_set = set(context_view_ids)
    context_rows = [row for row in evidence_rows if _clean_text(row.get("view_id")) in context_view_id_set]
    material_candidates: list[dict[str, Any]] = []
    visible_texts: list[str] = []
    context_notes: list[str] = []
    drawing_titles: list[str] = []

    for row in context_rows:
        view_id = _clean_text(row.get("view_id"))
        view_title = _clean_text(row.get("view_title"))
        view_type = _clean_text(row.get("view_type"))
        if view_title:
            drawing_titles.append(view_title)
        for text in row.get("visible_texts") or []:
            clean = _clean_text(text)
            if not clean:
                continue
            visible_texts.append(clean)
            if any(keyword in clean for keyword in ("图名", "施工图", "平面", "立面", "天花", "材料表", "图例")):
                drawing_titles.append(clean)
        for note in row.get("evidence_notes") or []:
            clean = _clean_text(note)
            if clean:
                context_notes.append(clean)
        for material in row.get("material_codes") or []:
            if not isinstance(material, Mapping):
                continue
            code = _clean_text(material.get("code"))
            name_or_hint = _clean_text(material.get("name_or_hint"))
            spec_or_method = _clean_text(material.get("spec_or_method"))
            if code or name_or_hint or spec_or_method:
                material_candidates.append(
                    {
                        "view_id": view_id,
                        "view_type": view_type,
                        "code": code,
                        "name_or_hint": name_or_hint,
                        "spec_or_method": spec_or_method,
                        "confidence": _float(material.get("confidence"), 0.0),
                    }
                )

    ocr_report = ocr_report or {}
    ocr_summary = ocr_report.get("summary") if isinstance(ocr_report.get("summary"), Mapping) else {}
    for ocr_row in ocr_report.get("ocr_rows") or []:
        if not isinstance(ocr_row, Mapping):
            continue
        text = _clean_text(ocr_row.get("text"))
        if text:
            visible_texts.append(text)
    for material in ocr_report.get("material_legend_candidates") or []:
        if not isinstance(material, Mapping):
            continue
        if not any(_clean_text(material.get(key)) for key in ("code", "name_or_hint", "spec_or_method")):
            continue
        material_candidates.append(
            {
                "view_id": "",
                "view_type": "local_ocr",
                "source": "local_ocr",
                "source_crop_ids": _clean_text_list(material.get("source_crop_ids")),
                "source_region_ids": _clean_text_list(material.get("source_region_ids")),
                "source_region_types": _clean_text_list(material.get("source_region_types")),
                "source_texts": _clean_text_list(material.get("source_texts")),
                "code": _clean_text(material.get("code")),
                "name_or_hint": _clean_text(material.get("name_or_hint")),
                "spec_or_method": _clean_text(material.get("spec_or_method")),
                "confidence": _float(material.get("confidence"), 0.0),
            }
        )

    return {
        "context_view_ids": context_view_ids,
        "ocr_status": _clean_text(ocr_summary.get("ocr_status") or ocr_report.get("ocr_status") or "not_run"),
        "ocr_crop_count": _int(ocr_summary.get("crop_count")),
        "ocr_region_crop_count": _int(ocr_summary.get("region_crop_count")),
        "ocr_text_line_count": _int(ocr_summary.get("ocr_text_line_count")),
        "region_ocr_text_line_count": _int(ocr_summary.get("region_ocr_text_line_count")),
        "ocr_material_legend_candidate_count": _int(ocr_summary.get("material_legend_candidate_count")),
        "region_material_legend_candidate_count": _int(ocr_summary.get("region_material_legend_candidate_count")),
        "drawing_titles": _unique_limited(drawing_titles, limit=20),
        "visible_texts": _unique_limited(visible_texts, limit=40),
        "material_legend_candidates": material_candidates[:80],
        "context_notes": _unique_limited(context_notes, limit=30),
    }


def _unique_limited(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        key = _normalize(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _serializable_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key
        in {
            "ok",
            "phase",
            "generated_at",
            "source_mode",
            "summary",
            "view_manifest",
            "agent_evidence_rows",
            "merged_evidence",
            "agent_bill_items",
            "agent_itemizability_rows",
            "agent_filtered_items",
            "agent_manual_review_items",
            "standard_mapping_rows",
            "quantity_list_rows",
            "layout_plan_report",
            "region_crop_report",
            "cad_view_detail_plan_report",
            "cad_view_detail_crop_report",
            "cad_view_detail_highres_report",
            "ocr_report",
            "issues",
            "agent_run",
        }
    }


def _ocr_output_paths(ocr_report: Mapping[str, Any] | None) -> dict[str, str]:
    outputs = (ocr_report or {}).get("outputs")
    if not isinstance(outputs, Mapping):
        return {}
    return {str(key): str(value) for key, value in outputs.items() if value}


def _generic_output_paths(report: Mapping[str, Any] | None) -> dict[str, str]:
    outputs = (report or {}).get("outputs")
    if not isinstance(outputs, Mapping):
        return {}
    return {str(key): str(value) for key, value in outputs.items() if value}


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: _csv_cell(row.get(header, "")) for header in headers})


def _build_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    outputs = report.get("outputs") if isinstance(report.get("outputs"), Mapping) else {}
    lines = [
        "# BIZ-2x PDF Agent 识图列项运行报告",
        "",
        f"- 阶段：{report.get('phase', '')}",
        f"- 视图数：{summary.get('selected_view_count', 0)}",
        f"- Layout Planner 状态：{summary.get('layout_plan_status', 'not_run')}，区域数：{summary.get('layout_plan_region_count', 0)}",
        f"- 区域裁图状态：{summary.get('region_crop_status', 'not_run')}，裁图数：{summary.get('region_crop_count', 0)}",
        f"- CAD 细栏规划状态：{summary.get('cad_view_detail_plan_status', 'not_run')}，细栏区域数：{summary.get('cad_view_detail_region_count', 0)}，细栏裁图数：{summary.get('cad_view_detail_crop_count', 0)}",
        f"- 区域 OCR 文本行数：{summary.get('region_ocr_text_line_count', 0)}，区域材料候选数：{summary.get('region_material_legend_candidate_count', 0)}",
        f"- 证据数：{summary.get('agent_evidence_count', 0)}",
        f"- 清单候选数：{summary.get('agent_bill_item_count', 0)}",
        f"- 进入国标匹配候选数：{summary.get('itemizability_mappable_count', 0)}",
        f"- 非施工项过滤数：{summary.get('itemizability_filtered_non_construction_count', 0)}",
        f"- 待确认候选数：{summary.get('itemizability_manual_review_count', 0)}",
        f"- 四字段行数：{summary.get('quantity_list_row_count', 0)}",
        f"- 国标匹配数：{summary.get('standard_mapped_count', 0)}",
        "",
        "## 输出文件",
    ]
    for key, value in sorted(outputs.items()):
        lines.append(f"- {key}: {value}")
    issues = list(report.get("issues") or [])
    if issues:
        lines.extend(["", "## Issues"])
        for issue in issues:
            lines.append(f"- {issue.get('级别', '')}: {issue.get('说明', '')}")
    return "\n".join(lines) + "\n"


def _csv_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return _clean_text(value)


def _clean_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _clean_text(raw)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _merge_unique_lists(left: Any, right: Any) -> list[str]:
    return _clean_text_list([*(_clean_text_list(left)), *(_clean_text_list(right))])


def _join_unique(values: list[Any], *, separator: str = "；") -> str:
    return separator.join(_clean_text_list(values))


def _image_file_size(path: Any) -> int:
    try:
        image_path = Path(str(path or ""))
        if image_path.exists() and image_path.is_file():
            return image_path.stat().st_size
    except (OSError, ValueError):
        return 0
    return 0


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _run_async_model_call(awaitable: Any) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(awaitable)
        return dict(result) if isinstance(result, Mapping) else {}
    raise RuntimeError("running_event_loop_in_sync_pdf_agent_itemization")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\-_—、，。；;:：/\\()（）\[\]{}【】<>\"'“”‘’+|]+", "", _clean_text(value)).lower()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _clean_text(value).lower()
    if text in {"1", "true", "yes", "y", "是"}:
        return True
    if text in {"0", "false", "no", "n", "否"}:
        return False
    return default
