"""Canonical tool contracts; no handler, network, or provider side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Annotated, Any, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import (
    Reference,
    StrictContentContract,
    StrictContract,
    TOOL_NAME_PATTERN,
    ToolName,
    validate_public_locator,
)


class ToolInputContract(StrictContract):
    """Base class for arguments visible to the model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class ToolOutputContract(StrictContentContract):
    """Base class for validated successful tool data."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ToolSafety(StrictContract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect: Literal["read_only", "mutating"]
    data_scope: Literal["context_bound", "explicit_resource"]
    external_egress: bool
    requires_approval: bool


class DisabledExecution(StrictContract):
    kind: Literal["disabled"] = "disabled"
    binding_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class LocalExecution(StrictContract):
    kind: Literal["local"] = "local"
    handler_id: str = Field(min_length=1, max_length=128)


class McpExecution(StrictContract):
    kind: Literal["mcp"] = "mcp"
    server_id: str = Field(min_length=1, max_length=128)
    remote_tool_name: str = Field(min_length=1, max_length=128)


ExecutionBinding = Annotated[
    Union[DisabledExecution, LocalExecution, McpExecution],
    Field(discriminator="kind"),
]


class ToolExecutionContext(StrictContract):
    """Runtime-injected authority context that is never model-visible."""

    user_ref: Reference
    tenant_ref: Reference
    conversation_ref: Reference
    task_ref: Reference
    state_version: int = Field(ge=1)
    context_snapshot_ref: Reference | None
    authorization_snapshot_ref: Reference
    authorized_document_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    enterprise_scope_ref: Reference | None

    @model_validator(mode="after")
    def validate_unique_documents(self) -> "ToolExecutionContext":
        if len(self.authorized_document_refs) != len(set(self.authorized_document_refs)):
            raise ValueError("authorized_document_refs must be unique")
        return self


@dataclass(frozen=True, slots=True)
class CanonicalToolDefinition:
    """The six-field canonical definition frozen by Architecture Baseline v0.1."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    execution: ExecutionBinding
    safety: ToolSafety

    def __post_init__(self) -> None:
        if re.fullmatch(TOOL_NAME_PATTERN, self.name) is None:
            raise ValueError("tool name must be provider-safe snake_case")
        if not issubclass(self.input_model, ToolInputContract):
            raise TypeError("input_model must extend ToolInputContract")
        if not issubclass(self.output_model, ToolOutputContract):
            raise TypeError("output_model must extend ToolOutputContract")
        if not isinstance(
            self.execution,
            (DisabledExecution, LocalExecution, McpExecution),
        ):
            raise TypeError("execution must be a validated execution binding")
        if not isinstance(self.safety, ToolSafety):
            raise TypeError("safety must be a validated ToolSafety")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")

    def model_visible_contract(self) -> "ModelVisibleToolContract":
        return ModelVisibleToolContract(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )


class ModelVisibleToolContract(StrictContract):
    name: ToolName
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any]


class ToolErrorCode(str, Enum):
    INVALID_ARGUMENTS = "invalid_arguments"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    CONTRACT_VIOLATION = "contract_violation"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class ToolError(ToolOutputContract):
    code: ToolErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool


DataT = TypeVar("DataT", bound=BaseModel)


class ToolExecutionResult(ToolOutputContract, Generic[DataT]):
    ok: bool
    data: DataT | None
    error: ToolError | None

    @model_validator(mode="after")
    def validate_success_or_failure(self) -> "ToolExecutionResult[DataT]":
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("success requires data and forbids error")
        if not self.ok and (self.data is not None or self.error is None):
            raise ValueError("failure requires error and forbids data")
        return self


class DocumentsOutlineInput(ToolInputContract):
    document_ref: Reference = Field(description="要读取结构导航的招标文档引用")
    navigation_goal: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "当前用户请求或已接受未决上下文中的具体导航信息需求；"
            "不得填写预防性、泛化或与当前任务无关的理由"
        ),
    )


class OutlineEntry(ToolOutputContract):
    title: str = Field(min_length=1, max_length=500)
    level: int = Field(ge=1)
    locator: str = Field(min_length=1, max_length=1000)

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return validate_public_locator(value)


class DocumentsOutlineOutput(ToolOutputContract):
    entries: tuple[OutlineEntry, ...] = Field(max_length=1000)
    citable: Literal[False] = False


class BidDocumentSearchInput(ToolInputContract):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="要在当前招标资料中查找的具体信息需求",
    )


class EnterpriseKnowledgeSearchInput(ToolInputContract):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="要在当前授权企业知识中查找的具体信息需求",
    )


class EvidenceCandidate(ToolOutputContract):
    evidence_ref: Reference
    excerpt: str = Field(min_length=1, max_length=4000)
    locator: str = Field(min_length=1, max_length=1000)
    citable: Literal[False] = False

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return validate_public_locator(value)


class EvidenceCandidatesOutput(ToolOutputContract):
    candidates: tuple[EvidenceCandidate, ...] = Field(max_length=100)


class EvidenceReadInput(ToolInputContract):
    evidence_refs: tuple[Reference, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> "EvidenceReadInput":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs must be unique")
        return self


class EvidenceAtom(ToolOutputContract):
    evidence_ref: Reference
    text: str = Field(min_length=1, max_length=20000)
    locator: str = Field(min_length=1, max_length=1000)
    citable: Literal[True] = True

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return validate_public_locator(value)


class EvidenceReadOutput(ToolOutputContract):
    evidence: tuple[EvidenceAtom, ...] = Field(min_length=1, max_length=32)
