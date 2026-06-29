from __future__ import annotations

import asyncio
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlparse

from app.core.config import settings
from app.services.model_gateway import post_json_via_gateway


SCHEMA_VERSION = "drawing_ocr_llm_cabinet_v1"
PROMPT_VERSION = "drawing_ocr_llm_cabinet_v1"

PRIMARY_CATEGORIES = [
    "材料/做法",
    "拆除项",
    "新建/安装项",
    "设备/构件",
    "规格尺寸",
    "工程量/数量线索",
    "图名/标题",
    "公司/人名/图签信息",
    "轴号/索引/编号",
    "噪声",
    "不确定",
]

DEFAULT_CONTEXT_PACKAGE_JSONL = Path(
    "outputs/biz2x_trial/ocr_cabinet/20260625_stage2_context_packages/ocr_context_packages.jsonl"
)

ClassifierCallable = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

DIMENSION_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?\s*(?:x|X|\*|×)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:mm|cm|m|米|毫米|公分))"
)
MATERIAL_CODE_RE = re.compile(r"(?i)\b(?:CT|ST|MT|PT|GL|WD|PB|AL|AP|AT|DN|De|SC|JDG|BV|BYJ|YJV|MR)\s*-?\s*\d{1,4}\b")
PURE_SYMBOL_RE = re.compile(r"^[\s.,:;，。；：`'\"!！?？/\\|_\-+=~^()[\]{}<>《》【】、]+$")
PURE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")

SAMPLE_BUCKET_ORDER = [
    "材料做法线索",
    "拆除线索",
    "新建安装线索",
    "设备构件线索",
    "规格尺寸线索",
    "工程量数字线索",
    "图名标题线索",
    "公司人名图签线索",
    "短文本编码线索",
    "明显噪声线索",
    "其他",
]

SYSTEM_PROMPT = """你是企业装修、机电、给排水施工图 OCR 证据分类助手。

你的任务：对输入中的每一条 OCR 证据逐条分类入柜。

硬原则：
1. 分类对象永远是 current_text 这一条 OCR 证据本身。
2. nearby_evidences 只作为判断上下文，不允许把周边文字合并成新的原文。
3. 不删除任何 OCR 证据。看起来无业务价值的内容也要返回，并归入“噪声”。
4. 不生成最终报价清单，不计算工程量，不归并项目，不编造图纸中没有的文字。
5. 短文本不能一概当作噪声。GL-01、CT-02、300x600、50mm、DN25 可能是有效编号、规格或尺寸。
6. 只有涉及图形关系、引线指向、表格结构、节点构造、材料代号对应图例、尺寸到底属于哪个构件等问题时，才标记 needs_vlm_review=true。
7. 单个汉字、单个数字、单个英文字母默认是低价值碎片。只有当它明确构成轴号、材料代号、图例编号、条文序号，且周边文字强烈支持时，才可以归入“轴号/索引/编号”；否则应归入“噪声”或“不确定”。
8. “有效”不是指文字真实存在，而是指它对后续图纸证据分类仍有业务价值。普通条文序号、重复的“一”、孤立数字、孤立字母通常不应轻易标为有效。
9. 如果当前文字本身是碎片，但周边文字包含材料/拆除/做法，不能把周边文字的类别强行套到当前文字上；当前文字仍按它自身判断。

主分类只能使用以下中文枚举之一：
材料/做法、拆除项、新建/安装项、设备/构件、规格尺寸、工程量/数量线索、图名/标题、公司/人名/图签信息、轴号/索引/编号、噪声、不确定。

分类口径：
- 材料/做法：材料名称、饰面做法、基层构造、施工做法、墙地顶做法、材料表内容。
- 拆除项：拆除、铲除、凿除、清运、原有构件拆改等。
- 新建/安装项：新建墙体、安装门窗、安装隔断、安装设备、铺贴、制作等动作性项目。
- 设备/构件：灯具、洁具、阀门、门套、踢脚线、玻璃隔断、方通、铝扣板、配电箱等对象。
- 规格尺寸：300x600、50mm、DN25、厚度、宽度、高度等规格或尺寸。
- 工程量/数量线索：面积、长度、数量、套数、个数、米数等可能和算量有关的文本；孤立数字如果无上下文可归噪声或不确定。
- 图名/标题：平面图、立面图、剖面图、大样图、材料表、设计说明、目录、图纸标题。
- 公司/人名/图签信息：公司、设计单位、建设单位、审核、校对、日期、证书编号、人员签字等图签信息。
- 轴号/索引/编号：轴号、索引号、详图号、房间号、材料代号、设备编号、图例编号。材料代号应视为有效信息，不是噪声。
- 噪声：孤立无意义单字符、OCR 乱码、重复符号、没有业务语义且周边也无法解释的碎片。
- 不确定：仅靠文字上下文无法稳定判断，需要人工或 VLM 复核。

必须返回严格 JSON，不要 Markdown，不要代码块。输出格式：
{
  "classifications": [
    {
      "text_id": "T00001",
      "current_text": "OCR原文",
      "primary_category": "材料/做法",
      "secondary_category": "墙面做法",
      "is_effective": true,
      "confidence": 0.0,
      "reason": "中文说明为什么这样判断",
      "related_text_ids": ["T00002"],
      "needs_vlm_review": false,
      "vlm_review_reason": "",
      "noise_reason": "",
      "suggested_usage": ["项目特征", "材料名称"]
    }
  ]
}

要求：
- classifications 必须和 input_items 一一对应，不多不少。
- current_text 必须原样返回输入的 current_text，不要改写。
- confidence 使用 0 到 1 的数字。
- is_effective 表示这条证据是否仍有业务识别价值；噪声通常为 false，材料、规格、图名、图签、编号等通常为 true。
- related_text_ids 只填写你判断时真正参考到的周边 text_id。
- reason、vlm_review_reason、noise_reason 必须使用中文。
"""


