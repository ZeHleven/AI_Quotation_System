from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from mcp_servers.tender_evidence.auth import (  # noqa: E402
    ALL_CAPABILITIES,
    ScopedTokenCodec,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Issue a short-lived local tender evidence service token."
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--assessment-id", required=True)
    parser.add_argument("--agent-run-id", required=True)
    parser.add_argument("--subject", default="local-bid-intake-agent")
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument(
        "--capability",
        action="append",
        choices=sorted(ALL_CAPABILITIES),
        dest="capabilities",
        help="Repeat to restrict capabilities; default allows all read-only capabilities.",
    )
    args = parser.parse_args()

    codec = ScopedTokenCodec(
        secret=_required_environment("TENDER_MCP_JWT_SECRET"),
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
        case_id=args.case_id,
        assessment_id=args.assessment_id,
        agent_run_id=args.agent_run_id,
        subject=args.subject,
        allowed_tools=set(args.capabilities or ALL_CAPABILITIES),
        ttl_seconds=args.ttl_seconds,
    )
    print(token)
    return 0


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
