from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import Workbook

from app.models.cost_item import COST_SOURCE_AI_SUGGESTED, COST_STATUS_ACTIVE, COST_STATUS_DRAFT


ACTION_HEADERS = [
    "issue_id",
    "risk_level",
    "trial_blocker",
    "issue_type",
    "cost_item_id",
    "related_item_ids",
    "status",
    "source",
    "item_name",
    "spec",
    "unit",
    "price",
    "quote_usage_count",
    "latest_quote_used_at",
    "evidence",
    "suggested_action",
    "owner",
    "done",
    "notes",
]

HIGH_RISK_CATEGORIES = {
    "exact_active_duplicate",
    "invalid_main_price",
    "missing_unit",
}
MEDIUM_RISK_CATEGORIES = {
    "missing_named_reference_price",
    "same_name_mixed_units",
    "missing_spec_on_multi_name",
    "rag_sync_latest_not_success",
    "rag_sync_count_mismatch",
    "ai_suggested_draft_review",
    "draft_duplicate_with_active",
}
RAG_CATEGORIES = {
    "rag_sync_latest_not_success",
    "rag_sync_count_mismatch",
    "rag_sync_not_checked",
}

_SPLITTER = re.compile(r"[\s\-_+&/\\(){}\[\],.;:，。；：、]+")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _SPLITTER.sub("", unicodedata.normalize("NFKC", _clean_text(value)).lower())


