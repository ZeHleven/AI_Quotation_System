from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfWriter

from app.models.enterprise_profile import EnterpriseProfileItem
from app.models.file_object import FileObject
from app.services import bidding_formal_package as package_service
from app.services.bidding_formal_package import build_formal_package_preview, create_formal_package


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, files):
        self.files = files
        self.added = []

    def query(self, model):
        if model is FileObject:
            return _Query(self.files)
        if model is EnterpriseProfileItem:
            return _Query([])
        return _Query([])

    def add(self, value):
        self.added.append(value)


def _assembly(*, file_id="file-1"):
    return {
        "formal_ready": True,
        "blocking_items": [],
        "directory": [{"item_key": "business:license", "requires_attachment": True}],
        "requirements": [{
            "requirement_uuid": "requirement-1",
            "format_item_key": "business:license",
            "title": "营业执照",
            "resolved": True,
            "submitted_file_ids": [file_id],
            "submitted_profile_item_uuids": [],
        }],
    }


def _file(*, filename="license.pdf", content_type="application/pdf"):
    return SimpleNamespace(
        file_id="file-1",
        original_filename=filename,
        content_type=content_type,
        size_bytes=100,
        bucket="test",
        object_name="license-object",
    )


def _blank_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_v13_preview_accepts_pdf_attachment():
    preview = build_formal_package_preview(_Db([_file()]), SimpleNamespace(id=1), _assembly())

    assert preview["available"] is True
    assert preview["pdf_attachment_count"] == 1
    assert preview["blocking_items"] == []


def test_v13_preview_blocks_non_pdf_attachment():
    preview = build_formal_package_preview(
        _Db([_file(filename="license.jpg", content_type="image/jpeg")]),
        SimpleNamespace(id=1),
        _assembly(),
    )

    assert preview["available"] is False
    assert preview["blocking_items"][0]["code"] == "attachment_not_pdf"


def test_v13_merges_core_pdf_and_attachment_and_records_manifest(monkeypatch):
    db = _Db([_file()])
    core_pdf = _blank_pdf()
    attachment_pdf = _blank_pdf()
    monkeypatch.setattr(package_service, "get_object_bytes", lambda object_name, bucket: attachment_pdf)
    monkeypatch.setattr(package_service, "store_file_bytes", lambda **kwargs: {"bucket": "test", "object_name": "output", "size_bytes": len(kwargs["content"]), "content_type": "application/pdf"})

    package, output, manifest = create_formal_package(
        db,
        SimpleNamespace(id=1, project_uuid="project-1", project_name="商务标测试"),
        SimpleNamespace(id=1, run_uuid="run-1"),
        SimpleNamespace(id=1, import_uuid="quote-1", version_no=2, source_snapshot_sha256="hash"),
        _assembly(),
        core_pdf,
        SimpleNamespace(id=1, username="tester"),
    )

    assert package.output_file_id == output.file_id
    assert manifest["merged_page_count"] == 2
    assert manifest["attachments"][0]["page_count"] == 1
    assert len(db.added) == 2