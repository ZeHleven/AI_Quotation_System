from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

PROJECT_DIR = Path(__file__).resolve().parents[1]

from app.agents.bid_intake.fake_adapters import (
    FakeTenderEvidenceClient,
    build_demo_draft,
    build_demo_evidence,
    build_demo_manifest,
    build_demo_script,
)
from app.agents.bid_intake.mcp_adapter import (
    McpTenderEvidencePort,
    PersistentStreamableHttpMcpToolCaller,
)
from app.agents.bid_intake.openai_compatible_model import (
    FailoverBidAnalysisModel,
    OpenAICompatibleBidAnalysisModel,
)
from app.agents.bid_intake.persistent_executor import (
    BidIntakeExecutionError,
    PersistentBidIntakeExecutor,
)
from app.agents.bid_intake.policy import InMemoryBidPolicy
from app.agents.bid_intake.ports import AgentRuntime
from app.core.database import Base
from app.models import registry as model_registry  # noqa: F401
from app.models.bid_intake_runtime import (
    BidIntakeAgentRun,
    BidIntakeAssessment,
    BidIntakeCheckpoint,
    BidIntakeCheckpointBlob,
    BidIntakeCheckpointWrite,
    BidIntakeHumanDecision,
    BidIntakeRunEvent,
)
from app.models.bidding import BidProject
from app.models.tender_evidence import BidEvidenceManifest
from app.models.user import User
from app.services.bid_intake_runtime import (
    BidIntakeRuntimeConflict,
    claim_agent_run,
    create_assessment_run,
    fail_claimed_agent_run,
    queue_human_decision,
    retry_failed_run,
)


RUNTIME_TABLES = [
    User.__table__,
    BidProject.__table__,
    BidEvidenceManifest.__table__,
    BidIntakeAssessment.__table__,
    BidIntakeAgentRun.__table__,
    BidIntakeHumanDecision.__table__,
    BidIntakeRunEvent.__table__,
    BidIntakeCheckpoint.__table__,
    BidIntakeCheckpointBlob.__table__,
    BidIntakeCheckpointWrite.__table__,
]


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'agent-runtime.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=RUNTIME_TABLES)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed(session_factory):
    db = session_factory()
    try:
        user = User(
            username=f"agent-{uuid.uuid4().hex[:8]}",
            hashed_password="test",
            role="manager",
            is_active=True,
        )
        db.add(user)
        db.flush()
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="持久化研判测试",
            status="draft",
            owner_user_id=user.id,
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        manifest_contract = build_demo_manifest().model_copy(
            update={"case_id": project.project_uuid}
        )
        manifest = BidEvidenceManifest(
            manifest_uuid=str(uuid.uuid4()),
            project_id=project.id,
            version_no=manifest_contract.manifest_version,
            manifest_hash=manifest_contract.manifest_hash,
            snapshot_json=json.dumps(
                manifest_contract.model_dump(mode="json"),
                ensure_ascii=False,
            ),
            active=True,
            created_by=user.id,
        )
        db.add(manifest)
        db.flush()
        created = create_assessment_run(
            db,
            project=project,
            current_user=user,
        )
        db.commit()
        return {
            "user_id": user.id,
            "project_uuid": project.project_uuid,
            "assessment_uuid": created.assessment.assessment_uuid,
            "run_uuid": created.run.run_uuid,
            "manifest": manifest_contract,
        }
    finally:
        db.close()


def _runtime(case_id: str) -> AgentRuntime:
    records = build_demo_evidence()
    return AgentRuntime(
        model=build_demo_script(records),
        evidence=FakeTenderEvidenceClient(
            case_id=case_id,
            records=records,
        ),
        policy=InMemoryBidPolicy(),
    )


def _claim(session_factory, *, run_uuid: str):
    db = session_factory()
    try:
        claimed = claim_agent_run(
            db,
            worker_id="pytest-worker",
            run_uuid=run_uuid,
        )
        db.commit()
        assert claimed is not None
        return claimed
    finally:
        db.close()


