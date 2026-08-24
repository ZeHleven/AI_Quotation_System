"""Exercise the localhost MVP-1 vertical slice with the synthetic text sample."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import mimetypes
import time
import uuid
from pathlib import Path

import requests


MIDDLE_OFFICE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = MIDDLE_OFFICE_DIR / "samples" / "mvp1-local-demo-tender.txt"


def _key(label: str) -> str:
    return f"mvp1-local-verify-{label}-{uuid.uuid4()}"


def _data(response: requests.Response):
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", payload)


def _ensure_synthetic_enterprise_snapshot(
    session: requests.Session,
    *,
    origin: str,
) -> str | None:
    capabilities = _data(
        session.get(
            f"{origin}/api/v1/bid-assessment-runtime-lab/capabilities",
            timeout=15,
        )
    )
    if not capabilities.get("enterprise_snapshot_configurable"):
        return None
    current = _data(
        session.get(
            f"{origin}/api/v1/bid-assessment-runtime-lab/enterprise-snapshot",
            timeout=15,
        )
    ).get("snapshot")
    if current:
        return str(current["snapshot_id"])

    values = {
        "I01": {"legal_name": "旗胜建设有限公司"},
        "I02": {"records": [{"code": "装修一级"}]},
        "I03": {"license_no": "LOCAL-SAFETY-001", "status": "active"},
        "I04": {"projects": [{"code": "商业精装修"}]},
        "I05": {"people": [{"role": "项目经理"}]},
        "I06": {"available_cash_cny": 1000000},
        "I07": {"max_bond_cny": 500000, "supported_forms": ["bank_guarantee"]},
        "I08": {"available_person_days": 30},
        "I09": {"rules": []},
        "I10": {"status": "clear"},
        "I11": {"records": []},
    }
    as_of = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace(
        "+00:00", "Z"
    )
    command = {
        "as_of": as_of,
        "change_note": "MVP-1 完全隔离合成企业能力基线",
        "records": [
            {
                "slot_code": slot_code,
                "coverage_status": "supported",
                "value": values[slot_code],
                "source_record_id": f"mvp1-local:{slot_code}",
                "source_version": "synthetic-v1",
                "source_status": "verified",
                "source_label": "MVP-1 完全隔离合成验证",
                "valid_from": None,
                "valid_to": None,
                "checked_at": as_of,
            }
            for slot_code in sorted(values)
        ],
    }
    preview = _data(
        session.post(
            f"{origin}/api/v1/bid-assessment-runtime-lab/enterprise-baseline/validate",
            json=command,
            timeout=15,
        )
    )
    frozen = _data(
        session.post(
            f"{origin}/api/v1/bid-assessment-runtime-lab/enterprise-snapshots",
            headers={
                "Idempotency-Key": _key("enterprise-snapshot"),
                "X-Enterprise-Candidate-Hash": preview["candidate_snapshot_hash"],
            },
            json=command,
            timeout=15,
        )
    )
    return str(frozen["snapshot_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--expected-model-provider")
    parser.add_argument("--title", default="MVP-1 本地隔离演示")
    parser.add_argument("--client-name", default="本地演示资料")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    origin = args.base_url.rstrip("/")
    base = origin + "/api/v1"
    content = args.sample.read_bytes()
    session = requests.Session()
    health = session.get(f"{origin}/health/live", timeout=15)
    health.raise_for_status()
    health_payload = health.json()
    model_provider = str(health_payload.get("model_provider") or "unknown")
    if (
        args.expected_model_provider
        and model_provider != args.expected_model_provider
    ):
        raise RuntimeError(
            "unexpected local model provider: "
            f"expected={args.expected_model_provider} actual={model_provider}"
        )
    enterprise_snapshot_id = _ensure_synthetic_enterprise_snapshot(
        session,
        origin=origin,
    )

    created = session.post(
        f"{base}/bid-assessments",
        headers={"Idempotency-Key": _key("assessment")},
        json={
            "title": args.title,
            "client_name": args.client_name,
            "internal_note": (
                "完全隔离本地运行；"
                f"受控模型 Provider={model_provider}"
            ),
            "external_ref": None,
        },
        timeout=15,
    )
    assessment = _data(created)
    assessment_id = assessment["assessment_id"]
    assessment_etag = created.headers["ETag"]

    batch_response = session.post(
        f"{base}/bid-assessments/{assessment_id}/upload-batches",
        headers={
            "Idempotency-Key": _key("batch"),
            "If-Match": assessment_etag,
        },
        json={"purpose": "initial", "base_manifest_id": None},
        timeout=15,
    )
    batch = _data(batch_response)
    batch_id = batch["batch_id"]
    batch_etag = batch_response.headers["ETag"]

    upload = session.post(
        f"{base}/bid-upload-batches/{batch_id}/files",
        headers={
            "Idempotency-Key": _key("file"),
            "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
        },
        data={"client_file_id": f"sample-{uuid.uuid4()}", "operation": "add"},
        files={
            "file": (
                args.sample.name,
                content,
                mimetypes.guess_type(args.sample.name)[0]
                or "application/octet-stream",
            )
        },
        timeout=30,
    )
    _data(upload)
    batch_etag = upload.headers["X-Batch-ETag"]

    committed_response = session.post(
        f"{base}/bid-upload-batches/{batch_id}/commit",
        headers={
            "Idempotency-Key": _key("commit"),
            "If-Match": batch_etag,
        },
        json={
            "expected_file_count": 1,
            "expected_deactivation_count": 0,
            "change_note": "synthetic local verification",
            "confirm_start_analysis": True,
        },
        timeout=30,
    )
    committed = _data(committed_response)
    manifest_id = committed["manifest"]["manifest_id"]

    deadline = time.monotonic() + max(10, int(args.timeout_seconds))
    candidate = None
    while time.monotonic() < deadline:
        lot_page = _data(
            session.get(f"{base}/bid-assessments/{assessment_id}/lots", timeout=15)
        )
        candidates = list(lot_page.get("candidates") or [])
        if candidates:
            candidate = candidates[0]
            break
        if (lot_page.get("generation") or {}).get("status") == "failed":
            raise RuntimeError(f"lot detection failed: {lot_page}")
        time.sleep(0.5)
    if candidate is None:
        raise TimeoutError("lot candidate was not produced before timeout")

    current_response = session.get(
        f"{base}/bid-assessments/{assessment_id}", timeout=15
    )
    _data(current_response)
    selected = session.post(
        f"{base}/bid-assessments/{assessment_id}/lot-selection",
        headers={
            "Idempotency-Key": _key("lot"),
            "If-Match": current_response.headers["ETag"],
        },
        json={
            "manifest_id": manifest_id,
            "lot_id": candidate["lot_id"],
            "selection_note": "synthetic local verification",
        },
        timeout=15,
    )
    _data(selected)

    deadline = time.monotonic() + max(10, int(args.timeout_seconds))
    report = None
    run = None
    while time.monotonic() < deadline:
        run_rows = _data(
            session.get(f"{base}/bid-assessment-runtime-lab/runs", timeout=15)
        )
        run = next(
            (row for row in run_rows if row["assessment_id"] == assessment_id),
            None,
        )
        if run and run.get("status") in {"failed", "stale", "cancelled"}:
            raise RuntimeError(f"MVP-1 Run terminated before convergence: {run}")
        reports = _data(
            session.get(f"{base}/bid-assessments/{assessment_id}/reports", timeout=15)
        )
        items = list(reports.get("items") or [])
        if items:
            report = _data(
                session.get(f"{base}/bid-reports/{items[0]['report_id']}", timeout=15)
            )
            if run and run.get("status") == "succeeded":
                break
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
    if args.expected_model_provider and not model_calls:
        raise RuntimeError("the full-chain run produced no governed model call")
    if len(model_results) != len(model_calls):
        raise RuntimeError(
            "model call/result cardinality mismatch: "
            f"calls={len(model_calls)} results={len(model_results)}"
        )
    invalid_model_statuses = sorted(
        {
            str(node.get("status"))
            for node in model_calls
            if str(node.get("status")) != "succeeded"
        }
    )
    if invalid_model_statuses:
        raise RuntimeError(
            f"non-succeeded model calls in successful run: {invalid_model_statuses}"
        )
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_microunits = 0
    action_types: Counter[str] = Counter()
    for node in model_calls:
        details = dict(node.get("details") or {})
        raw_tokens = str(details.get("tokens") or "0+0").split("+", 1)
        if len(raw_tokens) == 2:
            total_input_tokens += int(raw_tokens[0])
            total_output_tokens += int(raw_tokens[1])
        total_cost_microunits += int(details.get("cost_microunits") or 0)
    for node in model_results:
        details = dict(node.get("details") or {})
        action_types[str(details.get("action_type") or "unknown")] += 1
    summary = {
        "assessment_id": assessment_id,
        "manifest_id": manifest_id,
        "lot_id": candidate["lot_id"],
        "run_id": run["run_id"],
        "run_status": run["status"],
        "report_id": report["report_id"],
        "report_status": report["status"],
        "decision": report["report"]["decision"]["code"],
        "model_provider": model_provider,
        "enterprise_snapshot_id": enterprise_snapshot_id,
        "model_call_count": len(model_calls),
        "model_action_counts": dict(sorted(action_types.items())),
        "model_input_tokens": total_input_tokens,
        "model_output_tokens": total_output_tokens,
        "model_cost_microunits": total_cost_microunits,
        "tool_call_count": len(tool_calls),
        "checkpoint_count": len(checkpoints),
        "claim_count": len(report.get("report", {}).get("claims") or []),
        "citation_count": sum(
            len(row.get("citations") or [])
            for row in report.get("report", {}).get("claims") or []
        ),
        "hard_gate_statuses": dict(
            Counter(
                str(row.get("status") or "unknown")
                for row in report.get("report", {}).get("hard_gates") or []
            )
        ),
        "trace_node_count": len(nodes),
        "trace_timeline_count": len(trace.get("timeline") or []),
        "sample_sha256": hashlib.sha256(content).hexdigest(),
    }
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
