from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader

from app.services.bidding_business_bid import build_business_bid_pdf


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
        version_no=2,
        line_count=2,
        total_amount="2234.50",
        source_project_name="预算测试项目",
        source_draft_revision=3,
        pricing_mode="manual",
        snapshot_json='{"summary":{"pricing_mode":"manual"},"lines":[{"sequence":1,"item_name":"轻钢龙骨隔墙","spec":"75mm","unit":"m2","quantity":"10","unit_price":"123.45","line_total":"1234.50","remark":"测试"},{"sequence":2,"item_name":"乳胶漆","spec":"两遍","unit":"m2","quantity":"20","unit_price":"50","line_total":"1000","remark":"测试"}]}',
    )


def test_business_bid_v14_booklet_has_fixed_forms_toc_and_landscape_bill():
    project = SimpleNamespace(
        project_name="商务标 V1.4 测试项目",
        tenderer_name="测试投标人",
        tender_agency="测试招标人",
        summary_json='{"tender_number":"TEST-001"}',
        id=1,
    )
    assembly = {
        "directory": [
            {"item_key": "business:bid_letter"},
            {"item_key": "business:pricing_summary"},
            {"item_key": "business:boq"},
            {"item_key": "business:legal_representative"},
            {"item_key": "business:authorization"},
            {"item_key": "business:commitment"},
            {"item_key": "business:business_deviation"},
        ],
        "v12_review": {"business_responses": {"items": []}},
        "attachment_index": [],
    }

    reader = PdfReader(BytesIO(build_business_bid_pdf(_Db(), project, _quote_import(), assembly=assembly)))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    page_sizes = {(round(float(page.mediabox.width)), round(float(page.mediabox.height))) for page in reader.pages}

    assert len(reader.pages) >= 8
    assert "投 标 函" in text
    assert "投 标 报 价 汇 总 表" in text
    assert "商 务 条 款 偏 离 表" in text
    assert "工 程 量 清 单 报 价 表" in text
    assert "目录" in text
    assert any(width > height for width, height in page_sizes)
    assert any(height > width for width, height in page_sizes)
