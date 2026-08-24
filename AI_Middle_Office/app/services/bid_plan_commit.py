"""Phase 3B deterministic Planner validation and atomic Plan Commit.

This module intentionally contains no model-provider adapter.  It builds the
first PlanProposal from frozen Run inputs, validates every executable field
against the standard task registry, and commits PlanRevision/Task/Dependency,
Outbox, audit, and processed-event rows in one caller-owned transaction.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocument,
    BidDocumentManifest,
    BidDocumentVersion,
    BidManifestDocument,
)
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
)
from app.models.bid_assessment_eventing import (
    BidOutboxEvent,
    BidProcessedEvent,
)
from app.models.bid_assessment_runtime import (
    BidAnalysisRun,
    BidPlanRevision,
    BidTask,
    BidTaskDependency,
)
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    canonical_json,
    process_outbox_event_once,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_task_registry import (
    StandardTaskRegistry,
    load_standard_task_registry,
)


logger = logging.getLogger(__name__)

RUN_CREATED_EVENT = "bid.run.created.v1"
PLAN_COMMIT_CONSUMER = "bid-plan-commit-v1"
PLANNER_GENERATOR_VERSION = "bid-deterministic-bootstrap-planner-v1"
PLAN_VALIDATOR_VERSION = "bid-plan-validator-v1"
PLAN_ENVELOPE_SCHEMA = "bid.plan.commit.envelope.v1"
PLANNING_LIMITS = {"max_dynamic_tasks": 8, "max_dependency_depth": 3}
BOOTSTRAP_STAGE = "fact_baseline"

RUN_CREATED_REQUIRED_FIELDS = (
    "run_id",
    "assessment_id",
    "scope_id",
    "manifest_id",
    "run_kind",
    "run_sequence",
    "input_fingerprint",
    "input_hash",
    "resource_version",
)

HARD_GATE_TASK_TYPES = frozenset(
    {
        "evaluate_deadline_gate",
        "evaluate_qualification_gate",
        "evaluate_personnel_performance_gate",
        "evaluate_legal_compliance_gate",
        "evaluate_guarantee_cash_gate",
        "evaluate_minimum_bid_capacity_gate",
        "evaluate_enterprise_prohibited_risk_gate",
    }
)
DIMENSION_TASK_TYPES = frozenset(
    {
        "analyze_qualification_compliance",
        "analyze_win_probability",
        "analyze_project_economics",
        "analyze_bid_investment",
        "analyze_contract_delivery_risk",
        "analyze_capability_fit",
        "analyze_customer_strategy",
    }
)
REPORT_TASK_TYPES = frozenset(
    {
        "generate_preliminary_report",
        "generate_deep_report",
        "generate_version_delta",
    }
)
RUN_TERMINAL_STATES = frozenset({"succeeded", "stale", "cancelled"})
READY_DEPENDENCY_STATES = frozenset({"succeeded", "skipped"})


class BidPlanCommitError(RuntimeError):
    code = "BID_PLAN_COMMIT_ERROR"


class BidRunCreatedEventInvalid(BidPlanCommitError):
    code = "BID_RUN_CREATED_EVENT_INVALID"


class BidPlanValidationError(BidPlanCommitError):
    code = "BID_PLAN_VALIDATION_FAILED"

    def __init__(self, *violations: str):
        normalized = tuple(
            dict.fromkeys(str(item)[:200] for item in violations if str(item))
        )
        super().__init__(self.code)
        self.violations = normalized or ("unknown_validation_failure",)


@dataclass(frozen=True)
class PlanValidationResult:
    planner_input_hash: str
    proposal_hash: str
    validated_hash: str
    normalized_proposal: dict[str, Any]
    checks: tuple[str, ...]


@dataclass(frozen=True)
class PlanCommitBatchResult:
    scanned: int
    committed: int
    duplicate: int
    ignored: int
    failed: int


def _utc_text(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _load_required(db: Session, model: type, row_id: str, code: str) -> Any:
    row = db.query(model).filter(model.id == row_id).one_or_none()
    if row is None:
        raise BidPlanValidationError(code)
    return row


def _document_inventory(db: Session, *, manifest_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(
            BidManifestDocument,
            BidDocumentVersion,
            BidDocument,
            BidDocumentParseRun,
        )
        .join(
            BidDocumentVersion,
            BidDocumentVersion.id == BidManifestDocument.document_version_id,
        )
        .join(BidDocument, BidDocument.id == BidDocumentVersion.document_id)
        .outerjoin(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id == BidDocumentVersion.id,
        )
        .outerjoin(
            BidDocumentParseRun,
            and_(
                BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
                BidDocumentParseRun.document_version_id == BidDocumentVersion.id,
            ),
        )
        .filter(BidManifestDocument.manifest_id == manifest_id)
        .order_by(
            BidManifestDocument.order_no.asc(),
            BidManifestDocument.document_version_id.asc(),
        )
        .all()
    )
    return [
        {
            "type": str(document.document_type),
            "status": str(parse_run.status) if parse_run is not None else "not_requested",
            "version_id": str(version.id),
        }
        for _manifest_item, version, document, parse_run in rows
    ]


def _existing_task_rows(db: Session, *, run_id: str) -> list[BidTask]:
    return (
        db.query(BidTask)
        .filter(BidTask.run_id == run_id)
        .order_by(BidTask.created_at.asc(), BidTask.id.asc())
        .all()
    )


def build_planner_input(
    db: Session,
    run: BidAnalysisRun,
    *,
    registry: StandardTaskRegistry | None = None,
) -> dict[str, Any]:
    """Materialize the schema-level PlannerInput from frozen Run bindings."""

    task_registry = registry or load_standard_task_registry()
    assessment = _load_required(
        db, BidAssessment, str(run.assessment_id), "assessment_not_found"
    )
    scope = _load_required(db, BidAssessmentScope, str(run.scope_id), "scope_not_found")
    manifest = _load_required(
        db, BidDocumentManifest, str(run.manifest_id), "manifest_not_found"
    )
    enterprise = _load_required(
        db,
        BidEnterpriseSnapshot,
        str(run.enterprise_snapshot_id),
        "enterprise_snapshot_not_found",
    )
    rule_set = _load_required(db, BidRuleSet, str(run.rule_set_id), "rule_set_not_found")
    fact_catalog = _load_required(
        db,
        BidFactCatalogVersion,
        str(run.fact_catalog_version_id),
        "fact_catalog_not_found",
    )
    prompt_bundle = _load_required(
        db, BidPromptBundle, str(run.prompt_bundle_id), "prompt_bundle_not_found"
    )
    tool_registry = _load_required(
        db,
        BidToolRegistryVersion,
        str(run.tool_registry_version_id),
        "tool_registry_not_found",
    )
    model_profile = _load_required(
        db,
        BidModelProfileVersion,
        str(run.model_profile_version_id),
        "model_profile_not_found",
    )
    formula_catalog = _load_required(
        db,
        BidFormulaCatalogVersion,
        str(run.formula_catalog_version_id),
        "formula_catalog_not_found",
    )

    if (
        str(scope.assessment_id) != str(run.assessment_id)
        or str(manifest.assessment_id) != str(run.assessment_id)
        or str(assessment.id) != str(run.assessment_id)
    ):
        raise BidPlanValidationError("scope_version_consistency")

    task_rows = _existing_task_rows(db, run_id=str(run.id))
    return {
        "assessment": {
            "id": str(assessment.id),
            "goal": "bid_go_no_go",
            "scope_id": str(scope.id),
        },
        "run_kind": str(run.run_kind),
        "bound_versions": {
            "manifest_id": str(manifest.id),
            "manifest_version": int(manifest.version),
            "scope_id": str(scope.id),
            "scope_version": int(scope.version),
            "enterprise_snapshot_version": str(enterprise.version),
            "rule_set_version": str(rule_set.version),
            "fact_catalog_version": str(fact_catalog.version),
            "prompt_bundle_version": str(prompt_bundle.version),
            "tool_registry_version": str(tool_registry.version),
            "model_profile_version": str(model_profile.version),
            "formula_catalog_version": str(formula_catalog.version),
            "evaluation_time": _utc_text(run.evaluation_time),
        },
        "workflow_stage": str(run.current_stage or "planning"),
        "document_inventory": _document_inventory(db, manifest_id=str(manifest.id)),
        "fact_slot_summary": {
            "coverage": {
                "resolved": 0,
                "missing": 0,
                "unavailable": 0,
                "not_assessed": 0,
                "blocked_by_parent": 0,
                "not_applicable": 0,
                "stale": 0,
            },
            "resolved_facts": {"supported": 0, "partial": 0, "conflicted": 0},
        },
        "gate_summary": [],
        "task_summary": [
            {"task_key": str(task.task_key), "status": str(task.status)}
            for task in task_rows
        ],
        "open_questions": [],
        "allowed_task_types": list(task_registry.task_order),
        "planning_limits": dict(PLANNING_LIMITS),
    }


def build_bootstrap_plan_proposal(
    planner_input: dict[str, Any],
    *,
    registry: StandardTaskRegistry | None = None,
) -> dict[str, Any]:
    """Build the deterministic first batch; never call a model or a tool."""

    task_registry = registry or load_standard_task_registry()
    planner_input_hash = canonical_hash(planner_input)
    definitions = (
        ("phase3b.01.bind_assessment_snapshot", "bind_assessment_snapshot", ()),
        (
            "phase3b.02.inventory_documents",
            "inventory_documents",
            ("phase3b.01.bind_assessment_snapshot",),
        ),
        (
            "phase3b.03.build_coverage_baseline",
            "build_coverage_baseline",
            ("phase3b.02.inventory_documents",),
        ),
        (
            "phase3b.04.extract_tender_overview",
            "extract_tender_overview",
            ("phase3b.03.build_coverage_baseline",),
        ),
        (
            "phase3b.05.extract_critical_dates",
            "extract_critical_dates",
            ("phase3b.03.build_coverage_baseline",),
        ),
        (
            "phase3b.06.extract_qualification_requirements",
            "extract_qualification_requirements",
            ("phase3b.03.build_coverage_baseline",),
        ),
        (
            "phase3b.07.extract_rejection_clauses",
            "extract_rejection_clauses",
            ("phase3b.03.build_coverage_baseline",),
        ),
        (
            "phase3b.08.extract_guarantees_and_fees",
            "extract_guarantees_and_fees",
            ("phase3b.03.build_coverage_baseline",),
        ),
    )
    return {
        "proposal_id": f"planp_{planner_input_hash[:32]}",
        "reason_codes": [
            "INITIAL_RUN_PLAN",
            "FROZEN_INPUTS_BOUND",
            "PHASE2_AUTHORITIES_REUSED",
        ],
        "add_tasks": [
            task_registry.policies[task_type].definition(
                task_key=task_key,
                depends_on=depends_on,
            )
            for task_key, task_type, depends_on in definitions
        ],
        "supersede_tasks": [],
        "questions": [],
        "expected_stage_after": BOOTSTRAP_STAGE,
        "planner_confidence": "high",
    }


def _validate_proposal_shape(proposal: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    required = {
        "proposal_id",
        "reason_codes",
        "add_tasks",
        "supersede_tasks",
        "questions",
        "expected_stage_after",
        "planner_confidence",
    }
    if set(proposal) != required:
        violations.append("plan_proposal_shape")
    if not isinstance(proposal.get("proposal_id"), str) or not proposal.get("proposal_id"):
        violations.append("proposal_id_invalid")
    reason_codes = proposal.get("reason_codes")
    if (
        not isinstance(reason_codes, list)
        or not reason_codes
        or len(reason_codes) > 20
        or any(not isinstance(code, str) for code in reason_codes or [])
        or len(reason_codes) != len(set(reason_codes or []))
        or any(
            not isinstance(code, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None
            for code in reason_codes or []
        )
    ):
        violations.append("reason_codes_invalid")
    if not isinstance(proposal.get("add_tasks"), list):
        violations.append("add_tasks_invalid")
    if not isinstance(proposal.get("supersede_tasks"), list):
        violations.append("supersede_tasks_invalid")
    if not isinstance(proposal.get("questions"), list) or len(proposal.get("questions") or []) > 3:
        violations.append("questions_invalid")
    if proposal.get("planner_confidence") not in {"high", "medium", "low"}:
        violations.append("planner_confidence_invalid")
    if not isinstance(proposal.get("expected_stage_after"), str) or not proposal.get(
        "expected_stage_after"
    ):
        violations.append("expected_stage_after_invalid")
    return violations


def _dependency_depth(
    task_key: str,
    graph: dict[str, tuple[str, ...]],
    proposal_keys: frozenset[str],
    memo: dict[str, int],
) -> int:
    if task_key in memo:
        return memo[task_key]
    proposal_parents = [parent for parent in graph.get(task_key, ()) if parent in proposal_keys]
    depth = 0 if not proposal_parents else 1 + max(
        _dependency_depth(parent, graph, proposal_keys, memo)
        for parent in proposal_parents
    )
    memo[task_key] = depth
    return depth


def _ancestor_types(
    task_key: str,
    graph: dict[str, tuple[str, ...]],
    type_by_key: dict[str, str],
) -> set[str]:
    found: set[str] = set()
    stack = list(graph.get(task_key, ()))
    visited: set[str] = set()
    while stack:
        parent = stack.pop()
        if parent in visited:
            continue
        visited.add(parent)
        parent_type = type_by_key.get(parent)
        if parent_type:
            found.add(parent_type)
        stack.extend(graph.get(parent, ()))
    return found


def validate_plan_proposal(
    planner_input: dict[str, Any],
    proposal: dict[str, Any],
    *,
    run_input_hash: str,
    registry: StandardTaskRegistry | None = None,
    existing_tasks: Iterable[BidTask | dict[str, Any]] = (),
    existing_dependencies: Iterable[tuple[str, str]] = (),
) -> PlanValidationResult:
    """Apply the complete deterministic Phase 3B validation gate."""

    task_registry = registry or load_standard_task_registry()
    normalized = json.loads(canonical_json(proposal))
    violations = _validate_proposal_shape(normalized)
    add_tasks = normalized.get("add_tasks") if isinstance(normalized.get("add_tasks"), list) else []
    limits = dict(planner_input.get("planning_limits") or {})
    if limits != PLANNING_LIMITS:
        violations.append("planning_limits_not_frozen")
    if len(add_tasks) > int(limits.get("max_dynamic_tasks") or 0):
        violations.append("max_8_dynamic_tasks")

    allowed_task_types = tuple(planner_input.get("allowed_task_types") or ())
    if allowed_task_types != task_registry.task_order:
        violations.append("task_type_allowlist_not_frozen")
    allowed_task_set = frozenset(allowed_task_types)

    existing_by_key: dict[str, dict[str, Any]] = {}
    existing_id_to_key: dict[str, str] = {}
    for item in existing_tasks:
        if isinstance(item, dict):
            task_key = str(item.get("task_key") or "")
            task_id = str(item.get("id") or task_key)
            task_type = str(item.get("task_type") or "")
            status = str(item.get("status") or "")
        else:
            task_key = str(item.task_key)
            task_id = str(item.id)
            task_type = str(item.task_type)
            status = str(item.status)
        if not task_key or task_key in existing_by_key:
            violations.append("existing_task_key_ambiguous")
            continue
        existing_by_key[task_key] = {
            "id": task_id,
            "task_type": task_type,
            "status": status,
        }
        existing_id_to_key[task_id] = task_key

    required_task_fields = {
        "task_key",
        "task_type",
        "objective",
        "depends_on",
        "required_fact_slots",
        "tool_profile",
        "context_profile",
        "budget_profile",
        "completion_contract",
    }
    task_by_key: dict[str, dict[str, Any]] = {}
    for definition in add_tasks:
        if not isinstance(definition, dict) or set(definition) != required_task_fields:
            violations.append("task_definition_shape")
            continue
        task_key = str(definition.get("task_key") or "")
        task_type = str(definition.get("task_type") or "")
        if not task_key or len(task_key) > 160 or task_key in task_by_key or task_key in existing_by_key:
            violations.append("task_key_unique")
            continue
        task_by_key[task_key] = definition
        if task_type not in allowed_task_set or task_type not in task_registry.policies:
            violations.append(f"task_type_allowlist:{task_type}")
            continue
        policy = task_registry.policies[task_type]
        if (
            definition.get("tool_profile") != policy.tool_profile
            or definition.get("context_profile") != policy.context_profile
        ):
            violations.append(f"tool_profile_permissions:{task_key}")
        if definition.get("budget_profile") != policy.budget_profile:
            violations.append(f"budget_limits:{task_key}")
        if definition.get("completion_contract") != policy.completion_contract:
            violations.append(f"completion_contract:{task_key}")
        if not isinstance(definition.get("objective"), str) or not definition.get("objective"):
            violations.append(f"objective_invalid:{task_key}")
        for list_field in ("depends_on", "required_fact_slots"):
            values = definition.get(list_field)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values or [])
                or len(values) != len(set(values or []))
            ):
                violations.append(f"{list_field}_invalid:{task_key}")

    graph: dict[str, tuple[str, ...]] = {key: () for key in existing_by_key}
    for child_id, parent_id in existing_dependencies:
        child_key = existing_id_to_key.get(str(child_id), str(child_id))
        parent_key = existing_id_to_key.get(str(parent_id), str(parent_id))
        if child_key in graph:
            graph[child_key] = (*graph[child_key], parent_key)
    known_keys = frozenset(existing_by_key) | frozenset(task_by_key)
    for task_key, definition in task_by_key.items():
        dependencies = tuple(str(value) for value in definition.get("depends_on") or ())
        graph[task_key] = dependencies
        if task_key in dependencies:
            violations.append(f"acyclic_dependencies:{task_key}")
        unknown = sorted(set(dependencies) - known_keys)
        if unknown:
            violations.append(f"dependency_unknown:{task_key}:{','.join(unknown)}")

    colors: dict[str, int] = {}

    def _visit(node: str) -> None:
        state = colors.get(node, 0)
        if state == 1:
            violations.append(f"acyclic_dependencies:{node}")
            return
        if state == 2:
            return
        colors[node] = 1
        for parent in graph.get(node, ()):
            if parent in graph:
                _visit(parent)
        colors[node] = 2

    for key in graph:
        _visit(key)

    if not any(item.startswith("acyclic_dependencies") for item in violations):
        memo: dict[str, int] = {}
        proposal_keys = frozenset(task_by_key)
        for key in proposal_keys:
            if _dependency_depth(key, graph, proposal_keys, memo) > int(
                limits.get("max_dependency_depth") or 0
            ):
                violations.append(f"max_dependency_depth_3:{key}")

    type_by_key = {
        key: str(value["task_type"]) for key, value in existing_by_key.items()
    }
    type_by_key.update(
        {key: str(value.get("task_type") or "") for key, value in task_by_key.items()}
    )
    for task_key, definition in task_by_key.items():
        task_type = str(definition.get("task_type") or "")
        ancestors = _ancestor_types(task_key, graph, type_by_key)
        if task_type in HARD_GATE_TASK_TYPES and "resolve_fact_conflicts" not in ancestors:
            violations.append(f"hard_gate_ordering:{task_key}")
        if task_type in DIMENSION_TASK_TYPES and not HARD_GATE_TASK_TYPES.issubset(ancestors):
            violations.append(f"hard_gate_ordering:{task_key}")
        if task_type == "synthesize_assessment":
            run_kind = str(planner_input.get("run_kind") or "deep")
            if run_kind in {"preliminary", "reanalysis"}:
                if not HARD_GATE_TASK_TYPES.issubset(ancestors):
                    violations.append(f"synthesis_ordering:{task_key}")
            elif not DIMENSION_TASK_TYPES.issubset(ancestors):
                violations.append(f"synthesis_ordering:{task_key}")
        if task_type == "evaluate_final_decision" and "synthesize_assessment" not in ancestors:
            violations.append(f"decision_ordering:{task_key}")
        if task_type == "validate_claim_evidence" and "synthesize_assessment" not in ancestors:
            violations.append(f"report_validation_ordering:{task_key}")
        if task_type == "validate_report_consistency" and not {
            "evaluate_final_decision",
            "validate_claim_evidence",
        }.issubset(ancestors):
            violations.append(f"report_validation_ordering:{task_key}")
        if task_type in REPORT_TASK_TYPES and not {
            "evaluate_final_decision",
            "validate_claim_evidence",
            "validate_report_consistency",
        }.issubset(ancestors):
            violations.append(f"report_validation_ordering:{task_key}")

    supersede_tasks = normalized.get("supersede_tasks") or []
    if (
        not isinstance(supersede_tasks, list)
        or len(supersede_tasks) > 20
        or any(not isinstance(key, str) or not key for key in supersede_tasks or [])
        or len(supersede_tasks) != len(set(supersede_tasks))
        or any(str(key) not in existing_by_key for key in supersede_tasks)
    ):
        violations.append("supersede_tasks_invalid")

    expected_binding = planner_input.get("bound_versions") or {}
    if (
        str(planner_input.get("assessment", {}).get("scope_id") or "")
        != str(expected_binding.get("scope_id") or "")
        or not run_input_hash
        or len(run_input_hash) != 64
    ):
        violations.append("scope_version_consistency")

    if violations:
        raise BidPlanValidationError(*violations)

    planner_input_hash = canonical_hash(planner_input)
    proposal_hash = canonical_hash(normalized)
    validated_payload = {
        "schema": PLAN_ENVELOPE_SCHEMA,
        "generator_version": PLANNER_GENERATOR_VERSION,
        "validator_version": PLAN_VALIDATOR_VERSION,
        "task_registry_version": task_registry.version,
        "task_registry_hash": task_registry.registry_hash,
        "run_input_hash": run_input_hash,
        "planner_input_hash": planner_input_hash,
        "proposal_hash": proposal_hash,
    }
    return PlanValidationResult(
        planner_input_hash=planner_input_hash,
        proposal_hash=proposal_hash,
        validated_hash=canonical_hash(validated_payload),
        normalized_proposal=normalized,
        checks=(
            "task_type_allowlist",
            "acyclic_dependencies",
            "scope_version_consistency",
            "tool_profile_permissions",
            "budget_limits",
            "max_8_dynamic_tasks",
            "max_dependency_depth_3",
            "hard_gate_ordering",
            "report_validation_ordering",
        ),
    )


def _plan_envelope(
    *,
    run: BidAnalysisRun,
    planner_input: dict[str, Any],
    validation: PlanValidationResult,
    registry: StandardTaskRegistry,
) -> dict[str, Any]:
    return {
        "schema": PLAN_ENVELOPE_SCHEMA,
        "generator_version": PLANNER_GENERATOR_VERSION,
        "validator_version": PLAN_VALIDATOR_VERSION,
        "task_registry_version": registry.version,
        "task_registry_hash": registry.registry_hash,
        "run_input_hash": str(run.input_hash),
        "planner_input_hash": validation.planner_input_hash,
        "proposal_hash": validation.proposal_hash,
        "planner_input": planner_input,
        "proposal": validation.normalized_proposal,
        "validation": {
            "status": "accepted",
            "checks": list(validation.checks),
            "validated_hash": validation.validated_hash,
        },
    }


def _commit_initial_plan(
    db: Session,
    *,
    run: BidAnalysisRun,
    source_event: BidOutboxEvent,
    committed_at: datetime,
) -> dict[str, Any]:
    existing_plan = (
        db.query(BidPlanRevision)
        .filter(
            BidPlanRevision.run_id == run.id,
            BidPlanRevision.committed_slot_key == "committed",
        )
        .with_for_update()
        .one_or_none()
    )
    if existing_plan is not None:
        task_count = (
            db.query(func.count(BidTask.id))
            .filter(BidTask.plan_revision_id == existing_plan.id)
            .scalar()
            or 0
        )
        return {
            "ignored": False,
            "committed": False,
            "plan_revision_id": str(existing_plan.id),
            "task_count": int(task_count),
            "result_ref": f"plan_revision:{existing_plan.id}",
        }

    if str(run.status) in RUN_TERMINAL_STATES or (
        str(run.status) == "failed" and not bool(run.retryable)
    ):
        return {"ignored": True, "reason": "run_terminal"}
    if str(run.status) not in {"created", "planning"}:
        raise BidPlanCommitError(f"BID_RUN_STATUS_NOT_PLANNABLE:{run.status}")

    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidPlanCommitError("BID_ASSESSMENT_NOT_FOUND")
    if str(assessment.active_run_id or "") != str(run.id):
        return {"ignored": True, "reason": "run_no_longer_active"}

    registry = load_standard_task_registry()
    existing_tasks = _existing_task_rows(db, run_id=str(run.id))
    dependency_rows = (
        db.query(BidTaskDependency)
        .filter(BidTaskDependency.run_id == run.id)
        .all()
    )
    planner_input = build_planner_input(db, run, registry=registry)
    proposal = build_bootstrap_plan_proposal(planner_input, registry=registry)
    if proposal["supersede_tasks"]:
        raise BidPlanValidationError("bootstrap_supersede_not_allowed")
    validation = validate_plan_proposal(
        planner_input,
        proposal,
        run_input_hash=str(run.input_hash),
        registry=registry,
        existing_tasks=existing_tasks,
        existing_dependencies=(
            (str(row.task_id), str(row.depends_on_task_id)) for row in dependency_rows
        ),
    )

    revision_no = int(
        db.query(func.max(BidPlanRevision.revision_no))
        .filter(BidPlanRevision.run_id == run.id)
        .scalar()
        or 0
    ) + 1
    plan = BidPlanRevision(
        id=str(uuid.uuid4()),
        run_id=str(run.id),
        revision_no=revision_no,
        proposal_json=_plan_envelope(
            run=run,
            planner_input=planner_input,
            validation=validation,
            registry=registry,
        ),
        validated_hash=None,
        status="proposed",
        committed_slot_key=None,
        row_version=1,
    )
    db.add(plan)
    db.flush()
    plan.status = "validating"
    plan.row_version = 2
    db.flush()

    previous_run = {
        "status": str(run.status),
        "current_stage": run.current_stage,
        "row_version": int(run.row_version),
    }
    if str(run.status) == "created":
        run.status = "planning"
        run.current_stage = "planning"
        run.row_version = int(run.row_version) + 1
        run.last_checkpoint_at = committed_at
        db.flush()

    task_by_key: dict[str, BidTask] = {}
    all_existing_by_key = {str(task.task_key): task for task in existing_tasks}
    for definition in validation.normalized_proposal["add_tasks"]:
        task_type = str(definition["task_type"])
        policy = registry.policies[task_type]
        dependencies = tuple(str(value) for value in definition["depends_on"])
        dependencies_ready = all(
            dependency in all_existing_by_key
            and str(all_existing_by_key[dependency].status) in READY_DEPENDENCY_STATES
            for dependency in dependencies
        )
        task = BidTask(
            id=str(uuid.uuid4()),
            run_id=str(run.id),
            plan_revision_id=str(plan.id),
            task_key=str(definition["task_key"]),
            task_type=task_type,
            objective=str(definition["objective"]),
            status="ready" if dependencies_ready else "blocked",
            input_hash=canonical_hash(
                {
                    "run_input_hash": str(run.input_hash),
                    "task_definition": definition,
                }
            ),
            tool_profile=str(definition["tool_profile"]),
            context_profile=str(definition["context_profile"]),
            budget_profile=str(definition["budget_profile"]),
            completion_contract=str(definition["completion_contract"]),
            current_attempt_id=None,
            priority=int(policy.priority),
            row_version=1,
        )
        db.add(task)
        task_by_key[str(task.task_key)] = task
    db.flush()

    combined_by_key = {**all_existing_by_key, **task_by_key}
    for definition in validation.normalized_proposal["add_tasks"]:
        child = task_by_key[str(definition["task_key"])]
        for dependency_key in definition["depends_on"]:
            parent = combined_by_key[str(dependency_key)]
            db.add(
                BidTaskDependency(
                    run_id=str(run.id),
                    task_id=str(child.id),
                    depends_on_task_id=str(parent.id),
                )
            )
    db.flush()

    plan.status = "committed"
    plan.validated_hash = validation.validated_hash
    plan.committed_slot_key = "committed"
    plan.committed_at = committed_at
    plan.row_version = 3
    run.status = "queued"
    run.current_stage = str(validation.normalized_proposal["expected_stage_after"])
    run.started_at = run.started_at or committed_at
    run.last_checkpoint_at = committed_at
    run.waiting_reason = None
    run.retryable = False
    run.row_version = int(run.row_version) + 1
    db.flush()

    ready_tasks = [task for task in task_by_key.values() if str(task.status) == "ready"]
    plan_event = append_outbox_event(
        db,
        event_type="bid.plan.committed.v1",
        producer=PLAN_COMMIT_CONSUMER,
        aggregate_type="plan_revision",
        aggregate_id=str(plan.id),
        aggregate_version=int(plan.row_version),
        assessment_id=str(run.assessment_id),
        run_id=str(run.id),
        request_id=str(source_event.request_id),
        causation_event_id=str(source_event.event_id),
        payload_schema="bid.plan.committed.v1.payload",
        payload={
            "plan_revision_id": str(plan.id),
            "run_id": str(run.id),
            "revision_no": int(plan.revision_no),
            "validated_hash": str(plan.validated_hash),
            "task_count": len(task_by_key),
            "ready_task_count": len(ready_tasks),
            "task_registry_version": registry.version,
            "validator_version": PLAN_VALIDATOR_VERSION,
            "resource_version": int(plan.row_version),
        },
        dedupe_key=f"plan-committed:{plan.id}:{plan.row_version}",
        occurred_at=committed_at,
    )

    for task in sorted(ready_tasks, key=lambda item: (int(item.priority), str(item.task_key))):
        run.row_version = int(run.row_version) + 1
        db.flush()
        append_outbox_event(
            db,
            event_type="bid.task.ready.v1",
            producer=PLAN_COMMIT_CONSUMER,
            aggregate_type="task",
            aggregate_id=str(task.id),
            aggregate_version=int(task.row_version),
            assessment_id=str(run.assessment_id),
            run_id=str(run.id),
            request_id=str(source_event.request_id),
            causation_event_id=str(plan_event.event_id),
            payload_schema="bid.task.ready.v1.payload",
            payload={
                "task_id": str(task.id),
                "task_key": str(task.task_key),
                "task_type": str(task.task_type),
                "run_id": str(run.id),
                "plan_revision_id": str(plan.id),
                "stage_code": str(run.current_stage),
                "status": "ready",
                "message": "首个确定性计划任务已进入就绪队列",
                "completed_units": 0,
                "total_units": len(task_by_key),
                "resource_version": int(run.row_version),
            },
            dedupe_key=f"task-ready:{task.id}:{task.row_version}",
            occurred_at=committed_at,
        )

    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{PLAN_COMMIT_CONSUMER}",
        action="plan.commit",
        entity_type="plan_revision",
        entity_id=str(plan.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(source_event.request_id),
        correlation_id=str(plan_event.event_id),
        before=previous_run,
        after={
            "run_status": str(run.status),
            "current_stage": str(run.current_stage),
            "run_row_version": int(run.row_version),
            "plan_status": str(plan.status),
            "plan_revision_no": int(plan.revision_no),
            "validated_hash": str(plan.validated_hash),
            "task_count": len(task_by_key),
            "ready_task_count": len(ready_tasks),
        },
        metadata={
            "source_event_id": str(source_event.event_id),
            "generator_version": PLANNER_GENERATOR_VERSION,
            "validator_version": PLAN_VALIDATOR_VERSION,
            "task_registry_version": registry.version,
            "task_registry_hash": registry.registry_hash,
            "planner_input_hash": validation.planner_input_hash,
            "proposal_hash": validation.proposal_hash,
            "validation_checks": list(validation.checks),
        },
        occurred_at=committed_at,
    )
    db.flush()
    return {
        "ignored": False,
        "committed": True,
        "plan_revision_id": str(plan.id),
        "task_count": len(task_by_key),
        "ready_task_count": len(ready_tasks),
        "plan_event_id": str(plan_event.event_id),
        "result_ref": f"plan_revision:{plan.id}",
    }


def consume_run_created_event(
    db: Session,
    *,
    event_id: str,
    committed_at: datetime | None = None,
    phase4_plan_continuation: bool = False,
) -> ProcessedEventResult:
    """Consume one Run-created event exactly once and commit its initial Plan."""

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != RUN_CREATED_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = dict(event.payload_json or {})
        missing = [field for field in RUN_CREATED_REQUIRED_FIELDS if payload.get(field) is None]
        if missing:
            raise BidRunCreatedEventInvalid(
                f"BID_RUN_CREATED_EVENT_PAYLOAD_MISSING:{','.join(missing)}"
            )
        if (
            str(event.aggregate_type) != "run"
            or str(event.aggregate_id) != str(payload["run_id"])
            or str(event.run_id or "") != str(payload["run_id"])
            or str(event.assessment_id or "") != str(payload["assessment_id"])
        ):
            raise BidRunCreatedEventInvalid("BID_RUN_CREATED_EVENT_MISMATCH")
        resource_version = payload["resource_version"]
        run_sequence = payload["run_sequence"]
        if (
            isinstance(resource_version, bool)
            or not isinstance(resource_version, int)
            or resource_version < 1
            or int(event.aggregate_version) != resource_version
            or isinstance(run_sequence, bool)
            or not isinstance(run_sequence, int)
            or run_sequence < 1
            or re.fullmatch(r"[a-f0-9]{64}", str(payload["input_fingerprint"])) is None
            or re.fullmatch(r"[a-f0-9]{64}", str(payload["input_hash"])) is None
        ):
            raise BidRunCreatedEventInvalid("BID_RUN_CREATED_EVENT_VERSION_INVALID")

        run = (
            session.query(BidAnalysisRun)
            .filter(BidAnalysisRun.id == str(payload["run_id"]))
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            raise BidRunCreatedEventInvalid("BID_RUN_CREATED_RUN_NOT_FOUND")
        frozen_event_values = {
            "assessment_id": str(payload["assessment_id"]),
            "scope_id": str(payload["scope_id"]),
            "manifest_id": str(payload["manifest_id"]),
            "run_kind": str(payload["run_kind"]),
            "run_sequence": run_sequence,
            "input_fingerprint": str(payload["input_fingerprint"]),
            "input_hash": str(payload["input_hash"]),
        }
        frozen_run_values = {
            "assessment_id": str(run.assessment_id),
            "scope_id": str(run.scope_id),
            "manifest_id": str(run.manifest_id),
            "run_kind": str(run.run_kind),
            "run_sequence": int(run.run_sequence),
            "input_fingerprint": str(run.input_fingerprint),
            "input_hash": str(run.input_hash),
        }
        if frozen_event_values != frozen_run_values:
            raise BidRunCreatedEventInvalid("BID_RUN_CREATED_FROZEN_INPUT_MISMATCH")
        commit_time = as_utc(committed_at) if committed_at else database_utc_now(session)
        if phase4_plan_continuation:
            from app.services.bid_plan_continuation import (
                PHASE4_SUPPORTED_RUN_KINDS,
                commit_initial_phase4_plan,
            )

            if str(run.run_kind) in PHASE4_SUPPORTED_RUN_KINDS:
                return commit_initial_phase4_plan(
                    session,
                    run=run,
                    source_event=event,
                    committed_at=commit_time,
                )
        return _commit_initial_plan(
            session,
            run=run,
            source_event=event,
            committed_at=commit_time,
        )

    return process_outbox_event_once(
        db,
        consumer_name=PLAN_COMMIT_CONSUMER,
        event_id=event_id,
        handler=_handler,
        processed_at=as_utc(committed_at) if committed_at else None,
    )


def pending_run_created_event_ids(db: Session, *, limit: int = 20) -> list[str]:
    rows = (
        db.query(BidOutboxEvent.event_id)
        .outerjoin(
            BidProcessedEvent,
            and_(
                BidProcessedEvent.event_id == BidOutboxEvent.event_id,
                BidProcessedEvent.consumer_name == PLAN_COMMIT_CONSUMER,
            ),
        )
        .filter(
            BidOutboxEvent.event_type == RUN_CREATED_EVENT,
            BidProcessedEvent.event_id.is_(None),
        )
        .order_by(BidOutboxEvent.occurred_at.asc(), BidOutboxEvent.event_id.asc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [str(row[0]) for row in rows]


def process_pending_plan_commits(
    *,
    session_factory: Callable[[], Session],
    limit: int = 20,
    phase4_plan_continuation: bool = False,
) -> PlanCommitBatchResult:
    index_db = session_factory()
    try:
        event_ids = pending_run_created_event_ids(index_db, limit=limit)
    finally:
        index_db.close()

    committed = duplicate = ignored = failed = 0
    for event_id in event_ids:
        event_db = session_factory()
        try:
            with event_db.begin():
                result = consume_run_created_event(
                    event_db,
                    event_id=event_id,
                    phase4_plan_continuation=phase4_plan_continuation,
                )
            if result.duplicate:
                duplicate += 1
            elif isinstance(result.value, dict) and result.value.get("committed"):
                committed += 1
            else:
                ignored += 1
        except Exception:
            logger.exception(
                "bid_plan_commit_pending_event_failed",
                extra={"event_id": event_id},
            )
            failed += 1
        finally:
            event_db.close()
    return PlanCommitBatchResult(
        scanned=len(event_ids),
        committed=committed,
        duplicate=duplicate,
        ignored=ignored,
        failed=failed,
    )
