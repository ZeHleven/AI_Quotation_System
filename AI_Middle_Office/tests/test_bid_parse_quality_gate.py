from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    BidParseQualityGateBlocked,
    BidParseQualityGateError,
    assert_parse_run_consumer_allowed,
    evaluate_pdf_parse_quality,
    validate_quality_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _page(*, status: str = "succeeded", source: str = "native", ocr: str = "not_applicable"):
    return SimpleNamespace(status=status, content_source=source, ocr_status=ocr)


def _layout(pages, warnings=()):
    return SimpleNamespace(pages=tuple(pages), warnings=tuple(warnings))


def _chunks(
    *,
    children: int,
    atoms: int,
    headings: int,
    citable_headings: int,
    undersized: int = 0,
    warnings=(),
):
    undersized_warnings = tuple(
        {"code": "BID_CHUNK_CHILD_BELOW_SOFT_MIN"}
        for _ in range(undersized)
    )
    return SimpleNamespace(
        metrics={
            "retrieval_child_count": children,
            "evidence_atom_count": atoms,
            "heading_block_count": headings,
            "citable_heading_atom_count": citable_headings,
        },
        warnings=undersized_warnings + tuple(warnings),
    )


def _run(evaluation):
    return SimpleNamespace(
        parser_profile_version=PDF_RQ1B_PARSER_PROFILE_VERSION,
        quality_score=evaluation.score,
        quality_grade=evaluation.grade,
        warnings_json=[evaluation.to_warning()],
    )


def test_rq1b_machine_profile_and_schema_are_frozen() -> None:
    profile = json.loads(
        (ROOT / "contracts/bid_assessment/v1/rq1b-parse-quality-profile.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas/bid_assessment/v1/parse-quality.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    assert profile["contract_version"] == "bid.parse.quality.v1"
    assert profile["parser_profile_version"] == PDF_RQ1B_PARSER_PROFILE_VERSION
    assert sum(profile["dimension_weights"].values()) == 100
    assert profile["authority_policy"]["legacy_profile_output_unchanged"] is True


def test_rq1b_hong_kong_shape_is_medium_review_not_false_high() -> None:
    pages = [_page() for _ in range(306)] + [
        _page(status="partial", source="none", ocr="not_requested")
    ]
    evaluation = evaluate_pdf_parse_quality(
        layout=_layout(
            pages,
            warnings=(
                {"code": "PDF_REPEATED_MARGIN_ARTIFACTS_SUPPRESSED"},
                {"code": "PDF_PAGE_NATIVE_TEXT_INSUFFICIENT"},
                {"code": "PDF_PAGE_ONLY_REPEATED_MARGIN_ARTIFACT"},
            ),
        ),
        chunks=_chunks(
            children=1244,
            atoms=5276,
            headings=1039,
            citable_headings=1039,
            undersized=585,
        ),
    )

    assert evaluation.status == "review_required"
    assert evaluation.score == 84
    assert evaluation.grade == "medium"
    assert evaluation.blocking_reasons == ()
    assert evaluation.consumer_gates == {
        "retrieval_index": True,
        "lot_detection": True,
        "automated_assessment": True,
    }
    assert "undersized_child_ratio_above_0_35" in evaluation.review_reasons


def test_rq1b_severe_missing_pages_block_all_consumers() -> None:
    evaluation = evaluate_pdf_parse_quality(
        layout=_layout(
            [
                _page(),
                _page(status="partial", source="none", ocr="not_requested"),
            ]
        ),
        chunks=_chunks(
            children=2,
            atoms=4,
            headings=1,
            citable_headings=1,
        ),
    )

    assert evaluation.status == "blocked"
    assert evaluation.score <= 39
    assert evaluation.grade == "low"
    assert evaluation.consumer_gates == {
        "retrieval_index": False,
        "lot_detection": False,
        "automated_assessment": False,
    }
    with pytest.raises(BidParseQualityGateBlocked):
        assert_parse_run_consumer_allowed(
            _run(evaluation),
            consumer="retrieval_index",
        )


def test_rq1b_clean_shape_passes_and_hash_is_stable() -> None:
    kwargs = {
        "layout": _layout([_page(), _page()]),
        "chunks": _chunks(
            children=4,
            atoms=12,
            headings=3,
            citable_headings=3,
        ),
    }
    first = evaluate_pdf_parse_quality(**kwargs)
    second = evaluate_pdf_parse_quality(**kwargs)

    assert first.status == "pass"
    assert first.score == 100
    assert first.grade == "high"
    assert first.result_hash == second.result_hash
    assert_parse_run_consumer_allowed(
        _run(first),
        consumer="automated_assessment",
    )


def test_rq1b_report_hash_and_authority_fields_fail_closed() -> None:
    evaluation = evaluate_pdf_parse_quality(
        layout=_layout([_page()]),
        chunks=_chunks(
            children=1,
            atoms=2,
            headings=1,
            citable_headings=1,
        ),
    )
    warning = evaluation.to_warning()
    validate_quality_report(
        warnings=[warning],
        parser_profile_version=PDF_RQ1B_PARSER_PROFILE_VERSION,
        quality_score=evaluation.score,
        quality_grade=evaluation.grade,
    )

    drifted = deepcopy(warning)
    drifted["details"]["score"] -= 1
    with pytest.raises(BidParseQualityGateError):
        validate_quality_report(
            warnings=[drifted],
            parser_profile_version=PDF_RQ1B_PARSER_PROFILE_VERSION,
            quality_score=evaluation.score - 1,
            quality_grade=evaluation.grade,
        )


def test_legacy_profile_does_not_require_rq1b_report() -> None:
    assert (
        validate_quality_report(
            warnings=[],
            parser_profile_version="bid-document-parser-profile-v3-pdf-structure-rq1a",
            quality_score=100,
            quality_grade="high",
        )
        is None
    )
