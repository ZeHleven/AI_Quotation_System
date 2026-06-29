from __future__ import annotations

from typing import Any, Mapping

from app.services.codex_worker_contract import CODEX_WORKER_SCHEMA_VERSION


FAKE_CODEX_WORKER_SAMPLES = {"valid", "missing-field", "non-construction", "missing-evidence"}


def build_fake_codex_result(
    sample: str = "valid",
    *,
    job_id: str,
    source_files: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if sample not in FAKE_CODEX_WORKER_SAMPLES:
        raise ValueError(f"Unsupported fake Codex Worker sample: {sample}")

    source_file_rows = [dict(item) for item in source_files or []] or [
        {"file_name": "source.pdf", "page_count": 1, "sha256": "fake"}
    ]
    result = {
        "schema_version": CODEX_WORKER_SCHEMA_VERSION,
        "job_id": job_id,
        "status": "succeeded",
        "source_files": source_file_rows,
        "summary": {
            "view_count": 2,
            "evidence_count": 2,
            "quantity_list_row_count": 2,
            "manual_review_count": 2,
            "filtered_non_construction_count": 1,
        },
        "quantity_list_rows": [
            {
                "row_id": "CODPDF-ITEM-000001",
                "项目名称": "职工餐厅墙面 CT-1 地砖湿贴（块料墙、柱面）",
                "项目特征": "空间：职工餐厅；材料：CT-1；做法：墙面湿贴；来源：EV-000001；工程量待复核",
                "单位": "m2",
                "工程量": "约20.5，待复核",
                "itemizability_status": "施工项",
                "needs_manual_review": True,
                "evidence_refs": ["EV-000001"],
            },
            {
                "row_id": "CODPDF-ITEM-000002",
                "项目名称": "职工餐厅门套 MR-1 木饰面收边安装（木门窗套）",
                "项目特征": "空间：职工餐厅；材料：MR-1；做法：门套木饰面收边安装；来源：EV-000002",
                "单位": "m2",
                "工程量": "待复核",
                "itemizability_status": "安装项",
                "needs_manual_review": True,
                "evidence_refs": ["EV-000002"],
            },
        ],
        "filtered_items": [
            {
                "item_id": "CODPDF-FILTER-000001",
                "name": "职工餐厅餐桌布置",
                "itemizability_status": "非施工项",
                "filter_reason": "识别为活动家具摆放，不进入施工清单",
                "evidence_refs": ["EV-000003"],
            }
        ],
        "evidence_index": [
            {
                "evidence_id": "EV-000001",
                "source_file": source_file_rows[0].get("file_name", "source.pdf"),
                "page": 1,
                "view_id": "p001_view008",
                "view_type": "elevation",
                "evidence_type": "visible_text",
                "text": "注：墙面墙砖作美缝处理",
                "confidence": 0.8,
            },
            {
                "evidence_id": "EV-000002",
                "source_file": source_file_rows[0].get("file_name", "source.pdf"),
                "page": 1,
                "view_id": "p001_view008",
                "view_type": "elevation",
                "evidence_type": "object",
                "text": "门套 MR-1 木饰面收边",
                "confidence": 0.7,
            },
            {
                "evidence_id": "EV-000003",
                "source_file": source_file_rows[0].get("file_name", "source.pdf"),
                "page": 1,
                "view_id": "p001_view001",
                "view_type": "plan",
                "evidence_type": "object",
                "text": "餐桌布置",
                "confidence": 0.65,
            },
        ],
        "standard_mapping_rows": [],
        "issues": [],
        "metrics": {},
    }

    if sample == "missing-field":
        result["quantity_list_rows"][0]["单位"] = ""
    elif sample == "non-construction":
        result["quantity_list_rows"][1]["itemizability_status"] = "非施工项"
    elif sample == "missing-evidence":
        result["quantity_list_rows"][1]["evidence_refs"] = ["EV-NOT-FOUND"]
    return result
