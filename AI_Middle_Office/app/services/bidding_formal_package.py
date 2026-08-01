"""Formal business-bid package V1.3: attachment preflight, merge, and audit record."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.models.bidding import BidBusinessBidFormalPackage, BidParseRun, BidProject
from app.models.enterprise_profile import EnterpriseProfileItem
from app.models.file_object import FileObject
from app.models.user import User
from app.services.file_storage import get_object_bytes, store_file_bytes


class FormalPackageError(RuntimeError):
    def __init__(self, code: str, *, blocking_items: list[dict[str, Any]] | None = None):
        super().__init__(code)
        self.code = code
        self.blocking_items = blocking_items or []

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "blocking_items": self.blocking_items}


def build_formal_package_preview(db: Session, run: BidParseRun, assembly: dict[str, Any]) -> dict[str, Any]:
    attachments, blocking_items = _collect_attachments(db, run, assembly)
    return {
        "version": "business_bid_formal_package_v1.4",
        "available": bool(assembly.get("formal_ready")) and not blocking_items,
        "formal_gate_ready": bool(assembly.get("formal_ready")),
        "attachment_count": len(attachments),
        "pdf_attachment_count": len([item for item in attachments if item["is_pdf"]]),
        "attachments": attachments,
        "blocking_items": blocking_items,
        "delivery_checklist": [
            "确认正式报价快照、商务响应和企业资料均为当前提交版本。",
            "核验合并后的每份扫描件清晰、完整、主体名称与签章正确。",
            "系统不生成印章或签字；提交前须由人工完成签章、原件核验和电子投标加密。",
        ],
    }


def create_formal_package(
    db: Session,
    project: BidProject,
    run: BidParseRun,
    quote_import: Any,
    assembly: dict[str, Any],
    core_pdf: bytes,
    user: User,
) -> tuple[BidBusinessBidFormalPackage, FileObject, dict[str, Any]]:
    preview = build_formal_package_preview(db, run, assembly)
    blocking_items = list(assembly.get("blocking_items") or []) + list(preview["blocking_items"])
    if blocking_items:
        raise FormalPackageError("BUSINESS_BID_FORMAL_PACKAGE_BLOCKED", blocking_items=blocking_items)

    writer = PdfWriter()
    writer.append(BytesIO(core_pdf))
    manifest_attachments = []
    for item in preview["attachments"]:
        try:
            content = get_object_bytes(item["object_name"], item["bucket"])
            reader = PdfReader(BytesIO(content))
            writer.append(reader)
        except Exception as exc:
            raise FormalPackageError(
                "BUSINESS_BID_ATTACHMENT_MERGE_FAILED",
                blocking_items=[{
                    "code": "attachment_merge_failed",
                    "title": f"附件无法合并：{item['original_filename']}",
                    "severity": "high",
                    "detail": str(exc)[:300],
                    "file_id": item["file_id"],
                }],
            ) from exc
        manifest_attachments.append({
            **item,
            "sha256": hashlib.sha256(content).hexdigest(),
            "page_count": len(reader.pages),
        })

    stream = BytesIO()
    writer.write(stream)
    content = stream.getvalue()
    filename = f"{_safe_filename(project.project_name)}_business_bid_formal_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    stored = store_file_bytes(
        content=content,
        original_filename=filename,
        content_type="application/pdf",
        username=user.username,
        purpose="business_bid_formal_package",
    )
    file_obj = FileObject(
        file_id=str(uuid.uuid4()),
        username=user.username,
        purpose="business_bid_formal_package",
        bucket=stored["bucket"],
        object_name=stored["object_name"],
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=stored["size_bytes"],
    )
    manifest = {
        "version": "business_bid_formal_package_v1.4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_uuid": project.project_uuid,
        "run_uuid": run.run_uuid,
        "quote_import": {
            "import_uuid": getattr(quote_import, "import_uuid", None),
            "version_no": getattr(quote_import, "version_no", None),
            "source_snapshot_sha256": getattr(quote_import, "source_snapshot_sha256", None),
        },
        "core_pdf_sha256": hashlib.sha256(core_pdf).hexdigest(),
        "core_pdf_page_count": len(PdfReader(BytesIO(core_pdf)).pages),
        "template": assembly.get("template") if isinstance(assembly.get("template"), dict) else None,
        "attachments": manifest_attachments,
        "merged_page_count": len(PdfReader(BytesIO(content)).pages),
        "delivery_checklist": preview["delivery_checklist"],
    }
    package = BidBusinessBidFormalPackage(
        package_uuid=str(uuid.uuid4()),
        project_id=project.id,
        parse_run_id=run.id,
        quote_import_id=quote_import.id,
        status="generated",
        manifest_json=json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        output_file_id=file_obj.file_id,
        created_by=user.id,
    )
    db.add(file_obj)
    db.add(package)
    return package, file_obj, manifest


def list_formal_packages(db: Session, project: BidProject, limit: int = 20) -> list[BidBusinessBidFormalPackage]:
    return (
        db.query(BidBusinessBidFormalPackage)
        .filter(BidBusinessBidFormalPackage.project_id == project.id)
        .order_by(BidBusinessBidFormalPackage.id.desc())
        .limit(limit)
        .all()
    )


def serialize_formal_package(row: BidBusinessBidFormalPackage) -> dict[str, Any]:
    manifest = _json_load(row.manifest_json, {})
    file_obj = row.output_file
    return {
        "package_uuid": row.package_uuid,
        "status": row.status,
        "quote_import_id": row.quote_import_id,
        "output_file_id": row.output_file_id,
        "output_filename": file_obj.original_filename if file_obj else None,
        "size_bytes": file_obj.size_bytes if file_obj else None,
        "manifest": manifest,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _collect_attachments(db: Session, run: BidParseRun, assembly: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requirements = list(assembly.get("requirements") or [])
    directory_by_key = {str(item.get("item_key")): item for item in assembly.get("directory") or []}
    direct_file_ids: set[str] = set()
    profile_ids: set[str] = set()
    attachment_requirements: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for requirement in requirements:
        if not requirement.get("resolved") or requirement.get("status") == "not_applicable":
            continue
        title = str(requirement.get("title") or "企业资料")
        file_ids = [str(item) for item in requirement.get("submitted_file_ids") or [] if item]
        selected_profiles = [str(item) for item in requirement.get("submitted_profile_item_uuids") or [] if item]
        direct_file_ids.update(file_ids)
        profile_ids.update(selected_profiles)
        directory_item = directory_by_key.get(str(requirement.get("format_item_key"))) or {}
        if directory_item.get("requires_attachment"):
            attachment_requirements.append({
                "requirement_uuid": requirement.get("requirement_uuid"),
                "title": title,
                "file_ids": file_ids,
                "profile_ids": selected_profiles,
            })

    file_rows = (
        db.query(FileObject).filter(FileObject.file_id.in_(direct_file_ids)).all() if direct_file_ids else []
    )
    profile_rows = (
        db.query(EnterpriseProfileItem).filter(EnterpriseProfileItem.item_uuid.in_(profile_ids)).all() if profile_ids else []
    )
    collected: dict[str, dict[str, Any]] = {}
    for file_obj in file_rows:
        _add_file(collected, file_obj, source_titles=["人工补充文件"])
    profile_file_ids: dict[str, set[str]] = {}
    for profile in profile_rows:
        profile_file_ids[profile.item_uuid] = set()
        for attachment in profile.attachments or []:
            if attachment.file_object:
                profile_file_ids[profile.item_uuid].add(attachment.file_object.file_id)
                _add_file(collected, attachment.file_object, source_titles=[profile.title])
    for requirement in attachment_requirements:
        actual_file_ids = {file_id for file_id in requirement["file_ids"] if file_id in collected}
        for profile_id in requirement["profile_ids"]:
            actual_file_ids.update(profile_file_ids.get(profile_id, set()))
        if not actual_file_ids:
            blocking.append({
                "code": "attachment_file_missing",
                "title": f"附件资料未关联实际文件：{requirement['title']}",
                "severity": "high",
                "detail": "该目录项要求附件，请关联资料库中的 PDF 扫描件或上传 PDF 文件。",
                "requirement_uuid": requirement["requirement_uuid"],
            })
    attachments = list(collected.values())
    for item in attachments:
        if not item["is_pdf"]:
            blocking.append({
                "code": "attachment_not_pdf",
                "title": f"附件不是 PDF：{item['original_filename']}",
                "severity": "high",
                "detail": "正式成册当前只自动合并 PDF，请先转换为清晰的 PDF 扫描件后重新关联。",
                "file_id": item["file_id"],
            })
    return attachments, blocking


def _add_file(target: dict[str, dict[str, Any]], file_obj: FileObject, *, source_titles: list[str]) -> None:
    if not file_obj.file_id:
        return
    item = target.get(file_obj.file_id)
    if item is None:
        filename = file_obj.original_filename or file_obj.file_id
        extension = Path(filename).suffix.lower()
        item = {
            "file_id": file_obj.file_id,
            "original_filename": filename,
            "content_type": file_obj.content_type,
            "size_bytes": file_obj.size_bytes,
            "bucket": file_obj.bucket,
            "object_name": file_obj.object_name,
            "is_pdf": extension == ".pdf" or file_obj.content_type == "application/pdf",
            "source_titles": [],
        }
        target[file_obj.file_id] = item
    item["source_titles"] = sorted(set(item["source_titles"] + source_titles))


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _safe_filename(value: str | None) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in (value or "business_bid"))
    return cleaned.strip("_")[:80] or "business_bid"