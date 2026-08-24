from __future__ import annotations

import json

from app.agents.bid_assessment_pure.answer_contracts import AnswerDraft
from app.agents.bid_assessment_pure.answer_runtime import GroundingIntegrityGuard
from app.agents.bid_assessment_pure.citation_runtime import (
    AnswerBlockRenderer,
    CitationProjector,
)
from scripts.evaluate_bid_pure_agent_v606 import (
    SCHEMA_VERSION,
    build_synthetic_runtime,
    draft_from_blocks,
    evaluate_negative_guards,
    evaluate_reference_contracts,
    load_dataset,
    model_config,
    validate_and_render,
)


def _case(case_id: str) -> dict:
    return next(case for case in load_dataset()["model_cases"] if case["id"] == case_id)


def _accepted_boundary(case_id: str):
    case = _case(case_id)
    runtime = build_synthetic_runtime(case)
    draft = draft_from_blocks(runtime, case["reference_blocks"])
    validation = GroundingIntegrityGuard().validate(
        task=runtime.task,
        context=runtime.context,
        draft=draft,
        grounding_snapshot=runtime.grounding_snapshot,
    )
    projection = CitationProjector().project(
        task=runtime.task,
        context=runtime.context,
        draft=draft,
        validation=validation,
        grounding_snapshot=runtime.grounding_snapshot,
        authority_snapshot=runtime.authority_snapshot,
    )
    rendered = AnswerBlockRenderer().render(
        task=runtime.task,
        draft=draft,
        validation=validation,
        citation_decision=projection,
    )
    return case, runtime, draft, validation, projection, rendered


def test_v606_dataset_is_versioned_synthetic_closed_and_epistemically_complete() -> None:
    dataset = load_dataset()

    assert dataset["schema_version"] == SCHEMA_VERSION
    assert dataset["dataset_kind"] == "synthetic_only"
    assert all(value is False for value in dataset["execution_contract"].values())
    assert len(dataset["model_cases"]) == 4
    assert {
        case["expected"]["epistemic_status"] for case in dataset["model_cases"]
    } == {"supported", "partial", "conflicted", "unknown"}


def test_v606_reference_answers_pass_grounding_citation_and_rendering() -> None:
    result = evaluate_reference_contracts(load_dataset())

    assert result["passed"] is True
    assert result["passed_count"] == result["total"] == 4
    assert all(all(case["checks"].values()) for case in result["cases"])


def test_v606_negative_guards_reject_overclaim_missing_authority_stale_and_conflict() -> None:
    result = evaluate_negative_guards(load_dataset())

    assert result["passed"] is True
    assert result["passed_count"] == result["total"] == 5
    assert {case["id"] for case in result["cases"]} == {
        "N01_unknown_overclaim_rejected",
        "N02_missing_authority_rejected",
        "N03_model_authored_citation_rejected",
        "N04_stale_source_rejected",
        "N05_one_sided_conflict_rejected",
    }


def test_v606_supported_answer_projects_complete_runtime_owned_citations() -> None:
    _, _, _, validation, projection, rendered = _accepted_boundary(
        "A01_supported_deadline_and_guarantee"
    )

    assert validation.accepted is True
    assert projection.accepted is True
    assert projection.bundle is not None
    assert len(projection.bundle.citations) == 2
    assert all(support.citation_required for support in validation.statement_support)
    assert all(binding.citation_refs for binding in projection.bundle.statement_bindings)
    assert [citation.ordinal for citation in projection.bundle.citations] == [1, 2]
    assert [citation.marker for citation in rendered.citations] == ["[1]", "[2]"]
    assert "合成招标文件" in rendered.text


def test_v606_unknown_answer_is_explicit_limited_and_has_no_citation() -> None:
    case, runtime, draft, _, projection, rendered = _accepted_boundary(
        "A02_unknown_control_price"
    )
    result = validate_and_render(case, draft, runtime=runtime)

    assert result["passed"] is True
    assert result["epistemic_statuses"] == ["unknown"]
    assert result["limitation_codes"] == ["retrieval_no_result"]
    assert result["citation_count"] == 0
    assert projection.bundle is not None and projection.bundle.citations == ()
    assert "暂无法确认" in rendered.text
    assert "检索范围限制" in rendered.text


def test_v606_conflict_answer_requires_and_projects_two_source_groups() -> None:
    case, runtime, draft, _, projection, rendered = _accepted_boundary(
        "A03_conflicted_deadline"
    )
    result = validate_and_render(case, draft, runtime=runtime)

    assert result["passed"] is True
    assert projection.bundle is not None
    assert len(projection.bundle.citations) == 2
    assert {citation.conflict_group_ordinal for citation in projection.bundle.citations} == {
        1,
        2,
    }
    assert "存在冲突" in rendered.text
    assert "冲突来源 1" in rendered.text
    assert "冲突来源 2" in rendered.text


def test_v606_partial_answer_cites_known_part_and_discloses_missing_validity() -> None:
    case, runtime, draft, _, _, rendered = _accepted_boundary(
        "A04_partial_enterprise_qualification"
    )
    result = validate_and_render(case, draft, runtime=runtime)

    assert result["passed"] is True
    assert result["citation_count"] == 1
    assert result["limitation_codes"] == ["evidence_insufficient"]
    assert "部分确认" in rendered.text
    assert "证据不足" in rendered.text


def test_v606_runtime_projection_is_deterministic() -> None:
    first = evaluate_reference_contracts(load_dataset())
    second = evaluate_reference_contracts(load_dataset())

    assert [case["rendered_hash"] for case in first["cases"]] == [
        case["rendered_hash"] for case in second["cases"]
    ]


def test_v606_model_visible_draft_schema_has_no_citation_display_authority() -> None:
    schema = json.dumps(AnswerDraft.model_json_schema(), ensure_ascii=False)

    assert "safe_title" not in schema
    assert "safe_locator_label" not in schema
    assert "controlled_access_ref" not in schema
    assert "citation_ref" not in schema


def test_v606_model_config_is_optional_without_secret_or_environment(monkeypatch) -> None:
    for name in (
        "BID_ASSESSMENT_MODEL_API_KEY",
        "DEEPSEEK_API_KEY",
        "BID_ASSESSMENT_MODEL_CHAT_URL",
        "DEEPSEEK_CHAT_URL",
        "BID_ASSESSMENT_MODEL_ID",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert model_config(timeout_seconds=120, secret_env_file=None) is None


def test_v606_thresholds_require_complete_model_and_runtime_safety() -> None:
    thresholds = load_dataset()["thresholds"]

    assert thresholds == {
        "reference_runtime_pass_rate_min": 1.0,
        "negative_guard_pass_rate_min": 1.0,
        "model_case_pass_rate_min": 1.0,
        "citation_completeness_min": 1.0,
        "unknown_safety_min": 1.0,
        "conflict_dual_source_min": 1.0,
    }
