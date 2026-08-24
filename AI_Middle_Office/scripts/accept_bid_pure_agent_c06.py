"""Isolated C04-2/C05/C06 acceptance over the frozen V607 business inputs.

The script uses only an in-process SQLite database, native PDF parsing, the
local BCE snapshot and the official allowlisted DeepSeek endpoint.  It never
uses OCR, MCP, Milvus, ECS, production databases, or legacy bid_intake code.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any, Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.models.registry  # noqa: E402,F401
from app.agents.bid_assessment_pure.deepseek_provider import (  # noqa: E402
    DeepSeekMainAgentActionProvider,
    OfficialDeepSeekConfig,
    build_official_deepseek_adapter,
)
from app.agents.bid_assessment_pure.local_bootstrap import (  # noqa: E402
    LocalRuntimeBootstrapRequest,
    bootstrap_local_pure_agent_runtime,
)
from app.agents.bid_assessment_pure.local_runtime_factory import (  # noqa: E402
    LocalPureAgentCompositionConfig,
    build_local_pure_agent_adapters,
)
from app.agents.bid_assessment_pure.offline_rag_runtime import (  # noqa: E402
    CanonicalOfflineRagSources,
    OfflineEvidenceRecord,
    OfflineSearchResult,
)
from app.agents.bid_assessment_pure.runtime import (  # noqa: E402
    ContextProfile,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash  # noqa: E402
from app.agents.bid_assessment_pure.tools import (  # noqa: E402
    DocumentsOutlineOutput,
    OutlineEntry,
)
from app.api.v1 import bid_assessment_pure_agent as conversation_api  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.bid_field_aware_lexical import (  # noqa: E402
    lexical_tokens,
    rank_field_aware_bm25f,
)
from app.services.bid_local_semantic_vector_provider import (  # noqa: E402
    LocalBceExactSemanticProvider,
)
from app.services.bid_pdf_native_layout_parser import (  # noqa: E402
    RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    parse_pdf_native_layout,
)
from scripts.evaluate_bid_pure_agent_v607 import (  # noqa: E402
    DomainIndex,
    _file_sha256,
    _secret_values,
    _semantic_documents,
    build_document_index,
    build_enterprise_index,
    load_dataset,
    model_config,
)


DEFAULT_PDF = PROJECT_ROOT / ".local-mvp1-real-pdf-sources" / "香港中心.pdf"
DEFAULT_OUTPUT = PROJECT_ROOT / ".local-c06-pure-agent-acceptance" / "result.json"


class V607BceRagBackend:
    """Small hybrid adapter over the frozen V607 indexes and local BCE."""

    def __init__(
        self,
        indexes: tuple[DomainIndex, ...],
        *,
        embedding_model_path: Path,
    ) -> None:
        self.indexes = {index.domain: index for index in indexes}
        self.semantic = LocalBceExactSemanticProvider(
            model_path=str(embedding_model_path)
        )
        self.namespaces: dict[str, str] = {}
        self.records: dict[str, OfflineEvidenceRecord] = {}
        for domain, index in self.indexes.items():
            namespace = f"c06-{domain.replace('_', '-')}"
            self.semantic.upsert_documents(
                namespace=namespace,
                provider_request_id=f"c06:{index.source_index_set_hash}",
                documents=_semantic_documents(index),
            )
            self.namespaces[domain] = namespace
            for atoms in index.atoms_by_child_ref.values():
                for atom in atoms:
                    metadata = self._metadata(index, atom.source_block_keys)
                    self.records[atom.evidence_key] = OfflineEvidenceRecord(
                        evidence_ref=atom.evidence_key,
                        source_domain=domain,
                        source_scope_ref=index.scope_ref,
                        source_version_ref=index.version_ref,
                        text=atom.normalized_text,
                        locator=str(metadata["locator"]),
                    )

    async def document_outline(self, document_ref: str) -> DocumentsOutlineOutput:
        index = self.indexes["bid_document"]
        if document_ref != index.scope_ref:
            return DocumentsOutlineOutput(entries=(), citable=False)
        entries: list[OutlineEntry] = []
        for parent in index.parent_by_ref.values():
            metadata = self._metadata(index, parent.source_block_keys)
            path = tuple(parent.locator.get("section_path") or ())
            entries.append(
                OutlineEntry(
                    title=str(path[-1] if path else "招标文件"),
                    level=max(1, min(len(path) or 1, 12)),
                    locator=str(metadata["locator"]),
                )
            )
            if len(entries) >= 200:
                break
        return DocumentsOutlineOutput(entries=tuple(entries), citable=False)

    async def search(
        self,
        *,
        source_domain: str,
        query: str,
        top_k: int,
    ) -> OfflineSearchResult:
        index = self.indexes[source_domain]
        scores: defaultdict[str, float] = defaultdict(float)
        lexical = rank_field_aware_bm25f(query, index.lexical_corpus)
        for rank, hit in enumerate(lexical[:40], 1):
            scores[hit.child_id] += 1.0 / (60 + rank)
        semantic = self.semantic.search(
            namespace=self.namespaces[source_domain],
            query=query,
            top_k=40,
        )
        for rank, hit in enumerate(semantic, 1):
            scores[hit.retrieval_child_key] += 0.35 / (60 + rank)
        ordered_children = sorted(scores, key=lambda ref: (-scores[ref], ref))
        records: list[OfflineEvidenceRecord] = []
        seen: set[str] = set()
        for child_ref in ordered_children[: max(1, int(top_k))]:
            query_terms = set(lexical_tokens(query))
            atoms = index.atoms_by_child_ref[child_ref]
            selected = max(
                atoms,
                key=lambda atom: (
                    sum(
                        1
                        for term in query_terms
                        if term in atom.normalized_text.lower()
                    ),
                    len(atom.normalized_text),
                    atom.evidence_key,
                ),
            )
            if selected.evidence_key not in seen:
                seen.add(selected.evidence_key)
                records.append(self.records[selected.evidence_key])
        return OfflineSearchResult(records=tuple(records))

    async def resolve(
        self,
        evidence_refs: tuple[str, ...],
    ) -> tuple[OfflineEvidenceRecord, ...]:
        return tuple(
            self.records[ref]
            for ref in evidence_refs
            if ref in self.records
        )

    @staticmethod
    def _metadata(
        index: DomainIndex,
        source_block_keys: tuple[str, ...],
    ) -> Mapping[str, Any]:
        for key in source_block_keys:
            if key in index.block_metadata:
                return index.block_metadata[key]
        raise ValueError("frozen V607 evidence metadata is unavailable")


def _profiles(model_ref: str) -> tuple[ModelContextProfile, ContextProfile]:
    model_body = {
        "provider_ref": "provider:deepseek-official-local",
        "model_ref": model_ref,
        "context_capacity_tokens": 128_000,
        "max_output_tokens": 8_000,
        "token_counter_ref": "provider-token-counter:deepseek-conservative-v1",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        # Reserve the provider-only Main Agent request/schema envelope.  Context
        # selection sees this amount even though those bytes are appended only
        # when the final DeepSeek wire request is rendered.
        "framing_tokens": 16_000,
    }
    model_profile = ModelContextProfile(
        profile_ref="model-profile:deepseek-official-c06",
        profile_hash=canonical_hash(model_body),
        **model_body,
    )
    context_body = {
        "runtime_max_input_tokens": 96_000,
        "reserved_output_tokens": 8_000,
        "safety_margin_tokens": 8_000,
        "soft_compression_threshold_tokens": 80_000,
        "max_entries": 320,
    }
    context_profile = ContextProfile(
        profile_ref="context-profile:bid-pure-agent-c06",
        profile_hash=canonical_hash(context_body),
        **context_body,
    )
    return model_profile, context_profile


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--embedding-model-path", type=Path, required=True)
    parser.add_argument("--secret-env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--acceptance-phase",
        choices=("c06", "c07"),
        default="c06",
        help="Labels the isolated result artifact without changing its inputs.",
    )
    parser.add_argument(
        "--question",
        default=(
            "请结合招标文件和企业资料，判断本项目目前是否建议立即投标，"
            "列出最关键的两项风险；证据不足必须明确标为未知并引用原文。"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    started = time.perf_counter()
    dataset = load_dataset()
    pdf_path = args.pdf.resolve()
    if _file_sha256(pdf_path) != dataset["document"]["sha256"]:
        raise RuntimeError("C06 PDF does not match the frozen V607 input")
    embedding_path = args.embedding_model_path.resolve()
    if not embedding_path.is_dir():
        raise RuntimeError("C06 embedding snapshot is unavailable")
    secrets = _secret_values(args.secret_env_file)
    if not secrets:
        raise RuntimeError("C06 SecretEnvFile has no allowlisted model settings")
    provider_config = model_config(
        timeout_seconds=args.timeout_seconds,
        secret_env_file=args.secret_env_file,
    )
    if provider_config is None:
        raise RuntimeError("C06 DeepSeek configuration is unavailable")

    content = pdf_path.read_bytes()
    parsed = parse_pdf_native_layout(
        content,
        content_sha256=str(dataset["document"]["sha256"]),
        profile=RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    )
    if len(parsed.pages) != int(dataset["document"]["page_count"]):
        raise RuntimeError("C06 native PDF parse page count drifted")
    bid_index = build_document_index(parsed, dataset)
    enterprise_index = build_enterprise_index(dataset)
    backend = V607BceRagBackend(
        (bid_index, enterprise_index),
        embedding_model_path=embedding_path,
    )
    rag_sources = CanonicalOfflineRagSources(backend, search_top_k=8)
    model_profile, context_profile = _profiles(provider_config.model)
    deepseek_config = OfficialDeepSeekConfig(
        api_key=provider_config.api_key,
        chat_url=provider_config.chat_url,
        model_ref=provider_config.model,
        timeout_seconds=provider_config.timeout_seconds,
    )
    provider_adapter = build_official_deepseek_adapter(
        config=deepseek_config,
        model_profile=model_profile,
    )
    adapters = build_local_pure_agent_adapters(
        LocalPureAgentCompositionConfig(
            provider_adapter=provider_adapter,
            main_agent_provider=DeepSeekMainAgentActionProvider(provider_adapter),
            rag_sources=rag_sources,
            model_profile=model_profile,
            context_profile=context_profile,
            authorized_document_refs=(bid_index.scope_ref,),
            enterprise_scope_ref=enterprise_index.scope_ref,
            required_resource_refs=(bid_index.scope_ref, enterprise_index.scope_ref),
        )
    )

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    local_settings = SimpleNamespace(
        app_env="development",
        public_access_enabled=False,
        database_url="sqlite+pysqlite://",
        feature_bid_assessment_pure_agent=True,
        feature_bid_assessment_pure_agent_runtime=True,
        bid_assessment_pure_agent_continuation_secret=(
            "c06-real-local-continuation-secret-at-least-32-bytes"
        ),
    )
    installed = []
    bootstrap = bootstrap_local_pure_agent_runtime(
        request=LocalRuntimeBootstrapRequest(
            activation_ref="activation:c06-real-local",
            requested_by_ref="user:c06-acceptance",
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
        max_pulses_per_dispatch=48,
    )
    if not bootstrap.runtime_available:
        raise RuntimeError("C06 local Runtime bootstrap was rejected")
    dispatcher = installed[-1]
    conversation_api.settings = local_settings
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
        username="c06-acceptance-user",
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
            headers={"Idempotency-Key": "c06-real-create-conversation"},
            json={"title": "C06 isolated real business acceptance"},
        )
        created.raise_for_status()
        conversation_ref = created.json()["data"]["conversation_ref"]
        submitted = client.post(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/messages",
            headers={"Idempotency-Key": "c06-real-submit-message"},
            json={"text": args.question},
        )
        submitted.raise_for_status()
        task_ref = submitted.json()["data"]["task"]["task_ref"]
        task_payload = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/tasks/{task_ref}"
        ).json()["data"]
        message_payload = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/messages"
        ).json()["data"]
        event_payload = client.get(
            f"/bid-assessment-pure-agent/conversations/{conversation_ref}/tasks/{task_ref}/events",
            params={"limit": 100},
        ).json()["data"]

    messages = message_payload["items"]
    answer = messages[-1] if messages and messages[-1]["role"] == "assistant" else None
    passed = bool(
        task_payload["status"] == "completed"
        and answer
        and answer["message_type"] == "answer"
        and answer["content"].get("citations")
    )
    result = {
        "schema_version": (
            f"bid.pure_agent.{args.acceptance_phase}.local_acceptance.v1"
        ),
        "status": "passed" if passed else "failed",
        "isolation": {
            "sqlite_in_process": True,
            "native_pdf": True,
            "local_bce": True,
            "official_deepseek": True,
            "ocr": False,
            "external_mcp": False,
            "milvus": False,
            "ecs": False,
            "production_database": False,
        },
        "document": {
            "sha256": dataset["document"]["sha256"],
            "pages": len(parsed.pages),
        },
        "enterprise_baseline": {
            "version": dataset["enterprise_baseline"]["version"],
            "sha256": dataset["enterprise_baseline"]["sha256"],
        },
        "task": {
            "status": task_payload["status"],
            "state_version": task_payload["state_version"],
            "event_count": len(event_payload["events"]),
        },
        "answer": {
            "present": answer is not None,
            "citation_count": (
                len(answer["content"].get("citations") or ()) if answer else 0
            ),
            "text_sha256": (
                hashlib.sha256(
                    str(answer["content"].get("text") or "").encode("utf-8")
                ).hexdigest()
                if answer
                else None
            ),
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    engine.dispose()
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
