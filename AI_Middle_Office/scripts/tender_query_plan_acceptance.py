from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_DIR / ".env")

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.models.tender_evidence import BidEvidenceManifest  # noqa: E402
from app.models.tender_evidence_index import BidEvidenceIndexJob  # noqa: E402
from mcp_servers.tender_evidence.auth import TenderScope  # noqa: E402
from mcp_servers.tender_evidence.contracts import ResultStatus  # noqa: E402
from mcp_servers.tender_evidence.hybrid_client import (  # noqa: E402
    configured_hybrid_client,
)
from mcp_servers.tender_evidence.service import (  # noqa: E402
    TenderEvidenceService,
)
from mcp_servers.tender_evidence.sqlalchemy_repository import (  # noqa: E402
    SqlAlchemyTenderEvidenceRepository,
)


DEFAULT_QUERY = "项目的付款条件、工期风险和投标保证金分别是什么？"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one real compound-query acceptance against tender RAG."
    )
    parser.add_argument("--project-uuid")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    db = SessionLocal()
    try:
        query = (
            db.query(BidProject, BidEvidenceManifest, BidEvidenceIndexJob)
            .join(
                BidEvidenceManifest,
                BidEvidenceManifest.project_id == BidProject.id,
            )
            .join(
                BidEvidenceIndexJob,
                BidEvidenceIndexJob.manifest_id == BidEvidenceManifest.id,
            )
            .filter(
                BidEvidenceManifest.active.is_(True),
                BidEvidenceIndexJob.status == "completed",
                BidEvidenceIndexJob.indexed_block_count
                == BidEvidenceIndexJob.requested_block_count,
            )
        )
        if args.project_uuid:
            query = query.filter(
                BidProject.project_uuid == args.project_uuid
            )
        row = query.order_by(
            BidEvidenceIndexJob.requested_block_count.desc()
        ).first()
        if row is None:
            raise RuntimeError(
                "no completed active tender evidence index is available"
            )
        project, manifest, index_job = row
        project_uuid = project.project_uuid
        project_name = project.project_name
        indexed_block_count = index_job.indexed_block_count
    finally:
        db.close()

    now = int(time.time())
    scope = TenderScope(
        case_id=project_uuid,
        assessment_id="query-plan-acceptance",
        agent_run_id=f"query-plan-{now}",
        subject="query-plan-acceptance",
        allowed_tools=frozenset({"search_tender_evidence"}),
        issued_at=now,
        expires_at=now + 300,
        audience="https://query-plan.acceptance/mcp",
        issuer="https://query-plan.acceptance",
    )
    service = TenderEvidenceService(
        SqlAlchemyTenderEvidenceRepository(
            SessionLocal,
            hybrid_search_client=configured_hybrid_client(),
        ),
        scope_provider=lambda: scope,
    )

    started = time.monotonic()
    result = service.search_tender_evidence(
        query=args.query,
        top_k=max(1, min(args.top_k, 20)),
    )
    duration_ms = round((time.monotonic() - started) * 1000)
    if result.status not in {ResultStatus.OK, ResultStatus.NO_RESULT}:
        raise RuntimeError(
            f"compound query failed with status={result.status.value}"
        )
    data = result.data if isinstance(result.data, dict) else {}
    plan = data.get("query_plan")
    matches = data.get("matches")
    if not isinstance(plan, dict) or int(plan.get("query_count") or 0) < 2:
        raise RuntimeError("compound query was not decomposed")
    if not isinstance(matches, list) or not matches:
        raise RuntimeError("compound query returned no evidence")

    print(
        json.dumps(
            {
                "status": "ok",
                "project_uuid": project_uuid,
                "project_name": project_name,
                "indexed_block_count": indexed_block_count,
                "query_plan": plan,
                "routing_summary": plan.get("routing_summary"),
                "query_tasks": plan.get("query_tasks"),
                "match_count": len(matches),
                "evidence_ids": [
                    str(
                        (item.get("evidence_ref") or {}).get(
                            "evidence_id"
                        )
                        or ""
                    )
                    for item in matches
                    if isinstance(item, dict)
                ],
                "matched_query_counts": [
                    len(item.get("matched_queries") or [])
                    for item in matches
                    if isinstance(item, dict)
                ],
                "duration_ms": duration_ms,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