def test_process_restart_resumes_from_sql_checkpoint(tmp_path):
    session_factory = _database(tmp_path)
    seeded = _seed(session_factory)
    first_claim = _claim(session_factory, run_uuid=seeded["run_uuid"])

    first_executor = PersistentBidIntakeExecutor(session_factory)
    paused = first_executor.execute(
        run_uuid=seeded["run_uuid"],
        lease_token=first_claim.lease_token,
        runtime=_runtime(seeded["project_uuid"]),
        manifest=seeded["manifest"],
    )
    assert paused["status"] == "waiting_human"
    assert paused["waiting_human"] is True
    assert paused["checkpoint_id"]

    db = session_factory()
    try:
        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == seeded["run_uuid"])
            .one()
        )
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(BidIntakeAssessment.id == run.assessment_id)
            .one()
        )
        trace_events = (
            db.query(BidIntakeRunEvent)
            .filter(
                BidIntakeRunEvent.run_id == run.id,
                BidIntakeRunEvent.event_type.like("trace_step_%"),
            )
            .order_by(BidIntakeRunEvent.id.asc())
            .all()
        )
        trace_payloads = [
            json.loads(event.payload_json or "{}")
            for event in trace_events
        ]
        trace_kinds = {
            payload.get("kind")
            for payload in trace_payloads
        }
        assert {
            "llm_input",
            "react",
            "plan",
            "loop",
            "tool",
            "observation",
            "policy",
            "gate",
            "human",
        }.issubset(trace_kinds)
        assert any(
            event.event_type == "trace_step_waiting"
            and payload.get("kind") == "human"
            for event, payload in zip(trace_events, trace_payloads)
        )
        user = db.query(User).filter(User.id == seeded["user_id"]).one()
        decision, idempotent = queue_human_decision(
            db,
            assessment=assessment,
            run=run,
            current_user=user,
            decision_uuid=str(uuid.uuid4()),
            action="approved",
            report_version=1,
            manifest_version=1,
        )
        db.commit()
        decision_uuid = decision.decision_uuid
        assert idempotent is False
    finally:
        db.close()

    second_claim = _claim(session_factory, run_uuid=seeded["run_uuid"])
    restarted_executor = PersistentBidIntakeExecutor(session_factory)
    completed = restarted_executor.execute(
        run_uuid=seeded["run_uuid"],
        lease_token=second_claim.lease_token,
        runtime=_runtime(seeded["project_uuid"]),
        manifest=seeded["manifest"],
    )
    assert completed["status"] == "completed"
    assert completed["phase"] == "approved"
    assert completed["checkpoint_id"] != paused["checkpoint_id"]

    db = session_factory()
    try:
        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == seeded["run_uuid"])
            .one()
        )
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(BidIntakeAssessment.id == run.assessment_id)
            .one()
        )
        decision = (
            db.query(BidIntakeHumanDecision)
            .filter(
                BidIntakeHumanDecision.decision_uuid == decision_uuid
            )
            .one()
        )
        assert run.status == "completed"
        assert assessment.status == "approved"
        assert decision.status == "applied"
        assert db.query(BidIntakeCheckpoint).count() > 1
        assert db.query(BidIntakeCheckpointBlob).count() > 1
    finally:
        db.close()


def test_pre_execution_failure_releases_lease_and_can_retry(tmp_path):
    session_factory = _database(tmp_path)
    seeded = _seed(session_factory)
    claim = _claim(session_factory, run_uuid=seeded["run_uuid"])

    db = session_factory()
    try:
        changed = fail_claimed_agent_run(
            db,
            run_uuid=seeded["run_uuid"],
            lease_token=claim.lease_token,
            error_code="MCP_SESSION_INITIALIZATION_FAILED",
            error_message="MCP session initialization failed",
        )
        db.commit()
        assert changed is True

        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == seeded["run_uuid"])
            .one()
        )
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(BidIntakeAssessment.id == run.assessment_id)
            .one()
        )
        assert run.status == "failed"
        assert run.phase == "failed"
        assert run.error_code == "MCP_SESSION_INITIALIZATION_FAILED"
        assert run.worker_id is None
        assert run.lease_token is None
        assert run.lease_expires_at is None
        assert assessment.status == "failed"
        failed_event = (
            db.query(BidIntakeRunEvent)
            .filter(
                BidIntakeRunEvent.run_id == run.id,
                BidIntakeRunEvent.event_type == "run_failed",
            )
            .one()
        )
        assert json.loads(failed_event.payload_json)[
            "failure_stage"
        ] == "pre_execution"

        retry_failed_run(
            db,
            assessment=assessment,
            run=run,
        )
        db.commit()
        assert run.status == "queued"
        assert assessment.status == "queued"
    finally:
        db.close()


