"""Strict external request models for the bid-assessment v1 facade."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BidAssessmentCreateIn(BaseModel):
    """Frozen API-01 request body.

    Keep this model aligned with ``CreateAssessmentRequest`` in the Phase 0
    machine contract. Unknown fields are deliberately rejected.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    client_name: str = Field(min_length=1, max_length=300)
    internal_note: str | None = Field(default=None, max_length=2000)
    external_ref: str | None = Field(default=None, max_length=100)

    @field_validator("title", "client_name")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("internal_note", "external_ref")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidUploadBatchCreateIn(BaseModel):
    """Frozen API-10 request body."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["initial", "change"]
    base_manifest_id: str | None = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @model_validator(mode="after")
    def _validate_manifest_pair(self):
        if self.purpose == "initial" and self.base_manifest_id is not None:
            raise ValueError("base_manifest_id must be null for initial batches")
        if self.purpose == "change" and self.base_manifest_id is None:
            raise ValueError("base_manifest_id is required for change batches")
        return self


class BidUploadFileCreateIn(BaseModel):
    """Frozen non-binary API-12 multipart fields."""

    model_config = ConfigDict(extra="forbid")

    client_file_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    operation: Literal["add", "replace"]
    replace_document_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    relative_path: str | None = Field(default=None, max_length=1000)

    @field_validator("relative_path")
    @classmethod
    def _normalize_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            return None
        if normalized.startswith("/") or any(
            segment in {"", ".", ".."} for segment in normalized.split("/")
        ):
            raise ValueError("relative_path must be a safe relative display path")
        if any(ord(character) < 0x20 for character in normalized):
            raise ValueError("relative_path contains control characters")
        return normalized

    @model_validator(mode="after")
    def _validate_replace_target(self):
        if self.operation == "add" and self.replace_document_id is not None:
            raise ValueError("replace_document_id must be null for add")
        if self.operation == "replace" and self.replace_document_id is None:
            raise ValueError("replace_document_id is required for replace")
        return self


_BidResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]


class BidUploadBatchDeactivationCreateIn(BaseModel):
    """Frozen API-14 request body with order-insensitive document IDs."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[_BidResourceId] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("document_ids")
    @classmethod
    def _normalize_document_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("document_ids must be unique")
        return sorted(value)

    @field_validator("reason")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class BidUploadBatchCommitIn(BaseModel):
    """Frozen API-15 commit confirmation body."""

    model_config = ConfigDict(extra="forbid")

    expected_file_count: int = Field(ge=0)
    expected_deactivation_count: int = Field(ge=0)
    change_note: str | None = Field(max_length=1000)
    confirm_start_analysis: Literal[True]

    @field_validator("change_note")
    @classmethod
    def _normalize_change_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BidUploadBatchAbandonIn(BaseModel):
    """Frozen API-16 explicit abandonment reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized
