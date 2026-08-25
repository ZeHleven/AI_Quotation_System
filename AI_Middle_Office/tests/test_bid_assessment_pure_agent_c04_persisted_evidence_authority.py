from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agents.bid_assessment_pure.action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
)
from app.agents.bid_assessment_pure.context_runtime import ContextSourceUnavailable
from app.agents.bid_assessment_pure.persisted_capability_adapters import (
    PersistedAnswerAuthorityErrorCode,
    PersistedAnswerAuthorityRejected,
    PersistedEvidenceAnswerAuthorityProjector,
)
from app.agents.bid_assessment_pure.persisted_context_adapters import (
    PersistedContextCandidateSource,
    PersistedContextProjectionPolicy,
)
from app.agents.bid_assessment_pure.persisted_evidence_adapters import (
    PersistedEvidenceArtifactRejected,
    extract_persisted_evidence_atoms,
    load_prior_answer_evidence_lineage,
)
from app.agents.bid_assessment_pure.planning import ExecutionMode
from app.agents.bid_assessment_pure.repository import (
    PersistedObservationArtifactRow,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextAssemblyRequest,
    ContextConsumer,
    ContextEntryKind,
)
from app.agents.bid_assessment_pure.state import AgentTaskState, AgentTaskStatus
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash, canonical_json


def _artifact() -> PersistedObservationArtifactRow:
    text = "投标截止时间为2026年9月1日09时30分。"
    payload = {
        "schema_name": "bid.pure-agent.capability.tool-batch-result.v1",
        "calls": [
            {
                "call_ref": "tool-call:c04-evidence-read",
                "tool_name": "evidence_read",
                "result": {
                    "ok": True,
                    "data": {
                        "evidence": [
                            {
                                "evidence_ref": "evidence:c04-deadline",
                                "text": text,
                                "locator": "第12页",
                                "citable": True,
                            }
                        ]
                    },
                    "error": None,
                },
                "tool_message": None,
                "ledger_call_id": "ledger:c04-evidence-read",
                "accepted_for_context": True,
                "guard_decisions": [],
                "replayed": False,
                "provenance": [
                    {
                        "output_ref": "evidence:c04-deadline",
                        "source_domain": "bid_document",
                        "source_scope_ref": "document:c04-bid",
                        "source_version_ref": "document-version:c04-bid",
                        "content_hash": canonical_hash(text),
                        "locator": "第12页",
                        "citable": True,
                    }
                ],
            }
        ],
    }
    observation_body = {
        "task_ref": "task:c04-evidence",
        "source_action_ref": "action:c04-evidence-read",
        "action_sequence": 1,
        "state_version": 3,
        "kind": ActionObservationKind.TOOL_RESULT,
        "status": ActionObservationStatus.SUCCEEDED,
        "artifact_ref": "tool-batch-result:c04-evidence-read",
        "artifact_hash": canonical_hash(payload),
        "summary": "evidence_read succeeded",
        "material_progress": True,
        "progress_signal_refs": ("evidence:c04-deadline",),
        "limitation_codes": (),
    }
    observation_hash = canonical_hash(observation_body)
    observation = ActionObservation(
        **observation_body,
        observation_ref=(
            f"observation:{observation_hash.removeprefix('sha256:')}"
        ),
        observation_hash=observation_hash,
    )
    return PersistedObservationArtifactRow(
        observation=observation,
        artifact=payload,
        context_snapshot_ref="context:c04-evidence",
    )


def test_persisted_evidence_authority_accepts_exact_provenance() -> None:
    atoms = extract_persisted_evidence_atoms(_artifact())

    assert len(atoms) == 1
    assert atoms[0].evidence_ref == "evidence:c04-deadline"
    assert atoms[0].source_scope_ref == "document:c04-bid"
    assert atoms[0].locator == "第12页"


