"""Deterministic retrieval convergence for Provider Boundary V2.

Tool execution success is not evidence progress.  This module derives stable
semantic signals from persisted Tool results and decides when further retrieval
must stop.  It has no Provider, database, or external-data side effects.
"""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Mapping

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .runtime import ContextAssemblyResult, ContextEntryKind, ContextProjectionEntry
from .tool_runtime import canonical_hash


_TOOL_BATCH_SCHEMA = "bid.pure-agent.capability.tool-batch-result.v1"
_SEMANTIC_SIGNAL_PREFIX = "retrieval-signal:"


class RetrievalConvergenceReason(str, Enum):
    NO_NOVEL_INFORMATION_STREAK = "no_novel_information_streak"
    TOOL_BATCH_LIMIT_REACHED = "tool_batch_limit_reached"


class RetrievalConvergencePolicyV2(StrictContract):
    """Small deterministic limits; V2 remains behind its existing off switch."""

    max_consecutive_no_novelty_batches: int = Field(default=2, ge=1, le=8)
    max_tool_batches: int = Field(default=8, ge=1, le=64)
    max_tool_observations_in_terminal_context: int = Field(
        default=4,
        ge=1,
        le=16,
    )


class RetrievalConvergenceDecisionV2(StrictContract):
    schema_name: str = "bid.pure-agent.retrieval-convergence.v2"
    saturated: bool
    reason_codes: tuple[RetrievalConvergenceReason, ...] = Field(
        default_factory=tuple,
        max_length=4,
    )
    tool_batch_count: int = Field(ge=0, le=500)
    consecutive_no_novelty_batches: int = Field(ge=0, le=500)
    unique_semantic_signal_count: int = Field(ge=0)
    evidence_atom_count: int = Field(ge=0)
    latest_tool_action_sequence: int | None = Field(default=None, ge=1)
    decision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        saturated: bool,
        reason_codes: tuple[RetrievalConvergenceReason, ...],
        tool_batch_count: int,
        consecutive_no_novelty_batches: int,
        unique_semantic_signal_count: int,
        evidence_atom_count: int,
        latest_tool_action_sequence: int | None,
    ) -> "RetrievalConvergenceDecisionV2":
        body = {
            "schema_name": "bid.pure-agent.retrieval-convergence.v2",
            "saturated": saturated,
            "reason_codes": [item.value for item in reason_codes],
            "tool_batch_count": tool_batch_count,
            "consecutive_no_novelty_batches": (
                consecutive_no_novelty_batches
            ),
            "unique_semantic_signal_count": unique_semantic_signal_count,
            "evidence_atom_count": evidence_atom_count,
            "latest_tool_action_sequence": latest_tool_action_sequence,
        }
        return cls(**body, decision_hash=canonical_hash(body))

    @model_validator(mode="after")
    def validate_decision(self) -> "RetrievalConvergenceDecisionV2":
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("retrieval convergence reasons must be unique")
        if self.saturated != bool(self.reason_codes):
            raise ValueError("saturated state must match its reason codes")
        body = self.model_dump(mode="json", exclude={"decision_hash"})
        if self.decision_hash != canonical_hash(body):
            raise ValueError("retrieval convergence decision hash drifted")
        return self


class RetrievalConvergenceGateV2:
    """Evaluate persisted, model-visible Tool observations for novelty."""

    def __init__(
        self,
        policy: RetrievalConvergencePolicyV2 | None = None,
    ) -> None:
        self.policy = policy or RetrievalConvergencePolicyV2()

    def evaluate(
        self,
        context: ContextAssemblyResult,
    ) -> RetrievalConvergenceDecisionV2:
        batches = sorted(
            (
                item
                for entry in context.projection_entries
                if (item := tool_batch_observation(entry)) is not None
            ),
            key=lambda item: item[0],
        )
        known_signals: set[str] = set()
        no_novelty_streak = 0
        for _, signals in batches:
            novel = tuple(signal for signal in signals if signal not in known_signals)
            known_signals.update(signals)
            no_novelty_streak = 0 if novel else no_novelty_streak + 1

        reasons: list[RetrievalConvergenceReason] = []
        if (
            no_novelty_streak
            >= self.policy.max_consecutive_no_novelty_batches
        ):
            reasons.append(
                RetrievalConvergenceReason.NO_NOVEL_INFORMATION_STREAK
            )
        if len(batches) >= self.policy.max_tool_batches:
            reasons.append(RetrievalConvergenceReason.TOOL_BATCH_LIMIT_REACHED)

        evidence_atom_count = len(
            {
                entry.entry_ref
                for entry in context.projection_entries
                if entry.kind is ContextEntryKind.EVIDENCE_ATOM
            }
        )
        return RetrievalConvergenceDecisionV2.build(
            saturated=bool(reasons),
            reason_codes=tuple(reasons),
            tool_batch_count=len(batches),
            consecutive_no_novelty_batches=no_novelty_streak,
            unique_semantic_signal_count=len(known_signals),
            evidence_atom_count=evidence_atom_count,
            latest_tool_action_sequence=(batches[-1][0] if batches else None),
        )


