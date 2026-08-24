from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.bid_document_parser_adapter import (
    BidDocumentOcrRequired,
    BidDocumentParserProfileDisabled,
    parse_bid_document_bytes,
)
from app.services.bid_pdf_native_layout_parser import (
    PDF_C2_PARSER_PROFILE_VERSION,
    PDF_RQ1A_PARSER_PROFILE_VERSION,
    RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    BidPdfNativeLayoutInvalid,
    BidPdfNativeLayoutLimitExceeded,
    PdfNativeLayoutProfile,
    parse_pdf_native_layout,
)
from app.services.bid_lot_detection_worker import _is_citable_detection_fragment
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    QUALITY_GATE_WARNING_CODE,
)


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_pdf(*pages: list[tuple[str, float, float, float]]) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4, pageCompression=0)
    for page in pages:
        for text, x, y, size in page:
            document.setFont("Helvetica-Bold" if size >= 14 else "Helvetica", size)
            document.drawString(x, y, text)
        document.showPage()
    document.save()
    return output.getvalue()


def _table_pdf() -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4, pageCompression=0)
    x0, y0, cell_width, cell_height = 72, 650, 150, 28
    for column in range(3):
        document.line(x0 + column * cell_width, y0, x0 + column * cell_width, y0 + 2 * cell_height)
    for row in range(3):
        document.line(x0, y0 + row * cell_height, x0 + 2 * cell_width, y0 + row * cell_height)
    document.setFont("Helvetica", 10)
    document.drawString(x0 + 8, y0 + 38, "Item")
    document.drawString(x0 + cell_width + 8, y0 + 38, "Quantity")
    document.drawString(x0 + 8, y0 + 10, "Door")
    document.drawString(x0 + cell_width + 8, y0 + 10, "12")
    document.showPage()
    document.save()
    return output.getvalue()


