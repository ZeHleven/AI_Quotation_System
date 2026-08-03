from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class EnterpriseQuotaResourceUpdateIn(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = Field(default=None, max_length=128)
    resource_code: Optional[str] = Field(default=None, max_length=64)
    resource_type: Optional[str] = Field(default=None, max_length=32)
    resource_name: Optional[str] = Field(default=None, max_length=255)
    work_content: Optional[str] = None
    calculation_rule: Optional[str] = None
    specification: Optional[str] = Field(default=None, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=255)
    unit: Optional[str] = Field(default=None, max_length=64)
    default_quantity: Optional[float] = Field(default=None, ge=0)
    price: Optional[float] = Field(default=None, ge=0)


class EnterpriseQuotaResourceCreateIn(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=500)
    library_kind: Literal["labor", "material"]
    category: Optional[str] = Field(default=None, max_length=128)
    resource_code: Optional[str] = Field(default=None, max_length=64)
    resource_type: Optional[str] = Field(default=None, max_length=32)
    resource_name: str = Field(min_length=1, max_length=255)
    work_content: Optional[str] = None
    calculation_rule: Optional[str] = None
    specification: Optional[str] = Field(default=None, max_length=255)
    brand: Optional[str] = Field(default=None, max_length=255)
    unit: str = Field(min_length=1, max_length=64)
    default_quantity: Optional[float] = Field(default=None, ge=0)
    price: float = Field(ge=0)


class EnterpriseQuotaVersionCloneIn(BaseModel):
    version_code: Optional[str] = Field(default=None, max_length=64)
    version_name: Optional[str] = Field(default=None, max_length=255)
    reason: Optional[str] = Field(default=None, max_length=500)


class EnterpriseQuotaRecalculateIn(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=500)


class EnterpriseQuotaActivateIn(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)
    reason: str = Field(min_length=4, max_length=500)
    acknowledge_warnings: bool = False
