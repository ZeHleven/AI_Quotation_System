"""Isolated localhost-only shell for the Phase 4 engineering MVP-0 UI.

This module intentionally does not import the production application, database
configuration, runtime services, workers, or model/tool adapters.  It exists so
the engineering visualization can be reviewed before a dedicated local Agent
database is provisioned.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


_APP_DIR = Path(__file__).resolve().parent
_MIDDLE_OFFICE_DIR = _APP_DIR.parent
_REPOSITORY_DIR = _MIDDLE_OFFICE_DIR.parent
_VITE_DIST_DIR = _REPOSITORY_DIR / "ai-web" / "dist"
_VITE_ASSETS_DIR = _VITE_DIST_DIR / "assets"
_STATIC_DIR = _REPOSITORY_DIR / "static"


def _require_local_bind() -> None:
    bind_host = os.getenv("BID_MVP0_PREVIEW_BIND_HOST", "127.0.0.1").strip()
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MVP-0 preview may only bind to localhost")
    if not (_VITE_DIST_DIR / "index.html").is_file():
        raise RuntimeError("ai-web/dist is missing; build the Vite frontend first")


_require_local_bind()

app = FastAPI(
    title="Bid Assessment Runtime Lab — isolated MVP-0 preview",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(_VITE_ASSETS_DIR)), name="assets")


def _request_id(request: Request) -> str:
    return str(request.headers.get("X-Request-ID") or "mvp0-local-preview")[:80]


def _ok(request: Request, data: object) -> JSONResponse:
    return JSONResponse(
        {
            "code": 200,
            "message": "ok",
            "data": data,
            "error": None,
            "request_id": _request_id(request),
        },
        headers={
            "Cache-Control": "no-store",
            "X-Bid-MVP0-Mode": "isolated-local-preview",
        },
    )


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok", "mode": "isolated-local-preview"}


@app.get("/api/v1/auth/me", include_in_schema=False)
def preview_actor(request: Request) -> JSONResponse:
    """Return a synthetic local actor; no credential or authority is implied."""
    return _ok(
        request,
        {
            "id": 0,
            "username": "mvp0-preview",
            "role": "system_admin",
            "roles": ["system_admin"],
            "must_change_password": False,
            "available_modules": [],
            "default_home_path": "/admin/bid-assessment-runtime-lab",
            "preview_only": True,
        },
    )


@app.get(
    "/api/v1/bid-assessment-runtime-lab/capabilities",
    include_in_schema=False,
)
def preview_capabilities(request: Request) -> JSONResponse:
    return _ok(
        request,
        {
            "enabled": False,
            "mode": "isolated_local_protocol_preview",
            "access_mode": "view-only",
            "execution_enabled": False,
            "write_enabled": False,
            "worker_enabled": False,
            "worker_running": False,
            "model_calls_enabled": False,
            "model_provider": "not_started",
            "retrieval_mode": "not_started",
            "mvp1_enabled": False,
            "assessment_intake_enabled": False,
            "preliminary_report_enabled": False,
            "schema": "bid.runtime.trace.v1",
            "live_updates": "disabled",
            "live_sse_enabled": False,
            "redaction": {
                "policy": "control_plane_metadata_only",
                "omitted": [
                    "prompt_body",
                    "context_body",
                    "model_action_body",
                    "tool_arguments",
                    "tool_result_body",
                    "chain_of_thought",
                ],
            },
            "required_local_flags": [],
            "optional_live_sse_flag": None,
            "isolation": {
                "database": "not_loaded",
                "worker": "not_started",
                "model_gateway": "not_started",
                "tool_gateway": "not_started",
                "external_network": "not_used",
            },
        },
    )


def _serve_index() -> FileResponse:
    return FileResponse(
        _VITE_DIST_DIR / "index.html",
        headers={
            "Cache-Control": "no-store",
            "X-Bid-MVP0-Mode": "isolated-local-preview",
        },
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/admin/bid-assessment-runtime-lab")


@app.get("/login", include_in_schema=False)
@app.get("/admin/bid-assessment-runtime-lab", include_in_schema=False)
def preview_page() -> FileResponse:
    return _serve_index()
