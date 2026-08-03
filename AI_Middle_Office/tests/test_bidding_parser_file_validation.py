from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from app.services.bidding_parser import TenderParseError, extract_tender_text


def _minimal_docx_bytes(document_xml: str | None = None) -> bytes:
    xml = document_xml or (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>技术标编制要求</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_docx_parser_keeps_valid_docx_behavior():
    result = extract_tender_text(
        _minimal_docx_bytes(),
        "技术标要求.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert "技术标编制要求" in result["text"]
    assert result["section_count"] == 1


@pytest.mark.parametrize(
    ("content", "expected_message"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"legacy-word", "旧版 Word .doc"),
        (b"%PDF-1.7\nmock", "文件内容实际为 PDF"),
        (b"{\\rtf1\\ansi technical requirement}", "文件内容实际为 RTF"),
        (b"<!DOCTYPE html><html><body>requirement</body></html>", "文件内容实际为网页 HTML"),
        (b"not-a-word-document", "文件内容不是有效的 Word DOCX"),
        (b"PK\x03\x04truncated", "DOCX 文件已损坏或上传不完整"),
    ],
)
def test_docx_parser_reports_actionable_format_mismatch(content: bytes, expected_message: str):
    with pytest.raises(TenderParseError, match=expected_message):
        extract_tender_text(content, "技术标要求.docx")


def test_docx_suffix_validation_takes_priority_over_pdf_mime_type():
    with pytest.raises(TenderParseError, match="文件内容实际为 PDF"):
        extract_tender_text(b"%PDF-1.7\nmock", "技术标要求.docx", "application/pdf")


def test_docx_parser_rejects_office_lock_file_by_filename():
    with pytest.raises(TenderParseError, match="Word 临时锁定文件"):
        extract_tender_text(_minimal_docx_bytes(), "~$技术标要求.docx")


def test_docx_parser_reports_zip_without_word_main_document():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("metadata.txt", "not a Word document")

    with pytest.raises(TenderParseError, match="缺少 word/document.xml"):
        extract_tender_text(buffer.getvalue(), "技术标要求.docx")


def test_docx_parser_reports_broken_document_xml():
    with pytest.raises(TenderParseError, match="DOCX 主文档结构已损坏"):
        extract_tender_text(_minimal_docx_bytes("<w:document>"), "技术标要求.docx")


def test_legacy_doc_extension_has_specific_guidance():
    with pytest.raises(TenderParseError, match="暂不支持旧版 Word .doc"):
        extract_tender_text(b"legacy-word", "技术标要求.doc")
