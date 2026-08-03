from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.quote_job import QuoteJob


QUOTE_JOB_NUMBER_PREFIX = "BJ"
QUOTE_JOB_NUMBER_RE = re.compile(r"^BJ-(\d{8})-(\d{1,10})$", re.IGNORECASE)


def quote_job_number(job: Optional[QuoteJob]) -> Optional[str]:
    if not job:
        return None
    if not job.id or not job.created_at:
        return job.job_id
    return f"{QUOTE_JOB_NUMBER_PREFIX}-{_date_part(job.created_at)}-{int(job.id):06d}"


def quote_job_number_database_id(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = QUOTE_JOB_NUMBER_RE.match(value.strip())
    if not match:
        return None
    try:
        return int(match.group(2))
    except ValueError:
        return None


def find_quote_job_by_identifier(db: Session, identifier: Optional[str]) -> Optional[QuoteJob]:
    value = (identifier or "").strip()
    if not value:
        return None

    job = db.query(QuoteJob).filter(QuoteJob.job_id == value).first()
    if job:
        return job

    database_id = quote_job_number_database_id(value)
    if database_id is None:
        return None
    job = db.query(QuoteJob).filter(QuoteJob.id == database_id).first()
    if job and (quote_job_number(job) or "").upper() == value.upper():
        return job
    return None


def _date_part(value: datetime) -> str:
    return value.strftime("%Y%m%d")
