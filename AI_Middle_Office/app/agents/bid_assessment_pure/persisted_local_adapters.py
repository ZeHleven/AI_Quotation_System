"""Concrete local persistence adapters for the Pure Agent Runtime.

These adapters freeze existing SQL state plus explicitly injected local policy.
They never classify intent, choose an Action, create missing Budget accounts,
call a model, retrieve RAG evidence, execute a Tool, or grant authorization.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, model_validator

from .action_runtime import ActionReservationIntent, AgentActionKind
from .common import Reference, StrictContract
from .main_agent_boundary import (
    MainAgentBoundaryAssemblyInputs,
    MainAgentTurn,
)
from .repository import PureAgentPersistenceError, PureAgentRepository
from .runtime import ContextProfile, ModelContextProfile
from .runtime_controller import (
    PersistedRuntimeAction,
    RuntimeAdmissionContext,
    RuntimeActionRecoveryBinding,
    RunningActionRecoveryContext,
    RunningActionRecoveryUnavailable,
    RuntimeWakeup,
)
from .runtime_guards import (
    ActionRuntimeBinding,
    BudgetBalance,
    BudgetDemand,
    CancellationSnapshot,
    EffectFenceSnapshot,
    EffectFenceStatus,
    EffectReplayPolicy,
    ProgressWindow,
    RuntimeBudgetSnapshot,
    RuntimePolicyCeiling,
    RuntimeProfileSnapshot,
    RuntimeResourceType,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import RegistrySnapshot, canonical_hash


class PersistedLocalAdapterError(RuntimeError):
    """Safe base error for rejected local persistence projections."""


class PersistedLocalBoundaryRejected(PersistedLocalAdapterError):
    """Task, Conversation, Turn, or authorization scope lost its fence."""


class PersistedAdmissionContextRejected(PersistedLocalAdapterError):
    """The persisted Action admission boundary is incomplete or stale."""


class LocalBoundaryInputPolicy(StrictContract):
    """Explicit, already-authorized policy supplied to the local adapter."""

    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    authorization_policy_ref: Reference
    model_profile: ModelContextProfile
    context_profile: ContextProfile
    registry_snapshot: RegistrySnapshot | None = None
    information_need_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=63,
    )
    required_resource_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=127,
    )

    @model_validator(mode="after")
    def validate_refs(self) -> "LocalBoundaryInputPolicy":
        for field_name in ("information_need_refs", "required_resource_refs"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if (
            self.context_profile.reserved_output_tokens
            > self.model_profile.max_output_tokens
        ):
            raise ValueError("Context output reservation exceeds the model profile")
        if self.context_profile.effective_input_budget(self.model_profile) < 1:
            raise ValueError("Context and model profiles leave no input budget")
        return self


class LocalActionAdmissionRule(StrictContract):
    """One Action kind's explicit Guard binding; this is policy, not a route."""

    action_kind: AgentActionKind
    binding: ActionRuntimeBinding
    budget_demands: tuple[BudgetDemand, ...] = Field(min_length=1, max_length=16)
    expected_output_contract_ref: Reference

    @model_validator(mode="after")
    def validate_demands(self) -> "LocalActionAdmissionRule":
        resources = tuple(item.resource_type for item in self.budget_demands)
        if len(resources) != len(set(resources)):
            raise ValueError("Action Budget demand resources must be unique")
        if not set(self.binding.required_budget_resources).issubset(set(resources)):
            raise ValueError("Action rule omits a binding-required Budget demand")
        return self


