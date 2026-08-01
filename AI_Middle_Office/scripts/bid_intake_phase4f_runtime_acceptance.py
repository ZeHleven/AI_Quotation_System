from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_intake.calibration import (
    CalibrationGoldLabel,
    CalibrationLabelBasis,
    PolicyCalibrationCase,
)
from app.agents.bid_intake.contracts import (
    AssessmentDraft,
    DimensionReview,
    DimensionStatus,
    DocumentManifest,
    DocumentManifestItem,
    PolicyDecision,
    PolicyFactorInput,
    PolicyFactorRating,
    PolicyFactorSource,
    Recommendation,
    REQUIRED_DIMENSIONS,
)
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models import registry as _model_registry  # noqa: F401
from app.models.bid_intake_runtime import (
    BidIntakeAssessment,
    BidIntakePolicyCalibrationDataset,
    BidIntakePolicyCalibrationLabel,
    BidIntakePolicyCalibrationReview,
    BidIntakePolicyCandidate,
)
from app.models.bidding import BidProject
from app.models.tender_evidence import BidEvidenceManifest
from app.models.user import User, UserRole
from app.services.bid_policy_dataset_ops import review_calibration_label


MARKER = "phase4f-runtime-acceptance-20260727"
MAKER_USERNAME = f"{MARKER}-maker"
REVIEWER_USERNAME = f"{MARKER}-reviewer"
TEMP_PASSWORD = "Phase4fAcceptance!20260727"
FACTOR_IDS = (
    "compliance_risk",
    "qualification_fit",
    "scope_cost_clarity",
    "margin_potential",
    "payment_cashflow",
    "client_credit",
    "delivery_capacity",
    "strategic_value",
    "win_probability",
    "bond_exposure",
    "tender_readiness",
)


def _case(
    index: int,
    *,
    expected: PolicyDecision,
    ratings: dict[str, PolicyFactorRating] | None = None,
    split: str = "development",
    hard_stop: bool = False,
) -> PolicyCalibrationCase:
    ratings = ratings or {}
    manifest = DocumentManifest(
        case_id=f"{MARKER}-manifest-{index:02d}",
        manifest_version=1,
        manifest_hash=(f"{index:064x}"[-64:]),
        documents=[
            DocumentManifestItem(
                document_id=f"document-{index:02d}",
                file_name="验收招标文件.txt",
                document_type="tender_document",
                document_version=1,
                sha256=f"{index + 1:064x}"[-64:],
                parse_status="ready",
                active=True,
            )
        ],
    )
    assessment = AssessmentDraft(
        project_summary=f"{MARKER} synthetic case {index}.",
        recommendation=Recommendation.RECOMMEND_QUOTE,
        dimension_reviews=[
            DimensionReview(
                dimension=dimension,
                status=DimensionStatus.CONFIRMED,
                summary="验收资料已确认。",
            )
            for dimension in REQUIRED_DIMENSIONS
        ],
        policy_factors=[
            PolicyFactorInput(
                factor_id=factor_id,
                rating=ratings.get(
                    factor_id,
                    PolicyFactorRating.ACCEPTABLE,
                ),
                summary="Phase 4f 运行态验收因素。",
                source_type=PolicyFactorSource.TENDER_EVIDENCE,
                confidence=0.95,
            )
            for factor_id in FACTOR_IDS
        ],
        confidence=0.95,
        termination_reason="验收样本信息完整。",
    )
    return PolicyCalibrationCase(
        case_id=f"{MARKER}-case-{index:02d}",
        assessment_uuid=str(uuid.uuid4()),
        project_uuid=str(uuid.uuid4()),
        source="synthetic",
        dataset_split=split,
        manifest=manifest,
        assessment=assessment,
        gold_label=CalibrationGoldLabel(
            expected_decision=expected,
            hard_stop_expected=hard_stop,
            label_basis=CalibrationLabelBasis.PRE_BID_EXPERT_REVIEW,
            rationale=f"{MARKER} synthetic gold label.",
        ),
    )


