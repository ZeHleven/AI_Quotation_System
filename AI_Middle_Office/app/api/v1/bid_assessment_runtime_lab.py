"""Actor-authorized runtime observability APIs for Phase 4 MVP-0/MVP-1."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.user import User
from app.schemas.bid_assessment import (
    BidEnterpriseBusinessBaselineCreateIn,
    BidEnterpriseCapabilitySnapshotCreateIn,
    BidEnterpriseEvidencePackageCreateIn,
    BidHardGateComparisonBaselineCreateIn,
    BidMvpReleaseCandidateCreateIn,
)
from app.services.bid_assessment_idempotency import (
    BidIdempotencyError,
    BidIdempotencyInProgress,
    BidIdempotencyKeyReused,
    IdempotentCommandResult,
    execute_idempotent_request,
    validate_idempotency_key,
)
from app.services.bid_enterprise_capability import (
    BidEnterpriseCapabilityError,
    freeze_enterprise_snapshot,
    latest_frozen_enterprise_snapshot,
    preview_enterprise_baseline,
)
from app.services.bid_enterprise_business_baseline import (
    BidEnterpriseBusinessBaselineError,
    freeze_enterprise_business_baseline,
    get_enterprise_business_baseline,
    preview_enterprise_business_baseline,
)
from app.services.bid_enterprise_evidence_import import (
    BidEnterpriseEvidenceImportError,
    freeze_enterprise_evidence_package,
    import_enterprise_evidence_item,
    latest_enterprise_evidence_package,
    list_enterprise_evidence_items,
    preview_enterprise_evidence_package,
)
from app.services.bid_hard_gate_fact_verification import (
    BidHardGateFactVerificationError,
    build_hard_gate_comparison_draft,
    freeze_hard_gate_comparison_baseline,
    get_hard_gate_comparison_baseline,
    preview_hard_gate_comparison_baseline,
)
from app.services.bid_upload_file_storage import get_bid_upload_object_storage
from app.services.bid_upload_files import BidUploadFileError, inspect_bid_upload
from app.services.bid_runtime_trace import (
    TRACE_REDACTION,
    TRACE_SCHEMA,
    build_runtime_trace,
    list_visible_runs,
    runtime_trace_headers,
)
from app.services.bid_mvp1_execute_preflight import build_execute_preflight
from app.services.bid_mvp_release_candidate import (
    BidMvpReleaseCandidateError,
    freeze_mvp_release_candidate,
    get_mvp_release_candidate,
    preview_mvp_release_candidate,
)
from app.services.rbac import has_admin_role


router = APIRouter()
logger = logging.getLogger(__name__)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_ENTERPRISE_SNAPSHOT_ROUTE = "/api/v1/bid-assessment-runtime-lab/enterprise-snapshots"
_ENTERPRISE_BUSINESS_BASELINE_ROUTE = (
    "/api/v1/bid-assessment-runtime-lab/enterprise-business-baselines"
)
_ENTERPRISE_EVIDENCE_ITEM_ROUTE = (
    "/api/v1/bid-assessment-runtime-lab/enterprise-evidence-items"
)
_ENTERPRISE_EVIDENCE_PACKAGE_ROUTE = (
    "/api/v1/bid-assessment-runtime-lab/enterprise-evidence-packages"
)
_MVP_RC_ROUTE = "/api/v1/bid-assessment-runtime-lab/release-candidates"
_HARD_GATE_COMPARISON_ROUTE = (
    "/api/v1/bid-assessment-runtime-lab/hard-gate-comparison-baselines"
)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", ""))[:80]


def _enabled() -> bool:
    return bool(
        settings.feature_bid_assessment_phase4_mvp0_trace or _mvp1_enabled()
    )


def _mvp1_enabled() -> bool:
    """The historical P0-P4 MVP executor is no longer an executable mode."""

    return False


def _mvp_rc_enabled() -> bool:
    return bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_mvp_release_candidate",
            False,
        )
    )


def _business_baseline_enabled() -> bool:
    return bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_business_baseline",
            False,
        )
    )


def _enterprise_evidence_import_enabled() -> bool:
    return bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_enterprise_evidence_import",
            False,
        )
    )


def _fact_verification_enabled() -> bool:
    return bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_fact_verification",
            False,
        )
    )


def _runtime_access(request: Request) -> dict[str, object]:
    """Project the immutable read-only boundary of the historical lab."""

    app_state = request.app.state
    local_mode = getattr(app_state, "bid_mvp1_access_mode", None)
    return {
        "local_lab": local_mode is not None,
        "access_mode": "view-only",
        "execution_enabled": False,
        "write_enabled": False,
        "worker_enabled": False,
        "worker_running": False,
        "model_calls_enabled": False,
        "model_provider": str(
            getattr(app_state, "bid_mvp1_model_provider", "configured_gateway")
        ),
        "retrieval_mode": str(
            getattr(app_state, "bid_mvp1_retrieval_mode", "configured")
        ),
    }


def _body(data, request: Request) -> dict:
    return {
        "code": 200,
        "message": "ok",
        "data": data,
        "error": None,
        "request_id": _request_id(request),
    }


def _not_found(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "code": 404,
            "message": "resource not found",
            "data": None,
            "error": {
                "code": "BID_RESOURCE_NOT_FOUND",
                "retryable": False,
            },
            "request_id": _request_id(request),
        },
    )


@router.get(
    "/bid-assessment-runtime-lab/capabilities",
    summary="Get Phase 4 runtime lab capabilities",
    operation_id="getBidAssessmentRuntimeLabCapabilities",
)
def get_runtime_lab_capabilities(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    runtime_access = _runtime_access(request)
    execution_enabled = bool(runtime_access["execution_enabled"])
    return _body(
        {
            "enabled": _enabled(),
            "mode": "mvp1" if execution_enabled else "read_only",
            **runtime_access,
            "mvp1_enabled": execution_enabled,
            "assessment_intake_enabled": execution_enabled,
            "legacy_workflow_removed": True,
            "pure_agent_runtime_ready": False,
            "preliminary_report_enabled": bool(
                settings.feature_bid_assessment_phase4_preliminary_report
            ),
            "enterprise_capability_enabled": bool(
                settings.feature_bid_assessment_phase4_enterprise_capability
            ),
            "enterprise_snapshot_configurable": bool(
                runtime_access["local_lab"]
                and runtime_access["write_enabled"]
                and settings.feature_bid_assessment_phase4_enterprise_capability
                and has_admin_role(current_user)
            ),
            "mvp_release_candidate_enabled": bool(
                _mvp_rc_enabled()
            ),
            "mvp_release_candidate_configurable": bool(
                runtime_access["local_lab"]
                and runtime_access["write_enabled"]
                and _mvp_rc_enabled()
                and has_admin_role(current_user)
            ),
            "business_baseline_enabled": _business_baseline_enabled(),
            "business_baseline_configurable": bool(
                runtime_access["local_lab"]
                and runtime_access["write_enabled"]
                and _business_baseline_enabled()
                and has_admin_role(current_user)
            ),
            "enterprise_evidence_import_enabled": _enterprise_evidence_import_enabled(),
            "enterprise_evidence_import_configurable": bool(
                runtime_access["local_lab"]
                and runtime_access["write_enabled"]
                and _enterprise_evidence_import_enabled()
                and has_admin_role(current_user)
            ),
            "fact_verification_enabled": _fact_verification_enabled(),
            "fact_verification_configurable": bool(
                runtime_access["local_lab"]
                and runtime_access["write_enabled"]
                and _fact_verification_enabled()
                and has_admin_role(current_user)
            ),
            "schema": TRACE_SCHEMA,
            "live_updates": (
                "authorized_sse_plus_snapshot_refresh"
                if settings.feature_bid_assessment_v1_runtime and execution_enabled
                else "snapshot_refresh_only"
            ),
            "live_sse_enabled": bool(
                settings.feature_bid_assessment_v1_runtime and execution_enabled
            ),
            "redaction": TRACE_REDACTION,
            "required_local_flags": [
                (
                    "FEATURE_BID_ASSESSMENT_PHASE4_MVP"
                    if execution_enabled
                    else (
                        "BID_MVP1_LOCAL_ACCESS_MODE=execute"
                        if _mvp1_enabled() and bool(runtime_access["local_lab"])
                        else "FEATURE_BID_ASSESSMENT_PHASE4_MVP0_TRACE"
                    )
                ),
                *(
                    ["FEATURE_BID_ASSESSMENT_PHASE4_MVP_RELEASE_CANDIDATE"]
                    if _mvp_rc_enabled()
                    else []
                ),
                *(
                    ["FEATURE_BID_ASSESSMENT_PHASE4_BUSINESS_BASELINE"]
                    if _business_baseline_enabled()
                    else []
                ),
                *(
                    ["FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_EVIDENCE_IMPORT"]
                    if _enterprise_evidence_import_enabled()
                    else []
                ),
                *(
                    ["FEATURE_BID_ASSESSMENT_PHASE4_FACT_VERIFICATION"]
                    if _fact_verification_enabled()
                    else []
                ),
            ],
            "optional_live_sse_flag": "FEATURE_BID_ASSESSMENT_V1_RUNTIME",
        },
        request,
    )


@router.get(
    "/bid-assessment-runtime-lab/enterprise-snapshot",
    summary="Get the latest frozen enterprise capability snapshot",
    operation_id="getBidAssessmentRuntimeLabEnterpriseSnapshot",
)
def get_runtime_lab_enterprise_snapshot(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not settings.feature_bid_assessment_phase4_enterprise_capability
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    try:
        snapshot = latest_frozen_enterprise_snapshot(db, include_values=True)
    except BidEnterpriseCapabilityError:
        db.rollback()
        return _body({"snapshot": None, "legacy_snapshot_present": True}, request)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_enterprise_snapshot_read_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "enterprise capability snapshot is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return _body({"snapshot": snapshot}, request)


@router.post(
    "/bid-assessment-runtime-lab/enterprise-baseline/validate",
    summary="Validate and diff an enterprise baseline without persisting it",
    operation_id="validateBidAssessmentRuntimeLabEnterpriseBaseline",
)
def validate_runtime_lab_enterprise_baseline(
    payload: BidEnterpriseCapabilitySnapshotCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not settings.feature_bid_assessment_phase4_enterprise_capability
    ):
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        projection = preview_enterprise_baseline(
            db,
            command=payload.model_dump(),
        )
    except BidEnterpriseCapabilityError:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise baseline validation failed",
                "data": None,
                "error": {"code": "BID_ENTERPRISE_BASELINE_INVALID", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    return _body(projection, request)


@router.post(
    "/bid-assessment-runtime-lab/enterprise-snapshots",
    status_code=201,
    summary="Freeze an immutable local enterprise capability snapshot",
    operation_id="createBidAssessmentRuntimeLabEnterpriseSnapshot",
)
def create_runtime_lab_enterprise_snapshot(
    payload: BidEnterpriseCapabilitySnapshotCreateIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    candidate_hash: str | None = Header(
        default=None,
        alias="X-Enterprise-Candidate-Hash",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not settings.feature_bid_assessment_phase4_enterprise_capability
    ):
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_candidate_hash = str(candidate_hash or "").strip().lower() or None
    if normalized_candidate_hash is not None and not re.fullmatch(
        r"[0-9a-f]{64}", normalized_candidate_hash
    ):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "X-Enterprise-Candidate-Hash must be a SHA-256 hex value",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = payload.model_dump()
    request_payload = {
        **payload.model_dump(mode="json"),
        "_candidate_snapshot_hash": normalized_candidate_hash,
    }

    def _freeze(command_db: Session) -> IdempotentCommandResult:
        result = freeze_enterprise_snapshot(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            request_id=_request_id(request),
            expected_snapshot_hash=normalized_candidate_hash,
        )
        body = {**result.projection, "created": bool(result.created)}
        return IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="enterprise_snapshot",
            resource_id=str(result.snapshot.id),
            response_ref=f"enterprise-snapshot:{result.snapshot.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_ENTERPRISE_SNAPSHOT_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_freeze,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same enterprise snapshot request is still in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another payload",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidEnterpriseCapabilityError as exc:
        db.rollback()
        error_code = (
            "BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH"
            if str(exc) == "BID_ENTERPRISE_CANDIDATE_HASH_MISMATCH"
            else "BID_ENTERPRISE_SNAPSHOT_INVALID"
        )
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise capability snapshot could not be frozen",
                "data": None,
                "error": {"code": error_code, "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_enterprise_snapshot_create_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "enterprise capability snapshot is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


@router.get(
    "/bid-assessment-runtime-lab/hard-gate-comparison-draft",
    summary="Build a non-persistent hard-gate fact review draft",
    operation_id="getBidAssessmentRuntimeLabHardGateComparisonDraft",
)
def get_runtime_lab_hard_gate_comparison_draft(
    request: Request,
    assessment_id: str = Query(min_length=1, max_length=80),
    source_run_id: str = Query(min_length=1, max_length=80),
    business_baseline_id: str = Query(min_length=1, max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _fact_verification_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    try:
        draft = build_hard_gate_comparison_draft(
            db,
            assessment_id=assessment_id,
            source_run_id=source_run_id,
            business_baseline_id=business_baseline_id,
        )
    except BidHardGateFactVerificationError:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "hard-gate comparison draft is unavailable",
                "data": None,
                "error": {
                    "code": "BID_HARD_GATE_COMPARISON_SOURCE_NOT_READY",
                    "retryable": False,
                },
                "request_id": _request_id(request),
            },
        )
    return _body(draft, request)


@router.get(
    "/bid-assessment-runtime-lab/hard-gate-comparison-baseline",
    summary="Get the latest immutable hard-gate comparison baseline",
    operation_id="getBidAssessmentRuntimeLabHardGateComparisonBaseline",
)
def get_runtime_lab_hard_gate_comparison_baseline(
    request: Request,
    assessment_id: str | None = Query(default=None, max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _fact_verification_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    return _body(
        {
            "comparison_baseline": get_hard_gate_comparison_baseline(
                db,
                assessment_id=assessment_id,
            )
        },
        request,
    )


@router.post(
    "/bid-assessment-runtime-lab/hard-gate-comparison-baselines/validate",
    summary="Validate hard-gate comparable facts without persistence",
    operation_id="validateBidAssessmentRuntimeLabHardGateComparisonBaseline",
)
def validate_runtime_lab_hard_gate_comparison_baseline(
    payload: BidHardGateComparisonBaselineCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _fact_verification_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        projection = preview_hard_gate_comparison_baseline(
            db,
            actor_id=int(current_user.id),
            command=payload.model_dump(),
        )
    except BidHardGateFactVerificationError:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "hard-gate comparison baseline validation failed",
                "data": None,
                "error": {
                    "code": "BID_HARD_GATE_COMPARISON_BASELINE_INVALID",
                    "retryable": False,
                },
                "request_id": _request_id(request),
            },
        )
    return _body(projection, request)


@router.post(
    "/bid-assessment-runtime-lab/hard-gate-comparison-baselines",
    status_code=201,
    summary="Freeze immutable hard-gate comparable facts",
    operation_id="createBidAssessmentRuntimeLabHardGateComparisonBaseline",
)
def create_runtime_lab_hard_gate_comparison_baseline(
    payload: BidHardGateComparisonBaselineCreateIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    candidate_hash: str | None = Header(
        default=None,
        alias="X-Hard-Gate-Comparison-Candidate-Hash",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _fact_verification_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_candidate_hash = str(candidate_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_candidate_hash):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": (
                    "X-Hard-Gate-Comparison-Candidate-Hash must be a SHA-256 hex value"
                ),
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = payload.model_dump()
    request_payload = {
        **payload.model_dump(mode="json"),
        "_candidate_hash": normalized_candidate_hash,
    }

    def _freeze(command_db: Session) -> IdempotentCommandResult:
        result = freeze_hard_gate_comparison_baseline(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            request_id=_request_id(request),
            expected_candidate_hash=normalized_candidate_hash,
        )
        body = {**result.projection, "created": bool(result.created)}
        return IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="hard_gate_comparison_baseline",
            resource_id=str(result.baseline.id),
            response_ref=f"hard-gate-comparison-baseline:{result.baseline.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_HARD_GATE_COMPARISON_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_freeze,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same comparison baseline request is still in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another payload",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidHardGateFactVerificationError as exc:
        db.rollback()
        error_code = (
            "BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_MISMATCH"
            if str(exc) == "BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_MISMATCH"
            else "BID_HARD_GATE_COMPARISON_BASELINE_INVALID"
        )
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "hard-gate comparison baseline could not be frozen",
                "data": None,
                "error": {"code": error_code, "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_hard_gate_comparison_baseline_create_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "hard-gate comparison baseline is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


@router.get(
    "/bid-assessment-runtime-lab/enterprise-evidence-items",
    summary="List governed enterprise evidence items",
    operation_id="listBidAssessmentRuntimeLabEnterpriseEvidenceItems",
)
def list_runtime_lab_enterprise_evidence_items(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _enterprise_evidence_import_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    return _body({"items": list_enterprise_evidence_items(db)}, request)


@router.get(
    "/bid-assessment-runtime-lab/enterprise-evidence-package",
    summary="Get the latest governed enterprise evidence package",
    operation_id="getBidAssessmentRuntimeLabEnterpriseEvidencePackage",
)
def get_runtime_lab_enterprise_evidence_package(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _enterprise_evidence_import_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    return _body(
        {"evidence_package": latest_enterprise_evidence_package(db)},
        request,
    )


@router.post(
    "/bid-assessment-runtime-lab/enterprise-evidence-items",
    status_code=201,
    summary="Import one immutable enterprise evidence file",
    operation_id="createBidAssessmentRuntimeLabEnterpriseEvidenceItem",
)
async def create_runtime_lab_enterprise_evidence_item(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_content_sha256: str | None = Header(
        default=None,
        alias="X-Content-SHA256",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _enterprise_evidence_import_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_content_sha256 = str(expected_content_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_content_sha256):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "X-Content-SHA256 must be a SHA-256 hex value",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        form = await request.form(max_files=1, max_fields=7, max_part_size=1024 * 1024)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "message": "enterprise evidence multipart request is malformed",
                "data": None,
                "error": {"code": "BID_REQUEST_MALFORMED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    allowed_parts = {
        "file",
        "evidence_class",
        "source_record_id",
        "source_version",
        "source_label",
        "valid_from",
        "valid_to",
    }
    upload_file = form.get("file")
    if (
        set(form.keys()) - allowed_parts
        or any(len(form.getlist(name)) > 1 for name in allowed_parts)
        or not isinstance(upload_file, StarletteUploadFile)
    ):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "enterprise evidence upload fields are invalid",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        inspection = await inspect_bid_upload(
            upload_file,
            expected_sha256=normalized_content_sha256,
        )
    except BidUploadFileError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "enterprise evidence file failed bounded inspection",
                "data": None,
                "error": {"code": str(getattr(exc, "code", "BID_FILE_CONTENT_INVALID")), "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = {
        "evidence_class": form.get("evidence_class"),
        "source_record_id": form.get("source_record_id"),
        "source_version": form.get("source_version"),
        "source_label": form.get("source_label"),
        "valid_from": form.get("valid_from") or None,
        "valid_to": form.get("valid_to") or None,
    }
    request_payload = {
        **command,
        "file": {
            "filename": inspection.filename,
            "mime_type": inspection.canonical_mime_type,
            "size_bytes": inspection.size_bytes,
            "sha256": inspection.sha256,
        },
    }

    def _import_item(command_db: Session) -> IdempotentCommandResult:
        result = import_enterprise_evidence_item(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            file_stream=upload_file.file,
            inspection=inspection,
            request_id=_request_id(request),
            storage=get_bid_upload_object_storage(),
        )
        body = {**result.projection, "created": bool(result.created)}
        return IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="enterprise_evidence_item",
            resource_id=str(result.item.id),
            response_ref=f"enterprise-evidence-item:{result.item.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_ENTERPRISE_EVIDENCE_ITEM_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_import_item,
            processing_timeout_seconds=max(
                60,
                int(settings.bid_upload_processing_timeout_seconds),
            ),
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same evidence import is still in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another evidence file",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidEnterpriseEvidenceImportError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise evidence item could not be imported",
                "data": None,
                "error": {
                    "code": "BID_ENTERPRISE_EVIDENCE_IMPORT_INVALID",
                    "retryable": False,
                },
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_enterprise_evidence_item_import_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "enterprise evidence import is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


@router.post(
    "/bid-assessment-runtime-lab/enterprise-evidence-packages/validate",
    summary="Validate an enterprise evidence package without persistence",
    operation_id="validateBidAssessmentRuntimeLabEnterpriseEvidencePackage",
)
def validate_runtime_lab_enterprise_evidence_package(
    payload: BidEnterpriseEvidencePackageCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _enterprise_evidence_import_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        projection = preview_enterprise_evidence_package(
            db,
            actor_id=int(current_user.id),
            command=payload.model_dump(),
        )
    except BidEnterpriseEvidenceImportError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise evidence package validation failed",
                "data": None,
                "error": {
                    "code": "BID_ENTERPRISE_EVIDENCE_IMPORT_INVALID",
                    "retryable": False,
                },
                "request_id": _request_id(request),
            },
        )
    return _body(projection, request)


@router.post(
    "/bid-assessment-runtime-lab/enterprise-evidence-packages",
    status_code=201,
    summary="Freeze an immutable enterprise evidence package",
    operation_id="createBidAssessmentRuntimeLabEnterpriseEvidencePackage",
)
def create_runtime_lab_enterprise_evidence_package(
    payload: BidEnterpriseEvidencePackageCreateIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    candidate_hash: str | None = Header(
        default=None,
        alias="X-Enterprise-Evidence-Candidate-Hash",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _enterprise_evidence_import_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_candidate_hash = str(candidate_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_candidate_hash):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "X-Enterprise-Evidence-Candidate-Hash must be SHA-256 hex",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = payload.model_dump()
    request_payload = {
        **payload.model_dump(mode="json"),
        "_candidate_hash": normalized_candidate_hash,
    }

    def _freeze(command_db: Session) -> IdempotentCommandResult:
        result = freeze_enterprise_evidence_package(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            request_id=_request_id(request),
            expected_candidate_hash=normalized_candidate_hash,
        )
        body = {**result.projection, "created": bool(result.created)}
        return IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="enterprise_evidence_package",
            resource_id=str(result.package.id),
            response_ref=f"enterprise-evidence-package:{result.package.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_ENTERPRISE_EVIDENCE_PACKAGE_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_freeze,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same evidence package request is in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another payload",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidEnterpriseEvidenceImportError as exc:
        db.rollback()
        internal_code = str(exc)
        public_code = (
            internal_code
            if internal_code
            in {
                "BID_ENTERPRISE_EVIDENCE_CANDIDATE_HASH_MISMATCH",
                "BID_ENTERPRISE_EVIDENCE_PACKAGE_NOT_READY",
            }
            else "BID_ENTERPRISE_EVIDENCE_IMPORT_INVALID"
        )
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise evidence package could not be frozen",
                "data": None,
                "error": {"code": public_code, "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_enterprise_evidence_package_create_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "enterprise evidence package is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


@router.get(
    "/bid-assessment-runtime-lab/enterprise-business-baseline",
    summary="Get the latest verified enterprise business baseline",
    operation_id="getBidAssessmentRuntimeLabEnterpriseBusinessBaseline",
)
def get_runtime_lab_enterprise_business_baseline(
    request: Request,
    snapshot_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _business_baseline_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    return _body(
        {
            "business_baseline": get_enterprise_business_baseline(
                db,
                snapshot_id=snapshot_id,
            )
        },
        request,
    )


@router.post(
    "/bid-assessment-runtime-lab/enterprise-business-baselines/validate",
    summary="Validate a real enterprise business baseline without persistence",
    operation_id="validateBidAssessmentRuntimeLabEnterpriseBusinessBaseline",
)
def validate_runtime_lab_enterprise_business_baseline(
    payload: BidEnterpriseBusinessBaselineCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _business_baseline_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        projection = preview_enterprise_business_baseline(
            db,
            actor_id=int(current_user.id),
            command=payload.model_dump(),
        )
    except BidEnterpriseBusinessBaselineError:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise business baseline validation failed",
                "data": None,
                "error": {
                    "code": "BID_ENTERPRISE_BUSINESS_BASELINE_INVALID",
                    "retryable": False,
                },
                "request_id": _request_id(request),
            },
        )
    return _body(projection, request)


@router.post(
    "/bid-assessment-runtime-lab/enterprise-business-baselines",
    status_code=201,
    summary="Freeze an immutable verified enterprise business baseline",
    operation_id="createBidAssessmentRuntimeLabEnterpriseBusinessBaseline",
)
def create_runtime_lab_enterprise_business_baseline(
    payload: BidEnterpriseBusinessBaselineCreateIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    candidate_hash: str | None = Header(
        default=None,
        alias="X-Enterprise-Business-Candidate-Hash",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if not runtime_access["local_lab"] or not _business_baseline_enabled():
        return _not_found(request)
    if not has_admin_role(current_user):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_candidate_hash = str(candidate_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_candidate_hash):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "X-Enterprise-Business-Candidate-Hash must be a SHA-256 hex value",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = payload.model_dump()
    request_payload = {
        **payload.model_dump(mode="json"),
        "_candidate_hash": normalized_candidate_hash,
    }

    def _freeze(command_db: Session) -> IdempotentCommandResult:
        result = freeze_enterprise_business_baseline(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            request_id=_request_id(request),
            expected_candidate_hash=normalized_candidate_hash,
        )
        body = {**result.projection, "created": bool(result.created)}
        return IdempotentCommandResult(
            status_code=201,
            body=body,
            resource_type="enterprise_business_baseline",
            resource_id=str(result.baseline.id),
            response_ref=f"enterprise-business-baseline:{result.baseline.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_ENTERPRISE_BUSINESS_BASELINE_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_freeze,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same business baseline request is still in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another payload",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidEnterpriseBusinessBaselineError as exc:
        db.rollback()
        code = str(exc)
        mapped_code = (
            code
            if code.startswith("BID_ENTERPRISE_BUSINESS_")
            else "BID_ENTERPRISE_BUSINESS_BASELINE_INVALID"
        )
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "enterprise business baseline could not be frozen",
                "data": None,
                "error": {"code": mapped_code, "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_enterprise_business_baseline_create_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "enterprise business baseline is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


def _visible_release_run(
    db: Session,
    *,
    run_id: str,
    current_user: User,
) -> BidAnalysisRun | None:
    query = (
        db.query(BidAnalysisRun)
        .join(BidAssessment, BidAssessment.id == BidAnalysisRun.assessment_id)
        .filter(BidAnalysisRun.id == run_id)
    )
    if not has_admin_role(current_user):
        query = query.filter(BidAssessment.created_by == int(current_user.id))
    return query.one_or_none()


@router.get(
    "/bid-assessment-runtime-lab/release-candidate",
    summary="Get the immutable MVP release candidate for one visible Run",
    operation_id="getBidAssessmentRuntimeLabReleaseCandidate",
)
def get_runtime_lab_release_candidate(
    request: Request,
    run_id: str = Query(min_length=1, max_length=80),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _mvp_rc_enabled()
        or not _ID_PATTERN.fullmatch(run_id)
    ):
        return _not_found(request)
    if _visible_release_run(db, run_id=run_id, current_user=current_user) is None:
        return _not_found(request)
    try:
        projection = get_mvp_release_candidate(db, run_id=run_id)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_mvp_release_candidate_read_failed",
            extra={"request_id": _request_id(request), "run_id": run_id},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "MVP release candidate is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return _body({"release_candidate": projection}, request)


@router.post(
    "/bid-assessment-runtime-lab/release-candidates/validate",
    summary="Validate a business acceptance without persisting it",
    operation_id="validateBidAssessmentRuntimeLabReleaseCandidate",
)
def validate_runtime_lab_release_candidate(
    payload: BidMvpReleaseCandidateCreateIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _mvp_rc_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    if _visible_release_run(
        db,
        run_id=str(payload.run_id),
        current_user=current_user,
    ) is None:
        return _not_found(request)
    try:
        projection = preview_mvp_release_candidate(
            db,
            actor_id=int(current_user.id),
            command=payload.model_dump(),
        )
    except BidMvpReleaseCandidateError:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "MVP release candidate validation failed",
                "data": None,
                "error": {"code": "BID_MVP_RC_INVALID", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_mvp_release_candidate_validate_failed",
            extra={"request_id": _request_id(request), "run_id": str(payload.run_id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "MVP release candidate validation is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return _body(projection, request)


@router.post(
    "/bid-assessment-runtime-lab/release-candidates",
    status_code=201,
    summary="Freeze one immutable business-accepted MVP release candidate",
    operation_id="createBidAssessmentRuntimeLabReleaseCandidate",
)
def create_runtime_lab_release_candidate(
    payload: BidMvpReleaseCandidateCreateIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    candidate_hash: str | None = Header(default=None, alias="X-MVP-RC-Candidate-Hash"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime_access = _runtime_access(request)
    if (
        not runtime_access["local_lab"]
        or not _mvp_rc_enabled()
        or not has_admin_role(current_user)
    ):
        return _not_found(request)
    if not runtime_access["write_enabled"]:
        return JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is not authorized for writes",
                "data": None,
                "error": {"code": "BID_MVP1_VIEW_ONLY", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    if _visible_release_run(
        db,
        run_id=str(payload.run_id),
        current_user=current_user,
    ) is None:
        return _not_found(request)
    try:
        normalized_key = validate_idempotency_key(str(idempotency_key or ""))
    except BidIdempotencyError:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "Idempotency-Key must be 16-128 printable ASCII characters",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    normalized_candidate_hash = str(candidate_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_candidate_hash):
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": "X-MVP-RC-Candidate-Hash must be a SHA-256 hex value",
                "data": None,
                "error": {"code": "BID_REQUEST_VALIDATION_FAILED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    command = payload.model_dump()
    request_payload = {
        **payload.model_dump(mode="json"),
        "_candidate_hash": normalized_candidate_hash,
    }

    def _freeze(command_db: Session) -> IdempotentCommandResult:
        result = freeze_mvp_release_candidate(
            command_db,
            actor_id=int(current_user.id),
            command=command,
            request_id=_request_id(request),
            expected_candidate_hash=normalized_candidate_hash,
        )
        return IdempotentCommandResult(
            status_code=201,
            body={**result.projection, "created": bool(result.created)},
            resource_type="mvp_release_candidate",
            resource_id=str(result.release.id),
            response_ref=f"mvp-release-candidate:{result.release.id}",
        )

    try:
        execution = execute_idempotent_request(
            db,
            actor_id=int(current_user.id),
            http_method="POST",
            route_template=_MVP_RC_ROUTE,
            idempotency_key=normalized_key,
            request_payload=request_payload,
            request_id=_request_id(request),
            handler=_freeze,
        )
        db.commit()
    except BidIdempotencyInProgress:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "the same release freeze request is still in progress",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_IN_PROGRESS", "retryable": True},
                "request_id": _request_id(request),
            },
            headers={"Retry-After": "2"},
        )
    except BidIdempotencyKeyReused:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "Idempotency-Key was already used for another payload",
                "data": None,
                "error": {"code": "BID_IDEMPOTENCY_KEY_REUSED", "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except BidMvpReleaseCandidateError as exc:
        db.rollback()
        error_code = str(exc)
        if error_code not in {
            "BID_MVP_RC_CANDIDATE_HASH_MISMATCH",
            "BID_MVP_RC_ALREADY_FROZEN",
            "BID_MVP_RC_NOT_READY",
        }:
            error_code = "BID_MVP_RC_INVALID"
        return JSONResponse(
            status_code=409,
            content={
                "code": 409,
                "message": "MVP release candidate could not be frozen",
                "data": None,
                "error": {"code": error_code, "retryable": False},
                "request_id": _request_id(request),
            },
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_mvp_release_candidate_create_failed",
            extra={"request_id": _request_id(request), "run_id": str(payload.run_id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "MVP release candidate is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=int(execution.status_code),
        content=_body(execution.body, request),
        headers={"Idempotency-Replayed": "true" if execution.replayed else "false"},
    )


@router.get(
    "/bid-assessment-runtime-lab/execute-preflight",
    summary="Get non-secret MVP-1 execute readiness",
    operation_id="getBidAssessmentRuntimeLabExecutePreflight",
)
def get_runtime_lab_execute_preflight(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user
    runtime_access = _runtime_access(request)
    result = build_execute_preflight(
        db,
        runtime_access=runtime_access,
        expected_model_profile_version=str(
            getattr(request.app.state, "bid_mvp1_model_profile_version", "")
        ),
        rq2_runtime_ready=bool(
            getattr(request.app.state, "bid_mvp1_rq2_runtime_ready", False)
        ),
        authority_epoch=str(
            getattr(request.app.state, "bid_mvp1_authority_epoch", "unavailable")
        ),
        view_only_secret_isolated=bool(
            getattr(
                request.app.state,
                "bid_mvp1_view_only_secret_isolated",
                False,
            )
        ),
    )
    return _body(result, request)


@router.get(
    "/bid-assessment-runtime-lab/runs",
    summary="List actor-visible Phase 4 runs",
    operation_id="listBidAssessmentRuntimeLabRuns",
)
def list_runtime_lab_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _enabled():
        return _not_found(request)
    try:
        rows = list_visible_runs(
            db,
            actor_id=int(current_user.id),
            actor_is_admin=has_admin_role(current_user),
            limit=limit,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "bid_runtime_lab_runs_read_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "runtime trace is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return _body(rows, request)


@router.get(
    "/bid-assessment-runtime-lab/runs/{run_id}/trace",
    summary="Get a redacted Phase 4 runtime trace",
    operation_id="getBidAssessmentRuntimeLabTrace",
)
def get_runtime_lab_trace(
    run_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _enabled() or not _ID_PATTERN.fullmatch(run_id):
        return _not_found(request)
    try:
        query = (
            db.query(BidAnalysisRun)
            .join(BidAssessment, BidAssessment.id == BidAnalysisRun.assessment_id)
            .filter(BidAnalysisRun.id == run_id)
        )
        if not has_admin_role(current_user):
            query = query.filter(BidAssessment.created_by == int(current_user.id))
        run = query.one_or_none()
        if run is None:
            return _not_found(request)
        trace = build_runtime_trace(db, run)
        headers = runtime_trace_headers(trace)
        if if_none_match and if_none_match.strip() == headers["ETag"]:
            return Response(status_code=304, headers=headers)
    except Exception:
        db.rollback()
        logger.exception(
            "bid_runtime_lab_trace_read_failed",
            extra={
                "request_id": _request_id(request),
                "actor_id": int(current_user.id),
                "run_id": run_id,
            },
        )
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "message": "runtime trace is temporarily unavailable",
                "data": None,
                "error": {"code": "BID_STORAGE_UNAVAILABLE", "retryable": True},
                "request_id": _request_id(request),
            },
        )
    return JSONResponse(
        status_code=200,
        content=_body(trace, request),
        headers=headers,
        media_type="application/json; charset=utf-8",
    )
