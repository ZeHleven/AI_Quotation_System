from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import user as user_model  # noqa: F401 - ensure users table is registered for sync-run FK
from app.models.cost_item import COST_STATUS_ACTIVE, CostItem, CostRagSyncRun


logger = logging.getLogger(__name__)
DEFAULT_UNIT = "项"
SYNC_SOURCE = "cost_items.active"
SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_SUCCESS = "success"
SYNC_STATUS_FAILED = "failed"


def _format_price(label: str, value: float | None) -> str | None:
    if value is None:
        return None
    return f"{label}: {round(float(value), 4)}"


def _format_text(label: str, value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return f"{label}: {text}" if text else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_utc_offset(db: Session) -> timedelta:
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name not in {"mysql", "mariadb"}:
        return timedelta(0)
    try:
        seconds = db.execute(text("SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())")).scalar()
    except Exception:
        logger.debug("cost_rag_sync_db_timezone_offset_failed", exc_info=True)
        return timedelta(0)
    return timedelta(seconds=int(seconds or 0))


def _as_database_local_utc(value: datetime | None, db_utc_offset: timedelta) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc) - db_utc_offset


def _cost_item_rag_notes(item: CostItem) -> str:
    parts = [
        "数据源: cost_items.active",
        _format_text("成本条目ID", item.id),
        _format_text("类别", item.category),
        _format_text("子类", item.subcategory),
        _format_text("规格/项目特征", item.spec),
        _format_text("价格类型", item.price_type),
        _format_text("生效日期", item.effective_date.isoformat() if item.effective_date else None),
        _format_price("主参考价", item.price),
        _format_price("对甲税前综合单价", item.client_tax_excluded_price),
        _format_price("对甲人工费", item.client_labor_price),
        _format_price("对甲主材费", item.client_main_material_price),
        _format_price("对甲辅材费", item.client_auxiliary_material_price),
        _format_price("对甲直接费小计", item.client_direct_fee),
        _format_price("对甲管理费利润", item.client_management_profit),
        _format_price("劳务发包综合单价", item.subcontract_composite_price),
        _format_price("劳务人工费", item.subcontract_labor_price),
        _format_price("劳务主材费", item.subcontract_main_material_price),
        _format_price("劳务辅材费", item.subcontract_auxiliary_material_price),
        _format_price("班组标底税前价", item.crew_benchmark_price),
        _format_text("备注", item.notes),
    ]
    return "\n".join(part for part in parts if part)


def cost_item_to_rag_material(item: CostItem) -> dict[str, Any]:
    return {
        "id": f"cost_item_{item.id}",
        "item_name": item.item_name,
        "unit_price": float(item.price or 0),
        "unit": item.unit or DEFAULT_UNIT,
        "notes": _cost_item_rag_notes(item),
        "is_draft": False,
    }


def active_cost_items_rag_payload(db: Session) -> list[dict[str, Any]]:
    items = (
        db.query(CostItem)
        .filter(CostItem.status == COST_STATUS_ACTIVE)
        .order_by(CostItem.updated_at.desc(), CostItem.id.desc())
        .all()
    )
    return [cost_item_to_rag_material(item) for item in items]


