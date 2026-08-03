from __future__ import annotations

import argparse
import json
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
            "Merge approved Development JSONL datasets into one fixed "
            "cross-project pool without changing any case content."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Input Development JSONL; repeat for each source dataset.",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-projects", type=int, default=2)
    return parser.parse_args()


def _project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def _canonical_line(case: RetrievalEvalCase) -> str:
    return json.dumps(
        case.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments()
    source_paths = [_project_path(value) for value in args.input]
    output_path = _project_path(args.output)

    cases: list[RetrievalEvalCase] = []
    source_summary = []
    for source_path in source_paths:
        source_cases = load_eval_cases(source_path)
        if any(item.dataset_split != "development" for item in source_cases):
            raise RuntimeError(
                f"source contains non-Development cases: {source_path}"
            )
        if any(item.annotation_status != "approved" for item in source_cases):
            raise RuntimeError(
                f"source contains non-approved cases: {source_path}"
            )
        cases.extend(source_cases)
        source_summary.append(
            {
                "path": str(source_path.relative_to(PROJECT_DIR)).replace(
                    "\\", "/"
                ),
                "case_count": len(source_cases),
                "project_count": len(
                    {item.case_id for item in source_cases}
                ),
                "fingerprint": dataset_fingerprint(source_cases),
            }
        )

    eval_case_ids = [item.eval_case_id for item in cases]
    if len(eval_case_ids) != len(set(eval_case_ids)):
        raise RuntimeError("source datasets contain duplicate eval_case_id")
    project_ids = sorted({item.case_id for item in cases})
    if len(project_ids) < args.minimum_projects:
        raise RuntimeError(
            "fixed Development pool does not meet minimum project count"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(_canonical_line(item) for item in cases) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "schema_version": (
                    "bid_intake_fixed_development_pool_build_v1"
                ),
                "output": str(output_path),
                "case_count": len(cases),
                "project_count": len(project_ids),
                "project_ids": project_ids,
                "dataset_fingerprint": dataset_fingerprint(cases),
                "sources": source_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
