"""Read-only, redacted diagnostic projection over the Pure Agent ledger."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .diagnostic_contracts import (
    DiagnosticBudgetAccount,
    DiagnosticBudgetEntry,
    DiagnosticCallTrace,
    DiagnosticCancellationTrace,
    DiagnosticLoopAction,
    DiagnosticLoopTrace,
    DiagnosticRecoveryCheckpoint,
    DiagnosticRecoveryTrace,
    DiagnosticStateTransition,
    DiagnosticTaskListItem,
    DiagnosticTaskPage,
    DiagnosticTaskState,
    DiagnosticTaskStatusCounts,
    PureAgentDiagnosticSnapshot,
)
from .persistence_models import (
    BidPureAgentAction,
    BidPureAgentBudgetAccount,
    BidPureAgentBudgetEntry,
    BidPureAgentCall,
    BidPureAgentCancellationFence,
    BidPureAgentCheckpoint,
    BidPureAgentContextSnapshot,
    BidPureAgentConversation,
    BidPureAgentEffectFence,
    BidPureAgentEvent,
    BidPureAgentPlan,
    BidPureAgentResponse,
    BidPureAgentSlot,
    BidPureAgentTask,
)
from .planning import ExecutionMode
from .repository import PureAgentNotFound
from .slots import PendingPhase
from .state import AgentTaskStatus


_EVENT_ACTIVITY = {
    "action.accepted": "action_accepted",
    "observation.accepted": "observation_accepted",
    "execution_mode.changed": "execution_mode_changed",
    "information.required": "input_required",
    "slot.validation_started": "input_validation_started",
    "slot.format_accepted": "input_format_accepted",
    "slot.validation_rejected": "input_validation_rejected",
    "slot.resolved": "input_resolved",
    "completion.accepted": "answer_committed",
    "fatal_error": "task_failed",
    "cancel.requested": "task_cancelled",
}
_SAFE_TOOL_OPERATIONS = {
    "documents_outline",
    "bid_document_search",
    "enterprise_knowledge_search",
    "evidence_read",
}
_SAFE_ACTION_TYPES = {
    "main_agent_decision",
    "plan",
    "replan",
    "tool_call_batch",
    "request_information",
    "answer",
}
_REDACTED_FIELDS = (
    "chain_of_thought",
    "prompt_and_provider_payload",
    "message_and_evidence_content",
    "tool_arguments_and_results",
    "authorization_and_scope_credentials",
    "resume_token_and_effect_key",
    "provider_receipt_and_binding_reference",
    "raw_error_and_stack_trace",
    "storage_path_object_key_and_url",
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _duration_ms(started: datetime, completed: datetime | None) -> int | None:
    if completed is None:
        return None
    start = _as_utc(started)
    end = _as_utc(completed)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _error_class(error_code: str | None) -> str | None:
    if not error_code:
        return None
    value = str(error_code).upper()
    if "LATE" in value:
        return "late_result"
    if "CANCEL" in value:
        return "cancelled"
    if "BUDGET" in value or "LIMIT" in value:
        return "budget_or_limit"
    if "TIMEOUT" in value or "DEADLINE" in value:
        return "timeout"
    if "PERMISSION" in value or "AUTH" in value or "GUARD" in value:
        return "permission_or_guard"
    if "CONTRACT" in value or "SCHEMA" in value or "VALID" in value:
        return "contract_validation"
    if "PROVIDER" in value or "RATE" in value or "TRANSPORT" in value:
        return "provider_or_transport"
    if "STORAGE" in value or "DATABASE" in value:
        return "storage"
    return "other_typed_error"


def _safe_operation(kind: str, operation_name: str) -> str:
    value = str(operation_name or "").lower()
    if kind == "tool":
        return value if value in _SAFE_TOOL_OPERATIONS else "registered_tool"
    if "intent" in value:
        return "intent_understanding"
    if "planner" in value or value in {"plan", "replan"}:
        return "planner"
    if "answer" in value and "repair" in value:
        return "answer_repair"
    if "answer" in value:
        return "answer_generation"
    if "main_agent" in value or "decision" in value:
        return "main_agent_decision"
    if "summary" in value or "memory" in value:
        return "context_or_memory_summary"
    return "model_call"


def _guard_outcome(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "not_recorded"
    decisions = [item for item in value if isinstance(item, dict)]
    if not decisions:
        return "not_recorded"
    return "rejected" if any(item.get("allowed") is False for item in decisions) else "passed"


class PureAgentDiagnosticProjector:
    """Project existing rows only; never schedules, repairs, or mutates a Task."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tasks(
        self,
        *,
        status: AgentTaskStatus | None,
        task_ref: str | None,
        page: int,
        page_size: int,
    ) -> DiagnosticTaskPage:
        query = self.db.query(BidPureAgentTask, BidPureAgentConversation).join(
            BidPureAgentConversation,
            BidPureAgentConversation.id == BidPureAgentTask.conversation_id,
        )
        if status is not None:
            query = query.filter(BidPureAgentTask.status == status.value)
        if task_ref:
            query = query.filter(BidPureAgentTask.id == task_ref)
        total = int(query.count())
        rows = (
            query.order_by(BidPureAgentTask.updated_at.desc(), BidPureAgentTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        task_ids = [task.id for task, _conversation in rows]
        action_counts = self._counts(BidPureAgentAction, task_ids)
        call_counts = self._counts(BidPureAgentCall, task_ids)
        checkpoint_counts = self._counts(BidPureAgentCheckpoint, task_ids)
        budget_counts = self._counts(BidPureAgentBudgetAccount, task_ids)
        status_rows = (
            self.db.query(BidPureAgentTask.status, func.count(BidPureAgentTask.id))
            .group_by(BidPureAgentTask.status)
            .all()
        )
        status_values = {str(name): int(count) for name, count in status_rows}
        items = tuple(
            DiagnosticTaskListItem(
                task_ref=task.id,
                conversation_ref=task.conversation_id,
                status=AgentTaskStatus(task.status),
                execution_mode=ExecutionMode(task.execution_mode),
                state_version=int(task.state_version),
                pending_phase=(
                    PendingPhase(task.pending_phase) if task.pending_phase else None
                ),
                assessment_bound=conversation.assessment_id is not None,
                has_open_action=task.in_flight_action_id is not None,
                action_count=action_counts.get(task.id, 0),
                call_count=call_counts.get(task.id, 0),
                checkpoint_count=checkpoint_counts.get(task.id, 0),
                budget_account_count=budget_counts.get(task.id, 0),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            for task, conversation in rows
        )
        return DiagnosticTaskPage(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            status_counts=DiagnosticTaskStatusCounts(
                running=status_values.get("running", 0),
                pending=status_values.get("pending", 0),
                completed=status_values.get("completed", 0),
                failed=status_values.get("failed", 0),
                cancelled=status_values.get("cancelled", 0),
            ),
        )

    def snapshot(self, *, task_ref: str) -> PureAgentDiagnosticSnapshot:
        task = (
            self.db.query(BidPureAgentTask)
            .filter(BidPureAgentTask.id == task_ref)
            .one_or_none()
        )
        if task is None:
            raise PureAgentNotFound("task was not found")
        generated_at = datetime.now(timezone.utc)
        actions = (
            self.db.query(BidPureAgentAction)
            .filter(BidPureAgentAction.task_id == task_ref)
            .order_by(BidPureAgentAction.sequence_no.asc(), BidPureAgentAction.id.asc())
            .all()
        )
        action_numbers = {row.id: int(row.sequence_no) for row in actions}
        events = (
            self.db.query(BidPureAgentEvent)
            .filter(BidPureAgentEvent.task_id == task_ref)
            .order_by(BidPureAgentEvent.state_version_after.asc())
            .all()
        )
        calls = (
            self.db.query(BidPureAgentCall)
            .filter(BidPureAgentCall.task_id == task_ref)
            .order_by(BidPureAgentCall.created_at.asc(), BidPureAgentCall.id.asc())
            .all()
        )
        accounts = (
            self.db.query(BidPureAgentBudgetAccount)
            .filter(BidPureAgentBudgetAccount.task_id == task_ref)
            .order_by(BidPureAgentBudgetAccount.resource_type.asc())
            .all()
        )
        entries = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(BidPureAgentBudgetEntry.task_id == task_ref)
            .order_by(BidPureAgentBudgetEntry.created_at.asc(), BidPureAgentBudgetEntry.id.asc())
            .all()
        )
        effects = (
            self.db.query(BidPureAgentEffectFence)
            .filter(BidPureAgentEffectFence.task_id == task_ref)
            .all()
        )
        effect_by_id = {row.id: row for row in effects}
        cancellation = (
            self.db.query(BidPureAgentCancellationFence)
            .filter(BidPureAgentCancellationFence.task_id == task_ref)
            .one_or_none()
        )
        checkpoints = (
            self.db.query(BidPureAgentCheckpoint)
            .filter(BidPureAgentCheckpoint.task_id == task_ref)
            .order_by(BidPureAgentCheckpoint.created_at.asc(), BidPureAgentCheckpoint.id.asc())
            .all()
        )
        slots = (
            self.db.query(BidPureAgentSlot)
            .filter(BidPureAgentSlot.task_id == task_ref)
            .all()
        )
        slot_status = {row.id: row.status for row in slots}
        integrity_warnings = self._integrity_warnings(
            task=task,
            events=events,
            cancellation=cancellation,
            checkpoints=checkpoints,
            effect_by_id=effect_by_id,
        )
        return PureAgentDiagnosticSnapshot(
            generated_at=generated_at,
            task=self._task_state(task),
            state_trace=self._state_trace(
                task=task,
                events=events,
                action_numbers=action_numbers,
            ),
            call_trace=self._call_trace(calls, action_numbers=action_numbers),
            budget_trace=self._budget_trace(
                accounts,
                entries=entries,
                action_numbers=action_numbers,
            ),
            loop_trace=self._loop_trace(actions),
            cancellation_trace=self._cancellation_trace(
                cancellation,
                effects=effects,
                actions=actions,
                calls=calls,
            ),
            recovery_trace=self._recovery_trace(
                task=task,
                checkpoints=checkpoints,
                effect_by_id=effect_by_id,
                slot_status=slot_status,
                now=generated_at,
            ),
            integrity_warnings=integrity_warnings,
            redacted_fields=_REDACTED_FIELDS,
        )

    def _task_state(self, task: BidPureAgentTask) -> DiagnosticTaskState:
        return DiagnosticTaskState(
            task_ref=task.id,
            conversation_ref=task.conversation_id,
            status=AgentTaskStatus(task.status),
            execution_mode=ExecutionMode(task.execution_mode),
            state_version=int(task.state_version),
            pending_phase=(PendingPhase(task.pending_phase) if task.pending_phase else None),
            has_plan=task.plan_ref is not None,
            has_open_action=task.in_flight_action_id is not None,
            observation_count=len(task.observation_refs_json or []),
            plan_version_count=self._task_count(BidPureAgentPlan, task.id),
            context_snapshot_count=self._task_count(BidPureAgentContextSnapshot, task.id),
            response_version_count=self._task_count(BidPureAgentResponse, task.id),
            created_at=task.created_at,
            updated_at=task.updated_at,
            terminal_at=task.terminal_at,
        )

    @staticmethod
    def _state_trace(
        *,
        task: BidPureAgentTask,
        events: list[BidPureAgentEvent],
        action_numbers: dict[str, int],
    ) -> tuple[DiagnosticStateTransition, ...]:
        trace: list[DiagnosticStateTransition] = [
            DiagnosticStateTransition(
                transition_no=1,
                activity="task_created",
                state_version_before=0,
                state_version_after=1,
                status_before=None,
                status_after=AgentTaskStatus.RUNNING,
                action_no=None,
                occurred_at=task.created_at,
            )
        ]
        for index, event in enumerate(events, start=2):
            trace.append(
                DiagnosticStateTransition(
                    transition_no=index,
                    activity=_EVENT_ACTIVITY.get(event.event_type, "runtime_transition"),
                    state_version_before=int(event.state_version_before),
                    state_version_after=int(event.state_version_after),
                    status_before=AgentTaskStatus(event.status_before),
                    status_after=AgentTaskStatus(event.status_after),
                    action_no=action_numbers.get(event.action_id),
                    occurred_at=event.occurred_at,
                )
            )
        return tuple(trace)

    @staticmethod
    def _call_trace(
        calls: list[BidPureAgentCall],
        *,
        action_numbers: dict[str, int],
    ) -> tuple[DiagnosticCallTrace, ...]:
        return tuple(
            DiagnosticCallTrace(
                call_no=index,
                kind=row.call_kind,
                operation=_safe_operation(row.call_kind, row.operation_name),
                status=row.status,
                state_version=int(row.state_version),
                sequence_no=int(row.sequence_no),
                action_no=action_numbers.get(row.action_id),
                guard_outcome=_guard_outcome(row.guard_decisions_json),
                error_class=_error_class(row.error_code),
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cost_micro_usd=row.cost_micro_usd,
                duration_ms=_duration_ms(row.created_at, row.completed_at),
                created_at=row.created_at,
                completed_at=row.completed_at,
            )
            for index, row in enumerate(calls, start=1)
        )

    @staticmethod
    def _budget_trace(
        accounts: list[BidPureAgentBudgetAccount],
        *,
        entries: list[BidPureAgentBudgetEntry],
        action_numbers: dict[str, int],
    ) -> tuple[DiagnosticBudgetAccount, ...]:
        entries_by_account: dict[str, list[BidPureAgentBudgetEntry]] = defaultdict(list)
        for entry in entries:
            entries_by_account[entry.account_id].append(entry)
        result: list[DiagnosticBudgetAccount] = []
        for account in accounts:
            account_entries = tuple(
                DiagnosticBudgetEntry(
                    entry_no=index,
                    kind=row.entry_kind,
                    amount=int(row.amount),
                    reserved_after=int(row.reserved_after),
                    actual_after=int(row.actual_after),
                    action_no=action_numbers.get(row.action_id),
                    created_at=row.created_at,
                )
                for index, row in enumerate(entries_by_account[account.id], start=1)
            )
            limit_amount = int(account.limit_amount)
            used = int(account.reserved_amount) + int(account.actual_amount)
            utilization = 0.0 if limit_amount == 0 else min(100.0, used * 100 / limit_amount)
            result.append(
                DiagnosticBudgetAccount(
                    resource_type=account.resource_type,
                    unit=account.unit,
                    limit_amount=limit_amount,
                    reserved_amount=int(account.reserved_amount),
                    actual_amount=int(account.actual_amount),
                    remaining_amount=max(0, limit_amount - used),
                    utilization_percent=round(utilization, 2),
                    entries=account_entries,
                )
            )
        return tuple(result)

    @staticmethod
    def _loop_trace(actions: list[BidPureAgentAction]) -> DiagnosticLoopTrace:
        first_action_by_fingerprint: dict[tuple[str, str], int] = {}
        seen_results: set[str] = set()
        records: list[DiagnosticLoopAction] = []
        exact_repeat_count = 0
        repeated_result_count = 0
        ignored_late_count = 0
        for action in actions:
            action_no = int(action.sequence_no)
            fingerprint = (str(action.action_type), str(action.arguments_hash))
            repeated_from = first_action_by_fingerprint.get(fingerprint)
            if repeated_from is None:
                first_action_by_fingerprint[fingerprint] = action_no
            else:
                exact_repeat_count += 1
            repeats_result = bool(action.result_hash and action.result_hash in seen_results)
            produced_new = bool(action.result_hash and action.result_hash not in seen_results)
            if repeats_result:
                repeated_result_count += 1
            if action.result_hash:
                seen_results.add(action.result_hash)
            if action.status == "ignored_late":
                ignored_late_count += 1
            records.append(
                DiagnosticLoopAction(
                    action_no=action_no,
                    action_type=(
                        action.action_type
                        if action.action_type in _SAFE_ACTION_TYPES
                        else "other_action"
                    ),
                    status=action.status,
                    exact_repeat_of_action_no=repeated_from,
                    repeats_prior_result=repeats_result,
                    produced_new_result_fingerprint=produced_new,
                    created_at=action.created_at,
                )
            )
        return DiagnosticLoopTrace(
            exact_repeat_count=exact_repeat_count,
            repeated_result_count=repeated_result_count,
            ignored_late_count=ignored_late_count,
            actions=tuple(records),
        )

    @staticmethod
    def _cancellation_trace(
        cancellation: BidPureAgentCancellationFence | None,
        *,
        effects: list[BidPureAgentEffectFence],
        actions: list[BidPureAgentAction],
        calls: list[BidPureAgentCall],
    ) -> DiagnosticCancellationTrace:
        late_action_ids = {
            row.id for row in actions if row.status == "ignored_late"
        }
        late_action_ids.update(
            row.action_id for row in calls if row.status == "ignored_late"
        )
        late_action_ids.update(
            row.action_id for row in effects if row.status == "ignored_late"
        )
        actor_kind = None
        if cancellation is not None:
            prefix = str(cancellation.requested_by_ref or "").split(":", 1)[0]
            actor_kind = prefix if prefix in {"user", "system", "service"} else "unknown"
        return DiagnosticCancellationTrace(
            present=cancellation is not None,
            cancellation_state_version=(
                int(cancellation.state_version) if cancellation is not None else None
            ),
            requested_at=(cancellation.created_at if cancellation is not None else None),
            actor_kind=actor_kind,
            reason_recorded=bool(cancellation and cancellation.reason.strip()),
            cancelled_effect_count=sum(row.status == "cancelled" for row in effects),
            ignored_late_result_count=len(late_action_ids),
        )

    @staticmethod
    def _recovery_trace(
        *,
        task: BidPureAgentTask,
        checkpoints: list[BidPureAgentCheckpoint],
        effect_by_id: dict[str, BidPureAgentEffectFence],
        slot_status: dict[str, str],
        now: datetime,
    ) -> DiagnosticRecoveryTrace:
        result: list[DiagnosticRecoveryCheckpoint] = []
        for index, checkpoint in enumerate(checkpoints, start=1):
            effect = effect_by_id.get(checkpoint.effect_fence_id)
            effect_status = effect.status if effect is not None else "missing"
            replay_policy = effect.replay_policy if effect is not None else None
            if checkpoint.recovery_lease_owner is None:
                lease_state = "unclaimed" if checkpoint.status == "open" else "released"
            else:
                lease_until = _as_utc(checkpoint.recovery_lease_until)
                lease_state = "active" if lease_until and lease_until > now else "expired"
            is_active = (
                checkpoint.status == "open"
                and task.active_checkpoint_id == checkpoint.id
                and task.status == AgentTaskStatus.PENDING.value
            )
            if not is_active:
                disposition = "historical"
            elif slot_status.get(checkpoint.slot_id) != "resolved":
                disposition = "wait_for_user"
            elif effect_status in {"cancelled", "ignored_late", "missing"}:
                disposition = "blocked"
            elif effect_status == "uncertain" or (
                replay_policy == "reconcile_required"
                and effect_status not in {"succeeded", "failed"}
            ):
                disposition = "reconcile"
            elif replay_policy == "no_replay" and effect_status in {"reserved", "running"}:
                disposition = "blocked"
            else:
                disposition = "resume"
            result.append(
                DiagnosticRecoveryCheckpoint(
                    checkpoint_no=index,
                    status=checkpoint.status,
                    suspended_state_version=int(checkpoint.suspended_state_version),
                    execution_mode=ExecutionMode(checkpoint.execution_mode),
                    effect_status=effect_status,
                    replay_policy=replay_policy,
                    lease_state=lease_state,
                    recovery_claim_count=int(checkpoint.recovery_fencing_token),
                    disposition=disposition,
                    created_at=checkpoint.created_at,
                    consumed_at=checkpoint.consumed_at,
                )
            )
        return DiagnosticRecoveryTrace(
            active_checkpoint_present=any(
                row.status == "open" and row.id == task.active_checkpoint_id
                for row in checkpoints
            ),
            checkpoints=tuple(result),
        )

    @staticmethod
    def _integrity_warnings(
        *,
        task: BidPureAgentTask,
        events: list[BidPureAgentEvent],
        cancellation: BidPureAgentCancellationFence | None,
        checkpoints: list[BidPureAgentCheckpoint],
        effect_by_id: dict[str, BidPureAgentEffectFence],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        expected_before = 1
        expected_status = AgentTaskStatus.RUNNING.value
        for event in events:
            if (
                int(event.state_version_before) != expected_before
                or int(event.state_version_after) != expected_before + 1
            ):
                warnings.append("state_transition_version_gap")
                break
            if event.status_before != expected_status:
                warnings.append("state_transition_status_gap")
                break
            expected_before = int(event.state_version_after)
            expected_status = event.status_after
        if expected_before != int(task.state_version):
            warnings.append("task_state_version_differs_from_ledger")
        if events and expected_status != task.status:
            warnings.append("task_status_differs_from_ledger")
        if (task.cancellation_fence_id is None) != (cancellation is None):
            warnings.append("cancellation_fence_projection_mismatch")
        open_checkpoint_ids = {row.id for row in checkpoints if row.status == "open"}
        if task.active_checkpoint_id and task.active_checkpoint_id not in open_checkpoint_ids:
            warnings.append("active_checkpoint_not_open")
        if any(row.effect_fence_id not in effect_by_id for row in checkpoints):
            warnings.append("checkpoint_effect_fence_missing")
        return tuple(dict.fromkeys(warnings))

    def _task_count(self, model: Any, task_id: str) -> int:
        return int(
            self.db.query(func.count(model.id)).filter(model.task_id == task_id).scalar()
            or 0
        )

    def _counts(self, model: Any, task_ids: list[str]) -> dict[str, int]:
        if not task_ids:
            return {}
        rows = (
            self.db.query(model.task_id, func.count(model.id))
            .filter(model.task_id.in_(task_ids))
            .group_by(model.task_id)
            .all()
        )
        return {str(task_id): int(count) for task_id, count in rows}
