from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import jwt
import pytest
from mcp.shared.memory import create_connected_server_and_client_session


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.contracts import (  # noqa: E402
    DocumentManifest,
    EvidenceLocator as AgentEvidenceLocator,
    EvidenceRef as AgentEvidenceRef,
)
from app.agents.bid_intake.mcp_adapter import McpTenderEvidencePort  # noqa: E402
from mcp_servers.tender_evidence.auth import (  # noqa: E402
    ALL_CAPABILITIES,
    ScopeTokenError,
    ScopedTokenCodec,
    ScopedTokenVerifier,
    TenderScope,
)
from mcp_servers.tender_evidence.contracts import (  # noqa: E402
    EvidenceRefInput,
    ResultStatus,
)
from mcp_servers.tender_evidence.local_repository import (  # noqa: E402
    LocalTenderEvidenceRepository,
)
from mcp_servers.tender_evidence.server import create_tender_evidence_mcp  # noqa: E402
from mcp_servers.tender_evidence.service import (  # noqa: E402
    TenderCapabilityDeniedError,
    TenderEvidenceService,
)


DATASET_PATH = (
    PROJECT_DIR
    / "mcp_servers"
    / "tender_evidence"
    / "fixtures"
    / "demo_cases.json"
)
TOKEN_SECRET = "phase-one-test-secret-must-be-at-least-32-characters"
ISSUER = "https://tender-auth.example.test"
AUDIENCE = "https://tender-mcp.example.test/mcp"


def _scope(
    case_id: str = "CASE-DEMO-001",
    *,
    run_id: str = "RUN-001",
    capabilities: frozenset[str] = ALL_CAPABILITIES,
) -> TenderScope:
    now = int(time.time())
    return TenderScope(
        case_id=case_id,
        assessment_id="ASSESSMENT-001",
        agent_run_id=run_id,
        subject="bid-intake-agent",
        allowed_tools=capabilities,
        issued_at=now,
        expires_at=now + 300,
        audience=AUDIENCE,
        issuer=ISSUER,
    )


def _service(scope: TenderScope | None = None) -> TenderEvidenceService:
    current_scope = scope or _scope()
    return TenderEvidenceService(
        LocalTenderEvidenceRepository(DATASET_PATH),
        scope_provider=lambda: current_scope,
    )


