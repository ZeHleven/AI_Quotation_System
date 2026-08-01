from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


RETRIEVAL_ROUTE_SCHEMA_VERSION = "adaptive-retrieval-route/v1"
RetrievalMode = Literal["exact", "semantic", "hybrid"]


@dataclass(frozen=True)
class TenderRetrievalRoute:
    query: str
    mode: RetrievalMode
    confidence: float
    exact_signals: tuple[str, ...]
    semantic_signals: tuple[str, ...]
    reason_codes: tuple[str, ...]
    fallback_mode: RetrievalMode | None

    def to_payload(
        self,
        *,
        query_id: str,
        query_kind: str,
    ) -> dict[str, object]:
        return {
            "schema_version": RETRIEVAL_ROUTE_SCHEMA_VERSION,
            "query_id": query_id,
            "query_kind": query_kind,
            "query": self.query,
            "requested_mode": self.mode,
            "confidence": self.confidence,
            "exact_signals": list(self.exact_signals),
            "semantic_signals": list(self.semantic_signals),
            "reason_codes": list(self.reason_codes),
            "fallback_mode": self.fallback_mode,
        }


_EXACT_SIGNAL_PATTERNS = (
    (
        "evidence_identifier",
        re.compile(r"\b(?:EV|BLK|DOC)-[A-Za-z0-9][A-Za-z0-9_.:-]*\b", re.I),
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            re.I,
        ),
    ),
    (
        "clause_reference",
        re.compile(
            r"第\s*[一二三四五六七八九十百零0-9]+"
            r"(?:\.[0-9]+)*\s*[章节条款项]"
            r"|(?<!\d)\d+(?:\.\d+){1,3}\s*(?:条|款|项)?"
        ),
    ),
    (
        "date_or_time",
        re.compile(
            r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?"
            r"|\d{1,2}\s*月\s*\d{1,2}\s*日"
            r"|\d{1,2}\s*[:：]\s*\d{2}"
        ),
    ),
    (
        "amount_or_ratio",
        re.compile(
            r"\d+(?:\.\d+)?\s*(?:亿|万|千)?\s*元"
            r"|\d+(?:\.\d+)?\s*%"
        ),
    ),
    (
        "duration",
        re.compile(r"\d+(?:\.\d+)?\s*(?:日历天|工作日|天|个月|月|年)"),
    ),
    (
        "quoted_phrase",
        re.compile(r"[“\"《](.{2,80}?)[”\"》]"),
    ),
    (
        "file_name",
        re.compile(
            r"[\w\u4e00-\u9fff（）()【】\[\].-]+"
            r"\.(?:pdf|docx?|xlsx?|xlsm|txt|md)\b",
            re.I,
        ),
    ),
)

_SEMANTIC_SIGNAL_PATTERNS = (
    ("risk_analysis", re.compile(r"风险|隐患|不利|后果|影响|损失")),
    ("causal_analysis", re.compile(r"为什么|为何|原因|导致|意味着")),
    ("judgement", re.compile(r"是否值得|是否合理|是否可行|判断|评估|研判")),
    ("recommendation", re.compile(r"建议|应对|怎么处理|如何处理|注意事项")),
    ("comparison", re.compile(r"利弊|优先级|重要性|比较|权衡")),
    ("interpretation", re.compile(r"如何理解|怎么理解|解读|分析")),
)


def route_tender_query(query: str) -> TenderRetrievalRoute:
    """Classify one planned query without an additional model call."""

    normalized = re.sub(r"\s+", " ", str(query or "")).strip()
    if not normalized:
        raise ValueError("query must not be empty")

    exact_signals = tuple(
        name
        for name, pattern in _EXACT_SIGNAL_PATTERNS
        if pattern.search(normalized)
    )
    semantic_signals = tuple(
        name
        for name, pattern in _SEMANTIC_SIGNAL_PATTERNS
        if pattern.search(normalized)
    )

    if exact_signals and semantic_signals:
        return TenderRetrievalRoute(
            query=normalized,
            mode="hybrid",
            confidence=0.96,
            exact_signals=exact_signals,
            semantic_signals=semantic_signals,
            reason_codes=("exact_and_semantic_signals",),
            fallback_mode=None,
        )
    if semantic_signals:
        return TenderRetrievalRoute(
            query=normalized,
            mode="semantic",
            confidence=0.90,
            exact_signals=(),
            semantic_signals=semantic_signals,
            reason_codes=("semantic_intent_only",),
            fallback_mode="hybrid",
        )

    reason = (
        "strong_exact_signal"
        if exact_signals
        else "fact_or_keyword_lookup"
    )
    return TenderRetrievalRoute(
        query=normalized,
        mode="exact",
        confidence=0.92 if exact_signals else 0.82,
        exact_signals=exact_signals,
        semantic_signals=(),
        reason_codes=(reason,),
        fallback_mode="hybrid",
    )
