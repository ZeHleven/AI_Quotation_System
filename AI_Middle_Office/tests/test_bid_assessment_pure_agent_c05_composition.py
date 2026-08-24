from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.registry  # noqa: F401
from app.api.v1 import bid_assessment_pure_agent as conversation_api
from app.agents.bid_assessment_pure.action_runtime import (
    AnswerAction,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    MainAgentProviderOutcome,
    ToolCallBatchAction,
)
from app.agents.bid_assessment_pure.answer_contracts import (
    AnswerDraft,
    ClaimType,
    EpistemicStatus,
    StatementBlock,
)
from app.agents.bid_assessment_pure.deepseek_provider import (
    DeepSeekMainAgentActionProvider,
    OfficialDeepSeekChatCodec,
    OfficialDeepSeekConfig,
)
from app.agents.bid_assessment_pure.local_bootstrap import (
    LocalRuntimeBootstrapRequest,
    bootstrap_local_pure_agent_runtime,
)
from app.agents.bid_assessment_pure.local_runtime_factory import (
    LocalPureAgentCompositionConfig,
    build_local_pure_agent_adapters,
)
from app.agents.bid_assessment_pure.offline_rag_runtime import (
    CanonicalOfflineRagSources,
    OfflineEvidenceRecord,
    OfflineSearchResult,
)
from app.agents.bid_assessment_pure.provider_runtime import (
    OpenAICompatibleChatCodec,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderToolCallProposal,
)
from app.agents.bid_assessment_pure.repository import PureAgentRepository
from app.agents.bid_assessment_pure.runtime import (
    ContextEntryKind,
    ContextProfile,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    RuntimeWakeReason,
    RuntimeWakeup,
)
from app.agents.bid_assessment_pure.state import AgentTaskStatus
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash, canonical_json
from app.agents.bid_assessment_pure.tools import DocumentsOutlineOutput, OutlineEntry
from app.core.database import Base
from app.models.user import User


DOCUMENT_REF = "document:bid-c05"
EVIDENCE_REF = "evidence:bid-c05-deadline"


def _model_profile() -> ModelContextProfile:
    body = {
        "provider_ref": "provider:c05-static",
        "model_ref": "model:c05-static",
        "context_capacity_tokens": 64_000,
        "max_output_tokens": 8_000,
        "token_counter_ref": "counter:c05-static",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        "framing_tokens": 16,
    }
    return ModelContextProfile(
        profile_ref="model-profile:c05-static",
        profile_hash=canonical_hash(body),
        **body,
    )


def _context_profile() -> ContextProfile:
    body = {
        "runtime_max_input_tokens": 48_000,
        "reserved_output_tokens": 4_000,
        "safety_margin_tokens": 2_000,
        "soft_compression_threshold_tokens": 40_000,
        "max_entries": 256,
    }
    return ContextProfile(
        profile_ref="context-profile:c05",
        profile_hash=canonical_hash(body),
        **body,
    )


def _provider_adapter(profile: ModelContextProfile) -> ProviderAdapter:
    codec = OpenAICompatibleChatCodec()
    capabilities = ProviderCapabilities.build(
        capability_ref="provider-capability:c05-static",
        enabled=True,
        provider_ref=profile.provider_ref,
        model_ref=profile.model_ref,
        model_profile_ref=profile.profile_ref,
        model_profile_hash=profile.profile_hash,
        codec_ref=codec.codec_ref,
        token_counter_ref="counter:c05-static",
        supports_function_calling=True,
        supports_structured_output=True,
        supports_tool_calls_with_structured_output=True,
    )
    return ProviderAdapter(capabilities=capabilities, codec=codec)


class _Backend:
    record = OfflineEvidenceRecord(
        evidence_ref=EVIDENCE_REF,
        source_domain="bid_document",
        source_scope_ref=DOCUMENT_REF,
        source_version_ref="document-version:c05",
        text="本项目投标截止时间为2026年9月1日09时30分。",
        locator="第12页",
    )

    async def document_outline(self, document_ref: str):
        assert document_ref == DOCUMENT_REF
        return DocumentsOutlineOutput(
            entries=(OutlineEntry(title="投标须知", level=1, locator="第1-20页"),),
            citable=False,
        )

    async def search(self, *, source_domain: str, query: str, top_k: int):
        assert source_domain in {"bid_document", "enterprise_knowledge"}
        assert query and top_k >= 1
        return OfflineSearchResult(
            records=(self.record,) if source_domain == "bid_document" else ()
        )

    async def resolve(self, evidence_refs: tuple[str, ...]):
        if evidence_refs == (EVIDENCE_REF,):
            return (self.record,)
        return ()


