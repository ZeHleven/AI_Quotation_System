from __future__ import annotations

import csv
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from xml.etree import ElementTree


BID_TECHNICAL_REGRESSION_VERSION = "biz4c4_p9_technical_bid_regression_noise_filter_v1"

REGRESSION_NOISE_PATTERNS = (
    r"INCLUDEPICT",
    r"Content[_-]?Types",
    r"word[/\\]document\.xml",
    r"oleObject",
    r"ObjectPool",
    r"WeChat",
    r"wxid[_-]",
    r"FileStorage",
    r"AppData",
    r"Roaming",
    r"Tencent",
    r"微信C盘",
    r"C[:：][/\\]",
    r"公司介绍[/\\]精装案例分享",
    r"任意多边形",
    r"Microsoft",
    r"标题\s*\d?.*Char",
    r"未标题",
    r"报主管部门批准后实施",
    r"项目主管领导层",
    r"正文文本缩进",
    r"首行缩进",
    r"倍行距",
)

REGRESSION_MOJIBAKE_CHARS = set("儔嬀潃瑮湥祔嵳醕蒃蚅袇馘誢呓噕塗婙摣晥桧橩癵硷穹坖奘捚敤杦楨獪畴睶祸冴搀獲睯牮癥砮汭龥鎙融箁潵捩坝馢輀羈獵瀀洀樀鼀辘膃縀笀砀耀洁猄渄琄鶠隙辒袋膄穽獶汯琙却景睴牡楍牣獯祥钰颤鸁軦蠆噌葽胳劦峁檩铬焰衍鑃諅訫翓扬鞠贀綆煳渀欀栀閟讍螉畽爀歭漃鯟幞榥骾鉞倶檕赒墜蒂倐铔霍薶笴赉郥鄜孓傴伆痘櫛杶剬捬鯬肸稸袷炴娄耱愹驍麒榲藍樐喴襷僒珏醪傏瀳醫詁攦鄨霥餬晿啖勦聤蒉塌鬝萉覌螓熷螠顆栻旕瘏吅兑鷐琞鍴篛鰤玓黧婘臅挩筞廡盡椚妠藃畕噙輖圔揀褢雒肷嫒匱鷙匐繊坃蘘蘸鬾啞艅鬃啃漶枠讗旓巯")

REGRESSION_TERM_STOPWORDS = {
    "mm",
    "cm",
    "m2",
    "m3",
    "RE",
    "Char",
    "Files",
    "FileStorage",
    "AppData",
    "Roaming",
    "Tencent",
    "WeChat",
    "Content",
    "Types",
    "INCLUDEPICTU",
    "INCLUDEPICTURE",
    "直接连接符",
    "图片",
    "5m",
    "样式",
    "01公司介绍",
    "精装案例分享",
    "系统设计",
    "定期复查",
    "任意多边形",
    "耀任意多边形",
    "标题",
    "情况",
    "主管电气",
    "正文文本缩进",
    "首行缩进",
    "宋体",
    "小四",
    "厘米",
}

REGRESSION_COMMON_CJK_CHARS = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反明看原又利比或但质气第向道命此变条结解问意建月公无系很情者最立代想已通并提直题程展五果料象员位入常文总次品式活设及管特件长求老头基资边流路级少图统接知较将组见计别手期根论运指区强放决被干做必先回则任取据处给光门即保治造规领口导器志增争思术极交受联认权收证改清再采转更单风切打教速安场身例真务具每目至达走积示议声报完类离名确科信节整空况集传许步广记需段研界律叫该价严龙效查型按格置层片始专状育识适属包火调满局照参引听精值号维划选标写存候亲快院深难近周委技备办省习响约支史感便团市何除消构府称准验连断矿青列"
    "施工工程项目质量安全材料进度计划管理措施方案组织设计工期节点临时用电配电装修电气采购供应保证体系技术负责人现场验收保修维修建设工具办公室垃圾品牌样板合同图纸规范标准难点重点承诺经验简历证书资质营业法人授权委托人员机械设备协调成品半成品原材料文明防火生产责任制度流程检查交底班组作业处理制约破坏局面"
)

