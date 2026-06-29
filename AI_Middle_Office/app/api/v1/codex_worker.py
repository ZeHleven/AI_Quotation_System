from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.core.config import BASE_DIR
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.user import User
from app.services.codex_worker_contract import CodexWorkerContractError, run_codex_worker_contract
from app.services.codex_worker_fake import FAKE_CODEX_WORKER_SAMPLES, build_fake_codex_result
from app.services.drawing_pdf_agent_itemizer import run_pdf_agent_itemization_openai
from app.services.rbac import has_any_role


router = APIRouter()

JOB_ROOT = BASE_DIR / "runtime" / "codex_worker" / "jobs"
ALLOWED_PDF_EXTENSIONS = {".pdf"}
STATUS_FILE_NAME = "job_status.json"


def _require_worker_access(current_user: User) -> None:
    if not has_any_role(current_user, {"admin", "system_admin", "staff", "quote_user"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


@router.post("/admin/codex-worker/jobs/fake", status_code=status.HTTP_202_ACCEPTED, summary="Codex Worker POC：创建 fake PDF 识图任务")
async def create_fake_codex_worker_job(
    pdf_files: list[UploadFile] = File(...),
    sample: str = Query("valid"),
    current_user: User = Depends(get_current_user),
):
    _require_worker_access(current_user)
    if sample not in FAKE_CODEX_WORKER_SAMPLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="UNSUPPORTED_FAKE_SAMPLE")
    if not pdf_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传至少一份 PDF 图纸")

    job_id = f"codexpdf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    job_dir = JOB_ROOT / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    created_at = _utc_now()
    _write_job_status(
        job_dir,
        {
            "job_id": job_id,
            "mode": "fake",
            "status": "running",
            "stage": "saving_uploads",
            "created_at": created_at,
            "updated_at": created_at,
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": [],
            "errors": [],
            "warnings": [],
        },
    )

    try:
        input_files = await _save_uploaded_pdfs(pdf_files, input_dir)
        codex_result_path = output_dir / "codex_result.json"
        fake_result = build_fake_codex_result(sample, job_id=job_id, source_files=input_files)
        codex_result_path.write_text(json.dumps(fake_result, ensure_ascii=False, indent=2), encoding="utf-8")

        contract_result = await asyncio.to_thread(
            run_codex_worker_contract,
            codex_result_path,
            output_dir,
            excel_stem="four_field",
        )
        status_payload = _job_payload_from_contract_result(
            job_id=job_id,
            mode="fake",
            created_at=created_at,
            created_by=current_user.username,
            input_files=input_files,
            output_dir=output_dir,
            contract_result=contract_result,
        )
    except HTTPException as exc:
        status_payload = {
            "job_id": job_id,
            "mode": "fake",
            "status": "failed",
            "stage": "request_validation_failed",
            "created_at": created_at,
            "updated_at": _utc_now(),
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": _output_files(job_id, output_dir),
            "errors": [{"code": "REQUEST_VALIDATION_FAILED", "message": str(exc.detail)}],
            "warnings": [],
        }
        _write_job_status(job_dir, status_payload)
        raise
    except Exception as exc:
        status_payload = {
            "job_id": job_id,
            "mode": "fake",
            "status": "failed",
            "stage": "failed",
            "created_at": created_at,
            "updated_at": _utc_now(),
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": _output_files(job_id, output_dir),
            "errors": [{"code": "FAKE_CODEX_WORKER_FAILED", "message": str(exc)}],
            "warnings": [],
        }
        _write_job_status(job_dir, status_payload)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Codex Worker fake 任务失败：{exc}") from exc

    _write_job_status(job_dir, status_payload)
    return api_ok(status_payload)


@router.post("/admin/codex-worker/jobs/openai", status_code=status.HTTP_202_ACCEPTED, summary="Codex Worker POC：创建 OpenAI 真实识图任务")
async def create_openai_codex_style_worker_job(
    pdf_files: list[UploadFile] = File(...),
    max_views: int = Query(8, ge=1, le=24),
    current_user: User = Depends(get_current_user),
):
    _require_worker_access(current_user)
    if not pdf_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传至少一份 PDF 图纸")

    job_id = f"codexpdf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    job_dir = JOB_ROOT / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    agent_dir = job_dir / "agent"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)

    created_at = _utc_now()
    _write_job_status(
        job_dir,
        {
            "job_id": job_id,
            "mode": "openai_codex_style",
            "status": "running",
            "stage": "saving_uploads",
            "created_at": created_at,
            "updated_at": created_at,
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": [],
            "errors": [],
            "warnings": [],
        },
    )

    try:
        input_files = await _save_uploaded_pdfs(pdf_files, input_dir)
        _write_job_status(
            job_dir,
            {
                "job_id": job_id,
                "mode": "openai_codex_style",
                "status": "running",
                "stage": "openai_pdf_agent_running",
                "created_at": created_at,
                "updated_at": _utc_now(),
                "created_by": current_user.username,
                "input_files": input_files,
                "summary": {"max_views": max_views},
                "files": [],
                "errors": [],
                "warnings": [],
            },
        )
        agent_report = await asyncio.to_thread(
            run_pdf_agent_itemization_openai,
            pdf_dir=input_dir,
            output_dir=agent_dir,
            timestamp="run",
            max_views=max_views,
            username=current_user.username,
            trace_id=job_id,
        )
        codex_result_path = output_dir / "codex_result.json"
        worker_report_path = output_dir / "worker_report.json"
        codex_result = _codex_result_from_openai_agent_report(
            job_id=job_id,
            input_files=input_files,
            agent_report=agent_report,
        )
        codex_result_path.write_text(json.dumps(codex_result, ensure_ascii=False, indent=2), encoding="utf-8")
        worker_report_path.write_text(json.dumps(_safe_json(agent_report), ensure_ascii=False, indent=2), encoding="utf-8")

        contract_result = await asyncio.to_thread(
            run_codex_worker_contract,
            codex_result_path,
            output_dir,
            excel_stem="four_field",
        )
        status_payload = _job_payload_from_contract_result(
            job_id=job_id,
            mode="openai_codex_style",
            created_at=created_at,
            created_by=current_user.username,
            input_files=input_files,
            output_dir=output_dir,
            contract_result=contract_result,
        )
        status_payload["summary"]["agent_selected_view_count"] = (agent_report.get("summary") or {}).get("selected_view_count", 0)
        status_payload["summary"]["agent_bill_item_count"] = (agent_report.get("summary") or {}).get("agent_bill_item_count", 0)
        status_payload["summary"]["agent_evidence_count"] = (agent_report.get("summary") or {}).get("agent_evidence_count", 0)
        agent_issues = _agent_issues_for_status(agent_report)
        if agent_issues:
            status_payload["summary"]["agent_issue_count"] = len(agent_issues)
            status_payload["warnings"] = [*list(status_payload.get("warnings") or []), *agent_issues]
    except HTTPException as exc:
        status_payload = {
            "job_id": job_id,
            "mode": "openai_codex_style",
            "status": "failed",
            "stage": "request_validation_failed",
            "created_at": created_at,
            "updated_at": _utc_now(),
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": _output_files(job_id, output_dir),
            "errors": [{"code": "REQUEST_VALIDATION_FAILED", "message": str(exc.detail)}],
            "warnings": [],
        }
        _write_job_status(job_dir, status_payload)
        raise
    except Exception as exc:
        status_payload = {
            "job_id": job_id,
            "mode": "openai_codex_style",
            "status": "failed",
            "stage": "failed",
            "created_at": created_at,
            "updated_at": _utc_now(),
            "created_by": current_user.username,
            "input_files": [],
            "summary": {},
            "files": _output_files(job_id, output_dir),
            "errors": [{"code": "OPENAI_CODEX_STYLE_WORKER_FAILED", "message": str(exc)}],
            "warnings": [],
        }
        _write_job_status(job_dir, status_payload)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"OpenAI Codex-style 任务失败：{exc}") from exc

    _write_job_status(job_dir, status_payload)
    return api_ok(status_payload)