class _DynamicProvider:
    calls = 0

    async def decide(self, *, request, context, registry_snapshot):
        self.calls += 1
        evidence_entries = [
            entry
            for entry in context.projection_entries
            if entry.kind is ContextEntryKind.EVIDENCE_ATOM
        ]
        active_results = [
            entry
            for entry in context.projection_entries
            if entry.kind is ContextEntryKind.ACTIVE_TOOL_RESULT
        ]
        if evidence_entries:
            proposal = MainAgentModelDecision(
                action_kind=MainAgentModelActionKind.ANSWER,
                concise_basis="已取得可引用的截止时间原文。",
                answer=AnswerAction(
                    draft=AnswerDraft(
                        response_language="zh-CN",
                        blocks=(
                            StatementBlock(
                                block_id="statement:c05-deadline",
                                text="该项目投标截止时间为2026年9月1日09时30分。",
                                claim_type=ClaimType.FACT,
                                epistemic_status=EpistemicStatus.SUPPORTED,
                                grounding_refs=(evidence_entries[0].entry_ref,),
                            ),
                        ),
                        context_snapshot_ref=request.context_snapshot_ref,
                        state_version=request.origin_state_version,
                    )
                ),
            )
        elif active_results:
            proposal = self._tool_proposal(
                request=request,
                registry_snapshot=registry_snapshot,
                tool_name="evidence_read",
                arguments={"evidence_refs": [EVIDENCE_REF]},
                authorization_snapshot_ref=(
                    context.snapshot.authorization_snapshot_ref
                ),
            )
        else:
            proposal = self._tool_proposal(
                request=request,
                registry_snapshot=registry_snapshot,
                tool_name="bid_document_search",
                arguments={"query": "投标截止时间"},
                authorization_snapshot_ref=(
                    context.snapshot.authorization_snapshot_ref
                ),
            )
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": f"provider-result:c05-{self.calls}",
            "provider_response_hash": canonical_hash({"call": self.calls}),
            "provider_receipt_ref": f"provider-receipt:c05-{self.calls}",
            "proposal": proposal.model_dump(mode="json"),
            "concise_basis": (
                "调用已授权只读工具" if isinstance(proposal, ToolCallBatchAction)
                else proposal.concise_basis
            ),
        }
        return MainAgentProviderOutcome(**body, outcome_hash=canonical_hash(body))

    @staticmethod
    def _tool_proposal(
        *,
        request: MainAgentDecisionRequest,
        registry_snapshot,
        tool_name: str,
        arguments: dict,
        authorization_snapshot_ref: str,
    ) -> ToolCallBatchAction:
        raw = canonical_json(arguments)
        call = ProviderToolCallProposal(
            model_turn_ref=f"model-turn:c05-{request.decision_sequence}",
            provider_tool_call_id=f"provider-call:c05-{request.decision_sequence}",
            sequence=1,
            task_ref=request.task_ref,
            context_snapshot_ref=request.context_snapshot_ref,
            state_version=request.origin_state_version,
            tool_name=tool_name,
            raw_arguments_json=raw,
            raw_arguments_hash=canonical_hash(raw),
            arguments=arguments,
            arguments_hash=canonical_hash(arguments),
            registry_snapshot_ref=registry_snapshot.snapshot_ref,
            registry_snapshot_hash=registry_snapshot.snapshot_hash,
            visible_tools_hash=registry_snapshot.visible_tools_hash,
            authorization_snapshot_ref=authorization_snapshot_ref,
        )
        return ToolCallBatchAction(
            model_turn_ref=call.model_turn_ref,
            calls=(call,),
        )


def test_c05_official_deepseek_contract_is_narrow_and_secret_safe() -> None:
    config = OfficialDeepSeekConfig(api_key="secret-c05", model_ref="deepseek-chat")
    assert "secret-c05" not in repr(config)
    assert OfficialDeepSeekChatCodec.codec_ref.endswith("v1")
    assert DeepSeekMainAgentActionProvider


