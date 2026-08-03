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
)
from app.agents.bid_intake.policy import YamlBidPolicy  # noqa: E402
from app.agents.bid_intake.policy_candidate import (  # noqa: E402
    propose_threshold_candidate,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=(
            "Propose a constrained bid-policy threshold candidate using "
            "development cases only."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--candidate-policy-version",
        required=True,
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
    proposal = propose_threshold_candidate(
        base_policy=YamlBidPolicy.from_active(),
        cases=cases,
        candidate_version=args.candidate_policy_version,
    )
    print(
        json.dumps(
            proposal.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
