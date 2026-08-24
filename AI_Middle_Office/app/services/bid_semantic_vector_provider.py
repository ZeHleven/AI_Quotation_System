"""Controlled semantic-vector provider boundary for RQ2-A.

Nothing in this module connects to Milvus or loads an embedding model at import
time.  The concrete BCE/Milvus adapter performs lazy initialization only after
the RQ2-A feature gate is enabled and a semantic-index or recall operation is
explicitly executed.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import threading
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


SEMANTIC_PROVIDER_CONTRACT_VERSION = "bid.semantic-vector-provider.v1"
RQ2A_PROVIDER_ID = "bce-milvus"
RQ2A_EMBEDDING_MODEL_ID = "maidalun1020/bce-embedding-base_v1"
RQ2A_EMBEDDING_MODEL_REVISION = "9c0d82af44af61abe171ffae23fde5740c0ec1a8"
RQ2A_EMBEDDING_DIMENSION = 768
RQ2A_DISTANCE_METRIC = "COSINE"
RQ2A_NORMALIZED_EMBEDDINGS = True

_SAFE_COLLECTION = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,127}$")
_SAFE_NAMESPACE = re.compile(r"^[a-z0-9-]{8,100}$")
_SAFE_REQUEST_ID = re.compile(
    r"^bid-semantic-index:[a-f0-9]{64}(?::[0-9]{5})?$"
)
_HEX64 = re.compile(r"^[a-f0-9]{64}$")


class BidSemanticProviderError(RuntimeError):
    code = "BID_SEMANTIC_PROVIDER_ERROR"
    retryable = False


class BidSemanticProviderUnavailable(BidSemanticProviderError):
    code = "BID_SEMANTIC_PROVIDER_UNAVAILABLE"
    retryable = True


class BidSemanticProviderInvalid(BidSemanticProviderError):
    code = "BID_SEMANTIC_PROVIDER_INVALID"


@dataclass(frozen=True)
class SemanticModelDescriptor:
    provider_id: str
    model_id: str
    model_revision: str
    dimension: int
    distance_metric: str
    normalized_embeddings: bool

    def stable_payload(self) -> dict[str, object]:
        return {
            "provider_contract_version": SEMANTIC_PROVIDER_CONTRACT_VERSION,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "distance_metric": self.distance_metric,
            "normalized_embeddings": self.normalized_embeddings,
        }


@dataclass(frozen=True)
class SemanticDocument:
    provider_record_id: str
    retrieval_child_id: str
    retrieval_child_key: str
    source_entry_hash: str
    embedding_text_hash: str
    text: str


@dataclass(frozen=True)
class SemanticVectorReceipt:
    provider_record_id: str
    retrieval_child_key: str
    source_entry_hash: str
    embedding_text_hash: str
    vector_hash: str
    vector_dimension: int


@dataclass(frozen=True)
class SemanticProviderHit:
    provider_record_id: str
    retrieval_child_key: str
    source_entry_hash: str
    embedding_text_hash: str
    vector_hash: str
    score: float


class BidSemanticVectorProvider(Protocol):
    @property
    def descriptor(self) -> SemanticModelDescriptor: ...

    def upsert_documents(
        self,
        *,
        namespace: str,
        provider_request_id: str,
        documents: Sequence[SemanticDocument],
    ) -> tuple[SemanticVectorReceipt, ...]: ...

    def search(
        self,
        *,
        namespace: str,
        query: str,
        top_k: int,
    ) -> tuple[SemanticProviderHit, ...]: ...


def vector_hash(vector: Sequence[float]) -> str:
    values = tuple(float(value) for value in vector)
    if not values or any(not math.isfinite(value) for value in values):
        raise BidSemanticProviderInvalid("BID_SEMANTIC_VECTOR_INVALID")
    encoded = struct.pack(f"<{len(values)}f", *values)
    return hashlib.sha256(encoded).hexdigest()


def validate_descriptor(descriptor: SemanticModelDescriptor) -> None:
    if (
        descriptor.provider_id != RQ2A_PROVIDER_ID
        or descriptor.model_id != RQ2A_EMBEDDING_MODEL_ID
        or descriptor.model_revision != RQ2A_EMBEDDING_MODEL_REVISION
        or descriptor.dimension != RQ2A_EMBEDDING_DIMENSION
        or descriptor.distance_metric.upper() != RQ2A_DISTANCE_METRIC
        or descriptor.normalized_embeddings is not True
    ):
        raise BidSemanticProviderInvalid("BID_SEMANTIC_MODEL_PROFILE_MISMATCH")


class MilvusBceSemanticProvider:
    """Lazy BCE embedding + dedicated Milvus collection adapter.

    The adapter deliberately does not reuse the legacy quotation or
    ``bid_intake_*`` collections.  Its namespace and provider record IDs are
    immutable, content-addressed RQ2-A identities.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        collection_name: str,
        model_path: str,
        model_cache_dir: str,
        offline: bool = True,
        connect_timeout_seconds: float = 10.0,
        search_timeout_seconds: float = 20.0,
    ):
        if not str(host).strip():
            raise BidSemanticProviderInvalid("BID_SEMANTIC_MILVUS_HOST_INVALID")
        if not _SAFE_COLLECTION.fullmatch(str(collection_name)):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_COLLECTION_INVALID")
        self._host = str(host).strip()
        self._port = int(port)
        self._collection_name = str(collection_name)
        self._model_path = str(model_path).strip() or RQ2A_EMBEDDING_MODEL_ID
        self._model_cache_dir = str(model_cache_dir).strip() or None
        self._offline = bool(offline)
        self._connect_timeout_seconds = max(1.0, min(float(connect_timeout_seconds), 60.0))
        self._search_timeout_seconds = max(1.0, min(float(search_timeout_seconds), 120.0))
        self._alias = f"bid_semantic_{hashlib.sha256(self._collection_name.encode()).hexdigest()[:12]}"
        self._model: Any | None = None
        self._collection: Any | None = None
        self._lock = threading.RLock()
        self._descriptor = SemanticModelDescriptor(
            provider_id=RQ2A_PROVIDER_ID,
            model_id=RQ2A_EMBEDDING_MODEL_ID,
            model_revision=RQ2A_EMBEDDING_MODEL_REVISION,
            dimension=RQ2A_EMBEDDING_DIMENSION,
            distance_metric=RQ2A_DISTANCE_METRIC,
            normalized_embeddings=RQ2A_NORMALIZED_EMBEDDINGS,
        )

    @property
    def descriptor(self) -> SemanticModelDescriptor:
        return self._descriptor

    def _load_model(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                model_kwargs = {
                    "cache_folder": self._model_cache_dir,
                    "local_files_only": self._offline,
                }
                if self._model_path == RQ2A_EMBEDDING_MODEL_ID:
                    model_kwargs["revision"] = RQ2A_EMBEDDING_MODEL_REVISION
                self._model = SentenceTransformer(self._model_path, **model_kwargs)
            except Exception as exc:  # pragma: no cover - runtime dependency
                raise BidSemanticProviderUnavailable(
                    "BID_SEMANTIC_EMBEDDING_MODEL_UNAVAILABLE"
                ) from exc
            return self._model

    def _get_collection(self) -> Any:
        with self._lock:
            if self._collection is not None:
                return self._collection
            try:
                from pymilvus import (
                    Collection,
                    CollectionSchema,
                    DataType,
                    FieldSchema,
                    connections,
                    utility,
                )

                connections.connect(
                    alias=self._alias,
                    host=self._host,
                    port=str(self._port),
                    timeout=self._connect_timeout_seconds,
                )
                if not utility.has_collection(
                    self._collection_name,
                    using=self._alias,
                ):
                    schema = CollectionSchema(
                        fields=[
                            FieldSchema(
                                name="provider_record_id",
                                dtype=DataType.VARCHAR,
                                is_primary=True,
                                max_length=64,
                            ),
                            FieldSchema(
                                name="namespace",
                                dtype=DataType.VARCHAR,
                                max_length=100,
                            ),
                            FieldSchema(
                                name="retrieval_child_key",
                                dtype=DataType.VARCHAR,
                                max_length=100,
                            ),
                            FieldSchema(
                                name="source_entry_hash",
                                dtype=DataType.VARCHAR,
                                max_length=64,
                            ),
                            FieldSchema(
                                name="embedding_text_hash",
                                dtype=DataType.VARCHAR,
                                max_length=64,
                            ),
                            FieldSchema(
                                name="vector_hash",
                                dtype=DataType.VARCHAR,
                                max_length=64,
                            ),
                            FieldSchema(
                                name="vector",
                                dtype=DataType.FLOAT_VECTOR,
                                dim=RQ2A_EMBEDDING_DIMENSION,
                            ),
                        ],
                        description="Qisheng bid-assessment RQ2-A Child embeddings",
                        enable_dynamic_field=False,
                    )
                    collection = Collection(
                        name=self._collection_name,
                        schema=schema,
                        using=self._alias,
                    )
                    collection.create_index(
                        field_name="vector",
                        index_params={
                            "index_type": "HNSW",
                            "metric_type": RQ2A_DISTANCE_METRIC,
                            "params": {"M": 32, "efConstruction": 200},
                        },
                    )
                else:
                    collection = Collection(
                        self._collection_name,
                        using=self._alias,
                    )
                fields = {
                    str(field.name): field
                    for field in collection.schema.fields
                }
                expected_fields = {
                    "provider_record_id",
                    "namespace",
                    "retrieval_child_key",
                    "source_entry_hash",
                    "embedding_text_hash",
                    "vector_hash",
                    "vector",
                }
                vector_field = fields.get("vector")
                primary_field = fields.get("provider_record_id")
                varchar_lengths = {
                    "provider_record_id": 64,
                    "namespace": 100,
                    "retrieval_child_key": 100,
                    "source_entry_hash": 64,
                    "embedding_text_hash": 64,
                    "vector_hash": 64,
                }
                varchar_mismatch = any(
                    field_name not in fields
                    or getattr(fields[field_name], "dtype", None)
                    != DataType.VARCHAR
                    or int(
                        dict(getattr(fields[field_name], "params", {}) or {}).get(
                            "max_length",
                            0,
                        )
                    )
                    != max_length
                    for field_name, max_length in varchar_lengths.items()
                )
                if (
                    set(fields) != expected_fields
                    or varchar_mismatch
                    or primary_field is None
                    or not bool(getattr(primary_field, "is_primary", False))
                    or vector_field is None
                    or getattr(vector_field, "dtype", None) != DataType.FLOAT_VECTOR
                    or int(dict(getattr(vector_field, "params", {}) or {}).get("dim", 0))
                    != RQ2A_EMBEDDING_DIMENSION
                ):
                    raise BidSemanticProviderInvalid(
                        "BID_SEMANTIC_COLLECTION_SCHEMA_MISMATCH"
                    )
                vector_indexes = [
                    index
                    for index in collection.indexes
                    if str(getattr(index, "field_name", "")) == "vector"
                ]
                if len(vector_indexes) != 1:
                    raise BidSemanticProviderInvalid(
                        "BID_SEMANTIC_COLLECTION_INDEX_MISMATCH"
                    )
                index_params = dict(
                    getattr(vector_indexes[0], "params", {}) or {}
                )
                hnsw_params = dict(index_params.get("params") or {})
                if (
                    str(index_params.get("metric_type") or "").upper()
                    != RQ2A_DISTANCE_METRIC
                    or str(index_params.get("index_type") or "").upper()
                    != "HNSW"
                    or int(hnsw_params.get("M", 0)) != 32
                    or int(hnsw_params.get("efConstruction", 0)) != 200
                ):
                    raise BidSemanticProviderInvalid(
                        "BID_SEMANTIC_COLLECTION_INDEX_MISMATCH"
                    )
                collection.load(timeout=self._search_timeout_seconds)
                self._collection = collection
            except BidSemanticProviderError:
                raise
            except Exception as exc:  # pragma: no cover - runtime dependency
                raise BidSemanticProviderUnavailable(
                    "BID_SEMANTIC_VECTOR_BACKEND_UNAVAILABLE"
                ) from exc
            return self._collection

    def _encode(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        try:
            raw = self._load_model().encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            vectors = tuple(tuple(float(value) for value in row) for row in raw)
        except BidSemanticProviderError:
            raise
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise BidSemanticProviderUnavailable(
                "BID_SEMANTIC_EMBEDDING_EXECUTION_FAILED"
            ) from exc
        if any(len(row) != RQ2A_EMBEDDING_DIMENSION for row in vectors):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_VECTOR_DIMENSION_MISMATCH")
        return vectors

    def upsert_documents(
        self,
        *,
        namespace: str,
        provider_request_id: str,
        documents: Sequence[SemanticDocument],
    ) -> tuple[SemanticVectorReceipt, ...]:
        if not _SAFE_NAMESPACE.fullmatch(str(namespace)):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_NAMESPACE_INVALID")
        if not _SAFE_REQUEST_ID.fullmatch(str(provider_request_id)):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_REQUEST_ID_INVALID")
        ordered = tuple(sorted(documents, key=lambda row: row.provider_record_id))
        if not ordered or len({row.provider_record_id for row in ordered}) != len(ordered):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_DOCUMENT_SET_INVALID")
        for document in ordered:
            if (
                not _HEX64.fullmatch(document.provider_record_id)
                or not _HEX64.fullmatch(document.source_entry_hash)
                or not _HEX64.fullmatch(document.embedding_text_hash)
                or not document.text.strip()
            ):
                raise BidSemanticProviderInvalid("BID_SEMANTIC_DOCUMENT_INVALID")
        vectors = self._encode([row.text for row in ordered])
        vector_hashes = tuple(vector_hash(row) for row in vectors)
        try:
            collection = self._get_collection()
            collection.upsert(
                [
                    [row.provider_record_id for row in ordered],
                    [namespace for _row in ordered],
                    [row.retrieval_child_key for row in ordered],
                    [row.source_entry_hash for row in ordered],
                    [row.embedding_text_hash for row in ordered],
                    list(vector_hashes),
                    [list(vector) for vector in vectors],
                ]
            )
            collection.flush()
        except BidSemanticProviderError:
            raise
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise BidSemanticProviderUnavailable(
                "BID_SEMANTIC_VECTOR_UPSERT_FAILED"
            ) from exc
        return tuple(
            SemanticVectorReceipt(
                provider_record_id=document.provider_record_id,
                retrieval_child_key=document.retrieval_child_key,
                source_entry_hash=document.source_entry_hash,
                embedding_text_hash=document.embedding_text_hash,
                vector_hash=digest,
                vector_dimension=RQ2A_EMBEDDING_DIMENSION,
            )
            for document, digest in zip(ordered, vector_hashes)
        )

    def search(
        self,
        *,
        namespace: str,
        query: str,
        top_k: int,
    ) -> tuple[SemanticProviderHit, ...]:
        if not _SAFE_NAMESPACE.fullmatch(str(namespace)):
            raise BidSemanticProviderInvalid("BID_SEMANTIC_NAMESPACE_INVALID")
        normalized_query = str(query or "").strip()
        if not normalized_query or len(normalized_query) > 500:
            raise BidSemanticProviderInvalid("BID_SEMANTIC_QUERY_INVALID")
        vector = self._encode([normalized_query])[0]
        try:
            results = self._get_collection().search(
                data=[list(vector)],
                anns_field="vector",
                param={
                    "metric_type": RQ2A_DISTANCE_METRIC,
                    "params": {"ef": 128},
                },
                limit=max(1, min(int(top_k), 100)),
                expr=f'namespace == "{namespace}"',
                output_fields=[
                    "retrieval_child_key",
                    "source_entry_hash",
                    "embedding_text_hash",
                    "vector_hash",
                ],
                timeout=self._search_timeout_seconds,
            )
        except BidSemanticProviderError:
            raise
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise BidSemanticProviderUnavailable(
                "BID_SEMANTIC_VECTOR_SEARCH_FAILED"
            ) from exc
        hits: list[SemanticProviderHit] = []
        for hit in (results[0] if results else []):
            entity = hit.entity
            hits.append(
                SemanticProviderHit(
                    provider_record_id=str(hit.id),
                    retrieval_child_key=str(entity.get("retrieval_child_key") or ""),
                    source_entry_hash=str(entity.get("source_entry_hash") or ""),
                    embedding_text_hash=str(entity.get("embedding_text_hash") or ""),
                    vector_hash=str(entity.get("vector_hash") or ""),
                    score=round(float(hit.distance), 8),
                )
            )
        hits.sort(key=lambda row: (-row.score, row.provider_record_id))
        return tuple(hits)


def configured_bid_semantic_provider(settings: Any) -> BidSemanticVectorProvider:
    provider_id = str(
        getattr(settings, "bid_evidence_semantic_provider_id", "disabled")
    )
    if provider_id != RQ2A_PROVIDER_ID:
        raise BidSemanticProviderUnavailable("BID_SEMANTIC_PROVIDER_DISABLED")
    local_backend = os.getenv("BID_MVP1_LOCAL_SEMANTIC_BACKEND", "").strip()
    if local_backend:
        if (
            local_backend != "exact-cosine"
            or os.getenv("BID_MVP1_LOCAL_LAB", "").strip() != "1"
            or not str(getattr(settings, "database_url", "")).lower().startswith(
                "sqlite:"
            )
        ):
            raise BidSemanticProviderInvalid(
                "BID_LOCAL_SEMANTIC_BACKEND_BOUNDARY_INVALID"
            )
        from app.services.bid_local_semantic_vector_provider import (
            configured_local_bce_exact_provider,
        )

        provider = configured_local_bce_exact_provider(
            model_path=str(
                getattr(settings, "bid_evidence_semantic_model_path", "")
            ),
            model_cache_dir=str(
                getattr(settings, "bid_evidence_semantic_model_cache_dir", "")
            ),
        )
        validate_descriptor(provider.descriptor)
        return provider
    provider = MilvusBceSemanticProvider(
        host=str(getattr(settings, "bid_evidence_semantic_milvus_host", "ai-milvus")),
        port=int(getattr(settings, "bid_evidence_semantic_milvus_port", 19530)),
        collection_name=str(
            getattr(
                settings,
                "bid_evidence_semantic_collection",
                "bid_assessment_evidence_semantic_v1",
            )
        ),
        model_path=str(
            getattr(settings, "bid_evidence_semantic_model_path", "")
        ),
        model_cache_dir=str(
            getattr(settings, "bid_evidence_semantic_model_cache_dir", "")
        ),
        offline=bool(getattr(settings, "bid_evidence_semantic_model_offline", True)),
        connect_timeout_seconds=float(
            getattr(settings, "bid_evidence_semantic_connect_timeout_seconds", 10)
        ),
        search_timeout_seconds=float(
            getattr(settings, "bid_evidence_semantic_search_timeout_seconds", 20)
        ),
    )
    validate_descriptor(provider.descriptor)
    return provider


__all__ = [
    "BidSemanticProviderError",
    "BidSemanticProviderInvalid",
    "BidSemanticProviderUnavailable",
    "BidSemanticVectorProvider",
    "MilvusBceSemanticProvider",
    "RQ2A_DISTANCE_METRIC",
    "RQ2A_EMBEDDING_DIMENSION",
    "RQ2A_EMBEDDING_MODEL_ID",
    "RQ2A_EMBEDDING_MODEL_REVISION",
    "RQ2A_NORMALIZED_EMBEDDINGS",
    "RQ2A_PROVIDER_ID",
    "SEMANTIC_PROVIDER_CONTRACT_VERSION",
    "SemanticDocument",
    "SemanticModelDescriptor",
    "SemanticProviderHit",
    "SemanticVectorReceipt",
    "configured_bid_semantic_provider",
    "validate_descriptor",
    "vector_hash",
]
