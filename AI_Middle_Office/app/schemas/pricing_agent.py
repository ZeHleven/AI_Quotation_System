from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PricingSource = Literal["archive", "enterprise", "industry"]
PricingMatchMode = Literal["exact", "expanded"]


class PricingAgentContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(min_length=1, max_length=64)
    project_type: str = Field(min_length=1, max_length=64)
    decoration_level: str = Field(min_length=1, max_length=64)

    @field_validator("city", "project_type", "decoration_level")
    @classmethod
    def clean_context(cls, value: str) -> str:
        return " ".join(value.split())


class PricingAgentDemandLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str = Field(min_length=1, max_length=128)
    item_code: str | None = Field(default=None, max_length=128)
    item_name: str = Field(min_length=1, max_length=500)
    specification: str | None = Field(default=None, max_length=1000)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=64)

    @field_validator("row_key", "item_name")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return " ".join(value.split())


class PricingAgentRunCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PricingMatchMode
    sources: list[PricingSource] = Field(min_length=1, max_length=3)
    context: PricingAgentContextIn
    lines: list[PricingAgentDemandLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_contract(self):
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("组价依据不能重复")
        if len({line.row_key for line in self.lines}) != len(self.lines):
            raise ValueError("row_key 不能重复")
        if self.mode == "exact" and "industry" in self.sources:
            raise ValueError("准确模式不能使用行业数据；请选择“准确+近似”")
        return self


class PricingAgentCandidateSelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PricingSource
    source_record_id: str = Field(min_length=1, max_length=128)

    @field_validator("source_record_id")
    @classmethod
    def clean_source_record_id(cls, value: str) -> str:
        return value.strip()


class PricingAgentManualPriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None
