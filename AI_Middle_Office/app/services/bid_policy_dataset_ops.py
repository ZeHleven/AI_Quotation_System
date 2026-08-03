from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.bid_intake.calibration import PolicyCalibrationCase
from app.agents.bid_intake.calibration_dataset import (
    CalibrationDatasetLabelRef,
    PolicyCalibrationDatasetSnapshot,
    build_dataset_quality_report,
)
from app.agents.bid_intake.policy_candidate import (
    calibration_case_fingerprint,
)
from app.models.bid_intake_runtime import (
    BidIntakeAssessment,
    BidIntakePolicyCalibrationDataset,
    BidIntakePolicyCalibrationLabel,
    BidIntakePolicyCalibrationReview,
)
from app.models.bidding import BidProject
from app.models.user import User
from app.services.bid_policy_calibration import (
    BidPolicyCalibrationConflict,
    BidPolicyCalibrationNotFound,
    serialize_calibration_label,
)


def review_calibration_label(
    db: Session,
    *,
    label_uuid: str,
    action: str,
    note: str,
    current_user: User,
) -> BidIntakePolicyCalibrationReview:
    label = (
        db.query(BidIntakePolicyCalibrationLabel)
        .filter(
            BidIntakePolicyCalibrationLabel.label_uuid
            == label_uuid,
            BidIntakePolicyCalibrationLabel.active.is_(True),
        )
        .with_for_update()
        .one_or_none()
    )
    if label is None:
        raise BidPolicyCalibrationNotFound(
            "CALIBRATION_LABEL_NOT_FOUND"
        )
    if label.created_by == current_user.id:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_REVIEWER_MUST_DIFFER"
        )
    normalized_action = str(action or "").strip()
    if normalized_action not in {"approved", "rejected"}:
        raise BidPolicyCalibrationConflict(
            "INVALID_CALIBRATION_REVIEW_ACTION"
        )
    normalized_note = str(note or "").strip()
    if not normalized_note:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_REVIEW_NOTE_REQUIRED"
        )
    existing = (
        db.query(BidIntakePolicyCalibrationReview)
        .filter(
            BidIntakePolicyCalibrationReview.label_id == label.id
        )
        .one_or_none()
    )
    if existing is not None:
        if (
            existing.action == normalized_action
            and existing.note == normalized_note
            and existing.reviewed_by == current_user.id
        ):
            return existing
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_LABEL_ALREADY_REVIEWED"
        )
    review = BidIntakePolicyCalibrationReview(
        review_uuid=str(uuid.uuid4()),
        label_id=label.id,
        action=normalized_action,
        note=normalized_note,
        reviewed_by=current_user.id,
    )
    db.add(review)
    db.flush()
    return review


