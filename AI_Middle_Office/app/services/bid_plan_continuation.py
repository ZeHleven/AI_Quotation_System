"""Phase 4A-1 deterministic Plan Continuation for preliminary Runs.

This layer selects one frozen P0-P4 stage template, delegates the existing
DAG validation gate, and atomically supersedes the old current Plan while
committing the next revision.  It executes no model, OCR, MCP, Tool Adapter,
or object-storage operation.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import BidAssessment
from app.models.bid_assessment_eventing import BidOutboxEvent, BidProcessedEvent
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
    process_outbox_event_once,
)
from app.services.bid_plan_commit import (
    READY_DEPENDENCY_STATES,
    BidPlanCommitError,
    BidPlanValidationError,
    _existing_task_rows,
    build_planner_input,
    validate_plan_proposal,
)
from app.services.bid_run_bootstrap import database_utc_now
from app.services.bid_skill_registry import SkillCatalog, load_skill_catalog
from app.services.bid_task_registry import StandardTaskRegistry, load_standard_task_registry


PLAN_CONTINUATION_EVENT = "bid.plan.continuation_requested.v1"
PLAN_CONTINUATION_CONSUMER = "bid-plan-continuation-v1"
PLAN_CONTINUATION_PRODUCER = "bid-plan-continuation-v1"
PHASE4_PLAN_ENVELOPE_SCHEMA = "bid.plan.commit.envelope.v2"
PHASE4_PLANNER_GENERATOR_VERSION = "bid-deterministic-stage-planner-v2"
PHASE4_PLAN_VALIDATOR_VERSION = "bid-plan-validator-v2"
PHASE4_TASK_CATALOG_REF = "task-catalog-1.0.0-draft.1.json"
PHASE4C_TASK_CATALOG_REF = "task-catalog-1.1.0-phase4c1.json"
PHASE4C_SKILL_CATALOG_REF = "catalog-1.1.0.json"
PHASE4_INITIAL_STAGE = "P0"
PHASE4_FINAL_STAGE = "P4"
PHASE4_SUPPORTED_RUN_KINDS = frozenset({"preliminary", "reanalysis"})
RUN_TERMINAL_STATES = frozenset({"succeeded", "stale", "cancelled"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanStage:
    code: str
    runtime_stage: str
    final: bool
    definitions: tuple[tuple[str, str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class PlanContinuationBatchResult:
    scanned: int
    committed: int
    duplicate: int
    ignored: int
    failed: int


PHASE4C_STAGES: tuple[PlanStage, ...] = (
    PlanStage(
        code="P0",
        runtime_stage="fact_baseline",
        final=False,
        definitions=(
            ("mvp.p0.01.bind_assessment_snapshot", "bind_assessment_snapshot", ()),
            ("mvp.p0.02.inventory_documents", "inventory_documents", ("mvp.p0.01.bind_assessment_snapshot",)),
            ("mvp.p0.03.build_coverage_baseline", "build_coverage_baseline", ("mvp.p0.02.inventory_documents",)),
            ("mvp.p0.04.extract_tender_overview", "extract_tender_overview", ("mvp.p0.03.build_coverage_baseline",)),
            ("mvp.p0.05.extract_critical_dates", "extract_critical_dates", ("mvp.p0.03.build_coverage_baseline",)),
            ("mvp.p0.06.extract_qualification_requirements", "extract_qualification_requirements", ("mvp.p0.03.build_coverage_baseline",)),
            ("mvp.p0.07.extract_rejection_clauses", "extract_rejection_clauses", ("mvp.p0.03.build_coverage_baseline",)),
            ("mvp.p0.08.extract_guarantees_and_fees", "extract_guarantees_and_fees", ("mvp.p0.03.build_coverage_baseline",)),
        ),
    ),
    PlanStage(
        code="P1",
        runtime_stage="fact_completion",
        final=False,
        definitions=(
            ("mvp.p1.01.extract_evaluation_method", "extract_evaluation_method", ("mvp.p0.08.extract_guarantees_and_fees",)),
            ("mvp.p1.02.extract_scope_and_quantities", "extract_scope_and_quantities", ("mvp.p0.08.extract_guarantees_and_fees",)),
            ("mvp.p1.03.extract_deliverables_and_samples", "extract_deliverables_and_samples", ("mvp.p0.08.extract_guarantees_and_fees",)),
            ("mvp.p1.04.extract_contract_terms", "extract_contract_terms", ("mvp.p0.08.extract_guarantees_and_fees",)),
            ("mvp.p1.05.extract_schedule_and_site_constraints", "extract_schedule_and_site_constraints", ("mvp.p0.08.extract_guarantees_and_fees",)),
            (
                "mvp.p1.06.build_enterprise_snapshot",
                "build_enterprise_snapshot",
                ("mvp.p0.08.extract_guarantees_and_fees",),
            ),
            (
                "mvp.p1.07.resolve_fact_conflicts",
                "resolve_fact_conflicts",
                (
                    "mvp.p0.04.extract_tender_overview",
                    "mvp.p0.05.extract_critical_dates",
                    "mvp.p0.06.extract_qualification_requirements",
                    "mvp.p0.07.extract_rejection_clauses",
                    "mvp.p0.08.extract_guarantees_and_fees",
                    "mvp.p1.01.extract_evaluation_method",
                    "mvp.p1.02.extract_scope_and_quantities",
                    "mvp.p1.03.extract_deliverables_and_samples",
                    "mvp.p1.04.extract_contract_terms",
                    "mvp.p1.05.extract_schedule_and_site_constraints",
                    "mvp.p1.06.build_enterprise_snapshot",
                ),
            ),
        ),
    ),
    PlanStage(
        code="P2",
        runtime_stage="preliminary_gates",
        final=False,
        definitions=tuple(
            (f"mvp.p2.{index:02d}.{task_type}", task_type, ("mvp.p1.07.resolve_fact_conflicts",))
            for index, task_type in enumerate(
                (
                    "evaluate_deadline_gate",
                    "evaluate_qualification_gate",
                    "evaluate_personnel_performance_gate",
                    "evaluate_legal_compliance_gate",
                    "evaluate_guarantee_cash_gate",
                    "evaluate_minimum_bid_capacity_gate",
                    "evaluate_enterprise_prohibited_risk_gate",
                ),
                start=1,
            )
        ),
    ),
    PlanStage(
        code="P3",
        runtime_stage="preliminary_synthesis",
        final=False,
        definitions=(
            (
                "mvp.p3.01.synthesize_assessment",
                "synthesize_assessment",
                tuple(f"mvp.p2.{index:02d}.{task_type}" for index, task_type in enumerate(
                    (
                        "evaluate_deadline_gate",
                        "evaluate_qualification_gate",
                        "evaluate_personnel_performance_gate",
                        "evaluate_legal_compliance_gate",
                        "evaluate_guarantee_cash_gate",
                        "evaluate_minimum_bid_capacity_gate",
                        "evaluate_enterprise_prohibited_risk_gate",
                    ),
                    start=1,
                )),
            ),
            ("mvp.p3.02.evaluate_final_decision", "evaluate_final_decision", ("mvp.p3.01.synthesize_assessment",)),
            ("mvp.p3.03.validate_claim_evidence", "validate_claim_evidence", ("mvp.p3.01.synthesize_assessment",)),
            (
                "mvp.p3.04.validate_report_consistency",
                "validate_report_consistency",
                ("mvp.p3.02.evaluate_final_decision", "mvp.p3.03.validate_claim_evidence"),
            ),
        ),
    ),
    PlanStage(
        code="P4",
        runtime_stage="preliminary_report",
        final=True,
        definitions=(
            (
                "mvp.p4.01.generate_preliminary_report",
                "generate_preliminary_report",
                ("mvp.p3.04.validate_report_consistency",),
            ),
        ),
    ),
)

LEGACY_P1 = PlanStage(
    code="P1",
    runtime_stage="fact_completion",
    final=False,
    definitions=tuple(
        definition
        for definition in next(stage for stage in PHASE4C_STAGES if stage.code == "P1").definitions
        if definition[1] != "build_enterprise_snapshot"
    )[:-1]
    + (
        (
            "mvp.p1.06.resolve_fact_conflicts",
            "resolve_fact_conflicts",
            tuple(
                dependency
                for dependency in next(stage for stage in PHASE4C_STAGES if stage.code == "P1").definitions[-1][2]
                if dependency != "mvp.p1.06.build_enterprise_snapshot"
            ),
        ),
    ),
)
LEGACY_P2 = PlanStage(
    code="P2",
    runtime_stage="preliminary_gates",
    final=False,
    definitions=tuple(
        (task_key, task_type, ("mvp.p1.06.resolve_fact_conflicts",))
        for task_key, task_type, _depends_on in next(
            stage for stage in PHASE4C_STAGES if stage.code == "P2"
        ).definitions
    ),
)
STAGES: tuple[PlanStage, ...] = tuple(
    LEGACY_P1 if stage.code == "P1" else LEGACY_P2 if stage.code == "P2" else stage
    for stage in PHASE4C_STAGES
)
STAGE_BY_CODE = {stage.code: stage for stage in STAGES}
PHASE4C_STAGE_BY_CODE = {stage.code: stage for stage in PHASE4C_STAGES}
NEXT_STAGE = {STAGES[index].code: STAGES[index + 1].code for index in range(len(STAGES) - 1)}


def _stage_map() -> dict[str, PlanStage]:
    return (
        PHASE4C_STAGE_BY_CODE
        if settings.feature_bid_assessment_phase4_enterprise_capability
        else STAGE_BY_CODE
    )


def _active_skill_catalog() -> SkillCatalog:
    return load_skill_catalog(
        PHASE4C_SKILL_CATALOG_REF
        if settings.feature_bid_assessment_phase4_enterprise_capability
        else "catalog-1.0.0.json"
    )


def _active_task_registry() -> StandardTaskRegistry:
    return load_standard_task_registry(
        PHASE4C_TASK_CATALOG_REF
        if settings.feature_bid_assessment_phase4_enterprise_capability
        else PHASE4_TASK_CATALOG_REF
    )


class BidPlanContinuationError(BidPlanCommitError):
    code = "BID_PLAN_CONTINUATION_ERROR"


class BidPlanContinuationEventInvalid(BidPlanContinuationError):
    code = "BID_PLAN_CONTINUATION_EVENT_INVALID"


def build_stage_plan_proposal(
    planner_input: dict[str, Any],
    *,
    stage_code: str,
    registry: StandardTaskRegistry | None = None,
    skill_catalog: SkillCatalog | None = None,
) -> dict[str, Any]:
    stage = _stage_map().get(str(stage_code))
    if stage is None:
        raise BidPlanValidationError(f"stage_invalid:{stage_code}")
    task_registry = registry or _active_task_registry()
    catalog = skill_catalog or _active_skill_catalog()
    if str(planner_input.get("run_kind") or "") not in PHASE4_SUPPORTED_RUN_KINDS:
        raise BidPlanValidationError("stage_run_kind_invalid")
    planner_input_hash = canonical_hash(planner_input)
    return {
        "proposal_id": f"planp_{stage.code.lower()}_{planner_input_hash[:28]}",
        "reason_codes": [
            "INITIAL_RUN_PLAN" if stage.code == PHASE4_INITIAL_STAGE else "CONTROLLED_PLAN_CONTINUATION",
            f"STAGE_{stage.code}",
            "SKILL_BINDINGS_FROZEN",
        ],
        "add_tasks": [
            task_registry.policies[task_type].definition(
                task_key=task_key,
                depends_on=depends_on,
                skill_binding=catalog.binding_for_task(task_type).as_dict(),
                freeze_allowed_tools=True,
            )
            for task_key, task_type, depends_on in stage.definitions
        ],
        "supersede_tasks": [],
        "questions": [],
        "expected_stage_after": stage.runtime_stage,
        "planner_confidence": "high",
    }


def _validate_skill_frozen_proposal(
    *,
    proposal: dict[str, Any],
    registry: StandardTaskRegistry,
    skill_catalog: SkillCatalog,
) -> None:
    violations: list[str] = []
    for definition in proposal.get("add_tasks") or []:
        if not isinstance(definition, dict):
            violations.append("task_definition_shape")
            continue
        task_type = str(definition.get("task_type") or "")
        policy = registry.policies.get(task_type)
        try:
            expected_binding = skill_catalog.binding_for_task(task_type).as_dict()
        except RuntimeError:
            violations.append(f"skill_binding_missing:{task_type}")
            continue
        if definition.get("skill_binding") != expected_binding:
            violations.append(f"skill_binding_hash:{task_type}")
        if definition.get("allowed_tools") != list(policy.allowed_tools if policy else ()):
            violations.append(f"skill_allowed_tools:{task_type}")
        if expected_binding["output_schema"] != str(definition.get("completion_contract") or ""):
            violations.append(f"skill_output_schema:{task_type}")
    if violations:
        raise BidPlanValidationError(*violations)


def _validate_stage_proposal(
    *,
    run: BidAnalysisRun,
    planner_input: dict[str, Any],
    proposal: dict[str, Any],
    stage: PlanStage,
    registry: StandardTaskRegistry,
    skill_catalog: SkillCatalog,
    existing_tasks: list[BidTask],
    dependencies: list[BidTaskDependency],
):
    if stage.code != PHASE4_INITIAL_STAGE:
        bad_roots = [
            str(definition.get("task_key") or "")
            for definition in proposal.get("add_tasks") or []
            if not list(definition.get("depends_on") or [])
        ]
        if bad_roots:
            raise BidPlanValidationError(
                *[f"stage_dependency_root_missing:{task_key}" for task_key in bad_roots]
            )
    _validate_skill_frozen_proposal(
        proposal=proposal,
        registry=registry,
        skill_catalog=skill_catalog,
    )
    stripped = {
        **proposal,
        "add_tasks": [
            {
                key: value
                for key, value in definition.items()
                if key not in {"skill_binding", "allowed_tools"}
            }
            for definition in proposal["add_tasks"]
        ],
    }
    validation = validate_plan_proposal(
        planner_input,
        stripped,
        run_input_hash=str(run.input_hash),
        registry=registry,
        existing_tasks=existing_tasks,
        existing_dependencies=(
            (str(row.task_id), str(row.depends_on_task_id)) for row in dependencies
        ),
    )
    normalized = dict(validation.normalized_proposal)
    normalized["add_tasks"] = proposal["add_tasks"]
    validated_hash = canonical_hash(
        {
            "schema": PHASE4_PLAN_ENVELOPE_SCHEMA,
            "generator_version": PHASE4_PLANNER_GENERATOR_VERSION,
            "validator_version": PHASE4_PLAN_VALIDATOR_VERSION,
            "task_registry_version": registry.version,
            "task_registry_hash": registry.registry_hash,
            "task_catalog_ref": registry.catalog_ref,
            "skill_catalog_ref": skill_catalog.catalog_ref,
            "skill_catalog_version": skill_catalog.version,
            "skill_catalog_hash": skill_catalog.catalog_hash,
            "stage": stage.code,
            "final_stage": stage.final,
            "run_input_hash": str(run.input_hash),
            "planner_input_hash": validation.planner_input_hash,
            "proposal_hash": canonical_hash(normalized),
        }
    )
    return validation, normalized, validated_hash


def _phase4_envelope(
    *,
    run: BidAnalysisRun,
    stage: PlanStage,
    planner_input: dict[str, Any],
    normalized_proposal: dict[str, Any],
    validation: Any,
    validated_hash: str,
    registry: StandardTaskRegistry,
    skill_catalog: SkillCatalog,
) -> dict[str, Any]:
    return {
        "schema": PHASE4_PLAN_ENVELOPE_SCHEMA,
        "generator_version": PHASE4_PLANNER_GENERATOR_VERSION,
        "validator_version": PHASE4_PLAN_VALIDATOR_VERSION,
        "task_registry_version": registry.version,
        "task_registry_hash": registry.registry_hash,
        "task_catalog_ref": registry.catalog_ref,
        "skill_catalog_ref": skill_catalog.catalog_ref,
        "skill_catalog_version": skill_catalog.version,
        "skill_catalog_hash": skill_catalog.catalog_hash,
        "stage": stage.code,
        "final_stage": stage.final,
        "run_input_hash": str(run.input_hash),
        "planner_input_hash": validation.planner_input_hash,
        "proposal_hash": canonical_hash(normalized_proposal),
        "planner_input": planner_input,
        "proposal": normalized_proposal,
        "validation": {
            "status": "accepted",
            "checks": [*validation.checks, "skill_binding_hash", "stage_sequence"],
            "validated_hash": validated_hash,
        },
    }


def _current_plan(db: Session, run_id: str) -> BidPlanRevision | None:
    return (
        db.query(BidPlanRevision)
        .filter(
            BidPlanRevision.run_id == run_id,
            BidPlanRevision.status == "committed",
            BidPlanRevision.committed_slot_key == "committed",
        )
        .with_for_update()
        .one_or_none()
    )


def _plan_stage(plan: BidPlanRevision) -> str | None:
    envelope = dict(plan.proposal_json or {})
    if str(envelope.get("schema") or "") != PHASE4_PLAN_ENVELOPE_SCHEMA:
        return None
    stage = str(envelope.get("stage") or "")
    return stage if stage in _stage_map() else None


def _commit_stage(
    db: Session,
    *,
    run: BidAnalysisRun,
    source_event: BidOutboxEvent,
    stage_code: str,
    committed_at: datetime,
    previous_plan: BidPlanRevision | None,
) -> dict[str, Any]:
    if str(run.run_kind) not in PHASE4_SUPPORTED_RUN_KINDS:
        return {"ignored": True, "reason": "run_kind_not_supported"}
    if run.cancel_requested_at is not None:
        return {"ignored": True, "reason": "run_cancel_requested"}
    if str(run.status) in RUN_TERMINAL_STATES or (
        str(run.status) == "failed" and not bool(run.retryable)
    ):
        return {"ignored": True, "reason": "run_terminal"}
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == run.assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidPlanContinuationError("BID_ASSESSMENT_NOT_FOUND")
    if str(assessment.lifecycle_status) != "active":
        return {"ignored": True, "reason": "assessment_not_active"}
    if str(assessment.active_run_id or "") != str(run.id):
        return {"ignored": True, "reason": "run_no_longer_active"}

    stage = _stage_map().get(stage_code)
    if stage is None:
        raise BidPlanValidationError(f"stage_invalid:{stage_code}")
    if previous_plan is None:
        if stage.code != PHASE4_INITIAL_STAGE or str(run.status) not in {"created", "planning"}:
            raise BidPlanContinuationError("BID_PLAN_INITIAL_STAGE_INVALID")
        if str(run.status) == "created":
            run.status = "planning"
            run.current_stage = "planning"
            run.last_checkpoint_at = committed_at
            run.row_version = int(run.row_version) + 1
            db.flush()
    else:
        previous_stage = _plan_stage(previous_plan)
        if previous_stage is None or NEXT_STAGE.get(previous_stage) != stage.code:
            raise BidPlanContinuationError("BID_PLAN_STAGE_SEQUENCE_INVALID")
        if str(run.status) != "planning":
            raise BidPlanContinuationError("BID_PLAN_CONTINUATION_RUN_STATE_INVALID")

    registry = _active_task_registry()
    skill_catalog = _active_skill_catalog()
    existing_tasks = _existing_task_rows(db, run_id=str(run.id))
    dependency_rows = (
        db.query(BidTaskDependency)
        .filter(BidTaskDependency.run_id == run.id)
        .all()
    )
    planner_input = build_planner_input(db, run, registry=registry)
    proposal = build_stage_plan_proposal(
        planner_input,
        stage_code=stage.code,
        registry=registry,
        skill_catalog=skill_catalog,
    )
    validation, normalized_proposal, validated_hash = _validate_stage_proposal(
        run=run,
        planner_input=planner_input,
        proposal=proposal,
        stage=stage,
        registry=registry,
        skill_catalog=skill_catalog,
        existing_tasks=existing_tasks,
        dependencies=dependency_rows,
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
        proposal_json=_phase4_envelope(
            run=run,
            stage=stage,
            planner_input=planner_input,
            normalized_proposal=normalized_proposal,
            validation=validation,
            validated_hash=validated_hash,
            registry=registry,
            skill_catalog=skill_catalog,
        ),
        validated_hash=validated_hash,
        status="committed",
        committed_slot_key="committed",
        committed_at=committed_at,
        row_version=3,
    )
    if previous_plan is not None:
        previous_plan.status = "superseded"
        previous_plan.committed_slot_key = None
        previous_plan.superseded_at = committed_at
        previous_plan.row_version = int(previous_plan.row_version) + 1
        db.flush()
    db.add(plan)
    db.flush()

    existing_by_key = {str(task.task_key): task for task in existing_tasks}
    task_by_key: dict[str, BidTask] = {}
    for definition in normalized_proposal["add_tasks"]:
        task_type = str(definition["task_type"])
        policy = registry.policies[task_type]
        dependencies = tuple(str(value) for value in definition["depends_on"])
        dependencies_ready = all(
            dependency in existing_by_key
            and str(existing_by_key[dependency].status) in READY_DEPENDENCY_STATES
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
                {"run_input_hash": str(run.input_hash), "task_definition": definition}
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

    combined_by_key = {**existing_by_key, **task_by_key}
    for definition in normalized_proposal["add_tasks"]:
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

    from_state = str(run.status)
    run.status = "queued"
    run.current_stage = stage.runtime_stage
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
        producer=PLAN_CONTINUATION_PRODUCER,
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
            "validator_version": PHASE4_PLAN_VALIDATOR_VERSION,
            "stage": stage.code,
            "skill_catalog_version": skill_catalog.version,
            "skill_catalog_hash": skill_catalog.catalog_hash,
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
            producer=PLAN_CONTINUATION_PRODUCER,
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
                "message": f"受控计划阶段 {stage.code} 已进入就绪队列",
                "completed_units": sum(
                    1 for row in existing_tasks if str(row.status) in READY_DEPENDENCY_STATES
                ),
                "total_units": len(existing_tasks) + len(task_by_key),
                "resource_version": int(run.row_version),
            },
            dedupe_key=f"task-ready:{task.id}:{task.row_version}",
            occurred_at=committed_at,
        )

    append_audit_log(
        db,
        actor_type="service",
        actor_ref=f"service:{PLAN_CONTINUATION_CONSUMER}",
        action="plan.stage.commit",
        entity_type="plan_revision",
        entity_id=str(plan.id),
        assessment_id=str(run.assessment_id),
        outcome="succeeded",
        request_id=str(source_event.request_id),
        correlation_id=str(plan_event.event_id),
        before={
            "run_status": from_state,
            "previous_plan_revision_id": str(previous_plan.id) if previous_plan else None,
        },
        after={
            "run_status": str(run.status),
            "stage": stage.code,
            "plan_revision_no": int(plan.revision_no),
            "task_count": len(task_by_key),
            "ready_task_count": len(ready_tasks),
        },
        metadata={
            "source_event_id": str(source_event.event_id),
            "skill_catalog_version": skill_catalog.version,
            "skill_catalog_hash": skill_catalog.catalog_hash,
            "task_registry_version": registry.version,
            "task_registry_hash": registry.registry_hash,
        },
        occurred_at=committed_at,
    )
    db.flush()
    return {
        "ignored": False,
        "committed": True,
        "plan_revision_id": str(plan.id),
        "stage": stage.code,
        "final_stage": stage.final,
        "task_count": len(task_by_key),
        "ready_task_count": len(ready_tasks),
        "plan_event_id": str(plan_event.event_id),
        "result_ref": f"plan_revision:{plan.id}",
    }


def commit_initial_phase4_plan(
    db: Session,
    *,
    run: BidAnalysisRun,
    source_event: BidOutboxEvent,
    committed_at: datetime,
) -> dict[str, Any]:
    current = _current_plan(db, str(run.id))
    if current is not None:
        stage = _plan_stage(current)
        if stage is None:
            raise BidPlanContinuationError("BID_PLAN_PROTOCOL_ALREADY_COMMITTED")
        return {
            "ignored": False,
            "committed": False,
            "plan_revision_id": str(current.id),
            "stage": stage,
            "task_count": int(
                db.query(func.count(BidTask.id))
                .filter(BidTask.plan_revision_id == current.id)
                .scalar()
                or 0
            ),
            "result_ref": f"plan_revision:{current.id}",
        }
    return _commit_stage(
        db,
        run=run,
        source_event=source_event,
        stage_code=PHASE4_INITIAL_STAGE,
        committed_at=committed_at,
        previous_plan=None,
    )


def _require_continuation_event(event: BidOutboxEvent) -> dict[str, Any]:
    if str(event.event_type) != PLAN_CONTINUATION_EVENT:
        raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_EVENT_TYPE_INVALID")
    payload = dict(event.payload_json or {})
    required = {
        "run_id",
        "completed_plan_revision_id",
        "completed_stage",
        "next_stage",
        "from",
        "to",
        "stage_code",
        "status",
        "message",
        "completed_units",
        "total_units",
        "resource_version",
    }
    if not required.issubset(payload):
        raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_EVENT_PAYLOAD_INVALID")
    for field, minimum in (
        ("completed_units", 0),
        ("total_units", 1),
        ("resource_version", 1),
    ):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise BidPlanContinuationEventInvalid(
                "BID_PLAN_CONTINUATION_EVENT_PAYLOAD_INVALID"
            )
    if int(payload["completed_units"]) > int(payload["total_units"]):
        raise BidPlanContinuationEventInvalid(
            "BID_PLAN_CONTINUATION_EVENT_PAYLOAD_INVALID"
        )
    if (
        str(event.aggregate_type) != "run"
        or str(event.aggregate_id) != str(payload.get("run_id") or "")
        or str(event.run_id or "") != str(payload.get("run_id") or "")
        or re.fullmatch(r"P[0-4]", str(payload.get("completed_stage") or "")) is None
        or re.fullmatch(r"P[0-4]", str(payload.get("next_stage") or "")) is None
        or str(payload.get("from") or "") != "running"
        or str(payload.get("to") or "") != "planning"
        or str(payload.get("stage_code") or "") != "planning"
        or str(payload.get("status") or "") != "planning"
        or not str(payload.get("message") or "")
    ):
        raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_EVENT_MISMATCH")
    return payload


def consume_plan_continuation_requested_event(
    db: Session,
    *,
    event_id: str,
    committed_at: datetime | None = None,
) -> ProcessedEventResult:
    current_time = as_utc(committed_at) if committed_at else database_utc_now(db)

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != PLAN_CONTINUATION_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = _require_continuation_event(event)
        run = (
            session.query(BidAnalysisRun)
            .filter(BidAnalysisRun.id == str(payload["run_id"]))
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_RUN_NOT_FOUND")
        if str(event.assessment_id or "") != str(run.assessment_id):
            raise BidPlanContinuationEventInvalid(
                "BID_PLAN_CONTINUATION_ASSESSMENT_MISMATCH"
            )
        event_version = int(payload["resource_version"])
        run_version = int(run.row_version)
        if event_version > run_version:
            raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_VERSION_AHEAD")
        if event_version < run_version:
            if (
                run.cancel_requested_at is not None
                or str(run.status) in RUN_TERMINAL_STATES
                or (str(run.status) == "failed" and not bool(run.retryable))
            ):
                return {
                    "ignored": True,
                    "reason": "run_fenced_after_continuation_request",
                }
            raise BidPlanContinuationEventInvalid(
                "BID_PLAN_CONTINUATION_VERSION_MISMATCH"
            )
        current = _current_plan(session, str(run.id))
        if current is None:
            completed = (
                session.query(BidPlanRevision)
                .filter(
                    BidPlanRevision.id == str(payload["completed_plan_revision_id"]),
                    BidPlanRevision.run_id == run.id,
                    BidPlanRevision.status == "superseded",
                )
                .one_or_none()
            )
            if completed is not None:
                next_plan = (
                    session.query(BidPlanRevision)
                    .filter(
                        BidPlanRevision.run_id == run.id,
                        BidPlanRevision.revision_no == int(completed.revision_no) + 1,
                    )
                    .one_or_none()
                )
                if next_plan is not None:
                    return {
                        "ignored": False,
                        "committed": False,
                        "plan_revision_id": str(next_plan.id),
                        "stage": _plan_stage(next_plan),
                        "result_ref": f"plan_revision:{next_plan.id}",
                    }
            raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_CURRENT_PLAN_MISSING")
        current_stage = _plan_stage(current)
        if (
            str(current.id) != str(payload["completed_plan_revision_id"])
            or current_stage != str(payload["completed_stage"])
            or NEXT_STAGE.get(str(payload["completed_stage"])) != str(payload["next_stage"])
        ):
            raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_STAGE_MISMATCH")
        incomplete = int(
            session.query(func.count(BidTask.id))
            .filter(
                BidTask.run_id == run.id,
                BidTask.plan_revision_id == current.id,
                ~BidTask.status.in_(tuple(READY_DEPENDENCY_STATES)),
            )
            .scalar()
            or 0
        )
        if incomplete:
            raise BidPlanContinuationEventInvalid("BID_PLAN_CONTINUATION_STAGE_INCOMPLETE")
        total_tasks = int(
            session.query(func.count(BidTask.id))
            .filter(BidTask.run_id == run.id)
            .scalar()
            or 0
        )
        completed_tasks = int(
            session.query(func.count(BidTask.id))
            .filter(
                BidTask.run_id == run.id,
                BidTask.status.in_(tuple(READY_DEPENDENCY_STATES)),
            )
            .scalar()
            or 0
        )
        if (
            int(payload["total_units"]) != total_tasks
            or int(payload["completed_units"]) != completed_tasks
        ):
            raise BidPlanContinuationEventInvalid(
                "BID_PLAN_CONTINUATION_PROGRESS_MISMATCH"
            )
        return _commit_stage(
            session,
            run=run,
            source_event=event,
            stage_code=str(payload["next_stage"]),
            committed_at=current_time,
            previous_plan=current,
        )

    return process_outbox_event_once(
        db,
        consumer_name=PLAN_CONTINUATION_CONSUMER,
        event_id=str(event_id),
        handler=_handler,
        processed_at=current_time,
    )


def pending_plan_continuation_event_ids(db: Session, *, limit: int = 20) -> list[str]:
    rows = (
        db.query(BidOutboxEvent.event_id)
        .outerjoin(
            BidProcessedEvent,
            and_(
                BidProcessedEvent.event_id == BidOutboxEvent.event_id,
                BidProcessedEvent.consumer_name == PLAN_CONTINUATION_CONSUMER,
            ),
        )
        .filter(
            BidOutboxEvent.event_type == PLAN_CONTINUATION_EVENT,
            BidProcessedEvent.event_id.is_(None),
        )
        .order_by(BidOutboxEvent.occurred_at.asc(), BidOutboxEvent.event_id.asc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [str(row[0]) for row in rows]


def process_pending_plan_continuations(
    *,
    session_factory: Callable[[], Session],
    limit: int = 20,
) -> PlanContinuationBatchResult:
    index_db = session_factory()
    try:
        event_ids = pending_plan_continuation_event_ids(index_db, limit=limit)
    finally:
        index_db.close()
    committed = duplicate = ignored = failed = 0
    for event_id in event_ids:
        event_db = session_factory()
        try:
            with event_db.begin():
                result = consume_plan_continuation_requested_event(event_db, event_id=event_id)
            if result.duplicate:
                duplicate += 1
            elif isinstance(result.value, dict) and result.value.get("committed"):
                committed += 1
            else:
                ignored += 1
        except Exception:
            logger.exception(
                "bid_plan_continuation_pending_event_failed",
                extra={"event_id": event_id},
            )
            failed += 1
        finally:
            event_db.close()
    return PlanContinuationBatchResult(
        scanned=len(event_ids),
        committed=committed,
        duplicate=duplicate,
        ignored=ignored,
        failed=failed,
    )
