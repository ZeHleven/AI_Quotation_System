"""Resume/poll an existing isolated MVP-1 assessment and write its run summary."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import time
from pathlib import Path

import requests


def _data(response: requests.Response):
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--assessment-id", required=True)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()

    origin = args.base_url.rstrip("/")
    base = origin + "/api/v1"
    session = requests.Session()
    health = session.get(f"{origin}/health/live", timeout=15)
    health.raise_for_status()
    model_provider = str(health.json().get("model_provider") or "unknown")
    assessment = _data(
        session.get(f"{base}/bid-assessments/{args.assessment_id}", timeout=15)
    )
    manifest_id = str((assessment.get("current_manifest") or {}).get("manifest_id") or "")
    lot_id = str((assessment.get("scope") or {}).get("lot_id") or "")

    deadline = time.monotonic() + max(10, int(args.timeout_seconds))
    run = report = None
    while time.monotonic() < deadline:
        try:
            run_rows = _data(
                session.get(f"{base}/bid-assessment-runtime-lab/runs", timeout=15)
            )
            run = next(
                (row for row in run_rows if row["assessment_id"] == args.assessment_id),
                None,
            )
            if run and run.get("status") in {"failed", "stale", "cancelled"}:
                raise RuntimeError(f"MVP-1 Run terminated before convergence: {run}")
            reports = _data(
                session.get(
                    f"{base}/bid-assessments/{args.assessment_id}/reports",
                    timeout=15,
                )
            )
            items = list(reports.get("items") or [])
            if items:
                report = _data(
                    session.get(
                        f"{base}/bid-reports/{items[0]['report_id']}", timeout=15
                    )
                )
                if run and run.get("status") == "succeeded":
                    break
        except requests.RequestException:
            # A local worker restart must not discard an otherwise recoverable run.
            pass
        time.sleep(0.5)
    if report is None or run is None or run.get("status") != "succeeded":
        raise TimeoutError(f"MVP-1 Run did not converge: run={run}, report={report}")

    trace = _data(
        session.get(
            f"{base}/bid-assessment-runtime-lab/runs/{run['run_id']}/trace",
            timeout=15,
        )
    )
    nodes = list(trace.get("nodes") or [])
    model_calls = [node for node in nodes if node.get("kind") == "model_call"]
    model_results = [node for node in nodes if node.get("kind") == "model_result"]
    tool_calls = [node for node in nodes if node.get("kind") == "tool_invocation"]
    checkpoints = [node for node in nodes if node.get("kind") == "checkpoint"]
    total_input_tokens = total_output_tokens = total_cost_microunits = 0
    action_types: Counter[str] = Counter()
    for node in model_calls:
        details = dict(node.get("details") or {})
        raw_tokens = str(details.get("tokens") or "0+0").split("+", 1)
        if len(raw_tokens) == 2:
            total_input_tokens += int(raw_tokens[0])
            total_output_tokens += int(raw_tokens[1])
        total_cost_microunits += int(details.get("cost_microunits") or 0)
    for node in model_results:
        action_types[str((node.get("details") or {}).get("action_type") or "unknown")] += 1
    claims = list(report.get("report", {}).get("claims") or [])
    summary = {
        "assessment_id": args.assessment_id,
        "manifest_id": manifest_id,
        "lot_id": lot_id,
        "run_id": run["run_id"],
        "run_status": run["status"],
        "report_id": report["report_id"],
        "report_status": report["status"],
        "decision": report["report"]["decision"]["code"],
        "model_provider": model_provider,
        "model_call_count": len(model_calls),
        "model_action_counts": dict(sorted(action_types.items())),
        "model_input_tokens": total_input_tokens,
        "model_output_tokens": total_output_tokens,
        "model_cost_microunits": total_cost_microunits,
        "tool_call_count": len(tool_calls),
        "failed_tool_call_count": sum(
            1 for node in tool_calls if str(node.get("status")) == "failed"
        ),
        "checkpoint_count": len(checkpoints),
        "claim_count": len(claims),
        "citation_count": sum(len(row.get("citations") or []) for row in claims),
        "hard_gate_statuses": dict(
            Counter(
                str(row.get("status") or "unknown")
                for row in report.get("report", {}).get("hard_gates") or []
            )
        ),
        "trace_node_count": len(nodes),
        "trace_timeline_count": len(trace.get("timeline") or []),
        "sample_sha256": (
            hashlib.sha256(args.sample.read_bytes()).hexdigest() if args.sample else None
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
