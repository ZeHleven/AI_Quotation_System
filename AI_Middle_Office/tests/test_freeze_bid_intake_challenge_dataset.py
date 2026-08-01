from __future__ import annotations

import sys

import pytest

from app.agents.bid_intake.retrieval_evaluation import RetrievalEvalCase
from scripts.freeze_bid_intake_challenge_dataset import (
    _approved_case,
    main,
)


def _draft_case(**updates) -> RetrievalEvalCase:
    payload = {
        "eval_case_id": "CHALLENGE-001",
        "case_id": "PROJECT-001",
        "source": "historical",
        "dataset_split": "challenge",
        "question": "项目付款条件是什么？",
        "expected_routing": {
            "query_count": 1,
            "mode_counts": {"exact": 1},
            "required_topics": ["付款"],
        },
        "gold_evidence": [
            {
                "evidence_id": "EV-GOLD",
                "relevance": 3,
                "required_text_fragments": ["付款"],
            }
        ],
        "difficulty": "hard",
        "tags": ["payment"],
        "privacy": "private_restricted",
        "annotation_status": "draft",
        "annotated_by": "annotator-a",
    }
    payload.update(updates)
    return RetrievalEvalCase.model_validate(payload)


def test_approved_case_records_independent_business_review():
    approved = _approved_case(
        _draft_case(annotation_note="Gold 已核对。"),
        reviewer="reviewer-b",
        review_note="业务复核通过。",
    )

    assert approved.annotation_status == "approved"
    assert approved.reviewed_by == "reviewer-b"
    assert approved.annotation_note == "Gold 已核对。 业务复核通过。"


def test_approved_case_rejects_non_challenge_split():
    with pytest.raises(RuntimeError, match="not a Challenge"):
        _approved_case(
            _draft_case(dataset_split="development"),
            reviewer="reviewer-b",
            review_note="业务复核通过。",
        )


def test_main_refuses_to_overwrite_existing_approved_dataset(
    tmp_path,
    monkeypatch,
):
    input_path = tmp_path / "draft.jsonl"
    output_path = tmp_path / "approved.jsonl"
    output_path.write_text("locked\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_bid_intake_challenge_dataset.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--reviewer",
            "reviewer-b",
        ],
    )

    with pytest.raises(RuntimeError, match="refusing overwrite"):
        main()

    assert output_path.read_text(encoding="utf-8") == "locked\n"
