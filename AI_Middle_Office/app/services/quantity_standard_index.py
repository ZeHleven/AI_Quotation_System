from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.quantity_standard_library import (
    ACTIVE_STATUS,
    QuantityStandardItem,
    QuantityStandardLibrary,
    load_quantity_standard_library,
    quantity_standard_summary,
    search_quantity_standard_items,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = BACKEND_ROOT.parent
DEFAULT_STANDARD_LIBRARY_INDEX_PATH = BACKEND_ROOT / "data" / "standards" / "standard_library_index.json"

INDEX_VERSION = "biz2x-standard-library-index-v1"
ITEM_LIBRARY_TYPE = "quantity_item_library"
PRICING_RULE_LIBRARY_TYPE = "pricing_rule_library"


class QuantityStandardIndexError(ValueError):
    pass


@dataclass(frozen=True)
class StandardIndexEntry:
    standard_code: str
    standard_name: str
    library_type: str
    status: str
    path: Path
    scope: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class LoadedStandardLibraryIndex:
    version: str
    source_path: str
    entries: tuple[StandardIndexEntry, ...]
    quantity_libraries: dict[str, QuantityStandardLibrary]
    pricing_rule_libraries: dict[str, dict[str, Any]]

    @property
    def active_quantity_standard_codes(self) -> list[str]:
        return [
            entry.standard_code
            for entry in self.entries
            if entry.library_type == ITEM_LIBRARY_TYPE and entry.status == ACTIVE_STATUS
        ]


INSTALLATION_ROUTE_KEYWORDS = (
    "配电",
    "配管",
    "配线",
    "电缆",
    "电线",
    "桥架",
    "灯具",
    "照明",
    "插座",
    "开关",
    "弱电",
    "消防",
    "给水",
    "排水",
    "水表",
    "阀门",
    "洁具",
    "地漏",
    "管道",
    "风机",
    "空调",
    "喷淋",
)

BUILDING_DECORATION_ROUTE_KEYWORDS = (
    "楼地面",
    "地面",
    "地砖",
    "瓷砖",
    "块料",
    "墙面",
    "墙砖",
    "吊顶",
    "天棚",
    "涂料",
    "抹灰",
    "防水",
    "石材",
    "踢脚",
    "门窗",
    "窗台",
    "窗帘盒",
    "保温",
    "屋面",
    "砌筑",
    "混凝土",
)

QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "地砖": ("块料楼地面", "块料", "楼地面", "面层材料品种规格"),
    "瓷砖": ("块料楼地面", "块料墙面", "块料", "面层材料品种规格"),
    "CT": ("块料楼地面", "块料墙面", "地砖", "瓷砖"),
    "配电箱": ("成套配电箱", "配电", "电气"),
    "地漏": ("给、排水附件", "排水附件", "排水", "卫生器具附件"),
    "洁具": ("卫生器具", "给、排水附件", "洗脸盆", "大便器", "小便器"),
    "开关": ("开关", "照明开关", "电气"),
}

DIRECT_TARGET_HINTS: tuple[dict[str, Any], ...] = (
    {
        "triggers": ("地漏",),
        "standard_code": "GBT50856-2024",
        "item_code": "031003014",
        "reason": "地漏在 50856 中通常归入给、排水附件上位项目。",
        "boost": 40.0,
    },
    {
        "triggers": ("配电箱",),
        "standard_code": "GBT50856-2024",
        "item_code": "030402011",
        "reason": "配电箱应使用 50856 通用安装工程成套配电箱项目。",
        "boost": 35.0,
    },
    {
        "triggers": ("地砖", "瓷砖", "CT"),
        "standard_code": "GBT50854-2024",
        "item_code": "011102003",
        "reason": "地砖/瓷砖地面应优先召回 50854 块料楼地面。",
        "boost": 35.0,
    },
)

_SPLITTER_RE = re.compile(r"[\s\-_—、，。；;:：/\\()（）\[\]{}【】<>\"'“”‘’+|]+")


