from __future__ import annotations

from pathlib import Path

from app.services.bidding_technical_regression import (
    BID_TECHNICAL_REGRESSION_VERSION,
    build_technical_bid_regression_report,
    load_technical_regression_document,
    write_technical_bid_regression_outputs,
)


def test_technical_bid_regression_quantifies_section_gap_and_priorities(tmp_path: Path):
    official = tmp_path / "official.txt"
    generated = tmp_path / "generated.txt"
    official.write_text(
        "\n".join(
            [
                "第一章、投标人营业执照及资质证明(复印加盖公章)",
                "营业执照 资质证明 附件清单",
                "第十章、针对本工程的施工组织设计",
                "施工部署及协调措施",
                "弱电综合布线安装",
                "线缆保护",
                "末端测试",
                "成品保护措施",
                "整改闭环和资料归档",
                "第十三章、施工临时用电的施工方案",
                "三级配电 二级保护 一机一闸 漏电保护 巡检维护 应急处置",
            ]
        ),
        encoding="utf-8",
    )
    generated.write_text(
        "\n".join(
            [
                "第一章、投标人营业执照及资质证明(复印加盖公章)",
                "营业执照 资质证明 附件清单",
                "第十章、针对本工程的施工组织设计",
                "本章按施工组织、项目部职责、材料进场和质量安全文明施工要求组织实施。",
                "项目部建立周检查、整改闭环和资料归档机制。",
                "第十三章、施工临时用电的施工方案",
                "临时用电按三级配电、二级保护和漏电保护组织，建立巡检维护台账。",
            ]
        ),
        encoding="utf-8",
    )

    report = build_technical_bid_regression_report(official, generated)

    assert report["version"] == BID_TECHNICAL_REGRESSION_VERSION
    by_section = {row["section_no"]: row for row in report["sections"]}
    assert by_section["7.3.10"]["generated_char_count"] > 0
    assert by_section["7.3.10"]["status"] in {"high_gap", "critical_gap", "medium_gap"}
    assert "弱电综合布线安装" in by_section["7.3.10"]["missing_subtopics"]
    assert report["priorities"]
    assert report["summary"]["matched_section_count"] >= 3
    assert "average_effective_char_ratio" in report["summary"]


def test_technical_bid_regression_writes_json_markdown_and_csv(tmp_path: Path):
    official = tmp_path / "official.md"
    generated = tmp_path / "generated.md"
    official.write_text(
        "第十章、针对本工程的施工组织设计\n施工部署\n弱电综合布线安装\n线缆保护\n末端测试\n",
        encoding="utf-8",
    )
    generated.write_text(
        "第十章、针对本工程的施工组织设计\n施工部署\n",
        encoding="utf-8",
    )

    outputs = write_technical_bid_regression_outputs(
        official,
        generated,
        tmp_path / "reports",
        stem="p5_regression",
    )

    assert Path(outputs["json"]).exists()
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    assert markdown.startswith("# BIZ-4c4 P9")
    assert "整篇正文长度比" in markdown
    assert Path(outputs["csv"]).read_text(encoding="utf-8-sig").splitlines()[0].startswith("section_no")


def test_technical_bid_regression_filters_ole_and_path_noise_from_keywords(tmp_path: Path):
    official = tmp_path / "official.txt"
    generated = tmp_path / "generated.txt"
    official.write_text(
        "\n".join(
            [
                "第十章、针对本工程的施工组织设计",
                "INCLUDEPICTURE",
                "RE",
                r"C:\\Users\\demo\\WeChat Files\\wxid_abcd\\FileStorage\\AppData\\Roaming\\Tencent",
                "儔E儔E儔E 儔E儔E",
                "万科首铸东江之星 01公司介绍/精装案例分享 深圳盐田",
                "临时用电 系统设计 定期复查 专职电工每周一次",
                "鼀辘膃縀笀砀oe",
                "洁H猄H渄H琄H",
                "耀任意多边形 207",
                "报主管部门批准后实施 临时用电 系统设计 主管电气 技术人员",
                "鶠隙辒袋膄穽獶汯",
                "琙塅却景睴牡e楍牣獯景",
                "标题 3 Char,一 Char1,section:3 Char",
                "按图纸施工 责任分工 质量措施 项目主管领导层 项目主管领导层",
                "鞠贀綆煳渀欀栀_",
                "閟讍螉畽爀p歭漃",
                "样式 宋体 小四 行距: 1.5 倍行距 + 首行缩进: 2 字符",
                "櫛状杶檬剬捬鯬肸",
                "赒稸=袷炴8娄耱",
                "晿啖勦聤蒉塌鬝萉覌",
                "妘陑緣籯垛皌璚幅",
                "施工部署及协调措施",
                "施工机械设备准备齐全",
                "交叉作业协调",
            ]
        ),
        encoding="utf-8",
    )
    generated.write_text(
        "\n".join(
            [
                "第十章、针对本工程的施工组织设计",
                "施工部署及协调措施",
                "施工机械设备准备齐全",
            ]
        ),
        encoding="utf-8",
    )

    report = build_technical_bid_regression_report(official, generated)
    row = {item["section_no"]: item for item in report["sections"]}["7.3.10"]
    keyword_text = "、".join(row["matched_keywords"] + row["missing_keywords"])

    assert "INCLUDEPICTURE" not in keyword_text
    assert "WeChat" not in keyword_text
    assert "FileStorage" not in keyword_text
    assert "儔E" not in keyword_text
    assert "公司介绍" not in keyword_text
    assert "精装案例分享" not in keyword_text
    assert "系统设计" not in keyword_text
    assert "定期复查" not in keyword_text
    assert "任意多边形" not in keyword_text
    assert "鼀辘" not in keyword_text
    assert "洁H" not in keyword_text
    assert "主管电气" not in keyword_text
    assert "鶠隙" not in keyword_text
    assert "琙塅" not in keyword_text
    assert "标题" not in keyword_text
    assert "项目主管领导层" not in keyword_text
    assert "鞠贀" not in keyword_text
    assert "閟讍" not in keyword_text
    assert "宋体" not in keyword_text
    assert "小四" not in keyword_text
    assert "首行缩进" not in keyword_text
    assert "櫛状" not in keyword_text
    assert "赒稸" not in keyword_text
    assert "晿啖" not in keyword_text
    assert "妘陑" not in keyword_text
    assert "交叉作业协调" in row["missing_keywords"]
    assert report["summary"]["official_noise_line_count"] >= 20


def test_technical_bid_regression_prefers_real_chapter_heading_over_inner_title(tmp_path: Path):
    official = tmp_path / "official.txt"
    official.write_text(
        "\n".join(
            [
                "目录",
                "第十章、针对本工程的施工组织设计",
                "第十一章、办公室、工具间、材料间的管理方案",
                "第十章、针对本工程的施工组织设计",
                "施工准备先行",
                "施工组织设计",
                "工作面移交",
                "第十一章、办公室、工具间、材料间的管理方案",
                "办公室管理",
            ]
        ),
        encoding="utf-8",
    )

    document = load_technical_regression_document(official)

    assert "施工准备先行" in document.sections["7.3.10"]
    assert "工作面移交" in document.sections["7.3.10"]
    assert "办公室管理" in document.sections["7.3.11"]
