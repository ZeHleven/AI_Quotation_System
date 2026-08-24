"""Side-effect-free response commit and lifecycle governance capabilities."""

from __future__ import annotations

from datetime import datetime

from .action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
)
from .answer_contracts import AnswerDraft, AnswerDraftValidationDecision
from .citation_contracts import CitationProjectionDecision, RenderedAnswerCandidate
from .response_contracts import (
    CommittedResponseArtifact,
    PublishedAnswerMessage,
    ResponseCommitDecision,
    ResponseCommitIssue,
    ResponseCommitIssueCode,
    ResponseLifecycleEvent,
    ResponseLifecycleReason,
    ResponseLifecycleStatus,
    ResponsePersistenceEnvelope,
    ResponseStaleIntent,
    ResponseStaleReason,
    ResponseSupersedeReason,
    ResponseVersionHead,
)
from .runtime import ContextAssemblyStatus, ContextConsumer, ContextSnapshot
from .state import AgentTaskState, AgentTaskStatus, TaskEventType, TaskTransitionEvent
from .state_machine import TransitionRejected, decide_transition
from .tool_runtime import canonical_hash


class ResponseCommitRejected(ValueError):
    """A response cannot cross the immutable publication boundary."""


class ResponseVersionRejected(ValueError):
    """A response lifecycle mutation is stale, ambiguous, or illegal."""


