"""Business-bid V1.2: quote consistency and commercial response review."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from app.models.bidding import TenderResponseItem
from app.services.bidding_parser import loads_json

_MONEY_Q = Decimal("0.01")
_FORMAL_RESPONSE_STATUSES = {"confirmed", "done", "ignored"}
_UNRESOLVED_RESPONSE_STATUSES = {"to_clarify", "to_quote_allowance", "legal_review"}


def build_business_bid_v12_report(
    db: Session,
    run: Any,
    quote_import: Any | None,
    directory: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a read-only report from the frozen quote and business response items."""
    quote_checks, quote_summary = _quote_consistency_checks(quote_import, directory or [])
    response_items = _load_business_response_items(db, run)
    response_rows = [_serialize_response_item(item) for item in response_items]
    response_checks = _response_checks(response_rows)
    checks = quote_checks + response_checks
    blocking_items = [item for item in checks if item["severity"] == "high"]
    return {
        "version": "business_bid_v1.2",
        "quote_consistency": {
            "summary": quote_summary,
            "checks": quote_checks,
        },
        "business_responses": {
            "summary": _response_summary(response_rows),
            "items": response_rows,
            "checks": response_checks,
        },
        "formal_blocking_items": blocking_items,
        "formal_ready": not blocking_items,
    }


