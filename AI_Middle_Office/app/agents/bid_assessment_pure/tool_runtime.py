"""Shared B03 tool-runtime contracts and deterministic registry snapshots.

This module is side-effect free. It does not resolve a handler, open a database,
contact MCP, or execute a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import Reference, StrictContract, ToolName, validate_public_locator
from .registry import CanonicalToolRegistry
from .tools import ModelVisibleToolContract, ToolSafety


Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def canonical_json(value: Any) -> str:
    """Serialize a validated runtime value deterministically for hashing/ledger use."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ToolSnapshotEntry(StrictContract):
    name: ToolName
    definition_hash: Sha256Digest
    input_schema_hash: Sha256Digest
    output_schema_hash: Sha256Digest
    binding_hash: Sha256Digest
    safety_hash: Sha256Digest
    execution_kind: Literal["disabled", "local", "mcp"]
    safety: ToolSafety
    model_contract: ModelVisibleToolContract


class RegistrySnapshot(StrictContract):
    snapshot_ref: Reference
    snapshot_hash: Sha256Digest
    entries: tuple[ToolSnapshotEntry, ...] = Field(min_length=1, max_length=64)
    visible_tool_names: tuple[ToolName, ...] = Field(default_factory=tuple, max_length=32)
    visible_tools_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_snapshot(self) -> "RegistrySnapshot":
        names = tuple(entry.name for entry in self.entries)
        if len(names) != len(set(names)):
            raise ValueError("registry snapshot tool names must be unique")
        if len(self.visible_tool_names) != len(set(self.visible_tool_names)):
            raise ValueError("visible tool names must be unique")
        if not set(self.visible_tool_names).issubset(set(names)):
            raise ValueError("visible tools must exist in the frozen registry")
        if self.visible_tools_hash != canonical_hash(list(self.visible_tool_names)):
            raise ValueError("visible_tools_hash does not match visible_tool_names")
        return self

    def entry(self, name: str) -> ToolSnapshotEntry:
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise LookupError(f"tool is not present in registry snapshot: {name}")

    def model_visible_contracts(self) -> tuple[ModelVisibleToolContract, ...]:
        indexed = {entry.name: entry.model_contract for entry in self.entries}
        return tuple(indexed[name] for name in self.visible_tool_names)


def freeze_registry_snapshot(
    registry: CanonicalToolRegistry,
    *,
    visible_names: tuple[str, ...],
) -> RegistrySnapshot:
    """Freeze definitions and the per-turn relevance/permission projection."""

    if len(visible_names) != len(set(visible_names)):
        raise ValueError("visible tool names must be unique")
    for name in visible_names:
        registry.get(name)

    entries: list[ToolSnapshotEntry] = []
    for name in registry.names:
        definition = registry.get(name)
        input_schema = definition.input_model.model_json_schema()
        output_schema = definition.output_model.model_json_schema()
        execution_projection = definition.execution.model_dump(mode="json")
        safety_projection = definition.safety.model_dump(mode="json")
        model_contract = definition.model_visible_contract()
        definition_projection = {
            "name": definition.name,
            "description": definition.description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "execution": execution_projection,
            "safety": safety_projection,
        }
        entries.append(
            ToolSnapshotEntry(
                name=definition.name,
                definition_hash=canonical_hash(definition_projection),
                input_schema_hash=canonical_hash(input_schema),
                output_schema_hash=canonical_hash(output_schema),
                binding_hash=canonical_hash(execution_projection),
                safety_hash=canonical_hash(safety_projection),
                execution_kind=definition.execution.kind,
                safety=definition.safety,
                model_contract=model_contract,
            )
        )

    visible_tools_hash = canonical_hash(list(visible_names))
    snapshot_hash = canonical_hash(
        {
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "visible_tool_names": list(visible_names),
            "visible_tools_hash": visible_tools_hash,
        }
    )
    return RegistrySnapshot(
        snapshot_ref=f"registry-snapshot:{snapshot_hash.removeprefix('sha256:')}",
        snapshot_hash=snapshot_hash,
        entries=tuple(entries),
        visible_tool_names=visible_names,
        visible_tools_hash=visible_tools_hash,
    )


class ToolGuardPolicy(StrictContract):
    """Runtime-authoritative policy snapshot; never sent as model arguments."""

    authorization_snapshot_ref: Reference
    user_ref: Reference
    tenant_ref: Reference
    task_ref: Reference
    runtime_enabled: bool = False
    allowed_tool_names: tuple[ToolName, ...] = Field(default_factory=tuple, max_length=32)
    allow_local: bool = False
    allow_mcp: bool = False
    allow_external_egress: bool = False
    approved_tool_names: tuple[ToolName, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "ToolGuardPolicy":
        if len(self.allowed_tool_names) != len(set(self.allowed_tool_names)):
            raise ValueError("allowed_tool_names must be unique")
        if len(self.approved_tool_names) != len(set(self.approved_tool_names)):
            raise ValueError("approved_tool_names must be unique")
        if not set(self.approved_tool_names).issubset(set(self.allowed_tool_names)):
            raise ValueError("approved tools must also be allowed")
        return self


class GuardDecision(StrictContract):
    allowed: bool
    code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)


class ToolProvenanceRecord(StrictContract):
    output_ref: Reference
    source_domain: Literal["bid_document", "enterprise_knowledge"]
    source_scope_ref: Reference
    source_version_ref: Reference
    content_hash: Sha256Digest
    locator: str = Field(min_length=1, max_length=1000)
    citable: bool

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return validate_public_locator(value)


@dataclass(frozen=True, slots=True)
class BindingExecutionResult:
    structured_content: Any
    provenance: tuple[ToolProvenanceRecord, ...]
    provider_receipt_ref: str | None = None


class ExecutionDeadline(StrictContract):
    expires_at: datetime

    @model_validator(mode="after")
    def validate_timezone(self) -> "ExecutionDeadline":
        if self.expires_at.tzinfo is None:
            raise ValueError("execution deadline must be timezone-aware")
        return self

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        return max(0.0, (self.expires_at - current).total_seconds())

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return self.remaining_seconds(now=now) <= 0


class CanonicalToolMessage(StrictContract):
    role: Literal["tool"] = "tool"
    tool_call_id: Reference
    name: ToolName
    content: str = Field(min_length=1, max_length=131072)
    content_hash: Sha256Digest


class ToolGatewayLimits(StrictContract):
    max_arguments_bytes: int = Field(default=16 * 1024, ge=1024, le=1024 * 1024)
    max_binding_result_bytes: int = Field(default=64 * 1024, ge=1024, le=4 * 1024 * 1024)
    max_tool_message_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
