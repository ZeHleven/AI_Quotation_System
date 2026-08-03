from __future__ import annotations

import csv
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.core.config import settings
from app.services.model_gateway import call_glm_drawing_tile_extract


PHASE = "BIZ-2x-PDF-visual-evidence-fusion"

PDF_PAGE_HEADERS = [
    "PDF文件",
    "页码",
    "宽度pt",
    "高度pt",
    "旋转角度",
    "文字长度",
    "是否需要视觉识别",
]
PDF_TEXT_HEADERS = ["证据编号", "PDF文件", "页码", "类型", "文本", "置信度"]
PDF_RENDER_HEADERS = ["PDF文件", "页码", "DPI", "PNG路径", "状态", "宽度px", "高度px", "说明"]
PDF_TILE_HEADERS = ["tile_id", "PDF文件", "页码", "类型", "bbox_pdf", "bbox_pixel", "PNG路径", "状态", "优先级"]
PDF_EVIDENCE_HEADERS = [
    "证据编号",
    "来源",
    "PDF文件",
    "页码",
    "tile_id",
    "识别pass",
    "角色",
    "专业",
    "清单项目提示",
    "空间/部位",
    "材料编号",
    "规格/做法",
    "建议单位",
    "文本",
    "置信度",
    "需人工复核",
]
PDF_MATCH_HEADERS = ["匹配项", "分数", "状态", "说明"]
PDF_FUSION_HEADERS = ["融合编号", "类型", "DXF证据", "PDF证据", "置信度", "状态"]

MATERIAL_CODE_RE = re.compile(r"\b[A-Z]{1,5}[-－]?\d{1,4}[A-Z]?\b", re.IGNORECASE)
ELECTRICAL_SPEC_RE = re.compile(r"\b(?:WDZC|WDZN|NH|BV|BYJ|YJV|SC|MT|JDG)[A-Z0-9\-*xX.]*\s*\d*", re.IGNORECASE)
PLUMBING_SPEC_RE = re.compile(r"\b(?:DN|De)\s*\d+\b", re.IGNORECASE)
ROOM_KEYWORDS = ("室", "房", "厅", "间", "走廊", "卫生间", "洗手间", "餐厅", "办公室", "会议室", "厨房")
LEGEND_KEYWORDS = ("材料表", "材料说明", "图例", "材料名称", "主材", "做法表")
TITLE_KEYWORDS = ("项目名称", "工程名称", "图名", "图纸名称", "Drawing title", "Project Name")
DRAWING_CODE_KEYWORDS = ("图号", "编号", "Drawing No", "DWG")
NOTE_KEYWORDS = ("说明", "做法", "节点", "详图", "大样")
ELECTRICAL_KEYWORDS = ("配电", "配管", "配线", "电缆", "电线", "灯", "插座", "开关", "桥架", "弱电", "筒灯", "灯带")
PLUMBING_KEYWORDS = ("给水", "排水", "地漏", "阀", "水表", "洁具", "马桶", "台盆", "龙头", "管道")
DOOR_WINDOW_KEYWORDS = ("门窗", "门编号", "窗编号", "防火门", "玻璃门", "铝合金窗")


class PdfEvidencePipelineError(ValueError):
    pass


def run_pdf_evidence_pipeline(
    *,
    pdf_dir: str | Path,
    output_dir: str | Path,
    timestamp: str | None = None,
    dxf_context: Mapping[str, Any] | None = None,
    render_dpi: int = 400,
    tile_grid_size: int = 3,
    enable_llm_visual: bool | None = None,
    max_visual_tiles: int | None = None,
    vision_passes: Sequence[str] | None = None,
) -> dict[str, Any]:
    run_timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    source_dir = Path(pdf_dir)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_files = collect_pdf_files(source_dir)
    if not pdf_files:
        raise PdfEvidencePipelineError("没有找到可识别的 .pdf 文件")

    parse_report = build_pdf_basic_parse_report(pdf_files)
    render_report = build_pdf_render_report(
        parse_report,
        target_dir / f"pdf_pages_{run_timestamp}",
        render_dpi=render_dpi,
    )
    tile_report = build_pdf_tile_report(
        parse_report=parse_report,
        render_report=render_report,
        tile_dir=target_dir / f"pdf_tiles_{run_timestamp}",
        grid_size=tile_grid_size,
    )
    evidence_report = build_pdf_visual_evidence_report(
        parse_report=parse_report,
        tile_report=tile_report,
        enable_llm_visual=enable_llm_visual,
        max_visual_tiles=max_visual_tiles,
        vision_passes=vision_passes,
        trace_id=f"pdf-tile-{run_timestamp}",
    )
    match_report = build_dwg_pdf_match_report(
        pdf_report=evidence_report,
        dxf_context=dxf_context or {},
        pdf_files=pdf_files,
    )
    fusion_report = build_dxf_pdf_fusion_report(
        pdf_evidence_report=evidence_report,
        match_report=match_report,
        dxf_context=dxf_context or {},
    )

    report = {
        "ok": True,
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": "manual_pdf_upload",
        "summary": build_pdf_pipeline_summary(
            parse_report=parse_report,
            render_report=render_report,
            tile_report=tile_report,
            evidence_report=evidence_report,
            match_report=match_report,
            fusion_report=fusion_report,
        ),
        "parse_report": parse_report,
        "render_report": render_report,
        "tile_report": tile_report,
        "visual_evidence_report": evidence_report,
        "dwg_pdf_match_report": match_report,
        "fusion_report": fusion_report,
        "r0_r9_evidence_source": build_r0_r9_evidence_source(fusion_report, dxf_context or {}),
        "issues": build_pdf_pipeline_issues(render_report, match_report, fusion_report),
    }
    report["outputs"] = write_pdf_evidence_pipeline_outputs(
        report,
        target_dir,
        stem=f"BIZ2x_PDF视觉证据链_{run_timestamp}",
    )
    return report


def collect_pdf_files(source_dir: str | Path) -> list[Path]:
    directory = Path(source_dir)
    seen: set[str] = set()
    files: list[Path] = []
    for path in [*sorted(directory.glob("*.pdf")), *sorted(directory.glob("*.PDF"))]:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        files.append(path)
    return files


def build_pdf_basic_parse_report(pdf_files: Iterable[str | Path]) -> dict[str, Any]:
    page_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    dependency_status = _pdf_dependency_status()
    for pdf_path in [Path(item) for item in pdf_files]:
        content = pdf_path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        parsed = _parse_pdf_with_optional_libraries(pdf_path, content)
        file_rows.append(
            {
                "file_name": pdf_path.name,
                "path": str(pdf_path.resolve()),
                "size_bytes": pdf_path.stat().st_size,
                "sha256": file_hash,
                "page_count": len(parsed["page_rows"]),
                "parse_engine": parsed["parse_engine"],
                "parse_status": parsed["parse_status"],
            }
        )
        page_rows.extend(parsed["page_rows"])
        for row in parsed["text_rows"]:
            row["evidence_id"] = f"PDFTXT-{len(text_rows) + 1:06d}"
            text_rows.append(row)

    text_page_count = len({(row.get("source_file"), row.get("page")) for row in text_rows if row.get("text")})
    scanned_page_count = sum(1 for row in page_rows if row.get("needs_visual_recognition"))
    return {
        "ok": True,
        "phase": "PDF-2-basic-parse",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dependency_status": dependency_status,
        "summary": {
            "pdf_file_count": len(file_rows),
            "page_count": len(page_rows),
            "text_row_count": len(text_rows),
            "text_page_count": text_page_count,
            "scanned_or_visual_page_count": scanned_page_count,
        },
        "file_rows": file_rows,
        "page_rows": page_rows,
        "text_rows": text_rows,
    }


