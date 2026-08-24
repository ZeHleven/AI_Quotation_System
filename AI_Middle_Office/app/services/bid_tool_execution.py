"""Phase 3F durable Tool Adapter/Executor dispatch control plane.

The dispatcher commits an execution intent before adapter I/O.  This module
ships only deterministic local, read-only adapters; it does not call a model,
OCR, object storage, the public network, or legacy ``bid_intake_*`` services.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidManifestDocument
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidDocumentParseUnit,
)
from app.models.bid_assessment_runtime import BidAnalysisRun, BidAsyncOperation
from app.models.bid_assessment_results import (
    BidFactAssertion,
    BidFactCoverage,
    BidFactEvidenceLink,
    BidResolvedFact,
    BidResolvedFactHead,
)
from app.models.bid_assessment_tooling import BidToolInvocation
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.services.bid_assessment_eventing import (
    append_audit_log,
    as_utc,
    canonical_hash,
    canonical_json,
)
from app.services.bid_evidence_retrieval_index import (
    LEGACY_RETRIEVAL_PROFILE_VERSION,
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
)
from app.services.bid_evidence_semantic_index import (
    DISABLED_SEMANTIC_PROFILE_VERSION,
    RQ2A_SEMANTIC_PROFILE_VERSION,
)
from app.services.bid_field_aware_lexical import (
    FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
)
from app.services.bid_hybrid_candidate_fusion import (
    DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
)
from app.services.bid_lightweight_reranker import (
    DISABLED_RERANK_PROFILE_VERSION,
    RQ2C_RERANK_PROFILE_VERSION,
    BidLightweightRerankerError,
    configured_bid_reranker_provider,
)
from app.services.bid_query_optimizer import (
    LEGACY_QUERY_PLANNER_PROFILE_VERSION,
    QUERY_OPTIMIZER_PROFILE_VERSION,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_semantic_vector_provider import (
    BidSemanticProviderError,
    configured_bid_semantic_provider,
)
from app.services.bid_tool_context import (
    BidToolContextError,
    _scope_token,
    _scope_token_hash,
    defer_tool_invocation,
    settle_async_tool_operation,
)
from app.services.bid_task_runtime import TaskLeaseClaim
from mcp_servers.bid_assessment_evidence import (
    BidEvidenceMcpError,
    BidEvidenceMcpScope,
    BidEvidenceMcpService,
)


TOOL_EXECUTOR_PRODUCER = "bid-tool-executor-v1"
ACTIVE_DISPATCH_STATES = frozenset(
    {"queued", "leased", "sending", "awaiting_receipt", "retry_wait"}
)
LEASED_DISPATCH_STATES = frozenset({"leased", "sending"})

logger = logging.getLogger(__name__)


class BidToolExecutionError(RuntimeError):
    code = "BID_TOOL_EXECUTION_ERROR"


class BidToolAdapterUnavailable(BidToolExecutionError):
    code = "BID_TOOL_ADAPTER_UNAVAILABLE"


class BidToolAdapterRetryable(BidToolExecutionError):
    code = "BID_TOOL_ADAPTER_RETRYABLE"


class BidToolDispatchConflict(BidToolExecutionError):
    code = "BID_TOOL_DISPATCH_CONFLICT"


class BidToolDispatchFenceLost(BidToolExecutionError):
    code = "BID_TOOL_DISPATCH_FENCE_LOST"


class BidToolDispatchOutcomeUncertain(BidToolExecutionError):
    code = "BID_TOOL_DISPATCH_OUTCOME_UNCERTAIN"


@dataclass(frozen=True)
class ToolAdapterResult:
    status: str
    summary: str
    data: Any
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    elapsed_ms: int = 0
    returned_items: int = 0
    truncated: bool = False
    external_object_ref: str | None = None
    provider_receipt_id: str | None = None
    actual_cost_microunits: int = 0


@dataclass(frozen=True)
class ToolAdapterSpec:
    adapter_name: str
    adapter_version: str
    adapter_mode: str
    replay_policy: str
    max_attempts: int
    reserved_cost_microunits: int


@dataclass(frozen=True)
class ToolDispatchReceipt:
    dispatch_id: str
    invocation_id: str
    operation_id: str
    status: str
    duplicate: bool


@dataclass(frozen=True)
class ToolDispatchClaim:
    dispatch_id: str
    dispatch_attempt_id: str
    invocation_id: str
    operation_id: str
    worker_id: str
    fencing_token: int
    lease_until: datetime
    adapter_name: str
    adapter_version: str
    adapter_mode: str
    replay_policy: str
    provider_request_id: str
    envelope: dict[str, Any]
    scope_token: str


@dataclass(frozen=True)
class ToolDispatchBatchResult:
    claimed: int
    succeeded: int
    retry_wait: int
    failed: int
    uncertain: int


@dataclass(frozen=True)
class ToolDispatchMaintenanceResult:
    scanned: int
    recovered: int
    uncertain: int
    failed: int


class ToolAdapter(Protocol):
    def execute(
        self,
        db: Session,
        *,
        envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ToolAdapterResult: ...


LOCAL_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "documents.outline": ToolAdapterSpec(
        adapter_name="bid-local-documents-outline",
        adapter_version="v1",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-search",
        adapter_version="v1",
        # The transport is MCP, but execution remains inside the existing
        # Phase 3F local-readonly trust boundary.  Do not invent a third
        # persistence enum for an in-process transport detail.
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
    "evidence.read": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-read",
        adapter_version="v1",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
    "facts.query": ToolAdapterSpec(
        adapter_name="bid-local-facts-query",
        adapter_version="v1",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

ROLE_AWARE_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-role-aware-search",
        adapter_version="v2-role-aware",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
    "evidence.read": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-role-aware-read",
        adapter_version="v2-role-aware",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

RQ1C_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-rq1c-search",
        adapter_version="v3-rq1c-query-optimizer",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

RQ1D_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-rq1d-search",
        adapter_version="v4-field-aware-lexical",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

RQ2A_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-rq2a-search",
        adapter_version="v5-child-semantic-recall",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

RQ2B_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-rq2b-search",
        adapter_version="v6-bm25f-semantic-fusion",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}

RQ2C_EVIDENCE_ADAPTER_SPECS: dict[str, ToolAdapterSpec] = {
    "evidence.search": ToolAdapterSpec(
        adapter_name="bid-evidence-mcp-rq2c-search",
        adapter_version="v7-bce-anchor-preserving-rerank",
        adapter_mode="local_readonly",
        replay_policy="safe_idempotent",
        max_attempts=3,
        reserved_cost_microunits=0,
    ),
}


def _adapter_spec(tool_name: str) -> ToolAdapterSpec | None:
    from app.core.config import settings

    if (
        tool_name in RQ2C_EVIDENCE_ADAPTER_SPECS
        and getattr(
            settings,
            "feature_bid_assessment_rq2c_lightweight_rerank",
            False,
        )
    ):
        return RQ2C_EVIDENCE_ADAPTER_SPECS[tool_name]
    if (
        tool_name in RQ2B_EVIDENCE_ADAPTER_SPECS
        and getattr(
            settings,
            "feature_bid_assessment_rq2b_candidate_fusion",
            False,
        )
    ):
        return RQ2B_EVIDENCE_ADAPTER_SPECS[tool_name]
    if (
        tool_name in RQ2A_EVIDENCE_ADAPTER_SPECS
        and getattr(
            settings,
            "feature_bid_assessment_rq2a_semantic_recall",
            False,
        )
    ):
        return RQ2A_EVIDENCE_ADAPTER_SPECS[tool_name]
    if (
        tool_name in RQ1D_EVIDENCE_ADAPTER_SPECS
        and getattr(
            settings,
            "feature_bid_assessment_rq1d_field_aware_lexical",
            False,
        )
    ):
        return RQ1D_EVIDENCE_ADAPTER_SPECS[tool_name]
    if (
        tool_name in RQ1C_EVIDENCE_ADAPTER_SPECS
        and getattr(
            settings,
            "feature_bid_assessment_rq1c_query_optimizer",
            False,
        )
    ):
        return RQ1C_EVIDENCE_ADAPTER_SPECS[tool_name]
    if (
        tool_name in ROLE_AWARE_EVIDENCE_ADAPTER_SPECS
        and settings.feature_bid_assessment_pdf_c3_role_aware_retrieval
    ):
        return ROLE_AWARE_EVIDENCE_ADAPTER_SPECS[tool_name]
    return LOCAL_ADAPTER_SPECS.get(tool_name)


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise BidToolDispatchConflict("BID_TOOL_ADAPTER_CURSOR_INVALID") from exc
    if value < 0:
        raise BidToolDispatchConflict("BID_TOOL_ADAPTER_CURSOR_INVALID")
    return value


class LocalDocumentsOutlineAdapter:
    """Read the authoritative current ParseHead without raw-file I/O."""

    def execute(
        self,
        db: Session,
        *,
        envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ToolAdapterResult:
        arguments = dict(envelope["arguments"])
        document_version_id = str(arguments["document_version_id"])
        membership = (
            db.query(BidManifestDocument.document_version_id)
            .filter(
                BidManifestDocument.manifest_id == str(envelope["manifest_id"]),
                BidManifestDocument.document_version_id == document_version_id,
            )
            .one_or_none()
        )
        head_row = (
            db.query(BidDocumentParseHead, BidDocumentParseRun)
            .join(
                BidDocumentParseRun,
                BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
            )
            .filter(
                BidDocumentParseHead.document_version_id == document_version_id,
                BidDocumentParseRun.document_version_id == document_version_id,
                BidDocumentParseRun.status.in_(("succeeded", "partial")),
            )
            .one_or_none()
        )
        if membership is None or head_row is None:
            return ToolAdapterResult(
                status="no_result",
                summary="No authoritative parsed outline is available",
                data={"items": [], "cursor": None, "has_more": False},
            )
        _head, parse_run = head_row
        max_depth = int(arguments.get("max_depth", 4))
        rows = (
            db.query(BidDocumentParseUnit)
            .filter(
                BidDocumentParseUnit.run_id == parse_run.id,
                BidDocumentParseUnit.status.in_(("succeeded", "partial")),
            )
            .order_by(BidDocumentParseUnit.ordinal.asc(), BidDocumentParseUnit.id.asc())
            .all()
        )
        outline = []
        for row in rows:
            path = list(row.section_path_json or [])[:max_depth]
            if not path and row.unit_type == "document":
                continue
            outline.append(
                {
                    "unit_id": str(row.id),
                    "unit_type": str(row.unit_type),
                    "unit_key": str(row.unit_key),
                    "section_path": path,
                    "page_no": int(row.page_no) if row.page_no is not None else None,
                    "sheet_name": str(row.sheet_name) if row.sheet_name else None,
                }
            )
        offset = _cursor_offset(arguments.get("cursor"))
        page_size = 20
        items = outline[offset : offset + page_size]
        next_offset = offset + len(items)
        next_cursor = None
        if next_offset < len(outline):
            next_cursor = base64.urlsafe_b64encode(str(next_offset).encode()).decode()
        return ToolAdapterResult(
            status="ok" if items else "no_result",
            summary=(
                "Authoritative parsed document outline"
                if items
                else "No outline sections were found"
            ),
            data={
                "document_version_id": document_version_id,
                "parse_run_id": str(parse_run.id),
                "items": items,
                "cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
            returned_items=len(items),
            provider_receipt_id=f"local:{provider_request_id}",
        )


class BidEvidenceMcpAdapter:
    """Governed in-process transport for the read-only Evidence MCP service."""

    def __init__(
        self,
        operation: str,
        *,
        retrieval_profile_version: str = LEGACY_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version: str = LEGACY_QUERY_PLANNER_PROFILE_VERSION,
        lexical_search_profile_version: str = LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
        semantic_search_profile_version: str = DISABLED_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version: str = (
            DISABLED_CANDIDATE_FUSION_PROFILE_VERSION
        ),
        rerank_profile_version: str = DISABLED_RERANK_PROFILE_VERSION,
    ):
        self._operation = operation
        self._retrieval_profile_version = retrieval_profile_version
        self._query_optimizer_profile_version = query_optimizer_profile_version
        self._lexical_search_profile_version = lexical_search_profile_version
        self._semantic_search_profile_version = semantic_search_profile_version
        self._candidate_fusion_profile_version = candidate_fusion_profile_version
        self._rerank_profile_version = rerank_profile_version

    @staticmethod
    def _compact_search_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep model-useful candidates inside the governed inline-result cap.

        Search results are navigation hints and are never citable.  Full Atom
        text and locators remain available only through ``evidence.read``.
        """

        raw_hits = list(payload.get("hits") or [])
        compact_hits: list[dict[str, Any]] = []
        for hit in raw_hits[:8]:
            locator = dict(hit.get("locator") or {})
            section_path = [
                str(value)[:80]
                for value in list(locator.get("section_path") or [])[:6]
            ]
            compact_locator = {
                key: locator[key]
                for key in (
                    "page_no",
                    "sheet_name",
                    "row_range",
                    "cell_range",
                    "source_location",
                )
                if locator.get(key) is not None
            }
            if section_path:
                compact_locator["section_path"] = section_path
            compact_hits.append(
                {
                    key: hit[key]
                    for key in (
                        "evidence_id",
                        "fragment_role",
                        "is_citable",
                        "document_version_id",
                        "document_role",
                        "section_parent_id",
                        "score",
                        "fusion_score",
                        "fusion_rank",
                        "lexical_rank",
                        "semantic_rank",
                        "rerank_score",
                        "context_read",
                    )
                    if hit.get(key) is not None
                }
                | {
                    "locator": compact_locator,
                    "excerpt": str(hit.get("excerpt") or "")[:240],
                }
            )
        query_plan = dict(payload.get("query_plan") or {})
        warnings = sorted(
            set(str(value) for value in payload.get("warnings") or [])
            | {"MCP_TRANSPORT_COMPACTED"}
            | ({"RESULT_TRUNCATED"} if len(raw_hits) > 8 else set())
        )
        compact_payload = {
            "contract": payload.get("contract"),
            "status": payload.get("status"),
            "run_id": payload.get("run_id"),
            "manifest_id": payload.get("manifest_id"),
            "retrieval_mode": payload.get("retrieval_mode"),
            "query": str(query_plan.get("original_query") or "")[:500],
            "query_plan_hash": query_plan.get("plan_hash"),
            "source_result_hash": payload.get("result_hash"),
            "hits": compact_hits,
            "warnings": warnings,
            "transport_profile": "bid-evidence-mcp-inline-search-v1",
        }
        # The Result Store intentionally rejects oversized inline payloads.  A
        # final byte guard makes the adapter resilient to unusually long UTF-8
        # identifiers while retaining the highest-ranked navigation hints.
        while (
            len(canonical_json(compact_payload).encode("utf-8")) > 24 * 1024
            and len(compact_payload["hits"]) > 1
        ):
            compact_payload["hits"].pop()
            if "RESULT_TRUNCATED" not in compact_payload["warnings"]:
                compact_payload["warnings"].append("RESULT_TRUNCATED")
                compact_payload["warnings"].sort()
        return compact_payload

    @staticmethod
    def _compact_read_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist only the model-facing Atom view of a governed Read result."""

        raw_items = list(payload.get("items") or [])
        compact_items: list[dict[str, Any]] = []
        for item in raw_items[:12]:
            locator = dict(item.get("locator") or {})
            compact_locator = {
                key: locator[key]
                for key in (
                    "page_no",
                    "sheet_name",
                    "row_range",
                    "cell_range",
                    "block_type",
                )
                if locator.get(key) is not None
            }
            section_path = [
                str(value)[:60]
                for value in list(locator.get("section_path") or [])[:4]
            ]
            if section_path:
                compact_locator["section_path"] = section_path
            compact_items.append(
                {
                    key: item[key]
                    for key in (
                        "evidence_id",
                        "fragment_role",
                        "is_citable",
                        "context_read",
                        "document_version_id",
                        "document_role",
                        "source_child_id",
                        "section_parent_id",
                        "text_hash",
                    )
                    if item.get(key) is not None
                }
                | {
                    "locator": compact_locator,
                    "text": str(item.get("text") or "")[:180],
                }
            )
        warnings = sorted(
            set(str(value) for value in payload.get("warnings") or [])
            | {"MCP_TRANSPORT_COMPACTED"}
            | ({"RESULT_TRUNCATED"} if len(raw_items) > 12 else set())
        )
        compact_payload = {
            "contract": payload.get("contract"),
            "status": payload.get("status"),
            "run_id": payload.get("run_id"),
            "manifest_id": payload.get("manifest_id"),
            "retrieval_profile_version": payload.get("retrieval_profile_version"),
            "expansion": payload.get("expansion"),
            "source_result_hash": payload.get("result_hash"),
            "items": compact_items,
            "warnings": warnings,
            "transport_profile": "bid-evidence-mcp-inline-read-v1",
        }
        while (
            len(canonical_json(compact_payload).encode("utf-8")) > 24 * 1024
            and len(compact_payload["items"]) > 1
        ):
            compact_payload["items"].pop()
            if "RESULT_TRUNCATED" not in compact_payload["warnings"]:
                compact_payload["warnings"].append("RESULT_TRUNCATED")
                compact_payload["warnings"].sort()
        return compact_payload

    def execute(
        self,
        db: Session,
        *,
        envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ToolAdapterResult:
        from app.core.config import settings

        if not settings.feature_bid_assessment_phase4_evidence_mcp:
            raise BidToolDispatchConflict("BID_EVIDENCE_MCP_DISABLED")
        if (
            self._retrieval_profile_version
            == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
            and not settings.feature_bid_assessment_pdf_c3_role_aware_retrieval
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_RETRIEVAL_PROFILE_DISABLED")
        if (
            self._query_optimizer_profile_version
            == QUERY_OPTIMIZER_PROFILE_VERSION
            and not settings.feature_bid_assessment_rq1c_query_optimizer
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_QUERY_PROFILE_DISABLED")
        if (
            self._lexical_search_profile_version
            == FIELD_AWARE_LEXICAL_PROFILE_VERSION
            and not getattr(
                settings,
                "feature_bid_assessment_rq1d_field_aware_lexical",
                False,
            )
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_LEXICAL_PROFILE_DISABLED")
        if (
            self._semantic_search_profile_version
            == RQ2A_SEMANTIC_PROFILE_VERSION
            and not getattr(
                settings,
                "feature_bid_assessment_rq2a_semantic_recall",
                False,
            )
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_SEMANTIC_PROFILE_DISABLED")
        if (
            self._candidate_fusion_profile_version
            == RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
            and not getattr(
                settings,
                "feature_bid_assessment_rq2b_candidate_fusion",
                False,
            )
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_FUSION_PROFILE_DISABLED")
        if (
            self._rerank_profile_version == RQ2C_RERANK_PROFILE_VERSION
            and not getattr(
                settings,
                "feature_bid_assessment_rq2c_lightweight_rerank",
                False,
            )
        ):
            raise BidToolDispatchConflict("BID_EVIDENCE_RERANK_PROFILE_DISABLED")
        if (
            self._retrieval_profile_version
            == LEGACY_RETRIEVAL_PROFILE_VERSION
            and settings.feature_bid_assessment_pdf_c3_role_aware_retrieval
        ):
            # A Dispatch freezes its adapter version when it is enqueued.  Once
            # C3 is active, an older v1 Dispatch must not bypass the role-aware
            # index and search Parent/Child/Atom rows indiscriminately.
            raise BidToolDispatchConflict("BID_EVIDENCE_RETRIEVAL_PROFILE_DISABLED")
        semantic_provider = None
        if self._semantic_search_profile_version == RQ2A_SEMANTIC_PROFILE_VERSION:
            try:
                semantic_provider = configured_bid_semantic_provider(settings)
            except BidSemanticProviderError as exc:
                raise BidToolAdapterRetryable(str(exc) or exc.code) from exc
        reranker_provider = None
        if self._rerank_profile_version == RQ2C_RERANK_PROFILE_VERSION:
            try:
                reranker_provider = configured_bid_reranker_provider(settings)
            except BidLightweightRerankerError as exc:
                error_code = str(exc)[:100] or exc.code
                if getattr(exc, "retryable", False):
                    raise BidToolAdapterRetryable(error_code) from exc
                raise BidToolDispatchConflict(error_code) from exc
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=str(envelope["assessment_id"]),
                run_id=str(envelope["run_id"]),
                manifest_id=str(envelope["manifest_id"]),
            ),
            retrieval_profile_version=self._retrieval_profile_version,
            query_optimizer_profile_version=self._query_optimizer_profile_version,
            lexical_search_profile_version=self._lexical_search_profile_version,
            semantic_search_profile_version=self._semantic_search_profile_version,
            candidate_fusion_profile_version=(
                self._candidate_fusion_profile_version
            ),
            semantic_provider=semantic_provider,
            rerank_profile_version=self._rerank_profile_version,
            reranker_provider=reranker_provider,
        )
        try:
            payload = (
                service.search(dict(envelope["arguments"]))
                if self._operation == "search"
                else service.read(dict(envelope["arguments"]))
            )
        except BidEvidenceMcpError as exc:
            error_code = str(exc)[:100] or exc.code
            if error_code == "BID_EVIDENCE_RETRIEVAL_INDEX_NOT_READY":
                raise BidToolAdapterRetryable(error_code) from exc
            if error_code in {
                "BID_EVIDENCE_SEMANTIC_INDEX_NOT_READY",
                "BID_SEMANTIC_PROVIDER_UNAVAILABLE",
                "BID_SEMANTIC_EMBEDDING_MODEL_UNAVAILABLE",
                "BID_SEMANTIC_EMBEDDING_EXECUTION_FAILED",
                "BID_SEMANTIC_VECTOR_BACKEND_UNAVAILABLE",
                "BID_SEMANTIC_VECTOR_UPSERT_FAILED",
                "BID_SEMANTIC_VECTOR_SEARCH_FAILED",
                "BID_RERANK_PROVIDER_UNAVAILABLE",
                "BID_RERANK_MODEL_UNAVAILABLE",
                "BID_RERANK_EXECUTION_FAILED",
            }:
                raise BidToolAdapterRetryable(error_code) from exc
            raise BidToolDispatchConflict(error_code) from exc
        if self._operation == "search":
            payload = self._compact_search_payload(payload)
        else:
            payload = self._compact_read_payload(payload)
        items = list(payload.get("hits") or payload.get("items") or [])
        evidence_refs = tuple(
            sorted(
                set(
                    str(item["evidence_id"])
                    for item in items
                    if item.get("evidence_id")
                )
            )
        )
        return ToolAdapterResult(
            status=str(payload.get("status") or ("ok" if items else "no_result")),
            summary=(
                "Governed evidence candidates from current Manifest"
                if self._operation == "search"
                else "Governed bounded evidence context from current Manifest"
            ),
            data=payload,
            evidence_refs=evidence_refs,
            warnings=tuple(str(value) for value in payload.get("warnings") or []),
            returned_items=len(items),
            truncated="RESULT_TRUNCATED" in set(payload.get("warnings") or []),
            provider_receipt_id=f"mcp-local:{provider_request_id}",
        )


class BidFactsQueryAdapter:
    """Read only the current Run's governed resolved-fact heads."""

    def execute(
        self,
        db: Session,
        *,
        envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ToolAdapterResult:
        arguments = dict(envelope["arguments"])
        run_id = str(envelope["run_id"])
        requested_slots = tuple(
            sorted({str(value) for value in arguments.get("fact_slots") or []})
        )
        requested_statuses = tuple(
            sorted({str(value) for value in arguments.get("statuses") or []})
        )
        requested_coverages = tuple(
            sorted({str(value) for value in arguments.get("coverage_statuses") or []})
        )
        query = (
            db.query(BidResolvedFactHead, BidResolvedFact)
            .join(
                BidResolvedFact,
                (BidResolvedFact.run_id == BidResolvedFactHead.run_id)
                & (BidResolvedFact.id == BidResolvedFactHead.resolved_fact_id),
            )
            .filter(BidResolvedFactHead.run_id == run_id)
        )
        if requested_slots:
            query = query.filter(BidResolvedFact.fact_slot.in_(requested_slots))
        if requested_statuses:
            query = query.filter(BidResolvedFact.status.in_(requested_statuses))
        rows = query.order_by(
            BidResolvedFact.fact_slot.asc(),
            BidResolvedFact.scope_type.asc(),
            BidResolvedFact.scope_id.asc(),
        ).all()
        coverages = {
            str(row.fact_slot): row
            for row in db.query(BidFactCoverage)
            .filter(BidFactCoverage.run_id == run_id)
            .all()
        }
        include_assertions = bool(arguments.get("include_assertions", False))
        items: list[dict[str, Any]] = []
        for _head, fact in rows:
            coverage = coverages.get(str(fact.fact_slot))
            coverage_status = str(coverage.status) if coverage is not None else "not_assessed"
            if requested_coverages and coverage_status not in requested_coverages:
                continue
            item: dict[str, Any] = {
                "fact_id": str(fact.id),
                "fact_slot": str(fact.fact_slot),
                "scope": {"type": str(fact.scope_type), "id": str(fact.scope_id)},
                "status": str(fact.status),
                "value_type": str(fact.value_type) if fact.value_type else None,
                "value": fact.value_json,
                "coverage": {
                    "status": coverage_status,
                    "assertion_count": int(coverage.assertion_count) if coverage else 0,
                    "reason_codes": list(coverage.reason_codes_json or []) if coverage else [],
                },
                "reason_codes": list(fact.reason_codes_json or []),
            }
            if include_assertions:
                assertions = (
                    db.query(BidFactAssertion)
                    .filter(
                        BidFactAssertion.run_id == run_id,
                        BidFactAssertion.id.in_(
                            tuple(str(value) for value in fact.source_assertion_ids_json or [])
                        ),
                        BidFactAssertion.status == "accepted",
                    )
                    .order_by(BidFactAssertion.id.asc())
                    .all()
                    if fact.source_assertion_ids_json
                    else []
                )
                assertion_ids = tuple(str(row.id) for row in assertions)
                linked_evidence: dict[str, list[str]] = {}
                if assertion_ids:
                    for link in (
                        db.query(BidFactEvidenceLink)
                        .filter(
                            BidFactEvidenceLink.assertion_id.in_(assertion_ids),
                            BidFactEvidenceLink.context_read.is_(True),
                        )
                        .order_by(
                            BidFactEvidenceLink.assertion_id.asc(),
                            BidFactEvidenceLink.evidence_fragment_id.asc(),
                        )
                        .all()
                    ):
                        linked_evidence.setdefault(str(link.assertion_id), []).append(
                            str(link.evidence_fragment_id)
                        )
                item["assertions"] = [
                    {
                        "assertion_id": str(row.id),
                        "source_type": str(row.source_type),
                        "confidence": str(row.confidence),
                        "evidence_refs": linked_evidence.get(str(row.id), []),
                    }
                    for row in assertions
                ]
            items.append(item)
        offset = _cursor_offset(arguments.get("cursor"))
        page_size = 30
        page = items[offset : offset + page_size]
        next_offset = offset + len(page)
        next_cursor = (
            base64.urlsafe_b64encode(str(next_offset).encode()).decode()
            if next_offset < len(items)
            else None
        )
        return ToolAdapterResult(
            status="ok" if page else "no_result",
            summary="Governed current resolved facts for this Run",
            data={"items": page, "cursor": next_cursor, "has_more": next_cursor is not None},
            returned_items=len(page),
            provider_receipt_id=f"local:{provider_request_id}",
        )


LOCAL_ADAPTERS: dict[str, ToolAdapter] = {
    "bid-local-documents-outline": LocalDocumentsOutlineAdapter(),
    "bid-evidence-mcp-search": BidEvidenceMcpAdapter("search"),
    "bid-evidence-mcp-read": BidEvidenceMcpAdapter("read"),
    "bid-evidence-mcp-role-aware-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-role-aware-read": BidEvidenceMcpAdapter(
        "read",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-rq1c-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-rq1d-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-rq2a-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-rq2b-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
    ),
    "bid-evidence-mcp-rq2c-search": BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
        rerank_profile_version=RQ2C_RERANK_PROFILE_VERSION,
    ),
    "bid-local-facts-query": BidFactsQueryAdapter(),
}


