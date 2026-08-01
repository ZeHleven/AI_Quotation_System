from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class UnifiedTenderLocator:
    locator_type: str
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    section: str | None = None
    source_location: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


def normalize_tender_segment_locator(
    segment: dict[str, Any],
) -> UnifiedTenderLocator:
    source_location = _text(segment.get("source_location"), 500)
    page = _positive_int(segment.get("page") or segment.get("page_number"))
    sheet = _text(segment.get("sheet") or segment.get("sheet_name"), 160)
    cell_range = _text(
        segment.get("cell_range") or segment.get("range"),
        80,
    )
    section = _text(
        segment.get("section")
        or segment.get("document_section_label")
        or segment.get("document_section"),
        500,
    )

    if page is None and source_location:
        match = re.search(
            r"(?:PDF|第)?\s*(\d+)\s*页",
            source_location,
            flags=re.IGNORECASE,
        )
        if match:
            page = _positive_int(match.group(1))

    if source_location and (sheet is None or cell_range is None):
        match = re.search(
            r"(?:Excel[:：]?)?\s*['\"]?([^'\"!：:]+)['\"]?!"
            r"\s*([A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?)",
            source_location,
            flags=re.IGNORECASE,
        )
        if match:
            sheet = sheet or _text(match.group(1), 160)
            cell_range = cell_range or _text(match.group(2).upper(), 80)

    if page is not None:
        locator_type = "page"
    elif sheet or cell_range:
        locator_type = "spreadsheet"
    elif section or source_location:
        locator_type = "section"
    else:
        locator_type = "block"

    return UnifiedTenderLocator(
        locator_type=locator_type,
        page=page,
        sheet=sheet,
        cell_range=cell_range,
        section=section or source_location,
        source_location=source_location,
    )


def _text(value: Any, limit: int) -> str | None:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized[:limit] if normalized else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None
