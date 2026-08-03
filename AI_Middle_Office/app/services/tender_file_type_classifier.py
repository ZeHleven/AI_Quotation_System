from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


AUTO_FILE_TYPE = "auto"
OTHER_FILE_TYPE = "other"
SUPPORTED_FILE_TYPES = {
    "tender_document",
    "clarification",
    "addendum",
    "contract",
    "drawing",
    "bill_of_quantities",
    OTHER_FILE_TYPE,
}


@dataclass(frozen=True)
class TenderFileTypeClassification:
    file_type: str
    confidence: float
    matched_signals: tuple[str, ...]
    scores: dict[str, float]


_TYPE_PRIORITY = (
    "clarification",
    "addendum",
    "contract",
    "bill_of_quantities",
    "drawing",
    "tender_document",
)

_FILENAME_SIGNALS = {
    "clarification": (
        "答疑",
        "澄清",
        "投标疑问",
        "投标问题",
        "疑问回复",
        "疑问答复",
        "问题回复",
        "质疑回复",
        "clarification",
        "q&a",
    ),
    "addendum": (
        "补遗",
        "补充通知",
        "更正公告",
        "更正通知",
        "变更公告",
        "变更通知",
        "addendum",
        "corrigendum",
    ),
    "contract": (
        "施工合同",
        "工程合同",
        "合同协议书",
        "合同文件",
        "contract",
    ),
    "bill_of_quantities": (
        "工程量清单",
        "招标清单",
        "报价清单",
        "控制价清单",
        "boq",
        "bill of quantities",
    ),
    "drawing": (
        "施工图",
        "设计图",
        "平面图",
        "立面图",
        "节点图",
        "大样图",
        "图纸",
        "drawing",
    ),
    "tender_document": (
        "招标文件",
        "招标公告",
        "采购文件",
        "投标邀请",
        "资格预审",
        "询价文件",
        "tender",
    ),
}

_CONTENT_SIGNALS = {
    "clarification": (
        ("招标答疑", 5.0),
        ("答疑纪要", 5.0),
        ("澄清文件", 4.0),
        ("澄清答复", 4.0),
        ("问题及回复", 4.0),
        ("提问回复", 3.0),
    ),
    "addendum": (
        ("补遗文件", 5.0),
        ("补充通知", 5.0),
        ("更正公告", 5.0),
        ("变更公告", 4.0),
        ("变更通知", 4.0),
    ),
    "contract": (
        ("合同协议书", 7.0),
        ("专用合同条款", 6.0),
        ("通用合同条款", 4.0),
        ("签约合同价", 5.0),
        ("发包人", 2.0),
        ("承包人", 2.0),
        ("合同价款", 3.0),
        ("合同工期", 2.0),
    ),
    "bill_of_quantities": (
        ("分部分项工程和单价措施项目清单", 7.0),
        ("工程量清单计价", 5.0),
        ("项目编码", 2.0),
        ("项目名称", 1.0),
        ("工程量", 2.0),
        ("综合单价", 2.0),
        ("合价", 1.0),
    ),
    "drawing": (
        ("建筑施工图", 6.0),
        ("装饰施工图", 6.0),
        ("图纸目录", 4.0),
        ("设计说明", 2.0),
        ("图号", 2.0),
        ("比例", 1.0),
        ("节点详图", 4.0),
    ),
    "tender_document": (
        ("投标人须知", 6.0),
        ("招标文件", 5.0),
        ("招标公告", 4.0),
        ("投标邀请", 4.0),
        ("评标办法", 3.0),
        ("投标文件格式", 3.0),
        ("合同条款及格式", 3.0),
    ),
}


def classify_tender_file_type(
    *,
    original_filename: str,
    extracted_text: str,
) -> TenderFileTypeClassification:
    filename = _normalize(Path(original_filename or "").stem)
    text = _normalize(extracted_text)[:200_000]
    scores = {item: 0.0 for item in _TYPE_PRIORITY}
    matched: dict[str, list[str]] = {item: [] for item in _TYPE_PRIORITY}

    for file_type, signals in _FILENAME_SIGNALS.items():
        for signal in signals:
            if _normalize(signal) in filename:
                scores[file_type] += 8.0
                matched[file_type].append(f"文件名:{signal}")

    for file_type, signals in _CONTENT_SIGNALS.items():
        for signal, weight in signals:
            if _normalize(signal) in text:
                scores[file_type] += weight
                matched[file_type].append(f"内容:{signal}")

    extension = Path(original_filename or "").suffix.lower()
    if extension in {".xlsx", ".xlsm"}:
        spreadsheet_headers = sum(
            1
            for signal in ("项目编码", "项目名称", "工程量", "综合单价", "合价")
            if signal in text
        )
        if spreadsheet_headers >= 3:
            scores["bill_of_quantities"] += 5.0
            matched["bill_of_quantities"].append("格式:Excel清单字段")

    top_type = max(
        _TYPE_PRIORITY,
        key=lambda item: (scores[item], -_TYPE_PRIORITY.index(item)),
    )
    top_score = scores[top_type]
    if top_score < 3.0:
        return TenderFileTypeClassification(
            file_type=OTHER_FILE_TYPE,
            confidence=0.35,
            matched_signals=(),
            scores=scores,
        )

    ordered_scores = sorted(scores.values(), reverse=True)
    second_score = ordered_scores[1] if len(ordered_scores) > 1 else 0.0
    margin = max(0.0, top_score - second_score)
    confidence = min(0.99, 0.55 + min(top_score, 16.0) / 40.0 + margin / 50.0)
    return TenderFileTypeClassification(
        file_type=top_type,
        confidence=round(confidence, 3),
        matched_signals=tuple(matched[top_type][:8]),
        scores=scores,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
