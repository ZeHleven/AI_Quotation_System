"""Replaceable component ports for the Pure Agent runtime skeleton."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from .answer_contracts import (
    AnswerDraft,
    AnswerDraftValidationDecision,
    GroundingSnapshot,
)
from .action_runtime import (
    ActionLoopDecision,
    ActionObservation,
    ActionReservationIntent,
)
from .citation_contracts import (
    CitationAuthoritySnapshot,
    CitationProjectionDecision,
    RenderedAnswerCandidate,
)
from .memory_runtime import (
    MemoryAuthorizationContext,
    MemoryCandidate,
    MemoryCommitOutcome,
    MemoryProjection,
    MemoryReadRequest,
    MemoryRecord,
    MemoryValidity,
)
from .planning import ComplexityDecision, IntentUnderstanding, PlanRevision
from .planner_runtime import PlanRevisionReason
from .provider_runtime import ProviderInvocationRequest, ProviderModelResult
from .registry import CanonicalToolRegistry
from .response_contracts import (
    ResponseCommitDecision,
    ResponsePersistenceEnvelope,
    ResponseStaleIntent,
    ResponseStaleReason,
    ResponseSupersedeReason,
    ResponseVersionHead,
)
from .runtime import (
    ContextAssemblyRequest,
    ContextAssemblyResult,
    ContextProfile,
    ContextSnapshot,
    ModelContextProfile,
    ToolCallRequest,
)
from .runtime_guards import (
    ActionAdmissionDecision,
    ActionRuntimeBinding,
    BudgetDemand,
    CancellationSnapshot,
    EffectFenceSnapshot,
    ProgressWindow,
    RuntimeBudgetSnapshot,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
)
from .slots import ContinuationCheckpoint, SlotValidationOutcome
from .state import AgentTaskState
from .tool_gateway import ToolGatewayOutcome
from .tool_runtime import (
    ExecutionDeadline,
    GuardDecision,
    RegistrySnapshot,
    ToolGuardPolicy,
)
from .tools import CanonicalToolDefinition, ToolExecutionContext


class ActionLoopPort(Protocol):
    """Advance exactly one Main Agent decision boundary, never an outer loop."""

    def prepare_decision_reservation_intent(
        self,
        *,
        task: AgentTaskState,
        turn_ref: str,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ActionReservationIntent: ...

    async def decide_next(
        self,
        *,
        task: AgentTaskState,
        turn_ref: str,
        decision_action_ref: str,
        decision_sequence: int,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ActionLoopDecision: ...

    def build_decision_observation(
        self,
        *,
        task: AgentTaskState,
        decision: ActionLoopDecision,
        action_sequence: int,
    ) -> ActionObservation: ...

    def build_validated_answer_observation(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        action_sequence: int,
        validation: AnswerDraftValidationDecision,
    ) -> ActionObservation: ...

    def prepare_reservation_intent(
        self,
        *,
        task: AgentTaskState,
        decision: ActionLoopDecision,
        decision_observation: ActionObservation,
    ) -> ActionReservationIntent: ...


class ActionCapabilityPort(Protocol):
    """Execute one already-accepted capability action through injected bindings."""

    async def execute(
        self,
        *,
        task: AgentTaskState,
        action_ref: str,
        action_sequence: int,
        intent: ActionReservationIntent,
    ) -> ActionObservation: ...


class ActionAdmissionGuardPort(Protocol):
    """Govern one proposed Action without selecting its business purpose."""

    def admit_action(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
        binding: ActionRuntimeBinding,
        profile: RuntimeProfileSnapshot,
        policy: RuntimePolicyCeiling,
        budget_snapshot: RuntimeBudgetSnapshot,
        budget_demands: tuple[BudgetDemand, ...],
        progress_window: ProgressWindow,
        cancellation: CancellationSnapshot,
        existing_effect: EffectFenceSnapshot | None = None,
        semantic_basis_hash: str,
        scope_snapshot_hash: str | None = None,
        expected_output_hash: str | None = None,
        active_parallel_reads: int = 0,
    ) -> ActionAdmissionDecision: ...


class IntentUnderstandingPort(Protocol):
    async def understand(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
    ) -> IntentUnderstanding: ...


class ComplexityGatePort(Protocol):
    def decide(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
    ) -> ComplexityDecision: ...


class PlannerPort(Protocol):
    async def create_or_revise(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        complexity: ComplexityDecision,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        previous_plan: PlanRevision | None = None,
        revision_reasons: tuple[PlanRevisionReason, ...] = (),
    ) -> PlanRevision: ...


class ContextAssemblerPort(Protocol):
    async def assemble(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
        model_profile: ModelContextProfile,
        context_profile: ContextProfile,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ContextAssemblyResult: ...


class MemoryPort(Protocol):
    async def project(self, request: MemoryReadRequest) -> MemoryProjection: ...


class MemoryCommitPort(Protocol):
    async def commit(
        self,
        *,
        task: AgentTaskState,
        candidate: MemoryCandidate,
        authorization: MemoryAuthorizationContext,
        now: datetime | None = None,
    ) -> MemoryCommitOutcome: ...

    async def forget(
        self,
        *,
        memory_ref: str,
        authorization: MemoryAuthorizationContext,
        policy_ref: str,
        mutation_ref: str,
        now: datetime | None = None,
    ) -> MemoryRecord: ...

    async def invalidate_by_source(
        self,
        *,
        source_ref: str,
        validity: MemoryValidity,
        authorization: MemoryAuthorizationContext,
        policy_ref: str,
        mutation_ref: str,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]: ...


class ModelPort(Protocol):
    async def invoke(self, request: ProviderInvocationRequest) -> ProviderModelResult: ...


class ToolRouterPort(Protocol):
    def visible_tool_names(
        self,
        *,
        task: AgentTaskState,
        understanding: IntentUnderstanding,
        registry: CanonicalToolRegistry,
    ) -> tuple[str, ...]: ...


class VisibilityGuardPort(Protocol):
    def evaluate(
        self,
        *,
        definition: CanonicalToolDefinition,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> GuardDecision: ...


class ExecutionGuardPort(Protocol):
    async def evaluate(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> GuardDecision: ...


class PermissionGuardPort(Protocol):
    """Container port that keeps visibility and execution authority separate."""

    visibility_guard: VisibilityGuardPort
    execution_guard: ExecutionGuardPort


class ToolGatewayPort(Protocol):
    async def execute(
        self,
        *,
        call: ToolCallRequest,
        task: AgentTaskState,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
        deadline: ExecutionDeadline,
    ) -> ToolGatewayOutcome: ...


class SlotInputModelRegistryPort(Protocol):
    def resolve(self, input_model_ref: str) -> type[BaseModel]: ...


class SlotBusinessValidationPort(Protocol):
    async def validate(
        self,
        *,
        validator_refs: tuple[str, ...],
        candidate: BaseModel,
    ) -> SlotValidationOutcome: ...


class CheckpointPort(Protocol):
    async def save(self, checkpoint: ContinuationCheckpoint) -> None: ...

    async def load(self, checkpoint_ref: str) -> ContinuationCheckpoint: ...


class AnswerDraftGuardPort(Protocol):
    """Validate one complete draft; never render, publish, or choose an action."""

    def validate(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
        grounding_snapshot: GroundingSnapshot,
        active_slot_refs: tuple[str, ...] = (),
    ) -> AnswerDraftValidationDecision: ...


class CitationProjectorPort(Protocol):
    """Project safe Citations from accepted Grounding and Runtime authority."""

    def project(
        self,
        *,
        task: AgentTaskState,
        context: ContextAssemblyResult,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        grounding_snapshot: GroundingSnapshot,
        authority_snapshot: CitationAuthoritySnapshot,
    ) -> CitationProjectionDecision: ...


class AnswerRendererPort(Protocol):
    """Render accepted generic Blocks; never publish or invent claims."""

    def render(
        self,
        *,
        task: AgentTaskState,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        citation_decision: CitationProjectionDecision,
    ) -> RenderedAnswerCandidate: ...


class AnswerCommitterPort(Protocol):
    """Prepare one immutable Response commit; never persist or publish it."""

    def prepare(
        self,
        *,
        task: AgentTaskState,
        context_snapshot: ContextSnapshot,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        citation_decision: CitationProjectionDecision,
        rendered: RenderedAnswerCandidate,
        answer_observation: ActionObservation,
        previous_response: ResponseVersionHead | None = None,
        supersede_reason: ResponseSupersedeReason | None = None,
    ) -> ResponseCommitDecision: ...


class ResponseVersionControllerPort(Protocol):
    """Govern validity events separately from immutable answer content."""

    def prepare_stale(
        self,
        *,
        head: ResponseVersionHead,
        reason: ResponseStaleReason,
        cause_ref: str,
        idempotency_key: str,
    ) -> ResponseStaleIntent: ...

    def apply_stale(
        self,
        *,
        envelope: ResponsePersistenceEnvelope,
        intent: ResponseStaleIntent,
        occurred_at: datetime,
    ) -> tuple[ResponsePersistenceEnvelope, bool]: ...