def _rotated_pdf() -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=A4, pageCompression=0)
    document.setPageRotation(90)
    document.setFont("Helvetica", 10)
    document.drawString(72, 500, "Rotated native text")
    document.showPage()
    document.save()
    return output.getvalue()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_pdf_c2_machine_contract_and_layout_schema() -> None:
    profile = json.loads(
        (ROOT / "contracts/bid_assessment/v1/pdf-c2-native-layout-profile.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile["contract_version"] == "bid.pdf.native-layout.v1"
    assert profile["parser_profile_version"] == PDF_C2_PARSER_PROFILE_VERSION
    assert profile["authority_policy"]["ocr_calls_allowed"] is False
    assert profile["authority_policy"]["only_pdf_c1_builder_may_create_chunks"] is True

    content = _synthetic_pdf(
        [
            ("1 Project Overview", 72, 790, 18),
            ("This is deterministic native body text for layout extraction.", 72, 750, 10),
        ]
    )
    result = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    schema = json.loads(
        (ROOT / "schemas/bid_assessment/v1/pdf-native-layout.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result.to_payload())

    rq1a_profile = json.loads(
        (ROOT / "contracts/bid_assessment/v1/rq1a-structure-profile.json").read_text(
            encoding="utf-8"
        )
    )
    assert rq1a_profile["parser_profile_version"] == PDF_RQ1A_PARSER_PROFILE_VERSION
    assert rq1a_profile["margin_artifact_policy"]["business_keyword_rules"] is False
    assert rq1a_profile["structure_aggregation_policy"][
        "heading_atom_is_citable"
    ] is True


def test_pdf_c2_emits_stable_bbox_order_and_section_paths() -> None:
    content = _synthetic_pdf(
        [
            ("1 Project Overview", 72, 790, 18),
            ("First paragraph line.", 72, 750, 10),
            ("Second paragraph line.", 72, 735, 10),
        ]
    )
    first = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    second = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    assert first.result_hash == second.result_hash
    assert [block.block_key for block in first.blocks] == [
        block.block_key for block in second.blocks
    ]
    assert first.blocks[0].block_type == "heading"
    assert first.blocks[0].section_path == ("1 Project Overview",)
    assert first.blocks[1].section_path == ("1 Project Overview",)
    assert all(block.bbox and block.bbox[2] > block.bbox[0] for block in first.blocks)
    assert [block.ordinal for block in first.blocks] == list(range(len(first.blocks)))


def test_rq1a_suppresses_repeated_margin_artifacts_and_preserves_first() -> None:
    pages = [
        [
            ("REPEATED PACKAGE HEADER", 72, 820, 10),
            (f"Unique body evidence {page_no}", 72, 700, 10),
            (f"Page {page_no} of 6", 250, 18, 10),
        ]
        for page_no in range(1, 7)
    ]
    content = _synthetic_pdf(*pages)
    legacy = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    rq1a = parse_pdf_native_layout(
        content,
        content_sha256=_sha256(content),
        profile=RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    )

    assert sum("REPEATED PACKAGE HEADER" in row.text for row in legacy.blocks) == 6
    assert sum("REPEATED PACKAGE HEADER" in row.text for row in rq1a.blocks) == 1
    assert sum(row.text.startswith("Page ") for row in rq1a.blocks) == 1
    assert rq1a.metrics["suppressed_margin_artifact_count"] == 10
    assert rq1a.metrics["suppressed_margin_signature_count"] == 2
    assert rq1a.metrics["suppressed_margin_page_count"] == 5
    assert rq1a.profile_version == "bid-pdf-native-layout-profile-v2-rq1a"
    assert rq1a.result_hash == parse_pdf_native_layout(
        content,
        content_sha256=_sha256(content),
        profile=RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    ).result_hash


def test_pdf_c2_two_column_order_keeps_full_width_heading_before_columns() -> None:
    content = _synthetic_pdf(
        [
            ("1 Overview", 220, 800, 16),
            ("LEFT-1", 72, 740, 10),
            ("LEFT-2", 72, 720, 10),
            ("LEFT-3", 72, 700, 10),
            ("RIGHT-1", 330, 740, 10),
            ("RIGHT-2", 330, 720, 10),
            ("RIGHT-3", 330, 700, 10),
        ]
    )
    result = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    body_text = "\n".join(block.text for block in result.blocks)
    assert result.pages[0].reading_order_mode == "two_column"
    assert result.blocks[0].text == "1 Overview"
    assert body_text.index("LEFT-3") < body_text.index("RIGHT-1")


def test_pdf_c2_records_page_rotation_from_pdf_page_tree() -> None:
    content = _rotated_pdf()
    result = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    assert result.pages[0].rotation == 90


def test_pdf_c2_hash_and_frozen_limits_fail_closed() -> None:
    one_page = _synthetic_pdf([("one two", 72, 760, 10)])
    with pytest.raises(BidPdfNativeLayoutInvalid, match="HASH_MISMATCH"):
        parse_pdf_native_layout(one_page, content_sha256="0" * 64)
    with pytest.raises(BidPdfNativeLayoutLimitExceeded):
        parse_pdf_native_layout(
            one_page,
            content_sha256=_sha256(one_page),
            profile=PdfNativeLayoutProfile(max_words_per_page=1),
        )
    with pytest.raises(BidPdfNativeLayoutLimitExceeded):
        parse_pdf_native_layout(
            one_page,
            content_sha256=_sha256(one_page),
            profile=PdfNativeLayoutProfile(max_native_chars=3),
        )
    two_blocks = _synthetic_pdf(
        [("1 Overview", 72, 790, 16), ("body text", 72, 750, 10)]
    )
    with pytest.raises(BidPdfNativeLayoutLimitExceeded):
        parse_pdf_native_layout(
            two_blocks,
            content_sha256=_sha256(two_blocks),
            profile=PdfNativeLayoutProfile(max_blocks=1),
        )

    two_pages = _synthetic_pdf(
        [("page one", 72, 760, 10)],
        [("page two", 72, 760, 10)],
    )
    with pytest.raises(BidPdfNativeLayoutLimitExceeded):
        parse_pdf_native_layout(
            two_pages,
            content_sha256=_sha256(two_pages),
            profile=PdfNativeLayoutProfile(max_pages=1),
        )


def test_pdf_c2_profile_is_fail_closed_until_explicitly_enabled() -> None:
    content = _synthetic_pdf([("Native PDF text", 72, 760, 10)])
    with pytest.raises(
        BidDocumentParserProfileDisabled,
        match="BID_DOCUMENT_PARSER_PROFILE_DISABLED",
    ):
        parse_bid_document_bytes(
            content=content,
            expected_sha256=_sha256(content),
            mime_type="application/pdf",
            parser_profile_version=PDF_C2_PARSER_PROFILE_VERSION,
            pdf_native_layout_enabled=False,
        )


def test_rq1a_profile_requires_its_own_gate_and_emits_citable_heading_atom() -> None:
    content = _synthetic_pdf(
        [
            ("1 Scope Area 1000 m2", 72, 790, 18),
            ("Native supporting body", 72, 750, 10),
        ]
    )
    with pytest.raises(
        BidDocumentParserProfileDisabled,
        match="BID_DOCUMENT_PARSER_PROFILE_DISABLED",
    ):
        parse_bid_document_bytes(
            content=content,
            expected_sha256=_sha256(content),
            mime_type="application/pdf",
            parser_profile_version=PDF_RQ1A_PARSER_PROFILE_VERSION,
            pdf_native_layout_enabled=True,
            rq1a_structure_enabled=False,
        )

    result = parse_bid_document_bytes(
        content=content,
        expected_sha256=_sha256(content),
        mime_type="application/pdf",
        parser_profile_version=PDF_RQ1A_PARSER_PROFILE_VERSION,
        pdf_native_layout_enabled=True,
        rq1a_structure_enabled=True,
    )
    heading_atoms = [
        row
        for row in result.evidence
        if row.locator["fragment_role"] == "evidence_atom"
        and row.locator["block_type"] == "heading"
    ]
    assert len(heading_atoms) == 1
    assert heading_atoms[0].normalized_text == "1 Scope Area 1000 m2"
    assert heading_atoms[0].locator["is_citable"] is True
    assert heading_atoms[0].locator["layout_profile_version"] == (
        "bid-pdf-native-layout-profile-v2-rq1a"
    )
    assert heading_atoms[0].locator["chunk_profile_version"] == (
        "bid-evidence-chunk-profile-v2-rq1a"
    )


def test_rq1b_profile_requires_gate_and_emits_hashed_quality_report() -> None:
    content = _synthetic_pdf(
        [
            ("1 Scope", 72, 790, 18),
            ("Short native supporting body", 72, 750, 10),
        ]
    )
    with pytest.raises(BidDocumentParserProfileDisabled):
        parse_bid_document_bytes(
            content=content,
            expected_sha256=_sha256(content),
            mime_type="application/pdf",
            parser_profile_version=PDF_RQ1B_PARSER_PROFILE_VERSION,
            pdf_native_layout_enabled=True,
            rq1a_structure_enabled=True,
            rq1b_quality_gate_enabled=False,
        )

    result = parse_bid_document_bytes(
        content=content,
        expected_sha256=_sha256(content),
        mime_type="application/pdf",
        parser_profile_version=PDF_RQ1B_PARSER_PROFILE_VERSION,
        pdf_native_layout_enabled=True,
        rq1a_structure_enabled=True,
        rq1b_quality_gate_enabled=True,
    )
    quality_warning = result.warnings[0]
    assert quality_warning["code"] == QUALITY_GATE_WARNING_CODE
    report = quality_warning["details"]
    assert report["status"] == "review_required"
    assert report["score"] == result.quality_score
    assert report["grade"] == result.quality_grade
    assert report["consumer_gates"]["retrieval_index"] is True
    schema = json.loads(
        (ROOT / "schemas/bid_assessment/v1/parse-quality.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)


def test_pdf_c2_adapter_maps_layout_through_pdf_c1_parent_child_atom() -> None:
    content = _synthetic_pdf(
        [
            ("1 Project Overview", 72, 790, 18),
            ("A" * 120, 72, 750, 10),
            ("B" * 130, 72, 730, 10),
        ]
    )
    result = parse_bid_document_bytes(
        content=content,
        expected_sha256=_sha256(content),
        mime_type="application/pdf",
        parser_profile_version=PDF_C2_PARSER_PROFILE_VERSION,
        pdf_native_layout_enabled=True,
    )
    roles = [row.locator["fragment_role"] for row in result.evidence]
    assert "section_parent" in roles
    assert "retrieval_child" in roles
    assert "evidence_atom" in roles
    by_key = {row.evidence_key: row for row in result.evidence}
    for row in result.evidence:
        if row.parent_key is not None:
            assert row.parent_key in by_key
        assert row.unit_key == "page:1"
        assert row.locator["layout_contract_version"] == "bid.pdf.native-layout.v1"
        assert row.locator["chunk_contract_version"] == "bid.evidence.chunk.v2"
    assert all(
        row.locator["is_citable"] == (row.locator["fragment_role"] == "evidence_atom")
        for row in result.evidence
    )


def test_pdf_c2_blank_document_requests_ocr_without_invoking_it() -> None:
    content = _synthetic_pdf([])
    with pytest.raises(BidDocumentOcrRequired, match="BID_DOCUMENT_OCR_REQUIRED"):
        parse_bid_document_bytes(
            content=content,
            expected_sha256=_sha256(content),
            mime_type="application/pdf",
            parser_profile_version=PDF_C2_PARSER_PROFILE_VERSION,
            pdf_native_layout_enabled=True,
        )


def test_pdf_c2_heading_only_document_is_partial_without_citable_atom() -> None:
    content = _synthetic_pdf([("1 Overview", 72, 790, 16)])
    result = parse_bid_document_bytes(
        content=content,
        expected_sha256=_sha256(content),
        mime_type="application/pdf",
        parser_profile_version=PDF_C2_PARSER_PROFILE_VERSION,
        pdf_native_layout_enabled=True,
    )
    assert result.status == "partial"
    assert result.quality_score == 40
    assert not any(
        row.locator["fragment_role"] == "evidence_atom"
        for row in result.evidence
    )
    assert result.warnings[-1]["code"] == "PDF_DOCUMENT_NO_CITABLE_BODY"


def test_pdf_c2_mixed_native_and_blank_pages_remains_partial() -> None:
    content = _synthetic_pdf(
        [("1 Overview", 72, 790, 16), ("Native text page", 72, 750, 10)],
        [],
    )
    result = parse_bid_document_bytes(
        content=content,
        expected_sha256=_sha256(content),
        mime_type="application/pdf",
        parser_profile_version=PDF_C2_PARSER_PROFILE_VERSION,
        pdf_native_layout_enabled=True,
    )
    assert result.status == "partial"
    assert result.ocr_status == "not_requested"
    assert [unit.content_source for unit in result.units] == ["native", "none"]
    assert result.units[1].warnings[0]["code"] == "PDF_PAGE_NO_NATIVE_TEXT"


def test_pdf_c2_vector_table_becomes_isolated_table_rows() -> None:
    content = _table_pdf()
    result = parse_pdf_native_layout(content, content_sha256=_sha256(content))
    rows = [block for block in result.blocks if block.block_type == "table_row"]
    assert len(rows) == 2
    assert all(" | " in row.text for row in rows)
    assert result.pages[0].table_count == 1


def test_pdf_c2_lot_detection_uses_only_citable_atoms_with_legacy_fallback() -> None:
    assert _is_citable_detection_fragment({"source_location": "legacy"})
    assert _is_citable_detection_fragment(
        {"fragment_role": "evidence_atom", "is_citable": True}
    )
    assert not _is_citable_detection_fragment(
        {"fragment_role": "section_parent", "is_citable": False}
    )
    assert not _is_citable_detection_fragment(
        {"fragment_role": "retrieval_child", "is_citable": False}
    )
    assert not _is_citable_detection_fragment(
        {"fragment_role": "evidence_atom", "is_citable": False}
    )
