from __future__ import annotations

import asyncio
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

from app.core.config import settings
from app.services.model_gateway import post_json_via_gateway


PROMPT_VERSION = "drawing_material_semantic_extractor_v2"

CODE_RE = re.compile(r"(?<![A-Z])(?:CT|MT|ST|PT|PB|W)\s*-?\s*\d{1,3}(?![A-Z0-9])", re.IGNORECASE)

ITEM_TYPE_LABELS = {
    "material_name": "材料名称",
    "component": "构件/成品项",
    "construction_method": "施工做法",
    "finish_process": "饰面处理",
    "demolition_scope": "拆除范围",
    "material_code": "材料代号",
    "equipment_or_fixture": "设备/洁具/灯具",
    "non_quote_reference": "非报价参考",
    "uncertain": "不确定",
    "material_name_or_spec": "材料名称",
    "component_or_fixture": "构件/成品项",
    "construction_or_assembly": "施工做法",
    "finish_or_coating": "饰面处理",
}

ITEM_TYPE_ALIASES = {
    "设备/灯具": "设备/洁具/灯具",
    "灯具/设备": "设备/洁具/灯具",
    "设备/洁具": "设备/洁具/灯具",
    "设备": "设备/洁具/灯具",
    "灯具": "设备/洁具/灯具",
    "洁具": "设备/洁具/灯具",
}

SYSTEM_PROMPT = """你是装修/幕墙/机电施工图报价材料语义整理器。

你的输入不是图片，而是 OCR 从图纸中读出的文字行。你的任务是基于 OCR 原文和位置信息，判断哪些文字属于报价相关的材料、构件、做法、拆除范围、设备、材料代号或材料表信息，并整理为结构化证据。

核心原则：
1. 不要靠固定关键词机械判断，要用装修工程报价语义判断。
2. “材料”不只包括狭义材料名，也包括报价相关构件、基层做法、面层做法、拆除对象、成品构件、设备、洁具、灯具、管线、材料代号。
3. 不要把图名、项目名称、公司名称、日期、轴号、纯尺寸、纯方向符号误判为材料。
4. 看到残缺 OCR 文本时，可以输出候选，但必须降低 confidence 并标记 needs_manual_review=true。
5. 不要编造 OCR 中不存在的材料、规格、工程量或国标编码。
6. 不要计算工程量。
7. 同一批里重复出现的同一材料/构件可以分别保留 evidence_refs，但 normalized_name 应保持一致。

请识别的项目类型只允许使用以下中文枚举：
- 材料名称：材料名称或规格，如 600X1200白色墙面砖、15厘阻燃板基层、白色人造石、600*600铝扣板
- 构件/成品项：构件/成品项，如 原建筑窗、成品实木门及门套、白色人造石窗台板、不锈钢玻璃地弹门
- 施工做法：施工做法/基层/组合构造，如 轻钢龙骨石膏板隔墙刷白色无机涂料三度、0.8厚铝扣板吊顶
- 饰面处理：饰面处理/功能处理，如 墙砖美缝处理、防水处理、刷无机涂料
- 拆除范围：拆除范围，如 拆除实木门、拆除墙面墙纸、拆除天花条形扣板
- 材料代号：材料或饰面代号，如 CT 01、MT 02、ST02、PB-01 PT-03、W1
- 设备/洁具/灯具：灯具、洁具、厨房设备、阀门、风口、五金等
- 非报价参考：与材料相关但通常不直接报价的图名/标题/说明索引
- 不确定：OCR 残缺或语义不足，需要人工确认

输出必须是严格 JSON，不要 Markdown，不要解释。格式：
{
  "items": [
    {
      "normalized_name": "归并后的材料/构件/做法名称",
      "item_type": "材料名称|构件/成品项|施工做法|饰面处理|拆除范围|材料代号|设备/洁具/灯具|非报价参考|不确定",
      "quote_category": "墙面砖|地面砖|吊顶|隔墙|基层板|木饰面|石材|玻璃|门窗|拆除|机电|待映射|其他",
      "source_text": "直接来自 OCR 的原文",
      "evidence_refs": ["T00001"],
      "material_codes": ["CT-01"],
      "spec_or_method": "规格、材质、基层、面层、做法或安装方式",
      "space_or_location": "空间或部位，不确定则为空字符串",
      "suggested_unit": "m2|m|樘|套|个|项|台|n/a|",
      "is_quote_relevant": true,
      "confidence": 0.0,
      "needs_manual_review": true,
      "reason": "一句话说明为什么属于或不属于报价相关材料/构件/做法"
    }
  ]
}
"""

