"""Tool Call ledger ports and caller-transaction SQLAlchemy implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Protocol
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .persistence_models import (
    BidPureAgentAction,
    BidPureAgentCall,
    BidPureAgentContextSnapshot,
    BidPureAgentTask,
)
from .runtime import ToolCallRequest
from .tool_runtime import GuardDecision, canonical_hash, canonical_json
from .tools import CanonicalToolDefinition


class ToolLedgerError(RuntimeError):
    pass


class ToolLedgerConflict(ToolLedgerError):
    pass


@dataclass(frozen=True, slots=True)
class ToolLedgerReservation:
    ledger_call_id: str
    status: str
    replayed: bool
    canonical_result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ToolLedgerSettlement:
    ledger_call_id: str
    status: str
    accepted: bool
    replayed: bool


class ToolCallLedgerPort(Protocol):
    def reserve(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
    ) -> ToolLedgerReservation: ...

    def record_rejection(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
        canonical_result: dict[str, Any],
        error_code: str,
    ) -> ToolLedgerReservation: ...

    def mark_running(self, ledger_call_id: str) -> None: ...

    def settle(
        self,
        *,
        ledger_call_id: str,
        canonical_result: dict[str, Any],
        guard_decisions: tuple[GuardDecision, ...],
        provider_receipt_ref: str | None,
        error_code: str | None,
    ) -> ToolLedgerSettlement: ...


def _binding_ref(definition: CanonicalToolDefinition) -> str:
    binding = definition.execution
    if binding.kind == "disabled":
        return binding.binding_id
    if binding.kind == "local":
        return f"local:{binding.handler_id}"
    return f"mcp:{binding.server_id}:{binding.remote_tool_name}"


def _call_key(call: ToolCallRequest, arguments_hash: str) -> str:
    digest = canonical_hash(
        {
            "task_ref": call.task_ref,
            "state_version": call.state_version,
            "model_turn_ref": call.model_turn_ref,
            "provider_tool_call_id": call.provider_tool_call_id,
            "tool_name": call.tool_name,
            "arguments_hash": arguments_hash,
            "registry_snapshot_hash": call.registry_snapshot_hash,
            "visible_tools_hash": call.visible_tools_hash,
            "authorization_snapshot_ref": call.authorization_snapshot_ref,
        }
    )
    return f"tool-call:{digest.removeprefix('sha256:')}"


def _guard_json(decisions: tuple[GuardDecision, ...]) -> list[dict[str, Any]]:
    return [decision.model_dump(mode="json") for decision in decisions]


def _input_json(call: ToolCallRequest) -> dict[str, Any] | None:
    try:
        value = json.loads(canonical_json(call.arguments))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class SqlAlchemyToolCallLedger:
    """Flush-only ledger. The Gateway caller owns commit/rollback boundaries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def reserve(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
    ) -> ToolLedgerReservation:
        return self._reserve_or_replay(
            call=call,
            definition=definition,
            arguments_hash=arguments_hash,
            guard_decisions=guard_decisions,
            terminal_result=None,
            error_code=None,
        )

    def record_rejection(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
        canonical_result: dict[str, Any],
        error_code: str,
    ) -> ToolLedgerReservation:
        return self._reserve_or_replay(
            call=call,
            definition=definition,
            arguments_hash=arguments_hash,
            guard_decisions=guard_decisions,
            terminal_result=canonical_result,
            error_code=error_code,
        )

    def mark_running(self, ledger_call_id: str) -> None:
        row = self._row(ledger_call_id, lock=True)
        if row.status == "running":
            return
        if row.status != "reserved":
            raise ToolLedgerConflict("only a reserved tool call may start")
        row.status = "running"
        self.db.flush()

    def settle(
        self,
        *,
        ledger_call_id: str,
        canonical_result: dict[str, Any],
        guard_decisions: tuple[GuardDecision, ...],
        provider_receipt_ref: str | None,
        error_code: str | None,
    ) -> ToolLedgerSettlement:
        row = self._row(ledger_call_id, lock=True)
        output_hash = canonical_hash(canonical_result)
        terminal_status = "succeeded" if canonical_result.get("ok") is True else "failed"
        if row.status in {"succeeded", "failed", "ignored_late"}:
            if row.output_hash != output_hash.removeprefix("sha256:"):
                raise ToolLedgerConflict("tool call result changed after settlement")
            return ToolLedgerSettlement(
                ledger_call_id=row.id,
                status=row.status,
                accepted=row.status in {"succeeded", "failed"},
                replayed=True,
            )
        if row.status not in {"reserved", "running"}:
            raise ToolLedgerConflict("tool call cannot be settled from its current state")
        task = (
            self.db.query(BidPureAgentTask)
            .filter(BidPureAgentTask.id == row.task_id)
            .with_for_update()
            .one_or_none()
        )
        if task is None:
            raise ToolLedgerConflict("tool call task no longer exists")
        accepted = (
            task.status == "running"
            and task.cancellation_fence_id is None
            and int(task.state_version) == int(row.state_version)
            and task.in_flight_action_id == row.action_id
        )
        row.status = terminal_status if accepted else "ignored_late"
        row.output_ref = f"tool-result:{output_hash.removeprefix('sha256:')}"
        row.output_hash = output_hash.removeprefix("sha256:")
        row.output_json = canonical_result
        row.guard_decisions_json = _guard_json(guard_decisions)
        row.provider_receipt_ref = provider_receipt_ref
        row.error_code = error_code if accepted else "PURE_AGENT_LATE_RESULT_IGNORED"
        row.completed_at = datetime.now(timezone.utc)
        self.db.flush()
        return ToolLedgerSettlement(
            ledger_call_id=row.id,
            status=row.status,
            accepted=accepted,
            replayed=False,
        )

    def _reserve_or_replay(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
        terminal_result: dict[str, Any] | None,
        error_code: str | None,
    ) -> ToolLedgerReservation:
        call_key = _call_key(call, arguments_hash)
        existing = (
            self.db.query(BidPureAgentCall)
            .filter(
                BidPureAgentCall.task_id == call.task_ref,
                BidPureAgentCall.call_key == call_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            self._assert_same_call(
                existing,
                call=call,
                definition=definition,
                arguments_hash=arguments_hash,
            )
            return ToolLedgerReservation(
                ledger_call_id=existing.id,
                status=existing.status,
                replayed=True,
                canonical_result=existing.output_json,
            )

        provider_id_reuse = (
            self.db.query(BidPureAgentCall)
            .filter(
                BidPureAgentCall.task_id == call.task_ref,
                or_(
                    BidPureAgentCall.call_ref == call.call_ref,
                    and_(
                        BidPureAgentCall.model_turn_ref == call.model_turn_ref,
                        or_(
                            BidPureAgentCall.provider_tool_call_id
                            == call.provider_tool_call_id,
                            BidPureAgentCall.sequence_no == call.sequence,
                        ),
                    ),
                ),
            )
            .with_for_update()
            .one_or_none()
        )
        if provider_id_reuse is not None:
            raise ToolLedgerConflict("provider tool call identity was reused")

        self._assert_scope(call)
        now = datetime.now(timezone.utc)
        output_hash = canonical_hash(terminal_result) if terminal_result is not None else None
        row = BidPureAgentCall(
            id=str(uuid.uuid4()),
            task_id=call.task_ref,
            action_id=call.action_ref,
            context_snapshot_id=call.context_snapshot_ref,
            call_key=call_key,
            call_ref=call.call_ref,
            provider_tool_call_id=call.provider_tool_call_id,
            model_turn_ref=call.model_turn_ref,
            sequence_no=call.sequence,
            state_version=call.state_version,
            call_kind="tool",
            provider_binding_ref=_binding_ref(definition),
            operation_name=definition.name,
            registry_snapshot_ref=call.registry_snapshot_ref,
            registry_snapshot_hash=call.registry_snapshot_hash,
            visible_tools_hash=call.visible_tools_hash,
            authorization_snapshot_ref=call.authorization_snapshot_ref,
            guard_decisions_json=_guard_json(guard_decisions),
            status="failed" if terminal_result is not None else "reserved",
            input_hash=arguments_hash.removeprefix("sha256:"),
            input_json=_input_json(call),
            output_ref=(
                f"tool-result:{output_hash.removeprefix('sha256:')}"
                if output_hash is not None
                else None
            ),
            output_hash=(
                output_hash.removeprefix("sha256:") if output_hash is not None else None
            ),
            output_json=terminal_result,
            provider_receipt_ref=None,
            input_tokens=None,
            output_tokens=None,
            cost_micro_usd=None,
            error_code=error_code,
            created_at=now,
            completed_at=now if terminal_result is not None else None,
        )
        self.db.add(row)
        self.db.flush()
        return ToolLedgerReservation(
            ledger_call_id=row.id,
            status=row.status,
            replayed=False,
            canonical_result=terminal_result,
        )

    def _assert_scope(self, call: ToolCallRequest) -> None:
        task = (
            self.db.query(BidPureAgentTask)
            .filter(BidPureAgentTask.id == call.task_ref)
            .with_for_update()
            .one_or_none()
        )
        action = (
            self.db.query(BidPureAgentAction)
            .filter(BidPureAgentAction.id == call.action_ref)
            .with_for_update()
            .one_or_none()
        )
        if (
            task is None
            or action is None
            or action.task_id != call.task_ref
            or action.status not in {"accepted", "running"}
            or task.status != "running"
            or task.cancellation_fence_id is not None
            or task.in_flight_action_id != call.action_ref
            or int(task.state_version) != call.state_version
        ):
            raise ToolLedgerConflict("tool call task/action/state scope is invalid")
        if call.context_snapshot_ref is not None:
            context = (
                self.db.query(BidPureAgentContextSnapshot)
                .filter(BidPureAgentContextSnapshot.id == call.context_snapshot_ref)
                .one_or_none()
            )
            if context is None or context.task_id != call.task_ref:
                raise ToolLedgerConflict("tool call context snapshot is outside the task")

    @staticmethod
    def _assert_same_call(
        row: BidPureAgentCall,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
    ) -> None:
        if (
            row.provider_tool_call_id != call.provider_tool_call_id
            or row.model_turn_ref != call.model_turn_ref
            or row.call_ref != call.call_ref
            or int(row.sequence_no) != call.sequence
            or row.action_id != call.action_ref
            or row.operation_name != definition.name
            or row.input_hash != arguments_hash.removeprefix("sha256:")
            or (
                row.input_json is not None
                and canonical_hash(row.input_json) != arguments_hash
            )
            or row.registry_snapshot_ref != call.registry_snapshot_ref
            or row.registry_snapshot_hash != call.registry_snapshot_hash
            or row.visible_tools_hash != call.visible_tools_hash
            or row.authorization_snapshot_ref != call.authorization_snapshot_ref
        ):
            raise ToolLedgerConflict("tool call idempotency key was reused")

    def _row(self, ledger_call_id: str, *, lock: bool) -> BidPureAgentCall:
        query = self.db.query(BidPureAgentCall).filter(
            BidPureAgentCall.id == ledger_call_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise ToolLedgerConflict("tool call ledger row was not found")
        return row


class InMemoryToolCallLedger:
    """Explicit non-durable fake ledger for later isolated contract tests."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def reserve(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
    ) -> ToolLedgerReservation:
        return self._reserve(
            call=call,
            definition=definition,
            arguments_hash=arguments_hash,
            terminal_result=None,
        )

    def record_rejection(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        guard_decisions: tuple[GuardDecision, ...],
        canonical_result: dict[str, Any],
        error_code: str,
    ) -> ToolLedgerReservation:
        return self._reserve(
            call=call,
            definition=definition,
            arguments_hash=arguments_hash,
            terminal_result=canonical_result,
        )

    def mark_running(self, ledger_call_id: str) -> None:
        row = self._rows[ledger_call_id]
        if row["status"] == "reserved":
            row["status"] = "running"

    def settle(
        self,
        *,
        ledger_call_id: str,
        canonical_result: dict[str, Any],
        guard_decisions: tuple[GuardDecision, ...],
        provider_receipt_ref: str | None,
        error_code: str | None,
    ) -> ToolLedgerSettlement:
        row = self._rows[ledger_call_id]
        digest = canonical_hash(canonical_result)
        if row.get("output_hash") is not None:
            if row["output_hash"] != digest:
                raise ToolLedgerConflict("tool call result changed after settlement")
            return ToolLedgerSettlement(
                ledger_call_id=ledger_call_id,
                status=row["status"],
                accepted=True,
                replayed=True,
            )
        row["status"] = "succeeded" if canonical_result.get("ok") else "failed"
        row["result"] = canonical_result
        row["output_hash"] = digest
        return ToolLedgerSettlement(
            ledger_call_id=ledger_call_id,
            status=row["status"],
            accepted=True,
            replayed=False,
        )

    def _reserve(
        self,
        *,
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
        terminal_result: dict[str, Any] | None,
    ) -> ToolLedgerReservation:
        key = _call_key(call, arguments_hash)
        existing = self._rows.get(key)
        if existing is not None:
            if existing["identity"] != self._identity(call, definition, arguments_hash):
                raise ToolLedgerConflict("tool call idempotency key was reused")
            return ToolLedgerReservation(
                ledger_call_id=key,
                status=existing["status"],
                replayed=True,
                canonical_result=existing.get("result"),
            )
        for row in self._rows.values():
            identity = row["identity"]
            if identity["task_ref"] != call.task_ref:
                continue
            if identity["call_ref"] == call.call_ref or (
                identity["model_turn_ref"] == call.model_turn_ref
                and (
                    identity["provider_tool_call_id"] == call.provider_tool_call_id
                    or identity["sequence"] == call.sequence
                )
            ):
                raise ToolLedgerConflict("provider tool call identity was reused")
        self._rows[key] = {
            "status": "failed" if terminal_result is not None else "reserved",
            "result": terminal_result,
            "output_hash": (
                canonical_hash(terminal_result) if terminal_result is not None else None
            ),
            "identity": self._identity(call, definition, arguments_hash),
        }
        return ToolLedgerReservation(
            ledger_call_id=key,
            status=self._rows[key]["status"],
            replayed=False,
            canonical_result=terminal_result,
        )

    @staticmethod
    def _identity(
        call: ToolCallRequest,
        definition: CanonicalToolDefinition,
        arguments_hash: str,
    ) -> dict[str, Any]:
        return {
            "task_ref": call.task_ref,
            "call_ref": call.call_ref,
            "model_turn_ref": call.model_turn_ref,
            "provider_tool_call_id": call.provider_tool_call_id,
            "sequence": call.sequence,
            "action_ref": call.action_ref,
            "tool_name": definition.name,
            "arguments_hash": arguments_hash,
            "registry_snapshot_hash": call.registry_snapshot_hash,
            "visible_tools_hash": call.visible_tools_hash,
            "authorization_snapshot_ref": call.authorization_snapshot_ref,
        }
