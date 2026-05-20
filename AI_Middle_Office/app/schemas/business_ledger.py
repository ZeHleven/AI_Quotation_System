from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BusinessLedgerCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    stage: Optional[str] = None
    next_followup_at: datetime | str | None = None
    responder_id: Optional[int] = None
    notes: Optional[str] = None


class BusinessLedgerUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    stage: Optional[str] = None
    next_followup_at: datetime | str | None = None
    responder_id: Optional[int] = None
    notes: Optional[str] = None


class BusinessLedgerCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


class BusinessLedgerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    inquiry_id: str
    direction: str
    source: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    stage: Optional[str] = None
    next_followup_at: Optional[str] = None
    responder_id: Optional[int] = None
    responder_username: Optional[str] = None
    notes: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancelled_by_id: Optional[int] = None
    cancelled_by_username: Optional[str] = None
    cancel_reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BusinessLedgerListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BusinessLedgerOut]
    total: int
    page: int
    page_size: int