def serialize_cost_rag_sync_run(run: CostRagSyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "requested_count": int(run.requested_count or 0),
        "synced_count": int(run.synced_count or 0),
        "message": run.message,
        "error": run.error,
        "rag_service_url": run.rag_service_url,
        "http_status": run.http_status,
        "duration_ms": run.duration_ms,
        "triggered_by": run.triggered_by,
        "triggered_by_username": run.triggered_by_username,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def list_cost_rag_sync_runs(db: Session, *, page: int = 1, page_size: int = 20) -> tuple[list[CostRagSyncRun], int]:
    query = db.query(CostRagSyncRun)
    total = query.count()
    runs = (
        query.order_by(CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return runs, total


def cost_rag_sync_status_summary(db: Session) -> dict[str, Any]:
    active_count = db.query(func.count(CostItem.id)).filter(CostItem.status == COST_STATUS_ACTIVE).scalar() or 0
    latest_active_updated_at = (
        db.query(func.max(CostItem.updated_at)).filter(CostItem.status == COST_STATUS_ACTIVE).scalar()
    )
    latest_successful_run = (
        db.query(CostRagSyncRun)
        .filter(CostRagSyncRun.source == SYNC_SOURCE, CostRagSyncRun.status == SYNC_STATUS_SUCCESS)
        .order_by(CostRagSyncRun.finished_at.desc(), CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
        .first()
    )
    latest_run = (
        db.query(CostRagSyncRun)
        .filter(CostRagSyncRun.source == SYNC_SOURCE)
        .order_by(CostRagSyncRun.started_at.desc(), CostRagSyncRun.id.desc())
        .first()
    )

    active_count = int(active_count)
    db_utc_offset = _database_utc_offset(db)
    latest_active_at = _as_database_local_utc(latest_active_updated_at, db_utc_offset)
    latest_success_finished_at = _as_aware_utc(
        (latest_successful_run.finished_at or latest_successful_run.started_at) if latest_successful_run else None
    )
    latest_run_started_at = _as_aware_utc(latest_run.started_at if latest_run else None)
    latest_success_started_at = _as_aware_utc(latest_successful_run.started_at if latest_successful_run else None)

    if active_count == 0:
        status = "empty_active"
        label = "无 active 条目"
        message = "当前没有 active 成本条目，RAG 暂无可同步成本知识"
        needs_sync = False
        is_stale = False
    elif latest_successful_run is None:
        status = "failed" if latest_run and latest_run.status == SYNC_STATUS_FAILED else "never_synced"
        label = "同步失败" if status == "failed" else "未同步"
        message = "最近一次 RAG 同步失败" if status == "failed" else "active 成本库尚未成功同步到 RAG"
        needs_sync = True
        is_stale = True
    elif latest_run and latest_run.status == SYNC_STATUS_FAILED and (
        latest_success_started_at is None or (latest_run_started_at and latest_run_started_at > latest_success_started_at)
    ):
        status = "failed"
        label = "同步失败"
        message = "最近一次 RAG 同步失败，请处理后重新同步"
        needs_sync = True
        is_stale = True
    elif int(latest_successful_run.synced_count or 0) != active_count:
        status = "stale"
        label = "数量不一致"
        message = "active 成本条目数量与最近成功同步数量不一致，建议重新同步 RAG"
        needs_sync = True
        is_stale = True
    elif latest_active_at and latest_success_finished_at and latest_active_at > latest_success_finished_at:
        status = "stale"
        label = "有更新未同步"
        message = "active 成本库已更新，RAG 可能仍是旧知识"
        needs_sync = True
        is_stale = True
    else:
        status = "synced"
        label = "已同步"
        message = "active 成本库已同步至 RAG"
        needs_sync = False
        is_stale = False

    return {
        "status": status,
        "status_label": label,
        "message": message,
        "source": SYNC_SOURCE,
        "active_count": active_count,
        "latest_active_updated_at": latest_active_updated_at.isoformat() if latest_active_updated_at else None,
        "latest_active_updated_at_utc": latest_active_at.isoformat() if latest_active_at else None,
        "latest_successful_run": serialize_cost_rag_sync_run(latest_successful_run) if latest_successful_run else None,
        "latest_run": serialize_cost_rag_sync_run(latest_run) if latest_run else None,
        "needs_sync": needs_sync,
        "is_stale": is_stale,
    }


def _create_sync_run(db: Session, *, username: str, user_id: int | None, requested_count: int) -> CostRagSyncRun:
    run = CostRagSyncRun(
        source=SYNC_SOURCE,
        status=SYNC_STATUS_RUNNING,
        requested_count=requested_count,
        synced_count=0,
        rag_service_url=settings.rag_service_url,
        triggered_by=user_id,
        triggered_by_username=username,
        started_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_sync_run(
    db: Session,
    run: CostRagSyncRun,
    *,
    status: str,
    synced_count: int,
    message: str,
    started_monotonic: float,
    error: str | None = None,
    http_status: int | None = None,
) -> None:
    run.status = status
    run.synced_count = synced_count
    run.message = message
    run.error = error
    run.http_status = http_status
    run.finished_at = _utcnow()
    run.duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    db.add(run)
    db.commit()
    db.refresh(run)


async def sync_active_cost_items_to_rag(db: Session, username: str, user_id: int | None = None) -> dict[str, Any]:
    data = active_cost_items_rag_payload(db)
    started_monotonic = time.monotonic()
    run = _create_sync_run(db, username=username, user_id=user_id, requested_count=len(data))

    if not data:
        message = "没有 active 成本条目，无法同步 RAG"
        _finish_sync_run(
            db,
            run,
            status=SYNC_STATUS_FAILED,
            synced_count=0,
            message=message,
            error="NO_ACTIVE_COST_ITEMS",
            started_monotonic=started_monotonic,
        )
        return {
            "success": False,
            "message": message,
            "synced_count": 0,
            "source": SYNC_SOURCE,
            "error": "NO_ACTIVE_COST_ITEMS",
            "run": serialize_cost_rag_sync_run(run),
        }

    payload = {"materials": data, "secret": settings.reload_secret}
    try:
        async with httpx.AsyncClient(timeout=settings.rag_reload_timeout_seconds) as client:
            response = await client.post(f"{settings.rag_service_url}/admin/reload", json=payload)

        if response.status_code == 200:
            message = response.json().get("message", "active 成本条目已同步至 RAG")
            _finish_sync_run(
                db,
                run,
                status=SYNC_STATUS_SUCCESS,
                synced_count=len(data),
                message=message,
                http_status=response.status_code,
                started_monotonic=started_monotonic,
            )
            return {
                "success": True,
                "message": message,
                "synced_count": len(data),
                "source": SYNC_SOURCE,
                "error": None,
                "run": serialize_cost_rag_sync_run(run),
            }

        try:
            detail = response.json().get("detail", f"状态码 {response.status_code}")
        except Exception:
            detail = f"状态码 {response.status_code}"
        error_msg = f"RAG 服务返回错误: {detail}"
        logger.warning("cost_rag_sync_error", extra={"status": response.status_code, "username": username})
        _finish_sync_run(
            db,
            run,
            status=SYNC_STATUS_FAILED,
            synced_count=0,
            message=error_msg,
            error=error_msg,
            http_status=response.status_code,
            started_monotonic=started_monotonic,
        )
        return {
            "success": False,
            "message": error_msg,
            "synced_count": 0,
            "source": SYNC_SOURCE,
            "error": error_msg,
            "run": serialize_cost_rag_sync_run(run),
        }
    except httpx.TimeoutException:
        logger.warning("cost_rag_sync_timeout", extra={"username": username})
        message = "RAG 服务超时"
        error = "RAG 服务超时，请检查 CentOS 容器状态"
        _finish_sync_run(
            db,
            run,
            status=SYNC_STATUS_FAILED,
            synced_count=0,
            message=message,
            error=error,
            started_monotonic=started_monotonic,
        )
        return {
            "success": False,
            "message": message,
            "synced_count": 0,
            "source": SYNC_SOURCE,
            "error": error,
            "run": serialize_cost_rag_sync_run(run),
        }
    except Exception as exc:
        logger.exception("cost_rag_sync_exception", extra={"username": username})
        error = str(exc)
        _finish_sync_run(
            db,
            run,
            status=SYNC_STATUS_FAILED,
            synced_count=0,
            message=error,
            error=error,
            started_monotonic=started_monotonic,
        )
        return {
            "success": False,
            "message": error,
            "synced_count": 0,
            "source": SYNC_SOURCE,
            "error": error,
            "run": serialize_cost_rag_sync_run(run),
        }
