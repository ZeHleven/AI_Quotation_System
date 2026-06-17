from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.dwg_preview_probe import DwgPreviewProbeError, collect_dwg_files


DEFAULT_ODA_OUTPUT_VERSION = "ACAD2018"
DEFAULT_ODA_OUTPUT_TYPE = "DXF"


class DwgOdaConversionError(ValueError):
    pass


@dataclass(frozen=True)
class DwgOdaConversionResult:
    status: str
    source_dir: str
    output_dir: str
    oda_executable: str
    output_version: str
    output_type: str
    recursive: bool
    audit: bool
    exit_code: int
    input_count: int
    output_count: int
    output_files: tuple[str, ...]
    command: tuple[str, ...]
    started_at: str
    finished_at: str
    stdout: str
    stderr: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "oda_executable": self.oda_executable,
            "output_version": self.output_version,
            "output_type": self.output_type,
            "recursive": self.recursive,
            "audit": self.audit,
            "exit_code": self.exit_code,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "output_files": list(self.output_files),
            "command": list(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
        }


def convert_dwg_directory_to_dxf_with_oda(
    source_dir: str | Path,
    output_dir: str | Path,
    oda_executable: str | Path,
    *,
    output_version: str = DEFAULT_ODA_OUTPUT_VERSION,
    recursive: bool = False,
    audit: bool = True,
    timeout_seconds: int = 300,
) -> DwgOdaConversionResult:
    source_path = Path(source_dir)
    output_path = Path(output_dir)
    executable_path = Path(oda_executable)
    if not source_path.exists() or not source_path.is_dir():
        raise DwgOdaConversionError(f"source DWG directory not found: {source_path}")
    if not executable_path.exists() or not executable_path.is_file():
        raise DwgOdaConversionError(f"ODA executable not found: {executable_path}")

    try:
        input_files = collect_dwg_files(source_path)
    except DwgPreviewProbeError as exc:
        raise DwgOdaConversionError(str(exc)) from exc
    if not input_files:
        raise DwgOdaConversionError(f"source DWG directory has no .dwg files: {source_path}")

    output_path.mkdir(parents=True, exist_ok=True)
    command = build_oda_conversion_command(
        executable_path,
        source_path.resolve(),
        output_path.resolve(),
        output_version=output_version,
        output_type=DEFAULT_ODA_OUTPUT_TYPE,
        recursive=recursive,
        audit=audit,
    )
    started_at = _now()
    completed = subprocess.run(
        command,
        cwd=str(executable_path.parent),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    finished_at = _now()
    output_files = tuple(str(path.resolve()) for path in sorted(output_path.rglob("*.dxf")))
    status = _conversion_status(completed.returncode, len(input_files), len(output_files))

    return DwgOdaConversionResult(
        status=status,
        source_dir=str(source_path.resolve()),
        output_dir=str(output_path.resolve()),
        oda_executable=str(executable_path.resolve()),
        output_version=output_version,
        output_type=DEFAULT_ODA_OUTPUT_TYPE,
        recursive=recursive,
        audit=audit,
        exit_code=completed.returncode,
        input_count=len(input_files),
        output_count=len(output_files),
        output_files=output_files,
        command=tuple(command),
        started_at=started_at,
        finished_at=finished_at,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        message=_conversion_message(status),
    )


def build_oda_conversion_command(
    oda_executable: str | Path,
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    output_version: str,
    output_type: str,
    recursive: bool,
    audit: bool,
) -> list[str]:
    return [
        str(Path(oda_executable)),
        str(Path(source_dir)),
        str(Path(output_dir)),
        output_version,
        output_type,
        "1" if recursive else "0",
        "1" if audit else "0",
    ]


def build_oda_conversion_markdown(result: DwgOdaConversionResult) -> str:
    lines = [
        "# BIZ-2x-2 ODA DWG 转 DXF 结果",
        "",
        f"- 状态：`{result.status}`",
        f"- 输入 DWG 数：{result.input_count}",
        f"- 输出 DXF 数：{result.output_count}",
        f"- 输出目录：`{result.output_dir}`",
        f"- ODA 可执行文件：`{result.oda_executable}`",
        f"- 输出版本：`{result.output_version}`",
        f"- 审计：{'是' if result.audit else '否'}",
        f"- 说明：{result.message}",
        "",
        "## 输出文件",
        "",
    ]
    if result.output_files:
        for path in result.output_files:
            lines.append(f"- `{path}`")
    else:
        lines.append("- 无")
    lines.append("")
    if result.stderr:
        lines.extend(["## stderr", "", "```", result.stderr, "```", ""])
    if result.stdout:
        lines.extend(["## stdout", "", "```", result.stdout, "```", ""])
    return "\n".join(lines)


def write_oda_conversion_outputs(
    result: DwgOdaConversionResult,
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x2_ODA_DWG转DXF结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    json_path.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_oda_conversion_markdown(result), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def _conversion_status(exit_code: int, input_count: int, output_count: int) -> str:
    if exit_code != 0:
        return "failed"
    if output_count >= input_count:
        return "converted"
    if output_count > 0:
        return "partial_converted"
    return "no_outputs"


def _conversion_message(status: str) -> str:
    if status == "converted":
        return "DWG 已成功批量转换为 DXF。"
    if status == "partial_converted":
        return "仅部分 DWG 转换出 DXF，需要检查 ODA 日志或源文件。"
    if status == "no_outputs":
        return "ODA 返回成功但未发现 DXF 输出，需要确认命令参数或输出目录。"
    return "ODA 转换失败，需要查看 stderr/stdout 或改用 PDF/DXF 过渡输入。"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
