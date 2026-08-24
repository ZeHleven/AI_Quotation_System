"""V605-2 real local OCR and deterministic visual-structure evaluation.

The evaluator generates versioned synthetic raster pages at runtime. It loads
PaddleOCR only from explicit local model directories and uses OpenCV morphology
for table-grid detection. It performs no real PDF, external network, MCP,
generation-model, database, Milvus, or ECS operation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import statistics
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
import unicodedata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    PROJECT_ROOT
    / "evals"
    / "bid_assessment"
    / "v6052-ocr-vision-synthetic-cases.json"
)
SCHEMA_VERSION = "bid.pure_agent.v6052.ocr_vision.v1"
DETECTION_MODEL_NAME = "PP-OCRv5_mobile_det"
RECOGNITION_MODEL_NAME = "PP-OCRv5_mobile_rec"
MODEL_FILES = ("config.json", "inference.json", "inference.pdiparams", "inference.yml")
OCR_EVIDENCE_ROLE = "observation_candidate"


class V6052EvaluationError(RuntimeError):
    pass


class OcrProviderUnavailable(RuntimeError):
    code = "ocr_provider_unavailable"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_snapshot(path: Path, *, expected_name: str) -> tuple[Path, dict[str, Any]]:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise V6052EvaluationError(f"{expected_name} local model directory is unavailable")
    missing = [name for name in MODEL_FILES if not (resolved / name).is_file()]
    if missing:
        raise V6052EvaluationError(f"{expected_name} local model files are incomplete")
    file_entries: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for name in MODEL_FILES:
        model_file = resolved / name
        file_hash = _file_sha256(model_file)
        size = model_file.stat().st_size
        aggregate.update(name.encode("utf-8"))
        aggregate.update(file_hash.encode("ascii"))
        file_entries.append({"name": name, "bytes": size, "sha256": file_hash})
    return resolved, {
        "model_name": expected_name,
        "aggregate_sha256": aggregate.hexdigest(),
        "total_bytes": sum(entry["bytes"] for entry in file_entries),
        "files": file_entries,
    }


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise V6052EvaluationError("unsupported V605-2 dataset schema")
    if payload.get("dataset_kind") != "synthetic_only":
        raise V6052EvaluationError("V605-2 accepts synthetic-only datasets")
    contract = payload.get("execution_contract")
    if not isinstance(contract, dict) or not contract:
        raise V6052EvaluationError("V605-2 execution contract is required")
    if any(bool(value) for value in contract.values()):
        raise V6052EvaluationError("V605-2 external and real-data operations must be disabled")
    image_cases = payload.get("image_cases")
    routing_cases = payload.get("routing_cases")
    if not isinstance(image_cases, list) or not image_cases:
        raise V6052EvaluationError("V605-2 image cases are required")
    if not isinstance(routing_cases, list) or not routing_cases:
        raise V6052EvaluationError("V605-2 routing cases are required")
    all_cases = (*image_cases, *routing_cases)
    ids = [case.get("id") for case in all_cases if isinstance(case, dict)]
    if len(ids) != len(all_cases) or len(ids) != len(set(ids)):
        raise V6052EvaluationError("V605-2 case ids must be present and unique")
    kinds = {case.get("kind") for case in image_cases}
    if kinds != {"text_page", "table_page", "blank_page"}:
        raise V6052EvaluationError("V605-2 synthetic image kinds are incomplete")
    return payload


def select_extraction_route(*, native_text: str | None, ocr_available: bool) -> str:
    """Native text is authoritative for routing; OCR is conditional fallback."""

    if native_text and native_text.strip():
        return "native"
    return "ocr" if ocr_available else "degraded"


def guarded_ocr_result(
    operation: Callable[[], Mapping[str, Any]],
) -> dict[str, Any]:
    """Disclose expected provider unavailability without fabricating content."""

    try:
        result = dict(operation())
    except OcrProviderUnavailable as exc:
        return {
            "status": "degraded",
            "error_code": exc.code,
            "texts": [],
            "scores": [],
            "boxes": [],
            "evidence_role": OCR_EVIDENCE_ROLE,
            "citable": False,
            "review_required": True,
        }
    result.update(
        {
            "status": "enabled",
            "evidence_role": OCR_EVIDENCE_ROLE,
            "citable": False,
        }
    )
    return result


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized))


def _fragment_recall(texts: Sequence[str], expected: Sequence[str]) -> float:
    if not expected:
        return 1.0
    corpus = _normalize_text("".join(texts))
    matched = sum(1 for fragment in expected if _normalize_text(fragment) in corpus)
    return round(matched / len(expected), 6)


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 6) if values else 0.0


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int((percentile * len(ordered)) + 0.999999)))
    return round(ordered[rank - 1], 6)


def _render_case(case: Mapping[str, Any], path: Path, *, font_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    canvas = case["canvas"]
    background = int(canvas["background"])
    image = Image.new(
        "RGB",
        (int(canvas["width"]), int(canvas["height"])),
        (background, background, background),
    )
    draw = ImageDraw.Draw(image)
    kind = case["kind"]
    if kind == "text_page":
        font = ImageFont.truetype(str(font_path), int(case["font_size"]))
        foreground = int(case.get("foreground", 0))
        spacing = int(case["font_size"]) + 42
        for index, line in enumerate(case["lines"]):
            draw.text(
                (45, 38 + (index * spacing)),
                str(line),
                font=font,
                fill=(foreground, foreground, foreground),
            )
    elif kind == "table_page":
        font = ImageFont.truetype(str(font_path), int(case["font_size"]))
        table = case["table"]
        x0, y0 = (int(value) for value in table["origin"])
        width, height = int(table["width"]), int(table["height"])
        rows = table["rows"]
        row_count, column_count = len(rows), len(rows[0])
        row_height, column_width = height // row_count, width // column_count
        for row in range(row_count + 1):
            y = y0 + (row * row_height)
            draw.line((x0, y, x0 + width, y), fill=(0, 0, 0), width=4)
        for column in range(column_count + 1):
            x = x0 + (column * column_width)
            draw.line((x, y0, x, y0 + height), fill=(0, 0, 0), width=4)
        for row_index, row in enumerate(rows):
            for column_index, text in enumerate(row):
                bounds = draw.textbbox((0, 0), str(text), font=font)
                text_width = bounds[2] - bounds[0]
                text_height = bounds[3] - bounds[1]
                left = x0 + (column_index * column_width)
                top = y0 + (row_index * row_height)
                draw.text(
                    (
                        left + ((column_width - text_width) // 2),
                        top + ((row_height - text_height) // 2) - bounds[1],
                    ),
                    str(text),
                    font=font,
                    fill=(0, 0, 0),
                )
    elif kind != "blank_page":
        raise V6052EvaluationError(f"unsupported image kind: {kind}")
    blur_radius = float(case.get("blur_radius", 0.0))
    if blur_radius > 0:
        image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    image.save(path, format="PNG")


def _image_quality(path: Path) -> dict[str, float]:
    import cv2
    import numpy as np

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise V6052EvaluationError("synthetic image could not be read")
    return {
        "contrast_std_normalized": round(float(np.std(gray)) / 255.0, 6),
        "laplacian_variance": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 6),
    }


def assess_ocr_observation(
    *,
    texts: Sequence[str],
    scores: Sequence[float],
    quality: Mapping[str, float],
) -> dict[str, Any]:
    mean_confidence = _mean([float(value) for value in scores])
    reasons: list[str] = []
    if not any(text.strip() for text in texts):
        reasons.append("no_text_detected")
    if scores and mean_confidence < 0.85:
        reasons.append("low_ocr_confidence")
    if float(quality["contrast_std_normalized"]) < 0.025:
        reasons.append("low_image_contrast")
    if float(quality["laplacian_variance"]) < 12.0:
        reasons.append("low_image_sharpness")
    return {
        "mean_confidence": mean_confidence,
        "review_required": bool(reasons),
        "review_reasons": reasons,
        "evidence_role": OCR_EVIDENCE_ROLE,
        "citable": False,
        "requires_evidence_read": True,
    }


def _collapse_projection_positions(mask: Any, *, axis: int, threshold: int) -> list[int]:
    import numpy as np

    projection = np.count_nonzero(mask, axis=axis)
    positions = np.flatnonzero(projection >= threshold).tolist()
    groups: list[list[int]] = []
    for position in positions:
        if not groups or position > groups[-1][-1] + 1:
            groups.append([position])
        else:
            groups[-1].append(position)
    return [int(round(statistics.fmean(group))) for group in groups]


def detect_table_grid(path: Path) -> dict[str, Any]:
    """Detect an explicit ruled grid using deterministic OpenCV morphology."""

    import cv2
    import numpy as np

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise V6052EvaluationError("table image could not be read")
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    height, width = binary.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(25, width // 18), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(25, height // 10))
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    y_lines = _collapse_projection_positions(
        horizontal,
        axis=1,
        threshold=max(30, int(width * 0.45)),
    )
    x_lines = _collapse_projection_positions(
        vertical,
        axis=0,
        threshold=max(30, int(height * 0.45)),
    )
    cells: list[dict[str, Any]] = []
    for row, (top, bottom) in enumerate(zip(y_lines, y_lines[1:])):
        for column, (left, right) in enumerate(zip(x_lines, x_lines[1:])):
            if right - left < 20 or bottom - top < 20:
                continue
            cells.append(
                {
                    "id": f"r{row}c{column}",
                    "row": row,
                    "column": column,
                    "box": [left, top, right, bottom],
                }
            )
    return {
        "algorithm": "opencv_morphological_grid",
        "x_lines": x_lines,
        "y_lines": y_lines,
        "rows": max(0, len(y_lines) - 1),
        "columns": max(0, len(x_lines) - 1),
        "cells": cells,
    }


class LocalPaddleOcrProvider:
    def __init__(self, *, detection_dir: Path, recognition_dir: Path) -> None:
        try:
            import torch  # noqa: F401 - preload avoids Windows DLL import ordering issue
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                text_detection_model_name=DETECTION_MODEL_NAME,
                text_detection_model_dir=str(detection_dir),
                text_recognition_model_name=RECOGNITION_MODEL_NAME,
                text_recognition_model_dir=str(recognition_dir),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
                enable_mkldnn=False,
            )
        except Exception as exc:
            raise OcrProviderUnavailable("local PaddleOCR could not be initialized") from exc

    def predict(self, path: Path) -> dict[str, Any]:
        try:
            results = list(self._ocr.predict(str(path)))
        except Exception as exc:
            raise OcrProviderUnavailable("local PaddleOCR inference failed") from exc
        if not results:
            return {"texts": [], "scores": [], "boxes": []}
        payload = results[0].json.get("res", {})
        texts = [str(value) for value in payload.get("rec_texts", [])]
        scores = [float(value) for value in payload.get("rec_scores", [])]
        boxes = [
            [int(round(float(coordinate))) for coordinate in box]
            for box in payload.get("rec_boxes", [])
        ]
        if not (len(texts) == len(scores) == len(boxes)):
            raise V6052EvaluationError("PaddleOCR output arrays are misaligned")
        return {"texts": texts, "scores": scores, "boxes": boxes}


def _box_integrity(boxes: Sequence[Sequence[int]], *, width: int, height: int) -> float:
    if not boxes:
        return 1.0
    valid = sum(
        1
        for box in boxes
        if len(box) == 4
        and 0 <= box[0] < box[2] <= width
        and 0 <= box[1] < box[3] <= height
    )
    return round(valid / len(boxes), 6)


def _crop_table_cells(
    path: Path,
    grid: Mapping[str, Any],
    destination: Path,
) -> list[tuple[str, Path]]:
    from PIL import Image

    image = Image.open(path)
    crops: list[tuple[str, Path]] = []
    for cell in grid["cells"]:
        left, top, right, bottom = cell["box"]
        margin = 7
        crop = image.crop((left + margin, top + margin, right - margin, bottom - margin))
        crop_path = destination / f"{cell['id']}.png"
        crop.save(crop_path, format="PNG")
        crops.append((str(cell["id"]), crop_path))
    return crops


def _evaluate_image_case(
    case: Mapping[str, Any],
    *,
    provider: LocalPaddleOcrProvider,
    directory: Path,
    font_path: Path,
) -> dict[str, Any]:
    image_path = directory / f"{case['id']}.png"
    _render_case(case, image_path, font_path=font_path)
    quality = _image_quality(image_path)
    started = time.perf_counter()
    observation = provider.predict(image_path)
    visual: dict[str, Any] | None = None
    cell_texts: dict[str, list[str]] = {}
    cell_fragment_recall: float | None = None
    if case["kind"] == "table_page":
        grid = detect_table_grid(image_path)
        crop_dir = directory / f"{case['id']}-cells"
        crop_dir.mkdir()
        for cell_id, crop_path in _crop_table_cells(image_path, grid, crop_dir):
            cell_texts[cell_id] = provider.predict(crop_path)["texts"]
        expected_cells = case["expected_cell_fragments"]
        matched_cells = sum(
            1
            for cell_id, expected in expected_cells.items()
            if _normalize_text(str(expected))
            in _normalize_text("".join(cell_texts.get(cell_id, [])))
        )
        cell_fragment_recall = round(matched_cells / len(expected_cells), 6)
        visual = {
            "algorithm": grid["algorithm"],
            "rows": grid["rows"],
            "columns": grid["columns"],
            "cell_count": len(grid["cells"]),
            "cell_fragment_recall": cell_fragment_recall,
            "cell_texts": cell_texts,
        }
    elapsed = round(time.perf_counter() - started, 6)
    assessment = assess_ocr_observation(
        texts=observation["texts"],
        scores=observation["scores"],
        quality=quality,
    )
    expected_review = bool(case["expected_review_required"])
    canvas = case["canvas"]
    checks = {
        "review_gate": assessment["review_required"] is expected_review,
        "box_integrity": _box_integrity(
            observation["boxes"],
            width=int(canvas["width"]),
            height=int(canvas["height"]),
        )
        == 1.0,
        "non_citable_observation": (
            assessment["evidence_role"] == OCR_EVIDENCE_ROLE
            and assessment["citable"] is False
            and assessment["requires_evidence_read"] is True
        ),
    }
    if visual is not None:
        expected_grid = case["expected_grid"]
        checks["grid_shape"] = (
            visual["rows"] == int(expected_grid["rows"])
            and visual["columns"] == int(expected_grid["columns"])
            and visual["cell_count"] == int(expected_grid["cells"])
        )
    return {
        "id": case["id"],
        "kind": case["kind"],
        "texts": observation["texts"],
        "scores": [round(value, 6) for value in observation["scores"]],
        "box_count": len(observation["boxes"]),
        "box_integrity": _box_integrity(
            observation["boxes"],
            width=int(canvas["width"]),
            height=int(canvas["height"]),
        ),
        "fragment_recall": _fragment_recall(
            observation["texts"], case["expected_fragments"]
        ),
        "quality": quality,
        "assessment": assessment,
        "visual": visual,
        "latency_seconds": elapsed,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _evaluate_routing(dataset: Mapping[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in dataset["routing_cases"]:
        route = select_extraction_route(
            native_text=case["native_text"],
            ocr_available=bool(case["ocr_available"]),
        )
        cases.append(
            {
                "id": case["id"],
                "route": route,
                "ocr_invoked": route == "ocr",
                "passed": route == case["expected_route"],
            }
        )
    degradation = guarded_ocr_result(
        lambda: (_ for _ in ()).throw(OcrProviderUnavailable("synthetic unavailable"))
    )
    return {
        "cases": cases,
        "native_first": all(
            case["route"] == "native"
            for case in cases
            if case["id"] == "R01_native_text_priority"
        ),
        "safe_degradation": degradation,
    }


def _runtime_versions() -> dict[str, Any]:
    packages = ("numpy", "torch", "paddlepaddle", "paddleocr", "paddlex", "Pillow")
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    import cv2

    versions["opencv"] = str(cv2.__version__)
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "device": "cpu",
        "packages": versions,
    }


def evaluate(
    dataset: Mapping[str, Any],
    *,
    model_root: Path,
    font_path: Path,
) -> dict[str, Any]:
    resolved_root = model_root.resolve()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(resolved_root)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    detection_dir, detection_snapshot = _model_snapshot(
        resolved_root / "official_models" / DETECTION_MODEL_NAME,
        expected_name=DETECTION_MODEL_NAME,
    )
    recognition_dir, recognition_snapshot = _model_snapshot(
        resolved_root / "official_models" / RECOGNITION_MODEL_NAME,
        expected_name=RECOGNITION_MODEL_NAME,
    )
    resolved_font = font_path.resolve()
    if not resolved_font.is_file():
        raise V6052EvaluationError("synthetic page font is unavailable")
    provider = LocalPaddleOcrProvider(
        detection_dir=detection_dir,
        recognition_dir=recognition_dir,
    )
    with tempfile.TemporaryDirectory(prefix="bid-pa-v6052-") as raw_directory:
        directory = Path(raw_directory)
        image_cases = [
            _evaluate_image_case(
                case,
                provider=provider,
                directory=directory,
                font_path=resolved_font,
            )
            for case in dataset["image_cases"]
        ]
    routing = _evaluate_routing(dataset)
    thresholds = dataset["thresholds"]
    by_id = {case["id"]: case for case in image_cases}
    clear = by_id["O01_clear_scanned_text"]
    table = by_id["O02_clean_table_grid"]
    low_quality = by_id["O03_low_quality_scan"]
    blank = by_id["O04_blank_page"]
    latencies = [float(case["latency_seconds"]) for case in image_cases]
    visual = table["visual"] or {}
    degradation = routing["safe_degradation"]
    checks = {
        "clear_fragment_recall": clear["fragment_recall"]
        >= float(thresholds["clear_fragment_recall_min"]),
        "table_fragment_recall": table["fragment_recall"]
        >= float(thresholds["table_fragment_recall_min"]),
        "clear_confidence": clear["assessment"]["mean_confidence"]
        >= float(thresholds["clear_mean_confidence_min"]),
        "box_integrity": min(case["box_integrity"] for case in image_cases)
        >= float(thresholds["box_integrity_min"]),
        "table_grid": float(bool(table["checks"].get("grid_shape")))
        >= float(thresholds["table_grid_accuracy_min"]),
        "table_cell_mapping": float(visual.get("cell_fragment_recall", 0.0))
        >= float(thresholds["table_cell_fragment_recall_min"]),
        "low_quality_is_reviewed": low_quality["assessment"]["review_required"] is True,
        "blank_has_no_false_positive": blank["box_count"]
        <= int(thresholds["blank_false_positive_count_max"]),
        "native_first_and_routing": (
            routing["native_first"] and all(case["passed"] for case in routing["cases"])
        ),
        "safe_degradation": (
            degradation["status"] == "degraded"
            and degradation["texts"] == []
            and degradation["boxes"] == []
            and degradation["citable"] is False
            and degradation["review_required"] is True
        ),
        "non_citable_ocr": all(
            case["assessment"]["citable"] is False
            and case["assessment"]["evidence_role"] == OCR_EVIDENCE_ROLE
            for case in image_cases
        ),
        "case_contracts": all(case["passed"] for case in image_cases),
        "latency": max(latencies) <= float(thresholds["ocr_case_seconds_max"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": dataset["dataset_kind"],
        "runtime": _runtime_versions(),
        "models": {
            "detection": detection_snapshot,
            "recognition": recognition_snapshot,
        },
        "synthetic_font": {
            "filename": resolved_font.name,
            "bytes": resolved_font.stat().st_size,
            "sha256": _file_sha256(resolved_font),
        },
        "execution_scope": {
            "real_pdf_executed": False,
            "external_network_executed": False,
            "external_mcp_executed": False,
            "generative_vision_model_executed": False,
            "database_executed": False,
            "production_vector_store_executed": False,
            "local_ocr": "PaddleOCR",
            "local_visual_algorithm": "opencv_morphological_grid",
        },
        "metrics": {
            "clear_fragment_recall": clear["fragment_recall"],
            "table_fragment_recall": table["fragment_recall"],
            "clear_mean_confidence": clear["assessment"]["mean_confidence"],
            "minimum_box_integrity": min(case["box_integrity"] for case in image_cases),
            "table_grid_accuracy": float(bool(table["checks"].get("grid_shape"))),
            "table_cell_fragment_recall": visual.get("cell_fragment_recall", 0.0),
            "blank_false_positive_count": blank["box_count"],
            "review_gate_accuracy": _mean(
                [float(case["checks"]["review_gate"]) for case in image_cases]
            ),
        },
        "latency_seconds": {
            "case_mean": _mean(latencies),
            "case_p95": _percentile_nearest_rank(latencies, 0.95),
            "case_max": round(max(latencies), 6),
        },
        "routing": routing,
        "image_cases": image_cases,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real local PaddleOCR/OpenCV V605-2 synthetic evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument(
        "--font-path",
        type=Path,
        default=Path("C:/Windows/Fonts/msyh.ttc"),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report = evaluate(
        load_dataset(args.dataset),
        model_root=args.model_root,
        font_path=args.font_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_PATH",
    "OCR_EVIDENCE_ROLE",
    "OcrProviderUnavailable",
    "SCHEMA_VERSION",
    "V6052EvaluationError",
    "assess_ocr_observation",
    "detect_table_grid",
    "evaluate",
    "guarded_ocr_result",
    "load_dataset",
    "select_extraction_route",
]
