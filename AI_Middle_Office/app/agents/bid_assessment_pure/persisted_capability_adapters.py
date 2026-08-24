"""Persisted capability boundaries for the local Pure Agent Runtime.

The adapters in this module rebind one already accepted Action to current,
hash-verified persistence.  They do not select an Action, order capabilities,
call a provider, retrieve evidence, execute a Tool, or commit a transaction.

Answer publication remains deliberately conservative: persisted Context entries
become non-citable Runtime receipts unless a later evidence-specific adapter can
prove source head, locator, disclosure, and authorization authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from pydantic import Field, ValidationError, model_validator

from .action_runtime import AgentActionKind, PlanActionRequest
from .answer_contracts import (
    AnswerDraft,
    GroundingKind,
    GroundingRecord,
    GroundingSnapshot,
    GroundingStatus,
    SourceBasis,
)
from .capability_executors import (
    AnswerCapabilityBoundary,
    CapabilityExecutorFactories,
    PlanCapabilityBoundary,
    ToolBatchGatewayPort,
    ToolCallBatchBoundary,
)
from .citation_contracts import (
    CitationAuthorityRecord,
    CitationAuthoritySnapshot,
    CitationLocatorKind,
    CitationSourceType,
)
from .common import Reference, StrictContract, ToolName
from .complexity_gate import DefaultComplexityGate
from .context_runtime import ContextAssemblerRuntime
from .persisted_local_adapters import LocalBoundaryInputPolicy
from .persisted_evidence_adapters import (
    PersistedEvidenceArtifactRejected,
    PersistedEvidenceAtomAuthority,
    extract_persisted_evidence_atoms,
)
from .planner_runtime import PlannerRuntime
from .planning import ExecutionMode, PlanRevision
from .repository import PureAgentPersistenceError, PureAgentRepository
from .runtime import (
    ContextAssemblyRequest,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
    ContextProjectionEntry,
)
from .runtime_controller import PersistedRuntimeAction
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import (
    ExecutionDeadline,
    RegistrySnapshot,
    ToolGuardPolicy,
    canonical_hash,
)
from .tools import ToolExecutionContext


_USER_TURN_KINDS = frozenset(
    {
        "user.task_trigger",
        "user.steering_candidate",
        "user.slot_candidate",
    }
)
_MODEL_READY_CONTEXT_STATUSES = frozenset(
    {
        ContextAssemblyStatus.READY,
        ContextAssemblyStatus.READY_WITH_LIMITS,
    }
)


class PersistedCapabilityAdapterError(RuntimeError):
    """Safe base error for a persisted capability boundary."""


class PersistedCapabilityBoundaryRejected(PersistedCapabilityAdapterError):
    """The active Action lost a Task, scope, policy, or authorization fence."""


class PersistedAnswerAuthorityRejected(PersistedCapabilityAdapterError):
    """The fresh Context cannot authorize the model-authored Grounding refs."""


class PersistedToolBoundaryPolicy(StrictContract):
    """Explicit local Tool authority; false/empty defaults remain fail closed."""

    runtime_enabled: bool = False
    allowed_tool_names: tuple[ToolName, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    approved_tool_names: tuple[ToolName, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    allow_local: bool = False
    allow_mcp: bool = False
    allow_external_egress: bool = False
    authorized_document_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    enterprise_scope_ref: Reference | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_authority(self) -> "PersistedToolBoundaryPolicy":
        for field_name in (
            "allowed_tool_names",
            "approved_tool_names",
            "authorized_document_refs",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if not set(self.approved_tool_names).issubset(
            set(self.allowed_tool_names)
        ):
            raise ValueError("approved tools must also be allowed")
        if not self.runtime_enabled and any(
            (
                self.allow_local,
                self.allow_mcp,
                self.allow_external_egress,
            )
        ):
            raise ValueError("disabled Tool Runtime cannot grant execution authority")
        return self


class PersistedAnswerAuthorityProjector(Protocol):
    """Build publication authority from one freshly assembled Context."""

    def project(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
    ) -> tuple[GroundingSnapshot, CitationAuthoritySnapshot]: ...


class ReceiptOnlyAnswerAuthorityProjector:
    """Project selected Context entries as non-citable, unknown receipts.

    This gives limitations and receipt-aware answers an authoritative boundary
    without pretending that a Context projection proves a source locator or a
    disclosure decision.  Supported source claims therefore fail closed until
    an evidence-aware authority adapter is explicitly supplied.
    """

    def project(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
    ) -> tuple[GroundingSnapshot, CitationAuthoritySnapshot]:
        snapshot = context.snapshot
        if (
            snapshot.task_ref != task.task_id
            or snapshot.state_version != task.state_version
            or snapshot.status not in _MODEL_READY_CONTEXT_STATUSES
        ):
            raise PersistedAnswerAuthorityRejected(
                "Answer authority requires a fresh model-ready Context"
            )

        entries = {entry.entry_ref: entry for entry in context.projection_entries}
        records: list[GroundingRecord] = []
        for grounding_ref in draft.referenced_grounding_refs():
            entry = entries.get(grounding_ref)
            if entry is None:
                raise PersistedAnswerAuthorityRejected(
                    "Answer draft references Grounding outside the fresh Context"
                )
            records.append(
                self._receipt_record(
                    entry=entry,
                    task_ref=task.task_id,
                    authorization_snapshot_ref=(
                        snapshot.authorization_snapshot_ref
                    ),
                )
            )

        allowed_scopes = (task.task_id,)
        grounding = GroundingSnapshot.build(
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=snapshot.snapshot_ref,
            context_snapshot_hash=snapshot.snapshot_hash,
            authorization_snapshot_ref=snapshot.authorization_snapshot_ref,
            allowed_scope_refs=allowed_scopes,
            records=tuple(records),
        )
        citation = CitationAuthoritySnapshot.build(
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=snapshot.snapshot_ref,
            context_snapshot_hash=snapshot.snapshot_hash,
            grounding_snapshot_ref=grounding.snapshot_ref,
            authorization_snapshot_ref=snapshot.authorization_snapshot_ref,
            allowed_scope_refs=allowed_scopes,
            records=(),
        )
        return grounding, citation

    @staticmethod
    def _receipt_record(
        *,
        entry: ContextProjectionEntry,
        task_ref: str,
        authorization_snapshot_ref: str,
    ) -> GroundingRecord:
        source_basis, grounding_kind = {
            ContextEntryKind.POLICY: (
                SourceBasis.SYSTEM_RULE,
                GroundingKind.SYSTEM_RULE,
            ),
            ContextEntryKind.OUTPUT_CONTRACT: (
                SourceBasis.SYSTEM_RULE,
                GroundingKind.SYSTEM_RULE,
            ),
            ContextEntryKind.CURRENT_USER_MESSAGE: (
                SourceBasis.USER_ASSERTION,
                GroundingKind.USER_MESSAGE,
            ),
            ContextEntryKind.CONVERSATION_MESSAGE: (
                SourceBasis.USER_ASSERTION,
                GroundingKind.USER_MESSAGE,
            ),
            ContextEntryKind.ACTIVE_TOOL_RESULT: (
                SourceBasis.RUNTIME_RECEIPT,
                GroundingKind.TOOL_RECEIPT,
            ),
            ContextEntryKind.GROUNDING: (
                SourceBasis.RUNTIME_RECEIPT,
                GroundingKind.SOURCE_AVAILABILITY_RECEIPT,
            ),
        }.get(
            entry.kind,
            (SourceBasis.RUNTIME_RECEIPT, GroundingKind.CONTEXT_RECEIPT),
        )
        locator_hash = canonical_hash(
            {
                "receipt_entry_ref": entry.entry_ref,
                "source_ref": entry.source_ref,
            }
        )
        return GroundingRecord(
            grounding_ref=entry.entry_ref,
            context_entry_ref=entry.entry_ref,
            source_ref=entry.source_ref,
            source_basis=source_basis,
            grounding_kind=grounding_kind,
            source_scope_ref=task_ref,
            authorization_snapshot_ref=authorization_snapshot_ref,
            source_version_ref=entry.source_version_ref,
            source_head_version_ref=entry.source_version_ref,
            source_content_hash=entry.source_content_hash,
            source_head_content_hash=entry.source_content_hash,
            locator_hash=locator_hash,
            source_head_locator_hash=locator_hash,
            context_projection_hash=entry.projection_hash,
            status=GroundingStatus.UNKNOWN,
            citable=False,
            citation_projection_ready=False,
            quote_bindings=(),
        )


class PersistedEvidenceAnswerAuthorityProjector:
    """Promote only hash-verified persisted Evidence Atoms to citations."""

    def __init__(self, repository: PureAgentRepository) -> None:
        self._repository = repository

    def project(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
    ) -> tuple[GroundingSnapshot, CitationAuthoritySnapshot]:
        snapshot = context.snapshot
        if (
            snapshot.task_ref != task.task_id
            or snapshot.state_version != task.state_version
            or snapshot.status not in _MODEL_READY_CONTEXT_STATUSES
        ):
            raise PersistedAnswerAuthorityRejected(
                "Answer authority requires a fresh model-ready Context"
            )

        entries = {entry.entry_ref: entry for entry in context.projection_entries}
        authorities = self._load_authorities(task)
        records: list[GroundingRecord] = []
        citation_records: list[CitationAuthorityRecord] = []
        allowed_scopes: list[str] = [task.task_id]
        for grounding_ref in draft.referenced_grounding_refs():
            entry = entries.get(grounding_ref)
            if entry is None:
                raise PersistedAnswerAuthorityRejected(
                    "Answer draft references Grounding outside the fresh Context"
                )
            authority = authorities.get(grounding_ref)
            if authority is None:
                records.append(
                    ReceiptOnlyAnswerAuthorityProjector._receipt_record(
                        entry=entry,
                        task_ref=task.task_id,
                        authorization_snapshot_ref=(
                            snapshot.authorization_snapshot_ref
                        ),
                    )
                )
                continue
            self._validate_context_entry(
                entry=entry,
                authority=authority,
            )
            allowed_scopes.append(authority.source_scope_ref)
            locator_hash = canonical_hash(
                {
                    "source_ref": authority.evidence_ref,
                    "locator": authority.locator,
                }
            )
            source_basis = (
                SourceBasis.DOCUMENT
                if authority.source_domain == "bid_document"
                else SourceBasis.ENTERPRISE
            )
            record = GroundingRecord(
                grounding_ref=grounding_ref,
                context_entry_ref=grounding_ref,
                source_ref=authority.evidence_ref,
                source_basis=source_basis,
                grounding_kind=GroundingKind.EVIDENCE_ATOM,
                source_scope_ref=authority.source_scope_ref,
                authorization_snapshot_ref=snapshot.authorization_snapshot_ref,
                source_version_ref=authority.source_version_ref,
                source_head_version_ref=authority.source_version_ref,
                source_content_hash=authority.content_hash,
                source_head_content_hash=authority.content_hash,
                locator_hash=locator_hash,
                source_head_locator_hash=locator_hash,
                context_projection_hash=entry.projection_hash,
                status=GroundingStatus.SUPPORTED,
                citable=True,
                citation_projection_ready=True,
                quote_bindings=(),
            )
            records.append(record)
            citation_records.append(
                self._citation_record(
                    record=record,
                    authority=authority,
                )
            )

        scope_refs = tuple(dict.fromkeys(allowed_scopes))
        grounding = GroundingSnapshot.build(
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=snapshot.snapshot_ref,
            context_snapshot_hash=snapshot.snapshot_hash,
            authorization_snapshot_ref=snapshot.authorization_snapshot_ref,
            allowed_scope_refs=scope_refs,
            records=tuple(records),
        )
        citation = CitationAuthoritySnapshot.build(
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=snapshot.snapshot_ref,
            context_snapshot_hash=snapshot.snapshot_hash,
            grounding_snapshot_ref=grounding.snapshot_ref,
            authorization_snapshot_ref=snapshot.authorization_snapshot_ref,
            allowed_scope_refs=scope_refs,
            records=tuple(citation_records),
        )
        return grounding, citation

    def _load_authorities(
        self,
        task: AgentTaskState,
    ) -> dict[str, PersistedEvidenceAtomAuthority]:
        authorities: dict[str, PersistedEvidenceAtomAuthority] = {}
        try:
            for observation_ref in task.observation_refs:
                artifact = self._repository.load_context_observation_artifact(
                    task_id=task.task_id,
                    observation_ref=observation_ref,
                )
                for authority in extract_persisted_evidence_atoms(artifact):
                    if authority.evidence_ref in authorities:
                        raise PersistedEvidenceArtifactRejected(
                            "Evidence Atom authority is ambiguous across observations"
                        )
                    authorities[authority.evidence_ref] = authority
        except (PureAgentPersistenceError, PersistedEvidenceArtifactRejected) as exc:
            raise PersistedAnswerAuthorityRejected(
                "persisted Evidence Atom authority could not be verified"
            ) from exc
        return authorities

    @staticmethod
    def _validate_context_entry(
        *,
        entry: ContextProjectionEntry,
        authority: PersistedEvidenceAtomAuthority,
    ) -> None:
        if (
            entry.kind is not ContextEntryKind.EVIDENCE_ATOM
            or entry.entry_ref != authority.evidence_ref
            or entry.source_ref != authority.evidence_ref
            or entry.source_version_ref != authority.source_version_ref
            or entry.source_content_hash != authority.content_hash
            or entry.content != authority.context_content()
        ):
            raise PersistedAnswerAuthorityRejected(
                "fresh Context Evidence Atom drifted from persisted authority"
            )

    @staticmethod
    def _citation_record(
        *,
        record: GroundingRecord,
        authority: PersistedEvidenceAtomAuthority,
    ) -> CitationAuthorityRecord:
        source_type = (
            CitationSourceType.DOCUMENT
            if authority.source_domain == "bid_document"
            else CitationSourceType.ENTERPRISE_RECORD
        )
        locator_kind = (
            CitationLocatorKind.PAGE
            if authority.source_domain == "bid_document"
            else CitationLocatorKind.RECORD
        )
        safe_title = (
            "招标文件证据"
            if authority.source_domain == "bid_document"
            else "企业资料证据"
        )
        authority_body = {
            "grounding_ref": record.grounding_ref,
            "source_ref": record.source_ref,
            "source_scope_ref": record.source_scope_ref,
            "authorization_snapshot_ref": record.authorization_snapshot_ref,
            "source_version_ref": record.source_version_ref,
            "source_head_version_ref": record.source_head_version_ref,
            "source_content_hash": record.source_content_hash,
            "source_head_content_hash": record.source_head_content_hash,
            "locator_hash": record.locator_hash,
            "source_head_locator_hash": record.source_head_locator_hash,
            "context_projection_hash": record.context_projection_hash,
            "source_type": source_type.value,
            "locator_kind": locator_kind.value,
            "disclosure_allowed": True,
            "safe_title": safe_title,
            "safe_locator_label": authority.locator,
            "safe_version_label": "已冻结版本",
            "controlled_access_ref": None,
        }
        digest = canonical_hash(authority_body)
        return CitationAuthorityRecord(
            authority_ref=(
                "citation-authority-record:" + digest.removeprefix("sha256:")
            ),
            grounding_ref=record.grounding_ref,
            source_ref=record.source_ref,
            source_scope_ref=record.source_scope_ref,
            authorization_snapshot_ref=record.authorization_snapshot_ref,
            source_version_ref=record.source_version_ref,
            source_head_version_ref=record.source_head_version_ref,
            source_content_hash=record.source_content_hash,
            source_head_content_hash=record.source_head_content_hash,
            locator_hash=record.locator_hash,
            source_head_locator_hash=record.source_head_locator_hash,
            context_projection_hash=record.context_projection_hash,
            source_type=source_type,
            locator_kind=locator_kind,
            disclosure_allowed=True,
            safe_title=safe_title,
            safe_locator_label=authority.locator,
            safe_version_label="已冻结版本",
            controlled_access_ref=None,
        )


@dataclass(frozen=True, slots=True)
class _ActivePersistedCapabilityScope:
    scope: object
    decision_context: object
    registry_snapshot: RegistrySnapshot
    user_message_ref: str
    authorization_snapshot_ref: str


class _PersistedCapabilityBoundaryBase:
    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalBoundaryInputPolicy,
    ) -> None:
        self._repository = repository
        self._policy = policy

    def _load_active_scope(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> _ActivePersistedCapabilityScope:
        intent = action.envelope.intent
        registry = self._policy.registry_snapshot
        recovery = action.envelope.recovery_binding
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref != action.action_ref
            or action.status not in {"accepted", "running"}
            or intent.task_ref != task.task_id
            or intent.state_version + 1 != task.state_version
            or registry is None
            or recovery is None
        ):
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability Action has no active recoverable fence"
            )
        try:
            persisted_task = self._repository.load_task_state(task.task_id)
            scope = self._repository.load_local_task_scope(
                task_id=task.task_id,
                conversation_id=task.session_id,
            )
            decision_context = self._repository.load_context_snapshot(
                task_id=task.task_id,
                snapshot_ref=intent.context_snapshot_ref,
            )
        except PureAgentPersistenceError as exc:
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability scope is unavailable"
            ) from exc

        if (
            persisted_task != task
            or scope.conversation_status != "active"
            or scope.task_state_version != task.state_version
            or scope.goal_ref != task.goal_ref
            or scope.plan_ref != task.plan_ref
            or scope.cancellation_fence_ref is not None
            or decision_context.snapshot_hash != intent.context_snapshot_hash
            or decision_context.task_ref != task.task_id
            or decision_context.state_version + 2 != intent.state_version
            or decision_context.registry_snapshot_ref != intent.registry_snapshot_ref
            or decision_context.registry_snapshot_hash != intent.registry_snapshot_hash
            or registry.snapshot_ref != intent.registry_snapshot_ref
            or registry.snapshot_hash != intent.registry_snapshot_hash
            or registry.visible_tools_hash != intent.visible_tools_hash
            or recovery.authorization_policy_ref
            != self._policy.authorization_policy_ref
            or self._scope_snapshot_hash(
                task=task,
                intent=intent,
                scope=scope,
                context=decision_context,
            )
            != recovery.scope_snapshot_hash
        ):
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability scope or policy drifted"
            )

        user_entries = tuple(
            entry
            for entry in decision_context.included_entries
            if entry.kind is ContextEntryKind.CURRENT_USER_MESSAGE
        )
        if len(user_entries) != 1:
            raise PersistedCapabilityBoundaryRejected(
                "decision Context does not identify exactly one user turn"
            )
        user_message_ref = user_entries[0].source_ref
        try:
            user_message = self._repository.load_task_context_message(
                task_id=task.task_id,
                conversation_id=task.session_id,
                message_id=user_message_ref,
            )
        except PureAgentPersistenceError as exc:
            raise PersistedCapabilityBoundaryRejected(
                "decision user turn is unavailable"
            ) from exc
        if user_message.message_type not in _USER_TURN_KINDS:
            raise PersistedCapabilityBoundaryRejected(
                "decision user turn is outside the accepted message types"
            )
        return _ActivePersistedCapabilityScope(
            scope=scope,
            decision_context=decision_context,
            registry_snapshot=registry,
            user_message_ref=user_message_ref,
            authorization_snapshot_ref=decision_context.authorization_snapshot_ref,
        )

    async def _assemble_context(
        self,
        *,
        task: AgentTaskState,
        active: _ActivePersistedCapabilityScope,
        assembler: ContextAssemblerRuntime,
        consumer: ContextConsumer,
    ) -> ContextAssemblyResult:
        scope = active.scope
        assessment_ref = (
            None
            if scope.assessment_id is None
            else f"assessment:{scope.assessment_id}"
        )
        request = ContextAssemblyRequest(
            task_ref=task.task_id,
            state_version=task.state_version,
            consumer=consumer,
            user_message_ref=active.user_message_ref,
            visible_tool_names=active.registry_snapshot.visible_tool_names,
            information_need_refs=self._unique(
                (task.goal_ref, *self._policy.information_need_refs)
            ),
            required_resource_refs=self._unique(
                (
                    *((assessment_ref,) if assessment_ref is not None else ()),
                    *self._policy.required_resource_refs,
                )
            ),
            policy_snapshot_ref=self._policy.policy_snapshot_ref,
            prompt_template_ref=self._policy.prompt_template_ref,
            registry_snapshot_ref=active.registry_snapshot.snapshot_ref,
            model_profile_ref=self._policy.model_profile.profile_ref,
            context_profile_ref=self._policy.context_profile.profile_ref,
            checkpoint_snapshot_ref=scope.latest_checkpoint_ref,
            authorization_snapshot_ref=active.authorization_snapshot_ref,
            snapshot_sequence=task.state_version,
        )
        context = await assembler.assemble(
            task=task,
            request=request,
            model_profile=self._policy.model_profile,
            context_profile=self._policy.context_profile,
            registry_snapshot=active.registry_snapshot,
        )
        if context.snapshot.status not in _MODEL_READY_CONTEXT_STATUSES:
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability Context is not model ready"
            )
        return context

    @staticmethod
    def _scope_snapshot_hash(
        *,
        task: AgentTaskState,
        intent: object,
        scope: object,
        context: object,
    ) -> str:
        try:
            body = {
                "authorization_snapshot_ref": context.authorization_snapshot_ref,
                "task_ref": scope.task_id,
                "conversation_ref": scope.conversation_id,
                "owner_ref": f"user:{scope.owner_id}",
                "tenant_ref": scope.tenant_ref,
                "assessment_ref": (
                    None
                    if scope.assessment_id is None
                    else f"assessment:{scope.assessment_id}"
                ),
                "context_snapshot_ref": context.snapshot_ref,
                "context_snapshot_hash": context.snapshot_hash,
                "registry_snapshot_ref": context.registry_snapshot_ref,
                "registry_snapshot_hash": context.registry_snapshot_hash,
                "visible_tools_hash": intent.visible_tools_hash,
            }
        except AttributeError as exc:
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability scope is invalid"
            ) from exc
        if body["task_ref"] != task.task_id:
            raise PersistedCapabilityBoundaryRejected(
                "persisted capability scope crossed Task boundaries"
            )
        return canonical_hash(body)

    @staticmethod
    def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))


class PersistedPlanCapabilityBoundaryProvider(_PersistedCapabilityBoundaryBase):
    """Rebind one Plan/Replan Action to current Context and Plan head."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalBoundaryInputPolicy,
        context_assembler: ContextAssemblerRuntime,
        complexity_gate: DefaultComplexityGate | None = None,
    ) -> None:
        super().__init__(repository, policy=policy)
        self._context_assembler = context_assembler
        self._complexity_gate = complexity_gate or DefaultComplexityGate()

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        request: PlanActionRequest,
    ) -> PlanCapabilityBoundary:
        if action.action_kind not in {AgentActionKind.PLAN, AgentActionKind.REPLAN}:
            raise PersistedCapabilityBoundaryRejected(
                "Plan boundary received another capability kind"
            )
        active = self._load_active_scope(task=task, action=action)
        complexity = self._complexity_gate.decide(
            task=task,
            understanding=request.understanding,
        )
        if complexity.execution_mode is not ExecutionMode.PLANNED:
            raise PersistedCapabilityBoundaryRejected(
                "accepted Plan Action no longer passes the Complexity Gate"
            )
        previous = self._previous_plan(task=task, action=action)
        context = await self._assemble_context(
            task=task,
            active=active,
            assembler=self._context_assembler,
            consumer=ContextConsumer.PLANNER,
        )
        return PlanCapabilityBoundary(
            context=context,
            registry_snapshot=active.registry_snapshot,
            complexity=complexity,
            previous_plan=previous,
        )

    def _previous_plan(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> PlanRevision | None:
        if action.action_kind is AgentActionKind.PLAN:
            if task.execution_mode is not ExecutionMode.DIRECT or task.plan_ref is not None:
                raise PersistedCapabilityBoundaryRejected(
                    "initial Plan requires the current direct Task head"
                )
            return None
        if task.execution_mode is not ExecutionMode.PLANNED or task.plan_ref is None:
            raise PersistedCapabilityBoundaryRejected(
                "Replan requires the current planned Task head"
            )
        try:
            row = self._repository.load_task_plan(
                task_id=task.task_id,
                plan_id=task.plan_ref,
            )
            revision = PlanRevision.model_validate(row.body_json)
        except (PureAgentPersistenceError, TypeError, ValueError, ValidationError) as exc:
            raise PersistedCapabilityBoundaryRejected(
                "persisted active Plan is unavailable or invalid"
            ) from exc
        if (
            row.status != "active"
            or row.id != revision.plan_id
            or row.task_id != revision.task_id
            or canonical_hash(row.body_json).removeprefix("sha256:")
            != str(row.plan_hash).removeprefix("sha256:")
        ):
            raise PersistedCapabilityBoundaryRejected(
                "persisted active Plan head drifted"
            )
        return revision


class PersistedToolCallBatchBoundaryProvider(_PersistedCapabilityBoundaryBase):
    """Rebind one Tool batch to frozen Registry plus current local authority."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalBoundaryInputPolicy,
        tool_policy: PersistedToolBoundaryPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(repository, policy=policy)
        self._tool_policy = tool_policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
    ) -> ToolCallBatchBoundary:
        if action.action_kind is not AgentActionKind.TOOL_CALL_BATCH:
            raise PersistedCapabilityBoundaryRejected(
                "Tool boundary received another capability kind"
            )
        active = self._load_active_scope(task=task, action=action)
        registry_names = set(active.registry_snapshot.visible_tool_names)
        if (
            not set(self._tool_policy.allowed_tool_names).issubset(registry_names)
            or not set(self._tool_policy.approved_tool_names).issubset(registry_names)
        ):
            raise PersistedCapabilityBoundaryRejected(
                "Tool policy names are outside the frozen visible Registry"
            )
        now = self._clock()
        if now.tzinfo is None:
            raise PersistedCapabilityBoundaryRejected(
                "Tool boundary clock must be timezone aware"
            )
        scope = active.scope
        execution_context = ToolExecutionContext(
            user_ref=f"user:{scope.owner_id}",
            tenant_ref=scope.tenant_ref,
            conversation_ref=task.session_id,
            task_ref=task.task_id,
            state_version=task.state_version,
            context_snapshot_ref=action.envelope.intent.context_snapshot_ref,
            authorization_snapshot_ref=active.authorization_snapshot_ref,
            authorized_document_refs=self._tool_policy.authorized_document_refs,
            enterprise_scope_ref=self._tool_policy.enterprise_scope_ref,
        )
        guard_policy = ToolGuardPolicy(
            authorization_snapshot_ref=active.authorization_snapshot_ref,
            user_ref=execution_context.user_ref,
            tenant_ref=execution_context.tenant_ref,
            task_ref=task.task_id,
            runtime_enabled=self._tool_policy.runtime_enabled,
            allowed_tool_names=self._tool_policy.allowed_tool_names,
            allow_local=self._tool_policy.allow_local,
            allow_mcp=self._tool_policy.allow_mcp,
            allow_external_egress=self._tool_policy.allow_external_egress,
            approved_tool_names=self._tool_policy.approved_tool_names,
        )
        return ToolCallBatchBoundary(
            registry_snapshot=active.registry_snapshot,
            execution_context=execution_context,
            guard_policy=guard_policy,
            deadline=ExecutionDeadline(
                expires_at=now + timedelta(seconds=self._tool_policy.timeout_seconds)
            ),
        )


class PersistedAnswerCapabilityBoundaryProvider(_PersistedCapabilityBoundaryBase):
    """Rebind one Answer draft to fresh Context and Runtime-owned authority."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalBoundaryInputPolicy,
        context_assembler: ContextAssemblerRuntime,
        authority_projector: PersistedAnswerAuthorityProjector | None = None,
    ) -> None:
        super().__init__(repository, policy=policy)
        self._context_assembler = context_assembler
        self._authority_projector = (
            authority_projector or ReceiptOnlyAnswerAuthorityProjector()
        )

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        draft: AnswerDraft,
    ) -> AnswerCapabilityBoundary:
        if action.action_kind is not AgentActionKind.ANSWER:
            raise PersistedCapabilityBoundaryRejected(
                "Answer boundary received another capability kind"
            )
        active = self._load_active_scope(task=task, action=action)
        context = await self._assemble_context(
            task=task,
            active=active,
            assembler=self._context_assembler,
            consumer=ContextConsumer.MAIN_AGENT,
        )
        grounding, citation = self._authority_projector.project(
            task=task,
            context=context,
            draft=draft,
        )
        return AnswerCapabilityBoundary(
            context=context,
            grounding_snapshot=grounding,
            citation_authority_snapshot=citation,
            active_slot_refs=(),
            previous_response=None,
            supersede_reason=None,
        )