@router.get("/admin/codex-worker/jobs/{job_id}", summary="Codex Worker POC：查询任务状态")
def get_codex_worker_job(job_id: str, current_user: User = Depends(get_current_user)):
    _require_worker_access(current_user)
    job_dir = _resolve_job_dir(job_id)
    status_path = job_dir / STATUS_FILE_NAME
    if not status_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND")
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"任务状态读取失败：{exc}") from exc
    payload["files"] = _output_files(job_id, job_dir / "output")
    return api_ok(payload)


@router.get("/admin/codex-worker/jobs/{job_id}/files/{file_path:path}", summary="Codex Worker POC：下载任务输出文件")
def download_codex_worker_job_file(
    job_id: str,
    file_path: str,
    current_user: User = Depends(get_current_user),
):
    _require_worker_access(current_user)
    job_dir = _resolve_job_dir(job_id)
    relative_path = Path(file_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FILE_NOT_FOUND")
    target = (job_dir / relative_path).resolve()
    if job_dir.resolve() not in target.parents or not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FILE_NOT_FOUND")
    return FileResponse(target, filename=target.name)


async def _save_uploaded_pdfs(files: list[UploadFile], input_dir: Path) -> list[dict[str, Any]]:
    saved_files: list[dict[str, Any]] = []
    for index, file in enumerate(files, start=1):
        original_name = file.filename or f"drawing_{index}.pdf"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_PDF_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持上传 .pdf 图纸文件")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{original_name} 文件为空")
        safe_name = _safe_filename(Path(original_name).stem)
        target_path = input_dir / f"{index:02d}_{safe_name}.pdf"
        target_path.write_bytes(content)
        saved_files.append(
            {
                "file_name": original_name,
                "saved_name": target_path.name,
                "path": target_path.relative_to(input_dir.parent).as_posix(),
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "page_count": None,
            }
        )
    return saved_files


