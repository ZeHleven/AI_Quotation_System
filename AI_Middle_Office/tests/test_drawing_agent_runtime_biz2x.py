from __future__ import annotations

from pathlib import Path

from app.services.drawing_agent_runtime import (
    create_pdf_agent_run_tracker,
    read_drawing_agent_run_events,
    read_drawing_agent_run_state,
    read_latest_drawing_agent_run_state,
)
from app.services.drawing_pdf_agent_itemizer import run_pdf_agent_itemization


def test_drawing_agent_runtime_writes_state_and_events(tmp_path):
    tracker = create_pdf_agent_run_tracker(
        output_dir=tmp_path,
        run_id="20260623_runtime",
        input_dir=tmp_path / "input",
        provider="dashscope_agent",
    )

    tracker.update("rendering_pdf", progress=8, detail={"pdf_count": 1})
    tracker.complete(status="completed_with_review", summary={"quantity_list_row_count": 2}, outputs={"xlsx": "out.xlsx"})

    state = read_drawing_agent_run_state(tmp_path, "20260623_runtime")
    latest = read_latest_drawing_agent_run_state(tmp_path)
    events = read_drawing_agent_run_events(tmp_path, "20260623_runtime")

    assert state is not None
    assert state["status"] == "completed_with_review"
    assert state["progress"] == 100
    assert state["summary"]["quantity_list_row_count"] == 2
    assert latest and latest["run_id"] == "20260623_runtime"
    assert [event["event_type"] for event in events] == ["created", "stage_update", "completed"]


def test_pdf_agent_run_marks_empty_model_outputs_failed_with_report(monkeypatch, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "drawing.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    view_path = tmp_path / "whole.png"
    view_path.write_bytes(b"fake image")

    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.collect_pdf_files",
        lambda source_dir: [Path(source_dir) / "drawing.pdf"],
    )
    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.build_pdf_basic_parse_report",
        lambda pdf_files: {"pdf_files": [str(path) for path in pdf_files]},
    )
    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.build_pdf_render_report",
        lambda parse_report, page_dir, render_dpi=350: {"render_rows": [{"page": 1, "png_path": str(view_path)}]},
    )
    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.build_pdf_tile_report",
        lambda parse_report, render_report, tile_dir, grid_size=3: {
            "tile_rows": [
                {
                    "tile_id": "p001_whole",
                    "source_file": "drawing.pdf",
                    "page": 1,
                    "tile_type": "whole_page_preview",
                    "image_path": str(view_path),
                    "priority": 100,
                }
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.build_cad_view_frame_report",
        lambda parse_report, render_report, view_dir: {"cad_view_rows": []},
    )
    monkeypatch.setattr(
        "app.services.drawing_pdf_agent_itemizer.augment_tile_report_with_cad_views",
        lambda tile_report, cad_view_report: tile_report,
    )

    report = run_pdf_agent_itemization(
        pdf_dir=pdf_dir,
        output_dir=tmp_path / "out",
        timestamp="20260623_empty",
        evidence_extractor=lambda view_manifest: {"drawing_evidence": []},
        bill_summarizer=lambda merged_evidence: {"bill_items": []},
        standard_search=lambda query, limit=5: [],
    )

    state = read_drawing_agent_run_state(tmp_path / "out", "20260623_empty")
    events = read_drawing_agent_run_events(tmp_path / "out", "20260623_empty")
    issue_codes = {issue.get("code") for issue in report["issues"]}

    assert report["ok"] is False
    assert issue_codes >= {"NO_AGENT_EVIDENCE", "NO_AGENT_BILL_ITEMS"}
    assert state is not None
    assert state["status"] == "failed_with_report"
    assert state["errors"][0]["code"] == "NO_VALID_AGENT_OUTPUT"
    assert report["outputs"]["agent_run_state_json"].endswith("run_state.json")
    assert report["outputs"]["agent_run_events_jsonl"].endswith("events.jsonl")
    assert events[-1]["event_type"] == "failed"
