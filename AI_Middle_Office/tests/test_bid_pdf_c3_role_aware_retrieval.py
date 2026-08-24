from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401
import app.core.config as config_module
import app.services.bid_tool_execution as tool_execution_module
from app.core.database import Base
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidFileObject,
    BidManifestDocument,
)
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidEvidenceFragment,
)
from app.models.bid_assessment_retrieval import (
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalHead,
    BidEvidenceRetrievalIndex,
)
from app.models.bid_assessment_semantic import BidEvidenceSemanticIndex
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.user import User
from app.services.bid_assessment_eventing import canonical_hash, utc_now
from app.services.bid_document_parse_runs import ensure_document_parse_run
from app.services.bid_document_parse_worker import (
    DocumentParseResult,
    EvidenceFragmentResult,
    ParseUnitResult,
    claim_document_parse_run,
    complete_document_parse_run,
)
from app.services.bid_evidence_chunk_builder import normalize_evidence_text
from app.services.bid_evidence_retrieval_index import (
    ROLE_AWARE_PARSER_PROFILE_VERSIONS,
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
    build_role_aware_retrieval_index,
    ensure_role_aware_retrieval_index,
    invalidate_stale_retrieval_indexes,
    process_pending_retrieval_indexes,
    reconcile_current_role_aware_parse_heads,
    retrieval_index_input_hash,
)
from app.services.bid_evidence_semantic_index import (
    RQ2A_SEMANTIC_PROFILE_VERSION,
    build_semantic_index,
    ensure_semantic_index,
)
from app.services.bid_field_aware_lexical import (
    FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
    clear_field_aware_lexical_cache,
)
from app.services.bid_hybrid_candidate_fusion import (
    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
)
from app.services.bid_lightweight_reranker import (
    RQ2C_MODEL_ID,
    RQ2C_MODEL_REVISION,
    RQ2C_PROVIDER_ID,
    RQ2C_RERANK_PROFILE_VERSION,
    RerankProviderResult,
    RerankProviderScore,
    RerankerModelDescriptor,
)
from app.services.bid_mvp1_authority import BidMvp1AuthorityError, _scoped_evidence
from app.services.bid_lot_detection_runs import build_manifest_parse_set
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    BidParseQualityGateBlocked,
    evaluate_pdf_parse_quality,
)
from app.services.bid_run_bootstrap import _manifest_parse_quality_reasons
from app.services.bid_query_optimizer import (
    QUERY_OPTIMIZER_PROFILE_VERSION,
    QUERY_PLAN_CONTRACT_VERSION,
)
from app.services.bid_semantic_vector_provider import (
    RQ2A_DISTANCE_METRIC,
    RQ2A_EMBEDDING_DIMENSION,
    RQ2A_EMBEDDING_MODEL_ID,
    RQ2A_EMBEDDING_MODEL_REVISION,
    RQ2A_PROVIDER_ID,
    BidSemanticProviderUnavailable,
    SemanticModelDescriptor,
    SemanticProviderHit,
    SemanticVectorReceipt,
)
from app.services.bid_tool_execution import (
    BidEvidenceMcpAdapter,
    BidToolDispatchConflict,
    _adapter_spec,
)
from mcp_servers.bid_assessment_evidence.service import (
    BidEvidenceMcpError,
    BidEvidenceMcpScope,
    BidEvidenceMcpService,
)


ROOT = Path(__file__).resolve().parents[1]
PARSER_PROFILE = "bid-document-parser-profile-v2-pdf-native-layout"


def test_pdf_c3_accepts_all_frozen_role_aware_parser_profiles() -> None:
    assert ROLE_AWARE_PARSER_PROFILE_VERSIONS == {
        "bid-document-parser-profile-v2-pdf-native-layout",
        "bid-document-parser-profile-v3-pdf-structure-rq1a",
        "bid-document-parser-profile-v4-pdf-quality-gated-rq1b",
    }


def test_pdf_c3_legacy_input_hash_payload_is_unchanged_by_rq1b() -> None:
    run = SimpleNamespace(
        id="parse-run-legacy",
        document_version_id="document-version-legacy",
        parser_profile_version="bid-document-parser-profile-v3-pdf-structure-rq1a",
        result_hash="a" * 64,
        warnings_json=[],
        quality_score=100,
        quality_grade="high",
    )
    expected = canonical_hash(
        {
            "contract_version": "bid.evidence.retrieval-index.v1",
            "profile_version": "bid-evidence-retrieval-profile-v2-role-aware",
            "role_contract_version": "bid.evidence.chunk.v2",
            "document_version_id": "document-version-legacy",
            "parse_run_id": "parse-run-legacy",
            "source_result_hash": "a" * 64,
        }
    )
    assert retrieval_index_input_hash(run) == expected


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _SemanticProvider:
    def __init__(self):
        self.descriptor = SemanticModelDescriptor(
            provider_id=RQ2A_PROVIDER_ID,
            model_id=RQ2A_EMBEDDING_MODEL_ID,
            model_revision=RQ2A_EMBEDDING_MODEL_REVISION,
            dimension=RQ2A_EMBEDDING_DIMENSION,
            distance_metric=RQ2A_DISTANCE_METRIC,
            normalized_embeddings=True,
        )
        self.namespaces = {}

    def upsert_documents(self, *, namespace, provider_request_id, documents):
        assert provider_request_id.startswith("bid-semantic-index:")
        stored = dict(self.namespaces.get(namespace, {}))
        receipts = []
        for document in documents:
            receipt = SemanticVectorReceipt(
                provider_record_id=document.provider_record_id,
                retrieval_child_key=document.retrieval_child_key,
                source_entry_hash=document.source_entry_hash,
                embedding_text_hash=document.embedding_text_hash,
                vector_hash=_digest(document.provider_record_id + document.text),
                vector_dimension=RQ2A_EMBEDDING_DIMENSION,
            )
            stored[document.provider_record_id] = (document, receipt)
            receipts.append(receipt)
        self.namespaces[namespace] = stored
        return tuple(receipts)

    def search(self, *, namespace, query, top_k):
        rows = []
        for document, receipt in self.namespaces[namespace].values():
            rows.append(
                SemanticProviderHit(
                    provider_record_id=receipt.provider_record_id,
                    retrieval_child_key=receipt.retrieval_child_key,
                    source_entry_hash=receipt.source_entry_hash,
                    embedding_text_hash=receipt.embedding_text_hash,
                    vector_hash=receipt.vector_hash,
                    score=(0.93 if "acceptance" in document.text.lower() else 0.2),
                )
            )
        rows.sort(key=lambda row: (-row.score, row.provider_record_id))
        return tuple(rows[:top_k])


