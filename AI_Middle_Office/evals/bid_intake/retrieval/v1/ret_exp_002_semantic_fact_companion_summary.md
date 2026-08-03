# RET-EXP-002 — Semantic Fact Companion

This sanitized experiment summary contains no tender excerpts or client-specific answer facts.

## Hypothesis

Adding one factual supporting query to each single-topic hybrid risk question should raise Hybrid Recall@5 from 0.50 to 1.00 and overall Mean Recall@5 by at least 0.05, with no MRR or nDCG regression.

## Controlled change

- Independent variable: one broad factual supporting query, disabled → enabled
- Per-query candidate depth: unchanged at 20
- Final Top K: unchanged at 5
- Dataset fingerprint: unchanged
- Query-count metrics: primary queries only; supporting queries audited separately
- Holdout: not executed

## Results

| Metric | RET-EXP-001 | Candidate | Delta |
|---|---:|---:|---:|
| Hit@5 | 0.8500 | 0.9000 | +0.0500 |
| Mean Recall@5 | 0.7750 | 0.7917 | +0.0167 |
| Mean Precision@5 | 0.1900 | 0.2000 | +0.0100 |
| MRR | 0.7875 | 0.8000 | +0.0125 |
| nDCG@5 | 0.7535 | 0.7636 | +0.0101 |
| Hybrid Recall@5 | 0.5000 | 0.6667 | +0.1667 |
| Mean latency | 480.5ms | 534.5ms | +54.0ms |
| P95 latency | 1288ms | 1401ms | +113ms |
| Execution errors | 0 | 0 | 0 |

- Improved cases: 1
- Regressed cases: 0
- Gate result: failed the Recall and Hybrid Recall targets

## Reasoning

- Observation: a previously missed hybrid case gained one of its three Gold Evidence blocks.
- Inference: factual support is directionally useful, but one broad payment query cannot cover facts distributed across multiple payment-lifecycle blocks.
- Alternative explanations: final Top5 capacity, strict block-level Gold annotations, or missing context expansion could also limit coverage.
- Decision: do not enable this candidate in the MCP runtime.
- Generalization review: the original `RET-EXP-002B` plan would have introduced payment-specific facet queries based mainly on one improved case, so it is not accepted as the next experiment.
- Next experiment: `RET-EXP-003A`, generic atomic fact-slot decomposition based on object, requested attribute, and positive/negative constraints. It must improve at least two different phrasings across topics or projects before it can be kept.