class AnswerCommitRuntime:
    """Prepare one atomic commit intent; it never writes or publishes anything."""

    def prepare(
        self,
        *,
        task: AgentTaskState,
        context_snapshot: ContextSnapshot,
        draft: AnswerDraft,
        validation: AnswerDraftValidationDecision,
        citation_decision: CitationProjectionDecision,
        rendered: RenderedAnswerCandidate,
        answer_observation: ActionObservation,
        previous_response: ResponseVersionHead | None = None,
        supersede_reason: ResponseSupersedeReason | None = None,
    ) -> ResponseCommitDecision:
        issues: list[ResponseCommitIssue] = []

        def reject(code: ResponseCommitIssueCode, message: str) -> None:
            issues.append(ResponseCommitIssue(code=code, message=message))

        if (
            task.status is not AgentTaskStatus.RUNNING
            or task.in_flight_action_ref is not None
            or task.task_id != rendered.task_ref
            or task.state_version != rendered.state_version + 1
        ):
            reject(
                ResponseCommitIssueCode.TASK_NOT_COMMITTABLE,
                "Task is not at the accepted answer commit boundary",
            )

        if (
            answer_observation.task_ref != task.task_id
            or answer_observation.kind is not ActionObservationKind.ANSWER_DRAFT
            or answer_observation.status is not ActionObservationStatus.SUCCEEDED
            or answer_observation.state_version != rendered.state_version
            or rendered.draft_ref not in answer_observation.progress_signal_refs
            or answer_observation.observation_ref not in task.observation_refs
        ):
            reject(
                ResponseCommitIssueCode.ANSWER_OBSERVATION_NOT_ACCEPTED,
                "AnswerDraft Observation is not accepted by the current Task",
            )

        if (
            context_snapshot.task_ref != task.task_id
            or context_snapshot.consumer is not ContextConsumer.MAIN_AGENT
            or context_snapshot.status
            not in {
                ContextAssemblyStatus.READY,
                ContextAssemblyStatus.READY_WITH_LIMITS,
            }
            or context_snapshot.state_version != rendered.state_version
            or context_snapshot.snapshot_ref != rendered.context_snapshot_ref
        ):
            reject(
                ResponseCommitIssueCode.CONTEXT_BINDING_MISMATCH,
                "Rendered answer does not bind to the frozen Main Agent Context",
            )

        draft_hash = canonical_hash(draft)
        if (
            not validation.accepted
            or validation.issues
            or validation.task_ref != task.task_id
            or validation.state_version != rendered.state_version
            or validation.context_snapshot_ref != rendered.context_snapshot_ref
            or validation.draft_ref != rendered.draft_ref
            or validation.draft_hash != rendered.draft_hash
            or draft_hash != rendered.draft_hash
        ):
            reject(
                ResponseCommitIssueCode.DRAFT_VALIDATION_MISMATCH,
                "Rendered answer is not backed by the accepted Draft validation",
            )

        bundle = citation_decision.bundle
        if (
            not citation_decision.accepted
            or citation_decision.issues
            or bundle is None
            or citation_decision.task_ref != task.task_id
            or citation_decision.context_snapshot_ref != rendered.context_snapshot_ref
            or citation_decision.draft_ref != rendered.draft_ref
            or bundle.state_version != rendered.state_version
            or bundle.draft_hash != rendered.draft_hash
            or bundle.bundle_ref != rendered.citation_bundle_ref
            or bundle.bundle_hash != rendered.citation_bundle_hash
            or bundle.validation_grounding_snapshot_ref
            != validation.grounding_snapshot_ref
            or bundle.authorization_snapshot_ref
            != context_snapshot.authorization_snapshot_ref
        ):
            reject(
                ResponseCommitIssueCode.CITATION_BINDING_MISMATCH,
                "Rendered answer is not backed by the accepted Citation Bundle",
            )

        if (
            rendered.task_ref != task.task_id
            or rendered.context_snapshot_ref != context_snapshot.snapshot_ref
            or rendered.draft_hash != draft_hash
            or canonical_hash(
                rendered.model_dump(
                    mode="json",
                    exclude={"rendered_ref", "rendered_hash"},
                )
            )
            != rendered.rendered_hash
        ):
            reject(
                ResponseCommitIssueCode.RENDERED_CANDIDATE_MISMATCH,
                "Rendered answer candidate failed its immutable binding check",
            )

        if previous_response is None:
            if supersede_reason is not None:
                reject(
                    ResponseCommitIssueCode.VERSION_BINDING_MISMATCH,
                    "Supersede reason requires an explicit previous response",
                )
            response_version = 1
        else:
            if (
                supersede_reason is None
                or previous_response.conversation_ref != task.session_id
                or previous_response.status
                not in {
                    ResponseLifecycleStatus.COMMITTED,
                    ResponseLifecycleStatus.STALE,
                }
            ):
                reject(
                    ResponseCommitIssueCode.VERSION_BINDING_MISMATCH,
                    "Previous response is not an active version in this conversation",
                )
            response_version = previous_response.response_version + 1

        if issues:
            return ResponseCommitDecision(
                accepted=False,
                task_ref=task.task_id,
                conversation_ref=task.session_id,
                rendered_ref=rendered.rendered_ref,
                issues=tuple(issues),
            )

        message = PublishedAnswerMessage.build(
            response_version=response_version,
            response_language=rendered.response_language,
            text=rendered.text,
            blocks=rendered.blocks,
            citations=rendered.citations,
        )
        artifact = CommittedResponseArtifact.build(
            public_response_ref=message.public_response_ref,
            response_version=response_version,
            task_ref=task.task_id,
            conversation_ref=task.session_id,
            answer_state_version=rendered.state_version,
            commit_state_version=task.state_version,
            context_snapshot_ref=context_snapshot.snapshot_ref,
            context_snapshot_hash=context_snapshot.snapshot_hash,
            authorization_snapshot_ref=context_snapshot.authorization_snapshot_ref,
            draft_ref=rendered.draft_ref,
            draft_hash=rendered.draft_hash,
            draft_validation_hash=canonical_hash(validation),
            grounding_snapshot_ref=validation.grounding_snapshot_ref,
            citation_authority_snapshot_ref=citation_decision.citation_authority_snapshot_ref,
            citation_projection_hash=canonical_hash(citation_decision),
            citation_bundle_ref=rendered.citation_bundle_ref,
            citation_bundle_hash=rendered.citation_bundle_hash,
            rendered_ref=rendered.rendered_ref,
            rendered_hash=rendered.rendered_hash,
            answer_observation_ref=answer_observation.observation_ref,
            answer_observation_hash=answer_observation.observation_hash,
            answer_action_ref=answer_observation.source_action_ref,
            message=message,
            supersedes_response_ref=(
                None if previous_response is None else previous_response.response_ref
            ),
            supersedes_artifact_hash=(
                None if previous_response is None else previous_response.artifact_hash
            ),
            supersedes_version=(
                None if previous_response is None else previous_response.response_version
            ),
            supersede_reason=supersede_reason,
        )
        completion_event = TaskTransitionEvent(
            event_id=(
                "response-completion:"
                + artifact.artifact_hash.removeprefix("sha256:")
            ),
            task_id=task.task_id,
            expected_state_version=task.state_version,
            event_type=TaskEventType.COMPLETION_ACCEPTED,
            effect_idempotency_key=artifact.artifact_ref,
            action_ref=answer_observation.source_action_ref,
            pending_context=None,
            resume_proof=None,
            execution_mode=None,
            plan_ref=None,
            observation_ref=None,
            result_committed=True,
            error_ref=None,
            cancellation_fence_ref=None,
        )
        try:
            decide_transition(task, completion_event)
        except TransitionRejected:
            return ResponseCommitDecision(
                accepted=False,
                task_ref=task.task_id,
                conversation_ref=task.session_id,
                rendered_ref=rendered.rendered_ref,
                issues=(
                    ResponseCommitIssue(
                        code=ResponseCommitIssueCode.COMPLETION_TRANSITION_REJECTED,
                        message="Task completion transition rejected the response commit",
                    ),
                ),
            )
        return ResponseCommitDecision(
            accepted=True,
            task_ref=task.task_id,
            conversation_ref=task.session_id,
            rendered_ref=rendered.rendered_ref,
            artifact=artifact,
            completion_event=completion_event,
        )