def list_calibration_samples(
    db: Session,
    *,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
    review_status: str | None = None,
    dataset_split: str | None = None,
    expected_decision: str | None = None,
    hard_stop_expected: bool | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    query = (
        db.query(
            BidIntakePolicyCalibrationLabel,
            BidIntakeAssessment,
            BidProject,
            BidIntakePolicyCalibrationReview,
        )
        .join(
            BidIntakeAssessment,
            BidIntakeAssessment.id
            == BidIntakePolicyCalibrationLabel.assessment_id,
        )
        .join(
            BidProject,
            BidProject.id
            == BidIntakePolicyCalibrationLabel.project_id,
        )
        .outerjoin(
            BidIntakePolicyCalibrationReview,
            BidIntakePolicyCalibrationReview.label_id
            == BidIntakePolicyCalibrationLabel.id,
        )
        .filter(
            BidIntakePolicyCalibrationLabel.active.is_(True)
        )
    )
    if review_status == "pending":
        query = query.filter(
            BidIntakePolicyCalibrationReview.id.is_(None)
        )
    elif review_status in {"approved", "rejected"}:
        query = query.filter(
            BidIntakePolicyCalibrationReview.action == review_status
        )
    if dataset_split:
        query = query.filter(
            BidIntakePolicyCalibrationLabel.dataset_split
            == dataset_split
        )
    if expected_decision:
        query = query.filter(
            BidIntakePolicyCalibrationLabel.expected_decision
            == expected_decision
        )
    if hard_stop_expected is not None:
        query = query.filter(
            BidIntakePolicyCalibrationLabel.hard_stop_expected.is_(
                hard_stop_expected
            )
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        like = f"%{normalized_search}%"
        query = query.filter(
            or_(
                BidProject.project_name.like(like),
                BidProject.project_uuid.like(like),
                BidIntakeAssessment.assessment_uuid.like(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(
            BidIntakePolicyCalibrationLabel.created_at.desc(),
            BidIntakePolicyCalibrationLabel.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    user_ids = {
        identifier
        for label, _, _, review in rows
        for identifier in (
            label.created_by,
            review.reviewed_by if review is not None else None,
        )
        if identifier is not None
    }
    usernames = {
        user.id: user.username
        for user in (
            db.query(User).filter(User.id.in_(user_ids)).all()
            if user_ids
            else []
        )
    }
    return {
        "items": [
            _serialize_sample(
                label=label,
                assessment=assessment,
                project=project,
                review=review,
                current_user=current_user,
                usernames=usernames,
            )
            for label, assessment, project, review in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def build_current_dataset_quality(
    db: Session,
) -> dict[str, Any]:
    records = _reviewed_case_records(db)
    report = build_dataset_quality_report(
        cases=[record["case"] for record in records["approved"]],
        pending_review_count=records["pending_review_count"],
        rejected_review_count=records["rejected_review_count"],
        invalid_case_count=records["invalid_case_count"],
    )
    return report.model_dump(mode="json")


def freeze_calibration_dataset(
    db: Session,
    *,
    current_user: User,
    freeze_note: str | None = None,
) -> BidIntakePolicyCalibrationDataset:
    records = _reviewed_case_records(db, lock=True)
    cases = [record["case"] for record in records["approved"]]
    quality = build_dataset_quality_report(
        cases=cases,
        pending_review_count=records["pending_review_count"],
        rejected_review_count=records["rejected_review_count"],
        invalid_case_count=records["invalid_case_count"],
    )
    if not quality.ready_to_freeze:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_DATASET_NOT_READY",
            details={
                "failed_checks": [
                    check.code
                    for check in quality.checks
                    if not check.passed
                ],
                "quality": quality.model_dump(mode="json"),
            },
        )
    fingerprint = calibration_case_fingerprint(cases)
    existing = (
        db.query(BidIntakePolicyCalibrationDataset)
        .filter(
            BidIntakePolicyCalibrationDataset.dataset_fingerprint
            == fingerprint
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    dataset_uuid = str(uuid.uuid4())
    dataset_version = f"qs_calibration_dataset_{fingerprint[:12]}"
    snapshot = PolicyCalibrationDatasetSnapshot(
        dataset_uuid=dataset_uuid,
        dataset_version=dataset_version,
        cases=cases,
        label_refs=[
            CalibrationDatasetLabelRef(
                label_uuid=record["label"].label_uuid,
                label_version=record["label"].label_version,
                assessment_uuid=record["case"].assessment_uuid,
                project_uuid=record["case"].project_uuid,
                reviewed_by=record["review"].reviewed_by,
                review_uuid=record["review"].review_uuid,
            )
            for record in records["approved"]
        ],
    )
    row = BidIntakePolicyCalibrationDataset(
        dataset_uuid=dataset_uuid,
        dataset_version=dataset_version,
        status="frozen",
        dataset_fingerprint=fingerprint,
        snapshot_json=_dump_json(snapshot.model_dump(mode="json")),
        quality_report_json=_dump_json(
            quality.model_dump(mode="json")
        ),
        freeze_note=str(freeze_note or "").strip() or None,
        created_by=current_user.id,
    )
    db.add(row)
    db.flush()
    return row


def list_calibration_datasets(
    db: Session,
    *,
    limit: int = 20,
) -> list[BidIntakePolicyCalibrationDataset]:
    return (
        db.query(BidIntakePolicyCalibrationDataset)
        .order_by(
            BidIntakePolicyCalibrationDataset.created_at.desc(),
            BidIntakePolicyCalibrationDataset.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_calibration_dataset(
    db: Session,
    *,
    dataset_uuid: str,
) -> BidIntakePolicyCalibrationDataset:
    row = (
        db.query(BidIntakePolicyCalibrationDataset)
        .filter(
            BidIntakePolicyCalibrationDataset.dataset_uuid
            == dataset_uuid,
            BidIntakePolicyCalibrationDataset.status == "frozen",
        )
        .one_or_none()
    )
    if row is None:
        raise BidPolicyCalibrationNotFound(
            "CALIBRATION_DATASET_NOT_FOUND"
        )
    return row


def load_calibration_dataset_cases(
    row: BidIntakePolicyCalibrationDataset,
) -> list[PolicyCalibrationCase]:
    try:
        snapshot = PolicyCalibrationDatasetSnapshot.model_validate(
            _load_json(row.snapshot_json)
        )
    except (TypeError, ValueError) as exc:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_DATASET_SNAPSHOT_INVALID"
        ) from exc
    if (
        snapshot.dataset_uuid != row.dataset_uuid
        or snapshot.dataset_version != row.dataset_version
        or calibration_case_fingerprint(snapshot.cases)
        != row.dataset_fingerprint
    ):
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_DATASET_SNAPSHOT_MISMATCH"
        )
    return snapshot.cases


def serialize_calibration_dataset(
    row: BidIntakePolicyCalibrationDataset,
) -> dict[str, Any]:
    return {
        "dataset_uuid": row.dataset_uuid,
        "dataset_version": row.dataset_version,
        "status": row.status,
        "dataset_fingerprint": row.dataset_fingerprint,
        "quality_report": _load_json(
            row.quality_report_json,
            default={},
        ),
        "freeze_note": row.freeze_note,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "immutable": True,
    }


def serialize_calibration_review(
    row: BidIntakePolicyCalibrationReview,
) -> dict[str, Any]:
    return {
        "review_uuid": row.review_uuid,
        "action": row.action,
        "note": row.note,
        "reviewed_by": row.reviewed_by,
        "created_at": _iso(row.created_at),
    }


def _reviewed_case_records(
    db: Session,
    *,
    lock: bool = False,
) -> dict[str, Any]:
    query = (
        db.query(
            BidIntakePolicyCalibrationLabel,
            BidIntakePolicyCalibrationReview,
        )
        .outerjoin(
            BidIntakePolicyCalibrationReview,
            BidIntakePolicyCalibrationReview.label_id
            == BidIntakePolicyCalibrationLabel.id,
        )
        .filter(
            BidIntakePolicyCalibrationLabel.active.is_(True)
        )
        .order_by(BidIntakePolicyCalibrationLabel.id.asc())
    )
    if lock:
        query = query.with_for_update()
    rows = query.all()
    approved: list[dict[str, Any]] = []
    pending = 0
    rejected = 0
    invalid = 0
    for label, review in rows:
        if review is None:
            pending += 1
            continue
        if review.action == "rejected":
            rejected += 1
            continue
        try:
            case = PolicyCalibrationCase.model_validate(
                _load_json(label.case_snapshot_json)
            )
        except (TypeError, ValueError):
            invalid += 1
            continue
        approved.append(
            {
                "label": label,
                "review": review,
                "case": case,
            }
        )
    return {
        "approved": approved,
        "pending_review_count": pending,
        "rejected_review_count": rejected,
        "invalid_case_count": invalid,
    }


def _serialize_sample(
    *,
    label: BidIntakePolicyCalibrationLabel,
    assessment: BidIntakeAssessment,
    project: BidProject,
    review: BidIntakePolicyCalibrationReview | None,
    current_user: User,
    usernames: dict[int, str],
) -> dict[str, Any]:
    serialized = serialize_calibration_label(label) or {}
    review_payload = (
        serialize_calibration_review(review)
        if review is not None
        else None
    )
    if review_payload is not None:
        review_payload["reviewed_by_username"] = usernames.get(
            review.reviewed_by
        )
    serialized.update(
        {
            "assessment_uuid": assessment.assessment_uuid,
            "project_uuid": project.project_uuid,
            "project_name": project.project_name,
            "review_status": (
                review.action if review is not None else "pending"
            ),
            "review": review_payload,
            "created_by_username": usernames.get(label.created_by),
            "can_review": (
                review is None
                and label.created_by != current_user.id
            ),
        }
    )
    return serialized


def _dump_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_json(
    value: str | None,
    default: Any = None,
) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
