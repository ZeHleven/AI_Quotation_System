"""Pure adapter from stored bytes to Phase 2 ParseUnit/Evidence contracts.

The adapter deliberately replaces the user-controlled filename with a
canonical routing name before calling the legacy parser. This prevents the
legacy XLSX filename heuristics from affecting authoritative parse output.
Filename, MIME, and parser_hint are never emitted as evidence.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from io import BytesIO
from typing import Any

from app.services.bid_document_parse_worker import (
    DocumentParseResult,
    EvidenceFragmentResult,
    ParseUnitResult,
)
from app.services.bid_evidence_chunk_builder import (
    BidEvidenceChunkBuildError,
    DEFAULT_CHUNK_PROFILE,
    RQ1A_CHUNK_PROFILE,
    build_evidence_chunks,
)
from app.services.bid_pdf_native_layout_parser import (
    DEFAULT_PDF_NATIVE_LAYOUT_PROFILE,
    PDF_C2_PARSER_PROFILE_VERSION,
    PDF_NATIVE_LAYOUT_CONTRACT_VERSION,
    PDF_RQ1A_PARSER_PROFILE_VERSION,
    RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    BidPdfNativeLayoutInvalid,
    BidPdfNativeLayoutLimitExceeded,
    parse_pdf_native_layout,
)
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    evaluate_pdf_parse_quality,
)
from app.services.bidding_parser import TenderParseError, extract_tender_text


class BidDocumentParserAdapterError(RuntimeError):
    code = "BID_DOCUMENT_PARSE_FAILED"
    retryable = False


class BidDocumentFormatUnsupported(BidDocumentParserAdapterError):
    code = "BID_FILE_TYPE_UNSUPPORTED"


class BidDocumentContentInvalid(BidDocumentParserAdapterError):
    code = "BID_FILE_CONTENT_INVALID"


class BidDocumentOcrRequired(BidDocumentParserAdapterError):
    code = "BID_DOCUMENT_OCR_REQUIRED"
    # The Phase 2 native parser never invokes OCR. Repeating the same profile
    # cannot make progress; an OCR-capable profile must create a new ParseRun.
    retryable = False


class BidDocumentParserProfileDisabled(BidDocumentParserAdapterError):
    code = "BID_DOCUMENT_PARSER_PROFILE_DISABLED"


class BidDocumentLayoutLimitExceeded(BidDocumentParserAdapterError):
    code = "BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED"


def _detect_format(content: bytes, mime_type: str | None) -> tuple[str, str]:
    if content.startswith(b"%PDF-"):
        return "pdf", "document.pdf"
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise BidDocumentFormatUnsupported("BID_FILE_TYPE_UNSUPPORTED")
    if content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise BidDocumentContentInvalid("BID_FILE_CONTENT_INVALID") from exc
        if "word/document.xml" in names:
            return "docx", "document.docx"
        if "xl/workbook.xml" in names:
            return "xlsx", "document.xlsx"
        raise BidDocumentFormatUnsupported("BID_FILE_TYPE_UNSUPPORTED")
    if content.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        raise BidDocumentOcrRequired("BID_DOCUMENT_OCR_REQUIRED")

    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if normalized_mime.startswith("text/") or normalized_mime in {
        "application/json",
        "application/xml",
    }:
        return "text", "document.txt"
    try:
        content[:65536].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BidDocumentFormatUnsupported("BID_FILE_TYPE_UNSUPPORTED") from exc
    return "text", "document.txt"


def _warning(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "code": re.sub(r"[^A-Z0-9_]", "_", code.upper())[:80],
        "message": message[:500],
        "details": details,
    }


def _column_name(number: int) -> str:
    value = max(1, int(number))
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_range(diagnostic: dict[str, Any]) -> str | None:
    effective = diagnostic.get("effective_range")
    if not isinstance(effective, dict):
        return None
    try:
        return (
            f"{_column_name(int(effective['min_column']))}{int(effective['min_row'])}:"
            f"{_column_name(int(effective['max_column']))}{int(effective['max_row'])}"
        )
    except (KeyError, TypeError, ValueError):
        return None


def _text_fingerprint(values: list[str]) -> tuple[str | None, int]:
    text = "\n".join(value.strip() for value in values if value.strip())
    if not text:
        return None, 0
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


def _quality_grade(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _parse_legacy(content: bytes, routing_name: str) -> dict[str, Any]:
    try:
        return extract_tender_text(content, routing_name, None)
    except TenderParseError as exc:
        message = str(exc)
        if "OCR" in message or "扫描" in message:
            raise BidDocumentOcrRequired("BID_DOCUMENT_OCR_REQUIRED") from exc
        if "不支持" in message:
            raise BidDocumentFormatUnsupported("BID_FILE_TYPE_UNSUPPORTED") from exc
        raise BidDocumentContentInvalid("BID_FILE_CONTENT_INVALID") from exc


def _common_section_path(values: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not values:
        return ()
    common = list(values[0])
    for value in values[1:]:
        prefix_length = 0
        for index, item in enumerate(common):
            if index >= len(value) or value[index] != item:
                break
            prefix_length += 1
        common = common[:prefix_length]
        if not common:
            break
    return tuple(common)


def _parse_pdf_native_c2(
    *,
    content: bytes,
    expected_sha256: str,
    parser_profile_version: str,
) -> DocumentParseResult:
    rq1a_enabled = parser_profile_version in {
        PDF_RQ1A_PARSER_PROFILE_VERSION,
        PDF_RQ1B_PARSER_PROFILE_VERSION,
    }
    rq1b_enabled = parser_profile_version == PDF_RQ1B_PARSER_PROFILE_VERSION
    try:
        layout = parse_pdf_native_layout(
            content,
            content_sha256=expected_sha256,
            profile=(
                RQ1A_PDF_NATIVE_LAYOUT_PROFILE
                if rq1a_enabled
                else DEFAULT_PDF_NATIVE_LAYOUT_PROFILE
            ),
        )
    except BidPdfNativeLayoutLimitExceeded as exc:
        raise BidDocumentLayoutLimitExceeded(str(exc)) from exc
    except BidPdfNativeLayoutInvalid as exc:
        raise BidDocumentContentInvalid(str(exc)) from exc
    if not layout.blocks:
        raise BidDocumentOcrRequired("BID_DOCUMENT_OCR_REQUIRED")
    try:
        chunks = build_evidence_chunks(
            layout.blocks,
            profile=RQ1A_CHUNK_PROFILE if rq1a_enabled else DEFAULT_CHUNK_PROFILE,
        )
    except BidEvidenceChunkBuildError as exc:
        raise BidDocumentContentInvalid(str(exc)) from exc

    blocks_by_page: dict[int, list[Any]] = {}
    for block in layout.blocks:
        blocks_by_page.setdefault(int(block.page_no), []).append(block)
    units: list[ParseUnitResult] = []
    for page in layout.pages:
        page_blocks = blocks_by_page.get(int(page.page_no), [])
        text_hash, text_length = _text_fingerprint(
            [str(block.text) for block in page_blocks]
        )
        units.append(
            ParseUnitResult(
                unit_key=f"page:{page.page_no}",
                unit_type="page",
                ordinal=int(page.page_no) - 1,
                page_no=int(page.page_no),
                section_path=_common_section_path(
                    [tuple(block.section_path) for block in page_blocks]
                ),
                content_source=page.content_source,
                status=page.status,
                text_hash=text_hash,
                text_length=text_length,
                ocr_status=page.ocr_status,
                warnings=tuple(page.warnings),
                metrics={
                    "layout_contract_version": layout.contract_version,
                    "layout_profile_version": layout.profile_version,
                    "parser_profile_version": parser_profile_version,
                    "page_width": page.width,
                    "page_height": page.height,
                    "page_rotation": page.rotation,
                    "native_char_count": page.native_char_count,
                    "extracted_char_count": page.extracted_char_count,
                    "word_count": page.word_count,
                    "block_count": page.block_count,
                    "table_count": page.table_count,
                    "image_count": page.image_count,
                    "text_coverage_ratio": page.text_coverage_ratio,
                    "reading_order_mode": page.reading_order_mode,
                    "layout_result_hash": layout.result_hash,
                },
            )
        )

    evidence: list[EvidenceFragmentResult] = []
    valid_unit_keys = {unit.unit_key for unit in units}
    for fragment in chunks.fragments:
        page_no = int(fragment.locator.get("page_no") or 0)
        unit_key = f"page:{page_no}"
        if unit_key not in valid_unit_keys:
            raise BidDocumentContentInvalid("BID_PDF_CHUNK_PAGE_INVALID")
        locator = dict(fragment.locator)
        locator.update(
            {
                "layout_contract_version": PDF_NATIVE_LAYOUT_CONTRACT_VERSION,
                "layout_profile_version": layout.profile_version,
                "layout_result_hash": layout.result_hash,
                "parser_profile_version": parser_profile_version,
                "chunk_contract_version": chunks.contract_version,
                "chunk_profile_version": chunks.profile_version,
                "token_estimator_version": chunks.token_estimator_version,
                "estimated_tokens": fragment.estimated_tokens,
                "evidence_key": fragment.evidence_key,
                "text_hash": fragment.text_hash,
                "retrieval_hash": fragment.retrieval_hash,
                "is_citable": fragment.is_citable,
            }
        )
        evidence.append(
            EvidenceFragmentResult(
                evidence_key=fragment.evidence_key,
                unit_key=unit_key,
                locator_type=fragment.locator_type,
                locator=locator,
                normalized_text=fragment.normalized_text,
                ordinal=fragment.ordinal,
                parent_key=fragment.parent_key,
            )
        )

    page_count = max(len(layout.pages), 1)
    missing_ratio = sum(
        page.content_source == "none" for page in layout.pages
    ) / page_count
    partial_ratio = sum(page.status == "partial" for page in layout.pages) / page_count
    average_coverage = sum(
        page.text_coverage_ratio for page in layout.pages
    ) / page_count
    score = max(
        0,
        100
        - round(missing_ratio * 50)
        - round(partial_ratio * 20)
        - round(max(0.0, 1.0 - average_coverage) * 20),
    )
    has_citable_atoms = any(
        fragment.fragment_role == "evidence_atom" and fragment.is_citable
        for fragment in chunks.fragments
    )
    citable_warnings: tuple[dict[str, Any], ...] = ()
    if not has_citable_atoms:
        score = min(score, 40)
        citable_warnings = (
            _warning(
                "PDF_DOCUMENT_NO_CITABLE_BODY",
                "PDF 只有章节性原生文本，没有可引用正文 Atom，需要人工复核",
            ),
        )
    base_warnings = (
        tuple(layout.warnings) + tuple(chunks.warnings) + citable_warnings
    )
    quality_warning: tuple[dict[str, Any], ...] = ()
    if rq1b_enabled:
        evaluation = evaluate_pdf_parse_quality(
            layout=layout,
            chunks=chunks,
            parser_profile_version=parser_profile_version,
        )
        score = evaluation.score
        quality_warning = (evaluation.to_warning(),)
    warnings = quality_warning + base_warnings
    return DocumentParseResult(
        status=(
            "partial"
            if (
                any(page.status == "partial" for page in layout.pages)
                or not has_citable_atoms
            )
            else "succeeded"
        ),
        quality_grade=_quality_grade(score),
        quality_score=score,
        ocr_status=(
            "not_requested"
            if any(page.ocr_status == "not_requested" for page in layout.pages)
            else "not_applicable"
        ),
        units=tuple(units),
        evidence=tuple(evidence),
        warnings=warnings,
    )


def parse_bid_document_bytes(
    *,
    content: bytes,
    expected_sha256: str,
    mime_type: str | None,
    parser_profile_version: str = "bid-document-parser-profile-v1",
    pdf_native_layout_enabled: bool = False,
    rq1a_structure_enabled: bool = False,
    rq1b_quality_gate_enabled: bool = False,
) -> DocumentParseResult:
    """Return deterministic native-text units; this function never invokes OCR."""

    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != str(expected_sha256).lower():
        raise BidDocumentContentInvalid("BID_FILE_CONTENT_HASH_MISMATCH")
    document_format, routing_name = _detect_format(content, mime_type)
    effective_profile = str(parser_profile_version).strip()
    if document_format == "pdf" and effective_profile in {
        PDF_C2_PARSER_PROFILE_VERSION,
        PDF_RQ1A_PARSER_PROFILE_VERSION,
        PDF_RQ1B_PARSER_PROFILE_VERSION,
    }:
        if not pdf_native_layout_enabled:
            raise BidDocumentParserProfileDisabled(
                "BID_DOCUMENT_PARSER_PROFILE_DISABLED"
            )
        if (
            effective_profile
            in {PDF_RQ1A_PARSER_PROFILE_VERSION, PDF_RQ1B_PARSER_PROFILE_VERSION}
            and not rq1a_structure_enabled
        ):
            raise BidDocumentParserProfileDisabled(
                "BID_DOCUMENT_PARSER_PROFILE_DISABLED"
            )
        if (
            effective_profile == PDF_RQ1B_PARSER_PROFILE_VERSION
            and not rq1b_quality_gate_enabled
        ):
            raise BidDocumentParserProfileDisabled(
                "BID_DOCUMENT_PARSER_PROFILE_DISABLED"
            )
        return _parse_pdf_native_c2(
            content=content,
            expected_sha256=actual_sha256,
            parser_profile_version=effective_profile,
        )
    parsed = _parse_legacy(content, routing_name)
    segments = list(parsed.get("segments") or [])
    warnings: list[dict[str, Any]] = []
    units: list[ParseUnitResult] = []
    evidence: list[EvidenceFragmentResult] = []
    unit_for_segment: dict[int, str] = {}

    if document_format == "pdf":
        page_count = max(0, int(parsed.get("page_count") or 0))
        by_page: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for index, segment in enumerate(segments):
            page_no = int(segment.get("page_number") or 0)
            if page_no > 0:
                by_page.setdefault(page_no, []).append((index, segment))
        for page_no in range(1, page_count + 1):
            page_segments = by_page.get(page_no, [])
            unit_key = f"page:{page_no}"
            texts = [str(segment.get("text") or "") for _, segment in page_segments]
            text_hash, text_length = _text_fingerprint(texts)
            has_text = bool(text_hash)
            unit_warnings = ()
            if not has_text:
                warning = _warning(
                    "PDF_PAGE_NO_TEXT",
                    "该页未提取到原生文本，需要 OCR 或人工复核",
                    page_no=page_no,
                )
                warnings.append(warning)
                unit_warnings = (warning,)
            units.append(
                ParseUnitResult(
                    unit_key=unit_key,
                    unit_type="page",
                    ordinal=page_no - 1,
                    page_no=page_no,
                    content_source="native" if has_text else "none",
                    status="succeeded" if has_text else "partial",
                    text_hash=text_hash,
                    text_length=text_length,
                    ocr_status="not_applicable" if has_text else "not_requested",
                    warnings=unit_warnings,
                )
            )
            for index, _segment in page_segments:
                unit_for_segment[index] = unit_key

    elif document_format == "xlsx":
        diagnostics = dict(parsed.get("parse_diagnostics") or {})
        sheet_rows = list(diagnostics.get("sheets") or [])
        sheet_unit_keys: dict[str, str] = {}
        for sheet_index, diagnostic in enumerate(sheet_rows):
            sheet_name = str(diagnostic.get("sheet_name") or f"Sheet{sheet_index + 1}")
            unit_key = f"sheet:{sheet_index}:{hashlib.sha256(sheet_name.encode('utf-8')).hexdigest()[:12]}"
            sheet_unit_keys[sheet_name] = unit_key
            diagnostic_status = str(diagnostic.get("status") or "skipped")
            unit_status = "succeeded" if diagnostic_status == "parsed" else (
                "partial" if diagnostic_status == "quarantined" else "skipped"
            )
            unit_warning_rows = [
                _warning(
                    f"XLSX_{code}",
                    "Sheet 解析存在需要复核的结构警告",
                    sheet_index=sheet_index,
                    sheet_name=sheet_name,
                )
                for code in diagnostic.get("warning_codes") or []
            ]
            warnings.extend(unit_warning_rows)
            sheet_texts = [
                str(segment.get("text") or "")
                for segment in segments
                if str(segment.get("source_location") or "").startswith(
                    f"{sheet_name} 第"
                )
            ]
            text_hash, text_length = _text_fingerprint(sheet_texts)
            units.append(
                ParseUnitResult(
                    unit_key=unit_key,
                    unit_type="sheet",
                    ordinal=sheet_index,
                    sheet_index=sheet_index,
                    sheet_name=sheet_name,
                    cell_range=_sheet_range(diagnostic),
                    content_source="native" if text_hash else "none",
                    status=unit_status,
                    text_hash=text_hash,
                    text_length=text_length,
                    ocr_status="not_applicable",
                    warnings=tuple(unit_warning_rows),
                    metrics={
                        key: diagnostic[key]
                        for key in (
                            "physical_row_count",
                            "extracted_row_count",
                            "meaningful_cell_count",
                            "formula_cell_count",
                            "ignored_value_cell_count",
                        )
                        if key in diagnostic
                    },
                )
            )
        for index, segment in enumerate(segments):
            location = str(segment.get("source_location") or "")
            match = re.match(r"^(.*?) 第(\d+)行$", location)
            if match and match.group(1) in sheet_unit_keys:
                unit_for_segment[index] = sheet_unit_keys[match.group(1)]

    else:
        texts = [str(segment.get("text") or "") for segment in segments]
        text_hash, text_length = _text_fingerprint(texts)
        units.append(
            ParseUnitResult(
                unit_key="document:0",
                unit_type="document",
                ordinal=0,
                content_source="native",
                status="succeeded",
                text_hash=text_hash,
                text_length=text_length,
                ocr_status="not_applicable",
            )
        )
        unit_for_segment = {index: "document:0" for index in range(len(segments))}

    if not units:
        raise BidDocumentContentInvalid("BID_DOCUMENT_PARSE_NO_UNITS")

    for index, segment in enumerate(segments):
        unit_key = unit_for_segment.get(index)
        text = str(segment.get("text") or "").strip()
        if not unit_key or not text:
            continue
        location = str(segment.get("source_location") or "")
        locator_type = "section"
        locator: dict[str, Any] = {
            "source_location": location,
            "section_index": segment.get("section_index"),
            "document_section": segment.get("document_section"),
        }
        if document_format == "pdf":
            locator["page_no"] = int(segment.get("page_number") or 0)
        elif document_format == "xlsx":
            match = re.match(r"^(.*?) 第(\d+)行$", location)
            if match:
                locator_type = "sheet_range"
                locator = {
                    "sheet_name": match.group(1),
                    "row_range": f"{match.group(2)}:{match.group(2)}",
                    "source_location": location,
                }
        evidence.append(
            EvidenceFragmentResult(
                evidence_key=f"segment:{index}",
                unit_key=unit_key,
                locator_type=locator_type,
                locator=locator,
                normalized_text=text,
                ordinal=index,
            )
        )

    if not evidence:
        raise BidDocumentOcrRequired("BID_DOCUMENT_OCR_REQUIRED")
    warning_codes = list(
        dict.fromkeys(str(item.get("code") or "") for item in warnings)
    )
    missing_ratio = sum(unit.content_source == "none" for unit in units) / max(
        len(units),
        1,
    )
    score = max(0, 100 - min(len(warning_codes), 4) * 10 - round(missing_ratio * 40))
    status = "partial" if warnings or missing_ratio > 0 else "succeeded"
    return DocumentParseResult(
        status=status,
        quality_grade=_quality_grade(score),
        quality_score=score,
        ocr_status=(
            "not_requested"
            if any(unit.content_source == "none" for unit in units)
            else "not_applicable"
        ),
        units=tuple(units),
        evidence=tuple(evidence),
        warnings=tuple(warnings),
    )
