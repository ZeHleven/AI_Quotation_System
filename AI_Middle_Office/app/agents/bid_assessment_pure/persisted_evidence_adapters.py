"""Fail-closed projection of persisted ``evidence_read`` results.

Only successful Tool results whose provenance still matches every Evidence Atom
are exposed as citable authority.  Search candidates and legacy receipt-only
results intentionally remain non-citable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import ValidationError

from .repository import PersistedObservationArtifactRow
from .registry import EVIDENCE_READ
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
