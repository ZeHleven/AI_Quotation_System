from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.pricing_agent import (
    PricingAgentCandidateSelectIn,
    PricingAgentManualPriceIn,
    PricingAgentRunCreateIn,
)
from app.services.account_tenancy import AccountTenancyError
from app.services.pricing_agent import (
    PricingAgentError,
    confirm_pricing_agent_run_to_quote_draft,
    create_pricing_agent_run,
    get_pricing_agent_run,
    list_pricing_agent_runs,
    select_pricing_agent_candidate,
    serialize_pricing_agent_run,
    set_pricing_agent_manual_price,
)
from app.services.pricing_agent_hybrid import pricing_hybrid_configured
from app.services.pricing_archive_parser import PricingArchiveParseError, parse_demand_workbook
from app.services.pricing_archive_storage import PricingArchiveStorageError, selected_backend
from app.services.pricing_archives import (
    PricingArchiveError,
    disable_pricing_archive,
    import_pricing_archive,
    list_pricing_archives,
    serialize_pricing_archive,
)
from app.services.rbac import has_any_role


def require_pricing_agent_access(current_user: User = Depends(get_current_user)) -> None:
    if not has_any_role(current_user, {"system_admin", "admin", "quote_user", "quote_operator"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


router = APIRouter(prefix="/pricing-agent", dependencies=[Depends(require_pricing_agent_access)])


def _ensure_enabled() -> None:
    if not settings.feature_pricing_agent:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _http_error(exc: Any) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", 409),
        detail=getattr(exc, "detail", {"code": str(exc)}),
    )


@router.get("/capabilities", summary="报价 Agent 第一版能力与开关")
async def pricing_agent_capabilities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user, db
    _ensure_enabled()
    try:
        backend = selected_backend()
    except PricingArchiveStorageError as exc:
        backend = f"error:{exc}"
    return api_ok(
        {
            "version": "pricing-agent-v1.1",
            "rules_version": "pricing-agent-exact-v1.1",
            "expanded_rules_version": "pricing-agent-hybrid-v1.1",
            "isolated_from_existing_quote_flow": True,
            "sources": [
                {"value": "archive", "label": "存档数据", "available": True},
                {"value": "enterprise", "label": "企业数据", "available": True},
                {
                    "value": "industry",
                    "label": "行业数据",
                    "evidence_label": "行业数据·AI推算",
                    "available": bool(settings.feature_pricing_agent_industry_estimate),
                },
            ],
            "match_modes": [
                {
                    "value": "exact",
                    "label": "准确",
                    "channels": ["exact"],
                    "allows_industry": False,
                },
                {
                    "value": "expanded",
                    "label": "准确+近似（可匹配更多项）",
                    "channels": ["exact", "keyword", "vector"],
                    "vector_status": (
                        "configured"
                        if pricing_hybrid_configured()
                        else "keyword_fallback"
                    ),
                    "candidate_policy": "manual_adoption_required",
                    "available": bool(settings.feature_pricing_agent_expanded_match),
                },
            ],
            "archive_storage": {
                "backend": backend,
                "max_upload_mb": settings.pricing_agent_archive_max_upload_mb,
                "account_quota_gb": settings.pricing_agent_archive_account_quota_gb,
            },
        }
    )


@router.post("/demand-preview", summary="自动解析待套价需求清单")
async def preview_pricing_agent_demand(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user, db
    _ensure_enabled()
    max_bytes = max(int(settings.pricing_agent_archive_max_upload_mb), 1) * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "PRICING_AGENT_DEMAND_FILE_TOO_LARGE",
                "max_upload_mb": settings.pricing_agent_archive_max_upload_mb,
            },
        )
    try:
        parsed = parse_demand_workbook(content, file.filename or "demand.xlsx")
    except PricingArchiveParseError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    return api_ok(
        {
            "lines": list(parsed.lines),
            "summary": parsed.summary,
            "issues": list(parsed.issues),
        },
        message="需求清单已按固定系统字段自动识别",
    )


