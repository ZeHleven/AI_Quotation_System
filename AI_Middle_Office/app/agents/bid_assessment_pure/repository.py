"""Transactional repository for isolated Pure Agent development persistence.

Methods flush but never commit. Callers own the surrounding transaction so a
state update, event, slot/checkpoint, fence, and ledger entry can be committed
atomically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable
import uuid

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from .action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionReservationIntent,
    AgentActionKind,
    ToolCallBatchAction,
)
from .persistence_models import (
    BidPureAgentAction,
    BidPureAgentBudgetAccount,
    BidPureAgentBudgetEntry,
    BidPureAgentCall,
    BidPureAgentCancellationFence,
    BidPureAgentCheckpoint,
    BidPureAgentConversation,
    BidPureAgentContextSnapshot,
    BidPureAgentEffectFence,
    BidPureAgentEvent,
    BidPureAgentMessage,
    BidPureAgentObservationArtifact,
    BidPureAgentPlan,
    BidPureAgentResponse,
    BidPureAgentSlot,
    BidPureAgentSlotValidation,
    BidPureAgentTask,
)
from .planning import PlanRevision
from .response_contracts import (
    CommittedResponseArtifact,
    ResponseCommitDecision,
    ResponseLifecycleStatus,
    ResponsePersistenceEnvelope,
    ResponseStaleIntent,
    ResponseVersionHead,
)
from .response_runtime import ResponseVersionController, ResponseVersionRejected
from .runtime import ContextSnapshot
from .runtime_guards import (
    ActionAdmissionDecision,
    ActionRuntimeBinding,
    AdmissionDisposition,
)
from .slots import (
    CheckpointStatus,
    ContinuationCheckpoint,
    PendingContext,
    PendingPhase,
    ResumeProof,
    Slot,
    SlotStatus,
)
from .state import (
    AgentTaskState,
    AgentTaskStatus,
    TaskEventType,
    TaskTransitionEvent,
    TERMINAL_STATUSES,
)
from .state_machine import create_running_task, decide_transition


class PureAgentPersistenceError(RuntimeError):
    code = "PURE_AGENT_PERSISTENCE_ERROR"


class PureAgentNotFound(PureAgentPersistenceError):
    code = "PURE_AGENT_NOT_FOUND"


class PureAgentConflict(PureAgentPersistenceError):
    code = "PURE_AGENT_CONFLICT"


class PureAgentBudgetExceeded(PureAgentPersistenceError):
    code = "PURE_AGENT_BUDGET_EXCEEDED"


class PureAgentFenceRejected(PureAgentPersistenceError):
    code = "PURE_AGENT_FENCE_REJECTED"


@dataclass(frozen=True)
class TransitionCommit:
    state: AgentTaskState
    event_id: str
    replayed: bool


@dataclass(frozen=True)
class ActionReservation:
    action_id: str
    effect_fence_id: str
    fencing_token: int
    state: AgentTaskState
    replayed: bool


@dataclass(frozen=True)
class EffectSettlement:
    effect_fence_id: str
    status: str
    accepted_for_context: bool


@dataclass(frozen=True)
class ActionEffectSnapshot:
    effect_fence_id: str
    task_id: str
    action_id: str
    effect_key: str
    request_hash: str
    status: str
    fencing_token: int
    replay_policy: str
    result_ref: str | None
    result_hash: str | None
    error_code: str | None


@dataclass(frozen=True)
class PendingSuspension:
    slot: Slot
    checkpoint: ContinuationCheckpoint
    state: AgentTaskState


@dataclass(frozen=True)
class ValidationAttempt:
    attempt_id: str
    stage: str
    state: AgentTaskState
    replayed: bool


@dataclass(frozen=True)
class RecoveryClaim:
    checkpoint: ContinuationCheckpoint
    lease_owner: str
    lease_until: datetime
    fencing_token: int


@dataclass(frozen=True)
class RecoveryAssessment:
    checkpoint: ContinuationCheckpoint
    effect_status: str
    replay_policy: str
    decision: str
    reason: str


@dataclass(frozen=True)
class BudgetMutation:
    entry_id: str
    resource_type: str
    reserved_after: int
    actual_after: int
    replayed: bool


@dataclass(frozen=True)
class ActionBudgetReservationSnapshot:
    entry_id: str
    resource_type: str
    amount: int


@dataclass(frozen=True)
class GovernedActionReservation:
    action: ActionReservation
    budget_entries: tuple[BudgetMutation, ...]


@dataclass(frozen=True)
class AnswerResponseCommit:
    artifact: CommittedResponseArtifact
    message_id: str
    state: AgentTaskState
    current_status: ResponseLifecycleStatus
    replayed: bool


@dataclass(frozen=True)
class ResponseLifecycleMutation:
    head: ResponseVersionHead
    event_ref: str
    replayed: bool


@dataclass(frozen=True)
class UserMessageAdmission:
    """Deterministic intake receipt; intent remains owned by the Main Agent."""

    message: BidPureAgentMessage
    task: AgentTaskState
    disposition: str
    replayed: bool


@dataclass(frozen=True)
class LocalTaskScopeSnapshot:
    """Read-only persistence projection used by local Runtime adapters."""

    task_id: str
    conversation_id: str
    task_state_version: int
    task_row_version: int
    conversation_row_version: int
    conversation_status: str
    owner_id: int
    tenant_ref: str
    assessment_id: str | None
    trigger_message_id: str
    goal_ref: str
    plan_ref: str | None
    cancellation_fence_ref: str | None
    latest_checkpoint_ref: str | None


@dataclass(frozen=True)
class ContextMessageRow:
    """Hash-verified message projection safe for Context candidate assembly."""

    message_ref: str
    conversation_ref: str
    sequence_no: int
    role: str
    message_type: str
    content: Any
    content_hash: str
    reply_to_message_ref: str | None


@dataclass(frozen=True)
class ContextCommittedResponseRow:
    """Verified committed response lineage for one prior assistant message."""

    message_ref: str
    response_task_ref: str
    envelope: ResponsePersistenceEnvelope


@dataclass(frozen=True)
class PersistedObservationArtifactRow:
    """Hash-verified Observation and result body accepted by one Task."""

    observation: ActionObservation
    artifact: Any
    context_snapshot_ref: str


@dataclass(frozen=True)
class PersistedToolProtocolPairRow:
    """One complete, accepted Function Calling request/result pair."""

    ledger_call_id: str
    call_ref: str
    provider_tool_call_id: str
    model_turn_ref: str
    sequence_no: int
    tool_name: str
    arguments: dict[str, Any]
    input_hash: str
    output_ref: str
    output: dict[str, Any]
    output_hash: str
    registry_snapshot_ref: str
    registry_snapshot_hash: str


@dataclass(frozen=True)
class RuntimeBudgetBalanceRow:
    """Storage-neutral Budget balance projection for one Runtime Task."""

    resource_type: str
    unit: str
    limit_amount: int
    reserved_amount: int
    spent_amount: int
    row_version: int


@dataclass(frozen=True)
class RuntimeEffectFenceRow:
    """Complete Effect Fence receipt needed before Action admission."""

    effect_fence_ref: str
    task_ref: str
    action_ref: str
    effect_key: str
    request_hash: str
    replay_policy: str
    status: str
    fencing_token: int
    result_ref: str | None
    result_hash: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def normalize_json(value: Any) -> Any:
    return json.loads(canonical_json(value))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_resume_token(resume_token: str) -> str:
    token = str(resume_token)
    if len(token) < 16:
        raise PureAgentFenceRejected("resume token is too short")
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_id() -> str:
    return str(uuid.uuid4())


class PureAgentRepository:
    """Repository with CAS and ledger guards; it never opens its own session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_conversation(
        self,
        *,
        owner_id: int,
        tenant_ref: str,
        assessment_id: str | None = None,
        title: str | None = None,
        conversation_id: str | None = None,
        now: datetime | None = None,
    ) -> BidPureAgentConversation:
        if conversation_id is not None:
            existing = (
                self.db.query(BidPureAgentConversation)
                .filter(BidPureAgentConversation.id == conversation_id)
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                if (
                    int(existing.owner_id) != int(owner_id)
                    or existing.tenant_ref != str(tenant_ref)
                    or existing.assessment_id != assessment_id
                    or existing.title != title
                ):
                    raise PureAgentConflict(
                        "conversation idempotency reference was reused"
                    )
                return existing
        current_time = now or utc_now()
        row = BidPureAgentConversation(
            id=conversation_id or _new_id(),
            owner_id=int(owner_id),
            tenant_ref=str(tenant_ref),
            assessment_id=assessment_id,
            title=title,
            status="active",
            next_message_sequence=1,
            row_version=1,
            created_at=current_time,
            updated_at=current_time,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def load_owned_conversation(
        self,
        conversation_id: str,
        *,
        owner_id: int,
        lock: bool = False,
    ) -> BidPureAgentConversation:
        row = self._conversation(conversation_id, lock=lock)
        if int(row.owner_id) != int(owner_id):
            raise PureAgentNotFound("conversation was not found")
        return row

    def list_conversation_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[BidPureAgentMessage, ...]:
        rows = (
            self.db.query(BidPureAgentMessage)
            .filter(
                BidPureAgentMessage.conversation_id == conversation_id,
                BidPureAgentMessage.sequence_no > max(0, int(after_sequence)),
                BidPureAgentMessage.role.in_(("user", "assistant")),
            )
            .order_by(BidPureAgentMessage.sequence_no.asc())
            .limit(max(1, min(int(limit), 101)))
            .all()
        )
        return tuple(rows)

    def load_latest_task_user_turn(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_types: tuple[str, ...],
    ) -> BidPureAgentMessage:
        """Resolve the latest persisted user input explicitly bound to one Task.

        This is a persistence lookup only. It does not infer semantic intent or
        decide whether a message should alter the active Agent objective.
        """

        allowed = {
            "user.task_trigger",
            "user.steering_candidate",
            "user.slot_candidate",
        }
        requested = tuple(dict.fromkeys(str(item) for item in message_types))
        if not requested or not set(requested).issubset(allowed):
            raise PureAgentConflict("Main Agent turn message types are invalid")
        task = self._task(task_id, lock=False)
        if task.conversation_id != conversation_id:
            raise PureAgentNotFound("task was not found")

        dynamic_types = tuple(
            item for item in requested if item != "user.task_trigger"
        )
        if dynamic_types:
            rows = (
                self.db.query(BidPureAgentMessage)
                .filter(
                    BidPureAgentMessage.conversation_id == conversation_id,
                    BidPureAgentMessage.role == "user",
                    BidPureAgentMessage.message_type.in_(dynamic_types),
                )
                .order_by(BidPureAgentMessage.sequence_no.desc())
                .limit(1001)
                .all()
            )
            for row in rows[:1000]:
                content = normalize_json(row.content_json)
                if self._user_turn_targets_task(
                    row,
                    content=content,
                    task_id=task_id,
                ):
                    return row
            if len(rows) > 1000:
                raise PureAgentConflict("Main Agent turn lookup exceeded its safe bound")

        if "user.task_trigger" in requested:
            trigger = (
                self.db.query(BidPureAgentMessage)
                .filter(BidPureAgentMessage.id == task.trigger_message_id)
                .one_or_none()
            )
            if trigger is not None and self._user_turn_targets_task(
                trigger,
                content=normalize_json(trigger.content_json),
                task_id=task_id,
                trigger_message_id=task.trigger_message_id,
            ):
                return trigger
        raise PureAgentNotFound("Main Agent user turn was not found")

    def load_task_context_message(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ContextMessageRow:
        """Load the exact persisted user Turn after rechecking its Task fence."""

        task = self._task(task_id, lock=False)
        if task.conversation_id != conversation_id:
            raise PureAgentNotFound("task was not found")
        row = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == message_id)
            .one_or_none()
        )
        if row is None or row.conversation_id != conversation_id:
            raise PureAgentNotFound("Context message was not found")
        content = normalize_json(row.content_json)
        if not self._user_turn_targets_task(
            row,
            content=content,
            task_id=task_id,
            trigger_message_id=task.trigger_message_id,
        ):
            raise PureAgentFenceRejected(
                "Context message is not an authenticated Turn for this Task"
            )
        return self._context_message_row(row, content=content)

    def list_task_context_messages_before(
        self,
        *,
        task_id: str,
        conversation_id: str,
        before_sequence: int,
        limit: int = 20,
    ) -> tuple[ContextMessageRow, ...]:
        """Return bounded prior interaction rows without semantic selection."""

        task = self._task(task_id, lock=False)
        if task.conversation_id != conversation_id:
            raise PureAgentNotFound("task was not found")
        bounded_limit = max(0, min(int(limit), 50))
        if bounded_limit == 0:
            return ()
        rows = (
            self.db.query(BidPureAgentMessage)
            .filter(
                BidPureAgentMessage.conversation_id == conversation_id,
                BidPureAgentMessage.sequence_no < max(1, int(before_sequence)),
                BidPureAgentMessage.role.in_(("user", "assistant")),
            )
            .order_by(BidPureAgentMessage.sequence_no.desc())
            .limit(bounded_limit)
            .all()
        )
        projected: list[ContextMessageRow] = []
        for row in reversed(rows):
            content = normalize_json(row.content_json)
            if row.content_hash != canonical_hash(content):
                raise PureAgentFenceRejected(
                    "persisted Context interaction content drifted"
                )
            projected.append(self._context_message_row(row, content=content))
        return tuple(projected)

    def load_context_committed_response(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ContextCommittedResponseRow:
        """Load prior committed Answer lineage behind one Context message.

        The current Task trigger is the temporal fence. Stale, superseded,
        cross-conversation, future, or content-drifted responses are rejected.
        """

        task = self._task(task_id, lock=False)
        if task.conversation_id != conversation_id:
            raise PureAgentNotFound("task was not found")
        trigger = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == task.trigger_message_id)
            .one_or_none()
        )
        message = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == message_id)
            .one_or_none()
        )
        if (
            trigger is None
            or message is None
            or trigger.conversation_id != conversation_id
            or message.conversation_id != conversation_id
            or int(message.sequence_no) >= int(trigger.sequence_no)
            or message.role != "assistant"
            or message.message_type != "answer.committed"
        ):
            raise PureAgentNotFound("prior committed Answer message was not found")
        response = (
            self.db.query(BidPureAgentResponse)
            .filter(
                BidPureAgentResponse.rendered_message_id == message_id,
                BidPureAgentResponse.conversation_id == conversation_id,
                BidPureAgentResponse.status == ResponseLifecycleStatus.COMMITTED.value,
            )
            .one_or_none()
        )
        if response is None:
            raise PureAgentNotFound("prior committed Answer response was not found")
        envelope = self._response_envelope(response)
        content = normalize_json(message.content_json)
        if (
            envelope.current_status is not ResponseLifecycleStatus.COMMITTED
            or envelope.artifact.task_ref != response.task_id
            or envelope.artifact.conversation_ref != conversation_id
            or envelope.artifact.message.model_dump(mode="json") != content
            or not self._digest_matches(
                message.content_hash,
                canonical_hash(content),
            )
        ):
            raise PureAgentFenceRejected(
                "prior committed Answer lineage failed its persistence fence"
            )
        return ContextCommittedResponseRow(
            message_ref=message_id,
            response_task_ref=response.task_id,
            envelope=envelope,
        )

    def load_active_task_state(
        self,
        conversation_id: str,
        *,
        lock: bool = False,
    ) -> AgentTaskState | None:
        query = self.db.query(BidPureAgentTask).filter(
            BidPureAgentTask.conversation_id == conversation_id,
            BidPureAgentTask.status.in_(
                (AgentTaskStatus.RUNNING.value, AgentTaskStatus.PENDING.value)
            ),
        )
        rows = (query.with_for_update() if lock else query).limit(2).all()
        if len(rows) > 1:
            raise PureAgentConflict("conversation has multiple active tasks")
        return self._task_state(rows[0]) if rows else None

    def load_latest_task_state(self, conversation_id: str) -> AgentTaskState | None:
        row = (
            self.db.query(BidPureAgentTask)
            .filter(BidPureAgentTask.conversation_id == conversation_id)
            .order_by(BidPureAgentTask.created_at.desc(), BidPureAgentTask.id.desc())
            .first()
        )
        return self._task_state(row) if row is not None else None

    def load_owned_task_state(
        self,
        task_id: str,
        *,
        conversation_id: str,
        owner_id: int,
        lock: bool = False,
    ) -> AgentTaskState:
        row = self._task(task_id, lock=lock)
        if (
            row.conversation_id != conversation_id
            or int(row.owner_id) != int(owner_id)
        ):
            raise PureAgentNotFound("task was not found")
        return self._task_state(row)

    def load_owned_task_record(
        self,
        task_id: str,
        *,
        conversation_id: str,
        owner_id: int,
    ) -> BidPureAgentTask:
        row = self._task(task_id, lock=False)
        if (
            row.conversation_id != conversation_id
            or int(row.owner_id) != int(owner_id)
        ):
            raise PureAgentNotFound("task was not found")
        return row

    def load_local_task_scope(
        self,
        *,
        task_id: str,
        conversation_id: str,
    ) -> LocalTaskScopeSnapshot:
        """Freeze the persisted local authorization and continuation scope.

        This is intentionally read-only.  It does not grant authorization, infer
        intent, create a Budget account, or repair incomplete Runtime state.
        """

        task = self._task(task_id, lock=False)
        conversation = self._conversation(conversation_id, lock=False)
        if (
            task.conversation_id != conversation.id
            or int(task.owner_id) != int(conversation.owner_id)
        ):
            raise PureAgentFenceRejected(
                "task and conversation persistence scopes do not match"
            )
        checkpoint_ref = task.active_checkpoint_id
        if checkpoint_ref is None:
            checkpoint = (
                self.db.query(BidPureAgentCheckpoint)
                .filter(
                    BidPureAgentCheckpoint.task_id == task.id,
                    BidPureAgentCheckpoint.status == CheckpointStatus.CONSUMED.value,
                )
                .order_by(
                    BidPureAgentCheckpoint.consumed_at.desc(),
                    BidPureAgentCheckpoint.created_at.desc(),
                    BidPureAgentCheckpoint.id.desc(),
                )
                .first()
            )
            checkpoint_ref = None if checkpoint is None else checkpoint.id
        return LocalTaskScopeSnapshot(
            task_id=task.id,
            conversation_id=conversation.id,
            task_state_version=int(task.state_version),
            task_row_version=int(task.row_version),
            conversation_row_version=int(conversation.row_version),
            conversation_status=conversation.status,
            owner_id=int(task.owner_id),
            tenant_ref=conversation.tenant_ref,
            assessment_id=conversation.assessment_id,
            trigger_message_id=task.trigger_message_id,
            goal_ref=task.goal_ref,
            plan_ref=task.plan_ref,
            cancellation_fence_ref=task.cancellation_fence_id,
            latest_checkpoint_ref=checkpoint_ref,
        )

    def load_context_snapshot(
        self,
        *,
        task_id: str,
        snapshot_ref: str,
    ) -> ContextSnapshot:
        """Load and revalidate one immutable Context Snapshot receipt."""

        row = (
            self.db.query(BidPureAgentContextSnapshot)
            .filter(BidPureAgentContextSnapshot.id == snapshot_ref)
            .one_or_none()
        )
        if row is None or row.task_id != task_id:
            raise PureAgentNotFound("Context Snapshot was not found")
        try:
            snapshot = ContextSnapshot.model_validate(row.snapshot_json)
        except (TypeError, ValueError) as exc:
            raise PureAgentConflict("persisted Context Snapshot is invalid") from exc
        if (
            snapshot.snapshot_ref != row.id
            or snapshot.task_ref != row.task_id
            or snapshot.state_version != int(row.state_version)
            or not self._digest_matches(row.snapshot_hash, snapshot.snapshot_hash)
        ):
            raise PureAgentFenceRejected("persisted Context Snapshot receipt drifted")
        return snapshot

    def load_context_observation_artifact(
        self,
        *,
        task_id: str,
        observation_ref: str,
    ) -> PersistedObservationArtifactRow:
        """Load one accepted Observation body and revalidate both hashes."""

        task = self._task(task_id, lock=False)
        if observation_ref not in tuple(task.observation_refs_json or ()):
            raise PureAgentNotFound("Observation Artifact was not found")
        row = (
            self.db.query(BidPureAgentObservationArtifact)
            .filter(BidPureAgentObservationArtifact.id == observation_ref)
            .one_or_none()
        )
        if row is None or row.task_id != task_id:
            raise PureAgentNotFound("Observation Artifact was not found")
        return self._observation_artifact_row(row)

    def load_running_action_observation_artifact(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> PersistedObservationArtifactRow | None:
        """Load an unaccepted result body for the current Running Action only."""

        task = self._task(task_id, lock=False)
        action = self._action(action_id, lock=False)
        if (
            task.status != AgentTaskStatus.RUNNING.value
            or task.in_flight_action_id != action_id
            or action.task_id != task_id
        ):
            raise PureAgentFenceRejected(
                "Running Action result is outside the active Task fence"
            )
        row = (
            self.db.query(BidPureAgentObservationArtifact)
            .filter(BidPureAgentObservationArtifact.action_id == action_id)
            .one_or_none()
        )
        if row is None:
            return None
        artifact = self._observation_artifact_row(row)
        effect = (
            self.db.query(BidPureAgentEffectFence)
            .filter(
                BidPureAgentEffectFence.task_id == task_id,
                BidPureAgentEffectFence.action_id == action_id,
            )
            .one_or_none()
        )
        if (
            effect is None
            or row.task_id != task_id
            or artifact.observation.observation_ref
            in tuple(task.observation_refs_json or ())
            or artifact.observation.state_version != int(task.state_version)
            or artifact.observation.action_sequence != int(action.sequence_no)
            or action.status not in {"succeeded", "failed"}
            or effect.status != action.status
            or action.result_ref != artifact.observation.artifact_ref
            or effect.result_ref != artifact.observation.artifact_ref
            or not self._digest_matches(
                action.result_hash or "",
                artifact.observation.artifact_hash,
            )
            or not self._digest_matches(
                effect.result_hash or "",
                artifact.observation.artifact_hash,
            )
        ):
            raise PureAgentFenceRejected(
                "persisted Running Action result receipt drifted"
            )
        return artifact

    def list_context_tool_protocol_pairs(
        self,
        *,
        task_id: str,
        observation_ref: str,
        registry_snapshot_ref: str,
        registry_snapshot_hash: str,
        visible_tools_hash: str,
        visible_tool_names: tuple[str, ...],
    ) -> tuple[PersistedToolProtocolPairRow, ...]:
        """Return only a complete latest Tool batch; partial protocol is omitted."""

        artifact = self.load_context_observation_artifact(
            task_id=task_id,
            observation_ref=observation_ref,
        )
        observation = artifact.observation
        if observation.kind is not ActionObservationKind.TOOL_RESULT:
            return ()
        action = self.load_task_action(
            task_id=task_id,
            action_id=observation.source_action_ref,
        )
        proposals = self._tool_call_proposals(action)
        if not proposals:
            return ()
        rows = (
            self.db.query(BidPureAgentCall)
            .filter(
                BidPureAgentCall.task_id == task_id,
                BidPureAgentCall.action_id == action.id,
                BidPureAgentCall.call_kind == "tool",
            )
            .order_by(BidPureAgentCall.sequence_no.asc())
            .all()
        )
        if len(rows) != len(proposals):
            return ()
        result_calls = self._tool_result_calls(artifact.artifact)
        if len(result_calls) != len(proposals):
            return ()

        visible = set(visible_tool_names)
        pairs: list[PersistedToolProtocolPairRow] = []
        for proposal, row, result_call in zip(proposals, rows, result_calls):
            arguments = normalize_json(row.input_json)
            output = normalize_json(row.output_json)
            if not isinstance(arguments, dict) or not isinstance(output, dict):
                return ()
            arguments_hash = canonical_hash(arguments)
            output_hash = canonical_hash(output)
            expected_sequence = int(proposal.get("sequence", 0))
            expected_name = proposal.get("tool_name")
            if (
                expected_sequence != int(row.sequence_no)
                or expected_sequence < 1
                or proposal.get("provider_tool_call_id")
                != row.provider_tool_call_id
                or proposal.get("model_turn_ref") != row.model_turn_ref
                or proposal.get("task_ref") != task_id
                or proposal.get("context_snapshot_ref")
                != artifact.context_snapshot_ref
                or int(proposal.get("state_version", 0))
                >= observation.state_version
                or expected_name != row.operation_name
                or expected_name not in visible
                or normalize_json(proposal.get("arguments")) != arguments
                or proposal.get("arguments_hash")
                != f"sha256:{row.input_hash}"
                or proposal.get("registry_snapshot_ref")
                != registry_snapshot_ref
                or not self._digest_matches(
                    str(proposal.get("registry_snapshot_hash", "")),
                    registry_snapshot_hash,
                )
                or proposal.get("visible_tools_hash") != visible_tools_hash
                or proposal.get("authorization_snapshot_ref")
                != row.authorization_snapshot_ref
                or arguments_hash != row.input_hash
                or row.status not in {"succeeded", "failed"}
                or int(row.state_version) != observation.state_version
                or row.context_snapshot_id != artifact.context_snapshot_ref
                or row.output_ref is None
                or row.output_hash is None
                or output_hash != row.output_hash
                or row.registry_snapshot_ref != registry_snapshot_ref
                or not self._digest_matches(
                    row.registry_snapshot_hash,
                    registry_snapshot_hash,
                )
                or row.visible_tools_hash != visible_tools_hash
                or result_call.get("call_ref") != row.call_ref
                or result_call.get("tool_name") != row.operation_name
                or result_call.get("ledger_call_id") != row.id
                or result_call.get("accepted_for_context") is not True
                or normalize_json(result_call.get("result")) != output
                or not self._tool_message_matches(
                    result_call.get("tool_message"),
                    row=row,
                    output=output,
                )
            ):
                return ()
            pairs.append(
                PersistedToolProtocolPairRow(
                    ledger_call_id=row.id,
                    call_ref=row.call_ref,
                    provider_tool_call_id=row.provider_tool_call_id,
                    model_turn_ref=row.model_turn_ref,
                    sequence_no=int(row.sequence_no),
                    tool_name=row.operation_name,
                    arguments=arguments,
                    input_hash=f"sha256:{row.input_hash}",
                    output_ref=row.output_ref,
                    output=output,
                    output_hash=f"sha256:{row.output_hash}",
                    registry_snapshot_ref=row.registry_snapshot_ref,
                    registry_snapshot_hash=(
                        f"sha256:{self._storage_digest(row.registry_snapshot_hash)}"
                    ),
                )
            )
        if tuple(pair.sequence_no for pair in pairs) != tuple(
            range(1, len(pairs) + 1)
        ):
            return ()
        return tuple(pairs)

    def list_runtime_budget_balances(
        self,
        *,
        task_id: str,
    ) -> tuple[RuntimeBudgetBalanceRow, ...]:
        """Return the authoritative per-resource Budget ledger heads."""

        self._task(task_id, lock=False)
        rows = (
            self.db.query(BidPureAgentBudgetAccount)
            .filter(BidPureAgentBudgetAccount.task_id == task_id)
            .order_by(BidPureAgentBudgetAccount.resource_type.asc())
            .all()
        )
        return tuple(
            RuntimeBudgetBalanceRow(
                resource_type=row.resource_type,
                unit=row.unit,
                limit_amount=int(row.limit_amount),
                reserved_amount=int(row.reserved_amount),
                spent_amount=int(row.actual_amount),
                row_version=int(row.row_version),
            )
            for row in rows
        )

    def load_runtime_effect_fence_by_key(
        self,
        *,
        task_id: str,
        effect_key: str,
    ) -> RuntimeEffectFenceRow | None:
        """Load an existing Effect receipt without creating or replaying it."""

        row = (
            self.db.query(BidPureAgentEffectFence)
            .filter(
                BidPureAgentEffectFence.task_id == task_id,
                BidPureAgentEffectFence.effect_key == effect_key,
            )
            .one_or_none()
        )
        if row is None:
            return None
        return RuntimeEffectFenceRow(
            effect_fence_ref=row.id,
            task_ref=row.task_id,
            action_ref=row.action_id,
            effect_key=row.effect_key,
            request_hash=row.request_hash,
            replay_policy=row.replay_policy,
            status=row.status,
            fencing_token=int(row.fencing_token),
            result_ref=row.result_ref,
            result_hash=row.result_hash,
        )

    def list_task_events_after(
        self,
        *,
        task_id: str,
        after_state_version: int,
        limit: int,
    ) -> tuple[BidPureAgentEvent, ...]:
        rows = (
            self.db.query(BidPureAgentEvent)
            .filter(
                BidPureAgentEvent.task_id == task_id,
                BidPureAgentEvent.state_version_after
                > max(1, int(after_state_version)),
            )
            .order_by(BidPureAgentEvent.state_version_after.asc())
            .limit(max(1, min(int(limit), 101)))
            .all()
        )
        return tuple(rows)

    def load_task_action(self, *, task_id: str, action_id: str) -> BidPureAgentAction:
        row = self._action(action_id, lock=False)
        if row.task_id != task_id:
            raise PureAgentNotFound("action was not found")
        return row

    def load_action_effect_fence(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> ActionEffectSnapshot:
        row = (
            self.db.query(BidPureAgentEffectFence)
            .filter(
                BidPureAgentEffectFence.task_id == task_id,
                BidPureAgentEffectFence.action_id == action_id,
            )
            .one_or_none()
        )
        if row is None:
            raise PureAgentNotFound("action Effect Fence was not found")
        return ActionEffectSnapshot(
            effect_fence_id=row.id,
            task_id=row.task_id,
            action_id=row.action_id,
            effect_key=row.effect_key,
            request_hash=self._contract_digest(row.request_hash),
            status=row.status,
            fencing_token=int(row.fencing_token),
            replay_policy=row.replay_policy,
            result_ref=row.result_ref,
            result_hash=(
                None
                if row.result_hash is None
                else self._contract_digest(row.result_hash)
            ),
            error_code=row.error_code,
        )

    def load_action_budget_reservations(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> tuple[ActionBudgetReservationSnapshot, ...]:
        rows = (
            self.db.query(BidPureAgentBudgetEntry, BidPureAgentBudgetAccount)
            .join(
                BidPureAgentBudgetAccount,
                BidPureAgentBudgetAccount.id == BidPureAgentBudgetEntry.account_id,
            )
            .filter(
                BidPureAgentBudgetEntry.task_id == task_id,
                BidPureAgentBudgetEntry.action_id == action_id,
                BidPureAgentBudgetEntry.entry_kind == "reserve",
            )
            .order_by(BidPureAgentBudgetAccount.resource_type.asc())
            .all()
        )
        return tuple(
            ActionBudgetReservationSnapshot(
                entry_id=entry.id,
                resource_type=account.resource_type,
                amount=int(entry.amount),
            )
            for entry, account in rows
        )

    def assert_action_budget_settled(
        self,
        *,
        task_id: str,
        action_id: str,
    ) -> None:
        """Require every Action reservation to have one durable settlement."""

        reservations = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(
                BidPureAgentBudgetEntry.task_id == task_id,
                BidPureAgentBudgetEntry.action_id == action_id,
                BidPureAgentBudgetEntry.entry_kind == "reserve",
            )
            .all()
        )
        if not reservations:
            raise PureAgentFenceRejected(
                "Running Action result has no Budget reservation receipt"
            )
        reservation_ids = {row.id for row in reservations}
        settlements = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(BidPureAgentBudgetEntry.reservation_ref.in_(reservation_ids))
            .all()
        )
        settled_ids = {
            row.reservation_ref
            for row in settlements
            if row.task_id == task_id
            and row.action_id == action_id
            and row.entry_kind in {"settle", "release"}
        }
        if settled_ids != reservation_ids or len(settlements) != len(reservations):
            raise PureAgentFenceRejected(
                "Running Action result Budget settlement is incomplete"
            )

    def load_task_plan(self, *, task_id: str, plan_id: str) -> BidPureAgentPlan:
        row = (
            self.db.query(BidPureAgentPlan)
            .filter(BidPureAgentPlan.id == plan_id)
            .one_or_none()
        )
        if row is None or row.task_id != task_id:
            raise PureAgentNotFound("plan was not found")
        return row

    def load_task_answer_message(self, *, task_id: str) -> BidPureAgentMessage:
        response = (
            self.db.query(BidPureAgentResponse)
            .filter(
                BidPureAgentResponse.task_id == task_id,
                BidPureAgentResponse.rendered_message_id.isnot(None),
                BidPureAgentResponse.status.in_(("committed", "superseded", "stale")),
            )
            .order_by(BidPureAgentResponse.created_at.desc())
            .first()
        )
        if response is None:
            raise PureAgentNotFound("committed answer was not found")
        message = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == response.rendered_message_id)
            .one_or_none()
        )
        if (
            message is None
            or message.conversation_id != response.conversation_id
            or message.role != "assistant"
            or message.message_type != "answer.committed"
        ):
            raise PureAgentConflict("committed answer message is invalid")
        return message

    def load_task_slot(self, *, task_id: str, slot_id: str) -> Slot:
        row = self._slot(slot_id, lock=False)
        if row.task_id != task_id:
            raise PureAgentNotFound("slot was not found")
        return self._slot_contract(row)

    def load_task_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
    ) -> ContinuationCheckpoint:
        row = self._checkpoint(checkpoint_id, lock=False)
        if row.task_id != task_id:
            raise PureAgentNotFound("checkpoint was not found")
        return self._checkpoint_contract(row)

    def load_task_checkpoint_for_slot(
        self,
        *,
        task_id: str,
        slot_id: str,
    ) -> ContinuationCheckpoint:
        row = (
            self.db.query(BidPureAgentCheckpoint)
            .filter(
                BidPureAgentCheckpoint.task_id == task_id,
                BidPureAgentCheckpoint.slot_id == slot_id,
            )
            .one_or_none()
        )
        if row is None:
            raise PureAgentNotFound("checkpoint was not found")
        return self._checkpoint_contract(row)

    def load_validation_by_key(
        self,
        *,
        slot_id: str,
        idempotency_key: str,
    ) -> BidPureAgentSlotValidation | None:
        return (
            self.db.query(BidPureAgentSlotValidation)
            .filter(
                BidPureAgentSlotValidation.slot_id == slot_id,
                BidPureAgentSlotValidation.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )

    def load_validation_issues(
        self,
        *,
        task_id: str,
        validation_ref: str | None,
    ) -> tuple[dict[str, Any], ...]:
        if not validation_ref or not validation_ref.startswith("validation:"):
            return ()
        attempt_id = validation_ref.removeprefix("validation:")
        row = self._validation(attempt_id, lock=False)
        if row.task_id != task_id or row.status != "failed":
            return ()
        issues = normalize_json(row.issues_json or [])
        return tuple(issue for issue in issues if isinstance(issue, dict))

    def accept_user_message(
        self,
        *,
        conversation_id: str,
        owner_id: int,
        user_input: Any,
        created_by_ref: str,
        idempotency_key: str,
        reply_to_message_id: str | None = None,
        now: datetime | None = None,
    ) -> UserMessageAdmission:
        """Append open user input without deciding its semantic intent.

        A conversation lock only determines whether the message starts a new
        Task or is attached to the sole running/pending Task as a Steering
        Candidate. The Main Agent performs all later intent interpretation.
        """

        conversation = self.load_owned_conversation(
            conversation_id,
            owner_id=owner_id,
            lock=True,
        )
        if conversation.status != "active":
            raise PureAgentConflict("conversation is not active")
        normalized_input = normalize_json(user_input)
        existing = (
            self.db.query(BidPureAgentMessage)
            .filter(
                BidPureAgentMessage.conversation_id == conversation_id,
                BidPureAgentMessage.idempotency_key == idempotency_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            return self._user_message_admission_replay(
                existing,
                owner_id=owner_id,
                user_input=normalized_input,
                created_by_ref=created_by_ref,
                reply_to_message_id=reply_to_message_id,
            )

        active = self.load_active_task_state(conversation_id, lock=True)
        if active is None:
            disposition = "task_trigger"
            target_task_ref = None
            message_type = "user.task_trigger"
        else:
            disposition = "steering_candidate"
            target_task_ref = active.task_id
            message_type = "user.steering_candidate"
        content = {
            "schema_name": "bid.user-message.internal.v1",
            "input": normalized_input,
            "disposition": disposition,
            "target_task_ref": target_task_ref,
        }
        message = self.append_message(
            conversation_id=conversation_id,
            role="user",
            message_type=message_type,
            content=content,
            created_by_ref=created_by_ref,
            idempotency_key=idempotency_key,
            reply_to_message_id=reply_to_message_id,
            now=now,
        )
        task = active
        if task is None:
            task = self.create_task(
                conversation_id=conversation_id,
                trigger_message_id=message.id,
                owner_id=owner_id,
                goal_ref=f"message:{message.id}",
                now=now,
            )
        return UserMessageAdmission(
            message=message,
            task=task,
            disposition=disposition,
            replayed=False,
        )

    def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        message_type: str,
        content: Any,
        created_by_ref: str,
        idempotency_key: str | None = None,
        reply_to_message_id: str | None = None,
        now: datetime | None = None,
    ) -> BidPureAgentMessage:
        normalized = normalize_json(content)
        content_digest = canonical_hash(normalized)
        if idempotency_key:
            existing = (
                self.db.query(BidPureAgentMessage)
                .filter(
                    BidPureAgentMessage.conversation_id == conversation_id,
                    BidPureAgentMessage.idempotency_key == idempotency_key,
                )
                .with_for_update()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing.content_hash != content_digest
                    or existing.role != role
                    or existing.message_type != message_type
                    or existing.created_by_ref != created_by_ref
                    or existing.reply_to_message_id != reply_to_message_id
                ):
                    raise PureAgentConflict("message idempotency key was reused")
                return existing

        conversation = self._conversation(conversation_id, lock=True)
        if conversation.status != "active":
            raise PureAgentConflict("conversation is not active")
        if reply_to_message_id is not None:
            reply = (
                self.db.query(BidPureAgentMessage)
                .filter(BidPureAgentMessage.id == reply_to_message_id)
                .one_or_none()
            )
            if reply is None or reply.conversation_id != conversation_id:
                raise PureAgentConflict("reply target is outside the conversation")
        sequence_no = int(conversation.next_message_sequence)
        current_time = now or utc_now()
        row = BidPureAgentMessage(
            id=_new_id(),
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            role=str(role),
            message_type=str(message_type),
            content_json=normalized,
            content_hash=content_digest,
            reply_to_message_id=reply_to_message_id,
            idempotency_key=idempotency_key,
            created_by_ref=str(created_by_ref),
            created_at=current_time,
        )
        conversation.next_message_sequence = sequence_no + 1
        conversation.row_version = int(conversation.row_version) + 1
        conversation.updated_at = current_time
        self.db.add(row)
        self.db.flush()
        return row

    def create_task(
        self,
        *,
        conversation_id: str,
        trigger_message_id: str,
        owner_id: int,
        goal_ref: str,
        now: datetime | None = None,
    ) -> AgentTaskState:
        existing = (
            self.db.query(BidPureAgentTask)
            .filter(
                BidPureAgentTask.conversation_id == conversation_id,
                BidPureAgentTask.trigger_message_id == trigger_message_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if int(existing.owner_id) != int(owner_id) or existing.goal_ref != goal_ref:
                raise PureAgentConflict("task trigger was reused with different scope")
            return self._task_state(existing)

        conversation = self._conversation(conversation_id, lock=True)
        active_task = (
            self.db.query(BidPureAgentTask.id)
            .filter(
                BidPureAgentTask.conversation_id == conversation_id,
                BidPureAgentTask.status.in_(
                    (AgentTaskStatus.RUNNING.value, AgentTaskStatus.PENDING.value)
                ),
            )
            .limit(1)
            .first()
        )
        if active_task is not None:
            raise PureAgentConflict("conversation already has an active task")
        message = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == trigger_message_id)
            .one_or_none()
        )
        if (
            message is None
            or message.conversation_id != conversation_id
            or int(conversation.owner_id) != int(owner_id)
        ):
            raise PureAgentConflict("task trigger is outside the conversation scope")
        task_id = _new_id()
        state = create_running_task(
            task_id=task_id,
            session_id=conversation_id,
            goal_ref=goal_ref,
        )
        current_time = now or utc_now()
        row = BidPureAgentTask(
            id=task_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            owner_id=int(owner_id),
            status=state.status.value,
            execution_mode=state.execution_mode.value,
            state_version=state.state_version,
            row_version=1,
            goal_ref=goal_ref,
            plan_ref=None,
            active_slot_id=None,
            active_checkpoint_id=None,
            pending_phase=None,
            validation_attempt_id=None,
            in_flight_action_id=None,
            observation_refs_json=[],
            last_error_ref=None,
            cancellation_fence_id=None,
            terminal_at=None,
            created_at=current_time,
            updated_at=current_time,
        )
        self.db.add(row)
        self.db.flush()
        return state

    def load_task_state(self, task_id: str, *, lock: bool = False) -> AgentTaskState:
        return self._task_state(self._task(task_id, lock=lock))

    def load_runtime_task_state(
        self,
        *,
        task_id: str,
        conversation_id: str,
    ) -> AgentTaskState:
        """Lock the Conversation before Task using the shared commit order."""

        self._conversation(conversation_id, lock=True)
        row = self._task(task_id, lock=True)
        if row.conversation_id != conversation_id:
            raise PureAgentNotFound("task was not found")
        return self._task_state(row)

    def store_context_snapshot(
        self,
        snapshot: ContextSnapshot,
        *,
        now: datetime | None = None,
    ) -> BidPureAgentContextSnapshot:
        """Persist one immutable Context receipt required by Response Commit."""

        payload = normalize_json(snapshot.model_dump(mode="json"))
        existing = (
            self.db.query(BidPureAgentContextSnapshot)
            .filter(BidPureAgentContextSnapshot.id == snapshot.snapshot_ref)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.task_id != snapshot.task_ref
                or int(existing.state_version) != snapshot.state_version
                or not self._digest_matches(existing.snapshot_hash, snapshot.snapshot_hash)
                or normalize_json(existing.snapshot_json) != payload
            ):
                raise PureAgentConflict(
                    "Context Snapshot reference was reused with different content"
                )
            return existing
        task = self._task(snapshot.task_ref, lock=False)
        if snapshot.state_version > int(task.state_version):
            raise PureAgentFenceRejected("Context Snapshot is ahead of the Task state")
        row = BidPureAgentContextSnapshot(
            id=snapshot.snapshot_ref,
            task_id=snapshot.task_ref,
            state_version=snapshot.state_version,
            consumer=snapshot.consumer.value,
            status=snapshot.status.value,
            snapshot_json=payload,
            included_refs_json=list(snapshot.included_refs),
            excluded_refs_json=list(snapshot.excluded_refs),
            estimated_input_tokens=snapshot.estimated_input_tokens,
            snapshot_hash=self._storage_digest(snapshot.snapshot_hash),
            created_at=now or utc_now(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def store_observation_artifact(
        self,
        observation: ActionObservation,
        *,
        artifact: Any,
        context_snapshot_ref: str,
        now: datetime | None = None,
    ) -> BidPureAgentObservationArtifact:
        """Persist the accepted Observation body before its Task transition."""

        observation_payload = normalize_json(observation.model_dump(mode="json"))
        observation_body = normalize_json(
            observation.model_dump(
                mode="json",
                exclude={"observation_ref", "observation_hash"},
            )
        )
        artifact_payload = normalize_json(artifact)
        observation_digest = canonical_hash(observation_body)
        artifact_digest = canonical_hash(artifact_payload)
        if (
            not self._digest_matches(
                observation.observation_hash,
                observation_digest,
            )
            or not self._digest_matches(observation.artifact_hash, artifact_digest)
        ):
            raise PureAgentFenceRejected("Observation Artifact hash drifted")

        existing = (
            self.db.query(BidPureAgentObservationArtifact)
            .filter(BidPureAgentObservationArtifact.id == observation.observation_ref)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            persisted = self._observation_artifact_row(existing)
            if (
                persisted.observation != observation
                or persisted.context_snapshot_ref != context_snapshot_ref
                or persisted.artifact != artifact_payload
            ):
                raise PureAgentConflict(
                    "Observation reference was reused with different content"
                )
            return existing

        task = self._task(observation.task_ref, lock=True)
        action = self._action(observation.source_action_ref, lock=True)
        context = (
            self.db.query(BidPureAgentContextSnapshot)
            .filter(BidPureAgentContextSnapshot.id == context_snapshot_ref)
            .one_or_none()
        )
        action_payload = normalize_json(action.arguments_json)
        try:
            action_intent = ActionReservationIntent.model_validate(
                action_payload["intent"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PureAgentFenceRejected(
                "Observation source Action contract is invalid"
            ) from exc
        if (
            action.task_id != task.id
            or int(action.sequence_no) != observation.action_sequence
            or canonical_hash(action_payload) != action.arguments_hash
            or action_intent.task_ref != task.id
            or action_intent.action_kind.value != action.action_type
            or action_intent.context_snapshot_ref != context_snapshot_ref
            or action_intent.state_version + 1 != observation.state_version
            or action.status not in {"succeeded", "failed"}
            or action.result_ref != observation.artifact_ref
            or not self._digest_matches(action.result_hash or "", artifact_digest)
            or task.status != AgentTaskStatus.RUNNING.value
            or int(task.state_version) != observation.state_version
            or task.in_flight_action_id != action.id
            or context is None
            or context.task_id != task.id
        ):
            raise PureAgentFenceRejected(
                "Observation Artifact lost its Task, Action, or Context fence"
            )
        row = BidPureAgentObservationArtifact(
            id=observation.observation_ref,
            task_id=observation.task_ref,
            action_id=observation.source_action_ref,
            context_snapshot_id=context_snapshot_ref,
            state_version=observation.state_version,
            action_sequence=observation.action_sequence,
            kind=observation.kind.value,
            status=observation.status.value,
            observation_json=observation_payload,
            observation_hash=self._storage_digest(observation.observation_hash),
            artifact_ref=observation.artifact_ref,
            artifact_hash=self._storage_digest(observation.artifact_hash),
            artifact_json=artifact_payload,
            created_at=now or utc_now(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def commit_transition(
        self,
        event: TaskTransitionEvent,
        *,
        occurred_at: datetime | None = None,
    ) -> TransitionCommit:
        payload = event.model_dump(mode="json")
        payload_digest = canonical_hash(payload)
        existing = (
            self.db.query(BidPureAgentEvent)
            .filter(BidPureAgentEvent.event_id == event.event_id)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if existing.task_id != event.task_id or existing.payload_hash != payload_digest:
                raise PureAgentConflict("event id was reused with a different payload")
            return TransitionCommit(
                state=AgentTaskState.model_validate(existing.state_after_json),
                event_id=event.event_id,
                replayed=True,
            )

        task_row = self._task(event.task_id, lock=True)
        current = self._task_state(task_row)
        prior_effect_event = None
        if event.effect_idempotency_key is not None:
            prior_effect_event = (
                self.db.query(BidPureAgentEvent)
                .filter(
                    BidPureAgentEvent.task_id == event.task_id,
                    BidPureAgentEvent.effect_idempotency_key
                    == event.effect_idempotency_key,
                )
                .one_or_none()
            )
        consumed_effect_keys = (
            {event.effect_idempotency_key} if prior_effect_event is not None else set()
        )
        decision = decide_transition(
            current,
            event,
            consumed_effect_keys=consumed_effect_keys,
        )
        next_state = decision.next_state
        current_time = occurred_at or utc_now()
        values = self._task_values_from_state(
            task_row,
            next_state,
            event=event,
            now=current_time,
        )
        result = self.db.execute(
            update(BidPureAgentTask)
            .where(
                BidPureAgentTask.id == task_row.id,
                BidPureAgentTask.state_version == event.expected_state_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise PureAgentConflict("task state compare-and-swap failed")
        # The CAS uses a Core UPDATE so expire the already-loaded identity-map row.
        # A subsequent repository call in the same caller-owned transaction must
        # observe the new state_version instead of the pre-CAS object snapshot.
        self.db.expire(task_row)
        event_row = BidPureAgentEvent(
            id=_new_id(),
            event_id=event.event_id,
            task_id=event.task_id,
            action_id=event.action_ref,
            event_type=event.event_type.value,
            state_version_before=current.state_version,
            state_version_after=next_state.state_version,
            status_before=current.status.value,
            status_after=next_state.status.value,
            effect_idempotency_key=event.effect_idempotency_key,
            payload_json=payload,
            payload_hash=payload_digest,
            state_after_json=next_state.model_dump(mode="json"),
            occurred_at=current_time,
            created_at=current_time,
        )
        self.db.add(event_row)
        self.db.flush()
        return TransitionCommit(
            state=next_state,
            event_id=event.event_id,
            replayed=False,
        )

    def store_plan(
        self,
        revision: PlanRevision,
        *,
        context_snapshot_ref: str,
        now: datetime | None = None,
    ) -> BidPureAgentPlan:
        body = revision.model_dump(mode="json")
        plan_digest = canonical_hash(body)
        existing = (
            self.db.query(BidPureAgentPlan)
            .filter(
                BidPureAgentPlan.task_id == revision.task_id,
                BidPureAgentPlan.plan_version == revision.plan_version,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.id != revision.plan_id
                or existing.plan_hash != plan_digest
                or existing.context_snapshot_ref != context_snapshot_ref
            ):
                raise PureAgentConflict("plan version was reused with different content")
            return existing
        self._task(revision.task_id, lock=True)
        if revision.supersedes_ref is not None:
            previous = (
                self.db.query(BidPureAgentPlan)
                .filter(BidPureAgentPlan.id == revision.supersedes_ref)
                .with_for_update()
                .one_or_none()
            )
            if previous is None or previous.task_id != revision.task_id:
                raise PureAgentConflict("superseded plan is outside the task")
            previous.status = "superseded"
        row = BidPureAgentPlan(
            id=revision.plan_id,
            task_id=revision.task_id,
            plan_version=revision.plan_version,
            schema_version=revision.schema_version,
            status="active",
            body_json=body,
            plan_hash=plan_digest,
            supersedes_plan_id=revision.supersedes_ref,
            context_snapshot_ref=context_snapshot_ref,
            created_at=now or utc_now(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def load_response_head(
        self,
        response_ref: str,
        *,
        lock: bool = False,
    ) -> ResponseVersionHead:
        return self._response_envelope(
            self._response(response_ref, lock=lock)
        ).head()

    def commit_answer_response(
        self,
        decision: ResponseCommitDecision,
        *,
        created_by_ref: str,
        now: datetime | None = None,
    ) -> AnswerResponseCommit:
        """Atomically append the safe message, response, supersede event, and Task CAS.

        The method only flushes.  The caller must commit or roll back the surrounding
        transaction as one unit; this is the Response Commit versus Cancel fence.
        """

        artifact = decision.artifact
        completion_event = decision.completion_event
        if not decision.accepted or artifact is None or completion_event is None:
            raise PureAgentFenceRejected("only an accepted response decision can commit")

        existing = (
            self.db.query(BidPureAgentResponse)
            .filter(BidPureAgentResponse.id == artifact.response_ref)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            envelope = self._response_envelope(existing)
            if envelope.artifact != artifact or existing.rendered_message_id is None:
                raise PureAgentConflict("response reference was reused with different content")
            transition = self.commit_transition(completion_event)
            return AnswerResponseCommit(
                artifact=envelope.artifact,
                message_id=existing.rendered_message_id,
                state=transition.state,
                current_status=envelope.current_status,
                replayed=True,
            )

        # Conversation is the outer serialization boundary.  Lock it before Task so
        # concurrent turns cannot invert message order or race response publication.
        conversation = self._conversation(artifact.conversation_ref, lock=True)
        task_row = self._task(artifact.task_ref, lock=True)
        current = self._task_state(task_row)
        if (
            conversation.id != task_row.conversation_id
            or decision.conversation_ref != conversation.id
            or current.state_version != artifact.commit_state_version
            or current.status is not AgentTaskStatus.RUNNING
            or artifact.answer_observation_ref not in current.observation_refs
        ):
            raise PureAgentFenceRejected("response commit lost its Task or conversation fence")
        # Fail before writing if cancellation or another terminal commit already won.
        decide_transition(current, completion_event)

        context_row = (
            self.db.query(BidPureAgentContextSnapshot)
            .filter(BidPureAgentContextSnapshot.id == artifact.context_snapshot_ref)
            .with_for_update()
            .one_or_none()
        )
        if (
            context_row is None
            or context_row.task_id != artifact.task_ref
            or int(context_row.state_version) != artifact.answer_state_version
            or context_row.consumer != "main_agent"
            or context_row.status not in {"ready", "ready_with_limits"}
            or not self._digest_matches(
                context_row.snapshot_hash,
                artifact.context_snapshot_hash,
            )
        ):
            raise PureAgentFenceRejected("response Context Snapshot is absent or stale")

        prior_final = (
            self.db.query(BidPureAgentResponse)
            .filter(
                BidPureAgentResponse.task_id == artifact.task_ref,
                BidPureAgentResponse.status.in_(("committed", "stale", "superseded")),
            )
            .with_for_update()
            .one_or_none()
        )
        if prior_final is not None:
            raise PureAgentConflict("Task already has a final response artifact")

        current_time = now or utc_now()
        version_controller = ResponseVersionController()
        previous_row = None
        previous_envelope = None
        if artifact.supersedes_response_ref is not None:
            previous_row = self._response(artifact.supersedes_response_ref, lock=True)
            if previous_row.task_id == artifact.task_ref:
                raise PureAgentConflict("a terminal Task cannot replace its own response")
            previous_envelope = self._response_envelope(previous_row)
            try:
                previous_envelope, _ = version_controller.apply_superseded(
                    envelope=previous_envelope,
                    replacement=artifact,
                    occurred_at=current_time,
                )
            except ResponseVersionRejected as exc:
                raise PureAgentConflict("response supersede fence failed") from exc

        message = self.append_message(
            conversation_id=artifact.conversation_ref,
            role="assistant",
            message_type="answer.committed",
            content=artifact.message.model_dump(mode="json"),
            created_by_ref=created_by_ref,
            idempotency_key=f"answer-commit:{artifact.response_ref}",
            reply_to_message_id=task_row.trigger_message_id,
            now=current_time,
        )
        envelope = version_controller.initial_envelope(
            artifact=artifact,
            occurred_at=current_time,
        )
        row = BidPureAgentResponse(
            id=artifact.response_ref,
            conversation_id=artifact.conversation_ref,
            task_id=artifact.task_ref,
            context_snapshot_id=artifact.context_snapshot_ref,
            status=ResponseLifecycleStatus.COMMITTED.value,
            draft_json=normalize_json(envelope.model_dump(mode="json")),
            draft_hash=self._storage_digest(artifact.draft_hash),
            rendered_message_id=message.id,
            supersedes_response_id=artifact.supersedes_response_ref,
            created_at=current_time,
            committed_at=current_time,
        )
        self.db.add(row)
        if previous_row is not None and previous_envelope is not None:
            previous_row.status = ResponseLifecycleStatus.SUPERSEDED.value
            previous_row.draft_json = normalize_json(
                previous_envelope.model_dump(mode="json")
            )
        self.db.flush()

        transition = self.commit_transition(completion_event, occurred_at=current_time)
        return AnswerResponseCommit(
            artifact=artifact,
            message_id=message.id,
            state=transition.state,
            current_status=ResponseLifecycleStatus.COMMITTED,
            replayed=False,
        )

    def mark_response_stale(
        self,
        intent: ResponseStaleIntent,
        *,
        now: datetime | None = None,
    ) -> ResponseLifecycleMutation:
        """Append a Stale lifecycle event without rewriting the sent answer message."""

        row = self._response(intent.response_ref, lock=True)
        envelope = self._response_envelope(row)
        try:
            updated, replayed = ResponseVersionController().apply_stale(
                envelope=envelope,
                intent=intent,
                occurred_at=now or utc_now(),
            )
        except ResponseVersionRejected as exc:
            raise PureAgentConflict("response stale fence failed") from exc
        if not replayed:
            row.status = ResponseLifecycleStatus.STALE.value
            row.draft_json = normalize_json(updated.model_dump(mode="json"))
            self.db.flush()
        event = next(
            item
            for item in updated.lifecycle_events
            if item.idempotency_key == intent.idempotency_key
        )
        return ResponseLifecycleMutation(
            head=updated.head(),
            event_ref=event.event_ref,
            replayed=replayed,
        )

    def reserve_action(
        self,
        *,
        task_id: str,
        event_id: str,
        action_type: str,
        execution_kind: str,
        arguments: Any,
        effect_key: str,
        effect_type: str,
        replay_policy: str,
        fencing_token: int,
        effect_request_hash: str | None = None,
        now: datetime | None = None,
    ) -> ActionReservation:
        if int(fencing_token) < 1:
            raise PureAgentFenceRejected("fencing token must be positive")
        if execution_kind not in {"direct", "durable"}:
            raise PureAgentConflict("action execution kind is invalid")
        if replay_policy not in {
            "safe_idempotent",
            "reconcile_required",
            "no_replay",
        }:
            raise PureAgentConflict("effect replay policy is invalid")
        normalized_arguments = normalize_json(arguments)
        request_digest = canonical_hash(normalized_arguments)
        effect_request_digest = request_digest
        if effect_request_hash is not None:
            candidate = str(effect_request_hash)
            digest_body = candidate.removeprefix("sha256:")
            if (
                not candidate.startswith("sha256:")
                or len(digest_body) != 64
                or any(character not in "0123456789abcdef" for character in digest_body)
            ):
                raise PureAgentFenceRejected("effect request hash is invalid")
            effect_request_digest = digest_body
        existing_fence = (
            self.db.query(BidPureAgentEffectFence)
            .filter(
                BidPureAgentEffectFence.task_id == task_id,
                BidPureAgentEffectFence.effect_key == effect_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing_fence is not None:
            if (
                existing_fence.request_hash != effect_request_digest
                or existing_fence.effect_type != effect_type
                or existing_fence.replay_policy != replay_policy
            ):
                raise PureAgentConflict("effect key was reused with a different contract")
            existing_state = self.load_task_state(task_id, lock=True)
            if existing_state.in_flight_action_ref != existing_fence.action_id:
                raise PureAgentFenceRejected(
                    "existing Effect is no longer the active Action"
                )
            return ActionReservation(
                action_id=existing_fence.action_id,
                effect_fence_id=existing_fence.id,
                fencing_token=int(existing_fence.fencing_token),
                state=existing_state,
                replayed=True,
            )

        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        if current.status is not AgentTaskStatus.RUNNING:
            raise PureAgentConflict("only running task can reserve an action")
        sequence_no = int(
            self.db.query(func.max(BidPureAgentAction.sequence_no))
            .filter(BidPureAgentAction.task_id == task_id)
            .scalar()
            or 0
        ) + 1
        current_time = now or utc_now()
        action_id = _new_id()
        fence_id = _new_id()
        action = BidPureAgentAction(
            id=action_id,
            task_id=task_id,
            sequence_no=sequence_no,
            action_type=action_type,
            execution_kind=execution_kind,
            status="accepted",
            arguments_json=normalized_arguments,
            arguments_hash=request_digest,
            effect_idempotency_key=effect_key,
            result_ref=None,
            result_hash=None,
            error_code=None,
            created_at=current_time,
            started_at=None,
            completed_at=None,
        )
        fence = BidPureAgentEffectFence(
            id=fence_id,
            task_id=task_id,
            action_id=action_id,
            effect_key=effect_key,
            effect_type=effect_type,
            replay_policy=replay_policy,
            status="reserved",
            fencing_token=int(fencing_token),
            request_hash=effect_request_digest,
            result_ref=None,
            result_hash=None,
            error_code=None,
            reserved_at=current_time,
            settled_at=None,
        )
        self.db.add_all((action, fence))
        self.db.flush()
        commit = self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.ACTION_ACCEPTED,
                effect_idempotency_key=effect_key,
                action_ref=action_id,
            ),
            occurred_at=current_time,
        )
        return ActionReservation(
            action_id=action_id,
            effect_fence_id=fence_id,
            fencing_token=int(fencing_token),
            state=commit.state,
            replayed=False,
        )

    def reserve_governed_action(
        self,
        *,
        event_id: str,
        intent: ActionReservationIntent,
        binding: ActionRuntimeBinding,
        admission: ActionAdmissionDecision,
        fencing_token: int,
        persisted_action_payload: Any | None = None,
        now: datetime | None = None,
    ) -> GovernedActionReservation:
        """Atomically stage one Guard-approved Action, Effect, and Budget set.

        The caller still owns the transaction.  Any later failure while reserving
        a Budget account rolls back the Action transition and Effect Fence with it.
        """

        candidate = admission.candidate
        if (
            admission.disposition is not AdmissionDisposition.ADMIT
            or admission.execution is None
            or admission.execution.execution_kind is None
            or admission.budget is None
            or not admission.budget.allowed
            or candidate.task_ref != intent.task_ref
            or candidate.action_intent_ref != intent.intent_ref
            or candidate.arguments_hash != intent.arguments_hash
            or candidate.binding_ref != binding.binding_ref
            or candidate.binding_hash != binding.binding_hash
        ):
            raise PureAgentFenceRejected(
                "only a matching admitted Action may be reserved"
            )
        current_time = now or utc_now()
        action = self.reserve_action(
            task_id=intent.task_ref,
            event_id=event_id,
            action_type=intent.action_kind.value,
            execution_kind=admission.execution.execution_kind.value,
            # The Controller may persist a versioned envelope containing the
            # authoritative intent plus the minimum driver payload needed by a
            # later transaction.  Existing callers retain the original compact
            # arguments-only representation.
            arguments=(
                intent.arguments
                if persisted_action_payload is None
                else persisted_action_payload
            ),
            effect_key=candidate.effect_key,
            effect_type=binding.effect_type,
            replay_policy=binding.replay_policy.value,
            fencing_token=fencing_token,
            # Action persistence may contain a Controller envelope, while Effect
            # identity remains bound to the model-selected Action arguments.
            effect_request_hash=intent.arguments_hash,
            now=current_time,
        )
        if (
            action.replayed
            and action.state.in_flight_action_ref != action.action_id
        ):
            raise PureAgentFenceRejected(
                "replayed Effect is no longer the active Action"
            )
        entries = tuple(
            self.reserve_budget(
                task_id=intent.task_ref,
                resource_type=directive.resource_type.value,
                amount=directive.amount,
                idempotency_key=directive.idempotency_key,
                action_id=action.action_id,
                now=current_time,
            )
            for directive in admission.budget.reservations
        )
        return GovernedActionReservation(
            action=action,
            budget_entries=entries,
        )

    def mark_effect_running(
        self,
        *,
        effect_fence_id: str,
        fencing_token: int,
        expected_state_version: int,
        now: datetime | None = None,
    ) -> BidPureAgentEffectFence:
        fence = self._effect_fence(effect_fence_id, lock=True)
        task = self._task(fence.task_id, lock=True)
        self._assert_fencing_token(fence, fencing_token)
        if task.status == AgentTaskStatus.CANCELLED.value or task.cancellation_fence_id:
            raise PureAgentFenceRejected("cancelled task cannot start an effect")
        state = self._task_state(task)
        if (
            state.state_version != int(expected_state_version)
            or state.in_flight_action_ref != fence.action_id
        ):
            raise PureAgentFenceRejected(
                "stale or non-active Action cannot start an effect"
            )
        if fence.status != "reserved":
            raise PureAgentFenceRejected("effect is not reserved")
        fence.status = "running"
        action = self._action(fence.action_id, lock=True)
        action.status = "running"
        action.started_at = now or utc_now()
        self.db.flush()
        return fence

    def settle_effect(
        self,
        *,
        effect_fence_id: str,
        fencing_token: int,
        expected_state_version: int,
        status: str,
        result_ref: str | None,
        result: Any | None,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> EffectSettlement:
        if status not in {"succeeded", "failed", "uncertain"}:
            raise PureAgentFenceRejected("effect settlement status is invalid")
        fence = self._effect_fence(effect_fence_id, lock=True)
        task = self._task(fence.task_id, lock=True)
        self._assert_fencing_token(fence, fencing_token)
        current_time = now or utc_now()
        normalized_result = normalize_json(result) if result is not None else None
        result_digest = (
            canonical_hash(normalized_result) if normalized_result is not None else None
        )
        action = self._action(fence.action_id, lock=True)
        state = self._task_state(task)
        cancelled = task.status == AgentTaskStatus.CANCELLED.value or bool(
            task.cancellation_fence_id
        )
        stale = (
            state.state_version != int(expected_state_version)
            or state.in_flight_action_ref != fence.action_id
        )
        if fence.status == "ignored_late":
            if fence.result_hash == result_digest and fence.result_ref == result_ref:
                return EffectSettlement(
                    effect_fence_id=fence.id,
                    status="ignored_late",
                    accepted_for_context=False,
                )
            raise PureAgentFenceRejected("late effect result was already recorded")
        if cancelled or stale:
            if fence.status in {"succeeded", "failed", "uncertain"}:
                if (
                    fence.status == status
                    and fence.result_hash == result_digest
                    and fence.result_ref == result_ref
                ):
                    return EffectSettlement(
                        effect_fence_id=fence.id,
                        status=fence.status,
                        accepted_for_context=False,
                    )
                raise PureAgentFenceRejected("effect was settled before cancellation")
            fence.status = "ignored_late"
            fence.result_ref = result_ref
            fence.result_hash = result_digest
            fence.error_code = "PURE_AGENT_LATE_RESULT_IGNORED"
            fence.settled_at = current_time
            action.status = "ignored_late"
            action.result_ref = result_ref
            action.result_hash = result_digest
            action.error_code = "PURE_AGENT_LATE_RESULT_IGNORED"
            action.completed_at = current_time
            self.db.flush()
            return EffectSettlement(
                effect_fence_id=fence.id,
                status="ignored_late",
                accepted_for_context=False,
            )
        if fence.status not in {"reserved", "running"}:
            if (
                fence.status == status
                and fence.result_hash == result_digest
                and fence.result_ref == result_ref
            ):
                return EffectSettlement(
                    effect_fence_id=fence.id,
                    status=fence.status,
                    accepted_for_context=fence.status == "succeeded",
                )
            raise PureAgentFenceRejected("effect was already settled")
        fence.status = status
        fence.result_ref = result_ref
        fence.result_hash = result_digest
        fence.error_code = error_code
        fence.settled_at = current_time
        action.status = "succeeded" if status == "succeeded" else "failed"
        action.result_ref = result_ref
        action.result_hash = result_digest
        action.error_code = error_code
        action.completed_at = current_time
        self.db.flush()
        return EffectSettlement(
            effect_fence_id=fence.id,
            status=status,
            accepted_for_context=status == "succeeded",
        )

    def cancel_task(
        self,
        *,
        task_id: str,
        event_id: str,
        requested_by_ref: str,
        reason: str,
        expected_state_version: int | None = None,
        expected_owner_id: int | None = None,
        expected_conversation_id: str | None = None,
        now: datetime | None = None,
    ) -> TransitionCommit:
        # Response Commit takes the Conversation lock before the Task lock.
        # Cancellation follows the same order so both operations race on one
        # State Version without a lock-order inversion.
        scope = self._task(task_id, lock=False)
        self._conversation(scope.conversation_id, lock=True)
        task_row = self._task(task_id, lock=True)
        if (
            (expected_owner_id is not None and int(task_row.owner_id) != int(expected_owner_id))
            or (
                expected_conversation_id is not None
                and task_row.conversation_id != expected_conversation_id
            )
        ):
            raise PureAgentNotFound("task was not found")
        current = self._task_state(task_row)
        existing = (
            self.db.query(BidPureAgentCancellationFence)
            .filter(BidPureAgentCancellationFence.task_id == task_id)
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if current.status is AgentTaskStatus.CANCELLED:
                if (
                    existing.requested_by_ref != requested_by_ref
                    or existing.reason != reason
                    or (
                        expected_state_version is not None
                        and int(existing.state_version)
                        != int(expected_state_version)
                    )
                ):
                    raise PureAgentConflict("cancel request changed after cancellation")
                return TransitionCommit(state=current, event_id=event_id, replayed=True)
            raise PureAgentConflict("cancellation fence exists without cancelled state")
        if current.status in TERMINAL_STATUSES:
            raise PureAgentConflict("terminal task cannot be cancelled")
        if (
            expected_state_version is not None
            and current.state_version != int(expected_state_version)
        ):
            raise PureAgentConflict("expected task state version is stale")
        current_time = now or utc_now()
        fence = BidPureAgentCancellationFence(
            id=_new_id(),
            task_id=task_id,
            state_version=current.state_version,
            requested_by_ref=requested_by_ref,
            reason=reason,
            created_at=current_time,
        )
        self.db.add(fence)
        self.db.flush()
        running_effect_action_ids = {
            row[0]
            for row in (
                self.db.query(BidPureAgentEffectFence.action_id)
                .filter(
                    BidPureAgentEffectFence.task_id == task_id,
                    BidPureAgentEffectFence.status == "running",
                )
                .all()
            )
        }
        commit = self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.CANCEL_REQUESTED,
                cancellation_fence_ref=fence.id,
            ),
            occurred_at=current_time,
        )
        if current.pending_context is not None:
            checkpoint = self._checkpoint(
                current.pending_context.checkpoint_ref,
                lock=True,
            )
            if checkpoint.status == "open":
                checkpoint.status = "invalidated"
                checkpoint.recovery_lease_owner = None
                checkpoint.recovery_lease_until = None
        (
            self.db.query(BidPureAgentEffectFence)
            .filter(
                BidPureAgentEffectFence.task_id == task_id,
                BidPureAgentEffectFence.status.in_(("reserved", "running")),
            )
            .update(
                {
                    BidPureAgentEffectFence.status: "cancelled",
                    BidPureAgentEffectFence.error_code: "PURE_AGENT_CANCELLED",
                    BidPureAgentEffectFence.settled_at: current_time,
                },
                synchronize_session=False,
            )
        )
        (
            self.db.query(BidPureAgentAction)
            .filter(
                BidPureAgentAction.task_id == task_id,
                BidPureAgentAction.status.in_(("accepted", "running")),
            )
            .update(
                {
                    BidPureAgentAction.status: "cancelled",
                    BidPureAgentAction.error_code: "PURE_AGENT_CANCELLED",
                    BidPureAgentAction.completed_at: current_time,
                },
                synchronize_session=False,
            )
        )
        open_reservations = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(
                BidPureAgentBudgetEntry.task_id == task_id,
                BidPureAgentBudgetEntry.entry_kind == "reserve",
            )
            .with_for_update()
            .all()
        )
        for reservation in open_reservations:
            settled = (
                self.db.query(BidPureAgentBudgetEntry.id)
                .filter(
                    BidPureAgentBudgetEntry.account_id == reservation.account_id,
                    BidPureAgentBudgetEntry.reservation_ref == reservation.id,
                )
                .one_or_none()
            )
            if settled is not None:
                continue
            account = (
                self.db.query(BidPureAgentBudgetAccount)
                .filter(BidPureAgentBudgetAccount.id == reservation.account_id)
                .one()
            )
            usage_unverified = reservation.action_id in running_effect_action_ids
            self._budget_mutation(
                task_id=task_id,
                resource_type=account.resource_type,
                amount=(int(reservation.amount) if usage_unverified else 0),
                idempotency_key=(
                    f"cancel-settle:{reservation.id}"
                    if usage_unverified
                    else f"cancel-release:{reservation.id}"
                ),
                entry_kind=("settle" if usage_unverified else "release"),
                action_id=reservation.action_id,
                reservation_ref=reservation.id,
                reserved_release=int(reservation.amount),
                now=current_time,
            )
        self.db.flush()
        return commit

    def create_budget_account(
        self,
        *,
        task_id: str,
        resource_type: str,
        unit: str,
        limit_amount: int,
        now: datetime | None = None,
    ) -> BidPureAgentBudgetAccount:
        if int(limit_amount) < 0:
            raise PureAgentBudgetExceeded("budget limit cannot be negative")
        task = self._task(task_id, lock=True)
        if task.status in {status.value for status in TERMINAL_STATUSES}:
            raise PureAgentConflict("terminal task cannot create a budget account")
        existing = (
            self.db.query(BidPureAgentBudgetAccount)
            .filter(
                BidPureAgentBudgetAccount.task_id == task_id,
                BidPureAgentBudgetAccount.resource_type == resource_type,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if existing.unit != unit or int(existing.limit_amount) != int(limit_amount):
                raise PureAgentConflict("budget account definition changed")
            return existing
        current_time = now or utc_now()
        row = BidPureAgentBudgetAccount(
            id=_new_id(),
            task_id=task_id,
            resource_type=resource_type,
            unit=unit,
            limit_amount=int(limit_amount),
            reserved_amount=0,
            actual_amount=0,
            row_version=1,
            created_at=current_time,
            updated_at=current_time,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def reserve_budget(
        self,
        *,
        task_id: str,
        resource_type: str,
        amount: int,
        idempotency_key: str,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> BudgetMutation:
        return self._budget_mutation(
            task_id=task_id,
            resource_type=resource_type,
            amount=amount,
            idempotency_key=idempotency_key,
            entry_kind="reserve",
            action_id=action_id,
            reservation_ref=None,
            now=now,
        )

    def settle_budget(
        self,
        *,
        task_id: str,
        resource_type: str,
        reservation_entry_id: str,
        actual_amount: int,
        idempotency_key: str,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> BudgetMutation:
        account = self._budget_account(task_id, resource_type, lock=True)
        reservation = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(BidPureAgentBudgetEntry.id == reservation_entry_id)
            .one_or_none()
        )
        if (
            reservation is None
            or reservation.account_id != account.id
            or reservation.entry_kind != "reserve"
        ):
            raise PureAgentConflict("budget reservation is invalid")
        if int(actual_amount) < 0 or int(actual_amount) > int(reservation.amount):
            raise PureAgentBudgetExceeded("actual amount exceeds its reservation")
        prior_settlement = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(
                BidPureAgentBudgetEntry.account_id == account.id,
                BidPureAgentBudgetEntry.reservation_ref == reservation_entry_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if prior_settlement is not None:
            if (
                prior_settlement.idempotency_key != idempotency_key
                or int(prior_settlement.amount) != int(actual_amount)
            ):
                raise PureAgentConflict("budget reservation was already settled")
            return BudgetMutation(
                entry_id=prior_settlement.id,
                resource_type=resource_type,
                reserved_after=int(prior_settlement.reserved_after),
                actual_after=int(prior_settlement.actual_after),
                replayed=True,
            )
        return self._budget_mutation(
            task_id=task_id,
            resource_type=resource_type,
            amount=int(actual_amount),
            idempotency_key=idempotency_key,
            entry_kind="settle" if int(actual_amount) else "release",
            action_id=action_id,
            reservation_ref=reservation_entry_id,
            reserved_release=int(reservation.amount),
            now=now,
        )

    def charge_budget(
        self,
        *,
        task_id: str,
        resource_type: str,
        amount: int,
        idempotency_key: str,
        action_id: str | None = None,
        now: datetime | None = None,
    ) -> BudgetMutation:
        """Record trusted usage that has no open reservation, including late cost."""

        return self._budget_mutation(
            task_id=task_id,
            resource_type=resource_type,
            amount=amount,
            idempotency_key=idempotency_key,
            entry_kind="charge",
            action_id=action_id,
            reservation_ref=None,
            now=now,
        )

    def suspend_for_slot(
        self,
        *,
        task_id: str,
        event_id: str,
        name: str,
        request_message: str,
        input_model_ref: str,
        business_validator_refs: Iterable[str],
        context_snapshot_ref: str,
        suspended_action_id: str,
        effect_fence_id: str,
        resume_token: str,
        slot_id: str | None = None,
        checkpoint_id: str | None = None,
        now: datetime | None = None,
    ) -> PendingSuspension:
        replay = self._transition_replay(
            event_id,
            task_id,
            TaskEventType.INFORMATION_REQUIRED,
        )
        if replay is not None:
            pending = replay.state.pending_context
            if pending is None:
                raise PureAgentConflict("replayed suspension has no pending context")
            slot_row = self._slot(pending.slot_ref, lock=True)
            checkpoint_row = self._checkpoint(pending.checkpoint_ref, lock=True)
            return PendingSuspension(
                slot=self._slot_contract(slot_row),
                checkpoint=self._checkpoint_contract(checkpoint_row),
                state=replay.state,
            )
        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        if current.status is not AgentTaskStatus.RUNNING:
            raise PureAgentConflict("only running task can enter pending")
        if current.in_flight_action_ref is not None:
            raise PureAgentConflict("cannot suspend with an in-flight action")
        unresolved = (
            self.db.query(BidPureAgentSlot)
            .filter(
                BidPureAgentSlot.task_id == task_id,
                BidPureAgentSlot.status == "unresolved",
            )
            .with_for_update()
            .one_or_none()
        )
        if unresolved is not None:
            raise PureAgentConflict("task already has an unresolved slot")
        action = self._action(suspended_action_id, lock=True)
        effect = self._effect_fence(effect_fence_id, lock=True)
        if action.task_id != task_id or effect.task_id != task_id or effect.action_id != action.id:
            raise PureAgentConflict("checkpoint action/effect scope mismatch")
        if (
            effect.status not in {"succeeded", "failed"}
            or action.status != effect.status
        ):
            raise PureAgentFenceRejected(
                "only a settled action/effect pair can anchor a pending checkpoint"
            )
        current_time = now or utc_now()
        slot_id = slot_id or _new_id()
        checkpoint_id = checkpoint_id or _new_id()
        if slot_id == checkpoint_id:
            raise PureAgentConflict("Slot and Checkpoint references must differ")
        if (
            self.db.query(BidPureAgentSlot.id)
            .filter(BidPureAgentSlot.id == slot_id)
            .first()
            is not None
            or self.db.query(BidPureAgentCheckpoint.id)
            .filter(BidPureAgentCheckpoint.id == checkpoint_id)
            .first()
            is not None
        ):
            raise PureAgentConflict("Slot or Checkpoint reference was already used")
        validator_refs = tuple(str(value) for value in business_validator_refs)
        slot_row = BidPureAgentSlot(
            id=slot_id,
            task_id=task_id,
            name=name,
            request_message=request_message,
            input_model_ref=input_model_ref,
            business_validator_refs_json=list(validator_refs),
            status="unresolved",
            candidate_input_ref=None,
            resolved_value_ref=None,
            created_at=current_time,
            resolved_at=None,
        )
        checkpoint_row = BidPureAgentCheckpoint(
            id=checkpoint_id,
            task_id=task_id,
            slot_id=slot_id,
            suspended_state_version=current.state_version,
            execution_mode=current.execution_mode.value,
            context_snapshot_ref=context_snapshot_ref,
            suspended_action_id=suspended_action_id,
            effect_fence_id=effect_fence_id,
            resume_token_hash=hash_resume_token(resume_token),
            status="open",
            recovery_lease_owner=None,
            recovery_lease_until=None,
            recovery_fencing_token=0,
            created_at=current_time,
            consumed_at=None,
        )
        self.db.add_all((slot_row, checkpoint_row))
        self.db.flush()
        pending = PendingContext(
            slot_ref=slot_id,
            checkpoint_ref=checkpoint_id,
            phase=PendingPhase.WAITING_INPUT,
            validation_attempt_ref=None,
            last_error_ref=None,
        )
        commit = self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.INFORMATION_REQUIRED,
                pending_context=pending,
            ),
            occurred_at=current_time,
        )
        return PendingSuspension(
            slot=self._slot_contract(slot_row),
            checkpoint=self._checkpoint_contract(checkpoint_row),
            state=commit.state,
        )

    def begin_slot_validation(
        self,
        *,
        task_id: str,
        event_id: str,
        candidate_message_id: str,
        candidate: Any,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ValidationAttempt:
        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        if current.status is not AgentTaskStatus.PENDING or current.pending_context is None:
            raise PureAgentConflict("task is not waiting for slot input")
        slot = self._slot(current.pending_context.slot_ref, lock=True)
        candidate_json = normalize_json(candidate)
        candidate_digest = canonical_hash(candidate_json)
        existing = (
            self.db.query(BidPureAgentSlotValidation)
            .filter(
                BidPureAgentSlotValidation.slot_id == slot.id,
                BidPureAgentSlotValidation.idempotency_key == idempotency_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if existing.candidate_hash != candidate_digest:
                raise PureAgentConflict("validation idempotency key was reused")
            return ValidationAttempt(
                attempt_id=existing.id,
                stage=existing.stage,
                state=current,
                replayed=True,
            )
        if current.pending_context.phase is not PendingPhase.WAITING_INPUT:
            raise PureAgentConflict("slot is already being validated")
        if slot.status != "unresolved":
            raise PureAgentConflict("slot is already resolved")
        message = (
            self.db.query(BidPureAgentMessage)
            .filter(BidPureAgentMessage.id == candidate_message_id)
            .one_or_none()
        )
        if message is None or message.conversation_id != task_row.conversation_id:
            raise PureAgentConflict("slot candidate message is outside the conversation")
        current_time = now or utc_now()
        attempt = BidPureAgentSlotValidation(
            id=_new_id(),
            task_id=task_id,
            slot_id=slot.id,
            candidate_message_id=candidate_message_id,
            idempotency_key=idempotency_key,
            stage="format_validation",
            status="running",
            candidate_json=candidate_json,
            candidate_hash=candidate_digest,
            issues_json=None,
            issues_hash=None,
            resolved_value_json=None,
            resolved_value_hash=None,
            created_at=current_time,
            completed_at=None,
        )
        slot.candidate_input_ref = candidate_message_id
        self.db.add(attempt)
        self.db.flush()
        pending = PendingContext(
            slot_ref=slot.id,
            checkpoint_ref=current.pending_context.checkpoint_ref,
            phase=PendingPhase.VALIDATING_FORMAT,
            validation_attempt_ref=attempt.id,
            last_error_ref=None,
        )
        commit = self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.SLOT_VALIDATION_STARTED,
                pending_context=pending,
            ),
            occurred_at=current_time,
        )
        return ValidationAttempt(
            attempt_id=attempt.id,
            stage=attempt.stage,
            state=commit.state,
            replayed=False,
        )

    def accept_slot_format(
        self,
        *,
        task_id: str,
        event_id: str,
        format_attempt_id: str,
        typed_value: Any,
        business_idempotency_key: str,
        now: datetime | None = None,
    ) -> ValidationAttempt:
        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        attempt = self._validation(format_attempt_id, lock=True)
        normalized = normalize_json(typed_value)
        value_digest = canonical_hash(normalized)
        existing_business = (
            self.db.query(BidPureAgentSlotValidation)
            .filter(
                BidPureAgentSlotValidation.slot_id == attempt.slot_id,
                BidPureAgentSlotValidation.idempotency_key
                == business_idempotency_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing_business is not None:
            if (
                existing_business.stage != "business_validation"
                or existing_business.candidate_hash != value_digest
            ):
                raise PureAgentConflict("business validation idempotency key was reused")
            return ValidationAttempt(
                attempt_id=existing_business.id,
                stage=existing_business.stage,
                state=current,
                replayed=True,
            )
        if (
            current.status is not AgentTaskStatus.PENDING
            or current.pending_context is None
            or attempt.task_id != task_id
            or attempt.id != current.pending_context.validation_attempt_ref
            or current.pending_context.phase is not PendingPhase.VALIDATING_FORMAT
            or attempt.stage != "format_validation"
            or attempt.status != "running"
        ):
            raise PureAgentConflict("format validation attempt is not active")
        current_time = now or utc_now()
        attempt.status = "passed"
        attempt.resolved_value_json = normalized
        attempt.resolved_value_hash = value_digest
        attempt.completed_at = current_time
        business = BidPureAgentSlotValidation(
            id=_new_id(),
            task_id=task_id,
            slot_id=attempt.slot_id,
            candidate_message_id=attempt.candidate_message_id,
            idempotency_key=business_idempotency_key,
            stage="business_validation",
            status="running",
            candidate_json=normalized,
            candidate_hash=value_digest,
            issues_json=None,
            issues_hash=None,
            resolved_value_json=None,
            resolved_value_hash=None,
            created_at=current_time,
            completed_at=None,
        )
        self.db.add(business)
        self.db.flush()
        pending = PendingContext(
            slot_ref=attempt.slot_id,
            checkpoint_ref=current.pending_context.checkpoint_ref,
            phase=PendingPhase.VALIDATING_BUSINESS,
            validation_attempt_ref=business.id,
            last_error_ref=None,
        )
        commit = self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.SLOT_FORMAT_ACCEPTED,
                pending_context=pending,
            ),
            occurred_at=current_time,
        )
        return ValidationAttempt(
            attempt_id=business.id,
            stage=business.stage,
            state=commit.state,
            replayed=False,
        )

    def reject_slot_validation(
        self,
        *,
        task_id: str,
        event_id: str,
        attempt_id: str,
        issues: Any,
        now: datetime | None = None,
    ) -> TransitionCommit:
        replay = self._transition_replay(
            event_id,
            task_id,
            TaskEventType.SLOT_VALIDATION_REJECTED,
        )
        if replay is not None:
            return replay
        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        attempt = self._validation(attempt_id, lock=True)
        if (
            current.status is not AgentTaskStatus.PENDING
            or current.pending_context is None
            or attempt.task_id != task_id
            or attempt.id != current.pending_context.validation_attempt_ref
            or attempt.status != "running"
        ):
            raise PureAgentConflict("validation attempt is not active")
        safe_issues = normalize_json(issues)
        current_time = now or utc_now()
        attempt.status = "failed"
        attempt.issues_json = safe_issues
        attempt.issues_hash = canonical_hash(safe_issues)
        attempt.completed_at = current_time
        error_ref = f"validation:{attempt.id}"
        pending = PendingContext(
            slot_ref=attempt.slot_id,
            checkpoint_ref=current.pending_context.checkpoint_ref,
            phase=PendingPhase.WAITING_INPUT,
            validation_attempt_ref=None,
            last_error_ref=error_ref,
        )
        return self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.SLOT_VALIDATION_REJECTED,
                pending_context=pending,
                error_ref=error_ref,
            ),
            occurred_at=current_time,
        )

    def resolve_slot_and_resume(
        self,
        *,
        task_id: str,
        event_id: str,
        business_attempt_id: str,
        resolved_value: Any,
        resume_token: str,
        recovery_fencing_token: int | None = None,
        now: datetime | None = None,
    ) -> TransitionCommit:
        replay = self._transition_replay(
            event_id,
            task_id,
            TaskEventType.SLOT_RESOLVED,
        )
        if replay is not None:
            return replay
        task_row = self._task(task_id, lock=True)
        current = self._task_state(task_row)
        attempt = self._validation(business_attempt_id, lock=True)
        if (
            current.status is not AgentTaskStatus.PENDING
            or current.pending_context is None
            or attempt.id != current.pending_context.validation_attempt_ref
            or attempt.task_id != task_id
            or attempt.stage != "business_validation"
            or attempt.status != "running"
        ):
            raise PureAgentConflict("business validation attempt is not active")
        slot = self._slot(attempt.slot_id, lock=True)
        checkpoint = self._checkpoint(current.pending_context.checkpoint_ref, lock=True)
        if (
            checkpoint.status != "open"
            or checkpoint.task_id != task_id
            or checkpoint.slot_id != slot.id
            or checkpoint.resume_token_hash != hash_resume_token(resume_token)
        ):
            raise PureAgentFenceRejected("checkpoint resume proof is invalid")
        persisted_recovery_token = int(checkpoint.recovery_fencing_token)
        if persisted_recovery_token > 0 and (
            recovery_fencing_token is None
            or persisted_recovery_token != int(recovery_fencing_token)
        ):
            raise PureAgentFenceRejected("recovery fencing token is required or stale")
        effect = self._effect_fence(checkpoint.effect_fence_id, lock=True)
        if effect.status in {"uncertain", "cancelled", "ignored_late"}:
            raise PureAgentFenceRejected("checkpoint effect fence is not recoverable")
        normalized = normalize_json(resolved_value)
        value_digest = canonical_hash(normalized)
        current_time = now or utc_now()
        attempt.status = "passed"
        attempt.resolved_value_json = normalized
        attempt.resolved_value_hash = value_digest
        attempt.completed_at = current_time
        resolved_value_ref = f"slot-value:{attempt.id}"
        slot.status = "resolved"
        slot.resolved_value_ref = resolved_value_ref
        slot.resolved_at = current_time
        checkpoint.status = "consumed"
        checkpoint.consumed_at = current_time
        checkpoint.recovery_lease_owner = None
        checkpoint.recovery_lease_until = None
        proof = ResumeProof(
            slot_ref=slot.id,
            checkpoint_ref=checkpoint.id,
            resolved_value_ref=resolved_value_ref,
            resume_token_verified=True,
            effect_fence_verified=True,
            checkpoint_consumed=True,
        )
        return self.commit_transition(
            self._event(
                event_id=event_id,
                task=current,
                event_type=TaskEventType.SLOT_RESOLVED,
                resume_proof=proof,
            ),
            occurred_at=current_time,
        )

    def claim_pending_recovery(
        self,
        *,
        task_id: str,
        lease_owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> RecoveryClaim:
        if not str(lease_owner).strip():
            raise PureAgentConflict("checkpoint recovery lease owner is required")
        task = self._task(task_id, lock=True)
        state = self._task_state(task)
        if state.status is not AgentTaskStatus.PENDING or state.pending_context is None:
            raise PureAgentConflict("only pending task can be claimed for recovery")
        checkpoint = self._checkpoint(state.pending_context.checkpoint_ref, lock=True)
        if checkpoint.status != "open":
            raise PureAgentFenceRejected("continuation checkpoint is not open")
        current_time = now or utc_now()
        lease_until = checkpoint.recovery_lease_until
        if lease_until is not None and lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        if (
            checkpoint.recovery_lease_owner is not None
            and lease_until is not None
            and lease_until > current_time
            and checkpoint.recovery_lease_owner != lease_owner
        ):
            raise PureAgentConflict("checkpoint recovery lease is held")
        checkpoint.recovery_lease_owner = lease_owner
        checkpoint.recovery_lease_until = current_time + timedelta(
            seconds=max(5, int(lease_seconds))
        )
        checkpoint.recovery_fencing_token = int(checkpoint.recovery_fencing_token) + 1
        self.db.flush()
        return RecoveryClaim(
            checkpoint=self._checkpoint_contract(checkpoint),
            lease_owner=lease_owner,
            lease_until=checkpoint.recovery_lease_until,
            fencing_token=int(checkpoint.recovery_fencing_token),
        )

    def assess_pending_recovery(self, *, task_id: str) -> RecoveryAssessment:
        """Choose resume/reconcile/blocked without executing or replaying an effect."""

        task = self._task(task_id, lock=True)
        state = self._task_state(task)
        if state.status is not AgentTaskStatus.PENDING or state.pending_context is None:
            raise PureAgentConflict("only pending task has a continuation checkpoint")
        checkpoint = self._checkpoint(state.pending_context.checkpoint_ref, lock=True)
        if checkpoint.status != "open":
            raise PureAgentFenceRejected("continuation checkpoint is not open")
        effect = self._effect_fence(checkpoint.effect_fence_id, lock=True)
        if effect.status in {"cancelled", "ignored_late"}:
            decision = "blocked"
            reason = "effect was cancelled or fenced as a late result"
        elif effect.status == "uncertain" or (
            effect.replay_policy == "reconcile_required"
            and effect.status not in {"succeeded", "failed"}
        ):
            decision = "reconcile"
            reason = "effect outcome must be reconciled before continuation"
        elif effect.replay_policy == "no_replay" and effect.status in {
            "reserved",
            "running",
        }:
            decision = "blocked"
            reason = "non-replayable effect has no accepted terminal result"
        else:
            decision = "resume"
            reason = "checkpoint and effect fence permit continuation"
        return RecoveryAssessment(
            checkpoint=self._checkpoint_contract(checkpoint),
            effect_status=effect.status,
            replay_policy=effect.replay_policy,
            decision=decision,
            reason=reason,
        )

    def _budget_mutation(
        self,
        *,
        task_id: str,
        resource_type: str,
        amount: int,
        idempotency_key: str,
        entry_kind: str,
        action_id: str | None,
        reservation_ref: str | None,
        reserved_release: int = 0,
        now: datetime | None,
    ) -> BudgetMutation:
        if int(amount) < 0:
            raise PureAgentBudgetExceeded("budget amount cannot be negative")
        task = self._task(task_id, lock=True)
        if entry_kind == "reserve" and (
            task.status != AgentTaskStatus.RUNNING.value
            or task.cancellation_fence_id is not None
        ):
            raise PureAgentBudgetExceeded("only an uncancelled running task may reserve")
        account = self._budget_account(task_id, resource_type, lock=True)
        existing = (
            self.db.query(BidPureAgentBudgetEntry)
            .filter(
                BidPureAgentBudgetEntry.account_id == account.id,
                BidPureAgentBudgetEntry.idempotency_key == idempotency_key,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.entry_kind != entry_kind
                or int(existing.amount) != int(amount)
                or existing.action_id != action_id
                or existing.reservation_ref != reservation_ref
            ):
                raise PureAgentConflict("budget idempotency key was reused")
            return BudgetMutation(
                entry_id=existing.id,
                resource_type=resource_type,
                reserved_after=int(existing.reserved_after),
                actual_after=int(existing.actual_after),
                replayed=True,
            )
        if action_id is not None:
            action = self._action(action_id, lock=True)
            if action.task_id != task_id:
                raise PureAgentConflict("budget action is outside the task")
        reserved = int(account.reserved_amount)
        actual = int(account.actual_amount)
        if entry_kind == "reserve":
            if reserved + actual + int(amount) > int(account.limit_amount):
                raise PureAgentBudgetExceeded("budget reservation exceeds limit")
            reserved += int(amount)
        elif entry_kind in {"settle", "release"}:
            if int(reserved_release) > reserved:
                raise PureAgentBudgetExceeded("budget reservation is no longer available")
            reserved -= int(reserved_release)
            actual += int(amount)
            if reserved + actual > int(account.limit_amount):
                raise PureAgentBudgetExceeded("budget settlement exceeds limit")
        else:
            if reserved + actual + int(amount) > int(account.limit_amount):
                raise PureAgentBudgetExceeded("budget charge exceeds limit")
            actual += int(amount)
        current_time = now or utc_now()
        account.reserved_amount = reserved
        account.actual_amount = actual
        account.row_version = int(account.row_version) + 1
        account.updated_at = current_time
        entry = BidPureAgentBudgetEntry(
            id=_new_id(),
            task_id=task_id,
            account_id=account.id,
            action_id=action_id,
            entry_kind=entry_kind,
            amount=int(amount),
            idempotency_key=idempotency_key,
            reservation_ref=reservation_ref,
            reserved_after=reserved,
            actual_after=actual,
            created_at=current_time,
        )
        self.db.add(entry)
        self.db.flush()
        return BudgetMutation(
            entry_id=entry.id,
            resource_type=resource_type,
            reserved_after=reserved,
            actual_after=actual,
            replayed=False,
        )

    def _user_message_admission_replay(
        self,
        message: BidPureAgentMessage,
        *,
        owner_id: int,
        user_input: Any,
        created_by_ref: str,
        reply_to_message_id: str | None,
    ) -> UserMessageAdmission:
        content = normalize_json(message.content_json)
        if (
            message.role != "user"
            or message.created_by_ref != created_by_ref
            or message.reply_to_message_id != reply_to_message_id
            or not isinstance(content, dict)
            or content.get("schema_name") != "bid.user-message.internal.v1"
            or normalize_json(content.get("input")) != normalize_json(user_input)
        ):
            raise PureAgentConflict("message idempotency key was reused")
        disposition = content.get("disposition")
        if disposition == "task_trigger":
            if message.message_type != "user.task_trigger":
                raise PureAgentConflict("message admission receipt is inconsistent")
            task_row = (
                self.db.query(BidPureAgentTask)
                .filter(
                    BidPureAgentTask.conversation_id == message.conversation_id,
                    BidPureAgentTask.trigger_message_id == message.id,
                )
                .one_or_none()
            )
        elif disposition == "steering_candidate":
            if message.message_type != "user.steering_candidate":
                raise PureAgentConflict("message admission receipt is inconsistent")
            target_task_ref = content.get("target_task_ref")
            task_row = (
                self.db.query(BidPureAgentTask)
                .filter(BidPureAgentTask.id == target_task_ref)
                .one_or_none()
            )
        else:
            raise PureAgentConflict("message admission receipt is invalid")
        if (
            task_row is None
            or task_row.conversation_id != message.conversation_id
            or int(task_row.owner_id) != int(owner_id)
        ):
            raise PureAgentConflict("message admission task is unavailable")
        return UserMessageAdmission(
            message=message,
            task=self._task_state(task_row),
            disposition=disposition,
            replayed=True,
        )

    @staticmethod
    def _user_turn_targets_task(
        message: BidPureAgentMessage,
        *,
        content: Any,
        task_id: str,
        trigger_message_id: str | None = None,
    ) -> bool:
        if (
            message.role != "user"
            or not isinstance(content, dict)
            or message.content_hash != canonical_hash(content)
        ):
            return False
        if message.message_type == "user.task_trigger":
            return bool(
                trigger_message_id
                and message.id == trigger_message_id
                and content.get("schema_name") == "bid.user-message.internal.v1"
                and content.get("disposition") == "task_trigger"
                and content.get("target_task_ref") is None
                and isinstance(content.get("input"), dict)
            )
        if message.message_type == "user.steering_candidate":
            return bool(
                content.get("schema_name") == "bid.user-message.internal.v1"
                and content.get("disposition") == "steering_candidate"
                and content.get("target_task_ref") == task_id
                and isinstance(content.get("input"), dict)
            )
        if message.message_type == "user.slot_candidate":
            return bool(
                content.get("schema_name") == "bid.slot-input.internal.v1"
                and content.get("task_ref") == task_id
                and content.get("slot_ref")
                and "candidate" in content
            )
        return False

    def _event(
        self,
        *,
        event_id: str,
        task: AgentTaskState,
        event_type: TaskEventType,
        effect_idempotency_key: str | None = None,
        action_ref: str | None = None,
        pending_context: PendingContext | None = None,
        resume_proof: ResumeProof | None = None,
        execution_mode: Any | None = None,
        plan_ref: str | None = None,
        observation_ref: str | None = None,
        result_committed: bool = False,
        error_ref: str | None = None,
        cancellation_fence_ref: str | None = None,
    ) -> TaskTransitionEvent:
        return TaskTransitionEvent(
            event_id=event_id,
            task_id=task.task_id,
            expected_state_version=task.state_version,
            event_type=event_type,
            effect_idempotency_key=effect_idempotency_key,
            action_ref=action_ref,
            pending_context=pending_context,
            resume_proof=resume_proof,
            execution_mode=execution_mode,
            plan_ref=plan_ref,
            observation_ref=observation_ref,
            result_committed=result_committed,
            error_ref=error_ref,
            cancellation_fence_ref=cancellation_fence_ref,
        )

    def _transition_replay(
        self,
        event_id: str,
        task_id: str,
        event_type: TaskEventType,
    ) -> TransitionCommit | None:
        row = (
            self.db.query(BidPureAgentEvent)
            .filter(BidPureAgentEvent.event_id == event_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return None
        if row.task_id != task_id or row.event_type != event_type.value:
            raise PureAgentConflict("event id was reused for another transition")
        return TransitionCommit(
            state=AgentTaskState.model_validate(row.state_after_json),
            event_id=event_id,
            replayed=True,
        )

    def _task_values_from_state(
        self,
        row: BidPureAgentTask,
        state: AgentTaskState,
        *,
        event: TaskTransitionEvent,
        now: datetime,
    ) -> dict[str, Any]:
        pending = state.pending_context
        terminal_at = now if state.status in TERMINAL_STATUSES else None
        cancellation_fence_id = row.cancellation_fence_id
        if event.event_type is TaskEventType.CANCEL_REQUESTED:
            cancellation_fence_id = event.cancellation_fence_ref
        return {
            "status": state.status.value,
            "execution_mode": state.execution_mode.value,
            "state_version": state.state_version,
            "row_version": int(row.row_version) + 1,
            "plan_ref": state.plan_ref,
            "active_slot_id": pending.slot_ref if pending else None,
            "active_checkpoint_id": pending.checkpoint_ref if pending else None,
            "pending_phase": pending.phase.value if pending else None,
            "validation_attempt_id": pending.validation_attempt_ref if pending else None,
            "in_flight_action_id": state.in_flight_action_ref,
            "observation_refs_json": list(state.observation_refs),
            "last_error_ref": state.last_error_ref,
            "cancellation_fence_id": cancellation_fence_id,
            "terminal_at": terminal_at,
            "updated_at": now,
        }

    @staticmethod
    def _context_message_row(
        row: BidPureAgentMessage,
        *,
        content: Any,
    ) -> ContextMessageRow:
        return ContextMessageRow(
            message_ref=row.id,
            conversation_ref=row.conversation_id,
            sequence_no=int(row.sequence_no),
            role=row.role,
            message_type=row.message_type,
            content=content,
            content_hash=row.content_hash,
            reply_to_message_ref=row.reply_to_message_id,
        )

    def _observation_artifact_row(
        self,
        row: BidPureAgentObservationArtifact,
    ) -> PersistedObservationArtifactRow:
        try:
            observation = ActionObservation.model_validate(row.observation_json)
            artifact = normalize_json(row.artifact_json)
        except (TypeError, ValueError) as exc:
            raise PureAgentConflict(
                "persisted Observation Artifact is invalid"
            ) from exc
        if (
            observation.observation_ref != row.id
            or observation.task_ref != row.task_id
            or observation.source_action_ref != row.action_id
            or observation.state_version != int(row.state_version)
            or observation.action_sequence != int(row.action_sequence)
            or observation.kind.value != row.kind
            or observation.status.value != row.status
            or observation.artifact_ref != row.artifact_ref
            or not self._digest_matches(
                row.observation_hash,
                observation.observation_hash,
            )
            or not self._digest_matches(row.artifact_hash, observation.artifact_hash)
            or not self._digest_matches(row.artifact_hash, canonical_hash(artifact))
        ):
            raise PureAgentFenceRejected(
                "persisted Observation Artifact receipt drifted"
            )
        return PersistedObservationArtifactRow(
            observation=observation,
            artifact=artifact,
            context_snapshot_ref=row.context_snapshot_id,
        )

    @staticmethod
    def _tool_call_proposals(
        action: BidPureAgentAction,
    ) -> tuple[dict[str, Any], ...]:
        payload = normalize_json(action.arguments_json)
        if (
            action.action_type != AgentActionKind.TOOL_CALL_BATCH.value
            or not isinstance(payload, dict)
            or payload.get("schema_name") != "bid.pure-agent.action-envelope.v1"
            or not isinstance(payload.get("intent"), dict)
            or canonical_hash(payload) != action.arguments_hash
        ):
            return ()
        envelope_hash = payload.get("envelope_hash")
        envelope_body = dict(payload)
        envelope_body.pop("envelope_hash", None)
        if (
            not isinstance(envelope_hash, str)
            or envelope_hash.removeprefix("sha256:")
            != canonical_hash(envelope_body)
        ):
            return ()
        try:
            intent = ActionReservationIntent.model_validate(payload["intent"])
            batch = ToolCallBatchAction.model_validate(intent.arguments)
        except (TypeError, ValueError):
            return ()
        if intent.action_kind is not AgentActionKind.TOOL_CALL_BATCH:
            return ()
        return tuple(call.model_dump(mode="json") for call in batch.calls)

    @staticmethod
    def _tool_result_calls(artifact: Any) -> tuple[dict[str, Any], ...]:
        if (
            not isinstance(artifact, dict)
            or artifact.get("schema_name")
            != "bid.pure-agent.capability.tool-batch-result.v1"
            or not isinstance(artifact.get("calls"), list)
        ):
            return ()
        calls = tuple(artifact["calls"])
        if not all(isinstance(item, dict) for item in calls):
            return ()
        return calls

    @staticmethod
    def _tool_message_matches(
        message: Any,
        *,
        row: BidPureAgentCall,
        output: dict[str, Any],
    ) -> bool:
        if not isinstance(message, dict):
            return False
        content = canonical_json(output)
        return bool(
            message.get("tool_call_id") == row.provider_tool_call_id
            and message.get("name") == row.operation_name
            and message.get("content") == content
            and message.get("content_hash") == f"sha256:{row.output_hash}"
        )

    def _task_state(self, row: BidPureAgentTask) -> AgentTaskState:
        pending = None
        if row.status == AgentTaskStatus.PENDING.value:
            pending = PendingContext(
                slot_ref=row.active_slot_id,
                checkpoint_ref=row.active_checkpoint_id,
                phase=PendingPhase(row.pending_phase),
                validation_attempt_ref=row.validation_attempt_id,
                last_error_ref=row.last_error_ref,
            )
        return AgentTaskState(
            task_id=row.id,
            session_id=row.conversation_id,
            state_version=int(row.state_version),
            status=AgentTaskStatus(row.status),
            execution_mode=row.execution_mode,
            goal_ref=row.goal_ref,
            plan_ref=row.plan_ref,
            pending_context=pending,
            in_flight_action_ref=row.in_flight_action_id,
            observation_refs=tuple(row.observation_refs_json or []),
            last_error_ref=row.last_error_ref,
        )

    @staticmethod
    def _storage_digest(value: str) -> str:
        return value.removeprefix("sha256:")

    @staticmethod
    def _contract_digest(value: str) -> str:
        candidate = str(value)
        return candidate if candidate.startswith("sha256:") else f"sha256:{candidate}"

    @classmethod
    def _digest_matches(cls, stored: str, expected: str) -> bool:
        return cls._storage_digest(str(stored)) == cls._storage_digest(str(expected))

    @staticmethod
    def _response_envelope(row: BidPureAgentResponse) -> ResponsePersistenceEnvelope:
        try:
            envelope = ResponsePersistenceEnvelope.model_validate(row.draft_json)
        except (TypeError, ValueError) as exc:
            raise PureAgentConflict("response persistence envelope is invalid") from exc
        if envelope.current_status.value != row.status:
            raise PureAgentConflict("response row status differs from lifecycle envelope")
        if not PureAgentRepository._digest_matches(
            row.draft_hash,
            envelope.artifact.draft_hash,
        ):
            raise PureAgentConflict("response row Draft hash differs from its artifact")
        return envelope

    def _slot_contract(self, row: BidPureAgentSlot) -> Slot:
        return Slot(
            slot_id=row.id,
            task_id=row.task_id,
            name=row.name,
            request_message=row.request_message,
            input_model_ref=row.input_model_ref,
            business_validator_refs=tuple(row.business_validator_refs_json or []),
            status=SlotStatus(row.status),
            candidate_input_ref=row.candidate_input_ref,
            resolved_value_ref=row.resolved_value_ref,
        )

    def _checkpoint_contract(self, row: BidPureAgentCheckpoint) -> ContinuationCheckpoint:
        return ContinuationCheckpoint(
            checkpoint_id=row.id,
            task_id=row.task_id,
            slot_ref=row.slot_id,
            suspended_state_version=int(row.suspended_state_version),
            execution_mode=row.execution_mode,
            context_snapshot_ref=row.context_snapshot_ref,
            suspended_action_ref=row.suspended_action_id,
            effect_fence_ref=row.effect_fence_id,
            resume_token_hash=row.resume_token_hash,
            status=CheckpointStatus(row.status),
        )

    def _conversation(self, conversation_id: str, *, lock: bool) -> BidPureAgentConversation:
        query = self.db.query(BidPureAgentConversation).filter(
            BidPureAgentConversation.id == conversation_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("conversation was not found")
        return row

    def _task(self, task_id: str, *, lock: bool) -> BidPureAgentTask:
        query = self.db.query(BidPureAgentTask).filter(BidPureAgentTask.id == task_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("task was not found")
        return row

    def _action(self, action_id: str, *, lock: bool) -> BidPureAgentAction:
        query = self.db.query(BidPureAgentAction).filter(BidPureAgentAction.id == action_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("action was not found")
        return row

    def _response(self, response_id: str, *, lock: bool) -> BidPureAgentResponse:
        query = self.db.query(BidPureAgentResponse).filter(
            BidPureAgentResponse.id == response_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("response was not found")
        return row

    def _slot(self, slot_id: str, *, lock: bool) -> BidPureAgentSlot:
        query = self.db.query(BidPureAgentSlot).filter(BidPureAgentSlot.id == slot_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("slot was not found")
        return row

    def _checkpoint(self, checkpoint_id: str, *, lock: bool) -> BidPureAgentCheckpoint:
        query = self.db.query(BidPureAgentCheckpoint).filter(
            BidPureAgentCheckpoint.id == checkpoint_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("checkpoint was not found")
        return row

    def _validation(self, attempt_id: str, *, lock: bool) -> BidPureAgentSlotValidation:
        query = self.db.query(BidPureAgentSlotValidation).filter(
            BidPureAgentSlotValidation.id == attempt_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("slot validation attempt was not found")
        return row

    def _effect_fence(self, fence_id: str, *, lock: bool) -> BidPureAgentEffectFence:
        query = self.db.query(BidPureAgentEffectFence).filter(
            BidPureAgentEffectFence.id == fence_id
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("effect fence was not found")
        return row

    def _budget_account(
        self,
        task_id: str,
        resource_type: str,
        *,
        lock: bool,
    ) -> BidPureAgentBudgetAccount:
        query = self.db.query(BidPureAgentBudgetAccount).filter(
            BidPureAgentBudgetAccount.task_id == task_id,
            BidPureAgentBudgetAccount.resource_type == resource_type,
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise PureAgentNotFound("budget account was not found")
        return row

    @staticmethod
    def _assert_fencing_token(
        fence: BidPureAgentEffectFence,
        fencing_token: int,
    ) -> None:
        if int(fence.fencing_token) != int(fencing_token):
            raise PureAgentFenceRejected("effect fencing token is stale")