def test_persisted_evidence_authority_rejects_tampered_text() -> None:
    artifact = _artifact()
    payload = artifact.artifact.copy()
    payload["calls"] = [artifact.artifact["calls"][0].copy()]
    payload["calls"][0]["result"] = {
        **payload["calls"][0]["result"],
        "data": {
            "evidence": [
                {
                    "evidence_ref": "evidence:c04-deadline",
                    "text": "被篡改的时间。",
                    "locator": "第12页",
                    "citable": True,
                }
            ]
        },
    }

    with pytest.raises(PersistedEvidenceArtifactRejected, match="provenance"):
        extract_persisted_evidence_atoms(replace(artifact, artifact=payload))


class _ObservationRepository:
    def __init__(self, *artifacts: PersistedObservationArtifactRow) -> None:
        self._artifacts = {
            artifact.observation.observation_ref: artifact for artifact in artifacts
        }

    def load_context_observation_artifact(
        self,
        *,
        task_id: str,
        observation_ref: str,
    ) -> PersistedObservationArtifactRow:
        assert task_id == "task:c04-evidence"
        return self._artifacts[observation_ref]


def _repeated_artifact(
    artifact: PersistedObservationArtifactRow,
    *,
    conflicting_source_version: bool = False,
) -> PersistedObservationArtifactRow:
    payload = deepcopy(artifact.artifact)
    if conflicting_source_version:
        payload["calls"][0]["provenance"][0]["source_version_ref"] = (
            "document-version:c04-conflict"
        )
    observation = artifact.observation.model_copy(
        update={
            "observation_ref": "observation:c04-evidence-repeat",
            "source_action_ref": "action:c04-evidence-read-repeat",
            "action_sequence": 2,
            "state_version": 5,
        }
    )
    return replace(artifact, observation=observation, artifact=payload)


def _context_source_for(
    *artifacts: PersistedObservationArtifactRow,
) -> PersistedContextCandidateSource:
    return PersistedContextCandidateSource(
        _ObservationRepository(*artifacts),
        policy=PersistedContextProjectionPolicy(
            policy_snapshot_ref="policy:c04-evidence",
            prompt_template_ref="prompt:c04-evidence",
            system_policy="Test policy",
            output_contract="Test output contract",
            registry_snapshot=None,
        ),
    )


def _context_task(*artifacts: PersistedObservationArtifactRow) -> AgentTaskState:
    return AgentTaskState(
        task_id="task:c04-evidence",
        session_id="conversation:c04-evidence",
        state_version=7,
        status=AgentTaskStatus.RUNNING,
        execution_mode=ExecutionMode.DIRECT,
        goal_ref="goal:c04-evidence",
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref=None,
        observation_refs=tuple(
            artifact.observation.observation_ref for artifact in artifacts
        ),
        last_error_ref=None,
    )


def _context_request() -> ContextAssemblyRequest:
    return ContextAssemblyRequest(
        task_ref="task:c04-evidence",
        state_version=7,
        consumer=ContextConsumer.MAIN_AGENT,
        user_message_ref="message:c04-evidence",
        visible_tool_names=(),
        information_need_refs=(),
        required_resource_refs=(),
        policy_snapshot_ref="policy:c04-evidence",
        prompt_template_ref="prompt:c04-evidence",
        registry_snapshot_ref=None,
        model_profile_ref="model-profile:c04-evidence",
        context_profile_ref="context-profile:c04-evidence",
        checkpoint_snapshot_ref=None,
        authorization_snapshot_ref="authorization:c04-evidence",
        snapshot_sequence=7,
    )


def test_context_source_deduplicates_identical_evidence_across_observations() -> None:
    first = _artifact()
    repeated = _repeated_artifact(first)
    source = _context_source_for(first, repeated)

    candidates = source._observation_candidates(
        task=_context_task(first, repeated),
        request=_context_request(),
    )

    evidence = tuple(
        candidate
        for candidate in candidates
        if candidate.kind is ContextEntryKind.EVIDENCE_ATOM
    )
    observations = tuple(
        candidate
        for candidate in candidates
        if candidate.kind is ContextEntryKind.OBSERVATION
    )
    assert len(evidence) == 1
    assert evidence[0].entry_ref == "evidence:c04-deadline"
    assert len(observations) == 2