REGRESSION_VALID_CJK_ANCHORS = (
    "施工",
    "工程",
    "项目",
    "质量",
    "安全",
    "材料",
    "进度",
    "计划",
    "管理",
    "措施",
    "方案",
    "组织",
    "设计",
    "工期",
    "节点",
    "临时",
    "用电",
    "配电",
    "装修",
    "电气",
    "采购",
    "供应",
    "保证",
    "体系",
    "技术",
    "负责人",
    "现场",
    "验收",
    "保修",
    "维修",
    "建设",
    "工具",
    "办公室",
    "垃圾",
    "品牌",
    "样板",
    "合同",
    "图纸",
    "规范",
    "标准",
    "难点",
    "重点",
    "承诺",
    "经验",
    "简历",
    "证书",
    "资质",
    "营业",
    "法人",
    "授权",
    "委托",
    "人员",
    "机械",
    "设备",
    "协调",
    "成品",
    "文明",
    "防火",
    "生产",
    "责任",
    "制度",
    "流程",
    "检查",
    "交底",
    "班组",
    "作业",
    "处理",
    "制约",
    "破坏",
    "局面",
)

TECHNICAL_REGRESSION_SECTIONS: tuple[dict[str, Any], ...] = (
    {"section_no": "7.3.1", "chapter_no": 1, "category": "fixed_material", "title": "投标人营业执照及资质证明(复印加盖公章)", "aliases": ("投标人营业执照及资质证明", "营业执照及资质证明")},
    {"section_no": "7.3.2", "chapter_no": 2, "category": "fixed_material", "title": "法定代表人身份证明书", "aliases": ("法定代表人身份证明书",)},
    {"section_no": "7.3.3", "chapter_no": 3, "category": "fixed_material", "title": "投标文件签署授权委托书，委托书要求总公司授权", "aliases": ("投标文件签署授权委托书", "授权委托书")},
    {"section_no": "7.3.4", "chapter_no": 4, "category": "fixed_material", "title": "投标人拟派出的项目经理的《中华人民共和国一级建造师注册证书》复印件加盖投标人公章", "aliases": ("一级建造师注册证书", "项目经理注册证书")},
    {"section_no": "7.3.5", "chapter_no": 5, "category": "fixed_material", "title": "招标文件要求投标人提交的其它投标资料", "aliases": ("其它投标资料", "其他投标资料")},
    {"section_no": "7.3.6", "chapter_no": 6, "category": "scheme", "title": "投标人对本工程的工程质量和工期(请注明天数)的承诺及保证措施", "aliases": ("工程质量和工期", "质量和工期承诺", "承诺及保证措施")},
    {"section_no": "7.3.7", "chapter_no": 7, "category": "fixed_material", "title": "投标人近三年已完成的类似工程经验", "aliases": ("类似工程经验", "类似工程业绩")},
    {"section_no": "7.3.8", "chapter_no": 8, "category": "fixed_material", "title": "投标人拟派驻本项目的项目经理、技术负责人、安全负责人以及其它主要管理人员和技术人员的简历和资格证书", "aliases": ("拟派驻本项目", "主要管理人员", "简历和资格证书")},
    {"section_no": "7.3.9", "chapter_no": 9, "category": "scheme", "title": "施工总进度计划(包括总工期、主要材料与详细设备进场时间等)", "aliases": ("施工总进度计划", "总工期", "设备进场时间")},
    {"section_no": "7.3.10", "chapter_no": 10, "category": "scheme", "title": "针对本工程的施工组织设计", "aliases": ("施工组织设计",)},
    {"section_no": "7.3.11", "chapter_no": 11, "category": "scheme", "title": "办公室、工具间、材料间的管理方案", "aliases": ("办公室", "工具间", "材料间")},
    {"section_no": "7.3.12", "chapter_no": 12, "category": "scheme", "title": "垃圾的清理、堆置、运输、垃圾堆场管理方案", "aliases": ("垃圾的清理", "垃圾堆场", "垃圾清运")},
    {"section_no": "7.3.13", "chapter_no": 13, "category": "scheme", "title": "施工临时用电的施工方案", "aliases": ("施工临时用电", "临时用电")},
    {"section_no": "7.3.14", "chapter_no": 14, "category": "scheme", "title": "主要材料的采购计划（含甲指乙供材料）", "aliases": ("主要材料的采购计划", "甲指乙供", "材料采购")},
    {"section_no": "7.3.15", "chapter_no": 15, "category": "scheme", "title": "提供详细的安全生产、文明施工、防火施工方案和保证措施", "aliases": ("安全生产", "文明施工", "防火施工")},
    {"section_no": "7.3.16", "chapter_no": 16, "category": "scheme", "title": "重要的施工质量保障措施", "aliases": ("施工质量保障", "质量保障措施")},
    {"section_no": "7.3.17", "chapter_no": 17, "category": "scheme", "title": "投标单位按需于回标前提供主要材料样板，规格尺寸按发包人要求", "aliases": ("主要材料样板", "规格尺寸", "材料样板")},
    {"section_no": "7.3.18", "chapter_no": 18, "category": "fixed_material", "title": "投标单位拟采用的材料品牌表", "aliases": ("材料品牌表", "拟采用的材料品牌")},
    {"section_no": "7.3.19", "chapter_no": 19, "category": "scheme", "title": "项目重难点分析", "aliases": ("项目重难点", "重难点分析", "重点难点")},
    {"section_no": "7.3.20", "chapter_no": 20, "category": "scheme", "title": "投标单位认为能提升投标竞争力的内容", "aliases": ("提升投标竞争力", "竞争力的内容", "投标竞争力")},
)

