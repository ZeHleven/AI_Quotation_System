from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image


PHASE = "BIZ-2x-pdf-agent-local-ocr"
OCR_ENGINE_PADDLE = "paddleocr"

OcrRunner = Callable[[Path], Sequence[Any]]

MATERIAL_PREFIXES = {
    "AL",
    "CL",
    "CT",
    "FC",
    "GL",
    "GR",
    "LP",
    "MR",
    "MT",
    "PT",
    "SC",
    "SS",
    "ST",
    "TL",
    "WD",
    "WC",
}
MATERIAL_CODE_RE = re.compile(
    r"(?<![A-Z0-9])("
    + "|".join(sorted(MATERIAL_PREFIXES, key=len, reverse=True))
    + r")\s*[-_]?\s*([A-Z0-9]{0,4})(?![A-Z0-9])",
    re.IGNORECASE,
)


def build_pdf_agent_ocr_report(
    *,
    render_report: Mapping[str, Any],
    crop_dir: str | Path,
    ocr_dir: str | Path,
    context_dir: str | Path,
    ocr_engine: str = OCR_ENGINE_PADDLE,
    ocr_runner: OcrRunner | None = None,
    max_page_crops: int = 24,
    extra_crop_manifest: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    crop_root = Path(crop_dir)
    ocr_root = Path(ocr_dir)
    context_root = Path(context_dir)
    crop_root.mkdir(parents=True, exist_ok=True)
    ocr_root.mkdir(parents=True, exist_ok=True)
    context_root.mkdir(parents=True, exist_ok=True)

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    crop_manifest = build_agent_ocr_crop_manifest(
        render_report=render_report,
        crop_dir=crop_root,
        max_page_crops=max_page_crops,
        warnings=warnings,
    )
    crop_manifest.extend(_normalize_extra_crop_manifest(extra_crop_manifest or []))
    runner = ocr_runner
    unavailable_reason = ""
    if runner is None:
        runner, unavailable_reason = _load_local_ocr_runner(ocr_engine)

    ocr_rows: list[dict[str, Any]] = []
    ocr_status = "completed"
    completed_crops = 0
    if runner is None:
        ocr_status = "unavailable"
        warnings.append(
            {
                "code": "OCR_UNAVAILABLE",
                "message": unavailable_reason or "local OCR runner unavailable",
                "engine": ocr_engine,
            }
        )
    else:
        for crop in crop_manifest:
            image_path = Path(str(crop.get("image_path") or ""))
            if not image_path.exists() or not image_path.is_file():
                continue
            try:
                raw_result = runner(image_path)
                crop_rows = normalize_ocr_result(raw_result, crop=crop)
                ocr_rows.extend(crop_rows)
                completed_crops += 1
            except Exception as exc:  # noqa: BLE001 - OCR engines vary by install/version
                errors.append(
                    {
                        "code": "OCR_CROP_FAILED",
                        "message": str(exc),
                        "crop_id": crop.get("crop_id", ""),
                        "image_path": str(image_path),
                    }
                )
        if errors and completed_crops:
            ocr_status = "completed_with_errors"
        elif errors:
            ocr_status = "failed"

    region_ocr_rows = [row for row in ocr_rows if _clean_text(row.get("region_id"))]
    material_candidates = extract_material_legend_candidates(ocr_rows)
    region_material_candidates = [
        item for item in material_candidates if item.get("source_region_ids")
    ]
    summary = {
        "ocr_status": ocr_status,
        "ocr_engine": ocr_engine,
        "crop_count": len(crop_manifest),
        "default_crop_count": len([row for row in crop_manifest if not _clean_text(row.get("region_id"))]),
        "region_crop_count": len([row for row in crop_manifest if _clean_text(row.get("region_id"))]),
        "ocr_completed_crop_count": completed_crops,
        "ocr_text_line_count": len(ocr_rows),
        "region_ocr_text_line_count": len(region_ocr_rows),
        "material_legend_candidate_count": len(material_candidates),
        "region_material_legend_candidate_count": len(region_material_candidates),
        "material_code_count": len({item.get("code") for item in material_candidates if item.get("code")}),
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    if unavailable_reason:
        summary["unavailable_reason"] = unavailable_reason

    outputs = _write_ocr_outputs(
        ocr_dir=ocr_root,
        context_dir=context_root,
        summary=summary,
        crop_manifest=crop_manifest,
        ocr_rows=ocr_rows,
        material_candidates=material_candidates,
        warnings=warnings,
        errors=errors,
    )
    report = {
        "ok": ocr_status not in {"failed"},
        "phase": PHASE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ocr_engine": ocr_engine,
        "ocr_status": ocr_status,
        "summary": summary,
        "crop_manifest": crop_manifest,
        "ocr_rows": ocr_rows,
        "region_ocr_rows": region_ocr_rows,
        "material_legend_candidates": material_candidates,
        "region_material_legend_candidates": region_material_candidates,
        "warnings": warnings,
        "errors": errors,
        "outputs": outputs,
    }
    report_path = ocr_root / "ocr_report.json"
    report["outputs"]["agent_ocr_report_json"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_agent_ocr_crop_manifest(
    *,
    render_report: Mapping[str, Any],
    crop_dir: Path,
    max_page_crops: int,
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    warnings = warnings if warnings is not None else []
    for render in render_report.get("render_rows") or []:
        if len(rows) >= max_page_crops:
            break
        source_image = Path(str(render.get("png_path") or ""))
        if not source_image.exists() or not source_image.is_file():
            warnings.append(
                {
                    "code": "OCR_SOURCE_IMAGE_MISSING",
                    "message": "rendered page image missing",
                    "source_image": str(source_image),
                }
            )
            continue
        try:
            with Image.open(source_image) as image:
                width, height = image.size
                for crop_type, bbox, reason in _page_context_crop_boxes(width, height):
                    if len(rows) >= max_page_crops:
                        break
                    crop_id = f"ocr_p{_int(render.get('page')):03d}_{crop_type}_{len(rows) + 1:03d}"
                    output_path = crop_dir / f"{_safe_stem(str(render.get('source_file') or source_image.stem))}_{crop_id}.png"
                    normalized_bbox = _clamp_bbox(bbox, width=width, height=height)
                    if _bbox_area(normalized_bbox) <= 0:
                        continue
                    crop = image.convert("RGB").crop(normalized_bbox)
                    scale_factor = _ocr_scale_factor(crop.size)
                    if scale_factor > 1:
                        crop = crop.resize((crop.width * scale_factor, crop.height * scale_factor), Image.Resampling.LANCZOS)
                    crop.save(output_path)
                    rows.append(
                        {
                            "crop_id": crop_id,
                            "crop_type": crop_type,
                            "source_file": str(render.get("source_file") or ""),
                            "page": _int(render.get("page")),
                            "source_image_path": str(source_image.resolve()),
                            "image_path": str(output_path.resolve()),
                            "bbox_pixel": list(normalized_bbox),
                            "image_width_px": crop.width,
                            "image_height_px": crop.height,
                            "scale_factor": scale_factor,
                            "reason": reason,
                            "status": "created",
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - keep OCR prep from stopping PDF agent
            warnings.append(
                {
                    "code": "OCR_CROP_BUILD_FAILED",
                    "message": str(exc),
                    "source_image": str(source_image),
                }
            )
    return rows


def normalize_ocr_result(raw_result: Sequence[Any], *, crop: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for text, confidence, bbox in _iter_ocr_text_lines(raw_result):
        clean_text = _clean_text(text)
        if not clean_text:
            continue
        rows.append(
            {
                "crop_id": _clean_text(crop.get("crop_id")),
                "crop_type": _clean_text(crop.get("crop_type")),
                "source_file": _clean_text(crop.get("source_file")),
                "page": _int(crop.get("page")),
                "region_id": _clean_text(crop.get("region_id")),
                "region_type": _clean_text(crop.get("region_type")),
                "text": clean_text,
                "confidence": round(_float(confidence, 0.0), 4),
                "bbox": bbox,
                "image_path": _clean_text(crop.get("image_path")),
            }
        )
    return rows


def extract_material_legend_candidates(ocr_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in ocr_rows:
        text = _clean_text(row.get("text"))
        if not text:
            continue
        for match in MATERIAL_CODE_RE.finditer(text):
            prefix = match.group(1).upper()
            suffix = (match.group(2) or "").upper()
            if suffix:
                code = f"{prefix}-{suffix}" if "-" not in match.group(0) else f"{prefix}-{suffix}"
            else:
                code = prefix
            key = _normalize(code)
            if not key:
                continue
            hint = _clean_text(text.replace(match.group(0), " "))
            current = candidates.setdefault(
                key,
                {
                    "code": code,
                    "name_or_hint": "",
                    "spec_or_method": "",
                    "source": "region_ocr" if _clean_text(row.get("region_id")) else "local_ocr",
                    "source_crop_ids": [],
                    "source_region_ids": [],
                    "source_region_types": [],
                    "source_texts": [],
                    "confidence": 0.0,
                },
            )
            current["name_or_hint"] = current.get("name_or_hint") or hint
            current["spec_or_method"] = _join_unique([current.get("spec_or_method"), hint])
            current["source_crop_ids"] = _merge_unique_lists(current.get("source_crop_ids"), [row.get("crop_id")])
            current["source_region_ids"] = _merge_unique_lists(current.get("source_region_ids"), [row.get("region_id")])
            current["source_region_types"] = _merge_unique_lists(
                current.get("source_region_types"),
                [row.get("region_type")],
            )
            current["source_texts"] = _merge_unique_lists(current.get("source_texts"), [text])
            current["confidence"] = max(_float(current.get("confidence"), 0), _float(row.get("confidence"), 0))
    return sorted(candidates.values(), key=lambda item: (item.get("code") or ""))


def _normalize_extra_crop_manifest(crop_manifest: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(crop_manifest, start=1):
        if not isinstance(raw, Mapping):
            continue
        image_path = _clean_text(raw.get("image_path"))
        if not image_path:
            continue
        crop_id = _clean_text(raw.get("crop_id")) or f"extra_crop_{index:03d}"
        rows.append(
            {
                **dict(raw),
                "crop_id": crop_id,
                "crop_type": _clean_text(raw.get("crop_type")) or "layout_region",
                "image_path": image_path,
                "status": _clean_text(raw.get("status")) or "created",
            }
        )
    return rows


def _page_context_crop_boxes(width: int, height: int) -> list[tuple[str, tuple[int, int, int, int], str]]:
    right_start = int(width * 0.62)
    lower_start = int(height * 0.76)
    upper_end = int(height * 0.18)
    middle_start = int(height * 0.35)
    middle_end = int(height * 0.72)
    return [
        ("right_legend", (right_start, 0, width, height), "right side notes, legends, and material tables"),
        ("bottom_title_block", (0, lower_start, width, height), "title block and drawing metadata"),
        ("top_header", (0, 0, width, upper_end), "drawing title and top notes"),
        ("right_middle_notes", (right_start, middle_start, width, middle_end), "dense right-side material notes"),
    ]


def _load_local_ocr_runner(ocr_engine: str) -> tuple[OcrRunner | None, str]:
    if ocr_engine != OCR_ENGINE_PADDLE:
        return None, f"unsupported local OCR engine: {ocr_engine}"
    _prepare_paddleocr_runtime()
    if shutil.which("paddleocr") is None:
        # The Python package may still be installed without a CLI, so try importing it before giving up.
        pass
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional dependency
        return None, f"paddleocr package is not installed or cannot be imported: {exc}"
    try:
        try:
            engine = PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
            )
        except TypeError:
            engine = PaddleOCR(use_angle_cls=True, lang="ch")
    except Exception as exc:  # noqa: BLE001 - local OCR must degrade instead of stopping the agent
        return None, f"paddleocr engine cannot be initialized: {exc}"

    def _runner(image_path: Path) -> Sequence[Any]:
        if hasattr(engine, "predict"):
            return engine.predict(str(image_path))
        try:
            return engine.ocr(str(image_path), cls=True)
        except TypeError:
            return engine.ocr(str(image_path))

    return _runner, ""


def _prepare_paddleocr_runtime() -> None:
    project_root = Path(__file__).resolve().parents[2]
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str((project_root / "runtime" / "paddlex_cache").resolve()))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    torch_lib = _find_torch_lib_dir()
    if torch_lib is None:
        return
    try:
        os.add_dll_directory(str(torch_lib))
    except (AttributeError, OSError):
        return
    try:
        import torch  # noqa: F401
    except Exception:
        return


def _find_torch_lib_dir() -> Path | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
    except Exception:
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    lib_dir = Path(list(spec.submodule_search_locations)[0]) / "lib"
    return lib_dir if lib_dir.exists() and lib_dir.is_dir() else None


def _iter_ocr_text_lines(raw_result: Any) -> list[tuple[str, float, list[Any]]]:
    rows: list[tuple[str, float, list[Any]]] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            if isinstance(node.get("json"), Mapping):
                visit(node.get("json"))
                return
            if isinstance(node.get("res"), Mapping):
                visit(node.get("res"))
                return
            rec_texts = node.get("rec_texts")
            if isinstance(rec_texts, Sequence) and not isinstance(rec_texts, (str, bytes)):
                rec_scores = node.get("rec_scores") if isinstance(node.get("rec_scores"), Sequence) else []
                rec_polys = node.get("rec_polys") if isinstance(node.get("rec_polys"), Sequence) else []
                rec_boxes = node.get("rec_boxes") if isinstance(node.get("rec_boxes"), Sequence) else []
                for index, text in enumerate(rec_texts):
                    bbox = []
                    if index < len(rec_polys):
                        bbox = _json_safe_box(rec_polys[index])
                    elif index < len(rec_boxes):
                        bbox = _json_safe_box(rec_boxes[index])
                    score = rec_scores[index] if index < len(rec_scores) else 0.0
                    rows.append((_clean_text(text), _float(score, 0.0), bbox))
                return
            text = _clean_text(node.get("text") or node.get("transcription"))
            if text:
                rows.append((text, _float(node.get("confidence") or node.get("score"), 0.0), list(node.get("bbox") or [])))
            return
        if not isinstance(node, (list, tuple)):
            return
        if len(node) >= 2 and _looks_like_box(node[0]) and _looks_like_text_score(node[1]):
            text_score = node[1]
            text = _clean_text(text_score[0] if isinstance(text_score, (list, tuple)) and text_score else "")
            score = _float(text_score[1] if isinstance(text_score, (list, tuple)) and len(text_score) > 1 else 0.0, 0.0)
            rows.append((text, score, _json_safe_box(node[0])))
            return
        for item in node:
            visit(item)

    visit(raw_result)
    return rows


def _write_ocr_outputs(
    *,
    ocr_dir: Path,
    context_dir: Path,
    summary: Mapping[str, Any],
    crop_manifest: Sequence[Mapping[str, Any]],
    ocr_rows: Sequence[Mapping[str, Any]],
    material_candidates: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    region_ocr_rows = [row for row in ocr_rows if _clean_text(row.get("region_id"))]
    region_material_candidates = [
        item for item in material_candidates if item.get("source_region_ids")
    ]
    payloads = [
        ("agent_ocr_summary_json", ocr_dir / "ocr_summary.json", dict(summary)),
        ("agent_ocr_crop_manifest_json", ocr_dir / "crop_manifest.json", list(crop_manifest)),
        ("agent_ocr_rows_json", ocr_dir / "crop_ocr.json", list(ocr_rows)),
        ("agent_region_ocr_rows_json", ocr_dir / "region_ocr_rows.json", list(region_ocr_rows)),
        ("agent_material_legend_candidates_json", context_dir / "material_legend_candidates.json", list(material_candidates)),
        (
            "agent_region_material_legend_candidates_json",
            context_dir / "region_material_legend_candidates.json",
            list(region_material_candidates),
        ),
        (
            "agent_ocr_diagnostics_json",
            ocr_dir / "ocr_diagnostics.json",
            {"warnings": list(warnings), "errors": list(errors)},
        ),
    ]
    outputs: dict[str, str] = {}
    for key, path, payload in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs[key] = str(path.resolve())
    return outputs


def _looks_like_box(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 4


def _looks_like_text_score(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and isinstance(value[0], str)


def _json_safe_box(value: Any) -> list[Any]:
    if hasattr(value, "tolist"):
        try:
            value = value.tolist()
        except Exception:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[Any] = []
    for item in value:
        if hasattr(item, "tolist"):
            try:
                item = item.tolist()
            except Exception:
                item = str(item)
        if isinstance(item, tuple):
            result.append(list(item))
        else:
            result.append(item)
    return result


def _ocr_scale_factor(size: tuple[int, int]) -> int:
    width, height = size
    short_side = min(width, height)
    if short_side < 600:
        return 3
    if short_side < 1000:
        return 2
    return 1


def _clamp_bbox(bbox: tuple[int, int, int, int], *, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width, int(x1))),
        max(0, min(height, int(y1))),
        max(0, min(width, int(x2))),
        max(0, min(height, int(y2))),
    )


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _safe_stem(value: str) -> str:
    text = Path(value or "drawing").stem
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    return text or "drawing"


def _merge_unique_lists(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, Sequence):
            items = list(value)
        else:
            items = []
        for item in items:
            clean = _clean_text(item)
            key = _normalize(clean)
            if not clean or key in seen:
                continue
            seen.add(key)
            result.append(clean)
    return result


def _join_unique(values: Sequence[Any]) -> str:
    return "；".join(_merge_unique_lists(values))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).lower()


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