def enqueue_tool_dispatch(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    invocation_id: str,
    checkpoint_id: str,
    scope_token: str,
    scope_signing_key: str,
    timeout_seconds: int = 300,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ToolDispatchReceipt:
    """Persist async continuation and a durable dispatch intent atomically."""

    if len(str(scope_signing_key or "").strip()) < 32:
        raise BidToolDispatchConflict("BID_TOOL_SCOPE_SIGNING_KEY_INVALID")
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    invocation = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.id == str(invocation_id))
        .with_for_update()
        .one_or_none()
    )
    if invocation is None or str(invocation.status) not in {"accepted", "pending"}:
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_INVOCATION_INVALID")
    expected_token = _scope_token(
        signing_key=scope_signing_key,
        invocation_id=str(invocation.id),
        request_hash=str(invocation.request_hash),
    )
    if not hmac.compare_digest(str(scope_token), expected_token) or not hmac.compare_digest(
        str(invocation.scope_token_hash), _scope_token_hash(scope_token)
    ):
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_SCOPE_TOKEN_INVALID")
    existing = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.invocation_id == invocation.id)
        .one_or_none()
    )
    if existing is not None:
        return ToolDispatchReceipt(
            dispatch_id=str(existing.id),
            invocation_id=str(existing.invocation_id),
            operation_id=str(existing.async_operation_id),
            status=str(existing.status),
            duplicate=True,
        )
    spec = _adapter_spec(str(invocation.tool_name))
    if spec is None:
        raise BidToolAdapterUnavailable(
            f"BID_TOOL_ADAPTER_UNAVAILABLE:{invocation.tool_name}"
        )
    pending = defer_tool_invocation(
        db,
        claim,
        invocation_id=str(invocation.id),
        checkpoint_id=str(checkpoint_id),
        timeout_seconds=timeout_seconds,
        request_id=request_id,
        now=current_time,
    )
    operation_id = str(pending["operation_id"])
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == invocation.run_id)
        .one()
    )
    envelope = {
        "schema_version": "bid-tool-dispatch-envelope-v1",
        "invocation_id": str(invocation.id),
        "operation_id": operation_id,
        "assessment_id": str(invocation.assessment_id),
        "run_id": str(invocation.run_id),
        "task_id": str(invocation.task_id),
        "task_attempt_id": str(invocation.task_attempt_id),
        "context_manifest_id": str(invocation.context_manifest_id),
        "manifest_id": str(run.manifest_id),
        "tool_registry_version_id": str(invocation.tool_registry_version_id),
        "tool_name": str(invocation.tool_name),
        "arguments": dict(invocation.arguments_json or {}),
        "request_hash": str(invocation.request_hash),
        "provider_request_id": f"bid-tool:{invocation.id}",
    }
    # The run-bound manifest is server authority; never accept it from arguments.
    envelope_hash = canonical_hash(envelope)
    dispatch = BidToolDispatch(
        id=str(uuid.uuid4()),
        invocation_id=str(invocation.id),
        async_operation_id=operation_id,
        task_id=str(invocation.task_id),
        task_attempt_id=str(invocation.task_attempt_id),
        adapter_name=spec.adapter_name,
        adapter_version=spec.adapter_version,
        adapter_mode=spec.adapter_mode,
        replay_policy=spec.replay_policy,
        dispatch_key=f"tool-dispatch:{invocation.id}",
        envelope_json=envelope,
        envelope_hash=envelope_hash,
        scope_token_hash=str(invocation.scope_token_hash),
        status="queued",
        attempt_count=0,
        max_attempts=spec.max_attempts,
        fencing_token=0,
        available_at=current_time,
        provider_request_id=str(envelope["provider_request_id"]),
        reserved_cost_microunits=spec.reserved_cost_microunits,
        actual_cost_microunits=0,
        row_version=1,
    )
    db.add(dispatch)
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="tool.dispatch.enqueue",
        entity_type="tool_dispatch",
        entity_id=str(dispatch.id),
        assessment_id=str(invocation.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"tool-dispatch:{dispatch.id}"),
        after={
            "invocation_id": str(invocation.id),
            "operation_id": operation_id,
            "adapter_name": spec.adapter_name,
            "adapter_version": spec.adapter_version,
            "replay_policy": spec.replay_policy,
            "envelope_hash": envelope_hash,
        },
        occurred_at=current_time,
    )
    db.flush()
    return ToolDispatchReceipt(
        dispatch_id=str(dispatch.id),
        invocation_id=str(invocation.id),
        operation_id=operation_id,
        status="queued",
        duplicate=False,
    )


