from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DWG_VERSION_LABELS = {
    "AC1009": "AutoCAD R12 DWG",
    "AC1012": "AutoCAD R13 DWG",
    "AC1014": "AutoCAD R14 DWG",
    "AC1015": "AutoCAD 2000/2000i/2002 DWG",
    "AC1018": "AutoCAD 2004/2005/2006 DWG",
    "AC1021": "AutoCAD 2007/2008/2009 DWG",
    "AC1024": "AutoCAD 2010/2011/2012 DWG",
    "AC1027": "AutoCAD 2013/2014/2015/2016/2017 DWG",
    "AC1032": "AutoCAD 2018+ DWG",
}


CONVERTER_DEFINITIONS = [
    {
        "tool_id": "oda_file_converter",
        "label": "ODA File Converter",
        "executables": ("ODAFileConverter.exe", "ODAFileConverter"),
        "capability": "dwg_to_dxf",
        "output_targets": ("dxf",),
        "notes": "适合批量把 DWG 转成 DXF；首版可作为 CAD 结构化中间文件来源。",
        "install_hint": "安装 ODA File Converter，并将 ODAFileConverter.exe 加入 PATH，或配置转换服务的可执行路径。",
        "common_globs": (
            r"C:\Program Files\ODA*\**\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA*\**\ODAFileConverter.exe",
        ),
    },
    {
        "tool_id": "autocad_accoreconsole",
        "label": "AutoCAD Core Console",
        "executables": ("accoreconsole.exe", "accoreconsole"),
        "capability": "dwg_to_pdf_or_dxf",
        "output_targets": ("pdf", "dxf"),
        "notes": "适合用脚本批量打开 DWG 并输出 PDF/DXF；需要正式 AutoCAD 环境。",
        "install_hint": "在转换节点安装 AutoCAD，并确认 accoreconsole.exe 可被服务账号执行。",
        "common_globs": (
            r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
            r"C:\Program Files\Autodesk\AutoCAD*\accoreconsole.exe",
        ),
    },
    {
        "tool_id": "libredwg_dwgread",
        "label": "LibreDWG dwgread",
        "executables": ("dwgread.exe", "dwgread"),
        "capability": "dwg_metadata_or_text_probe",
        "output_targets": ("text", "json"),
        "notes": "可用于 DWG 元数据/文本探测；对专有 DWG 版本兼容性需要实测。",
        "install_hint": "安装 LibreDWG 工具集，并将 dwgread 加入 PATH。",
        "common_globs": (
            r"C:\Program Files\LibreDWG*\**\dwgread.exe",
            r"C:\Program Files (x86)\LibreDWG*\**\dwgread.exe",
        ),
    },
    {
        "tool_id": "libredwg_dwg2dxf",
        "label": "LibreDWG dwg2dxf",
        "executables": ("dwg2dxf.exe", "dwg2dxf"),
        "capability": "dwg_to_dxf",
        "output_targets": ("dxf",),
        "notes": "可作为 DWG 转 DXF 的开源候选；对商业图纸兼容性需要样例验证。",
        "install_hint": "安装 LibreDWG 工具集，并将 dwg2dxf 加入 PATH。",
        "common_globs": (
            r"C:\Program Files\LibreDWG*\**\dwg2dxf.exe",
            r"C:\Program Files (x86)\LibreDWG*\**\dwg2dxf.exe",
        ),
    },
]


class DwgPreviewProbeError(ValueError):
    pass


@dataclass(frozen=True)
class DwgFileProbe:
    path: str
    file_name: str
    suffix: str
    size_bytes: int
    modified_at: str
    header_signature: str
    format_label: str
    is_dwg_like: bool
    sha256: str
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_name": self.file_name,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "header_signature": self.header_signature,
            "format_label": self.format_label,
            "is_dwg_like": self.is_dwg_like,
            "sha256": self.sha256,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ConverterToolProbe:
    tool_id: str
    label: str
    available: bool
    executable: str
    capability: str
    output_targets: tuple[str, ...]
    notes: str
    install_hint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "label": self.label,
            "available": self.available,
            "executable": self.executable,
            "capability": self.capability,
            "output_targets": list(self.output_targets),
            "notes": self.notes,
            "install_hint": self.install_hint,
        }


