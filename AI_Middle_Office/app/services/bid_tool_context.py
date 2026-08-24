"""Phase 3E deterministic Context Assembler and Tool Gateway control plane.

The service authorizes and persists governed tool work but deliberately does
not call a model, parser, search provider, calculation adapter, object store,
or any external service.  Callers own the surrounding transaction.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessment, BidManifestDocument
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidEvidenceFragment,
)
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidAsyncOperation,
    BidCheckpoint,
    BidTask,
    BidTaskAttempt,
    BidTaskDependency,
)
from app.models.bid_assessment_tooling import (
    TOOL_RESULT_STATES,
    BidContextManifest,
    BidToolInvocation,
    BidToolResult,
)
from app.models.bid_tool_execution import BidToolDispatch, BidToolDispatchAttempt
from app.services.bid_assessment_eventing import (
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    canonical_json,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_runtime import (
    TaskLeaseClaim,
    build_task_contract,
    lock_task_claim,
)


TOOL_CONTEXT_PRODUCER = "bid-tool-context-v1"
CONTEXT_ASSEMBLER_VERSION = "bid-context-assembler-v1"
MAX_TOOL_ARGUMENT_BYTES = 24 * 1024
MAX_INLINE_RESULT_BYTES = 24 * 1024
MAX_RESULT_SUMMARY_CHARS = 600
RESULT_RETENTION_DAYS = 30
SCOPE_TOKEN_PREFIX = "ts_"
TOOLS_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "bid_assessment"
    / "v1"
    / "tools.schema.json"
)
MODEL_ROLE_BY_TASK = {
    "synthesize_assessment": "synthesizer",
    "validate_claim_evidence": "evidence_validator",
    "validate_report_consistency": "evidence_validator",
    "generate_preliminary_report": "report_writer",
    "generate_deep_report": "report_writer",
    "generate_version_delta": "report_writer",
}
SUCCESS_RESULT_STATES = frozenset({"ok", "no_result", "partial"})
ERROR_RESULT_STATES = frozenset(TOOL_RESULT_STATES) - SUCCESS_RESULT_STATES
ASYNC_ACTIVE_STATES = frozenset({"created", "submitted", "running"})

logger = logging.getLogger(__name__)


class BidToolContextError(RuntimeError):
    code = "BID_TOOL_CONTEXT_ERROR"


class BidContextAssemblyError(BidToolContextError):
    code = "BID_CONTEXT_ASSEMBLY_INVALID"


class BidToolUnauthorized(BidToolContextError):
    code = "BID_TOOL_UNAUTHORIZED"


class BidToolArgumentsInvalid(BidToolContextError):
    code = "BID_TOOL_ARGUMENTS_INVALID"


class BidToolBudgetExhausted(BidToolContextError):
    code = "BID_TOOL_BUDGET_EXHAUSTED"


class BidToolInvocationConflict(BidToolContextError):
    code = "BID_TOOL_INVOCATION_CONFLICT"


class BidToolResultInvalid(BidToolContextError):
    code = "BID_TOOL_RESULT_INVALID"


@dataclass(frozen=True)
class ContextAssemblyReceipt:
    context_manifest_id: str
    manifest_seq: int
    manifest_hash: str
    manifest: dict[str, Any]
    duplicate: bool


@dataclass(frozen=True)
class ToolInvocationDecision:
    invocation_id: str
    tool_call_id: str
    invocation_seq: int
    status: str
    call_envelope: dict[str, Any] | None
    result_envelope: dict[str, Any] | None
    duplicate: bool


@dataclass(frozen=True)
class ToolResultReceipt:
    invocation_id: str
    result_id: str
    result_hash: str
    envelope: dict[str, Any]
    duplicate: bool


@dataclass(frozen=True)
class ToolOperationMaintenanceResult:
    scanned: int
    timed_out: int
    recovered: int
    failed: int


def _utc_text(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_value(value: Any, *, field: str, max_bytes: int) -> Any:
    try:
        text = canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise BidToolArgumentsInvalid(f"BID_TOOL_{field.upper()}_INVALID") from exc
    if len(text.encode("utf-8")) > max_bytes:
        raise BidToolArgumentsInvalid(f"BID_TOOL_{field.upper()}_TOO_LARGE")
    return json.loads(text)


def _scope_token(*, signing_key: str, invocation_id: str, request_hash: str) -> str:
    secret = str(signing_key or "").encode("utf-8")
    if len(secret) < 32:
        raise BidToolContextError("BID_TOOL_SCOPE_SIGNING_KEY_INVALID")
    digest = hmac.new(
        secret,
        f"bid-tool-scope-v1:{invocation_id}:{request_hash}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SCOPE_TOKEN_PREFIX}{digest}"


def _scope_token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _tools_schema() -> dict[str, Any]:
    return json.loads(TOOLS_SCHEMA_PATH.read_text(encoding="utf-8"))


def _inline_local_schema_refs(
    root: dict[str, Any],
    value: Any,
    *,
    stack: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, list):
        return [
            _inline_local_schema_refs(root, item, stack=stack) for item in value
        ]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if ref is not None:
        normalized_ref = str(ref)
        if not normalized_ref.startswith("#/") or normalized_ref in stack:
            raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_REF_INVALID")
        target: Any = root
        for raw_part in normalized_ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_REF_INVALID")
            target = target[part]
        resolved = _inline_local_schema_refs(
            root,
            target,
            stack=(*stack, normalized_ref),
        )
        if not isinstance(resolved, dict):
            raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_REF_INVALID")
        siblings = {
            key: _inline_local_schema_refs(root, item, stack=stack)
            for key, item in value.items()
            if key != "$ref"
        }
        return {**resolved, **siblings}
    return {
        key: _inline_local_schema_refs(root, item, stack=stack)
        for key, item in value.items()
    }


def model_visible_tool_argument_contracts(
    tool_names: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Project the authoritative schemas for only the model-visible tools."""

    root = _tools_schema()
    registry = dict(root.get("x-tool-registry") or {})
    contracts: dict[str, dict[str, Any]] = {}
    for tool_name in sorted(set(str(value) for value in tool_names)):
        ref = registry.get(tool_name)
        if not ref:
            raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_NOT_REGISTERED")
        resolved = _inline_local_schema_refs(root, {"$ref": str(ref)})
        if not isinstance(resolved, dict):
            raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_REF_INVALID")
        contracts[tool_name] = resolved
    return contracts


