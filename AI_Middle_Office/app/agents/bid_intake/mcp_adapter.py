from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from typing import Any, AsyncContextManager, Callable, Protocol, Sequence
from uuid import uuid4

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from .contracts import (
    DocumentManifest,
    EvidenceRef,
    ToolResult,
    ToolResultStatus,
)


class McpInvocationError(RuntimeError):
    pass


class SyncMcpToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def read_json_resource(self, uri: str) -> dict[str, Any]: ...


class StreamableHttpMcpToolCaller:
    """Small synchronous boundary around the official async MCP client.

    This compatibility adapter opens a short-lived session per call. The
    persistent Agent worker uses ``PersistentStreamableHttpMcpToolCaller``.
    """

    def __init__(
        self,
        *,
        url: str,
        bearer_token: str,
        timeout_seconds: float = 20,
    ):
        if not url.strip():
            raise ValueError("MCP url is required")
        if not bearer_token.strip():
            raise ValueError("MCP bearer token is required")
        self._url = url
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return anyio.run(self._call_tool_async, name, arguments)

    def read_json_resource(self, uri: str) -> dict[str, Any]:
        return anyio.run(self._read_json_resource_async, uri)

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._session() as session:
            result = await session.call_tool(
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
            )
        if result.isError:
            raise McpInvocationError(f"MCP tool failed: {name}")
        if result.structuredContent is None:
            raise McpInvocationError(
                f"MCP tool returned no structured content: {name}"
            )
        return result.structuredContent

    async def _read_json_resource_async(self, uri: str) -> dict[str, Any]:
        async with self._session() as session:
            result = await session.read_resource(AnyUrl(uri))
        return _resource_json(result)

    def _session(self):
        return _StreamableHttpSession(
            url=self._url,
            bearer_token=self._bearer_token,
            timeout_seconds=self._timeout_seconds,
        )


class _StreamableHttpSession:
    def __init__(
        self,
        *,
        url: str,
        bearer_token: str,
        timeout_seconds: float,
    ):
        self._url = url
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._http_client: httpx.AsyncClient | None = None
        self._transport_context = None
        self._session_context = None

    async def __aenter__(self) -> ClientSession:
        self._http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._bearer_token}"},
            timeout=self._timeout_seconds,
        )
        self._transport_context = streamable_http_client(
            self._url,
            http_client=self._http_client,
        )
        read_stream, write_stream, _ = await self._transport_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        session = await self._session_context.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(exc_type, exc, traceback)
        if self._transport_context is not None:
            await self._transport_context.__aexit__(exc_type, exc, traceback)
        if self._http_client is not None:
            await self._http_client.aclose()


