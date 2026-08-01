from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.agents.bid_intake.contracts import (
    PolicyDecision,
    PolicyFactorRating,
)
from app.agents.bid_intake.policy import YamlBidPolicy
from app.agents.bid_intake.policy_candidate import (
    CandidateProposalError,
    propose_threshold_candidate,
)
from app.core.database import Base
from app.models import registry as model_registry  # noqa: F401
from app.models.bid_intake_runtime import (
    BidIntakePolicyCalibrationDataset,
    BidIntakePolicyCalibrationLabel,
    BidIntakePolicyCalibrationReview,
    BidIntakePolicyCandidate,
)
from app.models.user import User
from app.services.bid_policy_candidates import (
    blind_evaluate_policy_candidate,
    generate_policy_candidate,
    serialize_policy_candidate,
)
from app.services.bid_policy_calibration import (
    BidPolicyCalibrationConflict,
)
from app.services.bid_policy_dataset_ops import (
    freeze_calibration_dataset,
    review_calibration_label,
)

from .bid_policy_calibration_checks import _case


def _proposal_cases():
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
    return cases


def test_candidate_search_improves_development_without_weakening_safety():
    base = YamlBidPolicy.from_active()

    proposal = propose_threshold_candidate(
        base_policy=base,
        cases=_proposal_cases(),
        candidate_version="qs_policy_candidate_test",
    )

    thresholds = proposal.candidate_config.decision_thresholds
    assert thresholds.recommend_quote_min == 72.5
    assert thresholds.conditional_quote_min == 60
    assert proposal.baseline_result.development.exact_accuracy == 0.3
    assert proposal.candidate_result.development.exact_accuracy == 1
    assert proposal.candidate_objective.unsafe_quote_count == 0
    assert proposal.candidate_objective.hard_stop_miss_count == 0
    assert proposal.candidate_config.factors == (
        base._config.factors  # noqa: SLF001
    )
    assert proposal.candidate_config.hard_rules == (
        base._config.hard_rules  # noqa: SLF001
    )


def test_candidate_search_never_uses_holdout_for_selection():
    base = YamlBidPolicy.from_active()
    development = _proposal_cases()
    proposal_without_holdout = propose_threshold_candidate(
        base_policy=base,
        cases=development,
        candidate_version="qs_policy_candidate_a",
    )
    hostile_holdout = [
        _case(
            100 + index,
            expected=PolicyDecision.RECOMMEND_NO_QUOTE,
            ratings={
                "win_probability": PolicyFactorRating.ADVERSE,
            },
            split="holdout",
        )
        for index in range(10)
    ]
    proposal_with_holdout = propose_threshold_candidate(
        base_policy=base,
        cases=[*development, *hostile_holdout],
        candidate_version="qs_policy_candidate_b",
    )

    assert (
        proposal_with_holdout.candidate_config.decision_thresholds
        == proposal_without_holdout.candidate_config.decision_thresholds
    )
    assert (
        proposal_with_holdout.development_dataset_fingerprint
        == proposal_without_holdout.development_dataset_fingerprint
    )


def test_candidate_search_requires_enough_development_samples():
    with pytest.raises(CandidateProposalError) as exc_info:
        propose_threshold_candidate(
            base_policy=YamlBidPolicy.from_active(),
            cases=_proposal_cases()[:10],
            candidate_version="qs_policy_candidate_small",
        )

    assert (
        exc_info.value.code
        == "CALIBRATION_DEVELOPMENT_SAMPLE_INSUFFICIENT"
    )
    assert exc_info.value.details["case_count"] == 10


