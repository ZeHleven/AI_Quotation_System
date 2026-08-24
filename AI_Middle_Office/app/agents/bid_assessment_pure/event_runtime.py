"""Deterministic safe projection of the Pure Agent state-transition ledger."""

from __future__ import annotations

import json
from typing import Any

from .action_runtime import AgentActionKind
from .event_contracts import (
    SafeAgentEvent,
    SafeAgentEventPage,
    SafeAgentEventType,
    SafeAnswerPayload,
    SafeInputRequestPayload,
    SafeInputValidationPayload,
    SafePlanProjectionPayload,
    SafePlanStepProjection,
    SafeProgressPayload,
    SafeProgressPhase,
    SafeTaskStartedPayload,
    SafeTerminalPayload,
)
from .planning import PlanRevision
from .repository import (
    PureAgentConflict,
    PureAgentNotFound,
    PureAgentRepository,
)
from .response_contracts import PublishedAnswerMessage
from .slots import SlotValidationIssue
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
)


class SafeEventCursorRejected(PureAgentConflict):
    """The public task-version cursor is malformed or ahead of current state."""


class SafeEventProjector:
    """Project allowlisted public fields; never serialize internal payloads."""

    def __init__(self, repository: PureAgentRepository) -> None:
        self.repository = repository

    def page(
        self,
        *,
        task_record: Any,
        after_version: int,
        limit: int,
    ) -> SafeAgentEventPage:
        current = self.repository.load_task_state(task_record.id, lock=False)
        cursor = max(0, int(after_version))
        if cursor > current.state_version:
            raise SafeEventCursorRejected("event cursor is ahead of the task state")
        page_limit = max(1, min(int(limit), 100))
        events: list[SafeAgentEvent] = []
        if cursor < 1:
            events.append(self._started_event(task_record))
        remaining = page_limit - len(events)
        row_limit = remaining + 1 if remaining > 0 else 0
        rows = (
            self.repository.list_task_events_after(
                task_id=task_record.id,
                after_state_version=max(1, cursor),
                limit=row_limit,
            )
            if row_limit
            else ()
        )
        projected_rows = rows[:remaining]
        expected_version = max(1, cursor) + 1
        for row in projected_rows:
            if int(row.state_version_after) != expected_version:
                raise PureAgentConflict("task event ledger has a state-version gap")
            events.append(
                self._transition_event(
                    conversation_ref=task_record.conversation_id,
                    row=row,
                )
            )
            expected_version += 1
        next_after = events[-1].state_version if events else cursor
        if (
            remaining > 0
            and next_after < current.state_version
            and len(rows) <= remaining
        ):
            raise PureAgentConflict("task event ledger is incomplete")
        has_more = next_after < current.state_version
        return SafeAgentEventPage(
            task_ref=task_record.id,
            events=tuple(events),
            after_version=cursor,
            next_after_version=next_after,
            current_state_version=current.state_version,
            current_status=current.status,
            has_more=has_more,
        )

    def _started_event(self, task_record: Any) -> SafeAgentEvent:
        return SafeAgentEvent(
            event_id=event_id_for(task_record.id, 1),
            event_type=SafeAgentEventType.TASK_STARTED,
            conversation_ref=task_record.conversation_id,
            task_ref=task_record.id,
            state_version=1,
            status=AgentTaskStatus.RUNNING,
            terminal=False,
            occurred_at=task_record.created_at,
            payload=SafeTaskStartedPayload(message="已开始处理当前任务。"),
        )

    def _transition_event(self, *, conversation_ref: str, row: Any) -> SafeAgentEvent:
        state = AgentTaskState.model_validate(row.state_after_json)
        internal = TaskTransitionEvent.model_validate(row.payload_json)
        if (
            state.task_id != row.task_id
            or internal.task_id != row.task_id
            or internal.event_type.value != row.event_type
            or internal.expected_state_version != int(row.state_version_before)
            or state.state_version != int(row.state_version_after)
            or state.status.value != row.status_after
        ):
            raise PureAgentConflict("task event ledger binding is invalid")
        event_type, payload = self._safe_projection(
            state=state,
            internal=internal,
            row=row,
        )
        return SafeAgentEvent(
            event_id=event_id_for(row.task_id, state.state_version),
            event_type=event_type,
            conversation_ref=conversation_ref,
            task_ref=row.task_id,
            state_version=state.state_version,
            status=state.status,
            terminal=state.status in {
                AgentTaskStatus.COMPLETED,
                AgentTaskStatus.FAILED,
                AgentTaskStatus.CANCELLED,
            },
            occurred_at=row.occurred_at,
            payload=payload,
        )

    def _safe_projection(
        self,
        *,
        state: AgentTaskState,
        internal: TaskTransitionEvent,
        row: Any,
    ) -> tuple[SafeAgentEventType, Any]:
        event_type = internal.event_type
        if event_type is TaskEventType.ACTION_ACCEPTED:
            action = self.repository.load_task_action(
                task_id=state.task_id,
                action_id=internal.action_ref or "missing",
            )
            return self._action_progress(action.action_type, completed=False)
        if event_type is TaskEventType.OBSERVATION_ACCEPTED:
            action = self.repository.load_task_action(
                task_id=state.task_id,
                action_id=internal.action_ref or "missing",
            )
            if action.action_type in {
                AgentActionKind.PLAN.value,
                AgentActionKind.REPLAN.value,
            } and action.result_ref:
                try:
                    plan = self.repository.load_task_plan(
                        task_id=state.task_id,
                        plan_id=action.result_ref,
                    )
                except PureAgentNotFound:
                    pass
                else:
                    return SafeAgentEventType.PLAN_UPDATED, self._plan_payload(plan)
            return self._action_progress(action.action_type, completed=True)
        if event_type is TaskEventType.EXECUTION_MODE_CHANGED:
            if state.plan_ref is None:
                raise PureAgentConflict("planned state has no Plan reference")
            plan = self.repository.load_task_plan(
                task_id=state.task_id,
                plan_id=state.plan_ref,
            )
            return SafeAgentEventType.PLAN_UPDATED, self._plan_payload(plan)
        if event_type is TaskEventType.INFORMATION_REQUIRED:
            if state.pending_context is None:
                raise PureAgentConflict("pending event has no Slot context")
            slot = self.repository.load_task_slot(
                task_id=state.task_id,
                slot_id=state.pending_context.slot_ref,
            )
            return (
                SafeAgentEventType.INPUT_REQUIRED,
                SafeInputRequestPayload(
                    slot_ref=slot.slot_id,
                    phase=state.pending_context.phase,
                    request_message=slot.request_message,
                ),
            )
        if event_type in {
            TaskEventType.SLOT_VALIDATION_STARTED,
            TaskEventType.SLOT_FORMAT_ACCEPTED,
        }:
            if state.pending_context is None:
                raise PureAgentConflict("Slot validation has no pending context")
            return (
                SafeAgentEventType.INPUT_VALIDATING,
                SafeInputValidationPayload(
                    slot_ref=state.pending_context.slot_ref,
                    result="validating",
                    message="正在校验补充信息。",
                    issues=(),
                ),
            )
        if event_type is TaskEventType.SLOT_VALIDATION_REJECTED:
            if state.pending_context is None:
                raise PureAgentConflict("rejected Slot has no pending context")
            issues = tuple(
                SlotValidationIssue.model_validate(issue)
                for issue in self.repository.load_validation_issues(
                    task_id=state.task_id,
                    validation_ref=state.pending_context.last_error_ref,
                )
            )
            return (
                SafeAgentEventType.INPUT_REJECTED,
                SafeInputValidationPayload(
                    slot_ref=state.pending_context.slot_ref,
                    result="rejected",
                    message="补充信息未通过校验，请按提示重新输入。",
                    issues=issues,
                ),
            )
        if event_type is TaskEventType.SLOT_RESOLVED:
            if internal.resume_proof is None:
                raise PureAgentConflict("resolved Slot has no Resume proof")
            return (
                SafeAgentEventType.INPUT_ACCEPTED,
                SafeInputValidationPayload(
                    slot_ref=internal.resume_proof.slot_ref,
                    result="accepted",
                    message="补充信息已接收，正在从原位置继续处理。",
                    issues=(),
                ),
            )
        if event_type is TaskEventType.COMPLETION_ACCEPTED:
            message = self.repository.load_task_answer_message(task_id=state.task_id)
            return (
                SafeAgentEventType.ANSWER_COMPLETED,
                SafeAnswerPayload(
                    message=PublishedAnswerMessage.model_validate(message.content_json)
                ),
            )
        if event_type is TaskEventType.FATAL_ERROR:
            return (
                SafeAgentEventType.TASK_FAILED,
                SafeTerminalPayload(
                    outcome="failed",
                    message="当前任务未能安全完成。",
                    guidance="可以刷新状态后重试，或提交新的问题。",
                ),
            )
        if event_type is TaskEventType.CANCEL_REQUESTED:
            return (
                SafeAgentEventType.TASK_CANCELLED,
                SafeTerminalPayload(
                    outcome="cancelled",
                    message="当前任务已取消。",
                    guidance=None,
                ),
            )
        raise PureAgentConflict("task event type has no safe public projection")

    def _plan_payload(self, row: Any) -> SafePlanProjectionPayload:
        revision = PlanRevision.model_validate(row.body_json)
        if (
            revision.task_id != row.task_id
            or revision.plan_id != row.id
            or revision.plan_version != int(row.plan_version)
        ):
            raise PureAgentConflict("Plan projection binding is invalid")
        visible_ids = set(revision.plan.user_projection.visible_step_ids)
        steps = tuple(
            SafePlanStepProjection(step_id=step.id, title=step.title)
            for step in revision.plan.steps
            if step.id in visible_ids
        )
        return SafePlanProjectionPayload(
            plan_version=revision.plan_version,
            summary=revision.plan.user_projection.summary,
            steps=steps,
            revised=revision.plan_version > 1 or revision.supersedes_ref is not None,
        )

    @staticmethod
    def _action_progress(
        action_type: str,
        *,
        completed: bool,
    ) -> tuple[SafeAgentEventType, SafeProgressPayload]:
        phase_messages = {
            AgentActionKind.MAIN_AGENT_DECISION.value: (
                SafeProgressPhase.UNDERSTANDING,
                "已完成当前信息判断。" if completed else "正在分析当前问题和所需信息。",
            ),
            AgentActionKind.PLAN.value: (
                SafeProgressPhase.PLANNING,
                "已形成当前处理计划。" if completed else "正在整理当前处理计划。",
            ),
            AgentActionKind.REPLAN.value: (
                SafeProgressPhase.PLANNING,
                "已更新当前处理计划。" if completed else "正在调整当前处理计划。",
            ),
            AgentActionKind.TOOL_CALL_BATCH.value: (
                SafeProgressPhase.RETRIEVING,
                "已完成一次资料核对。" if completed else "正在查找并核对相关资料。",
            ),
            AgentActionKind.REQUEST_INFORMATION.value: (
                SafeProgressPhase.PREPARING_INPUT_REQUEST,
                "已整理需要补充的信息。" if completed else "正在确认需要补充的信息。",
            ),
            AgentActionKind.ANSWER.value: (
                SafeProgressPhase.PREPARING_ANSWER,
                "已完成回答草稿处理。" if completed else "正在生成并校验回答。",
            ),
        }
        phase, message = phase_messages.get(
            action_type,
            (
                SafeProgressPhase.CONTINUING,
                "已完成当前处理步骤。" if completed else "正在继续处理当前任务。",
            ),
        )
        public_type = (
            SafeAgentEventType.ANSWER_PREPARING
            if action_type == AgentActionKind.ANSWER.value
            else SafeAgentEventType.PROGRESS_UPDATED
        )
        return public_type, SafeProgressPayload(phase=phase, message=message)


def event_id_for(task_ref: str, state_version: int) -> str:
    if int(state_version) < 1:
        raise SafeEventCursorRejected("event state version must be positive")
    return f"paevt:{task_ref}:{int(state_version)}"


def resolve_after_version(*, task_ref: str, last_event_id: str | None) -> int:
    if last_event_id is None or not last_event_id.strip():
        return 0
    prefix = f"paevt:{task_ref}:"
    candidate = last_event_id.strip()
    if not candidate.startswith(prefix):
        raise SafeEventCursorRejected("Last-Event-ID belongs to another task")
    suffix = candidate[len(prefix) :]
    if not suffix.isascii() or not suffix.isdigit() or int(suffix) < 1:
        raise SafeEventCursorRejected("Last-Event-ID is invalid")
    return int(suffix)


def format_safe_event_sse(event: SafeAgentEvent, *, retry_ms: int = 3000) -> str:
    data = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"id: {event.event_id}\n"
        f"event: {event.event_type.value}\n"
        f"retry: {max(1000, min(int(retry_ms), 30000))}\n"
        f"data: {data}\n\n"
    )