TERM_STOPWORDS = {
    "本工程",
    "本项目",
    "施工",
    "工程",
    "项目",
    "技术标",
    "投标",
    "招标",
    "文件",
    "要求",
    "措施",
    "方案",
    "计划",
    "管理",
    "进行",
    "相关",
    "内容",
    "保证",
    "根据",
    "采用",
    "提供",
}


@dataclass(frozen=True)
class TechnicalRegressionDocument:
    path: str
    text: str
    lines: tuple[str, ...]
    sections: dict[str, str]
    raw_visible_length: int
    noise_line_count: int


def build_technical_bid_regression_report(
    official_path: str | Path,
    generated_path: str | Path,
) -> dict[str, Any]:
    official = load_technical_regression_document(official_path)
    generated = load_technical_regression_document(generated_path)
    rows = [
        _section_regression_row(spec, official.sections.get(spec["section_no"], ""), generated.sections.get(spec["section_no"], ""))
        for spec in TECHNICAL_REGRESSION_SECTIONS
    ]
    summary = _regression_summary(rows, official, generated)
    priorities = _regression_priorities(rows)
    return {
        "version": BID_TECHNICAL_REGRESSION_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "official_path": str(Path(official_path)),
        "generated_path": str(Path(generated_path)),
        "summary": summary,
        "priorities": priorities,
        "sections": rows,
    }


def write_technical_bid_regression_outputs(
    official_path: str | Path,
    generated_path: str | Path,
    output_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str]:
    report = build_technical_bid_regression_report(official_path, generated_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_stem = stem or f"BIZ4c4_P5_技术标真实样本回归_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = target_dir / f"{file_stem}.json"
    markdown_path = target_dir / f"{file_stem}.md"
    csv_path = target_dir / f"{file_stem}_章节差距.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_technical_bid_regression_markdown(report), encoding="utf-8")
    _write_section_csv(csv_path, report["sections"])
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "csv": str(csv_path),
    }


