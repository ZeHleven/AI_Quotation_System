from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.calibration import (  # noqa: E402
    PolicyCalibrationCase,
    compare_policy_versions,
)
from app.agents.bid_intake.policy import YamlBidPolicy  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            "Compare active and candidate bid policies on an immutable "
            "calibration dataset."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--candidate-policy-version")
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="Remove per-case diagnostics from stdout.",
    )
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Exit with code 2 when the release gate does not pass.",
    )
    args = parser.parse_args()

    payload = json.loads(
        Path(args.dataset).resolve().read_text(encoding="utf-8")
    )
    raw_cases = (
        payload.get("cases")
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(raw_cases, list):
        raise ValueError("dataset must be a list or an object with cases")
    cases = [
        PolicyCalibrationCase.model_validate(item)
        for item in raw_cases
    ]
    baseline = YamlBidPolicy.from_active()
    candidate = (
        YamlBidPolicy.from_version(args.candidate_policy_version)
        if args.candidate_policy_version
        else YamlBidPolicy.from_active()
    )
    report = compare_policy_versions(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
    )
    output = report.model_dump(mode="json")
    if args.aggregate_only:
        output["baseline"].pop("case_results", None)
        output["candidate"].pop("case_results", None)
    print(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.fail_on_gate and not report.release_gate.passed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
