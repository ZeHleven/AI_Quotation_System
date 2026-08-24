from __future__ import annotations

from dataclasses import replace

import pytest

from app.agents.bid_assessment_pure.action_runtime import (
    ActionObservation,
    ActionObservationKind,
    ActionObservationStatus,
)
from app.agents.bid_assessment_pure.persisted_evidence_adapters import (
    PersistedEvidenceArtifactRejected,
    extract_persisted_evidence_atoms,
)
from app.agents.bid_assessment_pure.repository import (
    PersistedObservationArtifactRow,
)
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash


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
