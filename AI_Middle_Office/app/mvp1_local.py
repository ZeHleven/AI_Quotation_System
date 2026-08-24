"""Localhost-only read-only viewer for historical MVP-1 runs.

The production FastAPI app and its lifespan are intentionally not imported.
The retired deterministic worker is not mounted; all non-safe HTTP methods are
rejected until the replacement pure-Agent runtime has its own entry point.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api.v1 import (
    bid_assessment_events,
    bid_assessment_reports,
    bid_assessment_runtime_lab,
    bid_assessments,
)
from app.core.config import settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.bid_mvp1_local_lab import (
    LOCAL_LAB_USERNAME,
    local_access_mode,
    local_lab_version,
    local_model_mode,
    require_local_lab_boundary,
    validate_local_lab_read_only,
)


logger = logging.getLogger(__name__)
_APP_DIR = Path(__file__).resolve().parent
_MIDDLE_OFFICE_DIR = _APP_DIR.parent
_REPOSITORY_DIR = _MIDDLE_OFFICE_DIR.parent
_VITE_DIST_DIR = _REPOSITORY_DIR / "ai-web" / "dist"
_STATIC_DIR = _REPOSITORY_DIR / "static"
_SAFE_VIEW_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _runtime_mode() -> str:
    return f"isolated-local-{local_access_mode()}-{local_model_mode()}"


def _model_provider_label() -> str:
    return (
        "deepseek-v4-flash"
        if local_model_mode() == "deepseek-v4-flash"
        else "deterministic_test_provider"
    )


def _require_local_bind() -> None:
    require_local_lab_boundary()
    host = os.getenv("BID_MVP1_LOCAL_BIND_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MVP-1 local lab may only bind to localhost")
    if not (_VITE_DIST_DIR / "index.html").is_file():
        raise RuntimeError("ai-web/dist is missing; build the Vite frontend first")


_require_local_bind()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_local_lab_read_only()
    _app.state.bid_mvp1_worker_running = False
    yield


app = FastAPI(
    title="Bid Assessment MVP-1 isolated local lab",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.state.bid_mvp1_access_mode = local_access_mode()
app.state.bid_mvp1_authority_epoch = uuid.uuid4().hex
app.state.bid_mvp1_worker_enabled = False
app.state.bid_mvp1_worker_running = False
app.state.bid_mvp1_model_calls_enabled = False
app.state.bid_mvp1_model_provider = _model_provider_label()
app.state.bid_mvp1_model_profile_version = local_lab_version()
app.state.bid_mvp1_view_only_secret_isolated = bool(
    settings.bid_assessment_model_api_key.strip() in {"", "local-view-only-disabled"}
)
app.state.bid_mvp1_retrieval_mode = os.getenv(
    "BID_MVP1_LOCAL_RETRIEVAL_MODE", "legacy"
).strip().lower()
app.state.bid_mvp1_rq2_runtime_ready = (
    _MIDDLE_OFFICE_DIR / ".tmp" / "rq2-locked-runtime"
).is_dir()


@app.middleware("http")
async def local_boundary_headers(request: Request, call_next):
    request.state.trace_id = str(request.headers.get("X-Request-ID") or uuid.uuid4())[:80]
    if request.method.upper() not in _SAFE_VIEW_METHODS:
        response = JSONResponse(
            status_code=403,
            content={
                "code": 403,
                "message": "runtime lab is in view-only mode",
                "data": None,
                "error": {
                    "code": "BID_MVP1_VIEW_ONLY",
                    "retryable": False,
                },
                "request_id": str(request.state.trace_id),
            },
        )
    else:
        response = await call_next(request)
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    response.headers["X-Bid-MVP1-Mode"] = _runtime_mode()
    response.headers["X-Bid-MVP1-Access-Mode"] = local_access_mode()
    return response


def _local_user(db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.username == LOCAL_LAB_USERNAME).one()
    list(user.role_assignments)
    return user


app.dependency_overrides[get_current_user] = _local_user
app.include_router(bid_assessments.router, prefix="/api/v1", tags=["Bid Assessment v1"])
app.include_router(bid_assessment_events.router, prefix="/api/v1", tags=["Bid Events"])
app.include_router(bid_assessment_reports.router, prefix="/api/v1", tags=["Bid Reports"])
app.include_router(
    bid_assessment_runtime_lab.router,
    prefix="/api/v1",
    tags=["Bid Runtime Lab"],
)


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "mode": _runtime_mode(),
        "access_mode": local_access_mode(),
        "write_enabled": False,
        "worker_enabled": False,
        "worker_running": bool(app.state.bid_mvp1_worker_running),
        "database": "sqlite",
        "queue": "disabled",
        "model_provider": _model_provider_label(),
        "model_calls_enabled": False,
        "retrieval_mode": str(app.state.bid_mvp1_retrieval_mode),
        "external_network": "disabled_by_design",
    }


@app.get("/api/v1/auth/me", include_in_schema=False)
def local_actor(request: Request, user: User = Depends(_local_user)) -> JSONResponse:
    return JSONResponse(
        {
            "code": 200,
            "message": "ok",
            "data": {
                "id": int(user.id),
                "username": str(user.username),
                "role": "system_admin",
                "roles": ["system_admin", "admin"],
                "must_change_password": False,
                "available_modules": [],
                "default_home_path": "/admin/bid-assessment-runtime-lab",
                "local_lab": True,
                "local_access_mode": local_access_mode(),
            },
            "error": None,
            "request_id": str(request.state.trace_id),
        }
    )


if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(_VITE_DIST_DIR / "assets")), name="assets")


def _serve_index() -> FileResponse:
    return FileResponse(
        _VITE_DIST_DIR / "index.html",
        headers={
            "Cache-Control": "no-store",
            "X-Bid-MVP1-Mode": _runtime_mode(),
            "X-Bid-MVP1-Access-Mode": local_access_mode(),
        },
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/admin/bid-assessment-runtime-lab")


@app.get("/login", include_in_schema=False)
@app.get("/admin/bid-assessment-runtime-lab", include_in_schema=False)
def runtime_lab_page() -> FileResponse:
    return _serve_index()