def build_pdf_render_report(
    parse_report: Mapping[str, Any],
    page_dir: str | Path,
    *,
    render_dpi: int = 400,
) -> dict[str, Any]:
    directory = Path(page_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tool = _find_pdftoppm()
    render_rows: list[dict[str, Any]] = []
    if not tool:
        pypdfium_rows = _render_pdf_pages_with_pypdfium2(parse_report, directory, render_dpi=render_dpi)
        if pypdfium_rows:
            return _render_report(pypdfium_rows, render_dpi, tool_path="pypdfium2")
        for page in parse_report.get("page_rows") or []:
            render_rows.append(
                {
                    "source_file": page.get("source_file", ""),
                    "page": page.get("page", 0),
                    "render_dpi": render_dpi,
                    "png_path": "",
                    "status": "render_tool_missing",
                    "image_width_px": "",
                    "image_height_px": "",
                    "message": "未找到 pdftoppm/pypdfium2，无法生成真实高清 PNG",
                }
            )
        return _render_report(render_rows, render_dpi, tool_path="")

    for file_row in parse_report.get("file_rows") or []:
        pdf_path = Path(file_row.get("path") or "")
        if not pdf_path.exists():
            continue
        prefix = directory / _safe_file_stem(pdf_path.stem)
        completed = subprocess.run(
            [tool, "-png", "-r", str(render_dpi), str(pdf_path), str(prefix)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        generated = sorted(directory.glob(f"{prefix.name}-*.png"))
        if completed.returncode != 0 or not generated:
            for page_number in range(1, int(file_row.get("page_count") or 0) + 1):
                render_rows.append(
                    {
                        "source_file": pdf_path.name,
                        "page": page_number,
                        "render_dpi": render_dpi,
                        "png_path": "",
                        "status": "render_failed",
                        "image_width_px": "",
                        "image_height_px": "",
                        "message": (completed.stderr or completed.stdout or "pdftoppm 渲染失败")[:300],
                    }
                )
            continue
        for image_path in generated:
            page_number = _page_number_from_pdftoppm_name(image_path)
            width, height = _image_size(image_path)
            render_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_number,
                    "render_dpi": render_dpi,
                    "png_path": str(image_path.resolve()),
                    "status": "rendered",
                    "image_width_px": width or "",
                    "image_height_px": height or "",
                    "message": "已生成高清 PNG",
                }
            )
    return _render_report(render_rows, render_dpi, tool_path=tool)


def build_pdf_tile_report(
    *,
    parse_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    tile_dir: str | Path,
    grid_size: int = 3,
) -> dict[str, Any]:
    directory = Path(tile_dir)
    directory.mkdir(parents=True, exist_ok=True)
    render_by_page = {
        (str(row.get("source_file") or ""), int(row.get("page") or 0)): row
        for row in render_report.get("render_rows") or []
    }
    rows: list[dict[str, Any]] = []
    for page in parse_report.get("page_rows") or []:
        source_file = str(page.get("source_file") or "")
        page_no = int(page.get("page") or 0)
        width_pt = _float(page.get("width_pt"), 0.0)
        height_pt = _float(page.get("height_pt"), 0.0)
        render = render_by_page.get((source_file, page_no), {})
        raw_image_path = str(render.get("png_path") or "").strip()
        image_path = Path(raw_image_path) if raw_image_path else None
        has_rendered_image = bool(image_path and image_path.exists() and image_path.is_file())
        image_width = int(_float(render.get("image_width_px"), 0))
        image_height = int(_float(render.get("image_height_px"), 0))
        rows.append(
            {
                "tile_id": f"p{page_no:03d}_whole",
                "source_file": source_file,
                "page": page_no,
                "tile_type": "whole_page_preview",
                "bbox_pdf": [0, 0, width_pt, height_pt],
                "bbox_pixel": [0, 0, image_width, image_height],
                "image_path": str(image_path.resolve()) if has_rendered_image and image_path else "",
                "status": "tile_uses_rendered_page" if has_rendered_image else "tile_planned_without_render_image",
                "priority": 100,
            }
        )
        rows.extend(
            _build_grid_tiles(
                source_file=source_file,
                page_no=page_no,
                width_pt=width_pt,
                height_pt=height_pt,
                image_path=image_path if has_rendered_image else None,
                image_width=image_width,
                image_height=image_height,
                tile_dir=directory,
                grid_size=grid_size,
            )
        )
    created_count = sum(1 for row in rows if row.get("image_path") and row.get("tile_type") == "grid")
    return {
        "ok": True,
        "phase": "PDF-4-tile-plan",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "tile_count": len(rows),
            "grid_tile_count": sum(1 for row in rows if row.get("tile_type") == "grid"),
            "rendered_tile_image_count": created_count,
            "whole_page_tile_count": sum(1 for row in rows if row.get("tile_type") == "whole_page_preview"),
        },
        "tile_rows": rows,
    }


def build_pdf_visual_evidence_report(
    *,
    parse_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    enable_llm_visual: bool | None = None,
    max_visual_tiles: int | None = None,
    vision_passes: Sequence[str] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for text_row in parse_report.get("text_rows") or []:
        text = _clean_text(text_row.get("text"))
        if not text:
            continue
        role = classify_pdf_evidence_role(text)
        rows.append(
            {
                "evidence_id": f"PDFEV-{len(rows) + 1:06d}",
                "source_kind": "pdf_embedded_text",
                "source_file": text_row.get("source_file", ""),
                "page": text_row.get("page", ""),
                "tile_id": "",
                "evidence_role": role,
                "discipline": _discipline_from_role_and_text(role, text),
                "item_hint": _item_hint_from_role_and_text(role, text),
                "space": text if role == "room_name" else "",
                "material_codes": extract_material_codes(text),
                "spec_or_method": text if role not in {"room_name", "drawing_title", "drawing_code"} else "",
                "suggested_unit": "",
                "text": text,
                "normalized_text": normalize_text(text),
                "bbox_pdf": text_row.get("bbox_pdf") or [],
                "bbox_pixel": [],
                "confidence": _role_confidence(role, text),
                "model": "pdf_text_extractor",
                "needs_manual_review": role in {"unknown_note", "room_name"},
            }
        )

    tile_rows = list(tile_report.get("tile_rows") or [])
    enabled = settings.feature_pdf_tile_vision if enable_llm_visual is None else bool(enable_llm_visual)
    max_tiles = settings.pdf_tile_vision_max_tiles if max_visual_tiles is None else int(max_visual_tiles or 0)
    llm_status_rows: list[dict[str, Any]] = []
    if enabled and max_tiles > 0:
        llm_rows, llm_status_rows = extract_llm_visual_evidence_from_tiles(
            tile_rows,
            max_tiles=max_tiles,
            vision_passes=vision_passes,
            trace_id=trace_id,
            start_index=len(rows),
        )
        rows.extend(llm_rows)
    role_counts = Counter(row["evidence_role"] for row in rows)
    success_count = sum(1 for row in llm_status_rows if row.get("status") == "success")
    error_count = sum(1 for row in llm_status_rows if row.get("status") == "error")
    skipped_count = sum(1 for row in llm_status_rows if row.get("status") == "skipped")
    return {
        "ok": True,
        "phase": "PDF-5-visual-evidence",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "visual_evidence_count": len(rows),
            "tile_count": len(tile_rows),
            "role_counts": dict(role_counts.most_common()),
            "recognition_mode": "pdf_text_and_glm_tile_visual" if enabled else "pdf_text_first_tile_traceable",
            "llm_visual_status": _llm_visual_status(enabled, success_count, error_count, skipped_count),
            "llm_visual_tile_success_count": success_count,
            "llm_visual_tile_error_count": error_count,
            "llm_visual_tile_skipped_count": skipped_count,
            "llm_visual_tile_limit": max_tiles,
            "llm_visual_passes": _normalize_vision_passes(vision_passes),
        },
        "evidence_rows": rows,
        "tile_rows": tile_rows,
        "llm_status_rows": llm_status_rows,
    }


