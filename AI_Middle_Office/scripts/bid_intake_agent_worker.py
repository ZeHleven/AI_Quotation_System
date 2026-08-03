from __future__ import annotations

import argparse
import atexit
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.mcp_adapter import (  # noqa: E402
    McpTenderEvidencePort,
    PersistentStreamableHttpMcpToolCaller,
)
from app.agents.bid_intake.contracts import (  # noqa: E402
    FactCoverageMode,
)
from app.agents.bid_intake.openai_compatible_model import (  # noqa: E402
    FailoverBidAnalysisModel,
    OpenAICompatibleBidAnalysisModel,
)
from app.agents.bid_intake.persistent_executor import (  # noqa: E402
    PersistentBidIntakeExecutor,
)
from app.agents.bid_intake.policy import YamlBidPolicy  # noqa: E402
from app.agents.bid_intake.ports import AgentBudgets, AgentRuntime  # noqa: E402
from app.agents.bid_intake.runtime_config import (  # noqa: E402
    resolve_bid_intake_fallback_model_config,
    resolve_bid_intake_model_config,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: F401,E402
from app.models.bid_intake_runtime import (  # noqa: E402
    BidIntakeAgentRun,
    BidIntakeAssessment,
)
from app.models.bidding import BidProject  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.bid_intake_runtime import claim_agent_run  # noqa: E402
from app.services.bid_intake_runtime import (  # noqa: E402
    fail_claimed_agent_run,
    touch_worker_heartbeat,
    worker_capabilities_from_environment,
)
from mcp_servers.tender_evidence.auth import (  # noqa: E402
    ALL_CAPABILITIES,
    ScopedTokenCodec,
)


def main() -> int:
    _configure_utf8_console()
    parser = argparse.ArgumentParser(
        description=(
            "Run the dedicated persistent worker for the bid-intake "
            "LangGraph Agent."
        )
    )
    parser.add_argument("--run-uuid")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one run and exit.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("BID_INTAKE_AGENT_POLL_SECONDS", "3")),
    )
    parser.add_argument(
        "--worker-id",
        default=f"{socket.gethostname()}:{os.getpid()}",
    )
    parser.add_argument(
        "--pid-file",
        type=Path,
        help="Write the actual Python Worker PID for safe Windows restarts.",
    )
    args = parser.parse_args()

    if args.pid_file is not None:
        _write_worker_pid_file(args.pid_file)
        atexit.register(_remove_worker_pid_file, args.pid_file)
    if not _bool_env("BID_INTAKE_AGENT_RUNTIME_ENABLED", False):
        raise SystemExit("BID_INTAKE_AGENT_RUNTIME_ENABLED must be true")
    if _bool_env("BID_INTAKE_MCP_PREFLIGHT_ENABLED", True):
        _preflight_mcp_authentication()
    capabilities = worker_capabilities_from_environment()
    _heartbeat(
        worker_id=args.worker_id,
        status="online",
        capabilities=capabilities,
    )
    try:
        while True:
            _heartbeat(
                worker_id=args.worker_id,
                status="online",
                capabilities=capabilities,
            )
            claim = _claim_one(
                worker_id=args.worker_id,
                run_uuid=args.run_uuid,
            )
            if claim is None:
                if args.once or args.run_uuid:
                    return 0 if not args.run_uuid else 2
                time.sleep(max(0.2, min(args.poll_seconds, 60)))
                continue
            try:
                result = _execute_claim_with_heartbeat(
                    run_uuid=claim.run_uuid,
                    lease_token=claim.lease_token,
                    worker_id=args.worker_id,
                    capabilities=capabilities,
                )
                print(
                    f"run={result['run_uuid']} status={result['status']} "
                    f"phase={result['phase']} "
                    f"checkpoint={result['checkpoint_id']}"
                )
            except Exception as exc:
                _fail_claimed_run(
                    run_uuid=claim.run_uuid,
                    lease_token=claim.lease_token,
                    exc=exc,
                )
                _heartbeat(
                    worker_id=args.worker_id,
                    status="error",
                    capabilities=capabilities,
                    error_message=str(exc),
                )
                print(
                    f"run={claim.run_uuid} failed: {exc}",
                    file=sys.stderr,
                )
                if args.run_uuid:
                    return 1
            if args.once or args.run_uuid:
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        _heartbeat(
            worker_id=args.worker_id,
            status="stopped",
            capabilities=capabilities,
        )


