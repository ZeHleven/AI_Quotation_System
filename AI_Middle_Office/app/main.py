import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from app.core.config import settings

settings.apply_proxy_env()

from app.api.v1 import chat, auth
from app.core.database import engine, Base, get_db
from app.models import user, quote_history  # noqa: F401 — 触发 SQLAlchemy 建表
from app.core.security import verify_password
from app.core.logging import configure_logging, reset_trace_id, set_trace_id


configure_logging()
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

# 启动时迁移：添加 must_change_password 列，并为仍使用默认密码 123 的 admin 标记强制修改
def _run_startup_migrations():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0"))
            conn.commit()
        except Exception:
            pass
        try:
            row = conn.execute(text("SELECT hashed_password FROM users WHERE username='admin'")).first()
            if row and verify_password("123", row[0]):
                conn.execute(text("UPDATE users SET must_change_password=1 WHERE username='admin'"))
                conn.commit()
        except Exception:
            pass

_run_startup_migrations()

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["权限认证"])
app.include_router(chat.router, prefix="/api/v1", tags=["AI Core"])


@app.middleware("http")
async def trace_request(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    trace_token = set_trace_id(trace_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.exception(
            "request_failed",
            extra={"method": request.method, "path": request.url.path, "duration_ms": duration_ms},
        )
        reset_trace_id(trace_token)
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Trace-Id"] = trace_id
    logger.info(
        "request_finished",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    reset_trace_id(trace_token)
    return response


@app.get("/health/live", include_in_schema=False)
def health_live():
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@app.get("/health/ready", include_in_schema=False)
def health_ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "ok",
            "rag_service_url": settings.rag_service_url,
        }
    except Exception as exc:
        logger.exception("health_ready_failed")
        return {"status": "degraded", "database": "error", "detail": str(exc)}

# 前端 HTML 文件所在目录（Clear_test/）
_FRONTEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@app.get("/", include_in_schema=False)
def serve_root():
    return FileResponse(os.path.join(_FRONTEND_DIR, "app.html"))

@app.get("/app.html", include_in_schema=False)
def serve_app():
    return FileResponse(os.path.join(_FRONTEND_DIR, "app.html"))

@app.get("/index.html", include_in_schema=False)
def serve_index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))

@app.get("/admin.html", include_in_schema=False)
def serve_admin():
    return FileResponse(os.path.join(_FRONTEND_DIR, "admin.html"))
