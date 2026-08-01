from __future__ import annotations

import re
from collections.abc import Sequence

from .contracts import (
    EvidenceBlock,
    EvidenceStructuralContext,
)


_DOCX_TABLE_ROW = re.compile(
    r"^DOCX表(?P<table>\d+)第(?P<row>\d+)行$",
    re.IGNORECASE,
)
_SHEET_ROW = re.compile(
    r"^(?P<sheet>.+?)\s+第(?P<row>\d+)行$",
)
_HEADING_PREFIX = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零〇两\d]+[章节篇部分]"
    r"|[一二三四五六七八九十百零〇两\d]+[、.．]"
    r"|[（(][一二三四五六七八九十百零〇两\d]+[）)]"
    r")"
)
_HEADING_BODY_SIGNALS = (
    "应",
    "须",
    "不得",
    "必须",
    "支付",
    "承担",
    "提交",
    "报价",
    "结算",
)


def build_structural_context_map(
    *,
    candidate_evidence_ids: Sequence[str],
    document_blocks: Sequence[EvidenceBlock],
    max_heading_lookback: int = 12,
) -> dict[str, list[EvidenceStructuralContext]]:
    """Resolve bounded, same-document structural parents.

    The function never invents a parent. Table/sheet parents must be an
    existing first row in the same structure, while section parents must be
    an existing preceding heading in the same immutable document version.
    """

    requested = set(candidate_evidence_ids)
    bounded_lookback = max(1, min(int(max_heading_lookback), 50))
    by_document: dict[
        tuple[str, int],
        list[EvidenceBlock],
    ] = {}
    for block in document_blocks:
        by_document.setdefault(
            (block.document_id, block.document_version),
            [],
        ).append(block)

    resolved: dict[str, list[EvidenceStructuralContext]] = {}
    for blocks in by_document.values():
        ordered = sorted(blocks, key=lambda item: item.block_order)
        positions = {
            item.evidence_id: index
            for index, item in enumerate(ordered)
        }
        table_rows: dict[str, list[tuple[int, EvidenceBlock]]] = {}
        sheet_rows: dict[str, list[tuple[int, EvidenceBlock]]] = {}
        for block in ordered:
            structure = structural_row_identity(block)
            if structure is None:
                continue
            kind, key, row_index = structure
            target = table_rows if kind == "docx_table" else sheet_rows
            target.setdefault(key, []).append((row_index, block))
        table_headers = {
            key: min(rows, key=lambda item: (item[0], item[1].block_order))[1]
            for key, rows in table_rows.items()
        }
        sheet_headers = {
            key: min(rows, key=lambda item: (item[0], item[1].block_order))[1]
            for key, rows in sheet_rows.items()
        }

        for block in ordered:
            if block.evidence_id not in requested:
                continue
            contexts: list[EvidenceStructuralContext] = []
            structure = structural_row_identity(block)
            heading_anchor = block
            if structure is not None:
                kind, key, _ = structure
                parent = (
                    table_headers.get(key)
                    if kind == "docx_table"
                    else sheet_headers.get(key)
                )
                if (
                    parent is not None
                    and parent.evidence_id != block.evidence_id
                    and _looks_like_header(parent.content)
                ):
                    relation = (
                        "table_header_parent"
                        if kind == "docx_table"
                        else "sheet_header_parent"
                    )
                    contexts.append(
                        _context_item(
                            relation=relation,
                            block=parent,
                        )
                    )
                    heading_anchor = parent

            anchor_position = positions.get(heading_anchor.evidence_id)
            if anchor_position is not None:
                start = max(0, anchor_position - bounded_lookback)
                for parent in reversed(ordered[start:anchor_position]):
                    if structural_row_identity(parent) is not None:
                        continue
                    if _looks_like_heading(parent.content):
                        contexts.insert(
                            0,
                            _context_item(
                                relation="section_parent",
                                block=parent,
                            ),
                        )
                        break
            if contexts:
                resolved[block.evidence_id] = contexts[:3]
    return resolved


def structural_row_identity(
    block: EvidenceBlock,
) -> tuple[str, str, int] | None:
    source_location = str(
        block.locator.source_location or ""
    ).strip()
    match = _DOCX_TABLE_ROW.fullmatch(source_location)
    if match is not None:
        return (
            "docx_table",
            f"{block.document_id}:table:{match.group('table')}",
            int(match.group("row")),
        )
    match = _SHEET_ROW.fullmatch(source_location)
    if match is None:
        return None
    sheet = re.sub(r"\s+", " ", match.group("sheet")).strip().casefold()
    if not sheet or sheet.upper().startswith("DOCX表"):
        return None
    return (
        "sheet",
        f"{block.document_id}:sheet:{sheet}",
        int(match.group("row")),
    )


def _context_item(
    *,
    relation: str,
    block: EvidenceBlock,
) -> EvidenceStructuralContext:
    content = re.sub(r"\s+", " ", block.content).strip()[:2000]
    return EvidenceStructuralContext(
        relation=relation,
        content=content,
        evidence_ref=block.to_ref(
            context_read=False,
            quote=content[:300],
        ),
    )


def _looks_like_header(value: str) -> bool:
    cells = [
        item.strip()
        for item in re.split(r"\s*\|\s*", value)
        if item.strip()
    ]
    return 2 <= len(cells) <= 30 and len(value) <= 1000


def _looks_like_heading(value: str) -> bool:
    text = re.sub(r"\s+", " ", value).strip()
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 80:
        return False
    if any(mark in text for mark in ("。", "；", ";")):
        return False
    if not _HEADING_PREFIX.search(text):
        return False
    return not any(signal in compact for signal in _HEADING_BODY_SIGNALS)