@router.post("/archives", summary="自动解析并导入历史带价清单")
async def upload_pricing_archive(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    max_bytes = max(int(settings.pricing_agent_archive_max_upload_mb), 1) * 1024 * 1024
    content = await file.read(max_bytes + 1)
    try:
        archive, duplicate = import_pricing_archive(
            db,
            current_user=current_user,
            filename=file.filename or "archive.xlsx",
            content=content,
            content_type=file.content_type,
        )
        db.commit()
        db.refresh(archive)
    except (PricingArchiveError, PricingArchiveParseError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except PricingArchiveStorageError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        serialize_pricing_archive(archive),
        message="该文件已存在，未重复占用空间" if duplicate else "存档文件已自动解析并建立精准索引",
        duplicate=duplicate,
    )


@router.get("/archives", summary="查询当前账户的存档数据")
async def list_pricing_archives_endpoint(
    include_disabled: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        rows, storage = list_pricing_archives(
            db,
            current_user=current_user,
            include_disabled=include_disabled,
        )
    except AccountTenancyError as exc:
        raise _http_error(exc) from exc
    return api_ok(
        {
            "items": [serialize_pricing_archive(row) for row in rows],
            "storage": storage,
        }
    )


@router.post("/archives/{archive_uuid}/disable", summary="停用一个存档数据源")
async def disable_pricing_archive_endpoint(
    archive_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        archive = disable_pricing_archive(
            db,
            current_user=current_user,
            archive_uuid=archive_uuid,
        )
        db.commit()
        db.refresh(archive)
    except (PricingArchiveError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    return api_ok(serialize_pricing_archive(archive), message="存档数据源已停用，原文件仍保留")


@router.post("/runs", summary="运行独立报价 Agent")
async def create_pricing_agent_run_endpoint(
    payload: PricingAgentRunCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    if payload.mode == "expanded" and not settings.feature_pricing_agent_expanded_match:
        raise HTTPException(status_code=403, detail="PRICING_AGENT_EXPANDED_MATCH_DISABLED")
    if "industry" in payload.sources and not settings.feature_pricing_agent_industry_estimate:
        raise HTTPException(status_code=403, detail="PRICING_AGENT_INDUSTRY_ESTIMATE_DISABLED")
    try:
        run = await create_pricing_agent_run(
            db,
            current_user=current_user,
            payload=payload,
        )
        db.commit()
        db.refresh(run)
    except (PricingAgentError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(serialize_pricing_agent_run(run), message="报价 Agent 已完成本次旁路测算")


@router.put(
    "/runs/{run_uuid}/lines/{line_uuid}/selection",
    summary="持久化人工采用的组价候选",
)
async def select_pricing_agent_candidate_endpoint(
    run_uuid: str,
    line_uuid: str,
    payload: PricingAgentCandidateSelectIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        run = select_pricing_agent_candidate(
            db,
            current_user=current_user,
            run_uuid=run_uuid,
            line_uuid=line_uuid,
            payload=payload,
        )
        db.commit()
        db.refresh(run)
    except (PricingAgentError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        serialize_pricing_agent_run(run),
        message="人工采用的候选结果已保存",
    )


@router.put(
    "/runs/{run_uuid}/lines/{line_uuid}/manual-price",
    summary="为未计价项目人工补充单价",
)
async def set_pricing_agent_manual_price_endpoint(
    run_uuid: str,
    line_uuid: str,
    payload: PricingAgentManualPriceIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        run = set_pricing_agent_manual_price(
            db,
            current_user=current_user,
            run_uuid=run_uuid,
            line_uuid=line_uuid,
            payload=payload,
        )
        db.commit()
        db.refresh(run)
    except (PricingAgentError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        serialize_pricing_agent_run(run),
        message="人工补价已保存",
    )


@router.post(
    "/runs/{run_uuid}/confirm-to-quote-draft",
    summary="确认组价结果并写入现有报价草稿",
)
async def confirm_pricing_agent_run_endpoint(
    run_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        confirmation = confirm_pricing_agent_run_to_quote_draft(
            db,
            current_user=current_user,
            run_uuid=run_uuid,
        )
        db.commit()
    except (PricingAgentError, AccountTenancyError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    return api_ok(
        confirmation,
        message="组价结果已确认并写入现有报价草稿",
    )


@router.get("/runs", summary="查询当前账户的报价 Agent 运行记录")
async def list_pricing_agent_runs_endpoint(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        rows, total = list_pricing_agent_runs(
            db,
            current_user=current_user,
            page=page,
            page_size=page_size,
        )
    except AccountTenancyError as exc:
        raise _http_error(exc) from exc
    return api_page(
        [serialize_pricing_agent_run(row, include_result=False) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_uuid}", summary="查询一次报价 Agent 的证据链")
async def get_pricing_agent_run_endpoint(
    run_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_enabled()
    try:
        run = get_pricing_agent_run(db, current_user=current_user, run_uuid=run_uuid)
    except (PricingAgentError, AccountTenancyError) as exc:
        raise _http_error(exc) from exc
    return api_ok(serialize_pricing_agent_run(run))
