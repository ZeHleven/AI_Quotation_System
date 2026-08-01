from types import SimpleNamespace

from app.services.bidding_business_bid import _amount_uppercase, build_business_bid_pdf, serialize_business_bid_quote_import


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
        import_uuid="import-1",
        version_no=1,
        status="active",
        budget_project_id=3,
        source_project_name="预算测试项目",
        source_draft_uuid="draft-1",
        source_draft_revision=2,
        pricing_mode="manual",
        source_snapshot_sha256="hash",
        line_count=1,
        total_amount="1234.50",
        snapshot_json='{"summary":{"pricing_mode":"manual"},"lines":[{"sequence":1,"item_name":"轻钢龙骨隔墙","spec":"75mm","unit":"m2","quantity":"10","unit_price":"123.45","line_total":"1234.50","remark":"测试"}]}',
        import_note=None,
        created_by=1,
        created_at=None,
        superseded_at=None,
    )


def test_business_bid_amount_uppercase_uses_chinese_currency():
    assert _amount_uppercase("1234.50") == "壹仟贰佰叁拾肆元伍角"
    assert _amount_uppercase("0") == "零元整"


def test_business_bid_snapshot_serialization_includes_lines():
    data = serialize_business_bid_quote_import(_quote_import(), include_lines=True)
    assert data["version_no"] == 1
    assert data["total_amount"] == "1234.50"
    assert data["lines"][0]["item_name"] == "轻钢龙骨隔墙"


def test_business_bid_pdf_is_generated_with_chinese_draft_content():
    project = SimpleNamespace(project_name="商务标测试项目", tenderer_name="测试投标人", id=1)
    content = build_business_bid_pdf(_Db(), project, _quote_import())
    assert content.startswith(b"%PDF")
    assert len(content) > 3000