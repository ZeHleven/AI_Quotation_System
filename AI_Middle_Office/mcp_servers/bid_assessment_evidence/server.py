"""Transport factory for the Run-scoped Evidence MCP.

Production wiring must build ``service_factory`` from verified request auth
context.  This module defines the transport contract but never starts a server
at import time.
"""
from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .service import BidEvidenceMcpService


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_bid_assessment_evidence_mcp(
    service_factory: Callable[[], BidEvidenceMcpService],
    *,
    host: str = "127.0.0.1",
    port: int = 8013,
) -> FastMCP:
    server = FastMCP(
        name="Qisheng Bid Assessment Evidence MCP",
        instructions="Read-only, current-Manifest evidence search and bounded context read.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(name="evidence.search", annotations=READ_ONLY, structured_output=True)
    def search(
        query: str,
        top_k: int = 5,
        document_roles: list[str] | None = None,
        document_types: list[str] | None = None,
        document_version_ids: list[str] | None = None,
    ) -> dict:
        return service_factory().search(
            {
                "query": query,
                "top_k": top_k,
                "document_roles": document_roles or [],
                "document_types": document_types or [],
                "document_version_ids": document_version_ids or [],
            }
        )

    @server.tool(name="evidence.read", annotations=READ_ONLY, structured_output=True)
    def read(
        evidence_ids: list[str],
        expansion: str = "none",
        radius: int = 1,
        max_pages: int = 2,
    ) -> dict:
        return service_factory().read(
            {
                "evidence_ids": evidence_ids,
                "expansion": expansion,
                "radius": radius,
                "max_pages": max_pages,
            }
        )

    return server