def semantic_progress_signal_refs_from_tool_batch(
    artifact: Mapping[str, Any],
    *,
    max_signals: int = 128,
) -> tuple[Reference, ...]:
    """Return content-derived signals, never per-call identities."""

    if artifact.get("schema_name") != _TOOL_BATCH_SCHEMA:
        return ()
    raw_calls = artifact.get("calls")
    if not isinstance(raw_calls, (list, tuple)):
        return ()
    signals: dict[str, None] = {}
    for call in raw_calls:
        if not isinstance(call, Mapping) or call.get("accepted_for_context") is not True:
            continue
        result = call.get("result")
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            continue
        data = result.get("data")
        if not isinstance(data, Mapping):
            continue
        _append_data_signals(
            signals,
            tool_name=str(call.get("tool_name") or "unknown"),
            data=data,
        )
        if len(signals) >= max_signals:
            break
    return tuple(signals)[:max_signals]


def tool_batch_observation(
    entry: ContextProjectionEntry,
) -> tuple[int, tuple[Reference, ...]] | None:
    """Project one compact persisted Tool observation into ordered signals."""

    if entry.kind is not ContextEntryKind.OBSERVATION:
        return None
    try:
        payload = json.loads(entry.content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    observation = payload.get("observation")
    projection = payload.get("artifact_projection")
    if (
        not isinstance(observation, dict)
        or observation.get("kind") != "tool_result"
        or not isinstance(projection, dict)
        or projection.get("projection_kind") != "tool_batch_result"
    ):
        return None
    sequence = observation.get("action_sequence")
    if not isinstance(sequence, int) or sequence < 1:
        return None

    declared = observation.get("progress_signal_refs")
    semantic_declared = tuple(
        str(value)
        for value in (declared if isinstance(declared, list) else ())
        if isinstance(value, str) and value.startswith(_SEMANTIC_SIGNAL_PREFIX)
    )
    if semantic_declared:
        return sequence, tuple(dict.fromkeys(semantic_declared))

    compact_calls = projection.get("calls")
    if not isinstance(compact_calls, list):
        return sequence, ()
    artifact = {
        "schema_name": _TOOL_BATCH_SCHEMA,
        "calls": [
            _expand_compact_call(call)
            for call in compact_calls
            if isinstance(call, dict)
        ],
    }
    return sequence, semantic_progress_signal_refs_from_tool_batch(artifact)


def _expand_compact_call(call: Mapping[str, Any]) -> dict[str, Any]:
    compact_result = call.get("result")
    projection = (
        compact_result.get("data_projection")
        if isinstance(compact_result, Mapping)
        else None
    )
    data: dict[str, Any] = {}
    if isinstance(projection, Mapping):
        kind = projection.get("kind")
        if kind == "search_candidates":
            data["candidates"] = projection.get("candidates") or []
        elif kind == "evidence_read_receipts":
            data["evidence"] = projection.get("evidence") or []
        elif kind == "document_outline":
            data["entries"] = projection.get("entries") or []
    return {
        "tool_name": call.get("tool_name"),
        "accepted_for_context": call.get("accepted_for_context") is True,
        "result": {"ok": True, "data": data},
    }


def _append_data_signals(
    signals: dict[str, None],
    *,
    tool_name: str,
    data: Mapping[str, Any],
) -> None:
    candidates = data.get("candidates")
    if isinstance(candidates, (list, tuple)):
        for item in candidates:
            if isinstance(item, Mapping) and item.get("evidence_ref"):
                _append_signal(
                    signals,
                    "candidate",
                    {"evidence_ref": str(item["evidence_ref"])},
                )
        return
    evidence = data.get("evidence")
    if isinstance(evidence, (list, tuple)):
        for item in evidence:
            if isinstance(item, Mapping) and item.get("evidence_ref"):
                _append_signal(
                    signals,
                    "evidence_atom",
                    {"evidence_ref": str(item["evidence_ref"])},
                )
        return
    entries = data.get("entries")
    if isinstance(entries, (list, tuple)):
        for item in entries:
            if isinstance(item, Mapping):
                _append_signal(
                    signals,
                    "outline_entry",
                    {
                        "tool_name": tool_name,
                        "title": item.get("title"),
                        "level": item.get("level"),
                        "locator": item.get("locator"),
                    },
                )


def _append_signal(
    signals: dict[str, None],
    signal_kind: str,
    identity: Mapping[str, Any],
) -> None:
    digest = canonical_hash(
        {"signal_kind": signal_kind, "identity": dict(identity)}
    ).removeprefix("sha256:")
    signals.setdefault(f"{_SEMANTIC_SIGNAL_PREFIX}{digest}", None)
