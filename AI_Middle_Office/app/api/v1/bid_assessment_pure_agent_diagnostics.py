"""Administrator-only, read-only diagnostics for the isolated Pure Agent."""

from __future__ import annotations

from typing import Annotated, Any
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.agents.bid_assessment_pure.conversation_contracts import (
    PureAgentApiError,
    PureAgentApiErrorDetail,
    PureAgentApiSuccess,
)
from app.agents.bid_assessment_pure.diagnostic_contracts import (
    DiagnosticTaskPage,
    PureAgentDiagnosticSnapshot,
)
from app.agents.bid_assessment_pure.diagnostic_runtime import (
    PureAgentDiagnosticProjector,
)
from app.agents.bid_assessment_pure.repository import PureAgentNotFound
from app.agents.bid_assessment_pure.runtime_config import (
    PureAgentDisabledError,
    PureAgentFeatureConfig,
)
from app.agents.bid_assessment_pure.state import AgentTaskStatus
from app.core.config import settings
from app.core.database import get_db
from app.dependencies import require_admin
from app.models.user import User


logger = logging.getLogger(__name__)
_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$"


def _require_feature_enabled() -> None:
    try:
        PureAgentFeatureConfig.from_application_settings(settings).require_enabled()
    except PureAgentDisabledError as exc:
        raise HTTPException(status_code=404, detail="resource not found") from exc


router = APIRouter(
    prefix="/bid-assessment-pure-agent/admin/diagnostics",
    dependencies=[Depends(_require_feature_enabled)],
)

ApiReference = Annotated[
    str,
    Path(
        min_length=1,
        max_length=160,
        pattern=_REFERENCE_PATTERN,
    ),
]


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", ""))[:80]


def _ok(data: Any, request: Request) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "ok",
        "data": data,
        "error": None,
        "request_id": _request_id(request),
    }


def _error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    guidance: str | None = None,
) -> JSONResponse:
    payload = PureAgentApiError(
        code=status_code,
        message=message,
        error=PureAgentApiErrorDetail(
            code=code,
            retryable=retryable,
            guidance=guidance,
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


@router.get(
    "/tasks",
    response_model=PureAgentApiSuccess[DiagnosticTaskPage],
    summary="列出 Pure Agent 诊断 Task",
    operation_id="listBidAssessmentPureAgentDiagnosticTasks",
)
def list_diagnostic_tasks_endpoint(
    request: Request,
    status: AgentTaskStatus | None = Query(default=None),
    task_ref: str | None = Query(
        default=None,
        min_length=1,
        max_length=160,
        pattern=_REFERENCE_PATTERN,
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        result = PureAgentDiagnosticProjector(db).list_tasks(
            status=status,
            task_ref=task_ref,
            page=page,
            page_size=page_size,
        )
        return _ok(result, request)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_diagnostic_task_list_failed",
            extra={"request_id": _request_id(request)},
        )
        return _error(
            request,
            status_code=503,
            code="PURE_AGENT_DIAGNOSTICS_UNAVAILABLE",
            message="诊断视图暂时不可用",
            retryable=True,
            guidance="请稍后重试。",
        )


@router.get(
    "/tasks/{task_ref}",
    response_model=PureAgentApiSuccess[PureAgentDiagnosticSnapshot],
    summary="读取 Pure Agent 脱敏诊断快照",
    operation_id="getBidAssessmentPureAgentDiagnosticSnapshot",
)
def get_diagnostic_snapshot_endpoint(
    task_ref: ApiReference,
    request: Request,
    _current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        return _ok(
            PureAgentDiagnosticProjector(db).snapshot(task_ref=task_ref),
            request,
        )
    except PureAgentNotFound:
        db.rollback()
        return _error(
            request,
            status_code=404,
            code="PURE_AGENT_DIAGNOSTIC_TASK_NOT_FOUND",
            message="诊断 Task 不存在",
            retryable=False,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_diagnostic_snapshot_failed",
            extra={"request_id": _request_id(request)},
        )
        return _error(
            request,
            status_code=503,
            code="PURE_AGENT_DIAGNOSTICS_UNAVAILABLE",
            message="诊断视图暂时不可用",
            retryable=True,
            guidance="请稍后重试。",
        )