class _RerankerProvider:
    descriptor = RerankerModelDescriptor(
        provider_id=RQ2C_PROVIDER_ID,
        model_id=RQ2C_MODEL_ID,
        model_revision=RQ2C_MODEL_REVISION,
        max_sequence_length=512,
        score_transform="sigmoid",
    )

    def score(self, *, query, candidates):
        assert query
        return RerankProviderResult(
            descriptor=self.descriptor,
            scores=tuple(
                RerankProviderScore(
                    child_key=value.child_key,
                    score=round(0.95 - value.fusion_rank * 0.01, 8),
                )
                for value in candidates
            ),
        )


@pytest.fixture()
def c3_session_factory(tmp_path):
    database_path = tmp_path / "pdf-c3.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    yield factory
    engine.dispose()


@dataclass(frozen=True)
class _C3Seed:
    assessment_id: str
    manifest_id: str
    document_version_id: str
    parse_run_id: str
    analysis_run_id: str
    parent_key: str
    child_key: str
    atom_keys: tuple[str, ...]


def _seed_runtime_authority(db, *, owner_id: int, assessment_id: str, manifest_id: str) -> str:
    marker = uuid.uuid4().hex
    now = utc_now()
    scope = BidAssessmentScope(
        id=f"scope_{uuid.uuid4().hex}",
        assessment_id=assessment_id,
        version=1,
        scope_type="lot",
        source_lot_candidate_id=None,
        selected_lot_snapshot_json={"lot_name": "Synthetic lot"},
        scope_hash=canonical_hash({"marker": marker, "type": "scope"}),
        created_by=owner_id,
    )
    enterprise = BidEnterpriseSnapshot(
        id=f"enterprise_{uuid.uuid4().hex}",
        version=f"enterprise-{marker}",
        as_of=now,
        snapshot_hash=None,
        source_catalog_version="test-v1",
        status="building",
        created_by=owner_id,
        row_version=1,
    )

    def artifact(model, name: str, **extra):
        return model(
            id=f"{name}_{uuid.uuid4().hex}",
            version=f"{name}-{marker}",
            status="draft",
            active_slot_key=None,
            artifact_ref=f"memory://{name}/{marker}",
            artifact_hash=canonical_hash({"marker": marker, "type": name}),
            authored_by=owner_id,
            row_version=1,
            **extra,
        )

    rule_set = artifact(
        BidRuleSet,
        "rules",
        test_cases_ref=f"memory://rules/{marker}/tests",
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
    run_id = f"run_{uuid.uuid4().hex}"
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
            input_fingerprint=canonical_hash({"marker": marker, "type": "fingerprint"}),
            input_hash=canonical_hash({"marker": marker, "type": "input"}),
            evaluation_time=now,
            current_stage="extract",
            row_version=1,
        )
    )
    db.flush()
    return run_id


def _locator(
    *,
    role: str,
    evidence_key: str,
    text: str,
    page_no: int = 1,
    is_citable: bool,
    context_prefix: str | None = None,
) -> dict:
    value = {
        "schema_version": "bid.evidence.chunk.v2",
        "fragment_role": role,
        "evidence_key": evidence_key,
        "page_no": page_no,
        "page_end": page_no,
        "text_hash": _digest(text),
        "is_citable": is_citable,
    }
    retrieval_text = text
    if context_prefix:
        value["context_prefix"] = context_prefix
        retrieval_text = normalize_evidence_text(f"{context_prefix}\n\n{text}")
    value["retrieval_hash"] = _digest(retrieval_text)
    return value


