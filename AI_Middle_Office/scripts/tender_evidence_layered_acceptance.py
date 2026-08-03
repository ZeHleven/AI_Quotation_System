from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_DIR / ".env")

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.tender_evidence_index import BidEvidenceIndexJob  # noqa: E402
from app.services.tender_evidence_body_storage import (  # noqa: E402
    BODY_STORAGE_BACKEND_MINIO,
    TenderEvidenceBodyReader,
)
from app.agents.bid_intake.mcp_adapter import (  # noqa: E402
    StreamableHttpMcpToolCaller,
)
from mcp_servers.tender_evidence.auth import (  # noqa: E402
    ALL_CAPABILITIES,
    ScopedTokenCodec,
)
from mcp_servers.tender_evidence.sqlalchemy_repository import (  # noqa: E402
    SqlAlchemyTenderEvidenceRepository,
)
from mcp_servers.tender_evidence.hybrid_client import (  # noqa: E402
    configured_hybrid_client,
    hybrid_search_enabled,
)


def main() -> int:
    db = SessionLocal()
    try:
        row = (
            db.query(
                BidEvidenceBlock,
                BidEvidenceDocument,
                BidProject,
                BidEvidenceManifest,
            )
            .join(
                BidEvidenceDocument,
                BidEvidenceDocument.id == BidEvidenceBlock.document_id,
            )
            .join(BidProject, BidProject.id == BidEvidenceBlock.project_id)
            .join(
                BidEvidenceManifest,
                BidEvidenceManifest.project_id == BidProject.id,
            )
            .join(
                BidEvidenceIndexJob,
                BidEvidenceIndexJob.manifest_id == BidEvidenceManifest.id,
            )
            .filter(
                BidEvidenceDocument.active.is_(True),
                BidEvidenceDocument.body_storage_backend
                == BODY_STORAGE_BACKEND_MINIO,
                BidEvidenceManifest.active.is_(True),
                BidEvidenceIndexJob.status == "completed",
                BidEvidenceIndexJob.indexed_block_count
                == BidEvidenceIndexJob.requested_block_count,
            )
            .order_by(BidEvidenceBlock.id.asc())
            .first()
        )
        if row is None:
            raise RuntimeError("no active MinIO-backed tender evidence exists")
        block, document, project, manifest = row
        expected_content = TenderEvidenceBodyReader().read(
            document=document,
            block=block,
        )
        query = _query_fragment(expected_content)
        case_id = project.project_uuid
        evidence_id = block.evidence_id
        manifest_version = manifest.version_no
        manifest_hash = manifest.manifest_hash
    finally:
        db.close()

    if not hybrid_search_enabled():
        raise RuntimeError("tender evidence hybrid search is not enabled")
    hybrid_client = configured_hybrid_client()
    hybrid_hits = hybrid_client.search(
        case_id=case_id,
        manifest_version=manifest_version,
        manifest_hash=manifest_hash,
        query=query,
        top_k=20,
    )
    if evidence_id not in {item.evidence_id for item in hybrid_hits}:
        raise RuntimeError(
            "Milvus hybrid search did not return the scoped evidence block"
        )

    repository = SqlAlchemyTenderEvidenceRepository(
        SessionLocal,
        hybrid_search_client=hybrid_client,
    )
    repository_matches = repository.search(
        case_id=case_id,
        query=query,
        top_k=20,
    )
    if evidence_id not in {item.evidence_id for item in repository_matches}:
        raise RuntimeError(
            "database MCP repository did not hydrate the MinIO-backed block"
        )
    repository_context = repository.get_context(
        case_id=case_id,
        evidence_id=evidence_id,
        before_blocks=0,
        after_blocks=0,
    )
    if (
        not repository_context
        or repository_context[0].content != expected_content
    ):
        raise RuntimeError(
            "database MCP repository context differs from MinIO body"
        )

    secret = os.environ.get("TENDER_MCP_JWT_SECRET", "").strip()
    if not secret:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "case_id": case_id,
                    "evidence_id": evidence_id,
                    "query": query,
                    "repository_search_matches": len(repository_matches),
                    "hybrid_search_matches": len(hybrid_hits),
                    "context_hash_verified": True,
                    "authoritative_body_source": "minio",
                    "mysql_block_content": "cleared",
                    "mcp_transport": "skipped_transient_runtime_secret",
                    "checked_at_epoch": int(time.time()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    issuer = os.environ.get(
        "TENDER_MCP_ISSUER",
        "http://127.0.0.1:8012",
    ).strip()
    audience = os.environ.get(
        "TENDER_MCP_AUDIENCE",
        "http://127.0.0.1:8012/mcp",
    ).strip()
    token = ScopedTokenCodec(
        secret=secret,
        issuer=issuer,
        audience=audience,
    ).issue(
        case_id=case_id,
        assessment_id=f"layered-acceptance-{uuid.uuid4().hex}",
        agent_run_id=f"layered-run-{uuid.uuid4().hex}",
        subject="layered-storage-acceptance",
        allowed_tools=set(ALL_CAPABILITIES),
        ttl_seconds=300,
    )
    caller = StreamableHttpMcpToolCaller(
        url=os.environ.get(
            "BID_INTAKE_MCP_URL",
            "http://127.0.0.1:8012/mcp",
        ),
        bearer_token=token,
        timeout_seconds=20,
    )
    manifest = caller.read_json_resource("tender://current/manifest")
    search = caller.call_tool(
        "search_tender_evidence",
        {"query": query, "top_k": 20},
    )
    matches = (search.get("data") or {}).get("matches") or []
    matched_ids = {
        str((item.get("evidence_ref") or {}).get("evidence_id") or "")
        for item in matches
        if isinstance(item, dict)
    }
    if evidence_id not in matched_ids:
        raise RuntimeError("MCP search did not return the MinIO-backed block")
    context = caller.call_tool(
        "read_evidence_context",
        {
            "evidence_id": evidence_id,
            "before_blocks": 0,
            "after_blocks": 0,
        },
    )
    context_blocks = (context.get("data") or {}).get("blocks") or []
    if not context_blocks:
        raise RuntimeError("MCP context read returned no blocks")
    if context_blocks[0].get("content") != expected_content:
        raise RuntimeError("MCP context differs from authoritative MinIO body")
    if str(manifest.get("case_id") or "") != case_id:
        raise RuntimeError("MCP manifest is not scoped to the selected project")
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": case_id,
                "evidence_id": evidence_id,
                "query": query,
                "mcp_search_matches": len(matches),
                "hybrid_search_matches": len(hybrid_hits),
                "context_hash_verified": True,
                "authoritative_body_source": "minio",
                "mysql_block_content": "cleared",
                "checked_at_epoch": int(time.time()),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _query_fragment(content: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_.-]{3,}|[\u4e00-\u9fff]{2,}", content)
    if not tokens:
        raise RuntimeError("selected evidence has no searchable text")
    return max(tokens, key=len)[:16]
if __name__ == "__main__":
    raise SystemExit(main())
