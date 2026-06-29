from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.drawing_mineru_parser import build_mineru_raw_parse_report


def test_mineru_raw_parser_collects_raw_markdown_and_json(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def fake_runner(command: Sequence[str], cwd: Path, timeout_seconds: int) -> Mapping[str, Any]:
        output_dir = Path(command[command.index("-o") + 1])
        nested = output_dir / "sample" / "auto"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "sample.md").write_text("# 设计说明\n\n| 代号 | 材料 |\n|---|---|\n| CT-04 | 白色墙面砖 |\n", encoding="utf-8")
        (nested / "sample_content_list.json").write_text(
            json.dumps({"pages": [{"page": 1, "blocks": [{"type": "table", "text": "CT-04 白色墙面砖"}]}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    report = build_mineru_raw_parse_report(
        pdf_path=pdf_path,
        output_dir=tmp_path / "mineru",
        command_template="mineru -p {pdf} -o {output_dir}",
        command_runner=fake_runner,
    )

    assert report["status"] == "completed"
    assert report["summary"]["markdown_file_count"] == 1
    assert report["summary"]["json_file_count"] == 1
    assert Path(report["outputs"]["mineru_raw_md"]).read_text(encoding="utf-8").startswith("# 设计说明")
    raw_structure = json.loads(Path(report["outputs"]["mineru_raw_structure_json"]).read_text(encoding="utf-8"))
    assert raw_structure["pages"][0]["blocks"][0]["type"] == "table"
    for path in report["outputs"].values():
        assert Path(path).exists()


def test_mineru_raw_parser_unavailable_writes_empty_outputs(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("MINERU_DISABLE_BUNDLED", "1")

    report = build_mineru_raw_parse_report(pdf_path=pdf_path, output_dir=tmp_path / "mineru")

    assert report["status"] in {"unavailable", "completed_with_warnings", "completed"}
    assert Path(report["outputs"]["mineru_raw_md"]).exists()
    assert Path(report["outputs"]["mineru_raw_structure_json"]).exists()
    assert Path(report["outputs"]["mineru_parse_report_json"]).exists()
    if report["status"] == "unavailable":
        assert report["warnings"][0]["code"] == "MINERU_COMMAND_UNAVAILABLE"