def claim_next_tool_dispatch(
    db: Session,
    *,
    worker_id: str,
    scope_signing_key: str,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> ToolDispatchClaim | None:
    if len(str(scope_signing_key or "").strip()) < 32:
        raise BidToolDispatchConflict("BID_TOOL_SCOPE_SIGNING_KEY_INVALID")
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    normalized_worker = str(worker_id or "")[:128]
    if not normalized_worker:
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_WORKER_REQUIRED")
    dispatch = (
        db.query(BidToolDispatch)
        .filter(
            BidToolDispatch.status.in_(("queued", "retry_wait")),
            BidToolDispatch.available_at <= current_time,
        )
        .order_by(BidToolDispatch.available_at.asc(), BidToolDispatch.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if dispatch is None:
        return None
    invocation = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.id == dispatch.invocation_id)
        .with_for_update()
        .one()
    )
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == dispatch.async_operation_id)
        .with_for_update()
        .one()
    )
    run = db.query(BidAnalysisRun).filter(BidAnalysisRun.id == invocation.run_id).one()
    expected_envelope = {
        "invocation_id": str(invocation.id),
        "operation_id": str(operation.id),
        "assessment_id": str(invocation.assessment_id),
        "run_id": str(invocation.run_id),
        "task_id": str(invocation.task_id),
        "task_attempt_id": str(invocation.task_attempt_id),
        "context_manifest_id": str(invocation.context_manifest_id),
        "tool_registry_version_id": str(invocation.tool_registry_version_id),
        "tool_name": str(invocation.tool_name),
        "request_hash": str(invocation.request_hash),
        "provider_request_id": str(dispatch.provider_request_id),
        "manifest_id": str(run.manifest_id),
    }
    if str(invocation.status) != "pending" or str(operation.status) not in {
        "created",
        "submitted",
        "running",
    }:
        dispatch.status = "cancelled"
        dispatch.completed_at = current_time
        dispatch.last_error_code = "BID_TOOL_DISPATCH_STALE"
        dispatch.row_version = int(dispatch.row_version) + 1
        db.flush()
        return None
    envelope = dict(dispatch.envelope_json or {})
    if canonical_hash(envelope) != str(dispatch.envelope_hash):
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_ENVELOPE_HASH_MISMATCH")
    if (
        str(envelope.get("schema_version")) != "bid-tool-dispatch-envelope-v1"
        or any(
            str(envelope.get(key, "")) != value
            for key, value in expected_envelope.items()
        )
        or canonical_hash(envelope.get("arguments", {}))
        != canonical_hash(invocation.arguments_json or {})
    ):
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_ENVELOPE_LINEAGE_MISMATCH")
    token = _scope_token(
        signing_key=scope_signing_key,
        invocation_id=str(invocation.id),
        request_hash=str(invocation.request_hash),
    )
    if not hmac.compare_digest(
        str(dispatch.scope_token_hash), _scope_token_hash(token)
    ):
        raise BidToolDispatchConflict("BID_TOOL_DISPATCH_SCOPE_HASH_MISMATCH")
    attempt_no = int(dispatch.attempt_count) + 1
    fencing_token = int(dispatch.fencing_token) + 1
    lease_until = current_time + timedelta(
        seconds=max(15, min(int(lease_seconds), 900))
    )
    dispatch.status = "leased"
    dispatch.attempt_count = attempt_no
    dispatch.fencing_token = fencing_token
    dispatch.lease_owner = normalized_worker
    dispatch.lease_until = lease_until
    dispatch.row_version = int(dispatch.row_version) + 1
    if str(operation.status) == "created":
        operation.status = "submitted"
        operation.submitted_at = current_time
        operation.row_version = int(operation.row_version) + 1
    attempt = BidToolDispatchAttempt(
        id=str(uuid.uuid4()),
        dispatch_id=str(dispatch.id),
        attempt_no=attempt_no,
        fencing_token=fencing_token,
        worker_id=normalized_worker,
        status="leased",
        execution_key=f"{dispatch.dispatch_key}:attempt:{attempt_no}",
        lease_until=lease_until,
        started_at=current_time,
    )
    db.add(attempt)
    db.flush()
    return ToolDispatchClaim(
        dispatch_id=str(dispatch.id),
        dispatch_attempt_id=str(attempt.id),
        invocation_id=str(invocation.id),
        operation_id=str(operation.id),
        worker_id=normalized_worker,
        fencing_token=fencing_token,
        lease_until=lease_until,
        adapter_name=str(dispatch.adapter_name),
        adapter_version=str(dispatch.adapter_version),
        adapter_mode=str(dispatch.adapter_mode),
        replay_policy=str(dispatch.replay_policy),
        provider_request_id=str(dispatch.provider_request_id),
        envelope=envelope,
        scope_token=token,
    )