def load_standard_library_index(path: str | Path | None = None) -> LoadedStandardLibraryIndex:
    source = Path(path) if path else DEFAULT_STANDARD_LIBRARY_INDEX_PATH
    if not source.exists():
        raise QuantityStandardIndexError(f"standard library index not found: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuantityStandardIndexError(f"invalid standard library index JSON: {source}") from exc

    version = _clean_text(raw.get("version"))
    if version != INDEX_VERSION:
        raise QuantityStandardIndexError(f"unsupported standard index version: {version}")
    standards = raw.get("standards")
    if not isinstance(standards, list) or not standards:
        raise QuantityStandardIndexError("standard index must contain standards")

    entries: list[StandardIndexEntry] = []
    quantity_libraries: dict[str, QuantityStandardLibrary] = {}
    pricing_rule_libraries: dict[str, dict[str, Any]] = {}

    for item in standards:
        if not isinstance(item, dict):
            raise QuantityStandardIndexError("standard index entries must be objects")
        entry = _entry_from_dict(item)
        entries.append(entry)
        if entry.status != ACTIVE_STATUS:
            continue
        if entry.library_type == ITEM_LIBRARY_TYPE:
            quantity_libraries[entry.standard_code] = load_quantity_standard_library(entry.path)
        elif entry.library_type == PRICING_RULE_LIBRARY_TYPE:
            pricing_rule_libraries[entry.standard_code] = _load_json(entry.path)

    if not quantity_libraries:
        raise QuantityStandardIndexError("no active quantity item standard libraries found")
    return LoadedStandardLibraryIndex(
        version=version,
        source_path=str(source),
        entries=tuple(entries),
        quantity_libraries=quantity_libraries,
        pricing_rule_libraries=pricing_rule_libraries,
    )


def standard_index_summary(index: LoadedStandardLibraryIndex) -> dict[str, Any]:
    quantity_summaries = {
        standard_code: quantity_standard_summary(library)
        for standard_code, library in sorted(index.quantity_libraries.items())
    }
    pricing_summaries = {
        standard_code: dict(library.get("summary") or {})
        for standard_code, library in sorted(index.pricing_rule_libraries.items())
    }
    return {
        "version": index.version,
        "source_path": index.source_path,
        "quantity_standard_count": len(index.quantity_libraries),
        "pricing_rule_standard_count": len(index.pricing_rule_libraries),
        "active_quantity_standard_codes": index.active_quantity_standard_codes,
        "quantity_libraries": quantity_summaries,
        "pricing_rule_libraries": pricing_summaries,
    }


def infer_standard_routes(text: str) -> list[str]:
    normalized = _normalize_for_route(text)
    if not normalized:
        return []
    has_installation = any(_normalize_for_route(keyword) in normalized for keyword in INSTALLATION_ROUTE_KEYWORDS)
    has_building = any(_normalize_for_route(keyword) in normalized for keyword in BUILDING_DECORATION_ROUTE_KEYWORDS)
    if has_installation and not has_building:
        return ["GBT50856-2024"]
    if has_building and not has_installation:
        return ["GBT50854-2024"]
    if has_installation and has_building:
        return ["GBT50856-2024", "GBT50854-2024"]
    return ["GBT50854-2024", "GBT50856-2024"]


def search_standard_index(
    query: str,
    *,
    index: LoadedStandardLibraryIndex | None = None,
    standard_codes: list[str] | tuple[str, ...] | None = None,
    include_draft: bool = False,
    limit: int = 10,
    limit_per_standard: int = 12,
) -> list[dict[str, Any]]:
    loaded_index = index or load_standard_library_index()
    route_codes = list(standard_codes or infer_standard_routes(query) or loaded_index.active_quantity_standard_codes)
    query_variants = _query_variants(query)
    scored: dict[tuple[str, str], dict[str, Any]] = {}

    for standard_code in route_codes:
        library = loaded_index.quantity_libraries.get(standard_code)
        if not library:
            continue
        for variant in query_variants:
            for result in search_quantity_standard_items(
                library,
                variant,
                include_draft=include_draft,
                limit=limit_per_standard,
            ):
                item = result["item"]
                key = (standard_code, item["item_code"])
                score = float(result.get("score") or 0)
                route_boost = 2.0 if standard_code in route_codes[:1] else 0.0
                candidate = _candidate_payload(
                    loaded_index,
                    standard_code=standard_code,
                    item=item,
                    score=score + route_boost,
                    matched_fields=list(result.get("matched_fields") or []),
                    match_reason=f"standard_search:{variant}",
                )
                _keep_best_candidate(scored, key, candidate)

    for hint in DIRECT_TARGET_HINTS:
        if not _hint_matches(query, hint):
            continue
        standard_code = hint["standard_code"]
        if standard_code not in route_codes and standard_codes is not None:
            continue
        item = find_standard_item(loaded_index, standard_code, hint["item_code"])
        if not item:
            continue
        key = (standard_code, item.item_code)
        candidate = _candidate_payload(
            loaded_index,
            standard_code=standard_code,
            item=item.as_dict(),
            score=float(hint["boost"]),
            matched_fields=["direct_target_hint"],
            match_reason=hint["reason"],
        )
        _keep_best_candidate(scored, key, candidate)

    ordered = sorted(
        scored.values(),
        key=lambda item: (-float(item["score"]), item["standard_code"], item["item_code"]),
    )
    return ordered[: max(0, limit)]


def find_standard_item(
    index: LoadedStandardLibraryIndex,
    standard_code: str,
    item_code: str,
) -> QuantityStandardItem | None:
    library = index.quantity_libraries.get(standard_code)
    if not library:
        return None
    target = _clean_text(item_code)
    for item in library.items:
        if item.item_code == target or item.official_item_code == target:
            return item
    return None


def search_pricing_rules(
    query: str,
    *,
    index: LoadedStandardLibraryIndex | None = None,
    standard_code: str = "GBT50500-2024",
    limit: int = 5,
) -> list[dict[str, Any]]:
    loaded_index = index or load_standard_library_index()
    library = loaded_index.pricing_rule_libraries.get(standard_code)
    if not library:
        return []
    normalized_query = _normalize_for_route(query)
    if not normalized_query:
        return []
    matches: list[dict[str, Any]] = []
    for rule in library.get("rules") or []:
        text = _normalize_for_route(rule.get("text"))
        keywords = _normalize_for_route(" ".join(rule.get("keywords") or []))
        if normalized_query not in text and normalized_query not in keywords:
            continue
        score = 8.0 if normalized_query in text else 4.0
        matches.append(
            {
                "standard_code": standard_code,
                "rule_id": rule.get("rule_id", ""),
                "clause_no": rule.get("clause_no", ""),
                "category": rule.get("category", ""),
                "text": rule.get("text", ""),
                "score": score,
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["rule_id"]))
    return matches[:limit]


def _entry_from_dict(raw: dict[str, Any]) -> StandardIndexEntry:
    standard_code = _required_text(raw, "standard_code")
    library_type = _required_text(raw, "library_type")
    status = _clean_text(raw.get("status")) or ACTIVE_STATUS
    path = _resolve_index_path(_required_text(raw, "path"))
    if not path.exists():
        raise QuantityStandardIndexError(f"{standard_code}: library path not found: {path}")
    return StandardIndexEntry(
        standard_code=standard_code,
        standard_name=_required_text(raw, "standard_name"),
        library_type=library_type,
        status=status,
        path=path,
        scope=_clean_text(raw.get("scope")),
        raw=dict(raw),
    )


def _candidate_payload(
    index: LoadedStandardLibraryIndex,
    *,
    standard_code: str,
    item: dict[str, Any],
    score: float,
    matched_fields: list[str],
    match_reason: str,
) -> dict[str, Any]:
    library = index.quantity_libraries[standard_code]
    return {
        "standard_code": standard_code,
        "standard_name": library.standard.get("name", ""),
        "item_code": item.get("item_code", ""),
        "official_item_code": item.get("official_item_code") or item.get("item_code", ""),
        "item_name": item.get("item_name", ""),
        "chapter_name": item.get("chapter_name", ""),
        "unit_options": list(item.get("unit_options") or []),
        "feature_fields": list(item.get("feature_fields") or []),
        "quantity_rule": dict(item.get("quantity_rule") or {}),
        "drawing_evidence_requirements": list(item.get("drawing_evidence_requirements") or []),
        "source_note": item.get("source_note", ""),
        "score": round(score, 3),
        "matched_fields": matched_fields,
        "match_reason": match_reason,
    }


def _query_variants(query: str) -> list[str]:
    variants = [_clean_text(query)]
    normalized = _normalize_for_route(query)
    for trigger, aliases in QUERY_ALIASES.items():
        if _normalize_for_route(trigger) not in normalized:
            continue
        variants.extend(aliases)
    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = _normalize_for_route(variant)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(variant)
    return deduped


def _hint_matches(query: str, hint: dict[str, Any]) -> bool:
    normalized = _normalize_for_route(query)
    return any(_normalize_for_route(trigger) in normalized for trigger in hint.get("triggers") or [])


def _keep_best_candidate(
    scored: dict[tuple[str, str], dict[str, Any]],
    key: tuple[str, str],
    candidate: dict[str, Any],
) -> None:
    current = scored.get(key)
    if current is None or float(candidate["score"]) > float(current["score"]):
        scored[key] = candidate


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuantityStandardIndexError(f"invalid JSON: {path}") from exc


def _resolve_index_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    workspace_path = WORKSPACE_ROOT / path
    if workspace_path.exists():
        return workspace_path
    backend_path = BACKEND_ROOT / path
    if backend_path.exists():
        return backend_path
    return workspace_path


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = _clean_text(raw.get(key))
    if not value:
        raise QuantityStandardIndexError(f"{key} is required")
    return value


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_for_route(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).lower()
    return _SPLITTER_RE.sub("", text)
