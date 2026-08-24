"""Governed four-scope Memory foundation for B04-2.

Memory is derived, untrusted Context assistance.  It is never an evidence
authority, a Checkpoint, a generic model-callable tool, or a vector index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Protocol, Union
import uuid

from pydantic import Field, field_validator, model_validator

from .common import Reference, StrictContract
from .runtime import ContextRepresentation, MemoryScope
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import canonical_hash, canonical_json


_MEMORY_NAMESPACE = uuid.UUID("4cbba54d-ce85-465c-a7a0-c3c19cb907de")

ShortMemoryText = Annotated[str, Field(min_length=1, max_length=500)]
MemoryItemText = Annotated[str, Field(min_length=1, max_length=1000)]
RelevanceKey = Annotated[str, Field(min_length=1, max_length=200)]


class MemoryKind(str, Enum):
    WORKING_STATE = "working_state"
    CONVERSATION_STATE = "conversation_state"
    CONVERSATION_SUMMARY = "conversation_summary"
    PROJECT_GROUNDING = "project_grounding"
    OPEN_FOLLOW_UP = "open_follow_up"
    USER_PREFERENCE = "user_preference"


class MemoryBasis(str, Enum):
    EXPLICIT_USER = "explicit_user"
    VALIDATED_SYSTEM = "validated_system"
    GROUNDED_EVIDENCE = "grounded_evidence"
    DERIVED_SUMMARY = "derived_summary"


class MemoryGroundingStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class MemoryValidity(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    STALE = "stale"
    CONFLICTED = "conflicted"
    REVOKED = "revoked"
    DELETED = "deleted"


class MemoryPolicyCode(str, Enum):
    ALLOWED = "ALLOWED"
    WRITES_DISABLED = "WRITES_DISABLED"
    TASK_NOT_RUNNING = "TASK_NOT_RUNNING"
    AUTHORIZATION_MISMATCH = "AUTHORIZATION_MISMATCH"
    SCOPE_DENIED = "SCOPE_DENIED"
    CONTENT_POLICY_MISSING = "CONTENT_POLICY_MISSING"
    SOURCE_VALIDATION_MISSING = "SOURCE_VALIDATION_MISSING"
    USER_CONFIRMATION_REQUIRED = "USER_CONFIRMATION_REQUIRED"
    SOURCE_REQUIRED = "SOURCE_REQUIRED"
    GROUNDING_REQUIRED = "GROUNDING_REQUIRED"
    BASIS_REJECTED = "BASIS_REJECTED"
    REVISION_CONFLICT = "REVISION_CONFLICT"


class MemoryRuntimeError(RuntimeError):
    """Safe base error for Memory boundary failures."""


class MemoryRepositoryUnavailable(MemoryRuntimeError):
    """No Memory repository is configured."""


class MemoryPolicyRejected(MemoryRuntimeError):
    """Memory candidate or mutation failed deterministic policy."""


class MemoryConflict(MemoryRuntimeError):
    """Memory optimistic version or stable-key guard failed."""


class SummaryItem(StrictContract):
    text: str = Field(min_length=1, max_length=1000)
    source_message_refs: tuple[Reference, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_sources(self) -> "SummaryItem":
        if len(self.source_message_refs) != len(set(self.source_message_refs)):
            raise ValueError("summary source message refs must be unique")
        return self


class WorkingMemoryPayload(StrictContract):
    payload_type: Literal["working_state"] = "working_state"
    goal_ref: Reference
    understanding_ref: Reference | None
    plan_ref: Reference | None
    action_ref: Reference | None
    slot_ref: Reference | None
    observation_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=500)
    open_items: tuple[MemoryItemText, ...] = Field(default_factory=tuple, max_length=64)
    limitations: tuple[ShortMemoryText, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator("observation_refs")
    @classmethod
    def validate_unique_observations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("working observation refs must be unique")
        return value


class ConversationStatePayload(StrictContract):
    payload_type: Literal["conversation_state"] = "conversation_state"
    topic: str = Field(min_length=1, max_length=500)
    message_refs: tuple[Reference, ...] = Field(min_length=1, max_length=500)
    correction_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=100)
    unresolved_items: tuple[MemoryItemText, ...] = Field(default_factory=tuple, max_length=64)
    resource_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)

    @field_validator("message_refs", "correction_refs", "resource_refs")
    @classmethod
    def validate_unique_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("conversation state refs must be unique")
        return value


class ConversationSummaryPayload(StrictContract):
    payload_type: Literal["conversation_summary"] = "conversation_summary"
    covered_message_refs: tuple[Reference, ...] = Field(min_length=1, max_length=500)
    source_range_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    valid_through_message_ref: Reference
    goals_and_constraints: tuple[SummaryItem, ...] = Field(default_factory=tuple, max_length=64)
    user_decisions_and_corrections: tuple[SummaryItem, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    unresolved_items: tuple[SummaryItem, ...] = Field(default_factory=tuple, max_length=64)
    resource_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    grounded_outcome_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    limitations: tuple[ShortMemoryText, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator(
        "covered_message_refs",
        "resource_refs",
        "grounded_outcome_refs",
    )
    @classmethod
    def validate_unique_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("conversation summary refs must be unique")
        return value


class ProjectGroundingPayload(StrictContract):
    payload_type: Literal["project_grounding"] = "project_grounding"
    subject: str = Field(min_length=1, max_length=500)
    outcome_ref: Reference | None
    grounding_refs: tuple[Reference, ...] = Field(min_length=1, max_length=128)
    unresolved_risks: tuple[MemoryItemText, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator("grounding_refs")
    @classmethod
    def validate_unique_grounding_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("project grounding refs must be unique")
        return value


class OpenFollowUpPayload(StrictContract):
    payload_type: Literal["open_follow_up"] = "open_follow_up"
    summary: str = Field(min_length=1, max_length=1000)
    related_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)

    @field_validator("related_refs")
    @classmethod
    def validate_unique_related_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("follow-up refs must be unique")
        return value


class UserPreferencePayload(StrictContract):
    payload_type: Literal["user_preference"] = "user_preference"
    preference_key: str = Field(min_length=1, max_length=120)
    preference_value: str = Field(min_length=1, max_length=1000)
    explicit_confirmation_ref: Reference


MemoryPayload = Annotated[
    Union[
        WorkingMemoryPayload,
        ConversationStatePayload,
        ConversationSummaryPayload,
        ProjectGroundingPayload,
        OpenFollowUpPayload,
        UserPreferencePayload,
    ],
    Field(discriminator="payload_type"),
]


class MemoryScopeBinding(StrictContract):
    scope: MemoryScope
    tenant_ref: Reference
    task_ref: Reference | None = None
    conversation_ref: Reference | None = None
    project_ref: Reference | None = None
    assessment_ref: Reference | None = None
    user_ref: Reference | None = None

    @property
    def scope_key(self) -> str:
        scoped_ref = {
            MemoryScope.WORKING: self.task_ref,
            MemoryScope.CONVERSATION: self.conversation_ref,
            MemoryScope.PROJECT_ASSESSMENT: self.project_ref or self.assessment_ref,
            MemoryScope.USER: self.user_ref,
        }[self.scope]
        return f"{self.tenant_ref}:{self.scope.value}:{scoped_ref}"

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryScopeBinding":
        if self.scope is MemoryScope.WORKING:
            if self.task_ref is None:
                raise ValueError("working memory requires task_ref")
            forbidden = (
                self.conversation_ref,
                self.project_ref,
                self.assessment_ref,
                self.user_ref,
            )
        elif self.scope is MemoryScope.CONVERSATION:
            if self.conversation_ref is None:
                raise ValueError("conversation memory requires conversation_ref")
            forbidden = (
                self.task_ref,
                self.project_ref,
                self.assessment_ref,
                self.user_ref,
            )
        elif self.scope is MemoryScope.PROJECT_ASSESSMENT:
            if (self.project_ref is None) == (self.assessment_ref is None):
                raise ValueError("project memory requires exactly one business scope ref")
            forbidden = (self.task_ref, self.conversation_ref, self.user_ref)
        else:
            if self.user_ref is None:
                raise ValueError("user memory requires user_ref")
            forbidden = (
                self.task_ref,
                self.conversation_ref,
                self.project_ref,
                self.assessment_ref,
            )
        if any(value is not None for value in forbidden):
            raise ValueError("memory scope contains incompatible references")
        return self


class MemorySourceDependency(StrictContract):
    source_ref: Reference
    source_version_ref: Reference
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority_label: str = Field(min_length=1, max_length=80)
    validation_decision_ref: Reference


class MemoryAuthorizationContext(StrictContract):
    authorization_snapshot_ref: Reference
    tenant_ref: Reference
    user_ref: Reference
    memory_reads_enabled: bool = False
    memory_writes_enabled: bool = False
    can_manage_user_memory: bool = False
    allowed_task_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    allowed_conversation_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    allowed_project_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    allowed_assessment_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    approved_content_policy_decision_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=256,
    )
    approved_source_validation_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=512,
    )

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "MemoryAuthorizationContext":
        for field_name in (
            "allowed_task_refs",
            "allowed_conversation_refs",
            "allowed_project_refs",
            "allowed_assessment_refs",
            "approved_content_policy_decision_refs",
            "approved_source_validation_refs",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class MemoryCandidate(StrictContract):
    candidate_ref: Reference
    stable_key: str = Field(min_length=1, max_length=200)
    kind: MemoryKind
    scope: MemoryScopeBinding
    payload: MemoryPayload
    source_dependencies: tuple[MemorySourceDependency, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    basis: MemoryBasis
    grounding_status: MemoryGroundingStatus | None
    expires_at: datetime | None
    authorization_snapshot_ref: Reference
    content_policy_decision_ref: Reference
    policy_ref: Reference
    expected_previous_ref: Reference | None

    @model_validator(mode="after")
    def validate_candidate(self) -> "MemoryCandidate":
        expected = {
            MemoryKind.WORKING_STATE: "working_state",
            MemoryKind.CONVERSATION_STATE: "conversation_state",
            MemoryKind.CONVERSATION_SUMMARY: "conversation_summary",
            MemoryKind.PROJECT_GROUNDING: "project_grounding",
            MemoryKind.OPEN_FOLLOW_UP: "open_follow_up",
            MemoryKind.USER_PREFERENCE: "user_preference",
        }[self.kind]
        if self.payload.payload_type != expected:
            raise ValueError("memory kind does not match payload type")
        allowed_scopes = {
            MemoryKind.WORKING_STATE: {MemoryScope.WORKING},
            MemoryKind.CONVERSATION_STATE: {MemoryScope.CONVERSATION},
            MemoryKind.CONVERSATION_SUMMARY: {MemoryScope.CONVERSATION},
            MemoryKind.PROJECT_GROUNDING: {MemoryScope.PROJECT_ASSESSMENT},
            MemoryKind.OPEN_FOLLOW_UP: {
                MemoryScope.CONVERSATION,
                MemoryScope.PROJECT_ASSESSMENT,
            },
            MemoryKind.USER_PREFERENCE: {MemoryScope.USER},
        }[self.kind]
        if self.scope.scope not in allowed_scopes:
            raise ValueError("memory kind is incompatible with scope")
        dependency_keys = {
            (item.source_ref, item.source_version_ref) for item in self.source_dependencies
        }
        if len(dependency_keys) != len(self.source_dependencies):
            raise ValueError("memory source dependencies must be unique")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("memory expiry must be timezone-aware")
        return self


class MemoryRecord(StrictContract):
    memory_ref: Reference
    candidate_ref: Reference
    stable_key: str = Field(min_length=1, max_length=200)
    kind: MemoryKind
    scope: MemoryScopeBinding
    payload: MemoryPayload
    source_dependencies: tuple[MemorySourceDependency, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    basis: MemoryBasis
    grounding_status: MemoryGroundingStatus | None
    validity: MemoryValidity
    version: int = Field(ge=1)
    supersedes_ref: Reference | None
    created_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_ref: Reference
    authorization_snapshot_ref: Reference

    @model_validator(mode="after")
    def validate_timestamps(self) -> "MemoryRecord":
        timestamps = (self.created_at, self.effective_at, self.expires_at)
        if any(value is not None and value.tzinfo is None for value in timestamps):
            raise ValueError("memory timestamps must be timezone-aware")
        if (self.version == 1) != (self.supersedes_ref is None):
            raise ValueError("memory version and supersedes_ref are inconsistent")
        return self


class MemoryPolicyDecision(StrictContract):
    allowed: bool
    code: MemoryPolicyCode
    message: str = Field(min_length=1, max_length=500)


class MemoryCommitOutcome(StrictContract):
    record: MemoryRecord
    created: bool
    replayed: bool


class MemorySourceHead(StrictContract):
    source_ref: Reference
    source_version_ref: Reference
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MemoryReadRequest(StrictContract):
    task_ref: Reference
    query: str = Field(min_length=1, max_length=1000)
    scope_bindings: tuple[MemoryScopeBinding, ...] = Field(min_length=1, max_length=4)
    authorization: MemoryAuthorizationContext
    relevance_keys: tuple[RelevanceKey, ...] = Field(default_factory=tuple, max_length=64)
    relevant_source_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    source_heads: tuple[MemorySourceHead, ...] = Field(default_factory=tuple, max_length=512)
    include_uncertain: bool = False
    max_entries: int = Field(default=24, ge=1, le=200)
    max_projection_chars: int = Field(default=16_000, ge=512, le=131_072)

    @model_validator(mode="after")
    def validate_request(self) -> "MemoryReadRequest":
        scope_keys = [binding.scope_key for binding in self.scope_bindings]
        if len(scope_keys) != len(set(scope_keys)):
            raise ValueError("memory scope bindings must be unique")
        if len(self.relevance_keys) != len(set(self.relevance_keys)):
            raise ValueError("memory relevance keys must be unique")
        if len(self.relevant_source_refs) != len(set(self.relevant_source_refs)):
            raise ValueError("relevant source refs must be unique")
        head_refs = [head.source_ref for head in self.source_heads]
        if len(head_refs) != len(set(head_refs)):
            raise ValueError("memory source heads must be unique")
        return self


class MemoryProjectionEntry(StrictContract):
    memory_ref: Reference
    stable_key: str = Field(min_length=1, max_length=200)
    kind: MemoryKind
    scope: MemoryScope
    basis: MemoryBasis
    grounding_status: MemoryGroundingStatus | None
    validity: MemoryValidity
    authority_label: str = Field(min_length=1, max_length=80)
    representation: ContextRepresentation
    display_text: str = Field(min_length=1, max_length=8000)
    source_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    untrusted_data: Literal[True] = True


class MemoryProjection(StrictContract):
    entries: tuple[MemoryProjectionEntry, ...] = Field(default_factory=tuple, max_length=200)
    entry_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=200)
    source_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=500)
    memory_version_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=200)
    limitation_messages: tuple[ShortMemoryText, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_projection(self) -> "MemoryProjection":
        if self.entry_refs != tuple(entry.memory_ref for entry in self.entries):
            raise ValueError("memory entry refs must match projection entries")
        projected_sources = tuple(
            dict.fromkeys(ref for entry in self.entries for ref in entry.source_refs)
        )
        if self.source_refs != projected_sources:
            raise ValueError("memory source refs must match projection entries")
        if self.memory_version_refs != self.entry_refs:
            raise ValueError("memory version refs must match current projection versions")
        return self


class MemoryRepository(Protocol):
    async def find_by_candidate(
        self,
        candidate_ref: str,
        *,
        scope_key: str,
    ) -> MemoryRecord | None: ...

    async def get(
        self,
        memory_ref: str,
        *,
        allowed_scope_keys: tuple[str, ...],
    ) -> MemoryRecord: ...

    async def current(self, *, scope_key: str, stable_key: str) -> MemoryRecord | None: ...

    async def list_current_for_scopes(
        self,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]: ...

    async def current_depending_on(
        self,
        source_ref: str,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]: ...

    async def commit_revision(
        self,
        record: MemoryRecord,
        *,
        expected_current_ref: str | None,
    ) -> None: ...


class DisabledMemoryRepository:
    async def find_by_candidate(
        self,
        candidate_ref: str,
        *,
        scope_key: str,
    ) -> MemoryRecord | None:
        del candidate_ref, scope_key
        raise MemoryRepositoryUnavailable("memory repository is disabled")

    async def get(
        self,
        memory_ref: str,
        *,
        allowed_scope_keys: tuple[str, ...],
    ) -> MemoryRecord:
        del memory_ref, allowed_scope_keys
        raise MemoryRepositoryUnavailable("memory repository is disabled")

    async def current(self, *, scope_key: str, stable_key: str) -> MemoryRecord | None:
        del scope_key, stable_key
        raise MemoryRepositoryUnavailable("memory repository is disabled")

    async def list_current_for_scopes(
        self,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]:
        del scope_keys
        raise MemoryRepositoryUnavailable("memory repository is disabled")

    async def current_depending_on(
        self,
        source_ref: str,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]:
        del source_ref, scope_keys
        raise MemoryRepositoryUnavailable("memory repository is disabled")

    async def commit_revision(
        self,
        record: MemoryRecord,
        *,
        expected_current_ref: str | None,
    ) -> None:
        del record, expected_current_ref
        raise MemoryRepositoryUnavailable("memory repository is disabled")


class InMemoryMemoryRepository:
    """Reference repository; no database, vector index, or external side effect."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._candidate_index: dict[str, str] = {}
        self._current_index: dict[tuple[str, str], str] = {}

    async def find_by_candidate(
        self,
        candidate_ref: str,
        *,
        scope_key: str,
    ) -> MemoryRecord | None:
        memory_ref = self._candidate_index.get(candidate_ref)
        if memory_ref is None:
            return None
        record = self._records[memory_ref]
        return record if record.scope.scope_key == scope_key else None

    async def get(
        self,
        memory_ref: str,
        *,
        allowed_scope_keys: tuple[str, ...],
    ) -> MemoryRecord:
        try:
            record = self._records[memory_ref]
        except KeyError as exc:
            raise MemoryRepositoryUnavailable("memory record does not exist") from exc
        if record.scope.scope_key not in set(allowed_scope_keys):
            raise MemoryRepositoryUnavailable("memory record does not exist")
        return record

    async def current(self, *, scope_key: str, stable_key: str) -> MemoryRecord | None:
        memory_ref = self._current_index.get((scope_key, stable_key))
        return None if memory_ref is None else self._records[memory_ref]

    async def list_current_for_scopes(
        self,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]:
        allowed = set(scope_keys)
        return tuple(
            self._records[memory_ref]
            for (scope_key, _), memory_ref in self._current_index.items()
            if scope_key in allowed
        )

    async def current_depending_on(
        self,
        source_ref: str,
        scope_keys: tuple[str, ...],
    ) -> tuple[MemoryRecord, ...]:
        current_refs = set(self._current_index.values())
        allowed = set(scope_keys)
        return tuple(
            record
            for memory_ref, record in self._records.items()
            if memory_ref in current_refs
            and record.scope.scope_key in allowed
            and any(
                dependency.source_ref == source_ref
                for dependency in record.source_dependencies
            )
        )

    async def commit_revision(
        self,
        record: MemoryRecord,
        *,
        expected_current_ref: str | None,
    ) -> None:
        key = (record.scope.scope_key, record.stable_key)
        current_ref = self._current_index.get(key)
        if current_ref != expected_current_ref:
            raise MemoryConflict("memory stable-key version changed concurrently")
        existing = self._records.get(record.memory_ref)
        if existing is not None:
            if existing.content_hash != record.content_hash:
                raise MemoryConflict("memory reference was reused with different content")
            return
        if current_ref is not None:
            current = self._records[current_ref]
            self._records[current_ref] = current.model_copy(
                update={"validity": MemoryValidity.SUPERSEDED}
            )
        self._records[record.memory_ref] = record
        self._candidate_index[record.candidate_ref] = record.memory_ref
        self._current_index[key] = record.memory_ref


