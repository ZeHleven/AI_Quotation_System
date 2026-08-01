from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.core.database import SessionLocal  # noqa: E402
from app.services.tender_evidence_ingestion import (  # noqa: E402
    ingest_bid_project_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promote one existing parsed BidProjectFile into the immutable "
            "tender evidence store."
        )
    )
    parser.add_argument("--project-uuid", required=True)
    parser.add_argument("--file-uuid", required=True)
    parser.add_argument(
        "--document-key",
        help=(
            "Stable logical identity shared by versions, for example "
            "'tender-notice'. Defaults to file type plus filename stem."
        ),
    )
    parser.add_argument("--document-type")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = ingest_bid_project_file(
            db,
            project_uuid=args.project_uuid,
            file_uuid=args.file_uuid,
            document_key=args.document_key,
            document_type=args.document_type,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
