"""API-60/API-61 immutable preliminary-report reads for Phase 4 MVP-1."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_results import BidPreliminaryReport
from app.models.user import User
from app.services.rbac import has_admin_role


router = APIRouter()
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


def _body(data, request: Request) -> dict:
    return {
        "code": 200,
        "message": "ok",
        "data": data,
        "error": None,
        "request_id": str(getattr(request.state, "trace_id", ""))[:80],
    }


def _not_found(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=_body(None, request)
        | {
            "code": 404,
            "message": "resource not found",
            "error": {"code": "BID_RESOURCE_NOT_FOUND", "retryable": False},
        },
    )


def _enabled() -> bool:
    return bool(
        settings.feature_bid_assessment_v1_runtime
        and settings.feature_bid_assessment_phase4_mvp
        and settings.feature_bid_assessment_phase4_preliminary_report
    )


def _visible_assessment(
    db: Session,
    *,
    assessment_id: str,
    user: User,
) -> BidAssessment | None:
    query = db.query(BidAssessment).filter(BidAssessment.id == assessment_id)
    if not has_admin_role(user):
        query = query.filter(BidAssessment.created_by == int(user.id))
    return query.one_or_none()


def _summary(row: BidPreliminaryReport) -> dict:
    decision = dict((row.report_json or {}).get("decision") or {})
    gates = list((row.report_json or {}).get("hard_gates") or [])
    return {
        "report_id": str(row.id),
        "assessment_id": str(row.assessment_id),
        "run_id": str(row.run_id),
        "report_type": "preliminary",
        "version": int(row.report_version),
        "status": str(row.status),
        "title": str(row.title),
        "executive_summary": str(row.executive_summary),
        "decision": decision.get("code"),
        "investment_level": decision.get("investment_level"),
        "gate_summary": {
            "pass": sum(value.get("status") == "pass" for value in gates),
            "fail": sum(value.get("status") == "fail" for value in gates),
            "unknown": sum(value.get("status") == "unknown" for value in gates),
        },
        "report_hash": str(row.report_hash),
        "generated_at": row.generated_at,
    }


@router.get(
    "/bid-assessments/{assessment_id}/reports",
    summary="List immutable bid-assessment reports (API-60)",
    operation_id="listBidReports",
)
def list_bid_reports(
    assessment_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _enabled() or not ID_PATTERN.fullmatch(assessment_id):
        return _not_found(request)
    if _visible_assessment(db, assessment_id=assessment_id, user=current_user) is None:
        return _not_found(request)
    query = db.query(BidPreliminaryReport).filter(
        BidPreliminaryReport.assessment_id == assessment_id,
        BidPreliminaryReport.status.in_(("ready", "stale")),
    )
    total = int(query.count())
    rows = (
        query.order_by(
            BidPreliminaryReport.report_version.desc(),
            BidPreliminaryReport.generated_at.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return JSONResponse(
        content=jsonable_encoder(
            _body(
                {
                    "items": [_summary(row) for row in rows],
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                },
                request,
            )
        ),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/bid-reports/{report_id}",
    summary="Get one immutable bid-assessment report (API-61)",
    operation_id="getBidReport",
)
def get_bid_report(
    report_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _enabled() or not ID_PATTERN.fullmatch(report_id):
        return _not_found(request)
    query = (
        db.query(BidPreliminaryReport)
        .join(BidAssessment, BidAssessment.id == BidPreliminaryReport.assessment_id)
        .filter(
            BidPreliminaryReport.id == report_id,
            BidPreliminaryReport.status.in_(("ready", "stale")),
        )
    )
    if not has_admin_role(current_user):
        query = query.filter(BidAssessment.created_by == int(current_user.id))
    row = query.one_or_none()
    if row is None:
        return _not_found(request)
    etag = f'"bid-report:{row.id}:{row.report_hash}"'
    if if_none_match and etag in {value.strip() for value in if_none_match.split(",")}:
        return Response(status_code=304, headers={"ETag": etag})
    payload = _summary(row) | {
        "decision_id": str(row.decision_id),
        "validation_id": str(row.validation_id),
        "report": dict(row.report_json or {}),
    }
    return JSONResponse(
        content=jsonable_encoder(_body(payload, request)),
        headers={"ETag": etag, "Cache-Control": "private, no-cache"},
    )
