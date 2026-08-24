from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401
from app.core.database import Base
from app.models.bid_assessment_retrieval import (
    BidEvidenceRetrievalEntry,
    BidEvidenceRetrievalHead,
    BidEvidenceRetrievalIndex,
)
from app.models.bid_assessment_semantic import (
    BidEvidenceSemanticEntry,
    BidEvidenceSemanticHead,
    BidEvidenceSemanticIndex,
)
from app.services.bid_assessment_eventing import canonical_hash, utc_now
from app.services.bid_evidence_retrieval_index import (
    ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
)
from app.services.bid_evidence_semantic_index import (
    BidEvidenceSemanticFenceLost,
    BidEvidenceSemanticIndexInvalid,
    RQ2A_SEMANTIC_PROFILE_VERSION,
    SEMANTIC_INDEX_CONTRACT_VERSION,
    _claim_semantic_index,
    build_semantic_index,
    ensure_semantic_index,
    heartbeat_semantic_index,
    invalidate_stale_semantic_indexes,
    process_pending_semantic_indexes,
    recall_semantic_children,
    semantic_index_input_hash,
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


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    ROOT
    / "contracts"
    / "bid_assessment"
    / "v1"
    / "rq2a-semantic-retrieval-profile.json"
)
SCHEMA_PATH = (
    ROOT
    / "schemas"
    / "bid_assessment"
    / "v1"
    / "semantic-retrieval.schema.json"
)


class _DeterministicProvider:
    def __init__(self):
        self.descriptor = SemanticModelDescriptor(
            provider_id=RQ2A_PROVIDER_ID,
            model_id=RQ2A_EMBEDDING_MODEL_ID,
            model_revision=RQ2A_EMBEDDING_MODEL_REVISION,
            dimension=RQ2A_EMBEDDING_DIMENSION,
            distance_metric=RQ2A_DISTANCE_METRIC,
            normalized_embeddings=True,
        )
        self._namespaces = {}
        self.upsert_count = 0
        self.provider_request_ids = []

    def upsert_documents(self, *, namespace, provider_request_id, documents):
        assert provider_request_id.startswith("bid-semantic-index:")
        self.upsert_count += 1
        self.provider_request_ids.append(provider_request_id)
        receipts = []
        stored = {
            document.provider_record_id: (document, receipt)
            for document, receipt in self._namespaces.get(namespace, ())
        }
        for document in documents:
            digest = hashlib.sha256(
                (document.provider_record_id + document.text).encode("utf-8")
            ).hexdigest()
            receipt = SemanticVectorReceipt(
                provider_record_id=document.provider_record_id,
                retrieval_child_key=document.retrieval_child_key,
                source_entry_hash=document.source_entry_hash,
                embedding_text_hash=document.embedding_text_hash,
                vector_hash=digest,
                vector_dimension=RQ2A_EMBEDDING_DIMENSION,
            )
            receipts.append(receipt)
            stored[document.provider_record_id] = (document, receipt)
        self._namespaces[namespace] = tuple(
            stored[key] for key in sorted(stored)
        )
        return tuple(receipts)

    def search(self, *, namespace, query, top_k):
        rows = []
        for document, receipt in self._namespaces[namespace]:
            score = 0.91 if "担保" in document.text and "风险" in query else 0.35
            rows.append(
                SemanticProviderHit(
                    provider_record_id=receipt.provider_record_id,
                    retrieval_child_key=receipt.retrieval_child_key,
                    source_entry_hash=receipt.source_entry_hash,
                    embedding_text_hash=receipt.embedding_text_hash,
                    vector_hash=receipt.vector_hash,
                    score=score,
                )
            )
        rows.sort(key=lambda row: (-row.score, row.provider_record_id))
        return tuple(rows[:top_k])


