"""
运行方式（在 Anaconda Prompt 里，切换到 AI_Middle_Office 目录后执行）：
    python create_admin.py
"""
import sys
from sqlalchemy import text
from app.core.database import SessionLocal, engine, Base
from app.models.user import User
from app.core.security import get_password_hash

Base.metadata.create_all(bind=engine)

# 补列迁移（数据库可能在 FastAPI 启动前不存在该列）
with engine.connect() as _conn:
    try:
        _conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0"))
        _conn.commit()
    except Exception:
        pass

username = "admin"
password = "123"

db = SessionLocal()
try:
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        existing.role = "admin"
        existing.hashed_password = get_password_hash(password)
        existing.quota = 9999
        db.commit()
        print(f"[OK] 已将现有账号 '{username}' 更新为 admin 角色，密码重置为: {password}")
    else:
        new_user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role="admin",
            quota=9999,
            is_active=True
        )
        db.add(new_user)
        db.commit()
        print(f"[OK] admin 账号创建成功，密码: {password}")
finally:
    db.close()
