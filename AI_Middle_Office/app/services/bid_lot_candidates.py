"""Read-only API-30 projection over Manifest-scoped lot-detection authority."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidLotCandidate,
)
from app.models.bid_assessment_lots import (
    BidLotCandidateEvidence,
    BidLotDetectionHead,
    BidLotDetectionRun,
)
from app.services.bid_lot_detection_runs import build_manifest_parse_set


LOT_CANDIDATE_CACHE_CONTROL = "private, no-cache, max-age=0, must-revalidate"
LOT_SELECTABLE_ASSESSMENT_STATES = frozenset({"awaiting_lot_selection"})
_WARNING_CODE = re.compile(r"^[A-Z0-9_]{1,80}$")


class BidLotManifestNotFound(LookupError):
    """The requested Manifest is not owned by the visible Assessment."""


def _utc_rfc3339(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _selected_manifest(
    db: Session,
    assessment: BidAssessment,
    manifest_id: str | None,
) -> BidDocumentManifest | None:
    selected_id = manifest_id or (
        str(assessment.current_manifest_id) if assessment.current_manifest_id else None
    )
    if selected_id is None:
        return None
    manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == selected_id,
            BidDocumentManifest.assessment_id == assessment.id,
        )
        .one_or_none()
    )
    if manifest is None:
        raise BidLotManifestNotFound(selected_id)
    return manifest


def _safe_warnings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    warnings: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "LOT_DETECTION_WARNING").upper()[:80]
        if not _WARNING_CODE.fullmatch(code):
            code = "LOT_DETECTION_WARNING"
        details = item.get("details")
        warnings.append(
            {
                "code": code,
                "message": str(item.get("message") or "标段候选需要复核")[:500],
                "details": dict(details) if isinstance(details, dict) else {},
            }
        )
    return warnings


def _ratio(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _generation(run: BidLotDetectionRun | None, *, stale: bool) -> dict[str, Any]:
    if run is None:
        return {
            "status": "not_started",
            "detection_run_id": None,
            "parse_set_hash": None,
            "candidate_count": 0,
            "retryable": False,
            "error_code": None,
            "requested_at": None,
            "started_at": None,
            "finished_at": None,
        }
    return {
        "status": "stale" if stale else str(run.status),
        "detection_run_id": str(run.id),
        "parse_set_hash": str(run.parse_set_hash),
        "candidate_count": int(run.candidate_count),
        "retryable": bool(run.retryable) if not stale else False,
        "error_code": str(run.error_code) if run.error_code else None,
        "requested_at": _utc_rfc3339(run.requested_at),
        "started_at": _utc_rfc3339(run.started_at),
        "finished_at": _utc_rfc3339(run.finished_at),
    }


def _blocking_reason(
    *,
    manifest: BidDocumentManifest | None,
    parse_set_status: str | None,
    generation_status: str,
    candidate_count: int,
    is_current_manifest: bool,
    scope_exists: bool,
    cloned_scope_for_manifest: bool,
) -> dict[str, str] | None:
    if manifest is None:
        return {"code": "manifest_not_available", "message": "尚无已提交的资料清单"}
    # API-32 creates an authoritative Scope and an independent Manifest but
    # intentionally does not copy the source detection run. The selected Scope
    # is therefore usable while this Manifest's candidate projection remains
    # not_started; that is not a user-blocking condition.
    if cloned_scope_for_manifest and generation_status == "not_started":
        return None
    if generation_status == "not_started":
        if parse_set_status == "failed":
            return {"code": "document_parse_failed", "message": "资料解析失败，暂不能生成标段候选"}
        if parse_set_status != "ready":
            return {"code": "document_parse_pending", "message": "资料解析尚未完成"}
        return {"code": "lot_detection_not_started", "message": "标段候选生成尚未开始"}
    if generation_status in {"queued", "running"}:
        return {"code": "lot_detection_pending", "message": "标段候选正在生成"}
    if generation_status == "failed":
        return {"code": "lot_detection_failed", "message": "标段候选生成失败"}
    if generation_status == "stale":
        return {"code": "lot_candidates_stale", "message": "资料解析结果已变化，历史候选不可选择"}
    if generation_status == "succeeded" and candidate_count == 0:
        return {"code": "no_supported_lot", "message": "资料正文中未找到有直接证据支持的标段"}
    if not is_current_manifest and not scope_exists:
        return {"code": "manifest_not_current", "message": "历史资料清单中的候选不可绑定到当前研判"}
    return None


def _allowed_action(code: str, *, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "enabled": True,
        "requires_confirmation": True,
        "reason_code": None,
        "target": target,
    }


def build_bid_lot_candidate_page(
    db: Session,
    assessment: BidAssessment,
    *,
    manifest_id: str | None,
) -> dict[str, Any]:
    """Build API-30 without scheduling parsing or lot detection."""

    manifest = _selected_manifest(db, assessment, manifest_id)
    if manifest is None:
        generation = _generation(None, stale=False)
        return {
            "assessment_id": str(assessment.id),
            "manifest": None,
            "generation": generation,
            "candidates": [],
            "selection_required": False,
            "selected_lot_id": None,
            "blocking_reason": _blocking_reason(
                manifest=None,
                parse_set_status=None,
                generation_status="not_started",
                candidate_count=0,
                is_current_manifest=False,
                scope_exists=False,
                cloned_scope_for_manifest=False,
            ),
            "allowed_actions": [],
        }

    is_current_manifest = str(assessment.current_manifest_id or "") == str(manifest.id)
    parse_set = build_manifest_parse_set(db, manifest_id=str(manifest.id))
    head_run = (
        db.query(BidLotDetectionHead, BidLotDetectionRun)
        .join(
            BidLotDetectionRun,
            BidLotDetectionRun.id == BidLotDetectionHead.current_run_id,
        )
        .filter(BidLotDetectionHead.manifest_id == manifest.id)
        .one_or_none()
    )
    run = head_run[1] if head_run is not None else None
    stale = bool(run is not None and str(run.parse_set_hash) != parse_set.parse_set_hash)
    generation = _generation(run, stale=stale)

    scope = (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment.id)
        .order_by(BidAssessmentScope.version.desc())
        .first()
    )
    selected_lot_id = None
    cloned_scope_for_manifest = False
    if scope is not None:
        selected_snapshot = dict(scope.selected_lot_snapshot_json or {})
        selected_lot_id = str(scope.source_lot_candidate_id) if scope.source_lot_candidate_id else None
        if selected_lot_id is None:
            snapshot_lot_id = selected_snapshot.get("lot_id")
            selected_lot_id = str(snapshot_lot_id) if snapshot_lot_id else None
        cloned_scope_for_manifest = bool(
            selected_snapshot.get("schema_version")
            == "bid-assessment-cloned-lot-scope-v1"
            and str(selected_snapshot.get("manifest_id") or "") == str(manifest.id)
        )

    candidates: list[dict[str, Any]] = []
    if run is not None:
        candidate_rows = (
            db.query(BidLotCandidate)
            .filter(BidLotCandidate.detection_run_id == run.id)
            .order_by(BidLotCandidate.normalized_lot_key.asc(), BidLotCandidate.id.asc())
            .all()
        )
        candidate_ids = [str(row.id) for row in candidate_rows]
        evidence_by_candidate: dict[str, list[BidLotCandidateEvidence]] = {
            candidate_id: [] for candidate_id in candidate_ids
        }
        if candidate_ids:
            evidence_rows = (
                db.query(BidLotCandidateEvidence)
                .filter(
                    BidLotCandidateEvidence.manifest_id == manifest.id,
                    BidLotCandidateEvidence.lot_candidate_id.in_(candidate_ids),
                )
                .order_by(
                    BidLotCandidateEvidence.lot_candidate_id.asc(),
                    BidLotCandidateEvidence.display_order.asc(),
                    BidLotCandidateEvidence.evidence_id.asc(),
                )
                .all()
            )
            for evidence in evidence_rows:
                evidence_by_candidate[str(evidence.lot_candidate_id)].append(evidence)
        for candidate in candidate_rows:
            candidate_id = str(candidate.id)
            if selected_lot_id == candidate_id:
                candidate_status = "selected"
            elif scope is not None:
                candidate_status = "rejected"
            else:
                candidate_status = "candidate"
            candidates.append(
                {
                    "lot_id": candidate_id,
                    "detection_run_id": str(candidate.detection_run_id),
                    "lot_code": str(candidate.lot_code) if candidate.lot_code else None,
                    "lot_name": str(candidate.lot_name),
                    "scope_summary": str(candidate.scope_summary)[:2000] if candidate.scope_summary else None,
                    "status": candidate_status,
                    "confidence": str(candidate.confidence_level),
                    "confidence_score": _ratio(candidate.confidence),
                    "evidence_refs": [
                        {
                            "evidence_id": str(evidence.evidence_id),
                            "display_label": str(evidence.display_label)[:300],
                            "detail_url": f"/api/v1/bid-evidence/{evidence.evidence_id}",
                        }
                        for evidence in evidence_by_candidate[candidate_id]
                    ],
                    "warnings": _safe_warnings(candidate.warnings_json),
                }
            )

    generation_status = str(generation["status"])
    selection_required = bool(
        generation_status == "succeeded"
        and candidates
        and is_current_manifest
        and scope is None
        and str(assessment.business_status) in LOT_SELECTABLE_ASSESSMENT_STATES
    )
    allowed_actions: list[dict[str, Any]] = []
    if selection_required:
        allowed_actions.append(
            _allowed_action(
                "lot.select",
                target={
                    "assessment_id": str(assessment.id),
                    "manifest_id": str(manifest.id),
                    "url": f"/api/v1/bid-assessments/{assessment.id}/lot-selection",
                },
            )
        )
    elif (
        generation_status == "succeeded"
        and candidates
        and (scope is not None or not is_current_manifest)
    ):
        allowed_actions.append(
            _allowed_action(
                "assessment.create_for_other_lot",
                target={
                    "assessment_id": str(assessment.id),
                    "manifest_id": str(manifest.id),
                    "url": f"/api/v1/bid-assessments/{assessment.id}/clone-for-lot",
                },
            )
        )

    return {
        "assessment_id": str(assessment.id),
        "manifest": {
            "manifest_id": str(manifest.id),
            "version": int(manifest.version),
            "manifest_hash": str(manifest.manifest_hash),
            "is_current_manifest": is_current_manifest,
        },
        "generation": generation,
        "candidates": candidates,
        "selection_required": selection_required,
        "selected_lot_id": selected_lot_id,
        "blocking_reason": _blocking_reason(
            manifest=manifest,
            parse_set_status=parse_set.status,
            generation_status=generation_status,
            candidate_count=len(candidates),
            is_current_manifest=is_current_manifest,
            scope_exists=scope is not None,
            cloned_scope_for_manifest=cloned_scope_for_manifest,
        ),
        "allowed_actions": allowed_actions,
    }


def bid_lot_candidate_etag(
    assessment: BidAssessment,
    page: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "assessment_id": str(assessment.id),
            "assessment_row_version": int(assessment.row_version),
            "page": page,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f'"bid-lot-candidates:{assessment.id}:{fingerprint}"'


def bid_lot_candidate_headers(
    assessment: BidAssessment,
    page: dict[str, Any],
) -> dict[str, str]:
    return {
        "ETag": bid_lot_candidate_etag(assessment, page),
        "X-Resource-Version": str(int(assessment.row_version)),
        "Cache-Control": LOT_CANDIDATE_CACHE_CONTROL,
        "Vary": "Authorization",
    }
