from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import user as user_model  # noqa: F401
from app.models.cost_item import (
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    CostItem,
    CostItemHistory,
    CostRagSyncRun,
)
from app.models.enterprise_quota import (
    IMPORT_BATCH_STATUS_ACTIVATED,
    QUOTA_VERSION_STATUS_ACTIVE,
    QUOTA_VERSION_STATUS_ARCHIVED,
    QUOTA_VERSION_STATUS_DRAFT,
    CostImportBatch,
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
)
from app.models.quote_cost_evidence import QuoteCostEvidence
from app.models.quote_job import QuoteJob


ACTIVE_QUOTE_JOB_STATUSES = {"queued", "running"}


class EnterpriseQuotaActivationError(RuntimeError):
    pass


def build_enterprise_quota_activation_plan(
    db: Session,
    version_id: int,
    *,
    clear_old_cost_db: bool = True,
) -> dict[str, Any]:
    """Build a non-mutating activation plan for one draft enterprise quota version."""

    version = db.get(EnterpriseQuotaVersion, version_id)
    if version is None:
        raise EnterpriseQuotaActivationError(f"Enterprise quota version not found: {version_id}")

    row_counts = _quota_row_counts(db, version_id)
    summary = _json_loads(version.summary_json)
    batch = version.import_batch
    batch_summary = _json_loads(batch.summary_json) if batch else {}
    error_count = _to_int(summary.get("error_count"), _to_int(batch.error_count if batch else None, 0))
    warning_count = _to_int(summary.get("warning_count"), _to_int(batch.warning_count if batch else None, 0))

    active_versions = _active_version_snapshots(db, exclude_version_id=None)
    active_quote_jobs = _active_quote_job_snapshots(db)
    old_cost_counts = _old_cost_counts(db)
    evidence_count = (
        db.query(func.count(QuoteCostEvidence.id))
        .filter(QuoteCostEvidence.cost_item_id.isnot(None))
        .scalar()
        or 0
    )
    rag_sync_count = db.query(func.count(CostRagSyncRun.id)).scalar() or 0

    checks: list[dict[str, Any]] = []
    _add_check(
        checks,
        code="target_is_draft",
        severity="blocker",
        passed=version.status == QUOTA_VERSION_STATUS_DRAFT,
        message=f"目标版本状态必须是 draft，当前为 {version.status}。",
    )
    _add_check(
        checks,
        code="target_not_already_active",
        severity="blocker",
        passed=not bool(version.is_active),
        message="目标版本当前不能已经是 active。",
    )
    _add_check(
        checks,
        code="phase0_has_no_errors",
        severity="blocker",
        passed=error_count == 0,
        message=f"Phase 0 error_count 必须为 0，当前为 {error_count}。",
    )
    _add_check(
        checks,
        code="quota_rows_complete",
        severity="blocker",
        passed=row_counts["items"] > 0 and row_counts["components"] > 0 and row_counts["resources"] > 0,
        message=(
            "目标版本必须包含主项、组成明细和资源价格；"
            f"当前 items={row_counts['items']} components={row_counts['components']} resources={row_counts['resources']}。"
        ),
    )
    _add_check(
        checks,
        code="no_active_quote_jobs",
        severity="blocker",
        passed=not active_quote_jobs["count"],
        message=f"激活前不能存在 queued/running 报价任务，当前为 {active_quote_jobs['count']} 个。",
    )
    _add_check(
        checks,
        code="clear_old_cost_db_requested",
        severity="warning",
        passed=bool(clear_old_cost_db),
        message="本阶段目标包含清空旧成本库；如未启用 clear_old_cost_db，将不会删除旧 cost_items。",
    )
    _add_check(
        checks,
        code="phase0_warnings_acknowledged",
        severity="warning",
        passed=warning_count == 0,
        message=f"Phase 0 warning_count={warning_count}，提交激活时需要显式确认接受这些警告。",
    )
    _add_check(
        checks,
        code="old_cost_db_will_be_cleared",
        severity="warning",
        passed=old_cost_counts["items_total"] == 0,
        message=f"旧 cost_items 将被清空，当前共有 {old_cost_counts['items_total']} 条成本条目。",
    )
    _add_check(
        checks,
        code="quote_cost_evidence_preserved",
        severity="warning",
        passed=evidence_count == 0,
        message=f"报价成本证据将保留，其中 {evidence_count} 条记录含旧 cost_item_id 快照引用。",
    )
    _add_check(
        checks,
        code="rag_sync_history_preserved",
        severity="warning",
        passed=rag_sync_count == 0,
        message=f"旧成本库 RAG 同步记录将保留用于审计，当前共有 {rag_sync_count} 条。",
    )

    blockers = [check for check in checks if check["severity"] == "blocker" and not check["passed"]]
    warnings = [check for check in checks if check["severity"] == "warning" and not check["passed"]]

    return {
        "ok": not blockers,
        "can_commit": not blockers,
        "needs_acknowledge_warnings": bool(warnings),
        "confirmation_code": _confirmation_code(version.version_code),
        "target_version": _version_snapshot(version),
        "import_batch": _batch_snapshot(batch),
        "phase0": {
            "error_count": error_count,
            "warning_count": warning_count,
            "summary": summary or batch_summary,
        },
        "row_counts": row_counts,
        "old_cost_db": {
            **old_cost_counts,
            "history_total": db.query(func.count(CostItemHistory.id)).scalar() or 0,
            "quote_cost_evidence_with_cost_item_id": evidence_count,
            "rag_sync_run_total": rag_sync_count,
            "clear_requested": bool(clear_old_cost_db),
            "clear_tables": ["cost_item_history", "cost_items"] if clear_old_cost_db else [],
            "preserve_tables": [
                "quote_cost_evidence",
                "quote_history",
                "quote_feedback",
                "cost_access_audit_logs",
                "cost_rag_sync_runs",
            ],
        },
        "existing_active_versions": active_versions,
        "active_quote_jobs": active_quote_jobs,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "actions": _planned_actions(clear_old_cost_db=clear_old_cost_db, active_version_count=len(active_versions)),
    }


