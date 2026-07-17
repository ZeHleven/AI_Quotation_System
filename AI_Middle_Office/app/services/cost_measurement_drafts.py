from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.cost_item import (
    CHANGE_TYPE_STATUS,
    COST_SOURCE_IMPORTED,
    COST_STATUS_ACTIVE,
    COST_STATUS_ARCHIVED,
    COST_STATUS_DRAFT,
    PRICE_TYPE_COMBINED,
    CostItem,
    CostItemHistory,
)
from app.models.cost_measurement import PRICING_MODE_COMPOSITE, MEASUREMENT_STATUS_LOCKED, CostMeasurement, CostMeasurementLine
from app.models.user import User
from app.services.cost_duplicate_guard import find_existing_duplicate_item, item_snapshot, normalize_cost_text
from app.services.cost_items import create_cost_item
from app.services.cost_measurement import clean_text, write_measurement_event


COST_MEASUREMENT_DRAFT_CATEGORY = "\u5386\u53f2\u6210\u672c\u6d4b\u7b97"
COST_MEASUREMENT_DRAFT_SUBCATEGORY = "\u672a\u5206\u7c7b"
COST_MEASUREMENT_DRAFT_REASON = "COST-MEASURE-2 \u9501\u5b9a\u6d4b\u7b97\u660e\u7ec6\u6c89\u6dc0\u4e3a\u6210\u672c\u5e93 draft"
ELIGIBLE_REVIEW_STATUSES = {"ready", "reviewed", "accepted"}


class CostMeasurementDraftError(ValueError):
    pass


def _rounded(value: Any) -> float:
    return round(float(value or 0), 6)


def _effective_date(measurement: CostMeasurement) -> date | None:
    value = measurement.locked_at or measurement.updated_at or measurement.created_at
    return value.date() if value else None


def _cost_item_notes(measurement: CostMeasurement, line: CostMeasurementLine) -> str:
    main_material_with_loss = float(line.main_material_unit_price or 0) * (1 + float(line.material_loss_rate or 0))
    rows = [
        "[COST-MEASURE-2 \u5386\u53f2\u6210\u672c\u6d4b\u7b97\u6c89\u6dc0]",
        f"measurement_id: {measurement.id}",
        f"measurement_code: {measurement.measurement_code}",
        f"measurement_uuid: {measurement.measurement_uuid}",
        f"project_name: {measurement.project_name or '-'}",
        f"source_filename: {measurement.source_filename or '-'}",
        f"line_id: {line.id}",
        f"line_key: {line.line_key}",
        f"source_sheet: {line.source_sheet or '-'}",
        f"source_row_index: {line.source_row_index if line.source_row_index is not None else '-'}",
        f"historical_quantity: {_rounded(line.quantity)}",
        f"review_status: {line.review_status}",
        f"pricing_mode: {line.pricing_mode}",
        f"source_unit_price: {_rounded(line.source_unit_price)}",
        f"calculated_unit_price: {_rounded(line.calculated_unit_price)}",
        f"direct_unit_price: {_rounded(line.direct_unit_price)}",
        f"labor_unit_price: {_rounded(line.labor_unit_price)}",
        f"main_material_unit_price_before_loss: {_rounded(line.main_material_unit_price)}",
        f"material_loss_rate: {_rounded(line.material_loss_rate)}",
        f"main_material_unit_price_after_loss: {_rounded(main_material_with_loss)}",
        f"auxiliary_machinery_unit_price: {_rounded(line.auxiliary_machinery_unit_price)}",
        f"subcontract_unit_price: {_rounded(line.subcontract_unit_price)}",
        f"subcontract_is_composite_proxy: {str(line.pricing_mode == PRICING_MODE_COMPOSITE).lower()}",
        f"management_unit_price: {_rounded(line.management_unit_price)}",
        f"profit_unit_price: {_rounded(line.profit_unit_price)}",
        f"management_rate: {_rounded(measurement.management_rate)}",
        f"profit_rate: {_rounded(measurement.profit_rate)}",
        f"tax_rate: {_rounded(measurement.tax_rate)}",
    ]
    return "\n".join(rows)


