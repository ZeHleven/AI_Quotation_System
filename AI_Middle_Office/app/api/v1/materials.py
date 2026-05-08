import csv
import json
import logging
import re
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.responses import api_ok
from app.dependencies import require_admin
from app.models.material import Material, MaterialSnapshot
from app.models.user import User
from app.services.material_sync import sync_materials_to_rag


router = APIRouter()
logger = logging.getLogger(__name__)

LEGACY_DATA_FILE = settings.legacy_materials_file
# Backward-compatible export for legacy imports from app.api.v1.chat.
DATA_FILE = LEGACY_DATA_FILE
MATERIAL_AUDIT_DIR = LEGACY_DATA_FILE.parent / "materials_audit"
SNAPSHOT_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")

class MaterialItem(BaseModel):
    id: str
    item_name: str
    unit_price: float
    unit: str
    notes: str
    is_draft: Optional[bool] = False


def _load_legacy_file_data() -> list[dict]:
    if not LEGACY_DATA_FILE.exists():
        return []
    with LEGACY_DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _normalize_material(raw: dict, used_ids: set[str] | None = None) -> dict:
    used_ids = used_ids if used_ids is not None else set()
    material_id = str(raw.get("id") or raw.get("material_id") or f"material_{uuid.uuid4().hex[:8]}").strip()
    if not material_id:
        material_id = f"material_{uuid.uuid4().hex[:8]}"
    while material_id in used_ids:
        material_id = f"{material_id}_{uuid.uuid4().hex[:4]}"
    used_ids.add(material_id)

    try:
        unit_price = float(raw.get("unit_price", 0) or 0)
    except (TypeError, ValueError):
        unit_price = 0.0

    return {
        "material_id": material_id,
        "item_name": str(raw.get("item_name") or "").strip(),
        "unit_price": unit_price,
        "unit": str(raw.get("unit") or "项").strip() or "项",
        "notes": str(raw.get("notes") or ""),
        "is_draft": bool(raw.get("is_draft", False)),
    }


def _normalize_materials(data: list[dict]) -> list[dict]:
    used_ids: set[str] = set()
    normalized = []
    for raw in data:
        item = _normalize_material(raw, used_ids)
        if item["item_name"]:
            normalized.append(item)
    return normalized


def _serialize_material(material: Material) -> dict:
    return {
        "id": material.material_id,
        "item_name": material.item_name,
        "unit_price": material.unit_price or 0.0,
        "unit": material.unit or "项",
        "notes": material.notes or "",
        "is_draft": bool(material.is_draft),
    }


def _ensure_legacy_file_imported(db: Session) -> None:
    if db.query(Material.id).first() is not None:
        return

    legacy_data = _load_legacy_file_data()
    if not legacy_data:
        return

    for item in _normalize_materials(legacy_data):
        db.add(Material(**item))
    try:
        db.commit()
        logger.info("materials_legacy_json_imported", extra={"count": len(legacy_data), "path": str(LEGACY_DATA_FILE)})
    except Exception:
        db.rollback()
        logger.exception("materials_legacy_json_import_failed", extra={"path": str(LEGACY_DATA_FILE)})
        raise


def load_data(db: Session | None = None) -> list[dict]:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        _ensure_legacy_file_imported(db)
        materials = db.query(Material).order_by(Material.id).all()
        return [_serialize_material(item) for item in materials]
    finally:
        if owns_session:
            db.close()


def save_data(data: list[dict], db: Session | None = None) -> None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        db.query(Material).delete()
        for item in _normalize_materials(data):
            db.add(Material(**item))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def _snapshot_metadata(snapshot: MaterialSnapshot | dict) -> dict:
    if isinstance(snapshot, dict):
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "created_at": snapshot.get("created_at"),
            "username": snapshot.get("username"),
            "action": snapshot.get("action"),
            "reason": snapshot.get("reason"),
            "item_count": snapshot.get("item_count", 0),
        }

    return {
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at.isoformat(timespec="seconds") if snapshot.created_at else None,
        "username": snapshot.username,
        "action": snapshot.action,
        "reason": snapshot.reason,
        "item_count": snapshot.item_count,
    }


def _safe_snapshot_id(snapshot_id: str) -> str:
    if not SNAPSHOT_ID_PATTERN.match(snapshot_id or ""):
        raise HTTPException(status_code=400, detail="Invalid snapshot id")
    return snapshot_id


