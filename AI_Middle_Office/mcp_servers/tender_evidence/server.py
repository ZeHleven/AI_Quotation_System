from __future__ import annotations

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .auth import ScopeTokenError
from .contracts import EvidenceRefInput, ToolEnvelope
from .service import TenderCapabilityDeniedError, TenderEvidenceService


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_tender_evidence_mcp(
    service: TenderEvidenceService,
    *,
    token_verifier: TokenVerifier | None = None,
    auth_settings: AuthSettings | None = None,
    host: str = "127.0.0.1",
    port: int = 8012,
) -> FastMCP:
    """Create the protocol server without starting a transport."""

    mcp = FastMCP(
        name="Tender Evidence MCP",
        instructions=(
            "Read-only evidence service for one token-scoped tender case. "
            "Tool arguments never accept case_id. Search returns candidate "
            "snippets; important claims must use read_evidence_context before "
            "the deterministic evidence gate validates references."
        ),
        token_verifier=token_verifier,
        auth=auth_settings,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.resource(
        "tender://current/manifest",
        name="current_tender_manifest",
        title="Current scoped tender document manifest",
        description=(
            "Immutable manifest snapshot for the tender case encoded in the "
            "service token. The URI deliberately contains no case identifier."
        ),
        mime_type="application/json",
    )
    def current_manifest() -> str:
        try:
            return service.get_manifest().model_dump_json()
        except (ScopeTokenError, TenderCapabilityDeniedError) as exc:
            raise ToolError("current token cannot read the tender manifest") from exc
        except Exception as exc:
            raise ToolError("tender manifest is unavailable") from exc

    @mcp.tool(
        name="search_tender_evidence",
        title="Search tender evidence",
        description=(
            "Search normalized evidence blocks inside the token-scoped tender "
            "case. Use concise factual queries. Results are candidates and "
            "have context_read=false until read_evidence_context is called."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def search_tender_evidence(query: str, top_k: int = 5) -> ToolEnvelope:
        try:
            return service.search_tender_evidence(query=query, top_k=top_k)
        except (ScopeTokenError, TenderCapabilityDeniedError) as exc:
            raise ToolError("current token cannot search tender evidence") from exc

    @mcp.tool(
        name="read_evidence_context",
        title="Read evidence context",
        description=(
            "Read a selected evidence block and bounded neighboring blocks. "
            "This records the high-risk context-read trace required by the "
            "evidence gate. before_blocks/after_blocks are clamped to 0..5."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def read_evidence_context(
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> ToolEnvelope:
        try:
            return service.read_evidence_context(
                evidence_id=evidence_id,
                before_blocks=before_blocks,
                after_blocks=after_blocks,
            )
        except (ScopeTokenError, TenderCapabilityDeniedError) as exc:
            raise ToolError("current token cannot read tender evidence") from exc

    @mcp.tool(
        name="compare_document_versions",
        title="Compare tender document versions",
        description=(
            "List all manifest versions for a stable document_key and return "
            "precomputed version conflicts. Use this when an addendum, change "
            "notice, or duplicate tender document may supersede older content."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def compare_document_versions(document_key: str) -> ToolEnvelope:
        try:
            return service.compare_document_versions(document_key=document_key)
        except (ScopeTokenError, TenderCapabilityDeniedError) as exc:
            raise ToolError("current token cannot compare document versions") from exc

    @mcp.tool(
        name="validate_evidence_refs",
        title="Validate evidence references",
        description=(
            "Deterministic gate-only tool. Verify evidence identity, manifest "
            "version, content hash, and whether the same agent run performed "
            "a context read. Do not bind this tool to the ReAct model."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def validate_evidence_refs(
        refs: list[EvidenceRefInput],
        manifest_version: int,
    ) -> ToolEnvelope:
        try:
            return service.validate_evidence_refs(
                refs=refs,
                manifest_version=manifest_version,
            )
        except (ScopeTokenError, TenderCapabilityDeniedError) as exc:
            raise ToolError("current token cannot validate evidence") from exc

    return mcp