def load_technical_regression_document(path: str | Path) -> TechnicalRegressionDocument:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"technical bid regression source not found: {source}")
    suffix = source.suffix.lower()
    if suffix == ".docx":
        text = _read_docx_text(source)
    elif suffix in {".txt", ".md"}:
        text = source.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".doc":
        raise ValueError("P5 regression does not parse legacy .doc directly; convert to .docx or provide extracted .txt text.")
    else:
        raise ValueError(f"unsupported technical bid regression source type: {suffix}")
    raw_lines = tuple(_clean_line(line) for line in text.splitlines() if _clean_line(line))
    lines = tuple(line for line in raw_lines if not _is_regression_noise_line(line))
    return TechnicalRegressionDocument(
        path=str(source),
        text="\n".join(lines),
        lines=lines,
        sections=_sectionize_lines(lines),
        raw_visible_length=_visible_length("\n".join(raw_lines)),
        noise_line_count=max(0, len(raw_lines) - len(lines)),
    )


def build_technical_bid_regression_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# BIZ-4c4 P9 技术标回归报告",
        "",
        f"- 正式标：`{report.get('official_path')}`",
        f"- 系统稿：`{report.get('generated_path')}`",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 整体状态：{summary.get('status_label')}",
        f"- 章节覆盖：{summary.get('matched_section_count')} / {summary.get('section_count')}",
        f"- 整篇正文长度比：{summary.get('document_length_ratio')}（系统稿 {summary.get('generated_visible_length')} / 正式标 {summary.get('official_visible_length')} 字符）",
        f"- 平均有效长度覆盖：{summary.get('average_effective_char_ratio')}",
        f"- 平均关键词覆盖率：{summary.get('average_keyword_coverage_rate')}",
        f"- 严重差距章节：{summary.get('critical_gap_count')} 个；明显差距章节：{summary.get('high_gap_count')} 个；基本达标章节：{summary.get('pass_count')} 个",
        f"- 噪声过滤：正式标过滤 {summary.get('official_noise_line_count', 0)} 行；系统稿过滤 {summary.get('generated_noise_line_count', 0)} 行",
        "",
        "## 下一步优先级",
        "",
    ]
    for index, item in enumerate(report.get("priorities") or [], start=1):
        lines.append(f"{index}. **{item.get('priority')} / {item.get('section_no')} {item.get('section_title')}**：{item.get('reason')}；建议：{item.get('suggestion')}")
    if not report.get("priorities"):
        lines.append("- 暂无明显高优先级差距。")
    lines.extend(["", "## 章节差距", ""])
    lines.append("| 章节 | 分类 | 状态 | 长度比 | 关键词覆盖 | 缺失关键词 | 建议 |")
    lines.append("|---|---|---|---:|---:|---|---|")
    for row in report.get("sections") or []:
        missing = "、".join((row.get("missing_keywords") or [])[:8])
        lines.append(
            f"| {row.get('section_no')} {row.get('section_title')} | {row.get('category')} | "
            f"{row.get('status_label')} | {row.get('char_ratio')} | {row.get('keyword_coverage_rate')} | "
            f"{missing or '-'} | {row.get('suggestion')} |"
        )
    return "\n".join(lines) + "\n"