def extract_llm_visual_evidence_from_tiles(
    tile_rows: list[dict[str, Any]],
    *,
    max_tiles: int,
    vision_passes: Sequence[str] | None = None,
    trace_id: str | None = None,
    start_index: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    passes = _normalize_vision_passes(vision_passes)
    selected_tiles_by_pass = {
        vision_pass: select_tiles_for_llm_visual(tile_rows, max_tiles=max_tiles, prompt_mode=vision_pass)
        for vision_pass in passes
    }
    if not any(selected_tiles_by_pass.values()):
        return [], [
            {"status": "skipped", "reason": "no_rendered_tile_images", "tile_id": "", "vision_pass": vision_pass}
            for vision_pass in passes
        ]
    evidence_rows: list[dict[str, Any]] = []
    status_by_key: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    result_by_key: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    prepared_calls: list[tuple[tuple[str, tuple[str, str]], str, dict[str, Any], Path]] = []
    for vision_pass in passes:
        selected_tiles = selected_tiles_by_pass.get(vision_pass) or []
        if not selected_tiles:
            status_by_key[(vision_pass, ("", ""))] = {
                "status": "skipped",
                "reason": "no_rendered_tile_images_for_pass",
                "tile_id": "",
                "vision_pass": vision_pass,
            }
            continue
        for tile in selected_tiles:
            tile_key = (vision_pass, _tile_identity(tile))
            tile_id = str(tile.get("tile_id") or "")
            raw_image_path = str(tile.get("image_path") or "").strip()
            image_path = Path(raw_image_path) if raw_image_path else None
            if not image_path or not image_path.exists() or not image_path.is_file():
                status_by_key[tile_key] = {
                    "status": "skipped",
                    "reason": "tile_image_missing",
                    "tile_id": tile_id,
                    "vision_pass": vision_pass,
                }
                continue
            prepared_calls.append((tile_key, vision_pass, tile, image_path))

    worker_count = _visual_tile_worker_count(len(prepared_calls))
    if worker_count <= 1:
        for tile_key, vision_pass, tile, image_path in prepared_calls:
            _run_prepared_tile_vision(
                tile_key=tile_key,
                vision_pass=vision_pass,
                tile=tile,
                image_path=image_path,
                trace_id=trace_id,
                result_by_key=result_by_key,
                status_by_key=status_by_key,
            )
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pdf-tile-vision") as executor:
            future_map = {
                executor.submit(
                    _run_tile_vision_call,
                    image_path,
                    tile,
                    trace_id=trace_id,
                    prompt_mode=vision_pass,
                ): (tile_key, vision_pass, tile)
                for tile_key, vision_pass, tile, image_path in prepared_calls
            }
            for future in as_completed(future_map):
                tile_key, vision_pass, tile = future_map[future]
                tile_id = str(tile.get("tile_id") or "")
                try:
                    result_by_key[tile_key] = future.result()
                except Exception as exc:
                    status_by_key[tile_key] = {
                        "status": "error",
                        "reason": str(exc)[:300],
                        "tile_id": tile_id,
                        "vision_pass": vision_pass,
                    }

    status_rows: list[dict[str, Any]] = []
    for vision_pass in passes:
        selected_tiles = selected_tiles_by_pass.get(vision_pass) or []
        if not selected_tiles:
            skipped_key = (vision_pass, ("", ""))
            if skipped_key in status_by_key:
                status_rows.append(status_by_key[skipped_key])
            continue
        for tile in selected_tiles:
            tile_id = str(tile.get("tile_id") or "")
            key = (vision_pass, _tile_identity(tile))
            if key in status_by_key:
                status_rows.append(status_by_key[key])
                continue
            model_result = result_by_key.get(key) or {}
            items = model_result.get("evidence_items") or []
            for item in items:
                text = _clean_text(item.get("text"))
                if not text:
                    continue
                evidence_rows.append(
                    {
                        "evidence_id": f"PDFEV-{start_index + len(evidence_rows) + 1:06d}",
                        "source_kind": "pdf_visual_tile_llm",
                        "source_file": tile.get("source_file", ""),
                        "page": tile.get("page", ""),
                        "tile_id": tile_id,
                        "vision_pass": vision_pass,
                        "evidence_role": _normalize_evidence_role(item.get("evidence_role")),
                        "discipline": _normalize_discipline(item.get("discipline")),
                        "item_hint": _clean_text(item.get("item_hint")),
                        "space": _clean_text(item.get("space")),
                        "material_codes": _normalize_string_list(item.get("material_codes")),
                        "spec_or_method": _clean_text(item.get("spec_or_method")),
                        "suggested_unit": _clean_text(item.get("suggested_unit")),
                        "text": text,
                        "normalized_text": _clean_text(item.get("normalized_text")) or normalize_text(text),
                        "bbox_pdf": tile.get("bbox_pdf") or [],
                        "bbox_pixel": tile.get("bbox_pixel") or [],
                        "confidence": max(0.0, min(1.0, _float(item.get("confidence"), 0.0))),
                        "model": settings.glm_vision_model,
                        "needs_manual_review": bool(item.get("needs_manual_review", True)),
                        "reason": _clean_text(item.get("reason")),
                    }
                )
            status_rows.append(
                {
                    "status": "success",
                    "reason": f"extracted_{len(items)}_items",
                    "tile_id": tile_id,
                    "vision_pass": vision_pass,
                    "raw_content": str(model_result.get("raw_content") or "")[:500],
                }
            )
    return evidence_rows, status_rows


def _run_prepared_tile_vision(
    *,
    tile_key: tuple[str, tuple[str, str]],
    vision_pass: str,
    tile: dict[str, Any],
    image_path: Path,
    trace_id: str | None,
    result_by_key: dict[tuple[str, tuple[str, str]], dict[str, Any]],
    status_by_key: dict[tuple[str, tuple[str, str]], dict[str, Any]],
) -> None:
    tile_id = str(tile.get("tile_id") or "")
    try:
        result_by_key[tile_key] = _run_tile_vision_call(
            image_path,
            tile,
            trace_id=trace_id,
            prompt_mode=vision_pass,
        )
    except Exception as exc:
        status_by_key[tile_key] = {
            "status": "error",
            "reason": str(exc)[:300],
            "tile_id": tile_id,
            "vision_pass": vision_pass,
        }


def _visual_tile_worker_count(task_count: int) -> int:
    if task_count <= 0:
        return 0
    configured = int(getattr(settings, "pdf_tile_vision_concurrency", 1) or 1)
    return max(1, min(configured, task_count))


def select_tiles_for_llm_visual(
    tile_rows: list[dict[str, Any]], *, max_tiles: int, prompt_mode: str = "general"
) -> list[dict[str, Any]]:
    limit = max(max_tiles, 0)
    if limit <= 0:
        return []

    rendered_grid_tiles = _rendered_visual_tiles(tile_rows, tile_type="grid")
    rendered_grid_tiles.sort(key=_visual_tile_sort_key)
    if not _visual_pass_prefers_whole_page(prompt_mode):
        return _balanced_tile_selection(rendered_grid_tiles, max_tiles=limit)

    rendered_whole_page_tiles = _rendered_visual_tiles(tile_rows, tile_type="whole_page_preview")
    rendered_whole_page_tiles.sort(key=_whole_page_tile_sort_key)
    selected = _balanced_tile_selection(rendered_whole_page_tiles, max_tiles=limit)
    remaining = limit - len(selected)
    if remaining <= 0:
        return selected

    selected_identities = {_tile_identity(tile) for tile in selected}
    fallback_grid_tiles = [
        tile for tile in rendered_grid_tiles if _tile_identity(tile) not in selected_identities
    ]
    selected.extend(_balanced_tile_selection(fallback_grid_tiles, max_tiles=remaining))
    return selected


def _visual_pass_prefers_whole_page(prompt_mode: str) -> bool:
    return str(prompt_mode or "").lower() in {
        "finish_schedule",
        "fixture_valve_schedule",
        "door_window_demolition",
        "table_legend",
        "node_detail",
    }


def _rendered_visual_tiles(tile_rows: Iterable[dict[str, Any]], *, tile_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in tile_rows:
        if row.get("tile_type") != tile_type:
            continue
        raw_path = str(row.get("image_path") or "").strip()
        if not raw_path:
            continue
        image_path = Path(raw_path)
        if image_path.exists() and image_path.is_file():
            rows.append(row)
    return rows


def _visual_tile_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -int(_float(row.get("priority"), 0)),
        -_image_file_size(row.get("image_path")),
        str(row.get("source_file") or ""),
        str(row.get("tile_id") or ""),
    )


def _whole_page_tile_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("source_file") or ""),
        int(_float(row.get("page"), 0)),
        -_image_file_size(row.get("image_path")),
        str(row.get("tile_id") or ""),
    )


