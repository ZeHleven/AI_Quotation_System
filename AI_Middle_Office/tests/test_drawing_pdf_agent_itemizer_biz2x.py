from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from app.services.drawing_pdf_agent_itemizer import (
    build_agent_itemization_report_from_views,
    build_agent_standard_mapping_rows,
    build_agent_four_field_rows,
    call_dashscope_agent_bill_summarizer,
    call_dashscope_agent_evidence_extractor,
    call_openai_agent_bill_summarizer,
    call_openai_agent_evidence_extractor,
    classify_agent_bill_items,
    merge_agent_evidence,
    parse_agent_bill_items_json,
    parse_agent_evidence_json,
    prepare_openai_agent_view_payloads,
    select_agent_views,
    write_pdf_agent_itemization_outputs,
)


def test_select_agent_views_keeps_whole_page_and_prefers_cad_views(tmp_path):
    whole_page = tmp_path / "whole.png"
    cad_one = tmp_path / "cad1.png"
    cad_two = tmp_path / "cad2.png"
    grid = tmp_path / "grid.png"
    for path in [whole_page, cad_one, cad_two, grid]:
        path.write_bytes(b"fake image bytes")

    selected = select_agent_views(
        [
            {
                "tile_id": "p001_g01",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "grid",
                "image_path": str(grid),
                "priority": 70,
            },
            {
                "tile_id": "p001_view002",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "image_path": str(cad_two),
                "priority": 250,
            },
            {
                "tile_id": "p001_whole",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "whole_page_preview",
                "image_path": str(whole_page),
                "priority": 100,
            },
            {
                "tile_id": "p001_view001",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "image_path": str(cad_one),
                "priority": 250,
            },
        ],
        max_views=3,
        include_whole_page=True,
    )

    assert [row["tile_id"] for row in selected] == ["p001_whole", "p001_view001", "p001_view002"]


def test_select_agent_views_reserves_page_context_grids_when_budget_allows(tmp_path):
    whole_page = tmp_path / "whole.png"
    cad_paths = [tmp_path / f"cad{i}.png" for i in range(1, 8)]
    grid_paths = [tmp_path / f"grid{i}.png" for i in range(1, 5)]
    for path in [whole_page, *cad_paths, *grid_paths]:
        path.write_bytes(b"fake image bytes")

    rows = [
        {
            "tile_id": "p001_whole",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "whole_page_preview",
            "image_path": str(whole_page),
            "priority": 100,
            "bbox_pixel": [0, 0, 3000, 2000],
        }
    ]
    rows.extend(
        {
            "tile_id": f"p001_view{i:03d}",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "cad_view",
            "image_path": str(path),
            "priority": 250,
            "bbox_pixel": [10 * i, 10 * i, 100 + 10 * i, 100 + 10 * i],
        }
        for i, path in enumerate(cad_paths, start=1)
    )
    rows.extend(
        [
            {
                "tile_id": "p001_g03_r01_c01",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "grid",
                "image_path": str(grid_paths[0]),
                "priority": 70,
                "bbox_pixel": [0, 0, 1000, 700],
            },
            {
                "tile_id": "p001_g03_r01_c03",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "grid",
                "image_path": str(grid_paths[1]),
                "priority": 70,
                "bbox_pixel": [2000, 0, 3000, 700],
            },
            {
                "tile_id": "p001_g03_r03_c01",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "grid",
                "image_path": str(grid_paths[2]),
                "priority": 70,
                "bbox_pixel": [0, 1400, 1000, 2000],
            },
            {
                "tile_id": "p001_g03_r03_c03",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "grid",
                "image_path": str(grid_paths[3]),
                "priority": 70,
                "bbox_pixel": [2000, 1400, 3000, 2000],
            },
        ]
    )

    selected = select_agent_views(rows, max_views=10, include_whole_page=True)

    assert len(selected) == 10
    assert selected[0]["tile_id"] == "p001_whole"
    assert selected[0]["selection_role"] == "whole_page_context"
    context_grids = [row for row in selected if row.get("selection_role") == "page_context"]
    assert [row["tile_id"] for row in context_grids] == ["p001_g03_r03_c03", "p001_g03_r01_c03"]
    assert sum(1 for row in selected if row.get("selection_role") == "local_cad_view") == 7


