from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "drawing_ocr_context_package_v1"

TILE_RE = re.compile(r"_r(?P<row>\d+)_c(?P<col>\d+)")


def build_ocr_context_packages(
    *,
    raw_evidences: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    max_nearby: int = 16,
    max_page_distance: float = 0.08,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    evidences = [_normalize_raw_evidence(row) for row in raw_evidences]
    evidence_by_id = {_clean_text(row.get("text_id")): row for row in evidences}
    page_index = _candidates_by_page(evidences)
    packages = [
        _build_context_package(
            current=current,
            candidates_by_page=page_index,
            max_nearby=max_nearby,
            max_page_distance=max_page_distance,
        )
        for current in evidences
    ]
    enriched_evidences = _with_nearby_ids(evidences=evidences, packages=packages, evidence_by_id=evidence_by_id)
    summary = _summary(
        packages=packages,
        evidences=evidences,
        max_nearby=max_nearby,
        max_page_distance=max_page_distance,
    )
    outputs = _write_outputs(
        directory=directory,
        packages=packages,
        enriched_evidences=enriched_evidences,
        summary=summary,
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "packages": packages,
        "enriched_evidences": enriched_evidences,
        "outputs": outputs,
    }


def read_raw_evidence_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            parsed = json.loads(text)
            if isinstance(parsed, Mapping):
                rows.append(dict(parsed))
    return rows


def read_raw_evidence_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        return [_normalize_raw_evidence(row) for row in csv.DictReader(file)]


def _build_context_package(
    *,
    current: Mapping[str, Any],
    candidates_by_page: Mapping[int, Sequence[Mapping[str, Any]]],
    max_nearby: int,
    max_page_distance: float,
) -> dict[str, Any]:
    current_id = _clean_text(current.get("text_id"))
    candidates = [
        _nearby_candidate(current, candidate, max_page_distance=max_page_distance)
        for candidate in candidates_by_page.get(_int(current.get("page")), [])
        if _clean_text(candidate.get("text_id")) != current_id
    ]
    valid_candidates = [candidate for candidate in candidates if candidate and candidate.get("include")]
    valid_candidates.sort(key=lambda row: tuple(row.get("sort_key") or ()))
    nearby_evidences = [
        _nearby_public(candidate, rank=index)
        for index, candidate in enumerate(valid_candidates[: max(0, max_nearby)], start=1)
    ]
    nearby_texts = [_clean_text(row.get("text")) for row in nearby_evidences if _clean_text(row.get("text"))]
    nearby_text_ids = [_clean_text(row.get("text_id")) for row in nearby_evidences if _clean_text(row.get("text_id"))]
    return {
        "schema_version": SCHEMA_VERSION,
        "text_id": current_id,
        "source_file": _clean_text(current.get("source_file")),
        "page": _int(current.get("page")),
        "current_text": _clean_text(current.get("text")),
        "confidence": _float(current.get("confidence")),
        "bbox_ratio": _number_list(current.get("bbox_ratio")),
        "tile_id": _clean_text(current.get("tile_id")),
        "snippet_id": _clean_text(current.get("snippet_id")),
        "image_path": _clean_text(current.get("image_path")),
        "current_features": {
            "text_length": _int(current.get("text_length")),
            "is_single_char": _bool(current.get("is_single_char")),
            "has_chinese": _bool(current.get("has_chinese")),
            "has_number": _bool(current.get("has_number")),
            "has_dimension_pattern": _bool(current.get("has_dimension_pattern")),
            "page_zone": _clean_text(current.get("page_zone")),
        },
        "nearby_text_ids": nearby_text_ids,
        "nearby_texts": nearby_texts,
        "nearby_evidences": nearby_evidences,
        "neighborhood_stats": _neighborhood_stats(nearby_evidences),
        "llm_context_text": _llm_context_text(_clean_text(current.get("text")), nearby_texts),
    }


def _nearby_candidate(
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    max_page_distance: float,
) -> dict[str, Any] | None:
    current_center = _center(current)
    candidate_center = _center(candidate)
    if current_center is None or candidate_center is None:
        return None
    current_tile = _tile_position(current.get("tile_id"))
    candidate_tile = _tile_position(candidate.get("tile_id"))
    dx = abs(candidate_center[0] - current_center[0])
    dy = abs(candidate_center[1] - current_center[1])
    page_distance = math.sqrt(dx * dx + dy * dy)
    avg_height = max(0.0005, (_float(current.get("bbox_height_ratio")) + _float(candidate.get("bbox_height_ratio"))) / 2)
    tile_dr = abs(current_tile[0] - candidate_tile[0]) if current_tile and candidate_tile else 999
    tile_dc = abs(current_tile[1] - candidate_tile[1]) if current_tile and candidate_tile else 999
    relation = _relation(
        dx=dx,
        dy=dy,
        avg_height=avg_height,
        tile_dr=tile_dr,
        tile_dc=tile_dc,
        page_distance=page_distance,
        max_page_distance=max_page_distance,
    )
    if relation == "outside_context_window":
        return None
    relation_rank = {
        "same_tile": 0,
        "adjacent_tile": 1,
        "same_line_nearby": 2,
        "vertical_neighbor": 3,
        "spatial_neighbor": 4,
        "nearest_same_page": 5,
    }.get(relation, 9)
    return {
        "include": True,
        "source": candidate,
        "relation": relation,
        "dx_ratio": dx,
        "dy_ratio": dy,
        "page_distance_ratio": page_distance,
        "tile_distance": None if tile_dr == 999 or tile_dc == 999 else max(tile_dr, tile_dc),
        "sort_key": (
            relation_rank,
            999 if tile_dr == 999 or tile_dc == 999 else max(tile_dr, tile_dc),
            round(page_distance, 8),
            round(dy, 8),
            round(dx, 8),
            _int(candidate.get("source_row_index")),
        ),
    }


def _relation(
    *,
    dx: float,
    dy: float,
    avg_height: float,
    tile_dr: int,
    tile_dc: int,
    page_distance: float,
    max_page_distance: float,
) -> str:
    same_line = dy <= max(0.0015, avg_height * 2.5)
    vertically_close = dy <= max(0.008, avg_height * 8)
    horizontally_close = dx <= 0.04
    if tile_dr == 0 and tile_dc == 0:
        return "same_tile"
    if tile_dr <= 1 and tile_dc <= 1:
        return "adjacent_tile"
    if same_line and dx <= 0.08:
        return "same_line_nearby"
    if vertically_close and horizontally_close:
        return "vertical_neighbor"
    if page_distance <= max_page_distance:
        return "spatial_neighbor"
    return "outside_context_window"


def _nearby_public(candidate: Mapping[str, Any], *, rank: int) -> dict[str, Any]:
    source = candidate.get("source") if isinstance(candidate.get("source"), Mapping) else {}
    return {
        "rank": rank,
        "text_id": _clean_text(source.get("text_id")),
        "text": _clean_text(source.get("text")),
        "confidence": _float(source.get("confidence")),
        "bbox_ratio": _number_list(source.get("bbox_ratio")),
        "tile_id": _clean_text(source.get("tile_id")),
        "snippet_id": _clean_text(source.get("snippet_id")),
        "image_path": _clean_text(source.get("image_path")),
        "relation": _clean_text(candidate.get("relation")),
        "dx_ratio": round(_float(candidate.get("dx_ratio")), 8),
        "dy_ratio": round(_float(candidate.get("dy_ratio")), 8),
        "page_distance_ratio": round(_float(candidate.get("page_distance_ratio")), 8),
        "tile_distance": candidate.get("tile_distance"),
    }


def _neighborhood_stats(nearby_evidences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    relation_counts = Counter(_clean_text(row.get("relation")) for row in nearby_evidences)
    return {
        "nearby_count": len(nearby_evidences),
        "same_tile_count": relation_counts.get("same_tile", 0),
        "adjacent_tile_count": relation_counts.get("adjacent_tile", 0),
        "same_line_nearby_count": relation_counts.get("same_line_nearby", 0),
        "vertical_neighbor_count": relation_counts.get("vertical_neighbor", 0),
        "spatial_neighbor_count": relation_counts.get("spatial_neighbor", 0),
        "relation_counts": dict(relation_counts),
    }


def _with_nearby_ids(
    *,
    evidences: Sequence[Mapping[str, Any]],
    packages: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    package_ids = {
        _clean_text(package.get("text_id")): list(package.get("nearby_text_ids") or [])
        for package in packages
        if _clean_text(package.get("text_id"))
    }
    enriched: list[dict[str, Any]] = []
    for evidence in evidences:
        text_id = _clean_text(evidence.get("text_id"))
        base = dict(evidence_by_id.get(text_id) or evidence)
        base["nearby_text_ids"] = package_ids.get(text_id, [])
        enriched.append(base)
    return enriched


def _summary(
    *,
    packages: Sequence[Mapping[str, Any]],
    evidences: Sequence[Mapping[str, Any]],
    max_nearby: int,
    max_page_distance: float,
) -> dict[str, Any]:
    nearby_counts = [_int((package.get("neighborhood_stats") or {}).get("nearby_count")) for package in packages]
    relation_counter: Counter[str] = Counter()
    for package in packages:
        stats = package.get("neighborhood_stats") if isinstance(package.get("neighborhood_stats"), Mapping) else {}
        relation_counter.update(stats.get("relation_counts") or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "input_evidence_count": len(evidences),
        "context_package_count": len(packages),
        "max_nearby": max_nearby,
        "max_page_distance": max_page_distance,
        "packages_with_nearby_count": sum(1 for count in nearby_counts if count > 0),
        "packages_without_nearby_count": sum(1 for count in nearby_counts if count <= 0),
        "total_nearby_link_count": sum(nearby_counts),
        "average_nearby_count": round(sum(nearby_counts) / len(nearby_counts), 4) if nearby_counts else 0,
        "min_nearby_count": min(nearby_counts) if nearby_counts else 0,
        "max_nearby_count": max(nearby_counts) if nearby_counts else 0,
        "relation_counts": dict(relation_counter),
        "page_counts": dict(Counter(str(row.get("page") or "") for row in evidences)),
    }


def _write_outputs(
    directory: Path,
    *,
    packages: Sequence[Mapping[str, Any]],
    enriched_evidences: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    context_jsonl = directory / "ocr_context_packages.jsonl"
    context_csv = directory / "ocr_context_packages.csv"
    enriched_jsonl = directory / "ocr_raw_evidence_with_context.jsonl"
    enriched_csv = directory / "ocr_raw_evidence_with_context.csv"
    summary_path = directory / "ocr_context_summary.json"
    _write_jsonl(context_jsonl, packages)
    _write_context_csv(context_csv, packages)
    _write_jsonl(enriched_jsonl, enriched_evidences)
    _write_enriched_csv(enriched_csv, enriched_evidences)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ocr_context_packages_jsonl": str(context_jsonl.resolve()),
        "ocr_context_packages_csv": str(context_csv.resolve()),
        "ocr_raw_evidence_with_context_jsonl": str(enriched_jsonl.resolve()),
        "ocr_raw_evidence_with_context_csv": str(enriched_csv.resolve()),
        "ocr_context_summary_json": str(summary_path.resolve()),
    }


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_context_csv(path: Path, packages: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "text_id",
        "page",
        "current_text",
        "confidence",
        "tile_id",
        "page_zone",
        "nearby_text_ids",
        "nearby_texts",
        "nearby_count",
        "relation_counts",
        "image_path",
        "llm_context_text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for package in packages:
            stats = package.get("neighborhood_stats") if isinstance(package.get("neighborhood_stats"), Mapping) else {}
            features = package.get("current_features") if isinstance(package.get("current_features"), Mapping) else {}
            writer.writerow(
                {
                    "text_id": package.get("text_id"),
                    "page": package.get("page"),
                    "current_text": package.get("current_text"),
                    "confidence": package.get("confidence"),
                    "tile_id": package.get("tile_id"),
                    "page_zone": features.get("page_zone"),
                    "nearby_text_ids": _csv_value(package.get("nearby_text_ids")),
                    "nearby_texts": " | ".join(str(item) for item in package.get("nearby_texts") or []),
                    "nearby_count": stats.get("nearby_count"),
                    "relation_counts": json.dumps(stats.get("relation_counts") or {}, ensure_ascii=False, sort_keys=True),
                    "image_path": package.get("image_path"),
                    "llm_context_text": package.get("llm_context_text"),
                }
            )


def _write_enriched_csv(path: Path, evidences: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "text_id",
        "source_file",
        "page",
        "text",
        "confidence",
        "bbox_ratio",
        "tile_id",
        "snippet_id",
        "image_path",
        "text_length",
        "is_single_char",
        "has_chinese",
        "has_number",
        "has_dimension_pattern",
        "page_zone",
        "nearby_text_ids",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in evidences:
            writer.writerow({key: _csv_value(row.get(key)) for key in headers})


def _candidates_by_page(evidences: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in evidences:
        grouped[_int(row.get("page"))].append(row)
    return grouped


def _normalize_raw_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["bbox_ratio"] = _number_list(row.get("bbox_ratio"))
    normalized["bbox_page_pt"] = _number_list(row.get("bbox_page_pt"))
    normalized["page"] = _int(row.get("page"))
    normalized["confidence"] = _float(row.get("confidence"))
    normalized["text"] = _clean_text(row.get("text"))
    normalized["text_id"] = _clean_text(row.get("text_id"))
    normalized["bbox_center_x"] = _center_value(normalized, axis="x")
    normalized["bbox_center_y"] = _center_value(normalized, axis="y")
    if not normalized.get("bbox_width_ratio") or not normalized.get("bbox_height_ratio"):
        bbox = normalized.get("bbox_ratio") or []
        if len(bbox) == 4:
            normalized["bbox_width_ratio"] = max(0.0, _float(bbox[2]) - _float(bbox[0]))
            normalized["bbox_height_ratio"] = max(0.0, _float(bbox[3]) - _float(bbox[1]))
    return normalized


def _center(row: Mapping[str, Any]) -> tuple[float, float] | None:
    x = row.get("bbox_center_x")
    y = row.get("bbox_center_y")
    if x is not None and y is not None:
        return _float(x), _float(y)
    bbox = _number_list(row.get("bbox_ratio"))
    if len(bbox) != 4:
        return None
    return (_float(bbox[0]) + _float(bbox[2])) / 2, (_float(bbox[1]) + _float(bbox[3])) / 2


def _center_value(row: Mapping[str, Any], *, axis: str) -> float | None:
    existing = row.get("bbox_center_x" if axis == "x" else "bbox_center_y")
    if existing is not None and str(existing).strip() != "":
        return _float(existing)
    bbox = _number_list(row.get("bbox_ratio"))
    if len(bbox) != 4:
        return None
    return (_float(bbox[0]) + _float(bbox[2])) / 2 if axis == "x" else (_float(bbox[1]) + _float(bbox[3])) / 2


def _tile_position(value: Any) -> tuple[int, int] | None:
    match = TILE_RE.search(_clean_text(value))
    if not match:
        return None
    return _int(match.group("row")), _int(match.group("col"))


def _llm_context_text(current_text: str, nearby_texts: Sequence[str]) -> str:
    lines = [f"当前文字：{current_text}"]
    if nearby_texts:
        lines.append("周边文字：")
        for text in nearby_texts:
            lines.append(f"- {text}")
    else:
        lines.append("周边文字：无")
    return "\n".join(lines)


def _number_list(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [_float(item) for item in value]
    text = _clean_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_float(item) for item in parsed]


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else value


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean_text(value).lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n", ""}:
        return False
    return bool(value)