def _section_regression_row(spec: dict[str, Any], official_text: str, generated_text: str) -> dict[str, Any]:
    official_analysis_text = _analysis_text(official_text)
    generated_analysis_text = _analysis_text(generated_text)
    official_chars = _visible_length(official_analysis_text)
    generated_chars = _visible_length(generated_analysis_text)
    char_ratio = round(generated_chars / official_chars, 4) if official_chars else (1.0 if generated_chars else 0.0)
    official_keywords = _top_keywords(official_analysis_text, limit=30)
    generated_normalized = _normalize(generated_analysis_text)
    matched_keywords = [term for term in official_keywords if _normalize(term) in generated_normalized]
    missing_keywords = [term for term in official_keywords if term not in matched_keywords]
    keyword_rate = round(len(matched_keywords) / len(official_keywords), 4) if official_keywords else 1.0
    official_subtopics = _subtopics(official_analysis_text)
    generated_subtopic_text = _normalize(generated_analysis_text)
    matched_subtopics = [item for item in official_subtopics if _normalize(item) in generated_subtopic_text]
    subtopic_rate = round(len(matched_subtopics) / len(official_subtopics), 4) if official_subtopics else 1.0
    table_gap = _table_like_count(official_analysis_text) > 0 and _table_like_count(generated_analysis_text) == 0
    status = _section_gap_status(spec["category"], char_ratio, keyword_rate, subtopic_rate, generated_chars, table_gap)
    return {
        "section_no": spec["section_no"],
        "chapter_no": spec["chapter_no"],
        "section_title": spec["title"],
        "category": spec["category"],
        "status": status,
        "status_label": _status_label(status),
        "official_char_count": official_chars,
        "generated_char_count": generated_chars,
        "char_ratio": char_ratio,
        "official_paragraph_count": _paragraph_count(official_analysis_text),
        "generated_paragraph_count": _paragraph_count(generated_analysis_text),
        "keyword_coverage_rate": keyword_rate,
        "matched_keywords": matched_keywords[:20],
        "missing_keywords": missing_keywords[:20],
        "subtopic_coverage_rate": subtopic_rate,
        "missing_subtopics": [item for item in official_subtopics if item not in matched_subtopics][:12],
        "official_table_like_count": _table_like_count(official_analysis_text),
        "generated_table_like_count": _table_like_count(generated_analysis_text),
        "table_structure_gap": table_gap,
        "suggestion": _section_suggestion(spec["category"], status, table_gap),
    }


def _regression_summary(
    rows: Sequence[dict[str, Any]],
    official: TechnicalRegressionDocument,
    generated: TechnicalRegressionDocument,
) -> dict[str, Any]:
    matched = [row for row in rows if row.get("generated_char_count", 0) > 0]
    average_char_ratio = round(sum(float(row["char_ratio"]) for row in rows) / len(rows), 4) if rows else 0.0
    average_effective_char_ratio = round(sum(min(float(row["char_ratio"]), 1.0) for row in rows) / len(rows), 4) if rows else 0.0
    average_keyword_rate = round(sum(float(row["keyword_coverage_rate"]) for row in rows) / len(rows), 4) if rows else 0.0
    counts = Counter(row["status"] for row in rows)
    status = "needs_major_optimization" if counts.get("critical_gap", 0) >= 3 else ("needs_targeted_optimization" if counts.get("high_gap", 0) >= 2 else "usable_with_review")
    return {
        "status": status,
        "status_label": {
            "needs_major_optimization": "仍需重点优化",
            "needs_targeted_optimization": "需定向优化",
            "usable_with_review": "可复核使用",
        }[status],
        "section_count": len(rows),
        "matched_section_count": len(matched),
        "critical_gap_count": counts.get("critical_gap", 0),
        "high_gap_count": counts.get("high_gap", 0),
        "medium_gap_count": counts.get("medium_gap", 0),
        "pass_count": counts.get("pass", 0),
        "average_char_ratio": average_char_ratio,
        "average_effective_char_ratio": average_effective_char_ratio,
        "average_keyword_coverage_rate": average_keyword_rate,
        "official_visible_length": _visible_length(official.text),
        "generated_visible_length": _visible_length(generated.text),
        "official_raw_visible_length": official.raw_visible_length,
        "generated_raw_visible_length": generated.raw_visible_length,
        "official_noise_line_count": official.noise_line_count,
        "generated_noise_line_count": generated.noise_line_count,
        "document_length_ratio": round(_visible_length(generated.text) / _visible_length(official.text), 4) if _visible_length(official.text) else 0.0,
    }