def test_persistent_mcp_caller_reuses_one_async_session():
    metrics = {"entered": 0, "exited": 0, "tools": 0, "resources": 0}

    class FakeSession:
        async def call_tool(self, name, arguments, read_timeout_seconds=None):
            del arguments, read_timeout_seconds
            metrics["tools"] += 1
            return SimpleNamespace(
                isError=False,
                structuredContent={"name": name},
            )

        async def read_resource(self, uri):
            metrics["resources"] += 1
            return SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        text=json.dumps({"uri": str(uri)})
                    )
                ]
            )

    @asynccontextmanager
    async def fake_context():
        metrics["entered"] += 1
        try:
            yield FakeSession()
        finally:
            metrics["exited"] += 1

    with PersistentStreamableHttpMcpToolCaller(
        url="http://mcp.test/mcp",
        bearer_token="test-token",
        session_context_factory=fake_context,
    ) as caller:
        assert caller.call_tool("one", {}) == {"name": "one"}
        assert caller.call_tool("two", {}) == {"name": "two"}
        assert caller.read_json_resource("tender://current/manifest")[
            "uri"
        ].startswith("tender://")
        assert metrics["entered"] == 1

    assert metrics == {
        "entered": 1,
        "exited": 1,
        "tools": 2,
        "resources": 1,
    }


def test_openai_compatible_model_maps_native_tool_call(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "search_tender_evidence",
                                        "arguments": (
                                            '{"query":"付款条件","top_k":5}'
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"total_tokens": 100},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.agents.bid_intake.openai_compatible_model.httpx.Client",
        FakeClient,
    )
    model = OpenAICompatibleBidAnalysisModel(
        api_url="https://model.test/chat/completions",
        api_key="secret",
        model="test-model",
    )
    result = model.invoke(
        [],
        system_prompt="受控研判",
        state_view={"case_id": "CASE-1"},
    )

    assert result.tool_calls == [
        {
            "name": "search_tender_evidence",
            "args": {"query": "付款条件", "top_k": 5},
            "id": "call-1",
            "type": "tool_call",
        }
    ]
    assert captured["payload"]["tool_choice"] == "auto"
    assert {
        item["function"]["name"]
        for item in captured["payload"]["tools"]
    } == {
        "search_tender_evidence",
        "read_evidence_context",
        "compare_document_versions",
        "get_bid_policy_rule",
    }


def test_failover_model_uses_secondary_provider_after_payment_error():
    request = httpx.Request(
        "POST",
        "https://primary.example/chat/completions",
    )
    payment_response = httpx.Response(
        402,
        request=request,
    )
    calls = {"primary": 0, "fallback": 0}

    class PrimaryModel:
        model_id = "primary-model"

        def invoke(self, messages, *, system_prompt, state_view):
            del messages, system_prompt, state_view
            calls["primary"] += 1
            raise httpx.HTTPStatusError(
                "Payment Required",
                request=request,
                response=payment_response,
            )

    class FallbackModel:
        model_id = "fallback-model"

        def invoke(self, messages, *, system_prompt, state_view):
            del messages, system_prompt, state_view
            calls["fallback"] += 1
            return AIMessage(content="fallback-ok")

    model = FailoverBidAnalysisModel(
        primary=PrimaryModel(),
        fallback=FallbackModel(),
    )

    result = model.invoke(
        [],
        system_prompt="受控研判",
        state_view={"case_id": "CASE-1"},
    )
    second_result = model.invoke(
        [],
        system_prompt="受控研判",
        state_view={"case_id": "CASE-1", "reasoning_loop_count": 2},
    )

    assert result.content == "fallback-ok"
    assert second_result.content == "fallback-ok"
    assert calls == {"primary": 1, "fallback": 2}
    assert result.response_metadata["bid_model_route"] == "fallback"
    assert result.response_metadata["bid_model_id"] == "fallback-model"
    assert result.response_metadata["bid_primary_error"] == "http_402"


def test_openai_compatible_model_disables_tools_for_forced_final(
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"project_summary":"partial"}',
                            "tool_calls": [],
                        },
                    }
                ],
                "usage": {"total_tokens": 80},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            return None

        def post(self, url, *, headers, json):
            del url, headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.agents.bid_intake.openai_compatible_model.httpx.Client",
        FakeClient,
    )
    model = OpenAICompatibleBidAnalysisModel(
        api_url="https://model.test/chat/completions",
        api_key="secret",
        model="test-model",
    )

    result = model.invoke(
        [],
        system_prompt="受控研判",
        state_view={
            "case_id": "CASE-1",
            "force_final_response": True,
        },
    )

    assert result.tool_calls == []
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]
    assert "禁止继续调用工具" in (
        captured["payload"]["messages"][0]["content"]
    )


