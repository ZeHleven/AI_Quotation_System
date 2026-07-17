from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BudgetProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    client_name: str | None = Field(default=None, max_length=128)
    address: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    owner_department: str | None = Field(default=None, max_length=128)


class BudgetProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    client_name: str | None = Field(default=None, max_length=128)
    address: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    owner_department: str | None = Field(default=None, max_length=128)


class BudgetProjectArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class BudgetSheetMappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str = Field(min_length=1, max_length=255)
    field_mapping: dict[str, str] = Field(default_factory=dict)


class BudgetImportRemap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_remap_revision: int | None = Field(default=None, ge=0)
    sheet_mappings: list[BudgetSheetMappingInput] = Field(min_length=1)


def model_payload(value: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_unset=exclude_unset)
    return value.dict(exclude_unset=exclude_unset)
