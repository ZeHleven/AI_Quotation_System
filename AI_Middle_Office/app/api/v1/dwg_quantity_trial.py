from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi import Body
from fastapi.responses import FileResponse

from app.core.config import BASE_DIR, settings
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.user import User
from app.services.dwg_item_listing import run_dwg_item_listing
from app.services.drawing_pdf_direct_itemizer import PHASE as PDF_DIRECT_ITEMIZATION_PHASE
from app.services.drawing_pdf_direct_itemizer import run_pdf_direct_itemization
from app.services.drawing_pdf_agent_itemizer import (
    run_pdf_agent_itemization_dashscope,
    run_pdf_agent_itemization_openai,
)
from app.services.drawing_agent_runtime import (
    DrawingAgentRunTracker,
    create_pdf_agent_run_tracker,
    read_drawing_agent_run_events,
    read_drawing_agent_run_state,
    read_latest_drawing_agent_run_state,
)
from app.services.dwg_selection_finalizer import (
    build_dwg_selection_finalization,
    write_dwg_selection_finalization_outputs,
)
from app.services.drawing_low_risk_mvp_finalizer import (
    build_low_risk_mvp_finalization,
    write_low_risk_mvp_finalization_outputs,
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
ALLOWED_PDF_EXTENSIONS = {".pdf"}


def _require_trial_access(current_user: User) -> None:
    if not has_any_role(current_user, {"admin", "system_admin", "quote_user"}):
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


@router.post("/admin/dwg-quantity-trial/list-items-from-pdf", summary="PDF 直接识图试运行：上传正式 PDF 并生成四字段清单")
async def list_items_from_pdf_upload(
    pdf_files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    if not settings.feature_pdf_direct_itemization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF_DIRECT_ITEMIZATION_DISABLED")
    if not pdf_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传正式 PDF 图纸文件")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = OUTPUT_DIR / f"BIZ2x_PDF直接识图上传_{timestamp}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_pdf_count = 0
    for index, file in enumerate(pdf_files, start=1):
        original_name = file.filename or f"drawing_{index}.pdf"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_PDF_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF 上传区仅支持 .pdf 正式图纸文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{original_name} 文件为空")
        safe_name = _safe_filename(Path(original_name).stem)
        (upload_dir / f"{index:02d}_{safe_name}.pdf").write_bytes(content)
        saved_pdf_count += 1

    if saved_pdf_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可处理的 PDF 文件")

    provider = _normalize_pdf_itemization_provider(settings.pdf_itemization_provider)
    run_tracker = create_pdf_agent_run_tracker(
        output_dir=OUTPUT_DIR,
        run_id=timestamp,
        input_dir=upload_dir,
        provider=provider,
    )

    try:
        report = await asyncio.to_thread(
            _run_pdf_itemization_by_provider,
            pdf_dir=upload_dir,
            output_dir=OUTPUT_DIR,
            timestamp=timestamp,
            provider=provider,
            run_tracker=run_tracker,
        )
    except Exception as exc:
        run_tracker.fail(
            stage=str(run_tracker.snapshot().get("stage") or "failed"),
            error_code="PDF_AGENT_RUN_FAILED",
            message=str(exc),
            detail={"provider": provider, "upload_dir": str(upload_dir)},
            with_report=True,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"PDF 识图列项失败：{exc}") from exc

    return api_ok(_listing_response_payload(report))


def _run_pdf_itemization_by_provider(
    *,
    pdf_dir: Path,
    output_dir: Path,
    timestamp: str,
    provider: str | None = None,
    run_tracker: DrawingAgentRunTracker | None = None,
) -> dict[str, Any]:
    provider = provider or _normalize_pdf_itemization_provider(settings.pdf_itemization_provider)
    if provider == "glm":
        if run_tracker is not None:
            run_tracker.update("running_vision", progress=50, detail={"provider": provider})
        report = run_pdf_direct_itemization(
            pdf_dir=pdf_dir,
            output_dir=output_dir,
            timestamp=timestamp,
        )
    elif provider == "dashscope_agent":
        report = _call_pdf_agent_provider(
            run_pdf_agent_itemization_dashscope,
            run_tracker=run_tracker,
            pdf_dir=pdf_dir,
            output_dir=output_dir,
            timestamp=timestamp,
        )
    elif provider == "openai_agent":
        report = _call_pdf_agent_provider(
            run_pdf_agent_itemization_openai,
            run_tracker=run_tracker,
            pdf_dir=pdf_dir,
            output_dir=output_dir,
            timestamp=timestamp,
        )
    else:
        raise ValueError(
            "PDF_ITEMIZATION_PROVIDER 仅支持 glm、dashscope_agent、openai_agent，"
            f"当前值：{settings.pdf_itemization_provider}"
        )
    report = _attach_pdf_itemization_provider(report, provider)
    if run_tracker is not None and provider == "glm":
        run_tracker.update("exporting", progress=98, detail={"provider": provider})
        if bool(report.get("ok")):
            run_tracker.complete(
                status="completed_with_review" if report.get("issues") else "completed",
                summary=report.get("summary") or {},
                outputs=report.get("outputs") or {},
                issues=list(report.get("issues") or []),
            )
        else:
            run_tracker.mark_report_failure(
                error_code="NO_VALID_PDF_DIRECT_OUTPUT",
                message="PDF direct itemization finished without usable output",
                report=report,
            )
        report["agent_run"] = run_tracker.snapshot()
    elif run_tracker is not None and str(run_tracker.snapshot().get("status") or "") not in {
        "completed",
        "completed_with_review",
        "failed_with_report",
        "failed",
    }:
        if bool(report.get("ok")):
            run_tracker.complete(
                status="completed_with_review" if report.get("issues") else "completed",
                summary=report.get("summary") or {},
                outputs=report.get("outputs") or {},
                issues=list(report.get("issues") or []),
            )
        else:
            run_tracker.mark_report_failure(
                error_code="NO_VALID_PDF_AGENT_OUTPUT",
                message="PDF agent provider finished without usable output",
                report=report,
            )
        report["agent_run"] = run_tracker.snapshot()
    return report


def _call_pdf_agent_provider(fn, *, run_tracker: DrawingAgentRunTracker | None, **kwargs) -> dict[str, Any]:
    call_kwargs = dict(kwargs)
    if run_tracker is not None and _callable_accepts_keyword(fn, "run_tracker"):
        call_kwargs["run_tracker"] = run_tracker
    return fn(**call_kwargs)


def _callable_accepts_keyword(fn, keyword: str) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == keyword:
            return True
    return False


def _normalize_pdf_itemization_provider(value: str | None) -> str:
    provider = str(value or "glm").strip().lower().replace("-", "_")
    aliases = {
        "": "glm",
        "direct": "glm",
        "glm_direct": "glm",
        "pdf_direct": "glm",
        "qwen": "dashscope_agent",
        "qwen_agent": "dashscope_agent",
        "dashscope": "dashscope_agent",
        "dashscope_qwen": "dashscope_agent",
        "openai": "openai_agent",
    }
    return aliases.get(provider, provider)


def _attach_pdf_itemization_provider(report: dict[str, Any], provider: str) -> dict[str, Any]:
    summary = report.setdefault("summary", {})
    if isinstance(summary, dict):
        summary["pdf_itemization_provider"] = provider
        if provider == "dashscope_agent":
            summary["pdf_itemization_model"] = settings.dashscope_vision_model
            summary["pdf_itemization_evidence_model"] = settings.dashscope_evidence_model
            summary["pdf_itemization_bill_summary_model"] = settings.dashscope_bill_summary_model
        elif provider == "openai_agent":
            summary["pdf_itemization_model"] = settings.openai_vision_model
        elif provider == "glm":
            summary["pdf_itemization_model"] = settings.glm_vision_model
    report["pdf_itemization_provider"] = provider
    return report


@router.post("/admin/dwg-quantity-trial/list-items-with-pdf", summary="DWG 识图试运行：上传 DWG + 正式 PDF 并生成列项候选")
async def list_items_from_dwg_pdf_upload(
    dwg_files: list[UploadFile] = File(...),
    pdf_files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    if not dwg_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传 .dwg 图纸文件")
    if not pdf_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传与 DWG 对应的正式 PDF 文件")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = OUTPUT_DIR / f"BIZ2x_DWG_PDF上传_{timestamp}"
    pdf_upload_dir = upload_dir / "pdf"
    upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_upload_dir.mkdir(parents=True, exist_ok=True)

    saved_dwg_count = 0
    for index, file in enumerate(dwg_files, start=1):
        original_name = file.filename or f"drawing_{index}.dwg"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_DWG_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="DWG 上传区仅支持 .dwg 图纸文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{original_name} 文件为空")
        safe_name = _safe_filename(Path(original_name).stem)
        (upload_dir / f"{index:02d}_{safe_name}.dwg").write_bytes(content)
        saved_dwg_count += 1

    saved_pdf_count = 0
    for index, file in enumerate(pdf_files, start=1):
        original_name = file.filename or f"drawing_{index}.pdf"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_PDF_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF 上传区仅支持 .pdf 正式图纸文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{original_name} 文件为空")
        safe_name = _safe_filename(Path(original_name).stem)
        (pdf_upload_dir / f"{index:02d}_{safe_name}.pdf").write_bytes(content)
        saved_pdf_count += 1

    if saved_dwg_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可处理的 DWG 文件")
    if saved_pdf_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可处理的 PDF 文件")

    try:
        report = await asyncio.to_thread(
            run_dwg_item_listing,
            upload_dir=upload_dir,
            pdf_upload_dir=pdf_upload_dir,
            output_dir=OUTPUT_DIR,
            timestamp=timestamp,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DWG + PDF 列项失败：{exc}") from exc

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


@router.post("/admin/dwg-quantity-trial/finalize-low-risk-mvp", summary="DWG 识图试运行：提交低风险 MVP 复核并回填四字段清单工程量")
async def finalize_dwg_low_risk_mvp(
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
):
    _require_trial_access(current_user)
    reviews = payload.get("reviews") or payload.get("mvp_reviews") or []
    if not isinstance(reviews, list) or not reviews:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先提交至少一条低风险 MVP 复核结果")

    result_filename = str(payload.get("result_filename") or payload.get("source_result_filename") or "").strip()
    result_json = _resolve_listing_result_json(result_filename)
    if result_json is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到可提交的 DWG 列项结果 JSON")

    try:
        listing_report = json.loads(result_json.read_text(encoding="utf-8"))
        finalization = build_low_risk_mvp_finalization(listing_report, reviews)
        finalization["inputs"] = {
            "listing_result_json": str(result_json),
            "review_count": len(reviews),
        }
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"BIZ2x_DWG低风险MVP回填四字段清单_{timestamp}"
        finalization["outputs"] = write_low_risk_mvp_finalization_outputs(finalization, OUTPUT_DIR, stem=stem)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"低风险 MVP 回填失败：{exc}") from exc

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
        report["__source_filename"] = _output_relative_path(result_path)
        listing_reports.append(report)

    try:
        regression = build_dwg_regression_report(
            listing_reports,
            reference_rows_by_sample=payload.get("reference_rows_by_sample") or payload.get("reference_rows"),
        )
        regression["inputs"] = {
            "result_filenames": [_output_relative_path(path) for path in _dedupe_paths(result_paths)],
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


@router.get("/admin/dwg-quantity-trial/pdf-agent-runs/latest", summary="PDF Agent 识图任务：最近一次运行状态")
def latest_pdf_agent_run(current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    state = read_latest_drawing_agent_run_state(OUTPUT_DIR)
    if not state:
        return api_ok({"has_run": False, "run": {}, "events": []})
    run_id = str(state.get("run_id") or "")
    events = read_drawing_agent_run_events(OUTPUT_DIR, run_id, limit=200) if run_id else []
    return api_ok({"has_run": True, "run": state, "events": events})


@router.get("/admin/dwg-quantity-trial/pdf-agent-runs/{run_id}", summary="PDF Agent 识图任务：运行状态和事件")
def get_pdf_agent_run(run_id: str, current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    try:
        state = read_drawing_agent_run_state(OUTPUT_DIR, run_id)
        events = read_drawing_agent_run_events(OUTPUT_DIR, run_id, limit=500)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF_AGENT_RUN_NOT_FOUND") from exc
    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF_AGENT_RUN_NOT_FOUND")
    return api_ok({"has_run": True, "run": state, "events": events})


@router.get("/admin/dwg-quantity-trial/files/{file_path:path}", summary="DWG 识图试运行：下载输出文件")
def download_dwg_quantity_trial_file(file_path: str, current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    relative_path = Path(file_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    resolved_path = (OUTPUT_DIR / relative_path).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in resolved_path.parents or not resolved_path.exists() or not resolved_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(resolved_path, filename=resolved_path.name)


@router.get("/admin/dwg-quantity-trial/artifacts/{artifact_path:path}", summary="DWG 识图试运行：预览输出目录内的图片证据")
def preview_dwg_quantity_trial_artifact(artifact_path: str, current_user: User = Depends(get_current_user)):
    _require_trial_access(current_user)
    relative_path = Path(artifact_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    file_path = (OUTPUT_DIR / relative_path).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in file_path.parents or not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(file_path, filename=file_path.name)


def _response_payload(conversion: dict[str, Any], uploaded_path: Path | None) -> dict[str, Any]:
    if conversion.get("phase") in {"BIZ-2x-dwg-upload-item-listing", PDF_DIRECT_ITEMIZATION_PHASE}:
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
        "validation_final_csv": "最终四字段 CSV",
        "low_risk_mvp_final_xlsx": "低风险 MVP 回填四字段 Excel",
        "low_risk_mvp_final_csv": "低风险 MVP 回填四字段 CSV",
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
    summary = report.get("summary") or {}
    pdf_evidence_summary = report.get("pdf_evidence_summary") or {}
    pdf_evidence_effective = _pdf_evidence_is_effective(pdf_evidence_summary or summary)
    business_labels = {
        "quantity_list_xlsx": "识图四字段 Excel",
        "quantity_list_csv": "识图四字段 CSV",
        "project_draft_four_field_xlsx": "标准列项草稿四字段 Excel",
    }
    preferred_labels = {
        "quantity_list_xlsx": "识图四字段 Excel",
        "quantity_list_csv": "识图四字段 CSV",
        "item_list_xlsx": "DWG 列项候选 Excel",
        "item_list_csv": "DWG 列项候选 CSV",
        "item_list_markdown": "列项报告",
        "quantity_suggestion_csv": "低风险几何建议量 CSV",
        "low_risk_quantity_mvp_csv": "首批低风险算量 MVP 候选 CSV",
        "low_risk_quantity_mvp_markdown": "首批低风险算量 MVP 报告",
        "low_risk_quantity_mvp_json": "首批低风险算量 MVP JSON",
        "low_risk_mvp_binding_csv": "低风险 MVP 项目行绑定明细",
        "low_risk_mvp_binding_markdown": "低风险 MVP 绑定确认报告",
        "low_risk_mvp_binding_json": "低风险 MVP 绑定确认 JSON",
        "low_risk_mvp_confirmation_xlsx": "低风险 MVP 人工确认工作簿",
        "low_risk_mvp_confirmation_csv": "低风险 MVP 人工确认 CSV",
        "low_risk_mvp_confirmation_markdown": "低风险 MVP 人工确认说明",
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
        "pdf_pipeline_markdown": "PDF 高清视觉证据链报告",
        "pdf_pipeline_json": "PDF 高清视觉证据链 JSON",
        "pdf_page_csv": "PDF 页面清单",
        "pdf_text_csv": "PDF 内嵌文字证据",
        "pdf_render_csv": "PDF 高清 PNG 渲染清单",
        "pdf_tile_csv": "PDF 分块 tile 清单",
        "pdf_visual_evidence_csv": "PDF 视觉证据",
        "dwg_pdf_match_csv": "DWG/PDF 对应关系校验",
        "dxf_pdf_fusion_csv": "DXF+PDF 证据合并",
        "pdf_r0_r9_evidence_json": "PDF 接入 R0-R9 证据输入",
        "pdf_direct_itemization_json": "PDF 直接识图列项 JSON",
        "pdf_direct_item_csv": "PDF 直接识图项目候选 CSV",
        "pdf_direct_standard_mapping_csv": "PDF 直接识图标准映射 CSV",
        "pdf_direct_itemization_markdown": "PDF 直接识图列项报告",
        "view_manifest_json": "PDF Agent 图框清单 JSON",
        "agent_evidence_json": "PDF Agent 识图证据 JSON",
        "merged_evidence_json": "PDF Agent 合并证据 JSON",
        "bill_items_raw_json": "PDF Agent 清单归纳 JSON",
        "agent_itemizability_json": "PDF Agent 可列项性判断 JSON",
        "agent_report_json": "PDF Agent 运行报告 JSON",
        "agent_run_state_json": "PDF Agent 运行状态 JSON",
        "agent_run_events_jsonl": "PDF Agent 进度事件 JSONL",
        "agent_ocr_report_json": "PDF Agent OCR 运行报告 JSON",
        "agent_ocr_summary_json": "PDF Agent OCR 摘要 JSON",
        "agent_ocr_crop_manifest_json": "PDF Agent OCR 裁切清单 JSON",
        "agent_ocr_rows_json": "PDF Agent OCR 文字行 JSON",
        "agent_material_legend_candidates_json": "PDF Agent OCR 材料图例候选 JSON",
        "agent_ocr_diagnostics_json": "PDF Agent OCR 诊断 JSON",
        "standard_mapping_csv": "PDF Agent 国标匹配 CSV",
        "agent_report_markdown": "PDF Agent 运行报告",
        "pdf_ai_quantity_json": "PDF AI 候选工程量 JSON",
        "pdf_ai_quantity_markdown": "PDF AI 候选工程量报告",
        "pdf_ai_quantity_csv": "PDF AI 候选工程量 CSV",
        "pdf_dxf_item_fusion_json": "PDF+DXF 列项融合 JSON",
        "pdf_dxf_item_fusion_markdown": "PDF+DXF 列项融合报告",
        "pdf_dxf_item_fusion_csv": "PDF+DXF 列项融合明细",
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
    debug_files = []
    for key, label in preferred_labels.items():
        value = outputs.get(key)
        if value:
            debug_files.append(_file_entry(Path(value), label, key))
    files = []
    for key, label in business_labels.items():
        value = outputs.get(key)
        if value:
            files.append(_file_entry(Path(value), label, key))
    return {
        "ok": report.get("ok", False),
        "phase": report.get("phase"),
        "generated_at": report.get("generated_at"),
        "summary": summary,
        "agent_run": report.get("agent_run") or {},
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
        "dwg_quantity_list_rows": list(report.get("dwg_quantity_list_rows") or [])[:200],
        "pdf_direct_itemization_summary": report.get("pdf_direct_itemization_summary") or {},
        "pdf_direct_item_rows": list(report.get("pdf_direct_item_rows") or [])[:200],
        "pdf_direct_standard_mapping_rows": list(report.get("pdf_direct_standard_mapping_rows") or [])[:200],
        "pdf_ai_quantity_summary": report.get("pdf_ai_quantity_summary") or {},
        "pdf_ai_quantity_rows": list(report.get("pdf_ai_quantity_rows") or [])[:200],
        "pdf_dxf_item_fusion_summary": report.get("pdf_dxf_item_fusion_summary") or {},
        "pdf_dxf_item_fusion_rows": list(report.get("pdf_dxf_item_fusion_rows") or [])[:300],
        "quantity_trace_rows": list(report.get("quantity_trace_rows") or [])[:200],
        "line_quantity_candidate_rows": list(report.get("line_quantity_candidate_rows") or [])[:500],
        "dynamic_itemization_summary": report.get("dynamic_itemization_summary") or {},
        "dynamic_itemization_stage_results": list(report.get("dynamic_itemization_stage_results") or [])[:20],
        "dynamic_itemization_decision_rows": list(report.get("dynamic_itemization_decision_rows") or [])[:200],
        "pdf_evidence_summary": pdf_evidence_summary,
        "pdf_page_rows": list(report.get("pdf_page_rows") or [])[:200],
        "pdf_render_rows": _attach_artifact_urls(list(report.get("pdf_render_rows") or []), "png_path")[:100],
        "pdf_tile_rows": _attach_artifact_urls(list(report.get("pdf_tile_rows") or []), "image_path")[:300],
        "pdf_visual_evidence_rows": list(report.get("pdf_visual_evidence_rows") or [])[:300],
        "dwg_pdf_match_rows": list(report.get("dwg_pdf_match_rows") or [])[:50],
        "dxf_pdf_fusion_rows": list(report.get("dxf_pdf_fusion_rows") or [])[:300],
        "geometry_quantity_summary": report.get("geometry_quantity_summary") or {},
        "low_risk_quantity_mvp_summary": report.get("low_risk_quantity_mvp_summary") or {},
        "low_risk_quantity_mvp_rows": list(report.get("low_risk_quantity_mvp_rows") or [])[:200],
        "low_risk_mvp_binding_summary": report.get("low_risk_mvp_binding_summary") or {},
        "low_risk_mvp_binding_rows": list(report.get("low_risk_mvp_binding_rows") or [])[:200],
        "issues": list(report.get("issues") or [])[:50],
        "files": files,
        "debug_files": debug_files,
        "debug_file_count": len(debug_files),
        "has_item_list_excel": bool(outputs.get("item_list_xlsx")),
        "has_quantity_list_excel": bool(outputs.get("quantity_list_xlsx") or outputs.get("project_draft_four_field_xlsx")),
        "has_quantity_suggestions": bool(outputs.get("quantity_suggestion_csv")),
        "has_low_risk_quantity_mvp": bool(outputs.get("low_risk_quantity_mvp_csv")),
        "has_low_risk_mvp_confirmation": bool(outputs.get("low_risk_mvp_confirmation_xlsx")),
        "has_trace_review_workbook": bool(outputs.get("trace_review_xlsx")),
        "has_dynamic_itemization": bool(outputs.get("dynamic_itemization_json")),
        "has_pdf_direct_itemization": bool(outputs.get("pdf_direct_itemization_json")),
        "has_pdf_agent_itemization": bool(outputs.get("agent_report_json")),
        "has_pdf_ai_quantity_suggestions": bool(outputs.get("pdf_ai_quantity_json")),
        "has_pdf_dxf_item_fusion": bool(outputs.get("pdf_dxf_item_fusion_json")),
        "has_pdf_evidence": pdf_evidence_effective,
        "pdf_evidence_effective": pdf_evidence_effective,
        "has_final_excel": False,
    }


def _pdf_evidence_is_effective(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    match_status = str(summary.get("dwg_pdf_match_status") or "")
    fusion_status = str(summary.get("dxf_pdf_fusion_status") or "")
    return (
        _int_summary_value(summary.get("pdf_rendered_page_count")) > 0
        and _int_summary_value(summary.get("pdf_visual_evidence_count")) > 0
        and match_status in {"auto_matched", "needs_manual_bind"}
        and fusion_status in {"ready", "manual_bind_required"}
        and _int_summary_value(summary.get("dxf_pdf_fusion_link_count")) > 0
    )


def _int_summary_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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
    relative_path = _output_relative_path(path)
    return {
        "key": key or label,
        "label": label,
        "filename": file_name,
        "path": relative_path,
        "download_url": f"/api/v1/admin/dwg-quantity-trial/files/{quote(relative_path, safe='/')}",
    }


def _output_relative_path(path: Path) -> str:
    try:
        resolved = path.resolve()
        output_root = OUTPUT_DIR.resolve()
        if output_root in resolved.parents:
            return resolved.relative_to(output_root).as_posix()
    except Exception:
        pass
    return path.name


def _attach_artifact_urls(rows: list[dict[str, Any]], path_key: str) -> list[dict[str, Any]]:
    updated_rows: list[dict[str, Any]] = []
    output_root = OUTPUT_DIR.resolve()
    for row in rows:
        updated = dict(row)
        raw_path = str(row.get(path_key) or "")
        if raw_path:
            try:
                artifact_path = Path(raw_path).resolve()
                if output_root in artifact_path.parents and artifact_path.exists() and artifact_path.is_file():
                    relative = artifact_path.relative_to(output_root).as_posix()
                    updated["preview_url"] = f"/api/v1/admin/dwg-quantity-trial/artifacts/{quote(relative)}"
            except Exception:
                pass
        updated_rows.append(updated)
    return updated_rows


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "trace_review"


def _latest_output_json() -> Path | None:
    if not OUTPUT_DIR.exists():
        return None
    matches = [
        *_listing_result_json_candidates(),
        *OUTPUT_DIR.glob("BIZ2x_DWG回归评估报告_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_DWG低风险MVP回填四字段清单_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_DWG专项trace生成最终清单_*.json"),
        *OUTPUT_DIR.glob("BIZ2x_trial_trace复核转确认行_*.json"),
    ]
    matches = sorted(matches, key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _resolve_listing_result_json(filename: str) -> Path | None:
    if filename:
        relative_path = Path(filename)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return None
        candidate = (OUTPUT_DIR / relative_path).resolve()
        output_root = OUTPUT_DIR.resolve()
        if output_root in candidate.parents and candidate.exists() and candidate.is_file():
            return candidate
        safe_name = relative_path.name
        matches = [path for path in _listing_result_json_candidates() if path.name == safe_name]
        matches = sorted(matches, key=lambda item: item.stat().st_mtime, reverse=True)
        return matches[0] if matches else None
    if not OUTPUT_DIR.exists():
        return None
    matches = sorted(_listing_result_json_candidates(), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _latest_listing_result_jsons(limit: int) -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    matches = sorted(_listing_result_json_candidates(), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[: max(limit, 1)]


def _listing_result_json_candidates() -> list[Path]:
    return [
        *OUTPUT_DIR.glob("BIZ2x_DWG上传列项_*.json"),
        *OUTPUT_DIR.glob("debug/*/BIZ2x_DWG上传列项_*.json"),
        *OUTPUT_DIR.glob("debug/*/BIZ2x_PDF直接识图列项_*.json"),
    ]


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
