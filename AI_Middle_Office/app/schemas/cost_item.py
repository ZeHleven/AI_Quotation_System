from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CostItemCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, max_length=128)
    subcategory: Optional[str] = Field(None, max_length=128)
    item_name: str = Field(..., min_length=1, max_length=255)
    spec: Optional[str] = None
    unit: str = Field(..., min_length=1, max_length=64)
    price: Optional[float] = None
    client_tax_excluded_price: Optional[float] = None
    client_labor_price: Optional[float] = None
    client_main_material_price: Optional[float] = None
    client_auxiliary_material_price: Optional[float] = None
    client_direct_fee: Optional[float] = None
    client_management_profit: Optional[float] = None
    subcontract_composite_price: Optional[float] = None
    subcontract_labor_price: Optional[float] = None
    subcontract_main_material_price: Optional[float] = None
    subcontract_auxiliary_material_price: Optional[float] = None
    crew_benchmark_price: Optional[float] = None
    price_type: str = Field(..., min_length=1, max_length=24)
    source: Optional[str] = Field("manual", max_length=32)
    effective_date: date | datetime | str | None = None
    notes: Optional[str] = None


class CostItemUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Optional[str] = Field(None, min_length=1, max_length=128)
    subcategory: Optional[str] = Field(None, max_length=128)
    item_name: Optional[str] = Field(None, min_length=1, max_length=255)
    spec: Optional[str] = None
    unit: Optional[str] = Field(None, min_length=1, max_length=64)
    price: Optional[float] = None
    client_tax_excluded_price: Optional[float] = None
    client_labor_price: Optional[float] = None
    client_main_material_price: Optional[float] = None
    client_auxiliary_material_price: Optional[float] = None
    client_direct_fee: Optional[float] = None
    client_management_profit: Optional[float] = None
    subcontract_composite_price: Optional[float] = None
    subcontract_labor_price: Optional[float] = None
    subcontract_main_material_price: Optional[float] = None
    subcontract_auxiliary_material_price: Optional[float] = None
    crew_benchmark_price: Optional[float] = None
    price_type: Optional[str] = Field(None, max_length=24)
    source: Optional[str] = Field(None, max_length=32)
    effective_date: date | datetime | str | None = None
    notes: Optional[str] = None
    change_reason: Optional[str] = Field(None, max_length=2000)


class CostItemArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, max_length=2000)


class CostItemActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(None, max_length=2000)


class CostItemWithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


class CostItemBulkStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[int] = Field(..., min_length=1, max_length=5000)
    target_status: Literal["active", "draft", "archived"]
    reason: Optional[str] = Field(None, max_length=2000)


class CostItemImportConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(..., min_length=1, max_length=64)
