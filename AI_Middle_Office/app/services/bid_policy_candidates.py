from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.agents.bid_intake.calibration import (
    PolicyCalibrationCase,
    compare_policy_versions,
)
from app.agents.bid_intake.policy import YamlBidPolicy
from app.agents.bid_intake.policy_candidate import (
    CandidateProposalError,
    calibration_case_fingerprint,
    propose_threshold_candidate,
)
from app.models.bid_intake_runtime import (
    BidIntakePolicyCandidate,
)
from app.models.user import User
from app.services.bid_policy_calibration import (
    BidPolicyCalibrationConflict,
    BidPolicyCalibrationNotFound,
)
from app.services.bid_policy_catalog import active_bid_policy_version
from app.services.bid_policy_dataset_ops import (
    get_calibration_dataset,
    load_calibration_dataset_cases,
)


def list_policy_candidates(
    db: Session,
    *,
    limit: int = 20,
) -> list[BidIntakePolicyCandidate]:
    return (
        db.query(BidIntakePolicyCandidate)
        .order_by(
            BidIntakePolicyCandidate.created_at.desc(),
            BidIntakePolicyCandidate.id.desc(),
        )
        .limit(limit)
        .all()
    )


def generate_policy_candidate(
    db: Session,
    *,
    current_user: User,
    dataset_uuid: str,
) -> BidIntakePolicyCandidate:
    base_version = active_bid_policy_version()
    dataset = get_calibration_dataset(
        db,
        dataset_uuid=dataset_uuid,
    )
    cases = load_calibration_dataset_cases(dataset)
    dataset_fingerprint = dataset.dataset_fingerprint
    existing = (
        db.query(BidIntakePolicyCandidate)
        .filter(
            BidIntakePolicyCandidate.base_policy_version
            == base_version,
            BidIntakePolicyCandidate.dataset_fingerprint
            == dataset_fingerprint,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    candidate_version = _candidate_version(
        base_version,
        dataset_fingerprint,
    )
    try:
        proposal = propose_threshold_candidate(
            base_policy=YamlBidPolicy.from_version(base_version),
            cases=cases,
            candidate_version=candidate_version,
        )
    except CandidateProposalError as exc:
        raise BidPolicyCalibrationConflict(
            exc.code,
            details=exc.details,
        ) from exc

    development_report = {
        "schema_version": proposal.schema_version,
        "development_dataset_fingerprint": (
            proposal.development_dataset_fingerprint
        ),
        "development_case_count": proposal.development_case_count,
        "development_expected_decision_counts": (
            proposal.development_expected_decision_counts
        ),
        "baseline_objective": proposal.baseline_objective.model_dump(
            mode="json"
        ),
        "candidate_objective": proposal.candidate_objective.model_dump(
            mode="json"
        ),
        "baseline": proposal.baseline_result.model_dump(mode="json"),
        "candidate": proposal.candidate_result.model_dump(mode="json"),
    }
    row = BidIntakePolicyCandidate(
        proposal_uuid=str(uuid.uuid4()),
        candidate_version=proposal.candidate_policy_version,
        base_policy_version=proposal.base_policy_version,
        status="draft",
        search_method=proposal.search_method,
        dataset_fingerprint=dataset_fingerprint,
        dataset_snapshot_json=_dump_json(
            [
                case.model_dump(mode="json")
                for case in sorted(cases, key=lambda item: item.case_id)
            ]
        ),
        policy_yaml=yaml.safe_dump(
            proposal.candidate_config.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        changed_fields_json=_dump_json(proposal.changed_fields),
        development_report_json=_dump_json(development_report),
        calibration_dataset_id=dataset.id,
        created_by=current_user.id,
    )
    db.add(row)
    db.flush()
    return row


def blind_evaluate_policy_candidate(
    db: Session,
    *,
    proposal_uuid: str,
    current_user: User,
) -> BidIntakePolicyCandidate:
    row = (
        db.query(BidIntakePolicyCandidate)
        .filter(
            BidIntakePolicyCandidate.proposal_uuid
            == proposal_uuid
        )
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise BidPolicyCalibrationNotFound(
            "CALIBRATION_CANDIDATE_NOT_FOUND"
        )
    if row.blind_report_json:
        return row
    if row.status != "draft":
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_CANDIDATE_NOT_EVALUABLE"
        )

    cases = _frozen_cases(row.dataset_snapshot_json)
    if (
        calibration_case_fingerprint(cases)
        != row.dataset_fingerprint
    ):
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_CANDIDATE_SNAPSHOT_MISMATCH"
        )
    try:
        baseline = YamlBidPolicy.from_version(
            row.base_policy_version
        )
        candidate = YamlBidPolicy.from_payload(
            yaml.safe_load(row.policy_yaml),
            expected_version=row.candidate_version,
        )
    except Exception as exc:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_CANDIDATE_POLICY_INVALID"
        ) from exc

    comparison = compare_policy_versions(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
    ).model_dump(mode="json")
    comparison["baseline"].pop("case_results", None)
    comparison["candidate"].pop("case_results", None)
    comparison["blind_evaluation"] = {
        "one_time": True,
        "holdout_case_details_exposed": False,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    row.blind_report_json = _dump_json(comparison)
    row.status = (
        "blind_passed"
        if comparison["release_gate"]["passed"]
        else "blind_failed"
    )
    row.blind_evaluated_by = current_user.id
    row.blind_evaluated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def serialize_policy_candidate(
    row: BidIntakePolicyCandidate,
) -> dict[str, Any]:
    dataset = row.calibration_dataset
    return {
        "proposal_uuid": row.proposal_uuid,
        "candidate_version": row.candidate_version,
        "base_policy_version": row.base_policy_version,
        "status": row.status,
        "search_method": row.search_method,
        "dataset_fingerprint": row.dataset_fingerprint,
        "changed_fields": _load_json(
            row.changed_fields_json,
            default={},
        ),
        "development_report": _load_json(
            row.development_report_json,
            default={},
        ),
        "blind_report": _load_json(
            row.blind_report_json,
            default=None,
        ),
        "created_by": row.created_by,
        "blind_evaluated_by": row.blind_evaluated_by,
        "created_at": _iso(row.created_at),
        "blind_evaluated_at": _iso(row.blind_evaluated_at),
        "calibration_dataset": (
            {
                "dataset_uuid": dataset.dataset_uuid,
                "dataset_version": dataset.dataset_version,
                "dataset_fingerprint": dataset.dataset_fingerprint,
            }
            if dataset is not None
            else None
        ),
        "activation_allowed": False,
        "activation_note": (
            "候选提案不能在本阶段切换active标准；"
            "必须进入独立的总经办发布审批阶段。"
        ),
    }


def _frozen_cases(value: str) -> list[PolicyCalibrationCase]:
    payload = _load_json(value, default=[])
    try:
        return [
            PolicyCalibrationCase.model_validate(item)
            for item in payload
        ]
    except (TypeError, ValueError) as exc:
        raise BidPolicyCalibrationConflict(
            "CALIBRATION_CANDIDATE_SNAPSHOT_INVALID"
        ) from exc


def _candidate_version(
    base_version: str,
    dataset_fingerprint: str,
) -> str:
    suffix = f"_cand_{dataset_fingerprint[:8]}"
    return f"{base_version[:64 - len(suffix)]}{suffix}"


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
