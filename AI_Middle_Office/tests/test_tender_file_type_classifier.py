from app.services.tender_file_type_classifier import classify_tender_file_type


def test_classifies_clarification_before_generic_tender_signals() -> None:
    result = classify_tender_file_type(
        original_filename="关于某项目的招标答疑文件.pdf",
        extracted_text="本答疑作为招标文件组成部分，问题及回复如下。",
    )

    assert result.file_type == "clarification"
    assert result.confidence >= 0.7


def test_classifies_bid_question_workbook_as_clarification() -> None:
    result = classify_tender_file_type(
        original_filename="投标疑问(1).xlsx",
        extracted_text=(
            "序号 | 内容 | 截图\n"
            "1 | 请明确结晶处理是否漏项 | 需要做结晶\n"
            "2 | 付款方式可否添加预付款30% | 后期合同洽谈"
        ),
    )

    assert result.file_type == "clarification"
    assert "文件名:投标疑问" in result.matched_signals
    assert result.confidence >= 0.7


def test_classifies_bill_of_quantities_from_excel_content() -> None:
    result = classify_tender_file_type(
        original_filename="附件2.xlsx",
        extracted_text="项目编码 项目名称 项目特征 单位 工程量 综合单价 合价",
    )

    assert result.file_type == "bill_of_quantities"
    assert "格式:Excel清单字段" in result.matched_signals


def test_classifies_addendum_before_generic_tender_signals() -> None:
    result = classify_tender_file_type(
        original_filename="招标文件补遗通知01.pdf",
        extracted_text="本补充通知是原招标文件的组成部分。",
    )

    assert result.file_type == "addendum"


def test_classifies_construction_contract_before_drawing_signals() -> None:
    result = classify_tender_file_type(
        original_filename="某项目公区精装修工程施工合同.docx",
        extracted_text=(
            "第一部分 合同协议书 第二部分 通用合同条款 "
            "第三部分 专用合同条款 发包人 承包人 合同价款 "
            "施工范围以施工图和图纸会审记录为准"
        ),
    )

    assert result.file_type == "contract"
    assert result.confidence >= 0.7


def test_classifies_drawing_from_generic_pdf_content() -> None:
    result = classify_tender_file_type(
        original_filename="附件5.pdf",
        extracted_text="建筑施工图 图纸目录 设计说明 图号 A-01 比例 1:100",
    )

    assert result.file_type == "drawing"


def test_classifies_tender_document_from_internal_structure() -> None:
    result = classify_tender_file_type(
        original_filename="采购资料.pdf",
        extracted_text="第一章 招标公告 第二章 投标人须知 第三章 评标办法",
    )

    assert result.file_type == "tender_document"


def test_uncertain_document_falls_back_to_other() -> None:
    result = classify_tender_file_type(
        original_filename="附件.txt",
        extracted_text="联系人：张三。请查收本次提交材料。",
    )

    assert result.file_type == "other"
    assert result.matched_signals == ()
