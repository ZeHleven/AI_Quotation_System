from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import jieba
from fastapi import Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi


COLLECTION_NAME = os.environ.get(
    "TENDER_EVIDENCE_COLLECTION",
    "tender_evidence_blocks_v1",
)
MILVUS_HOST = os.environ.get("MILVUS_HOST", "standalone")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
def _load_index_secret() -> str:
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
        return Path(secret_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


INDEX_SECRET = _load_index_secret()
VECTOR_DIMENSION = int(os.environ.get("TENDER_EVIDENCE_VECTOR_DIMENSION", "768"))
MAX_BLOCKS = int(os.environ.get("TENDER_EVIDENCE_MAX_BLOCKS", "10000"))
EMBED_CHUNK_SIZE = max(
    1,
    min(
        int(os.environ.get("TENDER_EVIDENCE_EMBED_CHUNK_SIZE", "128")),
        512,
    ),
)
SEARCH_TIMEOUT_SECONDS = float(
    os.environ.get("TENDER_EVIDENCE_SEARCH_TIMEOUT_SECONDS", "20")
)
VECTOR_MIN_SCORE = float(
    os.environ.get("TENDER_EVIDENCE_VECTOR_MIN_SCORE", "0.20")
)
RRF_K = int(os.environ.get("TENDER_EVIDENCE_RRF_K", "60"))
RRF_SCORE_THRESHOLD = float(
    os.environ.get("TENDER_EVIDENCE_RRF_SCORE_THRESHOLD", "0.008")
)


class TenderIndexBlock(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    block_id: str = Field(min_length=1, max_length=80)
    document_id: str = Field(min_length=1, max_length=36)
    document_key: str = Field(min_length=1, max_length=160)
    document_version: int = Field(ge=1)
    block_order: int = Field(ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    content: str = Field(min_length=1, max_length=50000)
    keywords: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)


class TenderReindexRequest(BaseModel):
    case_id: str
    manifest_version: int = Field(ge=1)
    manifest_hash: str = Field(min_length=64, max_length=64)
    index_schema_version: str = Field(min_length=1, max_length=64)
    blocks: list[TenderIndexBlock]


class TenderSearchRequest(BaseModel):
    case_id: str
    manifest_version: int = Field(ge=1)
    manifest_hash: str = Field(min_length=64, max_length=64)
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    search_mode: Literal["exact", "semantic", "hybrid"] = "hybrid"


@dataclass
class _CachedBm25:
    records: list[dict[str, Any]]
    index: BM25Okapi | None


class TenderIndexSnapshotMissing(RuntimeError):
    pass


class TenderEvidenceHybridIndex:
    def __init__(self, embedding_model):
        self._embedding_model = embedding_model
        self._lock = threading.RLock()
        self._collection = None
        self._bm25_cache: dict[tuple[str, int, str], _CachedBm25] = {}

    def reindex(self, request: TenderReindexRequest) -> dict[str, Any]:
        _validate_scope(request.case_id, request.manifest_hash)
        if len(request.blocks) > MAX_BLOCKS:
            raise ValueError(
                f"manifest exceeds TENDER_EVIDENCE_MAX_BLOCKS={MAX_BLOCKS}"
            )
        if not request.blocks:
            raise ValueError("manifest contains no evidence blocks")
        _validate_blocks(request.blocks)
        cache_key = (
            request.case_id,
            request.manifest_version,
            request.manifest_hash,
        )
        with self._lock:
            collection = self._get_collection()
            existing = self._load_records(
                collection,
                case_id=request.case_id,
                manifest_version=request.manifest_version,
                manifest_hash=request.manifest_hash,
            )
            expected_ids = {item.evidence_id for item in request.blocks}
            existing_ids = {
                str(item.get("evidence_id") or "") for item in existing
            }
            if len(existing) == len(request.blocks) and existing_ids == expected_ids:
                self._bm25_cache[cache_key] = _build_bm25(existing)
                return {
                    "case_id": request.case_id,
                    "manifest_version": request.manifest_version,
                    "manifest_hash": request.manifest_hash,
                    "indexed_block_count": len(existing),
                    "idempotent": True,
                }
            if existing:
                collection.delete(
                    _snapshot_expr(
                        request.case_id,
                        request.manifest_version,
                        request.manifest_hash,
                    )
                )
                collection.flush()

            rows: list[dict[str, Any]] = []
            for start in range(0, len(request.blocks), EMBED_CHUNK_SIZE):
                block_batch = request.blocks[start : start + EMBED_CHUNK_SIZE]
                vectors = self._embedding_model.encode(
                    [item.content for item in block_batch],
                    normalize_embeddings=True,
                    batch_size=32,
                ).tolist()
                if any(len(vector) != VECTOR_DIMENSION for vector in vectors):
                    raise ValueError(
                        "embedding model returned an unexpected dimension"
                    )
                batch = [
                    _block_record(
                        request=request,
                        block=block,
                        vector=vector,
                    )
                    for block, vector in zip(block_batch, vectors)
                ]
                collection.insert(
                    [
                        [item["pk"] for item in batch],
                        [item["case_id"] for item in batch],
                        [item["manifest_version"] for item in batch],
                        [item["manifest_hash"] for item in batch],
                        [item["evidence_id"] for item in batch],
                        [item["block_id"] for item in batch],
                        [item["document_id"] for item in batch],
                        [item["document_key"] for item in batch],
                        [item["document_version"] for item in batch],
                        [item["block_order"] for item in batch],
                        [item["content_hash"] for item in batch],
                        [item["page_content"] for item in batch],
                        [item["keywords_json"] for item in batch],
                        [item["locator_json"] for item in batch],
                        [item["vector"] for item in batch],
                    ]
                )
                rows.extend(batch)
                print(
                    "[Tender Evidence] indexed "
                    f"{len(rows)}/{len(request.blocks)} blocks "
                    f"for case={request.case_id}",
                    flush=True,
                )
            collection.flush()
            records = [
                {key: value for key, value in item.items() if key != "vector"}
                for item in rows
            ]
            self._bm25_cache[cache_key] = _build_bm25(records)
            return {
                "case_id": request.case_id,
                "manifest_version": request.manifest_version,
                "manifest_hash": request.manifest_hash,
                "indexed_block_count": len(rows),
                "idempotent": False,
            }

    def search(self, request: TenderSearchRequest) -> dict[str, Any]:
        _validate_scope(request.case_id, request.manifest_hash)
        cache_key = (
            request.case_id,
            request.manifest_version,
            request.manifest_hash,
        )
        with self._lock:
            collection = self._get_collection()
            cached = self._bm25_cache.get(cache_key)
            if cached is None:
                records = self._load_records(
                    collection,
                    case_id=request.case_id,
                    manifest_version=request.manifest_version,
                    manifest_hash=request.manifest_hash,
                )
                if not records:
                    raise TenderIndexSnapshotMissing(
                        "requested evidence manifest is not indexed"
                    )
                cached = _build_bm25(records)
                self._bm25_cache[cache_key] = cached

        vector_hits = {}
        if request.search_mode in {"semantic", "hybrid"}:
            vector_hits = self._vector_search(
                collection,
                request=request,
            )
        bm25_hits = {}
        exact_hits = {}
        if request.search_mode in {"exact", "hybrid"}:
            bm25_hits = _bm25_search(
                cached,
                query=request.query,
                limit=max(request.top_k * 4, 20),
            )
            exact_hits = _exact_identifier_search(
                cached,
                query=request.query,
                limit=max(request.top_k * 4, 20),
            )
            bm25_hits = _merge_lexical_hits(
                bm25_hits=bm25_hits,
                exact_hits=exact_hits,
            )
        merged = rrf_merge(
            vector_hits=vector_hits,
            bm25_hits=bm25_hits,
            k=RRF_K,
        )
        hits = [
            item
            for item in merged
            if item["rrf_score"] >= RRF_SCORE_THRESHOLD
        ][: request.top_k]
        return {
            "case_id": request.case_id,
            "manifest_version": request.manifest_version,
            "manifest_hash": request.manifest_hash,
            "hits": hits,
            "retrieval": {
                "requested_mode": request.search_mode,
                "executed_mode": request.search_mode,
                "vector_count": len(vector_hits),
                "bm25_count": len(bm25_hits),
                "exact_identifier_count": len(exact_hits),
                "fusion": (
                    "rrf"
                    if request.search_mode == "hybrid"
                    else request.search_mode
                ),
            },
        }

    def _vector_search(
        self,
        collection,
        *,
        request: TenderSearchRequest,
    ) -> dict[str, dict[str, Any]]:
        try:
            query_vector = self._embedding_model.encode(
                [request.query],
                normalize_embeddings=True,
            ).tolist()
            results = collection.search(
                data=query_vector,
                anns_field="vector",
                param={
                    "metric_type": "COSINE",
                    "params": {"ef": 128},
                },
                limit=max(request.top_k * 4, 20),
                expr=_snapshot_expr(
                    request.case_id,
                    request.manifest_version,
                    request.manifest_hash,
                ),
                output_fields=["evidence_id", "block_id"],
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
        except Exception:
            return {}
        hits: dict[str, dict[str, Any]] = {}
        for hit in results[0] if results else []:
            score = float(hit.distance)
            if score < VECTOR_MIN_SCORE:
                continue
            evidence_id = str(hit.entity.get("evidence_id") or "")
            block_id = str(hit.entity.get("block_id") or "")
            if not evidence_id or not block_id:
                continue
            hits[evidence_id] = {
                "evidence_id": evidence_id,
                "block_id": block_id,
                "vector_score": score,
                "bm25_score": None,
            }
        return hits

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        connections.connect(
            alias="tender_evidence",
            host=MILVUS_HOST,
            port=MILVUS_PORT,
            timeout=5,
        )
        if not utility.has_collection(
            COLLECTION_NAME,
            using="tender_evidence",
        ):
            fields = [
                FieldSchema(
                    name="pk",
                    dtype=DataType.VARCHAR,
                    max_length=64,
                    is_primary=True,
                ),
                FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="manifest_version", dtype=DataType.INT64),
                FieldSchema(
                    name="manifest_hash",
                    dtype=DataType.VARCHAR,
                    max_length=64,
                ),
                FieldSchema(
                    name="evidence_id",
                    dtype=DataType.VARCHAR,
                    max_length=80,
                ),
                FieldSchema(
                    name="block_id",
                    dtype=DataType.VARCHAR,
                    max_length=80,
                ),
                FieldSchema(
                    name="document_id",
                    dtype=DataType.VARCHAR,
                    max_length=36,
                ),
                FieldSchema(
                    name="document_key",
                    dtype=DataType.VARCHAR,
                    max_length=160,
                ),
                FieldSchema(name="document_version", dtype=DataType.INT64),
                FieldSchema(name="block_order", dtype=DataType.INT64),
                FieldSchema(
                    name="content_hash",
                    dtype=DataType.VARCHAR,
                    max_length=64,
                ),
                FieldSchema(
                    name="page_content",
                    dtype=DataType.VARCHAR,
                    max_length=65535,
                ),
                FieldSchema(
                    name="keywords_json",
                    dtype=DataType.VARCHAR,
                    max_length=4000,
                ),
                FieldSchema(
                    name="locator_json",
                    dtype=DataType.VARCHAR,
                    max_length=4000,
                ),
                FieldSchema(
                    name="vector",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=VECTOR_DIMENSION,
                ),
            ]
            collection = Collection(
                name=COLLECTION_NAME,
                schema=CollectionSchema(
                    fields=fields,
                    description="Project-scoped tender evidence blocks",
                ),
                using="tender_evidence",
            )
            collection.create_index(
                field_name="vector",
                index_params={
                    "metric_type": "COSINE",
                    "index_type": "HNSW",
                    "params": {"M": 16, "efConstruction": 128},
                },
            )
        else:
            collection = Collection(
                COLLECTION_NAME,
                using="tender_evidence",
            )
        collection.load(timeout=90)
        self._collection = collection
        return collection

    @staticmethod
    def _load_records(
        collection,
        *,
        case_id: str,
        manifest_version: int,
        manifest_hash: str,
    ) -> list[dict[str, Any]]:
        return collection.query(
            expr=_snapshot_expr(
                case_id,
                manifest_version,
                manifest_hash,
            ),
            output_fields=[
                "evidence_id",
                "block_id",
                "document_id",
                "document_key",
                "document_version",
                "block_order",
                "content_hash",
                "page_content",
                "keywords_json",
                "locator_json",
            ],
            limit=MAX_BLOCKS,
        )


def register_tender_evidence_routes(app, *, embedding_model) -> None:
    index = TenderEvidenceHybridIndex(embedding_model)

    def authorize(
        x_tender_index_secret: str | None = Header(
            default=None,
            alias="X-Tender-Index-Secret",
        ),
    ) -> None:
        if not INDEX_SECRET or not x_tender_index_secret:
            raise HTTPException(status_code=403, detail="forbidden")
        if not secrets.compare_digest(
            INDEX_SECRET,
            x_tender_index_secret,
        ):
            raise HTTPException(status_code=403, detail="forbidden")

    @app.post("/api/v1/tender-evidence/reindex")
    async def reindex_tender_evidence(
        request: TenderReindexRequest,
        _authorized: None = Depends(authorize),
    ):
        del _authorized
        try:
            data = await run_in_threadpool(index.reindex, request)
            return {"code": 200, "message": "ok", "data": data}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="tender evidence indexing unavailable",
            ) from exc

    @app.post("/api/v1/tender-evidence/search")
    async def search_tender_evidence(
        request: TenderSearchRequest,
        _authorized: None = Depends(authorize),
    ):
        del _authorized
        try:
            data = await run_in_threadpool(index.search, request)
            return {"code": 200, "message": "ok", "data": data}
        except TenderIndexSnapshotMissing as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="tender evidence search unavailable",
            ) from exc


