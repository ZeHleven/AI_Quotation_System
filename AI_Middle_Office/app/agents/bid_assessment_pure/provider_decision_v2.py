"""V2-B contracts that separate control selection from answer generation.

The next-action contract is intentionally small: it selects one Runtime
capability, but it cannot carry a Plan, Slot, Tool arguments, or an answer.
Native Function Calls remain a separate Provider branch.  When the selected
action is ``answer``, a second, independently hash-bound call produces the
minimal ``ProviderAnswerProjectionV2``.

For Plan, Replan, and information requests, a later payload call is bound to
the accepted action and cannot select another action kind.  This module
declares contracts and ports only; it does not call a Provider or execute a
capability.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from .action_runtime import (
    InformationRequestAction,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    PlanActionRequest,
)
from .answer_contracts import SourceBasis
from .common import Reference, StrictContract
from .provider_answer_projection_v2 import ProviderAnswerProjectionV2
from .provider_ingress_v2 import (
    ProviderIngressPayloadKind,
    ProviderIngressReceipt,
    ProviderValidationIssue,
)
from .retrieval_convergence_v2 import RetrievalConvergenceDecisionV2
from .runtime import ContextAssemblyResult
from .tool_runtime import RegistrySnapshot, canonical_hash


NEXT_ACTION_CONTRACT_REF = "bid.pure-agent.provider.next-action.v2"
ANSWER_PROJECTION_CONTRACT_REF = "bid.pure-agent.provider.answer-projection.v2.3"
LOCKED_PLAN_PAYLOAD_CONTRACT_REF = (
    "bid.pure-agent.provider.locked-plan-payload.v2"
)
LOCKED_INFORMATION_PAYLOAD_CONTRACT_REF = (
    "bid.pure-agent.provider.locked-information-payload.v2.1"
)

InformationNeed = Annotated[
    str,
    Field(min_length=1, max_length=500),
]
ProviderRetrievalToolName = Literal[
    "documents_outline",
    "bid_document_search",
    "enterprise_knowledge_search",
    "evidence_read",
]
LanguageTag = Annotated[
    str,
    Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
    ),
]


class ProviderRetrievalRequest(StrictContract):
    """Concrete unresolved needs and the smallest requested Tool surface."""

    information_needs: tuple[InformationNeed, ...] = Field(
        min_length=1,
        max_length=12,
    )
    requested_tool_names: tuple[ProviderRetrievalToolName, ...] = Field(
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_retrieval(self) -> "ProviderRetrievalRequest":
        if len(self.information_needs) != len(set(self.information_needs)):
            raise ValueError("retrieval information_needs must be unique")
        if len(self.requested_tool_names) != len(set(self.requested_tool_names)):
            raise ValueError("requested_tool_names must be unique")
        return self


class ProviderNextActionRecoveryReason(str, Enum):
    """Runtime-owned reasons that may narrow the next admissible action."""

    ANSWER_GUARD_EVIDENCE_UPGRADE = "answer_guard_evidence_upgrade"
    PRE_ANSWER_EVIDENCE_READINESS = "pre_answer_evidence_readiness"
    ANSWER_SCHEMA_EVIDENCE_UPGRADE = "answer_schema_evidence_upgrade"


class ProviderNextActionRecoveryConstraintV2(StrictContract):
    """Fail-closed eligibility constraint derived from accepted Runtime state.

    This is not a model-authored Plan.  It prevents an Answer from being generated
    or regenerated when readable search candidates still need to be upgraded into
    citable Evidence Atoms.
    """

    reason_code: ProviderNextActionRecoveryReason
    required_action_kind: Literal["retrieve"] = "retrieve"
    required_tool_names: tuple[ProviderRetrievalToolName, ...] = Field(
        min_length=1,
        max_length=4,
    )
    candidate_refs: tuple[Reference, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_constraint(self) -> "ProviderNextActionRecoveryConstraintV2":
        if len(self.required_tool_names) != len(set(self.required_tool_names)):
            raise ValueError("recovery required_tool_names must be unique")
        if len(self.candidate_refs) != len(set(self.candidate_refs)):
            raise ValueError("recovery candidate_refs must be unique")
        return self


class ProviderNextActionDecision(StrictContract):
    """Small model-visible control decision with no action payload."""

    action_kind: MainAgentModelActionKind | Literal["retrieve"]
    concise_basis: str = Field(min_length=1, max_length=500)
    information_needs: tuple[InformationNeed, ...] = Field(
        default_factory=tuple,
        max_length=12,
        description=(
            "Missing user-supplied facts only. Populate only when selecting "
            "request_information; Runtime creates and validates the actual Slot."
        ),
    )
    target_source_bases: tuple[SourceBasis, ...] = Field(
        default_factory=tuple,
        max_length=8,
        description=(
            "Source categories relevant to the selected action; advisory only and "
            "never an authorization or proof that a source exists."
        ),
    )
    retrieval_request: ProviderRetrievalRequest | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "ProviderNextActionDecision":
        if len(self.information_needs) != len(set(self.information_needs)):
            raise ValueError("information_needs must be unique")
        if len(self.target_source_bases) != len(set(self.target_source_bases)):
            raise ValueError("target_source_bases must be unique")
        requests_user_information = (
            self.action_kind is MainAgentModelActionKind.REQUEST_INFORMATION
        )
        if requests_user_information != bool(self.information_needs):
            raise ValueError(
                "information_needs must appear exactly for request_information"
            )
        retrieves = self.action_kind == "retrieve"
        if retrieves != (self.retrieval_request is not None):
            raise ValueError(
                "retrieval_request must appear exactly for retrieve"
            )
        return self


class ProviderInformationRequestPayloadV2(StrictContract):
    """Model-owned Slot semantics; Runtime injects every validator reference."""

    slot_kind: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
        description=(
            "Exact semantic Slot kind from available_slot_capabilities."
        ),
    )
    request_message: str = Field(min_length=1, max_length=2000)
    blocking_reason: str = Field(min_length=1, max_length=500)


class ProviderNextActionOutcome(StrictContract):
    """Hash-bound result of the small control-decision call."""

    outcome_ref: Reference
    outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    task_ref: Reference
    origin_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    registry_snapshot_ref: Reference | None
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference
    ingress_request_ref: Reference
    ingress_receipt: ProviderIngressReceipt
    decision: ProviderNextActionDecision
    recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None = None
    repair_attempt: int = Field(default=0, ge=0, le=1)
    repaired_from_response_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    repair_validation_issues: tuple[ProviderValidationIssue, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @classmethod
    def build(
        cls,
        *,
        request: MainAgentDecisionRequest,
        provider_result_ref: str,
        provider_response_hash: str,
        provider_receipt_ref: str,
        ingress_receipt: ProviderIngressReceipt,
        decision: ProviderNextActionDecision,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2 | None = None,
        repair_attempt: int = 0,
        repaired_from_response_hash: str | None = None,
        repair_validation_issues: tuple[ProviderValidationIssue, ...] = (),
    ) -> "ProviderNextActionOutcome":
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": provider_result_ref,
            "provider_response_hash": provider_response_hash,
            "provider_receipt_ref": provider_receipt_ref,
            "ingress_request_ref": ingress_receipt.request_ref,
            "ingress_receipt": ingress_receipt.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "recovery_constraint": (
                None
                if recovery_constraint is None
                else recovery_constraint.model_dump(mode="json")
            ),
            "repair_attempt": repair_attempt,
            "repaired_from_response_hash": repaired_from_response_hash,
            "repair_validation_issues": [
                issue.model_dump(mode="json")
                for issue in repair_validation_issues
            ],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            outcome_ref=f"provider-next-action:{digest.removeprefix('sha256:')}",
            outcome_hash=digest,
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderNextActionOutcome":
        if self.ingress_request_ref != self.ingress_receipt.request_ref:
            raise ValueError("Provider ingress receipt belongs to another request")
        if self.ingress_receipt.payload_kind is (
            ProviderIngressPayloadKind.TOOL_ARGUMENTS
        ):
            raise ValueError("Tool arguments cannot be a next-action decision")
        if not self.ingress_receipt.schema_validated:
            raise ValueError("next-action outcome requires schema-validated ingress")
        if self.ingress_receipt.validated_contract_hash != canonical_hash(
            self.decision
        ):
            raise ValueError("next-action decision drifted from validated ingress")
        if self.recovery_constraint is not None:
            retrieval_request = self.decision.retrieval_request
            if (
                self.decision.action_kind
                != self.recovery_constraint.required_action_kind
                or retrieval_request is None
                or retrieval_request.requested_tool_names
                != self.recovery_constraint.required_tool_names
            ):
                raise ValueError(
                    "next-action decision violates its Runtime recovery constraint"
                )
        repaired = self.repair_attempt == 1
        if repaired != (self.repaired_from_response_hash is not None):
            raise ValueError(
                "next-action repair hash must appear exactly for one repair"
            )
        if repaired != bool(self.repair_validation_issues):
            raise ValueError(
                "next-action repair issues must appear exactly for one repair"
            )
        issue_keys = tuple(
            (issue.path, issue.error_type)
            for issue in self.repair_validation_issues
        )
        if len(issue_keys) != len(set(issue_keys)):
            raise ValueError("next-action repair issues must be unique")
        body = self.model_dump(mode="json", exclude={"outcome_ref", "outcome_hash"})
        digest = canonical_hash(body)
        if self.outcome_hash != digest:
            raise ValueError("outcome_hash does not match next-action outcome")
        if self.outcome_ref != (
            f"provider-next-action:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("outcome_ref does not match next-action outcome")
        return self


class ProviderLockedActionPayloadOutcome(StrictContract):
    """Hash-bound payload generated under one immutable V2 action selection."""

    outcome_ref: Reference
    outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    task_ref: Reference
    origin_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    registry_snapshot_ref: Reference | None
    selected_outcome_ref: Reference
    selected_outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    action_kind: MainAgentModelActionKind
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference
    ingress_request_ref: Reference
    ingress_receipt: ProviderIngressReceipt
    plan_request: PlanActionRequest | None = None
    information_request: InformationRequestAction | None = None
    repair_attempt: int = Field(default=0, ge=0, le=1)
    repaired_from_response_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    repair_validation_issues: tuple[ProviderValidationIssue, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @classmethod
    def build(
        cls,
        *,
        request: MainAgentDecisionRequest,
        selected: ProviderNextActionOutcome,
        provider_result_ref: str,
        provider_response_hash: str,
        provider_receipt_ref: str,
        ingress_receipt: ProviderIngressReceipt,
        payload: PlanActionRequest | InformationRequestAction,
        repair_attempt: int = 0,
        repaired_from_response_hash: str | None = None,
        repair_validation_issues: tuple[ProviderValidationIssue, ...] = (),
    ) -> "ProviderLockedActionPayloadOutcome":
        if (
            selected.request_ref != request.request_ref
            or selected.task_ref != request.task_ref
            or selected.origin_state_version != request.origin_state_version
            or selected.context_snapshot_ref != request.context_snapshot_ref
            or selected.registry_snapshot_ref != request.registry_snapshot_ref
        ):
            raise ValueError(
                "locked payload selection belongs to another decision request"
            )
        is_plan = selected.decision.action_kind in {
            MainAgentModelActionKind.PLAN,
            MainAgentModelActionKind.REPLAN,
        }
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "selected_outcome_ref": selected.outcome_ref,
            "selected_outcome_hash": selected.outcome_hash,
            "action_kind": selected.decision.action_kind,
            "provider_result_ref": provider_result_ref,
            "provider_response_hash": provider_response_hash,
            "provider_receipt_ref": provider_receipt_ref,
            "ingress_request_ref": ingress_receipt.request_ref,
            "ingress_receipt": ingress_receipt.model_dump(mode="json"),
            "plan_request": (
                payload.model_dump(mode="json") if is_plan else None
            ),
            "information_request": (
                payload.model_dump(mode="json") if not is_plan else None
            ),
            "repair_attempt": repair_attempt,
            "repaired_from_response_hash": repaired_from_response_hash,
            "repair_validation_issues": [
                issue.model_dump(mode="json")
                for issue in repair_validation_issues
            ],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            outcome_ref=(
                "provider-locked-action-payload:"
                f"{digest.removeprefix('sha256:')}"
            ),
            outcome_hash=digest,
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderLockedActionPayloadOutcome":
        is_plan = self.action_kind in {
            MainAgentModelActionKind.PLAN,
            MainAgentModelActionKind.REPLAN,
        }
        if self.action_kind not in {
            MainAgentModelActionKind.PLAN,
            MainAgentModelActionKind.REPLAN,
            MainAgentModelActionKind.REQUEST_INFORMATION,
        }:
            raise ValueError("locked payload action kind is not payload-bearing")
        if self.ingress_request_ref != self.ingress_receipt.request_ref:
            raise ValueError(
                "locked payload ingress receipt belongs to another request"
            )
        if self.ingress_receipt.payload_kind is (
            ProviderIngressPayloadKind.TOOL_ARGUMENTS
        ):
            raise ValueError("Tool arguments cannot be a locked action payload")
        if is_plan != (self.plan_request is not None) or is_plan == (
            self.information_request is not None
        ):
            raise ValueError("locked payload shape does not match selected action")
        payload = self.plan_request or self.information_request
        if payload is None or (
            self.ingress_receipt.validated_contract_hash
            != canonical_hash(payload)
        ):
            raise ValueError("locked payload drifted from validated ingress")
        if not self.ingress_receipt.schema_validated:
            raise ValueError("locked payload requires schema-validated ingress")
        repaired = self.repair_attempt == 1
        if repaired != (self.repaired_from_response_hash is not None):
            raise ValueError("locked payload repair hash is inconsistent")
        if repaired != bool(self.repair_validation_issues):
            raise ValueError("locked payload repair issues are inconsistent")
        body = self.model_dump(mode="json", exclude={"outcome_ref", "outcome_hash"})
        digest = canonical_hash(body)
        if self.outcome_hash != digest or self.outcome_ref != (
            "provider-locked-action-payload:"
            f"{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("locked payload outcome identity drifted")
        return self


class RuntimeTerminalAnswerAuthorizationV2(StrictContract):
    """Runtime-owned authority to retry an Answer after retrieval saturation."""

    authorization_ref: Reference
    authorization_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    task_ref: Reference
    origin_state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    convergence_decision_ref: Reference
    convergence_decision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    guard_feedback_refs: tuple[Reference, ...] = Field(min_length=1, max_length=64)
    concise_basis: str = Field(min_length=1, max_length=500)

    @classmethod
    def build(
        cls,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        convergence: RetrievalConvergenceDecisionV2,
        guard_feedback_refs: tuple[str, ...],
    ) -> "RuntimeTerminalAnswerAuthorizationV2":
        if not convergence.saturated:
            raise ValueError("terminal Answer authorization requires saturation")
        if not guard_feedback_refs:
            raise ValueError("terminal Answer authorization requires Guard feedback")
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": context.snapshot.snapshot_ref,
            "context_snapshot_hash": context.snapshot.snapshot_hash,
            "convergence_decision_ref": (
                "retrieval-convergence:"
                + convergence.decision_hash.removeprefix("sha256:")
            ),
            "convergence_decision_hash": convergence.decision_hash,
            "guard_feedback_refs": guard_feedback_refs,
            "concise_basis": (
                "retrieval saturated after a rejected Answer; retry once using "
                "the persisted Grounding Guard feedback"
            ),
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            authorization_ref=(
                "runtime-terminal-answer-authorization:"
                + digest.removeprefix("sha256:")
            ),
            authorization_hash=digest,
        )

    @model_validator(mode="after")
    def validate_authorization(self) -> "RuntimeTerminalAnswerAuthorizationV2":
        if len(self.guard_feedback_refs) != len(set(self.guard_feedback_refs)):
            raise ValueError("Guard feedback refs must be unique")
        if self.convergence_decision_ref != (
            "retrieval-convergence:"
            + self.convergence_decision_hash.removeprefix("sha256:")
        ):
            raise ValueError("terminal Answer convergence ref drifted")
        body = self.model_dump(
            mode="json",
            exclude={"authorization_ref", "authorization_hash"},
        )
        digest = canonical_hash(body)
        if self.authorization_hash != digest:
            raise ValueError("terminal Answer authorization hash drifted")
        if self.authorization_ref != (
            "runtime-terminal-answer-authorization:"
            + digest.removeprefix("sha256:")
        ):
            raise ValueError("terminal Answer authorization ref drifted")
        return self


class ProviderAnswerAuthorizationKind(str, Enum):
    PROVIDER_NEXT_ACTION = "provider_next_action"
    RUNTIME_TERMINAL_CONVERGENCE = "runtime_terminal_convergence"


class ProviderAnswerGenerationRequest(StrictContract):
    """Dedicated Answer call bound to one accepted ``answer`` selection."""

    request_ref: Reference
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_ref: Reference
    turn_ref: Reference
    parent_decision_request_ref: Reference
    authorization_kind: ProviderAnswerAuthorizationKind
    answer_authorization_ref: Reference
    answer_authorization_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    next_action_kind: Literal[MainAgentModelActionKind.ANSWER] = (
        MainAgentModelActionKind.ANSWER
    )
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    context_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_grounding_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    allowed_runtime_fact_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    allowed_limitation_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    guard_feedback_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    response_language_hint: LanguageTag | None = None

    @classmethod
    def build(
        cls,
        *,
        decision_request: MainAgentDecisionRequest,
        next_action: ProviderNextActionOutcome,
        state_version: int,
        context_snapshot_ref: str,
        context_snapshot_hash: str,
        allowed_grounding_refs: tuple[str, ...] = (),
        allowed_runtime_fact_refs: tuple[str, ...] = (),
        allowed_limitation_refs: tuple[str, ...] = (),
        guard_feedback_refs: tuple[str, ...] = (),
        response_language_hint: str | None = None,
    ) -> "ProviderAnswerGenerationRequest":
        if next_action.decision.action_kind is not MainAgentModelActionKind.ANSWER:
            raise ValueError("Answer generation requires an accepted answer selection")
        if next_action.request_ref != decision_request.request_ref:
            raise ValueError("next-action outcome belongs to another decision request")
        body = {
            "task_ref": decision_request.task_ref,
            "turn_ref": decision_request.turn_ref,
            "parent_decision_request_ref": decision_request.request_ref,
            "authorization_kind": (
                ProviderAnswerAuthorizationKind.PROVIDER_NEXT_ACTION
            ),
            "answer_authorization_ref": next_action.outcome_ref,
            "answer_authorization_hash": next_action.outcome_hash,
            "next_action_kind": MainAgentModelActionKind.ANSWER,
            "state_version": state_version,
            "context_snapshot_ref": context_snapshot_ref,
            "context_snapshot_hash": context_snapshot_hash,
            "allowed_grounding_refs": allowed_grounding_refs,
            "allowed_runtime_fact_refs": allowed_runtime_fact_refs,
            "allowed_limitation_refs": allowed_limitation_refs,
            "guard_feedback_refs": guard_feedback_refs,
            "response_language_hint": response_language_hint,
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            request_ref=f"provider-answer-request:{digest.removeprefix('sha256:')}",
            request_hash=digest,
        )

    @classmethod
    def build_from_terminal_authorization(
        cls,
        *,
        decision_request: MainAgentDecisionRequest,
        authorization: RuntimeTerminalAnswerAuthorizationV2,
        state_version: int,
        context_snapshot_ref: str,
        context_snapshot_hash: str,
        allowed_grounding_refs: tuple[str, ...] = (),
        allowed_runtime_fact_refs: tuple[str, ...] = (),
        allowed_limitation_refs: tuple[str, ...] = (),
        response_language_hint: str | None = None,
    ) -> "ProviderAnswerGenerationRequest":
        if authorization.request_ref != decision_request.request_ref:
            raise ValueError("terminal Answer authority belongs to another request")
        if (
            authorization.task_ref != decision_request.task_ref
            or authorization.origin_state_version
            != decision_request.origin_state_version
            or authorization.context_snapshot_ref != context_snapshot_ref
            or authorization.context_snapshot_hash != context_snapshot_hash
        ):
            raise ValueError("terminal Answer authority is stale or cross-scoped")
        body = {
            "task_ref": decision_request.task_ref,
            "turn_ref": decision_request.turn_ref,
            "parent_decision_request_ref": decision_request.request_ref,
            "authorization_kind": (
                ProviderAnswerAuthorizationKind.RUNTIME_TERMINAL_CONVERGENCE
            ),
            "answer_authorization_ref": authorization.authorization_ref,
            "answer_authorization_hash": authorization.authorization_hash,
            "next_action_kind": MainAgentModelActionKind.ANSWER,
            "state_version": state_version,
            "context_snapshot_ref": context_snapshot_ref,
            "context_snapshot_hash": context_snapshot_hash,
            "allowed_grounding_refs": allowed_grounding_refs,
            "allowed_runtime_fact_refs": allowed_runtime_fact_refs,
            "allowed_limitation_refs": allowed_limitation_refs,
            "guard_feedback_refs": authorization.guard_feedback_refs,
            "response_language_hint": response_language_hint,
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            request_ref=f"provider-answer-request:{digest.removeprefix('sha256:')}",
            request_hash=digest,
        )

    @model_validator(mode="after")
    def validate_request(self) -> "ProviderAnswerGenerationRequest":
        for field_name in (
            "allowed_grounding_refs",
            "allowed_runtime_fact_refs",
            "allowed_limitation_refs",
            "guard_feedback_refs",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if set(self.allowed_grounding_refs) & set(self.allowed_runtime_fact_refs):
            raise ValueError(
                "business Grounding and runtime fact refs must remain disjoint"
            )
        if (
            self.authorization_kind
            is ProviderAnswerAuthorizationKind.RUNTIME_TERMINAL_CONVERGENCE
            and not self.guard_feedback_refs
        ):
            raise ValueError(
                "Runtime terminal Answer authority requires Guard feedback refs"
            )
        body = self.model_dump(mode="json", exclude={"request_ref", "request_hash"})
        digest = canonical_hash(body)
        if self.request_hash != digest:
            raise ValueError("request_hash does not match Answer generation request")
        if self.request_ref != (
            f"provider-answer-request:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("request_ref does not match Answer generation request")
        return self


class ProviderAnswerGenerationOutcome(StrictContract):
    """Answer-only Provider result; Runtime grounding remains authoritative."""

    outcome_ref: Reference
    outcome_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference
    provider_result_ref: Reference
    provider_response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_receipt_ref: Reference
    ingress_request_ref: Reference
    ingress_receipt: ProviderIngressReceipt
    projection: ProviderAnswerProjectionV2
    repair_attempt: int = Field(default=0, ge=0, le=1)
    repaired_from_response_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @classmethod
    def build(
        cls,
        *,
        request: ProviderAnswerGenerationRequest,
        provider_result_ref: str,
        provider_response_hash: str,
        provider_receipt_ref: str,
        ingress_receipt: ProviderIngressReceipt,
        projection: ProviderAnswerProjectionV2,
        repair_attempt: int = 0,
        repaired_from_response_hash: str | None = None,
    ) -> "ProviderAnswerGenerationOutcome":
        selected_evidence_refs = set(projection.evidence_grounding_refs())
        if not selected_evidence_refs.issubset(request.allowed_grounding_refs):
            raise ValueError(
                "Answer projection selected non-evidence factual Grounding"
            )
        selected_runtime_fact_refs = set(
            projection.runtime_fact_grounding_refs()
        )
        if not selected_runtime_fact_refs.issubset(
            request.allowed_runtime_fact_refs
        ):
            raise ValueError(
                "Answer projection selected non-authoritative runtime Grounding"
            )
        selected_limitation_refs = set(projection.limitation_grounding_refs())
        if not selected_limitation_refs.issubset(
            request.allowed_limitation_refs
        ):
            raise ValueError(
                "Answer projection selected an unauthorized limitation receipt"
            )
        if bool(repair_attempt) != bool(repaired_from_response_hash):
            raise ValueError(
                "repaired response hash must appear exactly for a repair attempt"
            )
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "state_version": request.state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "provider_result_ref": provider_result_ref,
            "provider_response_hash": provider_response_hash,
            "provider_receipt_ref": provider_receipt_ref,
            "ingress_request_ref": ingress_receipt.request_ref,
            "ingress_receipt": ingress_receipt.model_dump(mode="json"),
            "projection": projection.model_dump(mode="json"),
            "repair_attempt": repair_attempt,
            "repaired_from_response_hash": repaired_from_response_hash,
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            outcome_ref=f"provider-answer-outcome:{digest.removeprefix('sha256:')}",
            outcome_hash=digest,
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderAnswerGenerationOutcome":
        if self.ingress_request_ref != self.ingress_receipt.request_ref:
            raise ValueError("Provider ingress receipt belongs to another request")
        if self.ingress_receipt.payload_kind is (
            ProviderIngressPayloadKind.TOOL_ARGUMENTS
        ):
            raise ValueError("Tool arguments cannot be an Answer projection")
        if not self.ingress_receipt.schema_validated:
            raise ValueError("Answer outcome requires schema-validated ingress")
        if self.ingress_receipt.validated_contract_hash != canonical_hash(
            self.projection
        ):
            raise ValueError("Answer projection drifted from validated ingress")
        if bool(self.repair_attempt) != bool(self.repaired_from_response_hash):
            raise ValueError(
                "repaired response hash must appear exactly for a repair attempt"
            )
        body = self.model_dump(mode="json", exclude={"outcome_ref", "outcome_hash"})
        digest = canonical_hash(body)
        if self.outcome_hash != digest:
            raise ValueError("outcome_hash does not match Answer generation outcome")
        if self.outcome_ref != (
            f"provider-answer-outcome:{digest.removeprefix('sha256:')}"
        ):
            raise ValueError("outcome_ref does not match Answer generation outcome")
        return self


class ProviderNextActionPort(Protocol):
    """Select one non-Tool next action from a bounded decision Context."""

    async def decide_next_action(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> ProviderNextActionOutcome: ...


class ProviderAnswerGenerationPort(Protocol):
    """Generate only an Answer projection from its dedicated Context view."""

    async def generate_answer(
        self,
        *,
        request: ProviderAnswerGenerationRequest,
        context: ContextAssemblyResult,
    ) -> ProviderAnswerGenerationOutcome: ...