def _cases():
    cases = [
        _case(
            index,
            expected=PolicyDecision.RECOMMEND_QUOTE,
            ratings={
                "win_probability": PolicyFactorRating.ADVERSE,
            },
        )
        for index in range(1, 15)
    ]
    cases.extend(
        _case(
            index,
            expected=PolicyDecision.RECOMMEND_NO_QUOTE,
            ratings={
                "compliance_risk": PolicyFactorRating.CRITICAL,
            },
            hard_stop=True,
        )
        for index in range(15, 18)
    )
    low_score = {
        "scope_cost_clarity": PolicyFactorRating.ADVERSE,
        "margin_potential": PolicyFactorRating.ADVERSE,
        "client_credit": PolicyFactorRating.ADVERSE,
        "delivery_capacity": PolicyFactorRating.ADVERSE,
    }
    cases.extend(
        _case(
            index,
            expected=PolicyDecision.RECOMMEND_NO_QUOTE,
            ratings=low_score,
        )
        for index in range(18, 21)
    )
    cases.extend(
        _case(
            100 + index,
            expected=(
                PolicyDecision.RECOMMEND_NO_QUOTE
                if index <= 5
                else PolicyDecision.RECOMMEND_QUOTE
            ),
            ratings=(
                {"compliance_risk": PolicyFactorRating.CRITICAL}
                if index <= 5
                else None
            ),
            split="holdout",
            hard_stop=index <= 5,
        )
        for index in range(1, 11)
    )
    return cases


