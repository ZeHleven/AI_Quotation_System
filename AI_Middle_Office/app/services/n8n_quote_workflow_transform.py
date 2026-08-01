from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any


class NoRagWorkflowTransformError(ValueError):
    """Raised when an exported workflow cannot be transformed safely."""


@dataclass(frozen=True)
class NoRagWorkflowTransformReport:
    source_name: str
    candidate_name: str
    source_webhook_path: str
    candidate_webhook_path: str
    removed_rag_node: str
    dify_node: str
    predecessor_nodes: tuple[str, ...]
    successor_nodes: tuple[str, ...]
    node_count_before: int
    node_count_after: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_SERVER_MANAGED_WORKFLOW_KEYS = {
    "id",
    "createdAt",
    "updatedAt",
    "versionId",
    "activeVersionId",
    "activeVersion",
    "shared",
    "triggerCount",
}


def build_no_rag_quote_candidate(
    exported_payload: Any,
    *,
    source_webhook_path: str = "budget-calc",
    candidate_webhook_path: str = "budget-calc-no-rag",
    candidate_name_suffix: str = "【no-RAG候选】",
) -> tuple[dict[str, Any], NoRagWorkflowTransformReport]:
    """Create an inactive, importable no-RAG clone from an n8n export."""
    if not source_webhook_path.strip():
        raise NoRagWorkflowTransformError("source_webhook_path 不能为空")
    if not candidate_webhook_path.strip():
        raise NoRagWorkflowTransformError("candidate_webhook_path 不能为空")
    if source_webhook_path == candidate_webhook_path:
        raise NoRagWorkflowTransformError("候选 Webhook path 必须与现有 path 不同")

    source = _select_workflow(exported_payload, source_webhook_path)
    workflow = _materialize_importable_workflow(source)
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not nodes:
        raise NoRagWorkflowTransformError("工作流缺少 nodes")
    if not isinstance(connections, dict):
        raise NoRagWorkflowTransformError("工作流缺少 connections")

    source_name = str(workflow.get("name") or "budget-calc")
    candidate_name = f"{source_name}{candidate_name_suffix}"
    node_count_before = len(nodes)

    rag_nodes = [node for node in nodes if _is_rag_retrieve_node(node)]
    if len(rag_nodes) != 1:
        raise NoRagWorkflowTransformError(
            f"要求恰好一个 /api/v1/retrieve 节点，实际找到 {len(rag_nodes)} 个"
        )
    rag_node = rag_nodes[0]
    rag_node_name = _node_name(rag_node)

    dify_nodes = [node for node in nodes if _is_dify_workflow_node(node)]
    if len(dify_nodes) != 1:
        raise NoRagWorkflowTransformError(
            f"要求恰好一个包含 strict_pricing_json 的 Dify workflow 节点，实际找到 {len(dify_nodes)} 个"
        )
    dify_node = dify_nodes[0]
    dify_node_name = _node_name(dify_node)

    predecessor_names, successor_edges = _bypass_node(connections, rag_node_name)
    successor_names = tuple(dict.fromkeys(_edge_target(edge) for edge in successor_edges))
    if dify_node_name not in successor_names:
        raise NoRagWorkflowTransformError(
            f"RAG 节点后继不是 Dify 节点：successors={list(successor_names)}"
        )

    _replace_strict_pricing_json_with_empty_array(dify_node)
    _replace_webhook_path(nodes, source_webhook_path, candidate_webhook_path)

    workflow["nodes"] = [node for node in nodes if _node_name(node) != rag_node_name]
    workflow["connections"] = connections
    workflow["name"] = candidate_name
    workflow["active"] = False
    workflow["tags"] = []

    for key in _SERVER_MANAGED_WORKFLOW_KEYS:
        workflow.pop(key, None)
    for node in workflow["nodes"]:
        if isinstance(node, dict):
            node.pop("webhookId", None)

    _validate_candidate(
        workflow,
        candidate_webhook_path=candidate_webhook_path,
        removed_rag_node=rag_node_name,
        dify_node_name=dify_node_name,
    )

    report = NoRagWorkflowTransformReport(
        source_name=source_name,
        candidate_name=candidate_name,
        source_webhook_path=source_webhook_path,
        candidate_webhook_path=candidate_webhook_path,
        removed_rag_node=rag_node_name,
        dify_node=dify_node_name,
        predecessor_nodes=tuple(predecessor_names),
        successor_nodes=successor_names,
        node_count_before=node_count_before,
        node_count_after=len(workflow["nodes"]),
    )
    return workflow, report