class _UnknownThenReplayProvider(_DeterministicProvider):
    def __init__(self):
        super().__init__()
        self.fail_after_first_write = True

    def upsert_documents(self, *, namespace, provider_request_id, documents):
        receipts = super().upsert_documents(
            namespace=namespace,
            provider_request_id=provider_request_id,
            documents=documents,
        )
        if self.fail_after_first_write:
            self.fail_after_first_write = False
            raise BidSemanticProviderUnavailable(
                "BID_SEMANTIC_VECTOR_UPSERT_FAILED"
            )
        return receipts


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'semantic.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def _seed_source(db):
    retrieval_id = str(uuid.uuid4())
    document_version_id = str(uuid.uuid4())
    parse_run_id = str(uuid.uuid4())
    source = BidEvidenceRetrievalIndex(
        id=retrieval_id,
        document_version_id=document_version_id,
        parse_run_id=parse_run_id,
        retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
        role_contract_version="bid.evidence.chunk.v2",
        source_result_hash="a" * 64,
        input_hash="b" * 64,
        status="ready",
        parent_count=1,
        child_count=2,
        atom_count=2,
        entry_count=2,
        result_hash="c" * 64,
        row_version=1,
        requested_at=utc_now(),
        started_at=utc_now(),
        finished_at=utc_now(),
    )
    db.add(source)
    db.add(
        BidEvidenceRetrievalHead(
            document_version_id=document_version_id,
            retrieval_profile_version=ROLE_AWARE_RETRIEVAL_PROFILE_VERSION,
            current_index_id=retrieval_id,
            current_parse_run_id=parse_run_id,
            row_version=1,
        )
    )
    rows = []
    for ordinal, text in enumerate(
        ("履约担保责任可能形成现金流风险", "项目地址位于东莞市"),
        1,
    ):
        child_key = f"child:{hashlib.sha256(text.encode()).hexdigest()}"
        rows.append(
            BidEvidenceRetrievalEntry(
                id=str(uuid.uuid4()),
                index_id=retrieval_id,
                document_version_id=document_version_id,
                parse_run_id=parse_run_id,
                retrieval_child_id=str(uuid.uuid4()),
                retrieval_child_key=child_key,
                section_parent_id=str(uuid.uuid4()),
                section_parent_key=f"section:{hashlib.sha256((text + 'p').encode()).hexdigest()}",
                ordinal=ordinal,
                page_start=ordinal,
                page_end=ordinal,
                retrieval_text=text,
                retrieval_hash=hashlib.sha256(text.encode()).hexdigest(),
                child_text_hash=hashlib.sha256((text + "c").encode()).hexdigest(),
                source_atom_ids_json=[str(uuid.uuid4())],
                source_atom_keys_json=[
                    f"atom:{hashlib.sha256((text + 'a').encode()).hexdigest()}"
                ],
                source_atom_count=1,
                source_atoms_hash=hashlib.sha256((text + "atoms").encode()).hexdigest(),
                entry_hash=hashlib.sha256((text + "entry").encode()).hexdigest(),
            )
        )
    db.add_all(rows)
    db.flush()
    return source, tuple(rows)


def test_rq2a_profile_and_schema_freeze_child_only_semantics() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(profile)
    Draft202012Validator.check_schema(schema)
    assert profile["contract_version"] == SEMANTIC_INDEX_CONTRACT_VERSION
    assert profile["profile_version"] == RQ2A_SEMANTIC_PROFILE_VERSION
    assert profile["embedding"]["input_role"] == "retrieval_child"
    assert profile["embedding"]["parent_embedded"] is False
    assert profile["embedding"]["atom_embedded"] is False
    assert profile["recall"]["lexical_candidate_fusion"] is False
    assert profile["recall"]["reranker"] is False
    assert profile["compatibility"]["alembic_head"] == "20260815_0103"


def test_semantic_index_build_is_content_addressed_and_recall_is_child_only(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    provider = _DeterministicProvider()
    db = factory()
    with db.begin():
        source, source_entries = _seed_source(db)
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=provider.descriptor,
        )
        assert scheduled.created is True
        assert len(semantic_index_input_hash(source, provider.descriptor)) == 64
    db.close()

    result = build_semantic_index(
        session_factory=factory,
        semantic_index_id=scheduled.semantic_index_id,
        provider=provider,
        worker_id="test-worker",
    )
    assert result.status == "ready"
    assert result.entry_count == 2
    assert provider.upsert_count == 1

    db = factory()
    semantic = db.query(BidEvidenceSemanticIndex).one()
    assert semantic.status == "ready"
    assert db.query(BidEvidenceSemanticEntry).count() == 2
    assert db.query(BidEvidenceSemanticHead).one().current_retrieval_index_id == source.id
    recall = recall_semantic_children(
        db,
        provider=provider,
        source_indexes=[source],
        source_index_set_hash=canonical_hash({"source": source.result_hash}),
        allowed_document_versions=[source.document_version_id],
        allowed_retrieval_child_ids=[row.retrieval_child_id for row in source_entries],
        query_items=[{"text": "履约风险是什么", "weight": 1.0}],
        top_k=2,
    )
    assert recall.hits[0].retrieval_child_id == source_entries[0].retrieval_child_id
    assert recall.hits[0].semantic_score == 0.91
    assert len(recall.semantic_index_set_hash) == 64
    db.close()
    engine.dispose()


def test_retrieval_head_change_invalidates_semantic_head_without_fallback(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    provider = _DeterministicProvider()
    db = factory()
    with db.begin():
        source, _entries = _seed_source(db)
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=provider.descriptor,
        )
    db.close()
    assert build_semantic_index(
        session_factory=factory,
        semantic_index_id=scheduled.semantic_index_id,
        provider=provider,
        worker_id="test-worker",
    ).status == "ready"
    db = factory()
    with db.begin():
        source.status = "stale"
        db.merge(source)
        changed = invalidate_stale_semantic_indexes(db)
        assert changed == 1
    assert db.query(BidEvidenceSemanticIndex).one().status == "stale"
    assert db.query(BidEvidenceSemanticHead).count() == 0
    db.close()
    engine.dispose()


