from types import SimpleNamespace

import pytest

from app.services import bidding_business_bid_assembly as assembly_service
from app.services.bidding_business_bid import build_business_bid_pdf
from app.services.bidding_business_bid_assembly import BusinessBidAssemblyError, build_business_bid_assembly, ensure_business_bid_formal_exportable


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
        line_count=1,
        total_amount="1234.50",
        source_project_name="预算测试项目",
        source_draft_revision=3,
        pricing_mode="manual",
        snapshot_json='{"summary":{"pricing_mode":"manual"},"lines":[{"sequence":1,"item_name":"轻钢龙骨隔墙","spec":"75mm","unit":"m2","quantity":"10","unit_price":"123.45","line_total":"1234.50","remark":"测试"}]}',
    )


def _plan(review_status="confirmed"):
    return SimpleNamespace(
        plan_uuid="format-plan-1",
        review_status=review_status,
        structure_json='{"packages":[{"package_key":"business","items":[{"item_key":"quotation","item_title":"已标价工程量清单","content_type":"pricing_table","generation_strategy":"system","requires_attachment":false,"is_required":true},{"item_key":"license","item_title":"营业执照","content_type":"attachment","generation_strategy":"manual","requires_attachment":true,"is_required":true}]}]}',
    )


def _project():
    return SimpleNamespace(id=1, project_uuid="project-1", project_name="商务标测试项目", tenderer_name="测试投标人")


def _run():
    return SimpleNamespace(id=1, run_uuid="run-1")


def test_formal_export_is_blocked_until_format_plan_is_confirmed(monkeypatch):
    monkeypatch.setattr(assembly_service, "get_bid_file_format_plan", lambda db, run: _plan("draft"))
    assembly = build_business_bid_assembly(_Db(), _project(), _run(), _quote_import())

    assert assembly["draft_ready"] is True
    assert assembly["formal_ready"] is False
    assert assembly["blocking_items"][0]["code"] == "format_plan_unconfirmed"
    with pytest.raises(BusinessBidAssemblyError) as exc_info:
        ensure_business_bid_formal_exportable(assembly)
    assert exc_info.value.code == "BUSINESS_BID_FORMAL_EXPORT_BLOCKED"


def test_confirmed_directory_requires_business_response_review_before_formal_pdf(monkeypatch):
    monkeypatch.setattr(assembly_service, "get_bid_file_format_plan", lambda db, run: _plan("confirmed"))
    assembly = build_business_bid_assembly(_Db(), _project(), _run(), _quote_import())

    assert assembly["formal_ready"] is False
    assert [item["title"] for item in assembly["directory"]] == ["已标价工程量清单", "营业执照"]
    with pytest.raises(BusinessBidAssemblyError):
        ensure_business_bid_formal_exportable(assembly)
    content = build_business_bid_pdf(_Db(), _project(), _quote_import(), assembly=assembly, export_mode="draft")
    assert content.startswith(b"%PDF")
    assert len(content) > 3000