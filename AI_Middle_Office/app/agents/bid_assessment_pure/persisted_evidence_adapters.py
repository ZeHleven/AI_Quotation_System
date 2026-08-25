"""Fail-closed projection of persisted ``evidence_read`` results.

Only successful Tool results whose provenance still matches every Evidence Atom
are exposed as citable authority.  Search candidates and legacy receipt-only
results intentionally remain non-citable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .common import Reference, StrictContract
from .repository import PersistedObservationArtifactRow, PureAgentRepository
from .registry import EVIDENCE_READ
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import ToolProvenanceRecord, canonical_hash, canonical_json
from .tools import EvidenceReadOutput


class PersistedEvidenceArtifactRejected(RuntimeError):
    """A persisted citable artifact no longer satisfies its source contract."""


@dataclass(frozen=True, slots=True)
class PersistedEvidenceAtomAuthority:
    evidence_ref: str
    text: str
    locator: str
    source_domain: str
    source_scope_ref: str
    source_version_ref: str
    content_hash: str
    observation_ref: str

    def context_content(self) -> str:
        return canonical_json(
            {
                "evidence_ref": self.evidence_ref,
                "text": self.text,
                "locator": self.locator,
                "citable": True,
            }
        )


class PersistedPriorAnswerEvidenceLineage(StrictContract):
    """Hash-bound evidence refs inherited from one committed prior Answer."""

    schema_name: Literal["bid.pure-agent.prior-answer-evidence-lineage.v1"] = (
        "bid.pure-agent.prior-answer-evidence-lineage.v1"
    )
    message_ref: Reference
    response_ref: Reference
    response_artifact_ref: Reference
    response_artifact_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prior_task_ref: Reference
    answer_observation_ref: Reference
    evidence_refs: tuple[Reference, ...] = Field(min_length=1, max_length=128)
    lineage_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def build(cls, **body: object) -> "PersistedPriorAnswerEvidenceLineage":
        payload = {
            "schema_name": "bid.pure-agent.prior-answer-evidence-lineage.v1",
            **body,
        }
        return cls(**payload, lineage_hash=canonical_hash(payload))

    @model_validator(mode="after")
    def validate_lineage(self) -> "PersistedPriorAnswerEvidenceLineage":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("prior Answer evidence refs must be unique")
        body = self.model_dump(mode="json", exclude={"lineage_hash"})
        if self.lineage_hash != canonical_hash(body):
            raise ValueError("prior Answer evidence lineage hash drifted")
        return self


def load_prior_answer_evidence_lineage(
    *,
    repository: PureAgentRepository,
    current_task: AgentTaskState,
    message_ref: str,
    allowed_scope_refs: tuple[str, ...],
) -> tuple[
    PersistedPriorAnswerEvidenceLineage | None,
    tuple[PersistedEvidenceAtomAuthority, ...],
]:
    """Revalidate one prior committed Answer and return only its used atoms."""

    response_row = repository.load_context_committed_response(
        task_id=current_task.task_id,
        conversation_id=current_task.session_id,
        message_id=message_ref,
    )
    artifact = response_row.envelope.artifact
    prior_task = repository.load_task_state(response_row.response_task_ref)
    if (
        prior_task.status is not AgentTaskStatus.COMPLETED
        or prior_task.session_id != current_task.session_id
        or artifact.task_ref != prior_task.task_id
    ):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer evidence lineage belongs to an invalid Task"
        )
    answer_artifact = repository.load_context_observation_artifact(
        task_id=prior_task.task_id,
        observation_ref=artifact.answer_observation_ref,
    )
    if (
        answer_artifact.observation.observation_hash
        != artifact.answer_observation_hash
        or answer_artifact.observation.source_action_ref
        != artifact.answer_action_ref
    ):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer observation drifted from its committed response"
        )
    payload = answer_artifact.artifact
    if not isinstance(payload, dict) or payload.get("schema_name") != (
        "bid.pure-agent.capability.answer-result.v1"
    ):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer result artifact is unavailable"
        )
    validation = payload.get("validation")
    execution_draft = payload.get("execution_draft")
    if (
        not isinstance(validation, dict)
        or validation.get("accepted") is not True
        or not isinstance(execution_draft, dict)
        or canonical_hash(validation) != artifact.draft_validation_hash
        or canonical_hash(execution_draft) != artifact.draft_hash
    ):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer validation lineage failed its committed hashes"
        )
    raw_refs = validation.get("validated_grounding_refs")
    if not isinstance(raw_refs, list) or any(
        not isinstance(item, str) for item in raw_refs
    ):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer validated Grounding refs are malformed"
        )
    validated_refs = tuple(dict.fromkeys(raw_refs))
    if len(validated_refs) != len(raw_refs):
        raise PersistedEvidenceArtifactRejected(
            "prior Answer validated Grounding refs are duplicated"
        )

    authorities: list[PersistedEvidenceAtomAuthority] = []
    for observation_ref in prior_task.observation_refs:
        observation = repository.load_context_observation_artifact(
            task_id=prior_task.task_id,
            observation_ref=observation_ref,
        )
        authorities.extend(extract_persisted_evidence_atoms(observation))
    indexed = index_persisted_evidence_atoms(authorities)
    allowed_scopes = set(allowed_scope_refs)
    selected = tuple(
        indexed[grounding_ref]
        for grounding_ref in validated_refs
        if grounding_ref in indexed
        and indexed[grounding_ref].source_scope_ref in allowed_scopes
    )
    if not selected:
        return None, ()
    if len(selected) > 128:
        raise PersistedEvidenceArtifactRejected(
            "prior Answer evidence lineage exceeds the Context limit"
        )
    lineage = PersistedPriorAnswerEvidenceLineage.build(
        message_ref=message_ref,
        response_ref=artifact.response_ref,
        response_artifact_ref=artifact.artifact_ref,
        response_artifact_hash=artifact.artifact_hash,
        prior_task_ref=prior_task.task_id,
        answer_observation_ref=artifact.answer_observation_ref,
        evidence_refs=tuple(item.evidence_ref for item in selected),
    )
    return lineage, selected


def index_persisted_evidence_atoms(
    authorities: Iterable[PersistedEvidenceAtomAuthority],
) -> dict[str, PersistedEvidenceAtomAuthority]:
    """Keep one identical authority per ref and reject cross-receipt drift."""

    indexed: dict[str, PersistedEvidenceAtomAuthority] = {}
    for authority in authorities:
        existing = indexed.get(authority.evidence_ref)
        if existing is None:
            indexed[authority.evidence_ref] = authority
            continue
        if _evidence_authority_identity(existing) != _evidence_authority_identity(
            authority
        ):
            raise PersistedEvidenceArtifactRejected(
                "persisted Evidence Atom authority conflicts across observations"
            )
    return indexed


def _evidence_authority_identity(
    authority: PersistedEvidenceAtomAuthority,
) -> tuple[str, ...]:
    return (
        authority.evidence_ref,
        authority.text,
        authority.locator,
        authority.source_domain,
        authority.source_scope_ref,
        authority.source_version_ref,
        authority.content_hash,
    )


def extract_persisted_evidence_atoms(
    artifact: PersistedObservationArtifactRow,
) -> tuple[PersistedEvidenceAtomAuthority, ...]:
    """Return revalidated citable atoms from one accepted Observation Artifact."""

    payload = artifact.artifact
    if not isinstance(payload, dict) or payload.get("schema_name") != (
        "bid.pure-agent.capability.tool-batch-result.v1"
    ):
        return ()
    calls = payload.get("calls")
    if not isinstance(calls, list):
        return ()

    atoms: list[PersistedEvidenceAtomAuthority] = []
    seen: set[str] = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("tool_name") != EVIDENCE_READ:
            continue
        provenance_payload = call.get("provenance")
        if provenance_payload in (None, []):
            # Results written before C04-2 remain usable as receipts, never evidence.
            continue
        if (
            call.get("accepted_for_context") is not True
            or not isinstance(provenance_payload, list)
        ):
            raise PersistedEvidenceArtifactRejected(
                "persisted evidence provenance is outside an accepted Tool result"
            )
        result = call.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise PersistedEvidenceArtifactRejected(
                "persisted evidence provenance belongs to an unsuccessful Tool result"
            )
        try:
            output = EvidenceReadOutput.model_validate_json(
                canonical_json(result.get("data"))
            )
            provenance = tuple(
                ToolProvenanceRecord.model_validate_json(canonical_json(item))
                for item in provenance_payload
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise PersistedEvidenceArtifactRejected(
                "persisted evidence output failed canonical validation"
            ) from exc

        output_by_ref = {item.evidence_ref: item for item in output.evidence}
        provenance_by_ref = {item.output_ref: item for item in provenance}
        if (
            len(output_by_ref) != len(output.evidence)
            or len(provenance_by_ref) != len(provenance)
            or set(output_by_ref) != set(provenance_by_ref)
        ):
            raise PersistedEvidenceArtifactRejected(
                "persisted evidence provenance is incomplete or duplicated"
            )
        for evidence_ref, atom in output_by_ref.items():
            record = provenance_by_ref[evidence_ref]
            if (
                evidence_ref in seen
                or not record.citable
                or record.locator != atom.locator
                or record.content_hash != canonical_hash(atom.text)
            ):
                raise PersistedEvidenceArtifactRejected(
                    "persisted Evidence Atom no longer matches its provenance"
                )
            seen.add(evidence_ref)
            atoms.append(
                PersistedEvidenceAtomAuthority(
                    evidence_ref=evidence_ref,
                    text=atom.text,
                    locator=atom.locator,
                    source_domain=record.source_domain,
                    source_scope_ref=record.source_scope_ref,
                    source_version_ref=record.source_version_ref,
                    content_hash=record.content_hash,
                    observation_ref=artifact.observation.observation_ref,
                )
            )
    return tuple(atoms)
