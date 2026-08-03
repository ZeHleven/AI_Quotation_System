# Bid-intake Agent Retrieval Baseline v1

This sanitized summary contains no tender excerpts or client-specific answer facts.

## Setup

- Dataset: 20 independently approved historical Development cases from two projects
- Holdout: excluded
- Backend: layered evidence retrieval with exact, semantic, and hybrid routes
- Fusion: reciprocal rank fusion
- Reranker: none
- Final Top K: 5
- Analysis LLM: not invoked
- Dataset fingerprint: `48e44f2271aa831714e82855363980766acaa6b028a1a55f918c58cbca7bb80a`

## Baseline metrics

| Metric | Result |
|---|---:|
| Hit@5 | 0.8000 |
| Mean Recall@5 | 0.7250 |
| Mean Precision@5 | 0.1800 |
| MRR | 0.7375 |
| nDCG@5 | 0.7035 |
| Routing exact match | 1.0000 |
| Query-count accuracy | 1.0000 |
| Topic recall | 1.0000 |
| Mean latency | 465.1 ms |
| P95 latency | 1197 ms |
| Execution errors | 0 |

## Finding

Routing was not the baseline bottleneck. Exact factual queries were substantially stronger than hybrid risk queries, while hard cross-block and multi-topic cases had low recall. A Development-only diagnostic showed that candidate depth changed fused rankings materially, proving that internal recall depth was coupled too tightly to the final Top K.

## Engineering reasoning

- Observation: routing, query-count accuracy, and topic recall were all 1.0, while Mean Recall@5 was 0.725.
- Inference: the Agent generally knew how to search, but candidate generation and ranking did not place all required evidence in the final five results.
- Alternative explanations: strict Gold annotations, evidence granularity, embedding mismatch, and missing topic-coverage constraints could also contribute.
- Falsifiable hypothesis: increasing only per-route candidate depth from 5 to 20, while preserving final Top5, should improve Mean Recall@5 by at least 0.03 without reducing MRR.
- Next experiment: `RET-EXP-001`.

## Next controlled experiments

1. Separate per-route candidate depth from final Top K.
2. Add factual atomic queries to semantic risk questions.
3. Add topic-coverage constraints before final RRF truncation.
4. Improve discrimination between closely related clauses.
5. Tune only on Development; run the frozen Holdout once after selecting the candidate.
