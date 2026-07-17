"""Background one-click quote generation for enterprise-ai pricing drafts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.budget_project import BudgetProjectProfile
from app.models.budget_pricing_draft import (
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_RUNNING,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_ENTERPRISE_MATCHED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_FAILED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_PARTIAL_FAILED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_RUNNING,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_SUCCEEDED,
    PRICING_MODE_ENTERPRISE_AI,
    BudgetProjectPricingDraft,
    BudgetProjectPricingDraftLine,
    BudgetProjectPricingDraftQuoteJob,
    BudgetProjectPricingDraftQuoteJobLine,
)
from app.models.user import User
from app.schemas.budget_pricing import BudgetPricingDraftQuoteJobCreate
from app.services.budget_pricing import (
    BudgetPricingError,
    _decimal,
    _decimal_text,
    _json_dump,
    _json_load,
)
from app.services.budget_pricing_ai_estimates import (
    apply_budget_pricing_ai_estimate_to_line,
    build_budget_pricing_ai_estimate_input,
    generate_budget_pricing_ai_estimate_batch,
)
from app.services.budget_pricing_drafts import (
    _append_event,
    create_or_rebuild_budget_pricing_draft,
)
from app.services.model_gateway import CircuitOpenError


TERMINAL_JOB_STATUSES = {
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_SUCCEEDED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_PARTIAL_FAILED,
    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_FAILED,
}

BATCH_CIRCUIT_RETRY_LIMIT = 3
BATCH_FAILURE_RETRY_LIMIT = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, BudgetPricingError):
        return {"code": exc.code, "detail": exc.detail, "status_code": exc.status_code}
    return {"code": exc.__class__.__name__, "message": str(exc)[:1000]}


def _job_progress_percent(job: BudgetProjectPricingDraftQuoteJob) -> int:
    total = int(job.total_line_count or 0)
    if total <= 0:
        return 100
    done = (
        int(job.enterprise_priced_count or 0)
        + int(job.ai_completed_count or 0)
        + int(job.ai_failed_count or 0)
        + int(job.skipped_count or 0)
    )
    percent = int(round((done / total) * 100))
    if job.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_RUNNING:
        return max(1, min(99, percent))
    return max(0, min(100, percent))


def _recount_job_progress(db: Session, job: BudgetProjectPricingDraftQuoteJob) -> None:
    lines = (
        db.query(BudgetProjectPricingDraftQuoteJobLine)
        .filter(BudgetProjectPricingDraftQuoteJobLine.job_id == job.id)
        .all()
    )
    job.total_line_count = len(lines)
    job.enterprise_priced_count = sum(
        line.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_ENTERPRISE_MATCHED for line in lines
    )
    job.ai_total_count = sum(
        line.status
        in {
            BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING,
            BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_RUNNING,
            BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED,
            BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED,
        }
        for line in lines
    )
    job.ai_completed_count = sum(
        line.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED for line in lines
    )
    job.ai_failed_count = sum(
        line.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED for line in lines
    )
    job.skipped_count = sum(
        line.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED for line in lines
    )
    job.progress_percent = _job_progress_percent(job)


def _initial_job_line_status(line: BudgetProjectPricingDraftLine) -> tuple[str, str]:
    if line.base_unit_price is not None:
        return BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_ENTERPRISE_MATCHED, "enterprise_quota"
    if line.manual_unit_price is not None:
        return BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED, "manual"
    if line.ai_estimated_unit_price is not None:
        return BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED, "ai_estimate"
    return BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING, "ai_estimate"


def create_budget_pricing_draft_quote_job(
    db: Session,
    profile: BudgetProjectProfile,
    current_user: User,
    payload: BudgetPricingDraftQuoteJobCreate,
) -> BudgetProjectPricingDraftQuoteJob:
    if payload.pricing_mode != PRICING_MODE_ENTERPRISE_AI:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_QUOTE_JOB_MODE_INVALID", status_code=422)

    draft = create_or_rebuild_budget_pricing_draft(
        db,
        profile,
        current_user,
        pricing_mode=PRICING_MODE_ENTERPRISE_AI,
        source_import_batch_id=payload.source_import_batch_id,
        source_import_revision_id=payload.source_import_revision_id,
        expected_active_quota_version_id=payload.expected_active_quota_version_id,
        expected_revision=payload.expected_revision,
        reason=payload.reason or "one_click_enterprise_ai_quote",
    )
    active_job = (
        db.query(BudgetProjectPricingDraftQuoteJob)
        .filter(
            BudgetProjectPricingDraftQuoteJob.draft_id == draft.id,
            BudgetProjectPricingDraftQuoteJob.status.in_(
                [
                    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED,
                    BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_RUNNING,
                ]
            ),
        )
        .order_by(BudgetProjectPricingDraftQuoteJob.id.desc())
        .first()
    )
    if active_job is not None:
        setattr(active_job, "_quote_job_was_created", False)
        return active_job

    job = BudgetProjectPricingDraftQuoteJob(
        job_uuid=str(uuid4()),
        account_id=draft.account_id,
        project_id=draft.project_id,
        draft_id=draft.id,
        requested_mode=PRICING_MODE_ENTERPRISE_AI,
        status=BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_QUEUED,
        progress_percent=0,
        current_message="任务已创建，等待后台开始计价",
        source_import_batch_id=draft.source_import_batch_id,
        source_import_revision_id=draft.source_import_revision_id,
        enterprise_quota_version_id=draft.enterprise_quota_version_id,
        request_json=_json_dump(payload.model_dump(mode="json")),
        created_by=current_user.id,
    )
    db.add(job)
    db.flush()

    lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order, BudgetProjectPricingDraftLine.id)
        .all()
    )
    for line in lines:
        status, source = _initial_job_line_status(line)
        db.add(
            BudgetProjectPricingDraftQuoteJobLine(
                job_id=job.id,
                draft_line_id=line.id,
                line_uuid=line.line_uuid,
                source_row_key=line.source_row_key,
                source_sort_order=line.source_sort_order,
                item_name=line.item_name,
                status=status,
                source=source,
                provider=(line.ai_estimate_snapshot_json and "existing"),
                unit_price=line.effective_unit_price,
            )
        )
    db.flush()
    _recount_job_progress(db, job)
    if job.ai_total_count == job.ai_completed_count and job.ai_failed_count == 0:
        job.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_SUCCEEDED
        job.progress_percent = 100
        job.current_message = "企业定额已覆盖全部需要计价的清单行"
        job.finished_at = _utcnow()
    _append_event(
        db,
        draft=draft,
        current_user=current_user,
        event_type="quote_job_created",
        from_mode=draft.pricing_mode,
        from_revision=draft.revision,
        event={
            "job_uuid": job.job_uuid,
            "ai_total_count": job.ai_total_count,
            "enterprise_priced_count": job.enterprise_priced_count,
        },
    )
    db.flush()
    setattr(job, "_quote_job_was_created", True)
    return job


def get_budget_pricing_draft_quote_job(
    db: Session,
    identifier: str | int,
) -> BudgetProjectPricingDraftQuoteJob:
    text = str(identifier).strip()
    query = db.query(BudgetProjectPricingDraftQuoteJob)
    query = query.filter(BudgetProjectPricingDraftQuoteJob.id == int(text)) if text.isdigit() else query.filter(BudgetProjectPricingDraftQuoteJob.job_uuid == text)
    job = query.one_or_none()
    if job is None:
        raise BudgetPricingError("BUDGET_PRICING_DRAFT_QUOTE_JOB_NOT_FOUND", status_code=404)
    return job


def get_current_budget_pricing_draft_quote_job(
    db: Session,
    *,
    account_id: int,
    project_id: int,
) -> BudgetProjectPricingDraftQuoteJob | None:
    return (
        db.query(BudgetProjectPricingDraftQuoteJob)
        .filter(
            BudgetProjectPricingDraftQuoteJob.account_id == account_id,
            BudgetProjectPricingDraftQuoteJob.project_id == project_id,
        )
        .order_by(BudgetProjectPricingDraftQuoteJob.id.desc())
        .first()
    )


def _mark_job_running(job_id: int) -> tuple[int, int, int]:
    db = SessionLocal()
    try:
        job = (
            db.query(BudgetProjectPricingDraftQuoteJob)
            .filter(BudgetProjectPricingDraftQuoteJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            return 0, 1, 1
        request = _json_load(job.request_json, {})
        concurrency = int(request.get("ai_concurrency") or 3)
        concurrency = max(1, min(3, concurrency))
        batch_size = int(request.get("ai_batch_size") or 6)
        batch_size = max(1, min(20, batch_size))
        if job.status in TERMINAL_JOB_STATUSES:
            return 0, concurrency, batch_size
        job.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_RUNNING
        job.started_at = job.started_at or _utcnow()
        job.current_message = "正在调用基础定额与 AI 估价链路"
        _recount_job_progress(db, job)
        db.commit()
        return job.id, concurrency, batch_size
    finally:
        db.close()


def _pending_job_line_ids(job_id: int) -> list[int]:
    db = SessionLocal()
    try:
        return [
            int(row.id)
            for row in db.query(BudgetProjectPricingDraftQuoteJobLine.id)
            .filter(
                BudgetProjectPricingDraftQuoteJobLine.job_id == job_id,
                BudgetProjectPricingDraftQuoteJobLine.status == BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING,
            )
            .order_by(BudgetProjectPricingDraftQuoteJobLine.source_sort_order, BudgetProjectPricingDraftQuoteJobLine.id)
            .all()
        ]
    finally:
        db.close()


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _batch_retry_delay_seconds(*, depth: int, circuit_open: bool = False) -> int:
    if circuit_open:
        reset_seconds = int(settings.model_gateway_circuit_reset_seconds or 60)
        return max(1, min(90, reset_seconds + 2 + depth * 5))
    return max(1, min(30, 3 + depth * 5))


def _snapshot_for_job_line(job_line_id: int) -> tuple[dict[str, Any] | None, SimpleNamespace | None, str | None]:
    db = SessionLocal()
    try:
        job_line = (
            db.query(BudgetProjectPricingDraftQuoteJobLine)
            .filter(BudgetProjectPricingDraftQuoteJobLine.id == job_line_id)
            .with_for_update()
            .one_or_none()
        )
        if job_line is None:
            return None, None, "missing_job_line"
        job = db.query(BudgetProjectPricingDraftQuoteJob).filter(BudgetProjectPricingDraftQuoteJob.id == job_line.job_id).one()
        line = (
            db.query(BudgetProjectPricingDraftLine)
            .filter(BudgetProjectPricingDraftLine.id == job_line.draft_line_id)
            .one_or_none()
        )
        draft = db.query(BudgetProjectPricingDraft).filter(BudgetProjectPricingDraft.id == job.draft_id).one_or_none()
        actor = db.query(User).filter(User.id == job.created_by).one_or_none()
        if line is None or draft is None or actor is None:
            job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED
            job_line.error_json = _json_dump({"code": "BUDGET_PRICING_DRAFT_QUOTE_JOB_SOURCE_MISSING"})
            job_line.finished_at = _utcnow()
            db.commit()
            return None, None, "source_missing"
        if line.base_unit_price is not None:
            job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_ENTERPRISE_MATCHED
            job_line.source = "enterprise_quota"
            job_line.unit_price = line.effective_unit_price
            job_line.finished_at = _utcnow()
            db.commit()
            return None, None, "enterprise_matched"
        if line.manual_unit_price is not None:
            job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED
            job_line.source = "manual"
            job_line.unit_price = line.effective_unit_price
            job_line.finished_at = _utcnow()
            db.commit()
            return None, None, "manual_price_exists"
        if line.ai_estimated_unit_price is not None:
            job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED
            job_line.source = "ai_estimate"
            job_line.unit_price = line.ai_estimated_unit_price
            job_line.finished_at = _utcnow()
            db.commit()
            return None, None, "ai_exists"
        job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_RUNNING
        job_line.started_at = _utcnow()
        snapshot = build_budget_pricing_ai_estimate_input(draft, line)
        user_ref = SimpleNamespace(id=int(actor.id), username=str(actor.username or ""))
        db.commit()
        return snapshot, user_ref, None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_job_line_failed(job_line_id: int, exc: BaseException) -> None:
    db = SessionLocal()
    try:
        job_line = db.query(BudgetProjectPricingDraftQuoteJobLine).filter(BudgetProjectPricingDraftQuoteJobLine.id == job_line_id).one_or_none()
        if job_line is not None:
            job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_FAILED
            job_line.error_json = _json_dump(_error_payload(exc))
            job_line.finished_at = _utcnow()
            db.flush()
            job = db.query(BudgetProjectPricingDraftQuoteJob).filter(BudgetProjectPricingDraftQuoteJob.id == job_line.job_id).one()
            _recount_job_progress(db, job)
        db.commit()
    finally:
        db.close()


def _mark_job_line_succeeded(
    job_line_id: int,
    *,
    estimate: dict[str, Any],
    snapshot: dict[str, Any],
    user_ref: SimpleNamespace,
) -> None:
    db = SessionLocal()
    try:
        job_line = (
            db.query(BudgetProjectPricingDraftQuoteJobLine)
            .filter(BudgetProjectPricingDraftQuoteJobLine.id == job_line_id)
            .with_for_update()
            .one()
        )
        job = (
            db.query(BudgetProjectPricingDraftQuoteJob)
            .filter(BudgetProjectPricingDraftQuoteJob.id == job_line.job_id)
            .one()
        )
        try:
            draft, line = apply_budget_pricing_ai_estimate_to_line(
                db,
                draft_id=job.draft_id,
                line_id=job_line.draft_line_id,
                current_user=user_ref,  # type: ignore[arg-type]
                estimate=estimate,
                input_snapshot=snapshot,
                reason=f"quote_job:{job.job_uuid}",
                event_type="quote_job_ai_estimate_updated",
            )
        except BudgetPricingError as exc:
            if exc.code in {
                "BUDGET_PRICING_AI_ESTIMATE_MANUAL_PRICE_EXISTS",
                "BUDGET_PRICING_AI_ESTIMATE_BASE_PRICE_EXISTS",
            }:
                job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_SKIPPED
                job_line.source = "stale"
                job_line.error_json = _json_dump(_error_payload(exc))
                job_line.finished_at = _utcnow()
                _recount_job_progress(db, job)
                db.commit()
                return
            raise
        job_line.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_SUCCEEDED
        job_line.provider = estimate.get("provider")
        job_line.model = estimate.get("model")
        job_line.unit_price = line.ai_estimated_unit_price
        job_line.result_json = _json_dump({"estimate": estimate, "line_revision": line.line_revision, "draft_revision": draft.revision})
        job_line.finished_at = _utcnow()
        _recount_job_progress(db, job)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _snapshot_row_id(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("line_uuid") or snapshot.get("line_id") or snapshot.get("source_row_key") or "").strip()


async def _process_job_line_batch(
    job_line_ids: list[int],
    semaphore: asyncio.Semaphore,
    *,
    depth: int = 0,
) -> None:
    async with semaphore:
        await _process_job_line_batch_unlocked(job_line_ids, depth=depth)


async def _process_job_line_batch_unlocked(job_line_ids: list[int], *, depth: int = 0) -> None:
    work_items: list[tuple[int, dict[str, Any], SimpleNamespace]] = []
    for job_line_id in job_line_ids:
        try:
            snapshot, user_ref, skip_reason = _snapshot_for_job_line(job_line_id)
            if skip_reason or snapshot is None or user_ref is None:
                continue
            work_items.append((job_line_id, snapshot, user_ref))
        except Exception as exc:  # noqa: BLE001 - background jobs must record every row failure
            _mark_job_line_failed(job_line_id, exc)
    if not work_items:
        return
    try:
        estimates = await generate_budget_pricing_ai_estimate_batch(
            [item[1] for item in work_items],
            current_user=work_items[0][2],  # type: ignore[arg-type]
        )
    except CircuitOpenError:
        if depth < BATCH_CIRCUIT_RETRY_LIMIT:
            await asyncio.sleep(_batch_retry_delay_seconds(depth=depth, circuit_open=True))
            await _process_job_line_batch_unlocked(job_line_ids, depth=depth + 1)
            return
        for job_line_id, _snapshot, _user_ref in work_items:
            _mark_job_line_failed(job_line_id, CircuitOpenError("batch circuit remained open after retry"))
        return
    except Exception as exc:  # noqa: BLE001 - split failed batches before giving up
        if len(work_items) > 1 and depth < 2:
            await asyncio.sleep(_batch_retry_delay_seconds(depth=depth))
            midpoint = max(1, len(work_items) // 2)
            await _process_job_line_batch_unlocked([item[0] for item in work_items[:midpoint]], depth=depth + 1)
            await _process_job_line_batch_unlocked([item[0] for item in work_items[midpoint:]], depth=depth + 1)
            return
        if depth < BATCH_FAILURE_RETRY_LIMIT:
            await asyncio.sleep(_batch_retry_delay_seconds(depth=depth))
            await _process_job_line_batch_unlocked([item[0] for item in work_items], depth=depth + 1)
            return
        for job_line_id, _snapshot, _user_ref in work_items:
            _mark_job_line_failed(job_line_id, exc)
        return

    missing: list[int] = []
    for job_line_id, snapshot, user_ref in work_items:
        row_id = _snapshot_row_id(snapshot)
        estimate = estimates.get(row_id)
        if estimate is None:
            missing.append(job_line_id)
            continue
        try:
            _mark_job_line_succeeded(job_line_id, estimate=estimate, snapshot=snapshot, user_ref=user_ref)
        except Exception as exc:  # noqa: BLE001 - one bad row should not fail the batch
            _mark_job_line_failed(job_line_id, exc)
    if missing:
        if len(missing) > 1 and depth < 2:
            for job_line_id in missing:
                await _process_job_line_batch_unlocked([job_line_id], depth=depth + 1)
        else:
            for job_line_id in missing:
                _mark_job_line_failed(
                    job_line_id,
                    BudgetPricingError("BUDGET_PRICING_AI_ESTIMATE_BATCH_MISSING_ROW", status_code=502),
                )


def _finish_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = (
            db.query(BudgetProjectPricingDraftQuoteJob)
            .filter(BudgetProjectPricingDraftQuoteJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return
        _recount_job_progress(db, job)
        pending = (
            db.query(BudgetProjectPricingDraftQuoteJobLine)
            .filter(
                BudgetProjectPricingDraftQuoteJobLine.job_id == job.id,
                BudgetProjectPricingDraftQuoteJobLine.status.in_(
                    [
                        BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_PENDING,
                        BUDGET_PRICING_DRAFT_QUOTE_JOB_LINE_AI_RUNNING,
                    ]
                ),
            )
            .count()
        )
        if pending:
            job.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_FAILED
            job.error_json = _json_dump({"code": "BUDGET_PRICING_DRAFT_QUOTE_JOB_PENDING_REMAINED", "pending_count": pending})
            job.current_message = "后台计价异常终止，仍有未完成行"
        elif int(job.ai_failed_count or 0) > 0:
            job.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_PARTIAL_FAILED
            job.current_message = f"计价完成，但 {job.ai_failed_count} 行 AI 估价失败，需人工补价"
        else:
            job.status = BUDGET_PRICING_DRAFT_QUOTE_JOB_STATUS_SUCCEEDED
            job.current_message = "基础定额与 AI 估价已全部完成"
        job.progress_percent = 100
        job.finished_at = _utcnow()
        job.result_json = _json_dump(
            {
                "total_line_count": job.total_line_count,
                "enterprise_priced_count": job.enterprise_priced_count,
                "ai_total_count": job.ai_total_count,
                "ai_completed_count": job.ai_completed_count,
                "ai_failed_count": job.ai_failed_count,
                "skipped_count": job.skipped_count,
            }
        )
        db.commit()
    finally:
        db.close()


async def run_budget_pricing_draft_quote_job(job_id: int) -> None:
    actual_job_id, concurrency, batch_size = _mark_job_running(job_id)
    if not actual_job_id:
        return
    pending = _pending_job_line_ids(actual_job_id)
    if pending:
        semaphore = asyncio.Semaphore(concurrency)
        batches = _chunked(pending, batch_size)
        await asyncio.gather(*[_process_job_line_batch(batch, semaphore) for batch in batches])
    _finish_job(actual_job_id)


def run_budget_pricing_draft_quote_job_sync(job_id: int) -> None:
    asyncio.run(run_budget_pricing_draft_quote_job(job_id))


def serialize_budget_pricing_draft_quote_job(
    job: BudgetProjectPricingDraftQuoteJob,
    *,
    include_lines: bool = False,
) -> dict[str, Any]:
    data = {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "account_id": job.account_id,
        "project_id": job.project_id,
        "draft_id": job.draft_id,
        "requested_mode": job.requested_mode,
        "status": job.status,
        "terminal": job.status in TERMINAL_JOB_STATUSES,
        "progress_percent": job.progress_percent,
        "current_message": job.current_message,
        "total_line_count": job.total_line_count,
        "enterprise_priced_count": job.enterprise_priced_count,
        "ai_total_count": job.ai_total_count,
        "ai_completed_count": job.ai_completed_count,
        "ai_failed_count": job.ai_failed_count,
        "skipped_count": job.skipped_count,
        "source_import_batch_id": job.source_import_batch_id,
        "source_import_revision_id": job.source_import_revision_id,
        "enterprise_quota_version_id": job.enterprise_quota_version_id,
        "request": _json_load(job.request_json, {}),
        "result": _json_load(job.result_json, None),
        "error": _json_load(job.error_json, None),
        "created_by": job.created_by,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
    if include_lines:
        data["lines"] = [
            {
                "id": line.id,
                "draft_line_id": line.draft_line_id,
                "line_uuid": line.line_uuid,
                "source_row_key": line.source_row_key,
                "source_sort_order": line.source_sort_order,
                "item_name": line.item_name,
                "status": line.status,
                "source": line.source,
                "provider": line.provider,
                "model": line.model,
                "unit_price": _decimal_text(line.unit_price),
                "result": _json_load(line.result_json, None),
                "error": _json_load(line.error_json, None),
                "started_at": line.started_at.isoformat() if line.started_at else None,
                "finished_at": line.finished_at.isoformat() if line.finished_at else None,
            }
            for line in job.lines
        ]
    return data
