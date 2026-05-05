from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# 仅在 celery 模式下使用 Redis 存储（保证 Redis 可用）；local/disabled 模式使用进程内存存储
if settings.task_queue_mode == "celery":
    try:
        limiter = Limiter(key_func=get_remote_address, storage_uri=settings.celery_broker_url)
    except Exception:
        limiter = Limiter(key_func=get_remote_address)
else:
    limiter = Limiter(key_func=get_remote_address)