def test_context_source_rejects_conflicting_evidence_across_observations() -> None:
    first = _artifact()
    conflicting = _repeated_artifact(first, conflicting_source_version=True)
    source = _context_source_for(first, conflicting)

    with pytest.raises(ContextSourceUnavailable, match="authority is invalid"):
        source._observation_candidates(
            task=_context_task(first, conflicting),
            request=_context_request(),
        )


def test_answer_authority_deduplicates_identical_evidence_across_observations() -> None:
    first = _artifact()
    repeated = _repeated_artifact(first)
    projector = PersistedEvidenceAnswerAuthorityProjector(
        _ObservationRepository(first, repeated)
    )

    authorities = projector._load_authorities(_context_task(first, repeated))

    assert tuple(authorities) == ("evidence:c04-deadline",)
    assert authorities["evidence:c04-deadline"].observation_ref == (
        first.observation.observation_ref
    )


def test_answer_authority_rejects_conflicting_evidence_across_observations() -> None:
    first = _artifact()
    conflicting = _repeated_artifact(first, conflicting_source_version=True)
    projector = PersistedEvidenceAnswerAuthorityProjector(
        _ObservationRepository(first, conflicting)
    )

    with pytest.raises(
        PersistedAnswerAuthorityRejected,
        match="could not be verified",
    ) as captured:
        projector._load_authorities(_context_task(first, conflicting))

    assert captured.value.failure.code is (
        PersistedAnswerAuthorityErrorCode.EVIDENCE_AUTHORITY_UNVERIFIED
    )


class _PriorAnswerRepository:
    def __init__(self) -> None:
        self.evidence_artifact = _artifact()
        validation = {
            "accepted": True,
            "validated_grounding_refs": ["evidence:c04-deadline"],
        }
        execution_draft = {
            "response_language": "zh-CN",
            "blocks": [],
            "context_snapshot_ref": "context:c04-prior-answer",
            "state_version": 7,
        }
        answer_payload = {
            "schema_name": "bid.pure-agent.capability.answer-result.v1",
            "status": "accepted",
            "execution_draft": execution_draft,
            "validation": validation,
        }
        answer_observation_body = {
            "task_ref": "task:c04-evidence",
            "source_action_ref": "action:c04-prior-answer",
            "action_sequence": 3,
            "state_version": 7,
            "kind": ActionObservationKind.ANSWER_DRAFT,
            "status": ActionObservationStatus.SUCCEEDED,
            "artifact_ref": "answer-result:c04-prior-answer",
            "artifact_hash": canonical_hash(answer_payload),
            "summary": "prior answer accepted",
            "material_progress": True,
            "progress_signal_refs": ("answer-draft:c04-prior",),
            "limitation_codes": (),
        }
        answer_observation_hash = canonical_hash(answer_observation_body)
        answer_observation = ActionObservation(
            **answer_observation_body,
            observation_ref=(
                "observation:"
                + answer_observation_hash.removeprefix("sha256:")
            ),
            observation_hash=answer_observation_hash,
        )
        self.answer_artifact = PersistedObservationArtifactRow(
            observation=answer_observation,
            artifact=answer_payload,
            context_snapshot_ref="context:c04-prior-answer",
        )
        self.prior_task = AgentTaskState(
            task_id="task:c04-evidence",
            session_id="conversation:c04-evidence",
            state_version=7,
            status=AgentTaskStatus.COMPLETED,
            execution_mode=ExecutionMode.DIRECT,
            goal_ref="goal:c04-prior",
            plan_ref=None,
            pending_context=None,
            in_flight_action_ref=None,
            observation_refs=(
                self.evidence_artifact.observation.observation_ref,
                self.answer_artifact.observation.observation_ref,
            ),
            last_error_ref=None,
        )
        self.response_artifact = SimpleNamespace(
            response_ref="response:c04-prior",
            artifact_ref="response-artifact:c04-prior",
            artifact_hash=canonical_hash({"response": "c04-prior"}),
            task_ref=self.prior_task.task_id,
            answer_observation_ref=answer_observation.observation_ref,
            answer_observation_hash=answer_observation.observation_hash,
            answer_action_ref=answer_observation.source_action_ref,
            draft_validation_hash=canonical_hash(validation),
            draft_hash=canonical_hash(execution_draft),
        )

    def load_context_committed_response(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
    ) -> SimpleNamespace:
        assert task_id == "task:c04-followup"
        assert conversation_id == self.prior_task.session_id
        assert message_id == "message:c04-prior-answer"
        return SimpleNamespace(
            response_task_ref=self.prior_task.task_id,
            envelope=SimpleNamespace(artifact=self.response_artifact),
        )

    def load_task_state(self, task_id: str) -> AgentTaskState:
        assert task_id == self.prior_task.task_id
        return self.prior_task

    def load_context_observation_artifact(
        self,
        *,
        task_id: str,
        observation_ref: str,
    ) -> PersistedObservationArtifactRow:
        assert task_id == self.prior_task.task_id
        artifacts = {
            self.evidence_artifact.observation.observation_ref: (
                self.evidence_artifact
            ),
            self.answer_artifact.observation.observation_ref: self.answer_artifact,
        }
        return artifacts[observation_ref]


