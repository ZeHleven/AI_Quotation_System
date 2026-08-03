from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.retrieval_evaluation import (  # noqa: E402
    RetrievalEvalCase,
    dataset_fingerprint,
    load_eval_cases,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Promote a reviewed Challenge draft to an immutable approved "
            "JSONL dataset. Existing outputs are never overwritten."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument(
        "--review-note",
        default="业务人员已逐题复核并确认通过。",
    )
    return parser.parse_args()


def _approved_case(
    case: RetrievalEvalCase,
    *,
    reviewer: str,
    review_note: str,
) -> RetrievalEvalCase:
    if case.dataset_split != "challenge":
        raise RuntimeError(
            f"{case.eval_case_id} is not a Challenge case"
        )
    if case.annotation_status not in {"draft", "reviewed"}:
        raise RuntimeError(
            f"{case.eval_case_id} is already {case.annotation_status}"
        )
    note_parts = [
        str(case.annotation_note or "").strip(),
        review_note.strip(),
    ]
    return RetrievalEvalCase.model_validate(
        {
            **case.model_dump(mode="json"),
            "annotation_status": "approved",
            "reviewed_by": reviewer,
            "annotation_note": " ".join(
                item for item in note_parts if item
            ),
        }
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if output_path.exists():
        raise RuntimeError(
            f"approved dataset already exists; refusing overwrite: "
            f"{output_path}"
        )
    reviewer = args.reviewer.strip()
    if not reviewer:
        raise RuntimeError("reviewer must not be empty")

    draft_cases = load_eval_cases(input_path)
    approved_cases = [
        _approved_case(
            case,
            reviewer=reviewer,
            review_note=args.review_note,
        )
        for case in draft_cases
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(
        output_path.suffix + f".{os.getpid()}.tmp"
    )
    payload = "\n".join(
        json.dumps(
            item.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for item in approved_cases
    ) + "\n"
    temp_path.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temp_path, output_path)

    file_sha256 = hashlib.sha256(
        output_path.read_bytes()
    ).hexdigest()
    print(
        json.dumps(
            {
                "schema_version": (
                    "bid_intake_challenge_dataset_freeze_v1"
                ),
                "input": str(input_path),
                "output": str(output_path),
                "case_count": len(approved_cases),
                "reviewer": reviewer,
                "dataset_fingerprint": dataset_fingerprint(
                    approved_cases
                ),
                "file_sha256": file_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