class PersistentStreamableHttpMcpToolCaller:
    """One official async MCP session shared by all calls in one graph run.

    The current graph deliberately keeps synchronous nodes. A private event
    loop thread bridges those sync tool calls to a single async ClientSession,
    so the worker does not repeat transport initialization on every ReAct step.
    """

    def __init__(
        self,
        *,
        url: str,
        bearer_token: str,
        timeout_seconds: float = 20,
        session_context_factory: (
            Callable[[], AsyncContextManager[ClientSession]] | None
        ) = None,
    ):
        if not url.strip():
            raise ValueError("MCP url is required")
        if not bearer_token.strip():
            raise ValueError("MCP bearer token is required")
        self._url = url
        self._bearer_token = bearer_token
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._session_context_factory = (
            session_context_factory or self._default_session_context
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def __enter__(self) -> "PersistentStreamableHttpMcpToolCaller":
        if self._thread is not None:
            raise RuntimeError("persistent MCP caller is already open")
        self._thread = threading.Thread(
            target=self._thread_main,
            name="bid-intake-mcp-session",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=self._timeout_seconds + 5):
            self.close()
            raise McpInvocationError("MCP session initialization timed out")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise McpInvocationError(
                "MCP session initialization failed"
            ) from error
        if self._session is None:
            self.close()
            raise McpInvocationError("MCP session did not initialize")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._timeout_seconds + 5)
        self._thread = None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._submit(
            self._call_tool_async(name, arguments)
        )
        if result.isError:
            raise McpInvocationError(f"MCP tool failed: {name}")
        if result.structuredContent is None:
            raise McpInvocationError(
                f"MCP tool returned no structured content: {name}"
            )
        return result.structuredContent

    def read_json_resource(self, uri: str) -> dict[str, Any]:
        result = self._submit(self._read_resource_async(uri))
        return _resource_json(result)

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
    ):
        session = self._require_session()
        return await session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
        )

    async def _read_resource_async(self, uri: str):
        return await self._require_session().read_resource(AnyUrl(uri))

    def _submit(self, coroutine):
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise McpInvocationError("persistent MCP session is not open")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=self._timeout_seconds + 5)
        except Exception as exc:
            future.cancel()
            raise McpInvocationError("persistent MCP call failed") from exc

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise McpInvocationError("persistent MCP session is not ready")
        return self._session

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_session())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            self._session = None
            self._stop_event = None
            self._loop = None
            loop.close()

    async def _serve_session(self) -> None:
        self._stop_event = asyncio.Event()
        try:
            async with self._session_context_factory() as session:
                self._session = session
                self._ready.set()
                await self._stop_event.wait()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            raise

    def _default_session_context(self) -> AsyncContextManager[ClientSession]:
        return _StreamableHttpSession(
            url=self._url,
            bearer_token=self._bearer_token,
            timeout_seconds=self._timeout_seconds,
        )


class McpTenderEvidencePort:
    """Maps MCP structured output to the LangGraph evidence port contract."""

    def __init__(self, caller: SyncMcpToolCaller):
        self._caller = caller

    def read_manifest(self) -> DocumentManifest:
        payload = self._caller.read_json_resource("tender://current/manifest")
        # document_key is an MCP-only storage concern and is intentionally
        # removed from the Phase 0 LangGraph manifest contract.
        documents = [
            {
                key: value
                for key, value in item.items()
                if key != "document_key"
            }
            for item in payload.get("documents", [])
        ]
        return DocumentManifest.model_validate(
            {**payload, "documents": documents}
        )

    def search(self, *, query: str, top_k: int) -> ToolResult:
        return self._call(
            "search_tender_evidence",
            {"query": query, "top_k": top_k},
        )

    def read_context(
        self,
        *,
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> ToolResult:
        return self._call(
            "read_evidence_context",
            {
                "evidence_id": evidence_id,
                "before_blocks": before_blocks,
                "after_blocks": after_blocks,
            },
        )

    def compare_versions(self, *, document_key: str) -> ToolResult:
        return self._call(
            "compare_document_versions",
            {"document_key": document_key},
        )

    def validate_refs(
        self,
        *,
        refs: Sequence[EvidenceRef],
        manifest: DocumentManifest,
    ) -> ToolResult:
        arguments = {
            "refs": [
                {
                    "evidence_id": ref.evidence_id,
                    "block_id": ref.block_id,
                    "document_id": ref.document_id,
                    "document_version": ref.document_version,
                    "content_hash": ref.content_hash,
                }
                for ref in refs
            ],
            "manifest_version": manifest.manifest_version,
        }
        return self._call("validate_evidence_refs", arguments)

    def _call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            payload = self._caller.call_tool(name, arguments)
            return ToolResult.model_validate(payload)
        except Exception:
            return ToolResult(
                status=ToolResultStatus.FAILED,
                data=None,
                retryable=True,
                trace_id=f"mcp-client-{uuid4().hex}",
                error_code="mcp_invocation_failed",
                message=f"tender evidence MCP call failed: {name}",
            )


def _resource_json(result: Any) -> dict[str, Any]:
    if not result.contents:
        raise McpInvocationError("MCP resource returned no content")
    first = result.contents[0]
    text = getattr(first, "text", None)
    if not isinstance(text, str):
        raise McpInvocationError("MCP resource did not return JSON text")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise McpInvocationError("MCP resource returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise McpInvocationError("MCP resource JSON must be an object")
    return payload
