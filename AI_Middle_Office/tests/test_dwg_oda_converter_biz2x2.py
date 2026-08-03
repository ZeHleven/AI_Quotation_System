from __future__ import annotations

import os

import pytest

from app.services.dwg_oda_converter import (
    DwgOdaConversionError,
    build_oda_conversion_command,
    convert_dwg_directory_to_dxf_with_oda,
)


def _write_fake_dwg(path) -> None:
    path.write_bytes(b"AC1018" + b"\0" * 128)


def _write_fake_oda_converter(path) -> None:
    path.write_text(
        "\n".join(
            [
                "@echo off",
                "set SRC=%~1",
                "set OUT=%~2",
                "if not exist \"%OUT%\" mkdir \"%OUT%\"",
                "for %%f in (\"%SRC%\\*.dwg\") do (",
                "  type nul > \"%OUT%\\%%~nf.dxf\"",
                ")",
                "exit /b 0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_biz2x2_builds_oda_command_in_expected_order(tmp_path):
    command = build_oda_conversion_command(
        tmp_path / "ODAFileConverter.exe",
        tmp_path / "dwg",
        tmp_path / "dxf",
        output_version="ACAD2018",
        output_type="DXF",
        recursive=False,
        audit=True,
    )

    assert command[-4:] == ["ACAD2018", "DXF", "0", "1"]


@pytest.mark.skipif(os.name != "nt", reason="fake converter uses Windows batch syntax")
def test_biz2x2_converts_dwgs_with_fake_oda_converter(tmp_path):
    source_dir = tmp_path / "dwg"
    output_dir = tmp_path / "dxf"
    source_dir.mkdir()
    _write_fake_dwg(source_dir / "a.dwg")
    _write_fake_dwg(source_dir / "b.dwg")
    fake_converter = tmp_path / "fake_oda.cmd"
    _write_fake_oda_converter(fake_converter)

    result = convert_dwg_directory_to_dxf_with_oda(source_dir, output_dir, fake_converter)

    assert result.status == "converted"
    assert result.input_count == 2
    assert result.output_count == 2
    assert (output_dir / "a.dxf").exists()
    assert (output_dir / "b.dxf").exists()


def test_biz2x2_oda_conversion_requires_input_dwgs(tmp_path):
    source_dir = tmp_path / "empty"
    source_dir.mkdir()
    fake_converter = tmp_path / "fake_oda.cmd"
    _write_fake_oda_converter(fake_converter)

    with pytest.raises(DwgOdaConversionError, match="no .dwg files"):
        convert_dwg_directory_to_dxf_with_oda(source_dir, tmp_path / "dxf", fake_converter)