def test_scoped_token_round_trip_and_mcp_verifier() -> None:
    codec = ScopedTokenCodec(
        secret=TOKEN_SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    token = codec.issue(
        case_id="CASE-DEMO-001",
        assessment_id="ASSESSMENT-001",
        agent_run_id="RUN-001",
        subject="bid-intake-agent",
        allowed_tools=set(ALL_CAPABILITIES),
        ttl_seconds=300,
    )
    verified = codec.verify(token)
    assert verified.case_id == "CASE-DEMO-001"
    assert verified.allowed_tools == ALL_CAPABILITIES

    async def verify_access_token():
        return await ScopedTokenVerifier(codec).verify_token(token)

    access_token = anyio.run(verify_access_token)
    assert access_token is not None
    assert access_token.subject == "bid-intake-agent"
    assert access_token.claims
    assert access_token.claims["tender_scope"]["case_id"] == "CASE-DEMO-001"


def test_scoped_token_rejects_wrong_audience_and_expiry() -> None:
    codec = ScopedTokenCodec(
        secret=TOKEN_SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    token = codec.issue(
        case_id="CASE-DEMO-001",
        assessment_id="ASSESSMENT-001",
        agent_run_id="RUN-001",
        subject="bid-intake-agent",
        allowed_tools={"read_manifest"},
    )
    wrong_audience_codec = ScopedTokenCodec(
        secret=TOKEN_SECRET,
        issuer=ISSUER,
        audience="https://wrong-resource.example.test/mcp",
    )
    with pytest.raises(ScopeTokenError):
        wrong_audience_codec.verify(token)

    payload = jwt.decode(
        token,
        TOKEN_SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
    payload["exp"] = int(time.time()) - 1
    payload["tender_scope"]["expires_at"] = int(time.time()) - 1
    expired_token = jwt.encode(payload, TOKEN_SECRET, algorithm="HS256")
    with pytest.raises(ScopeTokenError):
        codec.verify(expired_token)


def test_service_enforces_capability_and_case_scope() -> None:
    search_only = _scope(capabilities=frozenset({"search_tender_evidence"}))
    service = _service(search_only)
    result = service.search_tender_evidence(query="控制价", top_k=20)
    assert result.status == ResultStatus.NO_RESULT
    assert "500万元" not in json.dumps(result.model_dump(), ensure_ascii=False)
    assert "EV-PRIVATE-001" not in json.dumps(
        result.model_dump(),
        ensure_ascii=False,
    )

    with pytest.raises(TenderCapabilityDeniedError):
        service.get_manifest()
    with pytest.raises(TenderCapabilityDeniedError):
        service.read_evidence_context(evidence_id="EV-DEMO-003")


def test_search_read_and_gate_trace_are_deterministic() -> None:
    service = _service()
    search = service.search_tender_evidence(query="投标截止时间", top_k=2)
    assert search.status == ResultStatus.OK
    matches = search.data["matches"]
    assert matches[0]["evidence_ref"]["evidence_id"] == "EV-DEMO-003"
    assert matches[0]["evidence_ref"]["context_read"] is False

    selected = matches[0]["evidence_ref"]
    selected_input = EvidenceRefInput(
        evidence_id=selected["evidence_id"],
        block_id=selected["block_id"],
        document_id=selected["document_id"],
        document_version=selected["document_version"],
        content_hash=selected["content_hash"],
    )
    before_read = service.validate_evidence_refs(
        refs=[selected_input],
        manifest_version=3,
    )
    assert before_read.data["all_valid"] is True
    assert before_read.data["all_context_read"] is False

    context = service.read_evidence_context(
        evidence_id="EV-DEMO-003",
        before_blocks=5,
        after_blocks=5,
    )
    assert context.status == ResultStatus.OK
    assert context.data["blocks"][0]["evidence_ref"]["context_read"] is True

    after_read = service.validate_evidence_refs(
        refs=[selected_input],
        manifest_version=3,
    )
    assert after_read.data["all_valid"] is True
    assert after_read.data["all_context_read"] is True


def test_validation_detects_hash_and_manifest_tampering() -> None:
    service = _service()
    tampered = EvidenceRefInput(
        evidence_id="EV-DEMO-003",
        block_id="BLK-NOTICE-V2-001",
        document_id="DOC-NOTICE-V2",
        document_version=2,
        content_hash="0" * 64,
    )
    result = service.validate_evidence_refs(
        refs=[tampered],
        manifest_version=2,
    )
    validation = result.data["results"][0]
    assert result.status == ResultStatus.OK
    assert validation["valid"] is False
    assert set(validation["reasons"]) == {
        "manifest_version_mismatch",
        "content_hash_mismatch",
    }

    inactive_version = EvidenceRefInput(
        evidence_id="EV-DEMO-001",
        block_id="BLK-NOTICE-V1-001",
        document_id="DOC-NOTICE-V1",
        document_version=1,
        content_hash=(
            "14c308b8c4c3bbf8421c327c99c5f980"
            "13b1ee730e6cdc26c4c6f70f9e9582b2"
        ),
    )
    inactive_result = service.validate_evidence_refs(
        refs=[inactive_version],
        manifest_version=3,
    )
    assert inactive_result.data["results"][0]["reasons"] == [
        "document_version_not_active"
    ]


def test_version_compare_returns_superseding_conflict() -> None:
    result = _service().compare_document_versions(document_key="tender-notice")
    assert result.status == ResultStatus.OK
    assert [item["document_version"] for item in result.data["versions"]] == [1, 2]
    assert result.data["versions"][1]["active"] is True
    assert result.data["conflicts"][0]["topic"] == "投标截止时间"


def test_fastmcp_contract_has_no_case_id_and_calls_all_read_only_tools() -> None:
    server = create_tender_evidence_mcp(_service())

    async def exercise_protocol() -> dict[str, Any]:
        async with create_connected_server_and_client_session(server) as session:
            tools = (await session.list_tools()).tools
            resources = (await session.list_resources()).resources
            search = await session.call_tool(
                "search_tender_evidence",
                {"query": "付款条件", "top_k": 1},
            )
            context = await session.call_tool(
                "read_evidence_context",
                {"evidence_id": "EV-DEMO-005"},
            )
            comparison = await session.call_tool(
                "compare_document_versions",
                {"document_key": "tender-notice"},
            )
            validation = await session.call_tool(
                "validate_evidence_refs",
                {
                    "refs": [
                        {
                            "evidence_id": "EV-DEMO-005",
                            "block_id": "BLK-SPEC-V1-002",
                            "document_id": "DOC-SPEC-V1",
                            "document_version": 1,
                            "content_hash": (
                                "e55c7fbb199d603f9f9667e6ba04b42c"
                                "2a7f5ac5e24b684f6d2ec65be25808f6"
                            ),
                        }
                    ],
                    "manifest_version": 3,
                },
            )
            manifest = await session.read_resource("tender://current/manifest")
            return {
                "tools": tools,
                "resources": resources,
                "search": search,
                "context": context,
                "comparison": comparison,
                "validation": validation,
                "manifest": manifest,
            }

    result = anyio.run(exercise_protocol)
    expected_tools = {
        "search_tender_evidence",
        "read_evidence_context",
        "compare_document_versions",
        "validate_evidence_refs",
    }
    assert {item.name for item in result["tools"]} == expected_tools
    for tool in result["tools"]:
        assert "case_id" not in tool.inputSchema.get("properties", {})
        assert tool.annotations
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False

    assert {str(item.uri) for item in result["resources"]} == {
        "tender://current/manifest"
    }
    for key in ("search", "context", "comparison", "validation"):
        assert result[key].isError is False
        assert result[key].structuredContent
    manifest_text = result["manifest"].contents[0].text
    assert json.loads(manifest_text)["case_id"] == "CASE-DEMO-001"
    assert result["validation"].structuredContent["data"]["all_context_read"] is True


def test_agent_adapter_maps_structured_output_without_case_id() -> None:
    class RecordingCaller:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
        ) -> dict[str, Any]:
            self.calls.append((name, arguments))
            return {
                "status": "ok",
                "data": {"accepted": True},
                "retryable": False,
                "trace_id": "adapter-test-trace",
                "error_code": None,
                "message": None,
            }

        def read_json_resource(self, uri: str) -> dict[str, Any]:
            assert uri == "tender://current/manifest"
            return {
                "case_id": "CASE-DEMO-001",
                "manifest_version": 3,
                "manifest_hash": "a" * 64,
                "documents": [
                    {
                        "document_id": "DOC-1",
                        "document_key": "notice",
                        "file_name": "notice.pdf",
                        "document_type": "notice",
                        "document_version": 1,
                        "sha256": "b" * 64,
                        "parse_status": "ready",
                        "active": True,
                    }
                ],
            }

    caller = RecordingCaller()
    adapter = McpTenderEvidencePort(caller)
    manifest = adapter.read_manifest()
    assert isinstance(manifest, DocumentManifest)

    ref = AgentEvidenceRef(
        evidence_id="EV-1",
        block_id="BLK-1",
        document_id="DOC-1",
        document_version=1,
        locator=AgentEvidenceLocator(page=1),
        content_hash="c" * 64,
    )
    result = adapter.validate_refs(refs=[ref], manifest=manifest)
    assert result.status.value == "ok"
    name, arguments = caller.calls[-1]
    assert name == "validate_evidence_refs"
    assert "case_id" not in arguments
    assert "locator" not in arguments["refs"][0]


def test_public_service_methods_do_not_accept_case_id() -> None:
    for method_name in (
        "get_manifest",
        "search_tender_evidence",
        "read_evidence_context",
        "compare_document_versions",
        "validate_evidence_refs",
    ):
        signature = inspect.signature(getattr(TenderEvidenceService, method_name))
        assert "case_id" not in signature.parameters
