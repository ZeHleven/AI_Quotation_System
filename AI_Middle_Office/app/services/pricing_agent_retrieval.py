"""Deterministic retrieval tools for Pricing Agent v1.1.

Exact matching remains database-only. Expanded matching combines keyword and
vector recall through the isolated hybrid-search service, hydrates every hit
from the current source rows, and degrades to local keyword similarity when
the service is unavailable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enterprise_quota import EnterpriseQuotaItem
from app.models.pricing_agent import ARCHIVE_STATUS_READY, PricingArchiveFile, PricingArchiveLine
from app.services.budget_pricing import BudgetPricingError, quota_item_health_reason, strict_active_quota_version
from app.services.pricing_agent_hybrid import (
    PricingHybridDocument,
    PricingHybridHit,
    PricingHybridResult,
    search_pricing_hybrid,
)
from app.services.pricing_archive_parser import normalize_text, normalize_unit


_Q6 = Decimal("0.000001")
_MAX_CANDIDATES = 5
_EXPANDED_MIN_SCORE = Decimal("0.580000")
_HYBRID_BLEND_MIN_LEXICAL_SCORE = Decimal("0.420000")
_RRF_TWO_CHANNEL_MAX = Decimal(2) / Decimal(61)
_ACTION_TERMS = (
    "拆除",
    "安装",
    "新建",
    "更换",
    "维修",
    "修复",
    "清理",
    "运输",
    "搬运",
    "喷涂",
    "铺设",
    "开孔",
    "封堵",
)


@dataclass(frozen=True)
class RetrievalSourceResult:
    source: str
    selected: dict[str, Any] | None
    candidates: tuple[dict[str, Any], ...]
    channel_status: dict[str, str]
    source_issue: dict[str, Any] | None = None


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite():
        return None
    return result.quantize(_Q6, rounding=ROUND_HALF_UP)


def _decimal_text(value: Any) -> str | None:
    result = _decimal(value)
    return format(result, "f") if result is not None else None


def _bigrams(value: str) -> set[str]:
    if not value:
        return set()
    if len(value) == 1:
        return {value}
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _jaccard(left: set[str], right: set[str]) -> Decimal:
    if not left or not right:
        return Decimal("0")
    return Decimal(len(left & right)) / Decimal(len(left | right))


def _similarity(left: str, right: str) -> Decimal:
    if not left or not right:
        return Decimal("0")
    sequence = Decimal(str(SequenceMatcher(None, left, right).ratio()))
    bigrams = _jaccard(_bigrams(left), _bigrams(right))
    contains = Decimal("0.88") if min(len(left), len(right)) >= 4 and (left in right or right in left) else Decimal("0")
    return max(sequence * Decimal("0.65") + bigrams * Decimal("0.35"), contains).quantize(_Q6)


def _context_terms(context: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        normalized
        for normalized in (
            normalize_text(context.get("city")),
            normalize_text(context.get("project_type")),
            normalize_text(context.get("decoration_level")),
        )
        if normalized
    )


def _candidate_score(
    *,
    query: dict[str, Any],
    candidate_name: str,
    candidate_spec: str,
    candidate_unit: str | None,
    raw_text: str,
    context: dict[str, str],
) -> Decimal:
    name_score = _similarity(normalize_text(query.get("item_name")), candidate_name)
    query_spec = normalize_text(query.get("specification"))
    spec_score = _similarity(query_spec, candidate_spec) if query_spec else Decimal("0")
    query_unit = normalize_unit(query.get("unit"))
    unit_score = Decimal("1") if query_unit and candidate_unit and query_unit == candidate_unit else Decimal("0")
    context_hits = sum(1 for term in _context_terms(context) if term in normalize_text(raw_text))
    context_score = min(Decimal(context_hits) * Decimal("0.015"), Decimal("0.045"))
    return min(
        Decimal("1"),
        name_score * Decimal("0.78")
        + spec_score * Decimal("0.10")
        + unit_score * Decimal("0.075")
        + context_score,
    ).quantize(_Q6)


def _unit_compatible(query_unit: Any, candidate_unit: Any) -> bool:
    left, right = normalize_unit(query_unit), normalize_unit(candidate_unit)
    if not left:
        return True
    return bool(right and left == right)


def _action_terms(value: Any) -> set[str]:
    normalized = normalize_text(value)
    return {
        term
        for term in _ACTION_TERMS
        if term in normalized
    }


def _action_compatible(
    query: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    query_actions = _action_terms(
        f"{query.get('item_name') or ''} {query.get('specification') or ''}"
    )
    if not query_actions:
        return True
    candidate_actions = _action_terms(
        f"{candidate.get('item_name') or ''} {candidate.get('specification') or ''}"
    )
    return bool(query_actions & candidate_actions)


def _candidate_passes_expanded_guard(
    candidate: dict[str, Any],
    query: dict[str, Any],
) -> bool:
    if not candidate.get("unit_compatible"):
        return False
    if not _action_compatible(query, candidate):
        return False
    lexical_score = _decimal(candidate.get("lexical_score") or candidate.get("score")) or Decimal("0")
    vector_score = _decimal(candidate.get("vector_score")) or Decimal("0")
    min_vector = Decimal(str(settings.pricing_agent_hybrid_min_vector_score or 0.72))
    return bool(
        lexical_score >= _EXPANDED_MIN_SCORE
        or vector_score >= min_vector
        or (
            lexical_score >= _HYBRID_BLEND_MIN_LEXICAL_SCORE
            and vector_score >= max(Decimal("0"), min_vector - Decimal("0.08"))
        )
    )


def _hybrid_match_type(hit: PricingHybridHit) -> str:
    if hit.vector_score is not None and hit.bm25_score is not None:
        return "hybrid_similar"
    if hit.vector_score is not None:
        return "semantic_similar"
    return "keyword_similar"


def _apply_hybrid_hit(
    candidate: dict[str, Any],
    hit: PricingHybridHit,
) -> dict[str, Any]:
    enriched = dict(candidate)
    lexical_score = _decimal(candidate.get("score")) or Decimal("0")
    vector_score = max(
        Decimal("0"),
        min(Decimal("1"), _decimal(hit.vector_score) or Decimal("0")),
    )
    rrf_score = max(Decimal("0"), _decimal(hit.rrf_score) or Decimal("0"))
    normalized_rrf = min(
        Decimal("1"),
        rrf_score / _RRF_TWO_CHANNEL_MAX,
    )
    blended = (
        lexical_score * Decimal("0.45")
        + vector_score * Decimal("0.45")
        + normalized_rrf * Decimal("0.10")
    ).quantize(_Q6)
    final_score = max(
        lexical_score,
        (vector_score * Decimal("0.95")).quantize(_Q6),
        blended,
    )
    enriched.update(
        {
            "score": _decimal_text(final_score),
            "lexical_score": _decimal_text(lexical_score),
            "vector_score": _decimal_text(hit.vector_score),
            "bm25_score": _decimal_text(hit.bm25_score),
            "rrf_score": _decimal_text(hit.rrf_score),
            "match_type": _hybrid_match_type(hit),
            "retrieval_channels": [
                channel
                for channel, present in (
                    ("keyword", hit.bm25_score is not None),
                    ("vector", hit.vector_score is not None),
                )
                if present
            ],
        }
    )
    return enriched


def _local_keyword_candidates(
    candidates: Iterable[dict[str, Any]],
    query: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        enriched = {
            **candidate,
            "lexical_score": candidate.get("score"),
            "vector_score": None,
            "bm25_score": None,
            "rrf_score": None,
            "match_type": "keyword_similar",
            "retrieval_channels": ["keyword"],
        }
        if _candidate_passes_expanded_guard(enriched, query):
            rows.append(enriched)
    return rows


def _merge_expanded_candidates(
    *,
    base_candidates: Iterable[dict[str, Any]],
    hybrid_hits: Iterable[PricingHybridHit],
    query: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {
        str(candidate.get("source_record_id") or ""): candidate
        for candidate in base_candidates
        if str(candidate.get("source_record_id") or "")
    }
    merged: dict[str, dict[str, Any]] = {
        str(candidate["source_record_id"]): candidate
        for candidate in _local_keyword_candidates(by_id.values(), query)
    }
    for hit in hybrid_hits:
        candidate = by_id.get(hit.record_id)
        if candidate is None:
            continue
        enriched = _apply_hybrid_hit(candidate, hit)
        if not _candidate_passes_expanded_guard(enriched, query):
            continue
        existing = merged.get(hit.record_id)
        if existing is None or Decimal(enriched["score"]) > Decimal(existing["score"]):
            merged[hit.record_id] = enriched
    rows = list(merged.values())
    rows.sort(
        key=lambda row: (
            -Decimal(str(row.get("score") or "0")),
            str(row.get("source_record_id") or ""),
        )
    )
    return rows[:_MAX_CANDIDATES]


def _strict_exact_candidates(
    candidates: list[dict[str, Any]],
    query: dict[str, Any],
) -> list[dict[str, Any]]:
    compatible = [candidate for candidate in candidates if candidate["unit_compatible"]]
    query_spec = normalize_text(query.get("specification"))
    code_exact = [candidate for candidate in compatible if candidate["code_exact"]]
    if query_spec:
        code_exact = [candidate for candidate in code_exact if candidate["normalized_spec"] == query_spec]
    if code_exact:
        return code_exact

    name_exact = [candidate for candidate in compatible if candidate["name_exact"]]
    if query_spec:
        name_exact = [candidate for candidate in name_exact if candidate["normalized_spec"] == query_spec]
    return name_exact


def _exact_selection(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    prices = {
        str(candidate.get("unit_price"))
        for candidate in candidates
        if candidate.get("unit_price") is not None
    }
    if len(prices) == 1:
        return candidates[0]
    return None


def _archive_candidate(line: PricingArchiveLine, query: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    code_exact = bool(normalize_text(query.get("item_code")) and normalize_text(query.get("item_code")) == (line.normalized_code or ""))
    name_exact = normalize_text(query.get("item_name")) == line.normalized_name
    score = Decimal("1") if code_exact else (
        Decimal("0.980000") if name_exact else _candidate_score(
            query=query,
            candidate_name=line.normalized_name,
            candidate_spec=line.normalized_spec or "",
            candidate_unit=line.normalized_unit,
            raw_text=line.raw_text or "",
            context=context,
        )
    )
    return {
        "source": "archive",
        "source_label": "存档数据",
        "source_record_id": line.line_uuid,
        "archive_uuid": line.archive_file.archive_uuid,
        "archive_filename": line.archive_file.original_filename,
        "source_sheet": line.source_sheet,
        "source_row_index": int(line.source_row_index),
        "item_code": line.item_code,
        "item_name": line.item_name,
        "specification": line.specification,
        "unit": line.unit,
        "unit_price": _decimal_text(line.unit_price),
        "score": _decimal_text(score),
        "match_type": "code_exact" if code_exact else ("name_exact" if name_exact else "lexical_similar"),
        "code_exact": code_exact,
        "name_exact": name_exact,
        "normalized_spec": line.normalized_spec or "",
        "unit_compatible": _unit_compatible(query.get("unit"), line.unit),
        "price_derivation": line.price_derivation,
    }


def _archive_hybrid_documents(
    rows: Iterable[PricingArchiveLine],
) -> list[PricingHybridDocument]:
    return [
        PricingHybridDocument(
            record_id=line.line_uuid,
            content=(
                f"项目编码：{line.item_code or ''}；"
                f"项目名称：{line.item_name or ''}；"
                f"项目特征：{line.specification or ''}；"
                f"单位：{line.unit or ''}。"
            ),
            document_key=(
                f"{line.archive_file.original_filename}:"
                f"{line.source_sheet}:{line.source_row_index}"
            ),
            keywords=tuple(
                value
                for value in (
                    line.item_code,
                    line.item_name,
                    line.specification,
                    line.unit,
                )
                if value
            ),
            locator={
                "source": "archive",
                "archive_uuid": line.archive_file.archive_uuid,
                "source_sheet": line.source_sheet,
                "source_row_index": int(line.source_row_index),
            },
        )
        for line in rows
    ]


def _search_archive_hybrid(
    rows: list[PricingArchiveLine],
    *,
    account_id: int,
    query_text: str,
) -> PricingHybridResult:
    grouped: dict[int, list[PricingArchiveLine]] = defaultdict(list)
    for row in rows:
        grouped[int(row.archive_file_id)].append(row)
    hits: dict[str, PricingHybridHit] = {}
    indexed_shards = 0
    searched_shards = 0
    issues: list[dict[str, Any]] = []
    statuses: list[str] = []
    for archive_rows in grouped.values():
        archive = archive_rows[0].archive_file
        result = search_pricing_hybrid(
            documents=_archive_hybrid_documents(archive_rows),
            scope_key=(
                f"archive-account:{account_id}:"
                f"{archive.archive_uuid}:{archive.file_sha256}"
            ),
            manifest_version=1,
            query=query_text,
        )
        statuses.append(result.status)
        indexed_shards += result.indexed_shard_count
        searched_shards += result.searched_shard_count
        if result.issue:
            issues.append(result.issue)
        for hit in result.hits:
            existing = hits.get(hit.record_id)
            if existing is None or hit.rrf_score > existing.rrf_score:
                hits[hit.record_id] = hit
    status = (
        "used"
        if "used" in statuses
        else (statuses[0] if statuses else "no_documents")
    )
    return PricingHybridResult(
        hits=tuple(
            sorted(
                hits.values(),
                key=lambda item: (-item.rrf_score, item.record_id),
            )
        ),
        status=status,
        indexed_shard_count=indexed_shards,
        searched_shard_count=searched_shards,
        issue=(
            {
                "code": "PRICING_AGENT_ARCHIVE_HYBRID_PARTIAL",
                "message": "部分存档数据混合索引不可用，已保留可用结果并降级关键词候选。",
                "source_issues": issues,
            }
            if issues
            else None
        ),
    )


def retrieve_archive(
    db: Session,
    *,
    account_id: int,
    query: dict[str, Any],
    context: dict[str, str],
    expanded: bool,
) -> RetrievalSourceResult:
    base = (
        db.query(PricingArchiveLine)
        .join(PricingArchiveFile, PricingArchiveLine.archive_file_id == PricingArchiveFile.id)
        .filter(
            PricingArchiveLine.account_id == account_id,
            PricingArchiveLine.searchable.is_(True),
            PricingArchiveFile.status == ARCHIVE_STATUS_READY,
        )
    )
    code = normalize_text(query.get("item_code"))
    name = normalize_text(query.get("item_name"))
    exact_rows: list[PricingArchiveLine] = []
    if code:
        exact_rows.extend(base.filter(PricingArchiveLine.normalized_code == code).limit(30).all())
    if name:
        seen = {row.id for row in exact_rows}
        exact_rows.extend(
            row
            for row in base.filter(PricingArchiveLine.normalized_name == name).limit(50).all()
            if row.id not in seen
        )
    candidates = _strict_exact_candidates(
        [_archive_candidate(row, query, context) for row in exact_rows],
        query,
    )
    candidates.sort(key=lambda row: (-Decimal(row["score"]), row["source_record_id"]))
    selected = _exact_selection(candidates)
    if candidates or not expanded:
        return RetrievalSourceResult(
            source="archive",
            selected=selected,
            candidates=tuple(candidates[:_MAX_CANDIDATES]),
            channel_status={"exact": "used", "lexical": "not_used", "vector": "not_used"},
        )

    approximate_query = base
    query_unit = normalize_unit(query.get("unit"))
    if query_unit:
        approximate_query = approximate_query.filter(PricingArchiveLine.normalized_unit == query_unit)
    rows = (
        approximate_query
        .order_by(PricingArchiveLine.archive_file_id.asc(), PricingArchiveLine.id.asc())
        .limit(max(1, int(settings.pricing_agent_archive_max_indexed_rows)))
        .all()
    )
    base_candidates = [_archive_candidate(row, query, context) for row in rows]
    hybrid = _search_archive_hybrid(
        rows,
        account_id=account_id,
        query_text=" ".join(
            str(query.get(field) or "").strip()
            for field in ("item_code", "item_name", "specification", "unit")
            if str(query.get(field) or "").strip()
        ),
    )
    candidates = _merge_expanded_candidates(
        base_candidates=base_candidates,
        hybrid_hits=hybrid.hits,
        query=query,
    )
    return RetrievalSourceResult(
        source="archive",
        selected=None,
        candidates=tuple(candidates),
        channel_status={
            "exact": "used",
            "lexical": "used",
            "vector": (
                "used"
                if hybrid.status == "used"
                else f"{hybrid.status}_fallback_keyword"
            ),
            "fusion": "rrf" if hybrid.status == "used" else "keyword_only",
        },
        source_issue=hybrid.issue,
    )


def _enterprise_candidate(item: EnterpriseQuotaItem, query: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    code_norm = normalize_text(item.quota_code)
    name_norm = normalize_text(item.item_name)
    spec_norm = normalize_text(item.specification or item.work_content)
    query_code = normalize_text(query.get("item_code"))
    code_exact = bool(query_code and query_code == code_norm)
    name_exact = normalize_text(query.get("item_name")) == name_norm
    score = Decimal("1") if code_exact else (
        Decimal("0.980000") if name_exact else _candidate_score(
            query=query,
            candidate_name=name_norm,
            candidate_spec=spec_norm,
            candidate_unit=normalize_unit(item.unit),
            raw_text=f"{item.item_name or ''} {item.specification or ''} {item.work_content or ''}",
            context=context,
        )
    )
    return {
        "source": "enterprise",
        "source_label": "企业数据",
        "source_record_id": str(item.id),
        "enterprise_quota_version_id": int(item.version_id),
        "item_code": item.quota_code,
        "item_name": item.item_name,
        "specification": item.specification or item.work_content,
        "unit": item.unit,
        "unit_price": _decimal_text(item.unit_price),
        "score": _decimal_text(score),
        "match_type": "code_exact" if code_exact else ("name_exact" if name_exact else "lexical_similar"),
        "code_exact": code_exact,
        "name_exact": name_exact,
        "normalized_spec": spec_norm,
        "unit_compatible": _unit_compatible(query.get("unit"), item.unit),
    }


def _enterprise_hybrid_documents(
    items: Iterable[EnterpriseQuotaItem],
    *,
    version_code: str,
) -> list[PricingHybridDocument]:
    return [
        PricingHybridDocument(
            record_id=str(item.id),
            content=(
                f"定额编码：{item.quota_code or ''}；"
                f"项目名称：{item.item_name or ''}；"
                f"项目特征：{item.specification or ''}；"
                f"工作内容：{item.work_content or ''}；"
                f"单位：{item.unit or ''}。"
            ),
            document_key=f"{version_code}:{item.quota_code or item.id}",
            keywords=tuple(
                value
                for value in (
                    item.quota_code,
                    item.item_name,
                    item.specification,
                    item.work_content,
                    item.unit,
                )
                if value
            ),
            locator={
                "source": "enterprise",
                "enterprise_quota_version_id": int(item.version_id),
                "enterprise_quota_item_id": int(item.id),
            },
        )
        for item in items
    ]


def _healthy_enterprise_items(items: Iterable[EnterpriseQuotaItem]) -> list[EnterpriseQuotaItem]:
    return [
        item
        for item in items
        if quota_item_health_reason(item) is None
        and _decimal(item.unit_price) is not None
        and (_decimal(item.unit_price) or Decimal("0")) > 0
    ]


def retrieve_enterprise(
    db: Session,
    *,
    query: dict[str, Any],
    context: dict[str, str],
    expanded: bool,
) -> RetrievalSourceResult:
    try:
        version = strict_active_quota_version(db)
    except BudgetPricingError as exc:
        return RetrievalSourceResult(
            source="enterprise",
            selected=None,
            candidates=(),
            channel_status={"exact": "unavailable", "lexical": "not_used", "vector": "not_used"},
            source_issue=exc.detail,
        )
    base = db.query(EnterpriseQuotaItem).filter(EnterpriseQuotaItem.version_id == version.id)
    code = normalize_text(query.get("item_code"))
    name = normalize_text(query.get("item_name"))
    # SQL fields are not normalized in this frozen source, so exact candidates
    # are conservatively filtered from the active version in memory.
    items = _healthy_enterprise_items(base.order_by(EnterpriseQuotaItem.id.asc()).all())
    exact_items = [
        item
        for item in items
        if (code and normalize_text(item.quota_code) == code) or (name and normalize_text(item.item_name) == name)
    ]
    candidates = _strict_exact_candidates(
        [_enterprise_candidate(item, query, context) for item in exact_items],
        query,
    )
    candidates.sort(key=lambda row: (-Decimal(row["score"]), row["source_record_id"]))
    selected = _exact_selection(candidates)
    if candidates or not expanded:
        return RetrievalSourceResult(
            source="enterprise",
            selected=selected,
            candidates=tuple(candidates[:_MAX_CANDIDATES]),
            channel_status={"exact": "used", "lexical": "not_used", "vector": "not_used"},
        )
    base_candidates = [_enterprise_candidate(item, query, context) for item in items]
    hybrid = search_pricing_hybrid(
        documents=_enterprise_hybrid_documents(
            items,
            version_code=version.version_code,
        ),
        scope_key=(
            f"enterprise-version:{version.id}:"
            f"{version.version_code}:revision:{version.revision}"
        ),
        manifest_version=max(1, int(version.revision or 1)),
        query=" ".join(
            str(query.get(field) or "").strip()
            for field in ("item_code", "item_name", "specification", "unit")
            if str(query.get(field) or "").strip()
        ),
    )
    candidates = _merge_expanded_candidates(
        base_candidates=base_candidates,
        hybrid_hits=hybrid.hits,
        query=query,
    )
    return RetrievalSourceResult(
        source="enterprise",
        selected=None,
        candidates=tuple(candidates),
        channel_status={
            "exact": "used",
            "lexical": "used",
            "vector": (
                "used"
                if hybrid.status == "used"
                else f"{hybrid.status}_fallback_keyword"
            ),
            "fusion": "rrf" if hybrid.status == "used" else "keyword_only",
        },
        source_issue=hybrid.issue,
    )