class LocalAdmissionPolicy(StrictContract):
    """Complete explicit Guard policy for the six open Action capabilities."""

    policy: RuntimePolicyCeiling
    profile: RuntimeProfileSnapshot
    rules: tuple[LocalActionAdmissionRule, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_policy(self) -> "LocalAdmissionPolicy":
        kinds = tuple(rule.action_kind for rule in self.rules)
        if len(kinds) != len(set(kinds)):
            raise ValueError("local Action admission rules must be unique")
        missing = set(AgentActionKind) - set(kinds)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"local Action admission policy is incomplete: {names}")
        if (
            self.profile.policy_ref != self.policy.policy_ref
            or self.profile.policy_hash != self.policy.policy_hash
        ):
            raise ValueError("Runtime Profile is outside its Policy snapshot")
        profile_limits = self.profile.limits.model_dump(mode="python")
        policy_limits = self.policy.limits.model_dump(mode="python")
        if any(
            profile_limits[name] > policy_limits[name]
            for name in profile_limits
        ):
            raise ValueError("Runtime Profile exceeds its Policy ceiling")
        return self

    def rule_for(self, action_kind: AgentActionKind) -> LocalActionAdmissionRule:
        for rule in self.rules:
            if rule.action_kind is action_kind:
                return rule
        raise LookupError(f"Action admission rule is missing: {action_kind.value}")


