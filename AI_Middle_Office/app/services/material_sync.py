import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.material import Material

logger = logging.getLogger(__name__)
DEFAULT_UNIT = "\u9879"


def _format_datetime(value) -> Optional[str]:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


async def sync_materials_to_rag(db: Session, username: str) -> dict:
    """POST all materials to RAG /admin/reload. Never raises — returns a result dict."""
    try:
        items = db.query(Material).order_by(Material.id).all()
        data = [
            {
                "id": m.material_id,
                "item_name": m.item_name,
                "unit_price": m.unit_price or 0.0,
                "unit": m.unit or DEFAULT_UNIT,
                "notes": m.notes or "",
                "category": m.category,
                "spec": m.spec,
                "brand": m.brand,
                "supplier": m.supplier,
                "region": m.region,
                "source": m.source or "manual",
                "status": m.status or ("draft" if m.is_draft else "active"),
                "last_verified_at": _format_datetime(m.last_verified_at),
                "usage_count": int(m.usage_count or 0),
                "last_used_at": _format_datetime(m.last_used_at),
                "is_draft": bool(m.is_draft),
            }
            for m in items
        ]
        if not data:
            return {
                "success": False,
                "message": "知识库为空，无法同步",
                "eval_triggered": False,
                "eval_report_id": None,
                "error": "知识库为空，无法同步",
            }

        payload = {"materials": data, "secret": settings.reload_secret}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{settings.rag_service_url}/admin/reload", json=payload)

        if response.status_code == 200:
            eval_report_id: Optional[str] = None
            if settings.rag_eval_enabled:
                from app.core.database import SessionLocal
                from app.services.rag_evaluator import trigger_eval_background
                eval_report_id = trigger_eval_background(username, SessionLocal)
            return {
                "success": True,
                "message": response.json().get("message", "同步完成"),
                "eval_triggered": settings.rag_eval_enabled,
                "eval_report_id": eval_report_id,
                "error": None,
            }

        detail = response.json().get("detail", f"状态码 {response.status_code}")
        error_msg = f"RAG 服务返回错误: {detail}"
        logger.warning("material_sync_rag_error", extra={"status": response.status_code, "username": username})
        return {
            "success": False,
            "message": error_msg,
            "eval_triggered": False,
            "eval_report_id": None,
            "error": error_msg,
        }

    except httpx.TimeoutException:
        logger.warning("material_sync_timeout", extra={"username": username})
        return {
            "success": False,
            "message": "RAG 服务超时",
            "eval_triggered": False,
            "eval_report_id": None,
            "error": "RAG 服务超时，请检查 CentOS 容器状态",
        }
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("material_sync_exception", extra={"username": username})
        return {
            "success": False,
            "message": error_msg,
            "eval_triggered": False,
            "eval_report_id": None,
            "error": error_msg,
        }