async def build_ocr_llm_cabinet(
    *,
    context_packages: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
    sample_size: int = 240,
    sample_strategy: str = "representative",
    max_nearby: int = 16,
    max_items_per_batch: int = 30,
    max_chars_per_batch: int = 28000,
    external_payload_mode: str = "minimal",
    mask_sensitive_text: bool = True,
    resume: bool = False,
    max_retries: int = 2,
    retry_delay_seconds: float = 3.0,
    execute: bool = False,
    provider: str = "deepseek",
    model: str | None = None,
    local_chat_url: str | None = None,
    local_api_key: str | None = None,
    classifier: ClassifierCallable | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    packages = [_normalize_context_package(row) for row in context_packages]
    selected_packages = select_context_packages(
        packages,
        sample_size=sample_size,
        strategy=sample_strategy,
    )
    payload_mode = _normalize_payload_mode(external_payload_mode)
    package_by_id = {_text(row.get("text_id")): row for row in selected_packages}
    input_items = [
        _classification_input(
            row,
            max_nearby=max_nearby,
            payload_mode=payload_mode,
            mask_sensitive_text=mask_sensitive_text,
        )
        for row in selected_packages
    ]
    batch_payloads = _build_batch_payloads(
        input_items,
        max_items_per_batch=max_items_per_batch,
        max_chars_per_batch=max_chars_per_batch,
    )

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "mode": "execute" if execute else "dry_run",
        "provider": provider,
        "model": _display_model(provider, model),
        "local_chat_url": local_chat_url or "",
        "input_context_package_count": len(packages),
        "selected_context_package_count": len(selected_packages),
        "sample_size": sample_size,
        "sample_strategy": sample_strategy,
        "max_nearby": max_nearby,
        "max_items_per_batch": max_items_per_batch,
        "max_chars_per_batch": max_chars_per_batch,
        "external_payload_mode": payload_mode,
        "mask_sensitive_text": mask_sensitive_text,
        "resume": resume,
        "max_retries": max_retries,
        "retry_delay_seconds": retry_delay_seconds,
        "batch_count": len(batch_payloads),
        "selected_bucket_counts": dict(Counter(_sample_bucket(row) for row in selected_packages)),
    }

    outputs = _write_pre_execution_outputs(
        directory=directory,
        batch_payloads=batch_payloads,
        selected_packages=selected_packages,
        summary=summary,
    )

    if not execute:
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_text(),
            "summary": summary,
            "outputs": outputs,
            "classifications": [],
        }

    partial_path = directory / "classified_ocr_cabinet.partial.json"
    classifications: list[dict[str, Any]] = _load_partial_classifications(partial_path) if resume else []
    completed_ids = {_text(row.get("text_id")) for row in classifications if _text(row.get("text_id"))}
    batch_results: list[dict[str, Any]] = []
    for batch_index, batch_payload in enumerate(batch_payloads, start=1):
        batch_ids = [_text(item.get("text_id")) for item in batch_payload.get("input_items") or [] if isinstance(item, Mapping)]
        if resume and batch_ids and all(text_id in completed_ids for text_id in batch_ids):
            batch_results.append(
                {
                    "batch_index": batch_index,
                    "mode": "resume_skipped",
                    "input_count": len(batch_ids),
                    "classification_count": len(batch_ids),
                    "raw_content": "",
                }
            )
            continue
        if classifier is not None:
            raw_result = await classifier(batch_payload)
            mode = "injected_classifier"
        elif provider.strip().lower() == "mock":
            raw_result = mock_classify_batch(batch_payload)
            mode = "mock_rule_classifier"
        else:
            raw_result = await _classify_batch_with_retries(
                batch_payload,
                provider=provider,
                model=model,
                local_chat_url=local_chat_url,
                local_api_key=local_api_key,
                username=username,
                trace_id=trace_id,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay_seconds,
            )
            mode = "llm"

        batch_classifications = _enrich_classifications_with_local_metadata(
            _normalize_batch_classifications(raw_result, batch_payload),
            package_by_id,
        )
        classifications.extend(batch_classifications)
        completed_ids.update(_text(row.get("text_id")) for row in batch_classifications if _text(row.get("text_id")))
        batch_results.append(
            {
                "batch_index": batch_index,
                "mode": mode,
                "input_count": len(batch_payload.get("input_items") or []),
                "classification_count": len(batch_classifications),
                "raw_content": _short_text(raw_result.get("raw_content"), 2000),
            }
        )
        _write_json(
            partial_path,
            {
                "schema_version": SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "classifications": classifications,
            },
        )

    summary.update(_classification_summary(classifications))
    summary["mode"] = "execute"

    classified_json = directory / "classified_ocr_cabinet.json"
    classified_csv = directory / "classified_ocr_cabinet.csv"
    review_md = directory / "classified_ocr_cabinet_review.md"
    summary_json = directory / "ocr_llm_cabinet_summary.json"

    _write_json(
        classified_json,
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "generated_at": _now_text(),
            "source": {
                "input_context_package_count": len(packages),
                "selected_context_package_count": len(selected_packages),
                "sample_strategy": sample_strategy,
            },
            "classifications": classifications,
            "batches": batch_results,
        },
    )
    write_classifications_csv(classified_csv, classifications)
    review_md.write_text(build_review_markdown(classifications, summary), encoding="utf-8")
    _write_json(summary_json, summary)

    outputs.update(
        {
            "classified_ocr_cabinet_json": str(classified_json.resolve()),
            "classified_ocr_cabinet_csv": str(classified_csv.resolve()),
            "classified_ocr_cabinet_review_md": str(review_md.resolve()),
            "ocr_llm_cabinet_summary_json": str(summary_json.resolve()),
        }
    )

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_text(),
        "summary": summary,
        "outputs": outputs,
        "classifications": classifications,
    }


