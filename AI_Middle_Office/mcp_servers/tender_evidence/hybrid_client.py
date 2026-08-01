from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import httpx

from .retrieval_router import RetrievalMode


class TenderHybridSearchError(RuntimeError):
    pass


class TenderHybridSearchUnavailable(TenderHybridSearchError):
    pass


class TenderHybridIndexStale(TenderHybridSearchError):
    pass


@dataclass(frozen=True)
class HybridIndexBlock:
    evidence_id: str
    block_id: str
    document_id: str
    document_key: str
    document_version: int
    block_order: int
    content_hash: str
    content: str
    keywords: tuple[str, ...] = ()
    locator: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["keywords"] = list(self.keywords)
        payload["locator"] = self.locator or {}
        return payload


@dataclass(frozen=True)
class HybridSearchHit:
    evidence_id: str
    block_id: str
    rrf_score: float
    vector_score: float | None = None
    bm25_score: float | None = None


@dataclass(frozen=True)
class HybridReindexResult:
    case_id: str
    manifest_version: int
    manifest_hash: str
    indexed_block_count: int
    idempotent: bool


class TenderHybridSearch(Protocol):
    def reindex(
        self,
        *,
        case_id: str,
        manifest_version: int,
        manifest_hash: str,
        index_schema_version: str,
        blocks: Sequence[HybridIndexBlock],
    ) -> HybridReindexResult: ...

    def search(
        self,
        *,
        case_id: str,
        manifest_version: int,
        manifest_hash: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode = "hybrid",
    ) -> list[HybridSearchHit]: ...