def _balanced_tile_selection(sorted_tiles: Iterable[dict[str, Any]], *, max_tiles: int) -> list[dict[str, Any]]:
    limit = max(max_tiles, 0)
    if limit <= 0:
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    all_tiles: list[dict[str, Any]] = []
    for tile in sorted_tiles:
        all_tiles.append(tile)
        key = str(tile.get("source_file") or "")
        groups.setdefault(key, []).append(tile)

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    for group in groups.values():
        if group and len(selected) < limit:
            tile = group[0]
            selected.append(tile)
            selected_keys.add(_tile_identity(tile))
    for tile in all_tiles:
        if len(selected) >= limit:
            break
        identity = _tile_identity(tile)
        if identity in selected_keys:
            continue
        selected.append(tile)
        selected_keys.add(identity)
    return selected


def _tile_identity(tile: Mapping[str, Any]) -> tuple[str, str]:
    return (str(tile.get("source_file") or ""), str(tile.get("tile_id") or ""))


def _image_file_size(path: Any) -> int:
    try:
        image_path = Path(str(path or ""))
        if image_path.exists() and image_path.is_file():
            return image_path.stat().st_size
    except (OSError, ValueError):
        return 0
    return 0


def _run_tile_vision_call(
    image_path: Path,
    tile: Mapping[str, Any],
    *,
    trace_id: str | None = None,
    prompt_mode: str = "general",
) -> dict[str, Any]:
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    mime_type = _image_mime_type(image_path)
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    context = {
        "source_file": tile.get("source_file", ""),
        "page": tile.get("page", ""),
        "tile_id": tile.get("tile_id", ""),
        "tile_type": tile.get("tile_type", ""),
        "bbox_pdf": tile.get("bbox_pdf") or [],
        "bbox_pixel": tile.get("bbox_pixel") or [],
    }
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            call_glm_drawing_tile_extract(
                encoded,
                mime_type,
                tile_context=context,
                prompt_mode=prompt_mode,
                trace_id=trace_id,
            )
        )
    raise RuntimeError("running_event_loop_in_sync_pdf_pipeline")


def _normalize_vision_passes(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        raw_values: list[str] = ["general"]
    elif isinstance(values, str):
        raw_values = [item.strip() for item in re.split(r"[,;，；\s]+", values) if item.strip()]
    else:
        raw_values = [str(item).strip() for item in values if str(item).strip()]
    allowed = {
        "general",
        "finish_schedule",
        "electrical_mep",
        "plumbing_fixture",
        "fixture_valve_schedule",
        "demolition_node",
        "door_window_demolition",
        "table_legend",
        "node_detail",
    }
    result: list[str] = []
    for value in raw_values or ["general"]:
        mode = value.lower()
        if mode not in allowed:
            raise PdfEvidencePipelineError(f"未知 PDF 视觉识别 pass: {value}")
        if mode not in result:
            result.append(mode)
    return result or ["general"]


def _llm_visual_status(enabled: bool, success_count: int, error_count: int, skipped_count: int) -> str:
    if not enabled:
        return "disabled"
    if success_count:
        return "success" if not error_count else "partial_success"
    if error_count:
        return "error"
    if skipped_count:
        return "skipped"
    return "no_tiles_selected"


def _normalize_evidence_role(value: Any) -> str:
    allowed = {
        "material_legend",
        "finish_material",
        "construction_method",
        "device_symbol",
        "electrical_spec",
        "plumbing_spec",
        "equipment_schedule",
        "door_window_mark",
        "room_name",
        "drawing_title",
        "drawing_code",
        "arrow_relation",
        "construction_note",
        "unknown_note",
    }
    role = _clean_text(value)
    aliases = {
        "material": "finish_material",
        "finish": "finish_material",
        "method": "construction_method",
        "device": "device_symbol",
        "electrical": "electrical_spec",
        "plumbing": "plumbing_spec",
        "schedule": "equipment_schedule",
        "door_window": "door_window_mark",
    }
    role = aliases.get(role, role)
    return role if role in allowed else "unknown_note"


def _normalize_discipline(value: Any) -> str:
    text = _clean_text(value).lower()
    aliases = {
        "装饰": "decoration",
        "装修": "decoration",
        "建筑装饰": "decoration",
        "电气": "electrical",
        "强电": "electrical",
        "弱电": "electrical",
        "给排水": "plumbing",
        "给水排水": "plumbing",
        "水": "plumbing",
    }
    text = aliases.get(text, text)
    return text if text in {"decoration", "electrical", "plumbing", "unknown"} else "unknown"


def _normalize_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = re.split(r"[,，、;；\s]+", value)
    elif isinstance(value, Iterable):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = _clean_text(item).replace("－", "-")
        if not cleaned:
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def build_dwg_pdf_match_report(
    *,
    pdf_report: Mapping[str, Any],
    dxf_context: Mapping[str, Any],
    pdf_files: Iterable[str | Path] = (),
) -> dict[str, Any]:
    pdf_texts = [str(row.get("text") or "") for row in pdf_report.get("evidence_rows") or []]
    dxf_texts = collect_dxf_context_texts(dxf_context)
    pdf_material_codes = extract_material_codes(" ".join(pdf_texts))
    dxf_material_codes = extract_material_codes(" ".join(dxf_texts))
    material_score = _overlap_score(pdf_material_codes, dxf_material_codes)
    text_score = _token_overlap_score(pdf_texts, dxf_texts)
    filename_score = _filename_score(pdf_files, dxf_context)
    raw_score = material_score * 0.45 + text_score * 0.40 + filename_score * 0.15
    score = round(raw_score, 4)
    if score >= 0.75:
        status = "auto_matched"
    elif score >= 0.55:
        status = "needs_manual_bind"
    else:
        status = "blocked"
    rows = [
        {"match_item": "material_code_overlap", "score": round(material_score, 4), "status": status, "message": "材料编号集合重合度"},
        {"match_item": "text_token_overlap", "score": round(text_score, 4), "status": status, "message": "PDF/DXF 文字 token 重合度"},
        {"match_item": "filename_similarity", "score": round(filename_score, 4), "status": status, "message": "文件名相似度"},
    ]
    return {
        "ok": True,
        "phase": "PDF-6-dwg-pdf-match",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "match_score": score,
            "match_status": status,
            "pdf_material_code_count": len(pdf_material_codes),
            "dxf_material_code_count": len(dxf_material_codes),
            "shared_material_code_count": len(set(pdf_material_codes) & set(dxf_material_codes)),
            "pdf_text_count": len(pdf_texts),
            "dxf_text_count": len(dxf_texts),
        },
        "match_rows": rows,
    }


