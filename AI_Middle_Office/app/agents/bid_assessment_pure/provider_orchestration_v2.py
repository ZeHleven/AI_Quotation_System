"""Default-disabled Decision/Answer orchestration for Provider Boundary V2.

This is one dynamic decision boundary, not a business workflow.  A Provider may
return native Function Calls or a small next-action decision.  Only an accepted
``answer`` decision permits one answer-only Provider invocation. Plan, Replan,
and information actions may request only their locked action-specific payload.
Next-action and projection validation allows at most one bounded recovery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Never, Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator

from .action_runtime import (
    ActionLoopContractRejected,
    InformationRequestAction,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    PlanActionRequest,
)
from .answer_contracts import SourceBasis
from .common import Reference, StrictContract, ToolName
from .provider_answer_projection_v2 import (
    ProviderAnswerProjectionV2,
    provider_answer_business_rules_v2,
)
from .provider_decision_v2 import (
    ANSWER_PROJECTION_CONTRACT_REF,
    LOCKED_INFORMATION_PAYLOAD_CONTRACT_REF,
    LOCKED_PLAN_PAYLOAD_CONTRACT_REF,
    NEXT_ACTION_CONTRACT_REF,
    ProviderAnswerAuthorizationKind,
    ProviderAnswerGenerationOutcome,
    ProviderAnswerGenerationRequest,
    ProviderInformationRequestPayloadV2,
    ProviderNextActionDecision,
    ProviderNextActionOutcome,
    ProviderNextActionRecoveryConstraintV2,
    ProviderRetrievalRequest,
    ProviderLockedActionPayloadOutcome,
    RuntimeTerminalAnswerAuthorizationV2,
)
from .provider_ingress_adapter_v2 import (
    DeterministicProviderJsonIngressAdapter,
)
from .provider_ingress_v2 import (
    ProviderBoundaryFailure,
    ProviderBoundaryFailureCode,
    ProviderBoundaryFailureStage,
    ProviderBoundaryRejected,
    ProviderIngressNormalizationStep,
    ProviderIngressPayloadKind,
    ProviderIngressReceipt,
    ProviderIngressRequest,
    ProviderValidationIssue,
)
from .provider_runtime import (
    ProviderAdapter,
    ProviderAdapterError,
    ProviderErrorCode,
    ProviderInvocationRequest,
    ProviderModelResult,
    ProviderOutputKind,
    ProviderRuntimeInput,
    ProviderStrictMode,
    ProviderStructuredOutputSpec,
    ProviderToolCallProposal,
    ProviderToolChoice,
)
from .retrieval_convergence_v2 import RetrievalConvergenceDecisionV2
from .runtime import (
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextConsumer,
    ContextEntryKind,
)
from .slot_validation import SlotCapabilitySnapshot
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


_MAX_REPAIR_PROJECTION_BYTES = 64 * 1024
_NEXT_ACTION_ADVISORY_SOURCE_BASES = tuple(item.value for item in SourceBasis)
_JSON_ENVELOPE_RECOVERABLE_CODES = frozenset(
    {
        ProviderBoundaryFailureCode.JSON_ENCODING_INVALID,
        ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
        ProviderBoundaryFailureCode.JSON_DUPLICATE_KEY,
        ProviderBoundaryFailureCode.JSON_MULTIPLE_OBJECTS,
        ProviderBoundaryFailureCode.JSON_TRUNCATED,
        ProviderBoundaryFailureCode.JSON_NON_OBJECT,
    }
)


class ProviderDecisionCycleBranch(str, Enum):
    TOOL_CALLS = "tool_calls"
    NEXT_ACTION = "next_action"
    ANSWER = "answer"


class ProviderToolCallIngressBinding(StrictContract):
    provider_tool_call_id: Reference
    tool_name: ToolName
    arguments_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ingress_receipt: ProviderIngressReceipt

    @model_validator(mode="after")
    def validate_binding(self) -> "ProviderToolCallIngressBinding":
        if self.ingress_receipt.payload_kind is not (
            ProviderIngressPayloadKind.TOOL_ARGUMENTS
        ):
            raise ValueError("Tool Call binding requires a Tool arguments receipt")
        if self.ingress_receipt.normalized_payload_hash != self.arguments_hash:
            raise ValueError("Tool Call arguments drifted from Provider ingress")
        return self


class ProviderToolCallsOutcomeV2(StrictContract):
    outcome_ref: Reference
    outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    task_ref: Reference
    origin_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    registry_snapshot_ref: Reference
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference
    proposals: tuple[ProviderToolCallProposal, ...] = Field(
        min_length=1,
        max_length=64,
    )
    ingress_bindings: tuple[ProviderToolCallIngressBinding, ...] = Field(
        min_length=1,
        max_length=64,
    )

    @classmethod
    def build(
        cls,
        *,
        request: MainAgentDecisionRequest,
        result: ProviderModelResult,
        bindings: tuple[ProviderToolCallIngressBinding, ...],
    ) -> "ProviderToolCallsOutcomeV2":
        if request.registry_snapshot_ref is None:
            raise ValueError("Tool Call outcome requires a Registry Snapshot")
        if (
            result.output_kind is not ProviderOutputKind.TOOL_CALLS
            or result.task_ref != request.task_ref
            or result.state_version != request.origin_state_version
            or result.context_snapshot_ref != request.context_snapshot_ref
        ):
            raise ValueError("Provider Tool Calls result is stale or cross-scoped")
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": result.result_ref,
            "provider_response_hash": result.response_hash,
            "provider_receipt_ref": result.provider_receipt_ref,
            "proposals": [
                item.model_dump(mode="json") for item in result.tool_call_proposals
            ],
            "ingress_bindings": [
                item.model_dump(mode="json") for item in bindings
            ],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            outcome_ref=f"provider-tool-calls-v2:{digest.removeprefix('sha256:')}",
            outcome_hash=digest,
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderToolCallsOutcomeV2":
        proposal_ids = tuple(item.provider_tool_call_id for item in self.proposals)
        binding_ids = tuple(
            item.provider_tool_call_id for item in self.ingress_bindings
        )
        if proposal_ids != binding_ids or len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("Tool proposals and ingress bindings must align")
        for proposal, binding in zip(
            self.proposals,
            self.ingress_bindings,
            strict=True,
        ):
            if (
                proposal.tool_name != binding.tool_name
                or proposal.arguments_hash != binding.arguments_hash
            ):
                raise ValueError("Tool proposal drifted from its ingress binding")
            if (
                proposal.task_ref != self.task_ref
                or proposal.state_version != self.origin_state_version
                or proposal.context_snapshot_ref != self.context_snapshot_ref
                or proposal.registry_snapshot_ref != self.registry_snapshot_ref
            ):
                raise ValueError("Tool proposal is stale or cross-scoped")
        body = self.model_dump(mode="json", exclude={"outcome_ref", "outcome_hash"})
        digest = canonical_hash(body)
        if self.outcome_hash != digest:
            raise ValueError("outcome_hash does not match Tool Calls outcome")
        if self.outcome_ref != (
            f"provider-tool-calls-v2:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("outcome_ref does not match Tool Calls outcome")
        return self


class ProviderAnswerContextBundle(StrictContract):
    context: ContextAssemblyResult
    guard_feedback_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    response_language_hint: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
    )

    @model_validator(mode="after")
    def validate_bundle(self) -> "ProviderAnswerContextBundle":
        if len(self.guard_feedback_refs) != len(set(self.guard_feedback_refs)):
            raise ValueError("guard_feedback_refs must be unique")
        return self


class ProviderAnswerContextProvider(Protocol):
    async def assemble_answer_context(
        self,
        *,
        decision_request: MainAgentDecisionRequest,
        next_action: ProviderNextActionOutcome,
    ) -> ProviderAnswerContextBundle: ...


class ProviderDecisionAnswerCycleResult(StrictContract):
    branch: ProviderDecisionCycleBranch
    tool_calls: ProviderToolCallsOutcomeV2 | None = None
    next_action: ProviderNextActionOutcome | None = None
    answer_request: ProviderAnswerGenerationRequest | None = None
    answer: ProviderAnswerGenerationOutcome | None = None

    @model_validator(mode="after")
    def validate_branch(self) -> "ProviderDecisionAnswerCycleResult":
        shape = (
            self.tool_calls is not None,
            self.next_action is not None,
            self.answer_request is not None,
            self.answer is not None,
        )
        expected = {
            ProviderDecisionCycleBranch.TOOL_CALLS: (True, False, False, False),
            ProviderDecisionCycleBranch.NEXT_ACTION: (False, True, False, False),
            ProviderDecisionCycleBranch.ANSWER: (False, True, True, True),
        }[self.branch]
        if shape != expected:
            raise ValueError("Provider Decision/Answer cycle branch is inconsistent")
        if self.answer_request is not None and self.answer is not None:
            if self.answer.request_ref != self.answer_request.request_ref:
                raise ValueError("Answer outcome belongs to another Answer request")
            if self.next_action is None or (
                self.answer_request.authorization_kind
                is not ProviderAnswerAuthorizationKind.PROVIDER_NEXT_ACTION
                or
                self.answer_request.answer_authorization_ref
                != self.next_action.outcome_ref
                or self.answer_request.answer_authorization_hash
                != self.next_action.outcome_hash
            ):
                raise ValueError("Answer request belongs to another next action")
        return self


class ProviderDecisionAnswerOrchestratorV2:
    """Dynamic decision/Answer boundary with one bounded repair per call."""

    def __init__(
        self,
        *,
        adapter: ProviderAdapter | None = None,
        ingress: DeterministicProviderJsonIngressAdapter | None = None,
        answer_context_provider: ProviderAnswerContextProvider | None = None,
        slot_capability_snapshot: SlotCapabilitySnapshot | None = None,
    ) -> None:
        self._adapter = adapter or ProviderAdapter()
        self._ingress = ingress or DeterministicProviderJsonIngressAdapter()
        self._answer_context_provider = answer_context_provider
        self._slot_capability_snapshot = (
            slot_capability_snapshot or SlotCapabilitySnapshot.build()
        )

    async def decide_and_maybe_answer(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> ProviderDecisionAnswerCycleResult:
        self._require_enabled()
        decision = await self.decide_next_action(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        if isinstance(decision, ProviderToolCallsOutcomeV2):
            return ProviderDecisionAnswerCycleResult(
                branch=ProviderDecisionCycleBranch.TOOL_CALLS,
                tool_calls=decision,
            )
        if decision.decision.action_kind is not MainAgentModelActionKind.ANSWER:
            return ProviderDecisionAnswerCycleResult(
                branch=ProviderDecisionCycleBranch.NEXT_ACTION,
                next_action=decision,
            )
        if self._answer_context_provider is None:
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "answer selection requires an Answer Context provider",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        bundle = await self._answer_context_provider.assemble_answer_context(
            decision_request=request,
            next_action=decision,
        )
        answer_request, answer = await self.generate_answer(
            decision_request=request,
            next_action=decision,
            bundle=bundle,
        )
        return ProviderDecisionAnswerCycleResult(
            branch=ProviderDecisionCycleBranch.ANSWER,
            next_action=decision,
            answer_request=answer_request,
            answer=answer,
        )

    async def decide_next_action(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        convergence: RetrievalConvergenceDecisionV2 | None = None,
        allow_native_tool_calls: bool = True,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None = None,
    ) -> ProviderNextActionOutcome | ProviderToolCallsOutcomeV2:
        self._require_enabled()
        self._validate_decision_binding(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        if recovery_constraint is not None:
            if convergence is not None and convergence.saturated:
                self._reject(
                    ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                    "next-action recovery cannot bypass retrieval saturation",
                    stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
                )
            if not set(recovery_constraint.required_tool_names).issubset(
                request.visible_tool_names
            ):
                self._reject(
                    ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                    "next-action recovery requires a Tool outside the visible Registry",
                    stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
                )
        tools_allowed = (
            allow_native_tool_calls
            and bool(request.visible_tool_names)
            and not (convergence is not None and convergence.saturated)
        )
        tool_call_constraints = self._tool_call_constraints(
            tools_allowed=tools_allowed
        )
        allowed_action_kinds = self._allowed_action_kinds(
            convergence,
            recovery_constraint=recovery_constraint,
        )
        slot_instruction = (
            " request_information is available only for an exact slot_kind from "
            "available_slot_capabilities; the Runtime owns all input and business "
            "validator references."
            if self._slot_capability_snapshot.definitions
            else (
                " request_information is unavailable because the Runtime has no "
                "executable Slot definition; select another allowed action and "
                "state evidence limitations in the answer when necessary."
            )
        )
        instruction = (
            "The Runtime Answer Guard requires an evidence upgrade before another "
            "Answer may be attempted. Select retrieve and request exactly the "
            f"required Tool names {list(recovery_constraint.required_tool_names)}. "
            "Use the readable candidate_refs in next_action_recovery_constraint; "
            "do not answer, plan, replan, or request user information."
            if recovery_constraint is not None
            else
            "Retrieval is saturated and Tool use is disabled for this decision. "
            f"Select only one action from {allowed_action_kinds}. Answer with explicit "
            "limitations when the available evidence is incomplete. "
            "target_source_bases is advisory only: use exact allowed values or "
            "an empty array."
            + slot_instruction
            if convergence is not None and convergence.saturated
            else (
                "No Tools are exposed in this control decision. Select retrieve "
                "only when external evidence or computation is required, and then "
                "populate retrieval_request with concrete unresolved information "
                "needs plus the smallest exact requested_tool_names set. Greetings, "
                "casual conversation, and questions already answerable from accepted "
                "Context must select answer. Use plan or replan only for genuinely "
                "complex tasks. Do not include Tool arguments, a Plan payload, Slot "
                "schema, or answer text. target_source_bases is advisory only: use "
                "exact allowed values or an empty array."
                + slot_instruction
                if not allow_native_tool_calls
                else (
                    "Select native Function Calls only when they can add new evidence "
                    "or computation. Return at most "
                    f"{tool_call_constraints['max_calls_per_response']} "
                    "non-overlapping, highest-value Tool Calls in this response. "
                    "If more work may be needed, defer it to a later decision. "
                    "Otherwise return the small next-action object; "
                    "do not include a Plan, Slot schema, Tool arguments, or answer text. "
                    "Every Function Call must bind to one concrete unresolved "
                    "information need from the current user request or accepted Context; "
                    "never call a Tool speculatively or merely because it is visible. "
                    "target_source_bases is advisory only: use exact allowed values or "
                    "an empty array."
                    + slot_instruction
                )
            )
        )
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=f"{request.request_ref}:v2-next-action",
            input_kind="main_agent_next_action_v2",
            payload={
                "request": request.model_dump(mode="json"),
                "decision_mode": (
                    "tool_capable" if tools_allowed else "control_only"
                ),
                "instruction": instruction,
                "tool_call_constraints": tool_call_constraints,
                "allowed_action_kinds": allowed_action_kinds,
                "available_slot_capabilities": (
                    self._model_visible_slot_capabilities()
                ),
                "next_action_schema": (
                    ProviderNextActionDecision.model_json_schema()
                ),
                "next_action_advisory_contract": (
                    self._next_action_advisory_contract()
                ),
                "retrieval_convergence": (
                    None
                    if convergence is None
                    else convergence.model_dump(mode="json")
                ),
                "next_action_recovery_constraint": (
                    None
                    if recovery_constraint is None
                    else recovery_constraint.model_dump(mode="json")
                ),
            },
        )
        provider_recovery_used = False
        repair_attempt = 0
        repaired_from_response_hash: str | None = None
        repair_validation_issues: tuple[ProviderValidationIssue, ...] = ()
        try:
            result = await self._invoke_next_action(
                request=request,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=runtime_input,
                call_kind="next-action",
                tools_allowed=tools_allowed,
            )
        except ProviderAdapterError as exc:
            if self._is_recoverable_adapter_json_envelope(exc):
                provider_recovery_used = True
                repair_attempt = 1
                repaired_from_response_hash = exc.failure.response_hash
                repair_validation_issues = (
                    self._adapter_json_envelope_issue(exc),
                )
                result = await self._recover_next_action_json_envelope(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    convergence=convergence,
                    recovery_constraint=recovery_constraint,
                    repair_reason=exc.failure.code.value,
                    validation_issues=repair_validation_issues,
                )
            elif tools_allowed and exc.failure.code in {
                    ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
                    ProviderErrorCode.TOOL_NAME_NOT_VISIBLE,
            }:
                provider_recovery_used = True
                is_overflow = exc.failure.code is (
                    ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED
                )
                recovery_input = ProviderRuntimeInput.from_payload(
                    input_ref=(
                        f"{request.request_ref}:tool-call-overflow:1"
                        if is_overflow
                        else f"{request.request_ref}:tool-registry:1"
                    ),
                    input_kind=(
                        "main_agent_tool_call_overflow_repair_v2"
                        if is_overflow
                        else "main_agent_tool_registry_repair_v2"
                    ),
                    payload={
                        "request": request.model_dump(mode="json"),
                        "instruction": (
                            (
                                "The previous response exceeded the Tool Call count "
                                "contract. Regenerate exactly one decision. Select at "
                                "most "
                                f"{tool_call_constraints['max_calls_per_response']} "
                                "non-overlapping, highest-value native Function Calls. "
                                "Do not repeat the same Tool plus arguments. If no Tool "
                                "Call is needed, return one valid small next-action object."
                            )
                            if is_overflow
                            else (
                                "The previous response selected a Tool outside the "
                                "visible Registry. Regenerate exactly one decision. "
                                "If a Tool is needed, select only an exact name from "
                                "allowed_tool_names and use native Function Calls. "
                                "Do not invent, alias, guess, or silently map Tool "
                                "names. If no Tool is needed, return one valid small "
                                "next-action object."
                            )
                        ),
                        "repair_attempt": 1,
                        "repair_reason": exc.failure.code.value,
                        "allowed_tool_names": list(request.visible_tool_names),
                        "tool_call_constraints": tool_call_constraints,
                        "allowed_action_kinds": allowed_action_kinds,
                        "next_action_recovery_constraint": (
                            None
                            if recovery_constraint is None
                            else recovery_constraint.model_dump(mode="json")
                        ),
                        "available_slot_capabilities": (
                            self._model_visible_slot_capabilities()
                        ),
                        "next_action_schema": (
                            ProviderNextActionDecision.model_json_schema()
                        ),
                        "next_action_advisory_contract": (
                            self._next_action_advisory_contract()
                        ),
                        "retrieval_convergence": (
                            None
                            if convergence is None
                            else convergence.model_dump(mode="json")
                        ),
                    },
                )
                result = await self._invoke_next_action(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    runtime_input=recovery_input,
                    call_kind=(
                        "next-action-tool-overflow-repair-1"
                        if is_overflow
                        else "next-action-tool-registry-repair-1"
                    ),
                    tools_allowed=True,
                )
            else:
                self._reject_adapter_json_failure(
                    exc,
                    "provider next-action response failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    repair_attempt=0,
                )
        if result.output_kind is ProviderOutputKind.TOOL_CALLS:
            if not tools_allowed:
                self._reject(
                    ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                    "provider emitted Tool Calls after retrieval saturation",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                )
            return self._tool_calls_outcome(request=request, result=result)
        try:
            ingress_request, ingress_receipt, payload = self._ingest_model_result(
                result=result,
                expected_contract_ref=NEXT_ACTION_CONTRACT_REF,
            )
        except ProviderBoundaryRejected as ingress_error:
            if ingress_error.failure.code not in _JSON_ENVELOPE_RECOVERABLE_CODES:
                raise
            if provider_recovery_used:
                self._reject(
                    ingress_error.failure.code,
                    "provider next-action JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=(
                        repair_validation_issues
                        or (
                            ProviderValidationIssue(
                                path="$",
                                error_type=ingress_error.failure.code.value,
                            ),
                        )
                    ),
                    cause=ingress_error,
                )
            if self._ingress.config.max_structural_repair_attempts < 1:
                raise
            provider_recovery_used = True
            repair_attempt = 1
            repaired_from_response_hash = result.response_hash
            repair_validation_issues = (
                ProviderValidationIssue(
                    path="$",
                    error_type=ingress_error.failure.code.value,
                ),
            )
            result = await self._recover_next_action_json_envelope(
                request=request,
                context=context,
                registry_snapshot=registry_snapshot,
                convergence=convergence,
                recovery_constraint=recovery_constraint,
                repair_reason=ingress_error.failure.code.value,
                validation_issues=repair_validation_issues,
            )
            try:
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=NEXT_ACTION_CONTRACT_REF,
                    )
                )
            except ProviderBoundaryRejected as repair_error:
                self._reject(
                    repair_error.failure.code,
                    "provider next-action JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=repair_validation_issues,
                    cause=repair_error,
                )
        (
            projection,
            validation_issues,
            validation_error,
            advisory_source_hints_filtered,
        ) = (
            self._validate_next_action_projection(
                payload,
                convergence=convergence,
                recovery_constraint=recovery_constraint,
            )
        )
        if validation_issues:
            if provider_recovery_used:
                self._reject(
                    ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID,
                    "provider next-action remained invalid after one bounded recovery",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=validation_issues,
                    cause=validation_error,
                )
            if self._ingress.config.max_structural_repair_attempts < 1:
                self._reject(
                    ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID,
                    "provider next-action output failed Runtime validation",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    validation_issues=validation_issues,
                    cause=validation_error,
                )
            repaired_from_response_hash = result.response_hash
            repair_validation_issues = validation_issues
            allowed_action_kinds = self._allowed_action_kinds(
                convergence,
                recovery_constraint=recovery_constraint,
            )
            repair_input = ProviderRuntimeInput.from_payload(
                input_ref=f"{request.request_ref}:repair:1",
                input_kind="main_agent_next_action_repair_v2",
                payload={
                    "request": request.model_dump(mode="json"),
                    "instruction": (
                        "Regenerate one corrected next-action object only. "
                        "Resolve every validation issue. Do not return answer "
                        "text, Plan payloads, Slot schemas, Tool arguments, "
                        "commentary, or Function Calls. target_source_bases is "
                        "advisory only: use exact allowed values or an empty array."
                    ),
                    "allowed_action_kinds": allowed_action_kinds,
                    "next_action_recovery_constraint": (
                        None
                        if recovery_constraint is None
                        else recovery_constraint.model_dump(mode="json")
                    ),
                    "available_slot_capabilities": (
                        self._model_visible_slot_capabilities()
                    ),
                    "validation_issues": [
                        issue.model_dump(mode="json")
                        for issue in validation_issues
                    ],
                    "rejected_decision": self._bounded_repair_projection(payload),
                    "next_action_schema": (
                        ProviderNextActionDecision.model_json_schema()
                    ),
                    "next_action_advisory_contract": (
                        self._next_action_advisory_contract()
                    ),
                    "retrieval_convergence": (
                        None
                        if convergence is None
                        else convergence.model_dump(mode="json")
                    ),
                },
            )
            try:
                result = await self._invoke_next_action(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    runtime_input=repair_input,
                    call_kind="next-action-repair-1",
                    tools_allowed=False,
                )
                if result.output_kind is ProviderOutputKind.TOOL_CALLS:
                    self._reject(
                        ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID,
                        "provider next-action repair emitted Tool Calls",
                        stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                        structurally_repairable=True,
                        repair_attempt=1,
                        validation_issues=validation_issues,
                    )
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=NEXT_ACTION_CONTRACT_REF,
                    )
                )
            except ProviderAdapterError as repair_exc:
                self._reject_adapter_json_failure(
                    repair_exc,
                    "provider next-action structural repair failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    repair_attempt=1,
                    validation_issues=validation_issues,
                )
            except ProviderBoundaryRejected as repair_exc:
                if repair_exc.failure.repair_attempt == 1:
                    raise
                self._reject(
                    repair_exc.failure.code,
                    "provider next-action structural repair failed at JSON ingress",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=validation_issues,
                    cause=repair_exc,
                )
            (
                projection,
                final_issues,
                validation_error,
                advisory_source_hints_filtered,
            ) = (
                self._validate_next_action_projection(
                    payload,
                    convergence=convergence,
                    recovery_constraint=recovery_constraint,
                )
            )
            if final_issues:
                self._reject(
                    ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID,
                    "provider next-action remained invalid after one repair",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=final_issues,
                    cause=validation_error,
                )
            repair_attempt = 1
        if projection is None:
            raise AssertionError("validated next-action projection is missing")
        normalization_steps = ingress_receipt.normalization_steps
        if advisory_source_hints_filtered:
            advisory_step = (
                ProviderIngressNormalizationStep.ADVISORY_SOURCE_HINTS_FILTERED
            )
            normalization_steps = (
                *normalization_steps,
                advisory_step,
            )
        validated_receipt = ProviderIngressReceipt.build(
            request=ingress_request,
            normalized_payload=payload,
            normalization_steps=normalization_steps,
            schema_validated=True,
            validated_contract=projection,
        )
        return ProviderNextActionOutcome.build(
            request=request,
            provider_result_ref=result.result_ref,
            provider_response_hash=result.response_hash,
            provider_receipt_ref=result.provider_receipt_ref,
            ingress_receipt=validated_receipt,
            decision=projection,
            recovery_constraint=recovery_constraint,
            repair_attempt=repair_attempt,
            repaired_from_response_hash=repaired_from_response_hash,
            repair_validation_issues=repair_validation_issues,
        )

    async def decide_retrieval_tool_calls(
        self,
        *,
        request: MainAgentDecisionRequest,
        control_selection: ProviderNextActionOutcome,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> ProviderToolCallsOutcomeV2:
        """Materialize one accepted retrieval request through a minimal Tool view."""

        self._require_enabled()
        self._validate_decision_binding(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        retrieval_request = control_selection.decision.retrieval_request
        if (
            control_selection.request_ref != request.request_ref
            or control_selection.task_ref != request.task_ref
            or control_selection.origin_state_version != request.origin_state_version
            or control_selection.context_snapshot_ref != request.context_snapshot_ref
            or control_selection.registry_snapshot_ref != request.registry_snapshot_ref
            or control_selection.decision.action_kind != "retrieve"
            or retrieval_request is None
            or registry_snapshot is None
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "retrieval Tool selection did not match its accepted V2 control decision",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        requested_tool_names = tuple(retrieval_request.requested_tool_names)
        if not set(requested_tool_names).issubset(request.visible_tool_names):
            self._reject(
                ProviderBoundaryFailureCode.TOOL_NOT_VISIBLE,
                "retrieval request exceeded the visible Registry",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        result = await self._invoke_retrieval_tool_calls(
            request=request,
            control_selection=control_selection,
            retrieval_request=retrieval_request,
            context=context,
            registry_snapshot=registry_snapshot,
            requested_tool_names=requested_tool_names,
            repair_attempt=0,
            repair_reason=None,
        )
        if result.output_kind is not ProviderOutputKind.TOOL_CALLS:
            self._reject(
                ProviderBoundaryFailureCode.DECISION_SCHEMA_INVALID,
                "accepted retrieval request did not produce native Function Calls",
                stage=ProviderBoundaryFailureStage.NEXT_ACTION,
            )
        return self._tool_calls_outcome(request=request, result=result)

    async def generate_locked_action_payload(
        self,
        *,
        request: MainAgentDecisionRequest,
        selected: ProviderNextActionOutcome,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> ProviderLockedActionPayloadOutcome:
        """Generate payload only; the accepted V2 action kind is immutable."""

        self._require_enabled()
        self._validate_locked_payload_binding(
            request=request,
            selected=selected,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        action_kind = selected.decision.action_kind
        output_model, contract_ref, schema_name = self._locked_payload_contract(
            action_kind
        )
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=f"{selected.outcome_ref}:locked-payload",
            input_kind="main_agent_locked_action_payload_v2",
            payload={
                "request": request.model_dump(mode="json"),
                "selected_next_action": selected.decision.model_dump(mode="json"),
                "locked_action_kind": action_kind.value,
                "instruction": self._locked_payload_instruction(action_kind),
                "information_needs": list(
                    selected.decision.information_needs
                ),
                "available_slot_capabilities": (
                    self._model_visible_slot_capabilities()
                    if action_kind
                    is MainAgentModelActionKind.REQUEST_INFORMATION
                    else []
                ),
                "action_payload_schema": output_model.model_json_schema(),
            },
        )
        repair_attempt = 0
        repaired_from_response_hash: str | None = None
        repair_validation_issues: tuple[ProviderValidationIssue, ...] = ()
        try:
            result = await self._invoke_locked_action_payload(
                request=request,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=runtime_input,
                output_model=output_model,
                schema_name=schema_name,
                call_kind="locked-action-payload",
            )
        except ProviderAdapterError as exc:
            if not self._is_recoverable_adapter_json_envelope(exc):
                self._reject_adapter_json_failure(
                    exc,
                    "provider locked action payload failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    repair_attempt=0,
                )
            repair_attempt = 1
            repaired_from_response_hash = exc.failure.response_hash
            repair_validation_issues = (
                self._adapter_json_envelope_issue(exc),
            )
            result = await self._recover_locked_action_json_envelope(
                request=request,
                selected=selected,
                context=context,
                registry_snapshot=registry_snapshot,
                output_model=output_model,
                schema_name=schema_name,
                action_kind=action_kind,
                repair_reason=exc.failure.code.value,
                validation_issues=repair_validation_issues,
            )
        try:
            ingress_request, ingress_receipt, payload = self._ingest_model_result(
                result=result,
                expected_contract_ref=contract_ref,
            )
        except ProviderBoundaryRejected as ingress_error:
            if ingress_error.failure.code not in _JSON_ENVELOPE_RECOVERABLE_CODES:
                raise
            if repair_attempt >= 1:
                self._reject(
                    ingress_error.failure.code,
                    "provider locked action payload JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=(
                        repair_validation_issues
                        or (
                            ProviderValidationIssue(
                                path="$",
                                error_type=ingress_error.failure.code.value,
                            ),
                        )
                    ),
                    cause=ingress_error,
                )
            if self._ingress.config.max_structural_repair_attempts < 1:
                raise
            repair_attempt = 1
            repaired_from_response_hash = result.response_hash
            repair_validation_issues = (
                ProviderValidationIssue(
                    path="$",
                    error_type=ingress_error.failure.code.value,
                ),
            )
            result = await self._recover_locked_action_json_envelope(
                request=request,
                selected=selected,
                context=context,
                registry_snapshot=registry_snapshot,
                output_model=output_model,
                schema_name=schema_name,
                action_kind=action_kind,
                repair_reason=ingress_error.failure.code.value,
                validation_issues=repair_validation_issues,
            )
            try:
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=contract_ref,
                    )
                )
            except ProviderAdapterError as repair_error:
                self._reject_adapter_json_failure(
                    repair_error,
                    (
                        "provider locked action payload repair failed before "
                        "JSON ingress"
                    ),
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    repair_attempt=1,
                    validation_issues=repair_validation_issues,
                )
            except ProviderBoundaryRejected as repair_error:
                self._reject(
                    repair_error.failure.code,
                    "provider locked action payload JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=repair_validation_issues,
                    cause=repair_error,
                )
        projection, validation_issues, validation_error = (
            self._validate_locked_action_payload(
                payload=payload,
                output_model=output_model,
                action_kind=action_kind,
                concise_basis=selected.decision.concise_basis,
            )
        )
        if validation_issues:
            if repair_attempt >= 1:
                self._reject(
                    ProviderBoundaryFailureCode.LOCKED_ACTION_PAYLOAD_INVALID,
                    "provider locked action payload remained invalid after one recovery",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=validation_issues,
                    cause=validation_error,
                )
            repaired_from_response_hash = result.response_hash
            repair_validation_issues = validation_issues
            repair_input = ProviderRuntimeInput.from_payload(
                input_ref=f"{selected.outcome_ref}:locked-payload-repair:1",
                input_kind="main_agent_locked_action_payload_repair_v2",
                payload={
                    "request": request.model_dump(mode="json"),
                    "selected_next_action": (
                        selected.decision.model_dump(mode="json")
                    ),
                    "locked_action_kind": action_kind.value,
                    "instruction": (
                        self._locked_payload_instruction(action_kind)
                        + " Resolve every validation issue and return one corrected "
                        "payload object only."
                    ),
                    "repair_attempt": 1,
                    "validation_issues": [
                        issue.model_dump(mode="json")
                        for issue in validation_issues
                    ],
                    "available_slot_capabilities": (
                        self._model_visible_slot_capabilities()
                        if action_kind
                        is MainAgentModelActionKind.REQUEST_INFORMATION
                        else []
                    ),
                    "rejected_payload": self._bounded_repair_projection(payload),
                    "action_payload_schema": output_model.model_json_schema(),
                },
            )
            try:
                result = await self._invoke_locked_action_payload(
                    request=request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    runtime_input=repair_input,
                    output_model=output_model,
                    schema_name=schema_name,
                    call_kind="locked-action-payload-repair-1",
                )
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=contract_ref,
                    )
                )
            except ProviderAdapterError as repair_error:
                self._reject_adapter_json_failure(
                    repair_error,
                    (
                        "provider locked action payload repair failed before "
                        "JSON ingress"
                    ),
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    repair_attempt=1,
                    validation_issues=repair_validation_issues,
                )
            except ProviderBoundaryRejected as repair_error:
                self._reject(
                    ProviderBoundaryFailureCode.LOCKED_ACTION_PAYLOAD_INVALID,
                    "provider locked action payload repair failed at JSON ingress",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=repair_validation_issues,
                    cause=repair_error,
                )
            projection, final_issues, validation_error = (
                self._validate_locked_action_payload(
                    payload=payload,
                    output_model=output_model,
                    action_kind=action_kind,
                    concise_basis=selected.decision.concise_basis,
                )
            )
            if final_issues:
                self._reject(
                    ProviderBoundaryFailureCode.LOCKED_ACTION_PAYLOAD_INVALID,
                    "provider locked action payload remained invalid after one repair",
                    stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=final_issues,
                    cause=validation_error,
                )
            repair_attempt = 1
        if projection is None:
            raise AssertionError("validated locked action payload is missing")
        validated_receipt = ProviderIngressReceipt.build(
            request=ingress_request,
            normalized_payload=payload,
            normalization_steps=ingress_receipt.normalization_steps,
            schema_validated=True,
            validated_contract=projection,
        )
        return ProviderLockedActionPayloadOutcome.build(
            request=request,
            selected=selected,
            provider_result_ref=result.result_ref,
            provider_response_hash=result.response_hash,
            provider_receipt_ref=result.provider_receipt_ref,
            ingress_receipt=validated_receipt,
            payload=projection,
            repair_attempt=repair_attempt,
            repaired_from_response_hash=repaired_from_response_hash,
            repair_validation_issues=repair_validation_issues,
        )

    async def _recover_next_action_json_envelope(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        convergence: RetrievalConvergenceDecisionV2 | None,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None,
        repair_reason: str,
        validation_issues: tuple[ProviderValidationIssue, ...],
    ) -> ProviderModelResult:
        envelope_repair_input = ProviderRuntimeInput.from_payload(
            input_ref=f"{request.request_ref}:json-envelope-repair:1",
            input_kind="main_agent_next_action_json_envelope_repair_v2",
            payload={
                "request": request.model_dump(mode="json"),
                "decision_mode": "control_only",
                "instruction": (
                    "The previous response was not one valid JSON object. "
                    "Regenerate exactly one next-action object that satisfies "
                    "next_action_schema. Do not return Markdown, commentary, "
                    "answer text, Tool Calls, Plan payloads, or Slot schemas."
                ),
                "repair_attempt": 1,
                "repair_reason": repair_reason,
                "allowed_action_kinds": self._allowed_action_kinds(
                    convergence,
                    recovery_constraint=recovery_constraint,
                ),
                "next_action_recovery_constraint": (
                    None
                    if recovery_constraint is None
                    else recovery_constraint.model_dump(mode="json")
                ),
                "available_slot_capabilities": (
                    self._model_visible_slot_capabilities()
                ),
                "next_action_schema": (
                    ProviderNextActionDecision.model_json_schema()
                ),
                "next_action_advisory_contract": (
                    self._next_action_advisory_contract()
                ),
            },
        )
        try:
            return await self._invoke_next_action(
                request=request,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=envelope_repair_input,
                call_kind="next-action-json-envelope-repair-1",
                tools_allowed=False,
            )
        except ProviderAdapterError as repair_error:
            self._reject_adapter_json_failure(
                repair_error,
                "provider next-action JSON envelope recovery failed",
                stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                repair_attempt=1,
                validation_issues=validation_issues,
            )

    async def _invoke_next_action(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        runtime_input: ProviderRuntimeInput,
        call_kind: str,
        tools_allowed: bool,
    ) -> ProviderModelResult:
        structured_output = None
        if (
            not tools_allowed
            and self._adapter.capabilities.supports_structured_output
        ):
            structured_output = ProviderStructuredOutputSpec.from_model(
                schema_name="provider_next_action_v2",
                output_model=ProviderNextActionDecision,
                strict_mode=ProviderStrictMode.PREFERRED,
            )
        return await self._adapter.invoke(
            ProviderInvocationRequest(
                call_ref=self._call_ref(
                    request_ref=request.request_ref,
                    request_hash=request.request_hash,
                    call_kind=call_kind,
                    context_snapshot_hash=context.snapshot.snapshot_hash,
                ),
                task_ref=request.task_ref,
                state_version=request.origin_state_version,
                consumer=ContextConsumer.MAIN_AGENT,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=runtime_input,
                structured_output=structured_output,
                tool_choice=(
                    ProviderToolChoice.AUTO
                    if tools_allowed
                    else ProviderToolChoice.NONE
                ),
                tool_strict_mode=ProviderStrictMode.PREFERRED,
                max_output_tokens=self._max_output_tokens(context),
            )
        )

    async def _invoke_retrieval_tool_calls(
        self,
        *,
        request: MainAgentDecisionRequest,
        control_selection: ProviderNextActionOutcome,
        retrieval_request: ProviderRetrievalRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot,
        requested_tool_names: tuple[ToolName, ...],
        repair_attempt: int,
        repair_reason: ProviderErrorCode | None,
    ) -> ProviderModelResult:
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=(
                f"{control_selection.outcome_ref}:retrieval-tools"
                if repair_attempt == 0
                else f"{control_selection.outcome_ref}:retrieval-tools-repair:1"
            ),
            input_kind=(
                "main_agent_retrieval_tool_calls_v2"
                if repair_attempt == 0
                else "main_agent_retrieval_tool_calls_repair_v2"
            ),
            payload={
                "request": request.model_dump(mode="json"),
                "accepted_control_selection": {
                    "outcome_ref": control_selection.outcome_ref,
                    "outcome_hash": control_selection.outcome_hash,
                    "action_kind": "retrieve",
                },
                "retrieval_request": retrieval_request.model_dump(mode="json"),
                "allowed_tool_names": list(requested_tool_names),
                "instruction": (
                    "Emit native Function Calls only. Use only exact names from "
                    "allowed_tool_names, and bind every call to one accepted "
                    "information need. Do not emit assistant text, JSON decisions, "
                    "aliases, speculative calls, or duplicate Tool plus arguments."
                    if repair_attempt == 0
                    else (
                        "The previous Function Calling response violated the bounded "
                        "Tool contract. Regenerate native Function Calls exactly once. "
                        "Use only exact allowed_tool_names, stay within the provider "
                        "call-count limit, and do not emit text or JSON decisions."
                    )
                ),
                "repair_attempt": repair_attempt,
                "repair_reason": (
                    None if repair_reason is None else repair_reason.value
                ),
                "tool_call_constraints": self._tool_call_constraints(
                    tools_allowed=True
                ),
            },
        )
        try:
            return await self._adapter.invoke(
                ProviderInvocationRequest(
                    call_ref=self._call_ref(
                        request_ref=request.request_ref,
                        request_hash=request.request_hash,
                        call_kind=(
                            "retrieval-tool-calls"
                            if repair_attempt == 0
                            else "retrieval-tool-calls-repair-1"
                        ),
                        context_snapshot_hash=context.snapshot.snapshot_hash,
                    ),
                    task_ref=request.task_ref,
                    state_version=request.origin_state_version,
                    consumer=ContextConsumer.MAIN_AGENT,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    tool_name_filter=requested_tool_names,
                    runtime_input=runtime_input,
                    structured_output=None,
                    tool_choice=ProviderToolChoice.REQUIRED,
                    tool_strict_mode=ProviderStrictMode.PREFERRED,
                    max_output_tokens=self._max_output_tokens(context),
                )
            )
        except ProviderAdapterError as exc:
            if (
                repair_attempt == 0
                and exc.failure.code
                in {
                    ProviderErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
                    ProviderErrorCode.TOOL_NAME_NOT_VISIBLE,
                }
            ):
                return await self._invoke_retrieval_tool_calls(
                    request=request,
                    control_selection=control_selection,
                    retrieval_request=retrieval_request,
                    context=context,
                    registry_snapshot=registry_snapshot,
                    requested_tool_names=requested_tool_names,
                    repair_attempt=1,
                    repair_reason=exc.failure.code,
                )
            raise

    async def _recover_locked_action_json_envelope(
        self,
        *,
        request: MainAgentDecisionRequest,
        selected: ProviderNextActionOutcome,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        output_model: type[BaseModel],
        schema_name: str,
        action_kind: MainAgentModelActionKind,
        repair_reason: str,
        validation_issues: tuple[ProviderValidationIssue, ...],
    ) -> ProviderModelResult:
        envelope_repair_input = ProviderRuntimeInput.from_payload(
            input_ref=(
                f"{selected.outcome_ref}:locked-payload-json-envelope-repair:1"
            ),
            input_kind=(
                "main_agent_locked_action_payload_json_envelope_repair_v2"
            ),
            payload={
                "request": request.model_dump(mode="json"),
                "selected_next_action": selected.decision.model_dump(mode="json"),
                "locked_action_kind": action_kind.value,
                "instruction": (
                    self._locked_payload_instruction(action_kind)
                    + " The previous response was not one valid JSON object. "
                    "Regenerate exactly one payload object without Markdown, "
                    "commentary, Tool Calls, or another action choice."
                ),
                "repair_attempt": 1,
                "repair_reason": repair_reason,
                "available_slot_capabilities": (
                    self._model_visible_slot_capabilities()
                    if action_kind
                    is MainAgentModelActionKind.REQUEST_INFORMATION
                    else []
                ),
                "action_payload_schema": output_model.model_json_schema(),
            },
        )
        try:
            return await self._invoke_locked_action_payload(
                request=request,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=envelope_repair_input,
                output_model=output_model,
                schema_name=schema_name,
                call_kind="locked-action-payload-json-envelope-repair-1",
            )
        except ProviderAdapterError as repair_error:
            self._reject_adapter_json_failure(
                repair_error,
                "provider locked action payload JSON envelope recovery failed",
                stage=ProviderBoundaryFailureStage.NEXT_ACTION,
                repair_attempt=1,
                validation_issues=validation_issues,
            )

    async def _invoke_locked_action_payload(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        runtime_input: ProviderRuntimeInput,
        output_model: type[BaseModel],
        schema_name: str,
        call_kind: str,
    ) -> ProviderModelResult:
        structured_output = None
        if self._adapter.capabilities.supports_structured_output:
            structured_output = ProviderStructuredOutputSpec.from_model(
                schema_name=schema_name,
                output_model=output_model,
                strict_mode=ProviderStrictMode.PREFERRED,
            )
        return await self._adapter.invoke(
            ProviderInvocationRequest(
                call_ref=self._call_ref(
                    request_ref=request.request_ref,
                    request_hash=request.request_hash,
                    call_kind=call_kind,
                    context_snapshot_hash=context.snapshot.snapshot_hash,
                ),
                task_ref=request.task_ref,
                state_version=request.origin_state_version,
                consumer=ContextConsumer.MAIN_AGENT,
                context=context,
                registry_snapshot=registry_snapshot,
                runtime_input=runtime_input,
                structured_output=structured_output,
                tool_choice=ProviderToolChoice.NONE,
                tool_strict_mode=ProviderStrictMode.PREFERRED,
                max_output_tokens=self._max_output_tokens(context),
            )
        )

    @staticmethod
    def _locked_payload_contract(
        action_kind: MainAgentModelActionKind,
    ) -> tuple[type[BaseModel], str, str]:
        if action_kind in {
            MainAgentModelActionKind.PLAN,
            MainAgentModelActionKind.REPLAN,
        }:
            return (
                PlanActionRequest,
                LOCKED_PLAN_PAYLOAD_CONTRACT_REF,
                "provider_locked_plan_payload_v2",
            )
        if action_kind is MainAgentModelActionKind.REQUEST_INFORMATION:
            return (
                ProviderInformationRequestPayloadV2,
                LOCKED_INFORMATION_PAYLOAD_CONTRACT_REF,
                "provider_locked_information_payload_v2",
            )
        raise ActionLoopContractRejected(
            "selected V2 action does not accept a locked payload"
        )

    @staticmethod
    def _locked_payload_instruction(
        action_kind: MainAgentModelActionKind,
    ) -> str:
        common = (
            "The Runtime has already selected and frozen the action kind. Return "
            "only the action-specific payload object. Do not return action_kind, "
            "concise_basis, Tool Calls, answer text, or another action choice."
        )
        if action_kind is MainAgentModelActionKind.PLAN:
            return (
                common
                + " Build one non-blocked planned IntentUnderstanding. Initial "
                "planning must use an empty revision_reasons array."
            )
        if action_kind is MainAgentModelActionKind.REPLAN:
            return (
                common
                + " Build one non-blocked planned IntentUnderstanding and include "
                "at least one exact revision_reasons enum value."
            )
        return (
            common
            + " Build exactly one Slot request for the supplied missing user facts. "
            "Choose one exact slot_kind from available_slot_capabilities. The "
            "request_message must ask only for those facts. Never invent or return "
            "an input model or business validator reference."
        )

    def _validate_locked_action_payload(
        self,
        *,
        payload: dict[str, Any],
        output_model: type[BaseModel],
        action_kind: MainAgentModelActionKind,
        concise_basis: str,
    ) -> tuple[
        PlanActionRequest | InformationRequestAction | None,
        tuple[ProviderValidationIssue, ...],
        ValidationError | None,
    ]:
        try:
            provider_projection = output_model.model_validate(payload)
            projection: PlanActionRequest | InformationRequestAction
            if isinstance(provider_projection, PlanActionRequest):
                projection = provider_projection
            elif isinstance(
                provider_projection,
                ProviderInformationRequestPayloadV2,
            ):
                definition = self._slot_capability_snapshot.resolve(
                    provider_projection.slot_kind
                )
                if definition is None:
                    return (
                        None,
                        (
                            ProviderValidationIssue(
                                path="$.slot_kind",
                                error_type="slot_kind_not_available",
                            ),
                        ),
                        None,
                    )
                projection = InformationRequestAction(
                    slot_name=definition.slot_name,
                    request_message=provider_projection.request_message,
                    input_model_ref=definition.input_model_ref,
                    business_validator_refs=(
                        definition.business_validator_refs
                    ),
                    blocking_reason=provider_projection.blocking_reason,
                )
            else:
                raise TypeError("locked payload model is not supported")
            decision_fields: dict[str, Any] = {
                "action_kind": action_kind,
                "concise_basis": concise_basis,
            }
            if isinstance(projection, PlanActionRequest):
                decision_fields["plan_request"] = projection
            elif isinstance(projection, InformationRequestAction):
                decision_fields["information_request"] = projection
            MainAgentModelDecision.model_validate(decision_fields)
        except ValidationError as exc:
            return None, self._safe_validation_issues(exc, payload), exc
        return projection, (), None

    def _allowed_action_kinds(
        self,
        convergence: RetrievalConvergenceDecisionV2 | None,
        *,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None = None,
    ) -> list[str]:
        if recovery_constraint is not None:
            return [recovery_constraint.required_action_kind]
        if convergence is not None and convergence.saturated:
            allowed = [MainAgentModelActionKind.ANSWER.value]
            if self._slot_capability_snapshot.definitions:
                allowed.append(
                    MainAgentModelActionKind.REQUEST_INFORMATION.value
                )
            return allowed
        return [
            *[
                item.value
                for item in MainAgentModelActionKind
                if (
                    item is not MainAgentModelActionKind.REQUEST_INFORMATION
                    or bool(self._slot_capability_snapshot.definitions)
                )
            ],
            "retrieve",
        ]

    def _model_visible_slot_capabilities(self) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in (
                self._slot_capability_snapshot.model_visible_capabilities()
            )
        ]

    def _tool_call_constraints(self, *, tools_allowed: bool) -> dict[str, Any]:
        return {
            "max_calls_per_response": (
                self._adapter.capabilities.max_tool_calls_per_response
                if tools_allowed
                else 0
            ),
            "parallel_calls_allowed": bool(
                tools_allowed
                and self._adapter.capabilities.supports_parallel_tool_calls
            ),
            "selection_rule": (
                "highest_value_non_overlapping_calls_then_redecide"
            ),
            "overflow_behavior": "regenerate_once_without_silent_truncation",
            "information_need_binding": {
                "required": bool(tools_allowed),
                "allowed_sources": [
                    "current_user_request",
                    "accepted_unresolved_context",
                ],
                "speculative_calls_forbidden": True,
                "no_need_behavior": "return_next_action_without_tool_calls",
            },
        }

    @staticmethod
    def _next_action_advisory_contract() -> dict[str, Any]:
        return {
            "field": "target_source_bases",
            "authority": "advisory_only",
            "allowed_values": list(_NEXT_ACTION_ADVISORY_SOURCE_BASES),
            "unknown_value_behavior": "omit",
            "empty_array_allowed": True,
        }

    @staticmethod
    def _normalize_next_action_advisories(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Filter only the non-authoritative source hint without alias guessing."""

        field = "target_source_bases"
        if field not in payload:
            return payload, False
        raw_hints = payload[field]
        normalized_hints: list[str] = []
        if isinstance(raw_hints, list):
            allowed = set(_NEXT_ACTION_ADVISORY_SOURCE_BASES)
            for value in raw_hints:
                if not isinstance(value, str) or value not in allowed:
                    continue
                if value in normalized_hints:
                    continue
                normalized_hints.append(value)
                if len(normalized_hints) == 8:
                    break
        if raw_hints == normalized_hints:
            return payload, False
        normalized_payload = dict(payload)
        normalized_payload[field] = normalized_hints
        return normalized_payload, True

    def _validate_next_action_projection(
        self,
        payload: dict[str, Any],
        *,
        convergence: RetrievalConvergenceDecisionV2 | None,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None = None,
    ) -> tuple[
        ProviderNextActionDecision | None,
        tuple[ProviderValidationIssue, ...],
        ValidationError | None,
        bool,
    ]:
        normalized_payload, advisory_source_hints_filtered = (
            self._normalize_next_action_advisories(payload)
        )
        try:
            projection = ProviderNextActionDecision.model_validate(
                normalized_payload
            )
        except ValidationError as exc:
            return (
                None,
                self._safe_validation_issues(exc, normalized_payload),
                exc,
                advisory_source_hints_filtered,
            )
        if (
            projection.action_kind
            is MainAgentModelActionKind.REQUEST_INFORMATION
            and not self._slot_capability_snapshot.definitions
        ):
            return (
                projection,
                (
                    ProviderValidationIssue(
                        path="$.action_kind",
                        error_type="action_not_available",
                    ),
                ),
                None,
                advisory_source_hints_filtered,
            )
        selected_action_kind = (
            projection.action_kind.value
            if isinstance(projection.action_kind, MainAgentModelActionKind)
            else projection.action_kind
        )
        if selected_action_kind not in self._allowed_action_kinds(
            convergence,
            recovery_constraint=recovery_constraint,
        ):
            return (
                projection,
                (
                    ProviderValidationIssue(
                        path="$.action_kind",
                        error_type=(
                            "guard_recovery_action_required"
                            if recovery_constraint is not None
                            else "terminal_action_forbidden"
                        ),
                    ),
                ),
                None,
                advisory_source_hints_filtered,
            )
        if recovery_constraint is not None:
            retrieval_request = projection.retrieval_request
            requested_tool_names = (
                ()
                if retrieval_request is None
                else retrieval_request.requested_tool_names
            )
            if requested_tool_names != recovery_constraint.required_tool_names:
                return (
                    projection,
                    (
                        ProviderValidationIssue(
                            path="$.retrieval_request.requested_tool_names",
                            error_type="guard_recovery_tool_required",
                        ),
                    ),
                    None,
                    advisory_source_hints_filtered,
                )
        return projection, (), None, advisory_source_hints_filtered

    async def generate_answer(
        self,
        *,
        decision_request: MainAgentDecisionRequest,
        bundle: ProviderAnswerContextBundle,
        next_action: ProviderNextActionOutcome | None = None,
        terminal_authorization: RuntimeTerminalAnswerAuthorizationV2 | None = None,
    ) -> tuple[ProviderAnswerGenerationRequest, ProviderAnswerGenerationOutcome]:
        self._require_enabled()
        if (next_action is None) == (terminal_authorization is None):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "Answer requires exactly one accepted authority",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        (
            allowed_grounding_refs,
            allowed_runtime_fact_refs,
            allowed_limitation_refs,
        ) = (
            self._allowed_answer_refs(bundle.context)
        )
        if next_action is not None:
            self._validate_answer_context(
                decision_request=decision_request,
                next_action=next_action,
                context=bundle.context,
            )
            answer_request = ProviderAnswerGenerationRequest.build(
                decision_request=decision_request,
                next_action=next_action,
                state_version=bundle.context.snapshot.state_version,
                context_snapshot_ref=bundle.context.snapshot.snapshot_ref,
                context_snapshot_hash=bundle.context.snapshot.snapshot_hash,
                allowed_grounding_refs=allowed_grounding_refs,
                allowed_runtime_fact_refs=allowed_runtime_fact_refs,
                allowed_limitation_refs=allowed_limitation_refs,
                guard_feedback_refs=bundle.guard_feedback_refs,
                response_language_hint=bundle.response_language_hint,
            )
        else:
            if terminal_authorization is None:
                raise AssertionError("terminal Answer authority is missing")
            self._validate_terminal_answer_context(
                decision_request=decision_request,
                authorization=terminal_authorization,
                context=bundle.context,
                guard_feedback_refs=bundle.guard_feedback_refs,
            )
            answer_request = (
                ProviderAnswerGenerationRequest.build_from_terminal_authorization(
                    decision_request=decision_request,
                    authorization=terminal_authorization,
                    state_version=bundle.context.snapshot.state_version,
                    context_snapshot_ref=bundle.context.snapshot.snapshot_ref,
                    context_snapshot_hash=bundle.context.snapshot.snapshot_hash,
                    allowed_grounding_refs=allowed_grounding_refs,
                    allowed_runtime_fact_refs=allowed_runtime_fact_refs,
                    allowed_limitation_refs=allowed_limitation_refs,
                    response_language_hint=bundle.response_language_hint,
                )
            )
        guard_instruction = (
            " The request contains guard_feedback_refs. Resolve the required "
            "actions in those persisted feedback entries. Never repeat a "
            "rejected support pattern; when support is still insufficient, "
            "emit uncertainty with an explicit limitation."
            if answer_request.guard_feedback_refs
            else ""
        )
        runtime_input = ProviderRuntimeInput.from_payload(
            input_ref=answer_request.request_ref,
            input_kind="main_agent_answer_projection_v2",
            payload={
                "request": answer_request.model_dump(mode="json"),
                "instruction": (
                    "Generate only the minimal answer projection. Facts, inferences, "
                    "and project recommendations may select only citable Evidence "
                    "Atoms from allowed_grounding_refs. Uncertainty may select only "
                    "allowed_limitation_refs. Runtime facts about the loaded resource "
                    "name, type, version, or load status may select only "
                    "allowed_runtime_fact_refs. They cannot assert resource contents. "
                    "Conversation, user, policy, Task, and "
                    "Tool protocol entries are context, never factual Grounding. "
                    "Use general_advice without grounding_refs for greetings, casual "
                    "conversation, or transformations that add no project facts. "
                    "Runtime creates canonical "
                    "block ids, epistemic status, limitation links, citations, and "
                    "all commit authority."
                    + guard_instruction
                ),
                "answer_business_rules": provider_answer_business_rules_v2(),
                "answer_projection_schema": (
                    ProviderAnswerProjectionV2.model_json_schema()
                ),
            },
        )
        repair_attempt = 0
        repaired_from_response_hash: str | None = None
        envelope_issues: tuple[ProviderValidationIssue, ...] = ()
        try:
            result = await self._invoke_answer_projection(
                answer_request=answer_request,
                context=bundle.context,
                runtime_input=runtime_input,
                call_kind="answer-projection",
            )
        except ProviderAdapterError as exc:
            if not self._is_recoverable_adapter_json_envelope(exc):
                self._reject_adapter_json_failure(
                    exc,
                    "provider Answer response failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    repair_attempt=0,
                )
            repair_attempt = 1
            repaired_from_response_hash = exc.failure.response_hash
            envelope_issues = (self._adapter_json_envelope_issue(exc),)
            result = await self._recover_answer_json_envelope(
                answer_request=answer_request,
                context=bundle.context,
                repair_reason=exc.failure.code.value,
                validation_issues=envelope_issues,
            )
        try:
            ingress_request, ingress_receipt, payload = self._ingest_model_result(
                result=result,
                expected_contract_ref=ANSWER_PROJECTION_CONTRACT_REF,
            )
        except ProviderBoundaryRejected as ingress_error:
            if ingress_error.failure.code not in _JSON_ENVELOPE_RECOVERABLE_CODES:
                raise
            if repair_attempt >= 1:
                self._reject(
                    ingress_error.failure.code,
                    "provider Answer JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=(
                        envelope_issues
                        or (
                            ProviderValidationIssue(
                                path="$",
                                error_type=ingress_error.failure.code.value,
                            ),
                        )
                    ),
                    cause=ingress_error,
                )
            if self._ingress.config.max_structural_repair_attempts < 1:
                raise
            repair_attempt = 1
            repaired_from_response_hash = result.response_hash
            envelope_issue = ProviderValidationIssue(
                path="$",
                error_type=ingress_error.failure.code.value,
            )
            envelope_issues = (envelope_issue,)
            result = await self._recover_answer_json_envelope(
                answer_request=answer_request,
                context=bundle.context,
                repair_reason=ingress_error.failure.code.value,
                validation_issues=envelope_issues,
            )
            try:
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=ANSWER_PROJECTION_CONTRACT_REF,
                    )
                )
            except ProviderBoundaryRejected as repair_error:
                self._reject(
                    repair_error.failure.code,
                    "provider Answer JSON envelope recovery failed",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=envelope_issues,
                    cause=repair_error,
                )
        try:
            projection = ProviderAnswerProjectionV2.model_validate(payload)
        except ValidationError as exc:
            initial_issues = self._safe_validation_issues(exc, payload)
            if (
                repair_attempt >= 1
                or self._ingress.config.max_structural_repair_attempts < 1
            ):
                self._reject(
                    ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID,
                    (
                        "provider Answer projection remained invalid after one recovery"
                        if repair_attempt >= 1
                        else "provider Answer projection failed Runtime validation"
                    ),
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=repair_attempt,
                    validation_issues=initial_issues,
                    cause=exc,
                )
            repaired_from_response_hash = result.response_hash
            repair_input = ProviderRuntimeInput.from_payload(
                input_ref=f"{answer_request.request_ref}:repair:1",
                input_kind="main_agent_answer_projection_repair_v2",
                payload={
                    "request": answer_request.model_dump(mode="json"),
                    "instruction": (
                        "Regenerate one corrected minimal answer projection only. "
                        "Resolve every validation issue, preserve supported business "
                        "meaning. Use allowed_grounding_refs only for factual Evidence "
                        "Atoms, allowed_runtime_fact_refs only for runtime_fact, and "
                        "allowed_limitation_refs only for uncertainty. Do not "
                        "return commentary or Runtime-owned block graph fields."
                    ),
                    "validation_issues": [
                        issue.model_dump(mode="json") for issue in initial_issues
                    ],
                    "rejected_projection": self._bounded_repair_projection(payload),
                    "answer_business_rules": provider_answer_business_rules_v2(),
                    "answer_projection_schema": (
                        ProviderAnswerProjectionV2.model_json_schema()
                    ),
                },
            )
            try:
                result = await self._invoke_answer_projection(
                    answer_request=answer_request,
                    context=bundle.context,
                    runtime_input=repair_input,
                    call_kind="answer-projection-repair-1",
                )
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=ANSWER_PROJECTION_CONTRACT_REF,
                    )
                )
            except ProviderAdapterError as repair_exc:
                self._reject_adapter_json_failure(
                    repair_exc,
                    "provider Answer structural repair failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    repair_attempt=1,
                    validation_issues=initial_issues,
                )
            except ProviderBoundaryRejected as repair_exc:
                self._reject(
                    repair_exc.failure.code,
                    "provider Answer structural repair failed at JSON ingress",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=initial_issues,
                    cause=repair_exc,
                )
            try:
                projection = ProviderAnswerProjectionV2.model_validate(payload)
            except ValidationError as repair_exc:
                self._reject(
                    ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID,
                    "provider Answer projection remained invalid after one repair",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=self._safe_validation_issues(
                        repair_exc,
                        payload,
                    ),
                    cause=repair_exc,
                )
            repair_attempt = 1
        if not self._answer_grounding_selection_valid(
            projection=projection,
            request=answer_request,
        ):
            if (
                repair_attempt >= 1
                or self._ingress.config.max_structural_repair_attempts < 1
            ):
                self._reject(
                    ProviderBoundaryFailureCode.ANSWER_GROUNDING_REJECTED,
                    "provider Answer selected Grounding outside its eligible Answer refs",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=repair_attempt,
                )
            repaired_from_response_hash = result.response_hash
            grounding_repair_input = ProviderRuntimeInput.from_payload(
                input_ref=f"{answer_request.request_ref}:grounding-repair:1",
                input_kind="main_agent_answer_grounding_repair_v2",
                payload={
                    "request": answer_request.model_dump(mode="json"),
                    "instruction": (
                        "Regenerate one corrected minimal answer projection only. "
                        "The previous projection selected Grounding outside its "
                        "eligible category. Use allowed_grounding_refs only for "
                        "fact, inference, or recommendation; use "
                        "allowed_runtime_fact_refs only for runtime_fact about the "
                        "loaded resource identity or status; use "
                        "allowed_limitation_refs only for uncertainty. If none of "
                        "those authorities can support the requested content, use "
                        "uncertainty or general_advice without inventing refs."
                    ),
                    "repair_attempt": 1,
                    "validation_issues": [
                        {
                            "path": "$.items[*].grounding_refs",
                            "error_type": "grounding_category_not_allowed",
                        }
                    ],
                    "answer_business_rules": provider_answer_business_rules_v2(),
                    "answer_projection_schema": (
                        ProviderAnswerProjectionV2.model_json_schema()
                    ),
                },
            )
            try:
                result = await self._invoke_answer_projection(
                    answer_request=answer_request,
                    context=bundle.context,
                    runtime_input=grounding_repair_input,
                    call_kind="answer-grounding-repair-1",
                )
                ingress_request, ingress_receipt, payload = (
                    self._ingest_model_result(
                        result=result,
                        expected_contract_ref=ANSWER_PROJECTION_CONTRACT_REF,
                    )
                )
            except ProviderAdapterError as repair_error:
                self._reject_adapter_json_failure(
                    repair_error,
                    "provider Answer Grounding recovery failed before JSON ingress",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    repair_attempt=1,
                    validation_issues=(
                        ProviderValidationIssue(
                            path="$.items.grounding_refs",
                            error_type="grounding_category_not_allowed",
                        ),
                    ),
                )
            except ProviderBoundaryRejected as repair_error:
                self._reject(
                    repair_error.failure.code,
                    "provider Answer Grounding recovery failed at JSON ingress",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=(
                        ProviderValidationIssue(
                            path="$.items.grounding_refs",
                            error_type="grounding_category_not_allowed",
                        ),
                    ),
                    cause=repair_error,
                )
            try:
                projection = ProviderAnswerProjectionV2.model_validate(payload)
            except ValidationError as repair_exc:
                self._reject(
                    ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID,
                    "provider Answer Grounding repair violated the Answer schema",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                    validation_issues=self._safe_validation_issues(
                        repair_exc,
                        payload,
                    ),
                    cause=repair_exc,
                )
            repair_attempt = 1
            if not self._answer_grounding_selection_valid(
                projection=projection,
                request=answer_request,
            ):
                self._reject(
                    ProviderBoundaryFailureCode.ANSWER_GROUNDING_REJECTED,
                    "provider Answer selected ineligible Grounding after one repair",
                    stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                    structurally_repairable=True,
                    repair_attempt=1,
                )
        validated_receipt = ProviderIngressReceipt.build(
            request=ingress_request,
            normalized_payload=payload,
            normalization_steps=ingress_receipt.normalization_steps,
            schema_validated=True,
            validated_contract=projection,
        )
        outcome = ProviderAnswerGenerationOutcome.build(
            request=answer_request,
            provider_result_ref=result.result_ref,
            provider_response_hash=result.response_hash,
            provider_receipt_ref=result.provider_receipt_ref,
            ingress_receipt=validated_receipt,
            projection=projection,
            repair_attempt=repair_attempt,
            repaired_from_response_hash=repaired_from_response_hash,
        )
        return answer_request, outcome

    @staticmethod
    def _answer_grounding_selection_valid(
        *,
        projection: ProviderAnswerProjectionV2,
        request: ProviderAnswerGenerationRequest,
    ) -> bool:
        return (
            set(projection.evidence_grounding_refs()).issubset(
                request.allowed_grounding_refs
            )
            and set(projection.runtime_fact_grounding_refs()).issubset(
                request.allowed_runtime_fact_refs
            )
            and set(projection.limitation_grounding_refs()).issubset(
                request.allowed_limitation_refs
            )
        )

    async def _recover_answer_json_envelope(
        self,
        *,
        answer_request: ProviderAnswerGenerationRequest,
        context: ContextAssemblyResult,
        repair_reason: str,
        validation_issues: tuple[ProviderValidationIssue, ...],
    ) -> ProviderModelResult:
        envelope_repair_input = ProviderRuntimeInput.from_payload(
            input_ref=f"{answer_request.request_ref}:json-envelope-repair:1",
            input_kind="main_agent_answer_projection_json_envelope_repair_v2",
            payload={
                "request": answer_request.model_dump(mode="json"),
                "instruction": (
                    "The previous response was not one valid JSON object. "
                    "Regenerate exactly one minimal answer projection that "
                    "satisfies answer_projection_schema. Do not return Markdown, "
                    "commentary, Tool Calls, or Runtime-owned block graph fields."
                ),
                "repair_attempt": 1,
                "repair_reason": repair_reason,
                "validation_issues": [
                    issue.model_dump(mode="json") for issue in validation_issues
                ],
                "answer_business_rules": provider_answer_business_rules_v2(),
                "answer_projection_schema": (
                    ProviderAnswerProjectionV2.model_json_schema()
                ),
            },
        )
        try:
            return await self._invoke_answer_projection(
                answer_request=answer_request,
                context=context,
                runtime_input=envelope_repair_input,
                call_kind="answer-projection-json-envelope-repair-1",
            )
        except ProviderAdapterError as repair_error:
            self._reject_adapter_json_failure(
                repair_error,
                "provider Answer JSON envelope recovery failed",
                stage=ProviderBoundaryFailureStage.ANSWER_PROJECTION,
                repair_attempt=1,
                validation_issues=validation_issues,
            )

    async def _invoke_answer_projection(
        self,
        *,
        answer_request: ProviderAnswerGenerationRequest,
        context: ContextAssemblyResult,
        runtime_input: ProviderRuntimeInput,
        call_kind: str,
    ) -> ProviderModelResult:
        structured_output = None
        if self._adapter.capabilities.supports_structured_output:
            structured_output = ProviderStructuredOutputSpec.from_model(
                schema_name="provider_answer_projection_v2",
                output_model=ProviderAnswerProjectionV2,
                strict_mode=ProviderStrictMode.PREFERRED,
            )
        return await self._adapter.invoke(
            ProviderInvocationRequest(
                call_ref=self._call_ref(
                    request_ref=answer_request.request_ref,
                    request_hash=answer_request.request_hash,
                    call_kind=call_kind,
                    context_snapshot_hash=context.snapshot.snapshot_hash,
                ),
                task_ref=answer_request.task_ref,
                state_version=answer_request.state_version,
                consumer=ContextConsumer.MAIN_AGENT,
                context=context,
                registry_snapshot=None,
                runtime_input=runtime_input,
                structured_output=structured_output,
                tool_choice=ProviderToolChoice.NONE,
                tool_strict_mode=ProviderStrictMode.PREFERRED,
                max_output_tokens=self._max_output_tokens(context),
            )
        )

    def _tool_calls_outcome(
        self,
        *,
        request: MainAgentDecisionRequest,
        result: ProviderModelResult,
    ) -> ProviderToolCallsOutcomeV2:
        bindings: list[ProviderToolCallIngressBinding] = []
        for proposal in result.tool_call_proposals:
            ingress_request = ProviderIngressRequest.from_raw(
                call_ref=proposal.provider_tool_call_id,
                payload_kind=ProviderIngressPayloadKind.TOOL_ARGUMENTS,
                expected_contract_ref=f"tool-input:{proposal.tool_name}",
                raw_value=proposal.raw_arguments_json,
                max_size_bytes=min(
                    self._adapter.capabilities.max_arguments_bytes,
                    self._ingress.config.max_tool_arguments_bytes,
                ),
                tool_name=proposal.tool_name,
            )
            ingress_result = self._ingress.normalize(
                request=ingress_request,
                raw_value=proposal.raw_arguments_json,
            )
            if ingress_result.payload_hash != proposal.arguments_hash:
                self._reject(
                    ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                    "Tool arguments drifted across Provider boundaries",
                    stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
                )
            bindings.append(
                ProviderToolCallIngressBinding(
                    provider_tool_call_id=proposal.provider_tool_call_id,
                    tool_name=proposal.tool_name,
                    arguments_hash=proposal.arguments_hash,
                    ingress_receipt=ingress_result.receipt,
                )
            )
        return ProviderToolCallsOutcomeV2.build(
            request=request,
            result=result,
            bindings=tuple(bindings),
        )

    def _ingest_model_result(
        self,
        *,
        result: ProviderModelResult,
        expected_contract_ref: str,
    ) -> tuple[ProviderIngressRequest, ProviderIngressReceipt, dict[str, Any]]:
        if result.output_kind is ProviderOutputKind.STRUCTURED:
            if result.structured_payload is None:
                self._reject(
                    ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                    "provider omitted its structured JSON object",
                )
            raw_value = canonical_json(result.structured_payload)
            payload_kind = ProviderIngressPayloadKind.STRUCTURED_OUTPUT
        elif result.output_kind is ProviderOutputKind.TEXT:
            if result.assistant_text is None:
                self._reject(
                    ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                    "provider omitted its assistant JSON object",
                )
            raw_value = result.assistant_text
            payload_kind = ProviderIngressPayloadKind.ASSISTANT_JSON
        else:
            self._reject(
                ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID,
                "Tool Calls cannot enter a non-Tool Provider contract",
            )
        ingress_request = ProviderIngressRequest.from_raw(
            call_ref=result.call_ref,
            payload_kind=payload_kind,
            expected_contract_ref=expected_contract_ref,
            raw_value=raw_value,
            max_size_bytes=min(
                self._adapter.capabilities.max_response_bytes,
                self._ingress.config.max_response_bytes,
            ),
        )
        ingress_result = self._ingress.normalize(
            request=ingress_request,
            raw_value=raw_value,
        )
        return ingress_request, ingress_result.receipt, ingress_result.payload

    def _validate_decision_binding(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> None:
        snapshot = context.snapshot
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            self._reject(
                ProviderBoundaryFailureCode.CONTEXT_NOT_MODEL_READY,
                "next-action Context is not model-ready",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        if (
            snapshot.task_ref != request.task_ref
            or snapshot.state_version != request.origin_state_version
            or snapshot.snapshot_ref != request.context_snapshot_ref
            or snapshot.snapshot_hash != request.context_snapshot_hash
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "next-action Context did not match its decision request",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        if registry_snapshot is None:
            if request.registry_snapshot_ref is not None:
                self._reject(
                    ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                    "next-action request requires its Registry Snapshot",
                    stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
                )
            return
        if (
            request.registry_snapshot_ref != registry_snapshot.snapshot_ref
            or request.registry_snapshot_hash != registry_snapshot.snapshot_hash
            or request.visible_tools_hash != registry_snapshot.visible_tools_hash
            or request.visible_tool_names != registry_snapshot.visible_tool_names
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "next-action Registry Snapshot did not match its request",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )

    def _validate_locked_payload_binding(
        self,
        *,
        request: MainAgentDecisionRequest,
        selected: ProviderNextActionOutcome,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> None:
        self._validate_decision_binding(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        if (
            selected.request_ref != request.request_ref
            or selected.task_ref != request.task_ref
            or selected.origin_state_version != request.origin_state_version
            or selected.context_snapshot_ref != request.context_snapshot_ref
            or selected.registry_snapshot_ref != request.registry_snapshot_ref
            or selected.decision.action_kind
            not in {
                MainAgentModelActionKind.PLAN,
                MainAgentModelActionKind.REPLAN,
                MainAgentModelActionKind.REQUEST_INFORMATION,
            }
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "locked action payload did not match its accepted V2 selection",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )

    def _validate_answer_context(
        self,
        *,
        decision_request: MainAgentDecisionRequest,
        next_action: ProviderNextActionOutcome,
        context: ContextAssemblyResult,
    ) -> None:
        snapshot = context.snapshot
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            self._reject(
                ProviderBoundaryFailureCode.CONTEXT_NOT_MODEL_READY,
                "Answer Context is not model-ready",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        if (
            next_action.request_ref != decision_request.request_ref
            or next_action.decision.action_kind is not MainAgentModelActionKind.ANSWER
            or snapshot.task_ref != decision_request.task_ref
            or snapshot.state_version != next_action.origin_state_version
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.registry_snapshot_ref is not None
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "Answer Context did not match its accepted answer selection",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )

    def _validate_terminal_answer_context(
        self,
        *,
        decision_request: MainAgentDecisionRequest,
        authorization: RuntimeTerminalAnswerAuthorizationV2,
        context: ContextAssemblyResult,
        guard_feedback_refs: tuple[str, ...],
    ) -> None:
        snapshot = context.snapshot
        available_refs = {entry.entry_ref for entry in context.projection_entries}
        if snapshot.status not in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            self._reject(
                ProviderBoundaryFailureCode.CONTEXT_NOT_MODEL_READY,
                "terminal Answer Context is not model-ready",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )
        if (
            authorization.request_ref != decision_request.request_ref
            or authorization.task_ref != decision_request.task_ref
            or authorization.origin_state_version
            != decision_request.origin_state_version
            or authorization.context_snapshot_ref != snapshot.snapshot_ref
            or authorization.context_snapshot_hash != snapshot.snapshot_hash
            or snapshot.task_ref != decision_request.task_ref
            or snapshot.state_version != decision_request.origin_state_version
            or snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or snapshot.registry_snapshot_ref is not None
            or guard_feedback_refs != authorization.guard_feedback_refs
            or not set(guard_feedback_refs).issubset(available_refs)
        ):
            self._reject(
                ProviderBoundaryFailureCode.RUNTIME_BINDING_INVALID,
                "terminal Answer authority did not match its Guard feedback Context",
                stage=ProviderBoundaryFailureStage.RUNTIME_BINDING,
            )

    @staticmethod
    def _allowed_answer_refs(
        context: ContextAssemblyResult,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        evidence: dict[str, None] = {}
        runtime_facts: dict[str, None] = {}
        limitations: dict[str, None] = {}
        for entry in context.projection_entries:
            if entry.kind is ContextEntryKind.EVIDENCE_ATOM:
                evidence.setdefault(entry.entry_ref, None)
                limitations.setdefault(entry.entry_ref, None)
                continue
            if (
                entry.kind is ContextEntryKind.GROUNDING
                and entry.authority_label
                == "authorized-resource-identity-receipt"
            ):
                runtime_facts.setdefault(entry.entry_ref, None)
                continue
            if entry.kind in {
                ContextEntryKind.GROUNDING,
                ContextEntryKind.LIMITATION,
            }:
                limitations.setdefault(entry.entry_ref, None)
        return tuple(evidence), tuple(runtime_facts), tuple(limitations)

    @staticmethod
    def _bounded_repair_projection(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep repair input bounded without persisting rejected Provider content."""

        serialized = canonical_json(payload)
        if len(serialized.encode("utf-8")) <= _MAX_REPAIR_PROJECTION_BYTES:
            return payload
        return {
            "projection_omitted": True,
            "projection_hash": canonical_hash(payload),
            "reason": "repair_projection_size_limit",
        }

    @staticmethod
    def _is_recoverable_adapter_json_envelope(
        error: ProviderAdapterError,
    ) -> bool:
        return (
            error.failure.code
            is ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID
            and error.failure.retryable
            and error.failure.response_hash is not None
            and error.failure.provider_receipt_ref is not None
        )

    @staticmethod
    def _adapter_json_envelope_issue(
        error: ProviderAdapterError,
    ) -> ProviderValidationIssue:
        return ProviderValidationIssue(
            path="$",
            error_type=error.failure.code.value,
        )

    def _reject_adapter_json_failure(
        self,
        error: ProviderAdapterError,
        safe_message: str,
        *,
        stage: ProviderBoundaryFailureStage,
        repair_attempt: int,
        validation_issues: tuple[ProviderValidationIssue, ...] = (),
    ) -> Never:
        if error.failure.code is ProviderErrorCode.RESPONSE_JSON_ENVELOPE_INVALID:
            code = ProviderBoundaryFailureCode.JSON_ENVELOPE_INVALID
        elif error.failure.code is ProviderErrorCode.RESPONSE_JSON_SIZE_LIMIT:
            code = ProviderBoundaryFailureCode.JSON_SIZE_LIMIT
        else:
            raise error
        self._reject(
            code,
            safe_message,
            stage=stage,
            structurally_repairable=(repair_attempt == 1),
            repair_attempt=repair_attempt,
            validation_issues=validation_issues,
            cause=error,
        )

    @classmethod
    def _safe_validation_issues(
        cls,
        error: ValidationError,
        payload: dict[str, Any],
    ) -> tuple[ProviderValidationIssue, ...]:
        """Project only schema paths/types, never Pydantic messages or inputs."""

        candidates: list[tuple[str, str]] = []
        for detail in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ):
            path = "$"
            for component in detail.get("loc", ()):
                if isinstance(component, int):
                    path += f"[{component}]"
                    continue
                safe_component = cls._safe_issue_token(str(component), "field")
                path += f".{safe_component}"
            error_type = cls._safe_issue_token(
                str(detail.get("type") or "validation_error"),
                "validation_error",
            ).lower()
            candidates.append((path[:240], error_type[:100]))

        items = payload.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items[:128]):
                if not isinstance(item, dict):
                    continue
                base = f"$.items[{index}]"
                kind = item.get("kind")
                refs = item.get("grounding_refs")
                has_refs = isinstance(refs, (list, tuple)) and bool(refs)
                has_basis = isinstance(item.get("basis"), str) and bool(
                    item["basis"].strip()
                )
                has_limitation = isinstance(item.get("limitation"), str) and bool(
                    item["limitation"].strip()
                )
                if kind in {"inference", "recommendation"} and not has_basis:
                    candidates.append((f"{base}.basis", "required_for_kind"))
                elif kind not in {"inference", "recommendation"} and (
                    item.get("basis") is not None
                ):
                    candidates.append((f"{base}.basis", "forbidden_for_kind"))
                if kind == "uncertainty" and not has_limitation:
                    candidates.append(
                        (f"{base}.limitation", "required_for_kind")
                    )
                elif kind != "uncertainty" and item.get("limitation") is not None:
                    candidates.append(
                        (f"{base}.limitation", "forbidden_for_kind")
                    )
                if kind == "general_advice" and has_refs:
                    candidates.append(
                        (f"{base}.grounding_refs", "forbidden_for_kind")
                    )
                elif kind != "general_advice" and not has_refs:
                    candidates.append(
                        (f"{base}.grounding_refs", "required_for_kind")
                    )
                if isinstance(refs, (list, tuple)) and len(refs) != len(
                    {str(value) for value in refs}
                ):
                    candidates.append((f"{base}.grounding_refs", "duplicate"))

        issues: list[ProviderValidationIssue] = []
        seen: set[tuple[str, str]] = set()
        for path, error_type in candidates:
            key = (path, error_type)
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                ProviderValidationIssue(path=path, error_type=error_type)
            )
            if len(issues) == 32:
                break
        return tuple(issues)

    @staticmethod
    def _safe_issue_token(value: str, fallback: str) -> str:
        token = "".join(
            character
            if (
                "a" <= character.lower() <= "z"
                or "0" <= character <= "9"
                or character in {"_", "-", "."}
            )
            else "_"
            for character in value.strip()
        )
        return token.strip(".") or fallback

    def _max_output_tokens(self, context: ContextAssemblyResult) -> int:
        return min(
            self._adapter.capabilities.max_output_tokens,
            context.snapshot.reserved_output_tokens,
        )

    def _require_enabled(self) -> None:
        if not self._ingress.config.enabled:
            self._reject(
                ProviderBoundaryFailureCode.BOUNDARY_DISABLED,
                "provider boundary V2 is disabled",
            )

    @staticmethod
    def _call_ref(
        *,
        request_ref: str,
        request_hash: str,
        call_kind: str,
        context_snapshot_hash: str,
    ) -> str:
        digest = canonical_hash(
            {
                "request_ref": request_ref,
                "request_hash": request_hash,
                "call_kind": call_kind,
                "context_snapshot_hash": context_snapshot_hash,
            }
        )
        return f"model-call:{digest.removeprefix('sha256:')}"

    @staticmethod
    def _reject(
        code: ProviderBoundaryFailureCode,
        safe_message: str,
        *,
        stage: ProviderBoundaryFailureStage = ProviderBoundaryFailureStage.INGRESS,
        structurally_repairable: bool = False,
        repair_attempt: int = 0,
        validation_issues: tuple[ProviderValidationIssue, ...] = (),
        cause: Exception | None = None,
    ) -> Never:
        rejection = ProviderBoundaryRejected(
            ProviderBoundaryFailure(
                stage=stage,
                code=code,
                safe_message=safe_message,
                structurally_repairable=structurally_repairable,
                repair_attempt=repair_attempt,
                validation_issues=validation_issues,
            )
        )
        if cause is None:
            raise rejection
        raise rejection from cause


def build_provider_decision_answer_orchestrator_v2(
    *,
    adapter: ProviderAdapter | None = None,
    ingress: DeterministicProviderJsonIngressAdapter | None = None,
    answer_context_provider: ProviderAnswerContextProvider | None = None,
    slot_capability_snapshot: SlotCapabilitySnapshot | None = None,
) -> ProviderDecisionAnswerOrchestratorV2:
    """Explicit composition helper; omitted ingress remains disabled."""

    return ProviderDecisionAnswerOrchestratorV2(
        adapter=adapter,
        ingress=ingress,
        answer_context_provider=answer_context_provider,
        slot_capability_snapshot=slot_capability_snapshot,
    )
