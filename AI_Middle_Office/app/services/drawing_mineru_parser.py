from __future__ import annotations

import csv
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PHASE = "BIZ-2x-mineru-raw-parse"
SCHEMA_VERSION = "mineru_raw_parse_v1"

MineruCommandRunner = Callable[[Sequence[str], Path, int], Mapping[str, Any]]


def build_mineru_raw_parse_report(
    *,
    pdf_path: str | Path,
    output_dir: str | Path,
    command_template: str | None = None,
    timeout_seconds: int = 1800,
    command_runner: MineruCommandRunner | None = None,
) -> dict[str, Any]:
    """Run MinerU and preserve its raw Markdown/JSON outputs.

    This layer intentionally does not clean, reorder, summarize, or semantically
    interpret MinerU output. It only invokes MinerU, collects raw artifacts, and
    writes stable files that later stages can consume and compare.
    """

    source_pdf = Path(pdf_path)
    directory = Path(output_dir)
    raw_dir = directory / "mineru_raw_output"
    outputs_dir = directory / "outputs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    command = _build_command(
        pdf_path=source_pdf,
        mineru_output_dir=raw_dir,
        command_template=command_template,
        warnings=warnings,
    )
    command_result: dict[str, Any] = {
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "duration_ms": None,
    }
    if not source_pdf.exists() or not source_pdf.is_file():
        errors.append({"code": "MINERU_SOURCE_PDF_MISSING", "message": "Source PDF does not exist.", "pdf_path": str(source_pdf)})
        status = "failed"
    elif command is None:
        warnings.append(
            {
                "code": "MINERU_COMMAND_UNAVAILABLE",
                "message": "MinerU CLI was not found. Install MinerU or pass a command template.",
            }
        )
        status = "unavailable"
    else:
        command_result = _run_command(
            command,
            cwd=directory,
            timeout_seconds=max(1, int(timeout_seconds or 1)),
            command_runner=command_runner,
        )
        if command_result.get("returncode") == 0:
            status = "completed"
        else:
            status = "failed"
            errors.append(
                {
                    "code": "MINERU_COMMAND_FAILED",
                    "message": "MinerU command returned a non-zero exit code.",
                    "returncode": command_result.get("returncode"),
                    "stderr": _limit_text(command_result.get("stderr"), 2000),
                }
            )

    raw_files = _collect_raw_files(raw_dir)
    selected_markdown = _select_primary_file(raw_files, suffix=".md")
    selected_json = _select_primary_file(raw_files, suffix=".json")

    mineru_raw_md_path = outputs_dir / "mineru_raw.md"
    mineru_raw_structure_path = outputs_dir / "mineru_raw_structure.json"
    raw_files_manifest_path = outputs_dir / "mineru_raw_files_manifest.json"
    summary_path = outputs_dir / "mineru_summary.json"
    report_path = outputs_dir / "mineru_parse_report.json"
    csv_manifest_path = outputs_dir / "mineru_raw_files_manifest.csv"

    _write_primary_markdown(mineru_raw_md_path, selected_markdown)
    _write_raw_structure_json(mineru_raw_structure_path, selected_json=selected_json, raw_files=raw_files, raw_dir=raw_dir)
    _write_raw_manifest_json(raw_files_manifest_path, raw_files=raw_files, raw_dir=raw_dir)
    _write_raw_manifest_csv(csv_manifest_path, raw_files=raw_files, raw_dir=raw_dir)

    if status == "failed" and (selected_markdown or selected_json):
        command_errors = [item for item in errors if item.get("code") == "MINERU_COMMAND_FAILED"]
        errors = [item for item in errors if item.get("code") != "MINERU_COMMAND_FAILED"]
        warnings.append(
            {
                "code": "MINERU_COMMAND_NONZERO_WITH_RAW_ARTIFACTS",
                "message": "MinerU returned a non-zero exit code, but raw Markdown/JSON artifacts were still collected.",
                "command_errors": command_errors,
            }
        )
        status = "completed_with_warnings"
    if status == "completed" and not selected_markdown:
        status = "completed_with_warnings"
        warnings.append({"code": "MINERU_MARKDOWN_NOT_FOUND", "message": "MinerU completed but no Markdown output was found."})
    if status == "completed" and not selected_json:
        status = "completed_with_warnings"
        warnings.append({"code": "MINERU_JSON_NOT_FOUND", "message": "MinerU completed but no JSON output was found."})

    summary = {
        "mineru_raw_parse_status": status,
        "source_pdf": str(source_pdf.resolve()) if source_pdf.exists() else str(source_pdf),
        "mineru_command": list(command or []),
        "returncode": command_result.get("returncode"),
        "markdown_file_count": sum(1 for item in raw_files if item["suffix"] == ".md"),
        "json_file_count": sum(1 for item in raw_files if item["suffix"] == ".json"),
        "raw_file_count": len(raw_files),
        "selected_markdown": _relative_or_empty(selected_markdown, raw_dir),
        "selected_json": _relative_or_empty(selected_json, raw_dir),
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "ok": status not in {"failed"},
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "summary": summary,
        "command_result": command_result,
        "raw_files": raw_files,
        "warnings": warnings,
        "errors": errors,
        "outputs": {
            "mineru_raw_md": str(mineru_raw_md_path.resolve()),
            "mineru_raw_structure_json": str(mineru_raw_structure_path.resolve()),
            "mineru_raw_files_manifest_json": str(raw_files_manifest_path.resolve()),
            "mineru_raw_files_manifest_csv": str(csv_manifest_path.resolve()),
            "mineru_summary_json": str(summary_path.resolve()),
            "mineru_parse_report_json": str(report_path.resolve()),
            "mineru_raw_output_dir": str(raw_dir.resolve()),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _build_command(
    *,
    pdf_path: Path,
    mineru_output_dir: Path,
    command_template: str | None,
    warnings: list[dict[str, Any]],
) -> list[str] | None:
    if command_template:
        formatted = command_template.format(
            pdf=str(pdf_path.resolve()),
            output_dir=str(mineru_output_dir.resolve()),
            pdf_stem=pdf_path.stem,
        )
        return shlex.split(formatted, posix=os.name != "nt")

    executable = shutil.which("mineru")
    if executable is None:
        executable = _bundled_mineru_executable()
    if executable:
        return [executable, "-p", str(pdf_path.resolve()), "-o", str(mineru_output_dir.resolve()), "-b", "pipeline"]

    legacy_executable = shutil.which("magic-pdf")
    if legacy_executable:
        warnings.append(
            {
                "code": "MINERU_LEGACY_MAGIC_PDF_CLI",
                "message": "Using legacy magic-pdf CLI because mineru CLI was not found.",
            }
        )
        return [legacy_executable, "-p", str(pdf_path.resolve()), "-o", str(mineru_output_dir.resolve())]
    return None


def _bundled_mineru_executable() -> str | None:
    if os.getenv("MINERU_DISABLE_BUNDLED", "").strip().lower() in {"1", "true", "yes", "y"}:
        return None
    root = Path(__file__).resolve().parents[2]
    candidate = root / "runtime" / "mineru_env" / "Scripts" / ("mineru.exe" if os.name == "nt" else "mineru")
    if candidate.exists() and candidate.is_file():
        return str(candidate)
    return None


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    command_runner: MineruCommandRunner | None,
) -> dict[str, Any]:
    started = datetime.now()
    if command_runner is not None:
        result = dict(command_runner(command, cwd, timeout_seconds))
    else:
        completed = subprocess.run(  # noqa: S603 - command is either discovered CLI or explicit operator template
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        result = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    return {
        "returncode": result.get("returncode"),
        "stdout": _limit_text(result.get("stdout"), 8000),
        "stderr": _limit_text(result.get("stderr"), 8000),
        "duration_ms": result.get("duration_ms") or duration_ms,
    }


def _collect_raw_files(raw_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return files
    for path in sorted(item for item in raw_dir.rglob("*") if item.is_file()):
        suffix = path.suffix.lower()
        if suffix not in {".md", ".json"}:
            continue
        stat = path.stat()
        files.append(
            {
                "relative_path": path.relative_to(raw_dir).as_posix(),
                "absolute_path": str(path.resolve()),
                "suffix": suffix,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return files


def _select_primary_file(raw_files: Sequence[Mapping[str, Any]], *, suffix: str) -> Path | None:
    candidates = [item for item in raw_files if item.get("suffix") == suffix]
    if not candidates:
        return None
    preferred_keywords = {
        ".md": ("full", "result", "output", "content", "markdown"),
        ".json": ("content_list", "middle", "model", "layout", "result", "output", "content"),
    }.get(suffix, ())
    candidates = sorted(
        candidates,
        key=lambda item: (
            not any(keyword in str(item.get("relative_path") or "").lower() for keyword in preferred_keywords),
            -int(item.get("size_bytes") or 0),
            str(item.get("relative_path") or ""),
        ),
    )
    return Path(str(candidates[0]["absolute_path"]))


def _write_primary_markdown(path: Path, selected_markdown: Path | None) -> None:
    if selected_markdown and selected_markdown.exists():
        path.write_text(selected_markdown.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        return
    path.write_text("", encoding="utf-8")


def _write_raw_structure_json(
    path: Path,
    *,
    selected_json: Path | None,
    raw_files: Sequence[Mapping[str, Any]],
    raw_dir: Path,
) -> None:
    if selected_json and selected_json.exists():
        try:
            parsed = json.loads(selected_json.read_text(encoding="utf-8", errors="replace"))
            path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        except json.JSONDecodeError:
            pass
    bundled = {
        "source": "mineru_raw_json_bundle",
        "raw_json_files": [],
    }
    for item in raw_files:
        if item.get("suffix") != ".json":
            continue
        file_path = raw_dir / str(item.get("relative_path") or "")
        try:
            content: Any = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - raw outputs can vary by MinerU version
            content = {"_raw_read_error": str(exc), "_raw_text": _limit_text(file_path.read_text(encoding="utf-8", errors="replace"), 20000)}
        bundled["raw_json_files"].append(
            {
                "relative_path": item.get("relative_path"),
                "content": content,
            }
        )
    path.write_text(json.dumps(bundled, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_raw_manifest_json(path: Path, *, raw_files: Sequence[Mapping[str, Any]], raw_dir: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "raw_output_dir": str(raw_dir.resolve()),
                "files": list(raw_files),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_raw_manifest_csv(path: Path, *, raw_files: Sequence[Mapping[str, Any]], raw_dir: Path) -> None:
    fieldnames = ["relative_path", "suffix", "size_bytes", "modified_at", "absolute_path"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{key: item.get(key, "") for key in fieldnames} for item in raw_files])


def _relative_or_empty(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _limit_text(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