def test_parse_and_merge_agent_evidence_keeps_source_view_ids():
    rows = parse_agent_evidence_json(
        {
            "drawing_evidence": [
                {
                    "view_id": "p001_view001",
                    "view_title": "地面铺装图",
                    "view_type": "floor_plan",
                    "spaces": ["餐厅"],
                    "visible_texts": ["CT-01"],
                    "material_codes": [{"code": "CT-01", "name_or_hint": "地砖", "spec_or_method": "600x1200", "confidence": 0.83}],
                    "objects": [{"name": "餐厅地面", "space": "餐厅", "method": "铺贴", "unit_hint": "m2", "confidence": 0.8}],
                    "methods": ["地面瓷砖铺贴"],
                    "quantity_clues": [{"text": "约135m2", "meaning": "地面面积粗估", "confidence": 0.6}],
                    "confidence": 0.82,
                },
                {
                    "view_id": "p001_view006",
                    "view_title": "立面图",
                    "view_type": "elevation",
                    "spaces": ["餐厅"],
                    "visible_texts": ["CT-01"],
                    "material_codes": [{"code": "CT-01", "name_or_hint": "瓷砖", "spec_or_method": "美缝", "confidence": 0.76}],
                    "methods": ["墙面瓷砖湿贴", "地面瓷砖铺贴"],
                    "confidence": 0.74,
                },
            ]
        }
    )

    merged = merge_agent_evidence(rows)

    material = merged["merged_materials"][0]
    assert material["code"] == "CT-01"
    assert material["source_view_ids"] == ["p001_view001", "p001_view006"]
    assert {item["view_id"] for item in merged["source_views"]} == {"p001_view001", "p001_view006"}
    assert any(item["method"] == "地面瓷砖铺贴" and item["evidence_count"] == 2 for item in merged["merged_methods"])


def test_merge_agent_evidence_builds_global_context_from_context_views():
    rows = parse_agent_evidence_json(
        {
            "drawing_evidence": [
                {
                    "view_id": "p001_whole",
                    "view_title": "职工餐厅材料表",
                    "view_type": "material_table",
                    "visible_texts": ["材料表：CT 地砖 600x1200；ST 石材"],
                    "material_codes": [
                        {"code": "CT", "name_or_hint": "地砖", "spec_or_method": "600x1200", "confidence": 0.86}
                    ],
                    "evidence_notes": ["整页右下角可见材料说明"],
                    "confidence": 0.82,
                },
                {
                    "view_id": "p001_view001",
                    "view_title": "地面铺装图",
                    "view_type": "floor_plan",
                    "methods": ["地面瓷砖铺贴"],
                    "confidence": 0.8,
                },
            ]
        }
    )

    merged = merge_agent_evidence(
        rows,
        view_manifest=[
            {"view_id": "p001_whole", "tile_type": "whole_page_preview", "selection_role": "whole_page_context"},
            {"view_id": "p001_view001", "tile_type": "cad_view", "selection_role": "local_cad_view"},
        ],
    )

    context = merged["global_context"]
    assert context["context_view_ids"] == ["p001_whole"]
    assert context["material_legend_candidates"][0]["code"] == "CT"
    assert "材料表：CT 地砖 600x1200；ST 石材" in context["visible_texts"]


def test_agent_bill_items_to_four_fields_with_standard_name():
    bill_items = parse_agent_bill_items_json(
        {
            "bill_items": [
                {
                    "concrete_item_name": "餐厅地面瓷砖铺贴CT系列",
                    "feature": "餐厅主要区域地面块料铺装；材料代号CT系列",
                    "unit": "m2",
                    "rough_quantity": "约135",
                    "quantity_note": "按图面区域粗估，待复核",
                    "source_view_ids": ["p001_view001", "p001_view006"],
                    "source_evidence": ["地面铺装图可见CT标注"],
                    "confidence": 0.78,
                    "needs_manual_review": True,
                }
            ]
        }
    )

    def fake_standard_search(query, limit=5):
        assert "餐厅地面瓷砖铺贴CT系列" in query
        return [
            {
                "standard_code": "GBT50854-2024",
                "item_code": "011102003",
                "item_name": "块料楼地面",
                "chapter_name": "楼地面装饰工程",
                "unit_options": ["m2"],
                "score": 42.0,
                "match_reason": "fake standard match",
            }
        ]

    mapping_rows = build_agent_standard_mapping_rows(bill_items, standard_search=fake_standard_search)
    four_field_rows = build_agent_four_field_rows(mapping_rows)

    assert four_field_rows == [
        {
            "项目名称": "餐厅地面瓷砖铺贴CT系列（块料楼地面）",
            "项目特征": "餐厅主要区域地面块料铺装；材料代号CT系列；来源视图：p001_view001,p001_view006；图纸证据：地面铺装图可见CT标注；复核提示：AI识图草稿，需人工复核",
            "单位": "m2",
            "工程量": "约135，待复核",
        }
    ]


