from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cost_audit import CostAccessAuditLog
from app.models.cost_item import (
    COST_SOURCE_AI_SUGGESTED,
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    CostItem,
)
from app.models.project_progress import Project, ProjectTask, ProjectTaskEvent, ProjectTaskEvidence
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.quote_preview_draft import PREVIEW_DRAFT_STATUS_EDITING, QuotePreviewDraft
from app.services.cost_rag_sync import cost_rag_sync_status_summary
from app.services.project_progress import non_budget_project_clause
from app.services.quote_dashboard import CN_TZ, VALID_RANGES, _avg, _db_time, _range_bounds, _to_local


logger = logging.getLogger(__name__)

STALE_PREVIEW_DRAFT_HOURS = 24

COST_STATUS_LABELS = {
    COST_STATUS_ACTIVE: "active",
    COST_STATUS_DRAFT: "draft",
    COST_STATUS_ARCHIVED: "archived",
}
COST_SOURCE_LABELS = {
    COST_SOURCE_AI_SUGGESTED: "AI 建议",
    "manual": "人工维护",
    "excel_import": "Excel 导入",
    "import": "导入",
    "unknown": "未知",
}


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _count(value: Any) -> int:
    return int(value or 0)


def _float(value: Any) -> float:
    return float(value or 0)


def _database_head(db: Session) -> str | None:
    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        logger.debug("business_lite_dashboard_database_head_unavailable", exc_info=True)
        return None


def _status_counts(rows: list[tuple[str | None, int]]) -> dict[str, int]:
    return {status or "unknown": _count(count) for status, count in rows}


def _distribution(
    counts: dict[str, int],
    *,
    preferred_order: list[str] | None = None,
    labels: dict[str, str] | None = None,
    key_name: str = "status",
) -> list[dict[str, Any]]:
    preferred_order = preferred_order or []
    labels = labels or {}
    keys = [key for key in preferred_order if key in counts]
    keys.extend(sorted(key for key in counts if key not in keys))
    return [{key_name: key, "count": counts[key], "label": labels.get(key, key)} for key in keys]


def _date_key(value: datetime | None) -> str | None:
    local_value = _to_local(value)
    return local_value.date().isoformat() if local_value else None


def _daily_seed(start: datetime, end: datetime, defaults: dict[str, int]) -> dict[str, dict[str, Any]]:
    current = start.date()
    end_date = end.date()
    result: dict[str, dict[str, Any]] = {}
    while current <= end_date:
        day = current.isoformat()
        result[day] = {"date": day, **defaults}
        current += timedelta(days=1)
    return result


