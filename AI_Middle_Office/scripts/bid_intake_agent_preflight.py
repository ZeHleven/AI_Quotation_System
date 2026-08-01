from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.mcp_adapter import (  # noqa: E402
    McpTenderEvidencePort,
    PersistentStreamableHttpMcpToolCaller,
)
from app.agents.bid_intake.openai_compatible_model import (  # noqa: E402
    OpenAICompatibleBidAnalysisModel,
)
from app.agents.bid_intake.policy import YamlBidPolicy  # noqa: E402
from app.agents.bid_intake.runtime_config import (  # noqa: E402
    resolve_bid_intake_model_config,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: F401,E402
from app.models.bidding import BidProject  # noqa: E402
from app.services.bid_intake_runtime import (  # noqa: E402
    build_project_runtime_readiness,
    worker_capabilities_from_environment,
)
from mcp_servers.tender_evidence.auth import (  # noqa: E402
    ALL_CAPABILITIES,
    ScopedTokenCodec,
)


def main() -> int:
    _configure_utf8_console()
    parser = argparse.ArgumentParser(
        description="Read-only preflight for one bid-intake Agent project."
    )
    parser.add_argument("--project-uuid", required=True)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Do not connect to MCP; only inspect safe configuration flags.",
    )
    parser.add_argument(
        "--probe-model",
        action="store_true",
        help="Make one explicit model request; this may incur provider cost.",
    )
    args = parser.parse_args()

    project = _project(args.project_uuid)
    report = {
        "project_uuid": project["project_uuid"],
        "project_name": project["project_name"],
        "configuration": worker_capabilities_from_environment(),
        "database_readiness": _database_readiness(project["project_id"]),
        "mcp_probe": {"status": "skipped"},
        "model_probe": {"status": "skipped"},
        "policy_probe": _probe_policy(),
    }
    if not args.config_only:
        report["mcp_probe"] = _probe_mcp(args.project_uuid)
    if args.probe_model:
        report["model_probe"] = _probe_model(args.project_uuid)
    if args.config_only:
        report["ok"] = (
            report["database_readiness"]["ready_to_start"]
            and report["policy_probe"]["status"] == "ok"
        )
    else:
        report["ok"] = (
            report["configuration"]["mcp_configured"]
            and report["configuration"]["model_configured"]
            and report["database_readiness"]["ready_to_start"]
            and report["policy_probe"]["status"] == "ok"
            and report["mcp_probe"]["status"] == "ok"
            and report["model_probe"]["status"] in {"ok", "skipped"}
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _project(project_uuid: str) -> dict:
    db = SessionLocal()
    try:
        project = (
            db.query(BidProject)
            .filter(BidProject.project_uuid == project_uuid)
            .one_or_none()
        )
        if project is None:
            raise RuntimeError("BID_PROJECT_NOT_FOUND")
        return {
            "project_id": project.id,
            "project_uuid": project.project_uuid,
            "project_name": project.project_name,
        }
    finally:
        db.close()


def _database_readiness(project_id: int) -> dict:
    db = SessionLocal()
    try:
        return build_project_runtime_readiness(
            db,
            project_id=project_id,
        )
    finally:
        db.close()


def _probe_mcp(project_uuid: str) -> dict:
    probe_id = f"preflight-{uuid.uuid4()}"
    codec = ScopedTokenCodec(
        secret=_required_env("TENDER_MCP_JWT_SECRET"),
        issuer=os.environ.get(
            "TENDER_MCP_ISSUER",
            "http://127.0.0.1:8012",
        ),
        audience=os.environ.get(
            "TENDER_MCP_AUDIENCE",
            "http://127.0.0.1:8012/mcp",
        ),
    )
    token = codec.issue(
        case_id=project_uuid,
        assessment_id=probe_id,
        agent_run_id=probe_id,
        subject="bid-intake-preflight",
        allowed_tools=set(ALL_CAPABILITIES),
        ttl_seconds=300,
    )
    with PersistentStreamableHttpMcpToolCaller(
        url=os.environ.get(
            "BID_INTAKE_MCP_URL",
            "http://127.0.0.1:8012/mcp",
        ),
        bearer_token=token,
        timeout_seconds=_float_env(
            "BID_INTAKE_MCP_TIMEOUT_SECONDS",
            20,
        ),
    ) as caller:
        manifest = McpTenderEvidencePort(caller).read_manifest()
    if manifest.case_id != project_uuid:
        raise RuntimeError("MCP_CASE_SCOPE_MISMATCH")
    return {
        "status": "ok",
        "case_id": manifest.case_id,
        "manifest_version": manifest.manifest_version,
        "manifest_hash": manifest.manifest_hash,
        "active_document_count": len(manifest.active_documents),
    }


def _probe_model(project_uuid: str) -> dict:
    model_config = resolve_bid_intake_model_config()
    if model_config is None:
        raise RuntimeError(
            "bid-intake model configuration is incomplete"
        )
    model = OpenAICompatibleBidAnalysisModel(
        api_url=model_config.api_url,
        api_key=model_config.api_key,
        model=model_config.model_id,
        timeout_seconds=_float_env(
            "BID_INTAKE_MODEL_TIMEOUT_SECONDS",
            90,
        ),
        temperature=0,
    )
    result = model.invoke(
        [],
        system_prompt=(
            "这是连通性检查。不要研判项目，不要编造证据。"
            "请只回复 JSON：{\"status\":\"ready\"}。"
        ),
        state_view={"case_id": project_uuid, "mode": "preflight"},
    )
    return {
        "status": "ok",
        "model_id": model.model_id,
        "returned_tool_calls": [
            item.get("name") for item in result.tool_calls
        ],
        "content_preview": str(result.content or "")[:200],
    }


def _probe_policy() -> dict:
    policy = YamlBidPolicy.from_active()
    return {
        "status": "ok",
        "policy_version": policy.version,
        "factor_count": len(
            policy.prompt_context["required_policy_factors"]
        ),
    }


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
