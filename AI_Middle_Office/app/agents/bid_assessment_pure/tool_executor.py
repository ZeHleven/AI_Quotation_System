"""ExecutionBinding dispatcher shared by Local and MCP tool bindings."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import BaseModel

from .tool_runtime import BindingExecutionResult, ExecutionDeadline
from .tools import CanonicalToolDefinition, ToolExecutionContext


class ToolBindingError(RuntimeError):
    code = "TOOL_BINDING_ERROR"


class ToolBindingUnavailable(ToolBindingError):
    code = "TOOL_BINDING_UNAVAILABLE"


class ToolDeadlineExceeded(ToolBindingError):
    code = "TOOL_DEADLINE_EXCEEDED"


class LocalToolHandler(Protocol):
    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult: ...


class McpStructuredClient(Protocol):
    async def execute_structured(
        self,
        *,
        remote_tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult: ...


class LocalHandlerRegistry:
    """Trusted handler-id registry; it is not a second Tool-name registry."""

    def __init__(self, handlers: Iterable[tuple[str, LocalToolHandler]] = ()) -> None:
        indexed: dict[str, LocalToolHandler] = {}
        for handler_id, handler in handlers:
            if handler_id in indexed:
                raise ValueError(f"duplicate local handler id: {handler_id}")
            indexed[handler_id] = handler
        self._handlers = MappingProxyType(indexed)

    def resolve(self, handler_id: str) -> LocalToolHandler:
        try:
            return self._handlers[handler_id]
        except KeyError as exc:
            raise ToolBindingUnavailable("local tool handler is unavailable") from exc


class McpClientRegistry:
    """Trusted server-id registry; endpoints and credentials remain outside schemas."""

    def __init__(self, clients: Iterable[tuple[str, McpStructuredClient]] = ()) -> None:
        indexed: dict[str, McpStructuredClient] = {}
        for server_id, client in clients:
            if server_id in indexed:
                raise ValueError(f"duplicate MCP server id: {server_id}")
            indexed[server_id] = client
        self._clients = MappingProxyType(indexed)

    def resolve(self, server_id: str) -> McpStructuredClient:
        try:
            return self._clients[server_id]
        except KeyError as exc:
            raise ToolBindingUnavailable("MCP tool client is unavailable") from exc


class CanonicalToolExecutor:
    def __init__(
        self,
        *,
        local_handlers: LocalHandlerRegistry | None = None,
        mcp_clients: McpClientRegistry | None = None,
    ) -> None:
        self._local_handlers = local_handlers or LocalHandlerRegistry()
        self._mcp_clients = mcp_clients or McpClientRegistry()

    async def execute(
        self,
        *,
        definition: CanonicalToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
        deadline: ExecutionDeadline,
    ) -> BindingExecutionResult:
        remaining = deadline.remaining_seconds()
        if remaining <= 0:
            raise ToolDeadlineExceeded("tool deadline expired before execution")

        binding = definition.execution
        if binding.kind == "disabled":
            raise ToolBindingUnavailable("tool execution binding is disabled")
        if binding.kind == "local":
            handler = self._local_handlers.resolve(binding.handler_id)
            operation = handler.execute(
                definition=definition,
                arguments=arguments,
                context=context,
                deadline=deadline,
            )
        elif binding.kind == "mcp":
            client = self._mcp_clients.resolve(binding.server_id)
            operation = client.execute_structured(
                remote_tool_name=binding.remote_tool_name,
                arguments=arguments.model_dump(mode="json"),
                context=context,
                deadline=deadline,
            )
        else:  # pragma: no cover - discriminated union is already closed
            raise ToolBindingUnavailable("unknown tool execution binding")

        try:
            result = await asyncio.wait_for(operation, timeout=remaining)
        except TimeoutError as exc:
            raise ToolDeadlineExceeded("tool execution deadline exceeded") from exc
        if not isinstance(result, BindingExecutionResult):
            raise ToolBindingError("binding did not return structured content")
        return result