def measurement_line_cost_item_payload(measurement: CostMeasurement, line: CostMeasurementLine) -> dict[str, Any]:
    main_material_with_loss = float(line.main_material_unit_price or 0) * (1 + float(line.material_loss_rate or 0))
    management_profit = float(line.management_unit_price or 0) + float(line.profit_unit_price or 0)
    subcontract = 0.0 if line.pricing_mode == PRICING_MODE_COMPOSITE else float(line.subcontract_unit_price or 0)
    return {
        "category": COST_MEASUREMENT_DRAFT_CATEGORY,
        "subcategory": clean_text(line.section_name, 128) or clean_text(line.source_sheet, 128) or COST_MEASUREMENT_DRAFT_SUBCATEGORY,
        "item_name": clean_text(line.item_name, 255) or "",
        "spec": clean_text(line.feature),
        "unit": clean_text(line.unit, 64) or "",
        "price": _rounded(line.calculated_unit_price),
        "client_tax_excluded_price": _rounded(line.calculated_unit_price),
        "client_labor_price": _rounded(line.labor_unit_price),
        "client_main_material_price": _rounded(main_material_with_loss),
        "client_auxiliary_material_price": _rounded(line.auxiliary_machinery_unit_price),
        "client_direct_fee": _rounded(line.direct_unit_price),
        "client_management_profit": _rounded(management_profit),
        "subcontract_composite_price": _rounded(subcontract) if subcontract > 0 else None,
        "subcontract_labor_price": None,
        "subcontract_main_material_price": None,
        "subcontract_auxiliary_material_price": None,
        "crew_benchmark_price": None,
        "price_type": PRICE_TYPE_COMBINED,
        "source": COST_SOURCE_IMPORTED,
        "effective_date": _effective_date(measurement),
        "notes": _cost_item_notes(measurement, line),
    }


def _candidate_identity(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        normalize_cost_text(payload.get("item_name")),
        normalize_cost_text(payload.get("spec")),
        normalize_cost_text(payload.get("unit")),
        str(payload.get("price_type") or PRICE_TYPE_COMBINED),
    )


def _line_base(line: CostMeasurementLine, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "line_id": line.id,
        "line_key": line.line_key,
        "source_sheet": line.source_sheet,
        "source_row_index": line.source_row_index,
        "sequence_no": line.sequence_no,
        "section_name": line.section_name,
        "item_name": line.item_name,
        "feature": line.feature,
        "unit": line.unit,
        "quantity": line.quantity,
        "line_type": line.line_type,
        "pricing_mode": line.pricing_mode,
        "review_status": line.review_status,
        "source_unit_price": line.source_unit_price,
        "calculated_unit_price": line.calculated_unit_price,
        "direct_unit_price": line.direct_unit_price,
        "labor_unit_price": line.labor_unit_price,
        "main_material_unit_price": line.main_material_unit_price,
        "main_material_unit_price_after_loss": payload.get("client_main_material_price"),
        "auxiliary_machinery_unit_price": line.auxiliary_machinery_unit_price,
        "subcontract_unit_price": line.subcontract_unit_price,
        "management_profit_unit_price": payload.get("client_management_profit"),
        "cost_item_payload": payload,
        "can_create": False,
        "candidate_status": "blocked",
        "reason_code": None,
        "reason_message": None,
        "existing_cost_item": None,
    }


