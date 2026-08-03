"""Keep construction notes separate from pricing-source explanations."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_PRICING_ONLY_CLAUSE_PATTERNS = (
    re.compile(r"^(?:报价|价格|取价|计价)来源\s*[：:]"),
    re.compile(r"^按[“\"]?账户定额.*企业定额.*AI\s*估价.*命中"),
    re.compile(r"账户定额与企业定额均未命中.*AI\s*估价"),
    re.compile(r"^(?:已使用|采用|使用)\s*AI\s*(?:估价|报价)"),
    re.compile(r"^未连接真实模型时的保守规则估价"),
    re.compile(r"^缺少真实模型推理依据"),
    re.compile(r"^未读取外部市场价或客户认可价格"),
)


def _clause_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("。；;，,:：")


def construction_note_only(
    value: Any,
    *,
    pricing_phrases: Iterable[Any] = (),
) -> str:
    """Remove legacy or duplicated pricing clauses while preserving construction content."""
    text = str(value or "").strip()
    if not text:
        return ""
    pricing_clause_keys = {
        _clause_key(clause)
        for phrase in pricing_phrases
        for clause in re.split(r"[。；;\n]+", str(phrase or ""))
        if _clause_key(clause)
    }
    clauses = [
        clause.strip()
        for clause in re.split(r"[。；;\n]+", text)
        if clause.strip()
    ]
    retained = [
        clause
        for clause in clauses
        if not any(pattern.search(clause) for pattern in _PRICING_ONLY_CLAUSE_PATTERNS)
        and _clause_key(clause) not in pricing_clause_keys
    ]
    return "；".join(retained) + ("。" if retained else "")
