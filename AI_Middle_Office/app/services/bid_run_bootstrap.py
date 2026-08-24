"""Phase 3A frozen-input selection and durable Run bootstrap.

The service deliberately stops at ``BidAnalysisRun`` creation.  It does not
invoke a planner, model, tool, parser, or mutable enterprise business table.
All callers must provide an already-bound Assessment Scope and the selector
only accepts an already-frozen enterprise snapshot plus reviewed active
configuration artifacts.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
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
from app.models.bid_assessment_eventing import (
    BidOutboxEvent,
    BidProcessedEvent,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidencePackage,
    BidHardGateComparisonBaseline,
)
from app.services.bid_assessment_eventing import (
    ProcessedEventResult,
    append_audit_log,
    append_outbox_event,
    as_utc,
    canonical_hash,
    process_outbox_event_once,
)
from app.services.bid_assessment_idempotency import IdempotentCommandResult
from app.services.bid_assessment_snapshots import assessment_etag
from app.services.bid_parse_quality_gate import (
    PDF_RQ1B_PARSER_PROFILE_VERSION,
    BidParseQualityGateBlocked,
    BidParseQualityGateError,
    assert_parse_run_consumer_allowed,
)


logger = logging.getLogger(__name__)

PLAN_REQUESTED_EVENT = "bid.plan.requested.v1"
RUN_BOOTSTRAP_CONSUMER = "bid-run-bootstrap-v1"
RUN_CREATE_ROUTE_TEMPLATE = "/api/v1/bid-assessments/{assessment_id}/runs"
RUN_KINDS = frozenset({"preliminary", "deep", "reanalysis"})

NON_TERMINAL_RUN_STATES = frozenset(
    {
        "created",
        "planning",
        "queued",
        "running",
        "waiting_input",
        "waiting_operation",
        "validating",
    }
)

PLAN_REQUESTED_REQUIRED_FIELDS = (
    "operation_id",
    "assessment_id",
    "scope_id",
    "manifest_id",
    "lot_id",
    "requested_run_kind",
    "resource_version",
)


class BidRunBootstrapError(RuntimeError):
    code = "BID_RUN_BOOTSTRAP_ERROR"


class BidRunNotFound(BidRunBootstrapError):
    code = "BID_RESOURCE_NOT_FOUND"


class BidRunVersionMismatch(BidRunBootstrapError):
    code = "BID_RESOURCE_VERSION_MISMATCH"

    def __init__(self, assessment: BidAssessment, *, provided_etag: str):
        super().__init__(self.code)
        self.assessment_id = str(assessment.id)
        self.provided_etag = str(provided_etag)
        self.current_row_version = int(assessment.row_version)
        self.current_etag = assessment_etag(
            self.assessment_id,
            self.current_row_version,
        )


class BidRunInputNotReady(BidRunBootstrapError):
    code = "BID_RUN_INPUT_NOT_READY"

    def __init__(self, *reasons: str):
        normalized = tuple(dict.fromkeys(str(reason)[:100] for reason in reasons if reason))
        super().__init__(self.code)
        self.reasons = normalized or ("input_not_ready",)


class BidActiveRunExists(BidRunBootstrapError):
    code = "BID_ACTIVE_RUN_EXISTS"

    def __init__(self, run: BidAnalysisRun):
        super().__init__(self.code)
        self.run_id = str(run.id)
        self.status = str(run.status)


class BidRunAlreadyExistsForInput(BidRunBootstrapError):
    code = "BID_RUN_ALREADY_EXISTS_FOR_INPUT"

    def __init__(self, run: BidAnalysisRun):
        super().__init__(self.code)
        self.run_id = str(run.id)
        self.status = str(run.status)


class BidPlanRequestedEventInvalid(BidRunBootstrapError):
    code = "BID_PLAN_REQUESTED_EVENT_INVALID"


@dataclass(frozen=True)
class FrozenRunInputs:
    assessment: BidAssessment
    scope: BidAssessmentScope
    manifest: BidDocumentManifest
    enterprise_snapshot: BidEnterpriseSnapshot
    business_baseline: BidEnterpriseBusinessBaseline | None
    hard_gate_comparison_baseline: BidHardGateComparisonBaseline | None
    rule_set: BidRuleSet
    fact_catalog: BidFactCatalogVersion
    prompt_bundle: BidPromptBundle
    tool_registry: BidToolRegistryVersion
    model_profile: BidModelProfileVersion
    formula_catalog: BidFormulaCatalogVersion
    evaluation_time: datetime
    input_fingerprint: str
    input_hash: str
    fingerprint_payload: dict[str, Any]
    input_payload: dict[str, Any]


@dataclass(frozen=True)
class RunBootstrapResult:
    run: BidAnalysisRun
    created_event_id: str
    assessment_row_version: int


@dataclass(frozen=True)
class RunBootstrapBatchResult:
    scanned: int
    created: int
    duplicate: int
    pending_input: int
    ignored: int
    failed: int


def database_utc_now(db: Session) -> datetime:
    """Read the Run evaluation time from the database transaction clock."""

    if db.get_bind().dialect.name == "mysql":
        value = db.execute(select(func.utc_timestamp(6))).scalar_one()
    else:
        value = db.execute(select(func.current_timestamp())).scalar_one()
    if not isinstance(value, datetime):
        raise BidRunBootstrapError("BID_DATABASE_TIME_INVALID")
    return as_utc(value)


def _active_artifact(db: Session, model: type) -> Any | None:
    return (
        db.query(model)
        .filter(model.status == "active", model.active_slot_key == "active")
        .with_for_update()
        .one_or_none()
    )


def _latest_scope(
    db: Session,
    *,
    assessment_id: str,
) -> BidAssessmentScope | None:
    return (
        db.query(BidAssessmentScope)
        .filter(BidAssessmentScope.assessment_id == assessment_id)
        .order_by(BidAssessmentScope.version.desc(), BidAssessmentScope.id.desc())
        .with_for_update()
        .first()
    )


def _manifest_parse_quality_reasons(
    db: Session,
    *,
    manifest_id: str,
) -> list[str]:
    """Fail closed only for documents explicitly parsed by the RQ1-B profile."""

    rows = (
        db.query(BidManifestDocument, BidDocumentParseRun)
        .outerjoin(
            BidDocumentParseHead,
            BidDocumentParseHead.document_version_id
            == BidManifestDocument.document_version_id,
        )
        .outerjoin(
            BidDocumentParseRun,
            BidDocumentParseRun.id == BidDocumentParseHead.current_run_id,
        )
        .filter(BidManifestDocument.manifest_id == manifest_id)
        .order_by(BidManifestDocument.order_no.asc())
        .all()
    )
    reasons: list[str] = []
    for member, run in rows:
        if (
            run is None
            or str(run.parser_profile_version) != PDF_RQ1B_PARSER_PROFILE_VERSION
        ):
            continue
        version_id = str(member.document_version_id)
        if str(run.status) not in {"succeeded", "partial"}:
            reasons.append(f"parse_quality_gate_not_ready:{version_id}")
            continue
        try:
            assert_parse_run_consumer_allowed(
                run,
                consumer="automated_assessment",
            )
        except BidParseQualityGateBlocked:
            reasons.append(f"parse_quality_gate_blocked_assessment:{version_id}")
        except BidParseQualityGateError:
            reasons.append(f"parse_quality_gate_invalid:{version_id}")
    return reasons


def _load_frozen_inputs(
    db: Session,
    *,
    assessment: BidAssessment,
    scope_id: str,
    manifest_id: str,
    evaluation_time: datetime,
) -> FrozenRunInputs:
    current_time = as_utc(evaluation_time)
    reasons: list[str] = []

    manifest = (
        db.query(BidDocumentManifest)
        .filter(
            BidDocumentManifest.id == manifest_id,
            BidDocumentManifest.assessment_id == assessment.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if manifest is None or str(assessment.current_manifest_id or "") != str(manifest_id):
        reasons.append("current_manifest_not_bound")
    elif manifest is not None:
        reasons.extend(
            _manifest_parse_quality_reasons(
                db,
                manifest_id=str(manifest.id),
            )
        )

    scope = _latest_scope(db, assessment_id=str(assessment.id))
    if scope is None or str(scope.id) != str(scope_id):
        reasons.append("current_scope_not_bound")

    business_baseline = None
    if getattr(settings, "feature_bid_assessment_phase4_business_baseline", False):
        from app.services.bid_enterprise_business_baseline import latest_business_snapshot

        business_pair = latest_business_snapshot(
            db,
            effective_at=current_time,
            lock=True,
        )
        if business_pair is None:
            enterprise_snapshot = None
            reasons.append("enterprise_business_baseline_missing")
        else:
            enterprise_snapshot, business_baseline = business_pair
    else:
        enterprise_snapshot = (
            db.query(BidEnterpriseSnapshot)
            .filter(
                BidEnterpriseSnapshot.status == "frozen",
                BidEnterpriseSnapshot.as_of <= current_time,
                BidEnterpriseSnapshot.frozen_at <= current_time,
            )
            .order_by(
                BidEnterpriseSnapshot.as_of.desc(),
                BidEnterpriseSnapshot.frozen_at.desc(),
                BidEnterpriseSnapshot.id.desc(),
            )
            .with_for_update()
            .first()
        )
    if enterprise_snapshot is None:
        reasons.append("enterprise_snapshot_missing")
    elif settings.feature_bid_assessment_phase4_enterprise_capability:
        from app.services.bid_enterprise_capability import (
            BidEnterpriseCapabilityError,
            validate_frozen_snapshot_metadata,
        )

        try:
            validate_frozen_snapshot_metadata(db, enterprise_snapshot)
        except BidEnterpriseCapabilityError:
            reasons.append("enterprise_snapshot_not_governed")

    rule_set = _active_artifact(db, BidRuleSet)
    fact_catalog = _active_artifact(db, BidFactCatalogVersion)
    prompt_bundle = _active_artifact(db, BidPromptBundle)
    tool_registry = _active_artifact(db, BidToolRegistryVersion)
    model_profile = _active_artifact(db, BidModelProfileVersion)
    formula_catalog = _active_artifact(db, BidFormulaCatalogVersion)
    artifacts = (
        ("rule_set", rule_set),
        ("fact_catalog", fact_catalog),
        ("prompt_bundle", prompt_bundle),
        ("tool_registry", tool_registry),
        ("model_profile", model_profile),
        ("formula_catalog", formula_catalog),
    )
    reasons.extend(f"{name}_missing" for name, value in artifacts if value is None)
    for name, value in artifacts:
        if value is None:
            continue
        if (
            value.reviewed_at is None
            or value.activated_at is None
            or as_utc(value.reviewed_at) > current_time
            or as_utc(value.activated_at) > current_time
        ):
            reasons.append(f"{name}_not_effective")
    if rule_set is not None:
        if rule_set.effective_from is None or as_utc(rule_set.effective_from) > current_time:
            reasons.append("rule_set_not_effective")
        if rule_set.effective_to is not None and as_utc(rule_set.effective_to) <= current_time:
            reasons.append("rule_set_expired")

    if reasons:
        raise BidRunInputNotReady(*reasons)
    assert manifest is not None
    assert scope is not None
    assert enterprise_snapshot is not None
    assert rule_set is not None
    assert fact_catalog is not None
    assert prompt_bundle is not None
    assert tool_registry is not None
    assert model_profile is not None
    assert formula_catalog is not None

    evidence_package = None
    if business_baseline is not None and business_baseline.evidence_package_id:
        evidence_package = (
            db.query(BidEnterpriseEvidencePackage)
            .filter(
                BidEnterpriseEvidencePackage.id
                == business_baseline.evidence_package_id,
                BidEnterpriseEvidencePackage.status == "frozen",
            )
            .one_or_none()
        )
        if (
            evidence_package is None
            or str(evidence_package.package_hash)
            != str(business_baseline.evidence_package_hash or "")
        ):
            raise BidRunInputNotReady("enterprise_evidence_package_hash_mismatch")

    hard_gate_comparison_baseline = None
    if getattr(settings, "feature_bid_assessment_phase4_fact_verification", False):
        if business_baseline is None:
            raise BidRunInputNotReady("hard_gate_comparison_business_baseline_missing")
        from app.services.bid_hard_gate_fact_verification import (
            latest_hard_gate_comparison_baseline,
        )

        hard_gate_comparison_baseline = latest_hard_gate_comparison_baseline(
            db,
            assessment_id=str(assessment.id),
            manifest_id=str(manifest.id),
            scope_id=str(scope.id),
            business_baseline_id=str(business_baseline.id),
            effective_at=current_time,
            lock=True,
        )
        if hard_gate_comparison_baseline is None:
            raise BidRunInputNotReady("hard_gate_comparison_baseline_missing_or_stale")

    fingerprint_payload = {
        "assessment_id": str(assessment.id),
        "assessment_scope_id": str(scope.id),
        "scope_version": int(scope.version),
        "document_manifest_version": int(manifest.version),
        "enterprise_snapshot_version": str(enterprise_snapshot.version),
        **(
            {
                "enterprise_business_baseline_version": str(business_baseline.version),
                "enterprise_business_baseline_hash": str(business_baseline.baseline_hash),
            }
            if business_baseline is not None
            else {}
        ),
        **(
            {
                "enterprise_evidence_package_version": str(evidence_package.version),
                "enterprise_evidence_package_hash": str(evidence_package.package_hash),
            }
            if evidence_package is not None
            else {}
        ),
        **(
            {
                "hard_gate_comparison_baseline_version": str(
                    hard_gate_comparison_baseline.version
                ),
                "hard_gate_comparison_baseline_hash": str(
                    hard_gate_comparison_baseline.baseline_hash
                ),
            }
            if hard_gate_comparison_baseline is not None
            else {}
        ),
        "rule_set_version": str(rule_set.version),
        "fact_catalog_version": str(fact_catalog.version),
        "prompt_bundle_version": str(prompt_bundle.version),
        "tool_registry_version": str(tool_registry.version),
        "model_profile_version": str(model_profile.version),
        "formula_catalog_version": str(formula_catalog.version),
    }
    input_payload = {
        **fingerprint_payload,
        "evaluation_time": current_time.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
    }
    return FrozenRunInputs(
        assessment=assessment,
        scope=scope,
        manifest=manifest,
        enterprise_snapshot=enterprise_snapshot,
        business_baseline=business_baseline,
        hard_gate_comparison_baseline=hard_gate_comparison_baseline,
        rule_set=rule_set,
        fact_catalog=fact_catalog,
        prompt_bundle=prompt_bundle,
        tool_registry=tool_registry,
        model_profile=model_profile,
        formula_catalog=formula_catalog,
        evaluation_time=current_time,
        input_fingerprint=canonical_hash(fingerprint_payload),
        input_hash=canonical_hash(input_payload),
        fingerprint_payload=fingerprint_payload,
        input_payload=input_payload,
    )


def _active_run(db: Session, assessment_id: str) -> BidAnalysisRun | None:
    return (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.assessment_id == assessment_id,
            or_(
                BidAnalysisRun.status.in_(NON_TERMINAL_RUN_STATES),
                and_(
                    BidAnalysisRun.status == "failed",
                    BidAnalysisRun.retryable.is_(True),
                ),
            ),
        )
        .order_by(BidAnalysisRun.run_sequence.desc())
        .with_for_update()
        .first()
    )


def _latest_same_fingerprint_cancelled(
    db: Session,
    *,
    assessment_id: str,
    input_fingerprint: str,
) -> BidAnalysisRun | None:
    return (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.assessment_id == assessment_id,
            BidAnalysisRun.input_fingerprint == input_fingerprint,
            BidAnalysisRun.status == "cancelled",
        )
        .order_by(BidAnalysisRun.run_sequence.desc())
        .with_for_update()
        .first()
    )


def _create_run(
    db: Session,
    *,
    frozen: FrozenRunInputs,
    run_kind: str,
    actor_type: str,
    actor_ref: str,
    actor_id: int | None,
    request_id: str,
    causation_event_id: str | None,
    manual_reason: str | None,
    note: str | None,
) -> RunBootstrapResult:
    if run_kind not in RUN_KINDS:
        raise BidRunInputNotReady("requested_run_kind_invalid")
    assessment_id = str(frozen.assessment.id)
    active = _active_run(db, assessment_id)
    if active is not None:
        raise BidActiveRunExists(active)

    existing = (
        db.query(BidAnalysisRun)
        .filter(
            BidAnalysisRun.assessment_id == assessment_id,
            BidAnalysisRun.input_hash == frozen.input_hash,
            BidAnalysisRun.run_kind == run_kind,
        )
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        raise BidRunAlreadyExistsForInput(existing)

    run_sequence = int(
        db.query(func.max(BidAnalysisRun.run_sequence))
        .filter(BidAnalysisRun.assessment_id == assessment_id)
        .scalar()
        or 0
    ) + 1
    restart_of = _latest_same_fingerprint_cancelled(
        db,
        assessment_id=assessment_id,
        input_fingerprint=frozen.input_fingerprint,
    )
    run = BidAnalysisRun(
        id=f"run_{uuid.uuid4().hex}",
        assessment_id=assessment_id,
        scope_id=str(frozen.scope.id),
        manifest_id=str(frozen.manifest.id),
        enterprise_snapshot_id=str(frozen.enterprise_snapshot.id),
        hard_gate_comparison_baseline_id=(
            str(frozen.hard_gate_comparison_baseline.id)
            if frozen.hard_gate_comparison_baseline is not None
            else None
        ),
        hard_gate_comparison_baseline_hash=(
            str(frozen.hard_gate_comparison_baseline.baseline_hash)
            if frozen.hard_gate_comparison_baseline is not None
            else None
        ),
        rule_set_id=str(frozen.rule_set.id),
        fact_catalog_version_id=str(frozen.fact_catalog.id),
        prompt_bundle_id=str(frozen.prompt_bundle.id),
        tool_registry_version_id=str(frozen.tool_registry.id),
        model_profile_version_id=str(frozen.model_profile.id),
        formula_catalog_version_id=str(frozen.formula_catalog.id),
        restart_of_run_id=str(restart_of.id) if restart_of is not None else None,
        run_sequence=run_sequence,
        run_kind=run_kind,
        status="created",
        retryable=False,
        input_fingerprint=frozen.input_fingerprint,
        input_hash=frozen.input_hash,
        evaluation_time=frozen.evaluation_time,
        current_stage="planning",
        waiting_reason=None,
        row_version=1,
    )
    db.add(run)
    db.flush()

    previous_assessment = {
        "business_status": str(frozen.assessment.business_status),
        "active_run_id": frozen.assessment.active_run_id,
        "row_version": int(frozen.assessment.row_version),
    }
    if manual_reason is not None and str(frozen.assessment.business_status) not in {
        "preparing",
        "preliminary_analyzing",
        "deep_analyzing",
        "validating",
    }:
        frozen.assessment.business_status = "preparing"
    frozen.assessment.active_run_id = str(run.id)
    if actor_id is not None:
        frozen.assessment.updated_by = int(actor_id)
    frozen.assessment.row_version = int(frozen.assessment.row_version) + 1
    db.flush()

    progress_url = f"/api/v1/bid-assessments/{assessment_id}/runs/{run.id}"
    created_event = append_outbox_event(
        db,
        event_type="bid.run.created.v1",
        producer="bid-run-bootstrap-v1",
        aggregate_type="run",
        aggregate_id=str(run.id),
        aggregate_version=int(run.row_version),
        assessment_id=assessment_id,
        run_id=str(run.id),
        request_id=request_id,
        causation_event_id=causation_event_id,
        payload_schema="bid.run.created.v1.payload",
        payload={
            "run_id": str(run.id),
            "assessment_id": assessment_id,
            "scope_id": str(run.scope_id),
            "manifest_id": str(run.manifest_id),
            "run_kind": str(run.run_kind),
            "run_sequence": int(run.run_sequence),
            "from": "not_created",
            "to": "created",
            "retryable": False,
            "resource_version": int(run.row_version),
            "assessment_resource_version": int(frozen.assessment.row_version),
            "input_fingerprint": str(run.input_fingerprint),
            "input_hash": str(run.input_hash),
            "hard_gate_comparison_baseline_hash": (
                str(run.hard_gate_comparison_baseline_hash)
                if run.hard_gate_comparison_baseline_hash
                else None
            ),
            "evaluation_time": frozen.input_payload["evaluation_time"],
            "progress_url": progress_url,
        },
        dedupe_key=f"run-created:{run.id}",
        occurred_at=frozen.evaluation_time,
    )
    append_audit_log(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_ref=actor_ref,
        action="run.bootstrap.create",
        entity_type="run",
        entity_id=str(run.id),
        assessment_id=assessment_id,
        outcome="succeeded",
        request_id=request_id,
        correlation_id=str(created_event.event_id),
        before=previous_assessment,
        after={
            "business_status": str(frozen.assessment.business_status),
            "active_run_id": str(run.id),
            "assessment_row_version": int(frozen.assessment.row_version),
            "run_status": str(run.status),
            "run_row_version": int(run.row_version),
            "input_fingerprint": str(run.input_fingerprint),
            "input_hash": str(run.input_hash),
        },
        metadata={
            "causation_event_id": causation_event_id,
            "manual_reason": manual_reason,
            "note_present": note is not None,
            "input_versions": frozen.fingerprint_payload,
        },
        occurred_at=frozen.evaluation_time,
    )
    db.flush()
    return RunBootstrapResult(
        run=run,
        created_event_id=str(created_event.event_id),
        assessment_row_version=int(frozen.assessment.row_version),
    )


def bootstrap_run(
    db: Session,
    *,
    assessment_id: str,
    scope_id: str,
    manifest_id: str,
    run_kind: str,
    actor_type: str,
    actor_ref: str,
    actor_id: int | None,
    request_id: str,
    causation_event_id: str | None = None,
    manual_reason: str | None = None,
    note: str | None = None,
    evaluation_time: datetime | None = None,
) -> RunBootstrapResult:
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None:
        raise BidRunNotFound()
    if str(assessment.lifecycle_status) != "active":
        raise BidRunInputNotReady("assessment_not_active")
    if str(assessment.business_status) == "superseded":
        raise BidRunInputNotReady("assessment_superseded")
    active = _active_run(db, str(assessment.id))
    if active is not None:
        raise BidActiveRunExists(active)
    frozen = _load_frozen_inputs(
        db,
        assessment=assessment,
        scope_id=scope_id,
        manifest_id=manifest_id,
        evaluation_time=as_utc(evaluation_time) if evaluation_time else database_utc_now(db),
    )
    return _create_run(
        db,
        frozen=frozen,
        run_kind=run_kind,
        actor_type=actor_type,
        actor_ref=actor_ref,
        actor_id=actor_id,
        request_id=request_id,
        causation_event_id=causation_event_id,
        manual_reason=manual_reason,
        note=note,
    )


def create_manual_run(
    db: Session,
    *,
    assessment_id: str,
    manifest_id: str,
    reason: str,
    note: str | None,
    expected_assessment_etag: str,
    actor_id: int,
    actor_ref: str,
    actor_is_admin: bool,
    request_id: str,
    evaluation_time: datetime | None = None,
) -> IdempotentCommandResult:
    assessment = (
        db.query(BidAssessment)
        .filter(BidAssessment.id == assessment_id)
        .with_for_update()
        .one_or_none()
    )
    if assessment is None or (
        int(assessment.created_by) != int(actor_id) and not actor_is_admin
    ):
        raise BidRunNotFound()
    current_etag = assessment_etag(str(assessment.id), int(assessment.row_version))
    if current_etag != expected_assessment_etag:
        raise BidRunVersionMismatch(assessment, provided_etag=expected_assessment_etag)
    if str(assessment.current_manifest_id or "") != str(manifest_id):
        raise BidRunInputNotReady("manifest_is_not_current")
    scope = _latest_scope(db, assessment_id=str(assessment.id))
    if scope is None:
        raise BidRunInputNotReady("current_scope_not_bound")

    result = bootstrap_run(
        db,
        assessment_id=str(assessment.id),
        scope_id=str(scope.id),
        manifest_id=str(manifest_id),
        run_kind="reanalysis",
        actor_type="user",
        actor_ref=f"user:{actor_ref}",
        actor_id=int(actor_id),
        request_id=request_id,
        manual_reason=reason,
        note=note,
        evaluation_time=evaluation_time,
    )
    from app.services.bid_run_snapshots import build_run_progress_snapshot

    snapshot = build_run_progress_snapshot(db, result.run)
    return IdempotentCommandResult(
        status_code=202,
        body={
            "code": 202,
            "message": "Run 已创建并等待确定性规划",
            "data": snapshot,
            "error": None,
            "request_id": request_id,
        },
        resource_type="run",
        resource_id=str(result.run.id),
    )


def consume_plan_requested_event(
    db: Session,
    *,
    event_id: str,
    evaluation_time: datetime | None = None,
) -> ProcessedEventResult:
    """Create one Run from a durable planning request exactly once.

    ``BidRunInputNotReady`` deliberately escapes without a processed marker so
    a maintenance scan can retry after governed snapshots/configuration become
    available.  No placeholder Run is written in that case.
    """

    def _handler(session: Session, event: BidOutboxEvent) -> dict[str, Any]:
        if str(event.event_type) != PLAN_REQUESTED_EVENT:
            return {"ignored": True, "event_type": str(event.event_type)}
        payload = dict(event.payload_json or {})
        missing = [field for field in PLAN_REQUESTED_REQUIRED_FIELDS if payload.get(field) is None]
        if missing:
            raise BidPlanRequestedEventInvalid(
                f"BID_PLAN_REQUESTED_EVENT_PAYLOAD_MISSING:{','.join(missing)}"
            )
        expected = {
            "aggregate_type": "scope",
            "aggregate_id": str(payload["scope_id"]),
            "assessment_id": str(payload["assessment_id"]),
        }
        actual = {
            "aggregate_type": str(event.aggregate_type),
            "aggregate_id": str(event.aggregate_id),
            "assessment_id": str(event.assessment_id or ""),
        }
        if actual != expected:
            raise BidPlanRequestedEventInvalid("BID_PLAN_REQUESTED_EVENT_MISMATCH")

        requested_run_kind = str(payload["requested_run_kind"])
        if requested_run_kind not in RUN_KINDS:
            raise BidPlanRequestedEventInvalid(
                "BID_PLAN_REQUESTED_RUN_KIND_INVALID"
            )
        resource_version = payload["resource_version"]
        if (
            isinstance(resource_version, bool)
            or not isinstance(resource_version, int)
            or resource_version < 1
        ):
            raise BidPlanRequestedEventInvalid(
                "BID_PLAN_REQUESTED_RESOURCE_VERSION_INVALID"
            )

        assessment = (
            session.query(BidAssessment)
            .filter(BidAssessment.id == str(payload["assessment_id"]))
            .with_for_update()
            .one_or_none()
        )
        if assessment is None:
            raise BidPlanRequestedEventInvalid("BID_PLAN_REQUESTED_ASSESSMENT_NOT_FOUND")

        scope = (
            session.query(BidAssessmentScope)
            .filter(
                BidAssessmentScope.id == str(payload["scope_id"]),
                BidAssessmentScope.assessment_id == str(assessment.id),
            )
            .with_for_update()
            .one_or_none()
        )
        if scope is None:
            raise BidPlanRequestedEventInvalid(
                "BID_PLAN_REQUESTED_SCOPE_NOT_FOUND"
            )
        selected_lot = dict(scope.selected_lot_snapshot_json or {})
        bound_lot_id = str(
            scope.source_lot_candidate_id or selected_lot.get("lot_id") or ""
        )
        if (
            bound_lot_id != str(payload["lot_id"])
            or str(selected_lot.get("manifest_id") or "")
            != str(payload["manifest_id"])
        ):
            raise BidPlanRequestedEventInvalid(
                "BID_PLAN_REQUESTED_SCOPE_BINDING_MISMATCH"
            )
        if (
            str(assessment.lifecycle_status) != "active"
            or str(assessment.business_status) in {"cancelled", "superseded"}
        ):
            return {
                "ignored": True,
                "reason": "assessment_no_longer_bootstrappable",
            }
        existing_active = _active_run(session, str(assessment.id))
        if existing_active is not None:
            if (
                str(existing_active.scope_id) == str(payload["scope_id"])
                and str(existing_active.manifest_id) == str(payload["manifest_id"])
            ):
                return {
                    "ignored": False,
                    "created": False,
                    "run_id": str(existing_active.id),
                    "status": str(existing_active.status),
                }
            return {
                "ignored": True,
                "reason": "different_active_run_exists",
                "run_id": str(existing_active.id),
            }

        result = bootstrap_run(
            session,
            assessment_id=str(payload["assessment_id"]),
            scope_id=str(payload["scope_id"]),
            manifest_id=str(payload["manifest_id"]),
            run_kind=requested_run_kind,
            actor_type="service",
            actor_ref="service:bid-run-bootstrap-v1",
            actor_id=None,
            request_id=str(event.request_id),
            causation_event_id=str(event.event_id),
            evaluation_time=evaluation_time,
        )
        return {
            "ignored": False,
            "created": True,
            "run_id": str(result.run.id),
            "created_event_id": result.created_event_id,
        }

    return process_outbox_event_once(
        db,
        consumer_name=RUN_BOOTSTRAP_CONSUMER,
        event_id=event_id,
        handler=_handler,
    )


def pending_plan_requested_event_ids(db: Session, *, limit: int = 20) -> list[str]:
    rows = (
        db.query(BidOutboxEvent.event_id)
        .outerjoin(
            BidProcessedEvent,
            and_(
                BidProcessedEvent.event_id == BidOutboxEvent.event_id,
                BidProcessedEvent.consumer_name == RUN_BOOTSTRAP_CONSUMER,
            ),
        )
        .filter(
            BidOutboxEvent.event_type == PLAN_REQUESTED_EVENT,
            BidProcessedEvent.event_id.is_(None),
        )
        .order_by(BidOutboxEvent.occurred_at.asc(), BidOutboxEvent.event_id.asc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [str(row[0]) for row in rows]


def process_pending_run_bootstraps(
    *,
    session_factory: Callable[[], Session],
    limit: int = 20,
) -> RunBootstrapBatchResult:
    index_db = session_factory()
    try:
        event_ids = pending_plan_requested_event_ids(index_db, limit=limit)
    finally:
        index_db.close()

    created = duplicate = pending_input = ignored = failed = 0
    for event_id in event_ids:
        event_db = session_factory()
        try:
            with event_db.begin():
                result = consume_plan_requested_event(event_db, event_id=event_id)
            if result.duplicate:
                duplicate += 1
            elif isinstance(result.value, dict) and result.value.get("created"):
                created += 1
            else:
                ignored += 1
        except BidRunInputNotReady:
            pending_input += 1
        except Exception:
            logger.exception(
                "bid_run_bootstrap_pending_event_failed",
                extra={"event_id": event_id},
            )
            failed += 1
        finally:
            event_db.close()
    return RunBootstrapBatchResult(
        scanned=len(event_ids),
        created=created,
        duplicate=duplicate,
        pending_input=pending_input,
        ignored=ignored,
        failed=failed,
    )