def build_dxf_pdf_fusion_report(
    *,
    pdf_evidence_report: Mapping[str, Any],
    match_report: Mapping[str, Any],
    dxf_context: Mapping[str, Any],
) -> dict[str, Any]:
    match_status = (match_report.get("summary") or {}).get("match_status")
    if match_status == "blocked":
        return {
            "ok": True,
            "phase": "PDF-7-dxf-pdf-evidence-fusion",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "fusion_status": "blocked_by_dwg_pdf_match",
                "fusion_link_count": 0,
                "project_evidence_signal_count": 0,
            },
            "fusion_rows": [],
            "project_evidence_signals": [],
            "blocked_reasons": ["DWG/PDF 对应关系不足，禁止合并证据"],
        }

    dxf_texts = collect_dxf_context_texts(dxf_context)
    dxf_code_index = _material_code_text_index(dxf_texts)
    fusion_rows: list[dict[str, Any]] = []
    project_signals: list[dict[str, Any]] = []
    for evidence in pdf_evidence_report.get("evidence_rows") or []:
        text = str(evidence.get("text") or "")
        codes = extract_material_codes(text)
        linked_dxf = []
        for code in codes:
            linked_dxf.extend(dxf_code_index.get(code, []))
        if codes and linked_dxf:
            fusion_rows.append(
                {
                    "fusion_id": f"FUS-{len(fusion_rows) + 1:06d}",
                    "fusion_type": "material_code_cross_source",
                    "dxf_evidence": "；".join(linked_dxf[:5]),
                    "pdf_evidence": text,
                    "confidence": min(0.9, float(evidence.get("confidence") or 0.6) + 0.1),
                    "status": "ready_for_itemization",
                }
            )
        if evidence.get("evidence_role") in {"material_legend", "construction_note", "room_name", "drawing_title"}:
            project_signals.append(
                {
                    "signal_id": f"PDFSIG-{len(project_signals) + 1:06d}",
                    "source_kind": "pdf_visual_fused_evidence",
                    "source_kind_label": "PDF视觉证据",
                    "source_file": evidence.get("source_file", ""),
                    "source_row_number": evidence.get("page", ""),
                    "source_name": _signal_name_from_evidence(evidence),
                    "source_spec_or_method": text,
                    "raw_row_text": text,
                    "evidence_text": text,
                    "evidence_source": "pdf_dxf_fusion",
                    "quantity": None,
                    "quantity_unit": "",
                    "quantity_source": "",
                    "confidence": evidence.get("confidence", 0.0),
                }
            )
    return {
        "ok": True,
        "phase": "PDF-7-dxf-pdf-evidence-fusion",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "fusion_status": "ready" if match_status == "auto_matched" else "manual_bind_required",
            "fusion_link_count": len(fusion_rows),
            "project_evidence_signal_count": len(project_signals),
        },
        "fusion_rows": fusion_rows,
        "project_evidence_signals": project_signals,
        "blocked_reasons": [],
    }


def build_r0_r9_evidence_source(fusion_report: Mapping[str, Any], dxf_context: Mapping[str, Any]) -> dict[str, Any]:
    signals = _dxf_context_to_evidence_signals(dxf_context)
    signals.extend(list(fusion_report.get("project_evidence_signals") or []))
    return {
        "evidence_signals": signals,
        "source_kind": "dxf_pdf_unified_evidence",
        "fusion_summary": fusion_report.get("summary") or {},
    }


def build_pdf_pipeline_summary(
    *,
    parse_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    tile_report: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    match_report: Mapping[str, Any],
    fusion_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "pdf_file_count": (parse_report.get("summary") or {}).get("pdf_file_count", 0),
        "pdf_page_count": (parse_report.get("summary") or {}).get("page_count", 0),
        "pdf_text_row_count": (parse_report.get("summary") or {}).get("text_row_count", 0),
        "pdf_render_status": (render_report.get("summary") or {}).get("render_status", ""),
        "pdf_rendered_page_count": (render_report.get("summary") or {}).get("rendered_page_count", 0),
        "pdf_tile_count": (tile_report.get("summary") or {}).get("tile_count", 0),
        "pdf_visual_evidence_count": (evidence_report.get("summary") or {}).get("visual_evidence_count", 0),
        "pdf_llm_visual_status": (evidence_report.get("summary") or {}).get("llm_visual_status", ""),
        "pdf_llm_visual_tile_success_count": (evidence_report.get("summary") or {}).get("llm_visual_tile_success_count", 0),
        "pdf_llm_visual_tile_error_count": (evidence_report.get("summary") or {}).get("llm_visual_tile_error_count", 0),
        "pdf_llm_visual_tile_skipped_count": (evidence_report.get("summary") or {}).get("llm_visual_tile_skipped_count", 0),
        "dwg_pdf_match_score": (match_report.get("summary") or {}).get("match_score", 0),
        "dwg_pdf_match_status": (match_report.get("summary") or {}).get("match_status", ""),
        "dxf_pdf_fusion_status": (fusion_report.get("summary") or {}).get("fusion_status", ""),
        "dxf_pdf_fusion_link_count": (fusion_report.get("summary") or {}).get("fusion_link_count", 0),
        "r0_r9_pdf_signal_count": (fusion_report.get("summary") or {}).get("project_evidence_signal_count", 0),
    }


def build_pdf_pipeline_issues(
    render_report: Mapping[str, Any],
    match_report: Mapping[str, Any],
    fusion_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    render_status = (render_report.get("summary") or {}).get("render_status")
    if render_status != "rendered":
        issues.append({"级别": "warning", "说明": "PDF 高清 PNG 未全部生成，请检查 pdftoppm/Poppler 渲染工具。"})
    match_status = (match_report.get("summary") or {}).get("match_status")
    if match_status == "blocked":
        issues.append({"级别": "error", "说明": "DWG 与 PDF 对应关系不足，已阻断 DXF+PDF 证据合并。"})
    elif match_status == "needs_manual_bind":
        issues.append({"级别": "warning", "说明": "DWG 与 PDF 需要人工确认对应关系后再采用合并证据。"})
    if (fusion_report.get("summary") or {}).get("fusion_status") == "blocked_by_dwg_pdf_match":
        issues.append({"级别": "error", "说明": "证据合并被 DWG/PDF 匹配闸门阻断。"})
    return issues


def write_pdf_evidence_pipeline_outputs(report: Mapping[str, Any], output_dir: str | Path, *, stem: str) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    page_csv = directory / f"{stem}_页面清单.csv"
    text_csv = directory / f"{stem}_PDF文字证据.csv"
    render_csv = directory / f"{stem}_高清PNG渲染.csv"
    tile_csv = directory / f"{stem}_分块tile清单.csv"
    evidence_csv = directory / f"{stem}_视觉证据.csv"
    match_csv = directory / f"{stem}_DWG_PDF匹配.csv"
    fusion_csv = directory / f"{stem}_DXF_PDF证据合并.csv"
    r0_r9_json = directory / f"{stem}_R0_R9证据输入.json"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_pdf_pipeline_markdown(report), encoding="utf-8")
    _write_csv(page_csv, PDF_PAGE_HEADERS, _page_csv_rows(report))
    _write_csv(text_csv, PDF_TEXT_HEADERS, _text_csv_rows(report))
    _write_csv(render_csv, PDF_RENDER_HEADERS, _render_csv_rows(report))
    _write_csv(tile_csv, PDF_TILE_HEADERS, _tile_csv_rows(report))
    _write_csv(evidence_csv, PDF_EVIDENCE_HEADERS, _evidence_csv_rows(report))
    _write_csv(match_csv, PDF_MATCH_HEADERS, _match_csv_rows(report))
    _write_csv(fusion_csv, PDF_FUSION_HEADERS, _fusion_csv_rows(report))
    r0_r9_json.write_text(json.dumps(report.get("r0_r9_evidence_source") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "pdf_pipeline_json": str(json_path),
        "pdf_pipeline_markdown": str(markdown_path),
        "pdf_page_csv": str(page_csv),
        "pdf_text_csv": str(text_csv),
        "pdf_render_csv": str(render_csv),
        "pdf_tile_csv": str(tile_csv),
        "pdf_visual_evidence_csv": str(evidence_csv),
        "dwg_pdf_match_csv": str(match_csv),
        "dxf_pdf_fusion_csv": str(fusion_csv),
        "pdf_r0_r9_evidence_json": str(r0_r9_json),
    }


def build_pdf_pipeline_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-2x PDF 高清视觉证据链报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- PDF 文件数：{summary.get('pdf_file_count', 0)}",
        f"- PDF 页数：{summary.get('pdf_page_count', 0)}",
        f"- PDF 文字证据：{summary.get('pdf_text_row_count', 0)}",
        f"- 高清 PNG 状态：{summary.get('pdf_render_status', '-')}",
        f"- 已渲染页面：{summary.get('pdf_rendered_page_count', 0)}",
        f"- 分块 tile：{summary.get('pdf_tile_count', 0)}",
        f"- 视觉证据：{summary.get('pdf_visual_evidence_count', 0)}",
        f"- DWG/PDF 匹配：{summary.get('dwg_pdf_match_status', '-')}（{summary.get('dwg_pdf_match_score', 0)}）",
        f"- DXF/PDF 合并：{summary.get('dxf_pdf_fusion_status', '-')}",
        "",
        "## 边界",
        "",
        "- PDF 证据只用于补充视觉空间语义，不直接生成最终工程量。",
        "- DWG/PDF 未匹配时，DXF+PDF 证据合并会被阻断。",
        "- 工程量仍必须来自 DXF 规则 trace 或人工补量。",
        "",
        "## 问题",
        "",
    ]
    issues = list(report.get("issues") or [])
    if not issues:
        lines.append("- 暂无阻断问题。")
    else:
        for issue in issues:
            lines.append(f"- {issue.get('级别', '-')}: {issue.get('说明', '')}")
    return "\n".join(lines) + "\n"