def mark_tool_dispatch_sending(
    db: Session,
    claim: ToolDispatchClaim,
    *,
    now: datetime | None = None,
) -> None:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    dispatch = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.id == claim.dispatch_id)
        .with_for_update()
        .one()
    )
    attempt = (
        db.query(BidToolDispatchAttempt)
        .filter(BidToolDispatchAttempt.id == claim.dispatch_attempt_id)
        .with_for_update()
        .one()
    )
    if (
        str(dispatch.status) != "leased"
        or str(dispatch.lease_owner or "") != claim.worker_id
        or int(dispatch.fencing_token) != claim.fencing_token
        or str(attempt.status) != "leased"
        or int(attempt.fencing_token) != claim.fencing_token
        or as_utc(dispatch.lease_until) <= current_time
    ):
        raise BidToolDispatchFenceLost(BidToolDispatchFenceLost.code)
    dispatch.status = "sending"
    dispatch.dispatched_at = dispatch.dispatched_at or current_time
    dispatch.row_version = int(dispatch.row_version) + 1
    attempt.status = "sending"
    attempt.send_started_at = current_time
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == dispatch.async_operation_id)
        .with_for_update()
        .one()
    )
    if str(operation.status) == "submitted":
        operation.status = "running"
        operation.started_at = current_time
        operation.row_version = int(operation.row_version) + 1
    db.flush()


