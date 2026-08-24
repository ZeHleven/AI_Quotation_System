"""Provider-neutral model, Context, and runtime boundary contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import Field, model_validator

from .common import Reference, StrictContract, ToolName


ContextLimitationMessage = Annotated[
    str,
    Field(min_length=1, max_length=500),
]


class ContextConsumer(str, Enum):
    INTENT = "intent"
    PLANNER = "planner"
    MAIN_AGENT = "main_agent"


class ContextAssemblyStatus(str, Enum):
    READY = "ready"
    READY_WITH_LIMITS = "ready_with_limits"
    NEEDS_NARROWING = "needs_narrowing"
    BLOCKED_ON_USER = "blocked_on_user"
    FAILED = "failed"


class ContextLane(str, Enum):
    POLICY_PROTOCOL = "policy_protocol"
    ACTIVE_CONTROL = "active_control"
    TOOL_CONTRACT_ACTIVE_CALLS = "tool_contract_active_calls"
    OBSERVATION_GROUNDING = "observation_grounding"
    RELEVANT_INTERACTION = "relevant_interaction"
    HISTORICAL_MEMORY = "historical_memory"


class ContextEntryKind(str, Enum):
    POLICY = "policy"
    OUTPUT_CONTRACT = "output_contract"
    TASK_STATE = "task_state"
    CURRENT_USER_MESSAGE = "current_user_message"
    SLOT_CHECKPOINT = "slot_checkpoint"
    PLAN_CONTROL = "plan_control"
    TOOL_CONTRACT = "tool_contract"
    ACTIVE_TOOL_CALL = "active_tool_call"
    ACTIVE_TOOL_RESULT = "active_tool_result"
    OBSERVATION = "observation"
    GROUNDING = "grounding"
    EVIDENCE_ATOM = "evidence_atom"
    EVIDENCE_PARENT = "evidence_parent"
    CONVERSATION_MESSAGE = "conversation_message"
    CONVERSATION_SUMMARY = "conversation_summary"
    MEMORY = "memory"
    LIMITATION = "limitation"


class ContextRepresentation(str, Enum):
    EXACT = "exact"
    STRUCTURED_PROJECTION = "structured_projection"
    STRUCTURED_SUMMARY = "structured_summary"
    REF_ONLY = "ref_only"


class ContextProtectionClass(str, Enum):
    MANDATORY_EXACT = "mandatory_exact"
    PROTECTED = "protected"
    ELASTIC = "elastic"


class ContextTrustClass(str, Enum):
    TRUSTED_POLICY = "trusted_policy"
    TRUSTED_RUNTIME = "trusted_runtime"
    TRUSTED_TOOL_CONTRACT = "trusted_tool_contract"
    UNTRUSTED_DATA = "untrusted_data"


class ContextEntryValidity(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    DELETED = "deleted"


class ContextCompressionLevel(str, Enum):
    NONE = "none"
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    L4 = "l4"


class ContextOmissionAction(str, Enum):
    LIMIT = "limit"
    NARROW = "narrow"
    ASK_USER = "ask_user"
    FAIL = "fail"


class ContextExclusionReason(str, Enum):
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"
    STALE = "stale"
    REVOKED = "revoked"
    DELETED = "deleted"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    ALTERNATE_NOT_NEEDED = "alternate_not_needed"
    REPLACED_BY_COMPRESSION = "replaced_by_compression"
    NOT_RELEVANT = "not_relevant"
    BUDGET = "budget"
    MISSING_REQUIRED = "missing_required"


class TokenCounterMode(str, Enum):
    MATCHED_TOKENIZER = "matched_tokenizer"
    CONSERVATIVE_ESTIMATOR = "conservative_estimator"


class ContextAssemblyRequest(StrictContract):
    task_ref: Reference
    state_version: int = Field(ge=1)
    consumer: ContextConsumer
    user_message_ref: Reference
    visible_tool_names: tuple[ToolName, ...] = Field(default_factory=tuple, max_length=32)
    information_need_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )
    required_resource_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    registry_snapshot_ref: Reference | None
    model_profile_ref: Reference
    context_profile_ref: Reference
    checkpoint_snapshot_ref: Reference | None
    authorization_snapshot_ref: Reference
    snapshot_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "ContextAssemblyRequest":
        if len(self.visible_tool_names) != len(set(self.visible_tool_names)):
            raise ValueError("visible_tool_names must be unique")
        if self.visible_tool_names and self.registry_snapshot_ref is None:
            raise ValueError("visible tools require registry_snapshot_ref")
        for field_name in ("information_need_refs", "required_resource_refs"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class ModelContextProfile(StrictContract):
    profile_ref: Reference
    profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_ref: Reference
    model_ref: Reference
    context_capacity_tokens: int = Field(ge=1024, le=10_000_000)
    max_output_tokens: int = Field(ge=1, le=1_000_000)
    token_counter_ref: Reference
    token_counter_mode: TokenCounterMode
    framing_tokens: int = Field(default=0, ge=0, le=100_000)


class ContextProfile(StrictContract):
    profile_ref: Reference
    profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_max_input_tokens: int = Field(ge=256, le=10_000_000)
    reserved_output_tokens: int = Field(ge=1, le=1_000_000)
    safety_margin_tokens: int = Field(ge=0, le=1_000_000)
    soft_compression_threshold_tokens: int = Field(ge=128, le=10_000_000)
    max_entries: int = Field(default=256, ge=8, le=1000)

    def effective_input_budget(self, model: ModelContextProfile) -> int:
        provider_budget = (
            model.context_capacity_tokens
            - self.reserved_output_tokens
            - self.safety_margin_tokens
        )
        return min(self.runtime_max_input_tokens, provider_budget)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ContextProfile":
        if self.soft_compression_threshold_tokens > self.runtime_max_input_tokens:
            raise ValueError("soft compression threshold exceeds runtime input limit")
        return self


class ContextEntryCandidate(StrictContract):
    entry_ref: Reference
    stable_key: str = Field(min_length=1, max_length=200)
    source_ref: Reference
    source_version_ref: Reference
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_snapshot_ref: Reference
    lane: ContextLane
    kind: ContextEntryKind
    representation: ContextRepresentation
    authority_label: str = Field(min_length=1, max_length=80)
    protection_class: ContextProtectionClass
    trust_class: ContextTrustClass
    content: str = Field(min_length=1, max_length=131_072)
    token_count: int = Field(ge=1, le=1_000_000)
    required: bool = False
    material_if_omitted: bool = False
    priority: int = Field(default=50, ge=0, le=100)
    validity: ContextEntryValidity = ContextEntryValidity.ACTIVE
    compression_level: ContextCompressionLevel = ContextCompressionLevel.NONE
    derived_from_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=128)
    omission_action: ContextOmissionAction = ContextOmissionAction.LIMIT
    tool_name: ToolName | None = None
    protocol_pair_ref: Reference | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "ContextEntryCandidate":
        if self.protection_class is ContextProtectionClass.MANDATORY_EXACT:
            if not self.required or self.representation is not ContextRepresentation.EXACT:
                raise ValueError("mandatory_exact entries must be required and exact")
        if self.required and self.omission_action is ContextOmissionAction.LIMIT:
            raise ValueError("required entries need an explicit blocking omission action")
        tool_protocol_kinds = {
            ContextEntryKind.TOOL_CONTRACT,
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        }
        if self.kind is ContextEntryKind.TOOL_CONTRACT:
            if (
                self.tool_name is None
                or self.lane is not ContextLane.TOOL_CONTRACT_ACTIVE_CALLS
                or self.trust_class is not ContextTrustClass.TRUSTED_TOOL_CONTRACT
                or self.protection_class is not ContextProtectionClass.MANDATORY_EXACT
            ):
                raise ValueError("tool contract requires tool_name in the tool lane")
        if self.kind in {
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        } and (
            self.tool_name is None
            or self.trust_class is ContextTrustClass.TRUSTED_POLICY
            or self.protocol_pair_ref is None
            or not self.required
            or self.lane is not ContextLane.TOOL_CONTRACT_ACTIVE_CALLS
            or self.protection_class is not ContextProtectionClass.MANDATORY_EXACT
        ):
            raise ValueError("active tool protocol entries must be named required pairs")
        if (
            self.kind is ContextEntryKind.ACTIVE_TOOL_CALL
            and self.trust_class is not ContextTrustClass.TRUSTED_RUNTIME
        ):
            raise ValueError("active Tool Calls must be trusted runtime protocol")
        if (
            self.kind is ContextEntryKind.ACTIVE_TOOL_RESULT
            and self.trust_class is not ContextTrustClass.UNTRUSTED_DATA
        ):
            raise ValueError("active Tool results must remain untrusted data")
        if self.kind not in tool_protocol_kinds and self.tool_name is not None:
            raise ValueError("tool_name is only valid for tool protocol entries")
        if self.kind not in {
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        } and self.protocol_pair_ref is not None:
            raise ValueError("protocol_pair_ref is only valid for active Tool pairs")
        if self.kind in {
            ContextEntryKind.POLICY,
            ContextEntryKind.OUTPUT_CONTRACT,
        } and (
            self.lane is not ContextLane.POLICY_PROTOCOL
            or self.trust_class is not ContextTrustClass.TRUSTED_POLICY
            or self.protection_class is not ContextProtectionClass.MANDATORY_EXACT
        ):
            raise ValueError("policy entries must be exact trusted policy")
        if self.kind is ContextEntryKind.CURRENT_USER_MESSAGE and (
            self.lane is not ContextLane.ACTIVE_CONTROL
            or self.trust_class is not ContextTrustClass.UNTRUSTED_DATA
            or self.protection_class is not ContextProtectionClass.MANDATORY_EXACT
        ):
            raise ValueError("current user message must remain exact untrusted data")
        if self.kind is ContextEntryKind.TASK_STATE and (
            self.lane is not ContextLane.ACTIVE_CONTROL
            or self.trust_class is not ContextTrustClass.TRUSTED_RUNTIME
            or not self.required
        ):
            raise ValueError("task state must be a required trusted runtime projection")
        if self.lane in {
            ContextLane.OBSERVATION_GROUNDING,
            ContextLane.RELEVANT_INTERACTION,
            ContextLane.HISTORICAL_MEMORY,
        } and self.trust_class is not ContextTrustClass.UNTRUSTED_DATA:
            raise ValueError("data lanes must remain untrusted data")
        if self.kind is ContextEntryKind.MEMORY and (
            self.lane is not ContextLane.HISTORICAL_MEMORY
            or self.trust_class is not ContextTrustClass.UNTRUSTED_DATA
        ):
            raise ValueError("memory must use the untrusted historical-memory lane")
        if self.representation is ContextRepresentation.EXACT:
            if self.compression_level is not ContextCompressionLevel.NONE:
                raise ValueError("exact entries cannot claim a compression level")
        elif self.compression_level is ContextCompressionLevel.NONE:
            raise ValueError("non-exact representations require a compression level")
        if len(self.derived_from_refs) != len(set(self.derived_from_refs)):
            raise ValueError("derived_from_refs must be unique")
        return self


class ContextIncludedEntry(StrictContract):
    entry_ref: Reference
    stable_key: str = Field(min_length=1, max_length=200)
    source_ref: Reference
    source_version_ref: Reference
    lane: ContextLane
    kind: ContextEntryKind
    representation: ContextRepresentation
    authority_label: str = Field(min_length=1, max_length=80)
    protection_class: ContextProtectionClass
    trust_class: ContextTrustClass
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    projection_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    token_count: int = Field(ge=1)
    tool_name: ToolName | None = None
    protocol_pair_ref: Reference | None = None

    @model_validator(mode="after")
    def validate_protocol_metadata(self) -> "ContextIncludedEntry":
        if self.kind in {
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        }:
            if self.tool_name is None or self.protocol_pair_ref is None:
                raise ValueError("active Tool pair receipts require name and pair ref")
        elif self.kind is ContextEntryKind.TOOL_CONTRACT:
            if self.tool_name is None or self.protocol_pair_ref is not None:
                raise ValueError("Tool contract receipts require only tool_name")
        elif self.tool_name is not None or self.protocol_pair_ref is not None:
            raise ValueError("non-Tool receipts cannot carry Tool protocol metadata")
        return self


class ContextProjectionEntry(ContextIncludedEntry):
    content: str = Field(min_length=1, max_length=131_072)
    untrusted_data: bool

    @model_validator(mode="after")
    def validate_trust_projection(self) -> "ContextProjectionEntry":
        expected = self.trust_class is ContextTrustClass.UNTRUSTED_DATA
        if self.untrusted_data != expected:
            raise ValueError("untrusted_data must match trust_class")
        return self


class ContextExcludedEntry(StrictContract):
    entry_ref: Reference
    source_ref: Reference
    lane: ContextLane
    reason: ContextExclusionReason
    protection_class: ContextProtectionClass
    material_limitation: bool = False


class ContextCompressionReceipt(StrictContract):
    level: ContextCompressionLevel
    input_entry_refs: tuple[Reference, ...] = Field(min_length=1, max_length=128)
    output_entry_ref: Reference | None
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    lossless: bool

    @model_validator(mode="after")
    def validate_token_reduction(self) -> "ContextCompressionReceipt":
        if self.level is ContextCompressionLevel.NONE:
            raise ValueError("compression receipt cannot use level none")
        if self.after_tokens > self.before_tokens:
            raise ValueError("compression cannot increase token count")
        if len(self.input_entry_refs) != len(set(self.input_entry_refs)):
            raise ValueError("compression input refs must be unique")
        return self


class ContextSnapshot(StrictContract):
    snapshot_ref: Reference
    snapshot_sequence: int = Field(ge=1)
    task_ref: Reference
    state_version: int = Field(ge=1)
    consumer: ContextConsumer
    status: ContextAssemblyStatus
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    model_profile_ref: Reference
    model_profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_profile_ref: Reference
    context_profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_snapshot_ref: Reference | None
    registry_snapshot_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    authorization_snapshot_ref: Reference
    dependency_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1000)
    included_entries: tuple[ContextIncludedEntry, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    excluded_entries: tuple[ContextExcludedEntry, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )
    compression_receipts: tuple[ContextCompressionReceipt, ...] = Field(
        default_factory=tuple,
        max_length=256,
    )
    included_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1000)
    excluded_refs: tuple[Reference, ...] = Field(default_factory=tuple, max_length=1000)
    limitation_messages: tuple[ContextLimitationMessage, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    estimated_input_tokens: int | None = Field(default=None, ge=0)
    effective_input_budget: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    safety_margin_tokens: int = Field(ge=0)
    projection_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_receipts(self) -> "ContextSnapshot":
        included = tuple(entry.entry_ref for entry in self.included_entries)
        excluded = tuple(entry.entry_ref for entry in self.excluded_entries)
        if self.included_refs != included or self.excluded_refs != excluded:
            raise ValueError("entry refs must match included/excluded receipts")
        if set(included) & set(excluded):
            raise ValueError("an entry cannot be both included and excluded")
        if len(self.dependency_refs) != len(set(self.dependency_refs)):
            raise ValueError("dependency_refs must be unique")
        if (self.registry_snapshot_ref is None) != (
            self.registry_snapshot_hash is None
        ):
            raise ValueError("registry snapshot ref/hash must appear together")
        if self.status is ContextAssemblyStatus.READY and self.limitation_messages:
            raise ValueError("ready snapshot cannot carry limitations")
        if self.status is not ContextAssemblyStatus.READY and not self.limitation_messages:
            raise ValueError("non-ready snapshot requires an explicit limitation")
        if self.status in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            if self.estimated_input_tokens is None:
                raise ValueError("model-ready snapshot requires a token count")
            if self.estimated_input_tokens > self.effective_input_budget:
                raise ValueError("model-ready snapshot exceeds effective input budget")
        return self


class ContextAssemblyResult(StrictContract):
    snapshot: ContextSnapshot
    projection_entries: tuple[ContextProjectionEntry, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_projection(self) -> "ContextAssemblyResult":
        if self.snapshot.status in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }:
            projected = tuple(entry.entry_ref for entry in self.projection_entries)
            if projected != self.snapshot.included_refs:
                raise ValueError("projection order must match included snapshot entries")
        elif self.projection_entries:
            raise ValueError("non-model-ready result cannot expose projection content")
        return self


class MemoryScope(str, Enum):
    WORKING = "working"
    CONVERSATION = "conversation"
    PROJECT_ASSESSMENT = "project_assessment"
    USER = "user"


class ToolCallRequest(StrictContract):
    call_ref: Reference
    provider_tool_call_id: Reference
    model_turn_ref: Reference
    sequence: int = Field(ge=1, le=64)
    task_ref: Reference
    action_ref: Reference
    context_snapshot_ref: Reference | None
    state_version: int = Field(ge=1)
    tool_name: ToolName
    arguments: dict[str, Any]
    registry_snapshot_ref: Reference
    registry_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    visible_tools_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_snapshot_ref: Reference