def classify_pdf_evidence_role(text: str) -> str:
    clean = _clean_text(text)
    if not clean:
        return "empty"
    if any(keyword in clean for keyword in ELECTRICAL_KEYWORDS) or ELECTRICAL_SPEC_RE.search(clean):
        return "electrical_spec"
    if any(keyword in clean for keyword in PLUMBING_KEYWORDS) or PLUMBING_SPEC_RE.search(clean):
        return "plumbing_spec"
    if any(keyword in clean for keyword in DOOR_WINDOW_KEYWORDS):
        return "door_window_mark"
    if any(keyword in clean for keyword in LEGEND_KEYWORDS) or MATERIAL_CODE_RE.search(clean):
        return "material_legend"
    if any(keyword in clean for keyword in TITLE_KEYWORDS):
        return "drawing_title"
    if any(keyword in clean for keyword in DRAWING_CODE_KEYWORDS):
        return "drawing_code"
    if any(keyword in clean for keyword in NOTE_KEYWORDS):
        return "construction_note"
    if any(keyword in clean for keyword in ROOM_KEYWORDS) and len(clean) <= 30:
        return "room_name"
    return "unknown_note"


def _discipline_from_role_and_text(role: str, text: str) -> str:
    clean = _clean_text(text)
    if role == "electrical_spec" or any(keyword in clean for keyword in ELECTRICAL_KEYWORDS):
        return "electrical"
    if role == "plumbing_spec" or any(keyword in clean for keyword in PLUMBING_KEYWORDS):
        return "plumbing"
    if role in {"material_legend", "finish_material", "construction_method", "door_window_mark"}:
        return "decoration"
    return "unknown"


def _item_hint_from_role_and_text(role: str, text: str) -> str:
    clean = _clean_text(text)
    lower = clean.lower()
    if role == "electrical_spec":
        if "配电箱" in clean:
            return "配电箱"
        if "插座" in clean:
            return "插座安装"
        if "开关" in clean:
            return "开关安装"
        if "灯带" in clean:
            return "LED灯带"
        if "筒灯" in clean:
            return "LED筒灯"
        if "灯" in clean:
            return "灯具安装"
        if "电缆" in clean:
            return "电缆敷设"
        if "配管" in clean or re.search(r"\b(?:sc|mt|jdg)\s*\d+", lower):
            return "电气配管"
        if "配线" in clean or "电线" in clean or "byj" in lower:
            return "电气配线"
        return "电气安装"
    if role == "plumbing_spec":
        if "给水" in clean:
            return "给水管安装"
        if "排水" in clean:
            return "排水管安装"
        if "地漏" in clean:
            return "地漏"
        if "阀" in clean:
            return "阀门安装"
        if "水表" in clean:
            return "水表"
        if any(term in clean for term in ("洁具", "马桶", "台盆", "龙头", "花洒")):
            return "洁具安装"
        return "给排水安装"
    if role in {"material_legend", "finish_material", "construction_method"}:
        if any(term in clean for term in ("地砖", "瓷砖", "地面")):
            return "块料楼地面"
        if any(term in clean for term in ("墙砖", "墙面砖")):
            return "块料墙面"
        if "吊顶" in clean or "石膏板" in clean:
            return "石膏板吊顶"
        if "铝扣" in clean:
            return "铝扣板吊顶"
        if "灯槽" in clean:
            return "灯槽"
        if "窗帘盒" in clean:
            return "窗帘盒"
        if "踢脚" in clean:
            return "踢脚线"
        if "玻璃隔" in clean:
            return "玻璃隔断"
    if role == "door_window_mark":
        if "窗" in clean:
            return "窗"
        if "门" in clean:
            return "门"
    return ""


def collect_dxf_context_texts(dxf_context: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for parsed in dxf_context.get("parsed_files") or []:
        for record in getattr(parsed, "text_records", []) or []:
            texts.append(str(getattr(record, "text", "") or ""))
    field_report = dxf_context.get("field_report") or {}
    for row in field_report.get("material_method_rows") or []:
        texts.extend([str(row.get("material_or_method_name") or ""), str(row.get("spec_or_method") or ""), str(row.get("raw_row_text") or "")])
    for row in field_report.get("drawing_catalog_rows") or []:
        texts.extend([str(row.get("drawing_name") or ""), str(row.get("drawing_code") or ""), str(row.get("raw_row_text") or "")])
    for row in field_report.get("drawing_annotation_rows") or []:
        texts.extend([str(row.get("material_or_method_name") or ""), str(row.get("spec_or_method") or ""), str(row.get("raw_row_text") or "")])
    return [text for text in texts if _clean_text(text)]


def extract_material_codes(text: str) -> list[str]:
    return sorted({match.group(0).upper().replace("－", "-") for match in MATERIAL_CODE_RE.finditer(text or "")})


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).upper()


def _parse_pdf_with_optional_libraries(pdf_path: Path, content: bytes) -> dict[str, Any]:
    try:
        import pdfplumber  # type: ignore

        return _parse_pdf_with_pdfplumber(pdf_path, pdfplumber)
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore

        return _parse_pdf_with_pypdf(pdf_path, PdfReader)
    except Exception:
        return _parse_pdf_with_regex_fallback(pdf_path, content)


def _parse_pdf_with_pdfplumber(pdf_path: Path, pdfplumber: Any) -> dict[str, Any]:
    page_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_rows.append(
                _page_row(
                    pdf_path.name,
                    page_index,
                    float(page.width or 0),
                    float(page.height or 0),
                    0,
                    text,
                    parse_status="parsed",
                )
            )
            for line in _split_pdf_text_lines(text):
                text_rows.append(
                    {
                        "source_file": pdf_path.name,
                        "page": page_index,
                        "text": line,
                        "text_type": "page_text_line",
                        "bbox_pdf": [],
                        "confidence": 0.85,
                    }
                )
    return {"parse_engine": "pdfplumber", "parse_status": "parsed", "page_rows": page_rows, "text_rows": text_rows}


