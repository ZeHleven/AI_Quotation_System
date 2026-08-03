from __future__ import annotations

from app.services.quantity_standard_index import (
    find_standard_item,
    infer_standard_routes,
    load_standard_library_index,
    search_pricing_rules,
    search_standard_index,
    standard_index_summary,
)


def test_biz2x_r0_loads_multi_standard_index():
    index = load_standard_library_index()
    summary = standard_index_summary(index)

    assert summary["quantity_standard_count"] >= 2
    assert summary["pricing_rule_standard_count"] >= 1
    assert "GBT50854-2024" in summary["active_quantity_standard_codes"]
    assert "GBT50856-2024" in summary["active_quantity_standard_codes"]
    assert summary["quantity_libraries"]["GBT50854-2024"]["active_count"] >= 400
    assert summary["quantity_libraries"]["GBT50856-2024"]["active_count"] >= 1000
    assert summary["pricing_rule_libraries"]["GBT50500-2024"]["rule_count"] >= 800


def test_biz2x_r0_searches_decoration_and_installation_libraries():
    index = load_standard_library_index()

    floor_hits = search_standard_index("CT-01 750x1500灰色地砖地面", index=index)
    electrical_hits = search_standard_index("配电箱 AL-01", index=index)
    water_meter_hits = search_standard_index("水表安装", index=index)

    assert any(hit["standard_code"] == "GBT50854-2024" and hit["item_code"] == "011102003" for hit in floor_hits)
    assert any(hit["standard_code"] == "GBT50856-2024" and hit["item_code"] == "030402011" for hit in electrical_hits)
    assert any(hit["standard_code"] == "GBT50856-2024" and hit["item_code"] == "031002011" for hit in water_meter_hits)


def test_biz2x_r0_routes_fine_subitem_to_standard_parent():
    index = load_standard_library_index()

    hits = search_standard_index("地漏供货及安装", index=index)
    parent = find_standard_item(index, "GBT50856-2024", "031003014")

    assert parent is not None
    assert parent.item_name == "给、排水附件"
    assert hits[0]["standard_code"] == "GBT50856-2024"
    assert hits[0]["item_code"] == "031003014"
    assert infer_standard_routes("地漏供货及安装") == ["GBT50856-2024"]


def test_biz2x_r0_pricing_rules_are_available_but_not_item_library():
    index = load_standard_library_index()

    rules = search_pricing_rules("清单", index=index, limit=3)

    assert rules
    assert all(rule["standard_code"] == "GBT50500-2024" for rule in rules)
