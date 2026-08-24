"""Deterministic structure-aware chunk construction for bid evidence.

PDF-C1 deliberately starts after layout extraction.  It accepts ordered,
structure-bearing text blocks and produces a three-level immutable hierarchy:

``section_parent -> retrieval_child -> evidence_atom``.

The module performs no PDF I/O, OCR, model call, database write, or retrieval.
Later PDF-C2 code may feed native PDF blocks into this builder, while Phase 4
retrieval may index ``retrieval_text`` and must still cite only atoms marked
``is_citable``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


CHUNK_CONTRACT_VERSION = "bid.evidence.chunk.v2"
CHUNK_PROFILE_VERSION = "bid-evidence-chunk-profile-v1"
RQ1A_CHUNK_PROFILE_VERSION = "bid-evidence-chunk-profile-v2-rq1a"
TOKEN_ESTIMATOR_VERSION = "bid-token-estimator-cjk-conservative-v1"

FRAGMENT_ROLES = frozenset(
    {"section_parent", "retrieval_child", "evidence_atom"}
)
BLOCK_TYPES = frozenset(
    {
        "heading",
        "paragraph",
        "clause",
        "list_item",
        "table",
        "table_row",
        "form_field",
        "image",
        "caption",
        "attachment_boundary",
    }
)
ISOLATED_BLOCK_TYPES = frozenset(
    {
        "clause",
        "table",
        "table_row",
        "form_field",
        "image",
        "caption",
        "attachment_boundary",
    }
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]|[^\s]")
_SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
_BLANK_LINE_PATTERN = re.compile(r"\n{3,}")
_SENTENCE_ENDINGS = frozenset("。！？!?；;")


class BidEvidenceChunkBuildError(ValueError):
    """Raised when a structure block or chunk profile violates the contract."""


@dataclass(frozen=True)
class ChunkBuilderProfile:
    """Frozen deterministic limits for one chunk-profile version."""

    profile_version: str = CHUNK_PROFILE_VERSION
    token_estimator_version: str = TOKEN_ESTIMATOR_VERSION
    soft_min_tokens: int = 220
    target_tokens: int = 380
    soft_max_tokens: int = 500
    hard_max_tokens: int = 600
    long_overlap_tokens: int = 80
    include_heading_atoms: bool = False
    parent_section_max_depth: int | None = None
    aggregate_table_rows: bool = False

    def __post_init__(self) -> None:
        values = (
            self.soft_min_tokens,
            self.target_tokens,
            self.soft_max_tokens,
            self.hard_max_tokens,
        )
        if not all(type(value) is int and value > 0 for value in values):
            raise BidEvidenceChunkBuildError("BID_CHUNK_PROFILE_LIMIT_INVALID")
        if not (
            self.soft_min_tokens
            <= self.target_tokens
            <= self.soft_max_tokens
            <= self.hard_max_tokens
        ):
            raise BidEvidenceChunkBuildError("BID_CHUNK_PROFILE_ORDER_INVALID")
        if (
            type(self.long_overlap_tokens) is not int
            or not 0 <= self.long_overlap_tokens < self.target_tokens
        ):
            raise BidEvidenceChunkBuildError("BID_CHUNK_PROFILE_OVERLAP_INVALID")
        if not str(self.profile_version).strip():
            raise BidEvidenceChunkBuildError("BID_CHUNK_PROFILE_VERSION_INVALID")
        if not str(self.token_estimator_version).strip():
            raise BidEvidenceChunkBuildError(
                "BID_CHUNK_TOKEN_ESTIMATOR_VERSION_INVALID"
            )
        if self.token_estimator_version != TOKEN_ESTIMATOR_VERSION:
            raise BidEvidenceChunkBuildError(
                "BID_CHUNK_TOKEN_ESTIMATOR_UNSUPPORTED"
            )
        if (
            type(self.include_heading_atoms) is not bool
            or type(self.aggregate_table_rows) is not bool
        ):
            raise BidEvidenceChunkBuildError("BID_CHUNK_PROFILE_FLAG_INVALID")
        if self.parent_section_max_depth is not None and (
            type(self.parent_section_max_depth) is not int
            or self.parent_section_max_depth < 1
            or self.parent_section_max_depth > 6
        ):
            raise BidEvidenceChunkBuildError(
                "BID_CHUNK_PROFILE_SECTION_DEPTH_INVALID"
            )


DEFAULT_CHUNK_PROFILE = ChunkBuilderProfile()
RQ1A_CHUNK_PROFILE = ChunkBuilderProfile(
    profile_version=RQ1A_CHUNK_PROFILE_VERSION,
    include_heading_atoms=True,
    parent_section_max_depth=1,
    aggregate_table_rows=True,
)


@dataclass(frozen=True)
class StructuredEvidenceBlock:
    """One ordered block emitted by a future structure-aware parser.

    ``char_start``/``char_end`` in generated atoms refer to this block's
    normalized text, never to raw PDF bytes or an unnormalised extraction.
    """

    block_key: str
    text: str
    block_type: str
    page_no: int
    ordinal: int
    section_path: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    boundary_before: bool = False
    boundary_after: bool = False


@dataclass(frozen=True)
class EvidenceChunkFragment:
    """A transport-neutral fragment ready for Phase 2 persistence mapping."""

    evidence_key: str
    fragment_role: str
    parent_key: str | None
    locator_type: str
    locator: dict[str, Any]
    normalized_text: str
    ordinal: int
    estimated_tokens: int
    is_citable: bool
    source_block_keys: tuple[str, ...]
    retrieval_text: str
    text_hash: str
    locator_hash: str
    retrieval_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "fragment_role": self.fragment_role,
            "parent_key": self.parent_key,
            "locator_type": self.locator_type,
            "locator": dict(self.locator),
            "normalized_text": self.normalized_text,
            "ordinal": self.ordinal,
            "estimated_tokens": self.estimated_tokens,
            "is_citable": self.is_citable,
            "source_block_keys": list(self.source_block_keys),
            "retrieval_text": self.retrieval_text,
            "text_hash": self.text_hash,
            "locator_hash": self.locator_hash,
            "retrieval_hash": self.retrieval_hash,
        }


@dataclass(frozen=True)
class EvidenceChunkBuildResult:
    contract_version: str
    profile_version: str
    token_estimator_version: str
    fragments: tuple[EvidenceChunkFragment, ...]
    metrics: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]
    result_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "profile_version": self.profile_version,
            "token_estimator_version": self.token_estimator_version,
            "fragments": [item.to_payload() for item in self.fragments],
            "metrics": dict(self.metrics),
            "warnings": [dict(item) for item in self.warnings],
            "result_hash": self.result_hash,
        }


@dataclass(frozen=True)
class _TokenSpan:
    start: int
    end: int


@dataclass(frozen=True)
class _SourcePiece:
    block: StructuredEvidenceBlock
    text: str
    char_start: int
    char_end: int
    estimated_tokens: int
    overlap_left_tokens: int = 0
    split_index: int = 0
    split_count: int = 1

    @property
    def standalone(self) -> bool:
        return (
            self.split_count > 1
            or self.block.block_type in ISOLATED_BLOCK_TYPES
        )


@dataclass
class _ChunkDraft:
    pieces: list[_SourcePiece] = field(default_factory=list)
    estimated_tokens: int = 0

    def append(self, piece: _SourcePiece) -> None:
        self.pieces.append(piece)
        self.estimated_tokens += piece.estimated_tokens


@dataclass(frozen=True)
class _SectionDraft:
    occurrence: int
    section_path: tuple[str, ...]
    blocks: tuple[StructuredEvidenceBlock, ...]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_evidence_text(value: str) -> str:
    """Normalize layout text without deleting business punctuation."""

    normalized = re.sub(r"\r\n?", "\n", str(value or ""))
    normalized = _SPACE_PATTERN.sub(" ", normalized)
    normalized = _BLANK_LINE_PATTERN.sub("\n\n", normalized)
    return normalized.strip()


def _token_spans(value: str) -> tuple[_TokenSpan, ...]:
    """Return conservative, deterministic pseudo-token spans.

    CJK characters and punctuation count as one token.  ASCII alpha-numeric
    runs count as one token per four characters.  This intentionally
    overestimates many provider tokenizers and therefore protects the hard
    chunk limit without loading an embedding or generation model tokenizer.
    """

    spans: list[_TokenSpan] = []
    for match in _TOKEN_PATTERN.finditer(value):
        token = match.group(0)
        if token.isascii() and token.isalnum():
            for start in range(match.start(), match.end(), 4):
                spans.append(_TokenSpan(start=start, end=min(start + 4, match.end())))
        else:
            spans.append(_TokenSpan(start=match.start(), end=match.end()))
    return tuple(spans)


def estimate_evidence_tokens(value: str) -> int:
    """Estimate tokens using the frozen PDF-C1 conservative profile."""

    return len(_token_spans(normalize_evidence_text(value)))


def _normalize_section_path(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = normalize_evidence_text(value)
        if normalized and (not result or result[-1] != normalized):
            result.append(normalized)
    return tuple(result)


def _validate_bbox(
    value: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if len(value) != 4:
        raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_BBOX_INVALID")
    normalized = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in normalized):
        raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_BBOX_INVALID")
    x1, y1, x2, y2 = normalized
    if x2 <= x1 or y2 <= y1:
        raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_BBOX_INVALID")
    return normalized


def _normalize_blocks(
    blocks: Sequence[StructuredEvidenceBlock],
) -> tuple[StructuredEvidenceBlock, ...]:
    normalized: list[StructuredEvidenceBlock] = []
    block_keys: set[str] = set()
    ordinals: set[int] = set()
    for row in blocks:
        block_key = str(row.block_key).strip()
        text = normalize_evidence_text(row.text)
        block_type = str(row.block_type).strip()
        if not block_key or block_key in block_keys:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_KEY_INVALID")
        if not text:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_TEXT_EMPTY")
        if block_type not in BLOCK_TYPES:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_TYPE_INVALID")
        if type(row.page_no) is not int or row.page_no < 1:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_PAGE_INVALID")
        if type(row.ordinal) is not int or row.ordinal < 0:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_ORDINAL_INVALID")
        if row.ordinal in ordinals:
            raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCK_ORDINAL_DUPLICATE")
        block_keys.add(block_key)
        ordinals.add(row.ordinal)
        normalized.append(
            StructuredEvidenceBlock(
                block_key=block_key,
                text=text,
                block_type=block_type,
                page_no=row.page_no,
                ordinal=row.ordinal,
                section_path=_normalize_section_path(row.section_path),
                bbox=_validate_bbox(row.bbox),
                boundary_before=bool(row.boundary_before),
                boundary_after=bool(row.boundary_after),
            )
        )
    return tuple(sorted(normalized, key=lambda item: (item.ordinal, item.block_key)))


def _partition_sections(
    blocks: Sequence[StructuredEvidenceBlock],
    *,
    profile: ChunkBuilderProfile,
) -> tuple[_SectionDraft, ...]:
    if not blocks:
        return ()
    sections: list[_SectionDraft] = []
    current_path: tuple[str, ...] | None = None
    current: list[StructuredEvidenceBlock] = []
    occurrence = 0
    for block in blocks:
        path = block.section_path
        if profile.parent_section_max_depth is not None:
            path = path[: profile.parent_section_max_depth]
        if current and path != current_path:
            sections.append(
                _SectionDraft(
                    occurrence=occurrence,
                    section_path=current_path or (),
                    blocks=tuple(current),
                )
            )
            occurrence += 1
            current = []
        current_path = path
        current.append(block)
    if current:
        sections.append(
            _SectionDraft(
                occurrence=occurrence,
                section_path=current_path or (),
                blocks=tuple(current),
            )
        )
    return tuple(sections)


def _is_boundary_after(text: str, spans: Sequence[_TokenSpan], index: int) -> bool:
    span = spans[index]
    if text[span.start : span.end] in _SENTENCE_ENDINGS:
        return True
    if index + 1 < len(spans):
        between = text[span.end : spans[index + 1].start]
        return "\n" in between
    return True


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _split_long_block(
    block: StructuredEvidenceBlock,
    *,
    profile: ChunkBuilderProfile,
) -> tuple[_SourcePiece, ...]:
    spans = _token_spans(block.text)
    if len(spans) <= profile.hard_max_tokens:
        return (
            _SourcePiece(
                block=block,
                text=block.text,
                char_start=0,
                char_end=len(block.text),
                estimated_tokens=len(spans),
            ),
        )

    slices: list[tuple[int, int, int, int, int]] = []
    start_token = 0
    while start_token < len(spans):
        hard_end = min(start_token + profile.hard_max_tokens, len(spans))
        target_end = min(start_token + profile.soft_max_tokens, hard_end)
        minimum_end = min(start_token + profile.soft_min_tokens, hard_end)
        before_target = [
            index + 1
            for index in range(minimum_end - 1, target_end)
            if _is_boundary_after(block.text, spans, index)
        ]
        after_target = [
            index + 1
            for index in range(target_end, hard_end)
            if _is_boundary_after(block.text, spans, index)
        ]
        end_token = (
            before_target[-1]
            if before_target
            else after_target[0]
            if after_target
            else hard_end
        )
        if end_token <= start_token:
            raise BidEvidenceChunkBuildError("BID_CHUNK_SPLIT_NO_PROGRESS")
        char_start, char_end = _trimmed_span(
            block.text,
            spans[start_token].start,
            spans[end_token - 1].end,
        )
        overlap_left = 0 if start_token == 0 else min(
            profile.long_overlap_tokens,
            end_token - start_token,
        )
        slices.append(
            (start_token, end_token, char_start, char_end, overlap_left)
        )
        if end_token >= len(spans):
            break
        next_start = max(0, end_token - profile.long_overlap_tokens)
        if next_start <= start_token:
            next_start = start_token + 1
        start_token = next_start

    pieces = tuple(
        _SourcePiece(
            block=block,
            text=block.text[char_start:char_end],
            char_start=char_start,
            char_end=char_end,
            estimated_tokens=end_token - start_token,
            overlap_left_tokens=overlap_left,
            split_index=index,
            split_count=len(slices),
        )
        for index, (
            start_token,
            end_token,
            char_start,
            char_end,
            overlap_left,
        ) in enumerate(slices)
    )
    if any(piece.estimated_tokens > profile.hard_max_tokens for piece in pieces):
        raise BidEvidenceChunkBuildError("BID_CHUNK_HARD_LIMIT_EXCEEDED")
    return pieces


def _section_title(section: _SectionDraft) -> str:
    if section.section_path:
        return section.section_path[-1]
    headings = [
        block.text for block in section.blocks if block.block_type == "heading"
    ]
    if headings:
        return headings[-1]
    return "文档正文"


def _section_key(
    section: _SectionDraft,
    *,
    profile: ChunkBuilderProfile,
) -> str:
    first = section.blocks[0]
    digest = _canonical_hash(
        {
            "contract_version": CHUNK_CONTRACT_VERSION,
            "profile_version": profile.profile_version,
            "occurrence": section.occurrence,
            "section_path": list(section.section_path),
            "first_block_key": first.block_key,
        }
    )
    return f"section:{digest}"


def _draft_section_chunks(
    section: _SectionDraft,
    *,
    profile: ChunkBuilderProfile,
) -> tuple[_ChunkDraft, ...]:
    chunks: list[_ChunkDraft] = []
    current = _ChunkDraft()

    def flush() -> None:
        nonlocal current
        if current.pieces:
            chunks.append(current)
            current = _ChunkDraft()

    def piece_is_standalone(piece: _SourcePiece) -> bool:
        if piece.split_count > 1:
            return True
        if (
            profile.aggregate_table_rows
            and piece.block.block_type == "table_row"
        ):
            return False
        return piece.block.block_type in ISOLATED_BLOCK_TYPES

    def current_contains_only_headings() -> bool:
        return bool(current.pieces) and all(
            piece.block.block_type == "heading" for piece in current.pieces
        )

    for block in section.blocks:
        if block.block_type == "heading" and not profile.include_heading_atoms:
            flush()
            continue
        pieces = _split_long_block(block, profile=profile)
        for piece in pieces:
            standalone = piece_is_standalone(piece)
            if block.block_type == "heading":
                if current.pieces and current.estimated_tokens >= profile.soft_min_tokens:
                    flush()
                if (
                    current.pieces
                    and current.estimated_tokens + piece.estimated_tokens
                    > profile.hard_max_tokens
                ):
                    flush()
                current.append(piece)
                continue

            keep_heading_prefix = (
                profile.include_heading_atoms and current_contains_only_headings()
            )
            if (block.boundary_before or standalone) and not keep_heading_prefix:
                flush()
            if standalone:
                if (
                    keep_heading_prefix
                    and current.estimated_tokens + piece.estimated_tokens
                    <= profile.hard_max_tokens
                ):
                    current.append(piece)
                    flush()
                    continue
                isolated = _ChunkDraft()
                isolated.append(piece)
                chunks.append(isolated)
                continue

            combined = current.estimated_tokens + piece.estimated_tokens
            if current.pieces and current.estimated_tokens >= profile.target_tokens:
                flush()
                combined = piece.estimated_tokens
            elif (
                current.pieces
                and combined > profile.soft_max_tokens
                and current.estimated_tokens >= profile.soft_min_tokens
            ):
                flush()
                combined = piece.estimated_tokens

            if current.pieces and combined > profile.hard_max_tokens:
                flush()
            current.append(piece)
            if block.boundary_after:
                flush()
    flush()
    return tuple(chunks)


def _context_prefix(
    *,
    document_label: str | None,
    section_path: tuple[str, ...],
    page_start: int,
    page_end: int,
    content_type: str,
) -> str:
    lines: list[str] = []
    normalized_label = normalize_evidence_text(document_label or "")
    if normalized_label:
        lines.append(f"[文档] {normalized_label}")
    lines.append(
        "[章节] "
        + (" > ".join(section_path) if section_path else "文档正文")
    )
    page_label = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
    lines.append(f"[页码] {page_label}")
    lines.append(f"[类型] {content_type}")
    return "\n".join(lines)


def _fragment(
    *,
    evidence_key: str,
    fragment_role: str,
    parent_key: str | None,
    locator_type: str,
    locator: dict[str, Any],
    normalized_text: str,
    ordinal: int,
    estimated_tokens: int,
    is_citable: bool,
    source_block_keys: tuple[str, ...],
    retrieval_text: str,
) -> EvidenceChunkFragment:
    if fragment_role not in FRAGMENT_ROLES:
        raise BidEvidenceChunkBuildError("BID_CHUNK_FRAGMENT_ROLE_INVALID")
    normalized = normalize_evidence_text(normalized_text)
    retrieval = normalize_evidence_text(retrieval_text)
    if not normalized or not retrieval:
        raise BidEvidenceChunkBuildError("BID_CHUNK_FRAGMENT_TEXT_EMPTY")
    return EvidenceChunkFragment(
        evidence_key=evidence_key,
        fragment_role=fragment_role,
        parent_key=parent_key,
        locator_type=locator_type,
        locator=dict(locator),
        normalized_text=normalized,
        ordinal=ordinal,
        estimated_tokens=estimated_tokens,
        is_citable=is_citable,
        source_block_keys=source_block_keys,
        retrieval_text=retrieval,
        text_hash=_text_hash(normalized),
        locator_hash=_canonical_hash(locator),
        retrieval_hash=_text_hash(retrieval),
    )


def _content_type(pieces: Sequence[_SourcePiece]) -> str:
    types = tuple(dict.fromkeys(piece.block.block_type for piece in pieces))
    return types[0] if len(types) == 1 else "mixed_body"


def _build_section_fragments(
    section: _SectionDraft,
    *,
    document_label: str | None,
    profile: ChunkBuilderProfile,
    starting_ordinal: int,
) -> tuple[list[EvidenceChunkFragment], int, list[dict[str, Any]]]:
    fragments: list[EvidenceChunkFragment] = []
    warnings: list[dict[str, Any]] = []
    ordinal = starting_ordinal
    parent_key = _section_key(section, profile=profile)
    page_start = min(block.page_no for block in section.blocks)
    page_end = max(block.page_no for block in section.blocks)
    heading_blocks = tuple(
        block for block in section.blocks if block.block_type == "heading"
    )
    parent_heading_blocks = heading_blocks
    if profile.parent_section_max_depth is not None and heading_blocks:
        parent_heading_blocks = tuple(
            block
            for block in heading_blocks
            if block.section_path == section.section_path
        ) or heading_blocks[:1]
    parent_title = _section_title(section)
    parent_source_keys = tuple(block.block_key for block in parent_heading_blocks)
    if not parent_source_keys:
        parent_source_keys = (section.blocks[0].block_key,)
    parent_locator: dict[str, Any] = {
        "schema_version": CHUNK_CONTRACT_VERSION,
        "fragment_role": "section_parent",
        "block_type": "heading",
        "section_path": list(section.section_path),
        "section_occurrence": section.occurrence,
        "page_no": page_start,
        "page_end": page_end,
        "source_block_ids": list(parent_source_keys),
        "token_count": estimate_evidence_tokens(parent_title),
        "is_citable": False,
    }
    parent_bbox = parent_heading_blocks[-1].bbox if parent_heading_blocks else None
    if parent_bbox is not None:
        parent_locator["bbox"] = list(parent_bbox)
    parent_prefix = _context_prefix(
        document_label=document_label,
        section_path=section.section_path,
        page_start=page_start,
        page_end=page_end,
        content_type="section_parent",
    )
    fragments.append(
        _fragment(
            evidence_key=parent_key,
            fragment_role="section_parent",
            parent_key=None,
            locator_type="page_bbox" if parent_bbox is not None else "section",
            locator=parent_locator,
            normalized_text=parent_title,
            ordinal=ordinal,
            estimated_tokens=estimate_evidence_tokens(parent_title),
            is_citable=False,
            source_block_keys=parent_source_keys,
            retrieval_text=f"{parent_prefix}\n\n{parent_title}",
        )
    )
    ordinal += 1

    drafts = _draft_section_chunks(section, profile=profile)
    if not drafts:
        warnings.append(
            {
                "code": "BID_CHUNK_SECTION_WITHOUT_BODY",
                "section_path": list(section.section_path),
                "page_no": page_start,
            }
        )
        return fragments, ordinal, warnings

    for child_index, draft in enumerate(drafts):
        child_text = "\n\n".join(piece.text for piece in draft.pieces)
        child_tokens = estimate_evidence_tokens(child_text)
        if child_tokens > profile.hard_max_tokens:
            raise BidEvidenceChunkBuildError("BID_CHUNK_HARD_LIMIT_EXCEEDED")
        child_page_start = min(piece.block.page_no for piece in draft.pieces)
        child_page_end = max(piece.block.page_no for piece in draft.pieces)
        source_descriptors = [
            {
                "block_key": piece.block.block_key,
                "char_start": piece.char_start,
                "char_end": piece.char_end,
                "split_index": piece.split_index,
            }
            for piece in draft.pieces
        ]
        child_digest = _canonical_hash(
            {
                "contract_version": CHUNK_CONTRACT_VERSION,
                "profile_version": profile.profile_version,
                "parent_key": parent_key,
                "child_index": child_index,
                "sources": source_descriptors,
            }
        )
        child_key = f"chunk:{child_digest}"
        child_type = _content_type(draft.pieces)
        context_prefix = _context_prefix(
            document_label=document_label,
            section_path=section.section_path,
            page_start=child_page_start,
            page_end=child_page_end,
            content_type=child_type,
        )
        child_source_keys = tuple(
            dict.fromkeys(piece.block.block_key for piece in draft.pieces)
        )
        child_locator = {
            "schema_version": CHUNK_CONTRACT_VERSION,
            "fragment_role": "retrieval_child",
            "block_type": child_type,
            "section_path": list(section.section_path),
            "page_no": child_page_start,
            "page_end": child_page_end,
            "source_block_ids": list(child_source_keys),
            "source_spans": source_descriptors,
            "token_count": child_tokens,
            "source_atom_count": len(draft.pieces),
            "context_prefix": context_prefix,
            "is_citable": False,
        }
        fragments.append(
            _fragment(
                evidence_key=child_key,
                fragment_role="retrieval_child",
                parent_key=parent_key,
                locator_type="section",
                locator=child_locator,
                normalized_text=child_text,
                ordinal=ordinal,
                estimated_tokens=child_tokens,
                is_citable=False,
                source_block_keys=child_source_keys,
                retrieval_text=f"{context_prefix}\n\n{child_text}",
            )
        )
        ordinal += 1
        structurally_isolated = (
            all(piece.block.block_type == "heading" for piece in draft.pieces)
            or (
                len(draft.pieces) == 1
                and (
                    draft.pieces[0].split_count > 1
                    or draft.pieces[0].block.block_type
                    in ISOLATED_BLOCK_TYPES
                )
            )
        )
        if child_tokens < profile.soft_min_tokens and not structurally_isolated:
            warnings.append(
                {
                    "code": "BID_CHUNK_CHILD_BELOW_SOFT_MIN",
                    "evidence_key": child_key,
                    "token_count": child_tokens,
                }
            )

        for piece in draft.pieces:
            atom_digest = _canonical_hash(
                {
                    "contract_version": CHUNK_CONTRACT_VERSION,
                    "profile_version": profile.profile_version,
                    "block_key": piece.block.block_key,
                    "char_start": piece.char_start,
                    "char_end": piece.char_end,
                    "text_hash": _text_hash(piece.text),
                }
            )
            atom_key = f"atom:{atom_digest}"
            atom_locator: dict[str, Any] = {
                "schema_version": CHUNK_CONTRACT_VERSION,
                "fragment_role": "evidence_atom",
                "block_type": piece.block.block_type,
                "section_path": list(
                    piece.block.section_path
                    if profile.include_heading_atoms
                    else section.section_path
                ),
                "page_no": piece.block.page_no,
                "page_end": piece.block.page_no,
                "source_block_ids": [piece.block.block_key],
                "char_start": piece.char_start,
                "char_end": piece.char_end,
                "split_index": piece.split_index,
                "split_count": piece.split_count,
                "overlap_left_tokens": piece.overlap_left_tokens,
                "token_count": piece.estimated_tokens,
                "source_normalized_text_hash": _text_hash(piece.block.text),
                "is_citable": True,
            }
            if piece.block.bbox is not None:
                atom_locator["bbox"] = list(piece.block.bbox)
            atom_prefix = _context_prefix(
                document_label=document_label,
                section_path=(
                    piece.block.section_path
                    if profile.include_heading_atoms
                    else section.section_path
                ),
                page_start=piece.block.page_no,
                page_end=piece.block.page_no,
                content_type=piece.block.block_type,
            )
            fragments.append(
                _fragment(
                    evidence_key=atom_key,
                    fragment_role="evidence_atom",
                    parent_key=child_key,
                    locator_type=(
                        "page_bbox" if piece.block.bbox is not None else "section"
                    ),
                    locator=atom_locator,
                    normalized_text=piece.text,
                    ordinal=ordinal,
                    estimated_tokens=piece.estimated_tokens,
                    is_citable=True,
                    source_block_keys=(piece.block.block_key,),
                    retrieval_text=f"{atom_prefix}\n\n{piece.text}",
                )
            )
            ordinal += 1
    return fragments, ordinal, warnings


def _validate_hierarchy(fragments: Sequence[EvidenceChunkFragment]) -> None:
    by_key = {item.evidence_key: item for item in fragments}
    if len(by_key) != len(fragments):
        raise BidEvidenceChunkBuildError("BID_CHUNK_FRAGMENT_KEY_DUPLICATE")
    for item in fragments:
        if item.fragment_role == "section_parent":
            if item.parent_key is not None or item.is_citable:
                raise BidEvidenceChunkBuildError(
                    "BID_CHUNK_SECTION_PARENT_INVALID"
                )
            continue
        parent = by_key.get(str(item.parent_key))
        if parent is None:
            raise BidEvidenceChunkBuildError("BID_CHUNK_PARENT_MISSING")
        if item.fragment_role == "retrieval_child":
            if parent.fragment_role != "section_parent" or item.is_citable:
                raise BidEvidenceChunkBuildError(
                    "BID_CHUNK_RETRIEVAL_CHILD_INVALID"
                )
        elif item.fragment_role == "evidence_atom":
            if parent.fragment_role != "retrieval_child" or not item.is_citable:
                raise BidEvidenceChunkBuildError("BID_CHUNK_EVIDENCE_ATOM_INVALID")


def build_evidence_chunks(
    blocks: Sequence[StructuredEvidenceBlock],
    *,
    document_label: str | None = None,
    profile: ChunkBuilderProfile = DEFAULT_CHUNK_PROFILE,
) -> EvidenceChunkBuildResult:
    """Build deterministic Parent/Child/Atom fragments from structured blocks."""

    normalized_blocks = _normalize_blocks(blocks)
    if not normalized_blocks:
        raise BidEvidenceChunkBuildError("BID_CHUNK_BLOCKS_EMPTY")
    sections = _partition_sections(normalized_blocks, profile=profile)
    fragments: list[EvidenceChunkFragment] = []
    warnings: list[dict[str, Any]] = []
    ordinal = 0
    for section in sections:
        section_fragments, ordinal, section_warnings = _build_section_fragments(
            section,
            document_label=document_label,
            profile=profile,
            starting_ordinal=ordinal,
        )
        fragments.extend(section_fragments)
        warnings.extend(section_warnings)
    _validate_hierarchy(fragments)

    parents = [row for row in fragments if row.fragment_role == "section_parent"]
    children = [row for row in fragments if row.fragment_role == "retrieval_child"]
    atoms = [row for row in fragments if row.fragment_role == "evidence_atom"]
    overlap_tokens = sum(
        int(row.locator.get("overlap_left_tokens") or 0) for row in atoms
    )
    source_tokens = sum(
        estimate_evidence_tokens(row.text)
        for row in normalized_blocks
        if profile.include_heading_atoms or row.block_type != "heading"
    )
    metrics = {
        "input_block_count": len(normalized_blocks),
        "heading_block_count": sum(
            row.block_type == "heading" for row in normalized_blocks
        ),
        "section_parent_count": len(parents),
        "retrieval_child_count": len(children),
        "evidence_atom_count": len(atoms),
        "source_estimated_tokens": source_tokens,
        "overlap_estimated_tokens": overlap_tokens,
        "overlap_ratio": round(overlap_tokens / max(source_tokens, 1), 6),
        "max_child_tokens": max(
            (row.estimated_tokens for row in children), default=0
        ),
        "children_below_soft_min": sum(
            row.estimated_tokens < profile.soft_min_tokens for row in children
        ),
        "long_split_atom_count": sum(
            int(row.locator.get("split_count") or 1) > 1 for row in atoms
        ),
    }
    if profile.include_heading_atoms:
        metrics.update(
            {
                "citable_heading_atom_count": sum(
                    row.fragment_role == "evidence_atom"
                    and row.locator.get("block_type") == "heading"
                    for row in fragments
                ),
                "aggregated_table_row_child_count": sum(
                    row.fragment_role == "retrieval_child"
                    and row.locator.get("block_type") == "table_row"
                    and int(row.locator.get("source_atom_count") or 0) > 1
                    for row in fragments
                ),
                "parent_section_max_depth": profile.parent_section_max_depth,
            }
        )
    result_body = {
        "contract_version": CHUNK_CONTRACT_VERSION,
        "profile_version": profile.profile_version,
        "token_estimator_version": profile.token_estimator_version,
        "fragments": [item.to_payload() for item in fragments],
        "metrics": metrics,
        "warnings": warnings,
    }
    return EvidenceChunkBuildResult(
        contract_version=CHUNK_CONTRACT_VERSION,
        profile_version=profile.profile_version,
        token_estimator_version=profile.token_estimator_version,
        fragments=tuple(fragments),
        metrics=metrics,
        warnings=tuple(warnings),
        result_hash=_canonical_hash(result_body),
    )


__all__ = [
    "BLOCK_TYPES",
    "CHUNK_CONTRACT_VERSION",
    "CHUNK_PROFILE_VERSION",
    "DEFAULT_CHUNK_PROFILE",
    "RQ1A_CHUNK_PROFILE",
    "RQ1A_CHUNK_PROFILE_VERSION",
    "FRAGMENT_ROLES",
    "TOKEN_ESTIMATOR_VERSION",
    "BidEvidenceChunkBuildError",
    "ChunkBuilderProfile",
    "EvidenceChunkBuildResult",
    "EvidenceChunkFragment",
    "StructuredEvidenceBlock",
    "build_evidence_chunks",
    "estimate_evidence_tokens",
    "normalize_evidence_text",
]
