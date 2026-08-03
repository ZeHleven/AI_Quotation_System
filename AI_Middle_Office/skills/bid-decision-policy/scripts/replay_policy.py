from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.contracts import (  # noqa: E402
    AssessmentDraft,
    DocumentManifest,
)
from app.agents.bid_intake.policy import YamlBidPolicy  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Replay one bid-intake assessment against a policy."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--policy-version")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    manifest = DocumentManifest.model_validate(payload["manifest"])
    assessment = AssessmentDraft.model_validate(payload["assessment"])
    policy = (
        YamlBidPolicy.from_version(args.policy_version)
        if args.policy_version
        else YamlBidPolicy.from_active()
    )
    evaluation = policy.evaluate(
        draft=assessment,
        manifest=manifest,
    )
    print(
        json.dumps(
            evaluation.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