class MemoryPolicyGuard:
    """Deterministic scope/source/consent guard for optional Memory commits."""

    def evaluate(
        self,
        *,
        task: AgentTaskState,
        candidate: MemoryCandidate,
        authorization: MemoryAuthorizationContext,
        previous: MemoryRecord | None,
    ) -> MemoryPolicyDecision:
        if not authorization.memory_writes_enabled:
            return self._deny(MemoryPolicyCode.WRITES_DISABLED, "memory writes are disabled")
        if task.status is not AgentTaskStatus.RUNNING:
            return self._deny(
                MemoryPolicyCode.TASK_NOT_RUNNING,
                "memory commit requires a running task",
            )
        if task.task_id not in set(authorization.allowed_task_refs):
            return self._deny(
                MemoryPolicyCode.SCOPE_DENIED,
                "current task is outside memory authorization",
            )
        if candidate.authorization_snapshot_ref != authorization.authorization_snapshot_ref:
            return self._deny(
                MemoryPolicyCode.AUTHORIZATION_MISMATCH,
                "memory candidate authorization snapshot does not match",
            )
        if not self.scope_allowed(candidate.scope, authorization, write=True):
            return self._deny(MemoryPolicyCode.SCOPE_DENIED, "memory scope is not authorized")
        if (
            candidate.scope.scope is MemoryScope.WORKING
            and candidate.scope.task_ref != task.task_id
        ):
            return self._deny(
                MemoryPolicyCode.SCOPE_DENIED,
                "working memory must belong to the current task",
            )
        if candidate.content_policy_decision_ref not in set(
            authorization.approved_content_policy_decision_refs
        ):
            return self._deny(
                MemoryPolicyCode.CONTENT_POLICY_MISSING,
                "memory content policy decision is not approved",
            )
        approved_source_decisions = set(authorization.approved_source_validation_refs)
        if any(
            item.validation_decision_ref not in approved_source_decisions
            for item in candidate.source_dependencies
        ):
            return self._deny(
                MemoryPolicyCode.SOURCE_VALIDATION_MISSING,
                "memory source validation is not current",
            )
        if candidate.kind is MemoryKind.USER_PREFERENCE:
            confirmation_ref = candidate.payload.explicit_confirmation_ref
            source_refs = {
                dependency.source_ref for dependency in candidate.source_dependencies
            }
            if (
                candidate.basis is not MemoryBasis.EXPLICIT_USER
                or not authorization.can_manage_user_memory
                or confirmation_ref not in source_refs
            ):
                return self._deny(
                    MemoryPolicyCode.USER_CONFIRMATION_REQUIRED,
                    "user memory requires explicit confirmation and management authority",
                )
        if candidate.kind in {
            MemoryKind.CONVERSATION_SUMMARY,
            MemoryKind.PROJECT_GROUNDING,
        } and not candidate.source_dependencies:
            return self._deny(
                MemoryPolicyCode.SOURCE_REQUIRED,
                "persistent derived memory requires source dependencies",
            )
        if candidate.kind is MemoryKind.PROJECT_GROUNDING:
            if candidate.grounding_status is None:
                return self._deny(
                    MemoryPolicyCode.GROUNDING_REQUIRED,
                    "project memory requires an explicit grounding status",
                )
            if candidate.basis not in {
                MemoryBasis.GROUNDED_EVIDENCE,
                MemoryBasis.VALIDATED_SYSTEM,
            }:
                return self._deny(
                    MemoryPolicyCode.BASIS_REJECTED,
                    "project grounding cannot be based on an unverified summary",
                )
        elif candidate.grounding_status is not None:
            return self._deny(
                MemoryPolicyCode.GROUNDING_REQUIRED,
                "non-project memory cannot claim fact grounding",
            )
        if candidate.kind is MemoryKind.CONVERSATION_SUMMARY and (
            candidate.basis is not MemoryBasis.DERIVED_SUMMARY
        ):
            return self._deny(
                MemoryPolicyCode.BASIS_REJECTED,
                "conversation summary must remain a derived summary",
            )
        if previous is None:
            if candidate.expected_previous_ref is not None:
                return self._deny(
                    MemoryPolicyCode.REVISION_CONFLICT,
                    "memory expected previous version does not exist",
                )
        elif candidate.expected_previous_ref != previous.memory_ref:
            return self._deny(
                MemoryPolicyCode.REVISION_CONFLICT,
                "memory expected previous version is stale",
            )
        return MemoryPolicyDecision(
            allowed=True,
            code=MemoryPolicyCode.ALLOWED,
            message="memory candidate passed deterministic policy",
        )

    @staticmethod
    def scope_allowed(
        scope: MemoryScopeBinding,
        authorization: MemoryAuthorizationContext,
        *,
        write: bool,
    ) -> bool:
        if scope.tenant_ref != authorization.tenant_ref:
            return False
        if scope.scope is MemoryScope.WORKING:
            return scope.task_ref in set(authorization.allowed_task_refs)
        if scope.scope is MemoryScope.CONVERSATION:
            return scope.conversation_ref in set(authorization.allowed_conversation_refs)
        if scope.scope is MemoryScope.PROJECT_ASSESSMENT:
            return (
                scope.project_ref in set(authorization.allowed_project_refs)
                if scope.project_ref is not None
                else scope.assessment_ref in set(authorization.allowed_assessment_refs)
            )
        return (
            scope.user_ref == authorization.user_ref
            and (not write or authorization.can_manage_user_memory)
        )

    @staticmethod
    def _deny(code: MemoryPolicyCode, message: str) -> MemoryPolicyDecision:
        return MemoryPolicyDecision(allowed=False, code=code, message=message)


