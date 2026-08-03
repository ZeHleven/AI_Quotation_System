from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from app.services.quantity_list_export import QUANTITY_LIST_HEADERS, write_quantity_list_outputs


CODEX_WORKER_SCHEMA_VERSION = "biz2x_codex_worker_result_v1"
VALIDATION_REPORT_NAME = "validation_report.json"
ALLOWED_TOP_LEVEL_STATUSES = {"succeeded", "partial", "needs_manual_review"}
MAPPABLE_ITEMIZABILITY_STATUSES = {"施工项", "安装项", "定制项", "待确认项"}
NON_CONSTRUCTION_STATUS = "非施工项"


class CodexWorkerContractError(ValueError):
    pass


def load_codex_worker_result(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CodexWorkerContractError(f"codex_result.json is not valid JSON: {source}") from exc
    if not isinstance(data, dict):
        raise CodexWorkerContractError("codex_result.json must be a JSON object")
    return data


def run_codex_worker_contract(
    codex_result_path: str | Path,
    output_dir: str | Path,
    *,
    excel_stem: str = "four_field",
) -> dict[str, Any]:
    result = load_codex_worker_result(codex_result_path)
    return write_codex_worker_contract_outputs(result, output_dir, excel_stem=excel_stem)


def write_codex_worker_contract_outputs(
    result: Mapping[str, Any],
    output_dir: str | Path,
    *,
    excel_stem: str = "four_field",
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    validation_report = validate_codex_worker_result(result)
    validation_path = directory / VALIDATION_REPORT_NAME
    validation_path.write_text(json.dumps(validation_report, ensure_ascii=False, indent=2), encoding="utf-8")

    outputs: dict[str, Any] = {
        "ok": bool(validation_report.get("ok")),
        "status": "validated" if validation_report.get("ok") else "validation_failed",
        "validation_report": str(validation_path.resolve()),
        "errors": validation_report.get("errors", []),
        "warnings": validation_report.get("warnings", []),
    }
    if not validation_report.get("ok"):
        return outputs

    quantity_rows = normalize_codex_quantity_rows(result.get("quantity_list_rows") or [])
    quantity_outputs = write_quantity_list_outputs(quantity_rows, directory, stem=excel_stem)
    outputs.update(
        {
            "status": "exported",
            "quantity_list_xlsx": str(Path(quantity_outputs["xlsx"]).resolve()),
            "quantity_list_csv": str(Path(quantity_outputs["csv"]).resolve()),
            "quantity_list_row_count": len(quantity_rows),
        }
    )
    return outputs


def validate_codex_worker_result(result: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not isinstance(result, Mapping):
        return _validation_report(
            errors=[
                _issue("ROOT_NOT_OBJECT", "codex_result.json must be a JSON object", location="$"),
            ],
            warnings=[],
            summary={},
        )

    schema_version = _clean_text(result.get("schema_version"))
    if schema_version != CODEX_WORKER_SCHEMA_VERSION:
        errors.append(
            _issue(
                "INVALID_SCHEMA_VERSION",
                f"schema_version must be {CODEX_WORKER_SCHEMA_VERSION}",
                location="schema_version",
                value=schema_version,
            )
        )

    status = _clean_text(result.get("status"))
    if status and status not in ALLOWED_TOP_LEVEL_STATUSES:
        errors.append(_issue("INVALID_STATUS", "status is not allowed for export", location="status", value=status))

    rows = result.get("quantity_list_rows")
    if not isinstance(rows, list):
        errors.append(_issue("QUANTITY_ROWS_NOT_ARRAY", "quantity_list_rows must be an array", location="quantity_list_rows"))
        rows = []
    if isinstance(rows, list) and not rows:
        errors.append(_issue("NO_QUANTITY_ROWS", "quantity_list_rows must contain at least one row", location="quantity_list_rows"))

    evidence_index = result.get("evidence_index") or []
    if evidence_index and not isinstance(evidence_index, list):
        warnings.append(_issue("EVIDENCE_INDEX_NOT_ARRAY", "evidence_index should be an array", location="evidence_index"))
        evidence_index = []
    evidence_ids = {
        _clean_text(item.get("evidence_id"))
        for item in evidence_index
        if isinstance(item, Mapping) and _clean_text(item.get("evidence_id"))
    }

    filtered_items = result.get("filtered_items") or []
    if filtered_items and not isinstance(filtered_items, list):
        warnings.append(_issue("FILTERED_ITEMS_NOT_ARRAY", "filtered_items should be an array", location="filtered_items"))
        filtered_items = []

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            errors.append(_issue("QUANTITY_ROW_NOT_OBJECT", "quantity_list_rows item must be an object", location=f"quantity_list_rows[{index - 1}]"))
            continue
        row_id = _row_id(row, index)
        _validate_four_fields(row, row_id=row_id, index=index, errors=errors)
        _validate_itemizability(row, row_id=row_id, index=index, errors=errors, warnings=warnings)
        _validate_evidence_refs(row, row_id=row_id, index=index, evidence_ids=evidence_ids, warnings=warnings)

    for index, item in enumerate(filtered_items, start=1):
        if not isinstance(item, Mapping):
            warnings.append(_issue("FILTERED_ITEM_NOT_OBJECT", "filtered_items item should be an object", location=f"filtered_items[{index - 1}]"))
            continue
        status_text = _clean_text(item.get("itemizability_status"))
        if status_text and status_text != NON_CONSTRUCTION_STATUS:
            warnings.append(
                _issue(
                    "FILTERED_ITEM_STATUS_UNEXPECTED",
                    "filtered_items should normally use 非施工项 status",
                    location=f"filtered_items[{index - 1}].itemizability_status",
                    value=status_text,
                )
            )

    summary = {
        "schema_version": schema_version,
        "status": status,
        "quantity_list_row_count": len(rows),
        "filtered_item_count": len(filtered_items),
        "evidence_count": len(evidence_ids),
    }
    return _validation_report(errors=errors, warnings=warnings, summary=summary)


def normalize_codex_quantity_rows(rows: Any) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        normalized.append({header: _clean_text(row.get(header)) for header in QUANTITY_LIST_HEADERS})
    return normalized


def _validate_four_fields(
    row: Mapping[str, Any],
    *,
    row_id: str,
    index: int,
    errors: list[dict[str, Any]],
) -> None:
    for header in QUANTITY_LIST_HEADERS:
        value = _clean_text(row.get(header))
        if not value:
            errors.append(
                _issue(
                    "REQUIRED_FIELD_EMPTY",
                    f"{header}不能为空",
                    row_id=row_id,
                    field=header,
                    location=f"quantity_list_rows[{index - 1}].{header}",
                )
            )


def _validate_itemizability(
    row: Mapping[str, Any],
    *,
    row_id: str,
    index: int,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    status = _clean_text(row.get("itemizability_status"))
    if status == NON_CONSTRUCTION_STATUS:
        errors.append(
            _issue(
                "NON_CONSTRUCTION_IN_QUANTITY_ROWS",
                "非施工项不能进入 quantity_list_rows",
                row_id=row_id,
                field="itemizability_status",
                location=f"quantity_list_rows[{index - 1}].itemizability_status",
                value=status,
            )
        )
        return
    if not status:
        warnings.append(
            _issue(
                "MISSING_ITEMIZABILITY_STATUS",
                "缺少可列项性判断，建议补充施工项/安装项/定制项/待确认项",
                row_id=row_id,
                field="itemizability_status",
                location=f"quantity_list_rows[{index - 1}].itemizability_status",
            )
        )
        return
    if status not in MAPPABLE_ITEMIZABILITY_STATUSES:
        errors.append(
            _issue(
                "INVALID_ITEMIZABILITY_STATUS",
                "itemizability_status is not allowed",
                row_id=row_id,
                field="itemizability_status",
                location=f"quantity_list_rows[{index - 1}].itemizability_status",
                value=status,
            )
        )
    if status == "待确认项" and not _bool(row.get("needs_manual_review"), default=False):
        warnings.append(
            _issue(
                "MANUAL_REVIEW_FLAG_MISSING",
                "待确认项建议设置 needs_manual_review=true",
                row_id=row_id,
                field="needs_manual_review",
                location=f"quantity_list_rows[{index - 1}].needs_manual_review",
            )
        )


def _validate_evidence_refs(
    row: Mapping[str, Any],
    *,
    row_id: str,
    index: int,
    evidence_ids: set[str],
    warnings: list[dict[str, Any]],
) -> None:
    refs = _clean_text_list(row.get("evidence_refs"))
    if not refs:
        warnings.append(
            _issue(
                "MISSING_EVIDENCE_REF",
                "该行缺少证据来源",
                row_id=row_id,
                field="evidence_refs",
                location=f"quantity_list_rows[{index - 1}].evidence_refs",
            )
        )
        return
    if not evidence_ids:
        warnings.append(
            _issue(
                "EVIDENCE_INDEX_EMPTY",
                "该行有 evidence_refs，但 evidence_index 为空，无法交叉校验",
                row_id=row_id,
                field="evidence_refs",
                location=f"quantity_list_rows[{index - 1}].evidence_refs",
                value=refs,
            )
        )
        return
    missing = [ref for ref in refs if ref not in evidence_ids]
    if missing:
        warnings.append(
            _issue(
                "EVIDENCE_REF_NOT_FOUND",
                "evidence_refs 中存在无法在 evidence_index 找到的引用",
                row_id=row_id,
                field="evidence_refs",
                location=f"quantity_list_rows[{index - 1}].evidence_refs",
                value=missing,
            )
        )


def _validation_report(
    *,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "summary": {
            **dict(summary),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def _issue(
    code: str,
    message: str,
    *,
    row_id: str | None = None,
    field: str | None = None,
    location: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    issue = {"code": code, "message": message}
    if row_id:
        issue["row_id"] = row_id
    if field:
        issue["field"] = field
    if location:
        issue["location"] = location
    if value not in (None, ""):
        issue["value"] = value
    return issue


def _row_id(row: Mapping[str, Any], index: int) -> str:
    return _clean_text(row.get("row_id")) or f"CODPDF-ITEM-{index:06d}"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _clean_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _clean_text(value).lower()
    if text in {"1", "true", "yes", "y", "on", "是"}:
        return True
    if text in {"0", "false", "no", "n", "off", "否"}:
        return False
    return default