def _seed_c3(session_factory) -> _C3Seed:
    token = uuid.uuid4().hex
    assessment_id = f"assessment_{uuid.uuid4().hex}"
    document_version_id = f"version_{uuid.uuid4().hex}"
    manifest_id = f"manifest_{uuid.uuid4().hex}"
    db = session_factory()
    try:
        with db.begin():
            owner = User(
                username=f"pdf-c3-{token}",
                hashed_password="not-used",
                role="user",
                role_version=1,
                quota=10,
                quota_reserved=0,
                is_active=True,
                must_change_password=False,
            )
            db.add(owner)
            db.flush()
            assessment = BidAssessment(
                id=assessment_id,
                title="PDF-C3 synthetic",
                client_name="Local fixture",
                lifecycle_status="active",
                business_status="preparing",
                created_by=int(owner.id),
                updated_by=int(owner.id),
                row_version=1,
            )
            db.add(assessment)
            file_id = f"file_{uuid.uuid4().hex}"
            document_id = f"document_{uuid.uuid4().hex}"
            db.add(
                BidFileObject(
                    id=file_id,
                    sha256=_digest(f"content:{token}"),
                    object_key=f"local/{token}.pdf",
                    size_bytes=100,
                    mime_type="application/pdf",
                    storage_status="available",
                    created_by=int(owner.id),
                    row_version=1,
                )
            )
            db.add(
                BidDocument(
                    id=document_id,
                    logical_identity_key=f"pdf-c3:{token}",
                    logical_name="synthetic.pdf",
                    document_type="tender_document",
                    created_by=int(owner.id),
                )
            )
            db.flush()
            db.add(
                BidDocumentVersion(
                    id=document_version_id,
                    document_id=document_id,
                    file_object_id=file_id,
                    version_no=1,
                    original_filename="synthetic.pdf",
                    source_metadata_hash=_digest(f"metadata:{token}"),
                    source_metadata_json={"source": "synthetic"},
                    created_by=int(owner.id),
                )
            )
            db.add(
                BidDocumentManifest(
                    id=manifest_id,
                    assessment_id=assessment_id,
                    version=1,
                    manifest_hash=_digest(f"manifest:{token}"),
                    committed_by=int(owner.id),
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
            assessment.current_manifest_id = manifest_id
            analysis_run_id = _seed_runtime_authority(
                db,
                owner_id=int(owner.id),
                assessment_id=assessment_id,
                manifest_id=manifest_id,
            )
            assessment.active_run_id = analysis_run_id
        db.close()

        db = session_factory()
        with db.begin():
            schedule = ensure_document_parse_run(
                db,
                document_version_id=document_version_id,
                parser_profile_version=PARSER_PROFILE,
                requested_at=utc_now(),
            )
            parse_run_id = str(schedule.run.id)
        db.close()

        db = session_factory()
        with db.begin():
            claim = claim_document_parse_run(
                db,
                run_id=parse_run_id,
                worker_id="pdf-c3-test",
                lease_seconds=300,
                max_attempts=3,
                request_id=f"request-{token}",
                causation_event_id=None,
            )
            assert claim is not None
        db.close()

        parent_text = "Parent navigation alpha"
        child_text = "Terminal construction includes testing and acceptance."
        atom_texts = (
            "Terminal construction includes testing.",
            "Acceptance records are required.",
        )
        parent_key = f"section:{_digest('parent:' + token)}"
        child_key = f"child:{_digest('child:' + token)}"
        atom_keys = tuple(
            f"atom:{_digest(f'atom:{index}:{token}') }"
            for index in range(len(atom_texts))
        )
        context_prefix = (
            "[文档] synthetic.pdf\n[章节] 1 Scope\n[页码] 1\n[类型] paragraph"
        )
        evidence = [
            EvidenceFragmentResult(
                evidence_key=parent_key,
                unit_key="page:1",
                locator_type="section",
                locator=_locator(
                    role="section_parent",
                    evidence_key=parent_key,
                    text=parent_text,
                    is_citable=False,
                ),
                normalized_text=parent_text,
                ordinal=0,
            ),
            EvidenceFragmentResult(
                evidence_key=child_key,
                unit_key="page:1",
                locator_type="section",
                locator=_locator(
                    role="retrieval_child",
                    evidence_key=child_key,
                    text=child_text,
                    is_citable=False,
                    context_prefix=context_prefix,
                ),
                normalized_text=child_text,
                ordinal=1,
                parent_key=parent_key,
            ),
        ]
        for ordinal, (atom_key, atom_text) in enumerate(
            zip(atom_keys, atom_texts),
            2,
        ):
            evidence.append(
                EvidenceFragmentResult(
                    evidence_key=atom_key,
                    unit_key="page:1",
                    locator_type="page_bbox",
                    locator=_locator(
                        role="evidence_atom",
                        evidence_key=atom_key,
                        text=atom_text,
                        is_citable=True,
                    ),
                    normalized_text=atom_text,
                    ordinal=ordinal,
                    parent_key=child_key,
                )
            )
        db = session_factory()
        with db.begin():
            complete_document_parse_run(
                db,
                claim=claim,
                result=DocumentParseResult(
                    status="succeeded",
                    quality_grade="high",
                    quality_score=100,
                    ocr_status="not_applicable",
                    units=(
                        ParseUnitResult(
                            unit_key="page:1",
                            unit_type="page",
                            ordinal=0,
                            page_no=1,
                            content_source="native",
                            status="succeeded",
                            ocr_status="not_applicable",
                        ),
                    ),
                    evidence=tuple(evidence),
                ),
                request_id=f"request-{token}",
                causation_event_id=None,
            )
        return _C3Seed(
            assessment_id=assessment_id,
            manifest_id=manifest_id,
            document_version_id=document_version_id,
            parse_run_id=parse_run_id,
            analysis_run_id=analysis_run_id,
            parent_key=parent_key,
            child_key=child_key,
            atom_keys=atom_keys,
        )
    finally:
        db.close()


def _build_index(session_factory, seed: _C3Seed):
    db = session_factory()
    try:
        with db.begin():
            scheduled = ensure_role_aware_retrieval_index(
                db,
                parse_run_id=seed.parse_run_id,
            )
        with db.begin():
            built = build_role_aware_retrieval_index(
                db,
                index_id=scheduled.index_id,
            )
        return scheduled, built
    finally:
        db.close()


def _build_semantic_index(session_factory, provider: _SemanticProvider):
    db = session_factory()
    try:
        with db.begin():
            retrieval = db.query(BidEvidenceRetrievalIndex).one()
            scheduled = ensure_semantic_index(
                db,
                retrieval_index=retrieval,
                descriptor=provider.descriptor,
            )
    finally:
        db.close()
    built = build_semantic_index(
        session_factory=session_factory,
        semantic_index_id=scheduled.semantic_index_id,
        provider=provider,
        worker_id="rq2a-c3-test",
    )
    assert built.status == "ready"
    return scheduled, built


def test_pdf_c3_machine_profile_and_schema() -> None:
    profile = json.loads(
        (ROOT / "contracts/bid_assessment/v1/pdf-c3-role-aware-retrieval-profile.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas/bid_assessment/v1/evidence-retrieval.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert profile["profile_version"] == ROLE_AWARE_RETRIEVAL_PROFILE_VERSION
    assert profile["role_policy"] == {
        "primary_search_role": "retrieval_child",
        "auxiliary_search_role": "section_parent",
        "read_output_role": "evidence_atom",
        "citable_roles": ["evidence_atom"],
        "legacy_role_fallback": False,
    }
    assert profile["invalidation"]["stale_fallback_allowed"] is False


def test_pdf_c3_rq1b_blocked_quality_never_schedules_index(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    blocked = evaluate_pdf_parse_quality(
        layout=SimpleNamespace(
            pages=(
                SimpleNamespace(
                    status="partial",
                    content_source="none",
                    ocr_status="not_requested",
                ),
            ),
            warnings=(),
        ),
        chunks=SimpleNamespace(
            metrics={
                "retrieval_child_count": 0,
                "evidence_atom_count": 0,
                "heading_block_count": 1,
                "citable_heading_atom_count": 0,
            },
            warnings=(),
        ),
    )
    db = c3_session_factory()
    try:
        with db.begin():
            run = (
                db.query(BidDocumentParseRun)
                .filter(BidDocumentParseRun.id == seed.parse_run_id)
                .one()
            )
            run.parser_profile_version = PDF_RQ1B_PARSER_PROFILE_VERSION
            run.quality_score = blocked.score
            run.quality_grade = blocked.grade
            run.warnings_json = [blocked.to_warning()]
        with pytest.raises(BidParseQualityGateBlocked):
            with db.begin():
                ensure_role_aware_retrieval_index(
                    db,
                    parse_run_id=seed.parse_run_id,
                )
        assert db.query(BidEvidenceRetrievalIndex).count() == 0
        parse_set = build_manifest_parse_set(db, manifest_id=seed.manifest_id)
        assert parse_set.status == "failed"
        assert parse_set.blocking_reasons == (
            f"parse_quality_gate_blocked_lot_detection:{seed.document_version_id}",
        )
        assert _manifest_parse_quality_reasons(
            db,
            manifest_id=seed.manifest_id,
        ) == [
            f"parse_quality_gate_blocked_assessment:{seed.document_version_id}"
        ]
    finally:
        db.close()


def test_pdf_c3_index_build_is_idempotent_and_promotes_current_head(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    scheduled, built = _build_index(c3_session_factory, seed)
    assert built.status == "ready"
    assert built.entry_count == 1
    db = c3_session_factory()
    try:
        head = db.query(BidEvidenceRetrievalHead).one()
        entry = db.query(BidEvidenceRetrievalEntry).one()
        assert str(head.current_index_id) == scheduled.index_id
        assert str(head.current_parse_run_id) == seed.parse_run_id
        assert entry.retrieval_child_key == seed.child_key
        assert list(entry.source_atom_keys_json) == list(seed.atom_keys)
        with db.begin_nested():
            duplicate = ensure_role_aware_retrieval_index(
                db,
                parse_run_id=seed.parse_run_id,
            )
        assert duplicate.created is False
        assert duplicate.index_id == scheduled.index_id
        with db.begin_nested():
            repeated = build_role_aware_retrieval_index(
                db,
                index_id=scheduled.index_id,
            )
        assert repeated.result_hash == built.result_hash
    finally:
        db.close()


def test_pdf_c3_reconcile_backfills_current_terminal_c2_parse_once(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    db = c3_session_factory()
    try:
        with db.begin():
            assert reconcile_current_role_aware_parse_heads(db) == 1
        index = db.query(BidEvidenceRetrievalIndex).one()
        assert str(index.parse_run_id) == seed.parse_run_id
        assert str(index.status) == "queued"
        db.rollback()
        with db.begin():
            assert reconcile_current_role_aware_parse_heads(db) == 0
    finally:
        db.close()


def test_pdf_c3_mcp_search_returns_child_and_read_returns_only_atoms(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        )
        searched = service.search(
            {
                "query": "terminal construction acceptance",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert searched["contract"] == "bid-assessment-evidence-mcp/v2"
        assert searched["retrieval_mode"] == (
            "role_aware_child_bm25_parent_rrf"
        )
        assert len(searched["hits"]) == 1
        hit = searched["hits"][0]
        assert hit["fragment_role"] == "retrieval_child"
        assert hit["is_citable"] is False
        assert hit["context_read"] is False
        assert len(hit["source_atom_ids"]) == 2

        parent_only = service.search(
            {
                "query": "parent navigation alpha",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert len(parent_only["hits"]) == 1
        assert parent_only["hits"][0]["evidence_id"] == hit["evidence_id"]
        assert parent_only["hits"][0]["matched_queries"] == []

        read = service.read(
            {
                "evidence_ids": [hit["evidence_id"]],
                "expansion": "none",
            }
        )
        assert len(read["items"]) == 2
        assert all(item["fragment_role"] == "evidence_atom" for item in read["items"])
        assert all(item["is_citable"] is True for item in read["items"])
        assert all(item["context_read"] is True for item in read["items"])

        parent_id = hit["section_parent_id"]
        with pytest.raises(BidEvidenceMcpError, match="REFERENCE_ROLE_INVALID"):
            service.read({"evidence_ids": [parent_id], "expansion": "none"})
    finally:
        db.close()


def test_rq1c_mcp_search_executes_hashed_query_plan_v2_without_reindex(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        )
        searched = service.search(
            {
                "query": "terminal construction acceptance 完工日期按什么条件认定",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert searched["contract"] == "bid-assessment-evidence-mcp/v2"
        assert searched["query_optimizer_profile_version"] == (
            QUERY_OPTIMIZER_PROFILE_VERSION
        )
        assert searched["query_plan"]["schema_version"] == (
            QUERY_PLAN_CONTRACT_VERSION
        )
        assert searched["query_plan"]["query_count"] > 1
        assert searched["retrieval_mode"] == (
            "role_aware_child_bm25_parent_weighted_rrf_rq1c"
        )
        assert len(searched["hits"]) == 1
        assert "lexical_search_profile_version" not in searched
        assert db.query(BidEvidenceRetrievalIndex).count() == 1
    finally:
        db.close()


def test_rq1d_mcp_search_projects_fields_and_reuses_the_same_c3_index(
    c3_session_factory,
) -> None:
    clear_field_aware_lexical_cache()
    seed = _seed_c3(c3_session_factory)
    scheduled, built = _build_index(c3_session_factory, seed)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        )
        searched = service.search(
            {
                "query": "terminal construction acceptance 完工日期按什么条件认定",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert searched["retrieval_mode"] == (
            "role_aware_child_bm25_baseline_bm25f_parent_weighted_rrf_rq1d"
        )
        assert searched["lexical_search_profile_version"] == (
            FIELD_AWARE_LEXICAL_PROFILE_VERSION
        )
        assert searched["lexical_projection"]["projection_count"] == 1
        assert searched["lexical_projection"]["channels"] == [
            "section_heading",
            "table_key",
            "table_value",
            "table_row",
            "body",
        ]
        assert len(searched["lexical_projection_set_hash"]) == 64
        assert searched["hits"][0]["matched_channels"] == ["body"]
        assert len(searched["hits"][0]["lexical_projection_hash"]) == 64
        assert db.query(BidEvidenceRetrievalIndex).count() == 1
        current = db.query(BidEvidenceRetrievalIndex).one()
        assert str(current.id) == str(scheduled.index_id)
        assert str(current.result_hash) == str(built.result_hash)
        replay = service.search(
            {
                "query": "terminal construction acceptance 完工日期按什么条件认定",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert replay["result_hash"] == searched["result_hash"]
    finally:
        db.close()


def test_rq2a_mcp_v5_search_is_semantic_child_only_and_read_remains_atom_only(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            semantic_provider=provider,
        )
        searched = service.search(
            {
                "query": "完工验收需要什么资料",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert searched["retrieval_mode"] == (
            "role_aware_child_semantic_query_rrf_rq2a"
        )
        assert searched["semantic_search_profile_version"] == (
            RQ2A_SEMANTIC_PROFILE_VERSION
        )
        assert searched["semantic_model"] == provider.descriptor.stable_payload()
        assert len(searched["semantic_index_set_hash"]) == 64
        assert len(searched["hits"]) == 1
        hit = searched["hits"][0]
        assert hit["fragment_role"] == "retrieval_child"
        assert hit["is_citable"] is False
        assert hit["context_read"] is False
        assert len(hit["semantic_vector_hash"]) == 64
        assert all(
            route["executed_mode"] == "semantic"
            for route in searched["retrieval_routes"]
        )
        read = service.read(
            {"evidence_ids": [hit["evidence_id"]], "expansion": "none"}
        )
        assert read["items"]
        assert all(item["fragment_role"] == "evidence_atom" for item in read["items"])
        assert all(item["is_citable"] is True for item in read["items"])
    finally:
        db.close()


def test_rq2a_service_requires_all_profiles_and_a_provider(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    db = c3_session_factory()
    try:
        scope = BidEvidenceMcpScope(
            assessment_id=seed.assessment_id,
            run_id=seed.analysis_run_id,
            manifest_id=seed.manifest_id,
        )
        with pytest.raises(BidEvidenceMcpError, match="SEMANTIC_PROFILE_DISABLED"):
            BidEvidenceMcpService(
                db,
                scope=scope,
                retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
                lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
                semantic_provider=None,
            )
    finally:
        db.close()


def test_rq2b_mcp_v6_fuses_bm25f_and_semantic_children_without_citing_search(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            candidate_fusion_profile_version=(
                RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
            ),
            semantic_provider=provider,
        )
        searched = service.search(
            {
                "query": "terminal acceptance",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert searched["retrieval_mode"] == (
            "role_aware_child_bm25f_semantic_weighted_rrf_rq2b"
        )
        assert searched["candidate_fusion_profile_version"] == (
            RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
        )
        assert searched["candidate_fusion"]["lexical_candidate_count"] == 1
        assert searched["candidate_fusion"]["semantic_candidate_count"] == 1
        assert searched["candidate_fusion"]["overlap_candidate_count"] == 1
        assert len(searched["candidate_fusion"]["result_hash"]) == 64
        assert searched["candidate_fusion"]["query_plan_hash"] == (
            searched["query_plan"]["plan_hash"]
        )
        assert all(
            route["executed_mode"] == "hybrid"
            for route in searched["retrieval_routes"]
        )
        assert "SEMANTIC_BACKEND_UNAVAILABLE_BM25_FALLBACK" not in set(
            searched["warnings"]
        )
        hit = searched["hits"][0]
        assert hit["fragment_role"] == "retrieval_child"
        assert hit["is_citable"] is False
        assert hit["context_read"] is False
        assert hit["fusion_channels"] == ["lexical_bm25f", "semantic_bce"]
        read = service.read(
            {"evidence_ids": [hit["evidence_id"]], "expansion": "none"}
        )
        assert read["items"]
        assert all(item["fragment_role"] == "evidence_atom" for item in read["items"])
        assert all(item["is_citable"] is True for item in read["items"])

        semantic_only = service.search(
            {
                "query": "完工验收需要什么资料",
                "document_roles": ["tender_document"],
                "top_k": 5,
            }
        )
        assert semantic_only["candidate_fusion"]["lexical_candidate_count"] == 0
        assert semantic_only["candidate_fusion"]["semantic_candidate_count"] == 1
        assert semantic_only["hits"][0]["fusion_channels"] == ["semantic_bce"]
    finally:
        db.close()


def test_rq2b_mcp_v6_distinguishes_empty_semantic_and_both_empty_channels(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    provider.search = lambda **_kwargs: ()
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
            semantic_provider=provider,
        )
        lexical_only = service.search({"query": "terminal acceptance", "top_k": 5})
        assert lexical_only["status"] == "ok"
        assert lexical_only["candidate_fusion"]["lexical_candidate_count"] == 1
        assert lexical_only["candidate_fusion"]["semantic_candidate_count"] == 0
        assert lexical_only["hits"][0]["fusion_channels"] == ["lexical_bm25f"]

        both_empty = service.search({"query": "完全无关问题", "top_k": 5})
        assert both_empty["status"] == "no_result"
        assert both_empty["hits"] == []
        assert both_empty["candidate_fusion"]["union_candidate_count"] == 0
    finally:
        db.close()


def test_rq2c_mcp_v7_reranks_only_frozen_children_and_preserves_atom_read(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    semantic_provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, semantic_provider)
    reranker_provider = _RerankerProvider()
    db = c3_session_factory()
    try:
        scope = BidEvidenceMcpScope(
            assessment_id=seed.assessment_id,
            run_id=seed.analysis_run_id,
            manifest_id=seed.manifest_id,
        )
        service = BidEvidenceMcpService(
            db,
            scope=scope,
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
            semantic_provider=semantic_provider,
            rerank_profile_version=RQ2C_RERANK_PROFILE_VERSION,
            reranker_provider=reranker_provider,
        )
        searched = service.search({"query": "terminal acceptance", "top_k": 5})
        assert searched["retrieval_mode"] == (
            "role_aware_child_bm25f_semantic_bce_guarded_rerank_rq2c"
        )
        assert searched["rerank_profile_version"] == RQ2C_RERANK_PROFILE_VERSION
        assert searched["rerank_model"] == reranker_provider.descriptor.stable_payload()
        assert searched["candidate_rerank"]["fusion_result_hash"] == (
            searched["candidate_fusion"]["result_hash"]
        )
        assert searched["candidate_rerank"]["promotion_count"] == 0
        assert searched["candidate_rerank"]["baseline_child_keys"] == (
            searched["candidate_rerank"]["final_child_keys"]
        )
        hit = searched["hits"][0]
        assert hit["fragment_role"] == "retrieval_child"
        assert hit["is_citable"] is False
        assert hit["context_read"] is False
        assert hit["rerank_score"] == hit["score"]
        assert len(hit["rerank_input_hash"]) == 64
        read = service.read(
            {"evidence_ids": [hit["evidence_id"]], "expansion": "none"}
        )
        assert read["items"]
        assert all(item["fragment_role"] == "evidence_atom" for item in read["items"])
        assert all(item["is_citable"] is True for item in read["items"])

        with pytest.raises(BidEvidenceMcpError, match="RERANK_PROFILE_DISABLED"):
            BidEvidenceMcpService(
                db,
                scope=scope,
                retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
                lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
                semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
                candidate_fusion_profile_version=(
                    RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
                ),
                semantic_provider=semantic_provider,
                rerank_profile_version=RQ2C_RERANK_PROFILE_VERSION,
                reranker_provider=None,
            )
    finally:
        db.close()


def test_rq2b_mcp_v6_fails_closed_on_provider_and_stale_semantic_authority(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    db = c3_session_factory()
    try:
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
            candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
            semantic_provider=provider,
        )
        provider.search = lambda **_kwargs: (_ for _ in ()).throw(
            BidSemanticProviderUnavailable("provider unavailable")
        )
        with pytest.raises(BidEvidenceMcpError, match="PROVIDER_UNAVAILABLE"):
            service.search({"query": "terminal acceptance", "top_k": 5})

        db.rollback()
        provider.search = _SemanticProvider.search.__get__(provider, _SemanticProvider)
        with db.begin():
            semantic_index = db.query(BidEvidenceSemanticIndex).one()
            semantic_index.status = "stale"
        with pytest.raises(BidEvidenceMcpError, match="SEMANTIC_INDEX_NOT_READY"):
            service.search({"query": "terminal acceptance", "top_k": 5})
    finally:
        db.close()


def test_pdf_c3_tool_adapter_uses_v2_and_fences_legacy_dispatch(
    c3_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
        ),
    )
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal", "top_k": 5},
    }
    db = c3_session_factory()
    try:
        role_aware = BidEvidenceMcpAdapter(
            "search",
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        ).execute(
            db,
            envelope=envelope,
            provider_request_id="pdf-c3-role-aware",
        )
        assert role_aware.data["contract"] == "bid-assessment-evidence-mcp/v2"
        assert role_aware.returned_items == 1
        with pytest.raises(
            BidToolDispatchConflict,
            match="RETRIEVAL_PROFILE_DISABLED",
        ):
            BidEvidenceMcpAdapter("search").execute(
                db,
                envelope=envelope,
                provider_request_id="pdf-c3-legacy",
            )
    finally:
        db.close()


def test_rq1c_tool_adapter_freezes_query_profile_and_fails_closed_when_disabled(
    c3_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal construction acceptance 完工日期", "top_k": 5},
    }
    adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
    )
    db = c3_session_factory()
    try:
        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(
                feature_bid_assessment_phase4_evidence_mcp=True,
                feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
                feature_bid_assessment_rq1c_query_optimizer=False,
            ),
        )
        with pytest.raises(BidToolDispatchConflict, match="QUERY_PROFILE_DISABLED"):
            adapter.execute(
                db,
                envelope=envelope,
                provider_request_id="rq1c-disabled",
            )
        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(
                feature_bid_assessment_phase4_evidence_mcp=True,
                feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
                feature_bid_assessment_rq1c_query_optimizer=True,
            ),
        )
        result = adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq1c-enabled",
        )
        assert result.data["query_optimizer_profile_version"] == (
            QUERY_OPTIMIZER_PROFILE_VERSION
        )
    finally:
        db.close()


def test_rq1d_v4_adapter_freezes_lexical_profile_and_fails_closed(
    c3_session_factory,
    monkeypatch,
) -> None:
    clear_field_aware_lexical_cache()
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal construction acceptance", "top_k": 5},
    }
    adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
    )
    db = c3_session_factory()
    try:
        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(
                feature_bid_assessment_phase4_evidence_mcp=True,
                feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
                feature_bid_assessment_rq1c_query_optimizer=True,
                feature_bid_assessment_rq1d_field_aware_lexical=False,
            ),
        )
        with pytest.raises(BidToolDispatchConflict, match="LEXICAL_PROFILE_DISABLED"):
            adapter.execute(
                db,
                envelope=envelope,
                provider_request_id="rq1d-disabled",
            )
        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(
                feature_bid_assessment_phase4_evidence_mcp=True,
                feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
                feature_bid_assessment_rq1c_query_optimizer=True,
                feature_bid_assessment_rq1d_field_aware_lexical=True,
            ),
        )
        spec = _adapter_spec("evidence.search")
        assert spec is not None
        assert spec.adapter_name == "bid-evidence-mcp-rq1d-search"
        assert spec.adapter_version == "v4-field-aware-lexical"
        result = adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq1d-enabled",
        )
        assert result.data["lexical_search_profile_version"] == (
            FIELD_AWARE_LEXICAL_PROFILE_VERSION
        )
    finally:
        db.close()


def test_rq2a_v5_adapter_freezes_semantic_profile_and_provider(
    c3_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal acceptance", "top_k": 5},
    }
    adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
    )
    db = c3_session_factory()
    try:
        disabled = SimpleNamespace(
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            feature_bid_assessment_rq1c_query_optimizer=True,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            feature_bid_assessment_rq2a_semantic_recall=False,
        )
        monkeypatch.setattr(config_module, "settings", disabled)
        with pytest.raises(BidToolDispatchConflict, match="SEMANTIC_PROFILE_DISABLED"):
            adapter.execute(
                db,
                envelope=envelope,
                provider_request_id="rq2a-disabled",
            )
        enabled = SimpleNamespace(
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            feature_bid_assessment_rq1c_query_optimizer=True,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            feature_bid_assessment_rq2a_semantic_recall=True,
        )
        monkeypatch.setattr(config_module, "settings", enabled)
        monkeypatch.setattr(
            tool_execution_module,
            "configured_bid_semantic_provider",
            lambda _settings: provider,
        )
        spec = _adapter_spec("evidence.search")
        assert spec is not None
        assert spec.adapter_name == "bid-evidence-mcp-rq2a-search"
        assert spec.adapter_version == "v5-child-semantic-recall"
        result = adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq2a-enabled",
        )
        assert result.data["semantic_search_profile_version"] == (
            RQ2A_SEMANTIC_PROFILE_VERSION
        )
        assert result.data["hits"][0]["fragment_role"] == "retrieval_child"
    finally:
        db.close()


def test_rq2b_v6_adapter_freezes_fusion_profile_and_preserves_v5(
    c3_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, provider)
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal acceptance", "top_k": 5},
    }
    adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
    )
    db = c3_session_factory()
    try:
        settings = SimpleNamespace(
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            feature_bid_assessment_rq1c_query_optimizer=True,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            feature_bid_assessment_rq2a_semantic_recall=True,
            feature_bid_assessment_rq2b_candidate_fusion=False,
        )
        monkeypatch.setattr(config_module, "settings", settings)
        with pytest.raises(BidToolDispatchConflict, match="FUSION_PROFILE_DISABLED"):
            adapter.execute(
                db,
                envelope=envelope,
                provider_request_id="rq2b-disabled",
            )
        settings.feature_bid_assessment_rq2b_candidate_fusion = True
        monkeypatch.setattr(
            tool_execution_module,
            "configured_bid_semantic_provider",
            lambda _settings: provider,
        )
        spec = _adapter_spec("evidence.search")
        assert spec is not None
        assert spec.adapter_name == "bid-evidence-mcp-rq2b-search"
        assert spec.adapter_version == "v6-bm25f-semantic-fusion"
        result = adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq2b-enabled",
        )
        assert result.data["candidate_fusion_profile_version"] == (
            RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
        )
        settings.feature_bid_assessment_rq2b_candidate_fusion = False
        v5 = _adapter_spec("evidence.search")
        assert v5 is not None
        assert v5.adapter_version == "v5-child-semantic-recall"
    finally:
        db.close()


def test_rq2c_v7_adapter_is_new_only_and_preserves_v6_dispatch(
    c3_session_factory,
    monkeypatch,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    semantic_provider = _SemanticProvider()
    _build_semantic_index(c3_session_factory, semantic_provider)
    reranker_provider = _RerankerProvider()
    envelope = {
        "assessment_id": seed.assessment_id,
        "run_id": seed.analysis_run_id,
        "manifest_id": seed.manifest_id,
        "arguments": {"query": "terminal acceptance", "top_k": 5},
    }
    v7_adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
        rerank_profile_version=RQ2C_RERANK_PROFILE_VERSION,
    )
    v6_adapter = BidEvidenceMcpAdapter(
        "search",
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
        lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
        semantic_search_profile_version=RQ2A_SEMANTIC_PROFILE_VERSION,
        candidate_fusion_profile_version=RQ2B_CANDIDATE_FUSION_PROFILE_VERSION,
    )
    db = c3_session_factory()
    try:
        settings = SimpleNamespace(
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            feature_bid_assessment_rq1c_query_optimizer=True,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            feature_bid_assessment_rq2a_semantic_recall=True,
            feature_bid_assessment_rq2b_candidate_fusion=True,
            feature_bid_assessment_rq2c_lightweight_rerank=False,
        )
        monkeypatch.setattr(config_module, "settings", settings)
        monkeypatch.setattr(
            tool_execution_module,
            "configured_bid_semantic_provider",
            lambda _settings: semantic_provider,
        )
        monkeypatch.setattr(
            tool_execution_module,
            "configured_bid_reranker_provider",
            lambda _settings: reranker_provider,
        )
        with pytest.raises(BidToolDispatchConflict, match="RERANK_PROFILE_DISABLED"):
            v7_adapter.execute(
                db,
                envelope=envelope,
                provider_request_id="rq2c-disabled",
            )
        settings.feature_bid_assessment_rq2c_lightweight_rerank = True
        spec = _adapter_spec("evidence.search")
        assert spec is not None
        assert spec.adapter_name == "bid-evidence-mcp-rq2c-search"
        assert spec.adapter_version == "v7-bce-anchor-preserving-rerank"
        result = v7_adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq2c-enabled",
        )
        assert result.data["rerank_profile_version"] == RQ2C_RERANK_PROFILE_VERSION

        historical = v6_adapter.execute(
            db,
            envelope=envelope,
            provider_request_id="rq2b-historical",
        )
        assert "rerank_profile_version" not in historical.data
        assert historical.data["candidate_fusion_profile_version"] == (
            RQ2B_CANDIDATE_FUSION_PROFILE_VERSION
        )
    finally:
        db.close()


def test_rq1d_service_requires_rq1c_and_role_aware_profiles(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    db = c3_session_factory()
    try:
        scope = BidEvidenceMcpScope(
            assessment_id=seed.assessment_id,
            run_id=seed.analysis_run_id,
            manifest_id=seed.manifest_id,
        )
        with pytest.raises(BidEvidenceMcpError, match="LEXICAL_PROFILE_DISABLED"):
            BidEvidenceMcpService(
                db,
                scope=scope,
                retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
                lexical_search_profile_version=FIELD_AWARE_LEXICAL_PROFILE_VERSION,
            )
        service = BidEvidenceMcpService(
            db,
            scope=scope,
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            query_optimizer_profile_version=QUERY_OPTIMIZER_PROFILE_VERSION,
            lexical_search_profile_version=LEGACY_LEXICAL_SEARCH_PROFILE_VERSION,
        )
        assert service.rq1d_field_aware_lexical is False
    finally:
        db.close()


def test_pdf_c3_fact_gate_accepts_only_citable_atoms(c3_session_factory) -> None:
    seed = _seed_c3(c3_session_factory)
    db = c3_session_factory()
    try:
        run = (
            db.query(BidAnalysisRun)
            .filter(BidAnalysisRun.id == seed.analysis_run_id)
            .one()
        )
        fragments = db.query(BidEvidenceFragment).all()
        by_role = {
            str((row.locator_json or {}).get("fragment_role")): str(row.id)
            for row in fragments
        }
        atom_id = next(
            str(row.id)
            for row in fragments
            if (row.locator_json or {}).get("fragment_role") == "evidence_atom"
        )
        assert set(_scoped_evidence(db, run=run, evidence_ids=[atom_id])) == {
            atom_id
        }
        for role in ("section_parent", "retrieval_child"):
            with pytest.raises(
                BidMvp1AuthorityError,
                match="ROLE_NOT_CITABLE",
            ):
                _scoped_evidence(
                    db,
                    run=run,
                    evidence_ids=[by_role[role]],
                )
    finally:
        db.close()


def test_pdf_c3_deterministic_build_failure_rolls_back_and_converges_failed(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    db = c3_session_factory()
    try:
        with db.begin():
            scheduled = ensure_role_aware_retrieval_index(
                db,
                parse_run_id=seed.parse_run_id,
            )
            child = (
                db.query(BidEvidenceFragment)
                .filter(BidEvidenceFragment.parent_id.isnot(None))
                .all()
            )
            child = next(
                row
                for row in child
                if (row.locator_json or {}).get("fragment_role")
                == "retrieval_child"
            )
            locator = dict(child.locator_json or {})
            locator["retrieval_hash"] = "f" * 64
            child.locator_json = locator
            child.locator_hash = canonical_hash(locator)
        batch = process_pending_retrieval_indexes(
            session_factory=c3_session_factory,
            limit=20,
        )
        assert batch.failed == 1
        assert batch.ready == 0
        db.rollback()
        index = (
            db.query(BidEvidenceRetrievalIndex)
            .filter(BidEvidenceRetrievalIndex.id == scheduled.index_id)
            .one()
        )
        assert str(index.status) == "failed"
        assert str(index.error_code) == (
            "BID_EVIDENCE_RETRIEVAL_TEXT_DERIVATION_INVALID"
        )
        assert db.query(BidEvidenceRetrievalEntry).count() == 0
        assert db.query(BidEvidenceRetrievalHead).count() == 0
    finally:
        db.close()


def test_pdf_c3_mcp_fails_closed_when_retrieval_entry_hash_drifts(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    _build_index(c3_session_factory, seed)
    db = c3_session_factory()
    try:
        with db.begin():
            entry = db.query(BidEvidenceRetrievalEntry).one()
            entry.retrieval_text = f"{entry.retrieval_text} tampered"
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        )
        with pytest.raises(BidEvidenceMcpError, match="INDEX_INVALID"):
            service.search({"query": "terminal", "top_k": 5})
    finally:
        db.close()


def test_pdf_c3_parse_head_change_fences_search_and_marks_index_stale(
    c3_session_factory,
) -> None:
    seed = _seed_c3(c3_session_factory)
    scheduled, _built = _build_index(c3_session_factory, seed)
    db = c3_session_factory()
    try:
        with db.begin():
            replacement = ensure_document_parse_run(
                db,
                document_version_id=seed.document_version_id,
                parser_profile_version="bid-document-parser-profile-v1",
                requested_at=utc_now(),
            )
            assert str(replacement.run.id) != seed.parse_run_id
        service = BidEvidenceMcpService(
            db,
            scope=BidEvidenceMcpScope(
                assessment_id=seed.assessment_id,
                run_id=seed.analysis_run_id,
                manifest_id=seed.manifest_id,
            ),
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        )
        with pytest.raises(BidEvidenceMcpError, match="INDEX_NOT_READY"):
            service.search({"query": "terminal", "top_k": 5})
        db.rollback()
        with db.begin():
            assert invalidate_stale_retrieval_indexes(db) == 1
        index = (
            db.query(BidEvidenceRetrievalIndex)
            .filter(BidEvidenceRetrievalIndex.id == scheduled.index_id)
            .one()
        )
        assert str(index.status) == "stale"
        assert db.query(BidEvidenceRetrievalHead).count() == 0
    finally:
        db.close()
