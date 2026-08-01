from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from mcp.server.auth.settings import AuthSettings  # noqa: E402

from mcp_servers.tender_evidence.auth import (  # noqa: E402
    EnvironmentScopeProvider,
    McpAccessTokenScopeProvider,
    ScopedTokenCodec,
    ScopedTokenVerifier,
)
from mcp_servers.tender_evidence.local_repository import (  # noqa: E402
    LocalTenderEvidenceRepository,
)
from mcp_servers.tender_evidence.server import (  # noqa: E402
    create_tender_evidence_mcp,
)
from mcp_servers.tender_evidence.service import TenderEvidenceService  # noqa: E402


DEFAULT_DATASET = (
    PROJECT_DIR
    / "mcp_servers"
    / "tender_evidence"
    / "fixtures"
    / "demo_cases.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the project-scoped tender evidence MCP server."
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument(
        "--repository",
        choices=["local", "database"],
        default="local",
        help="Use the Phase 1 JSON fixture or Phase 2 SQLAlchemy evidence store.",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--max-lexical-candidates", type=int, default=2000)
    parser.add_argument(
        "--candidate-top-k",
        type=int,
        default=int(
            os.environ.get(
                "TENDER_EVIDENCE_CANDIDATE_TOP_K",
                "20",
            )
        ),
        help=(
            "Per-query candidate depth before RRF. "
            "The MCP tool's requested top_k remains the final result limit."
        ),
    )
    parser.add_argument(
        "--candidate-coverage-selection",
        action=argparse.BooleanOptionalAction,
        default=_environment_bool(
            "TENDER_EVIDENCE_CANDIDATE_COVERAGE_SELECTION",
            False,
        ),
        help=(
            "Use generic answer-bearing need coverage when selecting final "
            "Top K from the existing candidate pool. No extra retrieval "
            "queries are executed."
        ),
    )
    parser.add_argument(
        "--candidate-coverage-policy",
        choices=(
            "greedy",
            "anchor_preserving_direct_alignment",
            "predicate_aware_marginal_gain",
        ),
        default=os.environ.get(
            "TENDER_EVIDENCE_CANDIDATE_COVERAGE_POLICY",
            "greedy",
        ).strip(),
        help=(
            "Choose greedy, anchor-preserving direct-alignment, or "
            "predicate-aware marginal-gain coverage selection. The "
            "coverage-selection feature flag still controls whether "
            "any policy is active."
        ),
    )
    parser.add_argument(
        "--evidence-sufficiency-assessment",
        action=argparse.BooleanOptionalAction,
        default=_environment_bool(
            "TENDER_EVIDENCE_SUFFICIENCY_ASSESSMENT",
            False,
        ),
        help=(
            "Assess direct subject-relation-answer coverage on the "
            "unchanged final evidence set. This adds audit state only "
            "and executes no retrieval query or Top-K promotion."
        ),
    )
    parser.add_argument(
        "--structured-context-groups",
        action=argparse.BooleanOptionalAction,
        default=_environment_bool(
            "TENDER_EVIDENCE_STRUCTURED_CONTEXT_GROUPS",
            False,
        ),
        help=(
            "Attach verified section/table/sheet parent evidence to "
            "retrieval candidates without adding search queries."
        ),
    )
    parser.add_argument(
        "--controlled-second-round",
        action=argparse.BooleanOptionalAction,
        default=_environment_bool(
            "TENDER_EVIDENCE_CONTROLLED_SECOND_ROUND",
            False,
        ),
        help=(
            "Run at most one bounded retry round for fact needs that remain "
            "uncovered after a partially successful first round."
        ),
    )
    parser.add_argument(
        "--selective-graph-expansion",
        action=argparse.BooleanOptionalAction,
        default=_environment_bool(
            "TENDER_EVIDENCE_SELECTIVE_GRAPH_EXPANSION",
            False,
        ),
        help=(
            "Attach bounded one-hop verified relation targets only when "
            "the strict multi-document relation trigger is satisfied."
        ),
    )
    parser.add_argument(
        "--search-mode",
        choices=["auto", "lexical", "hybrid"],
        default="auto",
        help=(
            "Database repository search mode. auto enables hybrid search only "
            "when TENDER_EVIDENCE_HYBRID_ENABLED=true."
        ),
    )
    args = parser.parse_args()

    secret = _required_environment("TENDER_MCP_JWT_SECRET")
    issuer = os.environ.get(
        "TENDER_MCP_ISSUER",
        f"http://{args.host}:{args.port}",
    ).strip()
    audience = os.environ.get(
        "TENDER_MCP_AUDIENCE",
        f"http://{args.host}:{args.port}/mcp",
    ).strip()
    codec = ScopedTokenCodec(
        secret=secret,
        issuer=issuer,
        audience=audience,
    )
    if args.repository == "database":
        from app.core.database import SessionLocal
        from mcp_servers.tender_evidence.sqlalchemy_repository import (
            SqlAlchemyTenderEvidenceRepository,
        )
        from mcp_servers.tender_evidence.hybrid_client import (
            configured_hybrid_client,
            hybrid_search_enabled,
        )

        use_hybrid = args.search_mode == "hybrid" or (
            args.search_mode == "auto" and hybrid_search_enabled()
        )
        repository = SqlAlchemyTenderEvidenceRepository(
            SessionLocal,
            max_lexical_candidates=args.max_lexical_candidates,
            hybrid_search_client=(
                configured_hybrid_client() if use_hybrid else None
            ),
        )
    else:
        repository = LocalTenderEvidenceRepository(args.dataset)

    if args.transport == "stdio":
        scope_provider = EnvironmentScopeProvider(codec)
        token_verifier = None
        auth_settings = None
    else:
        scope_provider = McpAccessTokenScopeProvider()
        token_verifier = ScopedTokenVerifier(codec)
        auth_settings = AuthSettings(
            issuer_url=issuer,
            resource_server_url=audience,
            required_scopes=None,
        )

    service = TenderEvidenceService(
        repository,
        scope_provider=scope_provider,
        per_query_candidate_top_k=args.candidate_top_k,
        enable_candidate_coverage_selection=(
            args.candidate_coverage_selection
        ),
        enable_evidence_sufficiency_assessment=(
            args.evidence_sufficiency_assessment
        ),
        candidate_coverage_selection_policy=(
            args.candidate_coverage_policy
        ),
        enable_structured_context_groups=(
            args.structured_context_groups
        ),
        enable_controlled_second_round=(
            args.controlled_second_round
        ),
        enable_selective_graph_expansion=(
            args.selective_graph_expansion
        ),
    )
    mcp = create_tender_evidence_mcp(
        service,
        token_verifier=token_verifier,
        auth_settings=auth_settings,
        host=args.host,
        port=args.port,
    )
    mcp.run(transport=args.transport)
    return 0


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _environment_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
