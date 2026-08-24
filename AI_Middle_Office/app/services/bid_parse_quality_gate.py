"""Deterministic RQ1-B parse-quality scoring and consumer gates.

The quality report contains aggregate structure metrics and stable reason
codes only.  It never persists document excerpts and performs no OCR, model,
network, or storage call.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


PARSE_QUALITY_CONTRACT_VERSION = "bid.parse.quality.v1"
PARSE_QUALITY_PROFILE_VERSION = "bid-parse-quality-profile-v1"
PDF_RQ1B_PARSER_PROFILE_VERSION = (
    "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
)
QUALITY_GATE_WARNING_CODE = "PDF_PARSE_QUALITY_GATE_EVALUATED"
QUALITY_GATE_STATUSES = frozenset({"pass", "review_required", "blocked"})
QUALITY_GATE_CONSUMERS = frozenset(
    {"retrieval_index", "lot_detection", "automated_assessment"}
)


class BidParseQualityGateError(RuntimeError):
    code = "BID_PARSE_QUALITY_GATE_INVALID"


class BidParseQualityGateBlocked(BidParseQualityGateError):
    code = "BID_PARSE_QUALITY_GATE_BLOCKED"

    def __init__(self, consumer: str) -> None:
        self.consumer = str(consumer)
        super().__init__(f"{self.code}:{self.consumer}")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return round(int(numerator) / max(int(denominator), 1), 6)


def _grade(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


@dataclass(frozen=True)
class ParseQualityEvaluation:
    parser_profile_version: str
    status: str
    score: int
    grade: str
    blocking_reasons: tuple[str, ...]
    review_reasons: tuple[str, ...]
    metrics: dict[str, int | float]
    dimension_scores: dict[str, int]
    consumer_gates: dict[str, bool]
    result_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": PARSE_QUALITY_CONTRACT_VERSION,
            "profile_version": PARSE_QUALITY_PROFILE_VERSION,
            "parser_profile_version": self.parser_profile_version,
            "status": self.status,
            "score": self.score,
            "grade": self.grade,
            "blocking_reasons": list(self.blocking_reasons),
            "review_reasons": list(self.review_reasons),
            "metrics": dict(self.metrics),
            "dimension_scores": dict(self.dimension_scores),
            "consumer_gates": dict(self.consumer_gates),
            "result_hash": self.result_hash,
        }

    def to_warning(self) -> dict[str, Any]:
        messages = {
            "pass": "PDF 解析质量门已通过",
            "review_required": "PDF 解析结果可继续受控处理，但需要质量复核",
            "blocked": "PDF 解析质量不足，已阻断自动下游消费",
        }
        return {
            "code": QUALITY_GATE_WARNING_CODE,
            "message": messages[self.status],
            "details": self.to_payload(),
        }


def evaluate_pdf_parse_quality(
    *,
    layout: Any,
    chunks: Any,
    parser_profile_version: str = PDF_RQ1B_PARSER_PROFILE_VERSION,
) -> ParseQualityEvaluation:
    """Score one immutable RQ1-B parse result from C2/C1 aggregates."""

    if str(parser_profile_version) != PDF_RQ1B_PARSER_PROFILE_VERSION:
        raise BidParseQualityGateError("BID_PARSE_QUALITY_PROFILE_INVALID")

    pages = tuple(layout.pages)
    page_count = len(pages)
    if page_count < 1:
        raise BidParseQualityGateError("BID_PARSE_QUALITY_PAGE_COUNT_INVALID")

    layout_warnings = tuple(layout.warnings)
    chunk_warnings = tuple(chunks.warnings)
    chunk_metrics = dict(chunks.metrics or {})
    missing_page_count = sum(page.content_source == "none" for page in pages)
    partial_page_count = sum(page.status == "partial" for page in pages)
    ocr_pending_page_count = sum(
        page.ocr_status in {"not_requested", "queued", "running", "partial", "failed"}
        for page in pages
    )
    child_count = int(chunk_metrics.get("retrieval_child_count") or 0)
    atom_count = int(chunk_metrics.get("evidence_atom_count") or 0)
    heading_count = int(chunk_metrics.get("heading_block_count") or 0)
    citable_heading_count = int(
        chunk_metrics.get("citable_heading_atom_count") or 0
    )
    undersized_non_isolated_count = sum(
        str(row.get("code") or "") == "BID_CHUNK_CHILD_BELOW_SOFT_MIN"
        for row in chunk_warnings
    )
    informational_codes = {
        "BID_CHUNK_CHILD_BELOW_SOFT_MIN",
        "PDF_REPEATED_MARGIN_ARTIFACTS_SUPPRESSED",
        QUALITY_GATE_WARNING_CODE,
    }
    actionable_warning_count = sum(
        str(row.get("code") or "") not in informational_codes
        for row in (*layout_warnings, *chunk_warnings)
    )

    missing_page_ratio = _ratio(missing_page_count, page_count)
    partial_page_ratio = _ratio(partial_page_count, page_count)
    ocr_pending_page_ratio = _ratio(ocr_pending_page_count, page_count)
    undersized_child_ratio = _ratio(
        undersized_non_isolated_count,
        child_count,
    )
    citable_heading_ratio = (
        min(1.0, _ratio(citable_heading_count, heading_count))
        if heading_count
        else 1.0
    )
    actionable_warning_density = _ratio(actionable_warning_count, page_count)

    native_readiness = round(
        30
        * max(
            0.0,
            1.0 - min(1.0, missing_page_ratio * 2 + partial_page_ratio),
        )
    )
    structural_coherence = round(
        35 * max(0.0, 1.0 - min(1.0, undersized_child_ratio))
    )
    citable_integrity = (
        0
        if atom_count == 0
        else 20 + round(5 * max(0.0, min(1.0, citable_heading_ratio)))
    )
    warning_hygiene = round(
        10
        * max(
            0.0,
            1.0 - min(1.0, actionable_warning_density / 2.0),
        )
    )
    dimension_scores = {
        "native_readiness": native_readiness,
        "structural_coherence": structural_coherence,
        "citable_integrity": citable_integrity,
        "warning_hygiene": warning_hygiene,
    }
    raw_score = sum(dimension_scores.values())

    blocking_reasons: list[str] = []
    if child_count == 0:
        blocking_reasons.append("retrieval_child_missing")
    if atom_count == 0:
        blocking_reasons.append("citable_atom_missing")
    if missing_page_ratio >= 0.20:
        blocking_reasons.append("missing_page_ratio_at_or_above_0_20")
    if partial_page_ratio >= 0.20:
        blocking_reasons.append("partial_page_ratio_at_or_above_0_20")
    if citable_heading_ratio < 0.90:
        blocking_reasons.append("citable_heading_ratio_below_0_90")

    score = min(raw_score, 39) if blocking_reasons else raw_score
    if not blocking_reasons and partial_page_count:
        # A partial parse can never claim a high grade, even when all other
        # dimensions are healthy.
        score = min(score, 84)
    grade = _grade(score)

    review_reasons: list[str] = []
    if partial_page_count:
        review_reasons.append("partial_pages_present")
    if ocr_pending_page_count:
        review_reasons.append("ocr_pending_pages_present")
    if undersized_child_ratio > 0.35:
        review_reasons.append("undersized_child_ratio_above_0_35")
    if actionable_warning_density > 0.50:
        review_reasons.append("warning_density_above_0_50_per_page")
    if score < 85:
        review_reasons.append("score_below_high_gate")

    if blocking_reasons:
        status = "blocked"
    elif review_reasons:
        status = "review_required"
    else:
        status = "pass"

    consumer_gates = {
        "retrieval_index": not blocking_reasons,
        "lot_detection": (
            not blocking_reasons
            and missing_page_ratio <= 0.10
            and citable_heading_ratio >= 0.95
        ),
        "automated_assessment": not blocking_reasons and score >= 60,
    }
    metrics: dict[str, int | float] = {
        "page_count": page_count,
        "missing_page_count": missing_page_count,
        "missing_page_ratio": missing_page_ratio,
        "partial_page_count": partial_page_count,
        "partial_page_ratio": partial_page_ratio,
        "ocr_pending_page_count": ocr_pending_page_count,
        "ocr_pending_page_ratio": ocr_pending_page_ratio,
        "retrieval_child_count": child_count,
        "evidence_atom_count": atom_count,
        "heading_block_count": heading_count,
        "citable_heading_atom_count": citable_heading_count,
        "citable_heading_ratio": citable_heading_ratio,
        "undersized_non_isolated_child_count": undersized_non_isolated_count,
        "undersized_non_isolated_child_ratio": undersized_child_ratio,
        "actionable_warning_count": actionable_warning_count,
        "actionable_warning_density_per_page": actionable_warning_density,
    }
    payload_without_hash = {
        "contract_version": PARSE_QUALITY_CONTRACT_VERSION,
        "profile_version": PARSE_QUALITY_PROFILE_VERSION,
        "parser_profile_version": str(parser_profile_version),
        "status": status,
        "score": score,
        "grade": grade,
        "blocking_reasons": blocking_reasons,
        "review_reasons": review_reasons,
        "metrics": metrics,
        "dimension_scores": dimension_scores,
        "consumer_gates": consumer_gates,
    }
    return ParseQualityEvaluation(
        parser_profile_version=str(parser_profile_version),
        status=status,
        score=score,
        grade=grade,
        blocking_reasons=tuple(blocking_reasons),
        review_reasons=tuple(review_reasons),
        metrics=metrics,
        dimension_scores=dimension_scores,
        consumer_gates=consumer_gates,
        result_hash=_canonical_hash(payload_without_hash),
    )


def _quality_warning_details(warnings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for row in warnings:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("code") or "") != QUALITY_GATE_WARNING_CODE:
            continue
        details = row.get("details")
        if not isinstance(details, Mapping):
            raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_FIELDS_INVALID")
        matches.append(dict(details))
    if len(matches) != 1:
        raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_CARDINALITY_INVALID")
    return matches[0]


def validate_quality_report(
    *,
    warnings: Iterable[Mapping[str, Any]],
    parser_profile_version: str,
    quality_score: int,
    quality_grade: str,
) -> dict[str, Any] | None:
    """Validate the persisted RQ1-B report; legacy profiles remain unchanged."""

    if str(parser_profile_version) != PDF_RQ1B_PARSER_PROFILE_VERSION:
        return None
    details = _quality_warning_details(warnings)
    required = {
        "contract_version",
        "profile_version",
        "parser_profile_version",
        "status",
        "score",
        "grade",
        "blocking_reasons",
        "review_reasons",
        "metrics",
        "dimension_scores",
        "consumer_gates",
        "result_hash",
    }
    if set(details) != required:
        raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_FIELDS_INVALID")
    if (
        details.get("contract_version") != PARSE_QUALITY_CONTRACT_VERSION
        or details.get("profile_version") != PARSE_QUALITY_PROFILE_VERSION
        or details.get("parser_profile_version") != PDF_RQ1B_PARSER_PROFILE_VERSION
        or details.get("status") not in QUALITY_GATE_STATUSES
        or int(details.get("score", -1)) != int(quality_score)
        or str(details.get("grade") or "") != str(quality_grade)
    ):
        raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_LINEAGE_INVALID")
    score = int(details["score"])
    status = str(details["status"])
    blocking_reasons = details.get("blocking_reasons")
    review_reasons = details.get("review_reasons")
    if (
        not isinstance(blocking_reasons, list)
        or not isinstance(review_reasons, list)
        or str(details["grade"]) != _grade(score)
        or (status == "blocked" and not blocking_reasons)
        or (status != "blocked" and bool(blocking_reasons))
        or (status == "pass" and (review_reasons or score < 85))
        or (status == "review_required" and not review_reasons)
    ):
        raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_STATE_INVALID")
    consumer_gates = details.get("consumer_gates")
    if (
        not isinstance(consumer_gates, dict)
        or set(consumer_gates) != QUALITY_GATE_CONSUMERS
        or any(type(value) is not bool for value in consumer_gates.values())
    ):
        raise BidParseQualityGateError("BID_PARSE_QUALITY_CONSUMER_GATES_INVALID")
    if status == "blocked" and any(consumer_gates.values()):
        raise BidParseQualityGateError("BID_PARSE_QUALITY_CONSUMER_GATES_INVALID")
    payload_without_hash = {
        key: value for key, value in details.items() if key != "result_hash"
    }
    if _canonical_hash(payload_without_hash) != str(details.get("result_hash") or ""):
        raise BidParseQualityGateError("BID_PARSE_QUALITY_REPORT_HASH_MISMATCH")
    return details


def quality_report_for_run(run: Any) -> dict[str, Any] | None:
    return validate_quality_report(
        warnings=tuple(run.warnings_json or ()),
        parser_profile_version=str(run.parser_profile_version),
        quality_score=int(run.quality_score or 0),
        quality_grade=str(run.quality_grade or ""),
    )


def assert_parse_run_consumer_allowed(run: Any, *, consumer: str) -> dict[str, Any] | None:
    normalized_consumer = str(consumer)
    if normalized_consumer not in QUALITY_GATE_CONSUMERS:
        raise BidParseQualityGateError("BID_PARSE_QUALITY_CONSUMER_INVALID")
    report = quality_report_for_run(run)
    if report is not None and report["consumer_gates"][normalized_consumer] is not True:
        raise BidParseQualityGateBlocked(normalized_consumer)
    return report


__all__ = [
    "BidParseQualityGateBlocked",
    "BidParseQualityGateError",
    "PARSE_QUALITY_CONTRACT_VERSION",
    "PARSE_QUALITY_PROFILE_VERSION",
    "PDF_RQ1B_PARSER_PROFILE_VERSION",
    "QUALITY_GATE_CONSUMERS",
    "QUALITY_GATE_STATUSES",
    "QUALITY_GATE_WARNING_CODE",
    "ParseQualityEvaluation",
    "assert_parse_run_consumer_allowed",
    "evaluate_pdf_parse_quality",
    "quality_report_for_run",
    "validate_quality_report",
]
