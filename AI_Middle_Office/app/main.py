from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from app.api.v1 import chat, auth
from app.core.database import engine, Base, get_db
from app.models import user, quote_history  # noqa: F401 — 触发 SQLAlchemy 建表
from app.core.security import verify_password

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

app = FastAPI(title="Enterprise AI Middle Office", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["权限认证"])
app.include_router(chat.router, prefix="/api/v1", tags=["AI Core"])

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