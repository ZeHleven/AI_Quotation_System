import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user, require_admin
from app.models.file_object import FileObject
from app.models.user import User
from app.services.file_storage import (
    StorageDisabledError,
    check_storage_health,
    generate_presigned_get_url,
    sanitize_purpose,
    store_file_bytes,
)


router = APIRouter()


def _format_dt(value) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _get_accessible_file(file_id: str, current_user: User, db: Session) -> FileObject:
    file_obj = db.query(FileObject).filter(FileObject.file_id == file_id).first()
    if not file_obj:
        raise HTTPException(status_code=404, detail="文件不存在")
    if current_user.role != "admin" and file_obj.username != current_user.username:
        raise HTTPException(status_code=404, detail="文件不存在")
    return file_obj


def _serialize_file(file_obj: FileObject) -> dict:
    return {
        "file_id": file_obj.file_id,
        "username": file_obj.username,
        "purpose": file_obj.purpose,
        "bucket": file_obj.bucket,
        "object_name": file_obj.object_name,
        "original_filename": file_obj.original_filename,
        "content_type": file_obj.content_type,
        "size_bytes": file_obj.size_bytes,
        "created_at": _format_dt(file_obj.created_at),
    }


@router.post("/files", status_code=status.HTTP_201_CREATED, summary="上传文件到 MinIO")
async def upload_file_to_storage(
    file: UploadFile = File(...),
    purpose: str = Form("general"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    purpose = sanitize_purpose(purpose)
    content = await file.read()
    max_bytes = settings.minio_max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.minio_max_upload_mb}MB 上限")

    try:
        stored = await asyncio.to_thread(
            store_file_bytes,
            content=content,
            original_filename=file.filename or "file",
            content_type=file.content_type,
            username=current_user.username,
            purpose=purpose,
        )
    except StorageDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MinIO 上传失败: {str(exc)}") from exc

    file_obj = FileObject(
        file_id=str(uuid.uuid4()),
        username=current_user.username,
        purpose=purpose,
        bucket=stored["bucket"],
        object_name=stored["object_name"],
        original_filename=file.filename or "file",
        content_type=stored["content_type"],
        size_bytes=stored["size_bytes"],
    )
    db.add(file_obj)
    db.commit()
    db.refresh(file_obj)

    data = _serialize_file(file_obj)
    url_data = await _build_download_url(file_obj)
    data.update(url_data)
    return api_ok(data)


@router.get("/files", summary="查询文件列表")
async def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    purpose: Optional[str] = None,
    username: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(FileObject)
    if current_user.role != "admin":
        query = query.filter(FileObject.username == current_user.username)
    elif username:
        query = query.filter(FileObject.username == username)
    if purpose:
        query = query.filter(FileObject.purpose == sanitize_purpose(purpose))

    total = query.count()
    files = (
        query.order_by(FileObject.created_at.desc(), FileObject.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [_serialize_file(file_obj) for file_obj in files],
        total=total,
        page=page,
        page_size=page_size,
    )


async def _build_download_url(file_obj: FileObject, expires_seconds: Optional[int] = None) -> dict:
    expires_seconds = expires_seconds or settings.minio_presigned_expire_seconds
    url = await asyncio.to_thread(generate_presigned_get_url, file_obj.object_name, expires_seconds, file_obj.bucket)
    expires_at = datetime.now() + timedelta(seconds=expires_seconds)
    return {
        "download_url": url,
        "expires_in_seconds": expires_seconds,
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/files/{file_id}/download_url", summary="生成临时下载链接")
async def get_file_download_url(
    file_id: str,
    expires_seconds: Optional[int] = Query(None, ge=60, le=604800),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file_obj = _get_accessible_file(file_id, current_user, db)
    try:
        return api_ok({**_serialize_file(file_obj), **await _build_download_url(file_obj, expires_seconds)})
    except StorageDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"生成临时下载链接失败: {str(exc)}") from exc


@router.get("/admin/files/storage/health", summary="查看 MinIO 文件存储状态")
async def get_file_storage_health(current_user: User = Depends(require_admin)):
    return api_ok(await asyncio.to_thread(check_storage_health))
