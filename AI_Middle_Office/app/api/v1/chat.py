"""Compatibility exports for the legacy chat module.

Route implementations now live in focused modules:
- app.api.v1.quote
- app.api.v1.materials
- app.api.v1.users
- app.api.v1.history
"""

from app.api.v1.materials import (
    DATA_FILE,
    MATERIAL_AUDIT_DIR,
    SNAPSHOT_ID_PATTERN,
    MaterialItem,
    create_material_snapshot,
    list_material_snapshots,
    load_data,
    load_material_snapshot,
    save_data,
)
from app.api.v1.quote import (
    N8N_WEBHOOK_URL_CALC,
    N8N_WEBHOOK_URL_PUSH,
    analyze_image_with_domestic_ai,
    confirm_and_push,
    process_chat,
    router,
)
from app.api.v1.users import QuotaUpdate
from app.core.config import settings
from app.dependencies import get_current_user, require_admin
from app.services.excel_service import build_excel_base64 as _build_excel_base64
from app.services.quote_helpers import (
    attach_quote_filename as _attach_quote_filename,
    build_quote_filename as _build_quote_filename,
    sign_payload as _sign_payload,
)


ZHIPU_API_KEY = settings.zhipu_api_key
WEBHOOK_SECRET = settings.webhook_secret
RAG_SERVICE_URL = settings.rag_service_url
RELOAD_SECRET = settings.reload_secret

__all__ = [
    "DATA_FILE",
    "MATERIAL_AUDIT_DIR",
    "SNAPSHOT_ID_PATTERN",
    "MaterialItem",
    "N8N_WEBHOOK_URL_CALC",
    "N8N_WEBHOOK_URL_PUSH",
    "QuotaUpdate",
    "RAG_SERVICE_URL",
    "RELOAD_SECRET",
    "WEBHOOK_SECRET",
    "ZHIPU_API_KEY",
    "_attach_quote_filename",
    "_build_excel_base64",
    "_build_quote_filename",
    "_sign_payload",
    "analyze_image_with_domestic_ai",
    "confirm_and_push",
    "create_material_snapshot",
    "get_current_user",
    "list_material_snapshots",
    "load_data",
    "load_material_snapshot",
    "process_chat",
    "require_admin",
    "router",
    "save_data",
]
