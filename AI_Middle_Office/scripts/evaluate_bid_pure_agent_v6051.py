"""V605-1 real local BCE embedding and reranker evaluation.

Only versioned synthetic text is accepted. Both models are loaded cache-only
from explicit immutable snapshot directories. The evaluator performs no PDF,
OCR, vision, generation-model, database, Milvus, MCP, or network operation.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.bid_field_aware_lexical import rank_field_aware_bm25f
from app.services.bid_lightweight_reranker import (
    RQ2C_MODEL_REVISION,
    BidLightweightRerankerUnavailable,
    LocalBceCrossEncoderReranker,
    RerankCandidateInput,
    rerank_frozen_candidates,
    validate_reranker_descriptor,
)
from app.services.bid_local_semantic_vector_provider import (
    LocalBceExactSemanticProvider,
)
from app.services.bid_semantic_vector_provider import (
    RQ2A_EMBEDDING_DIMENSION,
    RQ2A_EMBEDDING_MODEL_REVISION,
    BidSemanticProviderUnavailable,
    SemanticDocument,
    validate_descriptor,
)
from scripts.evaluate_bid_pure_agent_v604 import (
    DATASET_PATH as V604_DATASET_PATH,
    SyntheticOfflineRagHarness,
    load_dataset as load_v604_dataset,
)


DATASET_PATH = (
    PROJECT_ROOT
    / "evals"
    / "bid_assessment"
    / "v6051-real-model-synthetic-cases.json"
)
SCHEMA_VERSION = "bid.pure_agent.v6051.real_models.v1"


class V6051EvaluationError(RuntimeError):
    pass


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(
    path: Path,
    *,
    revision: str,
    role: str,
) -> tuple[Path, dict[str, Any]]:
    resolved = path.resolve()
    if not resolved.is_dir() or revision not in resolved.parts:
        raise V6051EvaluationError(f"{role} snapshot revision is unavailable")
    if not (resolved / "config.json").is_file():
        raise V6051EvaluationError(f"{role} snapshot config is unavailable")
    weights = next(
        (
            candidate
            for candidate in (
                resolved / "model.safetensors",
                resolved / "pytorch_model.bin",
            )
            if candidate.is_file()
        ),
        None,
    )
    if weights is None:
        raise V6051EvaluationError(f"{role} snapshot weights are unavailable")
    return resolved, {
        "revision": revision,
        "weight_format": weights.suffix.removeprefix("."),
        "weight_bytes": weights.stat().st_size,
        "weight_sha256": _file_sha256(weights),
    }


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise V6051EvaluationError("unsupported V605-1 dataset schema")
    if payload.get("dataset_kind") != "synthetic_only":
        raise V6051EvaluationError("V605-1 accepts synthetic-only datasets")
    embedding_cases = payload.get("embedding_cases")
    reranker_cases = payload.get("reranker_cases")
    if not isinstance(embedding_cases, list) or not embedding_cases:
        raise V6051EvaluationError("V605-1 requires embedding cases")
    if not isinstance(reranker_cases, list) or not reranker_cases:
        raise V6051EvaluationError("V605-1 requires reranker cases")
    ids = [
        case.get("id")
        for case in (*embedding_cases, *reranker_cases)
        if isinstance(case, dict)
    ]
    if len(ids) != len(embedding_cases) + len(reranker_cases):
        raise V6051EvaluationError("V605-1 cases must be objects with ids")
    if len(ids) != len(set(ids)):
        raise V6051EvaluationError("V605-1 case ids must be unique")
    return payload


def guarded_optional_result(
    operation: Callable[[], Sequence[str]],
    *,
    frozen_baseline: Sequence[str],
    unavailable_errors: tuple[type[Exception], ...],
) -> dict[str, Any]:
    """Preserve the frozen baseline and disclose degradation on unavailability."""

    baseline = tuple(frozen_baseline)
    try:
        selected = tuple(operation())
    except unavailable_errors as exc:
        return {
            "status": "degraded",
            "selected_keys": baseline,
            "baseline_preserved": True,
            "error_code": str(getattr(exc, "code", exc.__class__.__name__)),
        }
    return {
        "status": "enabled",
        "selected_keys": selected,
        "baseline_preserved": selected == baseline,
        "error_code": None,
    }


def _semantic_documents(index: Any) -> tuple[SemanticDocument, ...]:
    return tuple(
        SemanticDocument(
            provider_record_id=_sha256_json(
                {"domain": index.domain, "child_key": child.evidence_key}
            ),
            retrieval_child_id=child.evidence_key,
            retrieval_child_key=child.evidence_key,
            source_entry_hash=child.retrieval_hash,
            embedding_text_hash=hashlib.sha256(
                child.retrieval_text.encode("utf-8")
            ).hexdigest(),
            text=child.retrieval_text,
        )
        for child in index.child_by_ref.values()
    )


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 6) if values else 0.0


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int((percentile * len(ordered)) + 0.999999)))
    return round(ordered[rank - 1], 6)


def _evaluate_embedding(
    dataset: Mapping[str, Any],
    *,
    model_path: Path,
) -> dict[str, Any]:
    harness = SyntheticOfflineRagHarness(load_v604_dataset(V604_DATASET_PATH))
    provider = LocalBceExactSemanticProvider(model_path=str(model_path))
    validate_descriptor(provider.descriptor)
    namespaces: dict[str, str] = {}
    receipts_by_domain: dict[str, tuple[Any, ...]] = {}
    build_started = time.perf_counter()
    for domain, index in harness.indexes.items():
        namespace = f"v6051-{domain.replace('_', '-')}"
        documents = _semantic_documents(index)
        receipts = provider.upsert_documents(
            namespace=namespace,
            provider_request_id=(
                "bid-semantic-index:"
                + _sha256_json({"domain": domain, "documents": len(documents)})
            ),
            documents=documents,
        )
        namespaces[domain] = namespace
        receipts_by_domain[domain] = receipts
    cold_build_seconds = round(time.perf_counter() - build_started, 6)

    replay_receipts: dict[str, tuple[Any, ...]] = {}
    replay_started = time.perf_counter()
    for domain, index in harness.indexes.items():
        replay_receipts[domain] = provider.upsert_documents(
            namespace=namespaces[domain],
            provider_request_id=(
                "bid-semantic-index:"
                + _sha256_json({"domain": domain, "replay": True})
            ),
            documents=_semantic_documents(index),
        )
    replay_build_seconds = round(time.perf_counter() - replay_started, 6)

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    query_seconds: list[float] = []
    complement_count = 0
    cases: list[dict[str, Any]] = []
    deterministic = True
    fallback_lexical_keys: tuple[str, ...] = ()
    for case in dataset["embedding_cases"]:
        domain = str(case["domain"])
        index = harness.indexes[domain]
        expected_refs = {
            index.child_ref_by_block_key[block_key]
            for block_key in case["expected_block_keys"]
        }
        started = time.perf_counter()
        hits = provider.search(
            namespace=namespaces[domain],
            query=str(case["query"]),
            top_k=int(case["top_k"]),
        )
        query_seconds.append(time.perf_counter() - started)
        replay_hits = provider.search(
            namespace=namespaces[domain],
            query=str(case["query"]),
            top_k=int(case["top_k"]),
        )
        deterministic = deterministic and hits == replay_hits
        hit_refs = tuple(hit.retrieval_child_key for hit in hits)
        recall = len(set(hit_refs[:3]) & expected_refs) / len(expected_refs)
        rank = next(
            (
                position
                for position, child_ref in enumerate(hit_refs, 1)
                if child_ref in expected_refs
            ),
            None,
        )
        reciprocal_rank = 0.0 if rank is None else 1.0 / rank
        lexical = rank_field_aware_bm25f(
            str(case["query"]),
            index.lexical_corpus,
        )
        if not fallback_lexical_keys and lexical:
            fallback_lexical_keys = tuple(value.child_id for value in lexical[:3])
        lexical_miss = not any(
            value.child_id in expected_refs for value in lexical[:3]
        )
        if lexical_miss and recall == 1.0:
            complement_count += 1
        case_checks = {
            "expected_recall": recall == 1.0,
            "lexical_miss_when_declared": (
                lexical_miss if case.get("expect_lexical_miss") is True else True
            ),
            "child_only": all(ref in index.child_by_ref for ref in hit_refs),
        }
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        cases.append(
            {
                "id": case["id"],
                "domain": domain,
                "hit_count": len(hit_refs),
                "recall_at_3": round(recall, 6),
                "first_relevant_rank": rank,
                "top_scores": [hit.score for hit in hits],
                "lexical_expected_miss": lexical_miss,
                "checks": case_checks,
                "passed": all(case_checks.values()),
            }
        )

    all_receipts = tuple(
        receipt
        for receipts in receipts_by_domain.values()
        for receipt in receipts
    )
    missing_path = model_path.parent / "missing-v6051-snapshot"
    try:
        LocalBceExactSemanticProvider(model_path=str(missing_path))
    except BidSemanticProviderUnavailable:
        missing_path_fail_closed = True
    else:
        missing_path_fail_closed = False
    if not fallback_lexical_keys:
        raise V6051EvaluationError("V605-1 has no lexical fallback candidates")
    fallback = guarded_optional_result(
        lambda: (_ for _ in ()).throw(
            BidSemanticProviderUnavailable("synthetic unavailable")
        ),
        frozen_baseline=fallback_lexical_keys,
        unavailable_errors=(BidSemanticProviderUnavailable,),
    )
    return {
        "model_descriptor": provider.descriptor.stable_payload(),
        "backend": {
            "backend_id": provider.backend_id,
            "exact_cosine": True,
            "production_milvus_executed": False,
        },
        "document_count": len(all_receipts),
        "vector_dimension_valid": all(
            receipt.vector_dimension == RQ2A_EMBEDDING_DIMENSION
            for receipt in all_receipts
        ),
        "vector_hashes_valid": all(
            len(receipt.vector_hash) == 64 for receipt in all_receipts
        ),
        "idempotent_replay": replay_receipts == receipts_by_domain,
        "deterministic_query_replay": deterministic,
        "metrics": {
            "recall_at_3": _mean(recalls),
            "mean_reciprocal_rank": _mean(reciprocal_ranks),
            "complement_case_count": complement_count,
        },
        "latency_seconds": {
            "cold_build": cold_build_seconds,
            "replay_build": replay_build_seconds,
            "warm_query_mean": _mean(query_seconds),
            "warm_query_p95": _percentile_nearest_rank(query_seconds, 0.95),
        },
        "safe_degradation": {
            "missing_path_fail_closed": missing_path_fail_closed,
            **fallback,
        },
        "cases": cases,
    }


def _rerank_candidates(case: Mapping[str, Any]) -> tuple[
    tuple[RerankCandidateInput, ...], dict[str, str], dict[str, str]
]:
    candidates: list[RerankCandidateInput] = []
    label_by_key: dict[str, str] = {}
    key_by_label: dict[str, str] = {}
    for rank, raw in enumerate(case["candidates"], 1):
        label = str(raw["label"])
        child_key = "chunk:" + _sha256_json(
            {"case": case["id"], "label": label, "text": raw["text"]}
        )
        candidate = RerankCandidateInput(
            child_id=f"{case['id']}:{label}",
            child_key=child_key,
            parent_key="section:"
            + _sha256_json({"case": case["id"], "parent": raw["parent_label"]}),
            fusion_rank=rank,
            fusion_score=round(1.0 / (60 + rank), 10),
            lexical_rank=raw["lexical_rank"],
            semantic_rank=raw["semantic_rank"],
            retrieval_hash=hashlib.sha256(str(raw["text"]).encode("utf-8")).hexdigest(),
            text=str(raw["text"]),
        )
        candidates.append(candidate)
        label_by_key[child_key] = label
        key_by_label[label] = child_key
    return tuple(candidates), label_by_key, key_by_label


def _evaluate_reranker(
    dataset: Mapping[str, Any],
    *,
    model_path: Path,
) -> dict[str, Any]:
    provider = LocalBceCrossEncoderReranker(
        model_path=str(model_path),
        offline=True,
        batch_size=8,
    )
    validate_reranker_descriptor(provider.descriptor)
    baseline_recalls: list[float] = []
    final_recalls: list[float] = []
    first_seconds: list[float] = []
    warm_seconds: list[float] = []
    improvement_count = 0
    regression_count = 0
    promotion_count = 0
    deterministic = True
    cases: list[dict[str, Any]] = []
    first_case_baseline: tuple[str, ...] | None = None
    for case in dataset["reranker_cases"]:
        candidates, label_by_key, key_by_label = _rerank_candidates(case)
        expected_keys = {
            key_by_label[label] for label in case["expected_relevant_labels"]
        }
        fusion_hash = _sha256_json(
            [
                {
                    "child_key": candidate.child_key,
                    "fusion_rank": candidate.fusion_rank,
                    "fusion_score": candidate.fusion_score,
                }
                for candidate in candidates
            ]
        )
        query_hash = _sha256_json({"query": case["query"]})
        started = time.perf_counter()
        result = rerank_frozen_candidates(
            query=str(case["query"]),
            candidates=candidates,
            fusion_result_hash=fusion_hash,
            query_plan_hash=query_hash,
            top_k=int(case["top_k"]),
            provider=provider,
        )
        first_seconds.append(time.perf_counter() - started)
        replay_started = time.perf_counter()
        replay = rerank_frozen_candidates(
            query=str(case["query"]),
            candidates=candidates,
            fusion_result_hash=fusion_hash,
            query_plan_hash=query_hash,
            top_k=int(case["top_k"]),
            provider=provider,
        )
        warm_seconds.append(time.perf_counter() - replay_started)
        deterministic = deterministic and result == replay
        baseline_hit = bool(set(result.baseline_child_keys) & expected_keys)
        final_hit = bool(set(result.final_child_keys) & expected_keys)
        baseline_recall = float(baseline_hit)
        final_recall = float(final_hit)
        baseline_recalls.append(baseline_recall)
        final_recalls.append(final_recall)
        if final_recall > baseline_recall:
            improvement_count += 1
        if final_recall < baseline_recall:
            regression_count += 1
        promotion_count += result.promotion_count
        if first_case_baseline is None:
            first_case_baseline = result.baseline_child_keys
        scores = {
            label_by_key[value.child_key]: value.rerank_score
            for value in result.candidates
        }
        expects_improvement = case.get("expect_improvement") is True
        case_checks = {
            "no_recall_regression": final_recall >= baseline_recall,
            "expected_improvement": (
                final_recall > baseline_recall if expects_improvement else True
            ),
            "zero_promotion_identity": (
                result.final_child_keys == result.baseline_child_keys
                if not expects_improvement and result.promotion_count == 0
                else True
            ),
            "bounded_promotions": result.promotion_count <= 2,
        }
        cases.append(
            {
                "id": case["id"],
                "baseline_labels": [
                    label_by_key[key] for key in result.baseline_child_keys
                ],
                "final_labels": [label_by_key[key] for key in result.final_child_keys],
                "baseline_recall_at_k": baseline_recall,
                "final_recall_at_k": final_recall,
                "promotion_count": result.promotion_count,
                "scores": scores,
                "checks": case_checks,
                "passed": all(case_checks.values()),
                "result_hash": result.result_hash,
            }
        )

    if first_case_baseline is None:
        raise V6051EvaluationError("V605-1 reranker cases are empty")
    fallback = guarded_optional_result(
        lambda: (_ for _ in ()).throw(
            BidLightweightRerankerUnavailable("synthetic unavailable")
        ),
        frozen_baseline=first_case_baseline,
        unavailable_errors=(BidLightweightRerankerUnavailable,),
    )
    return {
        "model_descriptor": provider.descriptor.stable_payload(),
        "metrics": {
            "baseline_recall_at_k": _mean(baseline_recalls),
            "final_recall_at_k": _mean(final_recalls),
            "improvement_case_count": improvement_count,
            "regression_count": regression_count,
            "promotion_count": promotion_count,
        },
        "deterministic_replay": deterministic,
        "latency_seconds": {
            "cold_case_max": round(max(first_seconds), 6),
            "warm_case_mean": _mean(warm_seconds),
            "warm_case_p95": _percentile_nearest_rank(warm_seconds, 0.95),
        },
        "safe_degradation": fallback,
        "cases": cases,
    }


def _runtime_versions() -> dict[str, Any]:
    packages = ("numpy", "torch", "transformers", "sentence-transformers")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "device": "cpu",
        "packages": {
            package: importlib.metadata.version(package) for package in packages
        },
    }


def evaluate(
    dataset: Mapping[str, Any],
    *,
    embedding_model_path: Path,
    reranker_model_path: Path,
) -> dict[str, Any]:
    embedding_path, embedding_snapshot = _snapshot(
        embedding_model_path,
        revision=RQ2A_EMBEDDING_MODEL_REVISION,
        role="embedding",
    )
    reranker_path, reranker_snapshot = _snapshot(
        reranker_model_path,
        revision=RQ2C_MODEL_REVISION,
        role="reranker",
    )
    embedding = _evaluate_embedding(dataset, model_path=embedding_path)
    gc.collect()
    reranker = _evaluate_reranker(dataset, model_path=reranker_path)
    thresholds = dataset["thresholds"]
    checks = {
        "embedding_recall": embedding["metrics"]["recall_at_3"]
        >= float(thresholds["embedding_recall_at_3_min"]),
        "embedding_mrr": embedding["metrics"]["mean_reciprocal_rank"]
        >= float(thresholds["embedding_mrr_min"]),
        "embedding_complement": embedding["metrics"]["complement_case_count"]
        >= int(thresholds["embedding_complement_case_count_min"]),
        "embedding_contract": (
            embedding["vector_dimension_valid"]
            and embedding["vector_hashes_valid"]
            and embedding["idempotent_replay"]
            and embedding["deterministic_query_replay"]
            and all(case["passed"] for case in embedding["cases"])
        ),
        "reranker_value": reranker["metrics"]["improvement_case_count"]
        >= int(thresholds["reranker_improvement_case_count_min"]),
        "reranker_no_regression": reranker["metrics"]["regression_count"]
        <= int(thresholds["reranker_regression_count_max"]),
        "reranker_contract": (
            reranker["deterministic_replay"]
            and all(case["passed"] for case in reranker["cases"])
        ),
        "embedding_latency": (
            embedding["latency_seconds"]["cold_build"]
            <= float(thresholds["embedding_cold_build_seconds_max"])
            and embedding["latency_seconds"]["warm_query_p95"]
            <= float(thresholds["embedding_warm_query_seconds_max"])
        ),
        "reranker_latency": reranker["latency_seconds"]["warm_case_p95"]
        <= float(thresholds["reranker_warm_case_seconds_max"]),
        "safe_degradation": (
            embedding["safe_degradation"]["missing_path_fail_closed"]
            and embedding["safe_degradation"]["status"] == "degraded"
            and embedding["safe_degradation"]["baseline_preserved"]
            and reranker["safe_degradation"]["status"] == "degraded"
            and reranker["safe_degradation"]["baseline_preserved"]
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": dataset["dataset_kind"],
        "runtime": _runtime_versions(),
        "snapshots": {
            "embedding": embedding_snapshot,
            "reranker": reranker_snapshot,
        },
        "embedding": embedding,
        "reranker": reranker,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real local BCE V605-1 synthetic offline evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--embedding-model-path", type=Path, required=True)
    parser.add_argument("--reranker-model-path", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report = evaluate(
        load_dataset(args.dataset),
        embedding_model_path=args.embedding_model_path,
        reranker_model_path=args.reranker_model_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_PATH",
    "SCHEMA_VERSION",
    "V6051EvaluationError",
    "evaluate",
    "guarded_optional_result",
    "load_dataset",
]