def _select_workflow(payload: Any, webhook_path: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, list):
        candidates.extend(item for item in payload if isinstance(item, dict))
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            candidates.extend(item for item in data if isinstance(item, dict))
        if isinstance(payload.get("nodes"), list) or isinstance(payload.get("activeVersion"), dict):
            candidates.append(payload)
    else:
        raise NoRagWorkflowTransformError("不支持的 n8n 导出 JSON 根结构")

    matches = [item for item in candidates if webhook_path in _workflow_webhook_paths(item)]
    if len(matches) != 1:
        raise NoRagWorkflowTransformError(
            f"要求恰好一个 webhook path={webhook_path!r} 的工作流，实际找到 {len(matches)} 个"
        )
    return copy.deepcopy(matches[0])


def _materialize_importable_workflow(source: dict[str, Any]) -> dict[str, Any]:
    workflow = copy.deepcopy(source)
    if isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("connections"), dict):
        return workflow

    active_version = workflow.get("activeVersion")
    if not isinstance(active_version, dict):
        return workflow
    workflow["nodes"] = copy.deepcopy(active_version.get("nodes") or [])
    workflow["connections"] = copy.deepcopy(active_version.get("connections") or {})
    for key in ("settings", "staticData", "meta", "pinData"):
        if key not in workflow and key in active_version:
            workflow[key] = copy.deepcopy(active_version[key])
    return workflow


def _workflow_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        return [node for node in nodes if isinstance(node, dict)]
    active_version = workflow.get("activeVersion")
    if isinstance(active_version, dict) and isinstance(active_version.get("nodes"), list):
        return [node for node in active_version["nodes"] if isinstance(node, dict)]
    return []