def create_old_cost_database_backup(
    db: Session,
    output_dir: str | Path,
    *,
    version_id: int,
    reason: str | None = None,
) -> dict[str, Any]:
    """Write a JSON backup of the old cost master tables before activation."""

    backup_dir = Path(output_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    generated_at = _utcnow()
    file_name = f"old_cost_db_before_enterprise_quota_v{version_id}_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    backup_path = backup_dir / file_name

    cost_items = db.query(CostItem).order_by(CostItem.id).all()
    histories = db.query(CostItemHistory).order_by(CostItemHistory.id).all()
    payload = {
        "backup_kind": "old_cost_db_before_enterprise_quota_activation",
        "generated_at": generated_at.isoformat(),
        "target_version_id": version_id,
        "reason": reason,
        "counts": {
            "cost_items": len(cost_items),
            "cost_item_history": len(histories),
        },
        "tables": {
            "cost_items": [_model_to_dict(row) for row in cost_items],
            "cost_item_history": [_model_to_dict(row) for row in histories],
        },
    }
    content = _json_dumps(payload, pretty=True).encode("utf-8")
    backup_path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()

    return {
        "path": str(backup_path),
        "sha256": sha256,
        "generated_at": generated_at.isoformat(),
        "counts": payload["counts"],
    }


def run_enterprise_quota_activation(
    db: Session,
    version_id: int,
    *,
    clear_old_cost_db: bool,
    backup_dir: str | Path | None = None,
    confirm_code: str | None = None,
    acknowledge_warnings: bool = False,
    actor_id: int | None = None,
    reason: str | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Apply activation mutations in the current transaction.

    The caller owns commit/rollback. In dry-run mode this function still flushes
    the intended mutations so database constraints are exercised, then the CLI
    rolls the transaction back.
    """

    plan = build_enterprise_quota_activation_plan(db, version_id, clear_old_cost_db=clear_old_cost_db)
    if plan["blockers"]:
        codes = ", ".join(check["code"] for check in plan["blockers"])
        raise EnterpriseQuotaActivationError(f"Activation blocked by safety checks: {codes}")

    if commit and confirm_code != plan["confirmation_code"]:
        raise EnterpriseQuotaActivationError(
            "Confirmation code mismatch. "
            f"Expected {plan['confirmation_code']!r}; pass it with --confirm-code before using --commit."
        )
    if commit and plan["needs_acknowledge_warnings"] and not acknowledge_warnings:
        warning_codes = ", ".join(check["code"] for check in plan["warnings"])
        raise EnterpriseQuotaActivationError(
            "Activation has non-blocking warnings that must be acknowledged before commit: "
            f"{warning_codes}"
        )
    if commit and clear_old_cost_db and backup_dir is None:
        raise EnterpriseQuotaActivationError("A backup directory is required before committing old cost DB cleanup.")

    backup = None
    if clear_old_cost_db and backup_dir is not None:
        backup = create_old_cost_database_backup(db, backup_dir, version_id=version_id, reason=reason)

    now = _utcnow()
    deleted_history_count = 0
    deleted_cost_item_count = 0
    archived_versions: list[dict[str, Any]] = []
    if clear_old_cost_db:
        deleted_history_count = db.query(CostItemHistory).delete(synchronize_session=False)
        deleted_cost_item_count = db.query(CostItem).delete(synchronize_session=False)

    active_versions = (
        db.query(EnterpriseQuotaVersion)
        .filter(
            EnterpriseQuotaVersion.is_active.is_(True),
            EnterpriseQuotaVersion.id != version_id,
        )
        .all()
    )
    for active_version in active_versions:
        archived_versions.append(_version_snapshot(active_version))
        active_version.status = QUOTA_VERSION_STATUS_ARCHIVED
        active_version.is_active = False
        active_version.archived_at = now
        active_version.archived_by = actor_id

    version = db.get(EnterpriseQuotaVersion, version_id)
    if version is None:
        raise EnterpriseQuotaActivationError(f"Enterprise quota version not found during activation: {version_id}")
    version.status = QUOTA_VERSION_STATUS_ACTIVE
    version.is_active = True
    version.activated_at = now
    version.activated_by = actor_id
    if reason:
        version.notes = _append_note(version.notes, f"Phase 4 activation: {reason}")
    elif not version.notes:
        version.notes = "Activated by Phase 4 controlled activation flow."

    if version.import_batch is not None:
        version.import_batch.status = IMPORT_BATCH_STATUS_ACTIVATED

    db.flush()

    return {
        "ok": True,
        "dry_run": not commit,
        "target_version": _version_snapshot(version),
        "backup": backup,
        "deleted": {
            "cost_item_history": deleted_history_count,
            "cost_items": deleted_cost_item_count,
        },
        "archived_previous_active_versions": archived_versions,
        "warnings_acknowledged": bool(acknowledge_warnings),
        "confirmation_code": plan["confirmation_code"],
        "plan": plan,
        "message": (
            "Activation mutations flushed; caller must commit."
            if commit
            else "Dry run mutations flushed; caller should roll back."
        ),
    }


def _quota_row_counts(db: Session, version_id: int) -> dict[str, int]:
    return {
        "sections": db.query(func.count(EnterpriseQuotaSection.id)).filter(EnterpriseQuotaSection.version_id == version_id).scalar() or 0,
        "items": db.query(func.count(EnterpriseQuotaItem.id)).filter(EnterpriseQuotaItem.version_id == version_id).scalar() or 0,
        "components": db.query(func.count(EnterpriseQuotaComponent.id)).filter(EnterpriseQuotaComponent.version_id == version_id).scalar() or 0,
        "resources": db.query(func.count(EnterpriseCostResource.id)).filter(EnterpriseCostResource.version_id == version_id).scalar() or 0,
    }


def _old_cost_counts(db: Session) -> dict[str, int]:
    status_rows = db.query(CostItem.status, func.count(CostItem.id)).group_by(CostItem.status).all()
    by_status = {status or "unknown": int(count or 0) for status, count in status_rows}
    return {
        "items_total": sum(by_status.values()),
        "items_active": by_status.get(COST_STATUS_ACTIVE, 0),
        "items_draft": by_status.get(COST_STATUS_DRAFT, 0),
        "items_archived": by_status.get(COST_STATUS_ARCHIVED, 0),
    }


def _active_version_snapshots(db: Session, *, exclude_version_id: int | None) -> list[dict[str, Any]]:
    query = db.query(EnterpriseQuotaVersion).filter(EnterpriseQuotaVersion.is_active.is_(True))
    if exclude_version_id is not None:
        query = query.filter(EnterpriseQuotaVersion.id != exclude_version_id)
    return [_version_snapshot(row) for row in query.order_by(EnterpriseQuotaVersion.id).all()]


def _active_quote_job_snapshots(db: Session) -> dict[str, Any]:
    query = db.query(QuoteJob).filter(QuoteJob.status.in_(sorted(ACTIVE_QUOTE_JOB_STATUSES)))
    count = query.count()
    samples = (
        query.order_by(QuoteJob.created_at.desc(), QuoteJob.id.desc())
        .limit(10)
        .all()
    )
    return {
        "count": count,
        "statuses": sorted(ACTIVE_QUOTE_JOB_STATUSES),
        "sample_jobs": [
            {
                "job_id": row.job_id,
                "status": row.status,
                "stage": row.stage,
                "username": row.username,
                "created_at": _format_value(row.created_at),
            }
            for row in samples
        ],
    }


def _version_snapshot(version: EnterpriseQuotaVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_code": version.version_code,
        "version_name": version.version_name,
        "status": version.status,
        "is_active": bool(version.is_active),
        "source_filename": version.source_filename,
        "source_file_sha256": version.source_file_sha256,
        "import_batch_id": version.import_batch_id,
        "created_by": version.created_by,
        "activated_by": version.activated_by,
        "activated_at": _format_value(version.activated_at),
        "archived_by": version.archived_by,
        "archived_at": _format_value(version.archived_at),
    }


def _batch_snapshot(batch: CostImportBatch | None) -> dict[str, Any] | None:
    if batch is None:
        return None
    return {
        "id": batch.id,
        "batch_uuid": batch.batch_uuid,
        "status": batch.status,
        "source_filename": batch.source_filename,
        "source_file_sha256": batch.source_file_sha256,
        "error_count": batch.error_count,
        "warning_count": batch.warning_count,
        "created_by": batch.created_by,
    }


def _planned_actions(*, clear_old_cost_db: bool, active_version_count: int) -> list[dict[str, Any]]:
    actions = [
        {
            "order": 1,
            "action": "backup_old_cost_db",
            "required_for_commit": bool(clear_old_cost_db),
            "description": "导出 cost_items 与 cost_item_history 的 JSON 备份。",
        },
        {
            "order": 2,
            "action": "clear_old_cost_db",
            "enabled": bool(clear_old_cost_db),
            "description": "先删除 cost_item_history，再删除 cost_items；保留报价证据和审计记录。",
        },
        {
            "order": 3,
            "action": "archive_existing_active_enterprise_quota_versions",
            "enabled": active_version_count > 0,
            "description": "将现有 active 企业定额版本改为 archived/is_active=false。",
        },
        {
            "order": 4,
            "action": "activate_target_enterprise_quota_version",
            "enabled": True,
            "description": "将目标 draft 改为 active/is_active=true，并更新导入批次为 activated。",
        },
    ]
    return actions


def _add_check(checks: list[dict[str, Any]], *, code: str, severity: str, passed: bool, message: str) -> None:
    checks.append(
        {
            "code": code,
            "severity": severity,
            "passed": bool(passed),
            "message": message,
        }
    )


def _confirmation_code(version_code: str) -> str:
    return f"ACTIVATE-{version_code.upper()}"


def _model_to_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _format_value(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _append_note(existing: str | None, line: str) -> str:
    if not existing:
        return line
    return f"{existing}\n{line}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_dumps(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        default=_format_value,
    )


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _format_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