def _resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_EXTERNAL_REF_FORBIDDEN")
    current: Any = root
    for token in ref[2:].split("/"):
        current = current[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(current, dict):
        raise BidToolArgumentsInvalid("BID_TOOL_SCHEMA_REF_INVALID")
    return current


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _condition_matches(root: dict[str, Any], schema: dict[str, Any], value: Any) -> bool:
    try:
        _validate_schema_value(root, schema, value, path="$", conditional=True)
        return True
    except BidToolArgumentsInvalid:
        return False


def _validate_schema_value(
    root: dict[str, Any],
    schema: dict[str, Any],
    value: Any,
    *,
    path: str,
    conditional: bool = False,
) -> None:
    if "$ref" in schema:
        _validate_schema_value(
            root,
            _resolve_local_ref(root, str(schema["$ref"])),
            value,
            path=path,
            conditional=conditional,
        )
        return
    if "const" in schema and value != schema["const"]:
        raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_CONST:{path}")
    if "enum" in schema and value not in schema["enum"]:
        raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_ENUM:{path}")
    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_json_type_matches(value, str(item)) for item in expected_types):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_TYPE:{path}")
    if isinstance(value, dict):
        required = set(str(item) for item in schema.get("required") or [])
        missing = sorted(required - set(value))
        if missing:
            raise BidToolArgumentsInvalid(
                f"BID_TOOL_ARGUMENT_REQUIRED:{path}:{','.join(missing)}"
            )
        properties = dict(schema.get("properties") or {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise BidToolArgumentsInvalid(
                    f"BID_TOOL_ARGUMENT_EXTRA:{path}:{','.join(extras)}"
                )
        for key, child in properties.items():
            if key in value:
                _validate_schema_value(
                    root,
                    child,
                    value[key],
                    path=f"{path}.{key}",
                    conditional=conditional,
                )
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MIN_ITEMS:{path}")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MAX_ITEMS:{path}")
        if schema.get("uniqueItems") and len({canonical_json(item) for item in value}) != len(value):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_NOT_UNIQUE:{path}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_value(
                    root,
                    item_schema,
                    item,
                    path=f"{path}[{index}]",
                    conditional=conditional,
                )
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MIN_LENGTH:{path}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MAX_LENGTH:{path}")
        if "pattern" in schema and re.fullmatch(str(schema["pattern"]), value) is None:
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_PATTERN:{path}")
        if schema.get("format") == "date":
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_DATE:{path}") from exc
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if "T" not in value or parsed.tzinfo is None:
                    raise ValueError("RFC3339 date-time requires time and timezone")
            except ValueError as exc:
                raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_DATETIME:{path}") from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MINIMUM:{path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise BidToolArgumentsInvalid(f"BID_TOOL_ARGUMENT_MAXIMUM:{path}")
    for branch in schema.get("allOf") or []:
        if "if" not in branch or _condition_matches(root, branch["if"], value):
            target = branch.get("then") if "if" in branch else branch
            if isinstance(target, dict):
                _validate_schema_value(root, target, value, path=path, conditional=conditional)


def validate_tool_arguments(tool_name: str, arguments: Any) -> dict[str, Any]:
    normalized = _json_value(
        {"tool_name": str(tool_name), "arguments": arguments},
        field="arguments",
        max_bytes=MAX_TOOL_ARGUMENT_BYTES,
    )
    schema = _tools_schema()
    _validate_schema_value(schema, schema, normalized, path="$")
    return dict(normalized["arguments"])


def _scoped_evidence(
    db: Session,
    *,
    run: BidAnalysisRun,
    evidence_ids: Iterable[str],
) -> dict[str, BidEvidenceFragment]:
    normalized = tuple(sorted(set(str(value) for value in evidence_ids)))
    if not normalized:
        return {}
    rows = (
        db.query(BidEvidenceFragment)
        .join(
            BidManifestDocument,
            BidManifestDocument.document_version_id
            == BidEvidenceFragment.document_version_id,
        )
        .join(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id
            == BidEvidenceFragment.document_version_id,
        )
        .filter(
            BidEvidenceFragment.id.in_(normalized),
            BidManifestDocument.manifest_id == run.manifest_id,
            BidDocumentParseHead.current_run_id == BidEvidenceFragment.parse_run_id,
        )
        .all()
    )
    by_id = {str(row.id): row for row in rows}
    if set(by_id) != set(normalized):
        raise BidContextAssemblyError("BID_CONTEXT_EVIDENCE_OUT_OF_SCOPE")
    return by_id


def _model_role(task: BidTask) -> str:
    return MODEL_ROLE_BY_TASK.get(str(task.task_type), "local_research")


def _collect_reference_values(value: Any, keys: set[str]) -> set[str]:
    collected: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                if isinstance(child, list):
                    collected.update(str(item) for item in child)
                elif child is not None:
                    collected.add(str(child))
            collected.update(_collect_reference_values(child, keys))
    elif isinstance(value, list):
        for child in value:
            collected.update(_collect_reference_values(child, keys))
    return collected


def _validate_tool_reference_scope(
    db: Session,
    *,
    run: BidAnalysisRun,
    task: BidTask,
    context: BidContextManifest,
    arguments: dict[str, Any],
) -> None:
    document_ids = _collect_reference_values(
        arguments,
        {"document_version_id", "document_version_ids", "old_version_id", "new_version_id"},
    )
    if document_ids:
        count = int(
            db.query(func.count(BidManifestDocument.document_version_id))
            .filter(
                BidManifestDocument.manifest_id == run.manifest_id,
                BidManifestDocument.document_version_id.in_(tuple(document_ids)),
            )
            .scalar()
            or 0
        )
        if count != len(document_ids):
            raise BidToolUnauthorized("BID_TOOL_DOCUMENT_REFERENCE_OUT_OF_SCOPE")

    evidence_ids = _collect_reference_values(arguments, {"evidence_id", "evidence_ids"})
    _scoped_evidence(db, run=run, evidence_ids=evidence_ids)

    manifest = dict(context.manifest_json or {})
    tool_result_ids = set(str(value) for value in manifest.get("included_tool_result_ids") or [])
    result_ref_ids = _collect_reference_values(arguments, {"result_ref_id"})
    if not result_ref_ids <= tool_result_ids:
        raise BidToolUnauthorized("BID_TOOL_RESULT_REFERENCE_OUT_OF_CONTEXT")
    if result_ref_ids:
        count = int(
            db.query(func.count(BidToolResult.id))
            .join(BidToolInvocation, BidToolInvocation.id == BidToolResult.invocation_id)
            .filter(
                BidToolResult.id.in_(tuple(result_ref_ids)),
                BidToolInvocation.run_id == run.id,
                BidToolInvocation.task_id == task.id,
            )
            .scalar()
            or 0
        )
        if count != len(result_ref_ids):
            raise BidToolUnauthorized("BID_TOOL_RESULT_REFERENCE_OUT_OF_SCOPE")

    calculation_ids = _collect_reference_values(
        arguments,
        {
            "workload_calculation_id",
            "labor_calculation_id",
            "external_calculation_id",
            "fund_calculation_ids",
            "base_calculation_id",
        },
    )
    governed_calculations = set(
        str(value) for value in manifest.get("included_calculation_ids") or []
    ) | tool_result_ids
    if not calculation_ids <= governed_calculations:
        raise BidToolUnauthorized("BID_TOOL_CALCULATION_REFERENCE_OUT_OF_CONTEXT")

    source_ids = _collect_reference_values(
        arguments,
        {"fact_ids", "evidence_fact_ids", "source_ids"},
    )
    governed_sources = (
        set(str(value) for value in manifest.get("included_fact_ids") or [])
        | governed_calculations
    )
    if not source_ids <= governed_sources:
        raise BidToolUnauthorized("BID_TOOL_SOURCE_REFERENCE_OUT_OF_CONTEXT")


def assemble_context_manifest(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    evidence: list[dict[str, Any]] | None = None,
    dependency_output_ids: list[str] | None = None,
    included_tool_result_ids: list[str] | None = None,
    included_model_result_ids: list[str] | None = None,
    working_state: dict[str, Any] | None = None,
    base_token_estimate: int = 512,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ContextAssemblyReceipt:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    task_contract = build_task_contract(db, task)
    task_contract_hash = canonical_hash(task_contract)
    budget = dict(task_contract["budget"])
    hard_limit = min(int(budget["max_input_tokens"]), 32000)
    if int(base_token_estimate) < 0 or int(base_token_estimate) > hard_limit:
        raise BidContextAssemblyError("BID_CONTEXT_BASE_TOKEN_ESTIMATE_INVALID")
    if dependency_output_ids:
        # Phase 3E has no fact/report authority yet; only checkpoint output refs
        # from direct dependencies may enter this boundary.
        governed_outputs = {
            str(row[0])
            for row in db.query(BidCheckpoint.candidate_output_ref)
            .join(BidTaskAttempt, BidTaskAttempt.id == BidCheckpoint.task_attempt_id)
            .join(BidTask, BidTask.id == BidTaskAttempt.task_id)
            .join(
                BidTaskDependency,
                and_(
                    BidTaskDependency.depends_on_task_id == BidTask.id,
                    BidTaskDependency.task_id == task.id,
                    BidTaskDependency.run_id == run.id,
                ),
            )
            .filter(
                BidTask.run_id == run.id,
                BidCheckpoint.candidate_output_ref.is_not(None),
            )
            .all()
        }
        if not set(str(value) for value in dependency_output_ids) <= governed_outputs:
            raise BidContextAssemblyError("BID_CONTEXT_DEPENDENCY_OUTPUT_OUT_OF_SCOPE")
    result_ids = tuple(sorted(set(str(value) for value in included_tool_result_ids or [])))
    if result_ids:
        count = int(
            db.query(func.count(BidToolResult.id))
            .join(
                BidToolInvocation,
                BidToolInvocation.id == BidToolResult.invocation_id,
            )
            .filter(
                BidToolResult.id.in_(result_ids),
                BidToolInvocation.run_id == run.id,
                BidToolInvocation.task_id == task.id,
            )
            .scalar()
            or 0
        )
        if count != len(result_ids):
            raise BidContextAssemblyError("BID_CONTEXT_TOOL_RESULT_OUT_OF_SCOPE")
    model_result_ids = tuple(
        sorted(set(str(value) for value in included_model_result_ids or []))
    )
    if model_result_ids:
        # Lazy import avoids introducing a Phase 4 dependency into the default
        # disabled Phase 3E module at import time.
        from app.models.bid_model_execution import BidModelCall, BidModelResult

        count = int(
            db.query(func.count(BidModelResult.id))
            .join(BidModelCall, BidModelCall.id == BidModelResult.model_call_id)
            .filter(
                BidModelResult.id.in_(model_result_ids),
                BidModelCall.run_id == run.id,
                BidModelCall.task_id == task.id,
            )
            .scalar()
            or 0
        )
        if count != len(model_result_ids):
            raise BidContextAssemblyError("BID_CONTEXT_MODEL_RESULT_OUT_OF_SCOPE")
    candidates = []
    for item in evidence or []:
        evidence_id = str(item.get("id") or "")
        priority = str(item.get("priority") or "")
        chars = int(item.get("chars") or 0)
        if not evidence_id or priority not in {"P0", "P1", "P2", "P3", "P4"} or chars < 0:
            raise BidContextAssemblyError("BID_CONTEXT_EVIDENCE_DESCRIPTOR_INVALID")
        candidates.append({"id": evidence_id, "priority": priority, "chars": chars})
    evidence_rows = _scoped_evidence(
        db,
        run=run,
        evidence_ids=[item["id"] for item in candidates],
    )
    for item in candidates:
        item["chars"] = min(item["chars"], len(evidence_rows[item["id"]].normalized_text))
        item["tokens"] = (item["chars"] + 3) // 4
    candidates.sort(key=lambda item: (item["priority"], item["id"]))
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    token_estimate = int(base_token_estimate)
    for item in candidates:
        if token_estimate + item["tokens"] <= hard_limit:
            included.append({key: item[key] for key in ("id", "chars", "priority")})
            token_estimate += int(item["tokens"])
        elif item["priority"] in {"P0", "P1"}:
            raise BidContextAssemblyError("BID_CONTEXT_MANDATORY_EVIDENCE_EXCEEDS_BUDGET")
        else:
            excluded.append(
                {
                    "resource_type": "evidence",
                    "resource_id": item["id"],
                    "priority": item["priority"],
                    "reason": "token_budget",
                }
            )
    compression_level = 0
    if excluded:
        compression_level = 1
    if token_estimate > int(hard_limit * 0.9):
        compression_level = max(compression_level, 2)
    working_state_hash = canonical_hash(working_state) if working_state is not None else None
    governed_fact_ids: list[str] = []
    governed_gate_ids: list[str] = []
    from app.core.config import settings

    if settings.feature_bid_assessment_phase4_fact_authority:
        from app.models.bid_assessment_results import (
            BidHardGateResult,
            BidResolvedFactHead,
        )

        governed_fact_ids = sorted(
            str(value)
            for (value,) in db.query(BidResolvedFactHead.resolved_fact_id)
            .filter(BidResolvedFactHead.run_id == run.id)
            .all()
        )
        governed_gate_ids = sorted(
            str(value)
            for (value,) in db.query(BidHardGateResult.id)
            .filter(BidHardGateResult.run_id == run.id)
            .all()
        )
    semantic_payload = {
        "task_id": str(task.id),
        "task_attempt_id": str(attempt.id),
        "fencing_token": int(claim.fencing_token),
        "role": _model_role(task),
        "bound_versions": dict(task_contract["bound_versions"]),
        "included_fact_ids": governed_fact_ids,
        "included_calculation_ids": [],
        "included_evidence": included,
        "dependency_output_ids": sorted(set(str(value) for value in dependency_output_ids or [])),
        "excluded_due_to_budget": excluded,
        "token_estimate": token_estimate,
        "compression_level": compression_level,
        "assembler_version": CONTEXT_ASSEMBLER_VERSION,
        "task_contract_hash": task_contract_hash,
        "context_profile": str(task.context_profile),
        "budget_profile": str(task.budget_profile),
        "included_tool_result_ids": list(result_ids),
        "included_model_result_ids": list(model_result_ids),
        "working_state_hash": working_state_hash,
        "component_token_estimates": {
            "base": int(base_token_estimate),
            "evidence": token_estimate - int(base_token_estimate),
            "total": token_estimate,
        },
    }
    # Preserve pre-MVP-1 Context Manifest hashes.  Gate lineage becomes part
    # of the semantic payload only for the isolated MVP-1 fact authority.
    if settings.feature_bid_assessment_phase4_fact_authority:
        semantic_payload["included_gate_ids"] = governed_gate_ids
    manifest_hash = canonical_hash(semantic_payload)
    existing = (
        db.query(BidContextManifest)
        .filter(
            BidContextManifest.task_attempt_id == attempt.id,
            BidContextManifest.manifest_hash == manifest_hash,
        )
        .one_or_none()
    )
    if existing is not None:
        return ContextAssemblyReceipt(
            context_manifest_id=str(existing.id),
            manifest_seq=int(existing.manifest_seq),
            manifest_hash=str(existing.manifest_hash),
            manifest=dict(existing.manifest_json or {}),
            duplicate=True,
        )
    manifest_seq = int(
        db.query(func.max(BidContextManifest.manifest_seq))
        .filter(BidContextManifest.task_attempt_id == attempt.id)
        .scalar()
        or 0
    ) + 1
    context_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bid-context-manifest-v1:{attempt.id}:{manifest_hash}",
        )
    )
    payload = {
        "context_manifest_id": context_id,
        **semantic_payload,
        "hash": manifest_hash,
    }
    context = BidContextManifest(
        id=context_id,
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        task_id=str(task.id),
        task_attempt_id=str(attempt.id),
        manifest_seq=manifest_seq,
        fencing_token=int(claim.fencing_token),
        role=payload["role"],
        context_profile=str(task.context_profile),
        assembler_version=CONTEXT_ASSEMBLER_VERSION,
        token_estimate=token_estimate,
        compression_level=compression_level,
        bound_versions_json=payload["bound_versions"],
        manifest_json=payload,
        manifest_hash=manifest_hash,
        created_at=current_time,
    )
    db.add(context)
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="context.manifest.assemble",
        entity_type="context_manifest",
        entity_id=context_id,
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"context:{context_id}"),
        after={
            "run_id": str(run.id),
            "task_id": str(task.id),
            "attempt_id": str(attempt.id),
            "manifest_seq": manifest_seq,
            "manifest_hash": manifest_hash,
            "token_estimate": token_estimate,
            "compression_level": compression_level,
        },
        occurred_at=current_time,
    )
    db.flush()
    return ContextAssemblyReceipt(
        context_manifest_id=context_id,
        manifest_seq=manifest_seq,
        manifest_hash=manifest_hash,
        manifest=payload,
        duplicate=False,
    )


