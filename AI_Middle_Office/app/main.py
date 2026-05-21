import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from app.core.config import settings
from app.core.rate_limit import limiter

settings.apply_proxy_env()

from app.api.v1 import (
    auth,
    business_ledger,
    client_inquiries,
    cost_items,
    dashboard,
    execution_tasks,
    files,
    history,
    knowledge_candidates,
    materials,
    meetings,
    model_gateway,
    ops,
    prompt_regression,
    quote,
    quote_feedback,
    quote_jobs,
    rag_eval,
    users,
)
from app.core.database import engine, Base, get_db
from app.models import material, user, quote_history, quote_job, model_call_log  # noqa: F401 — 触发 SQLAlchemy 建表
from app.models import file_object  # noqa: F401 — 触发文件对象表建表
from app.models import rag_eval_report  # noqa: F401 — 触发 rag_eval_reports 表建表
from app.models import quote_feedback as quote_feedback_model  # noqa: F401 — 触发报价反馈表建表
from app.models import prompt_regression as prompt_regression_model  # noqa: F401 — 触发 Prompt 回归表建表
from app.models import knowledge_candidate as knowledge_candidate_model  # noqa: F401 — 触发知识候选表建表
from app.models import client_inquiry as client_inquiry_model  # noqa: F401 — 触发客户咨询表建表
from app.models import client_inquiry_event as client_inquiry_event_model  # noqa: F401 — 触发商务台账事件表建表
from app.models import cost_item as cost_item_model  # noqa: F401 — 触发成本库表建表
from app.models import execution_task as execution_task_model  # noqa: F401 — 触发执行任务表建表
from app.models import meeting as meeting_model  # noqa: F401 — 触发会议纪要与草稿表建表
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


def _run_startup_database_tasks():
    _wait_for_database_ready()
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    _run_schema_compat_migrations()
    _mark_default_admin_password()


async def _alert_loop() -> None:
    from app.core.database import SessionLocal
    from app.services.ops_monitor import build_ops_dashboard, send_dingtalk_alerts

    while True:
        await asyncio.sleep(settings.alert_check_interval_seconds)
        try:
            def _check():
                db = SessionLocal()
                try:
                    dashboard = build_ops_dashboard(db)
                    send_dingtalk_alerts(dashboard.get("alerts", []))
                finally:
                    db.close()
            await asyncio.to_thread(_check)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("alert_loop_error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_run_startup_database_tasks)
    task = asyncio.create_task(_alert_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["权限认证"])
app.include_router(client_inquiries.router, prefix="/api/v1", tags=["Client Inquiries"])
app.include_router(business_ledger.router, prefix="/api/v1", tags=["Business Ledger"])
app.include_router(cost_items.router, prefix="/api/v1", tags=["Cost Items"])
app.include_router(quote.router, prefix="/api/v1", tags=["Quote"])
app.include_router(materials.router, prefix="/api/v1", tags=["Materials"])
app.include_router(users.router, prefix="/api/v1", tags=["Users"])
app.include_router(dashboard.router, prefix="/api/v1", tags=["Dashboard"])
app.include_router(execution_tasks.router, prefix="/api/v1", tags=["Execution Tasks"])
app.include_router(meetings.router, prefix="/api/v1", tags=["Meetings"])
app.include_router(history.router, prefix="/api/v1", tags=["History"])
app.include_router(quote_jobs.router, prefix="/api/v1", tags=["Async Quote Jobs"])
app.include_router(quote_feedback.router, prefix="/api/v1", tags=["Quote Feedback"])
app.include_router(prompt_regression.router, prefix="/api/v1", tags=["Prompt Regression"])
app.include_router(knowledge_candidates.router, prefix="/api/v1", tags=["Knowledge Candidates"])
app.include_router(model_gateway.router, prefix="/api/v1", tags=["Model Gateway"])
app.include_router(files.router, prefix="/api/v1", tags=["File Storage"])
app.include_router(ops.router, prefix="/api/v1", tags=["Operations"])
app.include_router(rag_eval.router, prefix="/api/v1", tags=["RAG Eval"])


@app.middleware("http")
async def trace_request(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
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
_STATIC_DIR = os.path.join(_FRONTEND_DIR, "static")
_VITE_DIST_DIR = os.path.join(_FRONTEND_DIR, "ai-web", "dist")
_VITE_ASSETS_DIR = os.path.join(_VITE_DIST_DIR, "assets")
_VITE_PUBLIC_DIR = os.path.join(_FRONTEND_DIR, "ai-web", "public")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
else:
    logger.warning("frontend_static_dir_missing", extra={"path": _STATIC_DIR})
if os.path.isdir(_VITE_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_VITE_ASSETS_DIR), name="vite-assets")

@app.get("/", include_in_schema=False)
def serve_root():
    if settings.feature_vite_frontend and os.path.isfile(os.path.join(_VITE_DIST_DIR, "index.html")):
        return RedirectResponse("/login")
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


def _serve_vite_index():
    vite_index = os.path.join(_VITE_DIST_DIR, "index.html")
    if os.path.isfile(vite_index):
        return FileResponse(vite_index)
    return RedirectResponse("/app.html")


@app.get("/login", include_in_schema=False)
def serve_login():
    return _serve_vite_index()


@app.get("/admin/permissions", include_in_schema=False)
def serve_permissions():
    return _serve_vite_index()


@app.get("/admin/users", include_in_schema=False)
def serve_vite_users():
    return _serve_vite_index()


@app.get("/admin/dashboard", include_in_schema=False)
def serve_vite_dashboard():
    if not settings.feature_vite_frontend:
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve_vite_index()


@app.get("/admin/execution", include_in_schema=False)
def serve_vite_execution():
    if not settings.feature_vite_frontend:
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve_vite_index()


@app.get("/admin/business", include_in_schema=False)
def serve_vite_business():
    if not settings.feature_vite_frontend:
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve_vite_index()


@app.get("/admin/business-ledger", include_in_schema=False)
def serve_vite_business_ledger():
    if not settings.feature_vite_frontend:
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve_vite_index()


@app.get("/admin/cost-db", include_in_schema=False)
def serve_vite_cost_db():
    if not settings.feature_vite_frontend:
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve_vite_index()


@app.get("/favicon.svg", include_in_schema=False)
def serve_vite_favicon():
    for base_dir in (_VITE_DIST_DIR, _VITE_PUBLIC_DIR):
        path = os.path.join(base_dir, "favicon.svg")
        if os.path.isfile(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/icons.svg", include_in_schema=False)
def serve_vite_icons():
    for base_dir in (_VITE_DIST_DIR, _VITE_PUBLIC_DIR):
        path = os.path.join(base_dir, "icons.svg")
        if os.path.isfile(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="Not Found")