def test_classify_agent_bill_items_filters_loose_furniture_and_keeps_work_items():
    items = [
        {
            "item_id": "PDFAGITEM-000001",
            "concrete_item_name": "职工餐厅餐桌布置",
            "feature": "平面图可见餐桌摆放",
            "source_evidence": ["平面布置图可见餐桌"],
        },
        {
            "item_id": "PDFAGITEM-000002",
            "concrete_item_name": "职工餐厅售卖口固定吧台制作安装",
            "feature": "固定吧台，台面安装",
            "source_evidence": ["立面图可见售卖口台面"],
        },
        {
            "item_id": "PDFAGITEM-000003",
            "concrete_item_name": "职工餐厅窗帘",
            "feature": "图纸证据不足",
            "source_evidence": ["平面图疑似窗帘"],
        },
    ]

    report = classify_agent_bill_items(items)

    assert [item["item_id"] for item in report["mappable_items"]] == ["PDFAGITEM-000002"]
    assert report["mappable_items"][0]["itemizability_status"] == "定制项"
    assert [item["item_id"] for item in report["filtered_items"]] == ["PDFAGITEM-000001"]
    assert report["filtered_items"][0]["itemizability_status"] == "非施工项"
    assert [item["item_id"] for item in report["manual_review_items"]] == ["PDFAGITEM-000003"]
    assert report["manual_review_items"][0]["itemizability_status"] == "待确认项"


def test_build_agent_itemization_report_filters_non_construction_before_standard_mapping(tmp_path):
    image_path = tmp_path / "view.png"
    image_path.write_bytes(b"view")
    selected_views = [
        {
            "tile_id": "p001_view001",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "cad_view",
            "image_path": str(image_path),
            "priority": 250,
        }
    ]

    def fake_evidence_extractor(view_manifest):
        return {"drawing_evidence": [{"view_id": view_manifest[0]["view_id"], "confidence": 0.8}]}

    def fake_bill_summarizer(merged_evidence):
        return {
            "bill_items": [
                {
                    "item_id": "PDFAGITEM-000001",
                    "concrete_item_name": "职工餐厅墙面CT-1地砖湿贴",
                    "feature": "墙面地砖湿贴",
                    "unit": "m2",
                    "rough_quantity": "待复核",
                    "source_view_ids": ["p001_view001"],
                    "source_evidence": ["立面图可见CT-1墙面湿贴"],
                },
                {
                    "item_id": "PDFAGITEM-000002",
                    "concrete_item_name": "职工餐厅餐椅配套",
                    "feature": "平面图可见餐椅布置",
                    "unit": "把",
                    "rough_quantity": "待复核",
                    "source_view_ids": ["p001_view001"],
                    "source_evidence": ["平面图可见餐椅"],
                },
            ]
        }

    queries = []

    def fake_standard_search(query, limit=5):
        queries.append(query)
        return [
            {
                "standard_code": "GBT50854-2024",
                "item_code": "011204003",
                "item_name": "块料墙、柱面",
                "chapter_name": "墙、柱面装饰与隔断、幕墙工程",
                "unit_options": ["m2"],
                "score": 40,
                "match_reason": "fake wall tile match",
            }
        ]

    report = build_agent_itemization_report_from_views(
        selected_views,
        evidence_extractor=fake_evidence_extractor,
        bill_summarizer=fake_bill_summarizer,
        standard_search=fake_standard_search,
    )

    assert report["summary"]["agent_bill_item_count"] == 2
    assert report["summary"]["itemizability_mappable_count"] == 1
    assert report["summary"]["itemizability_filtered_non_construction_count"] == 1
    assert report["summary"]["standard_mapping_count"] == 1
    assert report["summary"]["quantity_list_row_count"] == 1
    assert report["source_mode"] == "pdf_agent_model_flow"
    assert report["summary"]["agent_status"] == "model_flow_completed"
    assert report["agent_filtered_items"][0]["concrete_item_name"] == "职工餐厅餐椅配套"
    assert "餐椅" not in " ".join(queries)