def _regression_priorities(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    priority_score = {"critical_gap": 0, "high_gap": 1, "medium_gap": 2, "pass": 9}
    result: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (priority_score.get(str(item.get("status")), 8), item.get("chapter_no") or 99)):
        if row.get("status") == "pass":
            continue
        reason_parts = []
        if float(row.get("char_ratio") or 0) < 0.55:
            reason_parts.append(f"正文长度仅为正式标 {row.get('char_ratio')}")
        if float(row.get("keyword_coverage_rate") or 0) < 0.55:
            reason_parts.append(f"关键词覆盖率 {row.get('keyword_coverage_rate')}")
        if row.get("table_structure_gap"):
            reason_parts.append("正式标存在表格/清单特征，系统稿缺少对应结构")
        result.append(
            {
                "priority": "P0" if row["status"] == "critical_gap" else ("P1" if row["status"] == "high_gap" else "P2"),
                "section_no": row.get("section_no"),
                "section_title": row.get("section_title"),
                "category": row.get("category"),
                "reason": "；".join(reason_parts) or "章节专业覆盖仍偏弱",
                "suggestion": row.get("suggestion"),
                "missing_keywords": row.get("missing_keywords", [])[:10],
            }
        )
        if len(result) >= 12:
            break
    return result


def _read_docx_text(source: Path) -> str:
    try:
        with zipfile.ZipFile(source) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ValueError(f"Word file missing word/document.xml: {source}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid Word .docx zip package: {source}") from exc
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    for body_child in root.findall("./w:body/*", ns):
        tag = _local_name(body_child.tag)
        if tag == "p":
            text = _docx_paragraph_text(body_child, ns)
            if text:
                lines.append(text)
        elif tag == "tbl":
            for row in body_child.findall(".//w:tr", ns):
                cells = [
                    _join_text(_docx_paragraph_text(p, ns) for p in cell.findall(".//w:p", ns))
                    for cell in row.findall("./w:tc", ns)
                ]
                if any(cells):
                    lines.append(" | ".join(cell for cell in cells if cell))
    return "\n".join(lines)


def _docx_paragraph_text(paragraph: ElementTree.Element, ns: dict[str, str]) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = _local_name(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag == "br":
            parts.append("\n")
    return _clean_line("".join(parts))


def _sectionize_lines(lines: Sequence[str]) -> dict[str, str]:
    candidate_indexes: dict[str, list[tuple[int, int]]] = {spec["section_no"]: [] for spec in TECHNICAL_REGRESSION_SECTIONS}
    for index, line in enumerate(lines):
        for spec in TECHNICAL_REGRESSION_SECTIONS:
            score = _section_heading_score(line, spec)
            if score > 0:
                candidate_indexes[spec["section_no"]].append((index, score))
    starts: dict[str, int] = {}
    upper_bound = len(lines)
    for spec in reversed(TECHNICAL_REGRESSION_SECTIONS):
        candidates = [(idx, score) for idx, score in candidate_indexes[spec["section_no"]] if idx < upper_bound]
        if not candidates:
            continue
        max_score = max(score for _idx, score in candidates)
        high_confidence_candidates = [(idx, score) for idx, score in candidates if score >= max_score - 10]
        start = max(idx for idx, _score in high_confidence_candidates)
        starts[spec["section_no"]] = start
        upper_bound = start
    result: dict[str, str] = {}
    sorted_starts = sorted((index, section_no) for section_no, index in starts.items())
    for position, (start, section_no) in enumerate(sorted_starts):
        end = sorted_starts[position + 1][0] if position + 1 < len(sorted_starts) else len(lines)
        result[section_no] = "\n".join(lines[start:end]).strip()
    return result


def _line_matches_section(line: str, spec: dict[str, Any]) -> bool:
    return _section_heading_score(line, spec) > 0


def _section_heading_score(line: str, spec: dict[str, Any]) -> int:
    raw = _clean_line(line)
    if not raw or _is_regression_noise_line(raw):
        return 0
    normalized_raw = _normalize(raw)
    stripped = _strip_chapter_prefix(raw)
    normalized_stripped = _normalize(stripped)
    if len(normalized_raw) > 120:
        return 0
    title_norm = _normalize(spec["title"])
    alias_norms = tuple(_normalize(alias) for alias in spec.get("aliases") or ())
    terms = (title_norm, *alias_norms)
    section_no_pattern = re.escape(str(spec["section_no"])).replace(r"\.", r"[\.\．]")
    if re.match(rf"^\s*{section_no_pattern}(?:\s|[、.．:：-]|$)", raw):
        return 100 if any(term and term in normalized_raw for term in terms) else 85
    chapter_prefixes = _chapter_prefixes(int(spec.get("chapter_no") or 0))
    if any(raw.startswith(prefix) for prefix in chapter_prefixes):
        return 95 if any(term and term in normalized_stripped for term in terms) else 80
    if normalized_stripped == title_norm or normalized_raw == title_norm:
        return 70
    if title_norm and title_norm in normalized_stripped and len(normalized_stripped) <= len(title_norm) + 18:
        return 65
    for alias_norm in alias_norms:
        if alias_norm and len(alias_norm) >= 6 and normalized_stripped == alias_norm:
            return 55
    return 0


def _chapter_prefixes(chapter_no: int) -> tuple[str, ...]:
    chinese = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
        8: "八",
        9: "九",
        10: "十",
        11: "十一",
        12: "十二",
        13: "十三",
        14: "十四",
        15: "十五",
        16: "十六",
        17: "十七",
        18: "十八",
        19: "十九",
        20: "二十",
    }.get(chapter_no, str(chapter_no))
    return (
        f"第{chinese}章",
        f"第 {chinese} 章",
        f"第{chapter_no}章",
        f"第 {chapter_no} 章",
    )