def read_context_packages_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            parsed = json.loads(text)
            if isinstance(parsed, Mapping):
                rows.append(dict(parsed))
    return rows


def select_context_packages(
    packages: Sequence[Mapping[str, Any]],
    *,
    sample_size: int,
    strategy: str = "representative",
) -> list[dict[str, Any]]:
    normalized = [_normalize_context_package(row) for row in packages]
    if sample_size <= 0 or sample_size >= len(normalized):
        return normalized
    if strategy == "first":
        return normalized[:sample_size]
    if strategy != "representative":
        raise ValueError(f"unsupported_sample_strategy:{strategy}")

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        buckets[_sample_bucket(row)].append(row)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for bucket in ["明显噪声线索", "短文本编码线索", "材料做法线索", "拆除线索", "规格尺寸线索"]:
        for row in buckets.get(bucket, [])[:1]:
            text_id = _text(row.get("text_id"))
            if text_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(text_id)
            break
        if len(selected) >= sample_size:
            return selected

    per_bucket = max(3, math.ceil(sample_size / max(1, len(SAMPLE_BUCKET_ORDER))))
    for bucket in SAMPLE_BUCKET_ORDER:
        for row in buckets.get(bucket, [])[:per_bucket]:
            text_id = _text(row.get("text_id"))
            if text_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(text_id)
            if len(selected) >= sample_size:
                return selected

    for row in normalized:
        text_id = _text(row.get("text_id"))
        if text_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(text_id)
        if len(selected) >= sample_size:
            break
    return selected