def settle_tool_dispatch(
    db: Session,
    claim: ToolDispatchClaim,
    result: ToolAdapterResult,
    *,
    now: datetime | None = None,
) -> str:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    dispatch = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.id == claim.dispatch_id)
        .with_for_update()
        .one()
    )
    attempt = (
        db.query(BidToolDispatchAttempt)
        .filter(BidToolDispatchAttempt.id == claim.dispatch_attempt_id)
        .with_for_update()
        .one()
    )
    if (
        str(dispatch.status) != "sending"
        or str(dispatch.lease_owner or "") != claim.worker_id
        or int(dispatch.fencing_token) != claim.fencing_token
        or str(attempt.status) != "sending"
        or int(attempt.fencing_token) != claim.fencing_token
        or dispatch.lease_until is None
        or as_utc(dispatch.lease_until) <= current_time
    ):
        raise BidToolDispatchFenceLost(BidToolDispatchFenceLost.code)
    if int(result.actual_cost_microunits) > int(dispatch.reserved_cost_microunits):
        raise BidToolDispatchConflict("BID_TOOL_ACTUAL_COST_EXCEEDS_RESERVATION")
    receipt = settle_async_tool_operation(
        db,
        operation_id=claim.operation_id,
        status=result.status,
        summary=result.summary,
        data=result.data,
        evidence_refs=list(result.evidence_refs),
        warnings=list(result.warnings),
        elapsed_ms=result.elapsed_ms,
        returned_items=result.returned_items,
        truncated=result.truncated,
        external_object_ref=result.external_object_ref,
        worker_id=claim.worker_id,
        request_id=f"tool-dispatch-settle:{dispatch.id}",
        now=current_time,
    )
    terminal_status = "succeeded" if result.status in {"ok", "no_result", "partial"} else "failed"
    dispatch.status = terminal_status
    dispatch.lease_owner = None
    dispatch.lease_until = None
    dispatch.provider_receipt_id = result.provider_receipt_id
    dispatch.actual_cost_microunits = max(0, int(result.actual_cost_microunits))
    dispatch.completed_at = current_time
    dispatch.last_error_code = None if terminal_status == "succeeded" else f"BID_TOOL_{result.status.upper()}"
    dispatch.row_version = int(dispatch.row_version) + 1
    attempt.status = terminal_status
    attempt.sent_at = current_time
    attempt.finished_at = current_time
    attempt.outcome_hash = str(receipt.result_hash)
    attempt.error_code = dispatch.last_error_code
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="tool.dispatch.settle",
        entity_type="tool_dispatch",
        entity_id=str(dispatch.id),
        assessment_id=str(claim.envelope["assessment_id"]),
        outcome="succeeded" if terminal_status == "succeeded" else "failed",
        request_id=f"tool-dispatch-settle:{dispatch.id}",
        after={
            "status": terminal_status,
            "result_id": receipt.result_id,
            "result_hash": receipt.result_hash,
            "provider_receipt_id": result.provider_receipt_id,
            "actual_cost_microunits": dispatch.actual_cost_microunits,
        },
        occurred_at=current_time,
    )
    db.flush()
    return terminal_status


