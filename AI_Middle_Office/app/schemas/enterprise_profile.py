from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class EnterpriseProfileItemCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, max_length=64)
    subcategory: Optional[str] = Field(None, max_length=128)
    profile_key: Optional[str] = Field(None, max_length=128)
    title: str = Field(..., min_length=1, max_length=255)
    summary: Optional[str] = None
    content_text: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    applicable_scope: Optional[str] = None
    source: Optional[str] = Field("manual", max_length=64)
    confidentiality: Optional[str] = Field("internal", max_length=32)
    valid_from: date | datetime | str | None = None
    valid_until: date | datetime | str | None = None


class EnterpriseProfileItemUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Optional[str] = Field(None, min_length=1, max_length=64)
    subcategory: Optional[str] = Field(None, max_length=128)
    profile_key: Optional[str] = Field(None, max_length=128)
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    summary: Optional[str] = None
    content_text: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    applicable_scope: Optional[str] = None
    source: Optional[str] = Field(None, max_length=64)
    confidentiality: Optional[str] = Field(None, max_length=32)
    valid_from: date | datetime | str | None = None
    valid_until: date | datetime | str | None = None
    change_reason: Optional[str] = Field(None, max_length=2000)


class EnterpriseProfileAttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(..., min_length=1, max_length=36)
    attachment_type: str = Field("source", min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=2000)
    is_primary: bool = False


class EnterpriseProfileStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


class EnterpriseProfileCandidateQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Optional[str] = Field(None, max_length=64)
    keyword: Optional[str] = Field(None, max_length=255)
    limit: int = Field(20, ge=1, le=100)


EnterpriseProfileStatusLiteral = Literal["draft", "active", "archived"]
