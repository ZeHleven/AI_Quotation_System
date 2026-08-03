from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader

from app.services.bidding_business_bid import build_business_bid_pdf
from app.services.bidding_business_bid_fields import build_business_bid_draft_field_plan
from app.services.bidding_business_bid_template import build_business_bid_template_plan


class _RowsQuery:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _Db:
    def query(self, *args, **kwargs):
        return _RowsQuery()


def _quote_import():
    return SimpleNamespace(
        version_no=1,
        line_count=1,
        total_amount="1000",
        source_project_name="预算项目",
        source_draft_revision=1,
        pricing_mode="manual",
        snapshot_json='{"lines":[{"sequence":1,"item_name":"测试清单项","spec":"测试规格","unit":"m2","quantity":"10","unit_price":"100","line_total":"1000","remark":""}]}',
    )


def _project():
    return SimpleNamespace(project_name="通用模板测试项目", tenderer_name="测试投标人", tender_agency="测试招标人", summary_json="{}", id=1)


def _project_with_missing_manual_fields():
    return SimpleNamespace(project_name="通用模板测试项目", tenderer_name="测试投标人", tender_agency=None, summary_json="{}", id=1)


def test_generic_template_uses_only_confirmed_directory_mappings():
    plan = build_business_bid_template_plan([
        {"sequence": 1, "item_key": "business:boq", "title": "工程量清单报价表"},
        {"sequence": 2, "item_key": "business:bid_bond", "title": "投标保证金"},
    ])

    assert plan["template_id"] == "general_construction_business_bid"
    assert [item["section_key"] for item in plan["generated_sections"]] == ["boq", "attachment_index"]
    assert plan["manual_directory_items"][0]["title"] == "投标保证金"
    assert plan["directory_driven"] is True


def test_draft_baseline_does_not_assume_project_specific_forms():
    plan = build_business_bid_template_plan([])

    assert [item["section_key"] for item in plan["generated_sections"]] == ["pricing_summary", "boq", "attachment_index"]
    assert plan["warnings"][0]["code"] == "directory_not_confirmed"


def test_pdf_keeps_unmapped_directory_item_manual_and_omits_unrequested_forms():
    directory = [
        {"sequence": 1, "item_key": "business:boq", "title": "工程量清单报价表"},
        {"sequence": 2, "item_key": "business:bid_bond", "title": "投标保证金"},
    ]
    assembly = {
        "directory": directory,
        "template": build_business_bid_template_plan(directory),
        "v12_review": {"business_responses": {"items": []}},
        "attachment_index": [],
    }

    reader = PdfReader(BytesIO(build_business_bid_pdf(_Db(), _project(), _quote_import(), assembly=assembly)))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "投 标 函" not in text
    assert "投 标 报 价 汇 总 表" not in text
    assert "工 程 量 清 单 报 价 表" in text
    assert "投标保证金" in text
    assert any(float(page.mediabox.width) > float(page.mediabox.height) for page in reader.pages)


def test_field_plan_locks_llm_sources_and_marks_manual_pdf_slots():
    directory = [
        {"sequence": 1, "item_key": "business:bid_letter", "title": "投标函"},
        {"sequence": 2, "item_key": "business:pricing_summary", "title": "投标报价汇总表"},
        {"sequence": 3, "item_key": "business:boq", "title": "工程量清单报价表"},
    ]
    template_plan = build_business_bid_template_plan(directory)
    field_plan = build_business_bid_draft_field_plan(
        project=_project_with_missing_manual_fields(),
        quote_import=_quote_import(),
        template_plan=template_plan,
        requirements=[],
        v12_report={"business_responses": {"items": []}},
    )

    assert field_plan["version"] == "business_bid_draft_field_plan_v1.4.1"
    assert "互联网企业事实补充" in field_plan["llm_source_policy"]["disallowed_sources"]
    assert field_plan["summary"]["yellow_placeholder_count"] >= 4
    assert any(item["mode"] == "llm_draft" for item in field_plan["fields"])
    assert any(item["placeholder"].startswith("【人工填写：招标人/招标代理") for item in field_plan["fields"])


def test_pdf_prints_yellow_placeholder_text_for_manual_and_llm_fields():
    directory = [
        {"sequence": 1, "item_key": "business:bid_letter", "title": "投标函"},
        {"sequence": 2, "item_key": "business:pricing_summary", "title": "投标报价汇总表"},
        {"sequence": 3, "item_key": "business:boq", "title": "工程量清单报价表"},
    ]
    template_plan = build_business_bid_template_plan(directory)
    field_plan = build_business_bid_draft_field_plan(
        project=_project_with_missing_manual_fields(),
        quote_import=_quote_import(),
        template_plan=template_plan,
        requirements=[],
        v12_report={"business_responses": {"items": []}},
    )
    assembly = {
        "directory": directory,
        "template": template_plan,
        "draft_field_plan": field_plan,
        "v12_review": {"business_responses": {"items": []}},
        "attachment_index": [],
    }

    reader = PdfReader(BytesIO(build_business_bid_pdf(_Db(), _project_with_missing_manual_fields(), _quote_import(), assembly=assembly)))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "【人工填写：招标人/招标代理" in text
    assert "【待确认 LLM 草稿：投标函承诺正文" in text
    assert "【人工签章：投标人签章" in text
    assert "【人工复核：清单税费/暂估价/招标约定复核" in text