def _followup_task() -> AgentTaskState:
    return AgentTaskState(
        task_id="task:c04-followup",
        session_id="conversation:c04-evidence",
        state_version=1,
        status=AgentTaskStatus.RUNNING,
        execution_mode=ExecutionMode.DIRECT,
        goal_ref="goal:c04-followup",
        plan_ref=None,
        pending_context=None,
        in_flight_action_ref=None,
        observation_refs=(),
        last_error_ref=None,
    )


def test_prior_committed_answer_rehydrates_only_validated_evidence_lineage() -> None:
    repository = _PriorAnswerRepository()
    current_task = _followup_task()

    lineage, selected = load_prior_answer_evidence_lineage(
        repository=repository,  # type: ignore[arg-type]
        current_task=current_task,
        message_ref="message:c04-prior-answer",
        allowed_scope_refs=("document:c04-bid",),
    )

    assert lineage is not None
    assert lineage.prior_task_ref == repository.prior_task.task_id
    assert lineage.evidence_refs == ("evidence:c04-deadline",)
    assert tuple(item.evidence_ref for item in selected) == lineage.evidence_refs

    context = SimpleNamespace(
        projection_entries=(
            SimpleNamespace(
                kind=ContextEntryKind.GROUNDING,
                authority_label="authorized-resource-receipt",
                source_ref="document:c04-bid",
            ),
            SimpleNamespace(
                kind=ContextEntryKind.EVIDENCE_PARENT,
                authority_label="persisted-prior-answer-evidence-lineage",
                source_ref=lineage.response_ref,
                source_version_ref=lineage.response_artifact_ref,
                source_content_hash=canonical_hash(lineage),
                content=canonical_json(lineage),
            ),
        )
    )
    authorities = PersistedEvidenceAnswerAuthorityProjector(
        repository  # type: ignore[arg-type]
    )._load_authorities(
        current_task,
        context,  # type: ignore[arg-type]
    )

    assert tuple(authorities) == ("evidence:c04-deadline",)
    assert authorities["evidence:c04-deadline"].source_scope_ref == (
        "document:c04-bid"
    )


def test_prior_answer_evidence_is_not_inherited_across_resource_scope() -> None:
    repository = _PriorAnswerRepository()

    lineage, selected = load_prior_answer_evidence_lineage(
        repository=repository,  # type: ignore[arg-type]
        current_task=_followup_task(),
        message_ref="message:c04-prior-answer",
        allowed_scope_refs=("document:another-bid",),
    )

    assert lineage is None
    assert selected == ()
