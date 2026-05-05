# Ops Alert Incidents

## 2026-05-05 Celery Worker Probe Alert

- Current `/health/ready` result: Redis broker and Celery worker are both ready, with `worker_count=1`.
- Root cause: the ops dashboard counted old Redis/Celery startup reconnect errors from recovered logs, so historical failures stayed visible as current abnormal clues.
- Fix: added `OPS_LOG_LOOKBACK_MINUTES` and timestamp parsing for FastAPI JSON logs and Celery worker logs. Stale timestamped error lines outside the lookback window are ignored.
- Fix: Celery worker ping now retries once after an empty or failed first probe before reporting worker failure.
- Verification: current log scan with the new logic reports `total_matches=0`; `python -m pytest` reports `57 passed`.