MERGE_PROMPT = """你是报价材料候选归并器。

输入是一批已经由 LLM 从 OCR 文本中识别出的材料/构件/做法候选。请把同义、残缺、重复、包含关系明显的候选归并为报价复核清单。

归并要求：
1. 不要丢弃报价相关项。宁可保留为 needs_manual_review，也不要直接删除。
2. 不同材料、不同规格、不同施工方式、不同构件类型不要强行合并。
3. 材料本体和应用构件可同时保留。例如“白色人造石”和“白色人造石窗台板”可以分别保留。
4. 材料代号如 CT/MT/ST/PT/PB/W 统一归到“材料/饰面代号（需映射材料表）”，保留 observed_codes。
5. 拆除项保留为“拆除范围”，不要并入新做材料。
6. item_type 必须使用中文枚举，不要输出英文类型。
7. 不要计算工程量，不要生成国标编码。

输出严格 JSON：
{
  "merged_items": [
    {
      "canonical_name": "归并后的报价候选名称",
      "item_type": "材料名称|构件/成品项|施工做法|饰面处理|拆除范围|材料代号|设备/洁具/灯具|不确定",
      "quote_category": "分类",
      "suggested_unit": "m2|m|樘|套|个|项|台|n/a|",
      "source_names": ["归并前名称"],
      "source_texts": ["OCR原文"],
      "evidence_refs": ["T00001"],
      "observed_codes": ["CT-01"],
      "confidence": 0.0,
      "needs_manual_review": true,
      "review_note": "需要人工复核的原因；若较确定也简要说明依据"
    }
  ]
}
"""


def load_ocr_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "text_id": row.get("text_id") or "",
                    "page": row.get("page") or "",
                    "text": text,
                    "confidence": _float_or_none(row.get("confidence")),
                    "bbox_ratio": _json_or_raw(row.get("bbox_ratio") or ""),
                    "tile_id": row.get("tile_id") or "",
                    "snippet_id": row.get("snippet_id") or "",
                }
            )
        return rows


