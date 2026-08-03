from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.agents.bid_intake.contracts import (
    AssessmentDraft,
    PolicyDecision,
    PolicyFactorRating,
    PolicyFactorSource,
)
from app.agents.bid_intake.fake_adapters import (
    build_demo_draft,
    build_demo_evidence,
    build_demo_manifest,
)
from app.agents.bid_intake.policy import YamlBidPolicy
from app.services.bid_policy_catalog import (
    BidPolicyCatalogError,
    active_bid_policy_version,
    bid_policy_path,
)


def _draft_with_factor(
    factor_id: str,
    *,
    rating: PolicyFactorRating,
) -> AssessmentDraft:
    payload = build_demo_draft(
        build_demo_evidence()
    ).model_dump(mode="json")
    for factor in payload["policy_factors"]:
        if factor["factor_id"] != factor_id:
            continue
        factor["rating"] = rating.value
        if rating == PolicyFactorRating.UNKNOWN:
            factor["source_type"] = PolicyFactorSource.UNKNOWN.value
            factor["source_note"] = None
            factor["confidence"] = 0
            factor["evidence_refs"] = []
        break
    return AssessmentDraft.model_validate(payload)


def _draft_with_ratings(
    ratings: dict[str, PolicyFactorRating],
) -> AssessmentDraft:
    payload = build_demo_draft(
        build_demo_evidence()
    ).model_dump(mode="json")
    for factor in payload["policy_factors"]:
        rating = ratings.get(factor["factor_id"])
        if rating is not None:
            factor["rating"] = rating.value
    return AssessmentDraft.model_validate(payload)


def test_active_policy_is_valid_and_weights_sum_to_100():
    version = active_bid_policy_version()
    policy = YamlBidPolicy.from_version(version)

    assert version == "qs_bid_decision_policy_2026_01"
    assert policy.version == version
    factors = policy.prompt_context["required_policy_factors"]
    assert len(factors) == 11
    assert {item["factor_id"] for item in factors} >= {
        "compliance_risk",
        "margin_potential",
        "payment_cashflow",
        "client_credit",
        "delivery_capacity",
    }


def test_demo_project_scores_recommend_quote_deterministically():
    policy = YamlBidPolicy.from_active()
    evaluation = policy.evaluate(
        draft=build_demo_draft(build_demo_evidence()),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "passed"
    assert evaluation.decision == PolicyDecision.RECOMMEND_QUOTE
    assert evaluation.score == 76.5
    assert evaluation.coverage == 100
    assert evaluation.hard_rule_hits == []
    assert evaluation.critical_unknown_factors == []


def test_critical_unknown_requires_supplement_instead_of_guessing():
    policy = YamlBidPolicy.from_active()
    evaluation = policy.evaluate(
        draft=_draft_with_factor(
            "client_credit",
            rating=PolicyFactorRating.UNKNOWN,
        ),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "not_evaluable"
    assert evaluation.decision == PolicyDecision.NEED_SUPPLEMENT
    assert evaluation.coverage == 90
    assert evaluation.critical_unknown_factors == ["client_credit"]


def test_hard_red_line_recommends_no_quote_and_special_approval():
    policy = YamlBidPolicy.from_active()
    evaluation = policy.evaluate(
        draft=_draft_with_factor(
            "compliance_risk",
            rating=PolicyFactorRating.CRITICAL,
        ),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "special_approval_required"
    assert evaluation.decision == PolicyDecision.RECOMMEND_NO_QUOTE
    assert {
        item.rule_id for item in evaluation.hard_rule_hits
    } == {"compliance_red_line"}


def test_score_between_60_and_75_is_conditional_quote():
    policy = YamlBidPolicy.from_active()
    evaluation = policy.evaluate(
        draft=_draft_with_factor(
            "win_probability",
            rating=PolicyFactorRating.ADVERSE,
        ),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "warning"
    assert evaluation.decision == PolicyDecision.CONDITIONAL_QUOTE
    assert evaluation.score == 74.5


def test_low_score_with_sufficient_coverage_recommends_no_quote():
    policy = YamlBidPolicy.from_active()
    evaluation = policy.evaluate(
        draft=_draft_with_ratings(
            {
                "scope_cost_clarity": PolicyFactorRating.ADVERSE,
                "margin_potential": PolicyFactorRating.ADVERSE,
                "client_credit": PolicyFactorRating.ADVERSE,
                "delivery_capacity": PolicyFactorRating.ADVERSE,
            }
        ),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "warning"
    assert evaluation.decision == PolicyDecision.RECOMMEND_NO_QUOTE
    assert evaluation.score == 56.5
    assert evaluation.coverage == 100


def test_hard_red_line_dominates_other_unknown_information():
    payload = _draft_with_factor(
        "client_credit",
        rating=PolicyFactorRating.UNKNOWN,
    ).model_dump(mode="json")
    for factor in payload["policy_factors"]:
        if factor["factor_id"] == "compliance_risk":
            factor["rating"] = PolicyFactorRating.CRITICAL.value
            break
    evaluation = YamlBidPolicy.from_active().evaluate(
        draft=AssessmentDraft.model_validate(payload),
        manifest=build_demo_manifest(),
    )

    assert evaluation.status == "special_approval_required"
    assert evaluation.decision == PolicyDecision.RECOMMEND_NO_QUOTE
    assert evaluation.critical_unknown_factors == ["client_credit"]


def test_policy_catalog_rejects_path_traversal():
    with pytest.raises(
        BidPolicyCatalogError,
        match="INVALID_BID_POLICY_VERSION",
    ):
        bid_policy_path("../secret")


def test_policy_engine_rejects_unconfigured_factor_instead_of_ignoring_it():
    payload = build_demo_draft(
        build_demo_evidence()
    ).model_dump(mode="json")
    payload["policy_factors"].append(
        {
            "factor_id": "model_invented_factor",
            "rating": PolicyFactorRating.FAVORABLE.value,
            "summary": "模型输出了标准之外的因素。",
            "source_type": PolicyFactorSource.HUMAN_INPUT.value,
            "source_note": "回归测试",
            "confidence": 1,
            "evidence_refs": [],
        }
    )

    with pytest.raises(
        ValueError,
        match="UNKNOWN_POLICY_FACTOR:model_invented_factor",
    ):
        YamlBidPolicy.from_active().evaluate(
            draft=AssessmentDraft.model_validate(payload),
            manifest=build_demo_manifest(),
        )


def test_skill_replay_script_returns_versioned_policy_result(tmp_path):
    payload = {
        "manifest": build_demo_manifest().model_dump(mode="json"),
        "assessment": build_demo_draft(
            build_demo_evidence()
        ).model_dump(mode="json"),
    }
    input_path = tmp_path / "replay-case.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    project_dir = Path(__file__).resolve().parents[1]
    script = (
        project_dir
        / "skills"
        / "bid-decision-policy"
        / "scripts"
        / "replay_policy.py"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(input_path),
        ],
        cwd=project_dir,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    evaluation = json.loads(completed.stdout)

    assert evaluation["policy_version"] == active_bid_policy_version()
    assert evaluation["decision"] == "recommend_quote"
    assert evaluation["score"] == 76.5