def fail_tool_dispatch_attempt(
    db: Session,
    claim: ToolDispatchClaim,
    *,
    error_code: str,
    retryable: bool,
    now: datetime | None = None,
) -> str:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    dispatch = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.id == claim.dispatch_id)
        .with_for_update()
        .one()
    )
    attempt = (
        db.query(BidToolDispatchAttempt)
        .filter(BidToolDispatchAttempt.id == claim.dispatch_attempt_id)
        .with_for_update()
        .one()
    )
    if (
        int(dispatch.fencing_token) != claim.fencing_token
        or str(dispatch.lease_owner or "") != claim.worker_id
        or str(dispatch.status) not in LEASED_DISPATCH_STATES
        or dispatch.lease_until is None
        or as_utc(dispatch.lease_until) <= current_time
    ):
        raise BidToolDispatchFenceLost(BidToolDispatchFenceLost.code)
    can_retry = (
        bool(retryable)
        and str(dispatch.replay_policy) == "safe_idempotent"
        and int(dispatch.attempt_count) < int(dispatch.max_attempts)
    )
    dispatch.lease_owner = None
    dispatch.lease_until = None
    dispatch.last_error_code = str(error_code)[:100]
    dispatch.row_version = int(dispatch.row_version) + 1
    attempt.error_code = str(error_code)[:100]
    attempt.finished_at = current_time
    if can_retry:
        dispatch.status = "retry_wait"
        dispatch.available_at = current_time + timedelta(
            seconds=min(300, 2 ** int(dispatch.attempt_count))
        )
        attempt.status = "failed"
        db.flush()
        return "retry_wait"
    dispatch.status = "dead_letter" if retryable else "failed"
    dispatch.completed_at = current_time
    attempt.status = "failed"
    settle_async_tool_operation(
        db,
        operation_id=claim.operation_id,
        status="failed",
        summary="Governed tool adapter execution failed",
        data={},
        warnings=[str(error_code)[:100]],
        worker_id=claim.worker_id,
        request_id=f"tool-dispatch-failed:{dispatch.id}",
        now=current_time,
    )
    db.flush()
    return str(dispatch.status)