def _find_user(db, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def _count_state(db) -> dict[str, int]:
    project_ids = list(
        db.scalars(
            select(BidProject.id).where(
                BidProject.project_name.like(f"{MARKER}%")
            )
        )
    )
    assessment_ids = (
        list(
            db.scalars(
                select(BidIntakeAssessment.id).where(
                    BidIntakeAssessment.project_id.in_(project_ids)
                )
            )
        )
        if project_ids
        else []
    )
    label_ids = (
        list(
            db.scalars(
                select(BidIntakePolicyCalibrationLabel.id).where(
                    BidIntakePolicyCalibrationLabel.assessment_id.in_(
                        assessment_ids
                    )
                )
            )
        )
        if assessment_ids
        else []
    )
    reviewer = _find_user(db, REVIEWER_USERNAME)
    return {
        "projects": len(project_ids),
        "assessments": len(assessment_ids),
        "labels": len(label_ids),
        "reviews": (
            len(
                list(
                    db.scalars(
                        select(BidIntakePolicyCalibrationReview.id).where(
                            BidIntakePolicyCalibrationReview.label_id.in_(
                                label_ids
                            )
                        )
                    )
                )
            )
            if label_ids
            else 0
        ),
        "datasets": (
            len(
                list(
                    db.scalars(
                        select(BidIntakePolicyCalibrationDataset.id).where(
                            BidIntakePolicyCalibrationDataset.created_by
                            == reviewer.id
                        )
                    )
                )
            )
            if reviewer
            else 0
        ),
        "candidates": (
            len(
                list(
                    db.scalars(
                        select(BidIntakePolicyCandidate.id).where(
                            BidIntakePolicyCandidate.created_by
                            == reviewer.id
                        )
                    )
                )
            )
            if reviewer
            else 0
        ),
    }


def seed() -> dict:
    db = SessionLocal()
    try:
        if _find_user(db, MAKER_USERNAME) or _find_user(
            db, REVIEWER_USERNAME
        ):
            raise RuntimeError(
                "Acceptance seed already exists; run cleanup first."
            )

        maker = User(
            username=MAKER_USERNAME,
            hashed_password=get_password_hash(TEMP_PASSWORD),
            role="user",
            quota=100,
            is_active=True,
            must_change_password=False,
        )
        reviewer = User(
            username=REVIEWER_USERNAME,
            hashed_password=get_password_hash(TEMP_PASSWORD),
            role="user",
            quota=100,
            is_active=True,
            must_change_password=False,
        )
        db.add_all([maker, reviewer])
        db.flush()
        db.add_all(
            [
                UserRole(
                    user_id=maker.id,
                    role="manager",
                    created_by=None,
                    note=f"{MARKER} temporary role",
                ),
                UserRole(
                    user_id=reviewer.id,
                    role="manager",
                    created_by=None,
                    note=f"{MARKER} temporary role",
                ),
            ]
        )

        first_project_uuid = None
        for index, base_case in enumerate(_cases(), start=1):
            project_uuid = str(uuid.uuid4())
            assessment_uuid = str(uuid.uuid4())
            case = base_case.model_copy(
                update={
                    "case_id": f"{MARKER}-case-{index:02d}",
                    "assessment_uuid": assessment_uuid,
                    "project_uuid": project_uuid,
                }
            )
            project = BidProject(
                project_uuid=project_uuid,
                project_name=f"{MARKER}-{index:02d}",
                status="draft",
                owner_user_id=reviewer.id,
                created_by=maker.id,
                summary_json=json.dumps(
                    {"acceptance_marker": MARKER},
                    ensure_ascii=False,
                ),
            )
            db.add(project)
            db.flush()
            manifest_hash = uuid.uuid4().hex + uuid.uuid4().hex
            manifest = BidEvidenceManifest(
                manifest_uuid=str(uuid.uuid4()),
                project_id=project.id,
                version_no=1,
                manifest_hash=manifest_hash,
                snapshot_json=json.dumps(
                    case.manifest.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                active=True,
                created_by=maker.id,
            )
            db.add(manifest)
            db.flush()
            assessment = BidIntakeAssessment(
                assessment_uuid=assessment_uuid,
                project_id=project.id,
                manifest_id=manifest.id,
                manifest_version=1,
                manifest_hash=manifest_hash,
                policy_version="qs_bid_decision_policy_2026_01",
                analysis_goal="Phase 4f runtime acceptance.",
                status="completed",
                report_version=1,
                recommendation=case.gold_label.expected_decision.value,
                assessment_json=json.dumps(
                    case.assessment.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                created_by=maker.id,
            )
            db.add(assessment)
            db.flush()
            db.add(
                BidIntakePolicyCalibrationLabel(
                    label_uuid=str(uuid.uuid4()),
                    assessment_id=assessment.id,
                    project_id=project.id,
                    label_version=1,
                    active=True,
                    dataset_split=case.dataset_split,
                    label_basis=case.gold_label.label_basis.value,
                    expected_decision=(
                        case.gold_label.expected_decision.value
                    ),
                    hard_stop_expected=(
                        case.gold_label.hard_stop_expected
                    ),
                    rationale=f"{MARKER} synthetic gold label.",
                    case_snapshot_json=json.dumps(
                        case.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    source_report_version=1,
                    source_manifest_version=1,
                    source_manifest_hash=manifest_hash,
                    source_policy_version=(
                        "qs_bid_decision_policy_2026_01"
                    ),
                    created_by=maker.id,
                )
            )
            if first_project_uuid is None:
                first_project_uuid = project_uuid

        db.commit()
        return {
            "marker": MARKER,
            "reviewer_username": REVIEWER_USERNAME,
            "temporary_password": TEMP_PASSWORD,
            "first_project_uuid": first_project_uuid,
            "state": _count_state(db),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def approve_pending() -> dict:
    db = SessionLocal()
    try:
        reviewer = _find_user(db, REVIEWER_USERNAME)
        if reviewer is None:
            raise RuntimeError("Acceptance reviewer does not exist.")
        project_ids = list(
            db.scalars(
                select(BidProject.id).where(
                    BidProject.project_name.like(f"{MARKER}%")
                )
            )
        )
        labels = list(
            db.scalars(
                select(BidIntakePolicyCalibrationLabel)
                .where(
                    BidIntakePolicyCalibrationLabel.project_id.in_(
                        project_ids
                    ),
                    BidIntakePolicyCalibrationLabel.active.is_(True),
                )
                .order_by(BidIntakePolicyCalibrationLabel.id.asc())
            )
        )
        existing_label_ids = set(
            db.scalars(
                select(BidIntakePolicyCalibrationReview.label_id).where(
                    BidIntakePolicyCalibrationReview.label_id.in_(
                        [label.id for label in labels]
                    )
                )
            )
        )
        approved = 0
        for label in labels:
            if label.id in existing_label_ids:
                continue
            review_calibration_label(
                db,
                label_uuid=label.label_uuid,
                action="approved",
                note=f"{MARKER} independent runtime review.",
                current_user=reviewer,
            )
            approved += 1
        db.commit()
        return {"approved_now": approved, "state": _count_state(db)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def inspect_state() -> dict:
    db = SessionLocal()
    try:
        return {
            "marker": MARKER,
            "maker_exists": _find_user(db, MAKER_USERNAME) is not None,
            "reviewer_exists": (
                _find_user(db, REVIEWER_USERNAME) is not None
            ),
            "state": _count_state(db),
        }
    finally:
        db.close()


def cleanup() -> dict:
    db = SessionLocal()
    try:
        maker = _find_user(db, MAKER_USERNAME)
        reviewer = _find_user(db, REVIEWER_USERNAME)
        project_ids = list(
            db.scalars(
                select(BidProject.id).where(
                    BidProject.project_name.like(f"{MARKER}%")
                )
            )
        )
        assessment_ids = (
            list(
                db.scalars(
                    select(BidIntakeAssessment.id).where(
                        BidIntakeAssessment.project_id.in_(project_ids)
                    )
                )
            )
            if project_ids
            else []
        )
        label_ids = (
            list(
                db.scalars(
                    select(BidIntakePolicyCalibrationLabel.id).where(
                        BidIntakePolicyCalibrationLabel.assessment_id.in_(
                            assessment_ids
                        )
                    )
                )
            )
            if assessment_ids
            else []
        )
        reviewer_id = reviewer.id if reviewer else None

        if reviewer_id is not None:
            db.query(BidIntakePolicyCandidate).filter(
                BidIntakePolicyCandidate.created_by == reviewer_id
            ).delete(synchronize_session=False)
            db.query(BidIntakePolicyCalibrationDataset).filter(
                BidIntakePolicyCalibrationDataset.created_by == reviewer_id
            ).delete(synchronize_session=False)
        if label_ids:
            db.query(BidIntakePolicyCalibrationReview).filter(
                BidIntakePolicyCalibrationReview.label_id.in_(label_ids)
            ).delete(synchronize_session=False)
            db.query(BidIntakePolicyCalibrationLabel).filter(
                BidIntakePolicyCalibrationLabel.id.in_(label_ids)
            ).delete(synchronize_session=False)
        if assessment_ids:
            db.query(BidIntakeAssessment).filter(
                BidIntakeAssessment.id.in_(assessment_ids)
            ).delete(synchronize_session=False)
        if project_ids:
            db.query(BidEvidenceManifest).filter(
                BidEvidenceManifest.project_id.in_(project_ids)
            ).delete(synchronize_session=False)
            db.query(BidProject).filter(
                BidProject.id.in_(project_ids)
            ).delete(synchronize_session=False)
        user_ids = [
            user.id for user in (maker, reviewer) if user is not None
        ]
        if user_ids:
            db.query(UserRole).filter(
                UserRole.user_id.in_(user_ids)
            ).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(user_ids)).delete(
                synchronize_session=False
            )
        db.commit()
        return inspect_state()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("seed", "approve-pending", "inspect", "cleanup"),
    )
    args = parser.parse_args()
    result = {
        "seed": seed,
        "approve-pending": approve_pending,
        "inspect": inspect_state,
        "cleanup": cleanup,
    }[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