def _safe_section(
    *,
    key: str,
    builder: Callable[[], dict[str, Any]],
    fallback: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    try:
        data = builder()
        data.setdefault("available", True)
        return data
    except Exception as exc:
        logger.exception("business_lite_dashboard_section_failed", extra={"section": key})
        errors.append({"section": key, "message": str(exc) or exc.__class__.__name__})
        data = dict(fallback)
        data["available"] = False
        return data


def _build_quote_daily_trend(
    *,
    start: datetime,
    end: datetime,
    job_rows: list[tuple[datetime | None, str | None]],
    history_rows: list[tuple[datetime | None]],
) -> list[dict[str, Any]]:
    days = _daily_seed(
        start,
        end,
        {
            "task_count": 0,
            "success_count": 0,
            "failed_or_timeout_count": 0,
            "pushed_count": 0,
        },
    )
    for created_at, status in job_rows:
        day = _date_key(created_at)
        if not day:
            continue
        row = days.setdefault(
            day,
            {
                "date": day,
                "task_count": 0,
                "success_count": 0,
                "failed_or_timeout_count": 0,
                "pushed_count": 0,
            },
        )
        row["task_count"] += 1
        if status == "succeeded":
            row["success_count"] += 1
        if status in {"failed", "timeout"}:
            row["failed_or_timeout_count"] += 1
    for (created_at,) in history_rows:
        day = _date_key(created_at)
        if not day:
            continue
        row = days.setdefault(
            day,
            {
                "date": day,
                "task_count": 0,
                "success_count": 0,
                "failed_or_timeout_count": 0,
                "pushed_count": 0,
            },
        )
        row["pushed_count"] += 1
    return list(days.values())


def _build_quote_section(db: Session, *, start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
    db_start = _db_time(start)
    db_end = _db_time(end)
    stale_before = _db_time(now - timedelta(hours=STALE_PREVIEW_DRAFT_HOURS))

    job_query = db.query(QuoteJob).filter(QuoteJob.created_at >= db_start, QuoteJob.created_at <= db_end)
    job_trend_rows = job_query.with_entities(QuoteJob.created_at, QuoteJob.status).all()
    status_counts = _status_counts(job_query.with_entities(QuoteJob.status, func.count(QuoteJob.id)).group_by(QuoteJob.status).all())
    duration_values = [
        row[0]
        for row in job_query.with_entities(QuoteJob.duration_ms)
        .filter(QuoteJob.status == "succeeded", QuoteJob.duration_ms.isnot(None))
        .all()
    ]
    pushed_count, pushed_total_amount = (
        db.query(func.count(QuoteHistory.id), func.coalesce(func.sum(QuoteHistory.total_amount), 0))
        .filter(QuoteHistory.created_at >= db_start, QuoteHistory.created_at <= db_end)
        .first()
    )
    range_draft_count = (
        db.query(func.count(QuotePreviewDraft.id))
        .filter(
            QuotePreviewDraft.status == PREVIEW_DRAFT_STATUS_EDITING,
            QuotePreviewDraft.updated_at >= db_start,
            QuotePreviewDraft.updated_at <= db_end,
        )
        .scalar()
    )
    editing_draft_count = (
        db.query(func.count(QuotePreviewDraft.id))
        .filter(QuotePreviewDraft.status == PREVIEW_DRAFT_STATUS_EDITING)
        .scalar()
    )
    stale_draft_count = (
        db.query(func.count(QuotePreviewDraft.id))
        .filter(QuotePreviewDraft.status == PREVIEW_DRAFT_STATUS_EDITING, QuotePreviewDraft.updated_at < stale_before)
        .scalar()
    )
    history_trend_rows = (
        db.query(QuoteHistory.created_at)
        .filter(QuoteHistory.created_at >= db_start, QuoteHistory.created_at <= db_end)
        .all()
    )

    return {
        "task_count": sum(status_counts.values()),
        "success_count": status_counts.get("succeeded", 0),
        "failed_count": status_counts.get("failed", 0),
        "timeout_count": status_counts.get("timeout", 0),
        "cancelled_count": status_counts.get("cancelled", 0),
        "draft_count": _count(editing_draft_count),
        "range_draft_count": _count(range_draft_count),
        "stale_draft_count": _count(stale_draft_count),
        "pushed_count": _count(pushed_count),
        "pushed_total_amount": round(_float(pushed_total_amount), 2),
        "avg_duration_ms": _avg(duration_values),
        "status_distribution": _distribution(
            status_counts,
            preferred_order=["queued", "running", "succeeded", "failed", "timeout", "cancelled"],
        ),
        "daily_trend": _build_quote_daily_trend(
            start=start,
            end=end,
            job_rows=job_trend_rows,
            history_rows=history_trend_rows,
        ),
        "stale_draft_hours": STALE_PREVIEW_DRAFT_HOURS,
    }


def _build_project_daily_trend(
    *,
    start: datetime,
    end: datetime,
    event_rows: list[tuple[datetime | None, str | None]],
    bypassed_missing_rows: list[tuple[int | None, datetime | None]],
) -> list[dict[str, Any]]:
    days = _daily_seed(
        start,
        end,
        {
            "bypass_gate_event_count": 0,
            "bypassed_missing_evidence_count": 0,
            "soft_reminder_event_count": 0,
        },
    )
    for created_at, event_type in event_rows:
        day = _date_key(created_at)
        if not day:
            continue
        row = days.setdefault(
            day,
            {
                "date": day,
                "bypass_gate_event_count": 0,
                "bypassed_missing_evidence_count": 0,
                "soft_reminder_event_count": 0,
            },
        )
        if event_type == "task_completed_bypass_gate":
            row["bypass_gate_event_count"] += 1
        elif event_type == "task_completed_without_evidence":
            row["soft_reminder_event_count"] += 1

    missing_task_ids_by_day: dict[str, set[int]] = {}
    for task_id, created_at in bypassed_missing_rows:
        day = _date_key(created_at)
        if not day or task_id is None:
            continue
        missing_task_ids_by_day.setdefault(day, set()).add(int(task_id))
    for day, task_ids in missing_task_ids_by_day.items():
        row = days.setdefault(
            day,
            {
                "date": day,
                "bypass_gate_event_count": 0,
                "bypassed_missing_evidence_count": 0,
                "soft_reminder_event_count": 0,
            },
        )
        row["bypassed_missing_evidence_count"] = len(task_ids)
    return list(days.values())


def _build_cost_section(db: Session, *, start: datetime, end: datetime) -> dict[str, Any]:
    db_start = _db_time(start)
    db_end = _db_time(end)
    status_counts = _status_counts(db.query(CostItem.status, func.count(CostItem.id)).group_by(CostItem.status).all())
    source_counts = _status_counts(db.query(CostItem.source, func.count(CostItem.id)).group_by(CostItem.source).all())
    no_cost_draft_count = (
        db.query(func.count(CostItem.id))
        .filter(CostItem.status == COST_STATUS_DRAFT, CostItem.source == COST_SOURCE_AI_SUGGESTED)
        .scalar()
    )
    audit_event_count = (
        db.query(func.count(CostAccessAuditLog.id))
        .filter(CostAccessAuditLog.created_at >= db_start, CostAccessAuditLog.created_at <= db_end)
        .scalar()
    )
    rag = cost_rag_sync_status_summary(db)
    latest_successful_run = rag.get("latest_successful_run") or {}

    return {
        "active_count": status_counts.get(COST_STATUS_ACTIVE, 0),
        "draft_count": status_counts.get(COST_STATUS_DRAFT, 0),
        "archived_count": status_counts.get(COST_STATUS_ARCHIVED, 0),
        "no_cost_draft_count": _count(no_cost_draft_count),
        "audit_event_count": _count(audit_event_count),
        "by_status": {
            COST_STATUS_ACTIVE: status_counts.get(COST_STATUS_ACTIVE, 0),
            COST_STATUS_DRAFT: status_counts.get(COST_STATUS_DRAFT, 0),
            COST_STATUS_ARCHIVED: status_counts.get(COST_STATUS_ARCHIVED, 0),
        },
        "by_source": source_counts,
        "status_distribution": _distribution(
            {
                COST_STATUS_ACTIVE: status_counts.get(COST_STATUS_ACTIVE, 0),
                COST_STATUS_DRAFT: status_counts.get(COST_STATUS_DRAFT, 0),
                COST_STATUS_ARCHIVED: status_counts.get(COST_STATUS_ARCHIVED, 0),
            },
            preferred_order=[COST_STATUS_ACTIVE, COST_STATUS_DRAFT, COST_STATUS_ARCHIVED],
            labels=COST_STATUS_LABELS,
        ),
        "source_distribution": _distribution(
            source_counts,
            preferred_order=["manual", "excel_import", COST_SOURCE_AI_SUGGESTED],
            labels=COST_SOURCE_LABELS,
            key_name="source",
        ),
        "rag_status": rag.get("status", "unknown"),
        "rag_status_label": rag.get("status_label", "未知"),
        "rag_message": rag.get("message"),
        "rag_needs_sync": bool(rag.get("needs_sync")),
        "rag_is_stale": bool(rag.get("is_stale")),
        "last_success_sync_at": latest_successful_run.get("finished_at") or latest_successful_run.get("started_at"),
        "latest_run": rag.get("latest_run"),
    }


def _build_project_section(db: Session, *, start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
    db_start = _db_time(start)
    db_end = _db_time(end)
    now_db = _db_time(now)
    project_counts = _status_counts(
        db.query(Project.status, func.count(Project.id))
        .filter(non_budget_project_clause(Project.id))
        .group_by(Project.status)
        .all()
    )
    task_counts = _status_counts(
        db.query(ProjectTask.status, func.count(ProjectTask.id))
        .filter(non_budget_project_clause(ProjectTask.project_id))
        .group_by(ProjectTask.status)
        .all()
    )
    active_evidence_task_ids = db.query(ProjectTaskEvidence.task_id).filter(
        ProjectTaskEvidence.status == "active",
        non_budget_project_clause(ProjectTaskEvidence.project_id),
    )

    open_task_filter = ProjectTask.status.notin_(["done", "cancelled"])
    missing_evidence_count = (
        db.query(func.count(ProjectTask.id))
        .filter(
            non_budget_project_clause(ProjectTask.project_id),
            ProjectTask.evidence_policy.in_(["soft_reminder", "complete_required"]),
            ProjectTask.status != "cancelled",
            ProjectTask.id.notin_(active_evidence_task_ids),
        )
        .scalar()
    )
    complete_required_count = (
        db.query(func.count(ProjectTask.id))
        .filter(
            non_budget_project_clause(ProjectTask.project_id),
            ProjectTask.evidence_policy == "complete_required",
            ProjectTask.status != "cancelled",
        )
        .scalar()
    )
    bypass_gate_event_count = (
        db.query(func.count(ProjectTaskEvent.id))
        .filter(
            non_budget_project_clause(ProjectTaskEvent.project_id),
            ProjectTaskEvent.event_type == "task_completed_bypass_gate",
            ProjectTaskEvent.created_at >= db_start,
            ProjectTaskEvent.created_at <= db_end,
        )
        .scalar()
    )
    bypassed_missing_rows = (
        db.query(ProjectTaskEvent.task_id, ProjectTaskEvent.created_at)
        .join(ProjectTask, ProjectTask.id == ProjectTaskEvent.task_id)
        .filter(
            non_budget_project_clause(ProjectTask.project_id),
            ProjectTaskEvent.event_type == "task_completed_bypass_gate",
            ProjectTaskEvent.created_at >= db_start,
            ProjectTaskEvent.created_at <= db_end,
            ProjectTaskEvent.task_id.isnot(None),
            ProjectTask.evidence_policy == "complete_required",
            ProjectTask.status != "cancelled",
            ProjectTask.id.notin_(active_evidence_task_ids),
        )
        .all()
    )
    hard_gate_bypassed_missing_evidence_count = len({int(task_id) for task_id, _ in bypassed_missing_rows if task_id is not None})
    soft_reminder_event_count = (
        db.query(func.count(ProjectTaskEvent.id))
        .filter(
            non_budget_project_clause(ProjectTaskEvent.project_id),
            ProjectTaskEvent.event_type == "task_completed_without_evidence",
            ProjectTaskEvent.created_at >= db_start,
            ProjectTaskEvent.created_at <= db_end,
        )
        .scalar()
    )
    overdue_task_count = (
        db.query(func.count(ProjectTask.id))
        .filter(
            non_budget_project_clause(ProjectTask.project_id),
            open_task_filter,
            ProjectTask.due_at.isnot(None),
            ProjectTask.due_at < now_db,
        )
        .scalar()
    )
    trend_event_rows = (
        db.query(ProjectTaskEvent.created_at, ProjectTaskEvent.event_type)
        .filter(
            non_budget_project_clause(ProjectTaskEvent.project_id),
            ProjectTaskEvent.event_type.in_(["task_completed_bypass_gate", "task_completed_without_evidence"]),
            ProjectTaskEvent.created_at >= db_start,
            ProjectTaskEvent.created_at <= db_end,
        )
        .all()
    )

    return {
        "project_count": sum(project_counts.values()),
        "active_project_count": sum(project_counts.get(status, 0) for status in ["planning", "active", "paused"]),
        "completed_project_count": project_counts.get("completed", 0),
        "task_count": sum(task_counts.values()),
        "open_task_count": sum(count for status, count in task_counts.items() if status not in {"done", "cancelled"}),
        "done_task_count": task_counts.get("done", 0),
        "blocked_task_count": task_counts.get("blocked", 0),
        "overdue_task_count": _count(overdue_task_count),
        "missing_evidence_task_count": _count(missing_evidence_count),
        "complete_required_task_count": _count(complete_required_count),
        "bypass_gate_event_count": _count(bypass_gate_event_count),
        "hard_gate_bypassed_missing_evidence_count": _count(hard_gate_bypassed_missing_evidence_count),
        "soft_reminder_event_count": _count(soft_reminder_event_count),
        "project_status_distribution": _distribution(project_counts),
        "task_status_distribution": _distribution(task_counts, preferred_order=["todo", "started", "submitted", "blocked", "done", "cancelled"]),
        "daily_trend": _build_project_daily_trend(
            start=start,
            end=end,
            event_rows=trend_event_rows,
            bypassed_missing_rows=bypassed_missing_rows,
        ),
    }


def _build_system_section(db: Session) -> dict[str, Any]:
    feature_flags = {
        "dashboard_business_lite": settings.feature_dashboard_business_lite,
        "dashboard_quote": settings.feature_dashboard_quote,
        "dashboard_response": settings.feature_dashboard_response,
        "dashboard_project": settings.feature_dashboard_project,
        "cost_db": settings.feature_cost_db,
        "project_progress": settings.feature_project_progress,
        "no_cost_draft_capture": settings.feature_no_cost_draft_capture,
    }
    return {
        "database_head": _database_head(db),
        "app_env": settings.app_env,
        "mode": "public_access" if settings.public_access_enabled else "internal_trial",
        "feature_flags": feature_flags,
        "health_endpoints": {
            "live": "/health/live",
            "ready": "/health/ready",
            "ops_dashboard": "/api/v1/admin/ops/dashboard",
        },
    }


def _fallback_quote() -> dict[str, Any]:
    return {
        "task_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "timeout_count": 0,
        "cancelled_count": 0,
        "draft_count": 0,
        "range_draft_count": 0,
        "stale_draft_count": 0,
        "pushed_count": 0,
        "pushed_total_amount": 0.0,
        "avg_duration_ms": None,
        "status_distribution": [],
        "daily_trend": [],
        "stale_draft_hours": STALE_PREVIEW_DRAFT_HOURS,
    }


def _fallback_cost() -> dict[str, Any]:
    return {
        "active_count": 0,
        "draft_count": 0,
        "archived_count": 0,
        "no_cost_draft_count": 0,
        "audit_event_count": 0,
        "by_status": {COST_STATUS_ACTIVE: 0, COST_STATUS_DRAFT: 0, COST_STATUS_ARCHIVED: 0},
        "by_source": {},
        "status_distribution": [],
        "source_distribution": [],
        "rag_status": "unknown",
        "rag_status_label": "未知",
        "rag_message": None,
        "rag_needs_sync": False,
        "rag_is_stale": False,
        "last_success_sync_at": None,
        "latest_run": None,
    }


def _fallback_project() -> dict[str, Any]:
    return {
        "project_count": 0,
        "active_project_count": 0,
        "completed_project_count": 0,
        "task_count": 0,
        "open_task_count": 0,
        "done_task_count": 0,
        "blocked_task_count": 0,
        "overdue_task_count": 0,
        "missing_evidence_task_count": 0,
        "complete_required_task_count": 0,
        "bypass_gate_event_count": 0,
        "hard_gate_bypassed_missing_evidence_count": 0,
        "soft_reminder_event_count": 0,
        "project_status_distribution": [],
        "task_status_distribution": [],
        "daily_trend": [],
    }


def _fallback_system() -> dict[str, Any]:
    return {
        "database_head": None,
        "app_env": settings.app_env,
        "mode": "public_access" if settings.public_access_enabled else "internal_trial",
        "feature_flags": {},
        "health_endpoints": {
            "live": "/health/live",
            "ready": "/health/ready",
            "ops_dashboard": "/api/v1/admin/ops/dashboard",
        },
    }


def _risk(
    *,
    key: str,
    severity: str,
    title: str,
    count: int,
    action: str,
    target_path: str,
) -> dict[str, Any] | None:
    if count <= 0:
        return None
    return {
        "key": key,
        "severity": severity,
        "title": title,
        "count": count,
        "action": action,
        "target_path": target_path,
    }


def _build_risks(quote: dict[str, Any], cost: dict[str, Any], project: dict[str, Any], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    candidates = [
        _risk(
            key="quote_failed_or_timeout",
            severity="warning",
            title="报价失败或超时",
            count=_count(quote.get("failed_count")) + _count(quote.get("timeout_count")),
            action="进入报价运营查看失败任务",
            target_path="/admin/dashboard",
        ),
        _risk(
            key="quote_draft_stale",
            severity="warning",
            title="预审草稿长时间未处理",
            count=_count(quote.get("stale_draft_count")),
            action="进入旧报价工作台处理草稿",
            target_path="/index.html",
        ),
        _risk(
            key="cost_draft_pending",
            severity="warning",
            title="成本库 draft 待审核",
            count=_count(cost.get("draft_count")),
            action="进入成本主库审核 draft",
            target_path="/admin/cost-db",
        ),
        _risk(
            key="no_cost_draft_pending",
            severity="warning",
            title="无底价沉淀 draft 待审核",
            count=_count(cost.get("no_cost_draft_count")),
            action="进入成本主库筛选 AI 建议来源 draft",
            target_path="/admin/cost-db",
        ),
        _risk(
            key="cost_rag_not_synced",
            severity="warning",
            title="成本库 RAG 同步可能滞后",
            count=1 if cost.get("rag_needs_sync") or cost.get("rag_is_stale") else 0,
            action="进入成本库同步状态查看",
            target_path="/admin/cost-db",
        ),
        _risk(
            key="project_blocked_tasks",
            severity="warning",
            title="项目存在阻塞任务",
            count=_count(project.get("blocked_task_count")),
            action="进入项目进度查看阻塞任务",
            target_path="/admin/projects",
        ),
        _risk(
            key="project_missing_evidence",
            severity="warning",
            title="项目任务缺成果证据",
            count=_count(project.get("missing_evidence_task_count")),
            action="进入项目进度补充成果证据",
            target_path="/admin/projects",
        ),
        _risk(
            key="hard_gate_bypassed_missing_evidence",
            severity="warning",
            title="硬门禁放行后仍缺成果证据",
            count=_count(project.get("hard_gate_bypassed_missing_evidence_count")),
            action="进入项目进度补齐放行节点证据",
            target_path="/admin/projects",
        ),
    ]
    risks.extend([item for item in candidates if item])
    risks.extend(
        {
            "key": f"section_unavailable_{error['section']}",
            "severity": "warning",
            "title": f"{error['section']} 区块聚合失败",
            "count": 1,
            "action": "查看后端日志并复核数据表状态",
            "target_path": "/api/v1/admin/ops/dashboard",
        }
        for error in errors
    )
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(risks, key=lambda item: (severity_order.get(item["severity"], 9), item["key"]))


def build_business_lite_dashboard(db: Session, *, range_name: str = "last_30_days") -> dict[str, Any]:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    now = _now()
    start, end = _range_bounds(range_name, now=now)
    section_errors: list[dict[str, str]] = []

    quote = _safe_section(
        key="quote",
        builder=lambda: _build_quote_section(db, start=start, end=end, now=now),
        fallback=_fallback_quote(),
        errors=section_errors,
    )
    cost = _safe_section(
        key="cost",
        builder=lambda: _build_cost_section(db, start=start, end=end),
        fallback=_fallback_cost(),
        errors=section_errors,
    )
    project = _safe_section(
        key="project_progress",
        builder=lambda: _build_project_section(db, start=start, end=end, now=now),
        fallback=_fallback_project(),
        errors=section_errors,
    )
    system = _safe_section(
        key="system",
        builder=lambda: _build_system_section(db),
        fallback=_fallback_system(),
        errors=section_errors,
    )
    risks = _build_risks(quote, cost, project, section_errors)
    overall_status = "degraded" if section_errors else ("warning" if any(item["severity"] == "warning" for item in risks) else "ok")

    return {
        "timezone": "Asia/Shanghai",
        "range": range_name,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "generated_at": now.isoformat(),
        "environment": {
            "database_head": system.get("database_head"),
            "app_env": system.get("app_env"),
            "mode": system.get("mode"),
            "overall_status": overall_status,
        },
        "quote": quote,
        "cost": cost,
        "project_progress": project,
        "system_health": system,
        "risks": risks,
        "links": [
            {"key": "dashboard", "label": "效率驾驶舱", "path": "/admin/dashboard"},
            {
                "key": "quote_workspace",
                "label": "报价工作台" if settings.feature_unified_quotes else "新建报价",
                "path": "/quote/new",
            },
            {"key": "cost_db", "label": "企业定额主库", "path": "/admin/cost-db"},
            {"key": "project_progress", "label": "项目进度", "path": "/admin/projects"},
            {"key": "ops_dashboard", "label": "运维接口", "path": "/api/v1/admin/ops/dashboard"},
        ],
        "section_errors": section_errors,
        "empty_state": (
            _count(quote.get("task_count"))
            + _count(cost.get("active_count"))
            + _count(cost.get("draft_count"))
            + _count(project.get("project_count"))
            == 0
        ),
    }
