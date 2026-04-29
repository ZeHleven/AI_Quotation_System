from app.services.quote_job_runner import run_quote_job
from app.tasks.celery_app import celery_app


if celery_app is None:
    run_quote_job_task = None
else:

    @celery_app.task(name="quote.run_quote_job")
    def run_quote_job_task(job_id: str) -> str:
        run_quote_job(job_id)
        return job_id
