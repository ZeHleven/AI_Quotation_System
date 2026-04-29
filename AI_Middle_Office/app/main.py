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

from app.api.v1 import auth, chat, files, model_gateway, ops, quote_jobs
from app.core.database import engine, Base, get_db
from app.models import user, quote_history, quote_job, model_call_log  # noqa: F401 — 触发 SQLAlchemy 建表
from app.models import file_object  # noqa: F401 — 触发文件对象表建表
from app.core.logging import configure_logging, reset_trace_id, set_trace_id
from app.core.security import verify_password
from app.services.queue_health import check_task_queue


configure_logging()
logger = logging.getLogger(__name__)

def _wait_for_database_ready():
    deadline = time.monotonic() + max(settings.database_startup_wait_seconds, 0)
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("database_ready_after_retry", extra={"attempt": attempt})
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                logger.exception(
                    "database_startup_wait_failed",
                    extra={"attempt": attempt, "wait_seconds": settings.database_startup_wait_seconds},
                )
                raise
            logger.warning(
                "database_not_ready",
                extra={"attempt": attempt, "retry_in_seconds": settings.database_startup_retry_interval_seconds},
            )
            time.sleep(settings.database_startup_retry_interval_seconds)


_wait_for_database_ready()
if settings.auto_create_tables:
    Base.metadata.create_all(bind=engine)

# 兼容旧库的保守 schema 迁移。正式 schema 变更应使用 Alembic，见 DEPLOY_DB_MIGRATIONS.md。
def _run_schema_compat_migrations():
    if not settings.startup_compat_migrations:
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE quote_jobs ADD COLUMN file_object_id VARCHAR(36)"))
            conn.commit()
        except Exception:
            pass


def _mark_default_admin_password():
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT hashed_password FROM users WHERE username='admin'")).first()
            if row and verify_password("123", row[0]):
                conn.execute(text("UPDATE users SET must_change_password=1 WHERE username='admin'"))
                conn.commit()
        except Exception:
            pass


_run_schema_compat_migrations()
_mark_default_admin_password()

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
app.include_router(quote_jobs.router, prefix="/api/v1", tags=["Async Quote Jobs"])
app.include_router(model_gateway.router, prefix="/api/v1", tags=["Model Gateway"])
app.include_router(files.router, prefix="/api/v1", tags=["File Storage"])
app.include_router(ops.router, prefix="/api/v1", tags=["Operations"])


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
    result = {
        "status": "ready",
        "database": "unknown",
        "rag_service_url": settings.rag_service_url,
        "task_queue_mode": settings.task_queue_mode,
    }
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception as exc:
        logger.exception("health_ready_failed")
        result.update({"status": "degraded", "database": "error", "detail": str(exc)})

    queue_status = check_task_queue()
    result["task_queue"] = queue_status
    if not queue_status.get("ok"):
        result["status"] = "degraded"
    return result

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
