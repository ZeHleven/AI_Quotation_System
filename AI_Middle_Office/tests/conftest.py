import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DIR = Path(__file__).resolve().parent
TEST_DB = TEST_DIR / ".test_sql_app.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret"
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["RELOAD_SECRET"] = "test-reload-secret"
os.environ["ZHIPU_API_KEY"] = "test-zhipu-key"
os.environ["RAG_SERVICE_URL"] = "http://127.0.0.1:8001"
os.environ["N8N_WEBHOOK_URL_CALC"] = "http://127.0.0.1:5678/webhook/budget-calc"
os.environ["N8N_WEBHOOK_URL_PUSH"] = "http://127.0.0.1:5678/webhook/budget-push"
os.environ["MATERIALS_FILE"] = str(TEST_DIR / ".test_rag_materials.json")

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