def _blocked(candidate: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    candidate["candidate_status"] = "blocked"
    candidate["reason_code"] = code
    candidate["reason_message"] = message
    return candidate


def _selected_lines(measurement: CostMeasurement, line_ids: Iterable[int] | None) -> list[CostMeasurementLine]:
    lines = list(measurement.lines)
    if line_ids is None:
        return lines
    ordered_ids = list(dict.fromkeys(int(line_id) for line_id in line_ids))
    by_id = {line.id: line for line in lines}
    missing = [line_id for line_id in ordered_ids if line_id not in by_id]
    if missing:
        raise CostMeasurementDraftError(f"COST_MEASUREMENT_LINES_NOT_FOUND:{','.join(map(str, missing[:20]))}")
    return [by_id[line_id] for line_id in ordered_ids]


def build_measurement_cost_draft_preview(
    db: Session,
    measurement: CostMeasurement,
    *,
    line_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    if measurement.status != MEASUREMENT_STATUS_LOCKED:
        raise CostMeasurementDraftError("MEASUREMENT_MUST_BE_LOCKED")

    candidates: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    for line in _selected_lines(measurement, line_ids):
        payload = measurement_line_cost_item_payload(measurement, line)
        candidate = _line_base(line, payload)
        if line.review_status not in ELIGIBLE_REVIEW_STATUSES:
            candidates.append(_blocked(candidate, "LINE_REVIEW_REQUIRED", "\u8be5\u6d4b\u7b97\u884c\u5c1a\u672a\u5b8c\u6210\u4eba\u5de5\u590d\u6838"))
            continue
        if not payload["item_name"]:
            candidates.append(_blocked(candidate, "ITEM_NAME_MISSING", "\u9879\u76ee\u540d\u79f0\u4e3a\u7a7a"))
            continue
        if not payload["unit"]:
            candidates.append(_blocked(candidate, "UNIT_MISSING", "\u8ba1\u91cf\u5355\u4f4d\u4e3a\u7a7a"))
            continue
        if float(payload["price"] or 0) <= 0:
            candidates.append(_blocked(candidate, "POSITIVE_PRICE_REQUIRED", "\u91cd\u7b97\u7efc\u5408\u6210\u672c\u5355\u4ef7\u5fc5\u987b\u5927\u4e8e 0"))
            continue

        existing = find_existing_duplicate_item(
            db,
            payload,
            statuses=(COST_STATUS_ACTIVE, COST_STATUS_DRAFT, COST_STATUS_ARCHIVED),
        )
        if existing is not None:
            candidate["existing_cost_item"] = item_snapshot(existing)
            if existing.status == COST_STATUS_ACTIVE:
                candidate["candidate_status"] = "existing_active"
                candidate["reason_code"] = "ACTIVE_DUPLICATE"
                candidate["reason_message"] = "\u5df2\u5b58\u5728\u76f8\u540c\u6216\u9ad8\u5ea6\u76f8\u4f3c\u7684 active \u6210\u672c\u6761\u76ee\uff0c\u4e0d\u8986\u76d6"
                candidates.append(candidate)
                continue
            if existing.status == COST_STATUS_DRAFT:
                candidate["candidate_status"] = "existing_draft"
                candidate["reason_code"] = "DRAFT_DUPLICATE"
                candidate["reason_message"] = "\u5df2\u5b58\u5728\u76f8\u540c\u6216\u9ad8\u5ea6\u76f8\u4f3c\u7684 draft \u6210\u672c\u6761\u76ee\uff0c\u4e0d\u91cd\u590d\u521b\u5efa"
                candidates.append(candidate)
                continue

        identity = _candidate_identity(payload)
        if identity in seen:
            candidate["can_create"] = True
            candidate["candidate_status"] = "duplicate_within_measurement"
            candidate["reason_code"] = "DUPLICATE_WITHIN_MEASUREMENT"
            candidate["reason_message"] = f"\u4e0e\u6d4b\u7b97\u884c #{seen[identity]} \u91cd\u590d\uff0c\u9ed8\u8ba4\u4e0d\u9009\uff1b\u53ef\u53d6\u6d88\u9996\u6761\u540e\u6539\u9009\u672c\u6761"
            candidates.append(candidate)
            continue
        seen[identity] = line.id

        candidate["can_create"] = True
        if existing is not None and existing.status == COST_STATUS_ARCHIVED:
            candidate["candidate_status"] = "ready_with_archived_history"
            candidate["reason_code"] = "ARCHIVED_DUPLICATE"
            candidate["reason_message"] = "\u5b58\u5728\u5df2\u5f52\u6863\u5386\u53f2\u6761\u76ee\uff0c\u53ef\u65b0\u5efa draft \u5e76\u4fdd\u7559\u5173\u8054"
        else:
            candidate["candidate_status"] = "ready"
            candidate["reason_code"] = "READY"
            candidate["reason_message"] = "\u53ef\u751f\u6210\u6210\u672c\u5e93 draft"
        candidates.append(candidate)

    summary = {
        "selected_line_count": len(candidates),
        "eligible_count": sum(1 for row in candidates if row["can_create"] and row["candidate_status"] != "duplicate_within_measurement"),
        "blocked_count": sum(1 for row in candidates if not row["can_create"]),
        "review_blocked_count": sum(1 for row in candidates if row["reason_code"] == "LINE_REVIEW_REQUIRED"),
        "existing_active_count": sum(1 for row in candidates if row["candidate_status"] == "existing_active"),
        "existing_draft_count": sum(1 for row in candidates if row["candidate_status"] == "existing_draft"),
        "within_measurement_duplicate_count": sum(1 for row in candidates if row["candidate_status"] == "duplicate_within_measurement"),
        "archived_duplicate_count": sum(1 for row in candidates if row["candidate_status"] == "ready_with_archived_history"),
    }
    return {
        "measurement_id": measurement.id,
        "measurement_code": measurement.measurement_code,
        "measurement_status": measurement.status,
        "summary": summary,
        "candidates": candidates,
    }


def create_measurement_cost_drafts(
    db: Session,
    measurement: CostMeasurement,
    user: User,
    *,
    line_ids: Iterable[int],
    note: str | None = None,
) -> dict[str, Any]:
    preview = build_measurement_cost_draft_preview(db, measurement, line_ids=line_ids)
    batch_note = clean_text(note, 2000)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in preview["candidates"]:
        if not candidate["can_create"] or candidate["candidate_status"] == "duplicate_within_measurement":
            skipped.append(
                {
                    "line_id": candidate["line_id"],
                    "item_name": candidate["item_name"],
                    "reason_code": candidate["reason_code"],
                    "existing_cost_item": candidate["existing_cost_item"],
                }
            )
            continue
        payload = dict(candidate["cost_item_payload"])
        extra_notes: list[str] = []
        if candidate["existing_cost_item"]:
            extra_notes.append(f"archived_duplicate_cost_item_id: {candidate['existing_cost_item']['id']}")
        if batch_note:
            extra_notes.append(f"batch_note: {batch_note}")
        if extra_notes:
            payload["notes"] = "\n".join([payload.get("notes") or "", *extra_notes]).strip()
        item = create_cost_item(db, user, payload)
        db.flush()
        db.add(
            CostItemHistory(
                cost_item_id=item.id,
                old_status=None,
                new_status=COST_STATUS_DRAFT,
                change_type=CHANGE_TYPE_STATUS,
                changed_by=user.id,
                change_reason=batch_note or COST_MEASUREMENT_DRAFT_REASON,
            )
        )
        created.append(
            {
                "cost_item_id": item.id,
                "line_id": candidate["line_id"],
                "item_name": item.item_name,
                "unit": item.unit,
                "price": item.price,
                "status": item.status,
                "source": item.source,
            }
        )

    write_measurement_event(
        db,
        measurement,
        event_type="cost_drafts_created",
        actor_user_id=user.id,
        message=batch_note or f"\u751f\u6210 {len(created)} \u6761\u6210\u672c\u5e93 draft",
        payload={
            "requested_line_ids": [int(line_id) for line_id in line_ids],
            "created": created,
            "skipped": skipped,
        },
    )
    db.flush()
    return {
        "measurement_id": measurement.id,
        "measurement_code": measurement.measurement_code,
        "requested_count": len(preview["candidates"]),
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created_items": created,
        "skipped": skipped,
    }