def _result_envelope(row: BidToolResult) -> dict[str, Any]:
    return {
        "status": str(row.status),
        "summary": str(row.summary),
        "data": row.inline_data_json if row.storage_kind == "inline" else {},
        "result_ref": {
            "type": "tool_result",
            "id": str(row.id),
            "expires_at": _utc_text(row.expires_at) if row.expires_at else None,
        },
        "evidence_refs": list(row.evidence_refs_json or []),
        "operation_id": str(row.async_operation_id) if row.async_operation_id else None,
        "truncated": bool(row.truncated),
        "warnings": list(row.warnings_json or []),
        "metrics": dict(row.metrics_json or {}),
    }


def authorize_tool_invocation(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    context_manifest_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    idempotency_key: str,
    scope_signing_key: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ToolInvocationDecision:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    normalized_key = str(idempotency_key or "")
    if not (16 <= len(normalized_key) <= 128):
        raise BidToolInvocationConflict("BID_TOOL_IDEMPOTENCY_KEY_INVALID")
    context = (
        db.query(BidContextManifest)
        .filter(
            BidContextManifest.id == str(context_manifest_id),
            BidContextManifest.task_attempt_id == attempt.id,
            BidContextManifest.task_id == task.id,
            BidContextManifest.run_id == run.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if context is None or int(context.fencing_token) != int(claim.fencing_token):
        raise BidToolUnauthorized("BID_TOOL_CONTEXT_MANIFEST_INVALID")
    normalized_arguments = validate_tool_arguments(str(tool_name), arguments)
    _validate_tool_reference_scope(
        db,
        run=run,
        task=task,
        context=context,
        arguments=normalized_arguments,
    )
    request_hash = canonical_hash(
        {
            "attempt_id": str(attempt.id),
            "fencing_token": int(claim.fencing_token),
            "context_manifest_id": str(context.id),
            "tool_name": str(tool_name),
            "arguments": normalized_arguments,
        }
    )
    existing = (
        db.query(BidToolInvocation)
        .filter(
            BidToolInvocation.task_attempt_id == attempt.id,
            BidToolInvocation.idempotency_key == normalized_key,
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        if str(existing.request_hash) != request_hash:
            raise BidToolInvocationConflict("BID_TOOL_IDEMPOTENCY_KEY_REUSED")
        result = (
            db.query(BidToolResult)
            .filter(BidToolResult.invocation_id == existing.id)
            .one_or_none()
        )
        token = _scope_token(
            signing_key=scope_signing_key,
            invocation_id=str(existing.id),
            request_hash=request_hash,
        )
        call_envelope = None
        if str(existing.status) == "accepted":
            call_envelope = {
                "tool_call_id": str(existing.tool_call_id),
                "tool_name": str(existing.tool_name),
                "arguments": dict(existing.arguments_json or {}),
                "task_id": str(existing.task_id),
                "scope_token": token,
                "idempotency_key": str(existing.idempotency_key),
            }
        return ToolInvocationDecision(
            invocation_id=str(existing.id),
            tool_call_id=str(existing.tool_call_id),
            invocation_seq=int(existing.invocation_seq),
            status=str(existing.status),
            call_envelope=call_envelope,
            result_envelope=_result_envelope(result) if result else None,
            duplicate=True,
        )
    task_contract = build_task_contract(db, task)
    # build_task_contract resolves the Task's own retained Phase 4 catalog and
    # SkillBinding.  Never route a historical Task through the current singleton
    # registry, which could drift after its Plan was committed.
    if str(tool_name) not in set(task_contract["allowed_tools"]):
        raise BidToolUnauthorized("BID_TOOL_NOT_ALLOWED_FOR_PROFILE")
    used_calls = int(
        db.query(func.count(BidToolInvocation.id))
        .filter(
            BidToolInvocation.task_id == task.id,
            BidToolInvocation.status.in_(("accepted", "pending", "succeeded", "failed")),
        )
        .scalar()
        or 0
    )
    max_calls = int(task_contract["budget"]["max_tool_calls"])
    if used_calls >= max_calls:
        raise BidToolBudgetExhausted(BidToolBudgetExhausted.code)
    invocation_seq = int(
        db.query(func.max(BidToolInvocation.invocation_seq))
        .filter(BidToolInvocation.task_attempt_id == attempt.id)
        .scalar()
        or 0
    ) + 1
    invocation_id = str(uuid.uuid4())
    tool_call_id = f"tc_{uuid.uuid4().hex}"
    token = _scope_token(
        signing_key=scope_signing_key,
        invocation_id=invocation_id,
        request_hash=request_hash,
    )
    budget_before = {"tool_calls_used": used_calls, "tool_calls_limit": max_calls}
    budget_after = {"tool_calls_used": used_calls + 1, "tool_calls_limit": max_calls}
    invocation = BidToolInvocation(
        id=invocation_id,
        tool_call_id=tool_call_id,
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        task_id=str(task.id),
        task_attempt_id=str(attempt.id),
        context_manifest_id=str(context.id),
        tool_registry_version_id=str(run.tool_registry_version_id),
        invocation_seq=invocation_seq,
        fencing_token=int(claim.fencing_token),
        tool_name=str(tool_name),
        tool_profile=str(task.tool_profile),
        idempotency_key=normalized_key,
        arguments_json=normalized_arguments,
        arguments_hash=canonical_hash(normalized_arguments),
        request_hash=request_hash,
        scope_token_hash=_scope_token_hash(token),
        status="accepted",
        budget_before_json=budget_before,
        budget_after_json=budget_after,
        accepted_at=current_time,
        row_version=1,
    )
    db.add(invocation)
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="tool.invocation.authorize",
        entity_type="tool_invocation",
        entity_id=invocation_id,
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"tool:{invocation_id}"),
        after={
            "run_id": str(run.id),
            "task_id": str(task.id),
            "attempt_id": str(attempt.id),
            "context_manifest_id": str(context.id),
            "tool_name": str(tool_name),
            "invocation_seq": invocation_seq,
            "request_hash": request_hash,
            "budget_after": budget_after,
        },
        occurred_at=current_time,
    )
    db.flush()
    return ToolInvocationDecision(
        invocation_id=invocation_id,
        tool_call_id=tool_call_id,
        invocation_seq=invocation_seq,
        status="accepted",
        call_envelope={
            "tool_call_id": tool_call_id,
            "tool_name": str(tool_name),
            "arguments": normalized_arguments,
            "task_id": str(task.id),
            "scope_token": token,
            "idempotency_key": normalized_key,
        },
        result_envelope=None,
        duplicate=False,
    )


def verify_tool_scope_token(
    db: Session,
    *,
    invocation_id: str,
    scope_token: str,
    scope_signing_key: str,
) -> bool:
    invocation = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.id == str(invocation_id))
        .one_or_none()
    )
    if invocation is None or str(invocation.status) != "accepted":
        return False
    expected = _scope_token(
        signing_key=scope_signing_key,
        invocation_id=str(invocation.id),
        request_hash=str(invocation.request_hash),
    )
    return hmac.compare_digest(expected, str(scope_token)) and hmac.compare_digest(
        str(invocation.scope_token_hash), _scope_token_hash(scope_token)
    )


def complete_tool_invocation(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    invocation_id: str,
    status: str,
    summary: str,
    data: Any,
    evidence_refs: list[str] | None = None,
    warnings: list[str] | None = None,
    elapsed_ms: int = 0,
    returned_items: int = 0,
    truncated: bool = False,
    external_object_ref: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ToolResultReceipt:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    invocation = (
        db.query(BidToolInvocation)
        .filter(
            BidToolInvocation.id == str(invocation_id),
            BidToolInvocation.task_attempt_id == attempt.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if invocation is None or int(invocation.fencing_token) != int(claim.fencing_token):
        raise BidToolInvocationConflict("BID_TOOL_INVOCATION_FENCE_LOST")
    normalized_status = str(status)
    if normalized_status not in TOOL_RESULT_STATES:
        raise BidToolResultInvalid("BID_TOOL_RESULT_STATUS_INVALID")
    normalized_summary = str(summary or "")
    if len(normalized_summary) > MAX_RESULT_SUMMARY_CHARS:
        raise BidToolResultInvalid("BID_TOOL_RESULT_SUMMARY_TOO_LONG")
    normalized_evidence = sorted(set(str(value) for value in evidence_refs or []))
    _scoped_evidence(db, run=run, evidence_ids=normalized_evidence)
    normalized_warnings = [str(value)[:500] for value in warnings or []]
    if int(elapsed_ms) < 0 or int(returned_items) < 0:
        raise BidToolResultInvalid("BID_TOOL_RESULT_METRICS_INVALID")
    try:
        data_text = canonical_json(data)
    except (TypeError, ValueError) as exc:
        raise BidToolResultInvalid("BID_TOOL_RESULT_DATA_INVALID") from exc
    byte_count = len(data_text.encode("utf-8"))
    if byte_count > MAX_INLINE_RESULT_BYTES and not external_object_ref:
        raise BidToolResultInvalid("BID_TOOL_RESULT_EXTERNAL_REF_REQUIRED")
    if external_object_ref and len(str(external_object_ref)) > 512:
        raise BidToolResultInvalid("BID_TOOL_RESULT_EXTERNAL_REF_INVALID")
    result_payload = {
        "status": normalized_status,
        "summary": normalized_summary,
        "data_hash": hashlib.sha256(data_text.encode("utf-8")).hexdigest(),
        "evidence_refs": normalized_evidence,
        "warnings": normalized_warnings,
        "metrics": {"elapsed_ms": int(elapsed_ms), "returned_items": int(returned_items)},
        "truncated": bool(truncated or external_object_ref),
        "external_object_ref": str(external_object_ref) if external_object_ref else None,
    }
    result_hash = canonical_hash(result_payload)
    existing_result = (
        db.query(BidToolResult)
        .filter(BidToolResult.invocation_id == invocation.id)
        .one_or_none()
    )
    if existing_result is not None:
        if str(existing_result.result_hash) != result_hash:
            raise BidToolInvocationConflict("BID_TOOL_INVOCATION_RESULT_REUSED")
        return ToolResultReceipt(
            invocation_id=str(invocation.id),
            result_id=str(existing_result.id),
            result_hash=str(existing_result.result_hash),
            envelope=_result_envelope(existing_result),
            duplicate=True,
        )
    if str(invocation.status) != "accepted":
        raise BidToolInvocationConflict("BID_TOOL_INVOCATION_NOT_ACCEPTED")
    result_id = str(uuid.uuid4())
    result = BidToolResult(
        id=result_id,
        invocation_id=str(invocation.id),
        task_attempt_id=str(attempt.id),
        status=normalized_status,
        summary=normalized_summary,
        storage_kind="external" if external_object_ref else "inline",
        inline_data_json=None if external_object_ref else json.loads(data_text),
        object_ref=str(external_object_ref) if external_object_ref else None,
        data_hash=result_payload["data_hash"],
        evidence_refs_json=normalized_evidence,
        warnings_json=normalized_warnings,
        metrics_json=result_payload["metrics"],
        truncated=result_payload["truncated"],
        byte_count=byte_count,
        returned_items=int(returned_items),
        result_hash=result_hash,
        expires_at=current_time + timedelta(days=RESULT_RETENTION_DAYS),
        created_at=current_time,
    )
    db.add(result)
    invocation.status = "succeeded" if normalized_status in SUCCESS_RESULT_STATES else "failed"
    invocation.error_code = None if invocation.status == "succeeded" else f"BID_TOOL_{normalized_status.upper()}"
    invocation.completed_at = current_time
    invocation.row_version = int(invocation.row_version) + 1
    db.flush()
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="tool.invocation.complete",
        entity_type="tool_invocation",
        entity_id=str(invocation.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded" if invocation.status == "succeeded" else "failed",
        request_id=str(request_id or f"tool-result:{result_id}"),
        after={
            "result_id": result_id,
            "result_hash": result_hash,
            "status": normalized_status,
            "storage_kind": str(result.storage_kind),
            "byte_count": byte_count,
        },
        occurred_at=current_time,
    )
    db.flush()
    return ToolResultReceipt(
        invocation_id=str(invocation.id),
        result_id=result_id,
        result_hash=result_hash,
        envelope=_result_envelope(result),
        duplicate=False,
    )


def defer_tool_invocation(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    invocation_id: str,
    checkpoint_id: str,
    timeout_seconds: int = 300,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist pending async work and release the current lease; do no I/O."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    invocation = (
        db.query(BidToolInvocation)
        .filter(
            BidToolInvocation.id == str(invocation_id),
            BidToolInvocation.task_attempt_id == attempt.id,
        )
        .with_for_update()
        .one_or_none()
    )
    checkpoint = (
        db.query(BidCheckpoint)
        .filter(
            BidCheckpoint.id == str(checkpoint_id),
            BidCheckpoint.task_attempt_id == attempt.id,
            BidCheckpoint.context_manifest_id == invocation.context_manifest_id
            if invocation is not None
            else False,
        )
        .one_or_none()
    )
    if (
        invocation is None
        or checkpoint is None
        or str(invocation.status) != "accepted"
        or int(invocation.fencing_token) != int(claim.fencing_token)
    ):
        raise BidToolInvocationConflict("BID_TOOL_ASYNC_CHECKPOINT_INVALID")
    operation = BidAsyncOperation(
        id=str(uuid.uuid4()),
        task_id=str(task.id),
        task_attempt_id=str(attempt.id),
        operation_type=f"tool:{invocation.tool_name}"[:64],
        status="created",
        input_hash=str(invocation.request_hash),
        retry_count=0,
        timeout_at=current_time
        + timedelta(seconds=max(30, min(int(timeout_seconds), 3600))),
        row_version=1,
    )
    db.add(operation)
    db.flush()
    invocation.status = "pending"
    invocation.async_operation_id = str(operation.id)
    invocation.checkpoint_id = str(checkpoint.id)
    invocation.row_version = int(invocation.row_version) + 1
    attempt.status = "waiting_operation"
    attempt.lease_owner = None
    attempt.lease_until = None
    attempt.heartbeat_at = None
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "waiting_operation"
    task.row_version = int(task.row_version) + 1
    run.status = "waiting_operation"
    run.waiting_reason = "tool_operation_pending"
    run.current_stage = "waiting_operation"
    run.row_version = int(run.row_version) + 1
    db.flush()
    event = append_outbox_event(
        db,
        event_type="bid.task.waiting_operation.v1",
        producer=TOOL_CONTEXT_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id or f"tool-pending:{invocation.id}"),
        payload_schema="bid.task.waiting_operation.v1.payload",
        payload={
            "task_id": str(task.id),
            "task_key": str(task.task_key),
            "task_type": str(task.task_type),
            "run_id": str(run.id),
            "plan_revision_id": str(task.plan_revision_id),
            "attempt_id": str(attempt.id),
            "operation_id": str(operation.id),
            "checkpoint_id": str(checkpoint.id),
            "stage_code": "waiting_operation",
            "status": "waiting_operation",
            "message": "Waiting for governed tool operation",
            "completed_units": 0,
            "total_units": 0,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"task-waiting-operation:{invocation.id}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{claim.worker_id}",
        action="tool.invocation.defer",
        entity_type="tool_invocation",
        entity_id=str(invocation.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"tool-pending:{invocation.id}"),
        correlation_id=str(event.event_id),
        after={
            "operation_id": str(operation.id),
            "checkpoint_id": str(checkpoint.id),
            "fencing_token": int(claim.fencing_token),
        },
        occurred_at=current_time,
    )
    db.flush()
    return {
        "status": "pending",
        "summary": "Governed tool operation accepted",
        "data": {},
        "result_ref": None,
        "evidence_refs": [],
        "operation_id": str(operation.id),
        "truncated": False,
        "warnings": [],
        "metrics": {"elapsed_ms": 0, "returned_items": 0},
    }


def settle_async_tool_operation(
    db: Session,
    *,
    operation_id: str,
    status: str,
    summary: str,
    data: Any,
    evidence_refs: list[str] | None = None,
    warnings: list[str] | None = None,
    elapsed_ms: int = 0,
    returned_items: int = 0,
    truncated: bool = False,
    external_object_ref: str | None = None,
    worker_id: str = "bid-tool-operation-worker",
    request_id: str | None = None,
    now: datetime | None = None,
) -> ToolResultReceipt:
    """Persist an async observation and make its task resumable on a new fence."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == str(operation_id))
        .with_for_update()
        .one_or_none()
    )
    invocation = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.async_operation_id == str(operation_id))
        .with_for_update()
        .one_or_none()
    )
    if operation is None or invocation is None:
        raise BidToolInvocationConflict("BID_TOOL_ASYNC_OPERATION_NOT_FOUND")
    task = (
        db.query(BidTask)
        .filter(BidTask.id == operation.task_id)
        .with_for_update()
        .one()
    )
    attempt = (
        db.query(BidTaskAttempt)
        .filter(BidTaskAttempt.id == operation.task_attempt_id)
        .with_for_update()
        .one()
    )
    run = (
        db.query(BidAnalysisRun)
        .filter(BidAnalysisRun.id == task.run_id)
        .with_for_update()
        .one()
    )
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .with_for_update()
        .one()
    )
    normalized_status = str(status)
    if normalized_status not in TOOL_RESULT_STATES:
        raise BidToolResultInvalid("BID_TOOL_RESULT_STATUS_INVALID")
    normalized_summary = str(summary or "")
    if len(normalized_summary) > MAX_RESULT_SUMMARY_CHARS:
        raise BidToolResultInvalid("BID_TOOL_RESULT_SUMMARY_TOO_LONG")
    normalized_evidence = sorted(set(str(value) for value in evidence_refs or []))
    _scoped_evidence(db, run=run, evidence_ids=normalized_evidence)
    normalized_warnings = [str(value)[:500] for value in warnings or []]
    if int(elapsed_ms) < 0 or int(returned_items) < 0:
        raise BidToolResultInvalid("BID_TOOL_RESULT_METRICS_INVALID")
    try:
        data_text = canonical_json(data)
    except (TypeError, ValueError) as exc:
        raise BidToolResultInvalid("BID_TOOL_RESULT_DATA_INVALID") from exc
    byte_count = len(data_text.encode("utf-8"))
    if byte_count > MAX_INLINE_RESULT_BYTES and not external_object_ref:
        raise BidToolResultInvalid("BID_TOOL_RESULT_EXTERNAL_REF_REQUIRED")
    result_payload = {
        "status": normalized_status,
        "summary": normalized_summary,
        "data_hash": hashlib.sha256(data_text.encode("utf-8")).hexdigest(),
        "evidence_refs": normalized_evidence,
        "warnings": normalized_warnings,
        "metrics": {"elapsed_ms": int(elapsed_ms), "returned_items": int(returned_items)},
        "truncated": bool(truncated or external_object_ref),
        "external_object_ref": str(external_object_ref) if external_object_ref else None,
    }
    result_hash = canonical_hash(result_payload)
    existing = (
        db.query(BidToolResult)
        .filter(BidToolResult.invocation_id == invocation.id)
        .one_or_none()
    )
    if existing is not None:
        if str(existing.result_hash) != result_hash:
            raise BidToolInvocationConflict("BID_TOOL_INVOCATION_RESULT_REUSED")
        return ToolResultReceipt(
            invocation_id=str(invocation.id),
            result_id=str(existing.id),
            result_hash=str(existing.result_hash),
            envelope=_result_envelope(existing),
            duplicate=True,
        )
    if (
        str(operation.status) not in {"created", "submitted", "running"}
        or str(invocation.status) != "pending"
        or str(task.status) != "waiting_operation"
        or str(attempt.status) != "waiting_operation"
        or str(task.current_attempt_id or "") != str(attempt.id)
        or int(invocation.fencing_token) != int(attempt.fencing_token)
        or str(run.status) != "waiting_operation"
        or run.cancel_requested_at is not None
        or str(assessment.lifecycle_status) != "active"
        or str(assessment.active_run_id or "") != str(run.id)
    ):
        raise BidToolInvocationConflict("BID_TOOL_ASYNC_OPERATION_STALE")
    result_id = str(uuid.uuid4())
    result = BidToolResult(
        id=result_id,
        invocation_id=str(invocation.id),
        task_attempt_id=str(attempt.id),
        async_operation_id=str(operation.id),
        status=normalized_status,
        summary=normalized_summary,
        storage_kind="external" if external_object_ref else "inline",
        inline_data_json=None if external_object_ref else json.loads(data_text),
        object_ref=str(external_object_ref) if external_object_ref else None,
        data_hash=result_payload["data_hash"],
        evidence_refs_json=normalized_evidence,
        warnings_json=normalized_warnings,
        metrics_json=result_payload["metrics"],
        truncated=result_payload["truncated"],
        byte_count=byte_count,
        returned_items=int(returned_items),
        result_hash=result_hash,
        expires_at=current_time + timedelta(days=RESULT_RETENTION_DAYS),
        created_at=current_time,
    )
    db.add(result)
    invocation.status = "succeeded" if normalized_status in SUCCESS_RESULT_STATES else "failed"
    invocation.error_code = None if invocation.status == "succeeded" else f"BID_TOOL_{normalized_status.upper()}"
    invocation.completed_at = current_time
    invocation.row_version = int(invocation.row_version) + 1
    operation.status = "succeeded" if invocation.status == "succeeded" else "failed"
    operation.result_ref = f"tool-result:{result_id}"
    operation.error_code = invocation.error_code
    operation.finished_at = current_time
    operation.row_version = int(operation.row_version) + 1
    # Completion of the external operation transfers continuation to a fresh
    # Attempt/Fence.  The waiting attempt did not complete the task itself and
    # must never be represented as a successful task execution.
    attempt.status = "cancelled"
    attempt.error_code = "BID_TOOL_OPERATION_CONTINUATION_TRANSFERRED"
    attempt.finished_at = current_time
    attempt.row_version = int(attempt.row_version) + 1
    task.status = "ready"
    task.current_attempt_id = None
    task.row_version = int(task.row_version) + 1
    run.status = "queued"
    run.waiting_reason = None
    run.current_stage = "task_execution"
    run.row_version = int(run.row_version) + 1
    db.flush()
    event = append_outbox_event(
        db,
        event_type="bid.task.ready.v1",
        producer=TOOL_CONTEXT_PRODUCER,
        aggregate_type="task",
        aggregate_id=str(task.id),
        aggregate_version=int(task.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(request_id or f"tool-operation:{operation.id}"),
        payload_schema="bid.task.ready.v1.payload",
        payload={
            "task_id": str(task.id),
            "task_key": str(task.task_key),
            "task_type": str(task.task_type),
            "run_id": str(run.id),
            "plan_revision_id": str(task.plan_revision_id),
            "stage_code": "task_execution",
            "status": "ready",
            "message": "Governed tool operation completed; task may resume",
            "completed_units": 0,
            "total_units": 0,
            "resource_version": int(run.row_version),
        },
        dedupe_key=f"tool-operation-ready:{operation.id}:{result_hash}",
        occurred_at=current_time,
    )
    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{str(worker_id)[:128]}",
        action="tool.operation.settle",
        entity_type="async_operation",
        entity_id=str(operation.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(request_id or f"tool-operation:{operation.id}"),
        correlation_id=str(event.event_id),
        after={
            "invocation_id": str(invocation.id),
            "result_id": result_id,
            "result_hash": result_hash,
            "previous_attempt_id": str(attempt.id),
            "task_status": "ready",
            "run_status": "queued",
        },
        occurred_at=current_time,
    )
    db.flush()
    return ToolResultReceipt(
        invocation_id=str(invocation.id),
        result_id=result_id,
        result_hash=result_hash,
        envelope=_result_envelope(result),
        duplicate=False,
    )


def read_tool_result_slice(
    db: Session,
    claim: TaskLeaseClaim,
    *,
    result_ref_id: str,
    cursor: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = as_utc(now) if now is not None else database_utc_now(db)
    _attempt, task, run = lock_task_claim(
        db,
        claim,
        now=current_time,
        allowed_attempt_states={"running", "validating"},
    )
    result = (
        db.query(BidToolResult)
        .join(
            BidToolInvocation,
            BidToolInvocation.id == BidToolResult.invocation_id,
        )
        .filter(
            BidToolResult.id == str(result_ref_id),
            BidToolInvocation.run_id == run.id,
            BidToolInvocation.task_id == task.id,
        )
        .one_or_none()
    )
    if result is None or (result.expires_at and as_utc(result.expires_at) <= current_time):
        raise BidToolUnauthorized("BID_TOOL_RESULT_NOT_FOUND")
    if str(result.storage_kind) != "inline":
        raise BidToolResultInvalid("BID_TOOL_RESULT_EXTERNAL_ADAPTER_REQUIRED")
    data = result.inline_data_json
    items = data if isinstance(data, list) else [data]
    try:
        offset = int(base64.urlsafe_b64decode((cursor or "MA==").encode()).decode())
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise BidToolArgumentsInvalid("BID_TOOL_RESULT_CURSOR_INVALID") from exc
    bounded_limit = max(1, min(int(limit), 20))
    page = items[offset : offset + bounded_limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < len(items):
        next_cursor = base64.urlsafe_b64encode(str(next_offset).encode()).decode()
    return {
        "items": page,
        "cursor": next_cursor,
        "has_more": next_cursor is not None,
        "result_hash": str(result.result_hash),
    }


def time_out_async_tool_operation(
    db: Session,
    *,
    operation_id: str,
    now: datetime | None = None,
) -> tuple[bool, ToolResultReceipt | None]:
    """Fence one overdue async call and make its Task resumable; do no I/O."""

    current_time = as_utc(now) if now is not None else database_utc_now(db)
    operation = (
        db.query(BidAsyncOperation)
        .filter(BidAsyncOperation.id == str(operation_id))
        .with_for_update()
        .one_or_none()
    )
    if (
        operation is None
        or not str(operation.operation_type).startswith("tool:")
        or str(operation.status) not in ASYNC_ACTIVE_STATES
        or operation.timeout_at is None
        or as_utc(operation.timeout_at) > current_time
    ):
        return False, None
    receipt = settle_async_tool_operation(
        db,
        operation_id=str(operation.id),
        status="failed",
        summary="Governed tool operation timed out",
        data={},
        warnings=["operation timeout reached before a governed result was received"],
        worker_id="bid-tool-operation-maintenance",
        request_id=f"tool-timeout:{operation.id}",
        now=current_time,
    )
    invocation = (
        db.query(BidToolInvocation)
        .filter(BidToolInvocation.async_operation_id == operation.id)
        .with_for_update()
        .one()
    )
    operation.status = "timed_out"
    operation.error_code = "BID_TOOL_OPERATION_TIMED_OUT"
    operation.row_version = int(operation.row_version) + 1
    invocation.error_code = "BID_TOOL_OPERATION_TIMED_OUT"
    invocation.row_version = int(invocation.row_version) + 1
    dispatch = (
        db.query(BidToolDispatch)
        .filter(BidToolDispatch.async_operation_id == operation.id)
        .with_for_update()
        .one_or_none()
    )
    if dispatch is not None and str(dispatch.status) in {
        "queued",
        "leased",
        "sending",
        "awaiting_receipt",
        "retry_wait",
    }:
        active_dispatch_attempts = (
            db.query(BidToolDispatchAttempt)
            .filter(
                BidToolDispatchAttempt.dispatch_id == dispatch.id,
                BidToolDispatchAttempt.status.in_(("leased", "sending")),
            )
            .with_for_update()
            .all()
        )
        for dispatch_attempt in active_dispatch_attempts:
            dispatch_attempt.status = "cancelled"
            dispatch_attempt.finished_at = current_time
            dispatch_attempt.error_code = "BID_TOOL_OPERATION_TIMED_OUT"
        dispatch.status = "failed"
        dispatch.lease_owner = None
        dispatch.lease_until = None
        dispatch.completed_at = current_time
        dispatch.last_error_code = "BID_TOOL_OPERATION_TIMED_OUT"
        dispatch.row_version = int(dispatch.row_version) + 1
    append_audit_log(
        db,
        actor_type="service",
        actor_ref="service:bid-tool-operation-maintenance",
        action="tool.operation.timeout",
        entity_type="async_operation",
        entity_id=str(operation.id),
        assessment_id=str(invocation.assessment_id),
        outcome="failed",
        request_id=f"tool-timeout:{operation.id}",
        after={
            "invocation_id": str(invocation.id),
            "result_id": str(receipt.result_id),
            "operation_status": "timed_out",
            "error_code": "BID_TOOL_OPERATION_TIMED_OUT",
        },
        occurred_at=current_time,
    )
    db.flush()
    return True, receipt


def maintain_tool_operations(
    *,
    session_factory: Callable[[], Session],
    limit: int = 100,
    now: datetime | None = None,
) -> ToolOperationMaintenanceResult:
    """Recover overdue Tool operations without dispatching tools or models."""

    scan_db = session_factory()
    try:
        current_time = as_utc(now) if now is not None else database_utc_now(scan_db)
        operation_ids = [
            str(row[0])
            for row in scan_db.query(BidAsyncOperation.id)
            .filter(
                BidAsyncOperation.operation_type.like("tool:%"),
                BidAsyncOperation.status.in_(tuple(ASYNC_ACTIVE_STATES)),
                BidAsyncOperation.timeout_at.is_not(None),
                BidAsyncOperation.timeout_at <= current_time,
            )
            .order_by(BidAsyncOperation.timeout_at.asc(), BidAsyncOperation.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        ]
    finally:
        scan_db.close()

    timed_out = recovered = failed = 0
    for operation_id in operation_ids:
        db = session_factory()
        try:
            with db.begin():
                changed, receipt = time_out_async_tool_operation(
                    db,
                    operation_id=operation_id,
                    now=current_time,
                )
            if changed:
                timed_out += 1
                recovered += int(receipt is not None)
        except Exception:
            logger.exception(
                "bid_tool_operation_maintenance_failed",
                extra={"operation_id": operation_id},
            )
            failed += 1
        finally:
            db.close()
    return ToolOperationMaintenanceResult(
        scanned=len(operation_ids),
        timed_out=timed_out,
        recovered=recovered,
        failed=failed,
    )
