"""Deterministic, evidence-only lot candidate detector for Phase 2.

This module receives normalized EvidenceFragments only. It has no filename,
MIME, parser_hint, FileObject, or object-store inputs by design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.bid_assessment_eventing import canonical_hash


_CODE = r"[0-9一二三四五六七八九十百零〇两]+"
_PREFIX_PATTERN = re.compile(
    rf"(?:第\s*)?(?P<code>{_CODE})\s*(?P<kind>标段|包)(?!含|括)",
)
_LABEL_PATTERN = re.compile(
    rf"(?P<kind>标段|包)(?:编号|号)?\s*[:：]?\s*(?P<code>{_CODE})(?!\s*个)",
)
_TRAILING_NAME_PATTERN = re.compile(
    rf"(?:第\s*)?(?P<code>{_CODE})\s*(?P<kind>标段|包)(?:名称)?\s*[:：\-—]\s*"
    r"(?P<name>[^\n；;。]{2,80})",
)


@dataclass(frozen=True)
class LotDetectionEvidenceInput:
    evidence_id: str
    document_version_id: str
    role: str
    text: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class LotCandidateEvidenceInput:
    evidence_id: str
    document_version_id: str
    support_role: str
    display_label: str


@dataclass(frozen=True)
class DetectedLotCandidate:
    lot_code: str | None
    lot_name: str
    scope_summary: str | None
    normalized_lot_key: str
    source_status: str
    confidence_score: str
    confidence_level: str
    candidate_hash: str
    warnings: tuple[dict[str, Any], ...]
    evidence: tuple[LotCandidateEvidenceInput, ...]


def _normalized_code(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or ""))
    if compact.isdigit():
        return str(int(compact))
    return compact


def _display_label(locator: dict[str, Any]) -> str:
    if locator.get("page_no"):
        return f"第{locator['page_no']}页"
    if locator.get("sheet_name"):
        row_range = locator.get("row_range")
        return (
            f"Sheet“{locator['sheet_name']}”第{row_range}行"
            if row_range
            else f"Sheet“{locator['sheet_name']}”"
        )
    if locator.get("source_location"):
        return str(locator["source_location"])[:300]
    return "解析正文"


def _candidate_name(text: str, *, code: str, kind: str) -> str:
    for match in _TRAILING_NAME_PATTERN.finditer(text):
        if _normalized_code(match.group("code")) != code:
            continue
        name = re.sub(r"\s+", " ", match.group("name")).strip(" -—:：")
        if name:
            return name[:500]
    return f"第{code}{kind}"[:500]


def detect_lot_candidates(
    evidence_rows: tuple[LotDetectionEvidenceInput, ...],
) -> tuple[DetectedLotCandidate, ...]:
    """Detect only explicit lot/package identifiers present in content evidence."""

    grouped: dict[str, dict[str, Any]] = {}
    for evidence in evidence_rows:
        text = re.sub(r"\s+", " ", evidence.text).strip()
        if not text:
            continue
        matches = list(_PREFIX_PATTERN.finditer(text)) + list(
            _LABEL_PATTERN.finditer(text)
        )
        seen_codes: set[str] = set()
        for match in matches:
            code = _normalized_code(match.group("code"))
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            kind = str(match.group("kind"))
            normalized_key = f"{kind}:{code}".lower()
            entry = grouped.setdefault(
                normalized_key,
                {
                    "code": code,
                    "kind": kind,
                    "names": [],
                    "snippets": [],
                    "evidence": {},
                },
            )
            entry["names"].append(_candidate_name(text, code=code, kind=kind))
            entry["snippets"].append(text[:500])
            entry["evidence"][evidence.evidence_id] = LotCandidateEvidenceInput(
                evidence_id=evidence.evidence_id,
                document_version_id=evidence.document_version_id,
                support_role=("scope" if "范围" in text else "identity"),
                display_label=_display_label(evidence.locator),
            )

    candidates: list[DetectedLotCandidate] = []
    for normalized_key, entry in sorted(grouped.items()):
        evidence = tuple(entry["evidence"].values())
        if not evidence:
            continue
        names = [name for name in entry["names"] if name]
        lot_name = max(names, key=lambda value: (names.count(value), -len(value)))
        score_value = min(0.95, 0.72 + 0.06 * min(len(evidence), 3))
        confidence_level = "high" if score_value >= 0.85 else (
            "medium" if score_value >= 0.65 else "low"
        )
        candidate_payload = {
            "normalized_lot_key": normalized_key,
            "lot_code": entry["code"],
            "lot_name": lot_name,
            "scope_summary": entry["snippets"][0] if entry["snippets"] else None,
            "evidence_ids": sorted(row.evidence_id for row in evidence),
        }
        candidates.append(
            DetectedLotCandidate(
                lot_code=entry["code"],
                lot_name=lot_name,
                scope_summary=candidate_payload["scope_summary"],
                normalized_lot_key=normalized_key,
                source_status="detected",
                confidence_score=f"{score_value:.6f}",
                confidence_level=confidence_level,
                candidate_hash=canonical_hash(candidate_payload),
                warnings=(),
                evidence=evidence,
            )
        )
    return tuple(candidates)


def build_whole_manifest_scope_candidate(
    evidence_rows: tuple[LotDetectionEvidenceInput, ...],
) -> DetectedLotCandidate | None:
    """Build an explicit whole-Manifest choice when no lot label exists.

    This is a user-selectable scope, not a lot inference.  It uses no filename,
    MIME, parser hint, keyword classification, OCR, or model output.
    """

    if not evidence_rows:
        return None
    anchor = evidence_rows[0]
    evidence = LotCandidateEvidenceInput(
        evidence_id=anchor.evidence_id,
        document_version_id=anchor.document_version_id,
        support_role="overall_scope",
        display_label=_display_label(anchor.locator),
    )
    payload = {
        "normalized_lot_key": "system_scope:whole_manifest",
        "lot_code": None,
        "lot_name": "整份资料研判（未识别显式标段）",
        "scope_summary": "按当前 Manifest 的全部已解析资料进行初步研判",
        "source_status": "system_scope",
        "evidence_ids": [anchor.evidence_id],
    }
    return DetectedLotCandidate(
        lot_code=None,
        lot_name=str(payload["lot_name"]),
        scope_summary=str(payload["scope_summary"]),
        normalized_lot_key=str(payload["normalized_lot_key"]),
        source_status="system_scope",
        confidence_score="0.400000",
        confidence_level="low",
        candidate_hash=canonical_hash(payload),
        warnings=(
            {
                "code": "WHOLE_MANIFEST_SCOPE_REQUIRES_CONFIRMATION",
                "message": "正文未识别到显式标段，需由用户确认按整份资料研判",
            },
        ),
        evidence=(evidence,),
    )
