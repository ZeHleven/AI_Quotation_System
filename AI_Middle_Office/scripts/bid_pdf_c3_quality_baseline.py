from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import app.models.registry  # noqa: E402,F401
from app.core.database import Base  # noqa: E402
from app.models.bid_assessment import (  # noqa: E402
    BidAssessment,
    BidAssessmentScope,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
)
from app.models.bid_assessment_config import (  # noqa: E402
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_documents import (  # noqa: E402
    BidDocumentParseRun,
    BidEvidenceFragment,
)
from app.models.bid_assessment_retrieval import (  # noqa: E402
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalIndex,
)
from app.models.bid_assessment_semantic import (  # noqa: E402
    BidEvidenceSemanticIndex,
)
from app.models.bid_assessment_runtime import BidAnalysisRun  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.bid_assessment_eventing import canonical_hash, utc_now  # noqa: E402
from app.services.bid_document_parse_runs import ensure_document_parse_run  # noqa: E402
from app.services.bid_document_parse_worker import (  # noqa: E402
    claim_document_parse_run,
    complete_document_parse_run,
)
from app.services.bid_document_parser_adapter import (  # noqa: E402
    parse_bid_document_bytes,
)
from app.services.bid_evidence_chunk_builder import normalize_evidence_text  # noqa: E402
from app.services.bid_evidence_retrieval_index import (  # noqa: E402
    PDF_C2_PARSER_PROFILE_VERSION,
    PDF_RQ1A_PARSER_PROFILE_VERSION,
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    build_role_aware_retrieval_index,
    ensure_role_aware_retrieval_index,
)
from app.services.bid_evidence_semantic_index import (  # noqa: E402
    DISABLED_SEMANTIC_PROFILE_VERSION,
    RQ2A_SEMANTIC_PROFILE_VERSION,
    build_semantic_index,
    ensure_semantic_index,
)
from app.services.bid_field_aware_lexical import (  # noqa: E402
    FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
)
from app.services.bid_hybrid_candidate_fusion import (  # noqa: E402
    DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
)
from app.services.bid_lightweight_reranker import (  # noqa: E402
    DISABLED_RERANK_PROFILE_VERSION,
    RQ2C_RERANK_PROFILE_VERSION,
    LocalBceCrossEncoderReranker,
)
from app.services.bid_parse_quality_gate import (  # noqa: E402
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    QUALITY_GATE_WARNING_CODE,
)
from app.services.bid_query_optimizer import (  # noqa: E402
    LEGACY_QUERY_PLANNER_PROFILE_VERSION,
    QUERY_OPTIMIZER_PROFILE_VERSION,
)
from app.services.bid_semantic_vector_provider import (  # noqa: E402
    RQ2A_DISTANCE_METRIC,
    RQ2A_EMBEDDING_DIMENSION,
    RQ2A_EMBEDDING_MODEL_ID,
    RQ2A_EMBEDDING_MODEL_REVISION,
    RQ2A_PROVIDER_ID,
    BidSemanticProviderInvalid,
    SemanticDocument,
    SemanticModelDescriptor,
    SemanticProviderHit,
    SemanticVectorReceipt,
    vector_hash,
)
from mcp_servers.bid_assessment_evidence.service import (  # noqa: E402
    BidEvidenceMcpScope,
    BidEvidenceMcpService,
)


SCHEMA_VERSION = "bid.pdf-c3.quality-baseline.v1"
ISOLATED_BCE_EXACT_BACKEND = "isolated-bce-exact-cosine"


@dataclass(frozen=True)
class RuntimeScope:
    assessment_id: str
    manifest_id: str
    document_version_id: str
    analysis_run_id: str


class IsolatedBceExactSemanticProvider:
    """Real frozen BCE embeddings with an explicitly non-Milvus eval store.

    This provider exists only for the isolated quality runner.  It exercises
    the production RQ2-A DB authority and Evidence MCP v5 while using exact
    in-process cosine search, so a workstation without an isolated Milvus can
    measure semantic quality without pretending that the Milvus adapter ran.
    """

    backend_id = ISOLATED_BCE_EXACT_BACKEND

    def __init__(self, *, model_path: Path, model_cache_dir: Path | None = None):
        if not model_path.is_dir():
            raise FileNotFoundError("BID_RQ2A_EVAL_BCE_SNAPSHOT_NOT_FOUND")
        self._model_path = str(model_path)
        self._model_cache_dir = str(model_cache_dir) if model_cache_dir else None
        self._model: Any | None = None
        self._records: dict[str, dict[str, tuple[SemanticDocument, Any, str]]] = {}
        self._descriptor = SemanticModelDescriptor(
            provider_id=RQ2A_PROVIDER_ID,
            model_id=RQ2A_EMBEDDING_MODEL_ID,
            model_revision=RQ2A_EMBEDDING_MODEL_REVISION,
            dimension=RQ2A_EMBEDDING_DIMENSION,
            distance_metric=RQ2A_DISTANCE_METRIC,
            normalized_embeddings=True,
        )

    @property
    def descriptor(self) -> SemanticModelDescriptor:
        return self._descriptor

    def _load_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._model_path,
                cache_folder=self._model_cache_dir,
                local_files_only=True,
                device="cpu",
            )
        return self._model

    def _encode(self, texts: Sequence[str]) -> Any:
        import numpy as np

        if not texts:
            return np.empty((0, RQ2A_EMBEDDING_DIMENSION), dtype=np.float32)
        vectors = self._load_model().encode(
            list(texts),
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        normalized = np.asarray(vectors, dtype=np.float32)
        if normalized.shape != (len(texts), RQ2A_EMBEDDING_DIMENSION):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_VECTOR_DIMENSION_INVALID")
        return normalized

    def upsert_documents(
        self,
        *,
        namespace: str,
        provider_request_id: str,
        documents: Sequence[SemanticDocument],
    ) -> tuple[SemanticVectorReceipt, ...]:
        del provider_request_id
        vectors = self._encode([row.text for row in documents])
        namespace_records = self._records.setdefault(str(namespace), {})
        receipts: list[SemanticVectorReceipt] = []
        for document, vector in zip(documents, vectors):
            digest = vector_hash(vector)
            existing = namespace_records.get(document.provider_record_id)
            if existing is not None:
                existing_document, _existing_vector, existing_hash = existing
                if existing_document != document or existing_hash != digest:
                    raise BidSemanticProviderInvalid(
                        "BID_SEMANTIC_PROVIDER_IDEMPOTENCY_CONFLICT"
                    )
            else:
                namespace_records[document.provider_record_id] = (
                    document,
                    vector,
                    digest,
                )
            receipts.append(
                SemanticVectorReceipt(
                    provider_record_id=document.provider_record_id,
                    retrieval_child_key=document.retrieval_child_key,
                    source_entry_hash=document.source_entry_hash,
                    embedding_text_hash=document.embedding_text_hash,
                    vector_hash=digest,
                    vector_dimension=RQ2A_EMBEDDING_DIMENSION,
                )
            )
        return tuple(receipts)

    def search(
        self,
        *,
        namespace: str,
        query: str,
        top_k: int,
    ) -> tuple[SemanticProviderHit, ...]:
        records = self._records.get(str(namespace), {})
        if not records:
            return ()
        query_vector = self._encode([str(query)])[0]
        ranked: list[tuple[float, str, SemanticDocument, str]] = []
        for provider_record_id, (document, vector, digest) in records.items():
            score = float(query_vector @ vector)
            ranked.append((score, provider_record_id, document, digest))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return tuple(
            SemanticProviderHit(
                provider_record_id=provider_record_id,
                retrieval_child_key=document.retrieval_child_key,
                source_entry_hash=document.source_entry_hash,
                embedding_text_hash=document.embedding_text_hash,
                vector_hash=digest,
                score=score,
            )
            for score, provider_record_id, document, digest in ranked[:top_k]
        )


def _identifier() -> str:
    return str(uuid.uuid4())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(float(ordered[position]), 3)


def _mean(values: Iterable[float]) -> float:
    rows = list(values)
    return round(float(statistics.fmean(rows)), 6) if rows else 0.0


def _dcg(gains: list[int], k: int) -> float:
    return sum(
        ((2**gain) - 1) / math.log2(rank + 2)
        for rank, gain in enumerate(gains[:k])
    )


def _ndcg(gains: list[int], ideal_gains: list[int], k: int) -> float:
    ideal = _dcg(sorted(ideal_gains, reverse=True), k)
    if ideal <= 0:
        return 0.0
    return round(_dcg(gains, k) / ideal, 6)


def _make_session_factory(database_path: Path):
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def _seed_runtime_authority(
    db,
    *,
    document_sha256: str,
    document_size: int,
    original_filename: str,
) -> RuntimeScope:
    owner = User(
        username=f"pdf-c3-eval-{uuid.uuid4().hex[:12]}",
        hashed_password="isolated-evaluation-only",
        role="user",
        role_version=1,
        quota=10,
        quota_reserved=0,
        is_active=True,
        must_change_password=False,
    )
    db.add(owner)
    db.flush()
    owner_id = int(owner.id)
    assessment_id = _identifier()
    manifest_id = _identifier()
    document_id = _identifier()
    document_version_id = _identifier()
    file_id = _identifier()
    now = utc_now()
    assessment = BidAssessment(
        id=assessment_id,
        title="Private PDF-C3 retrieval quality evaluation",
        client_name="restricted-local-evaluation",
        lifecycle_status="active",
        business_status="preparing",
        created_by=owner_id,
        updated_by=owner_id,
        row_version=1,
    )
    db.add(assessment)
    db.add(
        BidFileObject(
            id=file_id,
            sha256=document_sha256,
            object_key=f"isolated-eval/{document_sha256}.pdf",
            size_bytes=document_size,
            mime_type="application/pdf",
            storage_status="available",
            created_by=owner_id,
            row_version=1,
        )
    )
    db.add(
        BidDocument(
            id=document_id,
            logical_identity_key=f"isolated-eval:{document_sha256}",
            logical_name="restricted-evaluation-document.pdf",
            document_type="tender_document",
            created_by=owner_id,
        )
    )
    db.flush()
    db.add(
        BidDocumentVersion(
            id=document_version_id,
            document_id=document_id,
            file_object_id=file_id,
            version_no=1,
            original_filename=original_filename,
            source_metadata_hash=canonical_hash(
                {
                    "source": "isolated-real-document-evaluation",
                    "sha256": document_sha256,
                }
            ),
            source_metadata_json={
                "source": "isolated-real-document-evaluation",
                "authority": "user-authorized-local-only",
            },
            created_by=owner_id,
        )
    )
    manifest_hash = canonical_hash(
        {
            "documents": [
                {
                    "document_version_id": document_version_id,
                    "role": "tender_document",
                    "order_no": 0,
                }
            ]
        }
    )
    db.add(
        BidDocumentManifest(
            id=manifest_id,
            assessment_id=assessment_id,
            version=1,
            manifest_hash=manifest_hash,
            committed_by=owner_id,
        )
    )
    db.flush()
    db.add(
        BidManifestDocument(
            manifest_id=manifest_id,
            document_version_id=document_version_id,
            role="tender_document",
            order_no=0,
        )
    )
    scope = BidAssessmentScope(
        id=_identifier(),
        assessment_id=assessment_id,
        version=1,
        scope_type="lot",
        source_lot_candidate_id=None,
        selected_lot_snapshot_json={
            "lot_name": "restricted-real-document-evaluation"
        },
        scope_hash=canonical_hash(
            {
                "assessment_id": assessment_id,
                "manifest_id": manifest_id,
                "scope_type": "lot",
            }
        ),
        created_by=owner_id,
    )
    enterprise = BidEnterpriseSnapshot(
        id=_identifier(),
        version=f"isolated-eval-{document_sha256[:12]}",
        as_of=now,
        snapshot_hash=None,
        source_catalog_version="isolated-eval-v1",
        status="building",
        created_by=owner_id,
        row_version=1,
    )

    def artifact(model, name: str, **extra):
        return model(
            id=_identifier(),
            version=f"isolated-{name}-{document_sha256[:12]}",
            status="draft",
            active_slot_key=None,
            artifact_ref=f"memory://isolated-eval/{name}",
            artifact_hash=canonical_hash(
                {"type": name, "document_sha256": document_sha256}
            ),
            authored_by=owner_id,
            row_version=1,
            **extra,
        )

    rule_set = artifact(
        BidRuleSet,
        "rules",
        test_cases_ref="memory://isolated-eval/rules/tests",
    )
    fact_catalog = artifact(
        BidFactCatalogVersion,
        "facts",
        schema_version="v1",
    )
    prompt_bundle = artifact(
        BidPromptBundle,
        "prompts",
        bundle_schema_version="v1",
    )
    tool_registry = artifact(
        BidToolRegistryVersion,
        "tools",
        registry_schema_version="v1",
    )
    model_profile = artifact(
        BidModelProfileVersion,
        "models",
        role_routing_json={},
        provider_identifiers_json={},
        model_identifiers_json={},
    )
    formula_catalog = artifact(
        BidFormulaCatalogVersion,
        "formulas",
        rounding_policy_json={},
    )
    db.add_all(
        [
            scope,
            enterprise,
            rule_set,
            fact_catalog,
            prompt_bundle,
            tool_registry,
            model_profile,
            formula_catalog,
        ]
    )
    db.flush()
    run_id = _identifier()
    input_payload = {
        "assessment_id": assessment_id,
        "manifest_id": manifest_id,
        "document_sha256": document_sha256,
        "profile": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    }
    db.add(
        BidAnalysisRun(
            id=run_id,
            assessment_id=assessment_id,
            scope_id=str(scope.id),
            manifest_id=manifest_id,
            enterprise_snapshot_id=str(enterprise.id),
            rule_set_id=str(rule_set.id),
            fact_catalog_version_id=str(fact_catalog.id),
            prompt_bundle_id=str(prompt_bundle.id),
            tool_registry_version_id=str(tool_registry.id),
            model_profile_version_id=str(model_profile.id),
            formula_catalog_version_id=str(formula_catalog.id),
            run_sequence=1,
            run_kind="preliminary",
            status="running",
            retryable=False,
            input_fingerprint=canonical_hash(input_payload),
            input_hash=canonical_hash({**input_payload, "kind": "baseline"}),
            evaluation_time=now,
            current_stage="retrieve",
            row_version=1,
        )
    )
    db.flush()
    assessment.current_manifest_id = manifest_id
    assessment.active_scope_id = str(scope.id)
    assessment.active_run_id = run_id
    db.flush()
    return RuntimeScope(
        assessment_id=assessment_id,
        manifest_id=manifest_id,
        document_version_id=document_version_id,
        analysis_run_id=run_id,
    )


def _load_cases(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "bid.pdf-c3.silver-cases.v1":
        raise ValueError("BID_PDF_C3_EVAL_CASE_SCHEMA_INVALID")
    cases = list(payload.get("cases") or [])
    if not cases or len({str(row.get("case_id")) for row in cases}) != len(cases):
        raise ValueError("BID_PDF_C3_EVAL_CASES_INVALID")
    for row in cases:
        if not str(row.get("question") or "").strip():
            raise ValueError("BID_PDF_C3_EVAL_QUESTION_MISSING")
        targets = list(row.get("targets") or [])
        if not targets or any(not str(item.get("phrase") or "").strip() for item in targets):
            raise ValueError("BID_PDF_C3_EVAL_TARGET_MISSING")
    return payload


def _fragment_role(row: BidEvidenceFragment) -> str:
    return str((row.locator_json or {}).get("fragment_role") or "")


def _matchable_text(value: str) -> str:
    return re.sub(r"\s+", "", normalize_evidence_text(value))


def _resolve_target_groups(
    fragments: list[BidEvidenceFragment],
    targets: list[dict[str, Any]],
    child_ids_by_parent: dict[str, set[str]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for target in targets:
        phrase = _matchable_text(str(target["phrase"]))
        pages = {int(value) for value in target.get("pages") or []}
        matches = [
            row
            for row in fragments
            if (
                not pages
                or int((row.locator_json or {}).get("page_no") or 0) in pages
            )
            and phrase in _matchable_text(str(row.normalized_text or ""))
        ]
        if not matches:
            raise ValueError(
                "BID_PDF_C3_EVAL_GOLD_TARGET_NOT_FOUND:"
                + _sha256_text(
                    json.dumps(target, ensure_ascii=False, sort_keys=True)
                )[:16]
            )
        atom_ids = {
            str(row.id) for row in matches if _fragment_role(row) == "evidence_atom"
        }
        child_ids: set[str] = set()
        for row in matches:
            role = _fragment_role(row)
            if role == "evidence_atom" and row.parent_id:
                child_ids.add(str(row.parent_id))
            elif role == "retrieval_child":
                child_ids.add(str(row.id))
            elif role == "section_parent":
                child_ids.update(child_ids_by_parent.get(str(row.id), set()))
        if not child_ids:
            raise ValueError(
                "BID_PDF_C3_EVAL_GOLD_TARGET_UNMAPPABLE:"
                + _sha256_text(
                    json.dumps(target, ensure_ascii=False, sort_keys=True)
                )[:16]
            )
        groups.append(
            {
                "atom_ids": atom_ids,
                "child_ids": child_ids,
                "match_roles": sorted({_fragment_role(row) for row in matches}),
            }
        )
    return groups


def _entry_gains(
    child_ids: Iterable[str],
    target_groups: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        child_id: sum(
            child_id in set(target_group["child_ids"])
            for target_group in target_groups
        )
        for child_id in child_ids
    }


def _evaluate_case(
    service: BidEvidenceMcpService,
    *,
    case: dict[str, Any],
    target_groups: list[dict[str, Any]],
    atom_ids_by_child: dict[str, set[str]],
    include_excerpts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = service.search(
        {
            "query": str(case["question"]),
            "document_roles": ["tender_document"],
            "top_k": 8,
        }
    )
    search_ms = round((time.perf_counter() - started) * 1000, 3)
    hits = list(result.get("hits") or [])
    gains_by_child = _entry_gains(atom_ids_by_child, target_groups)
    gains = [gains_by_child.get(str(hit["evidence_id"]), 0) for hit in hits]
    ideal_gains = list(gains_by_child.values())

    def metrics_at(k: int) -> dict[str, Any]:
        selected = hits[:k]
        selected_ids = {str(hit["evidence_id"]) for hit in selected}
        target_hits = [
            bool(set(group["child_ids"]) & selected_ids) for group in target_groups
        ]
        first_rank = next(
            (
                index + 1
                for index, hit in enumerate(selected)
                if gains_by_child.get(str(hit["evidence_id"]), 0) > 0
            ),
            None,
        )
        relevant = sum(
            gains_by_child.get(str(hit["evidence_id"]), 0) > 0
            for hit in selected
        )
        return {
            "hit": any(target_hits),
            "target_recall": round(sum(target_hits) / len(target_groups), 6),
            "precision": round(relevant / max(len(selected), 1), 6),
            "mrr": round(1 / first_rank, 6) if first_rank else 0.0,
            "ndcg": _ndcg(gains, ideal_gains, k),
            "first_relevant_rank": first_rank,
            "relevant_child_count": relevant,
        }

    read_atom_ids: set[str] = set()
    atom_only_violations = 0
    read_warnings: set[str] = set()
    read_ms = 0.0
    for hit in hits[:5]:
        read_started = time.perf_counter()
        read = service.read(
            {
                "evidence_ids": [str(hit["evidence_id"])],
                "expansion": "none",
            }
        )
        read_ms += (time.perf_counter() - read_started) * 1000
        read_warnings.update(str(value) for value in read.get("warnings") or [])
        for item in read.get("items") or []:
            read_atom_ids.add(str(item["evidence_id"]))
            if not (
                item.get("fragment_role") == "evidence_atom"
                and item.get("is_citable") is True
                and item.get("context_read") is True
            ):
                atom_only_violations += 1
    read_target_hits = [
        bool(set(group["atom_ids"]) & read_atom_ids) for group in target_groups
    ]
    query_plan = dict(result.get("query_plan") or {})
    row = {
        "case_id": str(case["case_id"]),
        "category": str(case.get("category") or "unspecified"),
        "answer_status": str(case.get("answer_status") or "answerable"),
        "target_count": len(target_groups),
        "citable_target_count": sum(bool(group["atom_ids"]) for group in target_groups),
        "uncitable_target_count": sum(not bool(group["atom_ids"]) for group in target_groups),
        "gold_atom_count": len(
            set().union(*(set(group["atom_ids"]) for group in target_groups))
        ),
        "search_ms": search_ms,
        "read_ms": round(read_ms, 3),
        "returned_child_count": len(hits),
        "parent_only_hit_count": sum(not list(hit.get("matched_queries") or []) for hit in hits),
        "atom_only_violation_count": atom_only_violations,
        "read_target_recall_at_5": round(sum(read_target_hits) / len(target_groups), 6),
        "query_count": len(query_plan.get("queries") or []),
        "route_modes": [
            str(route.get("requested_mode") or "")
            for route in result.get("retrieval_routes") or []
        ],
        "warnings": sorted(str(value) for value in result.get("warnings") or []),
        "read_warnings": sorted(read_warnings),
        "metrics_at_5": metrics_at(5),
        "metrics_at_8": metrics_at(8),
        "result_hash": str(result.get("result_hash") or ""),
        "retrieval_mode": str(result.get("retrieval_mode") or ""),
        "lexical_projection_set_hash": str(
            result.get("lexical_projection_set_hash") or ""
        ),
        "semantic_index_set_hash": str(
            result.get("semantic_index_set_hash") or ""
        ),
        "semantic_model": dict(result.get("semantic_model") or {}),
        "candidate_fusion": dict(result.get("candidate_fusion") or {}),
        "rerank_model": dict(result.get("rerank_model") or {}),
        "candidate_rerank": dict(result.get("candidate_rerank") or {}),
    }
    if include_excerpts:
        row["question"] = str(case["question"])
        row["hits"] = [
            {
                "rank": rank,
                "evidence_id": str(hit["evidence_id"]),
                "page_start": int((hit.get("locator") or {}).get("page_no") or 0),
                "page_end": int((hit.get("locator") or {}).get("page_end") or 0),
                "gain": gains[index],
                "score": hit.get("score"),
                "matched_query_count": len(hit.get("matched_queries") or []),
                "matched_channels": list(hit.get("matched_channels") or []),
                "excerpt": str(hit.get("excerpt") or ""),
            }
            for index, (rank, hit) in enumerate(zip(range(1, len(hits) + 1), hits))
        ]
    return row


def _aggregate(
    *,
    rows: list[dict[str, Any]],
    parse_result,
    index: BidEvidenceRetrievalIndex,
    dataset: dict[str, Any],
    document_sha256: str,
    document_size: int,
    parser_profile_version: str,
    query_optimizer_profile_version: str,
    lexical_search_profile_version: str,
    semantic_search_profile_version: str,
    candidate_fusion_profile_version: str,
    rerank_profile_version: str,
    semantic_backend_id: str | None,
    rerank_backend_id: str | None,
    parse_ms: float,
    persist_ms: float,
    index_ms: float,
) -> dict[str, Any]:
    at5 = [dict(row["metrics_at_5"]) for row in rows]
    at8 = [dict(row["metrics_at_8"]) for row in rows]
    fragments = {
        "section_parent": int(index.parent_count),
        "retrieval_child": int(index.child_count),
        "evidence_atom": int(index.atom_count),
    }
    child_tokens = [
        int((item.locator or {}).get("estimated_tokens") or 0)
        for item in parse_result.evidence
        if str((item.locator or {}).get("fragment_role") or "")
        == "retrieval_child"
    ]
    atom_tokens = [
        int((item.locator or {}).get("estimated_tokens") or 0)
        for item in parse_result.evidence
        if str((item.locator or {}).get("fragment_role") or "")
        == "evidence_atom"
    ]
    page_metrics = [dict(unit.metrics or {}) for unit in parse_result.units]
    warning_codes = Counter(
        str(row.get("code") or "UNKNOWN") for row in parse_result.warnings
    )
    margin_suppression = next(
        (
            dict(row.get("details") or {})
            for row in parse_result.warnings
            if str(row.get("code") or "")
            == "PDF_REPEATED_MARGIN_ARTIFACTS_SUPPRESSED"
        ),
        {},
    )
    quality_gate = next(
        (
            dict(row.get("details") or {})
            for row in parse_result.warnings
            if str(row.get("code") or "") == QUALITY_GATE_WARNING_CODE
        ),
        None,
    )
    route_modes = Counter(
        mode for row in rows for mode in row.get("route_modes") or []
    )
    projection_hashes = sorted(
        {
            str(row.get("lexical_projection_set_hash") or "")
            for row in rows
            if str(row.get("lexical_projection_set_hash") or "")
        }
    )
    semantic_index_hashes = sorted(
        {
            str(row.get("semantic_index_set_hash") or "")
            for row in rows
            if str(row.get("semantic_index_set_hash") or "")
        }
    )
    semantic_models = {
        canonical_hash(dict(row.get("semantic_model") or {})): dict(
            row.get("semantic_model") or {}
        )
        for row in rows
        if row.get("semantic_model")
    }
    semantic_enabled = (
        semantic_search_profile_version == RQ2A_SEMANTIC_PROFILE_VERSION
    )
    fusion_hashes = sorted(
        {
            str((row.get("candidate_fusion") or {}).get("result_hash") or "")
            for row in rows
            if str(
                (row.get("candidate_fusion") or {}).get("result_hash")
                or ""
            )
        }
    )
    rerank_hashes = sorted(
        {
            str((row.get("candidate_rerank") or {}).get("result_hash") or "")
            for row in rows
            if str((row.get("candidate_rerank") or {}).get("result_hash") or "")
        }
    )
    rerank_models = {
        canonical_hash(dict(row.get("rerank_model") or {})): dict(
            row.get("rerank_model") or {}
        )
        for row in rows
        if row.get("rerank_model")
    }
    rerank_enabled = rerank_profile_version == RQ2C_RERANK_PROFILE_VERSION
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_kind": "single-document-silver-baseline",
        "annotation_status": str(dataset.get("annotation_status") or "draft"),
        "dataset_id": str(dataset.get("dataset_id") or "private"),
        "dataset_hash": canonical_hash(dataset),
        "document": {
            "sha256": document_sha256,
            "size_bytes": document_size,
            "page_count": len(parse_result.units),
        },
        "profiles": {
            "parser_profile_version": parser_profile_version,
            "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            "query_optimizer_profile_version": query_optimizer_profile_version,
            "lexical_search_profile_version": lexical_search_profile_version,
            "semantic_search_profile_version": semantic_search_profile_version,
            "candidate_fusion_profile_version": candidate_fusion_profile_version,
            "rerank_profile_version": rerank_profile_version,
            "semantic_backend_id": semantic_backend_id,
            "rerank_backend_id": rerank_backend_id,
            "retrieval_contract": (
                "bid-assessment-evidence-mcp/v7"
                if rerank_enabled
                else "bid-assessment-evidence-mcp/v6"
                if candidate_fusion_profile_version
                == RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
                else (
                    "bid-assessment-evidence-mcp/v5"
                    if semantic_enabled
                    else "bid-assessment-evidence-mcp/v4"
                )
            ),
            "ocr_used": False,
            "vision_used": False,
            "model_used": semantic_enabled or rerank_enabled,
            "embedding_model_used": semantic_enabled,
            "cross_encoder_model_used": rerank_enabled,
            "generation_model_used": False,
            "external_service_used": False,
        },
        "timing_ms": {
            "parse": round(parse_ms, 3),
            "persist": round(persist_ms, 3),
            "index": round(index_ms, 3),
            "search_p50": _percentile([float(row["search_ms"]) for row in rows], 0.50),
            "search_p95": _percentile([float(row["search_ms"]) for row in rows], 0.95),
            "read_p95": _percentile([float(row["read_ms"]) for row in rows], 0.95),
        },
        "structure": {
            "parse_status": str(parse_result.status),
            "quality_grade": str(parse_result.quality_grade),
            "quality_score": int(parse_result.quality_score),
            "quality_gate": quality_gate,
            "ocr_status": str(parse_result.ocr_status),
            "warning_count": len(parse_result.warnings),
            "warning_codes": dict(sorted(warning_codes.items())),
            "margin_artifact_suppression": {
                "suppressed_block_count": int(
                    margin_suppression.get("suppressed_count") or 0
                ),
                "suppressed_char_count": int(
                    margin_suppression.get("suppressed_char_count") or 0
                ),
                "signature_count": int(
                    margin_suppression.get("signature_count") or 0
                ),
                "affected_page_count": int(
                    margin_suppression.get("affected_page_count") or 0
                ),
            },
            "partial_page_count": sum(unit.status == "partial" for unit in parse_result.units),
            "native_page_count": sum(unit.content_source == "native" for unit in parse_result.units),
            "table_count": sum(int(row.get("table_count") or 0) for row in page_metrics),
            "image_count": sum(int(row.get("image_count") or 0) for row in page_metrics),
            "two_column_page_count": sum(
                row.get("reading_order_mode") == "two_column" for row in page_metrics
            ),
            "fragments": fragments,
            "entries": int(index.entry_count),
            "parents_per_page": round(fragments["section_parent"] / max(len(parse_result.units), 1), 6),
            "atoms_per_child": round(fragments["evidence_atom"] / max(fragments["retrieval_child"], 1), 6),
            "child_token_p50": _percentile([float(value) for value in child_tokens], 0.50),
            "child_token_p95": _percentile([float(value) for value in child_tokens], 0.95),
            "child_below_220_ratio": round(sum(value < 220 for value in child_tokens) / max(len(child_tokens), 1), 6),
            "atom_token_p50": _percentile([float(value) for value in atom_tokens], 0.50),
            "atom_token_p95": _percentile([float(value) for value in atom_tokens], 0.95),
            "index_result_hash": str(index.result_hash or ""),
        },
        "retrieval": {
            "case_count": len(rows),
            "hit_at_5": _mean(float(row["hit"]) for row in at5),
            "target_recall_at_5": _mean(float(row["target_recall"]) for row in at5),
            "precision_at_5": _mean(float(row["precision"]) for row in at5),
            "mrr_at_5": _mean(float(row["mrr"]) for row in at5),
            "ndcg_at_5": _mean(float(row["ndcg"]) for row in at5),
            "hit_at_8": _mean(float(row["hit"]) for row in at8),
            "target_recall_at_8": _mean(float(row["target_recall"]) for row in at8),
            "mrr_at_8": _mean(float(row["mrr"]) for row in at8),
            "ndcg_at_8": _mean(float(row["ndcg"]) for row in at8),
            "read_target_recall_at_5": _mean(
                float(row["read_target_recall_at_5"]) for row in rows
            ),
            "atom_only_violation_count": sum(
                int(row["atom_only_violation_count"]) for row in rows
            ),
            "parent_only_hit_count": sum(int(row["parent_only_hit_count"]) for row in rows),
            "citable_target_availability": round(
                sum(int(row["citable_target_count"]) for row in rows)
                / max(sum(int(row["target_count"]) for row in rows), 1),
                6,
            ),
            "uncitable_target_count": sum(
                int(row["uncitable_target_count"]) for row in rows
            ),
            "semantic_fallback_case_count": sum(
                "SEMANTIC_BACKEND_UNAVAILABLE_BM25_FALLBACK" in set(row["warnings"])
                for row in rows
            ),
            "route_mode_counts": dict(sorted(route_modes.items())),
            "average_planned_query_count": _mean(
                float(row["query_count"]) for row in rows
            ),
            "lexical_projection_set_hash": (
                projection_hashes[0] if len(projection_hashes) == 1 else None
            ),
            "lexical_projection_hash_consistent": len(projection_hashes) <= 1,
            "semantic_index_set_hash": (
                semantic_index_hashes[0]
                if len(semantic_index_hashes) == 1
                else None
            ),
            "semantic_index_hash_consistent": len(semantic_index_hashes) <= 1,
            "semantic_model": (
                next(iter(semantic_models.values()))
                if len(semantic_models) == 1
                else None
            ),
            "candidate_fusion_case_hash_count": len(fusion_hashes),
            "candidate_rerank_case_hash_count": len(rerank_hashes),
            "rerank_model": (
                next(iter(rerank_models.values()))
                if len(rerank_models) == 1
                else None
            ),
            "rerank_promotion_count": sum(
                int((row.get("candidate_rerank") or {}).get("promotion_count") or 0)
                for row in rows
            ),
            "failed_case_ids_at_5": [
                str(row["case_id"])
                for row in rows
                if float(row["metrics_at_5"]["target_recall"]) < 1.0
            ],
            "zero_hit_case_ids_at_5": [
                str(row["case_id"])
                for row in rows
                if not bool(row["metrics_at_5"]["hit"])
            ],
        },
        "case_metrics": [
            {
                "case_id": row["case_id"],
                "category": row["category"],
                "answer_status": row["answer_status"],
                "target_count": row["target_count"],
                "citable_target_count": row["citable_target_count"],
                "uncitable_target_count": row["uncitable_target_count"],
                "search_ms": row["search_ms"],
                "query_count": row["query_count"],
                "route_modes": row["route_modes"],
                "metrics_at_5": row["metrics_at_5"],
                "metrics_at_8": row["metrics_at_8"],
                "read_target_recall_at_5": row["read_target_recall_at_5"],
            }
            for row in rows
        ],
        "limitations": [
            "Silver targets are phrase-anchored and have not received independent business review.",
            "One document cannot establish cross-project generalization or a holdout result.",
            (
                "Semantic quality uses real frozen BCE embeddings with exact in-process cosine search; the production Milvus adapter was not executed."
                if semantic_enabled
                else "The semantic backend is intentionally disabled; semantic and hybrid routes fall back to BM25."
            ),
            (
                "RQ2-C uses the frozen local BCE Cross-Encoder on the exact RQ2-B Top-20 candidate window."
                if rerank_enabled
                else "The cross-encoder reranker is disabled."
            ),
            "No OCR or visual fidelity assessment was performed.",
            "No generation model or external MCP was called.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build an isolated real-PDF PDF-C2/C3 retrieval Silver baseline "
            "without OCR, vision, generation models, network, or external "
            "services; an explicitly requested RQ2-A comparison may load one "
            "frozen local embedding model."
        )
    )
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--parser-profile",
        choices=[
            PDF_C2_PARSER_PROFILE_VERSION,
            PDF_RQ1A_PARSER_PROFILE_VERSION,
            PDF_RQ1B_PARSER_PROFILE_VERSION,
        ],
        default=PDF_C2_PARSER_PROFILE_VERSION,
    )
    parser.add_argument(
        "--query-optimizer-profile",
        choices=[
            LEGACY_QUERY_PLANNER_PROFILE_VERSION,
            QUERY_OPTIMIZER_PROFILE_VERSION,
        ],
        default=LEGACY_QUERY_PLANNER_PROFILE_VERSION,
    )
    parser.add_argument(
        "--compare-query-optimizer-profile",
        choices=[
            LEGACY_QUERY_PLANNER_PROFILE_VERSION,
            QUERY_OPTIMIZER_PROFILE_VERSION,
        ],
        default=None,
        help=(
            "Optionally evaluate a second query profile against the exact same "
            "ParseHead and RetrievalIndexHead."
        ),
    )
    parser.add_argument(
        "--lexical-search-profile",
        choices=[
            LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
            FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        ],
        default=LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
    )
    parser.add_argument(
        "--compare-lexical-search-profile",
        choices=[
            LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
            FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        ],
        default=None,
        help=(
            "Optionally evaluate a second lexical profile against the exact "
            "same ParseHead and RetrievalIndexHead."
        ),
    )
    parser.add_argument(
        "--compare-semantic-profile",
        choices=[RQ2A_SEMANTIC_PROFILE_VERSION],
        default=None,
        help=(
            "Evaluate RQ2-A semantic-only recall against the primary lexical "
            "profile on the same ParseHead and RetrievalIndexHead."
        ),
    )
    parser.add_argument(
        "--compare-candidate-fusion-profile",
        choices=[RQ2B_CANDIDATE_FUSION_PROFILE_VERSION],
        default=None,
        help=(
            "Evaluate RQ2-B BM25F + Semantic candidate fusion against the "
            "primary RQ1-D profile on the same frozen authorities."
        ),
    )
    parser.add_argument(
        "--compare-rerank-profile",
        choices=[RQ2C_RERANK_PROFILE_VERSION],
        default=None,
        help=(
            "Evaluate RQ2-C frozen Top-20 BCE reranking against RQ1-D, "
            "RQ2-A semantic-only and RQ2-B fusion on shared authorities."
        ),
    )
    parser.add_argument(
        "--semantic-model-path",
        default=None,
        help="Local frozen BCE snapshot directory; network loading is forbidden.",
    )
    parser.add_argument(
        "--semantic-model-cache-dir",
        default=None,
        help="Optional local SentenceTransformer cache directory.",
    )
    parser.add_argument(
        "--reranker-model-path",
        default=None,
        help="Local frozen BCE reranker snapshot directory; network loading is forbidden.",
    )
    parser.add_argument(
        "--reranker-model-cache-dir",
        default=None,
        help="Optional local Transformers cache directory for the BCE reranker.",
    )
    parser.add_argument("--include-excerpts", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    parser_profile_version = str(args.parser_profile)
    query_optimizer_profile_version = str(args.query_optimizer_profile)
    lexical_search_profile_version = str(args.lexical_search_profile)
    comparison_query_optimizer_profile_version = (
        str(args.compare_query_optimizer_profile)
        if args.compare_query_optimizer_profile
        else None
    )
    comparison_lexical_search_profile_version = (
        str(args.compare_lexical_search_profile)
        if args.compare_lexical_search_profile
        else None
    )
    comparison_semantic_profile_version = (
        str(args.compare_semantic_profile)
        if args.compare_semantic_profile
        else None
    )
    comparison_candidate_fusion_profile_version = (
        str(args.compare_candidate_fusion_profile)
        if args.compare_candidate_fusion_profile
        else None
    )
    comparison_rerank_profile_version = (
        str(args.compare_rerank_profile)
        if args.compare_rerank_profile
        else None
    )
    comparison_enabled = bool(
        comparison_query_optimizer_profile_version is not None
        or comparison_lexical_search_profile_version is not None
        or comparison_semantic_profile_version is not None
        or comparison_candidate_fusion_profile_version is not None
        or comparison_rerank_profile_version is not None
    )
    effective_comparison_query_profile = (
        comparison_query_optimizer_profile_version
        or query_optimizer_profile_version
    )
    effective_comparison_lexical_profile = (
        comparison_lexical_search_profile_version
        or lexical_search_profile_version
    )
    effective_comparison_semantic_profile = (
        comparison_semantic_profile_version
        or (
            RQ2A_SEMANTIC_PROFILE_VERSION
            if (
                comparison_candidate_fusion_profile_version is not None
                or comparison_rerank_profile_version is not None
            )
            else None
        )
        or DISABLED_SEMANTIC_PROFILE_VERSION
    )
    effective_comparison_fusion_profile = (
        comparison_candidate_fusion_profile_version
        or (
            RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
            if comparison_rerank_profile_version is not None
            else None
        )
        or DISABLED_CANDIDATE_FUSION_PROFILE_VERSION
    )
    effective_comparison_rerank_profile = (
        comparison_rerank_profile_version or DISABLED_RERANK_PROFILE_VERSION
    )
    if comparison_enabled and (
        effective_comparison_query_profile == query_optimizer_profile_version
        and effective_comparison_lexical_profile == lexical_search_profile_version
        and effective_comparison_semantic_profile
        == DISABLED_SEMANTIC_PROFILE_VERSION
        and effective_comparison_fusion_profile
        == DISABLED_CANDIDATE_FUSION_PROFILE_VERSION
        and effective_comparison_rerank_profile == DISABLED_RERANK_PROFILE_VERSION
    ):
        raise ValueError("BID_PDF_C3_EVAL_COMPARISON_PROFILE_DUPLICATE")
    for query_profile, lexical_profile in (
        (query_optimizer_profile_version, lexical_search_profile_version),
        (
            effective_comparison_query_profile,
            effective_comparison_lexical_profile,
        ),
    ):
        if (
            lexical_profile == FIELD_AWARE_LEXICAL_PROFILE_VERSION
            and query_profile != QUERY_OPTIMIZER_PROFILE_VERSION
        ):
            raise ValueError("BID_PDF_C3_EVAL_LEXICAL_PROFILE_DEPENDENCY_INVALID")
    if (
        comparison_semantic_profile_version is not None
        or comparison_candidate_fusion_profile_version is not None
        or comparison_rerank_profile_version is not None
    ):
        if (
            effective_comparison_query_profile
            != QUERY_OPTIMIZER_PROFILE_VERSION
            or effective_comparison_lexical_profile
            != FIELD_AWARE_LEXICAL_PROFILE_VERSION
        ):
            raise ValueError("BID_RQ2A_EVAL_PROFILE_DEPENDENCY_INVALID")
        if not args.semantic_model_path:
            raise ValueError("BID_RQ2A_EVAL_BCE_SNAPSHOT_REQUIRED")
    if comparison_rerank_profile_version is not None and not args.reranker_model_path:
        raise ValueError("BID_RQ2C_EVAL_BCE_RERANKER_SNAPSHOT_REQUIRED")

    pdf_path = Path(args.pdf).resolve()
    case_path = Path(args.cases).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError("BID_PDF_C3_EVAL_PDF_NOT_FOUND")
    if output_dir.exists():
        raise FileExistsError("BID_PDF_C3_EVAL_OUTPUT_ALREADY_EXISTS")
    output_dir.mkdir(parents=True)
    database_path = output_dir / "isolated-runtime.sqlite3"
    document = pdf_path.read_bytes()
    document_sha256 = _sha256_bytes(document)
    dataset = _load_cases(case_path)
    expected_document_hash = str(dataset.get("document_sha256") or "").lower()
    if expected_document_hash and expected_document_hash != document_sha256:
        raise ValueError("BID_PDF_C3_EVAL_DOCUMENT_HASH_MISMATCH")

    engine, session_factory = _make_session_factory(database_path)
    try:
        db = session_factory()
        try:
            with db.begin():
                scope = _seed_runtime_authority(
                    db,
                    document_sha256=document_sha256,
                    document_size=len(document),
                    original_filename="restricted-real-document.pdf",
                )
            with db.begin():
                scheduled_parse = ensure_document_parse_run(
                    db,
                    document_version_id=scope.document_version_id,
                    parser_profile_version=parser_profile_version,
                    requested_at=utc_now(),
                )
                parse_run_id = str(scheduled_parse.run.id)
            with db.begin():
                claim = claim_document_parse_run(
                    db,
                    run_id=parse_run_id,
                    worker_id="pdf-c3-isolated-quality-baseline",
                    lease_seconds=3600,
                    max_attempts=1,
                    request_id=f"pdf-c3-quality:{document_sha256}",
                    causation_event_id=None,
                )
            if claim is None:
                raise RuntimeError("BID_PDF_C3_EVAL_PARSE_CLAIM_FAILED")

            parse_started = time.perf_counter()
            parse_result = parse_bid_document_bytes(
                content=document,
                expected_sha256=document_sha256,
                mime_type="application/pdf",
                parser_profile_version=parser_profile_version,
                pdf_native_layout_enabled=True,
                rq1a_structure_enabled=(
                    parser_profile_version
                    in {
                        PDF_RQ1A_PARSER_PROFILE_VERSION,
                        PDF_RQ1B_PARSER_PROFILE_VERSION,
                    }
                ),
                rq1b_quality_gate_enabled=(
                    parser_profile_version == PDF_RQ1B_PARSER_PROFILE_VERSION
                ),
            )
            parse_ms = (time.perf_counter() - parse_started) * 1000
            persist_started = time.perf_counter()
            with db.begin():
                completion = complete_document_parse_run(
                    db,
                    claim=claim,
                    result=parse_result,
                    request_id=f"pdf-c3-quality:{document_sha256}",
                    causation_event_id=None,
                )
            persist_ms = (time.perf_counter() - persist_started) * 1000
            if completion.status not in {"succeeded", "partial"}:
                raise RuntimeError("BID_PDF_C3_EVAL_PARSE_NOT_READY")

            index_started = time.perf_counter()
            with db.begin():
                scheduled_index = ensure_role_aware_retrieval_index(
                    db,
                    parse_run_id=parse_run_id,
                )
            with db.begin():
                built_index = build_role_aware_retrieval_index(
                    db,
                    index_id=scheduled_index.index_id,
                )
            index_ms = (time.perf_counter() - index_started) * 1000
            if built_index.status != "ready":
                raise RuntimeError("BID_PDF_C3_EVAL_INDEX_NOT_READY")

            semantic_provider = None
            semantic_build = None
            semantic_index_ms = 0.0
            if (
                comparison_semantic_profile_version is not None
                or comparison_candidate_fusion_profile_version is not None
                or comparison_rerank_profile_version is not None
            ):
                semantic_provider = IsolatedBceExactSemanticProvider(
                    model_path=Path(args.semantic_model_path).resolve(),
                    model_cache_dir=(
                        Path(args.semantic_model_cache_dir).resolve()
                        if args.semantic_model_cache_dir
                        else None
                    ),
                )
                semantic_started = time.perf_counter()
                with db.begin():
                    semantic_schedule = ensure_semantic_index(
                        db,
                        retrieval_index=(
                            db.query(BidEvidenceRetrievalIndex)
                            .filter(
                                BidEvidenceRetrievalIndex.id
                                == scheduled_index.index_id
                            )
                            .one()
                        ),
                        descriptor=semantic_provider.descriptor,
                    )
                semantic_build = build_semantic_index(
                    session_factory=session_factory,
                    semantic_index_id=semantic_schedule.semantic_index_id,
                    provider=semantic_provider,
                    worker_id="rq2a-isolated-quality-baseline",
                    lease_seconds=3600,
                )
                semantic_index_ms = (
                    time.perf_counter() - semantic_started
                ) * 1000
                if semantic_build.status != "ready":
                    raise RuntimeError(
                        "BID_RQ2A_EVAL_SEMANTIC_INDEX_NOT_READY:"
                        + str(semantic_build.error_code or "UNKNOWN")
                    )

            reranker_provider = None
            if comparison_rerank_profile_version is not None:
                reranker_provider = LocalBceCrossEncoderReranker(
                    model_path=str(Path(args.reranker_model_path).resolve()),
                    model_cache_dir=(
                        str(Path(args.reranker_model_cache_dir).resolve())
                        if args.reranker_model_cache_dir
                        else ""
                    ),
                    offline=True,
                    batch_size=8,
                )

            db.rollback()
            index = (
                db.query(BidEvidenceRetrievalIndex)
                .filter(BidEvidenceRetrievalIndex.id == scheduled_index.index_id)
                .one()
            )
            fragments = (
                db.query(BidEvidenceFragment)
                .filter(BidEvidenceFragment.parse_run_id == parse_run_id)
                .all()
            )
            entries = (
                db.query(BidEvidenceRetrievalEntry)
                .filter(BidEvidenceRetrievalEntry.index_id == index.id)
                .all()
            )
            atom_ids_by_child = {
                str(entry.retrieval_child_id): {
                    str(value) for value in entry.source_atom_ids_json or []
                }
                for entry in entries
            }
            child_ids_by_parent: dict[str, set[str]] = {}
            for entry in entries:
                child_ids_by_parent.setdefault(
                    str(entry.section_parent_id), set()
                ).add(str(entry.retrieval_child_id))
            target_groups_by_case: dict[str, list[dict[str, Any]]] = {}
            for case in dataset["cases"]:
                target_groups_by_case[str(case["case_id"])] = _resolve_target_groups(
                    fragments,
                    list(case.get("targets") or []),
                    child_ids_by_parent,
                )

            def evaluate_profile(
                query_profile_version: str,
                lexical_profile_version: str,
                semantic_profile_version: str,
                fusion_profile_version: str,
                rerank_profile_version: str,
                provider: IsolatedBceExactSemanticProvider | None,
                reranker: LocalBceCrossEncoderReranker | None,
            ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                service = BidEvidenceMcpService(
                    db,
                    scope=BidEvidenceMcpScope(
                        assessment_id=scope.assessment_id,
                        run_id=scope.analysis_run_id,
                        manifest_id=scope.manifest_id,
                    ),
                    retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                    query_optimizer_profile_version=query_profile_version,
                    lexical_search_profile_version=lexical_profile_version,
                    semantic_search_profile_version=semantic_profile_version,
                    candidate_fusion_profile_version=fusion_profile_version,
                    semantic_provider=provider,
                    rerank_profile_version=rerank_profile_version,
                    reranker_provider=reranker,
                )
                profile_rows: list[dict[str, Any]] = []
                for case in dataset["cases"]:
                    profile_rows.append(
                        _evaluate_case(
                            service,
                            case=case,
                            target_groups=target_groups_by_case[str(case["case_id"])],
                            atom_ids_by_child=atom_ids_by_child,
                            include_excerpts=bool(args.include_excerpts),
                        )
                    )
                profile_summary = _aggregate(
                    rows=profile_rows,
                    parse_result=parse_result,
                    index=index,
                    dataset=dataset,
                    document_sha256=document_sha256,
                    document_size=len(document),
                    parser_profile_version=parser_profile_version,
                    query_optimizer_profile_version=query_profile_version,
                    lexical_search_profile_version=lexical_profile_version,
                    semantic_search_profile_version=semantic_profile_version,
                    candidate_fusion_profile_version=fusion_profile_version,
                    rerank_profile_version=rerank_profile_version,
                    semantic_backend_id=(
                        provider.backend_id if provider is not None else None
                    ),
                    rerank_backend_id=(
                        reranker.descriptor.provider_id
                        if reranker is not None
                        else None
                    ),
                    parse_ms=parse_ms,
                    persist_ms=persist_ms,
                    index_ms=index_ms,
                )
                replay = service.search(
                    {
                        "query": str(dataset["cases"][0]["question"]),
                        "document_roles": ["tender_document"],
                        "top_k": 8,
                    }
                )
                profile_summary["retrieval"]["deterministic_replay_match"] = (
                    str(replay.get("result_hash") or "")
                    == str(profile_rows[0].get("result_hash") or "")
                )
                return profile_summary, profile_rows

            summary, rows = evaluate_profile(
                query_optimizer_profile_version,
                lexical_search_profile_version,
                DISABLED_SEMANTIC_PROFILE_VERSION,
                DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
                DISABLED_RERANK_PROFILE_VERSION,
                None,
                None,
            )
            (output_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            private_report = {
                "schema_version": SCHEMA_VERSION,
                "summary": summary,
                "cases": rows,
            }
            (output_dir / "private-report.json").write_text(
                json.dumps(private_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if comparison_enabled:
                semantic_only_summary = None
                semantic_only_rows = None
                if (
                    comparison_candidate_fusion_profile_version is not None
                    or comparison_rerank_profile_version is not None
                ):
                    semantic_only_summary, semantic_only_rows = evaluate_profile(
                        effective_comparison_query_profile,
                        effective_comparison_lexical_profile,
                        RQ2A_SEMANTIC_PROFILE_VERSION,
                        DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
                        DISABLED_RERANK_PROFILE_VERSION,
                        semantic_provider,
                        None,
                    )
                    (output_dir / "semantic-only-summary.json").write_text(
                        json.dumps(
                            semantic_only_summary,
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    (output_dir / "semantic-only-private-report.json").write_text(
                        json.dumps(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "summary": semantic_only_summary,
                                "cases": semantic_only_rows,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                fusion_summary = None
                fusion_rows = None
                if comparison_rerank_profile_version is not None:
                    fusion_summary, fusion_rows = evaluate_profile(
                        effective_comparison_query_profile,
                        effective_comparison_lexical_profile,
                        RQ2A_SEMANTIC_PROFILE_VERSION,
                        RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
                        DISABLED_RERANK_PROFILE_VERSION,
                        semantic_provider,
                        None,
                    )
                    (output_dir / "fusion-summary.json").write_text(
                        json.dumps(fusion_summary, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    (output_dir / "fusion-private-report.json").write_text(
                        json.dumps(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "summary": fusion_summary,
                                "cases": fusion_rows,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                comparison_summary, comparison_rows = evaluate_profile(
                    effective_comparison_query_profile,
                    effective_comparison_lexical_profile,
                    effective_comparison_semantic_profile,
                    effective_comparison_fusion_profile,
                    effective_comparison_rerank_profile,
                    semantic_provider,
                    reranker_provider,
                )
                (output_dir / "comparison-summary.json").write_text(
                    json.dumps(comparison_summary, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                comparison_private_report = {
                    "schema_version": SCHEMA_VERSION,
                    "summary": comparison_summary,
                    "cases": comparison_rows,
                }
                (output_dir / "comparison-private-report.json").write_text(
                    json.dumps(
                        comparison_private_report,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                retrieval_metrics = (
                    "hit_at_5",
                    "target_recall_at_5",
                    "precision_at_5",
                    "mrr_at_5",
                    "ndcg_at_5",
                    "hit_at_8",
                    "target_recall_at_8",
                    "mrr_at_8",
                    "ndcg_at_8",
                    "read_target_recall_at_5",
                    "atom_only_violation_count",
                    "citable_target_availability",
                    "average_planned_query_count",
                )
                deltas = {
                    name: round(
                        float(comparison_summary["retrieval"][name])
                        - float(summary["retrieval"][name]),
                        6,
                    )
                    for name in retrieval_metrics
                }
                semantic_only_deltas = (
                    {
                        name: round(
                            float(semantic_only_summary["retrieval"][name])
                            - float(summary["retrieval"][name]),
                            6,
                        )
                        for name in retrieval_metrics
                    }
                    if semantic_only_summary is not None
                    else None
                )
                fusion_minus_semantic_only = (
                    {
                        name: round(
                            float(
                                (fusion_summary or comparison_summary)["retrieval"][
                                    name
                                ]
                            )
                            - float(semantic_only_summary["retrieval"][name]),
                            6,
                        )
                        for name in retrieval_metrics
                    }
                    if semantic_only_summary is not None
                    else None
                )
                rerank_minus_fusion = (
                    {
                        name: round(
                            float(comparison_summary["retrieval"][name])
                            - float(fusion_summary["retrieval"][name]),
                            6,
                        )
                        for name in retrieval_metrics
                    }
                    if fusion_summary is not None
                    else None
                )
                ab_summary = {
                    "schema_version": (
                        "bid.pdf-c3.lightweight-rerank-ab.v1"
                        if comparison_rerank_profile_version is not None
                        else "bid.pdf-c3.candidate-fusion-ab.v1"
                        if comparison_candidate_fusion_profile_version is not None
                        else (
                            "bid.pdf-c3.semantic-retrieval-ab.v1"
                            if comparison_semantic_profile_version is not None
                            else (
                                "bid.pdf-c3.lexical-search-ab.v1"
                                if comparison_lexical_search_profile_version is not None
                                else "bid.pdf-c3.query-optimizer-ab.v1"
                            )
                        )
                    ),
                    "document_sha256": document_sha256,
                    "dataset_hash": str(summary["dataset_hash"]),
                    "parser_profile_version": parser_profile_version,
                    "retrieval_profile_version": ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                    "shared_authority": {
                        "database": str(database_path.name),
                        "parse_run_id": str(parse_run_id),
                        "retrieval_index_id": str(index.id),
                        "retrieval_index_input_hash": str(index.input_hash),
                        "retrieval_index_result_hash": str(index.result_hash),
                        "semantic_index_id": (
                            str(semantic_build.semantic_index_id)
                            if semantic_build is not None
                            else None
                        ),
                        "semantic_index_result_hash": (
                            str(semantic_build.result_hash)
                            if semantic_build is not None
                            else None
                        ),
                    },
                    "primary_profile_version": query_optimizer_profile_version,
                    "comparison_profile_version": effective_comparison_query_profile,
                    "primary_profiles": {
                        "query_optimizer": query_optimizer_profile_version,
                        "lexical_search": lexical_search_profile_version,
                        "semantic_search": DISABLED_SEMANTIC_PROFILE_VERSION,
                        "candidate_fusion": DISABLED_CANDIDATE_FUSION_PROFILE_VERSION,
                        "rerank": DISABLED_RERANK_PROFILE_VERSION,
                    },
                    "comparison_profiles": {
                        "query_optimizer": effective_comparison_query_profile,
                        "lexical_search": effective_comparison_lexical_profile,
                        "semantic_search": effective_comparison_semantic_profile,
                        "candidate_fusion": effective_comparison_fusion_profile,
                        "rerank": effective_comparison_rerank_profile,
                    },
                    "rerank_validation": {
                        "provider_id": (
                            reranker_provider.descriptor.provider_id
                            if reranker_provider is not None
                            else None
                        ),
                        "model_id": (
                            reranker_provider.descriptor.model_id
                            if reranker_provider is not None
                            else None
                        ),
                        "model_revision": (
                            reranker_provider.descriptor.model_revision
                            if reranker_provider is not None
                            else None
                        ),
                        "offline_cache_only": reranker_provider is not None,
                        "generation_model_executed": False,
                    },
                    "semantic_validation": {
                        "backend_id": (
                            semantic_provider.backend_id
                            if semantic_provider is not None
                            else None
                        ),
                        "production_milvus_adapter_executed": False,
                        "real_frozen_bce_embeddings": (
                            semantic_provider is not None
                        ),
                        "semantic_index_build_ms": round(
                            semantic_index_ms,
                            3,
                        ),
                    },
                    "primary_retrieval": summary["retrieval"],
                    "semantic_only_profiles": (
                        {
                            "query_optimizer": effective_comparison_query_profile,
                            "lexical_search": effective_comparison_lexical_profile,
                            "semantic_search": RQ2A_SEMANTIC_PROFILE_VERSION,
                            "candidate_fusion": (
                                DISABLED_CANDIDATE_FUSION_PROFILE_VERSION
                            ),
                            "rerank": DISABLED_RERANK_PROFILE_VERSION,
                        }
                        if semantic_only_summary is not None
                        else None
                    ),
                    "semantic_only_retrieval": (
                        semantic_only_summary["retrieval"]
                        if semantic_only_summary is not None
                        else None
                    ),
                    "fusion_profiles": (
                        {
                            "query_optimizer": effective_comparison_query_profile,
                            "lexical_search": effective_comparison_lexical_profile,
                            "semantic_search": RQ2A_SEMANTIC_PROFILE_VERSION,
                            "candidate_fusion": RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
                            "rerank": DISABLED_RERANK_PROFILE_VERSION,
                        }
                        if fusion_summary is not None
                        else None
                    ),
                    "fusion_retrieval": (
                        fusion_summary["retrieval"]
                        if fusion_summary is not None
                        else None
                    ),
                    "comparison_retrieval": comparison_summary["retrieval"],
                    "semantic_only_minus_primary": semantic_only_deltas,
                    "comparison_minus_primary": deltas,
                    "fusion_minus_semantic_only": fusion_minus_semantic_only,
                    "rerank_minus_fusion": rerank_minus_fusion,
                    "invariants": {
                        "same_database": True,
                        "same_parse_head": True,
                        "same_retrieval_index_head": True,
                        "same_semantic_index_head": (
                            semantic_build is not None
                            if comparison_rerank_profile_version is not None
                            else True
                        ),
                        "frozen_fusion_candidate_pool_identical": (
                            all(
                                str(
                                    (fusion_row.get("candidate_fusion") or {}).get(
                                        "result_hash"
                                    )
                                    or ""
                                )
                                == str(
                                    (comparison_row.get("candidate_fusion") or {}).get(
                                        "result_hash"
                                    )
                                    or ""
                                )
                                for fusion_row, comparison_row in zip(
                                    fusion_rows or [],
                                    comparison_rows,
                                    strict=True,
                                )
                            )
                            if fusion_rows is not None
                            else True
                        ),
                        "zero_promotion_identity": all(
                            int(
                                (row.get("candidate_rerank") or {}).get(
                                    "promotion_count"
                                )
                                or 0
                            )
                            != 0
                            or (
                                (row.get("candidate_rerank") or {}).get(
                                    "baseline_child_keys"
                                )
                                == (row.get("candidate_rerank") or {}).get(
                                    "final_child_keys"
                                )
                            )
                            for row in comparison_rows
                        ),
                        "atom_only_violation_unchanged": (
                            summary["retrieval"]["atom_only_violation_count"]
                            == comparison_summary["retrieval"][
                                "atom_only_violation_count"
                            ]
                        ),
                        "deterministic_replay_match": bool(
                            summary["retrieval"]["deterministic_replay_match"]
                            and (
                                semantic_only_summary is None
                                or semantic_only_summary["retrieval"][
                                    "deterministic_replay_match"
                                ]
                            )
                            and (
                                fusion_summary is None
                                or fusion_summary["retrieval"][
                                    "deterministic_replay_match"
                                ]
                            )
                            and comparison_summary["retrieval"][
                                "deterministic_replay_match"
                            ]
                        ),
                    },
                }
                (output_dir / "ab-summary.json").write_text(
                    json.dumps(ab_summary, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            if not args.quiet:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
        finally:
            db.close()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
