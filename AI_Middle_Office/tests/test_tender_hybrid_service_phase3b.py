from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import types
import uuid
from pathlib import Path

import pytest


class _FakeBm25:
    def __init__(self, corpus):
        self.corpus = [set(item) for item in corpus]

    def get_scores(self, tokens):
        query = set(tokens)
        return [float(len(query & document)) for document in self.corpus]


def _load_service_module(monkeypatch):
    fake_jieba = types.ModuleType("jieba")
    fake_jieba.lcut = lambda value: re.findall(
        r"[a-z0-9]+|[\u4e00-\u9fff]+",
        value,
        flags=re.IGNORECASE,
    )
    fake_rank = types.ModuleType("rank_bm25")
    fake_rank.BM25Okapi = _FakeBm25
    monkeypatch.setitem(sys.modules, "jieba", fake_jieba)
    monkeypatch.setitem(sys.modules, "rank_bm25", fake_rank)
    module_path = (
        Path(__file__).resolve().parents[2]
        / "rag_docker"
        / "tender_evidence_search.py"
    )
    spec = importlib.util.spec_from_file_location(
        "phase3b_tender_evidence_search",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeArray(list):
    def tolist(self):
        return list(self)


class _FakeEmbeddingModel:
    def __init__(self):
        self.calls = 0

    def encode(self, values, **kwargs):
        del kwargs
        self.calls += 1
        return _FakeArray(
            [[1.0] + [0.0] * 767 for _value in values]
        )


class _FakeEntity:
    def __init__(self, row):
        self.row = row

    def get(self, key):
        return self.row.get(key)


class _FakeHit:
    def __init__(self, row, distance):
        self.entity = _FakeEntity(row)
        self.distance = distance


class _FakeCollection:
    field_names = [
        "pk",
        "case_id",
        "manifest_version",
        "manifest_hash",
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
        "vector",
    ]

    def __init__(self):
        self.rows = []

    def query(self, **kwargs):
        del kwargs
        return [
            {key: value for key, value in row.items() if key != "vector"}
            for row in self.rows
        ]

    def insert(self, columns):
        for values in zip(*columns):
            self.rows.append(dict(zip(self.field_names, values)))

    def flush(self):
        return None

    def delete(self, expr):
        del expr
        self.rows = []

    def search(self, **kwargs):
        del kwargs
        return [
            [
                _FakeHit(row, 0.90 - index * 0.05)
                for index, row in enumerate(self.rows)
            ]
        ]


def _block(module, text: str, order: int):
    suffix = uuid.uuid4()
    return module.TenderIndexBlock(
        evidence_id=f"EV-{suffix}",
        block_id=f"BLK-{suffix}",
        document_id=str(uuid.uuid4()),
        document_key="tender-notice",
        document_version=1,
        block_order=order,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content=text,
        keywords=["保证金"] if "保证金" in text else [],
        locator={"locator_type": "page", "page": order + 1},
    )


def test_tender_hybrid_service_reindex_is_idempotent_and_searches(monkeypatch):
    module = _load_service_module(monkeypatch)
    embedding = _FakeEmbeddingModel()
    collection = _FakeCollection()
    index = module.TenderEvidenceHybridIndex(embedding)
    index._collection = collection
    case_id = str(uuid.uuid4())
    manifest_hash = "a" * 64
    request = module.TenderReindexRequest(
        case_id=case_id,
        manifest_version=1,
        manifest_hash=manifest_hash,
        index_schema_version="tender-hybrid-v1",
        blocks=[
            _block(module, "投标截止时间为2026年8月15日。", 0),
            _block(module, "投标保证金为20万元。", 1),
        ],
    )

    first = index.reindex(request)
    second = index.reindex(request)
    assert first["indexed_block_count"] == 2
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert len(collection.rows) == 2
    assert embedding.calls == 1

    result = index.search(
        module.TenderSearchRequest(
            case_id=case_id,
            manifest_version=1,
            manifest_hash=manifest_hash,
            query="保证金",
            top_k=2,
        )
    )
    assert result["retrieval"]["vector_count"] == 2
    assert result["retrieval"]["bm25_count"] >= 1
    assert result["retrieval"]["requested_mode"] == "hybrid"
    assert result["hits"][0]["rrf_score"] > 0


def test_tender_search_modes_skip_unneeded_channels(monkeypatch):
    module = _load_service_module(monkeypatch)
    embedding = _FakeEmbeddingModel()
    collection = _FakeCollection()
    index = module.TenderEvidenceHybridIndex(embedding)
    index._collection = collection
    case_id = str(uuid.uuid4())
    manifest_hash = "c" * 64
    index.reindex(
        module.TenderReindexRequest(
            case_id=case_id,
            manifest_version=1,
            manifest_hash=manifest_hash,
            index_schema_version="tender-hybrid-v1",
            blocks=[
                _block(module, "第12.3条投标保证金为20万元。", 0),
                _block(module, "延期责任可能造成履约风险。", 1),
            ],
        )
    )
    assert embedding.calls == 1

    exact = index.search(
        module.TenderSearchRequest(
            case_id=case_id,
            manifest_version=1,
            manifest_hash=manifest_hash,
            query="第12.3条",
            top_k=2,
            search_mode="exact",
        )
    )
    assert embedding.calls == 1
    assert exact["retrieval"]["vector_count"] == 0
    assert exact["retrieval"]["exact_identifier_count"] >= 1
    assert exact["hits"]

    semantic = index.search(
        module.TenderSearchRequest(
            case_id=case_id,
            manifest_version=1,
            manifest_hash=manifest_hash,
            query="延期会带来什么履约风险",
            top_k=2,
            search_mode="semantic",
        )
    )
    assert embedding.calls == 2
    assert semantic["retrieval"]["bm25_count"] == 0
    assert semantic["retrieval"]["vector_count"] == 2


def test_tender_hybrid_service_rejects_scope_and_content_tampering(monkeypatch):
    module = _load_service_module(monkeypatch)
    with pytest.raises(ValueError, match="case_id"):
        module._snapshot_expr(
            'bad" or case_id != ""',
            1,
            "a" * 64,
        )

    block = _block(module, "投标保证金为20万元。", 0)
    block.content_hash = "b" * 64
    with pytest.raises(ValueError, match="content hash mismatch"):
        module._validate_blocks([block])


def test_rrf_rewards_evidence_recalled_by_both_channels(monkeypatch):
    module = _load_service_module(monkeypatch)
    merged = module.rrf_merge(
        vector_hits={
            "EV-vector-only": {
                "block_id": "BLK-1",
                "vector_score": 0.95,
            },
            "EV-both": {
                "block_id": "BLK-2",
                "vector_score": 0.90,
            },
        },
        bm25_hits={
            "EV-both": {
                "block_id": "BLK-2",
                "bm25_score": 4.0,
            },
            "EV-bm25-only": {
                "block_id": "BLK-3",
                "bm25_score": 3.0,
            },
        },
    )
    assert merged[0]["evidence_id"] == "EV-both"
    assert merged[0]["vector_score"] == 0.90
    assert merged[0]["bm25_score"] == 4.0
