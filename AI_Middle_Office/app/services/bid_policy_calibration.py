from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.bid_intake.calibration import (
    CalibrationActualOutcome,
    CalibrationGoldLabel,
    CalibrationLabelBasis,
    PolicyCalibrationCase,
    compare_policy_versions,
)
from app.agents.bid_intake.contracts import (
    AssessmentDraft,
    DocumentManifest,
    PolicyDecision,
)
from app.agents.bid_intake.policy import YamlBidPolicy
from app.models.bid_intake_runtime import (
    BidIntakeAssessment,
    BidIntakePolicyCalibrationLabel,
    BidIntakePolicyCalibrationReview,
)
from app.models.bidding import BidProject
from app.models.tender_evidence import BidEvidenceManifest
from app.models.user import User
from app.services.bid_policy_catalog import (
    active_bid_policy_version,
    list_bid_policy_versions,
)


class BidPolicyCalibrationError(RuntimeError):
    pass


class BidPolicyCalibrationConflict(BidPolicyCalibrationError):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.details = details or {}


class BidPolicyCalibrationNotFound(BidPolicyCalibrationError):
    pass


def current_calibration_label(
    db: Session,
    *,
    assessment_id: int,
) -> BidIntakePolicyCalibrationLabel | None:
    return (
        db.query(BidIntakePolicyCalibrationLabel)
        .filter(
            BidIntakePolicyCalibrationLabel.assessment_id
            == assessment_id,
            BidIntakePolicyCalibrationLabel.active.is_(True),
        )
        .order_by(
            BidIntakePolicyCalibrationLabel.label_version.desc(),
            BidIntakePolicyCalibrationLabel.id.desc(),
        )
        .first()
    )


def create_calibration_label(
    db: Session,
    *,
    project: BidProject,
    assessment: BidIntakeAssessment,
    current_user: User,
    expected_current_label_version: int,
    dataset_split: str,
    label_basis: str,
    expected_decision: str,
    hard_stop_expected: bool,
    rationale: str,
    actual_outcome: dict[str, Any] | None = None,
) -> BidIntakePolicyCalibrationLabel:
    if assessment.project_id != project.id:
        raise BidPolicyCalibrationNotFound(
            "BID_INTAKE_ASSESSMENT_NOT_FOUND"
        )
    current = (
        db.query(BidIntakePolicyCalibrationLabel)
        .filter(
            BidIntakePolicyCalibrationLabel.assessment_id
            == assessment.id,
            BidIntakePolicyCalibrationLabel.active.is_(True),
        )
        .with_for_update()
        .one_or_none()
    )
    current_version = current.label_version if current else 0
    if int(expected_current_label_version) != current_version:
        raise BidPolicyCalibrationConflict(
            "STALE_CALIBRATION_LABEL_VERSION"
        )
    if not assessment.assessment_json or not assessment.policy_evaluation_json:
        raise BidPolicyCalibrationConflict(
            "ASSESSMENT_NOT_READY_FOR_CALIBRATION"
        )
    if current is None:
        project_split = (
            db.query(BidIntakePolicyCalibrationLabel.dataset_split)
            .filter(
                BidIntakePolicyCalibrationLabel.project_id
                == project.id
            )
            .order_by(BidIntakePolicyCalibrationLabel.id.asc())
            .with_for_update()
            .first()
        )
        if (
            project_split is not None
            and project_split[0] != dataset_split
        ):
            raise BidPolicyCalibrationConflict(
                "CALIBRATION_PROJECT_SPLIT_FROZEN"
            )
    try:
        gold_label = CalibrationGoldLabel(
            expected_decision=PolicyDecision(expected_decision),
            hard_stop_expected=bool(hard_stop_expected),
            label_basis=CalibrationLabelBasis(label_basis),
            rationale=str(rationale or "").strip(),
            actual_outcome=(
                CalibrationActualOutcome.model_validate(actual_outcome)
                if actual_outcome is not None
                else None
            ),
        )
        if current is not None:
            if current.dataset_split != dataset_split:
                raise BidPolicyCalibrationConflict(
                    "CALIBRATION_DATASET_SPLIT_FROZEN"
                )
            frozen_case = PolicyCalibrationCase.model_validate(
                _load_json(current.case_snapshot_json)
            )
            case = PolicyCalibrationCase.model_validate(
                {
                    **frozen_case.model_dump(mode="json"),
                    "gold_label": gold_label.model_dump(mode="json"),
                }
            )
        else:
            manifest_row = (
                db.query(BidEvidenceManifest)
                .filter(
                    BidEvidenceManifest.id == assessment.manifest_id
                )
                .one_or_none()
            )
            if manifest_row is None:
                raise BidPolicyCalibrationConflict(
                    "BOUND_MANIFEST_NOT_AVAILABLE"
                )
            assessment_contract = AssessmentDraft.model_validate(
                _load_json(assessment.assessment_json)
            )
            manifest_contract = _manifest_contract(
                _load_json(manifest_row.snapshot_json)
            )
            if (
                manifest_contract.case_id != project.project_uuid
                or manifest_contract.manifest_version
                != assessment.manifest_version
                or manifest_contract.manifest_hash
                != assessment.manifest_hash
            ):
                raise ValueError("bound manifest snapshot mismatch")
            case = PolicyCalibrationCase(
                case_id=f"historical:{assessment.assessment_uuid}",
                assessment_uuid=assessment.assessment_uuid,
                project_uuid=project.project_uuid,
                source="historical",
                dataset_split=dataset_split,
                manifest=manifest_contract,
                assessment=assessment_contract,
                gold_label=gold_label,
            )
    except BidPolicyCalibrationConflict:
        raise
    except (TypeError, ValueError) as exc:
        raise BidPolicyCalibrationConflict(
            "INVALID_CALIBRATION_LABEL"
        ) from exc

    now = datetime.now(timezone.utc)
    if current is not None:
        current.active = False
        current.superseded_at = now
    label = BidIntakePolicyCalibrationLabel(
        label_uuid=str(uuid.uuid4()),
        assessment_id=assessment.id,
        project_id=project.id,
        label_version=current_version + 1,
        active=True,
        supersedes_label_id=current.id if current else None,
        dataset_split=case.dataset_split,
        label_basis=gold_label.label_basis.value,
        expected_decision=gold_label.expected_decision.value,
        hard_stop_expected=gold_label.hard_stop_expected,
        rationale=gold_label.rationale,
        actual_outcome_json=(
            _dump_json(
                gold_label.actual_outcome.model_dump(mode="json")
            )
            if gold_label.actual_outcome is not None
            else None
        ),
        case_snapshot_json=_dump_json(case.model_dump(mode="json")),
        source_report_version=(
            current.source_report_version
            if current
            else assessment.report_version
        ),
        source_manifest_version=(
            current.source_manifest_version
            if current
            else assessment.manifest_version
        ),
        source_manifest_hash=(
            current.source_manifest_hash
            if current
            else assessment.manifest_hash
        ),
        source_policy_version=(
            current.source_policy_version
            if current
            else assessment.policy_version
        ),
        created_by=current_user.id,
    )
    db.add(label)
    db.flush()
    return label