def test_agent_standard_mapping_prefers_obvious_decoration_category_over_bad_top_hit():
    bill_items = [
        {
            "item_id": "PDFAGITEM-000001",
            "concrete_item_name": "职工餐厅门套MR-1木饰面收边安装",
            "feature": "门套木饰面收边安装",
            "unit": "个",
            "rough_quantity": "待复核",
            "source_view_ids": ["xinda_view_008"],
            "source_evidence": ["MR-1；门套"],
            "needs_manual_review": True,
        }
    ]

    def fake_standard_search(query, limit=5):
        if query == "木门窗套":
            return [
                {
                    "standard_code": "GBT50854-2024",
                    "item_code": "010808001",
                    "item_name": "木门窗套",
                    "chapter_name": "门窗工程",
                    "unit_options": ["m²"],
                    "score": 25,
                    "match_reason": "preferred decoration category",
                }
            ]
        return [
            {
                "standard_code": "GBT50856-2024",
                "item_code": "031101001",
                "item_name": "物联网 | M 2 M 设备",
                "chapter_name": "附录",
                "unit_options": ["个"],
                "score": 50,
                "match_reason": "bad fuzzy hit",
            }
        ]

    mapping_rows = build_agent_standard_mapping_rows(bill_items, standard_search=fake_standard_search)
    four_field_rows = build_agent_four_field_rows(mapping_rows)

    assert mapping_rows[0]["标准项目名称"] == "木门窗套"
    assert mapping_rows[0]["标准单位"] == "m²"
    assert four_field_rows[0]["项目名称"] == "职工餐厅门套MR-1木饰面收边安装（木门窗套）"


def test_build_agent_itemization_report_from_views_runs_stubbed_model_flow(tmp_path):
    whole_page = tmp_path / "whole.png"
    cad_view = tmp_path / "view.png"
    whole_page.write_bytes(b"whole")
    cad_view.write_bytes(b"view")

    selected_views = [
        {
            "tile_id": "p001_whole",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "whole_page_preview",
            "image_path": str(whole_page),
            "priority": 100,
        },
        {
            "tile_id": "p001_view001",
            "source_file": "drawing.pdf",
            "page": 1,
            "tile_type": "cad_view",
            "image_path": str(cad_view),
            "priority": 250,
        },
    ]

    def fake_evidence_extractor(view_manifest):
        assert view_manifest[0]["view_id"] == "p001_whole"
        return {
            "drawing_evidence": [
                {
                    "view_id": "p001_view001",
                    "view_title": "天花布置图",
                    "view_type": "ceiling_plan",
                    "spaces": ["餐厅"],
                    "methods": ["跌级造型石膏板吊顶"],
                    "objects": [{"name": "跌级造型石膏板吊顶", "space": "餐厅", "method": "安装", "unit_hint": "m2", "confidence": 0.82}],
                    "confidence": 0.8,
                }
            ]
        }

    def fake_bill_summarizer(merged_evidence):
        assert merged_evidence["evidence_count"] == 1
        return {
            "bill_items": [
                {
                    "concrete_item_name": "餐厅跌级造型石膏板吊顶",
                    "feature": "餐厅天花跌级造型；来源天花布置图",
                    "unit": "m2",
                    "rough_quantity": "待复核",
                    "source_view_ids": ["p001_view001"],
                    "source_evidence": ["天花布置图可见跌级吊顶范围"],
                    "confidence": 0.75,
                    "needs_manual_review": True,
                }
            ]
        }

    def fake_standard_search(query, limit=5):
        return [
            {
                "standard_code": "GBT50854-2024",
                "item_code": "011302001",
                "item_name": "吊顶天棚",
                "chapter_name": "天棚工程",
                "unit_options": ["m2"],
                "score": 36.0,
                "match_reason": "fake ceiling match",
            }
        ]

    report = build_agent_itemization_report_from_views(
        selected_views,
        evidence_extractor=fake_evidence_extractor,
        bill_summarizer=fake_bill_summarizer,
        standard_search=fake_standard_search,
    )

    assert report["ok"] is True
    assert report["source_mode"] == "pdf_agent_model_flow"
    assert "fake" not in report["source_mode"]
    assert "injected" not in report["source_mode"]
    assert report["summary"]["agent_status"] == "model_flow_completed"
    assert report["summary"]["selected_view_count"] == 2
    assert report["summary"]["context_view_count"] == 1
    assert report["summary"]["local_view_count"] == 1
    assert report["summary"]["agent_evidence_count"] == 1
    assert report["quantity_list_rows"][0]["项目名称"] == "餐厅跌级造型石膏板吊顶（吊顶天棚）"