def _legacy_line_matches_section(line: str, spec: dict[str, Any]) -> bool:
    normalized_line = _normalize(_strip_chapter_prefix(line))
    if len(normalized_line) > 90:
        return False
    terms = (spec["title"], *(spec.get("aliases") or ()))
    return any(_normalize(term) and _normalize(term) in normalized_line for term in terms)


def _strip_chapter_prefix(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^第[一二三四五六七八九十百0-9]+章[、.．\s-]*", "", text)
    text = re.sub(r"^\d+(?:\.\d+)*[、.．\s-]*", "", text)
    return text.strip()


def _top_keywords(text: str, *, limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9#]{2,16}", _analysis_text(text)):
        clean = _clean_term(term)
        if clean:
            counter[clean] += 1
    return [term for term, _count in counter.most_common(limit)]


def _clean_term(term: str) -> str:
    text = str(term or "").strip()
    if not text or text in TERM_STOPWORDS or text in REGRESSION_TERM_STOPWORDS:
        return ""
    if len(text) < 2:
        return ""
    if re.fullmatch(r"\d+", text):
        return ""
    if any(stop == text for stop in TERM_STOPWORDS):
        return ""
    if _is_noise_term(text):
        return ""
    return text


def _subtopics(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = _strip_chapter_prefix(_clean_line(raw_line))
        normalized = _normalize(line)
        if not line or len(normalized) < 4 or len(normalized) > 28:
            continue
        if any(_normalize(spec["title"]) in normalized for spec in TECHNICAL_REGRESSION_SECTIONS):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(line)
        if len(result) >= 30:
            break
    return result


def _section_gap_status(
    category: str,
    char_ratio: float,
    keyword_rate: float,
    subtopic_rate: float,
    generated_chars: int,
    table_gap: bool,
) -> str:
    if generated_chars <= 0:
        return "critical_gap"
    if category == "fixed_material":
        if table_gap or char_ratio < 0.25 or keyword_rate < 0.25:
            return "high_gap"
        if char_ratio < 0.45 or keyword_rate < 0.45:
            return "medium_gap"
        return "pass"
    if char_ratio < 0.35 or keyword_rate < 0.3:
        return "critical_gap"
    if char_ratio < 0.55 or keyword_rate < 0.5 or subtopic_rate < 0.35:
        return "high_gap"
    if char_ratio < 0.75 or keyword_rate < 0.65:
        return "medium_gap"
    return "pass"


def _section_suggestion(category: str, status: str, table_gap: bool) -> str:
    if status == "pass":
        return "保持当前生成质量，进入人工复核。"
    if category == "fixed_material" or table_gap:
        return "优先补齐企业资料、附件清单和可编辑表格结构，避免用正文段落替代表格/证照材料。"
    if status == "critical_gap":
        return "继续按章节模板、专业工法清单和评审关键词深化，补足部署、工序、责任、检查、验收和资料闭环。"
    if status == "high_gap":
        return "补强章节专业子题和项目化措施，并复跑 P2/P3 覆盖矩阵。"
    return "补充缺失关键词对应的措施段落，减少泛化表达。"


def _status_label(status: str) -> str:
    return {
        "pass": "基本达标",
        "medium_gap": "轻中度差距",
        "high_gap": "明显差距",
        "critical_gap": "严重差距",
    }.get(status, status)


def _write_section_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = [
        "section_no",
        "section_title",
        "category",
        "status",
        "official_char_count",
        "generated_char_count",
        "char_ratio",
        "keyword_coverage_rate",
        "subtopic_coverage_rate",
        "missing_keywords",
        "suggestion",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: "、".join(row.get(field) or []) if isinstance(row.get(field), list) else row.get(field) for field in fields})


def _clean_line(line: Any) -> str:
    return re.sub(r"\s+", " ", str(line or "").replace("\u3000", " ")).strip()


def _analysis_text(text: Any) -> str:
    return "\n".join(line for line in (_clean_line(item) for item in str(text or "").splitlines()) if line and not _is_regression_noise_line(line))


def _is_regression_noise_line(line: Any) -> bool:
    text = _clean_line(line)
    if not text:
        return True
    if text in REGRESSION_TERM_STOPWORDS:
        return True
    if any(re.search(pattern, text, flags=re.I) for pattern in REGRESSION_NOISE_PATTERNS):
        return True
    if _looks_like_mojibake(text):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}", text) and text not in {"BIM", "CAD"}:
        return True
    if re.fullmatch(r"[A-Za-z0-9_./\\:-]{8,}", text) and re.search(r"[/\\]|\.xml|\.bin|\.rels", text, flags=re.I):
        return True
    return False


def _is_noise_term(term: str) -> bool:
    text = str(term or "").strip()
    if not text:
        return True
    if text in REGRESSION_TERM_STOPWORDS:
        return True
    if any(re.search(pattern, text, flags=re.I) for pattern in REGRESSION_NOISE_PATTERNS):
        return True
    if _looks_like_mojibake(text):
        return True
    if re.fullmatch(r"[A-Za-z]{1,16}", text) and text not in {"BIM", "CAD"}:
        return True
    if re.fullmatch(r"[A-Za-z0-9_]{4,16}", text) and re.search(r"\d", text) and not re.search(r"[\u4e00-\u9fff]", text):
        return True
    return False


def _looks_like_mojibake(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    weird_count = sum(1 for char in value if char in REGRESSION_MOJIBAKE_CHARS)
    if weird_count >= 2:
        return True
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", value)
    cjk_count = len(cjk_chars)
    if cjk_count >= 8 and weird_count / max(cjk_count, 1) >= 0.18:
        return True
    if cjk_count >= 4:
        common_count = sum(1 for char in cjk_chars if char in REGRESSION_COMMON_CJK_CHARS)
        has_valid_anchor = any(anchor in value for anchor in REGRESSION_VALID_CJK_ANCHORS)
        if not has_valid_anchor and common_count / max(cjk_count, 1) < 0.4:
            return True
    return False


def _visible_length(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def _paragraph_count(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if _clean_line(line))


def _table_like_count(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if "|" in line or "\t" in line)


def _normalize(text: Any) -> str:
    return re.sub(r"[^0-9A-Za-z#\u4e00-\u9fff]+", "", str(text or "")).lower()


def _join_text(values: Sequence[str]) -> str:
    return _clean_line(" ".join(value for value in values if value))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
