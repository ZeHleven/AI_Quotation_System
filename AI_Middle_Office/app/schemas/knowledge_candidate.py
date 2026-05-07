from typing import Optional

from pydantic import BaseModel, Field


class KnowledgeCandidateBuildRequest(BaseModel):
    days: Optional[int] = Field(default=None, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)
    overwrite: bool = False
    include_new_materials: bool = True
    include_price_updates: bool = True
    include_rejected: bool = True
    min_abs_delta: float = Field(default=1.0, ge=0)
    min_delta_ratio: float = Field(default=0.05, ge=0)


class KnowledgeCandidateApproveRequest(BaseModel):
    item_name: Optional[str] = None
    unit_price: Optional[float] = None
    unit: Optional[str] = None
    notes: Optional[str] = None
    material_id: Optional[str] = None
    update_existing: bool = True
    as_draft: bool = True
    review_note: Optional[str] = None


class KnowledgeCandidateRejectRequest(BaseModel):
    review_note: Optional[str] = None