def test_prepare_openai_agent_view_payloads_encodes_existing_images(tmp_path):
    image_path = tmp_path / "view.png"
    image_path.write_bytes(b"fake image bytes")

    payloads = prepare_openai_agent_view_payloads(
        [
            {
                "view_id": "p001_view001",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "image_path": str(image_path),
            },
            {
                "view_id": "p001_view_missing",
                "image_path": str(tmp_path / "missing.png"),
            },
        ]
    )

    assert len(payloads) == 1
    assert payloads[0]["view_id"] == "p001_view001"
    assert payloads[0]["mime_type"] == "image/png"
    assert payloads[0]["image_base64"]


def test_openai_agent_adapters_call_model_gateway(monkeypatch, tmp_path):
    image_path = tmp_path / "view.png"
    image_path.write_bytes(b"fake image bytes")
    calls = []

    async def fake_evidence_extract(view_payloads, *, model_override=None, username=None, trace_id=None):
        calls.append(
            {
                "fn": "evidence",
                "view_payloads": view_payloads,
                "model_override": model_override,
                "username": username,
                "trace_id": trace_id,
            }
        )
        return {
            "drawing_evidence": [
                {
                    "view_id": view_payloads[0]["view_id"],
                    "view_type": "floor_plan",
                    "methods": ["地面瓷砖铺贴"],
                    "confidence": 0.8,
                }
            ]
        }

    async def fake_bill_summarize(merged_evidence, *, model_override=None, username=None, trace_id=None):
        calls.append(
            {
                "fn": "bill",
                "merged_evidence": merged_evidence,
                "model_override": model_override,
                "username": username,
                "trace_id": trace_id,
            }
        )
        return {
            "bill_items": [
                {
                    "concrete_item_name": "餐厅地面瓷砖铺贴CT系列",
                    "feature": "餐厅主要区域地面块料铺装",
                    "unit": "m2",
                    "rough_quantity": "约135",
                    "source_view_ids": ["p001_view001"],
                }
            ]
        }

    monkeypatch.setattr("app.services.model_gateway.call_openai_pdf_agent_evidence_extract", fake_evidence_extract)
    monkeypatch.setattr("app.services.model_gateway.call_openai_pdf_agent_bill_summarize", fake_bill_summarize)

    evidence = call_openai_agent_evidence_extractor(
        [
            {
                "view_id": "p001_view001",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "image_path": str(image_path),
            }
        ],
        username="tester",
        trace_id="trace-agent",
        batch_size=1,
    )
    bill = call_openai_agent_bill_summarizer({"merged_methods": [{"method": "地面瓷砖铺贴"}]}, username="tester", trace_id="trace-agent")

    assert evidence["drawing_evidence"][0]["view_id"] == "p001_view001"
    assert bill["bill_items"][0]["concrete_item_name"] == "餐厅地面瓷砖铺贴CT系列"
    assert calls[0]["fn"] == "evidence"
    assert calls[0]["view_payloads"][0]["image_base64"]
    assert calls[0]["trace_id"] == "trace-agent-evidence-b1"
    assert calls[1]["fn"] == "bill"
    assert calls[1]["trace_id"] == "trace-agent-bill"