def execute_tool_dispatch_claim(
    *,
    session_factory: Callable[[], Session],
    claim: ToolDispatchClaim,
    now: datetime | None = None,
) -> str:
    db = session_factory()
    try:
        with db.begin():
            mark_tool_dispatch_sending(db, claim, now=now)
    except BidToolDispatchFenceLost:
        return "cancelled"
    finally:
        db.close()
    adapter = LOCAL_ADAPTERS.get(claim.adapter_name)
    if adapter is None:
        error = BidToolAdapterUnavailable(
            f"BID_TOOL_ADAPTER_UNAVAILABLE:{claim.adapter_name}"
        )
        db = session_factory()
        try:
            with db.begin():
                return fail_tool_dispatch_attempt(
                    db,
                    claim,
                    error_code=error.code,
                    retryable=False,
                    now=now,
                )
        except BidToolDispatchFenceLost:
            return "cancelled"
        finally:
            db.close()
    try:
        read_db = session_factory()
        try:
            with read_db.begin():
                result = adapter.execute(
                    read_db,
                    envelope=claim.envelope,
                    provider_request_id=claim.provider_request_id,
                )
        finally:
            read_db.close()
    except BidToolDispatchConflict as exc:
        db = session_factory()
        try:
            with db.begin():
                return fail_tool_dispatch_attempt(
                    db,
                    claim,
                    error_code=str(exc)[:100] or exc.code,
                    retryable=False,
                    now=now,
                )
        except BidToolDispatchFenceLost:
            return "cancelled"
        finally:
            db.close()
    except BidToolContextError as exc:
        db = session_factory()
        try:
            with db.begin():
                return fail_tool_dispatch_attempt(
                    db,
                    claim,
                    error_code=str(exc)[:100] or exc.code,
                    retryable=False,
                    now=now,
                )
        except BidToolDispatchFenceLost:
            return "cancelled"
        finally:
            db.close()
    except BidToolAdapterRetryable as exc:
        db = session_factory()
        try:
            with db.begin():
                return fail_tool_dispatch_attempt(
                    db,
                    claim,
                    error_code=str(exc)[:100] or exc.code,
                    retryable=True,
                    now=now,
                )
        except BidToolDispatchFenceLost:
            return "cancelled"
        finally:
            db.close()
    except Exception:
        logger.exception(
            "bid_tool_adapter_execution_failed",
            extra={"dispatch_id": claim.dispatch_id, "adapter": claim.adapter_name},
        )
        db = session_factory()
        try:
            with db.begin():
                return fail_tool_dispatch_attempt(
                    db,
                    claim,
                    error_code="BID_TOOL_ADAPTER_EXECUTION_FAILED",
                    retryable=True,
                    now=now,
                )
        except BidToolDispatchFenceLost:
            return "cancelled"
        finally:
            db.close()
    settlement_error_code: str | None = None
    db = session_factory()
    try:
        with db.begin():
            return settle_tool_dispatch(db, claim, result, now=now)
    except BidToolDispatchConflict as exc:
        settlement_error_code = str(exc)[:100] or exc.code
    except BidToolDispatchFenceLost:
        return "cancelled"
    finally:
        db.close()
    db = session_factory()
    try:
        with db.begin():
            return fail_tool_dispatch_attempt(
                db,
                claim,
                error_code=settlement_error_code or BidToolDispatchConflict.code,
                retryable=False,
                now=now,
            )
    except BidToolDispatchFenceLost:
        return "cancelled"
    finally:
        db.close()


