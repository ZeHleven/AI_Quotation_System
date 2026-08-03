"""Isolated hybrid-search adapter for Pricing Agent v1.1.

The pricing agent reuses the internal embedding/BM25/RRF service through its
authenticated HTTP contract. Pricing sources remain authoritative in MySQL;
the hybrid index stores searchable text and record identifiers only, and every
hit is hydrated back from the current account-scoped database query.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid5

from app.core.config import settings
from mcp_servers.tender_evidence.hybrid_client import (
    HybridIndexBlock,
    HttpTenderHybridSearchClient,
    TenderHybridIndexStale,
    TenderHybridSearchError,
    configured_hybrid_client,
    hybrid_search_enabled,
)


_INDEX_SCHEMA_VERSION = "pricing-agent-hybrid-v1"


@dataclass(frozen=True)
class PricingHybridDocument:
    record_id: str
    content: str
    document_key: str
    keywords: tuple[str, ...] = ()
    locator: dict[str, Any] | None = None


@dataclass(frozen=True)
class PricingHybridHit:
    record_id: str
    rrf_score: float
    vector_score: float | None
    bm25_score: float | None


@dataclass(frozen=True)
class PricingHybridResult:
    hits: tuple[PricingHybridHit, ...]
    status: str
    indexed_shard_count: int
    searched_shard_count: int
    issue: dict[str, Any] | None = None


def pricing_hybrid_configured() -> bool:
    if not settings.feature_pricing_agent_hybrid_search:
        return False
    if not hybrid_search_enabled():
        return False
    try:
        configured_hybrid_client()
    except (TenderHybridSearchError, ValueError):
        return False
    return True


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _scope_uuid(scope_key: str, shard_index: int) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"pricing-agent:{scope_key}:shard:{shard_index}",
        )
    )


def _manifest_hash(documents: Sequence[PricingHybridDocument]) -> str:
    canonical = [
        {
            "record_id": item.record_id,
            "content_hash": _content_hash(item.content),
        }
        for item in documents
    ]
    raw = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _index_blocks(
    documents: Sequence[PricingHybridDocument],
    *,
    case_id: str,
    manifest_version: int,
) -> list[HybridIndexBlock]:
    blocks: list[HybridIndexBlock] = []
    for order, item in enumerate(documents):
        content = item.content.strip()[:50000]
        blocks.append(
            HybridIndexBlock(
                evidence_id=item.record_id[:80],
                block_id=item.record_id[:80],
                document_id=case_id,
                document_key=item.document_key[:160],
                document_version=max(1, int(manifest_version)),
                block_order=order,
                content_hash=_content_hash(content),
                content=content,
                keywords=tuple(
                    value[:500]
                    for value in item.keywords
                    if str(value).strip()
                ),
                locator={
                    **(item.locator or {}),
                    "pricing_agent_record_id": item.record_id,
                },
            )
        )
    return blocks


def search_pricing_hybrid(
    *,
    documents: Sequence[PricingHybridDocument],
    scope_key: str,
    manifest_version: int,
    query: str,
    top_k: int | None = None,
    client: HttpTenderHybridSearchClient | None = None,
    enabled: bool | None = None,
) -> PricingHybridResult:
    feature_enabled = (
        bool(settings.feature_pricing_agent_hybrid_search)
        if enabled is None
        else bool(enabled)
    )
    if not feature_enabled:
        return PricingHybridResult(
            hits=(),
            status="disabled",
            indexed_shard_count=0,
            searched_shard_count=0,
        )
    if not documents or not query.strip():
        return PricingHybridResult(
            hits=(),
            status="no_documents",
            indexed_shard_count=0,
            searched_shard_count=0,
        )
    if client is None and not hybrid_search_enabled():
        return PricingHybridResult(
            hits=(),
            status="service_disabled",
            indexed_shard_count=0,
            searched_shard_count=0,
            issue={
                "code": "PRICING_AGENT_HYBRID_SERVICE_DISABLED",
                "message": "混合检索服务未启用，已降级为本地关键词候选。",
            },
        )

    try:
        active_client = client or configured_hybrid_client()
    except (TenderHybridSearchError, ValueError) as exc:
        return PricingHybridResult(
            hits=(),
            status="unavailable",
            indexed_shard_count=0,
            searched_shard_count=0,
            issue={
                "code": "PRICING_AGENT_HYBRID_CLIENT_UNAVAILABLE",
                "message": str(exc),
            },
        )

    shard_size = max(
        1,
        min(int(settings.pricing_agent_hybrid_shard_rows or 5000), 10000),
    )
    requested_top_k = max(
        1,
        min(int(top_k or settings.pricing_agent_hybrid_top_k or 20), 20),
    )
    all_hits: dict[str, PricingHybridHit] = {}
    indexed_shards = 0
    searched_shards = 0
    issues: list[str] = []

    for shard_index, start in enumerate(range(0, len(documents), shard_size)):
        shard = list(documents[start : start + shard_size])
        case_id = _scope_uuid(scope_key, shard_index)
        manifest_hash = _manifest_hash(shard)
        try:
            try:
                hits = active_client.search(
                    case_id=case_id,
                    manifest_version=max(1, int(manifest_version)),
                    manifest_hash=manifest_hash,
                    query=query[:500],
                    top_k=requested_top_k,
                    search_mode="hybrid",
                )
            except TenderHybridIndexStale:
                active_client.reindex(
                    case_id=case_id,
                    manifest_version=max(1, int(manifest_version)),
                    manifest_hash=manifest_hash,
                    index_schema_version=_INDEX_SCHEMA_VERSION,
                    blocks=_index_blocks(
                        shard,
                        case_id=case_id,
                        manifest_version=manifest_version,
                    ),
                )
                indexed_shards += 1
                hits = active_client.search(
                    case_id=case_id,
                    manifest_version=max(1, int(manifest_version)),
                    manifest_hash=manifest_hash,
                    query=query[:500],
                    top_k=requested_top_k,
                    search_mode="hybrid",
                )
            searched_shards += 1
        except TenderHybridSearchError as exc:
            issues.append(str(exc))
            continue

        for hit in hits:
            record_id = str(hit.evidence_id or "").strip()
            if not record_id:
                continue
            candidate = PricingHybridHit(
                record_id=record_id,
                rrf_score=float(hit.rrf_score or 0),
                vector_score=hit.vector_score,
                bm25_score=hit.bm25_score,
            )
            existing = all_hits.get(record_id)
            if existing is None or candidate.rrf_score > existing.rrf_score:
                all_hits[record_id] = candidate

    ordered = sorted(
        all_hits.values(),
        key=lambda item: (-item.rrf_score, item.record_id),
    )[:requested_top_k]
    if searched_shards:
        return PricingHybridResult(
            hits=tuple(ordered),
            status="used",
            indexed_shard_count=indexed_shards,
            searched_shard_count=searched_shards,
            issue=(
                {
                    "code": "PRICING_AGENT_HYBRID_PARTIAL",
                    "message": "部分混合检索分片不可用，其余分片已完成检索。",
                    "failed_shard_count": len(issues),
                }
                if issues
                else None
            ),
        )
    return PricingHybridResult(
        hits=(),
        status="unavailable",
        indexed_shard_count=indexed_shards,
        searched_shard_count=0,
        issue={
            "code": "PRICING_AGENT_HYBRID_UNAVAILABLE",
            "message": "混合检索服务暂不可用，已降级为本地关键词候选。",
            "failed_shard_count": len(issues),
        },
    )