class HttpTenderHybridSearchClient:
    """Internal HTTP client for the isolated tender evidence RAG endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        secret: str,
        timeout_seconds: float = 30.0,
        reindex_timeout_seconds: float | None = None,
    ):
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise TenderHybridSearchError(
                "tender evidence search service URL is required"
            )
        if not secret.strip():
            raise TenderHybridSearchError(
                "tender evidence index service secret is required"
            )
        self.base_url = normalized_url
        self._secret = secret.strip()
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))
        configured_reindex_timeout = (
            timeout_seconds
            if reindex_timeout_seconds is None
            else reindex_timeout_seconds
        )
        self._reindex_timeout_seconds = max(
            1.0,
            min(float(configured_reindex_timeout), 1800.0),
        )

    @property
    def service_url(self) -> str:
        return self.base_url

    def reindex(
        self,
        *,
        case_id: str,
        manifest_version: int,
        manifest_hash: str,
        index_schema_version: str,
        blocks: Sequence[HybridIndexBlock],
    ) -> HybridReindexResult:
        payload = {
            "case_id": case_id,
            "manifest_version": manifest_version,
            "manifest_hash": manifest_hash,
            "index_schema_version": index_schema_version,
            "blocks": [item.to_payload() for item in blocks],
        }
        response = self._post(
            "/api/v1/tender-evidence/reindex",
            payload,
            timeout_seconds=self._reindex_timeout_seconds,
        )
        data = _response_data(response)
        result = HybridReindexResult(
            case_id=str(data.get("case_id") or ""),
            manifest_version=int(data.get("manifest_version") or 0),
            manifest_hash=str(data.get("manifest_hash") or ""),
            indexed_block_count=int(data.get("indexed_block_count") or 0),
            idempotent=bool(data.get("idempotent")),
        )
        _validate_scope(
            result.case_id,
            result.manifest_version,
            result.manifest_hash,
            case_id=case_id,
            manifest_version=manifest_version,
            manifest_hash=manifest_hash,
        )
        if result.indexed_block_count != len(blocks):
            raise TenderHybridSearchError(
                "hybrid index acknowledged an unexpected block count"
            )
        return result

    def search(
        self,
        *,
        case_id: str,
        manifest_version: int,
        manifest_hash: str,
        query: str,
        top_k: int,
        search_mode: RetrievalMode = "hybrid",
    ) -> list[HybridSearchHit]:
        response = self._post(
            "/api/v1/tender-evidence/search",
            {
                "case_id": case_id,
                "manifest_version": manifest_version,
                "manifest_hash": manifest_hash,
                "query": query,
                "top_k": top_k,
                "search_mode": search_mode,
            },
        )
        data = _response_data(response)
        _validate_scope(
            str(data.get("case_id") or ""),
            int(data.get("manifest_version") or 0),
            str(data.get("manifest_hash") or ""),
            case_id=case_id,
            manifest_version=manifest_version,
            manifest_hash=manifest_hash,
        )
        raw_hits = data.get("hits")
        if not isinstance(raw_hits, list):
            raise TenderHybridSearchError(
                "hybrid search response has no valid hits list"
            )
        hits: list[HybridSearchHit] = []
        for item in raw_hits[: max(1, min(int(top_k), 100))]:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            block_id = str(item.get("block_id") or "").strip()
            if not evidence_id or not block_id:
                continue
            hits.append(
                HybridSearchHit(
                    evidence_id=evidence_id,
                    block_id=block_id,
                    rrf_score=float(item.get("rrf_score") or 0.0),
                    vector_score=_optional_float(item.get("vector_score")),
                    bm25_score=_optional_float(item.get("bm25_score")),
                )
            )
        return hits

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=(
                    self._timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={
                        "X-Tender-Index-Secret": self._secret,
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TenderHybridSearchUnavailable(
                "tender evidence search service is unavailable"
            ) from exc
        if response.status_code == 409:
            raise TenderHybridIndexStale(
                "tender evidence hybrid index is stale"
            )
        if response.status_code >= 400:
            raise TenderHybridSearchUnavailable(
                f"tender evidence search service returned HTTP "
                f"{response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TenderHybridSearchError(
                "tender evidence search service returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise TenderHybridSearchError(
                "tender evidence search service returned an invalid payload"
            )
        return payload


def hybrid_search_enabled() -> bool:
    return os.environ.get(
        "TENDER_EVIDENCE_HYBRID_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}


def configured_hybrid_client() -> HttpTenderHybridSearchClient:
    base_url = os.environ.get(
        "TENDER_EVIDENCE_SEARCH_URL",
        os.environ.get("RAG_SERVICE_URL", "http://192.168.88.128:8001"),
    )
    secret = _configured_index_secret()
    timeout = os.environ.get("TENDER_EVIDENCE_SEARCH_TIMEOUT_SECONDS", "30")
    reindex_timeout = os.environ.get(
        "TENDER_EVIDENCE_INDEX_TIMEOUT_SECONDS",
        "900",
    )
    return HttpTenderHybridSearchClient(
        base_url=base_url,
        secret=secret,
        timeout_seconds=float(timeout),
        reindex_timeout_seconds=float(reindex_timeout),
    )


def _configured_index_secret() -> str:
    direct = os.environ.get("TENDER_EVIDENCE_INDEX_SECRET", "").strip()
    if direct:
        return direct
    secret_file = os.environ.get(
        "TENDER_EVIDENCE_INDEX_SECRET_FILE",
        "",
    ).strip()
    if not secret_file:
        return ""
    try:
        return Path(secret_file).expanduser().read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise TenderHybridSearchError(
            "tender evidence index secret file is unavailable"
        ) from exc


def _response_data(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("code") not in {None, 200}:
        raise TenderHybridSearchError(
            "tender evidence search service rejected the request"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TenderHybridSearchError(
            "tender evidence search response has no data object"
        )
    return data


def _validate_scope(
    actual_case_id: str,
    actual_manifest_version: int,
    actual_manifest_hash: str,
    *,
    case_id: str,
    manifest_version: int,
    manifest_hash: str,
) -> None:
    if (
        actual_case_id != case_id
        or actual_manifest_version != manifest_version
        or actual_manifest_hash != manifest_hash
    ):
        raise TenderHybridIndexStale(
            "hybrid index response does not match the active manifest"
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
