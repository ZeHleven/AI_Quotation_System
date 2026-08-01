from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .contracts import EvidenceBlock
from .structure_context import structural_row_identity


_DOCUMENT_ROLE_ALIASES: tuple[
    tuple[str, tuple[str, ...]],
    ...,
] = (
    (
        "tender_document",
        ("招标文件", "招标书", "投标须知"),
    ),
    (
        "bill_of_quantities",
        ("工程量清单", "招标清单", "报价清单"),
    ),
    (
        "contract",
        ("施工合同", "合同文件", "合同"),
    ),
    (
        "clarification",
        ("投标答疑", "招标答疑", "答疑", "澄清", "补遗"),
    ),
    (
        "drawing",
        ("施工图纸", "设计图纸", "施工图", "设计图", "图纸"),
    ),
)
_RELATION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "joint_evidence",
        ("共同表明", "共同说明", "结合"),
    ),
    (
        "consistency_or_conflict",
        ("是否一致", "一致", "冲突", "差异", "矛盾"),
    ),
    (
        "precedence",
        ("以哪个为准", "以何为准", "哪个为准"),
    ),
    (
        "state_sync",
        ("同步", "回写", "是否落实", "已经落实", "变更"),
    ),
    (
        "coverage_or_mapping",
        ("覆盖", "对应", "相互约束"),
    ),
    (
        "joint_completeness",
        ("完整约定", "是否已经完整", "是否完整"),
    ),
)
_LIST_REFERENCE_PATTERNS = (
    re.compile(
        r"(?:工程量)?清单\s*(?:序号|编号)\s*[:：]?\s*"
        r"(?P<identifier>\d{1,4})(?:\s*[项号])?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:工程量)?清单\s*第\s*(?P<identifier>\d{1,4})\s*项",
        re.IGNORECASE,
    ),
)
_EXACT_CODE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]{1,8}[-_]?\d{1,6}"
    r"(?![A-Za-z0-9_])"
)
_HEADER_SIGNALS = (
    "序号",
    "项目名称",
    "项目",
    "名称",
    "户型",
    "房型",
    "区域",
    "面积",
    "数量",
    "单位",
    "规格",
    "项目特征",
    "工程名称",
    "类型",
    "内容",
)
_HEADER_FIRST_CELL = re.compile(
    r"^(?:"
    r"序号|编号|项号|项目编码|项目名称|工程名称|"
    r"户型|房型|区域|楼层|部位|类别|类型"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GraphTriggerDecision:
    triggered: bool
    document_roles: tuple[str, ...]
    relation_signals: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "triggered": self.triggered,
            "document_roles": list(self.document_roles),
            "relation_signals": list(self.relation_signals),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class VerifiedExactReference:
    reference_type: str
    target_document_role: str
    identifier: str
    qualifiers: tuple[str, ...]

    @property
    def lookup_query(self) -> str:
        return " ".join((self.identifier, *self.qualifiers))

    @property
    def resolvable(self) -> bool:
        return bool(self.qualifiers)


def decide_graph_trigger(query: str) -> GraphTriggerDecision:
    normalized = re.sub(r"\s+", "", str(query or "")).casefold()
    roles = tuple(
        role
        for role, aliases in _DOCUMENT_ROLE_ALIASES
        if any(alias.casefold() in normalized for alias in aliases)
    )
    signals = tuple(
        signal
        for signal, aliases in _RELATION_SIGNALS
        if any(alias.casefold() in normalized for alias in aliases)
    )
    reasons: list[str] = []
    if len(roles) >= 2:
        reasons.append("multiple_explicit_document_roles")
    else:
        reasons.append("insufficient_explicit_document_roles")
    if signals:
        reasons.append("cross_source_relation_intent")
    else:
        reasons.append("missing_cross_source_relation_intent")
    return GraphTriggerDecision(
        triggered=len(roles) >= 2 and bool(signals),
        document_roles=roles,
        relation_signals=signals,
        reason_codes=tuple(reasons),
    )


def extract_verified_exact_references(
    content: str,
) -> tuple[VerifiedExactReference, ...]:
    text = str(content or "")
    qualifiers = tuple(
        dict.fromkeys(
            item.upper() for item in _EXACT_CODE.findall(text)
        )
    )[:5]
    references: list[VerifiedExactReference] = []
    seen: set[tuple[str, str]] = set()
    for pattern in _LIST_REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            identifier = match.group("identifier")
            key = ("bill_of_quantities_item", identifier)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                VerifiedExactReference(
                    reference_type="bill_of_quantities_item",
                    target_document_role="bill_of_quantities",
                    identifier=identifier,
                    qualifiers=qualifiers,
                )
            )
    return tuple(references)


def is_verified_table_parent_seed(block: EvidenceBlock) -> bool:
    if structural_row_identity(block) is None:
        return False
    cells = [
        item.strip().casefold()
        for item in re.split(r"\s*\|\s*", block.content)
        if item.strip()
    ]
    if not 2 <= len(cells) <= 30:
        return False
    first_cell = re.sub(
        r"[\s:：()（）【】\[\]]+",
        "",
        cells[0],
    )
    if _HEADER_FIRST_CELL.fullmatch(first_cell) is None:
        return False
    signal_count = sum(
        any(signal.casefold() in cell for cell in cells)
        for signal in _HEADER_SIGNALS
    )
    return signal_count >= 2


def verified_structural_children(
    *,
    seed: EvidenceBlock,
    context_blocks: Sequence[EvidenceBlock],
    max_children: int = 4,
) -> list[EvidenceBlock]:
    seed_identity = structural_row_identity(seed)
    if seed_identity is None or not is_verified_table_parent_seed(seed):
        return []
    kind, structure_key, seed_row = seed_identity
    bounded_max = max(1, min(int(max_children), 4))
    children: list[tuple[int, EvidenceBlock]] = []
    for candidate in context_blocks:
        if candidate.evidence_id == seed.evidence_id:
            continue
        if (
            candidate.document_id != seed.document_id
            or candidate.document_version != seed.document_version
        ):
            continue
        identity = structural_row_identity(candidate)
        if identity is None:
            continue
        candidate_kind, candidate_key, candidate_row = identity
        if (
            candidate_kind != kind
            or candidate_key != structure_key
            or candidate_row <= seed_row
            or candidate_row > seed_row + bounded_max
        ):
            continue
        if is_verified_table_parent_seed(candidate):
            continue
        children.append((candidate_row, candidate))
    children.sort(key=lambda item: (item[0], item[1].evidence_id))
    return [item[1] for item in children[:bounded_max]]


def exact_reference_target_matches(
    *,
    reference: VerifiedExactReference,
    target_document_role: str,
    block: EvidenceBlock,
) -> bool:
    if target_document_role != reference.target_document_role:
        return False
    first_cell = re.split(r"\s*\|\s*", block.content, maxsplit=1)[0]
    if first_cell.strip() != reference.identifier:
        return False
    if not reference.qualifiers:
        return True
    normalized = block.content.upper()
    return any(item in normalized for item in reference.qualifiers)