class PersistedLocalBoundaryInputsProvider:
    """Build Main Agent Boundary Inputs from durable scope and local policy."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalBoundaryInputPolicy,
    ) -> None:
        self._repository = repository
        self._policy = policy

    async def prepare(
        self,
        *,
        task: AgentTaskState,
        turn: MainAgentTurn,
        wakeup: RuntimeWakeup,
    ) -> MainAgentBoundaryAssemblyInputs:
        try:
            scope = self._repository.load_local_task_scope(
                task_id=task.task_id,
                conversation_id=task.session_id,
            )
        except PureAgentPersistenceError as exc:
            raise PersistedLocalBoundaryRejected(
                "persisted local Boundary scope is unavailable"
            ) from exc
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or scope.conversation_status != "active"
            or scope.task_state_version != task.state_version
            or scope.goal_ref != task.goal_ref
            or scope.plan_ref != task.plan_ref
            or scope.cancellation_fence_ref is not None
            or turn.task_ref != task.task_id
            or turn.conversation_ref != task.session_id
            or wakeup.task_ref != task.task_id
            or wakeup.conversation_ref != task.session_id
        ):
            raise PersistedLocalBoundaryRejected(
                "persisted local Boundary no longer matches the running Task"
            )

        registry = self._policy.registry_snapshot
        authorization_body = {
            "authorization_policy_ref": self._policy.authorization_policy_ref,
            "policy_snapshot_ref": self._policy.policy_snapshot_ref,
            "task_ref": scope.task_id,
            "task_state_version": scope.task_state_version,
            "task_row_version": scope.task_row_version,
            "conversation_ref": scope.conversation_id,
            "conversation_row_version": scope.conversation_row_version,
            "owner_ref": f"user:{scope.owner_id}",
            "tenant_ref": scope.tenant_ref,
            "assessment_ref": self._assessment_ref(scope.assessment_id),
            "turn_ref": turn.turn_ref,
            "turn_message_ref": turn.message_ref,
            "turn_message_hash": turn.message_content_hash,
            "registry_snapshot_ref": (
                None if registry is None else registry.snapshot_ref
            ),
            "registry_snapshot_hash": (
                None if registry is None else registry.snapshot_hash
            ),
            "visible_tools_hash": (
                None if registry is None else registry.visible_tools_hash
            ),
        }
        authorization_hash = canonical_hash(authorization_body)
        authorization_ref = (
            "authorization-snapshot:"
            + authorization_hash.removeprefix("sha256:")
        )
        information_needs = self._unique(
            (scope.goal_ref, *self._policy.information_need_refs)
        )
        assessment_ref = self._assessment_ref(scope.assessment_id)
        required_resources = self._unique(
            (
                *((assessment_ref,) if assessment_ref is not None else ()),
                *self._policy.required_resource_refs,
            )
        )
        return MainAgentBoundaryAssemblyInputs(
            policy_snapshot_ref=self._policy.policy_snapshot_ref,
            prompt_template_ref=self._policy.prompt_template_ref,
            authorization_snapshot_ref=authorization_ref,
            model_profile=self._policy.model_profile,
            context_profile=self._policy.context_profile,
            registry_snapshot=registry,
            information_need_refs=information_needs,
            required_resource_refs=required_resources,
            checkpoint_snapshot_ref=scope.latest_checkpoint_ref,
        )

    @staticmethod
    def _assessment_ref(assessment_id: str | None) -> str | None:
        return None if assessment_id is None else f"assessment:{assessment_id}"

    @staticmethod
    def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))


class PersistedRuntimeAdmissionContextProvider:
    """Project persisted Guard inputs for one model-selected Action intent."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: LocalAdmissionPolicy,
        current_registry_snapshot_hash: str | None = None,
        authorization_policy_ref: str | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._current_registry_snapshot_hash = current_registry_snapshot_hash
        self._authorization_policy_ref = authorization_policy_ref

    def for_action(
        self,
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
    ) -> RuntimeAdmissionContext:
        self._validate_task_intent(task=task, intent=intent)
        try:
            scope = self._repository.load_local_task_scope(
                task_id=task.task_id,
                conversation_id=task.session_id,
            )
            context = self._repository.load_context_snapshot(
                task_id=task.task_id,
                snapshot_ref=intent.context_snapshot_ref,
            )
            persisted_balances = self._repository.list_runtime_budget_balances(
                task_id=task.task_id
            )
        except PureAgentPersistenceError as exc:
            raise PersistedAdmissionContextRejected(
                "persisted Runtime admission inputs are unavailable"
            ) from exc
        if (
            scope.conversation_status != "active"
            or scope.task_state_version != task.state_version
            or scope.goal_ref != task.goal_ref
            or scope.plan_ref != task.plan_ref
            or context.snapshot_hash != intent.context_snapshot_hash
            or context.task_ref != task.task_id
            or context.state_version > task.state_version
            or context.registry_snapshot_ref != intent.registry_snapshot_ref
            or context.registry_snapshot_hash != intent.registry_snapshot_hash
        ):
            raise PersistedAdmissionContextRejected(
                "persisted Runtime admission boundary is stale or cross-scoped"
            )

        rule = self._policy.rule_for(intent.action_kind)
        balances = self._budget_balances(persisted_balances)
        available_resources = {item.resource_type for item in balances}
        required_resources = {
            demand.resource_type for demand in rule.budget_demands
        }
        if not required_resources.issubset(available_resources):
            missing = ", ".join(
                sorted(item.value for item in required_resources - available_resources)
            )
            raise PersistedAdmissionContextRejected(
                f"persisted Runtime Budget accounts are incomplete: {missing}"
            )

        budget_basis = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "profile_hash": self._policy.profile.profile_hash,
            "balances": [item.model_dump(mode="json") for item in balances],
        }
        budget_ref = (
            "runtime-budget-snapshot:"
            + canonical_hash(budget_basis).removeprefix("sha256:")
        )
        budget_snapshot = RuntimeBudgetSnapshot.build(
            snapshot_ref=budget_ref,
            task_ref=task.task_id,
            profile=self._policy.profile,
            balances=balances,
        )
        scope_hash = self._scope_hash(
            task=task,
            intent=intent,
            scope=scope,
            context=context,
        )
        semantic_basis_hash = canonical_hash(
            {
                "action_kind": intent.action_kind.value,
                "arguments": intent.arguments,
            }
        )
        expected_output_hash = canonical_hash(
            {
                "action_kind": intent.action_kind.value,
                "contract_ref": rule.expected_output_contract_ref,
            }
        )
        effect_key = self._effect_key(
            task_ref=task.task_id,
            action_type=intent.action_kind.value,
            arguments_hash=intent.arguments_hash,
            binding_hash=rule.binding.binding_hash,
            scope_snapshot_hash=scope_hash,
        )
        try:
            persisted_effect = self._repository.load_runtime_effect_fence_by_key(
                task_id=task.task_id,
                effect_key=effect_key,
            )
        except PureAgentPersistenceError as exc:
            raise PersistedAdmissionContextRejected(
                "persisted Effect Fence could not be read"
            ) from exc
        existing_effect = self._effect_snapshot(persisted_effect)

        # C03-1 has no separate progress table.  Bind the empty Guard window to
        # the durable accepted-observation head without inventing progress facts.
        progress_basis = {
            "task_ref": task.task_id,
            "state_version": task.state_version,
            "accepted_observation_refs": list(task.observation_refs),
        }
        progress_window = ProgressWindow.build(
            window_ref=(
                "progress-window:"
                + canonical_hash(progress_basis).removeprefix("sha256:")
            ),
            task_ref=task.task_id,
        )
        cancellation = CancellationSnapshot(
            task_ref=task.task_id,
            state_version=task.state_version,
            cancellation_fence_ref=scope.cancellation_fence_ref,
        )
        return RuntimeAdmissionContext(
            binding=rule.binding,
            profile=self._policy.profile,
            policy=self._policy.policy,
            budget_snapshot=budget_snapshot,
            budget_demands=rule.budget_demands,
            progress_window=progress_window,
            cancellation=cancellation,
            semantic_basis_hash=semantic_basis_hash,
            existing_effect=existing_effect,
            scope_snapshot_hash=scope_hash,
            expected_output_hash=expected_output_hash,
            # The Controller admits one Task Action at a time.  Internal Tool
            # batch concurrency remains owned by Tool Gateway/Executor policy.
            active_parallel_reads=0,
            fencing_token=(
                1 if existing_effect is None else existing_effect.fencing_token
            ),
            authorization_policy_ref=self._authorization_policy_ref,
        )

    def for_recovery(
        self,
        *,
        task: AgentTaskState,
        action: PersistedRuntimeAction,
        binding: RuntimeActionRecoveryBinding,
    ) -> RunningActionRecoveryContext:
        """Revalidate current local heads without granting replay authority."""

        intent = action.envelope.intent
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref != action.action_ref
            or intent.task_ref != task.task_id
            or intent.state_version + 1 != task.state_version
        ):
            raise RunningActionRecoveryUnavailable(
                "Running Action recovery lost its Task fence"
            )
        try:
            scope = self._repository.load_local_task_scope(
                task_id=task.task_id,
                conversation_id=task.session_id,
            )
            context = self._repository.load_context_snapshot(
                task_id=task.task_id,
                snapshot_ref=intent.context_snapshot_ref,
            )
        except PureAgentPersistenceError as exc:
            raise RunningActionRecoveryUnavailable(
                "persisted Running Action recovery inputs are unavailable"
            ) from exc

        scope_hash = self._scope_hash(
            task=task,
            intent=intent,
            scope=scope,
            context=context,
        )
        base_heads_valid = bool(
            scope.conversation_status == "active"
            and scope.task_state_version == task.state_version
            and scope.goal_ref == task.goal_ref
            and scope.plan_ref == task.plan_ref
            and context.snapshot_hash == intent.context_snapshot_hash
            and context.task_ref == task.task_id
            and context.state_version <= task.state_version
            and context.registry_snapshot_ref == intent.registry_snapshot_ref
            and context.registry_snapshot_hash == intent.registry_snapshot_hash
        )
        authorization_valid = bool(
            base_heads_valid
            and self._authorization_policy_ref is not None
            and self._authorization_policy_ref
            == binding.authorization_policy_ref
            and context.authorization_snapshot_ref
        )
        source_heads_valid = bool(
            base_heads_valid and scope_hash == binding.scope_snapshot_hash
        )
        return RunningActionRecoveryContext(
            profile=self._policy.profile,
            current_registry_snapshot_hash=self._current_registry_snapshot_hash,
            cancellation=CancellationSnapshot(
                task_ref=task.task_id,
                state_version=task.state_version,
                cancellation_fence_ref=scope.cancellation_fence_ref,
            ),
            # The local Runtime has no detached worker lease.  Terminal results
            # can be consumed immediately; non-terminal replay remains disabled
            # by RunningActionRecoveryController even when the Guard suggests it.
            lease_expired=True,
            authorization_valid=authorization_valid,
            source_heads_valid=source_heads_valid,
            retry_attempt_count=0,
        )

    @staticmethod
    def _validate_task_intent(
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
    ) -> None:
        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or intent.task_ref != task.task_id
            or intent.state_version != task.state_version
        ):
            raise PersistedAdmissionContextRejected(
                "Action intent is not at the current running Task boundary"
            )

    @staticmethod
    def _scope_hash(
        *,
        task: AgentTaskState,
        intent: ActionReservationIntent,
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
                "assessment_ref": PersistedLocalBoundaryInputsProvider._assessment_ref(
                    scope.assessment_id
                ),
                "context_snapshot_ref": context.snapshot_ref,
                "context_snapshot_hash": context.snapshot_hash,
                "registry_snapshot_ref": context.registry_snapshot_ref,
                "registry_snapshot_hash": context.registry_snapshot_hash,
                "visible_tools_hash": intent.visible_tools_hash,
            }
        except AttributeError as exc:
            raise RunningActionRecoveryUnavailable(
                "persisted Running Action scope is invalid"
            ) from exc
        if body["task_ref"] != task.task_id:
            raise RunningActionRecoveryUnavailable(
                "persisted Running Action scope crossed Task boundaries"
            )
        return canonical_hash(body)

    @staticmethod
    def _budget_balances(rows: tuple[object, ...]) -> tuple[BudgetBalance, ...]:
        try:
            return tuple(
                BudgetBalance(
                    resource_type=RuntimeResourceType(row.resource_type),
                    unit=row.unit,
                    limit_amount=row.limit_amount,
                    reserved_amount=row.reserved_amount,
                    spent_amount=row.spent_amount,
                    row_version=row.row_version,
                )
                for row in rows
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise PersistedAdmissionContextRejected(
                "persisted Runtime Budget balance is invalid"
            ) from exc

    @staticmethod
    def _effect_key(
        *,
        task_ref: str,
        action_type: str,
        arguments_hash: str,
        binding_hash: str,
        scope_snapshot_hash: str,
    ) -> str:
        digest = canonical_hash(
            {
                "task_ref": task_ref,
                "action_type": action_type,
                "arguments_hash": arguments_hash,
                "binding_hash": binding_hash,
                "scope_snapshot_hash": scope_snapshot_hash,
            }
        )
        return f"action-effect:{digest.removeprefix('sha256:')}"

    @staticmethod
    def _effect_snapshot(row: object | None) -> EffectFenceSnapshot | None:
        if row is None:
            return None
        try:
            return EffectFenceSnapshot(
                effect_fence_ref=row.effect_fence_ref,
                task_ref=row.task_ref,
                action_ref=row.action_ref,
                effect_key=row.effect_key,
                request_hash=PersistedRuntimeAdmissionContextProvider._digest(
                    row.request_hash
                ),
                replay_policy=EffectReplayPolicy(row.replay_policy),
                status=EffectFenceStatus(row.status),
                fencing_token=row.fencing_token,
                result_ref=row.result_ref,
                result_hash=(
                    None
                    if row.result_hash is None
                    else PersistedRuntimeAdmissionContextProvider._digest(
                        row.result_hash
                    )
                ),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise PersistedAdmissionContextRejected(
                "persisted Effect Fence receipt is invalid"
            ) from exc

    @staticmethod
    def _digest(value: str) -> str:
        candidate = str(value)
        return candidate if candidate.startswith("sha256:") else f"sha256:{candidate}"


@dataclass(frozen=True, slots=True)
class PersistedLocalRuntimeAdapterFactories:
    """Explicit C02-4 factory pair for the two concrete C03-1 adapters."""

    boundary_policy: LocalBoundaryInputPolicy
    admission_policy: LocalAdmissionPolicy

    def main_agent_inputs(
        self,
        repository: PureAgentRepository,
    ) -> PersistedLocalBoundaryInputsProvider:
        return PersistedLocalBoundaryInputsProvider(
            repository,
            policy=self.boundary_policy,
        )

    def admission_context(
        self,
        repository: PureAgentRepository,
    ) -> PersistedRuntimeAdmissionContextProvider:
        return PersistedRuntimeAdmissionContextProvider(
            repository,
            policy=self.admission_policy,
            current_registry_snapshot_hash=(
                None
                if self.boundary_policy.registry_snapshot is None
                else self.boundary_policy.registry_snapshot.snapshot_hash
            ),
            authorization_policy_ref=(
                self.boundary_policy.authorization_policy_ref
            ),
        )