async def classify_batch_with_llm(
    batch_payload: dict[str, Any],
    *,
    provider: str = "deepseek",
    model: str | None = None,
    local_chat_url: str | None = None,
    local_api_key: str | None = None,
    username: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    provider_config = _provider_config(
        provider,
        model,
        local_chat_url=local_chat_url,
        local_api_key=local_api_key,
    )
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
        endpoint_type="drawing_ocr_llm_cabinet_classify",
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


async def _classify_batch_with_retries(
    batch_payload: dict[str, Any],
    *,
    provider: str,
    model: str | None,
    local_chat_url: str | None,
    local_api_key: str | None,
    username: str | None,
    trace_id: str | None,
    max_retries: int,
    retry_delay_seconds: float,
) -> dict[str, Any]:
    attempts = max(1, int(max_retries) + 1)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await classify_batch_with_llm(
                batch_payload,
                provider=provider,
                model=model,
                local_chat_url=local_chat_url,
                local_api_key=local_api_key,
                username=username,
                trace_id=trace_id,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(max(0.0, float(retry_delay_seconds)) * attempt)
    assert last_error is not None
    raise last_error


def mock_classify_batch(batch_payload: Mapping[str, Any]) -> dict[str, Any]:
    classifications = [_mock_classification(item) for item in batch_payload.get("input_items") or []]
    return {"classifications": classifications, "raw_content": "mock_rule_classifier"}


def write_classifications_csv(path: str | Path, classifications: Sequence[Mapping[str, Any]]) -> None:
    headers = [
        "text_id",
        "原文",
        "主分类",
        "子分类",
        "是否有效",
        "置信度",
        "中文判断原因",
        "关联证据ID",
        "是否需要VLM复核",
        "VLM复核原因",
        "噪声原因",
        "建议用途",
        "周边文字",
        "截图路径",
        "页码",
        "tile_id",
    ]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in classifications:
            writer.writerow(
                {
                    "text_id": row.get("text_id"),
                    "原文": row.get("current_text"),
                    "主分类": row.get("primary_category"),
                    "子分类": row.get("secondary_category"),
                    "是否有效": "是" if row.get("is_effective") else "否",
                    "置信度": row.get("confidence"),
                    "中文判断原因": row.get("reason"),
                    "关联证据ID": " | ".join(row.get("related_text_ids") or []),
                    "是否需要VLM复核": "是" if row.get("needs_vlm_review") else "否",
                    "VLM复核原因": row.get("vlm_review_reason"),
                    "噪声原因": row.get("noise_reason"),
                    "建议用途": " | ".join(row.get("suggested_usage") or []),
                    "周边文字": " | ".join(row.get("nearby_texts") or []),
                    "截图路径": row.get("image_path"),
                    "页码": row.get("page"),
                    "tile_id": row.get("tile_id"),
                }
            )


def build_review_markdown(classifications: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> str:
    lines = [
        "# OCR 证据分类入柜审阅表",
        "",
        f"- 分类证据数：{summary.get('classification_count', len(classifications))}",
        f"- 有效证据数：{summary.get('effective_count', 0)}",
        f"- 噪声证据数：{summary.get('noise_count', 0)}",
        f"- 需要 VLM 复核：{summary.get('needs_vlm_review_count', 0)}",
        "",
        "## 分类统计",
        "",
        "| 主分类 | 数量 |",
        "|---|---:|",
    ]
    category_counts = summary.get("primary_category_counts") if isinstance(summary.get("primary_category_counts"), Mapping) else {}
    for category in PRIMARY_CATEGORIES:
        lines.append(f"| {category} | {category_counts.get(category, 0)} |")
    lines.extend(["", "## 前 80 条审阅样例", "", "| text_id | 原文 | 主分类 | 有效 | 置信度 | 原因 |", "|---|---|---|---|---:|---|"])
    for row in classifications[:80]:
        lines.append(
            "| {text_id} | {text} | {category} | {effective} | {confidence:.2f} | {reason} |".format(
                text_id=_md_cell(row.get("text_id")),
                text=_md_cell(row.get("current_text")),
                category=_md_cell(row.get("primary_category")),
                effective="是" if row.get("is_effective") else "否",
                confidence=_float(row.get("confidence")),
                reason=_md_cell(row.get("reason")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _write_pre_execution_outputs(
    *,
    directory: Path,
    batch_payloads: Sequence[Mapping[str, Any]],
    selected_packages: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    prompt_path = directory / "llm_cabinet_prompt.md"
    preview_path = directory / "llm_cabinet_batches_preview.json"
    sample_csv = directory / "llm_cabinet_sample_plan.csv"
    summary_path = directory / "ocr_llm_cabinet_summary.json"

    prompt_path.write_text(
        "# OCR 证据分类入柜提示词\n\n"
        "## System Prompt\n\n"
        f"```text\n{SYSTEM_PROMPT}\n```\n",
        encoding="utf-8",
    )
    _write_json(
        preview_path,
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "batch_count": len(batch_payloads),
            "batches": batch_payloads,
        },
    )
    _write_sample_plan_csv(sample_csv, selected_packages)
    _write_json(summary_path, summary)
    return {
        "llm_cabinet_prompt_md": str(prompt_path.resolve()),
        "llm_cabinet_batches_preview_json": str(preview_path.resolve()),
        "llm_cabinet_sample_plan_csv": str(sample_csv.resolve()),
        "ocr_llm_cabinet_summary_json": str(summary_path.resolve()),
    }


def _write_sample_plan_csv(path: Path, packages: Sequence[Mapping[str, Any]]) -> None:
    headers = ["text_id", "原文", "抽样桶", "周边数量", "截图路径", "页码", "tile_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in packages:
            writer.writerow(
                {
                    "text_id": row.get("text_id"),
                    "原文": row.get("current_text"),
                    "抽样桶": _sample_bucket(row),
                    "周边数量": len(row.get("nearby_evidences") or []),
                    "截图路径": row.get("image_path"),
                    "页码": row.get("page"),
                    "tile_id": row.get("tile_id"),
                }
            )


def _build_batch_payloads(
    input_items: Sequence[Mapping[str, Any]],
    *,
    max_items_per_batch: int,
    max_chars_per_batch: int,
) -> list[dict[str, Any]]:
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_chars = 0
    for item in input_items:
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if current and (len(current) >= max_items_per_batch or current_chars + item_chars > max_chars_per_batch):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        batches.append(current)
    return [
        {
            "prompt_version": PROMPT_VERSION,
            "task": "classify_each_ocr_evidence_into_chinese_cabinet",
            "batch_index": index + 1,
            "batch_count": len(batches),
            "input_items": list(batch),
            "output_contract": {
                "classifications": [
                    {
                        "text_id": "string",
                        "current_text": "must_equal_input_current_text",
                        "primary_category": "|".join(PRIMARY_CATEGORIES),
                        "secondary_category": "string",
                        "is_effective": True,
                        "confidence": 0.0,
                        "reason": "中文原因",
                        "related_text_ids": ["text_id"],
                        "needs_vlm_review": False,
                        "vlm_review_reason": "",
                        "noise_reason": "",
                        "suggested_usage": ["项目特征"],
                    }
                ]
            },
        }
        for index, batch in enumerate(batches)
    ]


def _classification_input(
    package: Mapping[str, Any],
    *,
    max_nearby: int,
    payload_mode: str,
    mask_sensitive_text: bool,
) -> dict[str, Any]:
    nearby = []
    for row in list(package.get("nearby_evidences") or [])[:max(0, max_nearby)]:
        if not isinstance(row, Mapping):
            continue
        nearby_item = {
            "text_id": _text(row.get("text_id")),
            "text": _external_text(row.get("text"), mask_sensitive=mask_sensitive_text),
            "relation": _text(row.get("relation")),
            "rank": _int(row.get("rank")),
        }
        if payload_mode == "full":
            nearby_item.update(
                {
                    "dx_ratio": _float(row.get("dx_ratio")),
                    "dy_ratio": _float(row.get("dy_ratio")),
                    "page_distance_ratio": _float(row.get("page_distance_ratio")),
                    "tile_id": _text(row.get("tile_id")),
                }
            )
        nearby.append(nearby_item)
    item = {
        "text_id": _text(package.get("text_id")),
        "current_text": _external_text(package.get("current_text"), mask_sensitive=mask_sensitive_text),
        "confidence": _float(package.get("confidence")),
        "current_features": package.get("current_features") if isinstance(package.get("current_features"), Mapping) else {},
        "nearby_evidences": nearby,
    }
    if payload_mode == "full":
        item.update(
            {
                "page": _int(package.get("page")),
                "bbox_ratio": _number_list(package.get("bbox_ratio")),
                "tile_id": _text(package.get("tile_id")),
                "image_path": _text(package.get("image_path")),
            }
        )
    return item


def _normalize_batch_classifications(raw_result: Mapping[str, Any], batch_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    input_items = [item for item in batch_payload.get("input_items") or [] if isinstance(item, Mapping)]
    returned = raw_result.get("classifications")
    if not isinstance(returned, list):
        returned = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in returned:
        if not isinstance(item, Mapping):
            continue
        text_id = _text(item.get("text_id") or item.get("文本ID") or item.get("证据ID"))
        if text_id and text_id not in by_id:
            by_id[text_id] = item
    return [_normalize_classification(by_id.get(_text(source.get("text_id"))), source) for source in input_items]


def _enrich_classifications_with_local_metadata(
    classifications: Sequence[Mapping[str, Any]],
    package_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in classifications:
        text_id = _text(row.get("text_id"))
        package = package_by_id.get(text_id) or {}
        enriched = dict(row)
        if package:
            enriched["current_text"] = _text(package.get("current_text"))
            enriched["nearby_texts"] = [_text(item) for item in package.get("nearby_texts") or [] if _text(item)]
            enriched["page"] = package.get("page")
            enriched["tile_id"] = package.get("tile_id")
            enriched["image_path"] = package.get("image_path")
            enriched["bbox_ratio"] = package.get("bbox_ratio")
        result.append(enriched)
    return result


def _load_partial_classifications(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = parsed.get("classifications") if isinstance(parsed, Mapping) else []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        text_id = _text(row.get("text_id"))
        if not text_id or text_id in seen:
            continue
        seen.add(text_id)
        result.append(dict(row))
    return result


def _normalize_classification(item: Mapping[str, Any] | None, source: Mapping[str, Any]) -> dict[str, Any]:
    source_text_id = _text(source.get("text_id"))
    current_text = _text(source.get("current_text"))
    nearby_texts = [_text(row.get("text")) for row in source.get("nearby_evidences") or [] if isinstance(row, Mapping)]
    if item is None:
        return _fallback_classification(
            source,
            primary_category="不确定",
            is_effective=True,
            confidence=0.0,
            reason="LLM 未返回本条证据的分类，保留为不确定并等待复核。",
        )

    primary_category = _text(item.get("primary_category") or item.get("主分类"))
    if primary_category not in PRIMARY_CATEGORIES:
        primary_category = "不确定"
    is_effective = _bool(item.get("is_effective", item.get("是否有效", primary_category != "噪声")))
    confidence = _bounded_float(item.get("confidence", item.get("置信度")))
    needs_vlm_review = _bool(item.get("needs_vlm_review", item.get("是否需要VLM复核", False)))
    reason = _text(item.get("reason") or item.get("中文判断原因"))
    if not reason:
        reason = "LLM 未给出中文原因，系统保留分类但标记为需复核。"
        needs_vlm_review = True

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "text_id": source_text_id,
        "current_text": current_text,
        "primary_category": primary_category,
        "secondary_category": _text(item.get("secondary_category") or item.get("子分类")),
        "is_effective": is_effective,
        "confidence": confidence,
        "reason": reason,
        "related_text_ids": _string_list(item.get("related_text_ids") or item.get("关联证据ID")),
        "needs_vlm_review": needs_vlm_review,
        "vlm_review_reason": _text(item.get("vlm_review_reason") or item.get("VLM复核原因")),
        "noise_reason": _text(item.get("noise_reason") or item.get("噪声原因")),
        "suggested_usage": _string_list(item.get("suggested_usage") or item.get("建议用途")),
        "nearby_texts": nearby_texts,
        "page": source.get("page"),
        "tile_id": source.get("tile_id"),
        "image_path": source.get("image_path"),
        "bbox_ratio": source.get("bbox_ratio"),
    }
    if normalized["current_text"] != current_text:
        normalized["current_text"] = current_text
    if primary_category == "噪声":
        normalized["is_effective"] = False
        if not normalized["noise_reason"]:
            normalized["noise_reason"] = reason
    return normalized


def _fallback_classification(
    source: Mapping[str, Any],
    *,
    primary_category: str,
    is_effective: bool,
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "text_id": _text(source.get("text_id")),
        "current_text": _text(source.get("current_text")),
        "primary_category": primary_category,
        "secondary_category": "",
        "is_effective": is_effective,
        "confidence": confidence,
        "reason": reason,
        "related_text_ids": [],
        "needs_vlm_review": primary_category in {"不确定", "规格尺寸", "工程量/数量线索", "轴号/索引/编号"},
        "vlm_review_reason": "需要结合图形关系确认。" if primary_category in {"规格尺寸", "工程量/数量线索", "轴号/索引/编号"} else "",
        "noise_reason": reason if primary_category == "噪声" else "",
        "suggested_usage": [],
        "nearby_texts": [_text(row.get("text")) for row in source.get("nearby_evidences") or [] if isinstance(row, Mapping)],
        "page": source.get("page"),
        "tile_id": source.get("tile_id"),
        "image_path": source.get("image_path"),
        "bbox_ratio": source.get("bbox_ratio"),
    }


def _mock_classification(item: Mapping[str, Any]) -> dict[str, Any]:
    text = _text(item.get("current_text"))
    compact = re.sub(r"\s+", "", text)
    nearby_text = " ".join(_text(row.get("text")) for row in item.get("nearby_evidences") or [] if isinstance(row, Mapping))
    combined = f"{text} {nearby_text}"
    related_ids = [
        _text(row.get("text_id"))
        for row in item.get("nearby_evidences") or []
        if isinstance(row, Mapping) and _text(row.get("text_id"))
    ][:3]

    if not compact or PURE_SYMBOL_RE.fullmatch(compact):
        return _mock_result(item, "噪声", "空文本或纯符号碎片，当前没有业务语义。", False, 0.92, noise_reason="空文本或纯符号")
    if "拆" in combined or "铲除" in combined or "凿除" in combined:
        return _mock_result(item, "拆除项", "当前文字或周边文字包含拆除语义，属于拆除相关证据。", True, 0.78, related_ids=related_ids)
    if DIMENSION_RE.search(text):
        return _mock_result(
            item,
            "规格尺寸",
            "当前文字包含尺寸或规格表达，可作为材料规格或工程量关联线索。",
            True,
            0.76,
            related_ids=related_ids,
            needs_vlm_review=True,
            vlm_reason="需要看图确认该尺寸属于哪个材料或构件。",
            usage=["规格尺寸"],
        )
    if MATERIAL_CODE_RE.search(text):
        return _mock_result(
            item,
            "轴号/索引/编号",
            "当前文字像材料代号、设备编号或图例编号，短文本但有业务价值。",
            True,
            0.72,
            related_ids=related_ids,
            needs_vlm_review=True,
            vlm_reason="需要看图例或材料表确认编号含义。",
            usage=["材料代号", "索引编号"],
        )
    if _contains_any(combined, ["涂料", "墙砖", "地砖", "石膏板", "龙骨", "玻璃", "踢脚线", "铝扣板", "不锈钢", "人造石", "木饰面", "防水", "乳胶漆", "瓷砖", "吊顶"]):
        return _mock_result(item, "材料/做法", "当前文字或周边文字包含材料、饰面或构造做法语义。", True, 0.74, related_ids=related_ids, usage=["材料名称", "项目特征"])
    if _contains_any(combined, ["安装", "新建", "砌筑", "铺贴", "制作", "成品"]):
        return _mock_result(item, "新建/安装项", "当前文字或周边文字包含新建、安装或制作动作。", True, 0.68, related_ids=related_ids, usage=["项目名称"])
    if _contains_any(combined, ["灯", "插座", "开关", "阀门", "洁具", "门套", "隔断", "配电箱", "风口", "方通"]):
        return _mock_result(item, "设备/构件", "当前文字或周边文字指向设备、构件或成品对象。", True, 0.66, related_ids=related_ids, usage=["设备构件"])
    if _contains_any(text, ["平面图", "立面图", "剖面图", "大样图", "材料表", "设计说明", "目录", "图名", "图号"]):
        return _mock_result(item, "图名/标题", "当前文字是图纸标题、材料表标题或说明标题。", True, 0.82)
    if _contains_any(text, ["公司", "设计", "建设单位", "审核", "校对", "日期", "证书", "资质"]):
        return _mock_result(item, "公司/人名/图签信息", "当前文字属于图签、公司、人名、日期或证书信息。", True, 0.8)
    if PURE_NUMBER_RE.fullmatch(compact):
        return _mock_result(item, "噪声", "当前是孤立数字，周边上下文不足以判断为工程量或编号。", False, 0.62, noise_reason="孤立数字")
    if len(compact) <= 1:
        return _mock_result(item, "噪声", "当前是孤立单字符，周边上下文不足以形成业务证据。", False, 0.7, noise_reason="孤立单字符")
    if re.fullmatch(r"[A-Za-z0-9\-_.]{1,8}", compact):
        return _mock_result(item, "轴号/索引/编号", "当前文字像轴号、房间号、索引号或编号，保留但不直接作为材料。", True, 0.55, needs_vlm_review=True, vlm_reason="需要结合图纸位置确认编号含义。")
    return _mock_result(item, "不确定", "当前文字仅靠文本上下文无法稳定归类，保留等待人工或 VLM 复核。", True, 0.35, needs_vlm_review=True, vlm_reason="需要结合局部图形或人工判断。")


def _mock_result(
    item: Mapping[str, Any],
    primary_category: str,
    reason: str,
    is_effective: bool,
    confidence: float,
    *,
    related_ids: Sequence[str] | None = None,
    needs_vlm_review: bool = False,
    vlm_reason: str = "",
    noise_reason: str = "",
    usage: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "text_id": item.get("text_id"),
        "current_text": item.get("current_text"),
        "primary_category": primary_category,
        "secondary_category": "",
        "is_effective": is_effective,
        "confidence": confidence,
        "reason": reason,
        "related_text_ids": list(related_ids or []),
        "needs_vlm_review": needs_vlm_review,
        "vlm_review_reason": vlm_reason,
        "noise_reason": noise_reason,
        "suggested_usage": list(usage or []),
    }


def _normalize_payload_mode(value: str | None) -> str:
    mode = (value or "minimal").strip().lower()
    if mode in {"minimal", "external_minimal", "safe"}:
        return "minimal"
    if mode in {"full", "debug"}:
        return "full"
    raise ValueError(f"unsupported_external_payload_mode:{value}")


def _external_text(value: Any, *, mask_sensitive: bool) -> str:
    text = _text(value)
    if not mask_sensitive:
        return text
    sensitive_label = _sensitive_text_label(text)
    if not sensitive_label:
        return text
    return f"[{sensitive_label}]"


def _sensitive_text_label(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return ""
    if re.fullmatch(r"(?:20\d{2}|19\d{2})[./年-]?\d{1,2}([./月-]?\d{1,2}日?)?", compact):
        return "图签日期"
    if re.search(r"(公司|设计院|事务所|建设单位|设计单位|施工单位|有限公司|集团|资质|证书|证号|注册|审核|校对|审定|签字)", compact):
        return "图签公司/人员/证书信息"
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", compact) and re.search(r"(审|校|设|制|核)", compact):
        return "图签人员信息"
    return ""


def _classification_summary(classifications: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    category_counts = Counter(_text(row.get("primary_category")) for row in classifications)
    return {
        "classification_count": len(classifications),
        "effective_count": sum(1 for row in classifications if row.get("is_effective")),
        "noise_count": category_counts.get("噪声", 0),
        "needs_vlm_review_count": sum(1 for row in classifications if row.get("needs_vlm_review")),
        "primary_category_counts": {category: category_counts.get(category, 0) for category in PRIMARY_CATEGORIES},
    }


def _sample_bucket(package: Mapping[str, Any]) -> str:
    text = _text(package.get("current_text"))
    nearby_text = " ".join(_text(item) for item in package.get("nearby_texts") or [])
    combined = f"{text} {nearby_text}"
    compact = re.sub(r"\s+", "", text)
    if not compact or PURE_SYMBOL_RE.fullmatch(compact):
        return "明显噪声线索"
    if DIMENSION_RE.search(text):
        return "规格尺寸线索"
    if MATERIAL_CODE_RE.search(text):
        return "短文本编码线索"
    if "拆" in text or "铲除" in text or "凿除" in text:
        return "拆除线索"
    if _contains_any(text, ["安装", "新建", "砌筑", "铺贴", "制作", "成品"]):
        return "新建安装线索"
    if _contains_any(text, ["灯", "插座", "开关", "阀门", "洁具", "门套", "隔断", "配电箱", "风口", "方通"]):
        return "设备构件线索"
    if PURE_NUMBER_RE.fullmatch(compact) or _contains_any(text, ["㎡", "m2", "M2", "米", "数量", "面积", "长度"]):
        return "工程量数字线索"
    if _contains_any(text, ["平面图", "立面图", "剖面图", "大样图", "材料表", "设计说明", "目录", "图名", "图号"]):
        return "图名标题线索"
    if _contains_any(text, ["公司", "设计", "建设单位", "审核", "校对", "日期", "证书", "资质"]):
        return "公司人名图签线索"
    if len(compact) <= 1:
        return "明显噪声线索"
    if len(compact) <= 4:
        return "短文本编码线索"
    if _contains_any(text, ["涂料", "墙砖", "地砖", "石膏板", "龙骨", "玻璃", "踢脚线", "铝扣板", "不锈钢", "人造石", "木饰面", "防水", "乳胶漆", "瓷砖", "吊顶"]):
        return "材料做法线索"
    if _contains_any(combined, ["涂料", "墙砖", "地砖", "石膏板", "龙骨", "玻璃", "踢脚线", "铝扣板", "不锈钢", "人造石", "木饰面", "防水", "乳胶漆", "瓷砖", "吊顶"]):
        return "其他"
    return "其他"


def _provider_config(
    provider: str,
    model: str | None = None,
    *,
    allow_mock: bool = False,
    local_chat_url: str | None = None,
    local_api_key: str | None = None,
) -> dict[str, Any]:
    normalized = (provider or "deepseek").strip().lower()
    if allow_mock and normalized == "mock":
        return {
            "provider": "mock",
            "model": "mock-rule-classifier",
            "url": "",
            "headers": {},
            "timeout": 0,
        }
    if normalized == "deepseek":
        api_key = settings.deepseek_api_key.strip()
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
        return {
            "provider": "deepseek",
            "model": (model or settings.deepseek_model or "deepseek-chat").strip(),
            "url": settings.deepseek_chat_url,
            "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "timeout": max(settings.agent_llm_timeout_seconds, 120),
        }
    if normalized in {"dashscope", "qwen"}:
        api_key = settings.dashscope_api_key.strip()
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured.")
        base_url = settings.dashscope_base_url.rstrip("/")
        return {
            "provider": "dashscope",
            "model": (model or settings.dashscope_bill_summary_model or "qwen-plus").strip(),
            "url": f"{base_url}/chat/completions",
            "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "timeout": max(settings.dashscope_timeout_seconds, 120),
        }
    if normalized == "local":
        url = _text(local_chat_url)
        if not url:
            raise RuntimeError("local_chat_url is required for provider=local.")
        if not _is_local_or_private_url(url):
            raise RuntimeError("provider=local only allows localhost or private intranet endpoints.")
        headers = {"Content-Type": "application/json"}
        if _text(local_api_key):
            headers["Authorization"] = f"Bearer {_text(local_api_key)}"
        return {
            "provider": "local",
            "model": (model or "local-ocr-cabinet-classifier").strip(),
            "url": url,
            "headers": headers,
            "timeout": 180,
        }
    raise ValueError(f"unsupported_ocr_cabinet_provider:{provider}")


def _display_model(provider: str, model: str | None = None) -> str:
    normalized = (provider or "deepseek").strip().lower()
    if model:
        return model
    if normalized == "mock":
        return "mock-rule-classifier"
    if normalized in {"dashscope", "qwen"}:
        return settings.dashscope_bill_summary_model or "qwen-plus"
    if normalized == "local":
        return model or "local-ocr-cabinet-classifier"
    return settings.deepseek_model or "deepseek-chat"


def _is_local_or_private_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host.startswith("10."):
        return True
    if host.startswith("192.168."):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
            except ValueError:
                return False
            return 16 <= second <= 31
    return False


def _normalize_context_package(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["text_id"] = _text(row.get("text_id"))
    normalized["current_text"] = _text(row.get("current_text"))
    normalized["page"] = _int(row.get("page"))
    normalized["confidence"] = _float(row.get("confidence"))
    normalized["bbox_ratio"] = _number_list(row.get("bbox_ratio"))
    normalized["tile_id"] = _text(row.get("tile_id"))
    normalized["image_path"] = _text(row.get("image_path"))
    normalized["nearby_texts"] = [_text(item) for item in row.get("nearby_texts") or [] if _text(item)]
    normalized["nearby_evidences"] = [
        dict(item) for item in row.get("nearby_evidences") or [] if isinstance(item, Mapping)
    ]
    return normalized


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


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_values = re.split(r"[,，、;\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = [str(item) for item in value]
    else:
        raw_values = [str(value)]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _number_list(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [_float(item) for item in value]
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [_float(item) for item in parsed]


def _bounded_float(value: Any) -> float:
    return max(0.0, min(1.0, _float(value)))


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "是", "有效", "需要"}:
        return True
    if text in {"0", "false", "no", "n", "否", "无效", "不需要", ""}:
        return False
    return bool(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _short_text(value: Any, max_len: int) -> str:
    text = _text(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _md_cell(value: Any) -> str:
    text = _short_text(value, 120).replace("|", "\\|").replace("\n", " ")
    return text


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(build_ocr_llm_cabinet(*args, **kwargs))
