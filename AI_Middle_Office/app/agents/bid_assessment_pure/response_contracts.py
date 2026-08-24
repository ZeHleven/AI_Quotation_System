"""Immutable response publication and version-governance contracts.

These contracts separate the immutable answer payload from its mutable validity
projection.  A published message is safe for conversation storage; internal
binding material remains inside the response artifact and is never rendered.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field, model_validator

from .citation_contracts import RenderedAnswerBlock, RenderedCitationLine
from .common import Reference, StrictContentContract, StrictContract
from .state import TaskEventType, TaskTransitionEvent
from .tool_runtime import Sha256Digest, canonical_hash


_RESPONSE_NAMESPACE = uuid.UUID("de8b4b46-d8c8-5f54-a812-99667683c5b6")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        serialized = value.isoformat()
        # Pydantic's JSON mode renders UTC with the RFC 3339 ``Z`` suffix.
        # Hash construction and post-validation must use the same canonical
        # representation or an otherwise valid event cannot be rebuilt.
        if serialized.endswith("+00:00"):
            return f"{serialized[:-6]}Z"
        return serialized
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class ResponseLifecycleStatus(str, Enum):
    COMMITTED = "committed"
    STALE = "stale"
    SUPERSEDED = "superseded"


class ResponseSupersedeReason(str, Enum):
    USER_CORRECTION = "user_correction"
    ANSWER_CORRECTION = "answer_correction"
    SOURCE_UPDATE = "source_update"


class ResponseStaleReason(str, Enum):
    SOURCE_HEAD_CHANGED = "source_head_changed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    AUTHORIZATION_CHANGED = "authorization_changed"
    GROUNDING_REVOKED = "grounding_revoked"
    CITATION_REVOKED = "citation_revoked"


class ResponseLifecycleReason(str, Enum):
    INITIAL_COMMIT = "initial_commit"
    REPLACED_BY_RESPONSE = "replaced_by_response"
    SOURCE_HEAD_CHANGED = "source_head_changed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    AUTHORIZATION_CHANGED = "authorization_changed"
    GROUNDING_REVOKED = "grounding_revoked"
    CITATION_REVOKED = "citation_revoked"


class PublishedAnswerMessage(StrictContentContract):
    """Only this projection may be appended as the assistant conversation message."""

    schema_name: Literal["bid.answer.message.v1"] = "bid.answer.message.v1"
    public_response_ref: Reference
    message_hash: Sha256Digest
    response_version: int = Field(ge=1)
    response_language: str = Field(min_length=2, max_length=32)
    text: str = Field(min_length=1, max_length=131_072)
    blocks: tuple[RenderedAnswerBlock, ...] = Field(min_length=1, max_length=256)
    citations: tuple[RenderedCitationLine, ...] = Field(default_factory=tuple, max_length=1000)

    @classmethod
    def build(
        cls,
        *,
        response_version: int,
        response_language: str,
        text: str,
        blocks: tuple[RenderedAnswerBlock, ...],
        citations: tuple[RenderedCitationLine, ...],
    ) -> "PublishedAnswerMessage":
        body = {
            "schema_name": "bid.answer.message.v1",
            "response_version": response_version,
            "response_language": response_language,
            "text": text,
            "blocks": [block.model_dump(mode="json") for block in blocks],
            "citations": [citation.model_dump(mode="json") for citation in citations],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            public_response_ref=f"answer:{digest.removeprefix('sha256:')}",
            message_hash=digest,
        )

    @model_validator(mode="after")
    def validate_message(self) -> "PublishedAnswerMessage":
        block_refs = tuple(block.block_ref for block in self.blocks)
        citation_refs = tuple(citation.citation_ref for citation in self.citations)
        if len(block_refs) != len(set(block_refs)):
            raise ValueError("published answer block refs must be unique")
        if len(citation_refs) != len(set(citation_refs)):
            raise ValueError("published answer Citation refs must be unique")
        if any(
            not set(block.citation_refs).issubset(set(citation_refs))
            for block in self.blocks
        ):
            raise ValueError("published answer block references an unknown Citation")
        body = self.model_dump(
            mode="json",
            exclude={"public_response_ref", "message_hash"},
        )
        digest = canonical_hash(body)
        if self.message_hash != digest:
            raise ValueError("published answer message hash does not match")
        if self.public_response_ref != f"answer:{digest.removeprefix('sha256:')}":
            raise ValueError("public response ref does not match the message")
        return self


class CommittedResponseArtifact(StrictContract):
    """Immutable internal receipt binding one safe message to its validated chain."""

    response_ref: Reference
    artifact_ref: Reference
    artifact_hash: Sha256Digest
    public_response_ref: Reference
    response_version: int = Field(ge=1)
    task_ref: Reference
    conversation_ref: Reference
    answer_state_version: int = Field(ge=1)
    commit_state_version: int = Field(ge=2)
    context_snapshot_ref: Reference
    context_snapshot_hash: Sha256Digest
    authorization_snapshot_ref: Reference
    draft_ref: Reference
    draft_hash: Sha256Digest
    draft_validation_hash: Sha256Digest
    grounding_snapshot_ref: Reference
    citation_authority_snapshot_ref: Reference
    citation_projection_hash: Sha256Digest
    citation_bundle_ref: Reference
    citation_bundle_hash: Sha256Digest
    rendered_ref: Reference
    rendered_hash: Sha256Digest
    answer_observation_ref: Reference
    answer_observation_hash: Sha256Digest
    answer_action_ref: Reference
    message: PublishedAnswerMessage
    supersedes_response_ref: Reference | None = None
    supersedes_artifact_hash: Sha256Digest | None = None
    supersedes_version: int | None = Field(default=None, ge=1)
    supersede_reason: ResponseSupersedeReason | None = None

    @classmethod
    def build(cls, **body: object) -> "CommittedResponseArtifact":
        digest = canonical_hash(_json_value(body))
        response_ref = str(uuid.uuid5(_RESPONSE_NAMESPACE, digest))
        return cls(
            **body,
            response_ref=response_ref,
            artifact_ref=f"response-artifact:{digest.removeprefix('sha256:')}",
            artifact_hash=digest,
        )

    @model_validator(mode="after")
    def validate_artifact(self) -> "CommittedResponseArtifact":
        if self.commit_state_version != self.answer_state_version + 1:
            raise ValueError("response commit must follow its accepted answer Observation")
        if self.public_response_ref != self.message.public_response_ref:
            raise ValueError("response artifact and published message refs differ")
        prior_values = (
            self.supersedes_response_ref,
            self.supersedes_artifact_hash,
            self.supersedes_version,
            self.supersede_reason,
        )
        if any(value is not None for value in prior_values) != all(
            value is not None for value in prior_values
        ):
            raise ValueError("supersede binding fields must appear together")
        if self.supersedes_response_ref is None:
            if self.response_version != 1:
                raise ValueError("a new response chain starts at version 1")
        elif self.response_version != int(self.supersedes_version or 0) + 1:
            raise ValueError("superseding response version must increment by one")
        body = self.model_dump(
            mode="json",
            exclude={"response_ref", "artifact_ref", "artifact_hash"},
        )
        digest = canonical_hash(body)
        if self.artifact_hash != digest:
            raise ValueError("response artifact hash does not match")
        if self.artifact_ref != f"response-artifact:{digest.removeprefix('sha256:')}":
            raise ValueError("response artifact ref does not match")
        if self.response_ref != str(uuid.uuid5(_RESPONSE_NAMESPACE, digest)):
            raise ValueError("response persistence ref does not match")
        return self


class ResponseVersionHead(StrictContract):
    response_ref: Reference
    artifact_ref: Reference
    artifact_hash: Sha256Digest
    public_response_ref: Reference
    response_version: int = Field(ge=1)
    task_ref: Reference
    conversation_ref: Reference
    status: ResponseLifecycleStatus

    @classmethod
    def from_artifact(
        cls,
        artifact: CommittedResponseArtifact,
        *,
        status: ResponseLifecycleStatus,
    ) -> "ResponseVersionHead":
        return cls(
            response_ref=artifact.response_ref,
            artifact_ref=artifact.artifact_ref,
            artifact_hash=artifact.artifact_hash,
            public_response_ref=artifact.public_response_ref,
            response_version=artifact.response_version,
            task_ref=artifact.task_ref,
            conversation_ref=artifact.conversation_ref,
            status=status,
        )


class ResponseCommitIssueCode(str, Enum):
    TASK_NOT_COMMITTABLE = "task_not_committable"
    ANSWER_OBSERVATION_NOT_ACCEPTED = "answer_observation_not_accepted"
    CONTEXT_BINDING_MISMATCH = "context_binding_mismatch"
    DRAFT_VALIDATION_MISMATCH = "draft_validation_mismatch"
    CITATION_BINDING_MISMATCH = "citation_binding_mismatch"
    RENDERED_CANDIDATE_MISMATCH = "rendered_candidate_mismatch"
    VERSION_BINDING_MISMATCH = "version_binding_mismatch"
    COMPLETION_TRANSITION_REJECTED = "completion_transition_rejected"


class ResponseCommitIssue(StrictContract):
    code: ResponseCommitIssueCode
    message: str = Field(min_length=1, max_length=500)


class ResponseCommitDecision(StrictContract):
    accepted: bool
    task_ref: Reference
    conversation_ref: Reference
    rendered_ref: Reference
    artifact: CommittedResponseArtifact | None = None
    completion_event: TaskTransitionEvent | None = None
    issues: tuple[ResponseCommitIssue, ...] = Field(default_factory=tuple, max_length=64)

    @model_validator(mode="after")
    def validate_decision(self) -> "ResponseCommitDecision":
        valid_payload = (
            self.artifact is not None
            and self.completion_event is not None
            and not self.issues
        )
        if self.accepted != valid_payload:
            raise ValueError("accepted response commit requires one valid artifact and event")
        if self.artifact is not None and self.completion_event is not None:
            if (
                self.artifact.task_ref != self.task_ref
                or self.artifact.conversation_ref != self.conversation_ref
                or self.artifact.rendered_ref != self.rendered_ref
                or self.completion_event.task_id != self.task_ref
                or self.completion_event.expected_state_version
                != self.artifact.commit_state_version
                or self.completion_event.event_type is not TaskEventType.COMPLETION_ACCEPTED
                or not self.completion_event.result_committed
                or self.completion_event.effect_idempotency_key
                != self.artifact.artifact_ref
                or self.completion_event.action_ref != self.artifact.answer_action_ref
            ):
                raise ValueError("response artifact and completion event differ")
        return self


class ResponseLifecycleEvent(StrictContract):
    event_ref: Reference
    event_hash: Sha256Digest
    response_ref: Reference
    sequence_no: int = Field(ge=1, le=128)
    idempotency_key: Reference
    from_status: ResponseLifecycleStatus | None
    to_status: ResponseLifecycleStatus
    reason: ResponseLifecycleReason
    cause_ref: Reference
    previous_event_hash: Sha256Digest | None
    occurred_at: datetime

    @classmethod
    def build(cls, **body: object) -> "ResponseLifecycleEvent":
        digest = canonical_hash(_json_value(body))
        return cls(
            **body,
            event_ref=f"response-event:{digest.removeprefix('sha256:')}",
            event_hash=digest,
        )

    @model_validator(mode="after")
    def validate_event(self) -> "ResponseLifecycleEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("response lifecycle timestamp must be timezone-aware")
        if self.sequence_no == 1:
            if (
                self.from_status is not None
                or self.to_status is not ResponseLifecycleStatus.COMMITTED
                or self.reason is not ResponseLifecycleReason.INITIAL_COMMIT
                or self.previous_event_hash is not None
            ):
                raise ValueError("first response lifecycle event must be initial commit")
        elif self.from_status is None or self.previous_event_hash is None:
            raise ValueError("later response lifecycle events require prior status and hash")
        allowed = {
            (ResponseLifecycleStatus.COMMITTED, ResponseLifecycleStatus.STALE),
            (ResponseLifecycleStatus.COMMITTED, ResponseLifecycleStatus.SUPERSEDED),
            (ResponseLifecycleStatus.STALE, ResponseLifecycleStatus.SUPERSEDED),
        }
        if self.sequence_no > 1 and (self.from_status, self.to_status) not in allowed:
            raise ValueError("illegal response lifecycle transition")
        stale_reasons = {ResponseLifecycleReason(item.value) for item in ResponseStaleReason}
        if self.to_status is ResponseLifecycleStatus.STALE and self.reason not in stale_reasons:
            raise ValueError("stale response requires an explicit stale reason")
        if (
            self.to_status is ResponseLifecycleStatus.SUPERSEDED
            and self.reason is not ResponseLifecycleReason.REPLACED_BY_RESPONSE
        ):
            raise ValueError("superseded response requires its replacement reason")
        body = self.model_dump(mode="json", exclude={"event_ref", "event_hash"})
        digest = canonical_hash(body)
        if self.event_hash != digest:
            raise ValueError("response lifecycle event hash does not match")
        if self.event_ref != f"response-event:{digest.removeprefix('sha256:')}":
            raise ValueError("response lifecycle event ref does not match")
        return self


class ResponsePersistenceEnvelope(StrictContract):
    schema_name: Literal["bid.response.persistence.v1"] = "bid.response.persistence.v1"
    envelope_hash: Sha256Digest
    artifact: CommittedResponseArtifact
    current_status: ResponseLifecycleStatus
    lifecycle_events: tuple[ResponseLifecycleEvent, ...] = Field(min_length=1, max_length=128)

    @classmethod
    def build(
        cls,
        *,
        artifact: CommittedResponseArtifact,
        lifecycle_events: tuple[ResponseLifecycleEvent, ...],
    ) -> "ResponsePersistenceEnvelope":
        body = {
            "schema_name": "bid.response.persistence.v1",
            "artifact": artifact.model_dump(mode="json"),
            "current_status": lifecycle_events[-1].to_status.value,
            "lifecycle_events": [event.model_dump(mode="json") for event in lifecycle_events],
        }
        return cls(**body, envelope_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_envelope(self) -> "ResponsePersistenceEnvelope":
        if tuple(event.sequence_no for event in self.lifecycle_events) != tuple(
            range(1, len(self.lifecycle_events) + 1)
        ):
            raise ValueError("response lifecycle sequence must be contiguous")
        for index, event in enumerate(self.lifecycle_events):
            if event.response_ref != self.artifact.response_ref:
                raise ValueError("response lifecycle event belongs to another response")
            if index > 0:
                prior = self.lifecycle_events[index - 1]
                if (
                    event.from_status is not prior.to_status
                    or event.previous_event_hash != prior.event_hash
                ):
                    raise ValueError("response lifecycle hash chain is broken")
        idempotency_keys = tuple(event.idempotency_key for event in self.lifecycle_events)
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise ValueError("response lifecycle idempotency keys must be unique")
        if self.current_status is not self.lifecycle_events[-1].to_status:
            raise ValueError("response envelope status does not match its last event")
        body = self.model_dump(mode="json", exclude={"envelope_hash"})
        if self.envelope_hash != canonical_hash(body):
            raise ValueError("response persistence envelope hash does not match")
        return self

    def head(self) -> ResponseVersionHead:
        return ResponseVersionHead.from_artifact(
            self.artifact,
            status=self.current_status,
        )


class ResponseStaleIntent(StrictContract):
    intent_ref: Reference
    intent_hash: Sha256Digest
    idempotency_key: Reference
    response_ref: Reference
    expected_artifact_hash: Sha256Digest
    expected_status: Literal[ResponseLifecycleStatus.COMMITTED]
    reason: ResponseStaleReason
    cause_ref: Reference

    @model_validator(mode="after")
    def validate_intent(self) -> "ResponseStaleIntent":
        body = self.model_dump(mode="json", exclude={"intent_ref", "intent_hash"})
        digest = canonical_hash(body)
        if self.intent_hash != digest:
            raise ValueError("response stale intent hash does not match")
        if self.intent_ref != f"response-stale:{digest.removeprefix('sha256:')}":
            raise ValueError("response stale intent ref does not match")
        return self
