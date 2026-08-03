import copy
from pathlib import Path

import pytest

from app.services.n8n_quote_workflow_transform import (
    NoRagWorkflowTransformError,
    build_no_rag_quote_candidate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return {
        "id": "existing-id",
        "name": "【新】1-智能预审流",
        "active": True,
        "versionId": "existing-version",
        "nodes": [
            {
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "webhookId": "existing-webhook-id",
                "parameters": {"path": "budget-calc", "httpMethod": "POST"},
            },
            {"name": "HMAC Verify", "type": "n8n-nodes-base.code", "parameters": {}},
            {
                "name": "调用_RAG_微服务",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {
                    "url": "http://rag-service:8001/api/v1/retrieve",
                    "jsonBody": '{"query": "demo", "top_k": 5}',
                },
            },
            {
                "name": "HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "credentials": {"httpHeaderAuth": {"id": "credential-id", "name": "Dify"}},
                "parameters": {
                    "url": "http://dify/v1/workflows/run",
                    "jsonBody": """={{
  {
    "inputs": {
      "customer_requirement": $('Webhook').first().json.body.text.content,
      "strict_pricing_json": JSON.stringify($json.data || [])
    },
    "response_mode": "blocking"
  }
}}""",
                },
            },
            {"name": "LLMOps", "type": "n8n-nodes-base.code", "parameters": {}},
            {"name": "Code", "type": "n8n-nodes-base.code", "parameters": {}},
            {
                "name": "Respond to Webhook",
                "type": "n8n-nodes-base.respondToWebhook",
                "parameters": {},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "HMAC Verify", "type": "main", "index": 0}]]},
            "HMAC Verify": {
                "main": [[{"node": "调用_RAG_微服务", "type": "main", "index": 0}]]
            },
            "调用_RAG_微服务": {
                "main": [[{"node": "HTTP Request", "type": "main", "index": 0}]]
            },
            "HTTP Request": {"main": [[{"node": "LLMOps", "type": "main", "index": 0}]]},
            "LLMOps": {"main": [[{"node": "Code", "type": "main", "index": 0}]]},
            "Code": {
                "main": [[{"node": "Respond to Webhook", "type": "main", "index": 0}]]
            },
        },
        "settings": {"executionOrder": "v1"},
        "shared": [{"role": "workflow:owner"}],
        "tags": [{"id": "tag-id"}],
    }


def _node(candidate, name):
    return next(node for node in candidate["nodes"] if node["name"] == name)


def test_build_no_rag_candidate_removes_rag_and_preserves_contract():
    source = _workflow()

    candidate, report = build_no_rag_quote_candidate(source)

    assert source["active"] is True
    assert any(node["name"] == "调用_RAG_微服务" for node in source["nodes"])
    assert candidate["active"] is False
    assert candidate["name"] == "【新】1-智能预审流【no-RAG候选】"
    assert candidate["tags"] == []
    assert "id" not in candidate
    assert "versionId" not in candidate
    assert "shared" not in candidate
    assert all(node["name"] != "调用_RAG_微服务" for node in candidate["nodes"])
    assert _node(candidate, "Webhook")["parameters"]["path"] == "budget-calc-no-rag"
    assert "webhookId" not in _node(candidate, "Webhook")
    assert candidate["connections"]["HMAC Verify"]["main"][0] == [
        {"node": "HTTP Request", "type": "main", "index": 0}
    ]
    assert "调用_RAG_微服务" not in candidate["connections"]
    assert "JSON.stringify([])" in _node(candidate, "HTTP Request")["parameters"]["jsonBody"]
    assert "$json.data" not in _node(candidate, "HTTP Request")["parameters"]["jsonBody"]
    assert _node(candidate, "HTTP Request")["credentials"] == {
        "httpHeaderAuth": {"id": "credential-id", "name": "Dify"}
    }
    assert report.removed_rag_node == "调用_RAG_微服务"
    assert report.predecessor_nodes == ("HMAC Verify",)
    assert report.successor_nodes == ("HTTP Request",)
    assert report.node_count_before == 7
    assert report.node_count_after == 6


def test_build_no_rag_candidate_selects_workflow_from_api_wrapper():
    unrelated = copy.deepcopy(_workflow())
    unrelated["nodes"][0]["parameters"]["path"] = "budget-push"
    wrapper = {"data": [unrelated, _workflow()], "nextCursor": None}

    candidate, _ = build_no_rag_quote_candidate(wrapper)

    assert _node(candidate, "Webhook")["parameters"]["path"] == "budget-calc-no-rag"


def test_build_no_rag_candidate_materializes_active_version_export():
    source = _workflow()
    active_version = {
        "nodes": source.pop("nodes"),
        "connections": source.pop("connections"),
        "settings": source["settings"],
    }
    source["activeVersion"] = active_version

    candidate, _ = build_no_rag_quote_candidate(source)

    assert len(candidate["nodes"]) == 6
    assert candidate["settings"] == {"executionOrder": "v1"}


def test_build_no_rag_candidate_rejects_missing_rag_node():
    source = _workflow()
    source["nodes"] = [
        node for node in source["nodes"] if node["name"] != "调用_RAG_微服务"
    ]

    with pytest.raises(NoRagWorkflowTransformError, match="实际找到 0 个"):
        build_no_rag_quote_candidate(source)


def test_build_no_rag_candidate_rejects_multiple_rag_nodes():
    source = _workflow()
    second_rag = copy.deepcopy(_node(source, "调用_RAG_微服务"))
    second_rag["name"] = "调用_RAG_微服务2"
    source["nodes"].append(second_rag)

    with pytest.raises(NoRagWorkflowTransformError, match="实际找到 2 个"):
        build_no_rag_quote_candidate(source)


def test_build_no_rag_candidate_rejects_same_webhook_path():
    with pytest.raises(NoRagWorkflowTransformError, match="必须与现有 path 不同"):
        build_no_rag_quote_candidate(
            _workflow(),
            source_webhook_path="budget-calc",
            candidate_webhook_path="budget-calc",
        )


def test_default_quote_webhook_points_to_no_rag_candidate():
    config_source = (PROJECT_ROOT / "app/core/config.py").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    expected_url = "http://192.168.88.128:5678/webhook/budget-calc-no-rag"
    assert (
        f'_env("N8N_WEBHOOK_URL_CALC", "{expected_url}")'
        in config_source
    )
    assert f"N8N_WEBHOOK_URL_CALC={expected_url}" in env_example