def _claim_one(*, worker_id: str, run_uuid: str | None):
    db = SessionLocal()
    try:
        claim = claim_agent_run(
            db,
            worker_id=worker_id,
            run_uuid=run_uuid,
            lease_seconds=_int_env(
                "BID_INTAKE_AGENT_LEASE_SECONDS",
                3600,
            ),
        )
        db.commit()
        return claim
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _heartbeat(
    *,
    worker_id: str,
    status: str,
    capabilities: dict,
    current_run_uuid: str | None = None,
    error_message: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        touch_worker_heartbeat(
            db,
            worker_id=worker_id,
            status=status,
            process_id=os.getpid(),
            hostname=socket.gethostname(),
            current_run_uuid=current_run_uuid,
            capabilities=capabilities,
            error_message=error_message,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _write_worker_pid_file(path: Path) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(str(os.getpid()), encoding="ascii")


def _remove_worker_pid_file(path: Path) -> None:
    resolved = path.expanduser().resolve()
    try:
        if (
            resolved.exists()
            and resolved.read_text(encoding="ascii").strip()
            == str(os.getpid())
        ):
            resolved.unlink()
    except OSError:
        pass


def _mcp_codec() -> ScopedTokenCodec:
    return ScopedTokenCodec(
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


def _preflight_mcp_authentication() -> None:
    codec = _mcp_codec()
    token = codec.issue(
        case_id="bid-intake-startup-preflight",
        assessment_id="bid-intake-startup-preflight",
        agent_run_id=f"startup-{uuid.uuid4()}",
        subject=f"bid-intake-worker-preflight:{socket.gethostname()}",
        allowed_tools=set(ALL_CAPABILITIES),
        ttl_seconds=60,
    )
    try:
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
        ):
            pass
    except Exception as exc:
        raise RuntimeError(
            "Tender Evidence MCP authentication preflight failed"
        ) from exc


def _fail_claimed_run(
    *,
    run_uuid: str,
    lease_token: str,
    exc: Exception,
) -> None:
    message = str(exc) or type(exc).__name__
    lowered = message.casefold()
    if "mcp session initialization" in lowered:
        error_code = "MCP_SESSION_INITIALIZATION_FAILED"
    elif "mcp" in lowered:
        error_code = "MCP_RUNTIME_FAILED"
    elif "model" in lowered:
        error_code = "MODEL_RUNTIME_CONFIGURATION_FAILED"
    else:
        error_code = "AGENT_PRE_EXECUTION_FAILED"
    db = SessionLocal()
    try:
        fail_claimed_agent_run(
            db,
            run_uuid=run_uuid,
            lease_token=lease_token,
            error_code=error_code,
            error_message=message,
        )
        db.commit()
    except Exception as persist_exc:
        db.rollback()
        print(
            f"run={run_uuid} failure persistence failed: {persist_exc}",
            file=sys.stderr,
        )
    finally:
        db.close()


def _execute_claim(*, run_uuid: str, lease_token: str):
    context = _run_context(run_uuid)
    codec = _mcp_codec()
    token = codec.issue(
        case_id=context["project_uuid"],
        assessment_id=context["assessment_uuid"],
        agent_run_id=run_uuid,
        subject=f"bid-intake-worker:{context['created_by_name']}",
        allowed_tools=set(ALL_CAPABILITIES),
        ttl_seconds=min(
            3600,
            max(60, _int_env("BID_INTAKE_MCP_TOKEN_TTL_SECONDS", 3600)),
        ),
    )
    model_config = resolve_bid_intake_model_config()
    if model_config is None:
        raise RuntimeError(
            "配置完整的 BID_INTAKE_MODEL_*，或配置现有 "
            "DEEPSEEK_API_KEY + DEEPSEEK_CHAT_URL/DEEPSEEK_MODEL"
        )
    primary_model = OpenAICompatibleBidAnalysisModel(
        api_url=model_config.api_url,
        api_key=model_config.api_key,
        model=model_config.model_id,
        timeout_seconds=_float_env(
            "BID_INTAKE_MODEL_TIMEOUT_SECONDS",
            90,
        ),
        temperature=_float_env(
            "BID_INTAKE_MODEL_TEMPERATURE",
            0.1,
        ),
    )
    fallback_config = resolve_bid_intake_fallback_model_config(
        model_config
    )
    fallback_model = (
        OpenAICompatibleBidAnalysisModel(
            api_url=fallback_config.api_url,
            api_key=fallback_config.api_key,
            model=fallback_config.model_id,
            timeout_seconds=_float_env(
                "BID_INTAKE_FALLBACK_MODEL_TIMEOUT_SECONDS",
                _float_env(
                    "BID_INTAKE_MODEL_TIMEOUT_SECONDS",
                    90,
                ),
            ),
            temperature=_float_env(
                "BID_INTAKE_MODEL_TEMPERATURE",
                0.1,
            ),
        )
        if fallback_config is not None
        else None
    )
    model = FailoverBidAnalysisModel(
        primary=primary_model,
        fallback=fallback_model,
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
        evidence = McpTenderEvidencePort(caller)
        manifest = evidence.read_manifest()
        runtime = AgentRuntime(
            model=model,
            evidence=evidence,
            policy=YamlBidPolicy.from_version(
                context["policy_version"]
            ),
            budgets=AgentBudgets(
                max_reasoning_loops=_int_env(
                    "BID_INTAKE_MAX_REASONING_LOOPS",
                    8,
                ),
                max_tool_calls=_int_env(
                    "BID_INTAKE_MAX_TOOL_CALLS",
                    24,
                ),
                max_tool_calls_per_turn=_int_env(
                    "BID_INTAKE_MAX_TOOL_CALLS_PER_TURN",
                    3,
                ),
                max_same_tool_args=_int_env(
                    "BID_INTAKE_MAX_SAME_TOOL_ARGS",
                    2,
                ),
                max_output_repairs=_int_env(
                    "BID_INTAKE_MAX_OUTPUT_REPAIRS",
                    1,
                ),
                max_gate_repairs=_int_env(
                    "BID_INTAKE_MAX_GATE_REPAIRS",
                    1,
                ),
            ),
            fact_coverage_mode=_fact_coverage_mode(),
        )
        return PersistentBidIntakeExecutor(SessionLocal).execute(
            run_uuid=run_uuid,
            lease_token=lease_token,
            runtime=runtime,
            manifest=manifest,
        )


def _execute_claim_with_heartbeat(
    *,
    run_uuid: str,
    lease_token: str,
    worker_id: str,
    capabilities: dict,
):
    """Keep a busy Worker visible during long model and MCP calls."""

    _heartbeat(
        worker_id=worker_id,
        status="busy",
        capabilities=capabilities,
        current_run_uuid=run_uuid,
    )
    stop_event = threading.Event()
    interval_seconds = max(
        0.5,
        min(
            _float_env("BID_INTAKE_WORKER_HEARTBEAT_SECONDS", 10),
            60,
        ),
    )

    def heartbeat_loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                _heartbeat(
                    worker_id=worker_id,
                    status="busy",
                    capabilities=capabilities,
                    current_run_uuid=run_uuid,
                )
            except Exception as exc:
                print(
                    f"worker heartbeat refresh failed: {exc}",
                    file=sys.stderr,
                )

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name=f"bid-intake-heartbeat:{worker_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        return _execute_claim(
            run_uuid=run_uuid,
            lease_token=lease_token,
        )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=interval_seconds + 1)


def _run_context(run_uuid: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        run = (
            db.query(BidIntakeAgentRun)
            .filter(BidIntakeAgentRun.run_uuid == run_uuid)
            .one()
        )
        assessment = (
            db.query(BidIntakeAssessment)
            .filter(BidIntakeAssessment.id == run.assessment_id)
            .one()
        )
        project = (
            db.query(BidProject)
            .filter(BidProject.id == run.project_id)
            .one()
        )
        creator = (
            db.query(User)
            .filter(User.id == run.created_by)
            .one()
        )
        return {
            "assessment_uuid": assessment.assessment_uuid,
            "project_uuid": project.project_uuid,
            "created_by_name": str(creator.username or creator.id),
            "policy_version": assessment.policy_version,
        }
    finally:
        db.close()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _fact_coverage_mode() -> FactCoverageMode:
    raw = os.environ.get(
        "BID_INTAKE_FACT_COVERAGE_MODE",
        FactCoverageMode.SHADOW.value,
    ).strip().lower()
    try:
        return FactCoverageMode(raw)
    except ValueError:
        return FactCoverageMode.SHADOW


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
