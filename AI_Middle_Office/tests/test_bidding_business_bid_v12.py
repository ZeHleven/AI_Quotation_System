from types import SimpleNamespace

from app.services.bidding_business_bid_v12 import build_business_bid_v12_report


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, response_rows):
        self.response_rows = response_rows

    def query(self, *args, **kwargs):
        return _Query(self.response_rows)


def _quote_import(*, total="1234.50", line_total="1234.50"):
    return SimpleNamespace(
        line_count=1,
        total_amount=total,
        snapshot_json=(
            '{"lines":[{"sequence":1,"item_name":"轻钢龙骨隔墙","unit":"m2",'
            '"quantity":"10","unit_price":"123.45","line_total":"' + line_total + '"}]}'
        ),
    )


def _response(*, status="done", risk_level="high", note="已按招标文件承诺", evidence='[{"source_file":"招标文件.pdf"}]'):
    return SimpleNamespace(
        response_item_uuid="response-1",
        response_category="commercial_requirement",
        response_action="direct_response",
        response_title="投标有效期承诺",
        source_text="投标有效期为 90 日。",
        owner_role="经营",
        risk_level=risk_level,
        status=status,
        response_note=note,
        reviewer_note="已复核",
        evidence_json=evidence,
        normalized_json="{}",
    )


def _directory():
    return [{"item_key": "business:boq", "title": "工程量清单报价表", "content_type": "pricing_table", "generation_strategy": "from_cost_quote"}]


def test_v12_passes_consistent_quote_and_closed_high_risk_business_response():
    report = build_business_bid_v12_report(_Db([_response()]), SimpleNamespace(id=1), _quote_import(), _directory())

    assert report["formal_ready"] is True
    assert report["quote_consistency"]["summary"]["calculated_total"] == "1,234.50"
    assert report["business_responses"]["summary"]["resolved_count"] == 1


def test_v12_blocks_total_mismatch_and_unresolved_business_response():
    report = build_business_bid_v12_report(
        _Db([_response(status="to_clarify", note="")]),
        SimpleNamespace(id=1),
        _quote_import(total="1300.00"),
        _directory(),
    )

    codes = {item["code"] for item in report["formal_blocking_items"]}
    assert report["formal_ready"] is False
    assert "quote_total_mismatch" in codes
    assert "business_response_unresolved" in codes