def process_tool_dispatch_queue(
    *,
    session_factory: Callable[[], Session],
    worker_id: str,
    scope_signing_key: str,
    limit: int = 20,
    lease_seconds: int = 60,
) -> ToolDispatchBatchResult:
    claimed = succeeded = retry_wait = failed = uncertain = 0
    for _ in range(max(1, min(int(limit), 100))):
        db = session_factory()
        try:
            with db.begin():
                claim = claim_next_tool_dispatch(
                    db,
                    worker_id=worker_id,
                    scope_signing_key=scope_signing_key,
                    lease_seconds=lease_seconds,
                )
        finally:
            db.close()
        if claim is None:
            break
        claimed += 1
        status = execute_tool_dispatch_claim(
            session_factory=session_factory,
            claim=claim,
        )
        if status == "succeeded":
            succeeded += 1
        elif status == "retry_wait":
            retry_wait += 1
        elif status == "uncertain":
            uncertain += 1
        elif status == "cancelled":
            continue
        else:
            failed += 1
    return ToolDispatchBatchResult(
        claimed=claimed,
        succeeded=succeeded,
        retry_wait=retry_wait,
        failed=failed,
        uncertain=uncertain,
    )


def recover_expired_tool_dispatch(
    db: Session,
    *,
    dispatch_id: str,
    now: datetime | None = None,
) -> str | None:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    dispatch = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.id == str(dispatch_id))
        .with_for_update()
        .one_or_none()
    )
    if (
        dispatch is None
        or str(dispatch.status) not in LEASED_DISPATCH_STATES
        or dispatch.lease_until is None
        or as_utc(dispatch.lease_until) > current_time
    ):
        return None
    attempt = (
        db.query(BidToolDispatchAttempt)
        .filter(
            BidToolDispatchAttempt.dispatch_id == dispatch.id,
            BidToolDispatchAttempt.fencing_token == dispatch.fencing_token,
        )
        .with_for_update()
        .one()
    )
    was_sending = str(dispatch.status) == "sending"
    safe_replay = str(dispatch.replay_policy) == "safe_idempotent"
    attempt.status = "lease_expired" if (not was_sending or safe_replay) else "uncertain"
    attempt.finished_at = current_time
    attempt.error_code = (
        "BID_TOOL_DISPATCH_LEASE_EXPIRED"
        if attempt.status == "lease_expired"
        else BidToolDispatchOutcomeUncertain.code
    )
    dispatch.lease_owner = None
    dispatch.lease_until = None
    dispatch.row_version = int(dispatch.row_version) + 1
    replayable = not was_sending or safe_replay
    if replayable and int(dispatch.attempt_count) < int(dispatch.max_attempts):
        dispatch.status = "retry_wait"
        dispatch.available_at = current_time
        dispatch.last_error_code = "BID_TOOL_DISPATCH_LEASE_EXPIRED"
        db.flush()
        return "recovered"
    if replayable:
        dispatch.status = "dead_letter"
        dispatch.completed_at = current_time
        dispatch.last_error_code = "BID_TOOL_DISPATCH_RETRIES_EXHAUSTED"
        settle_async_tool_operation(
            db,
            operation_id=str(dispatch.async_operation_id),
            status="failed",
            summary="Tool dispatch retries were exhausted",
            data={},
            warnings=["BID_TOOL_DISPATCH_RETRIES_EXHAUSTED"],
            worker_id="bid-tool-dispatch-maintenance",
            request_id=f"tool-dispatch-dead-letter:{dispatch.id}",
            now=current_time,
        )
        db.flush()
        return "failed"
    dispatch.status = "uncertain"
    dispatch.completed_at = current_time
    dispatch.last_error_code = BidToolDispatchOutcomeUncertain.code
    settle_async_tool_operation(
        db,
        operation_id=str(dispatch.async_operation_id),
        status="failed",
        summary="Tool dispatch outcome is uncertain after executor lease loss",
        data={},
        warnings=[BidToolDispatchOutcomeUncertain.code],
        worker_id="bid-tool-dispatch-maintenance",
        request_id=f"tool-dispatch-uncertain:{dispatch.id}",
        now=current_time,
    )
    db.flush()
    return "uncertain"


def maintain_tool_dispatches(
    *,
    session_factory: Callable[[], Session],
    limit: int = 100,
    now: datetime | None = None,
) -> ToolDispatchMaintenanceResult:
    scan_db = session_factory()
    try:
        current_time = as_utc(now) if now is not None else database_utc_now(scan_db)
        ids = [
            str(row[0])
            for row in scan_db.query(BidToolDispatch.id)
            .filter(
                BidToolDispatch.status.in_(tuple(LEASED_DISPATCH_STATES)),
                BidToolDispatch.lease_until.is_not(None),
                BidToolDispatch.lease_until <= current_time,
            )
            .order_by(BidToolDispatch.lease_until.asc(), BidToolDispatch.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        ]
    finally:
        scan_db.close()
    recovered = uncertain = failed = 0
    for dispatch_id in ids:
        db = session_factory()
        try:
            with db.begin():
                result = recover_expired_tool_dispatch(
                    db,
                    dispatch_id=dispatch_id,
                    now=now,
                )
            if result == "recovered":
                recovered += 1
            elif result == "uncertain":
                uncertain += 1
            elif result == "failed":
                failed += 1
        except Exception:
            logger.exception(
                "bid_tool_dispatch_maintenance_failed",
                extra={"dispatch_id": dispatch_id},
            )
            failed += 1
        finally:
            db.close()
    return ToolDispatchMaintenanceResult(
        scanned=len(ids),
        recovered=recovered,
        uncertain=uncertain,
        failed=failed,
    )
