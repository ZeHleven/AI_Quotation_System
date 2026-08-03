from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.agents.bid_intake.contracts import AgentVersions, HumanDecision  # noqa: E402
from app.agents.bid_intake.fake_adapters import (  # noqa: E402
    FakeTenderEvidenceClient,
    build_demo_evidence,
    build_demo_manifest,
    build_demo_script,
)
from app.agents.bid_intake.graph import build_bid_intake_agent, build_initial_state  # noqa: E402
from app.agents.bid_intake.policy import InMemoryBidPolicy  # noqa: E402
from app.agents.bid_intake.ports import AgentRuntime  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run the bid-intake LangGraph prototype.")
    parser.add_argument(
        "--decision",
        choices=[
            "approved",
            "approved_with_conditions",
            "rejected",
            "supplement_requested",
            "research_requested",
        ],
        default="approved",
    )
    parser.add_argument("--full", action="store_true", help="Print the complete assessment draft.")
    args = parser.parse_args()

    manifest = build_demo_manifest()
    records = build_demo_evidence()
    evidence = FakeTenderEvidenceClient(case_id=manifest.case_id, records=records)
    runtime = AgentRuntime(
        model=build_demo_script(records),
        evidence=evidence,
        policy=InMemoryBidPolicy(),
    )
    agent = build_bid_intake_agent(runtime)
    assessment_id = f"ASSESSMENT-{uuid4()}"
    thread_id = f"bid-assessment:{assessment_id}"
    state = build_initial_state(
        manifest=manifest,
        assessment_id=assessment_id,
        agent_run_id=f"RUN-{uuid4()}",
        versions=AgentVersions(model_id=runtime.model.model_id),
    )

    first_result = agent.start(state, thread_id=thread_id)
    snapshot = agent.snapshot(thread_id=thread_id)
    draft = snapshot.get("assessment_draft") or {}
    output = {
        "interrupted": "__interrupt__" in first_result,
        "assessment_id": assessment_id,
        "phase_before_review": snapshot.get("phase"),
        "tool_call_count": snapshot.get("tool_call_count"),
        "recommendation": draft.get("recommendation"),
        "project_summary": draft.get("project_summary"),
        "dimension_count": len(draft.get("dimension_reviews") or []),
        "risk_count": len(draft.get("risks") or []),
        "policy_evaluation": snapshot.get("policy_evaluation"),
        "gate_result": snapshot.get("gate_result"),
    }
    if args.full:
        output["assessment_draft"] = draft
    print(json.dumps(output, ensure_ascii=False, indent=2))

    decision = HumanDecision(
        decision_id=f"DECISION-{uuid4()}",
        action=args.decision,
        report_version=int(snapshot.get("report_version") or 1),
        manifest_version=manifest.manifest_version,
        decided_by="demo-reviewer",
        note="LangGraph prototype CLI decision.",
    )
    final_result = agent.resume(decision, thread_id=thread_id)
    print(
        json.dumps(
            {
                "final_phase": final_result.get("phase"),
                "human_decision": final_result.get("human_decision"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
