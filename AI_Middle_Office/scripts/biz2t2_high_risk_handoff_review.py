from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "biz2t" / "20260528_current"
DEFAULT_INPUT = DEFAULT_REPORT_DIR / "cost_governance_high_risk_handoff.csv"

REQUIRED_ISSUE_COUNT = 5
REVIEW_FIELDS = ("reviewer", "decision", "reason", "need_rag_sync", "done_at")
ALLOWED_DECISIONS = {"keep_active", "withdraw_to_draft", "archive", "accepted_risk"}
ALLOWED_SOURCE_PRICE_TYPES = {
    "client_tax_excluded_price",
    "subcontract_composite_price",
    "crew_benchmark_price",
    "other",
}
ALLOWED_RAG_SYNC = {"yes", "no"}

OUTPUT_HEADERS = [
    "issue_id",
    "cost_item_id",
    "item_name",
    "decision",
    "review_status",
    "trial_blocker",
    "missing_fields",
    "invalid_fields",
    "need_rag_sync",
    "reviewer",
    "done_at",
    "review_note",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review BIZ-2t-1 high-risk handoff completion.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="High-risk handoff CSV path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_REPORT_DIR), help="Report output directory.")
    return parser.parse_args()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _positive_number(value: str) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: _clean(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def _review_row(row: dict[str, str]) -> dict[str, Any]:
    decision = _clean(row.get("decision"))
    missing: list[str] = []
    invalid: list[str] = []

    if not decision:
        missing.extend(REVIEW_FIELDS)
        return {
            "issue_id": row.get("issue_id", ""),
            "cost_item_id": row.get("cost_item_id", ""),
            "item_name": row.get("item_name", ""),
            "decision": "",
            "review_status": "pending",
            "trial_blocker": "yes",
            "missing_fields": missing,
            "invalid_fields": invalid,
            "need_rag_sync": row.get("need_rag_sync", ""),
            "reviewer": row.get("reviewer", ""),
            "done_at": row.get("done_at", ""),
            "review_note": "成本部尚未填写复核结论，仍阻断试运行。",
        }

    for field in REVIEW_FIELDS:
        if not _clean(row.get(field)):
            missing.append(field)

    if decision not in ALLOWED_DECISIONS:
        invalid.append("decision")

    need_rag_sync = _clean(row.get("need_rag_sync")).lower()
    if need_rag_sync and need_rag_sync not in ALLOWED_RAG_SYNC:
        invalid.append("need_rag_sync")

    source_price_type = _clean(row.get("source_price_type"))
    source_price = _clean(row.get("source_price"))
    if source_price_type and source_price_type not in ALLOWED_SOURCE_PRICE_TYPES:
        invalid.append("source_price_type")
    if source_price and not _positive_number(source_price):
        invalid.append("source_price")

    if decision == "keep_active":
        if not source_price_type:
            missing.append("source_price_type")
        if not source_price:
            missing.append("source_price")
        elif not _positive_number(source_price):
            invalid.append("source_price")

    if missing or invalid:
        status = "invalid" if invalid else "pending"
        note = "复核字段不完整或不符合口径，仍阻断试运行。"
        blocker = "yes"
    elif decision == "accepted_risk":
        status = "accepted_risk"
        note = "成本部已明确接受风险并给出说明，可作为已知风险进入试运行准入判断。"
        blocker = "no"
    else:
        status = "cleared"
        note = "成本部处理结论字段完整，可视为本条高风险交接已闭环。"
        blocker = "no"

    return {
        "issue_id": row.get("issue_id", ""),
        "cost_item_id": row.get("cost_item_id", ""),
        "item_name": row.get("item_name", ""),
        "decision": decision,
        "review_status": status,
        "trial_blocker": blocker,
        "missing_fields": sorted(set(missing)),
        "invalid_fields": sorted(set(invalid)),
        "need_rag_sync": row.get("need_rag_sync", ""),
        "reviewer": row.get("reviewer", ""),
        "done_at": row.get("done_at", ""),
        "review_note": note,
    }


def _build_summary(rows: list[dict[str, str]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for review in reviews:
        status = review["review_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    pending_count = status_counts.get("pending", 0)
    invalid_count = status_counts.get("invalid", 0)
    accepted_risk_count = status_counts.get("accepted_risk", 0)
    cleared_count = status_counts.get("cleared", 0)
    blocker_count = sum(1 for review in reviews if review["trial_blocker"] == "yes")
    need_rag_sync_count = sum(1 for review in reviews if _clean(review.get("need_rag_sync")).lower() == "yes")

    if len(rows) != REQUIRED_ISSUE_COUNT:
        recommendation = "invalid_input"
        conclusion = f"输入记录数为 {len(rows)}，不是预期的 {REQUIRED_ISSUE_COUNT} 条。"
    elif blocker_count:
        recommendation = "still_blocked"
        conclusion = "高风险交接尚未闭环，仍不建议启动正式小范围试运行。"
    elif accepted_risk_count:
        recommendation = "ready_with_known_risks"
        conclusion = "5 条高风险项均有处理结论，但包含 accepted_risk，需要在试运行样例中登记为已知风险。"
    else:
        recommendation = "ready_for_trial_material_registration"
        conclusion = "5 条高风险项均已闭环，可进入试运行样例登记与首日准备。"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_record_count": len(rows),
        "expected_record_count": REQUIRED_ISSUE_COUNT,
        "status_counts": status_counts,
        "cleared_count": cleared_count,
        "accepted_risk_count": accepted_risk_count,
        "pending_count": pending_count,
        "invalid_count": invalid_count,
        "trial_blocker_count": blocker_count,
        "need_rag_sync_count": need_rag_sync_count,
        "recommendation": recommendation,
        "conclusion": conclusion,
    }


def _write_csv(path: Path, reviews: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        for review in reviews:
            row = {key: review.get(key, "") for key in OUTPUT_HEADERS}
            row["missing_fields"] = ";".join(review.get("missing_fields") or [])
            row["invalid_fields"] = ";".join(review.get("invalid_fields") or [])
            writer.writerow(row)


def _write_markdown(path: Path, summary: dict[str, Any], reviews: list[dict[str, Any]], input_path: Path) -> None:
    lines = [
        "# BIZ-2t-2 高风险整改结果复核报告",
        "",
        f"> 生成时间：{summary['generated_at']}  ",
        f"> 输入文件：`{input_path}`  ",
        "> 结论口径：只读复核，不写数据库，不自动改价、撤回、归档、启用 active，不触发 RAG 同步。",
        "",
        "## 1. 总体结论",
        "",
        f"- 输入记录数：{summary['input_record_count']} / {summary['expected_record_count']}",
        f"- 已闭环：{summary['cleared_count']}",
        f"- 已接受风险：{summary['accepted_risk_count']}",
        f"- 待处理：{summary['pending_count']}",
        f"- 填写无效：{summary['invalid_count']}",
        f"- 仍阻断试运行：{summary['trial_blocker_count']}",
        f"- 标记需要 RAG 同步：{summary['need_rag_sync_count']}",
        f"- 建议：`{summary['recommendation']}`",
        f"- 结论：{summary['conclusion']}",
        "",
        "## 2. 逐条复核",
        "",
        "| issue_id | cost_item_id | 项目名称 | decision | 状态 | 是否阻断 | 缺失字段 | 无效字段 | 复核说明 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for review in reviews:
        lines.append(
            "| {issue_id} | {cost_item_id} | {item_name} | {decision} | {review_status} | {trial_blocker} | {missing} | {invalid} | {note} |".format(
                issue_id=review["issue_id"],
                cost_item_id=review["cost_item_id"],
                item_name=review["item_name"],
                decision=review["decision"] or "-",
                review_status=review["review_status"],
                trial_blocker=review["trial_blocker"],
                missing=", ".join(review.get("missing_fields") or []) or "-",
                invalid=", ".join(review.get("invalid_fields") or []) or "-",
                note=review["review_note"],
            )
        )
    lines.extend(
        [
            "",
            "## 3. 下一步",
            "",
            "1. 成本部补齐 `reviewer`、`decision`、`reason`、`need_rag_sync`、`done_at`。",
            "2. 若 `decision=keep_active`，还需补齐 `source_price_type` 和正数 `source_price`。",
            "3. 若 `decision=accepted_risk`，需给出可审计的人工风险说明，并在 BIZ-2u-1 样例登记中标为已知风险。",
            "4. 若任何记录标记 `need_rag_sync=yes`，由 `cost_approver` 单独判断并手动触发 active 到 RAG 同步。",
            "5. 本报告变为无阻断后，再进入 BIZ-2u-1 样例登记和首日小范围内网试运行准备。",
            "",
            "## 4. 边界",
            "",
            "- 本报告不代表系统已经修改成本库。",
            "- 本报告不代表正式试运行已经启动。",
            "- 本报告不改变报价规则、价格口径、N8N/Dify、RAG 同步逻辑或成本库 active 规则。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(input_path)
    reviews = [_review_row(row) for row in rows]
    summary = _build_summary(rows, reviews)
    payload = {"summary": summary, "reviews": reviews}

    json_path = output_dir / "high_risk_handoff_review.json"
    csv_path = output_dir / "high_risk_handoff_review.csv"
    md_path = output_dir / "high_risk_handoff_review.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, reviews)
    _write_markdown(md_path, summary, reviews, input_path)

    print(
        json.dumps(
            {
                "status": "ok",
                "summary": summary,
                "outputs": {
                    "markdown": str(md_path),
                    "csv": str(csv_path),
                    "json": str(json_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
