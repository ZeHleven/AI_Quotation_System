"""Canonical B03 Tool Gateway.

The ordered checks below are protocol safety checks. They do not prescribe
which tool the Agent must call or turn tool use into a business workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .rag_adapters import RagSourceUnavailable
from .registry import CanonicalToolRegistry, ToolRegistryError
from .runtime import ToolCallRequest
from .state import AgentTaskState, AgentTaskStatus
from .tool_call_ledger import (
    ToolCallLedgerPort,
    ToolLedgerConflict,
    ToolLedgerReservation,
)
from .tool_executor import (
    CanonicalToolExecutor,
    ToolBindingError,
    ToolBindingUnavailable,
    ToolDeadlineExceeded,
)
from .tool_guards import (
    DefaultExecutionGuard,
    DefaultProvenanceGuard,
    DefaultVisibilityGuard,
)
from .tool_runtime import (
    CanonicalToolMessage,
    ExecutionDeadline,
    GuardDecision,
    RegistrySnapshot,
    ToolGatewayLimits,
    ToolGuardPolicy,
    ToolProvenanceRecord,
    canonical_hash,
    canonical_json,
    freeze_registry_snapshot,
)
from .tools import (
    CanonicalToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionResult,
)


@dataclass(frozen=True, slots=True)
class ToolVisibilityProjection:
    snapshot: RegistrySnapshot
    decisions: tuple[tuple[str, GuardDecision], ...]


@dataclass(frozen=True, slots=True)
class ToolGatewayOutcome:
    result: ToolExecutionResult[Any]
    tool_message: CanonicalToolMessage | None
    ledger_call_id: str | None
    accepted_for_context: bool
    guard_decisions: tuple[GuardDecision, ...]
    replayed: bool
    provenance: tuple[ToolProvenanceRecord, ...] = ()


class ToolVisibilityProjector:
    """Combine relevance names with visibility policy, then freeze one snapshot."""

    def __init__(self, guard: DefaultVisibilityGuard | None = None) -> None:
        self._guard = guard or DefaultVisibilityGuard()

    def project(
        self,
        *,
        registry: CanonicalToolRegistry,
        relevant_names: tuple[str, ...],
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
    ) -> ToolVisibilityProjection:
        if len(relevant_names) != len(set(relevant_names)):
            raise ValueError("relevant tool names must be unique")
        visible: list[str] = []
        decisions: list[tuple[str, GuardDecision]] = []
        for name in relevant_names:
            definition = registry.get(name)
            decision = self._guard.evaluate(
                definition=definition,
                context=context,
                policy=policy,
            )
            decisions.append((name, decision))
            if decision.allowed:
                visible.append(name)
        snapshot = freeze_registry_snapshot(
            registry,
            visible_names=tuple(visible),
        )
        return ToolVisibilityProjection(
            snapshot=snapshot,
            decisions=tuple(decisions),
        )


class CanonicalToolGateway:
    def __init__(
        self,
        *,
        registry: CanonicalToolRegistry,
        executor: CanonicalToolExecutor,
        ledger: ToolCallLedgerPort,
        execution_guard: DefaultExecutionGuard | None = None,
        provenance_guard: DefaultProvenanceGuard | None = None,
        limits: ToolGatewayLimits | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._ledger = ledger
        self._execution_guard = execution_guard or DefaultExecutionGuard()
        self._provenance_guard = provenance_guard or DefaultProvenanceGuard()
        self._limits = limits or ToolGatewayLimits()

    async def execute(
        self,
        *,
        call: ToolCallRequest,
        task: AgentTaskState,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
        policy: ToolGuardPolicy,
        deadline: ExecutionDeadline,
    ) -> ToolGatewayOutcome:
        decisions: list[GuardDecision] = []
        definition: CanonicalToolDefinition | None = None
        arguments_hash = self._safe_arguments_hash(call)
        accepted_provenance: tuple[ToolProvenanceRecord, ...] = ()

        envelope_decision = self._validate_envelope(
            call=call,
            task=task,
            snapshot=snapshot,
            context=context,
        )
        decisions.append(envelope_decision)
        if not envelope_decision.allowed:
            return self._reject_without_definition(
                call=call,
                decision=envelope_decision,
                decisions=tuple(decisions),
                accepted=False,
            )

        try:
            definition = self._registry.get(call.tool_name)
        except ToolRegistryError:
            decision = GuardDecision(
                allowed=False,
                code="UNKNOWN_TOOL",
                message="requested tool is not registered",
            )
            decisions.append(decision)
            return self._reject_without_definition(
                call=call,
                decision=decision,
                decisions=tuple(decisions),
                accepted=True,
            )

        snapshot_decision = self._validate_snapshot(
            call=call,
            snapshot=snapshot,
        )
        decisions.append(snapshot_decision)
        if not snapshot_decision.allowed:
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=snapshot_decision,
                decisions=tuple(decisions),
            )

        try:
            arguments_json = canonical_json(call.arguments)
        except (TypeError, ValueError):
            decision = GuardDecision(
                allowed=False,
                code="ARGUMENTS_NOT_JSON",
                message="tool arguments must be a JSON object",
            )
            decisions.append(decision)
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=decision,
                decisions=tuple(decisions),
            )
        if len(arguments_json.encode("utf-8")) > self._limits.max_arguments_bytes:
            decision = GuardDecision(
                allowed=False,
                code="ARGUMENTS_TOO_LARGE",
                message="tool arguments exceed the allowed size",
            )
            decisions.append(decision)
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=decision,
                decisions=tuple(decisions),
            )

        try:
            # Arguments originate as JSON. Validate in JSON mode so strict
            # contracts still accept JSON arrays for tuple-shaped fields.
            arguments = definition.input_model.model_validate_json(arguments_json)
        except ValidationError:
            decision = GuardDecision(
                allowed=False,
                code="INPUT_VALIDATION_FAILED",
                message="tool arguments do not match the required fields",
            )
            decisions.append(decision)
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=decision,
                decisions=tuple(decisions),
            )

        execution_decision = await self._execution_guard.evaluate(
            call=call,
            definition=definition,
            arguments=arguments,
            snapshot=snapshot,
            context=context,
            policy=policy,
        )
        decisions.append(execution_decision)
        if not execution_decision.allowed:
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=execution_decision,
                decisions=tuple(decisions),
                error_code=ToolErrorCode.ACCESS_DENIED,
            )

        if deadline.is_expired():
            decision = GuardDecision(
                allowed=False,
                code="DEADLINE_EXPIRED",
                message="tool execution deadline has expired",
            )
            decisions.append(decision)
            return self._reject(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                decision=decision,
                decisions=tuple(decisions),
                error_code=ToolErrorCode.DEADLINE_EXCEEDED,
            )

        try:
            reservation = self._ledger.reserve(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                guard_decisions=tuple(decisions),
            )
        except ToolLedgerConflict:
            return self._safe_outcome(
                call=call,
                tool_name=definition.name,
                result=self._error_result(
                    ToolErrorCode.UNAVAILABLE,
                    "tool call ledger rejected this invocation",
                    retryable=False,
                ),
                decisions=tuple(decisions),
                ledger_call_id=None,
                accepted=False,
                replayed=False,
            )

        if reservation.replayed:
            return self._replay(
                call=call,
                definition=definition,
                reservation=reservation,
                decisions=tuple(decisions),
            )

        try:
            self._ledger.mark_running(reservation.ledger_call_id)
        except ToolLedgerConflict:
            return self._safe_outcome(
                call=call,
                tool_name=definition.name,
                result=self._error_result(
                    ToolErrorCode.UNAVAILABLE,
                    "tool call could not enter running state",
                    retryable=False,
                ),
                decisions=tuple(decisions),
                ledger_call_id=reservation.ledger_call_id,
                accepted=False,
                replayed=False,
            )
        try:
            binding_result = await self._executor.execute(
                definition=definition,
                arguments=arguments,
                context=context,
                deadline=deadline,
            )
            raw_json = canonical_json(binding_result.structured_content)
            if (
                len(raw_json.encode("utf-8"))
                > self._limits.max_binding_result_bytes
            ):
                raise ValueError("binding result exceeds the transport limit")
            output = definition.output_model.model_validate_json(raw_json)
            provenance_decision = self._provenance_guard.validate(
                definition=definition,
                arguments=arguments,
                output=output,
                provenance=binding_result.provenance,
                context=context,
            )
            decisions.append(provenance_decision)
            if not provenance_decision.allowed:
                result = self._error_result(
                    ToolErrorCode.CONTRACT_VIOLATION,
                    "tool output provenance could not be verified",
                    retryable=False,
                )
            else:
                typed_result = ToolExecutionResult[definition.output_model]
                result = typed_result(ok=True, data=output, error=None)
                accepted_provenance = binding_result.provenance
        except ToolDeadlineExceeded:
            result = self._error_result(
                ToolErrorCode.DEADLINE_EXCEEDED,
                "tool execution deadline was exceeded",
                retryable=True,
            )
        except (ToolBindingUnavailable, RagSourceUnavailable):
            result = self._error_result(
                ToolErrorCode.UNAVAILABLE,
                "tool source is currently unavailable",
                retryable=True,
            )
        except (ValidationError, ValueError, TypeError):
            result = self._error_result(
                ToolErrorCode.CONTRACT_VIOLATION,
                "tool output did not satisfy its canonical contract",
                retryable=False,
            )
        except ToolBindingError:
            result = self._error_result(
                ToolErrorCode.UNAVAILABLE,
                "tool binding could not complete the request",
                retryable=True,
            )
        except Exception:
            result = self._error_result(
                ToolErrorCode.INTERNAL_ERROR,
                "tool execution failed safely",
                retryable=False,
            )

        result = self._bounded_result(result)
        if not result.ok:
            accepted_provenance = ()
        canonical_result = result.model_dump(mode="json")
        error_code = result.error.code.value if result.error is not None else None
        try:
            settlement = self._ledger.settle(
                ledger_call_id=reservation.ledger_call_id,
                canonical_result=canonical_result,
                guard_decisions=tuple(decisions),
                provider_receipt_ref=(
                    binding_result.provider_receipt_ref
                    if "binding_result" in locals()
                    else None
                ),
                error_code=error_code,
            )
        except ToolLedgerConflict:
            return ToolGatewayOutcome(
                result=self._error_result(
                    ToolErrorCode.UNAVAILABLE,
                    "tool result could not be committed safely",
                    retryable=False,
                ),
                tool_message=None,
                ledger_call_id=reservation.ledger_call_id,
                accepted_for_context=False,
                guard_decisions=tuple(decisions),
                replayed=False,
            )
        if not settlement.accepted:
            return ToolGatewayOutcome(
                result=self._error_result(
                    ToolErrorCode.CANCELLED,
                    "tool result arrived after the task was cancelled or advanced",
                    retryable=False,
                ),
                tool_message=None,
                ledger_call_id=settlement.ledger_call_id,
                accepted_for_context=False,
                guard_decisions=tuple(decisions),
                replayed=settlement.replayed,
            )
        return self._safe_outcome(
            call=call,
            tool_name=definition.name,
            result=result,
            decisions=tuple(decisions),
            ledger_call_id=settlement.ledger_call_id,
            accepted=True,
            replayed=settlement.replayed,
            provenance=(
                accepted_provenance if not settlement.replayed else ()
            ),
        )

    def _validate_envelope(
        self,
        *,
        call: ToolCallRequest,
        task: AgentTaskState,
        snapshot: RegistrySnapshot,
        context: ToolExecutionContext,
    ) -> GuardDecision:
        if task.status is not AgentTaskStatus.RUNNING:
            return GuardDecision(
                allowed=False,
                code="TASK_NOT_RUNNING",
                message="tool calls require a running task",
            )
        if (
            call.task_ref != task.task_id
            or call.task_ref != context.task_ref
            or call.state_version != task.state_version
            or call.state_version != context.state_version
            or task.in_flight_action_ref != call.action_ref
            or call.authorization_snapshot_ref
            != context.authorization_snapshot_ref
            or call.context_snapshot_ref != context.context_snapshot_ref
        ):
            return GuardDecision(
                allowed=False,
                code="CALL_ENVELOPE_MISMATCH",
                message="tool call envelope does not match the active task context",
            )
        if call.registry_snapshot_ref != snapshot.snapshot_ref:
            return GuardDecision(
                allowed=False,
                code="REGISTRY_SNAPSHOT_MISMATCH",
                message="tool call does not match the frozen registry snapshot",
            )
        return GuardDecision(
            allowed=True,
            code="CALL_ENVELOPE_ACCEPTED",
            message="tool call envelope is valid",
        )

    def _validate_snapshot(
        self,
        *,
        call: ToolCallRequest,
        snapshot: RegistrySnapshot,
    ) -> GuardDecision:
        current = freeze_registry_snapshot(
            self._registry,
            visible_names=snapshot.visible_tool_names,
        )
        if (
            snapshot.snapshot_hash != current.snapshot_hash
            or call.registry_snapshot_hash != snapshot.snapshot_hash
            or call.visible_tools_hash != snapshot.visible_tools_hash
            or call.tool_name not in snapshot.visible_tool_names
        ):
            return GuardDecision(
                allowed=False,
                code="FROZEN_TOOL_SET_MISMATCH",
                message="tool definition or visible tool set is stale",
            )
        return GuardDecision(
            allowed=True,
            code="FROZEN_TOOL_SET_ACCEPTED",
            message="tool exists in the frozen visible set",
        )

    def _reject(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        decision: GuardDecision,
        decisions: tuple[GuardDecision, ...],
        error_code: ToolErrorCode = ToolErrorCode.INVALID_ARGUMENTS,
    ) -> ToolGatewayOutcome:
        result = self._error_result(error_code, decision.message, retryable=False)
        canonical_result = result.model_dump(mode="json")
        ledger_call_id = None
        replayed = False
        try:
            reservation = self._ledger.record_rejection(
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
                guard_decisions=decisions,
                canonical_result=canonical_result,
                error_code=error_code.value,
            )
            ledger_call_id = reservation.ledger_call_id
            replayed = reservation.replayed
            if reservation.canonical_result is not None:
                result = ToolExecutionResult[Any].model_validate_json(
                    canonical_json(reservation.canonical_result)
                )
        except ToolLedgerConflict:
            pass
        return self._safe_outcome(
            call=call,
            tool_name=definition.name,
            result=result,
            decisions=decisions,
            ledger_call_id=ledger_call_id,
            accepted=ledger_call_id is not None,
            replayed=replayed,
        )

    def _reject_without_definition(
        self,
        *,
        call: ToolCallRequest,
        decision: GuardDecision,
        decisions: tuple[GuardDecision, ...],
        accepted: bool,
    ) -> ToolGatewayOutcome:
        return self._safe_outcome(
            call=call,
            tool_name=call.tool_name,
            result=self._error_result(
                ToolErrorCode.ACCESS_DENIED,
                decision.message,
                retryable=False,
            ),
            decisions=decisions,
            ledger_call_id=None,
            accepted=accepted,
            replayed=False,
        )

    def _replay(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        reservation: ToolLedgerReservation,
        decisions: tuple[GuardDecision, ...],
    ) -> ToolGatewayOutcome:
        if reservation.canonical_result is None:
            return ToolGatewayOutcome(
                result=self._error_result(
                    ToolErrorCode.UNAVAILABLE,
                    "tool call is already in progress and will not be executed twice",
                    retryable=True,
                ),
                tool_message=None,
                ledger_call_id=reservation.ledger_call_id,
                accepted_for_context=False,
                guard_decisions=decisions,
                replayed=True,
            )
        else:
            try:
                result = ToolExecutionResult[Any].model_validate_json(
                    canonical_json(reservation.canonical_result)
                )
                if result.ok:
                    definition.output_model.model_validate_json(
                        canonical_json(result.data)
                    )
            except ValidationError:
                result = self._error_result(
                    ToolErrorCode.CONTRACT_VIOLATION,
                    "stored tool result failed canonical validation",
                    retryable=False,
                )
        return self._safe_outcome(
            call=call,
            tool_name=definition.name,
            result=result,
            decisions=decisions,
            ledger_call_id=reservation.ledger_call_id,
            accepted=True,
            replayed=True,
        )

    def _safe_outcome(
        self,
        *,
        call: ToolCallRequest,
        tool_name: str,
        result: ToolExecutionResult[Any],
        decisions: tuple[GuardDecision, ...],
        ledger_call_id: str | None,
        accepted: bool,
        replayed: bool,
        provenance: tuple[ToolProvenanceRecord, ...] = (),
    ) -> ToolGatewayOutcome:
        payload = result.model_dump(mode="json")
        content = canonical_json(payload)
        if len(content.encode("utf-8")) > self._limits.max_tool_message_bytes:
            return ToolGatewayOutcome(
                result=self._error_result(
                    ToolErrorCode.CONTRACT_VIOLATION,
                    "validated tool result exceeds the model message limit",
                    retryable=False,
                ),
                tool_message=None,
                ledger_call_id=ledger_call_id,
                accepted_for_context=False,
                guard_decisions=decisions,
                replayed=replayed,
                provenance=(),
            )
        message = CanonicalToolMessage(
            tool_call_id=call.provider_tool_call_id,
            name=tool_name,
            content=content,
            content_hash=canonical_hash(payload),
        )
        return ToolGatewayOutcome(
            result=result,
            tool_message=message if accepted else None,
            ledger_call_id=ledger_call_id,
            accepted_for_context=accepted,
            guard_decisions=decisions,
            replayed=replayed,
            provenance=provenance if accepted and result.ok else (),
        )

    @staticmethod
    def _error_result(
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> ToolExecutionResult[Any]:
        return ToolExecutionResult[Any](
            ok=False,
            data=None,
            error=ToolError(code=code, message=message, retryable=retryable),
        )

    def _bounded_result(
        self,
        result: ToolExecutionResult[Any],
    ) -> ToolExecutionResult[Any]:
        payload = result.model_dump(mode="json")
        if (
            len(canonical_json(payload).encode("utf-8"))
            <= self._limits.max_tool_message_bytes
        ):
            return result
        return self._error_result(
            ToolErrorCode.CONTRACT_VIOLATION,
            "validated tool result exceeds the model message limit",
            retryable=False,
        )

    @staticmethod
    def _safe_arguments_hash(call: ToolCallRequest) -> str:
        try:
            return canonical_hash(call.arguments)
        except (TypeError, ValueError):
            return canonical_hash(
                {
                    "invalid_arguments": True,
                    "call_ref": call.call_ref,
                    "tool_name": call.tool_name,
                }
            )