def _parse_pdf_with_pypdf(pdf_path: Path, PdfReader: Any) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    page_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []
    for page_index, page in enumerate(reader.pages, start=1):
        media_box = page.mediabox
        width = float(media_box.width or 0)
        height = float(media_box.height or 0)
        rotation = int(page.get("/Rotate", 0) or 0)
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_rows.append(_page_row(pdf_path.name, page_index, width, height, rotation, text, parse_status="parsed"))
        for line in _split_pdf_text_lines(text):
            text_rows.append(
                {
                    "source_file": pdf_path.name,
                    "page": page_index,
                    "text": line,
                    "text_type": "page_text_line",
                    "bbox_pdf": [],
                    "confidence": 0.8,
                }
            )
    return {"parse_engine": "pypdf", "parse_status": "parsed", "page_rows": page_rows, "text_rows": text_rows}


def _parse_pdf_with_regex_fallback(pdf_path: Path, content: bytes) -> dict[str, Any]:
    page_count = len(re.findall(rb"/Type\s*/Page\b", content)) or 1
    media_box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", content)
    width = float(media_box.group(1)) if media_box else 595.0
    height = float(media_box.group(2)) if media_box else 842.0
    page_rows = [
        _page_row(pdf_path.name, page_index, width, height, 0, "", parse_status="parsed_by_regex_fallback")
        for page_index in range(1, page_count + 1)
    ]
    return {
        "parse_engine": "regex_fallback",
        "parse_status": "parsed_without_text_dependency_missing",
        "page_rows": page_rows,
        "text_rows": [],
    }


def _page_row(
    source_file: str,
    page: int,
    width: float,
    height: float,
    rotation: int,
    text: str,
    *,
    parse_status: str,
) -> dict[str, Any]:
    text_length = len(text or "")
    return {
        "source_file": source_file,
        "page": page,
        "width_pt": round(width, 3),
        "height_pt": round(height, 3),
        "rotation": rotation,
        "text_length": text_length,
        "needs_visual_recognition": text_length < 20,
        "parse_status": parse_status,
    }


def _render_report(rows: list[dict[str, Any]], render_dpi: int, *, tool_path: str) -> dict[str, Any]:
    rendered_count = sum(1 for row in rows if row.get("status") == "rendered")
    failed_count = sum(1 for row in rows if row.get("status") == "render_failed")
    missing_count = sum(1 for row in rows if row.get("status") == "render_tool_missing")
    if missing_count:
        render_status = "render_tool_missing"
    elif failed_count:
        render_status = "partial_failed" if rendered_count else "render_failed"
    else:
        render_status = "rendered"
    return {
        "ok": True,
        "phase": "PDF-3-high-resolution-png-render",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "render_status": render_status,
            "render_dpi": render_dpi,
            "rendered_page_count": rendered_count,
            "render_failed_count": failed_count,
            "render_tool_missing_count": missing_count,
            "pdftoppm_path": tool_path,
        },
        "render_rows": rows,
    }


def _build_grid_tiles(
    *,
    source_file: str,
    page_no: int,
    width_pt: float,
    height_pt: float,
    image_path: Path | None,
    image_width: int,
    image_height: int,
    tile_dir: Path,
    grid_size: int,
) -> list[dict[str, Any]]:
    grid = max(int(grid_size or 3), 1)
    rows: list[dict[str, Any]] = []
    pil_image = _open_image(image_path) if image_path else None
    for row_no in range(grid):
        for col_no in range(grid):
            tile_id = f"p{page_no:03d}_g{grid:02d}_r{row_no + 1:02d}_c{col_no + 1:02d}"
            pdf_bbox = [
                round(width_pt * col_no / grid, 3),
                round(height_pt * row_no / grid, 3),
                round(width_pt * (col_no + 1) / grid, 3),
                round(height_pt * (row_no + 1) / grid, 3),
            ]
            pixel_bbox = [
                int(image_width * col_no / grid) if image_width else 0,
                int(image_height * row_no / grid) if image_height else 0,
                int(image_width * (col_no + 1) / grid) if image_width else 0,
                int(image_height * (row_no + 1) / grid) if image_height else 0,
            ]
            tile_path = ""
            status = "tile_planned_without_render_image"
            if pil_image is not None:
                cropped = pil_image.crop(tuple(pixel_bbox))
                output_path = tile_dir / f"{_short_file_stem(source_file)}_{tile_id}.png"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                cropped.save(output_path)
                tile_path = str(output_path.resolve())
                status = "tile_image_created"
            rows.append(
                {
                    "tile_id": tile_id,
                    "source_file": source_file,
                    "page": page_no,
                    "tile_type": "grid",
                    "bbox_pdf": pdf_bbox,
                    "bbox_pixel": pixel_bbox,
                    "image_path": tile_path,
                    "status": status,
                    "priority": 70,
                }
            )
    if pil_image is not None:
        pil_image.close()
    return rows


def _open_image(path: Path | None) -> Any:
    if not path or not path.exists() or not path.is_file():
        return None
    try:
        from PIL import Image  # type: ignore

        return Image.open(path)
    except Exception:
        return None


