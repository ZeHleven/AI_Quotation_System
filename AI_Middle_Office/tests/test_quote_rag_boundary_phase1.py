from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

QUOTE_PRICING_PATH = (
    "app/api/v1/quote.py",
    "app/services/quote_job_runner.py",
    "app/services/quote_cost_context.py",
    "app/services/quote_cost_matching.py",
    "app/services/enterprise_quota_cost_reference.py",
)

DIRECT_RAG_OR_MILVUS_DEPENDENCIES = (
    "settings.rag_service_url",
    "/api/v1/retrieve",
    "/admin/reload",
    "from app.services.rag_evaluator",
    "from app.services.cost_rag_sync",
    "from app.services.material_sync",
    "import pymilvus",
    "from pymilvus",
)

OBSOLETE_QUOTE_RAG_LABELS = (
    "RAG & Agent",
    "穿透企业知识库",
)


@pytest.mark.parametrize("relative_path", QUOTE_PRICING_PATH)
def test_quote_pricing_path_has_no_direct_rag_or_milvus_dependency(relative_path: str):
    """Protect the current direct boundary; n8n/Dify internals are audited separately."""
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    found = [token for token in DIRECT_RAG_OR_MILVUS_DEPENDENCIES if token in source]

    assert found == [], f"{relative_path} introduced direct RAG/Milvus dependencies: {found}"


@pytest.mark.parametrize(
    "relative_path",
    (
        "app/api/v1/quote.py",
        "app/services/quote_job_runner.py",
    ),
)
def test_quote_runtime_does_not_present_rag_as_pricing_source(relative_path: str):
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    found = [token for token in OBSOLETE_QUOTE_RAG_LABELS if token in source]

    assert found == [], f"{relative_path} still presents RAG as a quote pricing source: {found}"
