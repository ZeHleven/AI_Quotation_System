"""Business-bid V1.4 typeset core: fixed forms and formal booklet layout."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, TableStyle
from reportlab.platypus.tableofcontents import TableOfContents

from app.services.bidding_business_bid_template import build_business_bid_template_plan
from app.services.bidding_business_bid_fields import field_lookup
from app.services.bidding_business_bid import (
    _FONT,
    _amount_uppercase,
    _material_rows,
    _money,
    _money_text,
    _paragraph,
    _quantity_text,
    _table,
    _styles,
)


TEMPLATE_VERSION = "business_bid_booklet_v1.4.1"


class _BookletCanvas(canvas.Canvas):
    def __init__(self, *args: Any, document_title: str, document_label: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._booklet_states: list[dict[str, Any]] = []
        self._document_title = document_title
        self._document_label = document_label

    def showPage(self) -> None:
        self._booklet_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        content_page_count = max(len(self._booklet_states) - 1, 0)
        for state in self._booklet_states:
            self.__dict__.update(state)
            physical_page = self._pageNumber
            if physical_page > 1:
                width, height = self._pagesize
                self.saveState()
                self.setStrokeColor(colors.HexColor("#A7A7A7"))
                self.setLineWidth(0.35)
                self.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
                self.setFont(_FONT, 8)
                self.setFillColor(colors.HexColor("#454545"))
                self.drawString(18 * mm, height - 10 * mm, self._document_title[:64])
                self.drawRightString(width - 18 * mm, height - 10 * mm, self._document_label)
                self.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
                self.setFillColor(colors.HexColor("#666666"))
                self.drawString(18 * mm, 9 * mm, "系统生成表单与人工核验附件共同构成商务标成册文件")
                self.drawRightString(width - 18 * mm, 9 * mm, f"第 {physical_page - 1} 页 / 共 {content_page_count} 页")
                self.restoreState()
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


class _BookletDocTemplate(BaseDocTemplate):
    def __init__(self, stream: BytesIO, *, title: str, author: str) -> None:
        super().__init__(stream, title=title, author=author)
        portrait_width, portrait_height = A4
        landscape_width, landscape_height = landscape(A4)
        self.addPageTemplates([
            PageTemplate(
                id="cover",
                pagesize=A4,
                frames=[Frame(24 * mm, 24 * mm, portrait_width - 48 * mm, portrait_height - 48 * mm, id="cover")],
            ),
            PageTemplate(
                id="portrait",
                pagesize=A4,
                frames=[Frame(18 * mm, 20 * mm, portrait_width - 36 * mm, portrait_height - 40 * mm, id="portrait")],
            ),
            PageTemplate(
                id="landscape",
                pagesize=landscape(A4),
                frames=[Frame(16 * mm, 20 * mm, landscape_width - 32 * mm, landscape_height - 40 * mm, id="landscape")],
            ),
        ])

    def afterFlowable(self, flowable: Any) -> None:
        level = getattr(flowable, "outlineLevel", None)
        if level is not None:
            self.notify("TOCEntry", (level, flowable.getPlainText(), max(self.page - 1, 1)))


def _booklet_styles() -> dict[str, ParagraphStyle]:
    styles = _styles()
    styles.update({
        "cover_title": ParagraphStyle("BusinessBidV14CoverTitle", parent=styles["title"], fontSize=27, leading=40, spaceAfter=8 * mm),
        "cover_subtitle": ParagraphStyle("BusinessBidV14CoverSubtitle", parent=styles["subtitle"], fontSize=14, leading=24),
        "section": ParagraphStyle("BusinessBidV14Section", parent=styles["heading"], fontSize=15, leading=24, textColor=colors.black, spaceBefore=2 * mm, spaceAfter=6 * mm, keepWithNext=True),
        "form_title": ParagraphStyle("BusinessBidV14FormTitle", parent=styles["subtitle"], fontSize=16, leading=24, spaceAfter=7 * mm),
        "form_body": ParagraphStyle("BusinessBidV14FormBody", parent=styles["body"], fontSize=10, leading=19, firstLineIndent=0),
        "form_center": ParagraphStyle("BusinessBidV14FormCenter", parent=styles["center"], fontSize=10, leading=19),
        "form_tiny": ParagraphStyle("BusinessBidV14FormTiny", parent=styles["tiny"], fontSize=8, leading=12),
        "toc": ParagraphStyle("BusinessBidV14Toc", parent=styles["body"], fontSize=10, leading=20, leftIndent=4 * mm),
        "toc_page": ParagraphStyle("BusinessBidV14TocPage", parent=styles["body"], fontSize=10, leading=20, alignment=TA_RIGHT),
        "note": ParagraphStyle("BusinessBidV14Note", parent=styles["body"], fontSize=8, leading=13, textColor=colors.HexColor("#555555")),
        "manual_placeholder": ParagraphStyle("BusinessBidV141ManualPlaceholder", parent=styles["body"], fontSize=8.5, leading=13, backColor=colors.HexColor("#FFF2A8"), textColor=colors.HexColor("#5A3B00")),
    })
    return styles


def _section(title: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    result = _paragraph(title, styles["section"])
    result.outlineLevel = 0
    return result


def _directory_keys(directory: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for item in directory:
        raw = str(item.get("item_key") or "").strip().lower()
        if raw:
            keys.add(raw.rsplit(":", 1)[-1])
    return keys


def _includes(keys: set[str], *candidates: str) -> bool:
    return not keys or bool(keys.intersection(candidates))


_SECTION_ORDER = ("bid_letter", "pricing_summary", "legal_representative", "authorization", "commitment", "business_deviation", "boq", "attachment_index")
_CN_SECTION_NUMBERS = ("一", "二", "三", "四", "五", "六", "七", "八", "九")


def _section_title(section_key: str, title: str, generated_keys: set[str]) -> str:
    active_keys = [key for key in _SECTION_ORDER if key in generated_keys]
    try:
        position = active_keys.index(section_key)
    except ValueError:
        position = len(active_keys)
    prefix = _CN_SECTION_NUMBERS[position] if position < len(_CN_SECTION_NUMBERS) else str(position + 1)
    return f"{prefix}、{title}"

def _profile_value(project: Any, *keys: str, fallback: str = "待人工填写") -> str:
    raw = getattr(project, "summary_json", None)
    if isinstance(raw, dict):
        source = raw
    else:
        try:
            import json
            source = json.loads(raw or "{}")
        except (TypeError, ValueError):
            source = {}
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        if value:
            return str(value).strip()
    return fallback


def _yellow(text: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return _paragraph(text, styles["manual_placeholder"])


def _field_placeholder(fields: dict[str, dict[str, Any]], key: str, styles: dict[str, ParagraphStyle], fallback: str) -> Paragraph:
    item = fields.get(key) or {}
    return _yellow(str(item.get("placeholder") or fallback), styles)


def _field_value_or_placeholder(
    value: Any,
    fields: dict[str, dict[str, Any]],
    key: str,
    styles: dict[str, ParagraphStyle],
    fallback: str,
) -> Paragraph:
    if value not in (None, ""):
        return _paragraph(str(value), styles["form_body"])
    return _field_placeholder(fields, key, styles, fallback)


def _signature_block(
    styles: dict[str, ParagraphStyle],
    *,
    include_authorized: bool = True,
    fields: dict[str, dict[str, Any]] | None = None,
    signature_key: str = "bidder_signature",
) -> list[Any]:
    lookup = fields or {}
    rows = [[_paragraph("投标人（盖章）", styles["form_body"]), _field_placeholder(lookup, signature_key, styles, "【人工签章：投标人盖章】")]]
    if include_authorized:
        rows.append([_paragraph("法定代表人或授权代理人（签字）", styles["form_body"]), _field_placeholder(lookup, signature_key, styles, "【人工签章：法定代表人或授权代理人签字】")])
    rows.append([_paragraph("日期", styles["form_body"]), _yellow("【人工填写：签署日期（依据：投标递交安排）】", styles)])
    table = _table(rows, [66 * mm, 92 * mm], styles, repeat_rows=0)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F4F4")), ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#FFF2A8"))]))
    return [Spacer(1, 8 * mm), table]


def _fixed_form_story(
    project: Any,
    quote_import: Any,
    assembly: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    generated_keys: set[str],
) -> list[Any]:
    keys = generated_keys
    fields = field_lookup(assembly.get("draft_field_plan") if isinstance(assembly.get("draft_field_plan"), dict) else None)
    total = _money(quote_import.total_amount)
    tenderer_value = getattr(project, "tenderer_name", None)
    tenderer = tenderer_value or "待人工填写"
    tenderer_agency_value = getattr(project, "tender_agency", None) or _profile_value(project, "tenderer", "tender_agency", fallback="")
    tender_number_value = _profile_value(project, "tender_number", "bid_number", "project_code", fallback="")
    response_items = ((assembly.get("v12_review") or {}).get("business_responses") or {}).get("items") or []
    story: list[Any] = []

    if _includes(keys, "bid_letter"):
        story += [_section(_section_title("bid_letter", "投标函", keys), styles), _paragraph("投 标 函", styles["form_title"])]
        story += [_table([[_paragraph("致", styles["form_body"]), _field_value_or_placeholder(tenderer_agency_value, fields, "tender_agency", styles, "【人工填写：招标人/招标代理（依据：本项目招标文件）】")]], [18 * mm, 140 * mm], styles, repeat_rows=0), Spacer(1, 3 * mm)]
        story += [_field_placeholder(fields, "bid_letter_text", styles, "【待确认 LLM 草稿：投标函承诺正文（依据：本项目招标文件 + 人工填写信息）】"), Spacer(1, 2 * mm)]
        story += [_paragraph(f"1. 我方已认真研究项目“{getattr(project, 'project_name', '')}”及其相关文件，愿意按招标文件要求承担相应工作。", styles["form_body"])]
        story += [_paragraph(f"2. 本次投标报价为人民币 {_amount_uppercase(total)}（小写：{_money_text(total)} 元），具体以《投标报价汇总表》和《工程量清单报价表》为准。", styles["form_body"])]
        story += [_paragraph("3. 本投标函须由投标人按招标文件要求完成签字、盖章并与附件原件一并提交；系统不生成签章。", styles["note"])]
        story += _signature_block(styles, fields=fields, signature_key="bidder_signature")
        story += [PageBreak()]

    if _includes(keys, "pricing_summary"):
        story += [_section(_section_title("pricing_summary", "投标报价汇总表", keys), styles), _paragraph("投 标 报 价 汇 总 表", styles["form_title"])]
        rows = [
            [_paragraph("项目名称", styles["form_body"]), _paragraph(getattr(project, "project_name", "-"), styles["form_body"]), _paragraph("招标编号", styles["form_body"]), _field_value_or_placeholder(tender_number_value, fields, "tender_number", styles, "【人工填写：招标编号（依据：本项目招标文件）】")],
            [_paragraph("投标人", styles["form_body"]), _field_value_or_placeholder(tenderer_value, fields, "tenderer_name", styles, "【人工填写：投标人名称（依据：系统已确认的企业资料）】"), _paragraph("报价清单版本", styles["form_body"]), _paragraph(f"商务标报价快照 V{quote_import.version_no}", styles["form_body"])],
            [_paragraph("报价合计（小写）", styles["form_body"]), _paragraph(f"人民币 {_money_text(total)} 元", styles["form_body"]), _paragraph("报价合计（大写）", styles["form_body"]), _paragraph(_amount_uppercase(total), styles["form_body"])],
            [_paragraph("报价说明", styles["form_body"]), _field_placeholder(fields, "pricing_review_note", styles, "【人工复核：报价说明复核（依据：本项目招标文件）】"), "", ""],
        ]
        table = _table(rows, [35 * mm, 50 * mm, 35 * mm, 50 * mm], styles, repeat_rows=0)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F4F4")), ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F4F4F4")), ("BACKGROUND", (1, 3), (3, 3), colors.HexColor("#FFF2A8")), ("SPAN", (1, 3), (3, 3))]))
        story += [table]
        story += _signature_block(styles, include_authorized=False, fields=fields)
        story += [PageBreak()]

    if _includes(keys, "legal_representative", "authorization"):
        story += [_section(_section_title("legal_representative" if "legal_representative" in keys else "authorization", "法定代表人身份证明及授权委托书", keys), styles)]
        if _includes(keys, "legal_representative"):
            story += [_paragraph("法定代表人身份证明", styles["form_title"])]
            story += [_paragraph(f"单位名称：{tenderer}。", styles["form_body"])]
            story += [_field_placeholder(fields, "legal_representative_identity", styles, "【人工填写：法定代表人身份信息（依据：系统已确认的企业资料）】")]
            story += [_field_placeholder(fields, "legal_representative_scan", styles, "【人工导入：法定代表人证明扫描件（依据：系统已确认的企业资料库）】")]
            story += [_paragraph("本页为系统排版的表单位，不能替代身份证明原件、签字或盖章。", styles["note"])]
            story += _signature_block(styles, include_authorized=False, fields=fields, signature_key="legal_representative_signature")
        if _includes(keys, "authorization"):
            story += [_paragraph("授权委托书", styles["form_title"])]
            story += [_paragraph(f"兹授权受托人代表“{tenderer}”就项目“{getattr(project, 'project_name', '')}”办理投标文件签署、递交及相关事宜。受托人姓名、身份证号、权限和期限须由人工据实填写并完成签章。", styles["form_body"])]
            story += [_field_placeholder(fields, "authorized_agent_identity", styles, "【人工填写：授权代理人身份信息（依据：人工填写信息 + 企业授权资料）】")]
            story += [_field_placeholder(fields, "authorization_scope", styles, "【人工填写：授权范围和期限（依据：本项目招标文件 + 人工填写信息）】")]
            story += [_field_placeholder(fields, "authorization_scan", styles, "【人工导入：授权委托扫描件（依据：签署盖章后的授权文件）】")]
            story += _signature_block(styles, fields=fields, signature_key="authorization_signature")
        story += [PageBreak()]

    if _includes(keys, "commitment", "integrity", "contract_objection", "clarification_reply"):
        story += [_section(_section_title("commitment", "商务承诺与响应", keys), styles), _paragraph("商 务 承 诺 书", styles["form_title"])]
        story += [_field_placeholder(fields, "commitment_text", styles, "【待确认 LLM 草稿：商务承诺正文（依据：本项目招标文件 + 已确认报价快照 + 人工填写信息）】"), Spacer(1, 2 * mm)]
        commitments = [
            "我方确认报价清单的项目名称、单位、工程量、单价及合价已按已导入确认报价快照复核。",
            "我方承诺按招标文件要求补充和提交营业执照、资质、授权、业绩、保证金或保函等企业资料。",
            "我方承诺在提交前完成商务条款、合同条款、税费口径及付款条件的最终人工审查。",
            "本承诺书须由有权人员签字并加盖投标人印章后生效，系统生成版本不含签章。",
        ]
        for index, item in enumerate(commitments, start=1):
            story += [_paragraph(f"{index}. {item}", styles["form_body"]), Spacer(1, 2 * mm)]
        story += _signature_block(styles, fields=fields, signature_key="commitment_signature")
        story += [PageBreak()]

    if _includes(keys, "business_deviation"):
        story += [_section(_section_title("business_deviation", "商务条款偏离表", keys), styles), _paragraph("商 务 条 款 偏 离 表", styles["form_title"])]
        rows = [[_paragraph("序号", styles["form_center"]), _paragraph("招标商务条款", styles["form_body"]), _paragraph("我方响应", styles["form_body"]), _paragraph("偏离情况", styles["form_center"]), _paragraph("依据/说明", styles["form_body"])]]
        for index, item in enumerate(response_items, start=1):
            rows.append([
                _paragraph(index, styles["form_tiny"]),
                _paragraph(item.get("source_text") or item.get("title") or "-", styles["form_tiny"]),
                _paragraph(item.get("response_note") or (fields.get("business_response_rows") or {}).get("placeholder") or "待人工填写", styles["form_tiny"]),
                _paragraph("无偏离" if item.get("status") in {"confirmed", "done"} else "待确认", styles["form_tiny"]),
                _paragraph(item.get("title") or "-", styles["form_tiny"]),
            ])
        if len(rows) == 1:
            rows.append([_paragraph("-", styles["form_body"]), _paragraph("暂未识别到需填报的商务条款", styles["form_body"]), _field_placeholder(fields, "business_response_rows", styles, "【待确认 LLM 草稿：商务条款响应行（依据：本项目招标文件 + 响应矩阵）】"), _field_placeholder(fields, "business_deviation_review", styles, "【人工复核：偏离情况人工确认（依据：本项目招标文件）】"), _paragraph("-", styles["form_body"])])
        table = _table(rows, [15 * mm, 49 * mm, 55 * mm, 25 * mm, 36 * mm], styles)
        if len(rows) > 1:
            table.setStyle(TableStyle([("BACKGROUND", (2, 1), (3, -1), colors.HexColor("#FFF2A8"))]))
        story += [table, _paragraph("偏离情况须由商务负责人结合招标文件逐项确认；本表不替代招标人指定的原始格式。", styles["note"]), PageBreak()]

    return story


def _bill_story(
    project: Any,
    quote_import: Any,
    styles: dict[str, ParagraphStyle],
    *,
    include: bool,
    generated_keys: set[str],
    field_plan: dict[str, Any] | None = None,
    start_on_current_page: bool = False,
) -> list[Any]:
    if not include:
        return []
    fields = field_lookup(field_plan)
    snapshot = _snapshot(quote_import)
    lines = snapshot.get("lines") if isinstance(snapshot.get("lines"), list) else []
    total = _money(quote_import.total_amount)
    story: list[Any] = [] if start_on_current_page else [NextPageTemplate("landscape"), PageBreak()]
    story += [_section(_section_title("boq", "工程量清单报价表", generated_keys), styles), _paragraph("工 程 量 清 单 报 价 表", styles["form_title"])]
    rows = [[_paragraph("序号", styles["form_center"]), _paragraph("项目名称", styles["form_body"]), _paragraph("项目特征/规格", styles["form_body"]), _paragraph("单位", styles["form_center"]), _paragraph("工程量", styles["form_center"]), _paragraph("单价（元）", styles["form_center"]), _paragraph("合价（元）", styles["form_center"]), _paragraph("备注", styles["form_body"])]]
    for row in lines:
        rows.append([
            _paragraph(row.get("sequence"), styles["form_tiny"]), _paragraph(row.get("item_name"), styles["form_tiny"]), _paragraph(row.get("spec"), styles["form_tiny"]),
            _paragraph(row.get("unit"), styles["form_tiny"]), _paragraph(_quantity_text(row.get("quantity")), styles["form_tiny"]),
            _paragraph(_money_text(row.get("unit_price")), styles["form_tiny"]), _paragraph(_money_text(row.get("line_total")), styles["form_tiny"]), _paragraph(row.get("remark") or "", styles["form_tiny"]),
        ])
    rows.append([_paragraph("合计", styles["form_body"]), "", "", "", "", "", _paragraph(_money_text(total), styles["form_body"]), _paragraph("以确认报价快照为准", styles["form_tiny"])])
    bill = _table(rows, [13 * mm, 43 * mm, 78 * mm, 15 * mm, 21 * mm, 29 * mm, 31 * mm, 33 * mm], styles)
    bill.setStyle(TableStyle([("SPAN", (0, -1), (5, -1)), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F4F4F4"))]))
    story += [
        bill,
        _field_placeholder(fields, "boq_manual_review", styles, "【人工复核：清单税费/暂估价/招标约定复核（依据：本项目招标文件）】"),
        _paragraph("本表金额与导入确认报价快照绑定；如报价草案更新，须重新导入并重新生成商务标。", styles["note"]),
    ]
    return story


def _closing_story(
    db: Any,
    project: Any,
    assembly: dict[str, Any],
    styles: dict[str, ParagraphStyle],
    *,
    export_mode: str,
    template_plan: dict[str, Any],
    generated_keys: set[str],
    start_on_current_page: bool = False,
) -> list[Any]:
    fields = field_lookup(assembly.get("draft_field_plan") if isinstance(assembly.get("draft_field_plan"), dict) else None)
    story: list[Any] = [] if start_on_current_page else [NextPageTemplate("portrait"), PageBreak()]
    story += [_section(_section_title("attachment_index", "企业资料附件目录", generated_keys), styles), _paragraph("企 业 资 料 附 件 目 录", styles["form_title"])]
    story += [_field_placeholder(fields, "enterprise_material_files", styles, "【人工导入：企业资料扫描件/截图/PDF（依据：系统已确认的企业资料库 + 人工导入附件）】"), Spacer(1, 2 * mm)]
    materials = [[_paragraph("序号", styles["form_center"]), _paragraph("资料名称", styles["form_body"]), _paragraph("当前状态", styles["form_body"]), _paragraph("人工处理要求", styles["form_body"])]]
    for index, row in enumerate(_material_rows(db, project), start=1):
        materials.append([_paragraph(index, styles["form_tiny"]), _paragraph(row["title"], styles["form_tiny"]), _paragraph(row["status"], styles["form_tiny"]), _paragraph(row["instruction"], styles["form_tiny"])])
    story += [_table(materials, [15 * mm, 48 * mm, 35 * mm, 72 * mm], styles)]
    attachment_index = assembly.get("attachment_index") if isinstance(assembly.get("attachment_index"), list) else []
    story += [Spacer(1, 7 * mm), _paragraph("附件关联索引", styles["form_title"])]
    rows = [[_paragraph("序号", styles["form_center"]), _paragraph("资料名称", styles["form_body"]), _paragraph("已关联资料", styles["form_body"]), _paragraph("状态", styles["form_body"])]]
    for item in attachment_index:
        evidence = [f"资料库 {value}" for value in item.get("profile_item_uuids") or []] + [f"文件 {value}" for value in item.get("file_ids") or []]
        rows.append([_paragraph(item.get("sequence"), styles["form_tiny"]), _paragraph(item.get("title") or "-", styles["form_tiny"]), _paragraph("；".join(evidence) if evidence else (fields.get("enterprise_material_files") or {}).get("placeholder") or "尚未关联", styles["form_tiny"]), _paragraph("已就绪" if item.get("resolved") else "待补充", styles["form_tiny"])])
    if len(rows) == 1:
        rows.append([_paragraph("-", styles["form_body"]), _paragraph("暂未生成项目附件要求", styles["form_body"]), _paragraph("请从企业资料库关联", styles["form_body"]), _paragraph("待补充", styles["form_body"])])
    story += [_table(rows, [15 * mm, 53 * mm, 66 * mm, 36 * mm], styles)]
    manual_items = template_plan.get("manual_directory_items") if isinstance(template_plan.get("manual_directory_items"), list) else []
    if manual_items:
        story += [Spacer(1, 7 * mm), _paragraph("项目专属人工处理目录项", styles["form_title"])]
        manual_rows = [[_paragraph("序号", styles["form_center"]), _paragraph("目录项", styles["form_body"]), _paragraph("处理方式", styles["form_body"])]]
        for index, item in enumerate(manual_items, start=1):
            manual_rows.append([
                _paragraph(index, styles["form_tiny"]),
                _paragraph(item.get("title") or "-", styles["form_tiny"]),
                _paragraph((fields.get("unmapped_directory_items") or {}).get("placeholder") or item.get("reason") or "请人工处理", styles["form_tiny"]),
            ])
        manual_table = _table(manual_rows, [15 * mm, 55 * mm, 100 * mm], styles)
        manual_table.setStyle(TableStyle([("BACKGROUND", (2, 1), (2, -1), colors.HexColor("#FFF2A8"))]))
        story += [manual_table]
    label = "本正式版仅合并已核验的 PDF 附件；签章、电子签名和投标加密仍须由人工完成。" if export_mode == "formal" else "草案阶段请先关联企业资料与扫描件，提交前完成签章、核验和正式成册。"
    story += [Spacer(1, 5 * mm), _paragraph(label, styles["note"])]
    return story


def _snapshot(quote_import: Any) -> dict[str, Any]:
    import json
    try:
        return json.loads(quote_import.snapshot_json or "{}")
    except (TypeError, ValueError):
        return {}


def build_business_bid_pdf_v14(
    db: Any,
    project: Any,
    quote_import: Any,
    *,
    assembly: dict[str, Any] | None = None,
    export_mode: str = "draft",
) -> bytes:
    if export_mode not in {"draft", "formal"}:
        raise ValueError("INVALID_BUSINESS_BID_EXPORT_MODE")
    assembly = assembly or {}
    template_plan = assembly.get("template") if isinstance(assembly.get("template"), dict) else build_business_bid_template_plan(assembly.get("directory"))
    field_plan = assembly.get("draft_field_plan") if isinstance(assembly.get("draft_field_plan"), dict) else {}
    generated_keys = {str(item.get("section_key")) for item in template_plan.get("generated_sections") or [] if item.get("section_key")}
    styles = _booklet_styles()
    document_label = "商务标正式版" if export_mode == "formal" else "商务标草案"
    stream = BytesIO()
    doc = _BookletDocTemplate(stream, title=f"{project.project_name}{document_label}", author="旗胜智价")
    story: list[Any] = []
    fixed_story = _fixed_form_story(project, quote_import, assembly, styles, generated_keys)
    if fixed_story and isinstance(fixed_story[-1], PageBreak):
        fixed_story = fixed_story[:-1]
    has_fixed_story = bool(fixed_story)
    has_boq_story = "boq" in generated_keys
    first_content_template = "landscape" if has_boq_story and not has_fixed_story else "portrait"
    story += [Spacer(1, 46 * mm), _paragraph("投 标 文 件", styles["cover_title"]), _paragraph(document_label, styles["cover_title"]), Spacer(1, 16 * mm)]
    story += [_paragraph(project.project_name, styles["cover_subtitle"]), Spacer(1, 6 * mm)]
    story += [_paragraph(f"投标人：{getattr(project, 'tenderer_name', None) or '待人工填写'}", styles["cover_subtitle"]), Spacer(1, 6 * mm)]
    story += [_paragraph(f"编制日期：{datetime.now().strftime('%Y年%m月%d日')}", styles["cover_subtitle"]), Spacer(1, 24 * mm)]
    story += [_paragraph("本文件由系统生成固定表式、报价清单和附件目录。企业扫描件、签字、印章、电子签名及加密提交均须人工核验和完成。", styles["note"])]
    source_policy = (field_plan.get("llm_source_policy") or {}).get("rule") if isinstance(field_plan, dict) else None
    if source_policy:
        story += [Spacer(1, 3 * mm), _paragraph(source_policy, styles["note"])]
    story += [NextPageTemplate("portrait"), PageBreak(), _section("目录", styles)]
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"]]
    story += [toc, NextPageTemplate(first_content_template), PageBreak()]
    story += fixed_story
    story += _bill_story(project, quote_import, styles, include=has_boq_story, generated_keys=generated_keys, field_plan=field_plan, start_on_current_page=not has_fixed_story)
    story += _closing_story(db, project, assembly, styles, export_mode=export_mode, template_plan=template_plan, generated_keys=generated_keys, start_on_current_page=not has_fixed_story and not has_boq_story)
    doc.multiBuild(
        story,
        canvasmaker=lambda *args, **kwargs: _BookletCanvas(*args, document_title=project.project_name, document_label=document_label, **kwargs),
    )
    return stream.getvalue()