class MemoryCommitter:
    def __init__(
        self,
        repository: MemoryRepository | None = None,
        policy_guard: MemoryPolicyGuard | None = None,
    ) -> None:
        self._repository = repository or DisabledMemoryRepository()
        self._policy_guard = policy_guard or MemoryPolicyGuard()

    async def commit(
        self,
        *,
        task: AgentTaskState,
        candidate: MemoryCandidate,
        authorization: MemoryAuthorizationContext,
        now: datetime | None = None,
    ) -> MemoryCommitOutcome:
        current_time = now or datetime.now(timezone.utc)
        self._guard_candidate_access(
            task=task,
            candidate=candidate,
            authorization=authorization,
        )
        replay = await self._repository.find_by_candidate(
            candidate.candidate_ref,
            scope_key=candidate.scope.scope_key,
        )
        if replay is not None:
            expected_hash = self._candidate_hash(candidate)
            if replay.content_hash != expected_hash:
                raise MemoryConflict("memory candidate ref was reused with different content")
            return MemoryCommitOutcome(record=replay, created=False, replayed=True)

        previous = await self._repository.current(
            scope_key=candidate.scope.scope_key,
            stable_key=candidate.stable_key,
        )
        decision = self._policy_guard.evaluate(
            task=task,
            candidate=candidate,
            authorization=authorization,
            previous=previous,
        )
        if not decision.allowed:
            raise MemoryPolicyRejected(f"{decision.code.value}: {decision.message}")
        if candidate.expires_at is not None and candidate.expires_at <= current_time:
            raise MemoryPolicyRejected("memory candidate is already expired")

        content_hash = self._candidate_hash(candidate)
        if (
            previous is not None
            and previous.validity is MemoryValidity.ACTIVE
            and previous.content_hash == content_hash
        ):
            return MemoryCommitOutcome(record=previous, created=False, replayed=False)
        version = 1 if previous is None else previous.version + 1
        identity = canonical_hash(
            {
                "scope_key": candidate.scope.scope_key,
                "stable_key": candidate.stable_key,
                "version": version,
                "content_hash": content_hash,
            }
        )
        memory_ref = str(uuid.uuid5(_MEMORY_NAMESPACE, identity))
        record = MemoryRecord(
            memory_ref=memory_ref,
            candidate_ref=candidate.candidate_ref,
            stable_key=candidate.stable_key,
            kind=candidate.kind,
            scope=candidate.scope,
            payload=candidate.payload,
            source_dependencies=candidate.source_dependencies,
            basis=candidate.basis,
            grounding_status=candidate.grounding_status,
            validity=MemoryValidity.ACTIVE,
            version=version,
            supersedes_ref=None if previous is None else previous.memory_ref,
            created_at=current_time,
            effective_at=current_time,
            expires_at=candidate.expires_at,
            content_hash=content_hash,
            policy_ref=candidate.policy_ref,
            authorization_snapshot_ref=candidate.authorization_snapshot_ref,
        )
        await self._repository.commit_revision(
            record,
            expected_current_ref=None if previous is None else previous.memory_ref,
        )
        return MemoryCommitOutcome(record=record, created=True, replayed=False)

    async def forget(
        self,
        *,
        memory_ref: str,
        authorization: MemoryAuthorizationContext,
        policy_ref: str,
        mutation_ref: str,
        now: datetime | None = None,
    ) -> MemoryRecord:
        if not authorization.memory_writes_enabled:
            raise MemoryPolicyRejected("memory writes are disabled")
        allowed_scope_keys = self._authorized_scope_keys(authorization)
        current = await self._repository.get(
            memory_ref,
            allowed_scope_keys=allowed_scope_keys,
        )
        replay = await self._repository.find_by_candidate(
            mutation_ref,
            scope_key=current.scope.scope_key,
        )
        if replay is not None:
            return replay
        latest = await self._repository.current(
            scope_key=current.scope.scope_key,
            stable_key=current.stable_key,
        )
        if latest is None or latest.memory_ref != memory_ref:
            raise MemoryConflict("only the current memory version can be forgotten")
        if not self._policy_guard.scope_allowed(current.scope, authorization, write=True):
            raise MemoryPolicyRejected("memory scope is not authorized for forgetting")
        return await self._transition_validity(
            current=current,
            validity=MemoryValidity.DELETED,
            authorization=authorization,
            policy_ref=policy_ref,
            mutation_ref=mutation_ref,
            now=now,
        )

    async def invalidate_by_source(
        self,
        *,
        source_ref: str,
        validity: MemoryValidity,
        authorization: MemoryAuthorizationContext,
        policy_ref: str,
        mutation_ref: str,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        if validity not in {
            MemoryValidity.STALE,
            MemoryValidity.CONFLICTED,
            MemoryValidity.REVOKED,
        }:
            raise MemoryPolicyRejected("source invalidation status is not allowed")
        if not authorization.memory_writes_enabled:
            raise MemoryPolicyRejected("memory writes are disabled")
        scope_keys = self._authorized_scope_keys(authorization)
        records = await self._repository.current_depending_on(source_ref, scope_keys)
        transitioned: list[MemoryRecord] = []
        for record in records:
            if not self._policy_guard.scope_allowed(record.scope, authorization, write=True):
                continue
            if record.validity is validity:
                transitioned.append(record)
                continue
            transitioned.append(
                await self._transition_validity(
                    current=record,
                    validity=validity,
                    authorization=authorization,
                    policy_ref=policy_ref,
                    mutation_ref=str(
                        uuid.uuid5(
                            _MEMORY_NAMESPACE,
                            f"{mutation_ref}:{record.memory_ref}",
                        )
                    ),
                    now=now,
                )
            )
        return tuple(transitioned)

    async def _transition_validity(
        self,
        *,
        current: MemoryRecord,
        validity: MemoryValidity,
        authorization: MemoryAuthorizationContext,
        policy_ref: str,
        mutation_ref: str,
        now: datetime | None,
    ) -> MemoryRecord:
        current_time = now or datetime.now(timezone.utc)
        version = current.version + 1
        identity_hash = canonical_hash(
            {
                "previous_ref": current.memory_ref,
                "validity": validity.value,
                "mutation_ref": mutation_ref,
            }
        )
        memory_ref = str(
            uuid.uuid5(
                _MEMORY_NAMESPACE,
                f"{current.memory_ref}:{version}:{identity_hash}",
            )
        )
        transitioned = current.model_copy(
            update={
                "memory_ref": memory_ref,
                "candidate_ref": mutation_ref,
                "validity": validity,
                "version": version,
                "supersedes_ref": current.memory_ref,
                "created_at": current_time,
                "effective_at": current_time,
                "content_hash": current.content_hash,
                "policy_ref": policy_ref,
                "authorization_snapshot_ref": authorization.authorization_snapshot_ref,
            }
        )
        await self._repository.commit_revision(
            transitioned,
            expected_current_ref=current.memory_ref,
        )
        return transitioned

    @staticmethod
    def _candidate_hash(candidate: MemoryCandidate) -> str:
        payload = candidate.model_dump(mode="json")
        for field_name in (
            "candidate_ref",
            "authorization_snapshot_ref",
            "content_policy_decision_ref",
            "policy_ref",
            "expected_previous_ref",
        ):
            payload.pop(field_name, None)
        payload["source_dependencies"] = [
            {
                key: value
                for key, value in item.model_dump(mode="json").items()
                if key != "validation_decision_ref"
            }
            for item in candidate.source_dependencies
        ]
        return canonical_hash(payload)

    @staticmethod
    def _guard_candidate_access(
        *,
        task: AgentTaskState,
        candidate: MemoryCandidate,
        authorization: MemoryAuthorizationContext,
    ) -> None:
        if not authorization.memory_writes_enabled:
            raise MemoryPolicyRejected("memory writes are disabled")
        if task.status is not AgentTaskStatus.RUNNING:
            raise MemoryPolicyRejected("memory commit requires a running task")
        if task.task_id not in set(authorization.allowed_task_refs):
            raise MemoryPolicyRejected("current task is outside memory authorization")
        if candidate.authorization_snapshot_ref != authorization.authorization_snapshot_ref:
            raise MemoryPolicyRejected("memory authorization snapshot does not match")
        if not MemoryPolicyGuard.scope_allowed(candidate.scope, authorization, write=True):
            raise MemoryPolicyRejected("memory replay scope is not authorized")
        if (
            candidate.scope.scope is MemoryScope.WORKING
            and candidate.scope.task_ref != task.task_id
        ):
            raise MemoryPolicyRejected("working memory belongs to another task")
        if candidate.content_policy_decision_ref not in set(
            authorization.approved_content_policy_decision_refs
        ):
            raise MemoryPolicyRejected("memory content policy decision is not approved")
        approved_sources = set(authorization.approved_source_validation_refs)
        if any(
            item.validation_decision_ref not in approved_sources
            for item in candidate.source_dependencies
        ):
            raise MemoryPolicyRejected("memory source validation is not current")

    @staticmethod
    def _authorized_scope_keys(
        authorization: MemoryAuthorizationContext,
    ) -> tuple[str, ...]:
        keys: list[str] = []
        keys.extend(
            f"{authorization.tenant_ref}:{MemoryScope.WORKING.value}:{ref}"
            for ref in authorization.allowed_task_refs
        )
        keys.extend(
            f"{authorization.tenant_ref}:{MemoryScope.CONVERSATION.value}:{ref}"
            for ref in authorization.allowed_conversation_refs
        )
        keys.extend(
            f"{authorization.tenant_ref}:{MemoryScope.PROJECT_ASSESSMENT.value}:{ref}"
            for ref in (
                *authorization.allowed_project_refs,
                *authorization.allowed_assessment_refs,
            )
        )
        if authorization.can_manage_user_memory:
            keys.append(
                f"{authorization.tenant_ref}:{MemoryScope.USER.value}:"
                f"{authorization.user_ref}"
            )
        return tuple(dict.fromkeys(keys))


class MemoryReader:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self._repository = repository or DisabledMemoryRepository()

    async def project(self, request: MemoryReadRequest) -> MemoryProjection:
        authorization = request.authorization
        if not authorization.memory_reads_enabled:
            raise MemoryPolicyRejected("memory reads are disabled")
        if request.task_ref not in set(authorization.allowed_task_refs):
            raise MemoryPolicyRejected("memory read task is outside authorization scope")
        for scope in request.scope_bindings:
            if not MemoryPolicyGuard.scope_allowed(scope, authorization, write=False):
                raise MemoryPolicyRejected("memory read scope is not authorized")
            if (
                scope.scope is MemoryScope.WORKING
                and scope.task_ref != request.task_ref
            ):
                raise MemoryPolicyRejected(
                    "working memory read must belong to the current task"
                )

        records = await self._repository.list_current_for_scopes(
            tuple(scope.scope_key for scope in request.scope_bindings)
        )
        now = datetime.now(timezone.utc)
        source_heads = {head.source_ref: head for head in request.source_heads}
        limitations: list[str] = []
        eligible: list[MemoryRecord] = []
        for record in records:
            if record.validity is MemoryValidity.ACTIVE:
                pass
            elif request.include_uncertain and record.validity in {
                MemoryValidity.STALE,
                MemoryValidity.CONFLICTED,
            }:
                limitations.append(
                    f"included_{record.validity.value}_memory:{record.memory_ref}"
                )
            else:
                continue
            if record.expires_at is not None and record.expires_at <= now:
                limitations.append(f"expired_memory_omitted:{record.memory_ref}")
                continue
            if not self._source_heads_match(record, source_heads):
                limitations.append(f"source_head_mismatch:{record.memory_ref}")
                continue
            if not self._is_relevant(record, request):
                continue
            eligible.append(record)

        eligible.sort(key=lambda record: self._sort_key(record, request))
        projected: list[MemoryProjectionEntry] = []
        used_chars = 0
        seen: set[tuple[str, str]] = set()
        for record in eligible:
            key = (record.stable_key, record.content_hash)
            if key in seen:
                continue
            entry, projected_ref_only = self._project_record(record)
            if projected_ref_only:
                limitations.append(f"memory_projected_ref_only:{record.memory_ref}")
            if len(projected) >= request.max_entries:
                limitations.append("memory_entry_limit_reached")
                break
            if used_chars + len(entry.display_text) > request.max_projection_chars:
                limitations.append("memory_projection_size_limit_reached")
                break
            projected_source_refs = {
                ref for projected_entry in projected for ref in projected_entry.source_refs
            }
            projected_source_refs.update(entry.source_refs)
            if len(projected_source_refs) > 500:
                limitations.append("memory_source_ref_limit_reached")
                break
            seen.add(key)
            projected.append(entry)
            used_chars += len(entry.display_text)

        source_refs = tuple(
            dict.fromkeys(ref for entry in projected for ref in entry.source_refs)
        )
        return MemoryProjection(
            entries=tuple(projected),
            entry_refs=tuple(entry.memory_ref for entry in projected),
            source_refs=source_refs,
            memory_version_refs=tuple(entry.memory_ref for entry in projected),
            limitation_messages=tuple(dict.fromkeys(limitations)),
        )

    @staticmethod
    def _source_heads_match(
        record: MemoryRecord,
        source_heads: dict[str, MemorySourceHead],
    ) -> bool:
        for dependency in record.source_dependencies:
            current = source_heads.get(dependency.source_ref)
            if current is None:
                return False
            if (
                current.source_version_ref != dependency.source_version_ref
                or current.source_content_hash != dependency.source_content_hash
            ):
                return False
        return True

    @staticmethod
    def _is_relevant(record: MemoryRecord, request: MemoryReadRequest) -> bool:
        if record.scope.scope is MemoryScope.WORKING:
            return True
        dependency_refs = {
            dependency.source_ref for dependency in record.source_dependencies
        }
        if dependency_refs & set(request.relevant_source_refs):
            return True
        haystack = (
            record.stable_key + " " + canonical_json(record.payload)
        ).casefold()
        needles = tuple(
            value.casefold().strip()
            for value in (request.relevance_keys or (request.query,))
            if value.strip()
        )
        return any(needle in haystack for needle in needles)

    @staticmethod
    def _sort_key(
        record: MemoryRecord,
        request: MemoryReadRequest,
    ) -> tuple[int, int, int, str]:
        relevant_sources = set(request.relevant_source_refs)
        source_priority = 0 if any(
            item.source_ref in relevant_sources for item in record.source_dependencies
        ) else 1
        basis_priority = {
            MemoryBasis.GROUNDED_EVIDENCE: 0,
            MemoryBasis.VALIDATED_SYSTEM: 1,
            MemoryBasis.EXPLICIT_USER: 2,
            MemoryBasis.DERIVED_SUMMARY: 3,
        }[record.basis]
        scope_priority = 3 if record.scope.scope is MemoryScope.USER else 0
        return source_priority, basis_priority, scope_priority, record.memory_ref

    @staticmethod
    def _project_record(
        record: MemoryRecord,
    ) -> tuple[MemoryProjectionEntry, bool]:
        authority = {
            MemoryBasis.GROUNDED_EVIDENCE: "derived_from_grounded_evidence",
            MemoryBasis.VALIDATED_SYSTEM: "derived_from_validated_system",
            MemoryBasis.EXPLICIT_USER: "explicit_user_memory",
            MemoryBasis.DERIVED_SUMMARY: "derived_summary",
        }[record.basis]
        source_refs = tuple(
            dict.fromkeys(
                dependency.source_ref for dependency in record.source_dependencies
            )
        )
        display_text = canonical_json(record.payload)
        projected_ref_only = len(display_text) > 8000
        if projected_ref_only:
            display_text = canonical_json(
                {
                    "memory_ref": record.memory_ref,
                    "kind": record.kind.value,
                    "stable_key": record.stable_key,
                    "source_refs": source_refs,
                    "notice": "content_available_by_authorized_source_ref",
                }
            )
        return MemoryProjectionEntry(
            memory_ref=record.memory_ref,
            stable_key=record.stable_key,
            kind=record.kind,
            scope=record.scope.scope,
            basis=record.basis,
            grounding_status=record.grounding_status,
            validity=record.validity,
            authority_label=authority,
            representation=(
                ContextRepresentation.REF_ONLY
                if projected_ref_only
                else ContextRepresentation.STRUCTURED_PROJECTION
            ),
            display_text=display_text,
            source_refs=source_refs,
            content_hash=record.content_hash,
            untrusted_data=True,
        ), projected_ref_only
