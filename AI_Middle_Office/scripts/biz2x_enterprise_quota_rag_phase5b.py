from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

import httpx


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.services.cost_rag_sync import (  # noqa: E402
    cost_rag_sync_status_summary,
    preview_active_cost_items_rag_sync,
    sync_active_cost_items_to_rag,
)
from app.services.quote_cost_matching import enrich_quote_payload_with_cost_refs  # noqa: E402


DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "outputs" / "biz2x_enterprise_quota_rag_phase5b"
DEFAULT_QUERIES = [
    "\u77f3\u6750\u5730\u9762",
    "\u74f7\u7816\u5730\u9762",
    "\u77f3\u6750\u8fc7\u95e8\u77f3",
    "\u697c\u5730\u9762\u627e\u5e73\u5c42",
    "\u5730\u9762\u9632\u6c34",
    "\u74f7\u7816\u7f8e\u7f1d",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BIZ-2X Phase 5B active enterprise quota RAG sync and regression helper."
    )
    parser.add_argument("--sync", action="store_true", help="Actually call RAG /admin/reload and write sync run.")
    parser.add_argument("--regression", action="store_true", help="Call RAG /api/v1/retrieve for sample queries.")
    parser.add_argument("--top-k", type=int, default=5, help="RAG retrieval top_k.")
    parser.add_argument("--sample-limit", type=int, default=5, help="Dry-run payload sample size.")
    parser.add_argument("--actor-username", default="phase5b-script", help="Username stored on sync run.")
    parser.add_argument("--actor-id", type=int, default=None, help="Optional users.id stored on sync run.")
    parser.add_argument("--rag-url", default=settings.rag_service_url, help="RAG service base URL.")
    parser.add_argument(
        "--reload-timeout-seconds",
        type=float,
        default=None,
        help="Temporarily override RAG_RELOAD_TIMEOUT_SECONDS for --sync.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Regression query. Can be passed multiple times. Defaults to a small enterprise quota sample set.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON/Markdown reports.")
    parser.add_argument(
        "--require-rag",
        action="store_true",
        help="Exit non-zero when any RAG regression query fails or returns no data.",
    )
    return parser.parse_args()


def _retrieve_rag(rag_url: str, query: str, top_k: int) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{rag_url.rstrip('/')}/api/v1/retrieve", json={"query": query, "top_k": top_k})
        response.raise_for_status()
        body = response.json()
        return {
            "ok": True,
            "query": query,
            "top_k": top_k,
            "http_status": response.status_code,
            "data": body.get("data", []),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "query": query,
            "top_k": top_k,
            "http_status": None,
            "data": [],
            "error": str(exc),
        }


def _quote_chain_probe(db, query: str) -> dict[str, Any]:
    payload = {
        "project_details": [
            {
                "project_name": query,
                "spec": "",
                "quantity": 1,
                "unit": "",
                "unit_price": 0,
                "total_price": 0,
            }
        ]
    }
    enriched = enrich_quote_payload_with_cost_refs(db, payload)
    row = (enriched.get("project_details") or [{}])[0] if isinstance(enriched, dict) else {}
    reference = row.get("cost_reference") or {}
    return {
        "query": query,
        "matched": bool(reference.get("matched")),
        "reference_source": reference.get("reference_source"),
        "reference_price_source": reference.get("reference_price_source"),
        "item_name": reference.get("item_name"),
        "quota_code": reference.get("quota_code"),
        "enterprise_quota_item_id": reference.get("enterprise_quota_item_id"),
        "reference_price": reference.get("reference_price"),
        "match_type": reference.get("match_type"),
    }


def _run_regression(db, *, rag_url: str, queries: list[str], top_k: int, call_rag: bool) -> list[dict[str, Any]]:
    cases = []
    for query in queries:
        quote_probe = _quote_chain_probe(db, query)
        rag_probe = _retrieve_rag(rag_url, query, top_k) if call_rag else None
        cases.append(
            {
                "query": query,
                "quote_chain": quote_probe,
                "rag": rag_probe,
            }
        )
    return cases


def _write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"phase5b_enterprise_quota_rag_{stamp}.json"
    md_path = output_dir / f"phase5b_enterprise_quota_rag_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BIZ-2X Phase 5B Enterprise Quota RAG Report",
        "",
        f"- Generated at: {report.get('generated_at')}",
        f"- Source: {report.get('dry_run', {}).get('source')}",
        f"- Requested count: {report.get('dry_run', {}).get('requested_count')}",
        f"- Sync executed: {bool(report.get('sync_result'))}",
        f"- RAG regression executed: {report.get('rag_regression_executed')}",
        "",
        "## Status",
        "",
        "```json",
        json.dumps(report.get("status_summary"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Regression Cases",
        "",
    ]
    for case in report.get("regression_cases", []):
        quote = case.get("quote_chain") or {}
        rag = case.get("rag") or {}
        rag_names = [item.get("item_name") for item in (rag.get("data") or []) if isinstance(item, dict)]
        lines.extend(
            [
                f"### {case.get('query')}",
                "",
                f"- Quote matched: {quote.get('matched')}",
                f"- Quote source: {quote.get('reference_source')}",
                f"- Quote item: {quote.get('quota_code') or ''} {quote.get('item_name') or ''}".strip(),
                f"- RAG ok: {rag.get('ok') if rag else 'not_run'}",
                f"- RAG returned: {', '.join(rag_names[:5]) if rag_names else '-'}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    queries = args.queries or DEFAULT_QUERIES
    db = SessionLocal()
    try:
        dry_run = preview_active_cost_items_rag_sync(db, sample_limit=args.sample_limit)
        sync_result = None
        if args.sync:
            if args.reload_timeout_seconds is not None:
                object.__setattr__(settings, "rag_reload_timeout_seconds", args.reload_timeout_seconds)
            sync_result = asyncio.run(
                sync_active_cost_items_to_rag(db, args.actor_username, user_id=args.actor_id)
            )
        status_summary = cost_rag_sync_status_summary(db)
        regression_cases = _run_regression(
            db,
            rag_url=args.rag_url,
            queries=queries,
            top_k=args.top_k,
            call_rag=args.regression,
        )
        report = {
            "generated_at": datetime.now().isoformat(),
            "dry_run": dry_run,
            "sync_result": sync_result,
            "status_summary": status_summary,
            "rag_url": args.rag_url,
            "rag_regression_executed": bool(args.regression),
            "regression_cases": regression_cases,
        }
    finally:
        db.close()

    json_path, md_path = _write_reports(report, Path(args.output_dir))
    print(json.dumps({"ok": True, "json_path": str(json_path), "markdown_path": str(md_path), **report}, ensure_ascii=False, indent=2))

    if args.sync and sync_result and not sync_result.get("success"):
        return 2
    if args.require_rag and args.regression:
        failed = [
            case
            for case in regression_cases
            if not (case.get("rag") or {}).get("ok") or not (case.get("rag") or {}).get("data")
        ]
        if failed:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