def create_material_snapshot(
    username: str,
    action: str,
    reason: str = "",
    data: Optional[list] = None,
    db: Session | None = None,
) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        snapshot_data = load_data(db) if data is None else data
        snapshot = MaterialSnapshot(
            snapshot_id=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(),
            username=username,
            action=action,
            reason=reason,
            item_count=len(snapshot_data),
            data_json=json.dumps(snapshot_data, ensure_ascii=False),
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return _snapshot_metadata(snapshot)
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def list_material_snapshots(limit: int = 30, db: Session | None = None) -> list[dict]:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        snapshots = (
            db.query(MaterialSnapshot)
            .order_by(MaterialSnapshot.created_at.desc(), MaterialSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        return [_snapshot_metadata(snapshot) for snapshot in snapshots]
    finally:
        if owns_session:
            db.close()


def load_material_snapshot(snapshot_id: str, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        snapshot = (
            db.query(MaterialSnapshot)
            .filter(MaterialSnapshot.snapshot_id == _safe_snapshot_id(snapshot_id))
            .first()
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        try:
            data = json.loads(snapshot.data_json)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Snapshot data is invalid") from exc
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="Snapshot data is invalid")
        return {**_snapshot_metadata(snapshot), "data": data}
    finally:
        if owns_session:
            db.close()


@router.get("/admin/materials", summary="获取所有物料清单")
async def get_materials(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return api_ok(load_data(db))


@router.post("/admin/materials", summary="保存/覆盖整个物料清单")
async def save_materials(
    items: list[MaterialItem],
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    before_data = load_data(db)
    snapshot = create_material_snapshot(
        username=current_user.username,
        action="save_before_overwrite",
        reason="Auto snapshot before saving materials",
        data=before_data,
        db=db,
    )
    data = [item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in items]
    save_data(data, db)
    return api_ok({"snapshot": snapshot}, message="保存成功", snapshot=snapshot)


@router.get("/admin/materials/audit", summary="查看知识库变更快照")
async def get_material_audit(
    limit: int = 30,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    return api_ok(list_material_snapshots(limit, db))


@router.post("/admin/materials/rollback/{snapshot_id}", summary="回滚知识库到指定快照")
async def rollback_materials(
    snapshot_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    snapshot = load_material_snapshot(snapshot_id, db)
    current_snapshot = create_material_snapshot(
        username=current_user.username,
        action="rollback_before_restore",
        reason=f"Auto snapshot before rollback to {snapshot_id}",
        db=db,
    )
    save_data(snapshot["data"], db)
    data = {
        "restored_snapshot": _snapshot_metadata(snapshot),
        "current_snapshot": current_snapshot,
    }
    return api_ok(data, message="Rollback completed", **data)


@router.post("/admin/upload_csv", summary="解析并提炼历史成交记录")
async def upload_csv_history(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """接收新的 CSV 成交记录，过滤重复项，提炼出异动/新项目"""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="目前仅支持 CSV 格式历史记录导入")

    content = await file.read()
    if content.startswith(b"PK"):
        raise HTTPException(
            status_code=400,
            detail="解析拦截：检测到您直接修改了 Excel 文件的后缀名。请在 Excel 中打开文件，点击【文件 -> 另存为 -> CSV (逗号分隔)】后再上传！",
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gbk")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="ignore")

    try:
        reader = csv.DictReader(StringIO(text, newline=""))
        rows = list(reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 格式异常或损坏: {str(e)}") from e

    existing_data = load_data(db)
    existing_items = {}
    for item in existing_data:
        try:
            existing_items[str(item["item_name"]).strip()] = float(item["unit_price"])
        except (KeyError, TypeError, ValueError):
            continue

    new_drafts = []
    for row in rows:
        item_name = row.get("施工项目") or row.get("项目名称") or row.get("item_name") or ""
        unit_price_str = row.get("AI核准单价(元)") or row.get("综合单价(元)") or row.get("单价") or row.get(
            "unit_price") or "0"
        unit = row.get("单位") or row.get("unit") or "项"
        notes = row.get("规格/工艺/材料说明") or row.get("备注说明") or row.get("备注") or row.get("notes") or ""

        item_name = str(item_name).strip()
        if not item_name or "汇总" in item_name or "结算" in item_name:
            continue

        try:
            price_val = float(unit_price_str)
        except ValueError:
            price_val = 0.0

        is_new = False
        if item_name not in existing_items:
            is_new = True
        else:
            old_price = existing_items[item_name]
            if old_price > 0 and abs(price_val - old_price) / old_price > 0.2:
                is_new = True
                item_name = f"{item_name} (历史异动待审)"

        if is_new and not any(d["item_name"] == item_name for d in new_drafts):
            new_drafts.append({
                "id": f"draft_{uuid.uuid4().hex[:8]}",
                "item_name": item_name,
                "unit_price": price_val,
                "unit": unit,
                "notes": notes,
                "is_draft": True,
            })

    return api_ok(new_drafts, message="解析成功")


@router.post("/admin/sync_milvus", summary="同步数据至 Milvus（委托 RAG 服务执行，零停机蓝绿切换）")
async def sync_to_milvus(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """将数据库物料数据 POST 至 CentOS RAG 服务的 /admin/reload，由其完成向量化和蓝绿切换。"""
    if not load_data(db):
        raise HTTPException(status_code=400, detail="本地知识库为空，无法同步")

    result = await sync_materials_to_rag(db, current_user.username)
    if result["success"]:
        resp_data = {
            "eval_triggered": result["eval_triggered"],
            "eval_report_id": result["eval_report_id"],
        }
        return api_ok(resp_data, message=result["message"], **resp_data)
    if "超时" in (result["error"] or ""):
        raise HTTPException(status_code=504, detail="RAG 服务超时，请检查 CentOS 容器状态")
    raise HTTPException(status_code=500, detail=result["error"])
