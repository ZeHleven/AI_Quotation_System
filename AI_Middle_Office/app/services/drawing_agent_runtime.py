from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


STATE_FILE_NAME = "run_state.json"
EVENTS_FILE_NAME = "events.jsonl"
RUN_ROOT_DIR_NAME = "agent_runs"
SCHEMA_VERSION = "drawing_agent_run_state_v1"

RUN_STATUSES = {
    "created",
    "running",
    "completed",
    "completed_with_review",
    "failed_with_report",
    "failed",
}

STAGE_PROGRESS = {
    "created": 0,
    "rendering_pdf": 5,
    "detecting_layout": 15,
    "planning_views": 25,
    "running_ocr": 35,
    "running_vision": 50,
    "zooming_unclear_regions": 65,
    "building_context": 75,
    "generating_items": 82,
    "mapping_standards": 90,
    "quality_review": 96,
    "exporting": 98,
    "completed": 100,
    "failed": 100,
}

STAGE_MESSAGES = {
    "created": "Drawing agent run created",
    "rendering_pdf": "Rendering PDF pages",
    "detecting_layout": "Detecting drawing layout and view frames",
    "planning_views": "Planning whole-page, context, and local views",
    "running_ocr": "Reading text evidence",
    "running_vision": "Extracting visual drawing evidence",
    "zooming_unclear_regions": "Checking unclear regions",
    "building_context": "Building global drawing context",
    "generating_items": "Generating candidate quantity items",
    "mapping_standards": "Mapping items to standard library",
    "quality_review": "Reviewing quality and manual-check risks",
    "exporting": "Writing output files and reports",
    "completed": "Drawing agent run completed",
    "failed": "Drawing agent run failed",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_pdf_agent_run_tracker(
    *,
    output_dir: str | Path,
    run_id: str,
    input_dir: str | Path | None = None,
    provider: str | None = None,
) -> "DrawingAgentRunTracker":
    tracker = DrawingAgentRunTracker(
        run_id=run_id,
        run_dir=Path(output_dir) / RUN_ROOT_DIR_NAME / run_id,
        input_dir=Path(input_dir) if input_dir is not None else None,
        provider=provider,
    )
    tracker.initialize()
    return tracker


def read_drawing_agent_run_state(output_dir: str | Path, run_id: str) -> dict[str, Any] | None:
    run_dir = _resolve_run_dir(Path(output_dir), run_id)
    state_path = run_dir / STATE_FILE_NAME
    if not state_path.exists() or not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_latest_drawing_agent_run_state(output_dir: str | Path) -> dict[str, Any] | None:
    root = Path(output_dir) / RUN_ROOT_DIR_NAME
    if not root.exists() or not root.is_dir():
        return None
    state_paths = sorted(
        (path / STATE_FILE_NAME for path in root.iterdir() if path.is_dir() and (path / STATE_FILE_NAME).is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not state_paths:
        return None
    try:
        data = json.loads(state_paths[0].read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_drawing_agent_run_events(output_dir: str | Path, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    run_dir = _resolve_run_dir(Path(output_dir), run_id)
    events_path = run_dir / EVENTS_FILE_NAME
    if not events_path.exists() or not events_path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events[-max(1, int(limit or 1)) :]


class DrawingAgentRunTracker:
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        input_dir: Path | None = None,
        provider: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.input_dir = input_dir
        self.provider = provider or ""
        self.state_path = self.run_dir / STATE_FILE_NAME
        self.events_path = self.run_dir / EVENTS_FILE_NAME
        self._state: dict[str, Any] = {}

    def initialize(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for dirname in ("input", "pages", "crops", "ocr", "vision", "context", "items", "reports"):
            (self.run_dir / dirname).mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        self._state = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "created",
            "stage": "created",
            "progress": STAGE_PROGRESS["created"],
            "current_message": STAGE_MESSAGES["created"],
            "created_at": now,
            "updated_at": now,
            "input_dir": str(self.input_dir.resolve()) if self.input_dir else "",
            "provider": self.provider,
            "workspace_dir": str(self.run_dir.resolve()),
            "artifact_dirs": {
                "input": str((self.run_dir / "input").resolve()),
                "pages": str((self.run_dir / "pages").resolve()),
                "crops": str((self.run_dir / "crops").resolve()),
                "ocr": str((self.run_dir / "ocr").resolve()),
                "vision": str((self.run_dir / "vision").resolve()),
                "context": str((self.run_dir / "context").resolve()),
                "items": str((self.run_dir / "items").resolve()),
                "reports": str((self.run_dir / "reports").resolve()),
            },
            "summary": {},
            "outputs": {},
            "warnings": [],
            "errors": [],
            "events_count": 0,
        }
        self._write_state()
        self.emit("created", stage="created", progress=0, message=STAGE_MESSAGES["created"])

    def bind_artifact_dirs(self, **paths: Path | str | None) -> None:
        artifact_dirs = dict(self._state.get("artifact_dirs") or {})
        for key, value in paths.items():
            if value is None:
                continue
            artifact_dirs[str(key)] = str(Path(value).resolve())
        self._state["artifact_dirs"] = artifact_dirs
        self._state["updated_at"] = utc_now_iso()
        self._write_state()

    def update(
        self,
        stage: str,
        *,
        progress: int | None = None,
        message: str | None = None,
        detail: Mapping[str, Any] | None = None,
        status: str = "running",
    ) -> None:
        safe_progress = _safe_progress(progress if progress is not None else STAGE_PROGRESS.get(stage, 0))
        self._state.update(
            {
                "status": status if status in RUN_STATUSES else "running",
                "stage": stage,
                "progress": safe_progress,
                "current_message": message or STAGE_MESSAGES.get(stage, stage),
                "updated_at": utc_now_iso(),
            }
        )
        if detail:
            self._state["latest_detail"] = _json_safe(dict(detail))
        self._write_state()
        self.emit("stage_update", stage=stage, progress=safe_progress, message=self._state["current_message"], detail=detail)

    def complete(
        self,
        *,
        status: str = "completed",
        summary: Mapping[str, Any] | None = None,
        outputs: Mapping[str, Any] | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        final_status = status if status in {"completed", "completed_with_review"} else "completed"
        self._state.update(
            {
                "status": final_status,
                "stage": "completed",
                "progress": 100,
                "current_message": STAGE_MESSAGES["completed"],
                "updated_at": utc_now_iso(),
                "completed_at": utc_now_iso(),
                "summary": _json_safe(dict(summary or {})),
                "outputs": _json_safe(dict(outputs or {})),
                "issues": _json_safe(list(issues or [])),
            }
        )
        self._write_state()
        self.emit("completed", stage="completed", progress=100, message=STAGE_MESSAGES["completed"], detail={"status": final_status})

    def fail(
        self,
        *,
        stage: str | None = None,
        error_code: str,
        message: str,
        detail: Mapping[str, Any] | None = None,
        with_report: bool = True,
    ) -> None:
        failed_stage = stage or str(self._state.get("stage") or "failed")
        error = {
            "code": error_code,
            "message": message,
            "stage": failed_stage,
            "detail": _json_safe(dict(detail or {})),
            "created_at": utc_now_iso(),
        }
        errors = list(self._state.get("errors") or [])
        errors.append(error)
        self._state.update(
            {
                "status": "failed_with_report" if with_report else "failed",
                "stage": failed_stage,
                "progress": _safe_progress(self._state.get("progress") or STAGE_PROGRESS.get(failed_stage, 100)),
                "current_message": message,
                "updated_at": utc_now_iso(),
                "failed_at": utc_now_iso(),
                "errors": errors,
            }
        )
        self._write_state()
        self.emit("failed", stage=failed_stage, progress=self._state["progress"], message=message, detail=error)

    def mark_report_failure(
        self,
        *,
        error_code: str,
        message: str,
        report: Mapping[str, Any],
    ) -> None:
        self._state["summary"] = _json_safe(dict(report.get("summary") or {}))
        self._state["outputs"] = _json_safe(dict(report.get("outputs") or {}))
        self._state["issues"] = _json_safe(list(report.get("issues") or []))
        self.fail(
            stage=str(self._state.get("stage") or "quality_review"),
            error_code=error_code,
            message=message,
            detail={"summary": report.get("summary") or {}, "issues": report.get("issues") or []},
            with_report=True,
        )

    def snapshot(self) -> dict[str, Any]:
        return _json_safe(dict(self._state))

    def emit(
        self,
        event_type: str,
        *,
        stage: str,
        progress: int,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        event = {
            "schema_version": "drawing_agent_event_v1",
            "run_id": self.run_id,
            "event_type": event_type,
            "stage": stage,
            "progress": _safe_progress(progress),
            "message": message,
            "detail": _json_safe(dict(detail or {})),
            "created_at": utc_now_iso(),
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._state["events_count"] = int(self._state.get("events_count") or 0) + 1
        self._write_state()

    def _write_state(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(_json_safe(self._state), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)


def pdf_agent_completion_status(report: Mapping[str, Any]) -> str:
    if not bool(report.get("ok")):
        return "failed_with_report"
    issues = list(report.get("issues") or [])
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    manual_review_count = _int(summary.get("itemizability_manual_review_count")) if summary else 0
    if issues or manual_review_count > 0:
        return "completed_with_review"
    return "completed"


def _resolve_run_dir(output_dir: Path, run_id: str) -> Path:
    if not run_id or any(part in {"", ".", ".."} for part in Path(run_id).parts) or Path(run_id).is_absolute():
        raise ValueError("invalid drawing agent run id")
    root = (output_dir / RUN_ROOT_DIR_NAME).resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise ValueError("invalid drawing agent run id")
    return run_dir


def _safe_progress(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