def _item_id(item: Any) -> int | None:
    value = getattr(item, "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _positive_price(value: Any) -> float | None:
    number = _price(value)
    if number is None or number <= 0:
        return None
    return number


def _duplicate_key(item: Any) -> tuple[str, str, str, str, str]:
    return (
        _norm(getattr(item, "category", "")),
        _norm(getattr(item, "subcategory", "")),
        _norm(getattr(item, "item_name", "")),
        _norm(getattr(item, "spec", "")),
        _norm(getattr(item, "unit", "")),
    )


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _quote_usage_for_item(quote_usage: Mapping[int, Mapping[str, Any]], item_id: int | None) -> dict[str, Any]:
    if item_id is None:
        return {"count": 0, "latest_used_at": ""}
    data = quote_usage.get(item_id) or {}
    return {
        "count": int(data.get("count") or 0),
        "latest_used_at": _iso(data.get("latest_used_at")),
    }


def _status_for_issue(issue: Mapping[str, Any], item_lookup: Mapping[int, Any]) -> str:
    item_id = issue.get("cost_item_id")
    if item_id in item_lookup:
        return _clean_text(getattr(item_lookup[item_id], "status", ""))
    if issue.get("category", "").startswith("rag_sync"):
        return "-"
    return COST_STATUS_ACTIVE


def _source_for_issue(issue: Mapping[str, Any], item_lookup: Mapping[int, Any]) -> str:
    item_id = issue.get("cost_item_id")
    if item_id in item_lookup:
        return _clean_text(getattr(item_lookup[item_id], "source", ""))
    return ""


def _risk_from_issue(issue: Mapping[str, Any], quote_usage_count: int) -> str:
    category = _clean_text(issue.get("category"))
    severity = _clean_text(issue.get("severity"))
    if category in HIGH_RISK_CATEGORIES or severity == "high":
        risk = "high"
    elif category in MEDIUM_RISK_CATEGORIES or severity == "medium":
        risk = "medium"
    else:
        risk = "low"
    if quote_usage_count > 0 and risk == "medium":
        return "high"
    if quote_usage_count > 0 and risk == "low":
        return "medium"
    return risk


def _owner_for_issue(issue_type: str) -> str:
    if issue_type in RAG_CATEGORIES:
        return "admin/cost_approver"
    if "draft" in issue_type:
        return "cost_editor/cost_approver"
    return "cost_department"


def _suggested_action(issue: Mapping[str, Any], risk: str) -> str:
    category = _clean_text(issue.get("category"))
    if category == "exact_active_duplicate":
        return "Cost department should keep the trusted active item and manually archive duplicates after review."
    if category == "invalid_main_price":
        return "Verify and fill a positive reference price; if the price cannot be confirmed, withdraw or archive before trial."
    if category == "missing_unit":
        return "Fill a clear unit before using this item in trial quotes."
    if category == "same_name_mixed_units":
        return "Confirm whether the unit difference is legitimate; normalize unit wording where possible."
    if category in RAG_CATEGORIES:
        return "After data cleanup, cost_approver should review sync records and trigger active-to-RAG sync if needed."
    suggestion = _clean_text(issue.get("suggestion"))
    if suggestion:
        return suggestion
    if risk == "high":
        return "Review and resolve before small-scope trial."
    if risk == "medium":
        return "Review before or during the first trial batch."
    return "Keep for observation during trial."


def _action_from_quality_issue(
    issue: Mapping[str, Any],
    *,
    issue_id: str,
    item_lookup: Mapping[int, Any],
    quote_usage: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    item_id = issue.get("cost_item_id")
    item_id_int = int(item_id) if isinstance(item_id, int) or (isinstance(item_id, str) and item_id.isdigit()) else None
    usage = _quote_usage_for_item(quote_usage, item_id_int)
    risk = _risk_from_issue(issue, usage["count"])
    issue_type = _clean_text(issue.get("category"))
    evidence = issue.get("evidence") or {}
    if usage["count"]:
        evidence = {**evidence, "quote_usage_count": usage["count"], "latest_quote_used_at": usage["latest_used_at"]}
    return {
        "issue_id": issue_id,
        "risk_level": risk,
        "trial_blocker": "yes" if risk == "high" else "no",
        "issue_type": issue_type,
        "cost_item_id": item_id_int or "",
        "related_item_ids": ",".join(str(value) for value in issue.get("related_item_ids", []) if value),
        "status": _status_for_issue(issue, item_lookup),
        "source": _source_for_issue(issue, item_lookup),
        "item_name": _clean_text(issue.get("item_name")),
        "spec": _clean_text(issue.get("spec")),
        "unit": _clean_text(issue.get("unit")),
        "price": issue.get("price") if issue.get("price") is not None else "",
        "quote_usage_count": usage["count"],
        "latest_quote_used_at": usage["latest_used_at"],
        "evidence": evidence,
        "suggested_action": _suggested_action(issue, risk),
        "owner": _owner_for_issue(issue_type),
        "done": "",
        "notes": "",
    }


def _draft_actions(
    items: Iterable[Any],
    *,
    active_keys: set[tuple[str, str, str, str, str]],
    start_index: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    index = start_index
    for item in items:
        if _clean_text(getattr(item, "status", "")) != COST_STATUS_DRAFT:
            continue
        item_id = _item_id(item)
        source = _clean_text(getattr(item, "source", ""))
        issue_type = ""
        risk = "medium"
        action = ""
        if _positive_price(getattr(item, "price", None)) is None or not _clean_text(getattr(item, "unit", "")):
            issue_type = "draft_missing_price_or_unit"
            risk = "high"
            action = "Fill price and unit before deciding whether this draft can become active; otherwise keep draft or archive."
        elif source == COST_SOURCE_AI_SUGGESTED:
            issue_type = "ai_suggested_draft_review"
            risk = "medium"
            action = "Cost department should review AI-suggested draft, complete fields, then activate only if reusable."
        if _duplicate_key(item) in active_keys:
            issue_type = "draft_duplicate_with_active" if not issue_type else f"{issue_type};draft_duplicate_with_active"
            risk = "high" if risk == "high" else "medium"
            action = "Draft matches an existing active item; verify whether to archive it or adjust spec/name before activation."
        if not issue_type:
            continue
        index += 1
        actions.append(
            {
                "issue_id": f"BIZ2T-{index:04d}",
                "risk_level": risk,
                "trial_blocker": "yes" if risk == "high" else "no",
                "issue_type": issue_type,
                "cost_item_id": item_id or "",
                "related_item_ids": "",
                "status": COST_STATUS_DRAFT,
                "source": source,
                "item_name": _clean_text(getattr(item, "item_name", "")),
                "spec": _clean_text(getattr(item, "spec", "")),
                "unit": _clean_text(getattr(item, "unit", "")),
                "price": _price(getattr(item, "price", None)) if _price(getattr(item, "price", None)) is not None else "",
                "quote_usage_count": 0,
                "latest_quote_used_at": "",
                "evidence": {
                    "source": source,
                    "created_at": _iso(getattr(item, "created_at", None)),
                    "updated_at": _iso(getattr(item, "updated_at", None)),
                },
                "suggested_action": action,
                "owner": "cost_editor/cost_approver",
                "done": "",
                "notes": "",
            }
        )
    return actions


def build_cost_governance_pack(
    items: Iterable[Any],
    quality_result: Mapping[str, Any],
    *,
    quote_usage: Mapping[int, Mapping[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    all_items = list(items)
    item_lookup = {_item_id(item): item for item in all_items if _item_id(item) is not None}
    quote_usage = quote_usage or {}

    actions: list[dict[str, Any]] = []
    for index, issue in enumerate(quality_result.get("issues", []), start=1):
        actions.append(
            _action_from_quality_issue(
                issue,
                issue_id=f"BIZ2T-{index:04d}",
                item_lookup=item_lookup,
                quote_usage=quote_usage,
            )
        )

    active_keys = {
        _duplicate_key(item)
        for item in all_items
        if _clean_text(getattr(item, "status", "")) == COST_STATUS_ACTIVE
    }
    actions.extend(_draft_actions(all_items, active_keys=active_keys, start_index=len(actions)))
    actions.sort(key=lambda row: ({"high": 0, "medium": 1, "low": 2}.get(row["risk_level"], 9), row["issue_id"]))

    status_counts = Counter(_clean_text(getattr(item, "status", "")) or "unknown" for item in all_items)
    source_counts = Counter(_clean_text(getattr(item, "source", "")) or "unknown" for item in all_items)
    risk_counts = Counter(row["risk_level"] for row in actions)
    blocker_count = sum(1 for row in actions if row["trial_blocker"] == "yes")
    quote_used_item_ids = {item_id for item_id, data in quote_usage.items() if int(data.get("count") or 0) > 0}
    active_item_ids = {_item_id(item) for item in all_items if _clean_text(getattr(item, "status", "")) == COST_STATUS_ACTIVE}

    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "scope": "cost_items all statuses + BIZ-2k active quality result",
        "summary": {
            "total_count": len(all_items),
            "status_counts": dict(sorted(status_counts.items())),
            "source_counts": dict(sorted(source_counts.items())),
            "quote_used_active_count": len(active_item_ids & quote_used_item_ids),
            "action_count": len(actions),
            "risk_counts": dict(sorted(risk_counts.items())),
            "trial_blocker_count": blocker_count,
            "latest_rag_sync": quality_result.get("sync_summary") or {},
        },
        "actions": actions,
        "trial_readiness": build_trial_readiness(actions, quality_result),
    }


def build_trial_readiness(actions: list[Mapping[str, Any]], quality_result: Mapping[str, Any]) -> dict[str, Any]:
    high_count = sum(1 for row in actions if row.get("risk_level") == "high")
    medium_count = sum(1 for row in actions if row.get("risk_level") == "medium")
    latest_sync = quality_result.get("sync_summary") or {}
    sync_ok = bool(latest_sync) and latest_sync.get("status") == "success"
    blockers: list[str] = []
    if high_count:
        blockers.append("High-risk governance actions remain.")
    if not sync_ok:
        blockers.append("Latest active-to-RAG sync is missing or not successful.")
    recommendation = "ready_with_monitoring" if not blockers else "cleanup_before_trial"
    if not blockers and medium_count:
        recommendation = "ready_with_medium_risk_watchlist"
    return {
        "recommendation": recommendation,
        "high_risk_count": high_count,
        "medium_risk_count": medium_count,
        "sync_ok": sync_ok,
        "blockers": blockers,
    }


def build_governance_summary_markdown(pack: Mapping[str, Any]) -> str:
    summary = pack.get("summary") or {}
    readiness = pack.get("trial_readiness") or {}
    latest_sync = summary.get("latest_rag_sync") or {}
    risk_counts = summary.get("risk_counts") or {}
    status_counts = summary.get("status_counts") or {}
    lines = [
        "# BIZ-2t Cost Data Governance Summary",
        "",
        f"- Generated at: {pack.get('generated_at')}",
        f"- Scope: {pack.get('scope')}",
        f"- Total cost items: {summary.get('total_count', 0)}",
        f"- Status counts: {json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}",
        f"- Action count: {summary.get('action_count', 0)}",
        f"- Risk counts: high={risk_counts.get('high', 0)} / medium={risk_counts.get('medium', 0)} / low={risk_counts.get('low', 0)}",
        f"- Trial blockers: {summary.get('trial_blocker_count', 0)}",
        f"- Quote-used active items: {summary.get('quote_used_active_count', 0)}",
        "",
        "## Trial Readiness",
        "",
        f"- Recommendation: {readiness.get('recommendation')}",
        f"- Latest RAG sync ok: {readiness.get('sync_ok')}",
    ]
    blockers = readiness.get("blockers") or []
    if blockers:
        lines.append("- Blockers:")
        lines.extend(f"  - {blocker}" for blocker in blockers)
    else:
        lines.append("- Blockers: none")

    lines.extend(["", "## Latest RAG Sync", ""])
    if latest_sync:
        for key in ("id", "status", "requested_count", "synced_count", "started_at", "message", "error"):
            lines.append(f"- {key}: {latest_sync.get(key)}")
    else:
        lines.append("- No sync record loaded.")

    lines.extend(
        [
            "",
            "## Priority Actions",
            "",
            "| Risk | Issue type | Item | Action | Owner |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in pack.get("actions", [])[:80]:
        item_label = f"#{row.get('cost_item_id')} {row.get('item_name')}".strip()
        lines.append(
            f"| {row.get('risk_level')} | {row.get('issue_type')} | {item_label or '-'} | "
            f"{row.get('suggested_action')} | {row.get('owner')} |"
        )
    if not pack.get("actions"):
        lines.append("| - | - | - | No governance actions. | - |")
    lines.append("")
    return "\n".join(lines)


def write_governance_actions_csv(pack: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACTION_HEADERS)
        writer.writeheader()
        for action in pack.get("actions", []):
            row = {key: action.get(key, "") for key in ACTION_HEADERS}
            row["evidence"] = json.dumps(row.get("evidence") or {}, ensure_ascii=False, sort_keys=True)
            writer.writerow(row)


def write_governance_actions_xlsx(pack: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_sheet.append(["field", "value"])
    summary = pack.get("summary") or {}
    readiness = pack.get("trial_readiness") or {}
    summary_sheet.append(["generated_at", pack.get("generated_at")])
    summary_sheet.append(["total_count", summary.get("total_count")])
    summary_sheet.append(["status_counts", json.dumps(summary.get("status_counts") or {}, ensure_ascii=False, sort_keys=True)])
    summary_sheet.append(["risk_counts", json.dumps(summary.get("risk_counts") or {}, ensure_ascii=False, sort_keys=True)])
    summary_sheet.append(["trial_blocker_count", summary.get("trial_blocker_count")])
    summary_sheet.append(["recommendation", readiness.get("recommendation")])

    actions_sheet = workbook.create_sheet("Actions")
    actions_sheet.append(ACTION_HEADERS)
    for action in pack.get("actions", []):
        actions_sheet.append(
            [
                json.dumps(action.get(header), ensure_ascii=False, sort_keys=True)
                if header == "evidence"
                else action.get(header)
                for header in ACTION_HEADERS
            ]
        )

    for sheet in workbook.worksheets:
        for column_cells in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 56)
    workbook.save(path)