def test_c05_composed_controller_retrieves_reads_cites_and_completes() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    profile = _model_profile()
    provider = _DynamicProvider()
    sources = CanonicalOfflineRagSources(_Backend())
    adapters = build_local_pure_agent_adapters(
        LocalPureAgentCompositionConfig(
            provider_adapter=_provider_adapter(profile),
            main_agent_provider=provider,
            rag_sources=sources,
            model_profile=profile,
            context_profile=_context_profile(),
            authorized_document_refs=(DOCUMENT_REF,),
            required_resource_refs=(DOCUMENT_REF,),
        )
    )
    installed = []
    result = bootstrap_local_pure_agent_runtime(
        request=LocalRuntimeBootstrapRequest(
            activation_ref="activation:c05-local",
            requested_by_ref="user:c05",
            target_environment="isolated_local_development",
            install_requested=True,
        ),
        settings=SimpleNamespace(
            app_env="development",
            public_access_enabled=False,
            database_url="sqlite+pysqlite://",
            feature_bid_assessment_pure_agent=True,
            feature_bid_assessment_pure_agent_runtime=True,
        ),
        session_factory=session_factory,
        continuation_secret="c05-local-continuation-secret-at-least-32-bytes",
        adapters=adapters,
        installer=installed.append,
        max_pulses_per_dispatch=32,
    )
    assert result.runtime_available
    dispatcher = installed[-1]

    session = session_factory()
    repository = PureAgentRepository(session)
    conversation = repository.create_conversation(
        owner_id=1,
        tenant_ref="tenant:c05",
        conversation_id="conversation:c05",
    )
    admission = repository.accept_user_message(
        conversation_id=conversation.id,
        owner_id=1,
        user_input={"text": "投标截止时间是什么？", "resources": []},
        created_by_ref="user:1",
        idempotency_key="message:c05-trigger",
    )
    session.commit()
    session.close()

    dispatch_outcome = asyncio.run(
        dispatcher.dispatch(
            RuntimeWakeup.build(
                task_ref=admission.task.task_id,
                conversation_ref=conversation.id,
                observed_state_version=admission.task.state_version,
                reason=RuntimeWakeReason.USER_MESSAGE,
                seed="c05-business-loop",
            )
        )
    )
    assert dispatch_outcome.disposition.value == "terminal"

    session = session_factory()
    repository = PureAgentRepository(session)
    task = repository.load_task_state(admission.task.task_id)
    messages = repository.list_conversation_messages(conversation.id)
    assert task.status is AgentTaskStatus.COMPLETED
    assert provider.calls == 3
    assert messages[-1].message_type == "answer.committed"
    answer = messages[-1].content_json
    assert "第12页" in answer["citations"][0]["text"]
    assert answer["blocks"][0]["citation_refs"]
    session.close()
    engine.dispose()


def test_c06_conversation_api_runs_composed_agent_to_published_answer(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    profile = _model_profile()
    provider = _DynamicProvider()
    adapters = build_local_pure_agent_adapters(
        LocalPureAgentCompositionConfig(
            provider_adapter=_provider_adapter(profile),
            main_agent_provider=provider,
            rag_sources=CanonicalOfflineRagSources(_Backend()),
            model_profile=profile,
            context_profile=_context_profile(),
            authorized_document_refs=(DOCUMENT_REF,),
            required_resource_refs=(DOCUMENT_REF,),
        )
    )
    installed = []
    local_settings = SimpleNamespace(
        app_env="development",
        public_access_enabled=False,
        database_url="sqlite+pysqlite://",
        feature_bid_assessment_pure_agent=True,
        feature_bid_assessment_pure_agent_runtime=True,
        bid_assessment_pure_agent_continuation_secret=(
            "c06-local-continuation-secret-at-least-32-bytes"
        ),
    )
    bootstrap = bootstrap_local_pure_agent_runtime(
        request=LocalRuntimeBootstrapRequest(
            activation_ref="activation:c06-local-api",
            requested_by_ref="user:c06",
            target_environment="isolated_local_development",
            install_requested=True,
        ),
        settings=local_settings,
        session_factory=session_factory,
        continuation_secret=(
            local_settings.bid_assessment_pure_agent_continuation_secret
        ),
        adapters=adapters,
        installer=installed.append,
        max_pulses_per_dispatch=32,
    )
    assert bootstrap.runtime_available
    dispatcher = installed[-1]
    monkeypatch.setattr(conversation_api, "settings", local_settings)

    app = FastAPI()
    app.include_router(conversation_api.router)

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[conversation_api.get_db] = override_db
    app.dependency_overrides[conversation_api.get_current_user] = lambda: User(
        id=1,
        username="c06-local-user",
        role="user",
        role_version=1,
        is_active=True,
    )
    app.dependency_overrides[
        conversation_api.get_pure_agent_runtime_dispatcher
    ] = lambda: dispatcher

    with TestClient(app) as client:
        created = client.post(
            "/bid-assessment-pure-agent/conversations",
            headers={"Idempotency-Key": "c06-create-conversation"},
            json={"title": "C06 local business loop"},
        )
        assert created.status_code == 200
        conversation_ref = created.json()["data"]["conversation_ref"]

        submitted = client.post(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/messages",
            headers={"Idempotency-Key": "c06-submit-message"},
            json={"text": "投标截止时间是什么？"},
        )
        assert submitted.status_code == 200
        task_ref = submitted.json()["data"]["task"]["task_ref"]

        task = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/tasks/{task_ref}"
        )
        messages = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/messages"
        )
        events = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/tasks/{task_ref}/events"
        )

    assert task.status_code == 200
    assert task.json()["data"]["status"] == "completed"
    assert messages.status_code == 200
    message_items = messages.json()["data"]["items"]
    assert [item["role"] for item in message_items] == ["user", "assistant"]
    assert message_items[-1]["message_type"] == "answer"
    assert "2026年9月1日09时30分" in message_items[-1]["content"]["text"]
    assert events.status_code == 200
    assert events.json()["data"]["events"]
    assert provider.calls == 3
    engine.dispose()
