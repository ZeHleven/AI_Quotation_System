from typing import Optional

from pydantic import BaseModel, Field


class PromptRegressionBuildRequest(BaseModel):
    days: Optional[int] = Field(default=None, ge=1, le=365)
    prompt_version: Optional[str] = None
    include_rejected: bool = True
    overwrite: bool = False
    active: bool = True
    limit: int = Field(default=100, ge=1, le=1000)


class PromptRegressionRunRequest(BaseModel):
    name: Optional[str] = None
    prompt_version: Optional[str] = None
    baseline_prompt_version: Optional[str] = None
    active_only: bool = True
    case_ids: Optional[list[int]] = None
    amount_tolerance: float = Field(default=1.0, ge=0)
    notes: Optional[str] = None
