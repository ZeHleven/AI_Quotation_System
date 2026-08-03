from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BudgetPricingRunCreate(BaseModel):
    """Explicit immutable inputs for one Phase 2 pricing run."""

    model_config = ConfigDict(extra="forbid")

    source_import_batch_id: int = Field(gt=0)
    source_import_revision_id: int = Field(gt=0)
    expected_active_quota_version_id: int | None = Field(default=None, gt=0)
    # The first frontend draft used this equivalent name. Keep both explicit
    # during P2-1 and reject a conflicting pair at the API boundary.
    expected_enterprise_quota_version_id: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=2000)

    def expected_quota_version_id(self) -> int | None:
        return self.expected_active_quota_version_id or self.expected_enterprise_quota_version_id

    def has_conflicting_version_ids(self) -> bool:
        return bool(
            self.expected_active_quota_version_id
            and self.expected_enterprise_quota_version_id
            and self.expected_active_quota_version_id != self.expected_enterprise_quota_version_id
        )


class BudgetPricingDraftCreate(BaseModel):
    """Create or rebuild the one mutable account/project pricing draft."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"]
    source_import_batch_id: int = Field(gt=0)
    source_import_revision_id: int = Field(gt=0)
    expected_active_quota_version_id: int | None = Field(default=None, gt=0)
    expected_revision: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetPricingDraftLinePatch(BaseModel):
    """Optimistic-lock manual draft edit; explicit null clears the override."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    expected_line_revision: int = Field(gt=0)
    manual_unit_price: Decimal | None = Field(gt=Decimal("0"), max_digits=20, decimal_places=6)
    pricing_breakdown: dict[str, Any] | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetPricingDraftLineConstructionNotePatch(BaseModel):
    """Optimistic-lock construction note edit that must not change pricing."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    expected_line_revision: int = Field(gt=0)
    remark: str = Field(min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetPricingDraftTotalsConfigPatch(BaseModel):
    """Draft-level editable totals/rate settings for the quote summary panel."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    measures_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1000"), max_digits=12, decimal_places=6)
    management_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1000"), max_digits=12, decimal_places=6)
    other_fee: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    suspended_amount: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    area: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    quote_adjustment_percent: Decimal | None = Field(default=None, ge=Decimal("-100"), le=Decimal("1000"), max_digits=12, decimal_places=6)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetPricingDraftLineAiEstimateIn(BaseModel):
    """Manual AI estimate trigger for one unpriced mutable draft line."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    expected_line_revision: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetProjectQuotaMaterializeIn(BaseModel):
    """Idempotently create the project-local quota snapshot for one draft line."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"


class BudgetProjectQuotaResourceCreateIn(BaseModel):
    """Create one project-local labor/material/machinery composition row."""

    model_config = ConfigDict(extra="forbid")

    expected_snapshot_revision: int = Field(gt=0)
    component_type: str | None = Field(default=None, max_length=64)
    resource_code: str | None = Field(default=None, max_length=64)
    resource_name: str = Field(min_length=1, max_length=255)
    worker_or_subtype: str | None = Field(default=None, max_length=128)
    work_content: str | None = Field(default=None, max_length=4000)
    specification: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=64)
    quantity: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), max_digits=20, decimal_places=6)
    unit_price: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), max_digits=20, decimal_places=6)
    amount: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    fee_bucket: Literal["labor", "main_material", "auxiliary_material", "machinery"]
    library_kind: Literal["labor", "material"] | None = None
    category: str | None = Field(default=None, max_length=128)
    calculation_rule: str | None = Field(default=None, max_length=4000)
    tax_rate: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=12, decimal_places=6)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetProjectQuotaResourceUpdateIn(BaseModel):
    """Optimistic-lock update for every editable project resource field."""

    model_config = ConfigDict(extra="forbid")

    expected_snapshot_revision: int = Field(gt=0)
    expected_resource_revision: int = Field(gt=0)
    component_type: str | None = Field(default=None, max_length=64)
    resource_code: str | None = Field(default=None, max_length=64)
    resource_name: str | None = Field(default=None, min_length=1, max_length=255)
    worker_or_subtype: str | None = Field(default=None, max_length=128)
    work_content: str | None = Field(default=None, max_length=4000)
    specification: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    unit_price: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    amount: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=20, decimal_places=6)
    fee_bucket: Literal["labor", "main_material", "auxiliary_material", "machinery"] | None = None
    library_kind: Literal["labor", "material"] | None = None
    category: str | None = Field(default=None, max_length=128)
    calculation_rule: str | None = Field(default=None, max_length=4000)
    tax_rate: Decimal | None = Field(default=None, ge=Decimal("0"), max_digits=12, decimal_places=6)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetProjectQuotaResourceDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_revision: int = Field(gt=0)
    expected_resource_revision: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetProjectQuotaEnterpriseSyncIn(BaseModel):
    """Explicit opt-in for writing the project snapshot to an enterprise draft."""

    model_config = ConfigDict(extra="forbid")

    expected_snapshot_revision: int = Field(gt=0)
    sync_to_enterprise: Literal[True]
    reason: str = Field(min_length=4, max_length=500)


class BudgetPricingDraftQuoteJobCreate(BaseModel):
    """One-click enterprise-quota plus AI fallback quote draft generation."""

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai"] = "enterprise_ai"
    source_import_batch_id: int = Field(gt=0)
    source_import_revision_id: int = Field(gt=0)
    expected_active_quota_version_id: int | None = Field(default=None, gt=0)
    expected_revision: int | None = Field(default=None, gt=0)
    ai_concurrency: int = Field(default=1, ge=1, le=3)
    ai_batch_size: int = Field(default=3, ge=1, le=20)
    reason: str | None = Field(default=None, max_length=2000)


class BudgetPricingDraftAccountQuotaSyncPreviewIn(BaseModel):
    """Read-only preview for priced draft lines.

    Omitting ``line_identifiers`` previews every draft line with a valid
    effective price. Supplying it is useful for a deliberate subset from the UI.
    """

    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    line_identifiers: list[str] | None = Field(default=None, max_length=500)


class BudgetPricingDraftAccountQuotaSyncLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_identifier: str = Field(min_length=1, max_length=64)
    expected_line_revision: int = Field(gt=0)
    expected_target_revision: int | None = Field(default=None, gt=0)
    action: Literal["create", "update_existing", "skip"]


class BudgetPricingDraftAccountQuotaSyncConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pricing_mode: Literal["enterprise_ai", "account_strict"] = "enterprise_ai"
    expected_revision: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=2000)
    items: list[BudgetPricingDraftAccountQuotaSyncLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_lines(self):
        identifiers = [item.line_identifier.strip() for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("同步行不能重复")
        return self
