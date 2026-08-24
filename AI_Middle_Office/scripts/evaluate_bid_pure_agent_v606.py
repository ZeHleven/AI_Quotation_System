"""V606 synthetic AnswerDraft, Grounding, Citation, and rendering evaluation.

The evaluator uses only fictional versioned evidence. Runtime checks are fully
deterministic; the optional DeepSeek pass sends the same synthetic material and
never reads bid files, RAG, Tools, MCP, databases, or credentials into output.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    AnswerLimitationCode,
    EpistemicStatus,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    GroundingStatus,
    LimitationBlock,
    SourceBasis,
    StatementBlock,
)
from app.agents.bid_assessment_pure.answer_runtime import GroundingIntegrityGuard
from app.agents.bid_assessment_pure.citation_contracts import (
    CitationAuthorityRecord,
    CitationAuthoritySnapshot,
    CitationLocatorKind,
    CitationSourceType,
)
from app.agents.bid_assessment_pure.citation_runtime import (
    AnswerBlockRenderer,
    CitationProjector,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextIncludedEntry,
    ContextLane,
    ContextProjectionEntry,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
)
from app.agents.bid_assessment_pure.state import AgentTaskState
from app.agents.bid_assessment_pure.state_machine import create_running_task
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash


DATASET_PATH = (
    PROJECT_ROOT
    / "evals"
    / "bid_assessment"
    / "v606-answer-citation-synthetic-cases.json"
)
SCHEMA_VERSION = "bid.pure_agent.v606.answer_citation.v1"
RESULT_SCHEMA_VERSION = "bid.pure_agent.v606.result.v1"
PROMPT_VERSION = "bid-pure-agent-v606-answer-draft-v1"
DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
ALLOWED_HOSTS = frozenset({"api.deepseek.com"})
AUTHORIZATION_REF = "authorization:v606-synthetic"


class V606EvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    chat_url: str
    model: str
    timeout_seconds: int


@dataclass(frozen=True)
class SyntheticAnswerRuntime:
    task: AgentTaskState
    context: ContextAssemblyResult
    grounding_snapshot: GroundingSnapshot
    authority_snapshot: CitationAuthoritySnapshot


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise V606EvaluationError("unsupported V606 dataset schema")
    if payload.get("dataset_kind") != "synthetic_only":
        raise V606EvaluationError("V606 accepts synthetic-only datasets")
    contract = payload.get("execution_contract")
    if not isinstance(contract, dict) or not contract:
        raise V606EvaluationError("V606 execution contract is required")
    if any(bool(value) for value in contract.values()):
        raise V606EvaluationError("V606 real-data and external dependencies must be disabled")
    cases = payload.get("model_cases")
    if not isinstance(cases, list) or not cases:
        raise V606EvaluationError("V606 model cases are required")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(ids) != len(set(ids)):
        raise V606EvaluationError("V606 case ids must be present and unique")
    statuses = {case.get("expected", {}).get("epistemic_status") for case in cases}
    if statuses != {"supported", "unknown", "conflicted", "partial"}:
        raise V606EvaluationError("V606 epistemic coverage is incomplete")
    for case in cases:
        if not case.get("sources") or not case.get("reference_blocks"):
            raise V606EvaluationError("V606 cases require sources and reference blocks")
    return payload


def _source_type(source_basis: SourceBasis) -> CitationSourceType:
    values = {
        SourceBasis.DOCUMENT: CitationSourceType.DOCUMENT,
        SourceBasis.ENTERPRISE: CitationSourceType.ENTERPRISE_RECORD,
        SourceBasis.BUSINESS_RECORD: CitationSourceType.BUSINESS_RECORD,
        SourceBasis.SYSTEM_RULE: CitationSourceType.SYSTEM_RULE,
        SourceBasis.USER_ASSERTION: CitationSourceType.USER_MESSAGE,
    }
    try:
        return values[source_basis]
    except KeyError as exc:
        raise V606EvaluationError("synthetic citable source basis is unsupported") from exc


def build_synthetic_runtime(case: Mapping[str, Any]) -> SyntheticAnswerRuntime:
    task = create_running_task(
        task_id=f"task:v606-{str(case['id']).lower()}",
        session_id="conversation:v606-synthetic",
        goal_ref=f"goal:v606-{str(case['id']).lower()}",
    )
    projection_entries: list[ContextProjectionEntry] = []
    grounding_records: list[GroundingRecord] = []
    authority_records: list[CitationAuthorityRecord] = []
    allowed_scopes: list[str] = []
    for index, raw in enumerate(case["sources"], 1):
        content = str(raw["content"])
        source_basis = SourceBasis(str(raw["source_basis"]))
        grounding_kind = GroundingKind(str(raw["grounding_kind"]))
        source_content_hash = _sha256_text(content)
        projection_hash = canonical_hash(
            {"grounding_ref": raw["grounding_ref"], "content": content}
        )
        locator_hash = canonical_hash(
            {
                "source_ref": raw["source_ref"],
                "safe_locator_label": raw.get("safe_locator_label") or f"receipt-{index}",
            }
        )
        entry = ContextProjectionEntry(
            entry_ref=str(raw["grounding_ref"]),
            stable_key=f"v606:{case['id']}:{index}",
            source_ref=str(raw["source_ref"]),
            source_version_ref=str(raw["source_version_ref"]),
            lane=ContextLane.OBSERVATION_GROUNDING,
            kind=(
                ContextEntryKind.EVIDENCE_ATOM
                if grounding_kind is GroundingKind.EVIDENCE_ATOM
                else ContextEntryKind.LIMITATION
            ),
            representation=ContextRepresentation.EXACT,
            authority_label="synthetic_runtime_evidence",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            source_content_hash=source_content_hash,
            projection_hash=projection_hash,
            token_count=max(1, len(content) // 2),
            tool_name=None,
            protocol_pair_ref=None,
            content=content,
            untrusted_data=True,
        )
        projection_entries.append(entry)
        scope_ref = str(raw["source_scope_ref"])
        if scope_ref not in allowed_scopes:
            allowed_scopes.append(scope_ref)
        citable = bool(raw["citable"])
        record = GroundingRecord(
            grounding_ref=entry.entry_ref,
            context_entry_ref=entry.entry_ref,
            source_ref=entry.source_ref,
            source_basis=source_basis,
            grounding_kind=grounding_kind,
            source_scope_ref=scope_ref,
            authorization_snapshot_ref=AUTHORIZATION_REF,
            source_version_ref=entry.source_version_ref,
            source_head_version_ref=entry.source_version_ref,
            source_content_hash=source_content_hash,
            source_head_content_hash=source_content_hash,
            locator_hash=locator_hash,
            source_head_locator_hash=locator_hash,
            context_projection_hash=projection_hash,
            status=GroundingStatus(str(raw["status"])),
            citable=citable,
            citation_projection_ready=citable,
            conflict_group_ref=raw.get("conflict_group_ref"),
            quote_bindings=(),
        )
        grounding_records.append(record)
        if citable:
            authority_records.append(
                CitationAuthorityRecord(
                    authority_ref=f"citation-authority-record:{case['id']}:{index}",
                    grounding_ref=record.grounding_ref,
                    source_ref=record.source_ref,
                    source_scope_ref=record.source_scope_ref,
                    authorization_snapshot_ref=AUTHORIZATION_REF,
                    source_version_ref=record.source_version_ref,
                    source_head_version_ref=record.source_head_version_ref,
                    source_content_hash=record.source_content_hash,
                    source_head_content_hash=record.source_head_content_hash,
                    locator_hash=record.locator_hash,
                    source_head_locator_hash=record.source_head_locator_hash,
                    context_projection_hash=record.context_projection_hash,
                    source_type=_source_type(source_basis),
                    locator_kind=CitationLocatorKind.CLAUSE,
                    disclosure_allowed=True,
                    safe_title=str(raw["safe_title"]),
                    safe_locator_label=str(raw["safe_locator_label"]),
                    safe_version_label=raw.get("safe_version_label"),
                    controlled_access_ref=None,
                )
            )

    included_entries = tuple(
        ContextIncludedEntry.model_validate(
            entry.model_dump(mode="python", exclude={"content", "untrusted_data"})
        )
        for entry in projection_entries
    )
    case_key = str(case["id"]).lower()
    snapshot_ref = f"context-snapshot:v606-{case_key}"
    projection_hash = canonical_hash(
        [entry.model_dump(mode="json") for entry in projection_entries]
    )
    snapshot_hash = canonical_hash(
        {
            "case_id": case["id"],
            "task_ref": task.task_id,
            "projection_hash": projection_hash,
            "authorization_snapshot_ref": AUTHORIZATION_REF,
        }
    )
    snapshot = ContextSnapshot(
        snapshot_ref=snapshot_ref,
        snapshot_sequence=1,
        task_ref=task.task_id,
        state_version=task.state_version,
        consumer=ContextConsumer.MAIN_AGENT,
        status=ContextAssemblyStatus.READY,
        request_hash=canonical_hash({"case_id": case["id"], "kind": "request"}),
        policy_snapshot_ref="policy:v606-synthetic",
        prompt_template_ref=f"prompt:{PROMPT_VERSION}",
        model_profile_ref="model-profile:v606-synthetic",
        model_profile_hash=canonical_hash({"profile": "v606-synthetic"}),
        context_profile_ref="context-profile:v606-synthetic",
        context_profile_hash=canonical_hash({"context": "v606-synthetic"}),
        registry_snapshot_ref=None,
        registry_snapshot_hash=None,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        dependency_refs=tuple(dict.fromkeys(entry.source_ref for entry in projection_entries)),
        included_entries=included_entries,
        excluded_entries=(),
        compression_receipts=(),
        included_refs=tuple(entry.entry_ref for entry in included_entries),
        excluded_refs=(),
        limitation_messages=(),
        estimated_input_tokens=sum(entry.token_count for entry in projection_entries),
        effective_input_budget=32_000,
        reserved_output_tokens=4_096,
        safety_margin_tokens=512,
        projection_hash=projection_hash,
        snapshot_hash=snapshot_hash,
    )
    context = ContextAssemblyResult(
        snapshot=snapshot,
        projection_entries=tuple(projection_entries),
    )
    grounding_snapshot = GroundingSnapshot.build(
        task_ref=task.task_id,
        state_version=task.state_version,
        context_snapshot_ref=snapshot.snapshot_ref,
        context_snapshot_hash=snapshot.snapshot_hash,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=tuple(allowed_scopes),
        records=tuple(grounding_records),
    )
    authority_snapshot = CitationAuthoritySnapshot.build(
        task_ref=task.task_id,
        state_version=task.state_version,
        context_snapshot_ref=snapshot.snapshot_ref,
        context_snapshot_hash=snapshot.snapshot_hash,
        grounding_snapshot_ref=grounding_snapshot.snapshot_ref,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=tuple(allowed_scopes),
        records=tuple(authority_records),
    )
    return SyntheticAnswerRuntime(
        task=task,
        context=context,
        grounding_snapshot=grounding_snapshot,
        authority_snapshot=authority_snapshot,
    )


def draft_from_blocks(
    runtime: SyntheticAnswerRuntime,
    blocks: Sequence[Mapping[str, Any]],
) -> AnswerDraft:
    return AnswerDraft.model_validate(
        {
            "schema_name": "bid.answer.draft.v1",
            "response_language": "zh-CN",
            "blocks": list(blocks),
            "context_snapshot_ref": runtime.context.snapshot.snapshot_ref,
            "state_version": runtime.context.snapshot.state_version,
        }
    )


def _limitation_codes(draft: AnswerDraft) -> set[str]:
    return {
        block.code.value for block in draft.blocks if isinstance(block, LimitationBlock)
    }


def validate_and_render(
    case: Mapping[str, Any],
    draft: AnswerDraft,
    *,
    runtime: SyntheticAnswerRuntime | None = None,
) -> dict[str, Any]:
    boundary = runtime or build_synthetic_runtime(case)
    validation = GroundingIntegrityGuard().validate(
        task=boundary.task,
        context=boundary.context,
        draft=draft,
        grounding_snapshot=boundary.grounding_snapshot,
    )
    citation_decision = None
    rendered = None
    if validation.accepted:
        citation_decision = CitationProjector().project(
            task=boundary.task,
            context=boundary.context,
            draft=draft,
            validation=validation,
            grounding_snapshot=boundary.grounding_snapshot,
            authority_snapshot=boundary.authority_snapshot,
        )
        if citation_decision.accepted:
            rendered = AnswerBlockRenderer().render(
                task=boundary.task,
                draft=draft,
                validation=validation,
                citation_decision=citation_decision,
            )
    statements = [block for block in draft.blocks if isinstance(block, StatementBlock)]
    expected = case["expected"]
    required_refs = set(expected["required_grounding_refs"])
    used_refs = {
        grounding_ref for statement in statements for grounding_ref in statement.grounding_refs
    }
    expected_status = EpistemicStatus(str(expected["epistemic_status"]))
    expected_limitations = set(expected["required_limitation_codes"])
    citation_count = (
        0
        if citation_decision is None or citation_decision.bundle is None
        else len(citation_decision.bundle.citations)
    )
    statement_bindings = (
        ()
        if citation_decision is None or citation_decision.bundle is None
        else citation_decision.bundle.statement_bindings
    )
    support_by_ref = {item.statement_ref: item for item in validation.statement_support}
    binding_by_ref = {item.statement_ref: item for item in statement_bindings}
    citation_complete = all(
        (
            not support_by_ref[statement.block_id].citation_required
            or bool(binding_by_ref.get(statement.block_id).citation_refs)
        )
        for statement in statements
        if statement.block_id in support_by_ref
        and binding_by_ref.get(statement.block_id) is not None
    ) and len(binding_by_ref) == len(statements)
    conflict_ordinals = (
        set()
        if citation_decision is None or citation_decision.bundle is None
        else {
            citation.conflict_group_ordinal
            for citation in citation_decision.bundle.citations
            if citation.conflict_group_ordinal is not None
        }
    )
    checks = {
        "draft_grounded": validation.accepted,
        "citation_projection": citation_decision is not None and citation_decision.accepted,
        "rendered": rendered is not None,
        "material_claims_are_statements": bool(statements)
        and all(block.block_type != "narrative" for block in draft.blocks),
        "epistemic_status": bool(statements)
        and all(statement.epistemic_status is expected_status for statement in statements),
        "required_grounding": required_refs <= used_refs,
        "required_limitations": expected_limitations <= _limitation_codes(draft),
        "citation_count": citation_count == int(expected["citation_count"]),
        "citation_completeness": citation_complete,
        "unknown_zero_citation": expected_status is not EpistemicStatus.UNKNOWN
        or citation_count == 0,
        "conflict_dual_source": expected_status is not EpistemicStatus.CONFLICTED
        or len(conflict_ordinals) >= 2,
    }
    return {
        "case_id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "statement_count": len(statements),
        "epistemic_statuses": sorted(
            {statement.epistemic_status.value for statement in statements}
        ),
        "used_grounding_ref_count": len(used_refs),
        "limitation_codes": sorted(_limitation_codes(draft)),
        "citation_count": citation_count,
        "grounding_issue_codes": [issue.code.value for issue in validation.issues],
        "citation_issue_codes": (
            []
            if citation_decision is None
            else [issue.code.value for issue in citation_decision.issues]
        ),
        "rendered_hash": None if rendered is None else rendered.rendered_hash,
    }


def evaluate_reference_contracts(dataset: Mapping[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in dataset["model_cases"]:
        runtime = build_synthetic_runtime(case)
        draft = draft_from_blocks(runtime, case["reference_blocks"])
        cases.append(validate_and_render(case, draft, runtime=runtime))
    passed_count = sum(bool(case["passed"]) for case in cases)
    return {
        "passed": passed_count == len(cases),
        "passed_count": passed_count,
        "total": len(cases),
        "cases": cases,
    }


def evaluate_negative_guards(dataset: Mapping[str, Any]) -> dict[str, Any]:
    cases_by_id = {case["id"]: case for case in dataset["model_cases"]}
    results: list[dict[str, Any]] = []

    unknown_case = cases_by_id["A02_unknown_control_price"]
    unknown_runtime = build_synthetic_runtime(unknown_case)
    overclaim_blocks = json.loads(json.dumps(unknown_case["reference_blocks"]))
    overclaim_blocks[0]["epistemic_status"] = "supported"
    overclaim = draft_from_blocks(unknown_runtime, overclaim_blocks)
    overclaim_validation = GroundingIntegrityGuard().validate(
        task=unknown_runtime.task,
        context=unknown_runtime.context,
        draft=overclaim,
        grounding_snapshot=unknown_runtime.grounding_snapshot,
    )
    results.append(
        {
            "id": "N01_unknown_overclaim_rejected",
            "passed": not overclaim_validation.accepted
            and "support_matrix_unsatisfied"
            in {issue.code.value for issue in overclaim_validation.issues},
        }
    )

    supported_case = cases_by_id["A01_supported_deadline_and_guarantee"]
    supported_runtime = build_synthetic_runtime(supported_case)
    supported_draft = draft_from_blocks(
        supported_runtime, supported_case["reference_blocks"]
    )
    supported_validation = GroundingIntegrityGuard().validate(
        task=supported_runtime.task,
        context=supported_runtime.context,
        draft=supported_draft,
        grounding_snapshot=supported_runtime.grounding_snapshot,
    )
    empty_authority = CitationAuthoritySnapshot.build(
        task_ref=supported_runtime.task.task_id,
        state_version=supported_runtime.task.state_version,
        context_snapshot_ref=supported_runtime.context.snapshot.snapshot_ref,
        context_snapshot_hash=supported_runtime.context.snapshot.snapshot_hash,
        grounding_snapshot_ref=supported_runtime.grounding_snapshot.snapshot_ref,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=supported_runtime.grounding_snapshot.allowed_scope_refs,
        records=(),
    )
    missing_authority = CitationProjector().project(
        task=supported_runtime.task,
        context=supported_runtime.context,
        draft=supported_draft,
        validation=supported_validation,
        grounding_snapshot=supported_runtime.grounding_snapshot,
        authority_snapshot=empty_authority,
    )
    results.append(
        {
            "id": "N02_missing_authority_rejected",
            "passed": not missing_authority.accepted
            and "authority_record_missing"
            in {issue.code.value for issue in missing_authority.issues},
        }
    )

    authored_blocks = json.loads(json.dumps(supported_case["reference_blocks"]))
    authored_blocks[0]["text"] += " [1]"
    authored_draft = draft_from_blocks(supported_runtime, authored_blocks)
    authored_validation = GroundingIntegrityGuard().validate(
        task=supported_runtime.task,
        context=supported_runtime.context,
        draft=authored_draft,
        grounding_snapshot=supported_runtime.grounding_snapshot,
    )
    authored_projection = CitationProjector().project(
        task=supported_runtime.task,
        context=supported_runtime.context,
        draft=authored_draft,
        validation=authored_validation,
        grounding_snapshot=supported_runtime.grounding_snapshot,
        authority_snapshot=supported_runtime.authority_snapshot,
    )
    results.append(
        {
            "id": "N03_model_authored_citation_rejected",
            "passed": not authored_projection.accepted
            and "model_authored_citation"
            in {issue.code.value for issue in authored_projection.issues},
        }
    )

    first_record = supported_runtime.grounding_snapshot.records[0]
    stale_record = first_record.model_copy(
        update={"source_head_version_ref": "version:stale-head"}
    )
    stale_snapshot = GroundingSnapshot.build(
        task_ref=supported_runtime.task.task_id,
        state_version=supported_runtime.task.state_version,
        context_snapshot_ref=supported_runtime.context.snapshot.snapshot_ref,
        context_snapshot_hash=supported_runtime.context.snapshot.snapshot_hash,
        authorization_snapshot_ref=AUTHORIZATION_REF,
        allowed_scope_refs=supported_runtime.grounding_snapshot.allowed_scope_refs,
        records=(stale_record, *supported_runtime.grounding_snapshot.records[1:]),
    )
    stale_validation = GroundingIntegrityGuard().validate(
        task=supported_runtime.task,
        context=supported_runtime.context,
        draft=supported_draft,
        grounding_snapshot=stale_snapshot,
    )
    results.append(
        {
            "id": "N04_stale_source_rejected",
            "passed": not stale_validation.accepted
            and "grounding_source_not_current"
            in {issue.code.value for issue in stale_validation.issues},
        }
    )

    conflict_case = cases_by_id["A03_conflicted_deadline"]
    conflict_runtime = build_synthetic_runtime(conflict_case)
    one_side_blocks = json.loads(json.dumps(conflict_case["reference_blocks"]))
    one_side_ref = one_side_blocks[0]["grounding_refs"][0]
    one_side_blocks[0]["grounding_refs"] = [one_side_ref]
    one_side_blocks[1]["grounding_refs"] = [one_side_ref]
    one_side_draft = draft_from_blocks(conflict_runtime, one_side_blocks)
    one_side_validation = GroundingIntegrityGuard().validate(
        task=conflict_runtime.task,
        context=conflict_runtime.context,
        draft=one_side_draft,
        grounding_snapshot=conflict_runtime.grounding_snapshot,
    )
    results.append(
        {
            "id": "N05_one_sided_conflict_rejected",
            "passed": not one_side_validation.accepted
            and "conflict_groups_insufficient"
            in {issue.code.value for issue in one_side_validation.issues},
        }
    )
    passed_count = sum(bool(item["passed"]) for item in results)
    return {
        "passed": passed_count == len(results),
        "passed_count": passed_count,
        "total": len(results),
        "cases": results,
    }


def _secret_values(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    allowed_names = {
        "BID_ASSESSMENT_MODEL_API_KEY",
        "DEEPSEEK_API_KEY",
        "BID_ASSESSMENT_MODEL_CHAT_URL",
        "DEEPSEEK_CHAT_URL",
        "BID_ASSESSMENT_MODEL_ID",
        "DEEPSEEK_MODEL",
    }
    values: dict[str, str] = {}
    for raw_line in path.resolve().read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name in allowed_names:
            values[name] = raw_value.strip().strip('"').strip("'")
    return values


def model_config(
    *,
    timeout_seconds: int,
    secret_env_file: Path | None,
) -> ModelConfig | None:
    secrets = _secret_values(secret_env_file)

    def configured(name: str) -> str:
        return os.getenv(name, "").strip() or secrets.get(name, "").strip()

    api_key = configured("BID_ASSESSMENT_MODEL_API_KEY") or configured(
        "DEEPSEEK_API_KEY"
    )
    if not api_key:
        return None
    chat_url = (
        configured("BID_ASSESSMENT_MODEL_CHAT_URL")
        or configured("DEEPSEEK_CHAT_URL")
        or DEFAULT_CHAT_URL
    )
    endpoint = urlsplit(chat_url)
    if endpoint.scheme != "https" or endpoint.hostname not in ALLOWED_HOSTS:
        raise V606EvaluationError("V606 requires the official HTTPS DeepSeek endpoint")
    if endpoint.path not in {"/chat/completions", "/v1/chat/completions"}:
        raise V606EvaluationError("V606 DeepSeek endpoint path is not allowed")
    return ModelConfig(
        api_key=api_key,
        chat_url=chat_url,
        model=(
            configured("BID_ASSESSMENT_MODEL_ID")
            or configured("DEEPSEEK_MODEL")
            or DEFAULT_MODEL
        ),
        timeout_seconds=max(30, min(timeout_seconds, 300)),
    )


def _answer_prompt() -> str:
    return (
        "你是投标机会研判主 Agent 的回答能力。只根据输入的 synthetic_evidence 形成自由回答草稿，"
        "不得补充外部事实。每项业务事实必须放在 StatementBlock，当前基准不要使用 NarrativeBlock。"
        "supported 只能绑定 supported Grounding；partial 必须披露证据不足并使用 limitation；"
        "unknown 不得猜测，必须绑定 coverage/availability receipt 并使用兼容 limitation；"
        "conflicted 必须同时绑定至少两个不同 conflict_group_ref 的来源并使用 evidence_conflicted limitation。"
        "Statement 与 Limitation 的引用必须双向一致。不要使用 quote_refs。"
        "不得手写 [1]、页码、URL、来源标题、Citation 或任何内部路径；Runtime 会生成引用。"
        "context_snapshot_ref、state_version 必须逐字使用输入值，response_language 使用 zh-CN。"
        "只返回符合 AnswerDraft JSON Schema 的 JSON 对象，不输出 Markdown 或思维链。Schema="
        + json.dumps(AnswerDraft.model_json_schema(), ensure_ascii=False, sort_keys=True)
    )


def _model_payload(case: Mapping[str, Any], runtime: SyntheticAnswerRuntime) -> dict[str, Any]:
    return {
        "user_message": case["user_message"],
        "context_snapshot_ref": runtime.context.snapshot.snapshot_ref,
        "state_version": runtime.context.snapshot.state_version,
        "synthetic_evidence": [
            {
                "grounding_ref": source["grounding_ref"],
                "source_basis": source["source_basis"],
                "grounding_kind": source["grounding_kind"],
                "status": source["status"],
                "citable": source["citable"],
                "conflict_group_ref": source.get("conflict_group_ref"),
                "content": source["content"],
            }
            for source in case["sources"]
        ],
    }


def _json_object(raw: str) -> dict[str, Any]:
    content = str(raw or "").strip()
    if content.startswith("```") and content.endswith("```"):
        content = content[3:-3].strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise V606EvaluationError("model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise V606EvaluationError("model response was not a JSON object")
    return payload


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


async def _call_json(
    client: httpx.AsyncClient,
    *,
    config: ModelConfig,
    case: Mapping[str, Any],
    runtime: SyntheticAnswerRuntime,
) -> tuple[dict[str, Any], dict[str, int | None]]:
    response = await client.post(
        config.chat_url,
        headers={"Authorization": f"Bearer {config.api_key}"},
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": _answer_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        _model_payload(case, runtime),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 2_048,
            "stream": False,
        },
    )
    if response.status_code in {401, 403}:
        raise V606EvaluationError("model authentication was rejected")
    if response.status_code == 429:
        raise V606EvaluationError("model rate limit was reached")
    if response.status_code >= 400:
        raise V606EvaluationError(
            f"model request failed with HTTP {response.status_code}"
        )
    try:
        envelope = response.json()
        content = envelope["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise V606EvaluationError("model response envelope was invalid") from exc
    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return _json_object(content), {
        "input_tokens": _optional_non_negative_int(usage.get("prompt_tokens")),
        "output_tokens": _optional_non_negative_int(usage.get("completion_tokens")),
        "total_tokens": _optional_non_negative_int(usage.get("total_tokens")),
    }


def _accumulate_usage(aggregate: dict[str, int], usage: Mapping[str, int | None]) -> None:
    for key in aggregate:
        if usage[key] is not None:
            aggregate[key] += int(usage[key])


async def evaluate_model_cases(
    dataset: Mapping[str, Any],
    config: ModelConfig,
    *,
    case_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    call_count = 0
    selected = [
        case
        for case in dataset["model_cases"]
        if not case_ids or case["id"] in case_ids
    ]
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        for case in selected:
            runtime = build_synthetic_runtime(case)
            try:
                payload, call_usage = await _call_json(
                    client,
                    config=config,
                    case=case,
                    runtime=runtime,
                )
                call_count += 1
                draft = AnswerDraft.model_validate(payload)
                result = validate_and_render(case, draft, runtime=runtime)
                _accumulate_usage(usage, call_usage)
            except (V606EvaluationError, httpx.HTTPError):
                raise
            except Exception as exc:
                result = {
                    "case_id": case["id"],
                    "passed": False,
                    "error_type": type(exc).__name__,
                }
            results.append(result)
    passed_count = sum(bool(result["passed"]) for result in results)
    return {
        "passed": passed_count == len(results),
        "passed_count": passed_count,
        "total": len(results),
        "call_count": call_count,
        "usage": usage,
        "cases": results,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run synthetic V606 answer grounding and citation evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--secret-env-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("all", "contracts", "model"), default="all")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--preflight", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        dataset = load_dataset(args.dataset)
        requested_case_ids = frozenset(args.case_id)
        known_case_ids = {case["id"] for case in dataset["model_cases"]}
        unknown_case_ids = sorted(requested_case_ids - known_case_ids)
        if unknown_case_ids:
            raise V606EvaluationError(
                f"unknown V606 case ids: {', '.join(unknown_case_ids)}"
            )
        selected_count = sum(
            1
            for case in dataset["model_cases"]
            if not requested_case_ids or case["id"] in requested_case_ids
        )
        if selected_count == 0:
            raise V606EvaluationError("V606 selection contains no runnable cases")
        config = model_config(
            timeout_seconds=args.timeout_seconds,
            secret_env_file=args.secret_env_file,
        )
        if args.preflight:
            print(
                json.dumps(
                    {
                        "status": "ready" if config else "model_configuration_missing",
                        "schema_version": dataset["schema_version"],
                        "dataset_kind": dataset["dataset_kind"],
                        "planned_model_calls": (
                            selected_count if args.mode in {"all", "model"} else 0
                        ),
                        "contract_case_count": len(dataset["model_cases"]),
                        "negative_guard_count": 5,
                        "business_data_included": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0 if args.mode == "contracts" or config else 3

        reference = (
            evaluate_reference_contracts(dataset)
            if args.mode in {"all", "contracts"}
            else None
        )
        negatives = (
            evaluate_negative_guards(dataset)
            if args.mode in {"all", "contracts"}
            else None
        )
        model_result = None
        if args.mode in {"all", "model"}:
            if config is None:
                raise V606EvaluationError(
                    "set BID_ASSESSMENT_MODEL_API_KEY or DEEPSEEK_API_KEY before V606"
                )
            model_result = asyncio.run(
                evaluate_model_cases(dataset, config, case_ids=requested_case_ids)
            )
        passed = all(
            result is None or bool(result["passed"])
            for result in (reference, negatives, model_result)
        )
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": "passed" if passed else "failed",
            "prompt_version": PROMPT_VERSION,
            "provider_ref": None if model_result is None else "deepseek",
            "model_ref": None if config is None or model_result is None else config.model,
            "dataset_kind": dataset["dataset_kind"],
            "business_data_included": False,
            "real_pdf_executed": False,
            "rag_executed": False,
            "tool_executed": False,
            "external_mcp_executed": False,
            "database_executed": False,
            "reference_runtime": reference,
            "negative_guards": negatives,
            "model": model_result,
        }
        serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            output_path = args.output.resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0 if passed else 2
    except httpx.HTTPError:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": "ModelTransportUnavailable",
                    "message": "model transport is unavailable",
                    "business_data_included": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3
    except (OSError, ValueError, V606EvaluationError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "business_data_included": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_PATH",
    "PROMPT_VERSION",
    "RESULT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SyntheticAnswerRuntime",
    "V606EvaluationError",
    "build_synthetic_runtime",
    "draft_from_blocks",
    "evaluate_model_cases",
    "evaluate_negative_guards",
    "evaluate_reference_contracts",
    "load_dataset",
    "model_config",
    "validate_and_render",
]
