from __future__ import annotations

import asyncio
import json

from app.services import drawing_dynamic_itemization
from app.core.config import settings
from app.services.drawing_dynamic_itemization import (
    build_dynamic_itemization_report,
    build_dynamic_itemization_report_with_llm,
    validate_itemization_decision,
)
from app.services.quantity_standard_index import load_standard_library_index


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _signals() -> list[dict[str, object]]:
    return [
        {
            "signal_id": "SIG-0001",
            "source_kind": "dxf_text",
            "source_file": "03.dxf",
            "source_name": "CT-01 750x1500灰色地砖地面",
            "source_spec_or_method": "750x1500 灰色地砖",
            "evidence_text": "CT-01 750x1500灰色地砖地面",
            "confidence": 0.86,
        },
        {
            "signal_id": "SIG-0002",
            "source_kind": "dxf_block",
            "source_file": "电气.dxf",
            "source_name": "配电箱 AL-01",
            "evidence_text": "配电箱 AL-01",
            "confidence": 0.9,
        },
        {
            "signal_id": "SIG-0003",
            "source_kind": "pdf_visual_note",
            "source_file": "给排水.pdf",
            "source_name": "地漏供货及安装",
            "evidence_text": "卫生间地漏供货及安装",
            "confidence": 0.78,
        },
    ]


def test_biz2x_r0_r9_dynamic_itemization_routes_and_selects_standards():
    index = load_standard_library_index()

    report = build_dynamic_itemization_report(_signals(), standard_index=index)
    decisions = {item["signal_id"]: item for item in report["itemization_decisions"]}

    assert report["ok"] is True
    assert report["summary"]["itemization_decision_count"] == 3
    assert decisions["SIG-0001"]["standard_code"] == "GBT50854-2024"
    assert decisions["SIG-0001"]["item_code"] == "011102003"
    assert decisions["SIG-0002"]["standard_code"] == "GBT50856-2024"
    assert decisions["SIG-0002"]["item_code"] == "030402011"
    assert decisions["SIG-0003"]["standard_code"] == "GBT50856-2024"
    assert decisions["SIG-0003"]["item_code"] == "031003014"
    assert decisions["SIG-0003"]["standard_item_name"] == "给、排水附件"
    assert decisions["SIG-0003"]["display_item_name"] == "地漏供货及安装"
    assert decisions["SIG-0003"]["split_required"] is True


def test_biz2x_r0_r9_report_exposes_all_stage_gates_and_confirmation_pack():
    index = load_standard_library_index()

    report = build_dynamic_itemization_report(_signals(), standard_index=index)

    stages = {item["stage"]: item["status"] for item in report["stage_results"]}
    assert set(stages) == {f"R{index}" for index in range(10)}
    assert stages["R0"] == "completed"
    assert stages["R5"] == "completed"
    assert stages["R7"] == "pending_manual"
    assert stages["R8"] == "blocked_until_manual_confirmation"
    assert report["manual_confirmation_pack"]["summary"]["confirmation_row_count"] == 3
    assert report["standard_match_report"]["standard_item_candidates"]
    assert report["quantity_evidence_report"]["quantity_candidates"]
    assert report["feedback_hooks"]


def test_biz2x_r0_r9_rejects_llm_invented_standard_code():
    index = load_standard_library_index()
    llm_decisions = {
        "SIG-0002": {
            "signal_id": "SIG-0002",
            "standard_code": "GBT50856-2024",
            "item_code": "039999999",
            "display_item_name": "AI编造配电箱项目",
            "selected_unit": "台",
            "feature_values": {},
            "quantity": None,
            "quantity_source": "none",
            "confidence": 0.92,
            "reasoning_summary": "invalid test decision",
        }
    }

    report = build_dynamic_itemization_report([_signals()[1]], standard_index=index, llm_decisions=llm_decisions)
    decision = report["itemization_decisions"][0]

    assert decision["hard_validation"]["status"] == "failed"
    assert "item_code_not_found_in_standard_library" in decision["hard_validation"]["issues"]
    assert decision["needs_human_review"] is True
    assert report["stage_results"][5]["stage"] == "R5"
    assert report["stage_results"][5]["status"] == "blocked"


def test_biz2x_r0_r9_rejects_llm_quantity_guess():
    index = load_standard_library_index()
    decision = {
        "standard_code": "GBT50856-2024",
        "item_code": "030402011",
        "selected_unit": "台",
        "feature_values": {},
        "quantity": "2",
        "quantity_source": "llm",
    }

    validation = validate_itemization_decision(decision, standard_index=index)

    assert validation["status"] == "failed"
    assert "quantity_source_must_not_be_llm" in validation["issues"]


def test_biz2x_r0_r9_uses_deepseek_json_decision_and_keeps_hard_validation(monkeypatch):
    index = load_standard_library_index()
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            content = json.dumps(
                {
                    "signal_id": "SIG-0002",
                    "standard_code": "GBT50856-2024",
                    "item_code": "030402011",
                    "display_item_name": "LLM selected panel box",
                    "selected_unit": "",
                    "feature_values": {},
                    "quantity": None,
                    "quantity_source": "none",
                    "confidence": 0.91,
                    "reasoning_summary": "selected from supplied GB/T candidates",
                }
            )
            return {"choices": [{"message": {"content": content}}]}

    async def fake_post_json_via_gateway(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    old_provider = _set_flag("agent_llm_provider", "deepseek")
    old_key = _set_flag("deepseek_api_key", "test-key")
    monkeypatch.setattr(drawing_dynamic_itemization, "post_json_via_gateway", fake_post_json_via_gateway)

    try:
        report = asyncio.run(
            build_dynamic_itemization_report_with_llm([_signals()[1]], standard_index=index, trace_id="trace-r0-r9")
        )
    finally:
        _set_flag("agent_llm_provider", old_provider)
        _set_flag("deepseek_api_key", old_key)
    decision = report["itemization_decisions"][0]

    assert captured["endpoint_type"] == "drawing_dynamic_itemization"
    assert captured["trace_id"] == "trace-r0-r9"
    assert report["llm_runtime"]["status_summary"]["success_count"] == 1
    assert decision["decision_source"] == "llm"
    assert decision["standard_code"] == "GBT50856-2024"
    assert decision["item_code"] == "030402011"
    assert decision["display_item_name"] == "LLM selected panel box"
    assert decision["hard_validation"]["status"] == "passed"


def test_biz2x_r0_r9_llm_runtime_falls_back_without_provider(monkeypatch):
    index = load_standard_library_index()

    async def fake_post_json_via_gateway(**kwargs):
        raise AssertionError("LLM gateway should not be called when provider is rule")

    old_provider = _set_flag("agent_llm_provider", "rule")
    monkeypatch.setattr(drawing_dynamic_itemization, "post_json_via_gateway", fake_post_json_via_gateway)

    try:
        report = asyncio.run(build_dynamic_itemization_report_with_llm([_signals()[0]], standard_index=index))
    finally:
        _set_flag("agent_llm_provider", old_provider)

    assert report["llm_runtime"]["provider"] == "rule"
    assert report["llm_runtime"]["status_summary"]["success_count"] == 0
    assert report["llm_runtime"]["status_summary"]["fallback_count"] == 1
    assert report["itemization_decisions"][0]["decision_source"] == "deterministic_fallback"
