from __future__ import annotations

import json

from app.services.dwg_preview_probe import (
    build_dwg_preview_probe_report,
    build_dwg_probe_csv_rows,
    build_dwg_probe_markdown,
    collect_dwg_files,
    probe_dwg_file,
    probe_converter_tools,
    write_dwg_probe_outputs,
)


def _write_fake_dwg(path, signature: bytes = b"AC1018") -> None:
    path.write_bytes(signature + b"\0" * 128)


def test_biz2x2_probe_dwg_file_detects_ac1018(tmp_path):
    dwg_path = tmp_path / "sample.dwg"
    _write_fake_dwg(dwg_path)

    probe = probe_dwg_file(dwg_path)

    assert probe.file_name == "sample.dwg"
    assert probe.header_signature == "AC1018"
    assert probe.format_label == "AutoCAD 2004/2005/2006 DWG"
    assert probe.is_dwg_like is True
    assert probe.warnings == ()
    assert len(probe.sha256) == 64


def test_biz2x2_report_blocks_when_no_converter_is_available(tmp_path):
    dwg_path = tmp_path / "sample.dwg"
    _write_fake_dwg(dwg_path)

    report = build_dwg_preview_probe_report([dwg_path], include_system_tools=False)

    assert report["summary"]["file_count"] == 1
    assert report["summary"]["dwg_like_file_count"] == 1
    assert report["summary"]["converter_available_count"] == 0
    assert report["summary"]["conversion_status"] == "blocked_missing_dwg_converter"
    assert report["summary"]["preview_status"] == "not_available"
    assert "缺少 DWG 转换器" in report["strategy"]["blocker"]


def test_biz2x2_report_detects_oda_from_extra_tool_dir(tmp_path):
    dwg_path = tmp_path / "sample.dwg"
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    _write_fake_dwg(dwg_path)
    (tool_dir / "ODAFileConverter.exe").write_text("", encoding="utf-8")

    tools = probe_converter_tools([tool_dir], include_system_tools=False)
    report = build_dwg_preview_probe_report([dwg_path], extra_search_paths=[tool_dir], include_system_tools=False)

    assert any(tool.tool_id == "oda_file_converter" and tool.available for tool in tools)
    assert report["summary"]["converter_available_count"] == 1
    assert report["summary"]["conversion_status"] == "ready_for_dxf_conversion_with_oda_file_converter"
    assert report["summary"]["preview_status"] == "needs_dxf_preview_renderer"


def test_biz2x2_collects_directory_dwgs_without_duplicates(tmp_path):
    dwg_path = tmp_path / "a.dwg"
    other_path = tmp_path / "b.txt"
    _write_fake_dwg(dwg_path)
    other_path.write_text("not dwg", encoding="utf-8")

    files = collect_dwg_files(tmp_path, [dwg_path])

    assert files == [dwg_path]


def test_biz2x2_writes_probe_outputs(tmp_path):
    dwg_path = tmp_path / "sample.dwg"
    _write_fake_dwg(dwg_path)
    report = build_dwg_preview_probe_report([dwg_path], include_system_tools=False)

    outputs = write_dwg_probe_outputs(report, tmp_path / "outputs", stem="probe")
    markdown = build_dwg_probe_markdown(report)
    rows = build_dwg_probe_csv_rows(report)

    assert set(outputs) == {"json", "markdown", "csv"}
    assert "BIZ-2x-2 DWG 转换与图纸预览探测报告" in markdown
    assert rows[0]["DWG版本标识"] == "AC1018"
    assert json.loads((tmp_path / "outputs" / "probe.json").read_text(encoding="utf-8"))["ok"] is True
