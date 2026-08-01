"""Business-bid assembly V1.1: directory, attachment index, and formal-export gate."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.bidding import BidMaterialRequirement, BidParseRun, BidProject
from app.services.bidding_file_format import get_bid_file_format_plan
from app.services.bidding_material_requirements import RESOLVED_STATUSES, build_bid_material_requirement_summary
from app.services.bidding_parser import loads_json
from app.services.bidding_business_bid_v12 import build_business_bid_v12_report
from app.services.bidding_business_bid_template import build_business_bid_template_plan
from app.services.bidding_business_bid_fields import build_business_bid_draft_field_plan


class BusinessBidAssemblyError(ValueError):
    def __init__(self, code: str, *, blocking_items: list[dict[str, Any]] | None = None):
        super().__init__(code)
        self.code = code
        self.blocking_items = blocking_items or []

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "blocking_items": self.blocking_items}


def build_business_bid_assembly(db: Session, project: BidProject, run: BidParseRun, quote_import: Any | None) -> dict[str, Any]:
    plan = get_bid_file_format_plan(db, run)
    directory = _business_directory(plan)
    rows = (
        db.query(BidMaterialRequirement)
        .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.package_key == "business")
        .order_by(BidMaterialRequirement.id.asc())
        .all()
    )
    requirements = [_serialize_requirement(row) for row in rows]
    attachment_index = _attachment_index(requirements)
    template_plan = build_business_bid_template_plan(directory)
    v12_report = build_business_bid_v12_report(db, run, quote_import, directory)
    draft_field_plan = build_business_bid_draft_field_plan(
        project=project,
        quote_import=quote_import,
        template_plan=template_plan,
        requirements=requirements,
        v12_report=v12_report,
    )
    blocking_items = _blocking_items(plan, quote_import, requirements, directory) + list(v12_report["formal_blocking_items"])
    formal_ready = not blocking_items
    return {
        "version": "business_bid_assembly_v1.4.1",
        "project_uuid": project.project_uuid,
        "run_uuid": run.run_uuid,
        "format_plan": {
            "plan_uuid": plan.plan_uuid if plan else None,
            "review_status": plan.review_status if plan else "missing",
            "confirmed": bool(plan and plan.review_status == "confirmed"),
        },
        "quote_import": {
            "available": quote_import is not None,
            "version_no": quote_import.version_no if quote_import else None,
            "line_count": quote_import.line_count if quote_import else 0,
            "total_amount": str(quote_import.total_amount) if quote_import else None,
        },
        "directory": directory,
        "template": template_plan,
        "draft_field_plan": draft_field_plan,
        "material_summary": build_bid_material_requirement_summary(rows),
        "requirements": requirements,
        "attachment_index": attachment_index,
        "v12_review": v12_report,
        "formal_ready": formal_ready,
        "draft_ready": quote_import is not None,
        "blocking_items": blocking_items,
        "export_modes": {
            "draft": {"available": quote_import is not None, "label": "草案 PDF"},
            "formal": {"available": formal_ready, "label": "正式版 PDF", "blocking_count": len(blocking_items)},
        },
    }


def ensure_business_bid_formal_exportable(assembly: dict[str, Any]) -> None:
    if assembly.get("formal_ready"):
        return
    raise BusinessBidAssemblyError(
        "BUSINESS_BID_FORMAL_EXPORT_BLOCKED",
        blocking_items=list(assembly.get("blocking_items") or []),
    )


def _business_directory(plan: Any | None) -> list[dict[str, Any]]:
    if not plan:
        return []
    structure = loads_json(plan.structure_json, {}) or {}
    packages = structure.get("packages") if isinstance(structure, dict) else []
    for package in packages if isinstance(packages, list) else []:
        if not isinstance(package, dict) or package.get("package_key") != "business":
            continue
        rows = []
        for index, item in enumerate(package.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            rows.append({
                "sequence": index,
                "item_key": item.get("item_key"),
                "title": item.get("item_title") or "未命名目录项",
                "content_type": item.get("content_type") or "other",
                "generation_strategy": item.get("generation_strategy") or "manual",
                "requires_signature": bool(item.get("requires_signature")),
                "requires_attachment": bool(item.get("requires_attachment")),
                "required": bool(item.get("is_required", True)),
                "owner_role": item.get("owner_role"),
            })
        return rows
    return []


def _serialize_requirement(row: BidMaterialRequirement) -> dict[str, Any]:
    submitted_profile_items = _submitted_list(row, "profile_item_uuids")
    submitted_file_ids = _submitted_list(row, "file_ids")
    return {
        "requirement_uuid": row.requirement_uuid,
        "format_item_key": row.format_item_key,
        "section_key": row.section_key,
        "title": row.title,
        "item_title": row.item_title,
        "requirement_type": row.requirement_type,
        "fulfillment_mode": row.fulfillment_mode,
        "priority": row.priority,
        "status": row.status,
        "profile_category": row.profile_category,
        "submitted_profile_item_uuids": submitted_profile_items,
        "submitted_file_ids": submitted_file_ids,
        "submitted_value": row.submitted_value,
        "notes": row.notes,
        "resolved": row.status in RESOLVED_STATUSES,
    }


def _submitted_list(row: BidMaterialRequirement, key: str) -> list[str]:
    normalized = loads_json(row.normalized_json, {}) or {}
    submission = normalized.get("manual_submission") if isinstance(normalized, dict) else {}
    values = submission.get(key) if isinstance(submission, dict) else []
    if isinstance(values, list):
        return [str(value) for value in values if value]
    fallback = row.submitted_profile_item_uuid if key == "profile_item_uuids" else row.submitted_file_id
    return [str(fallback)] if fallback else []


def _attachment_index(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, row in enumerate(requirements, start=1):
        profile_items = row["submitted_profile_item_uuids"]
        file_ids = row["submitted_file_ids"]
        evidence_count = len(profile_items) + len(file_ids) + (1 if row.get("submitted_value") else 0)
        result.append({
            "sequence": index,
            "title": row["title"],
            "requirement_uuid": row["requirement_uuid"],
            "status": row["status"],
            "resolved": row["resolved"],
            "evidence_count": evidence_count,
            "profile_item_uuids": profile_items,
            "file_ids": file_ids,
            "notes": row.get("notes"),
        })
    return result


def _blocking_items(plan: Any | None, quote_import: Any | None, requirements: list[dict[str, Any]], directory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if quote_import is None:
        items.append({"code": "quote_import_required", "title": "请先导入已确认的报价清单", "severity": "high"})
    if plan is None:
        items.append({"code": "format_plan_required", "title": "请先生成并确认投标文件格式目录", "severity": "high"})
    elif plan.review_status != "confirmed":
        items.append({"code": "format_plan_unconfirmed", "title": "商务标目录尚未确认", "severity": "high"})
    if plan and not directory:
        items.append({"code": "business_directory_empty", "title": "已确认目录中未识别到商务标目录项", "severity": "high"})
    for row in requirements:
        if row["resolved"]:
            continue
        if row["priority"] != "high":
            continue
        items.append({
            "code": "material_requirement_open",
            "title": row["title"],
            "requirement_uuid": row["requirement_uuid"],
            "severity": "high",
            "status": row["status"],
        })
    return items
