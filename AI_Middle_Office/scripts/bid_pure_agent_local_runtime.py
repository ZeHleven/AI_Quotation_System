"""Reusable frozen-input materializer for the explicit Pure Agent local entry.

Importing this module is side-effect free.  ``materialize_local_runtime`` is
the only boundary that reads the frozen PDF/enterprise dataset, loads local
BCE, reads allowlisted provider settings, or constructs enabled adapters.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.agents.bid_assessment_pure.deepseek_provider import (
    DeepSeekMainAgentActionProvider,
    OfficialDeepSeekConfig,
    build_official_deepseek_adapter,
)
from app.agents.bid_assessment_pure.local_bootstrap import (
    LocalPureAgentRuntimeAdapters,
)
from app.agents.bid_assessment_pure.local_runtime_factory import (
    LocalPureAgentCompositionConfig,
    build_local_pure_agent_adapters,
)
from app.agents.bid_assessment_pure.persisted_context_adapters import (
    AuthorizedResourceIdentity,
)
from app.agents.bid_assessment_pure.offline_rag_runtime import (
    CanonicalOfflineRagSources,
    OfflineEvidenceRecord,
    OfflineSearchResult,
)
from app.agents.bid_assessment_pure.runtime import (
    ContextProfile,
    ModelContextProfile,
    TokenCounterMode,
)
from app.agents.bid_assessment_pure.tool_runtime import canonical_hash
from app.agents.bid_assessment_pure.tools import (
    DocumentsOutlineOutput,
    OutlineEntry,
)
from app.services.bid_field_aware_lexical import (
    lexical_tokens,
    rank_field_aware_bm25f,
)
from app.services.bid_local_semantic_vector_provider import (
    LocalBceExactSemanticProvider,
)
from app.services.bid_pdf_native_layout_parser import (
    RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    parse_pdf_native_layout,
)
from scripts.evaluate_bid_pure_agent_v607 import (
    DomainIndex,
    _file_sha256,
    _semantic_documents,
    build_document_index,
    build_enterprise_index,
    load_dataset,
    model_config,
)


@dataclass(frozen=True, slots=True)
class FrozenLocalRuntimeConfig:
    pdf_path: Path
    embedding_model_path: Path
    secret_env_file: Path
    provider_timeout_seconds: int = 180
    provider_boundary_v2_enabled: bool = False


@dataclass(frozen=True, slots=True)
class FrozenLocalRuntimeMaterialization:
    adapters: LocalPureAgentRuntimeAdapters
    document_scope_ref: str
    enterprise_scope_ref: str
    document_sha256: str
    document_page_count: int
    enterprise_baseline_version: str


class FrozenBceRagBackend:
    """Hybrid read-only backend over the accepted V607 indexes and local BCE."""

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
            namespace = f"pure-agent-local-{domain.replace('_', '-')}"
            self.semantic.upsert_documents(
                namespace=namespace,
                provider_request_id=f"local:{index.source_index_set_hash}",
                documents=_semantic_documents(index),
            )
            self.namespaces[domain] = namespace
            for atoms in index.atoms_by_child_ref.values():
                for atom in atoms:
                    source_metadata = self._metadata(index, atom.source_block_keys)
                    self.records[atom.evidence_key] = OfflineEvidenceRecord(
                        evidence_ref=atom.evidence_key,
                        source_domain=domain,
                        source_scope_ref=index.scope_ref,
                        source_version_ref=index.version_ref,
                        text=atom.normalized_text,
                        locator=str(source_metadata["locator"]),
                    )

    async def document_outline(self, document_ref: str) -> DocumentsOutlineOutput:
        index = self.indexes["bid_document"]
        if document_ref != index.scope_ref:
            return DocumentsOutlineOutput(entries=(), citable=False)
        entries: list[OutlineEntry] = []
        for parent in index.parent_by_ref.values():
            source_metadata = self._metadata(index, parent.source_block_keys)
            section_path = tuple(parent.locator.get("section_path") or ())
            entries.append(
                OutlineEntry(
                    title=str(section_path[-1] if section_path else "招标文件"),
                    level=max(1, min(len(section_path) or 1, 12)),
                    locator=str(source_metadata["locator"]),
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
        query_terms = set(lexical_tokens(query))
        for child_ref in ordered_children[: max(1, int(top_k))]:
            selected = max(
                index.atoms_by_child_ref[child_ref],
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
        return tuple(self.records[ref] for ref in evidence_refs if ref in self.records)

    @staticmethod
    def _metadata(
        index: DomainIndex,
        source_block_keys: tuple[str, ...],
    ) -> Mapping[str, Any]:
        for key in source_block_keys:
            if key in index.block_metadata:
                return index.block_metadata[key]
        raise ValueError("frozen evidence metadata is unavailable")


def _profiles(model_ref: str) -> tuple[ModelContextProfile, ContextProfile]:
    model_body = {
        "provider_ref": "provider:deepseek-official-local",
        "model_ref": model_ref,
        "context_capacity_tokens": 128_000,
        "max_output_tokens": 8_000,
        "token_counter_ref": "provider-token-counter:deepseek-conservative-v1",
        "token_counter_mode": TokenCounterMode.CONSERVATIVE_ESTIMATOR.value,
        # The persisted daily loop can accumulate more Observation and Evidence
        # wrappers than the one-shot C07 acceptance harness.  Reserve the full
        # Provider-visible schema/runtime envelope up front so ContextAssembler
        # compresses before the final wire counter enforces the 96k input cap.
        "framing_tokens": 32_000,
    }
    model_profile = ModelContextProfile(
        profile_ref="model-profile:deepseek-official-local-daily-v2",
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
        profile_ref="context-profile:bid-pure-agent-local-daily-v1",
        profile_hash=canonical_hash(context_body),
        **context_body,
    )
    return model_profile, context_profile


def materialize_local_runtime(
    config: FrozenLocalRuntimeConfig,
) -> FrozenLocalRuntimeMaterialization:
    """Read frozen inputs and return adapters only after explicit startup."""

    dataset = load_dataset()
    pdf_path = config.pdf_path.resolve()
    if _file_sha256(pdf_path) != dataset["document"]["sha256"]:
        raise RuntimeError("local Pure Agent PDF does not match the frozen input")
    embedding_path = config.embedding_model_path.resolve()
    if not embedding_path.is_dir():
        raise RuntimeError("local BCE embedding snapshot is unavailable")

    provider_config = model_config(
        timeout_seconds=config.provider_timeout_seconds,
        secret_env_file=config.secret_env_file.resolve(),
    )
    if provider_config is None:
        raise RuntimeError("local official DeepSeek configuration is unavailable")

    parsed = parse_pdf_native_layout(
        pdf_path.read_bytes(),
        content_sha256=str(dataset["document"]["sha256"]),
        profile=RQ1A_PDF_NATIVE_LAYOUT_PROFILE,
    )
    expected_pages = int(dataset["document"]["page_count"])
    if len(parsed.pages) != expected_pages:
        raise RuntimeError("local frozen PDF page count drifted")

    bid_index = build_document_index(parsed, dataset)
    enterprise_index = build_enterprise_index(dataset)
    backend = FrozenBceRagBackend(
        (bid_index, enterprise_index),
        embedding_model_path=embedding_path,
    )
    rag_sources = CanonicalOfflineRagSources(backend, search_top_k=8)
    model_profile, context_profile = _profiles(provider_config.model)
    provider_adapter = build_official_deepseek_adapter(
        config=OfficialDeepSeekConfig(
            api_key=provider_config.api_key,
            chat_url=provider_config.chat_url,
            model_ref=provider_config.model,
            timeout_seconds=provider_config.timeout_seconds,
        ),
        model_profile=model_profile,
    )
    adapters = build_local_pure_agent_adapters(
        LocalPureAgentCompositionConfig(
            provider_adapter=provider_adapter,
            main_agent_provider=DeepSeekMainAgentActionProvider(provider_adapter),
            rag_sources=rag_sources,
            model_profile=model_profile,
            context_profile=context_profile,
            provider_boundary_v2_enabled=(
                config.provider_boundary_v2_enabled
            ),
            authorized_document_refs=(bid_index.scope_ref,),
            enterprise_scope_ref=enterprise_index.scope_ref,
            required_resource_refs=(bid_index.scope_ref, enterprise_index.scope_ref),
            resource_identities=(
                AuthorizedResourceIdentity(
                    resource_ref=bid_index.scope_ref,
                    resource_kind="bid_document",
                    display_name=bid_index.safe_title,
                    resource_version_ref=bid_index.version_ref,
                ),
                AuthorizedResourceIdentity(
                    resource_ref=enterprise_index.scope_ref,
                    resource_kind="enterprise_knowledge",
                    display_name=enterprise_index.safe_title,
                    resource_version_ref=enterprise_index.version_ref,
                ),
            ),
        )
    )
    return FrozenLocalRuntimeMaterialization(
        adapters=adapters,
        document_scope_ref=bid_index.scope_ref,
        enterprise_scope_ref=enterprise_index.scope_ref,
        document_sha256=str(dataset["document"]["sha256"]),
        document_page_count=len(parsed.pages),
        enterprise_baseline_version=str(dataset["enterprise_baseline"]["version"]),
    )
