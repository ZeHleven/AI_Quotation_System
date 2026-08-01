from app.services.tender_evidence_indexing import (
    run_tender_evidence_index_job,
)
from app.services.tender_parse_pipeline import run_tender_parse_job
from app.tasks.celery_app import celery_app


if celery_app is None:
    run_tender_parse_job_task = None
    run_tender_evidence_index_job_task = None
else:

    @celery_app.task(
        bind=True,
        name="tender_evidence.run_parse_job",
        max_retries=2,
    )
    def run_tender_parse_job_task(self, job_uuid: str) -> str:
        result = run_tender_parse_job(job_uuid)
        if result.status == "retryable":
            raise self.retry(countdown=min(30, 2 ** result.attempt_count))
        return result.status

    @celery_app.task(
        bind=True,
        name="tender_evidence.run_index_job",
        max_retries=2,
    )
    def run_tender_evidence_index_job_task(self, job_uuid: str) -> str:
        result = run_tender_evidence_index_job(job_uuid)
        if result.status == "retryable":
            raise self.retry(countdown=min(30, 2 ** result.attempt_count))
        return result.status
