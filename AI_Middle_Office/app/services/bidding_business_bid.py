"""Commercial-bid quote snapshots and draft PDF generation."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bidding import BidBusinessBidQuoteImport, BidMaterialRequirement, BidProject
from app.models.budget_pricing_draft import BudgetProjectPricingDraftLine
from app.models.user import User
from app.services.budget_pricing_drafts import (
    ensure_budget_pricing_draft_uses_active_import,
    get_current_budget_pricing_draft,
    serialize_budget_pricing_draft,
    serialize_budget_pricing_draft_line,
)
from app.services.budget_projects import get_budget_profile


_FONT = "STSong-Light"
_MONEY_Q = Decimal("0.01")
_STATUS_LABELS = {
    "missing": "待人工补充",
    "candidate_found": "已找到候选资料，待确认",
    "submitted": "已提交，待确认可用",
    "resolved": "资料已就绪",
    "not_required": "不适用",
}
_DEFAULT_MATERIALS = (
    ("营业执照及资质证书", "待人工补充", "请上传加盖公章的营业执照、资质证书或对应扫描件。"),
    ("法定代表人身份证明及授权书", "待人工补充", "请人工导入法定代表人身份证明、授权委托书及签章页。"),
    ("类似业绩与获奖证明", "待人工补充", "请从企业资料库选择或人工上传业绩合同、验收证明、获奖证书。"),
    ("投标保证金或保函材料", "待人工补充", "请按招标文件要求人工导入保证金凭证、保函或承诺函。"),
)


class BusinessBidError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409, context: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.context = context or {}

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _clean(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    return text[:limit] if limit else text


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _money(value: Any) -> Decimal:
    return (_decimal(value) or Decimal("0")).quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _money_text(value: Any) -> str:
    return format(_money(value), ",.2f")


def _quantity_text(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "-"
    result = format(parsed.normalize(), "f").rstrip("0").rstrip(".")
    return result or "0"


def _snapshot_lines(lines: list[BudgetProjectPricingDraftLine]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        item = serialize_budget_pricing_draft_line(line)
        result.append({
            "sequence": index,
            "line_uuid": item["line_uuid"],
            "source_sheet": item["source_sheet"],
            "source_raw_row_index": item["source_raw_row_index"],
            "item_name": item["item_name"] or "未命名清单项",
            "spec": item["spec"] or "",
            "unit": item["unit"] or "",
            "quantity": item["effective_calculation_quantity"] or item["calculation_quantity"] or "0",
            "unit_price": item["effective_unit_price"] or "0",
            "line_total": item["line_total"] or "0",
            "remark": item.get("remark") or "",
            "price_source": item.get("price_source") or "none",
        })
    return result


def import_business_bid_quote(
    db: Session,
    project: BidProject,
    current_user: User,
    *,
    budget_project_id: int,
    pricing_draft_uuid: str,
    expected_draft_revision: int,
    import_note: str | None = None,
) -> BidBusinessBidQuoteImport:
    profile = get_budget_profile(db, budget_project_id, current_user)
    draft = get_current_budget_pricing_draft(db, profile, current_user, for_update=True)
    if draft is None or draft.draft_uuid != pricing_draft_uuid:
        raise BusinessBidError("BUSINESS_BID_PRICING_DRAFT_NOT_CURRENT", status_code=404)
    if int(draft.revision) != int(expected_draft_revision):
        raise BusinessBidError(
            "BUSINESS_BID_PRICING_DRAFT_REVISION_CONFLICT",
            context={"expected_revision": expected_draft_revision, "current_revision": draft.revision},
        )
    ensure_budget_pricing_draft_uses_active_import(profile, draft)
    if draft.completeness_status != "complete" or draft.total_cost is None:
        raise BusinessBidError("BUSINESS_BID_PRICING_DRAFT_INCOMPLETE")
    lines = (
        db.query(BudgetProjectPricingDraftLine)
        .filter(BudgetProjectPricingDraftLine.draft_id == draft.id)
        .order_by(BudgetProjectPricingDraftLine.source_sort_order.asc(), BudgetProjectPricingDraftLine.id.asc())
        .with_for_update()
        .all()
    )
    if not lines or any(not line.amount_included or line.line_total is None for line in lines):
        raise BusinessBidError("BUSINESS_BID_PRICING_LINES_INCOMPLETE")

    snapshot = {
        "snapshot_version": "business_bid_quote_v1",
        "budget_project_id": int(profile.project_id),
        "source_project_name": profile.project.name,
        "pricing_draft_uuid": draft.draft_uuid,
        "pricing_draft_revision": int(draft.revision),
        "pricing_mode": draft.pricing_mode,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "summary": serialize_budget_pricing_draft(draft),
        "lines": _snapshot_lines(lines),
    }
    snapshot_json = _json_dump(snapshot)
    active_rows = (
        db.query(BidBusinessBidQuoteImport)
        .filter(BidBusinessBidQuoteImport.project_id == project.id, BidBusinessBidQuoteImport.status == "active")
        .with_for_update()
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in active_rows:
        row.status = "superseded"
        row.superseded_at = now
    version_no = int(
        db.query(func.max(BidBusinessBidQuoteImport.version_no))
        .filter(BidBusinessBidQuoteImport.project_id == project.id)
        .scalar()
        or 0
    ) + 1
    imported = BidBusinessBidQuoteImport(
        import_uuid=str(uuid.uuid4()),
        project_id=project.id,
        budget_project_id=profile.project_id,
        pricing_draft_id=draft.id,
        version_no=version_no,
        status="active",
        source_draft_uuid=draft.draft_uuid,
        source_draft_revision=int(draft.revision),
        pricing_mode=draft.pricing_mode,
        source_project_name=profile.project.name,
        source_snapshot_sha256=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        line_count=len(lines),
        total_amount=draft.total_cost,
        snapshot_json=snapshot_json,
        import_note=_clean(import_note, 2000) or None,
        created_by=current_user.id,
    )
    db.add(imported)
    db.flush()
    return imported


def get_active_business_bid_quote_import(db: Session, project: BidProject) -> BidBusinessBidQuoteImport | None:
    return (
        db.query(BidBusinessBidQuoteImport)
        .filter(BidBusinessBidQuoteImport.project_id == project.id, BidBusinessBidQuoteImport.status == "active")
        .order_by(BidBusinessBidQuoteImport.version_no.desc(), BidBusinessBidQuoteImport.id.desc())
        .first()
    )


def list_business_bid_quote_imports(db: Session, project: BidProject) -> list[BidBusinessBidQuoteImport]:
    return (
        db.query(BidBusinessBidQuoteImport)
        .filter(BidBusinessBidQuoteImport.project_id == project.id)
        .order_by(BidBusinessBidQuoteImport.version_no.desc(), BidBusinessBidQuoteImport.id.desc())
        .all()
    )


def serialize_business_bid_quote_import(row: BidBusinessBidQuoteImport, *, include_lines: bool = False) -> dict[str, Any]:
    snapshot = _json_load(row.snapshot_json, {})
    summary = snapshot.get("summary") if isinstance(snapshot, dict) else {}
    lines = snapshot.get("lines") if isinstance(snapshot, dict) else []
    data = {
        "import_uuid": row.import_uuid,
        "version_no": row.version_no,
        "status": row.status,
        "budget_project_id": row.budget_project_id,
        "source_project_name": row.source_project_name,
        "pricing_draft_uuid": row.source_draft_uuid,
        "pricing_draft_revision": row.source_draft_revision,
        "pricing_mode": row.pricing_mode,
        "source_snapshot_sha256": row.source_snapshot_sha256,
        "line_count": row.line_count,
        "total_amount": format(_money(row.total_amount), "f"),
        "summary": summary if isinstance(summary, dict) else {},
        "import_note": row.import_note,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "superseded_at": row.superseded_at.isoformat() if row.superseded_at else None,
    }
    if include_lines:
        data["lines"] = lines if isinstance(lines, list) else []
    return data


def _material_rows(db: Session, project: BidProject) -> list[dict[str, str]]:
    rows = (
        db.query(BidMaterialRequirement)
        .filter(BidMaterialRequirement.project_id == project.id, BidMaterialRequirement.package_key == "business")
        .order_by(BidMaterialRequirement.id.asc())
        .all()
    )
    if not rows:
        return [{"title": title, "status": status, "instruction": instruction} for title, status, instruction in _DEFAULT_MATERIALS]
    result = []
    for row in rows:
        instruction = _clean(row.description) or _clean(row.source_text) or "请人工补充或从企业资料库关联对应资料。"
        if row.candidate_profile_item_uuid and row.status in {"candidate_found", "missing"}:
            instruction = f"{instruction} 已找到企业资料候选项：{row.candidate_profile_item_uuid}。"
        result.append({
            "title": row.title or row.item_title or "企业资料",
            "status": _STATUS_LABELS.get(row.status, row.status or "待人工补充"),
            "instruction": instruction,
        })
    return result


def _amount_uppercase(value: Any) -> str:
    amount = _money(value)
    if amount < 0:
        return "负" + _amount_uppercase(-amount)
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ("", "拾", "佰", "仟")
    group_units = ("", "万", "亿", "兆")
    integer = int(amount)
    if integer == 0:
        integer_text = "零元"
    else:
        groups = []
        while integer:
            groups.append(integer % 10000)
            integer //= 10000
        integer_text = ""
        zero_gap = False
        for group_index in range(len(groups) - 1, -1, -1):
            group = groups[group_index]
            if group == 0:
                zero_gap = bool(integer_text)
                continue
            if zero_gap and not integer_text.endswith("零"):
                integer_text += "零"
            chars = ""
            zero = False
            for position in range(3, -1, -1):
                digit = group // (10 ** position) % 10
                if digit:
                    if zero:
                        chars += "零"
                    chars += digits[digit] + units[position]
                    zero = False
                elif chars:
                    zero = True
            integer_text += chars + group_units[group_index]
            zero_gap = False
        integer_text += "元"
    fraction = int((amount - int(amount)) * 100)
    jiao, fen = divmod(fraction, 10)
    if not fraction:
        return integer_text + "整"
    return integer_text + (digits[jiao] + "角" if jiao else "") + (digits[fen] + "分" if fen else "")


def _styles() -> dict[str, ParagraphStyle]:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
    except KeyError:
        pass
    base = getSampleStyleSheet()["Normal"]
    return {
        "title": ParagraphStyle("BusinessBidTitle", parent=base, fontName=_FONT, fontSize=24, leading=35, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("BusinessBidSubtitle", parent=base, fontName=_FONT, fontSize=13, leading=22, alignment=TA_CENTER),
        "heading": ParagraphStyle("BusinessBidHeading", parent=base, fontName=_FONT, fontSize=15, leading=23, textColor=colors.HexColor("#163A5F"), spaceAfter=8),
        "body": ParagraphStyle("BusinessBidBody", parent=base, fontName=_FONT, fontSize=9, leading=15),
        "center": ParagraphStyle("BusinessBidCenter", parent=base, fontName=_FONT, fontSize=9, leading=15, alignment=TA_CENTER),
        "tiny": ParagraphStyle("BusinessBidTiny", parent=base, fontName=_FONT, fontSize=7.5, leading=10),
    }


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_clean(value) or "-"), style)


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle], repeat_rows: int = 1) -> Table:
    data = [[cell if isinstance(cell, Paragraph) else _paragraph(cell, styles["body"]) for cell in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9AA7B4")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0F7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(_FONT, 8)
    canvas.setFillColor(colors.HexColor("#627282"))
    canvas.drawString(16 * mm, 10 * mm, "商务标成册 - 清单与文字说明，附件按索引人工合并")
    canvas.drawRightString(281 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _build_business_bid_pdf_v13(
    db: Session,
    project: BidProject,
    quote_import: BidBusinessBidQuoteImport,
    *,
    assembly: dict[str, Any] | None = None,
    export_mode: str = "draft",
) -> bytes:
    assembly = assembly or {}
    if export_mode not in {"draft", "formal"}:
        raise ValueError("INVALID_BUSINESS_BID_EXPORT_MODE")
    snapshot = _json_load(quote_import.snapshot_json, {})
    lines = snapshot.get("lines") if isinstance(snapshot, dict) and isinstance(snapshot.get("lines"), list) else []
    summary = snapshot.get("summary") if isinstance(snapshot, dict) and isinstance(snapshot.get("summary"), dict) else {}
    total = _money(quote_import.total_amount)
    styles = _styles()
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream, pagesize=landscape(A4), leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"{project.project_name} 商务标{'正式版' if export_mode == 'formal' else '草案'}", author="旗胜智价",
    )
    story: list[Any] = []
    document_label = "商 务 标 正 式 版" if export_mode == "formal" else "商 务 标 草 案"
    story += [Spacer(1, 44 * mm), _paragraph("投 标 文 件", styles["title"]), _paragraph(document_label, styles["title"]), Spacer(1, 10 * mm)]
    story += [_paragraph("项目目录、报价清单、商务承诺及企业资料附件索引", styles["subtitle"]), Spacer(1, 18 * mm)]
    story += [_paragraph(f"项目名称：{project.project_name}", styles["subtitle"]), Spacer(1, 5 * mm)]
    story += [_paragraph(f"投标人：{project.tenderer_name or '待人工填写'}", styles["subtitle"]), Spacer(1, 5 * mm)]
    story += [_paragraph(f"生成日期：{datetime.now().strftime('%Y年%m月%d日')}", styles["subtitle"]), Spacer(1, 18 * mm)]
    cover_note = "本文件已通过系统成册门禁，企业资料仍应按附件索引人工核验、签章并合并。" if export_mode == "formal" else "本文件为系统生成草案。企业证照、签章扫描件、业绩证明及保证金材料须由人工核验、补充并按招标文件要求装订。"
    story += [_paragraph(cover_note, styles["center"]), PageBreak()]

    story += [_paragraph("一、目录", styles["heading"])]
    toc = [[_paragraph("序号", styles["center"]), _paragraph("章节", styles["body"]), _paragraph("状态", styles["body"])]]
    directory = assembly.get("directory") if isinstance(assembly.get("directory"), list) else []
    if directory:
        for item in directory:
            title = item.get("title") or "未命名目录项"
            status_text = "待附件合并" if item.get("requires_attachment") else "已纳入成册"
            toc.append([_paragraph(item.get("sequence") or len(toc), styles["center"]), _paragraph(title, styles["body"]), _paragraph(status_text, styles["body"])])
    else:
        for index, title in enumerate(("报价来源说明", "报价汇总表", "分项报价清单", "商务承诺说明", "企业资料补位清单", "附件索引", "人工复核清单"), start=1):
            toc.append([_paragraph(index, styles["center"]), _paragraph(title, styles["body"]), _paragraph("已生成" if index <= 4 else "待人工补充", styles["body"])])
    story += [_table(toc, [24 * mm, 150 * mm, 70 * mm], styles), Spacer(1, 8 * mm)]
    story += [_paragraph("二、报价来源说明", styles["heading"])]
    story += [_paragraph(f"本商务标报价清单引用预算项目“{quote_import.source_project_name}”的确认报价快照 V{quote_import.version_no}，来源报价草案版本为 {quote_import.source_draft_revision}，共导入 {quote_import.line_count} 条清单。", styles["body"])]
    story += [_paragraph("导入后形成独立快照；后续预算报价调整不会自动覆盖本商务标。需要更新时，应人工复核后重新导入并形成新版本。", styles["body"]), PageBreak()]

    story += [_paragraph("三、报价汇总表", styles["heading"])]
    summary_rows = [
        [_paragraph("投标项目", styles["body"]), _paragraph(project.project_name, styles["body"]), _paragraph("报价来源项目", styles["body"]), _paragraph(quote_import.source_project_name, styles["body"])],
        [_paragraph("清单行数", styles["body"]), _paragraph(quote_import.line_count, styles["body"]), _paragraph("报价合计（元）", styles["body"]), _paragraph(_money_text(total), styles["body"])],
        [_paragraph("金额大写", styles["body"]), _paragraph(_amount_uppercase(total), styles["body"]), _paragraph("报价版本", styles["body"]), _paragraph(f"商务标快照 V{quote_import.version_no}", styles["body"])],
        [_paragraph("计价方式", styles["body"]), _paragraph(str(summary.get("pricing_mode") or quote_import.pricing_mode or "以确认报价单为准"), styles["body"]), _paragraph("使用说明", styles["body"]), _paragraph("正式投标前须人工复核全部金额与招标文件要求。", styles["body"])],
    ]
    summary_table = _table(summary_rows, [42 * mm, 94 * mm, 42 * mm, 94 * mm], styles, repeat_rows=0)
    summary_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F7FA")), ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F4F7FA"))]))
    story += [summary_table, Spacer(1, 8 * mm), _paragraph("系统仅提供清单表格与文字说明。盖章页、签字页、企业相关截图和扫描件不自动生成，需人工导入。", styles["body"]), PageBreak()]

    story += [_paragraph("四、分项报价清单", styles["heading"])]
    bill_rows = [[_paragraph("序号", styles["center"]), _paragraph("项目名称", styles["body"]), _paragraph("规格/特征", styles["body"]), _paragraph("单位", styles["center"]), _paragraph("工程量", styles["center"]), _paragraph("单价（元）", styles["center"]), _paragraph("合价（元）", styles["center"]), _paragraph("备注", styles["body"])]]
    for row in lines:
        bill_rows.append([
            _paragraph(row.get("sequence"), styles["tiny"]), _paragraph(row.get("item_name"), styles["tiny"]), _paragraph(row.get("spec"), styles["tiny"]),
            _paragraph(row.get("unit"), styles["tiny"]), _paragraph(_quantity_text(row.get("quantity")), styles["tiny"]),
            _paragraph(_money_text(row.get("unit_price")), styles["tiny"]), _paragraph(_money_text(row.get("line_total")), styles["tiny"]), _paragraph(row.get("remark") or "", styles["tiny"]),
        ])
    bill_rows.append([_paragraph("合计", styles["body"]), "", "", "", "", "", _paragraph(_money_text(total), styles["body"]), _paragraph("以确认报价快照为准", styles["body"])])
    bill = _table(bill_rows, [14 * mm, 36 * mm, 76 * mm, 15 * mm, 20 * mm, 28 * mm, 30 * mm, 34 * mm], styles)
    bill.setStyle(TableStyle([("SPAN", (0, -1), (5, -1)), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F4F7FA"))]))
    story += [bill, PageBreak()]

    story += [_paragraph("五、商务承诺说明", styles["heading"])]
    for paragraph in (
        f"我方（{project.tenderer_name or '投标人名称待填写'}）确认，本商务标报价清单引用已确认报价快照，并由授权人员在提交前完成复核。",
        f"我方承诺对项目“{project.project_name}”的报价、清单范围、单位、工程量及税费口径按招标文件要求进行最终确认。",
        f"本次系统生成报价合计为人民币 {_amount_uppercase(total)}（小写：{_money_text(total)} 元）。",
        "本草案中的企业证照、授权文件、类似业绩、保证金或保函等内容不以系统自动生成的文字替代原件、扫描件或签章材料。",
        "正式投标前，请在“企业资料补位清单”中逐项导入、核验、签章，并根据招标文件的格式和份数要求完成装订。",
    ):
        story += [_paragraph(paragraph, styles["body"]), Spacer(1, 4 * mm)]

    story += [PageBreak(), _paragraph("六、商务响应与一致性复核", styles["heading"])]
    v12_review = assembly.get("v12_review") if isinstance(assembly.get("v12_review"), dict) else {}
    quote_review = v12_review.get("quote_consistency") if isinstance(v12_review.get("quote_consistency"), dict) else {}
    response_review = v12_review.get("business_responses") if isinstance(v12_review.get("business_responses"), dict) else {}
    response_summary = response_review.get("summary") if isinstance(response_review.get("summary"), dict) else {}
    quote_summary = quote_review.get("summary") if isinstance(quote_review.get("summary"), dict) else {}
    consistency_rows = [
        [_paragraph("报价快照", styles["body"]), _paragraph(f"{quote_summary.get('snapshot_line_count', 0)} 行 / 合计 {quote_summary.get('calculated_total') or '-'} 元", styles["body"])],
        [_paragraph("商务响应", styles["body"]), _paragraph(f"共 {response_summary.get('total', 0)} 项，已闭环 {response_summary.get('resolved_count', 0)} 项，高风险 {response_summary.get('high_risk_count', 0)} 项", styles["body"])],
        [_paragraph("正式版门禁", styles["body"]), _paragraph("已通过" if not v12_review.get("formal_blocking_items") else f"待处理 {len(v12_review.get('formal_blocking_items') or [])} 项高风险问题", styles["body"])],
    ]
    consistency_table = _table(consistency_rows, [45 * mm, 213 * mm], styles)
    consistency_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F7FA"))]))
    story += [consistency_table, Spacer(1, 5 * mm)]
    response_rows = [[_paragraph("商务响应", styles["body"]), _paragraph("状态", styles["body"]), _paragraph("风险", styles["body"]), _paragraph("我方响应", styles["body"]), _paragraph("依据", styles["body"])]]
    status_labels = {"pending": "待处理", "confirmed": "已确认", "done": "已完成", "ignored": "不适用", "to_clarify": "待答疑", "to_quote_allowance": "待报价处理", "legal_review": "待法务复核"}
    for item in response_review.get("items") or []:
        response_rows.append([
            _paragraph(item.get("title") or "-", styles["tiny"]),
            _paragraph(status_labels.get(item.get("status"), item.get("status") or "-"), styles["tiny"]),
            _paragraph(item.get("risk_level") or "-", styles["tiny"]),
            _paragraph(item.get("response_note") or "待人工补充响应说明", styles["tiny"]),
            _paragraph(item.get("source_text") or "-", styles["tiny"]),
        ])
    if len(response_rows) == 1:
        response_rows.append([_paragraph("暂无商务响应项", styles["body"]), _paragraph("-", styles["body"]), _paragraph("-", styles["body"]), _paragraph("请先生成响应矩阵。", styles["body"]), _paragraph("-", styles["body"])])
    story += [_table(response_rows, [52 * mm, 25 * mm, 20 * mm, 78 * mm, 83 * mm], styles), PageBreak()]

    story += [_paragraph("七、企业资料补位清单", styles["heading"])]
    materials = [[_paragraph("资料名称", styles["body"]), _paragraph("当前状态", styles["body"]), _paragraph("人工处理要求", styles["body"])]]
    for row in _material_rows(db, project):
        materials.append([_paragraph(row["title"], styles["body"]), _paragraph(row["status"], styles["body"]), _paragraph(row["instruction"], styles["body"])])
    story += [_table(materials, [70 * mm, 58 * mm, 130 * mm], styles), Spacer(1, 8 * mm)]

    story += [PageBreak(), _paragraph("八、附件索引", styles["heading"])]
    attachment_index = assembly.get("attachment_index") if isinstance(assembly.get("attachment_index"), list) else []
    attachments = [[_paragraph("序号", styles["center"]), _paragraph("资料名称", styles["body"]), _paragraph("状态", styles["body"]), _paragraph("已关联资料", styles["body"]), _paragraph("处理说明", styles["body"])]]
    for item in attachment_index:
        evidence = [f"资料库 {value}" for value in item.get("profile_item_uuids") or []]
        evidence += [f"文件 {value}" for value in item.get("file_ids") or []]
        attachments.append([
            _paragraph(item.get("sequence"), styles["body"]),
            _paragraph(item.get("title") or "-", styles["body"]),
            _paragraph("已就绪" if item.get("resolved") else "待补充", styles["body"]),
            _paragraph("；".join(evidence) if evidence else "尚未关联", styles["tiny"]),
            _paragraph(item.get("notes") or "按目录要求人工核验、签章并合并扫描件。", styles["tiny"]),
        ])
    if len(attachments) == 1:
        attachments.append([_paragraph("-", styles["body"]), _paragraph("暂无项目资料要求", styles["body"]), _paragraph("待生成", styles["body"]), _paragraph("-", styles["body"]), _paragraph("请先生成并确认投标文件格式及资料需求。", styles["body"])])
    story += [_table(attachments, [16 * mm, 55 * mm, 30 * mm, 72 * mm, 85 * mm], styles), Spacer(1, 6 * mm)]
    story += [_paragraph("系统不自动生成或合并企业扫描件。附件索引仅记录已关联的资料库条目和上传文件，成册提交前须由人工按招标文件顺序合并、核验签章。", styles["body"])]

    story += [PageBreak(), _paragraph("九、人工复核清单", styles["heading"])]
    review = [[_paragraph("序号", styles["center"]), _paragraph("复核事项", styles["body"])]]
    for index, text in enumerate((
        "确认导入的报价版本、清单行数和报价合计与最终报价单一致。",
        "核验税率、计价口径、暂估价、甲供材及招标文件的报价要求。",
        "补齐营业执照、资质、授权、业绩、保证金或保函等企业资料扫描件。",
        "按招标文件要求完成法定代表人签字、授权签字、盖章和装订。",
        "导出正式投标文件前，由商务负责人完成最终复核并留存版本记录。",
    ), start=1):
        review.append([_paragraph(index, styles["center"]), _paragraph(text, styles["body"])])
    story += [_table(review, [22 * mm, 236 * mm], styles)]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return stream.getvalue()

def build_business_bid_pdf(
    db: Session,
    project: BidProject,
    quote_import: BidBusinessBidQuoteImport,
    *,
    assembly: dict[str, Any] | None = None,
    export_mode: str = "draft",
) -> bytes:
    """Build the V1.4 business-bid booklet with fixed forms and formal pagination."""
    from app.services.bidding_business_bid_v14 import build_business_bid_pdf_v14

    return build_business_bid_pdf_v14(
        db,
        project,
        quote_import,
        assembly=assembly,
        export_mode=export_mode,
    )