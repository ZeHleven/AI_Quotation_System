from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.bid_assessment_pure.answer_contracts import GroundingStatus
from app.agents.bid_assessment_pure.planning import InformationSourceHint
from scripts.evaluate_bid_pure_agent_v607 import (
    DATASET_PATH,
    MAX_ANSWER_REPAIRS,
    RealOfflineRetriever,
    V607ModelOutputError,
    _completed_history_turn_count,
    _json_object,
    _openai_tools,
    build_answer_runtime,
    build_enterprise_index,
    load_dataset,
    load_silver_dataset,
)


def test_v607_answer_repair_budget_is_bounded_by_runtime_profile() -> None:
    assert MAX_ANSWER_REPAIRS == 2


def test_v607_continuous_context_counts_turns_not_messages() -> None:
    history = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "继续追问"},
        {"role": "assistant", "content": "第二答"},
    ]

    assert _completed_history_turn_count(history) == 2


def test_v607_non_json_model_output_is_repairable() -> None:
    with pytest.raises(V607ModelOutputError):
        _json_object("not-json")


def test_v607_dataset_freezes_real_business_isolation() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert dataset["dataset_kind"] == "authorized_real_business"
    assert dataset["document"]["page_count"] == 307
    assert (
        dataset["document"]["sha256"]
        == "3e2d7a428df8ac72daec8b75124f0c470e8964b1c953d4799f163b75d1a0ad8f"
    )
    assert dataset["execution_contract"]["external_mcp_allowed"] is False
    assert dataset["execution_contract"]["database_allowed"] is False
    assert dataset["execution_contract"]["production_vector_store_allowed"] is False
    assert dataset["execution_contract"]["ecs_allowed"] is False


def test_v607_silver_dataset_is_hash_bound_to_real_pdf() -> None:
    dataset = load_dataset(DATASET_PATH)
    silver = load_silver_dataset(dataset)

    assert silver["document_sha256"] == dataset["document"]["sha256"]
    assert len(silver["cases"]) == 25
    assert {row["case_id"] for row in silver["cases"]} == {
        f"HKC-C3-{index:03d}" for index in range(1, 26)
    }


def test_v607_enterprise_baseline_preserves_partial_and_unknown() -> None:
    dataset = load_dataset(DATASET_PATH)
    facts = dataset["enterprise_baseline"]["facts"]

    assert dataset["enterprise_baseline"]["raw_files_reparsed"] is False
    assert [row["status"] for row in facts[:5]] == ["partial"] * 5
    assert [row["status"] for row in facts[5:]] == ["unknown"] * 6
    assert "2026年9月12日" in facts[2]["text"]
    assert "14份历史合同" in facts[3]["text"]


def test_v607_conversation_is_open_and_continuous() -> None:
    dataset = load_dataset(DATASET_PATH)
    turns = dataset["conversation"]

    assert len(turns) == 4
    assert turns[0]["expected"]["execution_mode"] == "planned"
    assert turns[1]["user_message"].startswith("投标担保")
    assert turns[2]["user_message"].startswith("那最终")
    assert turns[2]["expected"]["required_epistemic_status"] == "unknown"
    assert turns[3]["user_message"].startswith("我们的装修一级资质")


def test_v607_canonical_function_contracts_do_not_expose_output_schema() -> None:
    tools = _openai_tools(
        ("bid_document_search", "enterprise_knowledge_search", "evidence_read")
    )

    assert [row["function"]["name"] for row in tools] == [
        "bid_document_search",
        "enterprise_knowledge_search",
        "evidence_read",
    ]
    assert all("parameters" in row["function"] for row in tools)
    assert all("output_schema" not in row["function"] for row in tools)


def test_v607_enterprise_query_uses_existing_bounded_query_optimizer() -> None:
    dataset = load_dataset(DATASET_PATH)
    index = build_enterprise_index(dataset)
    retriever = object.__new__(RealOfflineRetriever)

    hits, plan_hash = retriever._lexical_channel(
        index,
        "我们的装修一级资质能满足项目要求吗？",
    )

    assert len(plan_hash) == 64
    assert hits
    top_atoms = [
        atom.normalized_text
        for hit in hits[:3]
        for atom in index.atoms_by_child_ref[hit.child_key]
    ]
    assert any("建筑装修装饰工程专业承包一级资质" in text for text in top_atoms)


def test_v607_answer_runtime_keeps_unknown_as_non_citable_receipt() -> None:
    sources = [
        {
            "grounding_ref": "grounding:v607-test-unknown",
            "source_ref": "receipt:v607-test",
            "source_scope_ref": "scope:v607-bid-document",
            "source_version_ref": "version:v607-test-v1",
            "source_basis": "runtime_receipt",
            "grounding_kind": "retrieval_receipt",
            "status": "unknown",
            "citable": False,
            "content": "已检索当前授权文档，但日期字段为空白模板。",
            "safe_title": "",
            "safe_locator_label": "",
            "safe_version_label": "",
            "locator_kind": "other",
        }
    ]

    runtime = build_answer_runtime("T03_test", sources)

    assert runtime.grounding_snapshot.records[0].status is GroundingStatus.UNKNOWN
    assert runtime.grounding_snapshot.records[0].citable is False
    assert runtime.authority_snapshot.records == ()


def test_v607_required_source_hints_are_valid_intent_values() -> None:
    dataset = load_dataset(DATASET_PATH)
    allowed = {row.value for row in InformationSourceHint}

    for turn in dataset["conversation"]:
        assert set(turn["expected"]["required_source_hints"]) <= allowed


def test_v607_dataset_path_stays_in_workspace() -> None:
    assert DATASET_PATH.is_file()
    assert DATASET_PATH.name == "v607-real-pdf-business-run.json"
    assert "AI_Middle_Office" in Path(DATASET_PATH).parts