def test_semantic_build_heartbeat_is_fenced(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    provider = _DeterministicProvider()
    db = factory()
    with db.begin():
        source, _entries = _seed_source(db)
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=provider.descriptor,
        )
    db.close()

    claim = _claim_semantic_index(
        session_factory=factory,
        semantic_index_id=scheduled.semantic_index_id,
        worker_id="heartbeat-worker",
        lease_seconds=60,
    )
    assert claim is not None
    db = factory()
    before_version = db.query(BidEvidenceSemanticIndex).one().row_version
    db.close()
    heartbeat_semantic_index(
        session_factory=factory,
        claim=claim,
        lease_seconds=120,
    )
    db = factory()
    with db.begin():
        row = db.query(BidEvidenceSemanticIndex).one()
        assert row.row_version == before_version + 1
        row.fencing_token += 1
        row.lease_expires_at = utc_now() + timedelta(minutes=5)
    db.close()
    with pytest.raises(BidEvidenceSemanticFenceLost):
        heartbeat_semantic_index(
            session_factory=factory,
            claim=claim,
            lease_seconds=120,
        )
    engine.dispose()


def test_semantic_recall_rejects_duplicate_provider_hits(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    provider = _DeterministicProvider()
    db = factory()
    with db.begin():
        source, source_entries = _seed_source(db)
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=provider.descriptor,
        )
    db.close()
    assert build_semantic_index(
        session_factory=factory,
        semantic_index_id=scheduled.semantic_index_id,
        provider=provider,
        worker_id="test-worker",
    ).status == "ready"

    original_search = provider.search

    def duplicate_search(*, namespace, query, top_k):
        hits = original_search(namespace=namespace, query=query, top_k=top_k)
        return (hits[0], hits[0])

    provider.search = duplicate_search
    db = factory()
    with pytest.raises(BidEvidenceSemanticIndexInvalid):
        recall_semantic_children(
            db,
            provider=provider,
            source_indexes=[source],
            source_index_set_hash=canonical_hash({"source": source.result_hash}),
            allowed_document_versions=[source.document_version_id],
            allowed_retrieval_child_ids=[
                row.retrieval_child_id for row in source_entries
            ],
            query_items=[{"text": "履约风险是什么", "weight": 1.0}],
            top_k=2,
            per_query_depth=2,
        )
    db.close()
    engine.dispose()


def test_semantic_reconcile_recovers_sent_unknown_with_same_authority(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    provider = _UnknownThenReplayProvider()
    db = factory()
    with db.begin():
        source, _entries = _seed_source(db)
    db.close()

    first = process_pending_semantic_indexes(
        provider=provider,
        session_factory=factory,
        worker_id="recovery-worker",
        limit=5,
        lease_seconds=120,
        max_attempts=3,
    )
    assert first.retry_wait == 1
    db = factory()
    semantic = db.query(BidEvidenceSemanticIndex).one()
    original_id = semantic.id
    original_namespace = semantic.vector_namespace
    original_records = tuple(
        row[0].provider_record_id
        for row in provider._namespaces[original_namespace]
    )
    assert semantic.status == "queued"
    assert semantic.attempt_count == 1
    assert db.query(BidEvidenceSemanticEntry).count() == 0
    db.close()

    second = process_pending_semantic_indexes(
        provider=provider,
        session_factory=factory,
        worker_id="recovery-worker",
        limit=5,
        lease_seconds=120,
        max_attempts=3,
    )
    assert second.ready == 1
    db = factory()
    semantic = db.query(BidEvidenceSemanticIndex).one()
    assert semantic.id == original_id
    assert semantic.vector_namespace == original_namespace
    assert semantic.status == "ready"
    assert semantic.attempt_count == 2
    assert tuple(
        row[0].provider_record_id
        for row in provider._namespaces[original_namespace]
    ) == original_records
    assert db.query(BidEvidenceSemanticEntry).count() == source.entry_count
    db.close()
    engine.dispose()


def test_semantic_recall_rejects_provider_hit_outside_database_authority(
    tmp_path,
) -> None:
    engine, factory = _factory(tmp_path)
    provider = _DeterministicProvider()
    db = factory()
    with db.begin():
        source, source_entries = _seed_source(db)
        scheduled = ensure_semantic_index(
            db,
            retrieval_index=source,
            descriptor=provider.descriptor,
        )
    db.close()
    assert build_semantic_index(
        session_factory=factory,
        semantic_index_id=scheduled.semantic_index_id,
        provider=provider,
        worker_id="test-worker",
    ).status == "ready"

    def out_of_scope_search(*, namespace, query, top_k):
        del namespace, query, top_k
        return (
            SemanticProviderHit(
                provider_record_id="f" * 64,
                retrieval_child_key="child:" + "e" * 64,
                source_entry_hash="d" * 64,
                embedding_text_hash="c" * 64,
                vector_hash="b" * 64,
                score=0.99,
            ),
        )

    provider.search = out_of_scope_search
    db = factory()
    with pytest.raises(BidEvidenceSemanticIndexInvalid):
        recall_semantic_children(
            db,
            provider=provider,
            source_indexes=[source],
            source_index_set_hash=canonical_hash({"source": source.result_hash}),
            allowed_document_versions=[source.document_version_id],
            allowed_retrieval_child_ids=[
                row.retrieval_child_id for row in source_entries
            ],
            query_items=[{"text": "履约风险是什么", "weight": 1.0}],
            top_k=2,
        )
    db.close()
    engine.dispose()
