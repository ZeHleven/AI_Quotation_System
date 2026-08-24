from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.services.bid_query_optimizer import (
    MAX_QUERY_COUNT,
    QUERY_OPTIMIZER_PROFILE_VERSION,
    QUERY_PLAN_CONTRACT_VERSION,
    BidQueryOptimizerError,
    optimize_bid_evidence_query,
    validate_bid_query_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "contracts"
    / "bid_assessment"
    / "v1"
    / "rq1c-query-optimizer-profile.json"
)
SCHEMA_PATH = (
    PROJECT_ROOT
    / "schemas"
    / "bid_assessment"
    / "v1"
    / "query-plan.schema.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_rq1c_machine_profile_and_schema_are_frozen() -> None:
    profile = _load(CONTRACT_PATH)
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert profile["contract_version"] == QUERY_PLAN_CONTRACT_VERSION
    assert profile["profile_version"] == QUERY_OPTIMIZER_PROFILE_VERSION
    assert profile["budget"] == {
        "max_queries": 6,
        "max_subjects": 4,
        "max_query_characters": 500,
        "original_query_required": True,
        "original_query_position": 1,
        "dedupe": "nfkc_lower_alnum_cjk_fingerprint",
    }
    assert profile["compatibility"]["pdf_c3_index_reused_without_reindex"] is True
    assert profile["external_execution"] == {
        "ocr_or_visual": False,
        "model": False,
        "embedding": False,
        "vector_database": False,
        "public_network": False,
        "external_mcp": False,
        "legacy_bid_intake_authority": False,
    }


def test_query_plan_is_deterministic_bounded_and_self_hashed() -> None:
    question = "进度款支付比例、累计暂停支付线以及结算后的付款比例分别是多少？"
    first = optimize_bid_evidence_query(question).to_payload()
    second = optimize_bid_evidence_query(question).to_payload()
    Draft202012Validator(_load(SCHEMA_PATH)).validate(first)
    assert first == second
    assert first["queries"][0] == first["original_query"]
    assert 2 <= first["query_count"] <= MAX_QUERY_COUNT
    assert first["query_count"] == len(first["query_items"])
    assert {"payment.progress", "payment.suspension", "payment.settlement"}.issubset(
        set(first["detected_field_codes"])
    )
    validate_bid_query_plan(first)


def test_parallel_subjects_are_split_without_losing_exclusion_polarity() -> None:
    payload = optimize_bid_evidence_query(
        "商业街区和32层办公区分别不包含哪些专业工程或配置？"
    ).to_payload()
    assert payload["detected_subjects"] == ["商业街区", "32层办公区"]
    subject_items = [
        row for row in payload["query_items"] if row["kind"] == "parallel_subject"
    ]
    assert [row["subject"] for row in subject_items] == ["商业街区", "32层办公区"]
    assert all(row["polarity"] == "exclude" for row in subject_items)
    assert all("不含" in row["text"] and "不包括" in row["text"] for row in subject_items)


def test_multi_field_question_gets_auditable_field_alias_queries() -> None:
    payload = optimize_bid_evidence_query(
        "本次招标工程名称和工程地点分别是什么？"
    ).to_payload()
    assert payload["detected_subjects"] == []
    assert payload["detected_field_codes"] == ["project.name", "project.location"]
    alias_items = [row for row in payload["query_items"] if row["kind"] == "field_alias"]
    assert [row["field_codes"] for row in alias_items[:2]] == [
        ["project.name"],
        ["project.location"],
    ]
    assert "招标项目名称" in alias_items[0]["text"]
    assert "建设地点" in alias_items[1]["text"]


def test_missing_datetime_shape_is_expanded_without_inventing_a_value() -> None:
    payload = optimize_bid_evidence_query(
        "招标文件是否给出了投标截止日期具体是几月几日几点？"
    ).to_payload()
    deadline = next(
        row
        for row in payload["query_items"]
        if row["kind"] == "field_alias"
        and row["field_codes"] == ["submission.deadline"]
    )
    assert deadline["answer_shapes"] == ["datetime"]
    assert all(token in deadline["text"] for token in ("年", "月", "日", "时"))
    assert "2026" not in deadline["text"]


def test_unknown_intent_keeps_original_and_reuses_legacy_deterministically() -> None:
    payload = optimize_bid_evidence_query("请说明该事项的具体安排").to_payload()
    assert payload["queries"][0] == "请说明该事项的具体安排"
    assert "NO_TENDER_FIELD_ALIAS_MATCH" in payload["warnings"]
    assert payload["clarification_required"] is False
    validate_bid_query_plan(payload)


def test_query_plan_hash_and_state_invariants_fail_closed() -> None:
    payload = optimize_bid_evidence_query("投标担保金额和提交形式是什么？").to_payload()
    payload["query_items"][0]["weight"] = 0.0
    with pytest.raises(BidQueryOptimizerError, match="BID_QUERY_PLAN_WEIGHT_INVALID"):
        validate_bid_query_plan(payload)