def test_dashscope_agent_adapters_call_model_gateway(monkeypatch, tmp_path):
    image_path = tmp_path / "view.png"
    image_path.write_bytes(b"fake image bytes")
    calls = []

    async def fake_evidence_extract(view_payloads, *, model_override=None, username=None, trace_id=None):
        calls.append(
            {
                "fn": "evidence",
                "view_payloads": view_payloads,
                "model_override": model_override,
                "username": username,
                "trace_id": trace_id,
            }
        )
        return {
            "drawing_evidence": [
                {
                    "view_id": view_payloads[0]["view_id"],
                    "view_type": "elevation",
                    "methods": ["墙面瓷砖湿贴"],
                    "confidence": 0.82,
                }
            ]
        }

    async def fake_bill_summarize(merged_evidence, *, model_override=None, username=None, trace_id=None):
        calls.append(
            {
                "fn": "bill",
                "merged_evidence": merged_evidence,
                "model_override": model_override,
                "username": username,
                "trace_id": trace_id,
            }
        )
        return {
            "bill_items": [
                {
                    "concrete_item_name": "餐厅墙面瓷砖湿贴CT系列",
                    "feature": "餐厅立面墙面瓷砖湿贴",
                    "unit": "m2",
                    "rough_quantity": "待复核",
                    "source_view_ids": ["p001_view001"],
                }
            ]
        }

    monkeypatch.setattr("app.services.model_gateway.call_dashscope_pdf_agent_evidence_extract", fake_evidence_extract)
    monkeypatch.setattr("app.services.model_gateway.call_dashscope_pdf_agent_bill_summarize", fake_bill_summarize)

    evidence = call_dashscope_agent_evidence_extractor(
        [
            {
                "view_id": "p001_view001",
                "source_file": "drawing.pdf",
                "page": 1,
                "tile_type": "cad_view",
                "image_path": str(image_path),
            }
        ],
        username="tester",
        trace_id="trace-dashscope-agent",
        batch_size=1,
    )
    bill = call_dashscope_agent_bill_summarizer(
        {"merged_methods": [{"method": "墙面瓷砖湿贴"}]},
        username="tester",
        trace_id="trace-dashscope-agent",
    )

    assert evidence["drawing_evidence"][0]["view_id"] == "p001_view001"
    assert bill["bill_items"][0]["concrete_item_name"] == "餐厅墙面瓷砖湿贴CT系列"
    assert calls[0]["fn"] == "evidence"
    assert calls[0]["view_payloads"][0]["image_base64"]
    assert calls[0]["trace_id"] == "trace-dashscope-agent-evidence-b1"
    assert calls[1]["fn"] == "bill"
    assert calls[1]["trace_id"] == "trace-dashscope-agent-bill"


def test_write_agent_outputs_writes_process_files_and_xlsx(tmp_path):
    report = {
        "ok": True,
        "phase": "BIZ-2x-pdf-agent-itemization",
        "summary": {"selected_view_count": 1, "agent_evidence_count": 1, "agent_bill_item_count": 1, "quantity_list_row_count": 1},
        "view_manifest": [{"view_id": "p001_view001", "image_path": "view.png"}],
        "agent_evidence_rows": [{"view_id": "p001_view001", "methods": ["地面瓷砖铺贴"]}],
        "merged_evidence": {"evidence_count": 1},
        "agent_bill_items": [{"concrete_item_name": "餐厅地面瓷砖铺贴CT系列"}],
        "standard_mapping_rows": [
            {
                "识别编号": "PDFAGITEM-000001",
                "映射状态": "standard_mapped",
                "标准号": "GBT50854-2024",
                "标准项目编码": "011102003",
                "标准项目名称": "块料楼地面",
                "标准章节": "楼地面装饰工程",
                "标准单位": "m2",
                "匹配分数": 42.0,
                "匹配原因": "fake standard match",
                "候选数量": 1,
                "具体做法名称": "餐厅地面瓷砖铺贴CT系列",
                "来源视图": "p001_view001",
                "项目特征": "来源视图：p001_view001",
                "工程量": "约135，待复核",
            }
        ],
        "quantity_list_rows": [
            {
                "项目名称": "餐厅地面瓷砖铺贴CT系列（块料楼地面）",
                "项目特征": "来源视图：p001_view001",
                "单位": "m2",
                "工程量": "约135，待复核",
            }
        ],
        "issues": [],
        "outputs": {},
    }

    outputs = write_pdf_agent_itemization_outputs(
        report,
        business_dir=tmp_path / "business",
        debug_dir=tmp_path / "debug",
        run_timestamp="20260622_010203",
    )

    expected_keys = {
        "quantity_list_xlsx",
        "quantity_list_csv",
        "view_manifest_json",
        "agent_evidence_json",
        "merged_evidence_json",
        "bill_items_raw_json",
        "agent_report_json",
        "standard_mapping_csv",
        "agent_report_markdown",
    }
    assert expected_keys.issubset(outputs)
    for path in outputs.values():
        assert Path(path).exists()

    workbook = load_workbook(outputs["quantity_list_xlsx"])
    sheet = workbook.active
    assert [cell.value for cell in sheet[1]] == ["项目名称", "项目特征", "单位", "工程量"]
    assert sheet["A2"].value == "餐厅地面瓷砖铺贴CT系列（块料楼地面）"
