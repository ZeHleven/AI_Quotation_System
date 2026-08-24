"""Deterministic native-layout PDF parsing for bid evidence (PDF-C2).

The parser reads only the PDF's native text and vector table layer.  It does
not render pages, invoke OCR/vision/models, infer bid lots, or persist data.
Its output is an ordered sequence of ``StructuredEvidenceBlock`` objects that
is consumed by the single PDF-C1 chunk builder.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Iterable, Sequence

import pdfplumber

from app.services.bid_evidence_chunk_builder import (
    StructuredEvidenceBlock,
    normalize_evidence_text,
)


PDF_NATIVE_LAYOUT_CONTRACT_VERSION = "bid.pdf.native-layout.v1"
PDF_NATIVE_LAYOUT_PROFILE_VERSION = "bid-pdf-native-layout-profile-v1"
PDF_C2_PARSER_PROFILE_VERSION = "bid-document-parser-profile-v2-pdf-native-layout"
PDF_RQ1A_NATIVE_LAYOUT_PROFILE_VERSION = "bid-pdf-native-layout-profile-v2-rq1a"
PDF_RQ1A_PARSER_PROFILE_VERSION = (
    "bid-document-parser-profile-v3-pdf-structure-rq1a"
)

_CJK_CHAR = re.compile(r"[\u3400-\u9fff]")
_BULLET_PREFIX = re.compile(r"^\s*(?:[•●○◆▪■□▶►·]|[-*])\s*")
_CHINESE_HEADING = re.compile(
    r"^\s*第\s*[一二三四五六七八九十百零〇两\d]+\s*(章|节|篇|部分)"
)
_CHINESE_ITEM = re.compile(
    r"^\s*(?:[一二三四五六七八九十百零〇两]+、|[（(][一二三四五六七八九十百零〇两\d]+[)）])"
)
_NUMBERED_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+){0,5})[.、)）\s]+")
_CLAUSE_PREFIX = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零〇两\d]+\s*条|\d+(?:\.\d+){1,5})(?:\s|[、.．:：])"
)
_NO_SPACE_BEFORE = frozenset(",.!?;:%)]}，。！？；：％）】》」』、")
_NO_SPACE_AFTER = frozenset("([{（【《「『")


class BidPdfNativeLayoutError(RuntimeError):
    code = "BID_PDF_NATIVE_LAYOUT_FAILED"


class BidPdfNativeLayoutInvalid(BidPdfNativeLayoutError):
    code = "BID_FILE_CONTENT_INVALID"


class BidPdfNativeLayoutLimitExceeded(BidPdfNativeLayoutError):
    code = "BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class PdfNativeLayoutProfile:
    profile_version: str = PDF_NATIVE_LAYOUT_PROFILE_VERSION
    max_pages: int = 2_000
    max_words_per_page: int = 50_000
    max_blocks: int = 200_000
    max_native_chars: int = 1_200_000
    x_tolerance: float = 2.0
    y_tolerance: float = 3.0
    line_merge_tolerance: float = 3.0
    paragraph_gap_ratio: float = 1.8
    heading_size_ratio: float = 1.22
    min_native_chars_per_page: int = 20
    min_text_coverage_ratio: float = 0.65
    suppress_repeated_margin_artifacts: bool = False
    margin_zone_ratio: float = 0.1
    margin_repeat_min_pages: int = 3
    margin_repeat_min_ratio: float = 0.15
    margin_artifact_max_chars: int = 160
    preserve_first_margin_occurrence: bool = True

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_pages,
            self.max_words_per_page,
            self.max_blocks,
            self.max_native_chars,
            self.min_native_chars_per_page,
            self.margin_repeat_min_pages,
            self.margin_artifact_max_chars,
        )
        if not all(type(value) is int and value > 0 for value in integer_limits):
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PROFILE_INVALID")
        if not str(self.profile_version).strip():
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PROFILE_INVALID")
        if not (
            self.x_tolerance > 0
            and self.y_tolerance > 0
            and self.line_merge_tolerance > 0
            and self.paragraph_gap_ratio >= 1
            and self.heading_size_ratio > 1
            and 0 < self.min_text_coverage_ratio <= 1
            and 0 < self.margin_zone_ratio < 0.25
            and 0 < self.margin_repeat_min_ratio <= 1
        ):
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PROFILE_INVALID")
        if (
            type(self.suppress_repeated_margin_artifacts) is not bool
            or type(self.preserve_first_margin_occurrence) is not bool
        ):
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PROFILE_INVALID")


DEFAULT_PDF_NATIVE_LAYOUT_PROFILE = PdfNativeLayoutProfile()
RQ1A_PDF_NATIVE_LAYOUT_PROFILE = PdfNativeLayoutProfile(
    profile_version=PDF_RQ1A_NATIVE_LAYOUT_PROFILE_VERSION,
    suppress_repeated_margin_artifacts=True,
)


@dataclass(frozen=True)
class PdfNativePageLayout:
    page_no: int
    width: float
    height: float
    rotation: int
    status: str
    content_source: str
    ocr_status: str
    native_char_count: int
    extracted_char_count: int
    word_count: int
    block_count: int
    table_count: int
    image_count: int
    text_coverage_ratio: float
    reading_order_mode: str
    warnings: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "status": self.status,
            "content_source": self.content_source,
            "ocr_status": self.ocr_status,
            "native_char_count": self.native_char_count,
            "extracted_char_count": self.extracted_char_count,
            "word_count": self.word_count,
            "block_count": self.block_count,
            "table_count": self.table_count,
            "image_count": self.image_count,
            "text_coverage_ratio": self.text_coverage_ratio,
            "reading_order_mode": self.reading_order_mode,
            "warnings": [dict(item) for item in self.warnings],
        }


@dataclass(frozen=True)
class PdfNativeLayoutResult:
    contract_version: str
    profile_version: str
    pages: tuple[PdfNativePageLayout, ...]
    blocks: tuple[StructuredEvidenceBlock, ...]
    warnings: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    result_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "profile_version": self.profile_version,
            "pages": [page.to_payload() for page in self.pages],
            "blocks": [_block_payload(block) for block in self.blocks],
            "warnings": [dict(item) for item in self.warnings],
            "metrics": dict(self.metrics),
            "result_hash": self.result_hash,
        }


@dataclass(frozen=True)
class _Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool


@dataclass(frozen=True)
class _Line:
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float
    bold_ratio: float


@dataclass(frozen=True)
class _RawBlock:
    page_no: int
    text: str
    block_type: str
    bbox: tuple[float, float, float, float]
    heading_level: int | None = None
    boundary_before: bool = False
    boundary_after: bool = False


@dataclass
class _ParagraphDraft:
    lines: list[_Line] = field(default_factory=list)

    def append(self, line: _Line) -> None:
        self.lines.append(line)


@dataclass(frozen=True)
class _MarginArtifactSuppression:
    blocks: tuple[_RawBlock, ...]
    suppressed_count: int = 0
    suppressed_char_count: int = 0
    signature_count: int = 0
    affected_pages: tuple[int, ...] = ()
    signature_hashes: tuple[str, ...] = ()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rounded(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_COORDINATE_INVALID")
    return round(number, 3)


def _bbox_payload(value: Sequence[float]) -> list[float]:
    return [_rounded(item) for item in value]


def _warning(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "code": re.sub(r"[^A-Z0-9_]", "_", code.upper())[:80],
        "message": str(message)[:500],
        "details": details,
    }


def _block_payload(block: StructuredEvidenceBlock) -> dict[str, Any]:
    return {
        "block_key": block.block_key,
        "text": block.text,
        "block_type": block.block_type,
        "page_no": block.page_no,
        "ordinal": block.ordinal,
        "section_path": list(block.section_path),
        "bbox": _bbox_payload(block.bbox) if block.bbox is not None else None,
        "boundary_before": block.boundary_before,
        "boundary_after": block.boundary_after,
    }


def _visible_char_count(value: str) -> int:
    return sum(not char.isspace() for char in str(value or ""))


def _normalize_rotation(value: Any) -> int:
    try:
        rotation = int(value or 0) % 360
    except (TypeError, ValueError):
        return 0
    return rotation if rotation in {0, 90, 180, 270} else 0


def _word_from_mapping(row: dict[str, Any], *, width: float, height: float) -> _Word | None:
    text = normalize_evidence_text(str(row.get("text") or ""))
    if not text:
        return None
    try:
        x0 = max(0.0, min(width, _rounded(row["x0"])))
        x1 = max(0.0, min(width, _rounded(row["x1"])))
        top = max(0.0, min(height, _rounded(row["top"])))
        bottom = max(0.0, min(height, _rounded(row["bottom"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_WORD_INVALID") from exc
    if x1 <= x0 or bottom <= top:
        return None
    try:
        size = float(row.get("size") or bottom - top)
    except (TypeError, ValueError):
        size = bottom - top
    if not math.isfinite(size) or size <= 0:
        size = bottom - top
    font_name = str(row.get("fontname") or "").lower()
    return _Word(
        text=text,
        x0=x0,
        top=top,
        x1=x1,
        bottom=bottom,
        size=round(size, 3),
        bold=("bold" in font_name or "black" in font_name or "heavy" in font_name),
    )


def _join_word_text(words: Sequence[_Word]) -> str:
    parts: list[str] = []
    for word in words:
        text = word.text
        if not parts:
            parts.append(text)
            continue
        previous = parts[-1][-1:]
        current = text[:1]
        needs_space = not (
            (previous and current and _CJK_CHAR.match(previous) and _CJK_CHAR.match(current))
            or current in _NO_SPACE_BEFORE
            or previous in _NO_SPACE_AFTER
        )
        parts.append((" " if needs_space else "") + text)
    return normalize_evidence_text("".join(parts))


def _group_words_into_lines(
    words: Sequence[_Word],
    *,
    tolerance: float,
) -> list[_Line]:
    if not words:
        return []
    ordered = sorted(words, key=lambda item: (item.top, item.x0, item.bottom, item.x1))
    groups: list[list[_Word]] = []
    group_tops: list[float] = []
    for word in ordered:
        selected: int | None = None
        selected_delta: float | None = None
        for index in range(max(0, len(groups) - 4), len(groups)):
            delta = abs(group_tops[index] - word.top)
            line_tolerance = max(tolerance, word.size * 0.28)
            if delta <= line_tolerance and (
                selected_delta is None or delta < selected_delta
            ):
                selected = index
                selected_delta = delta
        if selected is None:
            groups.append([word])
            group_tops.append(word.top)
        else:
            groups[selected].append(word)
            group_tops[selected] = statistics.median(
                item.top for item in groups[selected]
            )

    lines: list[_Line] = []
    for group in groups:
        row = sorted(group, key=lambda item: (item.x0, item.top, item.text))
        row_font_size = statistics.median(item.size for item in row)
        horizontal_gap_limit = max(48.0, row_font_size * 6.0)
        segments: list[list[_Word]] = []
        for word in row:
            if (
                segments
                and word.x0 - segments[-1][-1].x1 > horizontal_gap_limit
            ):
                segments.append([])
            if not segments:
                segments.append([])
            segments[-1].append(word)
        for segment in segments:
            text = _join_word_text(segment)
            if not text:
                continue
            lines.append(
                _Line(
                    text=text,
                    bbox=(
                        min(item.x0 for item in segment),
                        min(item.top for item in segment),
                        max(item.x1 for item in segment),
                        max(item.bottom for item in segment),
                    ),
                    font_size=round(
                        statistics.median(item.size for item in segment), 3
                    ),
                    bold_ratio=round(
                        sum(item.bold for item in segment) / len(segment), 4
                    ),
                )
            )
    return sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0], item.text))


def _bbox_contains_center(
    bbox: Sequence[float],
    *,
    x0: float,
    top: float,
    x1: float,
    bottom: float,
) -> bool:
    center_x = (x0 + x1) / 2
    center_y = (top + bottom) / 2
    return bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]


def _table_rows(
    page: Any,
    *,
    page_no: int,
    width: float,
    height: float,
    profile: PdfNativeLayoutProfile,
) -> tuple[list[_RawBlock], list[tuple[float, float, float, float]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        tables = list(
            page.find_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "intersection_tolerance": 3,
                    "text_x_tolerance": profile.x_tolerance,
                    "text_y_tolerance": profile.y_tolerance,
                }
            )
        )
    except Exception:
        return [], [], [
            _warning(
                "PDF_TABLE_DETECTION_FAILED",
                "原生矢量表格检测失败，已保留页面普通文本路径",
                page_no=page_no,
            )
        ]

    blocks: list[_RawBlock] = []
    accepted_bboxes: list[tuple[float, float, float, float]] = []
    for table_index, table in enumerate(
        sorted(tables, key=lambda item: (item.bbox[1], item.bbox[0]))
    ):
        try:
            extracted_rows = list(
                table.extract(
                    x_tolerance=profile.x_tolerance,
                    y_tolerance=profile.y_tolerance,
                )
                or []
            )
            bbox = tuple(_rounded(item) for item in table.bbox)
        except Exception:
            warnings.append(
                _warning(
                    "PDF_TABLE_EXTRACTION_FAILED",
                    "检测到的表格无法稳定抽取，已回退到普通文本",
                    page_no=page_no,
                    table_index=table_index,
                )
            )
            continue
        if (
            len(bbox) != 4
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
            or bbox[0] < 0
            or bbox[1] < 0
            or bbox[2] > width + 1
            or bbox[3] > height + 1
        ):
            continue
        normalized_rows: list[tuple[int, list[str]]] = []
        for row_index, row in enumerate(extracted_rows):
            cells = [normalize_evidence_text(str(cell or "")) for cell in row]
            if any(cells):
                normalized_rows.append((row_index, cells))
        max_columns = max((len(row) for _, row in normalized_rows), default=0)
        if not normalized_rows or max_columns < 2:
            continue
        accepted_bboxes.append(bbox)
        table_row_objects = list(getattr(table, "rows", []) or [])
        for emitted_index, (row_index, cells) in enumerate(normalized_rows):
            row_text = " | ".join(cells)
            row_bbox = bbox
            if row_index < len(table_row_objects):
                candidate = getattr(table_row_objects[row_index], "bbox", None)
                if candidate and len(candidate) == 4:
                    row_bbox = tuple(_rounded(item) for item in candidate)
            blocks.append(
                _RawBlock(
                    page_no=page_no,
                    text=row_text,
                    block_type="table_row",
                    bbox=row_bbox,
                    boundary_before=emitted_index == 0,
                    boundary_after=emitted_index == len(normalized_rows) - 1,
                )
            )
    return blocks, accepted_bboxes, warnings


def _order_line_region(
    lines: Sequence[_Line],
    *,
    page_width: float,
) -> tuple[list[_Line], str]:
    ordered = sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0], item.text))
    if len(ordered) < 6:
        return ordered, "single_column"
    center = page_width / 2
    narrow = [
        item
        for item in ordered
        if item.bbox[2] - item.bbox[0] <= page_width * 0.55
    ]
    left = [
        item
        for item in narrow
        if (item.bbox[0] + item.bbox[2]) / 2 < center
    ]
    right = [
        item
        for item in narrow
        if (item.bbox[0] + item.bbox[2]) / 2 >= center
    ]
    ambiguous = [
        item
        for item in ordered
        if item not in left and item not in right
    ]
    if len(left) >= 3 and len(right) >= 3 and len(ambiguous) <= max(1, len(ordered) // 10):
        result: list[_Line] = []
        remaining_left = list(left)
        remaining_right = list(right)
        for separator in sorted(
            ambiguous,
            key=lambda item: (item.bbox[1], item.bbox[0]),
        ):
            before_left = [
                item for item in remaining_left if item.bbox[1] < separator.bbox[1]
            ]
            before_right = [
                item for item in remaining_right if item.bbox[1] < separator.bbox[1]
            ]
            result.extend(
                sorted(before_left, key=lambda item: (item.bbox[1], item.bbox[0]))
            )
            result.extend(
                sorted(before_right, key=lambda item: (item.bbox[1], item.bbox[0]))
            )
            result.append(separator)
            remaining_left = [item for item in remaining_left if item not in before_left]
            remaining_right = [item for item in remaining_right if item not in before_right]
        result.extend(
            sorted(remaining_left, key=lambda item: (item.bbox[1], item.bbox[0]))
        )
        result.extend(
            sorted(remaining_right, key=lambda item: (item.bbox[1], item.bbox[0]))
        )
        return result, "two_column"
    return ordered, "single_column"


def _order_lines_with_table_bands(
    lines: Sequence[_Line],
    table_blocks: Sequence[_RawBlock],
    *,
    page_width: float,
) -> tuple[list[_Line | _RawBlock], str]:
    if not table_blocks:
        ordered, mode = _order_line_region(lines, page_width=page_width)
        return list(ordered), mode
    rows_by_band: list[list[_RawBlock]] = []
    for block in sorted(table_blocks, key=lambda item: (item.bbox[1], item.bbox[0])):
        if not rows_by_band or block.bbox[1] > max(
            item.bbox[3] for item in rows_by_band[-1]
        ) + 1:
            rows_by_band.append([block])
        else:
            rows_by_band[-1].append(block)
    remaining = list(lines)
    result: list[_Line | _RawBlock] = []
    modes: set[str] = set()
    cursor = 0.0
    for band in rows_by_band:
        band_top = min(item.bbox[1] for item in band)
        band_bottom = max(item.bbox[3] for item in band)
        region = [item for item in remaining if cursor <= item.bbox[1] < band_top]
        remaining = [item for item in remaining if item not in region]
        ordered, mode = _order_line_region(region, page_width=page_width)
        modes.add(mode)
        result.extend(ordered)
        result.extend(sorted(band, key=lambda item: (item.bbox[1], item.bbox[0])))
        cursor = max(cursor, band_bottom)
    tail = [item for item in remaining if item.bbox[1] >= cursor]
    before_cursor = [item for item in remaining if item.bbox[1] < cursor]
    ordered_before, mode_before = _order_line_region(
        before_cursor,
        page_width=page_width,
    )
    ordered_tail, mode_tail = _order_line_region(tail, page_width=page_width)
    modes.update({mode_before, mode_tail})
    result.extend(ordered_before)
    result.extend(ordered_tail)
    return result, "two_column" if "two_column" in modes else "single_column"


def _heading_level(text: str, *, font_size: float, body_size: float) -> int:
    chinese = _CHINESE_HEADING.match(text)
    if chinese:
        return 1 if chinese.group(1) in {"章", "篇", "部分"} else 2
    numbered = _NUMBERED_PREFIX.match(text)
    if numbered:
        return min(6, numbered.group(1).count(".") + 1)
    if _CHINESE_ITEM.match(text):
        return 2
    ratio = font_size / max(body_size, 1)
    if ratio >= 1.75:
        return 1
    if ratio >= 1.45:
        return 2
    return 3


def _line_kind(
    line: _Line,
    *,
    page_width: float,
    body_size: float,
    profile: PdfNativeLayoutProfile,
) -> tuple[str, int | None]:
    text = line.text
    width = line.bbox[2] - line.bbox[0]
    centered = abs(((line.bbox[0] + line.bbox[2]) / 2) - page_width / 2) <= page_width * 0.08
    short = len(text) <= 120
    numbered_heading = bool(
        _CHINESE_HEADING.match(text)
        or (_NUMBERED_PREFIX.match(text) and len(text) <= 80)
        or (_CHINESE_ITEM.match(text) and len(text) <= 60)
    )
    typography_heading = short and (
        line.font_size >= body_size * profile.heading_size_ratio
        or (line.bold_ratio >= 0.6 and line.font_size >= body_size * 1.08)
        or (centered and width <= page_width * 0.7 and line.font_size >= body_size * 1.12)
    )
    structural_heading = short and not text.rstrip().endswith(
        tuple("。！？!?；;：:")
    ) and (
        bool(_CHINESE_HEADING.match(text))
        or (
            numbered_heading
            and len(text) <= 60
            and (line.font_size >= body_size or line.bold_ratio >= 0.25)
        )
    )
    if structural_heading or (numbered_heading and typography_heading):
        return "heading", _heading_level(
            text,
            font_size=line.font_size,
            body_size=body_size,
        )
    if typography_heading and len(text) <= 60:
        return "heading", _heading_level(
            text,
            font_size=line.font_size,
            body_size=body_size,
        )
    if _CLAUSE_PREFIX.match(text):
        return "clause", None
    if _BULLET_PREFIX.match(text) or _CHINESE_ITEM.match(text) or _NUMBERED_PREFIX.match(text):
        return "list_item", None
    return "paragraph", None


def _paragraph_can_append(
    draft: _ParagraphDraft,
    line: _Line,
    *,
    page_width: float,
    profile: PdfNativeLayoutProfile,
) -> bool:
    if not draft.lines:
        return True
    previous = draft.lines[-1]
    previous_height = max(1.0, previous.bbox[3] - previous.bbox[1])
    gap = line.bbox[1] - previous.bbox[3]
    indent_delta = abs(line.bbox[0] - previous.bbox[0])
    return (
        gap <= max(profile.y_tolerance, previous_height * profile.paragraph_gap_ratio)
        and indent_delta <= max(18.0, page_width * 0.035)
        and abs(line.font_size - previous.font_size) <= max(2.0, previous.font_size * 0.2)
    )


def _paragraph_block(page_no: int, draft: _ParagraphDraft) -> _RawBlock:
    return _RawBlock(
        page_no=page_no,
        text="\n".join(line.text for line in draft.lines),
        block_type="paragraph",
        bbox=(
            min(line.bbox[0] for line in draft.lines),
            min(line.bbox[1] for line in draft.lines),
            max(line.bbox[2] for line in draft.lines),
            max(line.bbox[3] for line in draft.lines),
        ),
    )


def _page_raw_blocks(
    page: Any,
    *,
    page_no: int,
    profile: PdfNativeLayoutProfile,
) -> tuple[list[_RawBlock], dict[str, Any], list[dict[str, Any]]]:
    width = _rounded(page.width)
    height = _rounded(page.height)
    if width <= 0 or height <= 0:
        raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PAGE_DIMENSION_INVALID")
    try:
        raw_words = list(
            page.extract_words(
                x_tolerance=profile.x_tolerance,
                y_tolerance=profile.y_tolerance,
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["fontname", "size"],
            )
            or []
        )
    except Exception as exc:
        raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_WORD_EXTRACTION_FAILED") from exc
    if len(raw_words) > profile.max_words_per_page:
        raise BidPdfNativeLayoutLimitExceeded("BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED")
    words = [
        value
        for row in raw_words
        if (value := _word_from_mapping(row, width=width, height=height)) is not None
    ]
    native_char_count = sum(
        _visible_char_count(str(row.get("text") or ""))
        for row in list(getattr(page, "chars", []) or [])
    )
    table_blocks, table_bboxes, warnings = _table_rows(
        page,
        page_no=page_no,
        width=width,
        height=height,
        profile=profile,
    )
    body_words = [
        word
        for word in words
        if not any(
            _bbox_contains_center(
                bbox,
                x0=word.x0,
                top=word.top,
                x1=word.x1,
                bottom=word.bottom,
            )
            for bbox in table_bboxes
        )
    ]
    lines = _group_words_into_lines(
        body_words,
        tolerance=profile.line_merge_tolerance,
    )
    body_sizes = [word.size for word in body_words if word.size > 0]
    body_size = statistics.median(body_sizes) if body_sizes else 10.0
    ordered_items, reading_order_mode = _order_lines_with_table_bands(
        lines,
        table_blocks,
        page_width=width,
    )

    blocks: list[_RawBlock] = []
    paragraph = _ParagraphDraft()

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph.lines:
            blocks.append(_paragraph_block(page_no, paragraph))
            paragraph = _ParagraphDraft()

    for item in ordered_items:
        if isinstance(item, _RawBlock):
            flush_paragraph()
            blocks.append(item)
            continue
        block_type, level = _line_kind(
            item,
            page_width=width,
            body_size=body_size,
            profile=profile,
        )
        if block_type == "paragraph":
            if not _paragraph_can_append(
                paragraph,
                item,
                page_width=width,
                profile=profile,
            ):
                flush_paragraph()
            paragraph.append(item)
            continue
        flush_paragraph()
        blocks.append(
            _RawBlock(
                page_no=page_no,
                text=item.text,
                block_type=block_type,
                bbox=item.bbox,
                heading_level=level,
                boundary_before=block_type == "heading",
                boundary_after=block_type == "heading",
            )
        )
    flush_paragraph()

    extracted_char_count = sum(_visible_char_count(block.text) for block in blocks)
    coverage = round(
        min(1.0, extracted_char_count / max(native_char_count, 1)),
        6,
    )
    image_count = len(list(getattr(page, "images", []) or []))
    if not blocks:
        warnings.append(
            _warning(
                "PDF_PAGE_NO_NATIVE_TEXT",
                "页面未提取到可引用的原生文本，需要 OCR 或人工复核",
                page_no=page_no,
                image_count=image_count,
            )
        )
    elif native_char_count < profile.min_native_chars_per_page and image_count > 0:
        warnings.append(
            _warning(
                "PDF_PAGE_NATIVE_TEXT_INSUFFICIENT",
                "页面原生文本过少且包含图像，需要 OCR 或人工复核",
                page_no=page_no,
                native_char_count=native_char_count,
                image_count=image_count,
            )
        )
    elif coverage < profile.min_text_coverage_ratio:
        warnings.append(
            _warning(
                "PDF_PAGE_TEXT_COVERAGE_LOW",
                "页面原生字符到结构块的覆盖率低于冻结阈值",
                page_no=page_no,
                coverage_ratio=coverage,
            )
        )
    metrics = {
        "width": width,
        "height": height,
        "rotation": _normalize_rotation(getattr(page, "rotation", 0)),
        "native_char_count": native_char_count,
        "extracted_char_count": extracted_char_count,
        "word_count": len(words),
        "block_count": len(blocks),
        "table_count": len(table_bboxes),
        "image_count": image_count,
        "text_coverage_ratio": coverage,
        "reading_order_mode": reading_order_mode,
    }
    return blocks, metrics, warnings


def _margin_artifact_signature(
    block: _RawBlock,
    *,
    page_height: float,
    profile: PdfNativeLayoutProfile,
) -> str | None:
    if block.block_type in {"table", "table_row", "form_field", "image"}:
        return None
    text = normalize_evidence_text(block.text)
    if not text or len(text) > profile.margin_artifact_max_chars:
        return None
    if block.bbox[3] <= page_height * profile.margin_zone_ratio:
        zone = "header"
    elif block.bbox[1] >= page_height * (1 - profile.margin_zone_ratio):
        zone = "footer"
    else:
        return None
    folded = re.sub(r"\d+", "#", text.casefold())
    folded = re.sub(r"\s+", "", folded)
    folded = re.sub(r"#+", "#", folded)
    if not folded:
        return None
    return f"{zone}:{folded}"


def _suppress_repeated_margin_artifacts(
    raw_blocks: Sequence[_RawBlock],
    *,
    page_rows: Sequence[tuple[dict[str, Any], list[dict[str, Any]]]],
    profile: PdfNativeLayoutProfile,
) -> _MarginArtifactSuppression:
    if not profile.suppress_repeated_margin_artifacts or len(page_rows) < 2:
        return _MarginArtifactSuppression(blocks=tuple(raw_blocks))

    candidates: dict[str, list[int]] = {}
    for index, block in enumerate(raw_blocks):
        page_index = block.page_no - 1
        if not 0 <= page_index < len(page_rows):
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_PAGE_REFERENCE_INVALID")
        signature = _margin_artifact_signature(
            block,
            page_height=float(page_rows[page_index][0]["height"]),
            profile=profile,
        )
        if signature is not None:
            candidates.setdefault(signature, []).append(index)

    required_pages = max(
        profile.margin_repeat_min_pages,
        math.ceil(len(page_rows) * profile.margin_repeat_min_ratio),
    )
    repeated: dict[str, tuple[int, ...]] = {}
    for signature, indexes in candidates.items():
        pages = tuple(sorted({raw_blocks[index].page_no for index in indexes}))
        if len(pages) >= required_pages and pages[-1] - pages[0] >= 2:
            repeated[signature] = pages
    if not repeated:
        return _MarginArtifactSuppression(blocks=tuple(raw_blocks))

    suppressed_indexes: set[int] = set()
    signature_hashes: list[str] = []
    for signature in sorted(repeated):
        signature_hashes.append(hashlib.sha256(signature.encode("utf-8")).hexdigest())
        indexes = candidates[signature]
        preserved_index = indexes[0] if profile.preserve_first_margin_occurrence else None
        suppressed_indexes.update(
            index for index in indexes if index != preserved_index
        )

    retained = tuple(
        block for index, block in enumerate(raw_blocks) if index not in suppressed_indexes
    )
    suppressed_blocks = [raw_blocks[index] for index in sorted(suppressed_indexes)]
    return _MarginArtifactSuppression(
        blocks=retained,
        suppressed_count=len(suppressed_blocks),
        suppressed_char_count=sum(
            _visible_char_count(block.text) for block in suppressed_blocks
        ),
        signature_count=len(repeated),
        affected_pages=tuple(sorted({block.page_no for block in suppressed_blocks})),
        signature_hashes=tuple(signature_hashes),
    )


def _apply_section_paths(
    raw_blocks: Sequence[_RawBlock],
    *,
    content_sha256: str,
    profile: PdfNativeLayoutProfile,
) -> tuple[StructuredEvidenceBlock, ...]:
    stack: list[str] = []
    result: list[StructuredEvidenceBlock] = []
    seen_keys: set[str] = set()
    for ordinal, block in enumerate(raw_blocks):
        text = normalize_evidence_text(block.text)
        if not text:
            continue
        if block.block_type == "heading":
            requested_level = max(1, int(block.heading_level or 1))
            level = min(requested_level, len(stack) + 1)
            stack = stack[: level - 1]
            stack.append(text)
        section_path = tuple(stack)
        identity = {
            "contract_version": PDF_NATIVE_LAYOUT_CONTRACT_VERSION,
            "profile_version": profile.profile_version,
            "content_sha256": content_sha256,
            "page_no": block.page_no,
            "ordinal": ordinal,
            "block_type": block.block_type,
            "bbox": _bbox_payload(block.bbox),
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        block_key = f"pdf-block:{_canonical_hash(identity)}"
        if block_key in seen_keys:
            raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_BLOCK_KEY_DUPLICATE")
        seen_keys.add(block_key)
        result.append(
            StructuredEvidenceBlock(
                block_key=block_key,
                text=text,
                block_type=block.block_type,
                page_no=block.page_no,
                ordinal=len(result),
                section_path=section_path,
                bbox=tuple(_rounded(item) for item in block.bbox),
                boundary_before=block.boundary_before,
                boundary_after=block.boundary_after,
            )
        )
    return tuple(result)


def _common_section_path(paths: Iterable[Sequence[str]]) -> tuple[str, ...]:
    rows = [tuple(path) for path in paths]
    if not rows:
        return ()
    common = list(rows[0])
    for row in rows[1:]:
        prefix_length = 0
        for index, value in enumerate(common):
            if index >= len(row) or row[index] != value:
                break
            prefix_length += 1
        common = common[:prefix_length]
        if not common:
            break
    return tuple(common)


def parse_pdf_native_layout(
    content: bytes,
    *,
    content_sha256: str,
    profile: PdfNativeLayoutProfile = DEFAULT_PDF_NATIVE_LAYOUT_PROFILE,
) -> PdfNativeLayoutResult:
    """Extract deterministic native PDF layout without OCR or model calls."""

    if not content.startswith(b"%PDF-"):
        raise BidPdfNativeLayoutInvalid("BID_FILE_CONTENT_INVALID")
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != str(content_sha256).lower():
        raise BidPdfNativeLayoutInvalid("BID_FILE_CONTENT_HASH_MISMATCH")
    raw_blocks: list[_RawBlock] = []
    page_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    total_native_chars = 0
    try:
        with pdfplumber.open(BytesIO(content), strict_metadata=False) as document:
            page_count = len(document.pages)
            if page_count < 1:
                raise BidPdfNativeLayoutInvalid("BID_PDF_LAYOUT_NO_PAGES")
            if page_count > profile.max_pages:
                raise BidPdfNativeLayoutLimitExceeded(
                    "BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED"
                )
            for page_no, page in enumerate(document.pages, start=1):
                page_blocks, metrics, page_warnings = _page_raw_blocks(
                    page,
                    page_no=page_no,
                    profile=profile,
                )
                raw_blocks.extend(page_blocks)
                page_rows.append((metrics, page_warnings))
                total_native_chars += int(metrics["native_char_count"])
                if total_native_chars > profile.max_native_chars:
                    raise BidPdfNativeLayoutLimitExceeded(
                        "BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED"
                    )
                if len(raw_blocks) > profile.max_blocks:
                    raise BidPdfNativeLayoutLimitExceeded(
                        "BID_PDF_NATIVE_LAYOUT_LIMIT_EXCEEDED"
                    )
    except BidPdfNativeLayoutError:
        raise
    except Exception as exc:
        raise BidPdfNativeLayoutInvalid("BID_FILE_CONTENT_INVALID") from exc

    margin_suppression = _suppress_repeated_margin_artifacts(
        raw_blocks,
        page_rows=page_rows,
        profile=profile,
    )
    blocks = _apply_section_paths(
        margin_suppression.blocks,
        content_sha256=actual_sha256,
        profile=profile,
    )
    blocks_by_page: dict[int, list[StructuredEvidenceBlock]] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.page_no, []).append(block)
    pages: list[PdfNativePageLayout] = []
    warnings: list[dict[str, Any]] = []
    if margin_suppression.suppressed_count:
        warnings.append(
            _warning(
                "PDF_REPEATED_MARGIN_ARTIFACTS_SUPPRESSED",
                "跨页重复的页眉页脚结构块已从证据输入中确定性剔除",
                suppressed_count=margin_suppression.suppressed_count,
                suppressed_char_count=margin_suppression.suppressed_char_count,
                signature_count=margin_suppression.signature_count,
                affected_page_count=len(margin_suppression.affected_pages),
                signature_hashes=list(margin_suppression.signature_hashes),
            )
        )
    for page_no, (metrics, page_warnings) in enumerate(page_rows, start=1):
        page_blocks = blocks_by_page.get(page_no, [])
        effective_page_warnings = list(page_warnings)
        if (
            not page_blocks
            and int(metrics["block_count"]) > 0
            and page_no in margin_suppression.affected_pages
        ):
            effective_page_warnings.append(
                _warning(
                    "PDF_PAGE_ONLY_REPEATED_MARGIN_ARTIFACT",
                    "页面仅保留重复页眉页脚，缺少可引用正文，需要 OCR 或人工复核",
                    page_no=page_no,
                )
            )
        warnings.extend(effective_page_warnings)
        has_text = bool(page_blocks)
        status = (
            "succeeded" if has_text and not effective_page_warnings else "partial"
        )
        requires_ocr = any(
            str(item.get("code") or "")
            in {
                "PDF_PAGE_NO_NATIVE_TEXT",
                "PDF_PAGE_NATIVE_TEXT_INSUFFICIENT",
                "PDF_PAGE_TEXT_COVERAGE_LOW",
                "PDF_PAGE_ONLY_REPEATED_MARGIN_ARTIFACT",
            }
            for item in effective_page_warnings
        )
        pages.append(
            PdfNativePageLayout(
                page_no=page_no,
                width=float(metrics["width"]),
                height=float(metrics["height"]),
                rotation=int(metrics["rotation"]),
                status=status,
                content_source="native" if has_text else "none",
                ocr_status="not_requested" if requires_ocr else "not_applicable",
                native_char_count=int(metrics["native_char_count"]),
                extracted_char_count=int(metrics["extracted_char_count"]),
                word_count=int(metrics["word_count"]),
                block_count=len(page_blocks),
                table_count=int(metrics["table_count"]),
                image_count=int(metrics["image_count"]),
                text_coverage_ratio=float(metrics["text_coverage_ratio"]),
                reading_order_mode=str(metrics["reading_order_mode"]),
                warnings=tuple(effective_page_warnings),
            )
        )
    missing_pages = sum(page.content_source == "none" for page in pages)
    partial_pages = sum(page.status == "partial" for page in pages)
    two_column_pages = sum(page.reading_order_mode == "two_column" for page in pages)
    section_paths = _common_section_path(
        block.section_path for block in blocks if block.section_path
    )
    metrics = {
        "page_count": len(pages),
        "block_count": len(blocks),
        "native_char_count": sum(page.native_char_count for page in pages),
        "extracted_char_count": sum(page.extracted_char_count for page in pages),
        "missing_page_count": missing_pages,
        "partial_page_count": partial_pages,
        "table_count": sum(page.table_count for page in pages),
        "image_count": sum(page.image_count for page in pages),
        "two_column_page_count": two_column_pages,
        "common_section_path": list(section_paths),
    }
    if profile.suppress_repeated_margin_artifacts:
        metrics.update(
            {
                "suppressed_margin_artifact_count": (
                    margin_suppression.suppressed_count
                ),
                "suppressed_margin_artifact_char_count": (
                    margin_suppression.suppressed_char_count
                ),
                "suppressed_margin_signature_count": (
                    margin_suppression.signature_count
                ),
                "suppressed_margin_page_count": len(
                    margin_suppression.affected_pages
                ),
            }
        )
    result_body = {
        "contract_version": PDF_NATIVE_LAYOUT_CONTRACT_VERSION,
        "profile_version": profile.profile_version,
        "pages": [page.to_payload() for page in pages],
        "blocks": [_block_payload(block) for block in blocks],
        "warnings": warnings,
        "metrics": metrics,
    }
    return PdfNativeLayoutResult(
        contract_version=PDF_NATIVE_LAYOUT_CONTRACT_VERSION,
        profile_version=profile.profile_version,
        pages=tuple(pages),
        blocks=blocks,
        warnings=tuple(warnings),
        metrics=metrics,
        result_hash=_canonical_hash(result_body),
    )


__all__ = [
    "DEFAULT_PDF_NATIVE_LAYOUT_PROFILE",
    "PDF_C2_PARSER_PROFILE_VERSION",
    "PDF_RQ1A_NATIVE_LAYOUT_PROFILE_VERSION",
    "PDF_RQ1A_PARSER_PROFILE_VERSION",
    "PDF_NATIVE_LAYOUT_CONTRACT_VERSION",
    "PDF_NATIVE_LAYOUT_PROFILE_VERSION",
    "RQ1A_PDF_NATIVE_LAYOUT_PROFILE",
    "BidPdfNativeLayoutError",
    "BidPdfNativeLayoutInvalid",
    "BidPdfNativeLayoutLimitExceeded",
    "PdfNativeLayoutProfile",
    "PdfNativeLayoutResult",
    "PdfNativePageLayout",
    "parse_pdf_native_layout",
]
