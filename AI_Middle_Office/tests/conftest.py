import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DIR = Path(__file__).resolve().parent
TEST_DB = TEST_DIR / ".test_sql_app.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["STARTUP_COMPAT_MIGRATIONS"] = "true"
os.environ["AUTO_RUN_DB_MIGRATIONS"] = "false"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret"
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["RELOAD_SECRET"] = "test-reload-secret"
os.environ["ZHIPU_API_KEY"] = "test-zhipu-key"
os.environ["RAG_SERVICE_URL"] = "http://127.0.0.1:8001"
os.environ["N8N_WEBHOOK_URL_CALC"] = "http://127.0.0.1:5678/webhook/budget-calc"
os.environ["N8N_WEBHOOK_URL_PUSH"] = "http://127.0.0.1:5678/webhook/budget-push"
os.environ["MATERIALS_FILE"] = str(TEST_DIR / ".test_rag_materials.json")
os.environ["RAG_EVAL_REPORT_DIR"] = str(TEST_DIR / ".test_rag_eval_reports")
os.environ["TASK_QUEUE_MODE"] = "disabled"
os.environ["CELERY_BROKER_URL"] = "redis://127.0.0.1:6379/0"
os.environ["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6379/1"
os.environ["QUEUE_HEALTH_TIMEOUT_SECONDS"] = "0.1"
os.environ["OPS_PROBE_TIMEOUT_SECONDS"] = "0.05"
os.environ["OPS_STUCK_JOB_MINUTES"] = "30"
os.environ["OPS_LOG_SCAN_LINES"] = "50"
os.environ["OPS_LOG_MAX_FILES"] = "2"
os.environ["MINIO_ENABLED"] = "false"
os.environ["MINIO_ENDPOINT"] = "127.0.0.1:9002"
os.environ["MINIO_BUCKET"] = "test-quote-files"
os.environ["RAG_EVAL_ENABLED"] = "false"
os.environ["LOGIN_RATE_LIMIT"] = "1000/5minutes"

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
