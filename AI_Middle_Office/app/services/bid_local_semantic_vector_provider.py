"""Local-only exact-cosine BCE provider for the runnable MVP lab.

This adapter intentionally has no production configuration path.  It keeps
vectors in the isolated lab process and exists so a Windows workstation can
exercise the governed RQ2 semantic authority without a Milvus daemon.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Sequence

from app.services.bid_semantic_vector_provider import (
    RQ2A_DISTANCE_METRIC,
    RQ2A_EMBEDDING_DIMENSION,
    RQ2A_EMBEDDING_MODEL_ID,
    RQ2A_EMBEDDING_MODEL_REVISION,
    RQ2A_PROVIDER_ID,
    BidSemanticProviderInvalid,
    BidSemanticProviderUnavailable,
    SemanticDocument,
    SemanticModelDescriptor,
    SemanticProviderHit,
    SemanticVectorReceipt,
    vector_hash,
)


LOCAL_EXACT_COSINE_BACKEND = "local-bce-exact-cosine"


class LocalBceExactSemanticProvider:
    """Frozen BCE embeddings with an in-process exact cosine store."""

    backend_id = LOCAL_EXACT_COSINE_BACKEND

    def __init__(self, *, model_path: str, model_cache_dir: str = "") -> None:
        resolved = Path(str(model_path)).resolve()
        if not resolved.is_dir():
            raise BidSemanticProviderUnavailable(
                "BID_LOCAL_SEMANTIC_MODEL_SNAPSHOT_NOT_FOUND"
            )
        self._model_path = str(resolved)
        self._model_cache_dir = str(model_cache_dir).strip() or None
        self._model: Any | None = None
        self._records: dict[
            str, dict[str, tuple[SemanticDocument, Any, str]]
        ] = {}
        self._lock = threading.RLock()
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
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self._model_path,
                    cache_folder=self._model_cache_dir,
                    local_files_only=True,
                    device="cpu",
                )
            except Exception as exc:  # pragma: no cover - workstation dependency
                raise BidSemanticProviderUnavailable(
                    "BID_LOCAL_SEMANTIC_MODEL_LOAD_FAILED"
                ) from exc
            return self._model

    def _encode(self, texts: Sequence[str]) -> Any:
        import numpy as np

        if not texts:
            return np.empty((0, RQ2A_EMBEDDING_DIMENSION), dtype=np.float32)
        try:
            vectors = self._load_model().encode(
                list(texts),
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
        except BidSemanticProviderUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - workstation dependency
            raise BidSemanticProviderUnavailable(
                "BID_LOCAL_SEMANTIC_EMBEDDING_FAILED"
            ) from exc
        normalized = np.asarray(vectors, dtype=np.float32)
        if normalized.shape != (len(texts), RQ2A_EMBEDDING_DIMENSION):
            raise BidSemanticProviderInvalid(
                "BID_SEMANTIC_VECTOR_DIMENSION_INVALID"
            )
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
        receipts: list[SemanticVectorReceipt] = []
        with self._lock:
            namespace_records = self._records.setdefault(str(namespace), {})
            for document, vector in zip(documents, vectors, strict=True):
                digest = vector_hash(vector)
                existing = namespace_records.get(document.provider_record_id)
                if existing is not None:
                    old_document, _old_vector, old_digest = existing
                    if old_document != document or old_digest != digest:
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
        query_vector = self._encode([str(query)])[0]
        with self._lock:
            records = tuple(self._records.get(str(namespace), {}).items())
        ranked: list[tuple[float, str, SemanticDocument, str]] = []
        for provider_record_id, (document, vector, digest) in records:
            ranked.append(
                (float(query_vector @ vector), provider_record_id, document, digest)
            )
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return tuple(
            SemanticProviderHit(
                provider_record_id=provider_record_id,
                retrieval_child_key=document.retrieval_child_key,
                source_entry_hash=document.source_entry_hash,
                embedding_text_hash=document.embedding_text_hash,
                vector_hash=digest,
                score=round(score, 8),
            )
            for score, provider_record_id, document, digest in ranked[
                : max(1, min(int(top_k), 200))
            ]
        )


_provider_lock = threading.RLock()
_provider: LocalBceExactSemanticProvider | None = None
_provider_key: tuple[str, str] | None = None


def configured_local_bce_exact_provider(
    *,
    model_path: str,
    model_cache_dir: str = "",
) -> LocalBceExactSemanticProvider:
    """Return the process singleton required by index-build then search."""

    global _provider, _provider_key
    key = (str(Path(model_path).resolve()), str(model_cache_dir).strip())
    with _provider_lock:
        if _provider is None:
            _provider = LocalBceExactSemanticProvider(
                model_path=key[0],
                model_cache_dir=key[1],
            )
            _provider_key = key
        elif _provider_key != key:
            raise BidSemanticProviderInvalid(
                "BID_LOCAL_SEMANTIC_PROVIDER_CONFIGURATION_CHANGED"
            )
        return _provider


__all__ = [
    "LOCAL_EXACT_COSINE_BACKEND",
    "LocalBceExactSemanticProvider",
    "configured_local_bce_exact_provider",
]