def build_calibration_report(
    db: Session,
    *,
    candidate_policy_version: str | None = None,
) -> dict[str, Any]:
    baseline_version = active_bid_policy_version()
    candidate_version = (
        str(candidate_policy_version or "").strip()
        or baseline_version
    )
    available_versions = list_bid_policy_versions()
    if candidate_version not in available_versions:
        raise BidPolicyCalibrationNotFound(
            "BID_POLICY_VERSION_NOT_FOUND"
        )
    baseline = YamlBidPolicy.from_version(baseline_version)
    candidate = YamlBidPolicy.from_version(candidate_version)
    labels = (
        db.query(BidIntakePolicyCalibrationLabel)
        .join(
            BidIntakePolicyCalibrationReview,
            BidIntakePolicyCalibrationReview.label_id
            == BidIntakePolicyCalibrationLabel.id,
        )
        .filter(BidIntakePolicyCalibrationLabel.active.is_(True))
        .filter(
            BidIntakePolicyCalibrationReview.action == "approved"
        )
        .order_by(
            BidIntakePolicyCalibrationLabel.created_at.asc(),
            BidIntakePolicyCalibrationLabel.id.asc(),
        )
        .all()
    )
    cases: list[PolicyCalibrationCase] = []
    invalid_labels: list[str] = []
    for label in labels:
        try:
            cases.append(
                PolicyCalibrationCase.model_validate(
                    _load_json(label.case_snapshot_json)
                )
            )
        except (TypeError, ValueError):
            invalid_labels.append(label.label_uuid)
    report = compare_policy_versions(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
    )
    payload = report.model_dump(mode="json")
    payload["baseline"].pop("case_results", None)
    payload["candidate"].pop("case_results", None)
    payload["available_policy_versions"] = available_versions
    payload["reviewed_only"] = True
    payload["invalid_label_count"] = len(invalid_labels)
    if invalid_labels:
        payload["warnings"].append(
            f"{len(invalid_labels)}个金标快照无效，已从评测集中排除。"
        )
    payload["activation_allowed"] = False
    payload["activation_note"] = (
        "本阶段只做影子评测；即使发布门通过，也必须由总经办单独批准切换active版本。"
    )
    return payload


def serialize_calibration_label(
    label: BidIntakePolicyCalibrationLabel | None,
) -> dict[str, Any] | None:
    if label is None:
        return None
    return {
        "label_uuid": label.label_uuid,
        "label_version": label.label_version,
        "active": bool(label.active),
        "dataset_split": label.dataset_split,
        "label_basis": label.label_basis,
        "expected_decision": label.expected_decision,
        "hard_stop_expected": bool(label.hard_stop_expected),
        "rationale": label.rationale,
        "actual_outcome": _load_json(
            label.actual_outcome_json,
            default=None,
        ),
        "source_report_version": label.source_report_version,
        "source_manifest_version": label.source_manifest_version,
        "source_manifest_hash": label.source_manifest_hash,
        "source_policy_version": label.source_policy_version,
        "created_by": label.created_by,
        "created_at": _iso(label.created_at),
        "superseded_at": _iso(label.superseded_at),
    }


def _manifest_contract(payload: dict[str, Any]) -> DocumentManifest:
    normalized = dict(payload)
    normalized["documents"] = [
        {
            key: value
            for key, value in item.items()
            if key != "document_key"
        }
        for item in payload.get("documents", [])
        if isinstance(item, dict)
    ]
    return DocumentManifest.model_validate(normalized)


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