def _workflow_webhook_paths(workflow: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for node in _workflow_nodes(workflow):
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        parameters = node.get("parameters")
        if not isinstance(parameters, dict):
            continue
        path = parameters.get("path")
        if isinstance(path, str) and path:
            paths.add(path)
    return paths


def _node_name(node: dict[str, Any]) -> str:
    name = node.get("name")
    if not isinstance(name, str) or not name:
        raise NoRagWorkflowTransformError("发现缺少 name 的节点")
    return name


def _node_url(node: dict[str, Any]) -> str:
    parameters = node.get("parameters")
    if not isinstance(parameters, dict):
        return ""
    url = parameters.get("url")
    return url if isinstance(url, str) else ""


def _is_rag_retrieve_node(node: dict[str, Any]) -> bool:
    return "/api/v1/retrieve" in _node_url(node)


def _is_dify_workflow_node(node: dict[str, Any]) -> bool:
    if "/v1/workflows/run" not in _node_url(node):
        return False
    parameters = node.get("parameters")
    if not isinstance(parameters, dict):
        return False
    body = parameters.get("jsonBody")
    if isinstance(body, str):
        return "strict_pricing_json" in body
    return _contains_key(body, "strict_pricing_json")


def _contains_key(value: Any, target_key: str) -> bool:
    if isinstance(value, dict):
        return target_key in value or any(_contains_key(item, target_key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target_key) for item in value)
    return False


def _edge_target(edge: dict[str, Any]) -> str:
    target = edge.get("node")
    if not isinstance(target, str) or not target:
        raise NoRagWorkflowTransformError(f"发现无效连接边：{edge!r}")
    return target


def _main_successor_edges(connections: dict[str, Any], node_name: str) -> list[dict[str, Any]]:
    config = connections.get(node_name)
    if not isinstance(config, dict):
        raise NoRagWorkflowTransformError(f"节点 {node_name!r} 没有连接配置")
    unsupported_channels = [key for key, value in config.items() if key != "main" and value]
    if unsupported_channels:
        raise NoRagWorkflowTransformError(
            f"节点 {node_name!r} 存在不支持的连接通道：{unsupported_channels}"
        )
    branches = config.get("main")
    if not isinstance(branches, list):
        raise NoRagWorkflowTransformError(f"节点 {node_name!r} 缺少 main 后继")

    edges: list[dict[str, Any]] = []
    for branch in branches:
        if not isinstance(branch, list):
            continue
        for edge in branch:
            if isinstance(edge, dict):
                edges.append(copy.deepcopy(edge))
    if not edges:
        raise NoRagWorkflowTransformError(f"节点 {node_name!r} 没有后继节点")
    return edges


def _bypass_node(
    connections: dict[str, Any],
    removed_node_name: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    successor_edges = _main_successor_edges(connections, removed_node_name)
    predecessor_names: list[str] = []

    for source_name, config in list(connections.items()):
        if source_name == removed_node_name or not isinstance(config, dict):
            continue
        for channel_name, branches in config.items():
            if not isinstance(branches, list):
                continue
            for branch_index, branch in enumerate(branches):
                if not isinstance(branch, list):
                    continue
                replaced = False
                new_branch: list[Any] = []
                for edge in branch:
                    if isinstance(edge, dict) and edge.get("node") == removed_node_name:
                        if channel_name != "main":
                            raise NoRagWorkflowTransformError(
                                f"RAG 前驱 {source_name!r} 使用了非 main 连接通道"
                            )
                        new_branch.extend(copy.deepcopy(successor_edges))
                        replaced = True
                    else:
                        new_branch.append(edge)
                if replaced:
                    predecessor_names.append(source_name)
                    branches[branch_index] = _deduplicate_edges(new_branch)

    if not predecessor_names:
        raise NoRagWorkflowTransformError(f"没有找到 RAG 节点 {removed_node_name!r} 的前驱")
    connections.pop(removed_node_name, None)
    return list(dict.fromkeys(predecessor_names)), successor_edges


def _deduplicate_edges(edges: list[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for edge in edges:
        marker = json.dumps(edge, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(edge)
    return unique


def _replace_strict_pricing_json_with_empty_array(node: dict[str, Any]) -> None:
    parameters = node.get("parameters")
    if not isinstance(parameters, dict):
        raise NoRagWorkflowTransformError("Dify 节点缺少 parameters")
    body = parameters.get("jsonBody")

    if isinstance(body, str):
        lines = body.splitlines()
        matched_indexes = [
            index
            for index, line in enumerate(lines)
            if "strict_pricing_json" in line and ":" in line
        ]
        if len(matched_indexes) != 1:
            raise NoRagWorkflowTransformError(
                f"Dify jsonBody 中 strict_pricing_json 行数应为 1，实际为 {len(matched_indexes)}"
            )
        index = matched_indexes[0]
        original = lines[index]
        colon_index = original.index(":")
        suffix = "," if original.rstrip().endswith(",") else ""
        lines[index] = f"{original[: colon_index + 1]} JSON.stringify([]){suffix}"
        parameters["jsonBody"] = "\n".join(lines)
        return

    replaced = _replace_nested_key(body, "strict_pricing_json", "[]")
    if replaced != 1:
        raise NoRagWorkflowTransformError(
            f"Dify jsonBody 中 strict_pricing_json 字段数应为 1，实际为 {replaced}"
        )


def _replace_nested_key(value: Any, target_key: str, replacement: Any) -> int:
    replaced = 0
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key == target_key:
                value[key] = replacement
                replaced += 1
            else:
                replaced += _replace_nested_key(value[key], target_key, replacement)
    elif isinstance(value, list):
        for item in value:
            replaced += _replace_nested_key(item, target_key, replacement)
    return replaced


def _replace_webhook_path(nodes: list[dict[str, Any]], source_path: str, candidate_path: str) -> None:
    matched = 0
    for node in nodes:
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        parameters = node.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("path") != source_path:
            continue
        parameters["path"] = candidate_path
        matched += 1
    if matched != 1:
        raise NoRagWorkflowTransformError(
            f"待替换 Webhook path={source_path!r} 的节点数应为 1，实际为 {matched}"
        )


def _validate_candidate(
    workflow: dict[str, Any],
    *,
    candidate_webhook_path: str,
    removed_rag_node: str,
    dify_node_name: str,
) -> None:
    if workflow.get("active") is not False:
        raise NoRagWorkflowTransformError("候选工作流必须保持 inactive")

    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        raise NoRagWorkflowTransformError("候选工作流结构不完整")

    node_names = {_node_name(node) for node in nodes if isinstance(node, dict)}
    if removed_rag_node in node_names:
        raise NoRagWorkflowTransformError("候选工作流仍包含 RAG 节点")
    if any(_is_rag_retrieve_node(node) for node in nodes if isinstance(node, dict)):
        raise NoRagWorkflowTransformError("候选工作流仍包含 /api/v1/retrieve 调用")
    if candidate_webhook_path not in _workflow_webhook_paths(workflow):
        raise NoRagWorkflowTransformError("候选 Webhook path 未生效")
    if dify_node_name not in node_names:
        raise NoRagWorkflowTransformError("候选工作流缺少 Dify 节点")

    dify_node = next(node for node in nodes if _node_name(node) == dify_node_name)
    body = (dify_node.get("parameters") or {}).get("jsonBody")
    if isinstance(body, str) and "strict_pricing_json" in body and "JSON.stringify([])" not in body:
        raise NoRagWorkflowTransformError("strict_pricing_json 未替换为兼容空数组")

    for source_name, config in connections.items():
        if source_name not in node_names:
            raise NoRagWorkflowTransformError(f"connections 包含不存在的源节点：{source_name}")
        if not isinstance(config, dict):
            continue
        for branches in config.values():
            if not isinstance(branches, list):
                continue
            for branch in branches:
                if not isinstance(branch, list):
                    continue
                for edge in branch:
                    if not isinstance(edge, dict):
                        continue
                    target = _edge_target(edge)
                    if target not in node_names:
                        raise NoRagWorkflowTransformError(f"connections 包含不存在的目标节点：{target}")
                    if target == removed_rag_node:
                        raise NoRagWorkflowTransformError("connections 仍引用已移除的 RAG 节点")

    respond_nodes = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "n8n-nodes-base.respondToWebhook"
    ]
    if not respond_nodes:
        raise NoRagWorkflowTransformError("候选工作流缺少 Respond to Webhook 节点")