def _image_size(path: Path) -> tuple[int | None, int | None]:
    image = _open_image(path)
    if image is None:
        return None, None
    try:
        return int(image.width), int(image.height)
    finally:
        image.close()


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _find_pdftoppm() -> str | None:
    explicit = os.environ.get("PDFTOPPM_EXE") or os.environ.get("PDFTOPPM_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("pdftoppm")
    return found


def _render_pdf_pages_with_pypdfium2(
    parse_report: Mapping[str, Any],
    directory: Path,
    *,
    render_dpi: int,
) -> list[dict[str, Any]]:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    scale = max(float(render_dpi or 300) / 72.0, 0.1)
    for file_row in parse_report.get("file_rows") or []:
        pdf_path = Path(file_row.get("path") or "")
        if not pdf_path.exists():
            continue
        try:
            document = pdfium.PdfDocument(str(pdf_path))
            page_count = len(document)
            for page_index in range(page_count):
                page = document[page_index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                output_path = directory / f"{_safe_file_stem(pdf_path.stem)}-{page_index + 1}.png"
                image.save(output_path)
                width, height = image.size
                rows.append(
                    {
                        "source_file": pdf_path.name,
                        "page": page_index + 1,
                        "render_dpi": render_dpi,
                        "png_path": str(output_path.resolve()),
                        "status": "rendered",
                        "image_width_px": width,
                        "image_height_px": height,
                        "message": "已通过 pypdfium2 生成高清 PNG",
                    }
                )
                page.close()
            document.close()
        except Exception as exc:
            for page_number in range(1, int(file_row.get("page_count") or 0) + 1):
                rows.append(
                    {
                        "source_file": pdf_path.name,
                        "page": page_number,
                        "render_dpi": render_dpi,
                        "png_path": "",
                        "status": "render_failed",
                        "image_width_px": "",
                        "image_height_px": "",
                        "message": f"pypdfium2 渲染失败：{str(exc)[:240]}",
                    }
                )
    return rows


def _pdf_dependency_status() -> dict[str, bool]:
    return {
        "pdfplumber": _has_module("pdfplumber"),
        "pypdf": _has_module("pypdf"),
        "pillow": _has_module("PIL"),
        "pypdfium2": _has_module("pypdfium2"),
        "pdftoppm": bool(_find_pdftoppm()),
    }


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _split_pdf_text_lines(text: str) -> list[str]:
    rows: list[str] = []
    for raw in re.split(r"[\r\n]+", text or ""):
        clean = _clean_text(raw)
        if clean and len(clean) >= 2:
            rows.append(clean)
    if not rows and _clean_text(text):
        rows.append(_clean_text(text))
    return rows[:2000]


def _role_confidence(role: str, text: str) -> float:
    if role == "material_legend" and MATERIAL_CODE_RE.search(text):
        return 0.88
    if role in {"electrical_spec", "plumbing_spec"}:
        return 0.84
    if role in {"finish_material", "construction_method", "device_symbol", "equipment_schedule", "door_window_mark"}:
        return 0.78
    if role in {"drawing_title", "drawing_code", "construction_note"}:
        return 0.78
    if role == "room_name":
        return 0.62
    return 0.45


def _overlap_score(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / max(len(left_set), len(right_set), 1)


def _token_overlap_score(pdf_texts: list[str], dxf_texts: list[str]) -> float:
    pdf_tokens = _meaningful_tokens(" ".join(pdf_texts))
    dxf_tokens = _meaningful_tokens(" ".join(dxf_texts))
    if not pdf_tokens or not dxf_tokens:
        return 0.0
    return len(pdf_tokens & dxf_tokens) / max(min(len(pdf_tokens), len(dxf_tokens)), 1)


def _filename_score(pdf_files: Iterable[str | Path], dxf_context: Mapping[str, Any]) -> float:
    pdf_tokens = _meaningful_tokens(" ".join(Path(item).stem for item in pdf_files))
    dxf_names: list[str] = []
    conversion = dxf_context.get("conversion") or {}
    dxf_names.extend(Path(path).stem for path in conversion.get("output_files") or [])
    for parsed in dxf_context.get("parsed_files") or []:
        dxf_names.append(str(getattr(parsed, "file_name", "") or ""))
    dxf_tokens = _meaningful_tokens(" ".join(dxf_names))
    if not pdf_tokens or not dxf_tokens:
        return 0.0
    return len(pdf_tokens & dxf_tokens) / max(min(len(pdf_tokens), len(dxf_tokens)), 1)


def _meaningful_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff-]+", " ", text or "")
    tokens = {item.upper() for item in normalized.split() if len(item) >= 2}
    tokens.update(extract_material_codes(text))
    return tokens


def _material_code_text_index(texts: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for text in texts:
        for code in extract_material_codes(text):
            index.setdefault(code, []).append(text)
    return index


def _dxf_context_to_evidence_signals(dxf_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    field_report = dxf_context.get("field_report") or {}
    signals: list[dict[str, Any]] = []
    for row in field_report.get("material_method_rows") or []:
        text = _join_text(row.get("material_or_method_name"), row.get("spec_or_method"), row.get("raw_row_text"))
        if not text:
            continue
        signals.append(
            {
                "signal_id": f"DXFSIG-{len(signals) + 1:06d}",
                "source_kind": row.get("row_type") or "dxf_material_method",
                "source_kind_label": row.get("row_type_label") or "DXF材料/做法",
                "source_file": row.get("source_file", ""),
                "source_row_number": row.get("source_row_number", ""),
                "source_name": row.get("material_or_method_name") or text,
                "source_spec_or_method": row.get("spec_or_method", ""),
                "raw_row_text": row.get("raw_row_text", ""),
                "evidence_text": text,
                "evidence_source": "dxf_structured_evidence",
                "quantity": None,
                "quantity_unit": "",
                "quantity_source": "",
                "confidence": row.get("confidence", 0.0),
            }
        )
    for row in field_report.get("drawing_annotation_rows") or []:
        text = _join_text(row.get("material_or_method_name"), row.get("spec_or_method"), row.get("raw_row_text"))
        if not text:
            continue
        signals.append(
            {
                "signal_id": f"DXFSIG-{len(signals) + 1:06d}",
                "source_kind": row.get("row_type") or "dxf_drawing_annotation",
                "source_kind_label": row.get("row_type_label") or "DXF图纸文字",
                "source_file": row.get("source_file", ""),
                "source_row_number": row.get("source_row_number", ""),
                "source_name": row.get("material_or_method_name") or text,
                "source_spec_or_method": row.get("spec_or_method", ""),
                "raw_row_text": row.get("raw_row_text", ""),
                "evidence_text": text,
                "evidence_source": "dxf_structured_evidence",
                "quantity": None,
                "quantity_unit": row.get("unit", ""),
                "quantity_source": "",
                "confidence": row.get("confidence", 0.0),
            }
        )
    return signals


def _signal_name_from_evidence(evidence: Mapping[str, Any]) -> str:
    text = _clean_text(evidence.get("text"))
    codes = extract_material_codes(text)
    if codes:
        return codes[0]
    return text[:40]


def _page_number_from_pdftoppm_name(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name, flags=re.IGNORECASE)
    if not match:
        return 0
    return int(match.group(1))


def _page_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "PDF文件": row.get("source_file", ""),
            "页码": row.get("page", ""),
            "宽度pt": row.get("width_pt", ""),
            "高度pt": row.get("height_pt", ""),
            "旋转角度": row.get("rotation", ""),
            "文字长度": row.get("text_length", ""),
            "是否需要视觉识别": "是" if row.get("needs_visual_recognition") else "否",
        }
        for row in (report.get("parse_report") or {}).get("page_rows", [])
    ]


def _text_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "证据编号": row.get("evidence_id", ""),
            "PDF文件": row.get("source_file", ""),
            "页码": row.get("page", ""),
            "类型": row.get("text_type", ""),
            "文本": row.get("text", ""),
            "置信度": row.get("confidence", ""),
        }
        for row in (report.get("parse_report") or {}).get("text_rows", [])
    ]


def _render_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "PDF文件": row.get("source_file", ""),
            "页码": row.get("page", ""),
            "DPI": row.get("render_dpi", ""),
            "PNG路径": row.get("png_path", ""),
            "状态": row.get("status", ""),
            "宽度px": row.get("image_width_px", ""),
            "高度px": row.get("image_height_px", ""),
            "说明": row.get("message", ""),
        }
        for row in (report.get("render_report") or {}).get("render_rows", [])
    ]


def _tile_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "tile_id": row.get("tile_id", ""),
            "PDF文件": row.get("source_file", ""),
            "页码": row.get("page", ""),
            "类型": row.get("tile_type", ""),
            "bbox_pdf": json.dumps(row.get("bbox_pdf") or [], ensure_ascii=False),
            "bbox_pixel": json.dumps(row.get("bbox_pixel") or [], ensure_ascii=False),
            "PNG路径": row.get("image_path", ""),
            "状态": row.get("status", ""),
            "优先级": row.get("priority", ""),
        }
        for row in (report.get("tile_report") or {}).get("tile_rows", [])
    ]


def _evidence_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "证据编号": row.get("evidence_id", ""),
            "来源": row.get("source_kind", ""),
            "PDF文件": row.get("source_file", ""),
            "页码": row.get("page", ""),
            "tile_id": row.get("tile_id", ""),
            "识别pass": row.get("vision_pass", ""),
            "角色": row.get("evidence_role", ""),
            "专业": row.get("discipline", ""),
            "清单项目提示": row.get("item_hint", ""),
            "空间/部位": row.get("space", ""),
            "材料编号": "；".join(row.get("material_codes") or []),
            "规格/做法": row.get("spec_or_method", ""),
            "建议单位": row.get("suggested_unit", ""),
            "文本": row.get("text", ""),
            "置信度": row.get("confidence", ""),
            "需人工复核": "是" if row.get("needs_manual_review") else "否",
        }
        for row in (report.get("visual_evidence_report") or {}).get("evidence_rows", [])
    ]


def _match_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "匹配项": row.get("match_item", ""),
            "分数": row.get("score", ""),
            "状态": row.get("status", ""),
            "说明": row.get("message", ""),
        }
        for row in (report.get("dwg_pdf_match_report") or {}).get("match_rows", [])
    ]


def _fusion_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "融合编号": row.get("fusion_id", ""),
            "类型": row.get("fusion_type", ""),
            "DXF证据": row.get("dxf_evidence", ""),
            "PDF证据": row.get("pdf_evidence", ""),
            "置信度": row.get("confidence", ""),
            "状态": row.get("status", ""),
        }
        for row in (report.get("fusion_report") or {}).get("fusion_rows", [])
    ]


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _join_text(*parts: Any) -> str:
    return "；".join(dict.fromkeys(_clean_text(part) for part in parts if _clean_text(part)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_file_stem(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "pdf"


def _short_file_stem(value: str, *, max_prefix: int = 6) -> str:
    safe = _safe_file_stem(value)
    digest = hashlib.sha1(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe[:max_prefix].strip('._') or 'pdf'}_{digest}"
