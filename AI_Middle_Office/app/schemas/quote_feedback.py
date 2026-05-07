from typing import Optional

from pydantic import BaseModel


class QuoteFeedbackRejectRequest(BaseModel):
    quote_job_id: Optional[str] = None
    trace_id: Optional[str] = None
    reason: Optional[str] = None