def _job_payload_from_contract_result(
    *,
    job_id: str,
    mode: str,
    created_at: str,
    created_by: str,
    input_files: list[dict[str, Any]],
    output_dir: Path,
    contract_result: dict[str, Any],
) -> dict[str, Any]:
    validation_report = _read_json_file(Path(contract_result["validation_report"]))
    validation_summary = validation_report.get("summary") or {}
    status_text = "succeeded" if contract_result.get("ok") else "validation_failed"
    return {
        "job_id": job_id,
        "mode": mode,
        "status": status_text,
        "stage": contract_result.get("status") or status_text,
        "created_at": created_at,
        "updated_at": _utc_now(),
        "created_by": created_by,
        "input_files": input_files,
        "summary": {
            "quantity_list_row_count": contract_result.get("quantity_list_row_count", 0),
            "error_count": validation_summary.get("error_count", 0),
            "warning_count": validation_summary.get("warning_count", 0),
            "validation_status": validation_report.get("status"),
        },
        "files": _output_files(job_id, output_dir),
        "errors": contract_result.get("errors", []),
        "warnings": contract_result.get("warnings", []),
    }


def _output_files(job_id: str, output_dir: Path) -> list[dict[str, Any]]:
    file_defs = [
        ("codex_result_json", "codex_result.json", "Codex Worker JSON"),
        ("validation_report", "validation_report.json", "合同校验报告"),
        ("worker_report_json", "worker_report.json", "识图过程报告 JSON"),
        ("four_field_xlsx", "four_field.xlsx", "四字段清单 Excel"),
        ("four_field_csv", "four_field.csv", "四字段清单 CSV"),
    ]
    files: list[dict[str, Any]] = []
    for key, file_name, label in file_defs:
        path = output_dir / file_name
        if not path.exists() or not path.is_file():
            continue
        relative = path.relative_to(output_dir.parent).as_posix()
        files.append(
            {
                "key": key,
                "label": label,
                "filename": path.name,
                "path": relative,
                "download_url": f"/api/v1/admin/codex-worker/jobs/{job_id}/files/{quote(relative, safe='/')}",
                "size_bytes": path.stat().st_size,
            }
        )
    return files


def _codex_result_from_openai_agent_report(
    *,
    job_id: str,
    input_files: list[dict[str, Any]],
    agent_report: dict[str, Any],
) -> dict[str, Any]:
    summary = agent_report.get("summary") if isinstance(agent_report.get("summary"), dict) else {}
    quantity_rows = [
        dict(row)
        for row in agent_report.get("quantity_list_rows") or []
        if isinstance(row, dict)
    ]
    mapping_rows = [
        dict(row)
        for row in agent_report.get("standard_mapping_rows") or []
        if isinstance(row, dict)
    ]
    evidence_rows = [
        dict(row)
        for row in agent_report.get("agent_evidence_rows") or []
        if isinstance(row, dict)
    ]
    filtered_items = [
        _codex_filtered_item_from_agent_item(item, index)
        for index, item in enumerate(agent_report.get("agent_filtered_items") or [], start=1)
        if isinstance(item, dict)
    ]
    quantity_list_rows = [
        _codex_quantity_row_from_agent_row(row, mapping_rows[index] if index < len(mapping_rows) else {}, index + 1)
        for index, row in enumerate(quantity_rows)
    ]
    return {
        "schema_version": "biz2x_codex_worker_result_v1",
        "job_id": job_id,
        "status": "needs_manual_review" if summary.get("itemizability_manual_review_count", 0) else "succeeded",
        "source_files": input_files,
        "summary": {
            "view_count": summary.get("selected_view_count", 0),
            "evidence_count": summary.get("agent_evidence_count", 0),
            "quantity_list_row_count": len(quantity_list_rows),
            "manual_review_count": summary.get("itemizability_manual_review_count", 0),
            "filtered_non_construction_count": len(filtered_items),
            "agent_bill_item_count": summary.get("agent_bill_item_count", 0),
            "standard_mapped_count": summary.get("standard_mapped_count", 0),
        },
        "quantity_list_rows": quantity_list_rows,
        "filtered_items": filtered_items,
        "evidence_index": [
            _codex_evidence_from_agent_evidence(row, index)
            for index, row in enumerate(evidence_rows, start=1)
        ],
        "standard_mapping_rows": [_safe_json(row) for row in mapping_rows],
        "issues": list(agent_report.get("issues") or []),
        "metrics": {},
    }


