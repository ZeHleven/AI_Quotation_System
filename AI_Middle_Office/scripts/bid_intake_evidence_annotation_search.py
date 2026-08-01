from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.core.database import SessionLocal  # noqa: E402
from app.models import registry as model_registry  # noqa: E402,F401
from app.models.bidding import BidProject  # noqa: E402
from app.models.tender_evidence import (  # noqa: E402
    BidEvidenceBlock,
    BidEvidenceDocument,
)
from app.services.tender_evidence_body_storage import (  # noqa: E402
    TenderEvidenceBodyReader,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Search every active evidence block for private gold-label "
            "annotation. This intentionally bypasses retrieval candidate "
            "limits and must not be used as an Agent production search."
        )
    )
    parser.add_argument(
        "--case-id",
        required=True,
        help="Project UUID or dataset_case_code.",
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    return parser.parse_args()


def _terms(query: str) -> list[str]:
    return [
        item
        for item in re.split(r"[\s|,，、;；]+", query.strip())
        if item
    ]


def _dataset_case_code(project: BidProject) -> str:
    summary = project.summary_json or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return ""
    if not isinstance(summary, dict):
        return ""
    return str(summary.get("dataset_case_code") or "").strip()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    terms = _terms(args.query)
    if not terms:
        raise RuntimeError("query must contain at least one term")

    db = SessionLocal()
    reader = TenderEvidenceBodyReader()
    try:
        project = (
            db.query(BidProject)
            .filter(BidProject.project_uuid == args.case_id)
            .one_or_none()
        )
        if project is None:
            project = next(
                (
                    item
                    for item in db.query(BidProject).all()
                    if _dataset_case_code(item) == args.case_id
                ),
                None,
            )
        if project is None:
            raise RuntimeError(
                f"project not found by UUID or dataset case code: {args.case_id}"
            )

        rows = (
            db.query(BidEvidenceBlock, BidEvidenceDocument)
            .join(
                BidEvidenceDocument,
                BidEvidenceDocument.id == BidEvidenceBlock.document_id,
            )
            .filter(
                BidEvidenceBlock.project_id == project.id,
                BidEvidenceDocument.active.is_(True),
                BidEvidenceDocument.parse_status != "failed",
            )
            .order_by(
                BidEvidenceDocument.document_key.asc(),
                BidEvidenceBlock.block_order.asc(),
            )
            .all()
        )
        matches = []
        normalized_query = "".join(terms)
        for block, document in rows:
            content = reader.read(
                document=document,
                block=block,
            )
            compact = re.sub(r"\s+", "", content)
            matched_terms = [
                term for term in terms if term in compact
            ]
            if not matched_terms:
                continue
            score = sum(
                4 if term == normalized_query else 1
                for term in matched_terms
            )
            score += sum(
                compact.count(term) for term in matched_terms
            )
            matches.append(
                {
                    "score": score,
                    "evidence_id": block.evidence_id,
                    "document_key": document.document_key,
                    "document_type": document.document_type,
                    "document_version": document.version_no,
                    "block_order": block.block_order,
                    "section": block.section,
                    "sheet": block.sheet,
                    "cell_range": block.cell_range,
                    "matched_terms": matched_terms,
                    "excerpt": content[:700],
                }
            )
    finally:
        db.close()

    matches.sort(
        key=lambda item: (
            item["score"],
            len(item["matched_terms"]),
            -item["block_order"],
        ),
        reverse=True,
    )
    print(
        json.dumps(
            matches[: max(1, min(args.top_k, 50))],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