def rrf_merge(
    *,
    vector_hits: dict[str, dict[str, Any]],
    bm25_hits: dict[str, dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    vector_ranked = sorted(
        vector_hits,
        key=lambda key: float(vector_hits[key].get("vector_score") or 0),
        reverse=True,
    )
    bm25_ranked = sorted(
        bm25_hits,
        key=lambda key: float(bm25_hits[key].get("bm25_score") or 0),
        reverse=True,
    )
    scores: dict[str, float] = {}
    for rank, key in enumerate(vector_ranked, start=1):
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    for rank, key in enumerate(bm25_ranked, start=1):
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    merged = []
    for key in sorted(scores, key=lambda item: (-scores[item], item)):
        vector = vector_hits.get(key, {})
        bm25 = bm25_hits.get(key, {})
        merged.append(
            {
                "evidence_id": key,
                "block_id": str(
                    vector.get("block_id") or bm25.get("block_id") or ""
                ),
                "rrf_score": scores[key],
                "vector_score": vector.get("vector_score"),
                "bm25_score": bm25.get("bm25_score"),
            }
        )
    return merged


def _build_bm25(records: list[dict[str, Any]]) -> _CachedBm25:
    corpus = [
        _tokenize(
            " ".join(
                [
                    str(item.get("document_key") or ""),
                    str(item.get("page_content") or ""),
                    " ".join(_json_list(item.get("keywords_json"))),
                ]
            )
        )
        for item in records
    ]
    index = BM25Okapi(corpus) if records and any(corpus) else None
    return _CachedBm25(records=records, index=index)


def _bm25_search(
    cached: _CachedBm25,
    *,
    query: str,
    limit: int,
) -> dict[str, dict[str, Any]]:
    if cached.index is None:
        return {}
    tokens = _tokenize(query)
    if not tokens:
        return {}
    scores = cached.index.get_scores(tokens)
    indexes = sorted(
        range(len(scores)),
        key=lambda index: float(scores[index]),
        reverse=True,
    )[:limit]
    hits = {}
    for index in indexes:
        score = float(scores[index])
        if score <= 0:
            continue
        record = cached.records[index]
        evidence_id = str(record.get("evidence_id") or "")
        block_id = str(record.get("block_id") or "")
        if evidence_id and block_id:
            hits[evidence_id] = {
                "evidence_id": evidence_id,
                "block_id": block_id,
                "vector_score": None,
                "bm25_score": score,
            }
    return hits


def _exact_identifier_search(
    cached: _CachedBm25,
    *,
    query: str,
    limit: int,
) -> dict[str, dict[str, Any]]:
    anchors = _exact_anchors(query)
    if not anchors:
        return {}
    scored: list[tuple[float, str, str]] = []
    for record in cached.records:
        evidence_id = str(record.get("evidence_id") or "")
        block_id = str(record.get("block_id") or "")
        if not evidence_id or not block_id:
            continue
        identifier_text = " ".join(
            [
                evidence_id,
                block_id,
                str(record.get("document_id") or ""),
                str(record.get("document_key") or ""),
            ]
        ).casefold()
        content = str(record.get("page_content") or "").casefold()
        score = 0.0
        for anchor in anchors:
            normalized_anchor = anchor.casefold()
            if normalized_anchor in identifier_text:
                score += 200.0
            if normalized_anchor in content:
                score += 50.0
        if score > 0:
            scored.append((score, evidence_id, block_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {
        evidence_id: {
            "evidence_id": evidence_id,
            "block_id": block_id,
            "vector_score": None,
            "bm25_score": score,
        }
        for score, evidence_id, block_id in scored[:limit]
    }


def _merge_lexical_hits(
    *,
    bm25_hits: dict[str, dict[str, Any]],
    exact_hits: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {key: dict(value) for key, value in bm25_hits.items()}
    for evidence_id, exact_hit in exact_hits.items():
        current = merged.get(evidence_id)
        if current is None:
            merged[evidence_id] = dict(exact_hit)
            continue
        current["bm25_score"] = float(
            current.get("bm25_score") or 0.0
        ) + float(exact_hit.get("bm25_score") or 0.0)
    return merged


def _exact_anchors(query: str) -> list[str]:
    patterns = (
        r"\b(?:EV|BLK|DOC)-[A-Za-z0-9][A-Za-z0-9_.:-]*\b",
        (
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
        ),
        (
            r"第\s*[一二三四五六七八九十百零0-9]+"
            r"(?:\.[0-9]+)*\s*[章节条款项]"
            r"|(?<!\d)\d+(?:\.\d+){1,3}\s*(?:条|款|项)?"
        ),
        (
            r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
            r"|\d{1,2}\s*月\s*\d{1,2}\s*日"
            r"|\d{1,2}\s*[:：]\s*\d{2}"
        ),
        r"\d+(?:\.\d+)?\s*(?:亿|万|千)?\s*元|\d+(?:\.\d+)?\s*%",
        r"\d+(?:\.\d+)?\s*(?:日历天|工作日|天|个月|月|年)",
        (
            r"[\w\u4e00-\u9fff（）()【】\[\].-]+"
            r"\.(?:pdf|docx?|xlsx?|xlsm|txt|md)\b"
        ),
    )
    anchors: list[str] = []
    for pattern in patterns:
        anchors.extend(
            match.group(0).strip()
            for match in re.finditer(pattern, query, flags=re.I)
            if match.group(0).strip()
        )
    anchors.extend(
        match.group(1).strip()
        for match in re.finditer(r"[“\"《](.{2,80}?)[”\"》]", query)
        if match.group(1).strip()
    )
    return list(dict.fromkeys(anchors))


def _tokenize(value: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(value or "")).casefold().strip()
    tokens = [
        item.strip()
        for item in jieba.lcut(normalized)
        if item.strip() and re.search(r"[\w\u4e00-\u9fff]", item)
    ]
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    for run in chinese_runs:
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _block_record(
    *,
    request: TenderReindexRequest,
    block: TenderIndexBlock,
    vector: list[float],
) -> dict[str, Any]:
    pk = hashlib.sha256(
        (
            f"{request.case_id}:{request.manifest_version}:"
            f"{request.manifest_hash}:{block.evidence_id}"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "pk": pk,
        "case_id": request.case_id,
        "manifest_version": request.manifest_version,
        "manifest_hash": request.manifest_hash,
        "evidence_id": block.evidence_id,
        "block_id": block.block_id,
        "document_id": block.document_id,
        "document_key": block.document_key,
        "document_version": block.document_version,
        "block_order": block.block_order,
        "content_hash": block.content_hash,
        "page_content": block.content,
        "keywords_json": json.dumps(
            block.keywords,
            ensure_ascii=False,
            separators=(",", ":"),
        )[:4000],
        "locator_json": json.dumps(
            block.locator,
            ensure_ascii=False,
            separators=(",", ":"),
        )[:4000],
        "vector": vector,
    }


def _validate_scope(case_id: str, manifest_hash: str) -> None:
    try:
        parsed = uuid.UUID(case_id)
    except ValueError as exc:
        raise ValueError("case_id must be a UUID") from exc
    if str(parsed) != case_id.lower():
        raise ValueError("case_id must use canonical UUID form")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_hash.lower()):
        raise ValueError("manifest_hash must be a SHA-256 hex digest")


def _validate_blocks(blocks: list[TenderIndexBlock]) -> None:
    evidence_ids = [item.evidence_id for item in blocks]
    block_ids = [item.block_id for item in blocks]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_id values must be unique inside a snapshot")
    if len(block_ids) != len(set(block_ids)):
        raise ValueError("block_id values must be unique inside a snapshot")
    for item in blocks:
        if not re.fullmatch(r"[0-9a-f]{64}", item.content_hash.lower()):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        actual = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        if actual != item.content_hash.lower():
            raise ValueError(
                f"content hash mismatch for evidence_id={item.evidence_id}"
            )


def _snapshot_expr(
    case_id: str,
    manifest_version: int,
    manifest_hash: str,
) -> str:
    _validate_scope(case_id, manifest_hash)
    return (
        f'case_id == "{case_id}" and '
        f"manifest_version == {int(manifest_version)} and "
        f'manifest_hash == "{manifest_hash}"'
    )


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]