def collect_dwg_files(dwg_dir: str | Path | None = None, dwg_files: Iterable[str | Path] | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    if dwg_dir:
        directory = Path(dwg_dir)
        if not directory.exists():
            raise DwgPreviewProbeError(f"DWG directory not found: {directory}")
        if not directory.is_dir():
            raise DwgPreviewProbeError(f"DWG directory is not a directory: {directory}")
        for path in [*sorted(directory.glob("*.dwg")), *sorted(directory.glob("*.DWG"))]:
            _append_unique_path(paths, seen, path)
    for raw_path in dwg_files or []:
        _append_unique_path(paths, seen, Path(raw_path))
    return paths


def probe_dwg_file(path: str | Path) -> DwgFileProbe:
    file_path = Path(path)
    if not file_path.exists():
        raise DwgPreviewProbeError(f"DWG file not found: {file_path}")
    if not file_path.is_file():
        raise DwgPreviewProbeError(f"DWG path is not a file: {file_path}")

    with file_path.open("rb") as handle:
        header_bytes = handle.read(6)
    signature = header_bytes.decode("ascii", errors="replace")
    stat = file_path.stat()
    suffix = file_path.suffix.lower()
    warnings: list[str] = []
    is_dwg_like = signature.startswith("AC")
    if suffix != ".dwg":
        warnings.append("文件扩展名不是 .dwg")
    if not is_dwg_like:
        warnings.append("文件头不是常见 DWG ACxxxx 标识，需人工确认文件类型")
    if signature.startswith("AC") and signature not in DWG_VERSION_LABELS:
        warnings.append("DWG 版本标识未在内置映射中，需要转换器实测兼容性")

    return DwgFileProbe(
        path=str(file_path.resolve()),
        file_name=file_path.name,
        suffix=suffix,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        header_signature=signature,
        format_label=DWG_VERSION_LABELS.get(signature, "Unknown DWG version" if is_dwg_like else "Not a recognized DWG"),
        is_dwg_like=is_dwg_like,
        sha256=_sha256_file(file_path),
        warnings=tuple(warnings),
    )


def probe_converter_tools(
    extra_search_paths: Iterable[str | Path] | None = None,
    *,
    include_system_tools: bool = True,
) -> list[ConverterToolProbe]:
    probes: list[ConverterToolProbe] = []
    for definition in CONVERTER_DEFINITIONS:
        executable = _find_converter_executable(definition, extra_search_paths, include_system_tools=include_system_tools)
        probes.append(
            ConverterToolProbe(
                tool_id=str(definition["tool_id"]),
                label=str(definition["label"]),
                available=bool(executable),
                executable=executable or "",
                capability=str(definition["capability"]),
                output_targets=tuple(str(item) for item in definition["output_targets"]),
                notes=str(definition["notes"]),
                install_hint=str(definition["install_hint"]),
            )
        )
    return probes


def build_dwg_preview_probe_report(
    dwg_paths: Iterable[str | Path],
    *,
    extra_search_paths: Iterable[str | Path] | None = None,
    include_system_tools: bool = True,
) -> dict[str, Any]:
    file_probes = [probe_dwg_file(path) for path in dwg_paths]
    tool_probes = probe_converter_tools(extra_search_paths, include_system_tools=include_system_tools)
    available_tools = [tool for tool in tool_probes if tool.available]
    dwg_like_count = sum(1 for item in file_probes if item.is_dwg_like)
    unsupported_count = len(file_probes) - dwg_like_count

    strategy = _build_conversion_strategy(file_probes, tool_probes)
    return {
        "ok": True,
        "phase": "BIZ-2x-2",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "file_count": len(file_probes),
            "dwg_like_file_count": dwg_like_count,
            "unsupported_file_count": unsupported_count,
            "converter_available_count": len(available_tools),
            "conversion_status": strategy["conversion_status"],
            "preview_status": strategy["preview_status"],
            "recommended_first_step": strategy["recommended_first_step"],
        },
        "files": [item.as_dict() for item in file_probes],
        "converter_tools": [tool.as_dict() for tool in tool_probes],
        "strategy": strategy,
    }


