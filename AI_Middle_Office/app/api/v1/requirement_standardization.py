from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.user import User
from app.services.rbac import has_any_role
from app.services.requirement_standardizer import (
    RequirementStandardizationError,
    apply_manual_field_mappings,
    confirm_standardized_rows,
    standardize_requirement_excel_bytes,
)


router = APIRouter()


class RequirementRemapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: dict[str, Any]
    sheet_mappings: Any = Field(default_factory=list)


class RequirementConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[dict[str, Any]]


def _ensure_feature_enabled() -> None:
    if not settings.feature_requirement_standardization:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _require_requirement_access(current_user: User) -> None:
    if not has_any_role(current_user, {"admin", "system_admin", "quote_user"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PERMISSION_DENIED")


@router.post("/admin/requirement-standardization/preview", summary="需求单标准化预览")
async def preview_requirement_standardization(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    _ensure_feature_enabled()
    _require_requirement_access(current_user)
    content = await file.read()
    try:
        result = standardize_requirement_excel_bytes(content, filename=file.filename)
    except RequirementStandardizationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return api_ok(result)


@router.post("/admin/requirement-standardization/remap", summary="应用人工列映射")
async def remap_requirement_standardization(
    payload: RequirementRemapRequest,
    current_user: User = Depends(get_current_user),
):
    _ensure_feature_enabled()
    _require_requirement_access(current_user)
    return api_ok(apply_manual_field_mappings(payload.preview, payload.sheet_mappings))


@router.post("/admin/requirement-standardization/confirm", summary="确认标准化需求清单")
async def confirm_requirement_standardization(
    payload: RequirementConfirmRequest,
    current_user: User = Depends(get_current_user),
):
    _ensure_feature_enabled()
    _require_requirement_access(current_user)
    return api_ok(confirm_standardized_rows(payload.rows))
