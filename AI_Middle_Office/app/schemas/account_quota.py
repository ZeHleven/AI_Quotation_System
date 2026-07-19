from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AccountQuotaSource = Literal["manual", "imported", "pricing_draft_sync", "ai_estimate"]
AccountQuotaStatus = Literal["draft", "active", "archived"]


class AccountQuotaCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota_code: str | None = Field(default=None, max_length=64)
    item_name: str = Field(min_length=1, max_length=255)
    item_features: str | None = Field(default=None, max_length=10000)
    spec: str | None = Field(default=None, max_length=10000)
    unit: str = Field(min_length=1, max_length=64)
    unit_price: Decimal = Field(gt=Decimal("0"), max_digits=18, decimal_places=6)
    source: Literal["manual"] = "manual"
    notes: str | None = Field(default=None, max_length=10000)
    reason: str | None = Field(default=None, max_length=2000)


class AccountQuotaUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    quota_code: str | None = Field(default=None, max_length=64)
    item_name: str | None = Field(default=None, min_length=1, max_length=255)
    item_features: str | None = Field(default=None, max_length=10000)
    spec: str | None = Field(default=None, max_length=10000)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    unit_price: Decimal | None = Field(default=None, gt=Decimal("0"), max_digits=18, decimal_places=6)
    notes: str | None = Field(default=None, max_length=10000)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_patch(self):
        mutable_fields = {
            "quota_code",
            "item_name",
            "item_features",
            "spec",
            "unit",
            "unit_price",
            "notes",
        }
        supplied = mutable_fields.intersection(self.model_fields_set)
        if not supplied:
            raise ValueError("至少提供一个要修改的字段")
        for field_name in ("item_name", "unit", "unit_price"):
            if field_name in supplied and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        return self


class AccountQuotaStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: AccountQuotaStatus
    expected_revision: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=2000)


class AccountQuotaBatchStatusItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_identifier: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(gt=0)


class AccountQuotaBatchStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_status: Literal["active", "archived"]
    reason: str = Field(min_length=2, max_length=2000)
    items: list[AccountQuotaBatchStatusItemIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_items(self):
        identifiers = [item.item_identifier.strip() for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("批量状态流转条目不能重复")
        return self