def build_text_batches(
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int = 180,
    max_chars: int = 14000,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for row in rows:
        compact = {
            "text_id": row.get("text_id"),
            "page": row.get("page"),
            "text": row.get("text"),
            "confidence": row.get("confidence"),
            "bbox_ratio": row.get("bbox_ratio"),
            "tile_id": row.get("tile_id"),
        }
        row_chars = len(json.dumps(compact, ensure_ascii=False))
        if current and (len(current) >= max_rows or current_chars + row_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(compact)
        current_chars += row_chars
    if current:
        batches.append(current)
    return batches


def build_batch_payload(
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
) -> dict[str, Any]:
    return {
        "prompt_version": PROMPT_VERSION,
        "task": "classify_ocr_text_into_quote_material_candidates",
        "batch_index": batch_index,
        "batch_count": batch_count,
        "input_rows": batch,
        "output_contract": {
            "items": [
                {
                    "normalized_name": "string",
                    "item_type": "材料名称|构件/成品项|施工做法|饰面处理|拆除范围|材料代号|设备/洁具/灯具|非报价参考|不确定",
                    "quote_category": "string",
                    "source_text": "string",
                    "evidence_refs": ["text_id"],
                    "material_codes": ["string"],
                    "spec_or_method": "string",
                    "space_or_location": "string",
                    "suggested_unit": "string",
                    "is_quote_relevant": True,
                    "confidence": 0.0,
                    "needs_manual_review": True,
                    "reason": "string",
                }
            ]
        },
    }


async def classify_batch_with_llm(
    batch_payload: dict[str, Any],
    *,
    provider: str = "deepseek",
    model: str | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    provider_config = _provider_config(provider, model)
    payload = {
        "model": provider_config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(batch_payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    response = await post_json_via_gateway(
        provider=provider_config["provider"],
        model=provider_config["model"],
        endpoint_type="drawing_material_semantic_extract",
        url=provider_config["url"],
        json_payload=payload,
        headers=provider_config["headers"],
        timeout=provider_config["timeout"],
        username=username,
        trace_id=trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _extract_json_object(content)
    parsed["raw_content"] = content
    return parsed


async def merge_items_with_llm(
    items: list[dict[str, Any]],
    *,
    provider: str = "deepseek",
    model: str | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    provider_config = _provider_config(provider, model)
    payload = {
        "model": provider_config["model"],
        "messages": [
            {"role": "system", "content": MERGE_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt_version": PROMPT_VERSION,
                        "task": "merge_quote_material_candidates",
                        "candidate_count": len(items),
                        "items": items,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    response = await post_json_via_gateway(
        provider=provider_config["provider"],
        model=provider_config["model"],
        endpoint_type="drawing_material_semantic_merge",
        url=provider_config["url"],
        json_payload=payload,
        headers=provider_config["headers"],
        timeout=provider_config["timeout"],
        username=username,
        trace_id=trace_id,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _extract_json_object(content)
    parsed["raw_content"] = content
    return parsed


async def run_llm_material_semantic_extraction(
    csv_path: Path,
    output_dir: Path,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    max_rows_per_batch: int = 180,
    max_chars_per_batch: int = 14000,
    execute: bool = False,
    drop_obvious_noise: bool = True,
    trace_id: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = load_ocr_rows(csv_path)
    rows = [row for row in raw_rows if not _is_obvious_ocr_noise(row)] if drop_obvious_noise else raw_rows
    batches = build_text_batches(rows, max_rows=max_rows_per_batch, max_chars=max_chars_per_batch)
    batch_payloads = [
        build_batch_payload(batch, batch_index=index + 1, batch_count=len(batches))
        for index, batch in enumerate(batches)
    ]

    preview = {
        "prompt_version": PROMPT_VERSION,
        "mode": "execute" if execute else "dry_run",
        "provider": provider,
        "model": model or _provider_config(provider, model)["model"],
        "source_csv": str(csv_path),
        "raw_row_count": len(raw_rows),
        "row_count": len(rows),
        "drop_obvious_noise": drop_obvious_noise,
        "batch_count": len(batch_payloads),
        "batch_payloads": batch_payloads,
    }
    preview_path = output_dir / "llm_material_semantic_batches_preview.json"
    preview_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")

    prompt_path = output_dir / "llm_material_semantic_prompt.md"
    prompt_path.write_text(
        "# LLM 材料语义整理提示词\n\n"
        "## 分类提示词\n\n"
        f"```text\n{SYSTEM_PROMPT}\n```\n\n"
        "## 归并提示词\n\n"
        f"```text\n{MERGE_PROMPT}\n```\n",
        encoding="utf-8",
    )

    if not execute:
        return {
            "mode": "dry_run",
            "summary": {
                "raw_row_count": len(raw_rows),
                "row_count": len(rows),
                "batch_count": len(batch_payloads),
                "provider": provider,
                "model": model or _provider_config(provider, model)["model"],
            },
            "outputs": {
                "preview_json": str(preview_path),
                "prompt_md": str(prompt_path),
            },
        }

    batch_results: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    batch_dir = output_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for payload in batch_payloads:
        batch_index = int(payload["batch_index"])
        batch_result_path = batch_dir / f"batch_{batch_index:03d}.json"
        if batch_result_path.exists():
            saved = json.loads(batch_result_path.read_text(encoding="utf-8"))
            items = _normalize_classified_items(saved.get("items"), payload)
            batch_results.append({"batch_index": batch_index, "items": items, "raw_content": saved.get("raw_content", "")})
            all_items.extend(items)
            continue

        result: dict[str, Any] | None = None
        last_error = ""
        for attempt in range(1, 3):
            try:
                result = await classify_batch_with_llm(payload, provider=provider, model=model, trace_id=trace_id)
                break
            except Exception as exc:  # pragma: no cover - network retry guard
                last_error = str(exc)
                error_path = batch_dir / f"batch_{batch_index:03d}_attempt_{attempt}_error.json"
                error_path.write_text(
                    json.dumps(
                        {
                            "batch_index": batch_index,
                            "attempt": attempt,
                            "error": last_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                if attempt < 2:
                    await asyncio.sleep(2 * attempt)
        if result is None:
            raise RuntimeError(f"batch_{batch_index:03d}_failed:{last_error[:300]}")
        items = _normalize_classified_items(result.get("items"), payload)
        batch_result_path.write_text(
            json.dumps(
                {"batch_index": batch_index, "items": items, "raw_content": result.get("raw_content", "")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        partial_path = output_dir / "llm_material_semantic_classified_items.partial.json"
        partial_path.write_text(json.dumps({"items": all_items + items}, ensure_ascii=False, indent=2), encoding="utf-8")
        batch_results.append({"batch_index": batch_index, "items": items, "raw_content": result.get("raw_content", "")})
        all_items.extend(items)

    classified_path = output_dir / "llm_material_semantic_classified_items.json"
    classified_path.write_text(json.dumps({"items": all_items, "batches": batch_results}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_classified_items_csv(output_dir / "llm_material_semantic_classified_items.csv", all_items)

    merge_result = await merge_items_with_llm(all_items, provider=provider, model=model, trace_id=trace_id)
    merged_items = _normalize_merged_items(merge_result.get("merged_items"), all_items)
    merged_path = output_dir / "llm_merged_quote_material_list.json"
    merged_path.write_text(json.dumps({"merged_items": merged_items, "raw_content": merge_result.get("raw_content", "")}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_merged_items_csv(output_dir / "llm_merged_quote_material_list.csv", merged_items)
    review_path = output_dir / "llm_material_semantic_review.md"
    review_path.write_text(build_review_markdown(csv_path, all_items, merged_items), encoding="utf-8")

    return {
        "mode": "execute",
        "summary": {
            "raw_row_count": len(raw_rows),
            "row_count": len(rows),
            "batch_count": len(batch_payloads),
            "classified_item_count": len(all_items),
            "merged_item_count": len(merged_items),
            "provider": provider,
            "model": model or _provider_config(provider, model)["model"],
        },
        "outputs": {
            "preview_json": str(preview_path),
            "prompt_md": str(prompt_path),
            "classified_json": str(classified_path),
            "classified_csv": str(output_dir / "llm_material_semantic_classified_items.csv"),
            "merged_json": str(merged_path),
            "merged_csv": str(output_dir / "llm_merged_quote_material_list.csv"),
            "review_md": str(review_path),
        },
    }


def write_classified_items_csv(path: Path, items: list[dict[str, Any]]) -> None:
    fieldnames = [
        "normalized_name",
        "item_type",
        "quote_category",
        "source_text",
        "evidence_refs",
        "material_codes",
        "spec_or_method",
        "space_or_location",
        "suggested_unit",
        "is_quote_relevant",
        "confidence",
        "needs_manual_review",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({key: _csv_cell(item.get(key)) for key in fieldnames})


def write_merged_items_csv(path: Path, items: list[dict[str, Any]]) -> None:
    fieldnames = [
        "canonical_name",
        "item_type",
        "quote_category",
        "suggested_unit",
        "source_names",
        "source_texts",
        "evidence_refs",
        "observed_codes",
        "confidence",
        "needs_manual_review",
        "review_note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({key: _csv_cell(item.get(key)) for key in fieldnames})


def build_review_markdown(csv_path: Path, classified_items: list[dict[str, Any]], merged_items: list[dict[str, Any]]) -> str:
    lines = [
        "# LLM 材料语义整理结果",
        "",
        f"来源：`{csv_path}`",
        "",
        f"- LLM 分类候选：{len(classified_items)}",
        f"- LLM 归并报价候选：{len(merged_items)}",
        "",
        "## 归并后的报价候选",
        "",
        "| 序号 | 名称 | 类型 | 分类 | 建议单位 | 置信度 | 需复核 | 来源文本 |",
        "|---:|---|---|---|---|---:|---|---|",
    ]
    for index, item in enumerate(merged_items, 1):
        source_texts = _truncate(_csv_cell(item.get("source_texts")), 180).replace("|", "\\|")
        lines.append(
            f"| {index} | {item.get('canonical_name', '')} | {item.get('item_type', '')} | "
            f"{item.get('quote_category', '')} | {item.get('suggested_unit', '')} | "
            f"{_float_or_none(item.get('confidence')) or 0:.2f} | {item.get('needs_manual_review', True)} | {source_texts} |"
        )
    lines.append("")
    return "\n".join(lines)


def _provider_config(provider: str, model: str | None = None) -> dict[str, Any]:
    normalized = (provider or "deepseek").strip().lower()
    if normalized == "deepseek":
        api_key = settings.deepseek_api_key.strip()
        selected_model = (model or settings.deepseek_model or "deepseek-chat").strip()
        if selected_model.lower() in {"deepseek-v4-pro", "deepseek-v4-flash"}:
            selected_model = selected_model.lower()
        return {
            "provider": "deepseek",
            "model": selected_model,
            "url": settings.deepseek_chat_url,
            "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "timeout": max(settings.agent_llm_timeout_seconds, 180),
        }
    if normalized in {"dashscope", "qwen"}:
        api_key = settings.dashscope_api_key.strip()
        selected_model = (model or settings.dashscope_bill_summary_model or "qwen-plus").strip()
        base_url = settings.dashscope_base_url.rstrip("/")
        return {
            "provider": "dashscope",
            "model": selected_model,
            "url": f"{base_url}/chat/completions",
            "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "timeout": max(settings.dashscope_timeout_seconds, 180),
        }
    raise ValueError(f"unsupported_material_llm_provider:{provider}")


def _is_obvious_ocr_noise(row: dict[str, Any]) -> bool:
    text = _text(row.get("text"))
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if CODE_RE.search(text):
        return False
    if len(compact) <= 1:
        return True
    confidence = _float_or_none(row.get("confidence"))
    if confidence is not None and confidence < 0.5 and len(compact) <= 5:
        return True
    if re.fullmatch(r"[\d.,:：/\\\-+*()（）\[\]{}|_]+", compact):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}", compact):
        return True
    upper = text.upper().strip()
    english_non_quote_headers = {
        "PROJECT NAME",
        "PROJECT NO.",
        "DRAWING NO.",
        "RELEASE STAMP",
        "DISCIPLINE",
        "VERSION",
        "DATE",
        "SIZE",
        "STAGE",
        "ELEVATION",
        "SECTION",
    }
    if upper in english_non_quote_headers:
        return True
    if re.fullmatch(r"SCALE\s*\d*\s*[:：]\s*\d+", upper):
        return True
    if re.fullmatch(r"\d{4}[./-]\d{1,2}", compact):
        return True
    return False


def _extract_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise ValueError("EMPTY_LLM_CONTENT")
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM_JSON_NOT_OBJECT")
    return value


def _normalize_classified_items(value: Any, batch_payload: dict[str, Any]) -> list[dict[str, Any]]:
    source_ids = {str(row.get("text_id")) for row in batch_payload.get("input_rows") or [] if row.get("text_id")}
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_refs = _string_list(item.get("evidence_refs"))
        evidence_refs = [ref for ref in evidence_refs if not source_ids or ref in source_ids] or evidence_refs
        result.append(
            {
                "normalized_name": _text(item.get("normalized_name")),
                "item_type": _normalize_item_type(item.get("item_type")),
                "quote_category": _text(item.get("quote_category")),
                "source_text": _text(item.get("source_text")),
                "evidence_refs": evidence_refs,
                "material_codes": _string_list(item.get("material_codes")),
                "spec_or_method": _text(item.get("spec_or_method")),
                "space_or_location": _text(item.get("space_or_location")),
                "suggested_unit": _text(item.get("suggested_unit")),
                "is_quote_relevant": bool(item.get("is_quote_relevant", True)),
                "confidence": _bounded_float(item.get("confidence")),
                "needs_manual_review": bool(item.get("needs_manual_review", True)),
                "reason": _text(item.get("reason")),
            }
        )
    return result


def _normalize_merged_items(value: Any, classified_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "canonical_name": _text(item.get("canonical_name")),
                "item_type": _normalize_item_type(item.get("item_type")),
                "quote_category": _text(item.get("quote_category")),
                "suggested_unit": _text(item.get("suggested_unit")),
                "source_names": _string_list(item.get("source_names")),
                "source_texts": _string_list(item.get("source_texts")),
                "evidence_refs": _string_list(item.get("evidence_refs")),
                "observed_codes": _string_list(item.get("observed_codes")),
                "confidence": _bounded_float(item.get("confidence")),
                "needs_manual_review": bool(item.get("needs_manual_review", True)),
                "review_note": _text(item.get("review_note")),
            }
        )
    return result


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_float(value: Any) -> float:
    parsed = _float_or_none(value)
    if parsed is None:
        return 0.0
    return max(0.0, min(1.0, parsed))


def _json_or_raw(value: str) -> Any:
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_item_type(value: Any) -> str:
    text = _text(value)
    if not text:
        return ITEM_TYPE_LABELS["uncertain"]
    if text in ITEM_TYPE_ALIASES:
        return ITEM_TYPE_ALIASES[text]
    if text in set(ITEM_TYPE_LABELS.values()):
        return text
    normalized = re.sub(r"[\s\-]+", "_", text.strip().lower())
    return ITEM_TYPE_LABELS.get(normalized, text)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _csv_cell(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _text(value)


def _truncate(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[: max_len - 3] + "..."


def run_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_llm_material_semantic_extraction(*args, **kwargs))