def test_production_adapters_complete_a_mocked_assessment(monkeypatch, tmp_path):
    """Compose the production model/MCP ports without external network calls."""

    session_factory = _database(tmp_path)
    seeded = _seed(session_factory)
    records = build_demo_evidence()
    evidence_double = FakeTenderEvidenceClient(
        case_id=seeded["project_uuid"],
        records=records,
    )
    mcp_calls: list[str] = []

    class FakeMcpCaller:
        def read_json_resource(self, uri):
            assert uri == "tender://current/manifest"
            return seeded["manifest"].model_dump(mode="json")

        def call_tool(self, name, arguments):
            mcp_calls.append(name)
            if name == "search_tender_evidence":
                result = evidence_double.search(**arguments)
            elif name == "read_evidence_context":
                result = evidence_double.read_context(**arguments)
            elif name == "compare_document_versions":
                result = evidence_double.compare_versions(**arguments)
            elif name == "validate_evidence_refs":
                refs = [
                    records[item["evidence_id"]].ref()
                    for item in arguments["refs"]
                ]
                result = evidence_double.validate_refs(
                    refs=refs,
                    manifest=seeded["manifest"],
                )
            else:
                raise AssertionError(f"unexpected MCP tool: {name}")
            return result.model_dump(mode="json")

    scripted_messages = build_demo_script(records).responses
    model_turn = {"index": 0}

    class FakeResponse:
        def __init__(self, message):
            self._message = message

        def raise_for_status(self):
            return None

        def json(self):
            tool_calls = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(
                            call["args"],
                            ensure_ascii=False,
                        ),
                    },
                }
                for call in self._message.tool_calls
            ]
            return {
                "choices": [
                    {
                        "finish_reason": (
                            "tool_calls" if tool_calls else "stop"
                        ),
                        "message": {
                            "content": self._message.content,
                            "tool_calls": tool_calls,
                        },
                    }
                ],
                "usage": {"total_tokens": 100},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            return None

        def post(self, url, *, headers, json):
            del url, headers, json
            index = model_turn["index"]
            model_turn["index"] += 1
            return FakeResponse(scripted_messages[index])

    monkeypatch.setattr(
        "app.agents.bid_intake.openai_compatible_model.httpx.Client",
        FakeClient,
    )
    model = OpenAICompatibleBidAnalysisModel(
        api_url="https://model.test/chat/completions",
        api_key="secret",
        model="test-model",
    )
    runtime = AgentRuntime(
        model=model,
        evidence=McpTenderEvidencePort(FakeMcpCaller()),
        policy=InMemoryBidPolicy(),
    )
    claim = _claim(session_factory, run_uuid=seeded["run_uuid"])

    result = PersistentBidIntakeExecutor(session_factory).execute(
        run_uuid=seeded["run_uuid"],
        lease_token=claim.lease_token,
        runtime=runtime,
        manifest=seeded["manifest"],
    )
    db = session_factory()
    try:
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(
                BidIntakeAssessment.assessment_uuid
                == seeded["assessment_uuid"]
            )
            .one()
        )
        persisted_gate = json.loads(assessment.gate_result_json)
        persisted_draft = json.loads(assessment.assessment_json)
    finally:
        db.close()

    assert result["status"] == "waiting_human"
    assert result["waiting_human"] is True
    assert model_turn["index"] == 5, json.dumps(
        {
            "result": result,
            "gate": persisted_gate,
            "mcp_calls": mcp_calls,
        },
        ensure_ascii=False,
        default=str,
    )
    assert mcp_calls == [
        "search_tender_evidence",
        "read_evidence_context",
        "read_evidence_context",
        "compare_document_versions",
        "validate_evidence_refs",
    ]
    assert persisted_gate["status"] == "passed"
    assert persisted_draft == build_demo_draft(
        records
    ).model_dump(mode="json")


def test_worker_refreshes_heartbeat_during_long_execution(monkeypatch):
    from scripts import bid_intake_agent_worker as worker

    heartbeats: list[dict] = []
    monkeypatch.setenv("BID_INTAKE_WORKER_HEARTBEAT_SECONDS", "0.5")
    monkeypatch.setattr(
        worker,
        "_heartbeat",
        lambda **payload: heartbeats.append(payload),
    )

    def slow_execute(*, run_uuid, lease_token):
        assert run_uuid == "run-long"
        assert lease_token == "lease-long"
        time.sleep(0.65)
        return {"status": "waiting_human"}

    monkeypatch.setattr(worker, "_execute_claim", slow_execute)
    result = worker._execute_claim_with_heartbeat(
        run_uuid="run-long",
        lease_token="lease-long",
        worker_id="worker-long",
        capabilities={"mcp_configured": True},
    )

    assert result == {"status": "waiting_human"}
    assert len(heartbeats) >= 2
    assert all(item["status"] == "busy" for item in heartbeats)
    assert all(
        item["current_run_uuid"] == "run-long" for item in heartbeats
    )


