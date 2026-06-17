from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi import Body
from fastapi.responses import FileResponse

from app.core.config import BASE_DIR
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.user import User
from app.services.dwg_item_listing import run_dwg_item_listing
from app.services.dwg_selection_finalizer import (
    build_dwg_selection_finalization,
    write_dwg_selection_finalization_outputs,
)
from app.services.drawing_special_trace_finalizer import (
    build_special_trace_finalization,
    write_special_trace_finalization_outputs,
)
from app.services.drawing_regression_evaluator import (
    build_dwg_regression_report,
    write_dwg_regression_outputs,
)
from app.services.dxf_trace_review_converter import (
    build_trace_review_conversion,
    read_trace_review_workbook,
    write_trace_review_conversion_outputs,
)
from app.services import drawing_quantity_confirmation as quantity_confirmation
from app.services.rbac import has_any_role


router = APIRouter()

OUTPUT_DIR = BASE_DIR.parent / "outputs" / "biz2x_trial"
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
ALLOWED_DWG_EXTENSIONS = {".dwg"}


def _require_trial_access(current_user: User) -> None:
    if not has_any_role(current_user, {"admin", "system_admin", "staff", "quote_user"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


@router.post("/admin/dwg-quantity-trial/convert", summary="DWG 识图试运行：trace 复核表转最终四字段清单")
async def convert_trace_review_workbook(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    original_name = file.filename or "trace-review.xlsx"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持上传 .xlsx / .xlsm 复核表")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_filename(Path(original_name).stem)
    uploaded_path = OUTPUT_DIR / f"BIZ2x_trial_uploaded_{timestamp}_{safe_name}{suffix}"
    uploaded_path.write_bytes(content)

    try:
        rows = read_trace_review_workbook(uploaded_path)
        conversion = build_trace_review_conversion(rows)
        conversion["inputs"] = {"trace_review_workbook": str(uploaded_path)}
        stem = f"BIZ2x_trial_trace复核转确认行_{timestamp}"
        conversion["outputs"] = write_trace_review_conversion_outputs(conversion, OUTPUT_DIR, stem=stem)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"复核表转换失败：{exc}") from exc

    return api_ok(_response_payload(conversion, uploaded_path))


@router.post("/admin/dwg-quantity-trial/validate-confirmation", summary="DWG 识图试运行：校验 R0-R9/BIZ-2x-6 人工确认表并导出四字段清单")
async def validate_confirmation_workbook(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    original_name = file.filename or "drawing-confirmation.xlsx"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持上传 .xlsx / .xlsm 人工确认表")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_filename(Path(original_name).stem)
    uploaded_path = OUTPUT_DIR / f"BIZ2x_R0_R9_confirmation_uploaded_{timestamp}_{safe_name}{suffix}"
    uploaded_path.write_bytes(content)

    try:
        rows = quantity_confirmation.read_confirmation_workbook(uploaded_path)
        validation = quantity_confirmation.validate_confirmation_rows(rows)
        validation["phase"] = "BIZ-2x-r0-r9-confirmation-validation"
        validation["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        validation["inputs"] = {"confirmation_workbook": str(uploaded_path)}
        outputs = quantity_confirmation.write_validation_report(
            validation,
            OUTPUT_DIR,
            stem=f"BIZ2x_R0_R9确认表校验_{timestamp}",
        )
        validation["outputs"] = {"json": outputs["json"]}
        if outputs.get("final_xlsx"):
            validation["outputs"]["validation_final_xlsx"] = outputs["final_xlsx"]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"人工确认表校验失败：{exc}") from exc

    return api_ok(_response_payload(validation, uploaded_path))


@router.post("/admin/dwg-quantity-trial/list-items", summary="DWG 识图试运行：上传 DWG 并生成列项候选")
async def list_items_from_dwg_upload(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传 .dwg 图纸文件")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = OUTPUT_DIR / f"BIZ2x_DWG上传_{timestamp}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for index, file in enumerate(files, start=1):
        original_name = file.filename or f"drawing_{index}.dwg"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_DWG_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持上传 .dwg 图纸文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{original_name} 文件为空")
        safe_name = _safe_filename(Path(original_name).stem)
        (upload_dir / f"{index:02d}_{safe_name}.dwg").write_bytes(content)
        saved_count += 1

    if saved_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可处理的 DWG 文件")

    try:
        report = await asyncio.to_thread(
            run_dwg_item_listing,
            upload_dir=upload_dir,
            output_dir=OUTPUT_DIR,
            timestamp=timestamp,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DWG 列项失败：{exc}") from exc

    return api_ok(_listing_response_payload(report))


@router.post("/admin/dwg-quantity-trial/finalize-selection", summary="DWG 识图试运行：提交 CAD 候选采纳并生成最终四字段清单")
async def finalize_dwg_quantity_selection(
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    selections = payload.get("selections") or []
    if not isinstance(selections, list) or not selections:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先在页面选择至少一条 CAD 候选")

    result_filename = str(payload.get("result_filename") or payload.get("source_result_filename") or "").strip()
    result_json = _resolve_listing_result_json(result_filename)
    if result_json is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到可提交的 DWG 列项结果 JSON")

    try:
        listing_report = json.loads(result_json.read_text(encoding="utf-8"))
        finalization = build_dwg_selection_finalization(listing_report, selections)
        finalization["inputs"] = {
            "listing_result_json": str(result_json),
            "selection_count": len(selections),
        }
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"BIZ2x_DWG候选采纳生成最终清单_{timestamp}"
        finalization["outputs"] = write_dwg_selection_finalization_outputs(finalization, OUTPUT_DIR, stem=stem)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CAD 候选采纳生成失败：{exc}") from exc

    return api_ok(_response_payload(finalization, None))


@router.post("/admin/dwg-quantity-trial/finalize-special-traces", summary="DWG 识图试运行：提交专项 trace 复核并生成最终四字段清单")
async def finalize_dwg_special_traces(
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    reviews = payload.get("reviews") or payload.get("special_trace_reviews") or []
    if not isinstance(reviews, list) or not reviews:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先提交至少一条专项 trace 复核结果")

    result_filename = str(payload.get("result_filename") or payload.get("source_result_filename") or "").strip()
    result_json = _resolve_listing_result_json(result_filename)
    if result_json is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到可提交的 DWG 列项结果 JSON")

    try:
        listing_report = json.loads(result_json.read_text(encoding="utf-8"))
        finalization = build_special_trace_finalization(listing_report, reviews)
        finalization["inputs"] = {
            "listing_result_json": str(result_json),
            "review_count": len(reviews),
        }
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"BIZ2x_DWG专项trace生成最终清单_{timestamp}"
        finalization["outputs"] = write_special_trace_finalization_outputs(finalization, OUTPUT_DIR, stem=stem)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"专项 trace 生成最终清单失败：{exc}") from exc

    return api_ok(_response_payload(finalization, None))


@router.post("/admin/dwg-quantity-trial/regression-report", summary="DWG 识图试运行：生成 3-5 套图纸回归评估报告")
async def build_dwg_trial_regression_report(
    payload: dict[str, Any] | None = Body(default=None),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    payload = payload or {}
    result_filenames = payload.get("result_filenames") or payload.get("filenames") or []
    if payload.get("result_filename"):
        result_filenames = [payload.get("result_filename")]
    if result_filenames and not isinstance(result_filenames, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="result_filenames 必须是数组")

    result_paths: list[Path] = []
    if result_filenames:
        for filename in result_filenames:
            result_json = _resolve_listing_result_json(str(filename or ""))
            if result_json is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"未找到 DWG 列项结果 JSON：{filename}")
            result_paths.append(result_json)
    else:
        limit = _positive_int(payload.get("limit"), 5)
        result_paths = _latest_listing_result_jsons(limit)
    if not result_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到可评估的 DWG 列项结果 JSON")

    listing_reports: list[dict[str, Any]] = []
    for result_path in _dedupe_paths(result_paths):
        try:
            report = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DWG 列项结果读取失败：{result_path.name}：{exc}") from exc
        report["__source_filename"] = result_path.name
        listing_reports.append(report)

    try:
        regression = build_dwg_regression_report(
            listing_reports,
            reference_rows_by_sample=payload.get("reference_rows_by_sample") or payload.get("reference_rows"),
        )
        regression["inputs"] = {
            "result_filenames": [path.name for path in _dedupe_paths(result_paths)],
            "reference_rows_provided": bool(payload.get("reference_rows_by_sample") or payload.get("reference_rows")),
        }
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"BIZ2x_DWG回归评估报告_{timestamp}"
        regression["outputs"] = write_dwg_regression_outputs(regression, OUTPUT_DIR, stem=stem)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DWG 回归评估失败：{exc}") from exc

    return api_ok(_regression_response_payload(regression))


@router.get("/admin/dwg-quantity-trial/latest", summary="DWG 识图试运行：最近一次转换结果")
def latest_dwg_quantity_trial(current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    latest_json = _latest_output_json()
    if latest_json is None:
        return api_ok({"has_result": False, "summary": {}, "issues": [], "files": []})
    try:
        import json

        conversion = json.loads(latest_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"最近结果读取失败：{exc}") from exc
    return api_ok({"has_result": True, **_response_payload(conversion, None)})


@router.get("/admin/dwg-quantity-trial/files/{filename}", summary="DWG 识图试运行：下载输出文件")
def download_dwg_quantity_trial_file(filename: str, current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    safe_name = Path(filename).name
    file_path = (OUTPUT_DIR / safe_name).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in file_path.parents or not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(file_path, filename=safe_name)


def _response_payload(conversion: dict[str, Any], uploaded_path: Path | None) -> dict[str, Any]:
    if conversion.get("phase") == "BIZ-2x-dwg-upload-item-listing":
        return _listing_response_payload(conversion)
    if conversion.get("phase") == "BIZ-2x-dwg-regression-evaluation":
        return _regression_response_payload(conversion)
    summary = conversion.get("summary") or {}
    outputs = conversion.get("outputs") or {}
    files = []
    if uploaded_path is not None:
        files.append(_file_entry(uploaded_path, "上传的复核表"))
    preferred_labels = {
        "markdown": "转换报告",
        "issue_csv": "转换问题 CSV",
        "skipped_csv": "跳过行 CSV",
        "converted_confirmation_csv": "确认行 CSV",
        "confirmation_confirmation_xlsx": "BIZ-2x-6 确认行工作簿",
        "validation_final_xlsx": "最终四字段 Excel",
        "json": "转换 JSON",
    }
    for key, label in preferred_labels.items():
        value = outputs.get(key)
        if value:
            files.append(_file_entry(Path(value), label, key))
    return {
        "ok": conversion.get("ok", False),
        "phase": conversion.get("phase"),
        "generated_at": conversion.get("generated_at"),
        "summary": summary,
        "issues": list(conversion.get("issues") or [])[:50],
        "files": files,
        "has_final_excel": bool(outputs.get("validation_final_xlsx")),
    }


def _listing_response_payload(report: dict[str, Any]) -> dict[str, Any]:
    outputs = report.get("outputs") or {}
    preferred_labels = {
        "quantity_list_xlsx": "识图四字段 Excel",
        "quantity_list_csv": "识图四字段 CSV",
        "item_list_xlsx": "DWG 列项候选 Excel",
        "item_list_csv": "DWG 列项候选 CSV",
        "item_list_markdown": "列项报告",
        "quantity_suggestion_csv": "低风险几何建议量 CSV",
        "standard_rule_trace_csv": "标准规则工程量 trace CSV",
        "trace_review_xlsx": "标准规则 trace 复核工作簿",
        "standard_match_csv": "标准项目匹配明细",
        "feature_fill_csv": "项目特征待填充明细",
        "project_recognition_csv": "图纸项目识别清单",
        "project_draft_four_field_xlsx": "标准列项草稿四字段 Excel",
        "project_recognition_markdown": "图纸项目识别报告",
        "project_geometry_binding_csv": "项目-CAD 绑定状态",
        "project_geometry_candidate_csv": "项目-CAD 候选明细",
        "project_geometry_binding_markdown": "项目-CAD 绑定报告",
        "project_region_binding_csv": "项目-区域绑定状态",
        "project_region_candidate_csv": "项目-区域候选明细",
        "project_region_binding_markdown": "项目-区域绑定报告",
        "project_material_binding_csv": "项目-材料编号-CAD证据绑定",
        "project_material_index_csv": "材料编号证据索引",
        "project_material_table_csv": "材料表编号证据",
        "project_material_inheritance_csv": "材料编号区域/房间继承候选",
        "project_material_binding_markdown": "材料编号区域/房间继承候选报告",
        "floor_paving_project_csv": "地面铺装项目候选",
        "floor_paving_geometry_csv": "地面图层面积候选",
        "floor_paving_material_text_csv": "地面材料文字坐标",
        "floor_paving_locator_markdown": "地面铺装有效区域定位报告",
        "floor_paving_locator_json": "地面铺装有效区域定位 JSON",
        "floor_layer_rescan_markdown": "地面图层定向重扫报告",
        "floor_layer_rescan_json": "地面图层定向重扫 JSON",
        "floor_layer_segment_csv": "地面线段",
        "floor_layer_package_csv": "材料文字线段包络",
        "floor_region_reconstruct_markdown": "地面线段闭合区域重构报告",
        "floor_region_reconstruct_json": "地面线段闭合区域重构 JSON",
        "floor_region_closed_csv": "地面闭合区域候选",
        "floor_region_project_csv": "项目闭合区域绑定",
        "room_boundary_csv": "房间边界净周长",
        "room_opening_candidate_csv": "门洞/开口候选明细",
        "room_boundary_markdown": "房间边界净周长报告",
        "special_quantity_trace_csv": "专项算量 trace",
        "special_quantity_markdown": "专项算量 trace 报告",
        "special_trace_confirmation_xlsx": "专项 trace 确认工作簿",
        "special_trace_confirmation_csv": "专项 trace 确认 CSV",
        "special_trace_confirmation_markdown": "专项 trace 确认说明",
        "dynamic_itemization_markdown": "R0-R9 标准库约束动态列项报告",
        "dynamic_itemization_json": "R0-R9 动态列项 JSON",
        "dynamic_itemization_csv": "R0-R9 动态列项明细 CSV",
        "dynamic_itemization_confirmation_xlsx": "R0-R9 动态列项人工确认表",
        "field_annotation_csv": "图纸文字标注字段",
        "geometry_candidate_csv": "CAD 几何候选明细",
        "region_label_csv": "CAD 区域文字绑定",
        "region_label_markdown": "CAD 区域文字绑定报告",
        "layer_mapping_csv": "低风险图层块名映射",
        "text_csv": "DXF 文字提取明细",
        "conversion_markdown": "DWG 转 DXF 报告",
        "json": "结果 JSON",
        "item_list_json": "结果 JSON",
    }
    files = []
    for key, label in preferred_labels.items():
        value = outputs.get(key)
        if value:
            files.append(_file_entry(Path(value), label, key))
    return {
        "ok": report.get("ok", False),
        "phase": report.get("phase"),
        "generated_at": report.get("generated_at"),
        "summary": report.get("summary") or {},
        "quantity_list_rows": _quantity_list_rows_for_response(report)[:200],
        "project_rows": list(report.get("project_rows") or [])[:200],
        "project_recognition_summary": report.get("project_recognition_summary") or {},
        "project_geometry_binding_rows": list(report.get("project_geometry_binding_rows") or [])[:200],
        "project_geometry_candidate_rows": list(report.get("project_geometry_candidate_rows") or [])[:500],
        "project_geometry_binding_summary": report.get("project_geometry_binding_summary") or {},
        "project_region_binding_rows": list(report.get("project_region_binding_rows") or [])[:200],
        "project_region_candidate_rows": list(report.get("project_region_candidate_rows") or [])[:500],
        "project_region_binding_summary": report.get("project_region_binding_summary") or {},
        "project_material_binding_rows": list(report.get("project_material_binding_rows") or [])[:200],
        "project_material_index_rows": list(report.get("project_material_index_rows") or [])[:500],
        "project_material_inheritance_rows": list(report.get("project_material_inheritance_rows") or [])[:500],
        "project_material_binding_summary": report.get("project_material_binding_summary") or {},
        "floor_paving_project_rows": list(report.get("floor_paving_project_rows") or [])[:200],
        "floor_paving_geometry_rows": list(report.get("floor_paving_geometry_rows") or [])[:500],
        "floor_paving_material_text_rows": list(report.get("floor_paving_material_text_rows") or [])[:500],
        "floor_paving_summary": report.get("floor_paving_summary") or {},
        "floor_layer_segment_rows": list(report.get("floor_layer_segment_rows") or [])[:500],
        "floor_layer_package_rows": list(report.get("floor_layer_package_rows") or [])[:200],
        "floor_layer_rescan_summary": report.get("floor_layer_rescan_summary") or {},
        "floor_closed_region_rows": list(report.get("floor_closed_region_rows") or [])[:500],
        "floor_region_project_rows": list(report.get("floor_region_project_rows") or [])[:200],
        "floor_region_reconstruct_summary": report.get("floor_region_reconstruct_summary") or {},
        "room_boundary_rows": list(report.get("room_boundary_rows") or [])[:200],
        "room_opening_candidate_rows": list(report.get("room_opening_candidate_rows") or [])[:500],
        "room_boundary_summary": report.get("room_boundary_summary") or {},
        "special_quantity_trace_rows": list(report.get("special_quantity_trace_rows") or [])[:500],
        "special_quantity_summary": report.get("special_quantity_summary") or {},
        "item_rows": list(report.get("item_rows") or [])[:200],
        "quantity_trace_rows": list(report.get("quantity_trace_rows") or [])[:200],
        "line_quantity_candidate_rows": list(report.get("line_quantity_candidate_rows") or [])[:500],
        "dynamic_itemization_summary": report.get("dynamic_itemization_summary") or {},
        "dynamic_itemization_stage_results": list(report.get("dynamic_itemization_stage_results") or [])[:20],
        "dynamic_itemization_decision_rows": list(report.get("dynamic_itemization_decision_rows") or [])[:200],
        "geometry_quantity_summary": report.get("geometry_quantity_summary") or {},
        "issues": list(report.get("issues") or [])[:50],
        "files": files,
        "has_item_list_excel": bool(outputs.get("item_list_xlsx")),
        "has_quantity_list_excel": bool(outputs.get("quantity_list_xlsx") or outputs.get("project_draft_four_field_xlsx")),
        "has_quantity_suggestions": bool(outputs.get("quantity_suggestion_csv")),
        "has_trace_review_workbook": bool(outputs.get("trace_review_xlsx")),
        "has_dynamic_itemization": bool(outputs.get("dynamic_itemization_json")),
        "has_final_excel": False,
    }


def _quantity_list_rows_for_response(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("quantity_list_rows")
    if isinstance(rows, list) and rows:
        return [
            {
                "项目名称": row.get("项目名称", ""),
                "项目特征": row.get("项目特征", ""),
                "单位": row.get("单位", ""),
                "工程量": row.get("工程量", ""),
            }
            for row in rows
        ]

    traces = {
        str(row.get("识别项目编号") or ""): row
        for row in report.get("special_quantity_trace_rows") or []
        if row.get("识别项目编号")
    }
    fallback_rows: list[dict[str, Any]] = []
    for project in report.get("project_rows") or []:
        trace = traces.get(str(project.get("识别项目编号") or "")) or {}
        ready_quantity = trace.get("建议工程量") not in (None, "") and str(trace.get("是否可复核") or "") == "是"
        fallback_rows.append(
            {
                "项目名称": project.get("项目名称", ""),
                "项目特征": project.get("项目特征", ""),
                "单位": (trace.get("建议单位") if ready_quantity else project.get("单位", "")) or "",
                "工程量": str(trace.get("建议工程量")) if ready_quantity else str(project.get("工程量") or "待算量"),
            }
        )
    return fallback_rows


def _regression_response_payload(report: dict[str, Any]) -> dict[str, Any]:
    outputs = report.get("outputs") or {}
    preferred_labels = {
        "xlsx": "DWG 回归评估工作簿",
        "markdown": "DWG 回归评估报告",
        "sample_csv": "样例汇总 CSV",
        "issue_csv": "问题清单 CSV",
        "reference_compare_csv": "参考清单对比 CSV",
        "json": "回归评估 JSON",
    }
    files = []
    for key, label in preferred_labels.items():
        value = outputs.get(key)
        if value:
            files.append(_file_entry(Path(value), label, key))
    return {
        "ok": report.get("ok", False),
        "phase": report.get("phase"),
        "generated_at": report.get("generated_at"),
        "summary": report.get("summary") or {},
        "sample_rows": list(report.get("sample_rows") or [])[:50],
        "issue_rows": list(report.get("issue_rows") or [])[:100],
        "reference_compare_rows": list(report.get("reference_compare_rows") or [])[:100],
        "files": files,
    }


def _file_entry(path: Path, label: str, key: str | None = None) -> dict[str, Any]:
    file_name = path.name
    return {
        "key": key or label,
        "label": label,
        "filename": file_name,
        "download_url": f"/api/v1/admin/dwg-quantity-trial/files/{quote(file_name)}",
    }


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "trace_review"


def _latest_output_json() -> Path | None:
    if not OUTPUT_DIR.exists():
        return None
    matches = [
        *OUTPUT_DIR.glob("BIZ2x_DWG上传列项_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_DWG回归评估报告_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_DWG专项trace生成最终清单_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_trial_trace复核转确认行_*.json"),
    ]
    matches = sorted(matches, key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _resolve_listing_result_json(filename: str) -> Path | None:
    if filename:
        safe_name = Path(filename).name
        candidate = (OUTPUT_DIR / safe_name).resolve()
        output_root = OUTPUT_DIR.resolve()
        if output_root not in candidate.parents or not candidate.exists() or not candidate.is_file():
            return None
        return candidate
    if not OUTPUT_DIR.exists():
        return None
    matches = sorted(OUTPUT_DIR.glob("BIZ2x_DWG上传列项_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _latest_listing_result_jsons(limit: int) -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    matches = sorted(OUTPUT_DIR.glob("BIZ2x_DWG上传列项_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[: max(limit, 1)]


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