def build_dwg_probe_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BIZ-2x-2 DWG 转换与图纸预览探测报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- DWG 文件数：{summary['file_count']}",
        f"- 可识别 DWG 文件数：{summary['dwg_like_file_count']}",
        f"- 可用转换工具数：{summary['converter_available_count']}",
        f"- 转换状态：`{summary['conversion_status']}`",
        f"- 预览状态：`{summary['preview_status']}`",
        f"- 推荐下一步：{summary['recommended_first_step']}",
        "",
        "## 样例 DWG 文件",
        "",
        "| 文件 | 大小 | DWG 标识 | 格式判断 | 风险提示 |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for item in report["files"]:
        warnings = "；".join(item["warnings"]) if item["warnings"] else "-"
        lines.append(
            f"| {item['file_name']} | {item['size_bytes']} | `{item['header_signature']}` | {item['format_label']} | {warnings} |"
        )

    lines.extend(
        [
            "",
            "## 转换工具探测",
            "",
            "| 工具 | 是否可用 | 能力 | 可执行文件 | 处理建议 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for tool in report["converter_tools"]:
        available = "是" if tool["available"] else "否"
        executable = tool["executable"] or "-"
        hint = tool["notes"] if tool["available"] else tool["install_hint"]
        lines.append(f"| {tool['label']} | {available} | {tool['capability']} | {executable} | {hint} |")

    strategy = report["strategy"]
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 当前结论：{strategy['business_conclusion']}",
            f"- 阻断原因：{strategy['blocker'] or '-'}",
            "- 后续动作：",
        ]
    )
    for action in strategy["next_actions"]:
        lines.append(f"  - {action}")
    lines.append("")
    return "\n".join(lines)


def build_dwg_probe_csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report["files"]:
        rows.append(
            {
                "文件名": item["file_name"],
                "文件路径": item["path"],
                "文件大小": item["size_bytes"],
                "修改时间": item["modified_at"],
                "DWG版本标识": item["header_signature"],
                "格式判断": item["format_label"],
                "是否像DWG": "是" if item["is_dwg_like"] else "否",
                "风险提示": "；".join(item["warnings"]),
                "SHA256": item["sha256"],
            }
        )
    return rows


def write_dwg_probe_outputs(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ2x2_DWG转换预览探测报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_dwg_probe_markdown(report), encoding="utf-8")

    rows = build_dwg_probe_csv_rows(report)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "csv": str(csv_path),
    }