class ResponseVersionController:
    """Build and evolve response validity without rewriting answer content."""

    def initial_envelope(
        self,
        *,
        artifact: CommittedResponseArtifact,
        occurred_at: datetime,
    ) -> ResponsePersistenceEnvelope:
        event = ResponseLifecycleEvent.build(
            response_ref=artifact.response_ref,
            sequence_no=1,
            idempotency_key=f"response-committed:{artifact.response_ref}",
            from_status=None,
            to_status=ResponseLifecycleStatus.COMMITTED,
            reason=ResponseLifecycleReason.INITIAL_COMMIT,
            cause_ref=artifact.artifact_ref,
            previous_event_hash=None,
            occurred_at=occurred_at,
        )
        return ResponsePersistenceEnvelope.build(
            artifact=artifact,
            lifecycle_events=(event,),
        )

    def prepare_stale(
        self,
        *,
        head: ResponseVersionHead,
        reason: ResponseStaleReason,
        cause_ref: str,
        idempotency_key: str,
    ) -> ResponseStaleIntent:
        if head.status is not ResponseLifecycleStatus.COMMITTED:
            raise ResponseVersionRejected("only a current committed response can become stale")
        body = {
            "idempotency_key": idempotency_key,
            "response_ref": head.response_ref,
            "expected_artifact_hash": head.artifact_hash,
            "expected_status": ResponseLifecycleStatus.COMMITTED.value,
            "reason": reason.value,
            "cause_ref": cause_ref,
        }
        digest = canonical_hash(body)
        return ResponseStaleIntent(
            **body,
            intent_ref=f"response-stale:{digest.removeprefix('sha256:')}",
            intent_hash=digest,
        )

    def apply_stale(
        self,
        *,
        envelope: ResponsePersistenceEnvelope,
        intent: ResponseStaleIntent,
        occurred_at: datetime,
    ) -> tuple[ResponsePersistenceEnvelope, bool]:
        if (
            envelope.artifact.response_ref != intent.response_ref
            or envelope.artifact.artifact_hash != intent.expected_artifact_hash
        ):
            raise ResponseVersionRejected("stale intent does not match the response artifact")
        replay = self._find_replay(envelope, intent.idempotency_key)
        if replay is not None:
            if (
                replay.to_status is not ResponseLifecycleStatus.STALE
                or replay.reason is not ResponseLifecycleReason(intent.reason.value)
                or replay.cause_ref != intent.cause_ref
            ):
                raise ResponseVersionRejected("response lifecycle idempotency key was reused")
            return envelope, True
        if envelope.current_status is not intent.expected_status:
            raise ResponseVersionRejected("response status compare-and-set failed")
        event = ResponseLifecycleEvent.build(
            response_ref=envelope.artifact.response_ref,
            sequence_no=len(envelope.lifecycle_events) + 1,
            idempotency_key=intent.idempotency_key,
            from_status=envelope.current_status,
            to_status=ResponseLifecycleStatus.STALE,
            reason=ResponseLifecycleReason(intent.reason.value),
            cause_ref=intent.cause_ref,
            previous_event_hash=envelope.lifecycle_events[-1].event_hash,
            occurred_at=occurred_at,
        )
        return self._append(envelope, event), False

    def apply_superseded(
        self,
        *,
        envelope: ResponsePersistenceEnvelope,
        replacement: CommittedResponseArtifact,
        occurred_at: datetime,
    ) -> tuple[ResponsePersistenceEnvelope, bool]:
        if (
            replacement.supersedes_response_ref != envelope.artifact.response_ref
            or replacement.supersedes_artifact_hash != envelope.artifact.artifact_hash
            or replacement.supersedes_version != envelope.artifact.response_version
            or replacement.conversation_ref != envelope.artifact.conversation_ref
            or replacement.response_version != envelope.artifact.response_version + 1
        ):
            raise ResponseVersionRejected("replacement does not extend the response version")
        idempotency_key = f"response-superseded:{replacement.response_ref}"
        replay = self._find_replay(envelope, idempotency_key)
        if replay is not None:
            if (
                replay.to_status is not ResponseLifecycleStatus.SUPERSEDED
                or replay.cause_ref != replacement.artifact_ref
            ):
                raise ResponseVersionRejected("response lifecycle idempotency key was reused")
            return envelope, True
        if envelope.current_status not in {
            ResponseLifecycleStatus.COMMITTED,
            ResponseLifecycleStatus.STALE,
        }:
            raise ResponseVersionRejected("only a current or stale response can be superseded")
        event = ResponseLifecycleEvent.build(
            response_ref=envelope.artifact.response_ref,
            sequence_no=len(envelope.lifecycle_events) + 1,
            idempotency_key=idempotency_key,
            from_status=envelope.current_status,
            to_status=ResponseLifecycleStatus.SUPERSEDED,
            reason=ResponseLifecycleReason.REPLACED_BY_RESPONSE,
            cause_ref=replacement.artifact_ref,
            previous_event_hash=envelope.lifecycle_events[-1].event_hash,
            occurred_at=occurred_at,
        )
        return self._append(envelope, event), False

    @staticmethod
    def _append(
        envelope: ResponsePersistenceEnvelope,
        event: ResponseLifecycleEvent,
    ) -> ResponsePersistenceEnvelope:
        return ResponsePersistenceEnvelope.build(
            artifact=envelope.artifact,
            lifecycle_events=(*envelope.lifecycle_events, event),
        )

    @staticmethod
    def _find_replay(
        envelope: ResponsePersistenceEnvelope,
        idempotency_key: str,
    ) -> ResponseLifecycleEvent | None:
        return next(
            (
                event
                for event in envelope.lifecycle_events
                if event.idempotency_key == idempotency_key
            ),
            None,
        )