@dataclass(frozen=True, slots=True)
class PersistedCapabilityAdapterFactories:
    """Explicit repository-scoped C04-1 factories with no construction I/O."""

    boundary_policy: LocalBoundaryInputPolicy
    context_assembler: Callable[[PureAgentRepository], ContextAssemblerRuntime]
    tool_policy: PersistedToolBoundaryPolicy = PersistedToolBoundaryPolicy()
    answer_authority_projector: PersistedAnswerAuthorityProjector | None = None
    answer_authority_projector_factory: (
        Callable[[PureAgentRepository], PersistedAnswerAuthorityProjector] | None
    ) = None
    complexity_gate: Callable[[], DefaultComplexityGate] = DefaultComplexityGate
    clock: Callable[[], datetime] | None = None

    def __post_init__(self) -> None:
        if self.boundary_policy.registry_snapshot is None:
            raise TypeError("persisted capability adapters require a Registry snapshot")
        for name in ("context_assembler", "complexity_gate"):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} capability adapter factory is not callable")
        if self.clock is not None and not callable(self.clock):
            raise TypeError("clock capability adapter factory is not callable")
        if (
            self.answer_authority_projector is not None
            and self.answer_authority_projector_factory is not None
        ):
            raise TypeError(
                "configure either one Answer authority projector or its factory"
            )
        if (
            self.answer_authority_projector_factory is not None
            and not callable(self.answer_authority_projector_factory)
        ):
            raise TypeError("Answer authority projector factory is not callable")

    def plan_boundary(
        self,
        repository: PureAgentRepository,
    ) -> PersistedPlanCapabilityBoundaryProvider:
        return PersistedPlanCapabilityBoundaryProvider(
            repository,
            policy=self.boundary_policy,
            context_assembler=self.context_assembler(repository),
            complexity_gate=self.complexity_gate(),
        )

    def tool_boundary(
        self,
        repository: PureAgentRepository,
    ) -> PersistedToolCallBatchBoundaryProvider:
        return PersistedToolCallBatchBoundaryProvider(
            repository,
            policy=self.boundary_policy,
            tool_policy=self.tool_policy,
            clock=self.clock,
        )

    def answer_boundary(
        self,
        repository: PureAgentRepository,
    ) -> PersistedAnswerCapabilityBoundaryProvider:
        projector = self.answer_authority_projector
        if self.answer_authority_projector_factory is not None:
            projector = self.answer_authority_projector_factory(repository)
        return PersistedAnswerCapabilityBoundaryProvider(
            repository,
            policy=self.boundary_policy,
            context_assembler=self.context_assembler(repository),
            authority_projector=projector,
        )

    def capability_executors(
        self,
        *,
        planner: Callable[[], PlannerRuntime],
        tool_gateway: Callable[[PureAgentRepository], ToolBatchGatewayPort],
    ) -> CapabilityExecutorFactories:
        """Expose explicit C02-3 composition; no capability is invoked here."""

        return CapabilityExecutorFactories(
            planner=planner,
            plan_boundary=self.plan_boundary,
            tool_boundary=self.tool_boundary,
            tool_gateway=tool_gateway,
            answer_boundary=self.answer_boundary,
        )