def test_worker_pid_file_records_actual_python_process(tmp_path):
    from scripts import bid_intake_agent_worker as worker

    pid_file = tmp_path / "bid_intake_worker.pid"
    worker._write_worker_pid_file(pid_file)

    assert pid_file.read_text(encoding="ascii") == str(os.getpid())

    worker._remove_worker_pid_file(pid_file)
    assert not pid_file.exists()


def test_worker_mcp_preflight_initializes_authenticated_session(monkeypatch):
    from scripts import bid_intake_agent_worker as worker

    captured: dict = {}

    class FakeCaller:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            captured["entered"] = True
            return self

        def __exit__(self, exc_type, exc, traceback):
            captured["exited"] = True

    monkeypatch.setenv(
        "TENDER_MCP_JWT_SECRET",
        "test-secret-with-at-least-thirty-two-characters",
    )
    monkeypatch.setenv("TENDER_MCP_ISSUER", "http://127.0.0.1:8012")
    monkeypatch.setenv(
        "TENDER_MCP_AUDIENCE",
        "http://127.0.0.1:8012/mcp",
    )
    monkeypatch.setenv(
        "BID_INTAKE_MCP_URL",
        "http://127.0.0.1:8012/mcp",
    )
    monkeypatch.setattr(
        worker,
        "PersistentStreamableHttpMcpToolCaller",
        FakeCaller,
    )

    worker._preflight_mcp_authentication()

    assert captured["url"] == "http://127.0.0.1:8012/mcp"
    assert captured["entered"] is True
    assert captured["exited"] is True
    assert captured["bearer_token"]


def test_windows_launcher_tracks_the_actual_worker_pid():
    launcher = (
        PROJECT_DIR / "start_bid_intake_agent.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "Wait-WorkerPidFile" in launcher
    assert '"--pid-file"' in launcher
    assert "$actualWorkerProcess.Id" in launcher
    assert (
        "Set-Content -Path $WorkerPidFile -Value $workerProcess.Id"
        not in launcher
    )


def test_executor_rejects_a_policy_version_different_from_task_binding(
    tmp_path,
):
    session_factory = _database(tmp_path)
    seeded = _seed(session_factory)
    claim = _claim(session_factory, run_uuid=seeded["run_uuid"])
    runtime = _runtime(seeded["project_uuid"])

    class MismatchedPolicy(InMemoryBidPolicy):
        @property
        def version(self):
            return "qs_bid_decision_policy_future"

    mismatched = AgentRuntime(
        model=runtime.model,
        evidence=runtime.evidence,
        policy=MismatchedPolicy(),
    )
    with pytest.raises(
        BidIntakeExecutionError,
        match="BOUND_POLICY_VERSION_MISMATCH",
    ):
        PersistentBidIntakeExecutor(session_factory).execute(
            run_uuid=seeded["run_uuid"],
            lease_token=claim.lease_token,
            runtime=mismatched,
            manifest=seeded["manifest"],
        )

    db = session_factory()
    try:
        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == seeded["run_uuid"])
            .one()
        )
        assert run.status == "failed"
        assert run.error_code == "AGENT_EXECUTION_FAILED"
    finally:
        db.close()


def test_policy_manual_review_blocks_ordinary_approval_at_control_plane(
    tmp_path,
):
    session_factory = _database(tmp_path)
    seeded = _seed(session_factory)
    db = session_factory()
    try:
        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == seeded["run_uuid"])
            .one()
        )
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(BidIntakeAssessment.id == run.assessment_id)
            .one()
        )
        user = db.query(User).filter(User.id == seeded["user_id"]).one()
        run.status = "waiting_human"
        assessment.status = "waiting_human"
        assessment.gate_result_json = json.dumps(
            {
                "status": "manual_review_required",
                "issues": [
                    {
                        "code": "POLICY_REQUIRES_MANUAL_REVIEW",
                        "message": "policy blocks ordinary approval",
                    }
                ],
            }
        )
        db.flush()

        with pytest.raises(
            BidIntakeRuntimeConflict,
            match="APPROVAL_BLOCKED_BY_POLICY",
        ):
            queue_human_decision(
                db,
                assessment=assessment,
                run=run,
                current_user=user,
                decision_uuid=str(uuid.uuid4()),
                action="approved",
                report_version=1,
                manifest_version=1,
            )
    finally:
        db.rollback()
        db.close()
