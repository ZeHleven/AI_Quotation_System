from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field


PriceValue = Union[float, int, str, None]


class QuoteProjectDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_name: Optional[str] = None
    unit_price: PriceValue = None
    total_price: PriceValue = None
    notes: Optional[str] = None


class ConfirmPushRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_details: list[QuoteProjectDetail] = Field(default_factory=list)
    quote_id: Optional[str] = None
    quote_job_id: Optional[str] = None
    trace_id: Optional[str] = None
    feedback_reason_category: Optional[str] = None
    feedback_reason: Optional[str] = None
    excel_filename: Optional[str] = None
    filename: Optional[str] = None
    display_title: Optional[str] = None
