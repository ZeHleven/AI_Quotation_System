"""Generic business-bid template registry and directory-driven render plan."""

from __future__ import annotations

from typing import Any


TEMPLATE_ID = "general_construction_business_bid"
TEMPLATE_VERSION = "v1.4"
TEMPLATE_TITLE = "通用建设工程商务标模板"


_SECTION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "bid_letter",
        "title": "投标函",
        "render_mode": "system_form",
        "item_keys": {"bid_letter"},
        "title_keywords": ("投标函", "投标书"),
    },
    {
        "key": "pricing_summary",
        "title": "投标报价汇总表",
        "render_mode": "system_table",
        "item_keys": {"pricing_summary", "quotation"},
        "title_keywords": ("报价汇总", "报价一览"),
    },
    {
        "key": "boq",
        "title": "工程量清单报价表",
        "render_mode": "system_table",
        "item_keys": {"boq"},
        "title_keywords": ("工程量清单", "清单报价", "报价明细"),
    },
    {
        "key": "legal_representative",
        "title": "法定代表人身份证明",
        "render_mode": "system_form_requires_manual_signature",
        "item_keys": {"legal_representative"},
        "title_keywords": ("法定代表人", "法人代表证明"),
    },
    {
        "key": "authorization",
        "title": "授权委托书",
        "render_mode": "system_form_requires_manual_signature",
        "item_keys": {"authorization"},
        "title_keywords": ("授权委托书", "法人授权"),
    },
    {
        "key": "commitment",
        "title": "投标承诺书",
        "render_mode": "system_form_requires_manual_signature",
        "item_keys": {"commitment"},
        "title_keywords": ("投标承诺", "投标承诺书"),
    },
    {
        "key": "business_deviation",
        "title": "商务条款偏离表",
        "render_mode": "system_response_table",
        "item_keys": {"business_deviation"},
        "title_keywords": ("商务条款偏离", "商务偏离"),
    },
    {
        "key": "attachment_index",
        "title": "企业资料附件目录",
        "render_mode": "system_attachment_index",
        "item_keys": set(),
        "title_keywords": (),
        "always_include": True,
    },
)

_DRAFT_BASELINE_KEYS = ("pricing_summary", "boq", "attachment_index")


def build_business_bid_template_plan(directory: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Resolve a generic template plan from a project's confirmed business directory.

    The registry never infers project-specific wording from reference projects. A directory
    item is only rendered when it maps to a generic section; every other item remains an
    explicit manual item for human completion.
    """
    normalized_directory = [item for item in (directory or []) if isinstance(item, dict)]
    has_confirmed_directory = bool(normalized_directory)
    consumed_item_keys: set[str] = set()
    sections: list[dict[str, Any]] = []

    for spec in _SECTION_SPECS:
        matches = _matches_for_spec(spec, normalized_directory)
        include = bool(matches) or bool(spec.get("always_include")) or (not has_confirmed_directory and spec["key"] in _DRAFT_BASELINE_KEYS)
        if not include:
            continue
        for match in matches:
            item_key = str(match.get("item_key") or "")
            if item_key:
                consumed_item_keys.add(item_key)
        sections.append({
            "section_key": spec["key"],
            "title": spec["title"],
            "render_mode": spec["render_mode"],
            "source_directory_items": [
                {
                    "item_key": item.get("item_key"),
                    "title": item.get("title") or item.get("item_title"),
                    "sequence": item.get("sequence"),
                }
                for item in matches
            ],
            "requires_manual_signature": spec["render_mode"].endswith("manual_signature"),
            "fallback_in_draft": not has_confirmed_directory and spec["key"] in _DRAFT_BASELINE_KEYS,
        })

    manual_items = []
    for item in normalized_directory:
        item_key = str(item.get("item_key") or "")
        if item_key in consumed_item_keys:
            continue
        manual_items.append({
            "item_key": item.get("item_key"),
            "title": item.get("title") or item.get("item_title") or "未命名目录项",
            "sequence": item.get("sequence"),
            "reason": "该目录项没有通用系统表式，须按招标文件原格式人工编制或关联附件。",
            "requires_attachment": bool(item.get("requires_attachment")),
            "requires_signature": bool(item.get("requires_signature")),
        })

    warnings: list[dict[str, str]] = []
    if not has_confirmed_directory:
        warnings.append({
            "code": "directory_not_confirmed",
            "message": "当前为草案基线模板，仅生成报价汇总、工程量清单和附件目录；确认项目商务标目录后才会生成其他表单。",
        })
    if manual_items:
        warnings.append({
            "code": "manual_directory_items",
            "message": f"有 {len(manual_items)} 个项目目录项未映射到通用模板，已保留为人工处理项。",
        })

    return {
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "template_title": TEMPLATE_TITLE,
        "directory_driven": True,
        "has_confirmed_directory": has_confirmed_directory,
        "generated_sections": sections,
        "manual_directory_items": manual_items,
        "warnings": warnings,
    }


def _matches_for_spec(spec: dict[str, Any], directory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if spec.get("always_include"):
        return []
    matches = []
    for item in directory:
        raw_key = str(item.get("item_key") or "").lower().rsplit(":", 1)[-1]
        title = str(item.get("title") or item.get("item_title") or "")
        if raw_key in spec["item_keys"] or any(keyword in title for keyword in spec["title_keywords"]):
            matches.append(item)
    return matches