def _build_conversion_strategy(
    file_probes: list[DwgFileProbe],
    tool_probes: list[ConverterToolProbe],
) -> dict[str, Any]:
    available_by_id = {tool.tool_id: tool for tool in tool_probes if tool.available}
    if not file_probes:
        return {
            "conversion_status": "blocked_no_dwg_files",
            "preview_status": "not_started",
            "recommended_first_step": "上传至少一个 DWG 文件后再执行探测。",
            "business_conclusion": "未发现可处理的 DWG 文件。",
            "blocker": "没有输入 DWG 文件",
            "next_actions": ["补充 DWG 文件后重新运行探测。"],
        }

    if any(not item.is_dwg_like for item in file_probes):
        return {
            "conversion_status": "blocked_invalid_dwg_file",
            "preview_status": "not_started",
            "recommended_first_step": "先确认上传文件是否为真实 DWG。",
            "business_conclusion": "存在文件头不符合 DWG 标识的文件，不能进入自动转换。",
            "blocker": "存在非 DWG 或损坏文件",
            "next_actions": ["要求用户重新上传原始 DWG，或提供 PDF/DXF 过渡文件。"],
        }

    if "autocad_accoreconsole" in available_by_id:
        return {
            "conversion_status": "ready_with_autocad_core_console",
            "preview_status": "ready_for_pdf_preview_script",
            "recommended_first_step": "编写 accoreconsole 批处理脚本，将样例 DWG 输出为 PDF/PNG 预览。",
            "business_conclusion": "当前机器具备 AutoCAD Core Console，可继续做真实预览转换脚本验证。",
            "blocker": "",
            "next_actions": [
                "新增 AutoCAD 脚本模板，固定输出 PDF 到任务目录。",
                "再接 PDF 转 PNG 或前端 PDF 预览。",
                "记录每个 layout 的输出文件和转换日志。",
            ],
        }

    if "oda_file_converter" in available_by_id or "libredwg_dwg2dxf" in available_by_id:
        tool = available_by_id.get("oda_file_converter") or available_by_id["libredwg_dwg2dxf"]
        return {
            "conversion_status": f"ready_for_dxf_conversion_with_{tool.tool_id}",
            "preview_status": "needs_dxf_preview_renderer",
            "recommended_first_step": "先将样例 DWG 批量转换为 DXF，再补充 DXF 到 PDF/PNG 的预览渲染方案。",
            "business_conclusion": "当前机器具备 DWG 转 DXF 条件，但还需要预览渲染器才能给业务员看图。",
            "blocker": "缺少 DXF/PDF/PNG 预览渲染链路",
            "next_actions": [
                "编写 DWG 转 DXF 批处理脚本并保存转换日志。",
                "选择 DXF 渲染方案，或要求首版同时上传 PDF 图纸作为预览。",
                "确认 DXF 中的文字、图层、布局是否足够支撑后续识图。",
            ],
        }

    return {
        "conversion_status": "blocked_missing_dwg_converter",
        "preview_status": "not_available",
        "recommended_first_step": "在转换节点安装 ODA File Converter 或 AutoCAD Core Console；首版可要求用户同时上传 PDF 图纸作为预览过渡。",
        "business_conclusion": "样例 DWG 文件本身可识别，但当前机器没有可调用的 DWG 转换工具，无法生成真实图纸预览。",
        "blocker": "缺少 DWG 转换器",
        "next_actions": [
            "优先安装 ODA File Converter，用于 DWG -> DXF 批量转换验证。",
            "若已有 AutoCAD 授权，可改用 accoreconsole 输出 PDF 预览。",
            "在转换器安装前，页面首版应提示用户同时上传 PDF/DXF 作为过渡输入。",
        ],
    }


def _find_converter_executable(
    definition: dict[str, Any],
    extra_search_paths: Iterable[str | Path] | None,
    *,
    include_system_tools: bool,
) -> str:
    for directory in extra_search_paths or []:
        base = Path(directory)
        for executable_name in definition["executables"]:
            candidate = base / executable_name
            if candidate.exists() and candidate.is_file():
                return str(candidate.resolve())

    if include_system_tools:
        for executable_name in definition["executables"]:
            found = shutil.which(executable_name)
            if found:
                return str(Path(found).resolve())
        for pattern in definition.get("common_globs", ()):
            for candidate in Path().glob(pattern) if not _is_absolute_windows_glob(pattern) else _glob_windows_absolute(pattern):
                if candidate.exists() and candidate.is_file():
                    return str(candidate.resolve())
    return ""


def _glob_windows_absolute(pattern: str) -> list[Path]:
    try:
        import glob

        return [Path(path) for path in glob.glob(pattern, recursive=True)]
    except OSError:
        return []


def _is_absolute_windows_glob(pattern: str) -> bool:
    return len(pattern) >= 3 and pattern[1:3] == ":\\"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_unique_path(paths: list[Path], seen: set[str], path: Path) -> None:
    key = str(path.resolve()).lower() if path.exists() else str(path).lower()
    if key not in seen:
        seen.add(key)
        paths.append(path)
