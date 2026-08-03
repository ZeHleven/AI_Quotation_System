# RET-EXP-001 — Candidate Depth Decoupling

This sanitized experiment summary contains no tender excerpts or client-specific answer facts.

## Hypothesis

The baseline coupled per-query candidate depth to the final Top K. Increasing only the internal candidate depth from 5 to 20, while preserving final Top5, should improve Mean Recall@5 by at least 0.03 without reducing MRR.

## Controlled change

- Independent variable: `per_query_candidate_top_k`, 5 → 20
- Final Top K: unchanged at 5
- Dataset fingerprint: unchanged
- Query planner, router, embedding, chunking, RRF, and reranker: unchanged
- Holdout: not executed

## Results

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| Hit@5 | 0.8000 | 0.8500 | +0.0500 |
| Mean Recall@5 | 0.7250 | 0.7750 | +0.0500 |
| Mean Precision@5 | 0.1800 | 0.1900 | +0.0100 |
| MRR | 0.7375 | 0.7875 | +0.0500 |
| nDCG@5 | 0.7035 | 0.7535 | +0.0500 |
| Mean latency | 465.1ms | 480.5ms | +15.4ms |
| P95 latency | 1197ms | 1288ms | +91ms |
| Execution errors | 0 | 0 | 0 |

- Improved cases: 1
- Regressed cases: 0
- P95 latency increase: approximately 7.6%, below the 15% gate

## Reasoning

- Observation: one previously missed hybrid case became a rank-1 hit, with no case-level regression.
- Inference: shallow candidate generation was one real retrieval bottleneck, and internal candidate depth should remain independent from the final result limit.
- Limitation: only one case improved, so candidate depth did not explain the remaining failures.
- Decision: keep the change.
- Next experiment: `RET-EXP-002`, adding factual atomic queries to hybrid risk questions.