def _quote_consistency_checks(quote_import: Any | None, directory: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if quote_import is None:
        return [
            _check("quote_import_required", "未导入确认报价快照", "high", "请先从预算项目导入已确认报价。"),
        ], {"line_count": 0, "snapshot_line_count": 0, "total_amount": None, "calculated_total": None}

    snapshot = loads_json(_value(quote_import, "snapshot_json"), {}) or {}
    lines = snapshot.get("lines") if isinstance(snapshot, dict) and isinstance(snapshot.get("lines"), list) else []
    expected_count = _int_value(_value(quote_import, "line_count"))
    quoted_total = _decimal(_value(quote_import, "total_amount"))
    calculated_total = sum((_decimal(line.get("line_total")) for line in lines if isinstance(line, dict)), Decimal("0.00"))
    pricing_tables = [item for item in directory if item.get("content_type") == "pricing_table" or item.get("generation_strategy") == "from_cost_quote"]

    if not lines:
        checks.append(_check("quote_snapshot_empty", "确认报价快照没有可成册的清单行", "high", "请重新导入完整确认报价。"))
    if expected_count != len(lines):
        checks.append(_check("quote_line_count_mismatch", "报价快照行数与导入记录不一致", "high", f"导入记录 {expected_count} 行，快照实际 {len(lines)} 行。"))
    if quoted_total != calculated_total.quantize(_MONEY_Q, rounding=ROUND_HALF_UP):
        checks.append(_check("quote_total_mismatch", "报价合计与分项合价之和不一致", "high", f"导入合计 {_money_text(quoted_total)} 元，分项合计 {_money_text(calculated_total)} 元。"))
    if directory and not pricing_tables:
        checks.append(_check("pricing_table_missing", "已确认的商务标目录未识别到报价表", "high", "请在商务标格式目录中补充报价汇总表或工程量清单报价表。"))

    invalid_lines = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            invalid_lines.append((index, "清单行数据无效"))
            continue
        item_name = str(line.get("item_name") or "").strip()
        unit = str(line.get("unit") or "").strip()
        quantity = _decimal(line.get("quantity"))
        unit_price = _decimal(line.get("unit_price"))
        line_total = _decimal(line.get("line_total"))
        if not item_name or not unit:
            invalid_lines.append((index, "项目名称或单位缺失"))
        elif quantity <= 0:
            invalid_lines.append((index, "工程量未填写或小于等于零"))
        elif unit_price <= 0:
            invalid_lines.append((index, "单价未填写或小于等于零"))
        elif abs((quantity * unit_price).quantize(_MONEY_Q, rounding=ROUND_HALF_UP) - line_total) > _MONEY_Q:
            invalid_lines.append((index, "工程量、单价与合价不一致"))
    for index, reason in invalid_lines[:20]:
        checks.append(_check("quote_line_invalid", f"报价清单第 {index} 行：{reason}", "high", "请在预算报价中修正后重新导入商务标。", line_index=index))
    if len(invalid_lines) > 20:
        checks.append(_check("quote_line_invalid_more", f"另有 {len(invalid_lines) - 20} 行报价数据异常", "high", "请逐行复核确认报价快照。"))

    if not checks:
        checks.append(_check("quote_consistent", "报价快照与分项金额校验通过", "info", "清单行数、合计和分项计算一致。"))
    return checks, {
        "line_count": expected_count,
        "snapshot_line_count": len(lines),
        "total_amount": _money_text(quoted_total),
        "calculated_total": _money_text(calculated_total),
        "pricing_table_count": len(pricing_tables),
        "invalid_line_count": len(invalid_lines),
    }


def _load_business_response_items(db: Session, run: Any) -> list[Any]:
    rows = (
        db.query(TenderResponseItem)
        .filter(TenderResponseItem.parse_run_id == run.id)
        .order_by(TenderResponseItem.id.asc())
        .all()
    )
    return [row for row in rows if _is_business_response(row)]


def _is_business_response(row: Any) -> bool:
    category = str(_value(row, "response_category") or "")
    if category == "technical_requirement":
        return False
    normalized = loads_json(_value(row, "normalized_json"), {}) or {}
    if isinstance(normalized, dict) and normalized.get("package_key") == "technical":
        return False
    title = " ".join((str(_value(row, key) or "") for key in ("response_title", "source_text", "response_note")))
    return not any(token in title for token in ("施工组织", "技术方案", "安全文明", "质量验收"))


def _serialize_response_item(row: Any) -> dict[str, Any]:
    evidence = loads_json(_value(row, "evidence_json"), []) or []
    if not isinstance(evidence, list):
        evidence = []
    normalized = loads_json(_value(row, "normalized_json"), {}) or {}
    return {
        "response_item_uuid": _value(row, "response_item_uuid"),
        "title": _value(row, "response_title") or "未命名商务响应",
        "response_category": _value(row, "response_category"),
        "response_action": _value(row, "response_action"),
        "owner_role": _value(row, "owner_role") or "经营",
        "risk_level": _value(row, "risk_level") or "low",
        "status": _value(row, "status") or "pending",
        "response_note": _value(row, "response_note"),
        "reviewer_note": _value(row, "reviewer_note"),
        "source_text": _value(row, "source_text"),
        "evidence_count": len(evidence),
        "evidence": evidence,
        "coverage": normalized.get("coverage") if isinstance(normalized, dict) else {},
    }


def _response_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return [_check("business_response_missing", "尚未生成商务响应项", "high", "请先生成响应矩阵，并在商务标范围完成响应复核。")]
    checks: list[dict[str, Any]] = []
    for row in rows:
        status = row["status"]
        high_risk = row["risk_level"] == "high"
        requires_resolution = high_risk or status in _UNRESOLVED_RESPONSE_STATUSES
        if requires_resolution and status not in _FORMAL_RESPONSE_STATUSES:
            checks.append(_check(
                "business_response_unresolved",
                f"商务响应未闭环：{row['title']}",
                "high",
                "请补充响应说明、完成责任复核并将状态更新为已确认或已完成。",
                response_item_uuid=row["response_item_uuid"],
            ))
        elif high_risk and not row["response_note"]:
            checks.append(_check(
                "business_response_note_missing",
                f"高风险商务响应缺少我方响应说明：{row['title']}",
                "high",
                "请填写明确的商务响应或偏离说明。",
                response_item_uuid=row["response_item_uuid"],
            ))
        elif high_risk and not row["evidence_count"]:
            checks.append(_check(
                "business_response_evidence_missing",
                f"高风险商务响应缺少来源证据：{row['title']}",
                "high",
                "请关联招标要求原文或企业资料证据后复核。",
                response_item_uuid=row["response_item_uuid"],
            ))
    if not checks:
        checks.append(_check("business_response_reviewed", "商务响应高风险项已完成复核", "info", "可写入商务标响应章节，仍建议在提交前人工抽查。"))
    return checks


def _response_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "high_risk_count": sum(1 for row in rows if row["risk_level"] == "high"),
        "resolved_count": sum(1 for row in rows if row["status"] in _FORMAL_RESPONSE_STATUSES),
        "open_count": sum(1 for row in rows if row["status"] not in _FORMAL_RESPONSE_STATUSES),
    }


def _check(code: str, title: str, severity: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "title": title, "severity": severity, "detail": detail, **extra}


def _value(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in {None, ""} else 0)).quantize(_MONEY_Q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _money_text(value: Decimal) -> str:
    return f"{value.quantize(_MONEY_Q, rounding=ROUND_HALF_UP):,.2f}"