def _codex_quantity_row_from_agent_row(row: dict[str, Any], mapping_row: dict[str, Any], index: int) -> dict[str, Any]:
    source_item = mapping_row.get("source_item") if isinstance(mapping_row.get("source_item"), dict) else {}
    evidence_refs = _source_view_ids_to_evidence_refs(source_item.get("source_view_ids") or mapping_row.get("来源视图"))
    status_text = str(source_item.get("itemizability_status") or mapping_row.get("列项判断") or "待确认项").strip()
    return {
        "row_id": str(source_item.get("item_id") or mapping_row.get("识别编号") or f"CODPDF-ITEM-{index:06d}"),
        "项目名称": str(row.get("项目名称") or "").strip(),
        "项目特征": str(row.get("项目特征") or "").strip(),
        "单位": str(row.get("单位") or "").strip(),
        "工程量": str(row.get("工程量") or "").strip(),
        "itemizability_status": _normalize_codex_itemizability_status(status_text),
        "needs_manual_review": True,
        "evidence_refs": evidence_refs or [f"EV-{index:06d}"],
        "source_view_ids": list(source_item.get("source_view_ids") or []),
        "source_evidence": list(source_item.get("source_evidence") or []),
        "standard_item_name": str(mapping_row.get("标准项目名称") or "").strip(),
        "standard_item_code": str(mapping_row.get("标准项目编码") or "").strip(),
    }


def _codex_filtered_item_from_agent_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "item_id": str(item.get("item_id") or f"CODPDF-FILTER-{index:06d}"),
        "name": str(item.get("concrete_item_name") or "").strip(),
        "itemizability_status": "非施工项",
        "filter_reason": str(item.get("itemizability_reason") or item.get("reason") or "").strip(),
        "evidence_refs": _source_view_ids_to_evidence_refs(item.get("source_view_ids")) or [],
    }


def _codex_evidence_from_agent_evidence(row: dict[str, Any], index: int) -> dict[str, Any]:
    view_id = str(row.get("view_id") or "").strip()
    return {
        "evidence_id": f"EV-{view_id}" if view_id else f"EV-{index:06d}",
        "view_id": view_id,
        "view_title": str(row.get("view_title") or "").strip(),
        "view_type": str(row.get("view_type") or "").strip(),
        "text": _evidence_text(row),
        "confidence": row.get("confidence", 0),
        "needs_manual_review": bool(row.get("needs_manual_review", True)),
    }


def _evidence_text(row: dict[str, Any]) -> str:
    pieces: list[str] = []
    pieces.extend(str(text) for text in row.get("visible_texts") or [] if text)
    for material in row.get("material_codes") or []:
        if isinstance(material, dict):
            pieces.append(" ".join(str(material.get(key) or "") for key in ("code", "name_or_hint", "spec_or_method")).strip())
    for obj in row.get("objects") or []:
        if isinstance(obj, dict):
            pieces.append(" ".join(str(obj.get(key) or "") for key in ("name", "space", "method")).strip())
    pieces.extend(str(text) for text in row.get("methods") or [] if text)
    return "；".join(dict.fromkeys(piece for piece in pieces if piece))[:1000]


def _source_view_ids_to_evidence_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        items = []
    return [f"EV-{item}" for item in dict.fromkeys(items) if item]


def _normalize_codex_itemizability_status(value: str) -> str:
    text = str(value or "").strip()
    if text in {"施工项", "安装项", "定制项", "待确认项", "非施工项"}:
        return text
    if "非施工" in text:
        return "非施工项"
    if "安装" in text:
        return "安装项"
    if "定制" in text or "制作" in text:
        return "定制项"
    if "施工" in text:
        return "施工项"
    return "待确认项"


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _agent_issues_for_status(agent_report: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, issue in enumerate(agent_report.get("issues") or [], start=1):
        if not isinstance(issue, dict):
            continue
        issues.append(
            {
                "code": f"OPENAI_AGENT_ISSUE_{index:03d}",
                "level": str(issue.get("级别") or issue.get("level") or "warning"),
                "message": str(issue.get("说明") or issue.get("message") or issue)[:1000],
            }
        )
    return issues


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexWorkerContractError(f"JSON 文件读取失败：{path}") from exc
    if not isinstance(data, dict):
        raise CodexWorkerContractError(f"JSON 文件必须是对象：{path}")
    return data


def _write_job_status(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / STATUS_FILE_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", job_id or ""):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND")
    job_dir = (JOB_ROOT / job_id).resolve()
    root = JOB_ROOT.resolve()
    if root != job_dir and root not in job_dir.parents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND")
    if not job_dir.exists() or not job_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND")
    return job_dir


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "drawing"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