def test_candidate_search_does_not_create_change_when_base_is_best():
    cases = [
        _case(
            index,
            expected=PolicyDecision.RECOMMEND_QUOTE,
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
        for index in range(15, 21)
    )

    with pytest.raises(CandidateProposalError) as exc_info:
        propose_threshold_candidate(
            base_policy=YamlBidPolicy.from_active(),
            cases=cases,
            candidate_version="qs_policy_candidate_noop",
        )

    assert exc_info.value.code == "NO_BETTER_CANDIDATE_FOUND"


def test_candidate_persistence_freezes_dataset_and_blind_test_once(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'candidate.db').as_posix()}"
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            BidIntakePolicyCalibrationLabel.__table__,
            BidIntakePolicyCalibrationReview.__table__,
            BidIntakePolicyCalibrationDataset.__table__,
            BidIntakePolicyCandidate.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False)
    db = session_factory()
    try:
        user = User(
            username=f"candidate-{uuid.uuid4().hex[:8]}",
            hashed_password="test",
            role="manager",
            is_active=True,
        )
        db.add(user)
        reviewer = User(
            username=f"reviewer-{uuid.uuid4().hex[:8]}",
            hashed_password="test",
            role="manager",
            is_active=True,
        )
        db.add(reviewer)
        db.flush()
        cases = _proposal_cases()
        cases.extend(
            _case(
                100 + index,
                expected=(
                    PolicyDecision.RECOMMEND_NO_QUOTE
                    if index <= 5
                    else PolicyDecision.RECOMMEND_QUOTE
                ),
                ratings=(
                    {
                        "compliance_risk": (
                            PolicyFactorRating.CRITICAL
                        )
                    }
                    if index <= 5
                    else None
                ),
                split="holdout",
                hard_stop=index <= 5,
            )
            for index in range(1, 11)
        )
        for index, case in enumerate(cases, start=1):
            db.add(
                BidIntakePolicyCalibrationLabel(
                    label_uuid=str(uuid.uuid4()),
                    assessment_id=index,
                    project_id=index,
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
                    rationale=case.gold_label.rationale,
                    case_snapshot_json=json.dumps(
                        case.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    source_report_version=1,
                    source_manifest_version=1,
                    source_manifest_hash="a" * 64,
                    source_policy_version=(
                        "qs_bid_decision_policy_2026_01"
                    ),
                    created_by=user.id,
                )
            )
        db.commit()

        first_label = (
            db.query(BidIntakePolicyCalibrationLabel)
            .order_by(BidIntakePolicyCalibrationLabel.id.asc())
            .first()
        )
        with pytest.raises(
            BidPolicyCalibrationConflict,
            match="CALIBRATION_REVIEWER_MUST_DIFFER",
        ):
            review_calibration_label(
                db,
                label_uuid=first_label.label_uuid,
                action="approved",
                note="创建人不能复核自己的金标。",
                current_user=user,
            )
        for label in db.query(BidIntakePolicyCalibrationLabel).all():
            review_calibration_label(
                db,
                label_uuid=label.label_uuid,
                action="approved",
                note="独立复核通过。",
                current_user=reviewer,
            )
        dataset = freeze_calibration_dataset(
            db,
            current_user=reviewer,
            freeze_note="候选引擎测试数据集。",
        )
        db.commit()
        assert dataset.status == "frozen"

        proposal = generate_policy_candidate(
            db,
            current_user=reviewer,
            dataset_uuid=dataset.dataset_uuid,
        )
        db.commit()
        assert proposal.status == "draft"
        assert proposal.calibration_dataset_id == dataset.id
        assert len(json.loads(proposal.dataset_snapshot_json)) == 30
        assert "hard_rules:" in proposal.policy_yaml
        assert (
            serialize_policy_candidate(proposal)[
                "calibration_dataset"
            ]["dataset_uuid"]
            == dataset.dataset_uuid
        )

        evaluated = blind_evaluate_policy_candidate(
            db,
            proposal_uuid=proposal.proposal_uuid,
            current_user=reviewer,
        )
        db.commit()
        first_evaluated_at = evaluated.blind_evaluated_at
        assert evaluated.status == "blind_passed"
        blind_report = json.loads(evaluated.blind_report_json)
        assert blind_report["release_gate"]["passed"] is True
        assert "case_results" not in blind_report["candidate"]

        repeated = blind_evaluate_policy_candidate(
            db,
            proposal_uuid=proposal.proposal_uuid,
            current_user=reviewer,
        )
        db.commit()
        assert repeated.blind_evaluated_at == first_evaluated_at
        assert db.query(BidIntakePolicyCandidate).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_candidate_skill_script_outputs_proposal_without_writing_policy(
    tmp_path,
):
    dataset_path = tmp_path / "candidate-dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    case.model_dump(mode="json")
                    for case in _proposal_cases()
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "bid-decision-policy"
        / "scripts"
        / "propose_candidate_policy.py"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dataset",
            str(dataset_path),
            "--candidate-policy-version",
            "qs_policy_candidate_cli_test",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = json.loads(completed.stdout)

    assert output["candidate_policy_version"] == (
        "qs_policy_candidate_cli_test"
    )
    assert (
        output["candidate_config"]["decision_thresholds"][
            "recommend_quote_min"
        ]
        == 72.5
    )
