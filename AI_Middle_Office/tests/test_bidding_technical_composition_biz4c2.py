from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.bidding import BidDraftSection, BidMaterialRequirement, BidParseRun, BidProject, BidProjectFile
from app.models.enterprise_profile import ENTERPRISE_PROFILE_STATUS_ACTIVE, EnterpriseProfileFile, EnterpriseProfileItem
from app.models.file_object import FileObject
from app.models.user import User, UserRole
from app.services.bidding_draft_sections import _technical_composition_evidence_for_section
from app.services.bidding_parser import dumps_json
from app.services.bidding_technical_word_export import (
    BidTechnicalWordExportError,
    build_technical_bid_draft_export_document,
    build_technical_bid_final_export_document,
)
from app.services.bidding_tender_analysis import _TenderAnalysisDocxBuilder
from app.services import (
    bidding_draft_sections,
    bidding_material_requirements,
    bidding_technical_composition,
    bidding_technical_quality,
    bidding_technical_word_export,
)


PASSWORD = "secret123"
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63600000020001e221bc330000000049454e44ae426082"
)


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "admin") -> User:
    username = f"biz4c2_{role}_{uuid.uuid4().hex[:10]}"
    legacy_role = "admin" if role in {"admin", "system_admin"} else "user"
    db = SessionLocal()
    try:
        user = User(
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=legacy_role,
            role_version=1,
            quota=20,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz4c2 test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_project_run_and_file(
    user: User,
    *,
    extracted_text: str | None = None,
    segments: list[dict] | None = None,
) -> tuple[BidProject, BidParseRun]:
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="BIZ-4c-2 技术标组成识别测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4c-2"}),
        )
        db.add(project)
        db.flush()
        default_text = (
            "7. 投标文件的组成：技术标包括营业执照及资质证明、项目经理/建造师证书、施工组织设计。"
            "施工组织设计应结合招标范围、工期、质量标准编制。招标范围为办公楼室内装饰工程。"
        )
        default_segments = [
            {
                "source_file": "招标文件.docx",
                "source_location": "第7节",
                "text": "7. 投标文件的组成：技术标包括营业执照及资质证明、项目经理/建造师证书、施工组织设计。",
            },
            {
                "source_file": "招标文件.docx",
                "source_location": "第2节",
                "text": "招标范围为办公楼室内装饰工程，工期60日历天，质量标准为合格。",
            },
        ]
        file_obj = BidProjectFile(
            file_uuid=str(uuid.uuid4()),
            project_id=project.id,
            file_type="tender_document",
            original_filename="招标文件.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=1024,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            parser_status="parsed",
            parser_version="test-parser",
            extracted_text=extracted_text or default_text,
            segments_json=dumps_json(segments or default_segments),
            page_count=3,
            section_count=2,
            uploaded_by=user.id,
        )
        db.add(file_obj)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test-parser",
            input_file_ids_json=dumps_json([file_obj.file_uuid]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 6, 10, 0),
        )
        db.add(run)
        db.commit()
        db.refresh(project)
        db.refresh(run)
        return project, run
    finally:
        db.close()


def _create_active_profile(user: User, *, category: str, title: str, summary: str) -> EnterpriseProfileItem:
    db = SessionLocal()
    try:
        item = EnterpriseProfileItem(
            item_uuid=str(uuid.uuid4()),
            category=category,
            title=title,
            summary=summary,
            content_text=summary,
            structured_json=dumps_json({}),
            tags_json=dumps_json([title]),
            source="manual",
            confidentiality="internal",
            status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
            valid_until=date.today() + timedelta(days=365),
            created_by=user.id,
            updated_by=user.id,
            approved_by=user.id,
            approved_at=datetime(2026, 7, 6, 10, 1),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    finally:
        db.close()


def _docx_document_xml(docx_bytes: bytes) -> str:
    with ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _docx_part_xml(docx_bytes: bytes, part_name: str) -> str:
    with ZipFile(BytesIO(docx_bytes)) as archive:
        return archive.read(part_name).decode("utf-8")


def test_llm_technical_composition_syncs_profile_requirements(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        license_profile = _create_active_profile(user, category="certificate", title="营业执照", summary="统一社会信用代码证照资料")

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "business_license",
                        "component_title": "营业执照及资质证明",
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "该项为企业固定资质文件。",
                        "source_evidence": [
                            {
                                "source_file": "招标文件.docx",
                                "source_location": "第7节",
                                "original_text": "技术标包括营业执照及资质证明。",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": "business_license_file",
                                "need_title": "营业执照",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "营业执照",
                                "reason": "需要从企业资料库取用已盖章证照文件。",
                                "source_evidence": [
                                    {
                                        "source_file": "招标文件.docx",
                                        "source_location": "第7节",
                                        "original_text": "营业执照及资质证明",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component_key": "project_manager_cert",
                        "component_title": "项目经理/建造师证书",
                        "package_key": "technical",
                        "order_index": 2,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "该项为固定人员证书资料。",
                        "source_evidence": [
                            {
                                "source_file": "招标文件.docx",
                                "source_location": "第7节",
                                "original_text": "项目经理/建造师证书",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": "project_manager_certificate",
                                "need_title": "项目经理/建造师证书",
                                "source_type": "enterprise_profile",
                                "profile_category": "personnel",
                                "query": "项目经理 建造师证书",
                                "reason": "需要取用企业固定人员资格资料。",
                                "source_evidence": [
                                    {
                                        "source_file": "招标文件.docx",
                                        "source_location": "第7节",
                                        "original_text": "项目经理/建造师证书",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component_key": "construction_organization",
                        "component_title": "施工组织设计",
                        "package_key": "technical",
                        "order_index": 3,
                        "classification": "tender_extracted_content",
                        "classification_reason": "需要结合本项目招标范围、工期和质量标准编写。",
                        "source_evidence": [
                            {
                                "source_file": "招标文件.docx",
                                "source_location": "第7节",
                                "original_text": "施工组织设计应结合招标范围、工期、质量标准编制。",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": "project_scope_summary",
                                "need_title": "招标范围、工期及质量标准",
                                "source_type": "tender_document",
                                "query": "招标范围 工期 质量标准",
                                "reason": "需从招标文件抽取项目专属条件。",
                                "polished_text": "本项目招标范围为办公楼室内装饰工程，计划工期为60日历天，质量标准为合格。",
                                "source_evidence": [
                                    {
                                        "source_file": "招标文件.docx",
                                        "source_location": "第2节",
                                        "original_text": "招标范围为办公楼室内装饰工程，工期60日历天，质量标准为合格。",
                                    }
                                ],
                            }
                        ],
                    },
                ],
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["summary"]["component_count"] == 3
        assert data["summary"]["enterprise_profile_need_count"] == 2
        assert data["summary"]["tender_document_need_count"] == 1
        assert data["summary"]["auto_matched_profile_count"] == 1
        assert data["components"][2]["information_needs"][0]["polished_text"].startswith("本项目招标范围")

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.package_key == "technical")
                .order_by(BidMaterialRequirement.id.asc())
                .all()
            )
            assert len(rows) == 2
            matched = next(row for row in rows if row.title == "营业执照")
            missing = next(row for row in rows if row.title == "项目经理/建造师证书")
            assert matched.status == "submitted"
            assert matched.submitted_profile_item_uuid == license_profile.item_uuid
            assert missing.status == "missing"
            assert missing.submitted_profile_item_uuid is None
            refreshed_run = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            composition_evidence = _technical_composition_evidence_for_section(
                refreshed_run,
                {
                    "package_key": "technical",
                    "section_key": "technical:construction_organization",
                    "format_item_key": "construction_organization",
                    "section_title": "施工组织设计",
                },
            )
            assert composition_evidence
            assert composition_evidence[0]["source_kind"] == "technical_composition"
            assert "办公楼室内装饰工程" in composition_evidence[0]["original_text"]
        finally:
            db.close()

        requirements_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements",
            params={"run_uuid": run.run_uuid, "package_key": "technical"},
            headers=headers,
        )
        assert requirements_response.status_code == 200, requirements_response.text
        requirement_titles = {item["title"] for item in requirements_response.json()["data"]}
        assert {"营业执照", "项目经理/建造师证书"} <= requirement_titles
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_bid_final_quality_api_returns_formal_profile(client):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        db = SessionLocal()
        try:
            project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
            run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            run_db.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "status": "generated",
                        "components": [
                            {
                                "source_item_no": "7.3.18",
                                "component_key": "material_brand_table",
                                "component_title": "材料品牌表",
                                "classification": "fixed_enterprise_material",
                            }
                        ],
                    }
                }
            )
            db.add(
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_18",
                    section_title="7.3.18 材料品牌表",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.18 材料品牌表\n材料品牌按招标文件要求执行。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="accepted",
                    created_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-final/quality",
            params={"run_uuid": run.run_uuid},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["status"] == "warning"
        assert payload["issue_count"] == 0
        assert payload["quality_report"]["formal_profile"]["version"] == bidding_technical_quality.BID_TECHNICAL_FORMAL_PROFILE_VERSION
        assert payload["quality_report"]["formal_profile"]["fixed_material_section_count"] == 1
        warning_codes = {item["code"] for item in payload["quality_report"]["warnings"]}
        assert "formal_table_structure_missing" in warning_codes
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)


def test_technical_composition_expands_compound_license_and_qualification_need(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        _create_active_profile(
            user,
            category="certificate",
            title=f"营业执照_复合资料拆分测试_{uuid.uuid4().hex[:6]}",
            summary="营业执照副本，统一社会信用代码。",
        )
        _create_active_profile(
            user,
            category="qualification",
            title=f"建筑业企业资质证书_复合资料拆分测试_{uuid.uuid4().hex[:6]}",
            summary="建筑装修装饰工程专业承包一级，企业资质证明文件。",
        )

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "business_license_qualification",
                        "component_title": "营业执照及资质证明文件",
                        "source_item_no": "7.3.1",
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "固定企业证照资料。",
                        "source_evidence": [],
                        "information_needs": [
                            {
                                "need_key": "business_license_qualification",
                                "need_title": "营业执照及资质证明文件",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "营业执照及资质证明文件",
                                "reason": "同时需要营业执照和资质证明。",
                                "source_evidence": [],
                            }
                        ],
                    }
                ],
                "coverage_check": {"source_item_count": 1, "component_count": 1, "missing_source_item_nos": []},
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["summary"]["enterprise_profile_need_count"] == 2
        need_titles = {need["need_title"] for need in data["components"][0]["information_needs"]}
        assert {"营业执照", "企业资质证明文件"} <= need_titles

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.package_key == "technical")
                .order_by(BidMaterialRequirement.title.asc())
                .all()
            )
            assert len(rows) == 2
            assert {row.title for row in rows} == {"营业执照", "企业资质证明文件"}
            assert {row.status for row in rows} == {"submitted"}
            assert all(row.submitted_profile_item_uuid for row in rows)
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_refreshes_preserved_profile_submission_conflict(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        correct_profile = _create_active_profile(
            user,
            category="certificate",
            title=f"营业执照_旧冲突刷新测试_{uuid.uuid4().hex[:6]}",
            summary="营业执照副本，统一社会信用代码。",
        )
        wrong_profile = _create_active_profile(
            user,
            category="basic_info",
            title=f"资质证书_旧冲突刷新测试_{uuid.uuid4().hex[:6]}",
            summary="历史错误提交资料。",
        )
        component = {
            "component_key": "business_license",
            "component_title": "营业执照",
            "source_item_no": "7.3.1",
        }
        need = {
            "need_key": "business_license",
            "need_title": "营业执照",
            "source_type": "enterprise_profile",
            "profile_category": "certificate",
            "query": correct_profile.title,
        }
        material_identity = bidding_technical_composition._material_identity_candidates(
            component,
            need,
            selected_profile_item_uuid=correct_profile.item_uuid,
        )[0]
        material_key = bidding_technical_composition._material_key(component, need, material_identity=material_identity)

        db = SessionLocal()
        try:
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="business_license",
                    package_key="technical",
                    package_title="技术标",
                    section_key="technical_composition:7_3_1",
                    item_title="营业执照",
                    requirement_type="profile",
                    profile_category="certificate",
                    material_key=material_key,
                    title="营业执照",
                    fulfillment_mode="enterprise_profile",
                    status="approved",
                    priority="high",
                    owner_role="经营",
                    candidate_profile_item_uuid=correct_profile.item_uuid,
                    submitted_profile_item_uuid=wrong_profile.item_uuid,
                    normalized_json=dumps_json(
                        {
                            "extractor": "llm_technical_composition",
                            "material_identity": material_identity,
                            "candidate_profile_item": {"item_uuid": correct_profile.item_uuid},
                            "manual_submission": {"profile_item_uuids": [wrong_profile.item_uuid]},
                        }
                    ),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                    reviewed_by=user.id,
                    reviewed_at=datetime(2026, 7, 6, 10, 8),
                )
            )
            db.commit()
        finally:
            db.close()

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        **component,
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "固定企业证照资料。",
                        "source_evidence": [],
                        "information_needs": [need],
                    }
                ],
                "coverage_check": {"source_item_count": 1, "component_count": 1, "missing_source_item_nos": []},
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text

        db = SessionLocal()
        try:
            row = (
                db.query(BidMaterialRequirement)
                .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.material_key == material_key)
                .one()
            )
            assert row.status == "submitted"
            assert row.submitted_profile_item_uuid == correct_profile.item_uuid
            assert row.submitted_profile_item_uuid != wrong_profile.item_uuid
            assert row.reviewed_by is None
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_promotes_scheme_manual_need_and_matches_team_profile(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        team_profile = _create_active_profile(
            user,
            category="basic_info",
            title=f"项目经理及主要人员信息_方案归一化测试_{uuid.uuid4().hex[:6]}",
            summary="项目经理、技术负责人、安全负责人及主要管理人员信息。",
        )

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "construction_organization",
                        "component_title": "施工组织设计，含组织架构及主要管理人员简历",
                        "source_item_no": "7.3.10",
                        "package_key": "technical",
                        "order_index": 10,
                        "classification": "mixed",
                        "classification_reason": "需要企业人员资料，并结合项目编写方案。",
                        "source_evidence": [],
                        "information_needs": [
                            {
                                "need_key": "organization_team",
                                "need_title": "项目组织架构及主要管理人员简历",
                                "source_type": "enterprise_profile",
                                "profile_category": "personnel",
                                "query": "项目组织架构及主要管理人员简历",
                                "reason": "需要引用项目人员资料。",
                                "source_evidence": [],
                            },
                            {
                                "need_key": "construction_plan_manual",
                                "need_title": "施工组织设计项目化编写内容",
                                "source_type": "manual_input",
                                "query": "施工组织设计 安全文明 进度 质量 临时设施",
                                "reason": "需要结合项目编写。",
                                "source_evidence": [],
                            },
                        ],
                    }
                ],
                "coverage_check": {"source_item_count": 1, "component_count": 1, "missing_source_item_nos": []},
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["summary"]["enterprise_profile_need_count"] == 1
        assert data["summary"]["tender_document_need_count"] == 1
        assert data["summary"]["manual_input_need_count"] == 0
        source_types = {need["source_type"] for need in data["components"][0]["information_needs"]}
        assert source_types == {"enterprise_profile", "tender_document"}

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.package_key == "technical")
                .all()
            )
            assert len(rows) == 1
            assert rows[0].title == "项目经理及主要人员信息"
            assert rows[0].status == "submitted"
            assert rows[0].submitted_profile_item_uuid == team_profile.item_uuid
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_llm_technical_composition_regeneration_reuses_existing_requirements(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        license_profile = _create_active_profile(user, category="certificate", title="营业执照", summary="统一社会信用代码证照资料")
        calls = {"count": 0}

        async def fake_llm(*_args, **_kwargs):
            calls["count"] += 1
            suffix = calls["count"]
            return {
                "components": [
                    {
                        "component_key": f"business_license_random_{suffix}",
                        "component_title": "营业执照及资质证明",
                        "source_item_no": "7.3.1",
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "该项为企业固定证照资料。",
                        "source_evidence": [
                            {
                                "source_file": "招标文件.docx",
                                "source_location": "7.3.1",
                                "original_text": "7.3.1 营业执照及资质证明。",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": f"license_file_random_{suffix}",
                                "need_title": "营业执照",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "营业执照",
                                "reason": "从企业资料库取用营业执照扫描件。",
                                "source_evidence": [
                                    {
                                        "source_file": "招标文件.docx",
                                        "source_location": "7.3.1",
                                        "original_text": "营业执照及资质证明。",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "coverage_check": {
                    "source_item_count": 1,
                    "component_count": 1,
                    "excluded_count": 0,
                    "missing_source_item_nos": [],
                },
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)

        first = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["generation"]["created_count"] == 0
        assert second.json()["generation"]["refreshed_count"] == 1

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(
                    BidMaterialRequirement.parse_run_id == run.id,
                    BidMaterialRequirement.package_key == "technical",
                    BidMaterialRequirement.section_key.like("technical_composition:%"),
                )
                .all()
            )
            assert len(rows) == 1
            assert rows[0].title == "营业执照"
            assert rows[0].submitted_profile_item_uuid == license_profile.item_uuid
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_llm_technical_composition_dedupes_same_profile_item_in_one_generation(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        license_profile = _create_active_profile(
            user,
            category="certificate",
            title="Business License",
            summary="Business License scan and certificate material",
        )

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "business_license",
                        "component_title": "Business License",
                        "source_item_no": "7.3.1",
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "information_needs": [
                            {
                                "need_key": "business_license",
                                "need_title": "Business License",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "Business License",
                                "reason": "Use the fixed company license material.",
                            }
                        ],
                    },
                    {
                        "component_key": "bidder_license_copy",
                        "component_title": "Bidder Business License Copy",
                        "source_item_no": "7.3.2",
                        "package_key": "technical",
                        "order_index": 2,
                        "classification": "fixed_enterprise_material",
                        "information_needs": [
                            {
                                "need_key": "bidder_license_copy",
                                "need_title": "Bidder Business License Copy",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "Business License",
                                "reason": "Same fixed company license under another tender wording.",
                            }
                        ],
                    },
                ],
                "coverage_check": {
                    "source_item_count": 2,
                    "component_count": 2,
                    "excluded_count": 0,
                    "missing_source_item_nos": [],
                },
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        generation = response.json()["generation"]
        assert generation["created_count"] == 2
        assert generation["refreshed_count"] == 0
        assert len(generation["rows"]) == 2

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(
                    BidMaterialRequirement.parse_run_id == run.id,
                    BidMaterialRequirement.package_key == "technical",
                    BidMaterialRequirement.section_key.like("technical_composition:%"),
                )
                .all()
            )
            assert len(rows) == 2
            assert {row.section_key for row in rows} == {"technical_composition:7_3_1", "technical_composition:7_3_2"}
            assert len({row.material_key for row in rows}) == 2
            assert {row.submitted_profile_item_uuid for row in rows} == {license_profile.item_uuid}
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_material_requirement_list_prefers_llm_technical_composition_rows(client):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)

        db = SessionLocal()
        try:
            current_material_key = "technical_composition:profile-license"
            stale_material_key = "technical_composition:profile-stale-license"
            run_obj = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            run_obj.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "requirement_sync": {
                            "rows": [
                                {"material_key": current_material_key, "title": "Business License"},
                            ]
                        }
                    }
                }
            )
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="business_license",
                    package_key="technical",
                    package_title="Technical",
                    section_key="technical_composition:business_license",
                    item_title="Business License",
                    requirement_type="profile",
                    profile_category="certificate",
                    material_key=current_material_key,
                    title="Business License",
                    description="LLM technical composition requirement.",
                    fulfillment_mode="enterprise_profile",
                    status="submitted",
                    priority="high",
                    submitted_profile_item_uuid=str(uuid.uuid4()),
                    normalized_json=dumps_json({"extractor": "llm_technical_composition"}),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="stale_business_license",
                    package_key="technical",
                    package_title="Technical",
                    section_key="technical_composition:stale_business_license",
                    item_title="Stale Business License",
                    requirement_type="profile",
                    profile_category="certificate",
                    material_key=stale_material_key,
                    title="Stale Business License",
                    description="Old LLM technical composition row should not appear.",
                    fulfillment_mode="enterprise_profile",
                    status="approved",
                    priority="high",
                    submitted_profile_item_uuid=str(uuid.uuid4()),
                    normalized_json=dumps_json({"extractor": "llm_technical_composition"}),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="legacy_construction_plan",
                    package_key="technical",
                    package_title="Technical",
                    section_key="technical_construction_plan",
                    item_title="Construction Plan",
                    requirement_type="section_text",
                    profile_category="technical_solution",
                    material_key="technical:legacy_construction_plan:section_text",
                    title="Construction plan drafting material",
                    description="Legacy format-plan rule row should not appear after LLM composition exists.",
                    fulfillment_mode="enterprise_profile",
                    status="missing",
                    priority="high",
                    normalized_json=dumps_json({"extractor": "format_plan_rule"}),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements",
            params={"run_uuid": run.run_uuid, "package_key": "technical"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        rows = data["data"]
        assert [row["title"] for row in rows] == ["Business License"]
        assert data["summary"]["total"] == 1
        assert data["summary"]["missing_count"] == 0
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_material_requirement_list_keeps_empty_llm_technical_scope_on_refresh(client):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)

        db = SessionLocal()
        try:
            run_obj = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            run_obj.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "status": "generated",
                        "components": [
                            {
                                "component_key": "construction_plan",
                                "component_title": "Construction Plan",
                                "source_item_no": "7.3.10",
                                "classification": "tender_extracted_content",
                                "information_needs": [
                                    {
                                        "need_key": "project_scope",
                                        "need_title": "Project scope",
                                        "source_type": "tender_document",
                                    }
                                ],
                            }
                        ],
                        "requirement_sync": {"rows": []},
                    }
                }
            )
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="legacy_quality_safety",
                    package_key="technical",
                    package_title="Technical",
                    section_key="technical_quality_safety",
                    item_title="Quality Safety Measures",
                    requirement_type="section_text",
                    profile_category="technical_solution",
                    material_key="technical:legacy_quality_safety:section_text",
                    title="Legacy quality and safety drafting material",
                    description="Old format-plan row should not reappear after LLM composition has been generated.",
                    fulfillment_mode="enterprise_profile",
                    status="missing",
                    priority="high",
                    normalized_json=dumps_json({"extractor": "format_plan_rule"}),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements",
            params={"run_uuid": run.run_uuid, "package_key": "technical"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["data"] == []
        assert data["summary"]["total"] == 0
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_profile_match_refreshes_stale_auto_mismatch(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        wrong_profile = _create_active_profile(
            user,
            category="basic_info",
            title="Quality and schedule commitment guarantee measures",
            summary="Quality assurance and construction period commitment text.",
        )
        correct_profile = _create_active_profile(
            user,
            category="project_performance",
            title="Similar project performance contracts list",
            summary="Completed similar decoration projects and contract references.",
        )

        db = SessionLocal()
        try:
            db.add(
                BidMaterialRequirement(
                    requirement_uuid=str(uuid.uuid4()),
                    project_id=project.id,
                    parse_run_id=run.id,
                    format_item_key="similar_project_experience",
                    package_key="technical",
                    package_title="Technical",
                    section_key="technical_composition:similar_project_experience",
                    item_title="Similar project experience",
                    requirement_type="profile",
                    profile_category="project_performance",
                    material_key="technical_composition:old_wrong_profile",
                    title="Similar project performance list",
                    description="Old auto mismatch should be refreshed.",
                    fulfillment_mode="enterprise_profile",
                    status="submitted",
                    priority="high",
                    owner_role="business",
                    candidate_profile_item_uuid=wrong_profile.item_uuid,
                    submitted_profile_item_uuid=wrong_profile.item_uuid,
                    normalized_json=dumps_json(
                        {
                            "extractor": "llm_technical_composition",
                            "material_identity": "profile:" + wrong_profile.item_uuid,
                            "manual_submission": {"profile_item_uuids": [wrong_profile.item_uuid]},
                        }
                    ),
                    evidence_json=dumps_json([]),
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "similar_project_experience",
                        "component_title": "Similar project experience",
                        "source_item_no": "7.3.7",
                        "package_key": "technical",
                        "order_index": 1,
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "Historical performance material.",
                        "source_evidence": [],
                        "information_needs": [
                            {
                                "need_key": "similar_projects",
                                "need_title": "Similar project performance list",
                                "source_type": "enterprise_profile",
                                "profile_category": "project_performance",
                                "query": "similar project performance contracts",
                                "reason": "Use completed similar project experience.",
                                "source_evidence": [],
                            }
                        ],
                    }
                ],
                "coverage_check": {"source_item_count": 1, "component_count": 1, "missing_source_item_nos": []},
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        rows = response.json()["generation"]["rows"]
        assert len(rows) == 1
        assert rows[0]["submitted_profile_item_uuid"] == correct_profile.item_uuid
        assert rows[0]["candidate_profile_item_uuid"] == correct_profile.item_uuid

        db = SessionLocal()
        try:
            db_rows = (
                db.query(BidMaterialRequirement)
                .filter(
                    BidMaterialRequirement.parse_run_id == run.id,
                    BidMaterialRequirement.package_key == "technical",
                    BidMaterialRequirement.section_key == "technical_composition:7_3_7",
                )
                .all()
            )
            assert len(db_rows) == 1
            assert db_rows[0].submitted_profile_item_uuid == correct_profile.item_uuid
            assert db_rows[0].submitted_profile_item_uuid != wrong_profile.item_uuid
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_material_requirement_candidate_quality_rejects_semantic_mismatch():
    class FakeProfile:
        title = "Similar project performance list"
        summary = "Completed similar decoration project contracts."
        content_text = ""
        profile_key = None
        subcategory = None
        tags_json = None
        category = "project_performance"

    query = "quality safety civilized construction guarantee measures"
    score = bidding_material_requirements._profile_candidate_match_score(query, FakeProfile())
    quality = bidding_material_requirements._profile_candidate_match_quality(
        query,
        "technical_solution",
        FakeProfile(),
        max(score, 0.6),
    )
    assert quality["eligible"] is False
    assert quality["reason"] == "required_phrase_missing"


def test_generate_technical_bid_draft_from_composition_mvp(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        license_profile = _create_active_profile(
            user,
            category="certificate",
            title="Business License",
            summary="Unified social credit code and stamped business license scan.",
        )

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "business_license",
                        "component_title": "Business License",
                        "source_item_no": "7.3.1",
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "Fixed enterprise certificate material.",
                        "source_evidence": [
                            {
                                "source_file": "tender.pdf",
                                "source_location": "7.3.1",
                                "original_text": "Submit business license copy.",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": "business_license",
                                "need_title": "Business License",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "Business License",
                                "reason": "Use enterprise profile license.",
                            }
                        ],
                    },
                    {
                        "component_key": "construction_plan",
                        "component_title": "Construction Organization Plan",
                        "source_item_no": "7.3.10",
                        "classification": "tender_extracted_content",
                        "classification_reason": "Project-specific method statement.",
                        "source_evidence": [
                            {
                                "source_file": "tender.pdf",
                                "source_location": "7.3.10",
                                "original_text": "Prepare construction plan covering safety and schedule.",
                            }
                        ],
                        "information_needs": [
                            {
                                "need_key": "project_scope",
                                "need_title": "Project scope and construction requirements",
                                "source_type": "tender_document",
                                "query": "Project scope, safety, schedule",
                                "polished_text": "The section shall cover project scope, safety management and schedule control.",
                                "reason": "Extract project-specific tender requirements.",
                            }
                        ],
                    },
                    {
                        "component_key": "key_difficulties",
                        "component_title": "Key Difficulties Analysis",
                        "source_item_no": "7.3.19",
                        "classification": "manual_input",
                        "classification_reason": "Needs project team judgement.",
                        "information_needs": [
                            {
                                "need_key": "difficulty_analysis",
                                "need_title": "Key difficulty judgement",
                                "source_type": "manual_input",
                                "query": "Key project difficulties",
                                "reason": "Need manual technical judgement.",
                            }
                        ],
                    },
                ],
                "coverage_check": {
                    "source_item_count": 3,
                    "component_count": 3,
                    "excluded_count": 0,
                    "missing_source_item_nos": [],
                },
                "warnings": [],
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        llm_draft_calls: list[str] = []

        async def fake_draft_llm(*args, **_kwargs):
            component = args[3]
            llm_draft_calls.append(component["component_key"])
            title = component["component_title"]
            return f"""# {component['source_item_no']} {title}

## 编制目标
本章节依据招标文件对 {title} 的要求进行编制，围绕项目范围、施工组织、安全文明施工和进度控制形成可执行的技术响应。The section shall cover project scope, safety management and schedule control.

## 施工部署
我方将结合办公楼室内装饰工程特点，按工作面、专业工序和交叉作业关系组织施工，优先保障现场移交、材料进场、样板确认和隐蔽验收等关键节点。

## 过程控制
施工过程中建立技术交底、质量检查、安全巡查和问题闭环机制，对影响质量、安全、文明施工和工期的事项及时记录、整改并复核。

## 复核要求
正式投标前应由技术负责人复核本章节与招标范围、工期要求和企业资料引用的一致性，缺少硬事实时保留人工确认入口。
"""

        monkeypatch.setattr(
            "app.services.bidding_draft_sections._build_technical_composition_llm_content_markdown",
            fake_draft_llm,
        )

        composition_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert composition_response.status_code == 200, composition_response.text

        db = SessionLocal()
        try:
            stale_generated = BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                section_key="technical_composition:business_license",
                section_title="7.3.1 Business License",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_version=1,
                content_markdown="# stale generated business license draft",
                generator_type="rule",
                generator_model="old_component_key_model",
                review_status="draft",
                created_by=user.id,
            )
            stale_reviewed = BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                section_key="technical_composition:construction_plan",
                section_title="7.3.10 Construction Organization Plan",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_version=1,
                content_markdown="# reviewed old component-key draft",
                generator_type="llm",
                generator_model="old_component_key_model",
                review_status="reviewed",
                created_by=user.id,
            )
            db.add_all([stale_generated, stale_reviewed])
            db.commit()
        finally:
            db.close()

        draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-draft/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert draft_response.status_code == 200, draft_response.text
        result = draft_response.json()["data"]
        assert result["component_count"] == 3
        assert result["generated_count"] == 3
        assert result["created_count"] == 3
        assert result["stale_removed_count"] == 1
        assert result["placeholder_count"] == 1
        assert result["llm_generated_count"] == 2
        assert result["llm_fallback_count"] == 0
        assert llm_draft_calls == ["construction_plan", "key_difficulties"]
        assert {draft["section_key"] for draft in result["drafts"]} == {
            "technical_composition:7_3_1",
            "technical_composition:7_3_10",
            "technical_composition:7_3_19",
        }
        assert any("Business License" in draft["content_markdown"] for draft in result["drafts"])
        assert any("The section shall cover project scope" in draft["content_markdown"] for draft in result["drafts"])
        assert any("施工部署" in draft["content_markdown"] for draft in result["drafts"])

        db = SessionLocal()
        try:
            rows = (
                db.query(BidDraftSection)
                .filter(BidDraftSection.parse_run_id == run.id, BidDraftSection.section_key.like("technical_composition:%"))
                .all()
            )
            assert len(rows) == 4
            assert not any(row.section_key == "technical_composition:business_license" for row in rows)
            assert any(row.section_key == "technical_composition:construction_plan" for row in rows)
            license_draft = next(row for row in rows if row.section_key == "technical_composition:7_3_1")
            construction_draft = next(row for row in rows if row.section_key == "technical_composition:7_3_10")
            difficulties_draft = next(row for row in rows if row.section_key == "technical_composition:7_3_19")
            assert license_profile.title in license_draft.content_markdown
            assert license_draft.generator_type == "rule"
            assert construction_draft.generator_type == "llm"
            assert difficulties_draft.generator_type == "llm"
            assert "待人工确认" in difficulties_draft.content_markdown
        finally:
            db.close()

        list_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections",
            params={"run_uuid": run.run_uuid, "package_key": "technical"},
            headers=headers,
        )
        assert list_response.status_code == 200, list_response.text
        draft_keys = {item["section_key"] for item in list_response.json()["data"]}
        assert "technical_composition:7_3_1" in draft_keys
        assert "technical_composition:7_3_10" in draft_keys
        assert "technical_composition:7_3_19" in draft_keys
        assert "technical_composition:business_license" not in draft_keys
        assert "technical_composition:construction_plan" not in draft_keys
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_llm_payload_uses_project_tender_and_profile_context(client, monkeypatch):
    old_provider = _set_flag("bidding_llm_provider", "deepseek")
    old_key = _set_flag("deepseek_api_key", "test-deepseek-key")
    captured: dict[str, object] = {}
    try:
        user = _create_user("admin")
        profile = _create_active_profile(
            user,
            category="technical_solution",
            title="装饰工程施工组织管理经验",
            summary="公司具备商业办公装修项目施工组织、质量安全巡检和文明施工管理经验。",
        )
        background_text = (
            "工程概况：东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程。"
            "招标范围包括商业街区及办公区室内装修，质量标准为合格，需重点关注安全文明施工、成品保护和交叉作业协调。"
        )
        project, run = _create_project_run_and_file(
            user,
            extracted_text=background_text,
            segments=[{"source_file": "招标文件.pdf", "source_location": "工程概况", "text": background_text}],
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "content_markdown": (
                                            "# 7.3.10 施工组织设计\n\n"
                                            "## 编制目标\n"
                                            "本章节结合东莞香港中心项目装修范围、质量标准和安全文明施工要求编制。\n\n"
                                            "## 实施安排\n"
                                            "围绕商业街区与办公区交叉作业组织施工部署，明确材料进场、样板确认和工序衔接。\n\n"
                                            "## 过程控制\n"
                                            "依托企业既有施工组织管理经验开展质量安全巡检、文明施工检查和问题闭环。\n\n"
                                            "## 复核要求\n"
                                            "正式投标前复核招标范围、质量目标和企业资料引用是否一致。"
                                        )
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        async def fake_post_json_via_gateway(**kwargs):
            captured["payload"] = kwargs["json_payload"]
            captured["endpoint_type"] = kwargs["endpoint_type"]
            return FakeResponse()

        monkeypatch.setattr("app.services.bidding_draft_sections.post_json_via_gateway", fake_post_json_via_gateway)

        db = SessionLocal()
        try:
            project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
            run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            material = BidMaterialRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                format_item_key="construction_plan",
                package_key="technical",
                package_title="技术标",
                section_key="technical_composition:construction_plan",
                item_title="施工组织设计",
                requirement_type="profile_material",
                profile_category="technical_solution",
                material_key="technical_composition:construction_plan:profile",
                title="施工组织设计企业经验素材",
                description="引用企业资料库中与施工组织、质量安全管理相关的经验素材。",
                source_file="招标文件.pdf",
                source_location="7.3.10",
                source_text="针对本工程的施工组织设计。",
                fulfillment_mode="enterprise_profile",
                status="submitted",
                priority="normal",
                owner_role="技术",
                submitted_profile_item_uuid=profile.item_uuid,
                normalized_json=dumps_json({"manual_submission": {"profile_item_uuids": [profile.item_uuid]}}),
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(material)
            db.flush()
            component = {
                "component_key": "construction_plan",
                "component_title": "施工组织设计",
                "source_item_no": "7.3.10",
                "classification": "mixed",
                "classification_reason": "方案型章节，需要结合项目背景和企业管理经验编写。",
                "information_needs": [
                    {
                        "need_key": "project_scope",
                        "need_title": "招标范围与施工要求",
                        "source_type": "tender_document",
                        "query": "招标范围 质量 安全文明施工",
                        "polished_text": "需结合商业街区及办公区室内装修范围、质量标准和安全文明施工要求编写。",
                    },
                    {
                        "need_key": "company_method",
                        "need_title": "施工组织管理经验",
                        "source_type": "enterprise_profile",
                        "profile_category": "technical_solution",
                        "query": "装饰工程施工组织管理经验",
                    },
                ],
            }
            content = asyncio.run(
                bidding_draft_sections._build_technical_composition_llm_content_markdown(
                    db,
                    project_db,
                    run_db,
                    component,
                    [material],
                    [
                        {
                            "source_kind": "technical_composition_source",
                            "source_file": "招标文件.pdf",
                            "source_location": "7.3.10",
                            "original_text": "针对本工程的施工组织设计。",
                        }
                    ],
                    [],
                    rule_content="# 7.3.10 施工组织设计\n\n## 投标响应草稿\n- 规则兜底稿",
                    order_index=10,
                    username=user.username,
                    trace_id=run_db.run_uuid,
                )
            )
            assert "施工组织设计" in content
            payload = captured["payload"]
            assert captured["endpoint_type"] == "bidding_technical_composition_draft"
            prompt_message = next(item for item in payload["messages"] if item.get("role") == "user")
            prompt_payload = json.loads(prompt_message["content"])
            assert "document_anti_repetition" in prompt_payload
            assert prompt_payload["project"]["project_name"] == project_db.project_name
            assert prompt_payload["component"]["source_item_no"] == "7.3.10"
            assert "项目组织架构与职责" in prompt_payload["expected_headings"]
            assert any("施工组织设计必须形成正式技术标章节" in item for item in prompt_payload["writing_requirements"])
            assert prompt_payload["project_context"]["work_zone_names"] == ["商业街区", "办公区"]
            assert prompt_payload["project_facts"]["scope"]["work_zones"] == ["商业街区", "办公区"]
            assert prompt_payload["project_facts"]["quality"]["goal"] == "合格"
            assert "东莞香港中心" in json.dumps(prompt_payload["tender_background_segments"], ensure_ascii=False)
            profile_payload = prompt_payload["enterprise_profile_materials"][0]["profile_items"][0]
            assert profile_payload["title"] == profile.title
            assert "质量安全巡检" in profile_payload["content_text"]
        finally:
            db.close()
    finally:
        _set_flag("bidding_llm_provider", old_provider)
        _set_flag("deepseek_api_key", old_key)


def test_technical_composition_rule_draft_expands_7309_schedule_plan_depth():
    component = {
        "component_key": "schedule_plan",
        "component_title": "施工总进度计划",
        "source_item_no": "7.3.9",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合总工期、材料和设备进场时间编制施工总进度计划。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.9",
                "original_text": "施工总进度计划(包括总工期、主要材料与详细设备进场时间等)。",
            }
        ],
        "information_needs": [
            {
                "need_key": "schedule_scope",
                "need_title": "施工总进度计划要求",
                "source_type": "tender_document",
                "query": "施工总进度计划 总工期 主要材料 设备进场时间",
                "polished_text": "需覆盖总工期、主要材料与详细设备进场时间、阶段施工节点和进度控制。",
            }
        ],
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
    )

    assert not placeholders
    assert "## 工期响应与编制原则" in content
    assert "## 总体进度安排" in content
    assert "## 阶段施工计划" in content
    assert "## 主要材料进场计划" in content
    assert "## 主要设备及机械进场计划" in content
    assert "## 劳动力与交叉作业协调" in content
    assert "## 进度检查、纠偏与验收移交" in content
    assert "总控计划" in content
    assert "样板确认" in content
    assert "隐蔽验收" in content
    assert "材料报审" in content
    assert "设备及机具按施工阶段分批进场" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "商业街区" not in content
    assert "6#楼32F办公区" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 8
    assert "主要材料进场计划" in decision["writing_plan"]["suggested_headings"]
    assert any("施工总进度计划必须形成正式进度计划章节" in item for item in bidding_draft_sections._technical_composition_writing_requirements(component))


def test_technical_composition_rule_drafts_use_project_context_without_previous_project_leakage():
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "schedule_zone_phrase": "后厨区、就餐区及相关专业工作面",
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
    }
    components = [
        {
            "component_key": "schedule_plan",
            "component_title": "施工总进度计划",
            "source_item_no": "7.3.9",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖总工期、材料设备进场时间。"}],
        },
        {
            "component_key": "construction_plan",
            "component_title": "施工组织设计",
            "source_item_no": "7.3.10",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖组织架构、工序衔接。"}],
        },
        {
            "component_key": "safety_civil_fire_plan",
            "component_title": "安全生产、文明施工、防火施工方案和保证措施",
            "source_item_no": "7.3.15",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖安全文明和防火施工。"}],
        },
    ]

    contents = []
    for index, component in enumerate(components, start=1):
        content, _evidence, placeholders, _warnings, _decision = bidding_draft_sections._build_technical_composition_draft_content(
            None,
            component,
            [],
            order_index=index,
            project_context=project_context,
        )
        assert not placeholders
        contents.append(content)

    joined = "\n".join(contents)
    assert "后厨区、就餐区及相关专业工作面" in joined
    assert "后厨区、就餐区及相关配合区域" in joined
    assert "后厨区、就餐区及周边受影响区域" in joined
    assert "商业街区" not in joined
    assert "6#楼32F办公区" not in joined
    assert "东莞香港中心" not in joined


def test_technical_composition_rule_drafts_route_by_intent_without_73_section_numbers():
    cases = [
        (
            {
                "component_key": "chapter_8_schedule",
                "component_title": "总进度与材料设备进场计划",
                "source_item_no": "8.2",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需说明总工期、主要材料和设备进场时间。"}],
            },
            "schedule_plan",
            "## 主要材料进场计划",
        ),
        (
            {
                "component_key": "chapter_9_organization",
                "component_title": "项目施工部署及组织架构",
                "source_item_no": "9.1",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖施工部署、组织架构、工序衔接。"}],
            },
            "construction_organization",
            "## 项目组织架构与职责",
        ),
        (
            {
                "component_key": "chapter_10_safety",
                "component_title": "安全文明施工与消防管理方案",
                "source_item_no": "10.3",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖安全文明、动火消防和临时用电管理。"}],
            },
            "safety_civil_fire",
            "## 临时用电、动火与消防管理",
        ),
        (
            {
                "component_key": "chapter_11_quality",
                "component_title": "质量保证体系及措施",
                "source_item_no": "11.5",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖质量保证体系、隐蔽验收和实测实量。"}],
            },
            "quality_assurance",
            "## 工序过程控制与隐蔽验收",
        ),
        (
            {
                "component_key": "chapter_12_power",
                "component_title": "施工临时用电专项方案",
                "source_item_no": "12.1",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖三级配电、漏电保护、配电箱巡检和停送电管理。"}],
            },
            "temporary_power_plan",
            "## 配电系统与箱体布置",
        ),
        (
            {
                "component_key": "chapter_13_material",
                "component_title": "主要材料采购计划及报审管理",
                "source_item_no": "13.2",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖材料采购、样板确认、材料报审和分批进场。"}],
            },
            "material_procurement_plan",
            "## 品牌规格、样板与报审复核",
        ),
        (
            {
                "component_key": "chapter_14_difficulty",
                "component_title": "项目重点难点分析及对策",
                "source_item_no": "14.3",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需分析施工难点、交叉作业、成品保护和验收移交。"}],
            },
            "key_difficulty_analysis",
            "## 项目特点与重难点识别",
        ),
        (
            {
                "component_key": "chapter_15_site_facility",
                "component_title": "办公室工具间材料间现场管理方案",
                "source_item_no": "15.1",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖办公室、工具间、材料间布置和台账管理。"}],
            },
            "site_facility_management",
            "## 工具间管理",
        ),
        (
            {
                "component_key": "chapter_16_waste",
                "component_title": "装修垃圾清理堆放运输管理方案",
                "source_item_no": "16.2",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖垃圾清理、临时堆放、运输路线和工完场清。"}],
            },
            "waste_management_plan",
            "## 临时堆放点与堆场管理",
        ),
        (
            {
                "component_key": "chapter_17_samples",
                "component_title": "主要材料样板提供及封样计划",
                "source_item_no": "17.3",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖材料样板、样板报审、封样确认和进场复核。"}],
            },
            "material_sample_plan",
            "## 样板制作、封样确认与留存管理",
        ),
        (
            {
                "component_key": "chapter_18_competitive",
                "component_title": "提升投标竞争力的技术优势和增值服务",
                "source_item_no": "18.4",
                "classification": "tender_extracted_content",
                "information_needs": [{"source_type": "tender_document", "polished_text": "需说明投标竞争力、技术优势、增值服务和持续改进。"}],
            },
            "competitive_enhancement",
            "## 投标竞争力提升总体思路",
        ),
    ]

    for index, (component, expected_intent, expected_heading) in enumerate(cases, start=1):
        content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
            None,
            component,
            [],
            order_index=index,
        )

        assert not placeholders
        assert decision["section_intent"] == expected_intent
        assert expected_heading in content
        assert "## 投标响应草稿" not in content


def test_technical_composition_intent_template_drives_alias_headings_and_requirements():
    template = bidding_draft_sections.TECHNICAL_COMPOSITION_INTENT_TEMPLATES["quality_assurance"]
    component = {
        "component_key": "custom_quality",
        "component_title": "质量保证体系及措施",
        "source_item_no": "第十一章",
        "classification": "tender_extracted_content",
        "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖质量保证体系。"}],
    }

    assert "质量保证体系" in template["aliases"]
    assert bidding_draft_sections._technical_composition_intent(component) == "quality_assurance"
    assert bidding_draft_sections._technical_composition_rule_suggested_headings(component) == list(template["headings"])
    for requirement in template["writing_requirements"]:
        assert requirement in bidding_draft_sections._technical_composition_writing_requirements(component)


def test_technical_composition_rule_draft_expands_7310_construction_organization_depth():
    component = {
        "component_key": "construction_plan",
        "component_title": "施工组织设计",
        "source_item_no": "7.3.10",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合项目背景编制施工组织设计。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.10",
                "original_text": "针对本工程的施工组织设计，主要包括组织架构、临时设施安排、安全措施、文明施工措施、对周围环境保护、成品保护等。",
            }
        ],
        "information_needs": [
            {
                "need_key": "construction_scope",
                "need_title": "施工组织设计项目化要求",
                "source_type": "tender_document",
                "query": "组织架构 临时设施 安全文明施工 成品保护",
                "polished_text": "需覆盖组织架构、临时设施安排、安全文明施工、环境保护和成品保护。",
            }
        ],
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
    )

    assert not placeholders
    assert "## 施工组织总体部署" in content
    assert "## 项目组织架构与职责" in content
    assert "## 施工流程与工序衔接" in content
    assert "## 资源投入与现场平面管理" in content
    assert "## 质量、安全文明与成品保护" in content
    assert "## 进度协调与验收移交" in content
    assert "## 应急与沟通机制" in content
    assert "样板先行" in content
    assert "隐蔽验收" in content
    assert "交叉作业协调" in content
    assert "成品保护" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "商业街区" not in content
    assert "6#楼32F办公区" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 8
    assert "项目组织架构与职责" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7315_safety_civil_fire_depth():
    component = {
        "component_key": "safety_civil_fire_plan",
        "component_title": "安全生产、文明施工、防火施工方案和保证措施",
        "source_item_no": "7.3.15",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合项目背景编制安全文明与防火专项方案。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.15",
                "original_text": "提供详细的安全生产、文明施工、防火施工方案和保证措施。",
            }
        ],
        "information_needs": [
            {
                "need_key": "safety_civil_fire_scope",
                "need_title": "安全文明与防火施工要求",
                "source_type": "tender_document",
                "query": "安全生产 文明施工 防火施工 保证措施",
                "polished_text": "需覆盖安全生产、文明施工、防火施工方案和保证措施。",
            }
        ],
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
    )

    assert not placeholders
    assert "## 安全生产管理目标与责任体系" in content
    assert "## 安全教育交底与作业许可" in content
    assert "## 临时用电、动火与消防管理" in content
    assert "## 文明施工与环境保护" in content
    assert "## 高处、临边及交叉作业控制" in content
    assert "## 检查整改、应急处置与资料闭环" in content
    assert "三级配电" in content
    assert "动火作业执行审批" in content
    assert "消防通道" in content
    assert "工完场清" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "商业街区" not in content
    assert "6#楼32F办公区" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 8
    assert "临时用电、动火与消防管理" in decision["writing_plan"]["suggested_headings"]
    assert any("安全生产、文明施工、防火施工方案和保证措施必须形成正式专项章节" in item for item in bidding_draft_sections._technical_composition_writing_requirements(component))


def test_technical_composition_rule_draft_expands_7316_quality_assurance_depth():
    component = {
        "component_key": "quality_assurance_measures",
        "component_title": "重要的施工质量保障措施",
        "source_item_no": "7.3.16",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合项目背景编制施工质量保障措施。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.16",
                "original_text": "重要的施工质量保障措施。",
            }
        ],
        "information_needs": [
            {
                "need_key": "quality_assurance_scope",
                "need_title": "施工质量保障措施要求",
                "source_type": "tender_document",
                "query": "质量保障 样板引路 隐蔽验收 质量通病 成品保护",
                "polished_text": "需覆盖施工质量目标、样板引路、隐蔽验收、过程检查和质量整改闭环。",
            }
        ],
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
    )

    assert not placeholders
    assert "## 质量目标与管理责任体系" in content
    assert "## 样板引路与技术交底" in content
    assert "## 材料设备进场与报审复核" in content
    assert "## 工序过程控制与隐蔽验收" in content
    assert "## 实测实量与质量通病防治" in content
    assert "## 成品保护、整改复验与资料闭环" in content
    assert "样板先行" in content
    assert "隐蔽验收" in content
    assert "实测实量" in content
    assert "质量通病" in content
    assert "检验批" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 8
    assert "工序过程控制与隐蔽验收" in decision["writing_plan"]["suggested_headings"]
    assert any("施工质量保障措施必须形成正式专项章节" in item for item in bidding_draft_sections._technical_composition_writing_requirements(component))


def test_technical_composition_rule_draft_expands_7313_temporary_power_depth():
    component = {
        "component_key": "temporary_power_plan",
        "component_title": "施工临时用电施工方案",
        "source_item_no": "7.3.13",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合现场用电组织编制施工临时用电施工方案。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.13",
                "original_text": "施工临时用电的施工方案。",
            }
        ],
        "information_needs": [
            {
                "need_key": "temporary_power_scope",
                "need_title": "施工临时用电要求",
                "source_type": "tender_document",
                "query": "临时用电 三级配电 漏电保护 配电箱 巡检",
                "polished_text": "需覆盖临时用电组织管理、三级配电、漏电保护、线路敷设、巡检维护和应急处置。",
            }
        ],
    }
    project_context = {
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
        "schedule": {"total_duration_days": 60},
        "quality": {"goal": "合格"},
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "temporary_power_plan"
    assert "## 临时用电管理目标与组织职责" in content
    assert "## 配电系统与箱体布置" in content
    assert "## 线路敷设、照明与机具用电控制" in content
    assert "## 动火、潮湿区域及交叉作业用电管理" in content
    assert "## 巡检维护、停送电与应急处置" in content
    assert "三级配电" in content
    assert "一机一闸一漏一箱" in content
    assert "后厨区、就餐区及周边受影响区域" in content
    assert "待确认" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 7
    assert "配电系统与箱体布置" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7314_material_procurement_depth():
    component = {
        "component_key": "material_procurement_plan",
        "component_title": "主要材料的采购计划（含甲指乙供材料）",
        "source_item_no": "7.3.14",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合材料品牌、样板和进场批次编制采购计划。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.14",
                "original_text": "主要材料的采购计划（含甲指乙供材料）。",
            }
        ],
        "information_needs": [
            {
                "need_key": "material_procurement_scope",
                "need_title": "主要材料采购要求",
                "source_type": "tender_document",
                "query": "材料采购 甲指乙供 样板确认 材料报审 进场验收",
                "polished_text": "需覆盖材料采购计划、样板确认、材料报审、甲指乙供配合和进场验收。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "schedule": {"total_duration_days": 60},
        "quality": {"goal": "合格"},
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "material_procurement_plan"
    assert "## 采购组织原则与责任分工" in content
    assert "## 材料需求计划与进场批次" in content
    assert "## 品牌规格、样板与报审复核" in content
    assert "## 供应周期、运输到场与现场验收" in content
    assert "## 甲指乙供、替代审批与风险纠偏" in content
    assert "样板确认" in content
    assert "材料报审" in content
    assert "替代材料必须经审批确认后使用" in content
    assert "后厨区、就餐区及相关配合区域" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 7
    assert "品牌规格、样板与报审复核" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7319_key_difficulty_depth():
    component = {
        "component_key": "key_difficulty_analysis",
        "component_title": "项目重难点分析",
        "source_item_no": "7.3.19",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合项目范围、交叉作业和成品保护分析重难点。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.19",
                "original_text": "项目重难点分析。",
            }
        ],
        "information_needs": [
            {
                "need_key": "key_difficulty_scope",
                "need_title": "项目重难点分析要求",
                "source_type": "tender_document",
                "query": "重难点 交叉作业 成品保护 材料供应 验收移交",
                "polished_text": "需覆盖工期组织、交叉作业、材料样板、成品保护、安全文明和验收移交。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
        "scope": {"scope_text": "后厨区及就餐区室内装饰装修"},
        "schedule": {"total_duration_days": 60},
        "quality": {"goal": "合格"},
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "key_difficulty_analysis"
    assert "## 项目特点与重难点识别" in content
    assert "## 工期组织与交叉作业控制" in content
    assert "## 材料样板、报审与供应保障" in content
    assert "## 隐蔽工程、细部收口与观感质量控制" in content
    assert "## 成品保护、既有设施与现场秩序维护" in content
    assert "## 重难点跟踪、纠偏与验收移交" in content
    assert "难点：" in content
    assert "对策：" in content
    assert "后厨区及就餐区室内装饰装修" in content
    assert "质量目标为“合格”" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 8
    assert "项目特点与重难点识别" in decision["writing_plan"]["suggested_headings"]


def test_technical_quality_gate_includes_p0_depth_intents():
    assert {
        "temporary_power_plan",
        "material_procurement_plan",
        "key_difficulty_analysis",
        "site_facility_management",
        "waste_management_plan",
        "material_sample_plan",
        "competitive_enhancement",
    }.issubset(bidding_technical_quality.TECHNICAL_FORMAL_DEPTH_INTENTS)

    draft = BidDraftSection(
        section_key="technical_composition:7_3_13",
        section_title="7.3.13 施工临时用电施工方案",
        content_markdown="# 7.3.13 施工临时用电施工方案\n临时用电按现场要求执行。",
    )
    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[draft],
        components_by_key={
            "technical_composition:7_3_13": {
                "source_item_no": "7.3.13",
                "component_title": "施工临时用电施工方案",
            }
        },
        final_content_by_draft_id={},
    )

    assert report["status"] == "warning"
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "section_depth_weak" in warning_codes


def test_technical_quality_gate_builds_p0_formal_profile_gap_report():
    brand_draft = BidDraftSection(
        section_key="technical_composition:7_3_18",
        section_title="7.3.18 材料品牌表",
        content_markdown="# 7.3.18 材料品牌表\n材料品牌按招标文件要求执行。",
    )
    organization_draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown="# 7.3.10 施工组织设计\n本工程按装饰装修施工要求组织实施。",
    )

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[brand_draft, organization_draft],
        components_by_key={
            "technical_composition:7_3_18": {
                "source_item_no": "7.3.18",
                "component_title": "材料品牌表",
            },
            "technical_composition:7_3_10": {
                "source_item_no": "7.3.10",
                "component_title": "施工组织设计",
            },
        },
        final_content_by_draft_id={},
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["status"] == "warning"
    assert report["formal_profile"]["version"] == bidding_technical_quality.BID_TECHNICAL_FORMAL_PROFILE_VERSION
    assert report["formal_profile"]["generated_section_count"] == 2
    assert report["formal_profile"]["fixed_material_section_count"] == 1
    assert "formal_fixed_material_signal_weak" in warning_codes
    assert "formal_table_structure_missing" in warning_codes
    assert "formal_section_depth_weak" in warning_codes
    assert "formal_required_topic_missing" in warning_codes
    assert report["formal_profile"]["warning_code_counts"]["formal_table_structure_missing"] == 1
    profile_check_codes = {item["code"] for item in report["formal_profile"]["checks"]}
    assert {"composition_draft_coverage", "fixed_material_sections", "table_sections", "scheme_section_depth"}.issubset(profile_check_codes)


def test_technical_quality_gate_builds_p2_requirement_coverage_matrix():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown=(
            "# 7.3.10 施工组织设计\n"
            "本工程设置项目组织架构，项目经理、技术负责人、安全负责人形成职责分工，"
            "并按施工部署组织现场协调、工序交叉配合和质量安全文明施工检查。\n\n"
            "项目部建立材料进场、样板引路和成品保护联动机制，按周检查现场问题，"
            "对整改闭环、资料归档、班组交底和总包协调形成台账，确保装修施工组织连续受控。\n"
        ),
    )

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[draft],
        components_by_key={
            "technical_composition:7_3_10": {
                "source_item_no": "7.3.10",
                "component_title": "施工组织设计",
                "classification": "mixed",
                "classification_reason": "需结合项目组织架构、职责分工和施工部署编写。",
                "source_evidence": [{"original_text": "7.3.10 施工组织设计"}],
                "information_needs": [
                    {
                        "need_key": "organization_duty",
                        "need_title": "组织架构与职责分工",
                        "source_type": "tender_document",
                        "query": "项目组织架构 职责分工 施工部署",
                        "polished_text": "需覆盖项目组织架构、职责分工和施工部署。",
                    },
                    {
                        "need_key": "sample_acceptance",
                        "need_title": "样板引路与移交验收",
                        "source_type": "tender_document",
                        "query": "样板引路 移交验收 售后服务",
                        "polished_text": "需覆盖样板引路、移交验收和售后服务。",
                    },
                    {
                        "need_key": "weak_current",
                        "need_title": "弱电综合布线保护",
                        "source_type": "tender_document",
                        "query": "弱电综合布线 线缆保护 末端测试",
                        "polished_text": "需覆盖弱电综合布线、线缆保护和末端测试。",
                    },
                    {
                        "need_key": "award_material",
                        "need_title": "企业获奖证明",
                        "source_type": "manual_input",
                        "query": "企业获奖证明 奖项名称 证书编号",
                        "polished_text": "需补充企业获奖证明、奖项名称和证书编号。",
                    },
                ],
            }
        },
        final_content_by_draft_id={},
    )

    coverage = report["requirement_coverage"]
    assert coverage["version"] == bidding_technical_quality.BID_TECHNICAL_REQUIREMENT_COVERAGE_VERSION
    assert coverage["status"] == "warning"
    assert coverage["requirement_count"] == 5
    assert coverage["missing_count"] == 1
    assert coverage["needs_manual_review_count"] == 1
    assert coverage["partially_covered_count"] >= 1

    status_by_title = {item["requirement_title"]: item["coverage_status"] for item in coverage["items"]}
    assert status_by_title["组织架构与职责分工"] == "covered"
    assert status_by_title["样板引路与移交验收"] == "partially_covered"
    assert status_by_title["弱电综合布线保护"] == "missing"
    assert status_by_title["企业获奖证明"] == "needs_manual_review"

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "technical_requirement_coverage_gap" in warning_codes
    assert report["formal_profile"]["warning_code_counts"]["technical_requirement_coverage_gap"] == 1


def test_technical_bid_final_word_export_p3_auto_reinforces_requirement_coverage_gap(monkeypatch):
    monkeypatch.setattr(
        bidding_technical_word_export,
        "_requirements_for_draft_attachments",
        lambda db, run, draft: [],
    )
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown=(
            "# 7.3.10 施工组织设计\n"
            "本章按施工组织、项目部职责、材料进场和质量安全文明施工要求组织实施。\n"
            "项目部建立周检查、整改闭环和资料归档机制，保证现场组织受控。\n"
        ),
    )
    draft.id = 93010
    component = {
        "source_item_no": "7.3.10",
        "component_title": "施工组织设计",
        "classification": "mixed",
        "information_needs": [
            {
                "need_key": "weak_current",
                "need_title": "弱电综合布线保护",
                "source_type": "tender_document",
                "query": "弱电综合布线 线缆保护 末端测试",
                "polished_text": "需覆盖弱电综合布线、线缆保护和末端测试。",
            },
            {
                "need_key": "award_material",
                "need_title": "企业获奖证明",
                "source_type": "manual_input",
                "query": "企业获奖证明 奖项名称 证书编号",
                "polished_text": "需补充企业获奖证明、奖项名称和证书编号。",
            },
        ],
    }

    updated = bidding_technical_word_export._final_export_content_for_draft(
        None,
        BidParseRun(id=1),
        draft,
        component,
    )

    assert "招标要求深化响应" in updated
    assert "弱电综合布线" in updated
    assert "线缆保护" in updated
    assert "末端测试" in updated
    assert "奖项名称" not in updated
    assert "证书编号" not in updated

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="测试项目",
        project_context={},
        project_facts={},
        drafts=[draft],
        components_by_key={"technical_composition:7_3_10": component},
        final_content_by_draft_id={draft.id: updated},
    )
    status_by_title = {item["requirement_title"]: item["coverage_status"] for item in report["requirement_coverage"]["items"]}
    assert status_by_title["弱电综合布线保护"] == "covered"
    assert status_by_title["企业获奖证明"] == "needs_manual_review"


def test_technical_bid_final_quality_report_includes_p4_reinforcement_audit(client, monkeypatch):
    monkeypatch.setattr(
        bidding_technical_word_export,
        "_requirements_for_draft_attachments",
        lambda db, run, draft: [],
    )
    old_bidding = _set_flag("feature_bidding_mvp", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        db = SessionLocal()
        try:
            project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
            run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            component = {
                "source_item_no": "7.3.10",
                "component_key": "construction_organization",
                "component_title": "施工组织设计",
                "classification": "mixed",
                "information_needs": [
                    {
                        "need_key": "weak_current",
                        "need_title": "弱电综合布线保护",
                        "source_type": "tender_document",
                        "query": "弱电综合布线 线缆保护 末端测试",
                        "polished_text": "需覆盖弱电综合布线、线缆保护和末端测试。",
                    },
                    {
                        "need_key": "award_material",
                        "need_title": "企业获奖证明",
                        "source_type": "manual_input",
                        "query": "企业获奖证明 奖项名称 证书编号",
                        "polished_text": "需补充企业获奖证明、奖项名称和证书编号。",
                    },
                ],
            }
            run_db.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "status": "generated",
                        "components": [component],
                    }
                }
            )
            db.add(
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_10",
                    section_title="7.3.10 施工组织设计",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    created_by=user.id,
                    content_markdown=(
                        "# 7.3.10 施工组织设计\n"
                        "本章按施工组织、项目部职责、材料进场和质量安全文明施工要求组织实施。\n"
                        "项目部建立周检查、整改闭环和资料归档机制，保证现场组织受控。\n"
                    ),
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-final/quality",
            params={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        quality_report = response.json()["data"]["quality_report"]
        audit = quality_report["requirement_reinforcement"]
        assert audit["version"] == bidding_technical_word_export.BID_TECHNICAL_FINAL_QUALITY_VISIBILITY_VERSION
        assert audit["status"] == "applied"
        assert audit["reinforced_section_count"] == 1
        assert audit["auto_reinforced_count"] == 1
        assert audit["manual_review_count"] == 1
        transition = audit["transitions"][0]
        assert transition["requirement_title"] == "弱电综合布线保护"
        assert transition["before_status"] == "missing"
        assert transition["after_status"] == "covered"
        skipped = audit["skipped_items"][0]
        assert skipped["requirement_title"] == "企业获奖证明"
        assert skipped["coverage_status"] == "needs_manual_review"
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)


def test_technical_composition_rule_draft_expands_7311_site_facility_depth():
    component = {
        "component_key": "site_facility_management",
        "component_title": "办公室、工具间、材料间的管理方案",
        "source_item_no": "7.3.11",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合现场平面和文明施工要求编制临时设施管理方案。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.11",
                "original_text": "办公室、工具间、材料间的管理方案。",
            }
        ],
        "information_needs": [
            {
                "need_key": "site_facility_scope",
                "need_title": "办公室工具间材料间管理要求",
                "source_type": "tender_document",
                "query": "办公室 工具间 材料间 消防 临电 台账",
                "polished_text": "需覆盖办公室、工具间、材料间布置、消防临电、文明施工和台账管理。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "site_facility_management"
    assert "## 临时设施布置原则与管理目标" in content
    assert "## 办公室管理" in content
    assert "## 工具间管理" in content
    assert "## 材料间管理" in content
    assert "## 消防、临电、文明施工与成品保护" in content
    assert "收发存台账" in content
    assert "领用登记" in content
    assert "后厨区、就餐区及相关配合区域" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 7
    assert "工具间管理" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7312_waste_management_depth():
    component = {
        "component_key": "waste_management_plan",
        "component_title": "垃圾的清理、堆放、运输、垃圾堆场管理方案",
        "source_item_no": "7.3.12",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合文明施工要求编制垃圾清运管理方案。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.12",
                "original_text": "垃圾的清理、堆放、运输、垃圾堆场管理方案。",
            }
        ],
        "information_needs": [
            {
                "need_key": "waste_scope",
                "need_title": "垃圾清运管理要求",
                "source_type": "tender_document",
                "query": "垃圾清理 垃圾堆放 垃圾运输 工完场清",
                "polished_text": "需覆盖垃圾分类清理、临时堆放、场内运输、外运配合和工完场清。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "waste_management_plan"
    assert "## 垃圾管理目标与责任分工" in content
    assert "## 分类收集与日常清理" in content
    assert "## 临时堆放点与堆场管理" in content
    assert "## 场内运输、外运配合与路线控制" in content
    assert "## 扬尘、噪声、消防和安全文明控制" in content
    assert "工完场清" in content
    assert "消防通道" in content
    assert "后厨区、就餐区及周边受影响区域" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 7
    assert "临时堆放点与堆场管理" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7317_material_sample_depth():
    component = {
        "component_key": "material_sample_plan",
        "component_title": "主要材料样板提供计划",
        "source_item_no": "7.3.17",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合材料样板、规格尺寸和封样要求编制样板提供计划。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.17",
                "original_text": "投标单位按需于回标前提供主要材料样板，规格尺寸按发包人要求。",
            }
        ],
        "information_needs": [
            {
                "need_key": "sample_scope",
                "need_title": "材料样板提供要求",
                "source_type": "tender_document",
                "query": "材料样板 规格尺寸 样板报审 封样确认",
                "polished_text": "需覆盖材料样板清单、规格尺寸复核、样板报审、封样确认和进场复核。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "schedule": {"total_duration_days": 60},
        "quality": {"goal": "合格"},
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "material_sample_plan"
    assert "## 样板提供原则与责任分工" in content
    assert "## 样板清单、规格复核与报审计划" in content
    assert "## 样板制作、封样确认与留存管理" in content
    assert "## 样板与采购、进场和施工质量联动" in content
    assert "## 变更替代、复核纠偏与资料闭环" in content
    assert "封样" in content
    assert "样板报审" in content
    assert "批量材料与样板不一致" in content
    assert "质量目标为“合格”" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 6
    assert "样板制作、封样确认与留存管理" in decision["writing_plan"]["suggested_headings"]


def test_technical_composition_rule_draft_expands_7320_competitive_enhancement_depth():
    component = {
        "component_key": "competitive_enhancement",
        "component_title": "投标单位认为能提升投标竞争力的内容",
        "source_item_no": "7.3.20",
        "classification": "tender_extracted_content",
        "classification_reason": "需结合项目管理优势编制提升竞争力内容。",
        "source_evidence": [
            {
                "source_file": "招标文件.pdf",
                "source_location": "7.3.20",
                "original_text": "投标单位认为能提升投标竞争力的内容。",
            }
        ],
        "information_needs": [
            {
                "need_key": "competitive_scope",
                "need_title": "提升投标竞争力内容要求",
                "source_type": "tender_document",
                "query": "投标竞争力 技术优势 增值服务 合理化建议",
                "polished_text": "需覆盖进度组织、质量样板、安全文明、材料供应、风险响应和持续服务。",
            }
        ],
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "scope": {"scope_text": "后厨区及就餐区室内装饰装修"},
        "schedule": {"total_duration_days": 60},
        "quality": {"goal": "合格"},
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    assert not placeholders
    assert decision["section_intent"] == "competitive_enhancement"
    assert "## 投标竞争力提升总体思路" in content
    assert "## 进度、组织与协同优势" in content
    assert "## 质量、样板与精细化管控优势" in content
    assert "## 安全文明、成品保护与现场秩序优势" in content
    assert "## 材料供应、成本控制与风险响应优势" in content
    assert "## 服务承诺、资料移交与持续改进" in content
    assert "可执行、可检查、可追溯" in content
    assert "不以空泛承诺为主" in content
    assert "后厨区及就餐区室内装饰装修" in content
    assert "待人工完善" not in content
    assert "待补充" not in content
    assert "东莞香港中心" not in content
    assert bidding_draft_sections._technical_composition_paragraph_count(content) >= 7
    assert "投标竞争力提升总体思路" in decision["writing_plan"]["suggested_headings"]


def test_technical_project_facts_extract_generic_technical_requirements(client):
    user = _create_user("admin")
    extracted_text = (
        "招标范围：后厨区及就餐区室内装饰装修。"
        "总工期60天，质量目标为合格。"
        "安全文明施工应控制扬尘噪声并保持工完场清。"
        "临时用电应执行三级配电、漏电保护和配电箱巡检。"
        "装修垃圾应分类收集、袋装清运，保持工完场清。"
        "主要材料采购应结合品牌规格、供应周期和材料报审组织。"
        "主要材料样板须在回标前提供并封样确认，规格尺寸按发包人要求。"
        "施工过程应加强成品保护，并与总承包单位做好交叉作业协调。"
    )
    project, run = _create_project_run_and_file(user, extracted_text=extracted_text)

    db = SessionLocal()
    try:
        facts = bidding_draft_sections._technical_project_facts(db, project, run)
        context = bidding_draft_sections._technical_project_context(db, project, run)
    finally:
        db.close()

    requirements = facts["technical_requirements"]
    assert "扬尘噪声" in requirements["safety_civilized"]["summary"]
    assert "三级配电" in requirements["temporary_power"]["summary"]
    assert "袋装清运" in requirements["waste_management"]["summary"]
    assert "品牌规格" in requirements["material_procurement"]["summary"]
    assert "封样确认" in requirements["material_sample"]["summary"]
    assert "成品保护" in requirements["finished_product_protection"]["summary"]
    assert "总承包单位" in requirements["coordination"]["summary"]
    assert context["technical_requirements"]["temporary_power"]["summary"] == requirements["temporary_power"]["summary"]


def test_technical_composition_rule_drafts_use_extracted_requirement_facts():
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
        "technical_requirements": {
            "temporary_power": {
                "label": "临时用电",
                "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                "keywords": ["三级配电", "漏电保护"],
            },
            "waste_management": {
                "label": "垃圾清运",
                "summary": "装修垃圾应分类收集、袋装清运，保持工完场清",
                "keywords": ["袋装清运", "工完场清"],
            },
            "material_sample": {
                "label": "材料样板",
                "summary": "主要材料样板须在回标前提供并封样确认，规格尺寸按发包人要求",
                "keywords": ["封样确认", "规格尺寸"],
            },
            "finished_product_protection": {
                "label": "成品保护",
                "summary": "施工过程应加强成品保护，避免交叉污染和既有设施损坏",
                "keywords": ["成品保护", "既有设施"],
            },
            "coordination": {
                "label": "现场协调",
                "summary": "施工过程应与总承包单位做好交叉作业协调",
                "keywords": ["总承包", "交叉作业"],
            },
        },
    }
    components = [
        {
            "component_key": "temporary_power_plan",
            "component_title": "施工临时用电施工方案",
            "source_item_no": "7.3.13",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖施工临时用电。"}],
        },
        {
            "component_key": "waste_management_plan",
            "component_title": "垃圾清理、堆放、运输、堆场管理方案",
            "source_item_no": "7.3.12",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖垃圾清运。"}],
        },
        {
            "component_key": "material_sample_plan",
            "component_title": "主要材料样板提供计划",
            "source_item_no": "7.3.17",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖材料样板。"}],
        },
        {
            "component_key": "quality_assurance",
            "component_title": "重要的施工质量保障措施",
            "source_item_no": "7.3.16",
            "classification": "tender_extracted_content",
            "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖质量保障和成品保护。"}],
        },
    ]

    contents = []
    for index, component in enumerate(components, start=1):
        content, _evidence, placeholders, _warnings, _decision = bidding_draft_sections._build_technical_composition_draft_content(
            None,
            component,
            [],
            order_index=index,
            project_context=project_context,
        )
        assert not placeholders
        contents.append(content)

    joined = "\n".join(contents)
    assert "临时用电应执行三级配电、漏电保护和配电箱巡检" in joined
    assert "装修垃圾应分类收集、袋装清运，保持工完场清" in joined
    assert "主要材料样板须在回标前提供并封样确认，规格尺寸按发包人要求" in joined
    assert "施工过程应加强成品保护，避免交叉污染和既有设施损坏" in joined
    assert "施工过程应与总承包单位做好交叉作业协调" in joined
    assert "东莞香港中心" not in joined


def test_technical_quality_gate_warns_when_requirement_fact_not_used():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_13",
        section_title="7.3.13 施工临时用电施工方案",
        content_markdown=(
            "# 7.3.13 施工临时用电施工方案\n\n"
            "本章围绕现场施工用电组织实施，项目部将设置专人管理，并做好日常检查、问题整改、资料归档和应急处置。"
            "施工前完成报审，施工中保持安全可靠，发现异常及时处理。"
        ),
    )

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="后厨改造项目",
        project_context={
            "technical_requirements": {
                "temporary_power": {
                    "label": "临时用电",
                    "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                    "keywords": ["三级配电", "漏电保护", "配电箱巡检"],
                }
            }
        },
        project_facts={},
        drafts=[draft],
        components_by_key={
            "technical_composition:7_3_13": {
                "source_item_no": "7.3.13",
                "component_title": "施工临时用电施工方案",
            }
        },
        final_content_by_draft_id={},
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["status"] == "warning"
    assert "technical_requirement_fact_not_used" in warning_codes
    assert any(item.get("evidence", {}).get("fact_key") == "temporary_power" for item in report["warnings"])


def test_technical_quality_gate_allows_current_project_floor_alias():
    draft = BidDraftSection(
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
        content_markdown=(
            "# 7.3.10 施工组织设计\n\n"
            "本施工组织设计围绕东莞香港中心项目商业街区及6#楼32层办公区组织实施。"
            "项目部按工作面移交、材料进场、样板确认、成品保护和交叉作业协调推进。"
            "现场执行计划交底、过程检查、隐蔽验收、整改复查和资料归档闭环。"
        ),
    )

    report = bidding_technical_quality.validate_technical_bid_draft_for_formal_export(
        project_name="东莞香港中心项目商业街区及6#楼32F办公区装修工程",
        project_context={
            "work_zone_names": ["商业街区", "6#楼32F办公区"],
            "scope": {"work_zones": ["商业街区", "6#楼32F办公区"]},
        },
        project_facts={},
        drafts=[draft],
        components_by_key={
            "technical_composition:7_3_10": {
                "source_item_no": "7.3.10",
                "component_title": "施工组织设计",
            }
        },
        final_content_by_draft_id={},
    )

    blocker_codes = {item["code"] for item in report["blockers"]}
    assert "previous_project_leakage" not in blocker_codes


def test_technical_composition_p3_self_review_records_fact_coverage():
    component = {
        "component_key": "temporary_power_plan",
        "component_title": "施工临时用电施工方案",
        "source_item_no": "7.3.13",
        "classification": "tender_extracted_content",
        "information_needs": [{"source_type": "tender_document", "polished_text": "需覆盖施工临时用电。"}],
    }
    project_context = {
        "affected_zone_phrase": "后厨区、就餐区及周边受影响区域",
        "technical_requirements": {
            "temporary_power": {
                "label": "临时用电",
                "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                "keywords": ["三级配电", "漏电保护", "配电箱巡检"],
            }
        },
    }

    content, _evidence, placeholders, _warnings, decision = bidding_draft_sections._build_technical_composition_draft_content(
        None,
        component,
        [],
        order_index=1,
        project_context=project_context,
    )

    review = decision["composition_quality_review"]
    assert not placeholders
    assert "临时用电应执行三级配电、漏电保护和配电箱巡检" in content
    assert review["status"] == "pass"
    assert review["score"] >= 85
    assert "temporary_power" in review["available_fact_keys"]
    assert "temporary_power" in review["used_fact_keys"]
    assert not review["missing_fact_keys"]
    assert decision["auto_enhancement"]["status"] == "not_needed"
    assert decision["quality_profile"]["composition_quality_score"] == review["score"]


def test_technical_composition_p3_auto_enhances_thin_content_with_project_facts():
    component = {
        "component_key": "temporary_power_plan",
        "component_title": "施工临时用电施工方案",
        "source_item_no": "7.3.13",
    }
    project_context = {
        "work_zone_phrase": "后厨区、就餐区及相关配合区域",
        "technical_requirements": {
            "temporary_power": {
                "label": "临时用电",
                "summary": "临时用电应执行三级配电、漏电保护和配电箱巡检",
                "keywords": ["三级配电", "漏电保护", "配电箱巡检"],
            }
        },
    }
    generation_decision = {
        "quality_profile": {"quality_status": "ready"},
        "acceptance_check": {"status": "pass"},
    }

    content, warnings, decision = bidding_draft_sections._apply_technical_composition_quality_self_review(
        "# 施工临时用电施工方案\n\n本章按现场要求执行。",
        [],
        generation_decision,
        component,
        project_context,
        [],
    )

    assert "## 项目要求落地补充" in content
    assert "临时用电应执行三级配电、漏电保护和配电箱巡检" in content
    assert "## 执行检查与闭环补充" in content
    assert decision["auto_enhancement"]["status"] == "applied"
    assert decision["auto_enhancement"]["before_score"] < decision["auto_enhancement"]["after_score"]
    assert "temporary_power" in decision["auto_enhancement"]["added_fact_keys"]
    assert decision["composition_quality_review"]["status"] == "pass"
    assert {item["code"] for item in warnings} == {"technical_composition_auto_enhanced"}


def test_technical_composition_validates_source_item_count_for_73_section(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        page_9 = (
            "7.3 对技术标部分的要求：\n"
            "投标人须编制相应本工程需要的施工组织设计，技术标书主要包括下列内容 :\n"
            "7.3.1 投标人营业执照及资质证明(复印加盖公章)；\n"
            "7.3.2 法定代表人身份证明书；\n"
            "7.3.3 投标文件签署授权委托书，委托书要求总公司授权；\n"
            "7.3.4 投标人拟派出的项目经理的《中华人民共和国一级建造师注册证书》复印件加盖投标人公章；\n"
            "7.3.5 招标文件要求投标人提交的其它投标资料：\n"
            "­ 安全生产许可证\n"
            "7.3.6 投标人对本工程的工程质量和工期(请注明天数)的承诺及保证措施（此项作为重点技术评审项）；\n"
            "7.3.7 投标人近三年已完成的类似工程经验；\n"
            "7.3.8 投标人拟派驻本项目的项目经理、技术负责人、安全负责人以及其它主要管理人员和技术人员的简历和资格证书；\n"
            "7.3.9 施工总进度计划(包括总工期、主要材料与详细设备进场时间等)；\n"
            "7.3.10 针对本工程的施工组织设计，主要包括组织架构、临时设施安排、安全措施、文明施工措施、对周围环境保护、成品保护等；"
        )
        page_10 = (
            "7.3.11 办公室、工具间、材料间的管理方案；\n"
            "7.3.12 垃圾的清理、堆置、运输、垃圾堆场管理方案；\n"
            "7.3.13 施工临时用电的施工方案；\n"
            "7.3.14 主要材料的采购计划；\n"
            "7.3.15 提供详细的安全生产、文明施工、防火施工方案和保证措施；\n"
            "7.3.16 重要的施工质量保障措施；\n"
            "7.3.17 投标单位按需于回标前提供主要材料样板，规格尺寸按发包人要求；\n"
            "7.3.18 投标单位拟采用的材料品牌表；\n"
            "7.3.19 项目重难点分析；\n"
            "7.3.20 投标单位认为能提升投标竞争力的内容。\n"
            "8. 投标文件电子文档的提交及要求"
        )
        project, run = _create_project_run_and_file(
            user,
            extracted_text=f"{page_9}\n{page_10}",
            segments=[
                {"source_file": "招标文件.pdf", "source_location": "第9页", "text": page_9},
            ],
        )

        async def fake_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "business_license",
                        "component_title": "投标人营业执照及资质证明",
                        "source_item_no": "7.3.1",
                        "classification": "fixed_enterprise_material",
                        "information_needs": [
                            {
                                "need_key": "business_license",
                                "need_title": "投标人营业执照及资质证明",
                                "source_type": "enterprise_profile",
                                "profile_category": "certificate",
                                "query": "营业执照 资质证明",
                            }
                        ],
                    },
                    {
                        "component_key": "legal_rep",
                        "component_title": "法定代表人身份证明书",
                        "source_item_no": "7.3.2",
                        "classification": "fixed_enterprise_material",
                        "information_needs": [
                            {
                                "need_key": "legal_rep",
                                "need_title": "法定代表人身份证明书",
                                "source_type": "enterprise_profile",
                                "profile_category": "basic_info",
                                "query": "法定代表人身份证明书",
                            }
                        ],
                    },
                    {
                        "component_key": "quality_duration_commitment",
                        "component_title": "工程质量和工期承诺及保证措施",
                        "source_item_no": "7.3.6",
                        "classification": "tender_extracted_content",
                        "information_needs": [
                            {
                                "need_key": "quality_duration",
                                "need_title": "质量和工期要求",
                                "source_type": "tender_document",
                                "query": "质量 工期 承诺 保证措施",
                                "polished_text": "我方将结合招标文件要求，对工程质量和工期作出明确承诺并制定保证措施。",
                            }
                        ],
                    },
                ],
                "coverage_check": {
                    "source_item_count": 20,
                    "component_count": 3,
                    "excluded_count": 0,
                    "missing_source_item_nos": [],
                },
            }

        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_llm)
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["summary"]["source_item_count"] == 20
        assert data["summary"]["llm_component_count"] == 3
        assert data["summary"]["component_count"] == 20
        assert data["summary"]["repaired_missing_count"] == 17
        assert data["coverage_check"]["coverage_status"] == "complete"
        assert "7.3.20" in {item["source_item_no"] for item in data["components"]}
        assert any(warning["code"] == "source_item_repair" for warning in data["warnings"])
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_retries_source_item_classification_when_primary_returns_no_components(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        license_title = f"Business License retry {uuid.uuid4().hex[:8]}"
        license_profile = _create_active_profile(
            user,
            category="certificate",
            title=license_title,
            summary=f"Stamped business license and qualification certificate for {license_title}.",
        )
        project, run = _create_project_run_and_file(user)
        context = {
            "files": [],
            "segments": [{"segment_id": "S1", "source_file": "tender.pdf", "source_location": "7.3", "text": "technical bid"}],
            "composition_sections": [
                {
                    "section_no": "7.3",
                    "section_title": "Technical bid requirements",
                    "source_file": "tender.pdf",
                    "source_location": "7.3",
                    "raw_item_count": 2,
                    "raw_items": [
                        {
                            "item_no": "7.3.1",
                            "order_index": 1,
                            "item_title": "Business license and qualification certificate",
                            "original_text": "7.3.1 Business license and qualification certificate.",
                        },
                        {
                            "item_no": "7.3.10",
                            "order_index": 10,
                            "item_title": "Construction organization design",
                            "original_text": "7.3.10 Construction organization design including safety and schedule.",
                        },
                    ],
                }
            ],
            "composition_source_item_count": 2,
            "source_segment_count": 1,
        }

        async def fake_primary_llm(*_args, **_kwargs):
            return {"components": [], "warnings": [{"code": "empty", "message": "empty primary result"}]}

        retry_calls: list[str] = []

        async def fake_retry_llm(*_args, **_kwargs):
            retry_calls.append("called")
            return {
                "components": [
                    {
                        "component_key": "business_license",
                        "component_title": "Business license and qualification certificate",
                        "source_item_no": "7.3.1",
                        "classification": "fixed_enterprise_material",
                        "classification_reason": "Fixed enterprise profile material.",
                        "information_needs": [
                                {
                                    "need_key": "business_license",
                                    "need_title": license_title,
                                    "source_type": "enterprise_profile",
                                    "profile_category": "certificate",
                                    "query": license_title,
                                    "reason": "Use enterprise profile material.",
                                }
                        ],
                    },
                    {
                        "component_key": "construction_organization",
                        "component_title": "Construction organization design",
                        "source_item_no": "7.3.10",
                        "classification": "tender_extracted_content",
                        "classification_reason": "Project-specific technical chapter.",
                        "information_needs": [
                            {
                                "need_key": "construction_design",
                                "need_title": "Construction organization design requirements",
                                "source_type": "tender_document",
                                "query": "safety schedule construction organization",
                                "polished_text": "Prepare construction organization design based on tender requirements.",
                                "reason": "Use tender-specific content.",
                            }
                        ],
                    },
                ],
                "coverage_check": {"source_item_count": 2, "component_count": 2, "excluded_count": 0, "missing_source_item_nos": []},
            }

        monkeypatch.setattr(bidding_technical_composition, "_build_document_context", lambda _files: context)
        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_primary_llm)
        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_source_item_llm", fake_retry_llm)

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert retry_calls == ["called"]
        assert data["summary"]["source_item_count"] == 2
        assert data["summary"]["llm_component_count"] == 2
        assert data["summary"]["component_count"] == 2
        assert data["summary"]["repaired_missing_count"] == 0
        assert data["summary"]["enterprise_profile_need_count"] == 1
        assert data["summary"]["auto_matched_profile_count"] == 1
        assert any(warning["code"] == "source_item_llm_retry" for warning in data["warnings"])
        assert not any(warning["code"] == "source_item_repair" for warning in data["warnings"])

        db = SessionLocal()
        try:
            rows = (
                db.query(BidMaterialRequirement)
                .filter(BidMaterialRequirement.parse_run_id == run.id, BidMaterialRequirement.package_key == "technical")
                .all()
            )
            assert len(rows) == 1
            assert rows[0].section_key == "technical_composition:7_3_1"
            assert rows[0].submitted_profile_item_uuid == license_profile.item_uuid
        finally:
            db.close()
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_technical_composition_retries_source_item_classification_when_primary_call_fails(client, monkeypatch):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        context = {
            "files": [],
            "segments": [{"segment_id": "S1", "source_file": "tender.pdf", "source_location": "7.3", "text": "technical bid"}],
            "composition_sections": [
                {
                    "section_no": "7.3",
                    "section_title": "Technical bid requirements",
                    "source_file": "tender.pdf",
                    "source_location": "7.3",
                    "raw_item_count": 1,
                    "raw_items": [
                        {
                            "item_no": "7.3.10",
                            "order_index": 10,
                            "item_title": "Construction organization design",
                            "original_text": "7.3.10 Construction organization design including safety and schedule.",
                        }
                    ],
                }
            ],
            "composition_source_item_count": 1,
            "source_segment_count": 1,
        }

        async def fake_primary_llm(*_args, **_kwargs):
            raise RuntimeError("ssl transient failure")

        async def fake_retry_llm(*_args, **_kwargs):
            return {
                "components": [
                    {
                        "component_key": "construction_organization",
                        "component_title": "Construction organization design",
                        "source_item_no": "7.3.10",
                        "classification": "tender_extracted_content",
                        "information_needs": [
                            {
                                "need_key": "construction_design",
                                "need_title": "Construction organization design requirements",
                                "source_type": "tender_document",
                                "query": "construction organization",
                                "polished_text": "Prepare construction organization design based on tender requirements.",
                            }
                        ],
                    }
                ],
                "coverage_check": {"source_item_count": 1, "component_count": 1, "excluded_count": 0, "missing_source_item_nos": []},
            }

        monkeypatch.setattr(bidding_technical_composition, "_build_document_context", lambda _files: context)
        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_llm", fake_primary_llm)
        monkeypatch.setattr(bidding_technical_composition, "_call_technical_composition_source_item_llm", fake_retry_llm)

        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-composition/generate",
            json={"run_uuid": run.run_uuid},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["summary"]["llm_component_count"] == 1
        assert data["summary"]["component_count"] == 1
        assert data["components"][0]["source_item_no"] == "7.3.10"
        assert any(warning["code"] == "source_item_llm_retry" for warning in data["warnings"])
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)
        _set_flag("feature_enterprise_profile", old_profile)


def test_pending_confirmation_replaces_schedule_and_quality_facts():
    schedule_content = (
        "# 7.3.9 施工总进度计划\n"
        "总工期【待确认：总工期为XX日历天，计划开工日期为XXXX年XX月XX日，计划竣工日期为XXXX年XX月XX日】。\n"
        "施工准备阶段计划用时【待确认：X日历天】。"
    )
    quality_content = (
        "# 7.3.16 重要的施工质量保障措施\n"
        "确保工程质量达到【待确认：合同约定的质量标准，如“合格”或“优良”】，争创【待确认：具体奖项名称】。"
    )
    facts = {
        "schedule": {
            "commercial_start": "2026年4月15日",
            "office_start": "2026年6月1日",
            "commercial_days": 45,
            "office_days": 60,
            "zones": [
                {"name": "商业街区", "start_date": "2026年4月15日", "duration_days": 45},
                {"name": "6#楼32F办公区", "start_date": "2026年6月1日", "duration_days": 60},
            ],
            "sentence": "商业街区开工日期暂定 2026年4月15日，合同工期 45 天；6#楼32层办公区开工日期暂定 2026年6月1日，合同工期 60 天",
            "evidence": {"source_file": "tender.pdf", "source_location": "第20页 第四条 合同工期"},
        },
        "quality": {
            "goal": "广东省优质工程奖项目",
            "evidence": {"source_file": "tender.pdf", "source_location": "第20页 第五条 工程质量"},
        },
    }

    schedule_updated, schedule_evidence, schedule_count = bidding_draft_sections._replace_pending_confirmations_with_tender_facts(
        schedule_content,
        section_no="7.3.9",
        facts=facts,
    )
    quality_updated, quality_evidence, quality_count = bidding_draft_sections._replace_pending_confirmations_with_tender_facts(
        quality_content,
        section_no="7.3.16",
        facts=facts,
    )

    assert "待确认" not in schedule_updated
    assert "商业街区合同工期45天" in schedule_updated
    assert "6#楼32F办公区合同工期60天" in schedule_updated
    assert "计划用时按总控计划前置完成" in schedule_updated
    assert schedule_count >= 2
    assert schedule_evidence
    assert "待确认" not in quality_updated
    assert "合同图纸及技术规范要求" in quality_updated
    assert "广东省优质工程奖项目" in quality_updated
    assert quality_count >= 2
    assert quality_evidence


def test_technical_project_facts_extract_generic_scope_schedule_and_quality(client):
    user = _create_user("admin")
    _login(client, user)
    extracted_text = (
        "工程概况：本工程为员工餐厅装修改造工程。\n"
        "招标范围：后厨区、就餐区及备餐区室内装饰装修、给排水和电气末端配合。\n"
        "计划开工日期为2026年8月1日，总工期90日历天。\n"
        "质量标准：达到国家现行验收规范合格标准。"
    )
    project, run = _create_project_run_and_file(user, extracted_text=extracted_text)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).one()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).one()

        facts = bidding_draft_sections._technical_project_facts(db, project_db, run_db)
        context = bidding_draft_sections._technical_project_context(db, project_db, run_db)
    finally:
        db.close()

    assert facts["scope"]["work_zones"] == ["后厨区", "就餐区", "备餐区"]
    assert facts["schedule"]["total_days"] == 90
    assert facts["schedule"]["zones"][0]["name"] == "本项目"
    assert facts["schedule"]["zones"][0]["duration_days"] == 90
    assert facts["schedule"]["zones"][0]["start_date"] == "2026年8月1日"
    assert facts["quality"]["goal"] == "达到国家现行验收规范合格标准"
    assert context["work_zone_names"] == ["后厨区", "就餐区", "备餐区"]
    assert context["work_zone_phrase"] == "后厨区、就餐区、备餐区及相关配合区域"


def test_technical_project_facts_keep_hk_center_office_zone_complete(client):
    user = _create_user("admin")
    _login(client, user)
    extracted_text = (
        "招标范围：商业街区及6#楼32F办公区装修。\n"
        "合同工期：商业街区工期45天，6#楼32层办公区工期60天。\n"
        "质量目标：广东省优质工程奖项目。"
    )
    project, run = _create_project_run_and_file(user, extracted_text=extracted_text)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).one()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).one()

        facts = bidding_draft_sections._technical_project_facts(db, project_db, run_db)
        context = bidding_draft_sections._technical_project_context(db, project_db, run_db)
    finally:
        db.close()

    zone_names = [item["name"] for item in facts["schedule"]["zones"]]
    assert zone_names == ["商业街区", "6#楼32F办公区"]
    assert "商业街区，合同工期45天；6#楼32F办公区，合同工期60天" in facts["schedule"]["sentence"]
    assert context["work_zone_names"] == ["商业街区", "6#楼32F办公区"]
    assert context["work_zone_phrase"] == "商业街区、6#楼32F办公区及相关配合区域"
    assert "层办公" not in context["work_zone_phrase"]


def test_unlabeled_personnel_pending_marker_becomes_other_staff_requirement():
    markers = bidding_draft_sections._pending_confirmation_markers("| 【待确认】 | 其他需补充的关键岗位 |")
    specs = bidding_draft_sections._personnel_pending_requirement_specs(markers)

    assert markers[0]["label"] == "其他主要管理人员补充"
    assert specs[0]["key"] == "other_management_staff_roster_certificates"
    assert specs[0]["title"] == "其他主要管理人员名单、岗位及证书"


def test_scheme_pending_markers_are_formalized_without_new_material_gap():
    content = (
        "# 7.3.13 施工临时用电施工方案\n"
        "一级配电接入点以【待确认：甲方提供的一级配电箱位置及变压器容量，需进场后复核】为准。\n"
        "临电平面布置【待确认：需根据现场条件出图】。"
    )

    updated, count = bidding_draft_sections._formalize_scheme_pending_markers(content, section_no="7.3.13")

    assert count == 2
    assert "待确认" not in updated
    assert "以甲方或总包确认的临时用电接入点及现场复核容量为准" in updated
    assert "结合现场条件深化完善" in updated


def test_material_brand_table_uses_enterprise_profile_material_text():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        profile = EnterpriseProfileItem(
            item_uuid=str(uuid.uuid4()),
            category="other",
            subcategory="material_brand_library",
            profile_key="brand_table_test",
            title="企业常用材料品牌库",
            summary="材料品牌表",
            content_text="投标单位拟采用的材料品牌表\n材料名称 招标品牌 投标单位选定品牌\n水泥 金羊 华润",
            structured_json=dumps_json({"raw_table_text": "投标单位拟采用的材料品牌表\n材料名称 招标品牌 投标单位选定品牌\n水泥 金羊 华润"}),
            tags_json=dumps_json(["材料品牌"]),
            source="manual",
            confidentiality="internal",
            status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
            valid_until=date.today() + timedelta(days=365),
            created_by=user.id,
            updated_by=user.id,
            approved_by=user.id,
            approved_at=datetime(2026, 7, 6, 10, 1),
        )
        db.add(profile)
        db.flush()
        requirement = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project_db.id,
            parse_run_id=run_db.id,
            format_item_key="material_brand_table",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:7_3_18",
            item_title="7.3.18 拟采用的材料品牌表",
            requirement_type="profile",
            profile_category="other",
            material_key="technical_composition:7_3_18:brand_table",
            title="企业常用材料品牌库",
            fulfillment_mode="enterprise_profile",
            status="submitted",
            submitted_profile_item_uuid=profile.item_uuid,
            priority="normal",
            created_by=user.id,
        )
        db.add(requirement)
        db.flush()

        content, evidence, replacement_count = bidding_draft_sections._technical_brand_table_content_from_materials(
            db,
            {
                "source_item_no": "7.3.18",
                "component_title": "拟采用的材料品牌表",
                "component_key": "material_brand_table",
            },
            [requirement],
        )

        assert replacement_count == 1
        assert "投标单位拟采用的材料品牌表" in content
        assert "水泥 金羊 华润" in content
        assert "待确认" not in content
        assert evidence[0]["source_kind"] == "enterprise_profile_material"
    finally:
        db.close()


def test_technical_bid_word_export_uses_current_composition_sections_only():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        plan = {
            "status": "generated",
            "components": [
                {
                    "source_item_no": "7.3.1",
                    "component_key": "legacy_generated_key_should_not_win",
                    "component_title": "法定代表人资格证明书",
                    "classification": "fixed_enterprise_material",
                },
                {
                    "source_item_no": "7.3.18",
                    "component_key": "material_brand_table",
                    "component_title": "拟采用的材料品牌表",
                    "classification": "fixed_enterprise_material",
                },
            ],
        }
        run_db.summary_json = dumps_json({"technical_composition_plan": plan})
        db.add_all(
            [
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_1",
                    section_title="7.3.1 法定代表人资格证明书",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.1 法定代表人资格证明书\n## 编制依据\n本章节引用企业资料库中的法定代表人证明资料。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="draft",
                    created_by=user.id,
                ),
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_18",
                    section_title="7.3.18 拟采用的材料品牌表",
                    section_type="technical",
                    draft_mode="placeholder",
                    draft_status="needs_input",
                    content_markdown=(
                        "# 7.3.18 拟采用的材料品牌表\n"
                        "| 材料类别 | 拟采用品牌 |\n"
                        "| --- | --- |\n"
                        "| 瓷砖 | 马可波罗、东鹏 |\n"
                        "正文仍有一处待确认。"
                    ),
                    placeholders_json=dumps_json([{"label": "材料品牌库确认", "detail": "请确认品牌表可用于本项目。"}]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="draft",
                    created_by=user.id,
                ),
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:old_llm_component_key",
                    section_title="旧 key 生成的重复章节",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 旧 key 生成的重复章节\n不应进入导出文件。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="draft",
                    created_by=user.id,
                ),
            ]
        )
        db.flush()
        db.add(
            BidMaterialRequirement(
                requirement_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                format_item_key="technical_composition:7_3_18",
                package_key="technical",
                package_title="技术标",
                section_key="technical_composition:7_3_18",
                item_title="7.3.18 拟采用的材料品牌表",
                requirement_type="profile",
                profile_category="other",
                material_key="technical_composition:7_3_18:brand_table",
                title="企业常用材料品牌库",
                fulfillment_mode="enterprise_profile",
                status="submitted",
                priority="normal",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_draft_export_document(db, project_db, run_db))

        assert "技术标投标文件草稿" in document_xml
        assert "法定代表人资格证明书" in document_xml
        assert "拟采用的材料品牌表" in document_xml
        assert "马可波罗、东鹏" in document_xml
        assert "导出前待处理事项" in document_xml
        assert "企业常用材料品牌库" in document_xml
        assert "旧 key 生成的重复章节" not in document_xml
        assert "old_llm_component_key" not in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_blocks_unresolved_internal_draft_items():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.18",
                            "component_key": "material_brand_table",
                            "component_title": "拟采用的材料品牌表",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_18",
                section_title="7.3.18 拟采用的材料品牌表",
                section_type="technical",
                draft_mode="formal",
                draft_status="needs_input",
                content_markdown="# 7.3.18 拟采用的材料品牌表\n品牌表仍待确认。",
                placeholders_json=dumps_json([{"label": "材料品牌库确认", "detail": "请确认品牌表可用于本项目。"}]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="draft",
                created_by=user.id,
            )
        )
        db.commit()

        try:
            build_technical_bid_final_export_document(db, project_db, run_db)
            assert False, "正式导出应拦截未处理的待确认内容"
        except BidTechnicalWordExportError as exc:
            assert exc.code == "BID_TECHNICAL_FINAL_EXPORT_BLOCKED"
            issues = exc.details["issues"]
            issue_text = "\n".join(item["issue"] for item in issues)
            assert "待确认" in issue_text
            assert "正文仍包含" in issue_text
    finally:
        db.close()


def test_technical_bid_final_word_export_blocks_requirement_list_references():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.8",
                            "component_key": "personnel_resume",
                            "component_title": "拟派驻主要管理人员简历和资格证书",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_8",
                section_title="7.3.8 拟派驻主要管理人员简历和资格证书",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.8 拟派驻主要管理人员简历和资格证书\n"
                    "项目经理：黄入亮。\n"
                    "技术负责人：见资料需求清单《技术负责人完整简历及资格证书》。"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        try:
            build_technical_bid_final_export_document(db, project_db, run_db)
            assert False, "正式导出应拦截资料需求清单占位引用"
        except BidTechnicalWordExportError as exc:
            assert exc.code == "BID_TECHNICAL_FINAL_EXPORT_BLOCKED"
            issue_text = "\n".join(item["issue"] for item in exc.details["issues"])
            assert "资料需求清单" in issue_text
    finally:
        db.close()


def test_technical_bid_final_word_export_quality_gate_blocks_previous_project_leakage(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        project_db.project_name = "信达资产职工餐厅装饰装修工程"
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.10",
                            "component_key": "construction_organization",
                            "component_title": "施工组织设计",
                            "classification": "tender_extracted_content",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_10",
                section_title="7.3.10 施工组织设计",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.10 施工组织设计\n"
                    "本施工组织设计针对东莞香港中心项目商业街区及6#楼32F办公区装修工程编制，"
                    "现场组织、材料运输、成品保护和交叉作业均按该项目界面执行。"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        try:
            build_technical_bid_final_export_document(db, project_db, run_db)
            assert False, "正式导出应阻断非当前项目内容泄漏"
        except BidTechnicalWordExportError as exc:
            assert exc.code == "BID_TECHNICAL_FINAL_EXPORT_BLOCKED"
            report = exc.details["quality_report"]
            blocker_codes = {item["code"] for item in report["blockers"]}
            assert "previous_project_leakage" in blocker_codes
            issue_text = "\n".join(item["issue"] for item in exc.details["issues"])
            assert "非当前项目内容" in issue_text
    finally:
        db.close()


def test_technical_final_export_dedupes_tracked_requirement_reference_issue(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        material = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            format_item_key="generic_pending_1_1_1",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:1_1_1",
            item_title="地面铺石材工程",
            requirement_type="manual_input",
            profile_category="technical_solution",
            material_key=f"technical_composition:generic_pending:{uuid.uuid4().hex}",
            title="1.1.1 资料补充",
            description="请补充具体人员数量及设备清单。",
            fulfillment_mode="manual_upload",
            status="missing",
            priority="high",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(material)
        db.flush()
        draft = BidDraftSection(
            draft_uuid=str(uuid.uuid4()),
            project_id=project.id,
            parse_run_id=run.id,
            section_key="technical_composition:1_1_1",
            section_title="1.1.1 地面铺石材工程",
            section_type="technical",
            draft_mode="placeholder",
            draft_status="needs_input",
            content_markdown=(
                "# 1.1.1 地面铺石材工程\n"
                "## 待人工完善\n"
                "项目资源配置见资料需求清单《1.1.1 资料补充》。"
            ),
            placeholders_json=dumps_json([{"label": "具体人员数量及设备清单"}]),
            source_requirement_ids_json=dumps_json([material.id]),
            generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
            generator_type="rule",
            generator_model="test",
            review_status="draft",
            created_by=user.id,
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).one()

        issues = bidding_technical_word_export._technical_final_export_issue_rows(
            db,
            run_db,
            drafts=[draft],
            missing_draft_sections=[],
            attachments_by_draft={},
            final_content_by_draft_id={draft.id: draft.content_markdown},
        )

        assert len(issues) == 1
        assert issues[0]["code"] == "material_requirement_missing"
        assert issues[0]["required_information"] == "请补充具体人员数量及设备清单"
        assert "需补充：请补充具体人员数量及设备清单" in issues[0]["issue"]
        assert "技术标资料需求与补齐清单" in issues[0]["suggestion"]
        assert issues[0]["action"] == "fill_confirm_and_regenerate"
    finally:
        db.close()


def test_technical_bid_final_word_export_quality_gate_blocks_schedule_duration_conflict(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(
        user,
        extracted_text="招标范围：信达资产职工餐厅室内装饰装修。总工期60天，质量目标为合格。",
        segments=[
            {
                "source_file": "招标文件.docx",
                "source_location": "工期要求",
                "text": "招标范围：信达资产职工餐厅室内装饰装修。总工期60天，质量目标为合格。",
            }
        ],
    )
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        project_db.project_name = "信达资产职工餐厅装饰装修工程"
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.9",
                            "component_key": "schedule_plan",
                            "component_title": "施工总进度计划",
                            "classification": "tender_extracted_content",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_9",
                section_title="7.3.9 施工总进度计划",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.9 施工总进度计划\n"
                    "本工程计划工期90天，进场后依次完成施工准备、基层处理、面层安装、调试收口和竣工移交。"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        try:
            build_technical_bid_final_export_document(db, project_db, run_db)
            assert False, "正式导出应阻断工期事实冲突"
        except BidTechnicalWordExportError as exc:
            assert exc.code == "BID_TECHNICAL_FINAL_EXPORT_BLOCKED"
            report = exc.details["quality_report"]
            blocker_codes = {item["code"] for item in report["blockers"]}
            assert "schedule_duration_conflict" in blocker_codes
            issue_text = "\n".join(item["issue"] for item in exc.details["issues"])
            assert "60天" in issue_text
            assert "90天" in issue_text
    finally:
        db.close()


def test_technical_bid_final_word_export_formalizes_personnel_requirement_refs_when_material_ready():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.8",
                            "component_key": "personnel_resume",
                            "component_title": "拟派驻主要管理人员简历和资格证书",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        requirement = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project_db.id,
            parse_run_id=run_db.id,
            format_item_key="technical_composition:7_3_8",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:7_3_8",
            item_title="7.3.8 拟派驻主要管理人员简历和资格证书",
            requirement_type="manual",
            profile_category="personnel",
            material_key="technical_composition:7_3_8:technical_lead",
            title="技术负责人完整简历及资格证书",
            fulfillment_mode="manual_fill",
            status="approved",
            submitted_value="技术负责人：张三，职称：工程师，负责施工技术管理。",
            priority="high",
            created_by=user.id,
        )
        db.add(requirement)
        db.flush()
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_8",
                section_title="7.3.8 拟派驻主要管理人员简历和资格证书",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.8 拟派驻主要管理人员简历和资格证书\n"
                    "项目经理：黄入亮。\n"
                    "技术负责人：见资料需求清单《技术负责人完整简历及资格证书》。"
                ),
                placeholders_json=dumps_json([]),
                source_requirement_ids_json=dumps_json([requirement.id]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "资料需求清单" not in document_xml
        assert "拟派驻主要管理人员表" in document_xml
        assert "技术负责人：张三，职称：工程师，负责施工技术管理。" in document_xml
        assert "详见本章后附人员简历及资格证书资料" not in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_formalizes_weak_commitment_wording():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.14",
                            "component_key": "material_purchase_plan",
                            "component_title": "主要材料采购计划",
                            "classification": "tender_extracted_content",
                        },
                        {
                            "source_item_no": "7.3.19",
                            "component_key": "key_difficulties",
                            "component_title": "项目重难点分析",
                            "classification": "tender_extracted_content",
                        },
                    ],
                }
            }
        )
        db.add_all(
            [
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_14",
                    section_title="7.3.14 主要材料采购计划",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.14 主要材料采购计划\n材料品牌、规格、数量待图纸、协议书及发包人审批进一步补充完善并细化。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="accepted",
                    created_by=user.id,
                ),
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_19",
                    section_title="7.3.19 项目重难点分析",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown=(
                        "# 7.3.19 项目重难点分析\n"
                        "（注：以上重难点分析基于招标文件已知信息及类似工程经验编制，具体工期、特殊技术要求等细节待图纸及协议书进一步明确后细化。）"
                    ),
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="accepted",
                    created_by=user.id,
                ),
            ]
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "待图纸" not in document_xml
        assert "进一步明确后细化" not in document_xml
        assert "进场后我方将结合施工图、深化设计、合同协议书、现场复核情况及发包人审批意见完成细化报审" in document_xml
        assert "本章内容将作为投标阶段施工策划和实施承诺" in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_cleans_source_markers_self_refs_and_residual_weak_words():
    content = (
        "营业执照（香港中心技术标提取）\n"
        "技术负责人：详见本技术标“拟派驻主要管理人员简历和资格证书”章节\n"
        "涉及外立面施工若使用吊篮，吊篮方案须经专家论证（如需）。\n"
        "所有材料设备进场时间可根据现场实际进度动态调整。\n"
        "总视在功率需结合现场实际同时系数计算。"
    )

    updated = bidding_technical_word_export._formalize_final_export_text(content)

    assert "技术标提取" not in updated
    assert "香港中心技术标提取" not in updated
    assert "详见本技术标" not in updated
    assert "如需" not in updated
    assert "可根据" not in updated
    assert "需结合" not in updated
    assert "随本章后附人员简历及资格证书资料一并提交" not in updated
    assert "见本章人员简历及资格证书附件资料" in updated
    assert "根据现场实际进度动态优化调整" in updated
    assert "按现场实际同时系数复核计算" in updated


def test_technical_bid_final_word_export_formalizes_bid_roles_placeholders_and_canonical_titles():
    content = (
        "临时用电按合同约定配备，涉及外立面施工如需使用吊篮，吊篮方案须经甲方及总包审批。\n"
        "材料品牌按合同，数量按清单，甲方及总包确认后实施。相关复印件、证照或证明文件随本章后附。"
    )

    updated = bidding_technical_word_export._formalize_final_export_text(content)
    title_10 = bidding_technical_word_export._formal_chapter_title(
        BidDraftSection(section_key="technical_composition:7_3_10", section_title="7.3.10 施工组织设计"),
        {"source_item_no": "7.3.10", "component_title": "施工组织设计"},
        1,
    )
    title_13 = bidding_technical_word_export._formal_chapter_title(
        BidDraftSection(section_key="technical_composition:7_3_13", section_title="7.3.13 临时用电方案"),
        {"source_item_no": "7.3.13", "component_title": "临时用电方案"},
        2,
    )
    title_14 = bidding_technical_word_export._formal_chapter_title(
        BidDraftSection(section_key="technical_composition:7_3_14", section_title="7.3.14 主要材料采购计划"),
        {"source_item_no": "7.3.14", "component_title": "主要材料采购计划"},
        3,
    )

    assert title_10 == "第一章、 针对本工程的施工组织设计"
    assert title_13 == "第二章、 施工临时用电的施工方案"
    assert title_14 == "第三章、 主要材料的采购计划（含甲指乙供材料）"
    assert "甲方" not in updated
    assert "总包" not in updated
    assert "按合同" not in updated
    assert "按清单" not in updated
    assert "随本章后附" not in updated
    assert "材料品牌按招标文件品牌要求及发包人确认结果执行" in updated
    assert "按招标工程量清单及经确认的设计图纸执行" in updated
    assert "发包人及总承包单位确认后实施" in updated
    assert "见本章附件资料" in updated


def test_technical_bid_final_word_export_p0_cleans_hard_finalization_defects():
    content = (
        "相关内容结合商业街、层办公、分段不连续开工组织实施。\n"
        "工期组织方面，商业街，合同工期 45天；层办公，合同工期 60天，实际开工时间以发包人发出的开工令为准。\n"
        "- 劳动力：高峰期计划投入装饰技工约XX人，根据进度动态增补，确保作业面充足时能够突击抢工。\n"
        "- 劳动力补充：高峰期配铺贴工×人、辅助工×人，按工作面组织流水。\n"
        "项目组织架构图具体见附图（略，实际标书中将附清晰的架构框图）。\n"
        "如因我司施工原因未达到上述质量目标，愿按照招标文件及合同约定约定承担相应责任。\n"
        "- 招标文件招标文件技术标要求中明确施工组织设计大纲。"
    )

    updated = bidding_technical_word_export._formalize_final_export_text(
        content,
        section_no="7.3.10",
        project_context={"work_zone_names": ["商业街区", "6#楼32F办公区"]},
    )
    findings = bidding_technical_word_export._formal_export_blocking_findings(updated)

    assert "商业街、层办公" not in updated
    assert "层办公" not in updated
    assert "商业街区、6#楼32F办公区" in updated
    assert "约XX人" not in updated
    assert "XX" not in updated
    assert "高峰期按施工段及作业面配置充足装饰技工" in updated
    assert "×人" not in updated
    assert "高峰期根据施工段、作业面和进度计划动态配置专业工种及辅助人员" in updated
    assert "附图（略" not in updated
    assert "实际标书" not in updated
    assert "项目组织架构详见本章项目管理组织机构及岗位职责表" in updated
    assert "合同约定约定" not in updated
    assert "招标文件招标文件" not in updated
    assert not findings


def test_technical_bid_final_word_export_p1_formalizes_internal_source_trace_wording():
    content = (
        "项目管理与业绩表格截图素材包（源序：A-01，第2页）。\n"
        "复核资料包括：样板确认记录、隐蔽验收记录。关键词响应：样板引路、隐蔽验收。\n"
        "施工组织措施源序：3，按招标文件、施工图纸和现场移交条件组织实施。\n"
        "- 招标文件技术要求（DOCX第245-278段）\n"
        "- 招标文件木饰面专项技术要求（技术要求.docx / DOCX第471段）"
    )

    updated = bidding_technical_word_export._formalize_final_export_text(content, section_no="7.3.10")
    findings = bidding_technical_word_export._formal_export_blocking_findings(updated)

    assert "素材包" not in updated
    assert "截图素材包" not in updated
    assert "源序" not in updated
    assert "复核资料包括" not in updated
    assert "关键词响应" not in updated
    assert "DOCX第" not in updated
    assert "技术要求.docx" not in updated
    assert "项目管理与业绩表格附件资料" in updated
    assert "过程复核资料以样板确认记录、隐蔽验收记录为主" in updated
    assert "围绕样板引路、隐蔽验收等评审要点形成检查、验收和整改闭环" in updated
    assert not findings


def test_technical_bid_final_word_export_blocks_p1_residual_internal_source_traces():
    findings = bidding_technical_word_export._formal_export_blocking_findings(
        "项目管理素材包。源序：A-01。复核资料包括：检查记录。关键词响应：文明施工。技术要求.docx / DOCX第471段。"
    )
    issue_text = "\n".join(item["issue"] for item in findings)

    assert "系统工作流痕迹" in issue_text
    assert "源文件名或内部段页坐标" in issue_text


def test_technical_bid_final_word_export_blocks_p0_residual_hard_defects():
    findings = bidding_technical_word_export._formal_export_blocking_findings(
        "相关内容结合商业街、层办公组织。高峰期约XX人。项目组织架构图见附图（略，实际标书中将附）。"
    )
    issue_text = "\n".join(item["issue"] for item in findings)

    assert "项目范围截断表达" in issue_text
    assert "显性占位符" in issue_text
    assert "未定稿图示表达" in issue_text
    assert not bidding_technical_word_export._formal_export_blocking_findings("钢板规格为2×3mm，按深化图施工。")


def test_technical_bid_final_word_export_blocks_residual_public_draft_wording():
    findings = bidding_technical_word_export._formal_export_blocking_findings(
        "本章内容由甲方确认，按合同、按清单执行，资料随本章后附。"
    )
    issue_text = "\n".join(item["issue"] for item in findings)

    assert "草稿化或口语化投标表述" in issue_text


def test_technical_bid_final_word_export_strips_scheme_pending_action_sections_only():
    construction_content = (
        "# 7.3.10 施工组织设计\n"
        "## 施工组织安排\n"
        "我方按项目经理负责制组织实施，材料堆场布置待确认后实施。\n"
        "## 待人工完善\n"
        "- 【待补充】施工总平面图：需项目负责人补图。\n"
        "- 【待补充】临时设施安排：需现场确认。\n"
        "## 质量安全管理\n"
        "施工过程中执行日检查、周协调和节点验收机制。"
    )
    power_content = (
        "# 7.3.13 施工临时用电施工方案\n"
        "一级配电接入点待确认，临电平面布置按审批方案实施。\n"
        "## 待补充/待复核\n"
        "- 【待确认：现场一级配电箱位置】\n"
    )

    construction_updated = bidding_technical_word_export._formalize_final_export_text(construction_content, section_no="7.3.10")
    power_updated = bidding_technical_word_export._formalize_final_export_text(power_content, section_no="7.3.13")
    brand_updated = bidding_technical_word_export._formalize_final_export_text("品牌表仍待确认。", section_no="7.3.18")

    assert "待人工完善" not in construction_updated
    assert "待补充" not in construction_updated
    assert "待确认" not in construction_updated
    assert "经深化设计、现场复核及发包人审批后实施" in construction_updated
    assert "质量安全管理" in construction_updated
    assert "待补充" not in power_updated
    assert "待确认" not in power_updated
    assert "经现场复核并报发包人、监理及总承包单位审批后确定" in power_updated
    assert not bidding_technical_word_export._formal_export_blocking_findings(construction_updated)
    assert not bidding_technical_word_export._formal_export_blocking_findings(power_updated)
    assert "待确认" in brand_updated
    assert bidding_technical_word_export._formal_export_blocking_findings(brand_updated)


def test_technical_bid_final_word_export_issue_check_recleans_scheme_content():
    draft = BidDraftSection(
        id=123,
        section_key="technical_composition:7_3_10",
        section_title="7.3.10 施工组织设计",
    )
    content_by_id = {
        123: (
            "# 7.3.10 施工组织设计\n"
            "## 施工组织安排\n"
            "我方按项目经理负责制组织实施。\n"
            "## 待人工完善\n"
            "- 【待补充】施工总平面图：需项目负责人补图。\n"
        )
    }

    updated = bidding_technical_word_export._formal_final_content_for_issue_check(draft, content_by_id[123], content_by_id)

    assert "待人工完善" not in updated
    assert "待补充" not in updated
    assert not bidding_technical_word_export._formal_export_blocking_findings(updated)
    assert content_by_id[123] == updated


def test_technical_bid_final_word_export_builder_writes_header_footer_and_page_fields():
    doc = _TenderAnalysisDocxBuilder()
    doc.set_page_header_footer(
        header_text="东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程",
        footer_text="技术标部分",
    )
    doc.add_paragraph("正式技术标正文。")
    docx_bytes = doc.to_bytes()

    document_xml = _docx_part_xml(docx_bytes, "word/document.xml")
    rels_xml = _docx_part_xml(docx_bytes, "word/_rels/document.xml.rels")
    content_types_xml = _docx_part_xml(docx_bytes, "[Content_Types].xml")
    header_xml = _docx_part_xml(docx_bytes, "word/header1.xml")
    footer_xml = _docx_part_xml(docx_bytes, "word/footer1.xml")

    assert '<w:headerReference w:type="default" r:id="rIdHeader1"/>' in document_xml
    assert '<w:footerReference w:type="default" r:id="rIdFooter1"/>' in document_xml
    assert 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"' in rels_xml
    assert 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"' in rels_xml
    assert 'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"' in content_types_xml
    assert 'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"' in content_types_xml
    assert "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程" in header_xml
    assert "技术标部分" in footer_xml
    assert " PAGE " in footer_xml
    assert " NUMPAGES " in footer_xml


def test_technical_bid_final_word_export_cleans_internal_evidence_and_markdown_fences():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        project_db.project_name = "施工组织设计测试工程"
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.10",
                            "component_key": "construction_organization",
                            "component_title": "施工组织设计",
                            "classification": "tender_extracted_content",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_10",
                section_title="7.3.10 施工组织设计",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.10 施工组织设计\n"
                    "## 编制依据\n"
                    "- 技术标组成识别：需编制施工组织设计。\n"
                    "- 企业资料库：施工组织设计通用素材。\n"
                    "## 项目组织架构\n"
                    "```\n"
                    "项目经理\n"
                    "  技术负责人\n"
                    "```\n"
                    "我方将建立项目经理负责制，统筹施工组织、质量、安全和进度管理。"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "技术标组成识别" not in document_xml
        assert "企业资料库" not in document_xml
        assert "```" not in document_xml
        assert "项目经理" in document_xml
        assert "技术负责人" in document_xml
        assert "项目经理负责制" in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_renders_brand_plain_text_as_word_table():
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.18",
                            "component_key": "material_brand_table",
                            "component_title": "拟采用的材料品牌表",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        profile = EnterpriseProfileItem(
            item_uuid=str(uuid.uuid4()),
            category="other",
            subcategory="material_brand_library",
            profile_key="brand_table_final_export_test",
            title="企业常用材料品牌库",
            summary="材料品牌表",
            content_text="投标单位拟采用的材料品牌表\n材料名称 招标品牌 投标单位选定品牌\n水泥 金羊 南华 海螺",
            structured_json=dumps_json({"raw_table_text": "投标单位拟采用的材料品牌表\n材料名称 招标品牌 投标单位选定品牌\n水泥 金羊 南华 海螺"}),
            tags_json=dumps_json(["材料品牌"]),
            source="manual",
            confidentiality="internal",
            status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
            valid_until=date.today() + timedelta(days=365),
            created_by=user.id,
            updated_by=user.id,
            approved_by=user.id,
            approved_at=datetime(2026, 7, 6, 10, 1),
        )
        db.add(profile)
        db.flush()
        requirement = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project_db.id,
            parse_run_id=run_db.id,
            format_item_key="technical_composition:7_3_18",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:7_3_18",
            item_title="7.3.18 拟采用的材料品牌表",
            requirement_type="profile",
            profile_category="other",
            material_key="technical_composition:7_3_18:brand_table",
            title="企业常用材料品牌库",
            fulfillment_mode="enterprise_profile",
            status="approved",
            submitted_profile_item_uuid=profile.item_uuid,
            priority="normal",
            created_by=user.id,
        )
        db.add(requirement)
        db.flush()
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_18",
                section_title="7.3.18 拟采用的材料品牌表",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown="# 7.3.18 拟采用的材料品牌表\n品牌表按企业常用材料品牌库编制。",
                placeholders_json=dumps_json([]),
                source_requirement_ids_json=dumps_json([requirement.id]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert document_xml.count("<w:tbl>") >= 2
        assert "TOC \\o" in document_xml
        assert "材料名称" in document_xml
        assert "招标品牌" in document_xml
        assert "投标单位选定品牌" in document_xml
        assert "金羊、南华" in document_xml
        assert "海螺" in document_xml
        assert "企业资料库" not in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_renders_personnel_fallback_as_word_table(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.8",
                            "component_key": "personnel_resume",
                            "component_title": "拟派驻主要管理人员简历和资格证书",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_8",
                section_title="7.3.8 拟派驻主要管理人员简历和资格证书",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.8 拟派驻主要管理人员简历和资格证书\n"
                    "项目经理：黄入亮；一级建造师注册证书号：粤1442020202205942；企业资料库匹配后点击“填写”时采用企业资料。\n"
                    "技术负责人：张三，职称：工程师，负责施工技术管理；系统工作流已生成正文，正式投标前应核对。\n"
                    "安全负责人：李四，持安全生产考核合格证书；已绑定资料随本章后附。"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "<w:tbl>" in document_xml
        assert "岗位" in document_xml
        assert "姓名" in document_xml
        assert "项目经理" in document_xml
        assert "黄入亮" in document_xml
        assert "技术负责人" in document_xml
        assert "张三" in document_xml
        assert "安全负责人" in document_xml
        assert "李四" in document_xml
        assert "企业资料库" not in document_xml
        assert "点击" not in document_xml
        assert "采用企业资料" not in document_xml
        assert "系统工作流" not in document_xml
        assert "工作流" not in document_xml
        assert "已生成正文" not in document_xml
        assert "已绑定资料" not in document_xml
        assert "正式投标前应核对" not in document_xml
        assert "随本章后附" not in document_xml
        assert "见本章附件资料" in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_renders_brand_fallback_plain_text_as_word_table(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.18",
                            "component_key": "material_brand_table",
                            "component_title": "拟采用的材料品牌表",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_18",
                section_title="7.3.18 拟采用的材料品牌表",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown=(
                    "# 7.3.18 拟采用的材料品牌表\n"
                    "材料名称 招标品牌 投标单位选定品牌\n"
                    "水泥 金羊 南华 海螺"
                ),
                placeholders_json=dumps_json([]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "<w:tbl>" in document_xml
        assert "材料名称" in document_xml
        assert "招标品牌" in document_xml
        assert "投标单位选定品牌" in document_xml
        assert "水泥" in document_xml
        assert "金羊、南华" in document_xml
        assert "海螺" in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_outputs_formal_cover_and_no_internal_draft_pages(client):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        project_db.project_name = "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程招标文件"
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.1",
                            "component_key": "business_license",
                            "component_title": "投标人营业执照及资质证明(复印加盖公章)",
                            "classification": "fixed_enterprise_material",
                        },
                        {
                            "source_item_no": "7.3.10",
                            "component_key": "construction_organization",
                            "component_title": "施工组织设计",
                            "classification": "tender_extracted_content",
                        },
                    ],
                }
            }
        )
        db.add(
            EnterpriseProfileItem(
                item_uuid=str(uuid.uuid4()),
                category="basic_info",
                title="企业基本信息",
                summary="企业名称：广东旗胜智能装饰有限公司；法定代表人：覃士峰",
                content_text="企业名称：广东旗胜智能装饰有限公司；法定代表人：覃士峰",
                structured_json=dumps_json({"企业名称": "广东旗胜智能装饰有限公司", "法定代表人": "覃士峰"}),
                tags_json=dumps_json(["企业基本信息"]),
                source="manual",
                confidentiality="internal",
                status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
                valid_until=date.today() + timedelta(days=365),
                created_by=user.id,
                updated_by=user.id,
                approved_by=user.id,
                approved_at=datetime(2026, 7, 6, 10, 1),
            )
        )
        db.add_all(
            [
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_1",
                    section_title="7.3.1 投标人营业执照及资质证明(复印加盖公章)",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.1 投标人营业执照及资质证明(复印加盖公章)\n本章节引用企业营业执照及资质证明资料。\n日期：2026 年 04 月 27 日",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="accepted",
                    created_by=user.id,
                ),
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_10",
                    section_title="7.3.10 施工组织设计",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.10 施工组织设计\n项目经理：黄入亮；一级建造师注册证书号：粤1442020202205942。\n本工程施工组织设计正文。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="reviewed",
                    created_by=user.id,
                ),
            ]
        )
        db.commit()

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "建设工程施工招标" in document_xml
        assert "投 标 文 件" in document_xml
        assert "技术标部分" in document_xml
        assert "广东旗胜智能装饰有限公司" in document_xml
        assert "覃士峰" in document_xml
        assert "黄入亮" in document_xml
        assert "粤1442020202205942" in document_xml
        assert "2026 年 04 月 27 日" in document_xml
        assert "项目招标编号" not in document_xml
        assert "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程招标文件" not in document_xml
        assert "东莞香港中心项目商业街区及6#楼32F办公区装修专业分包工程" in document_xml
        assert "目 录" in document_xml
        toc_cache = document_xml.split('<w:fldChar w:fldCharType="separate"/>', 1)[1].split(
            '<w:fldChar w:fldCharType="end"/>',
            1,
        )[0]
        assert "TOC \\o" in document_xml
        assert "第一章、 投标人营业执照及资质证明" in toc_cache
        assert "第二章、 针对本工程的施工组织设计" in toc_cache
        assert "第一章、 投标人营业执照及资质证明" in document_xml
        assert "第二章、 针对本工程的施工组织设计" in document_xml
        assert "草稿状态" not in document_xml
        assert "导出范围" not in document_xml
        assert "导出前待处理事项" not in document_xml
        assert "技术标投标文件草稿" not in document_xml
    finally:
        db.close()


def test_technical_bid_final_word_export_cleans_import_prefix_from_project_name():
    assert bidding_technical_word_export._formal_project_name(
        "Qdxx--深圳龙岗区丰隆深港科技园项目三期品质效果提升工程（一标段）招标文件"
    ) == "深圳龙岗区丰隆深港科技园项目三期品质效果提升工程（一标段）"


def test_technical_bid_final_word_export_cleans_profile_source_markers_from_attachments(monkeypatch):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "招标编号": "DG-HK-2026-001",
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.1",
                            "component_key": "business_license",
                            "component_title": "投标人营业执照及资质证明",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                },
            }
        )
        profile = EnterpriseProfileItem(
            item_uuid=str(uuid.uuid4()),
            category="certificate",
            title="营业执照（香港中心技术标提取）",
            summary="营业执照（香港中心技术标提取）",
            content_text="营业执照（香港中心技术标提取）",
            structured_json=dumps_json({}),
            tags_json=dumps_json(["营业执照"]),
            source="manual",
            confidentiality="internal",
            status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
            valid_until=date.today() + timedelta(days=365),
            created_by=user.id,
            updated_by=user.id,
            approved_by=user.id,
            approved_at=datetime(2026, 7, 6, 10, 1),
        )
        db.add(profile)
        db.flush()
        file_obj = FileObject(
            file_id=str(uuid.uuid4()),
            username=user.username,
            purpose="enterprise_profile",
            bucket="quote-files",
            object_name="enterprise_profile/test/business_license_clean.png",
            original_filename="营业执照（香港中心技术标提取）.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
        )
        db.add(file_obj)
        db.flush()
        db.add(
            EnterpriseProfileFile(
                attachment_uuid=str(uuid.uuid4()),
                item_id=profile.id,
                file_id=file_obj.file_id,
                attachment_type="source",
                original_filename=file_obj.original_filename,
                description="营业执照",
                is_primary=True,
                uploaded_by=user.id,
            )
        )
        requirement = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project_db.id,
            parse_run_id=run_db.id,
            format_item_key="technical_composition:7_3_1",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:7_3_1",
            item_title="7.3.1 投标人营业执照及资质证明",
            requirement_type="profile",
            profile_category="certificate",
            material_key="technical_composition:7_3_1:business_license",
            title="营业执照资料",
            fulfillment_mode="enterprise_profile",
            status="approved",
            priority="high",
            submitted_profile_item_uuid=profile.item_uuid,
            created_by=user.id,
        )
        db.add(requirement)
        db.flush()
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_1",
                section_title="7.3.1 投标人营业执照及资质证明",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown="# 7.3.1 投标人营业执照及资质证明\n本章提交营业执照（香港中心技术标提取）。",
                placeholders_json=dumps_json([]),
                source_requirement_ids_json=dumps_json([requirement.id]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="accepted",
                created_by=user.id,
            )
        )
        db.commit()

        monkeypatch.setattr(bidding_technical_word_export, "get_object_bytes", lambda object_name, bucket=None: PNG_1X1)

        document_xml = _docx_document_xml(build_technical_bid_final_export_document(db, project_db, run_db))

        assert "项目招标编号：DG-HK-2026-001" in document_xml
        assert "技术标提取" not in document_xml
        assert "香港中心技术标提取" not in document_xml
        assert "营业执照.png" in document_xml
        assert '<a:blip r:embed="rIdImage1"' in document_xml
    finally:
        db.close()


def test_technical_bid_word_export_embeds_enterprise_profile_image_attachment(monkeypatch):
    user = _create_user("admin")
    project, run = _create_project_run_and_file(user)
    db = SessionLocal()
    try:
        project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
        run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
        run_db.summary_json = dumps_json(
            {
                "technical_composition_plan": {
                    "status": "generated",
                    "components": [
                        {
                            "source_item_no": "7.3.1",
                            "component_key": "business_license",
                            "component_title": "营业执照及资质证明",
                            "classification": "fixed_enterprise_material",
                        }
                    ],
                }
            }
        )
        profile = EnterpriseProfileItem(
            item_uuid=str(uuid.uuid4()),
            category="certificate",
            title="营业执照图片资料",
            summary="营业执照扫描件",
            content_text="营业执照扫描件",
            structured_json=dumps_json({}),
            tags_json=dumps_json(["营业执照"]),
            source="manual",
            confidentiality="internal",
            status=ENTERPRISE_PROFILE_STATUS_ACTIVE,
            valid_until=date.today() + timedelta(days=365),
            created_by=user.id,
            updated_by=user.id,
            approved_by=user.id,
            approved_at=datetime(2026, 7, 6, 10, 1),
        )
        db.add(profile)
        db.flush()
        file_obj = FileObject(
            file_id=str(uuid.uuid4()),
            username=user.username,
            purpose="enterprise_profile",
            bucket="quote-files",
            object_name="enterprise_profile/test/business_license.png",
            original_filename="营业执照.png",
            content_type="image/png",
            size_bytes=len(PNG_1X1),
        )
        db.add(file_obj)
        db.flush()
        db.add(
            EnterpriseProfileFile(
                attachment_uuid=str(uuid.uuid4()),
                item_id=profile.id,
                file_id=file_obj.file_id,
                attachment_type="source",
                original_filename=file_obj.original_filename,
                description="营业执照扫描件",
                is_primary=True,
                uploaded_by=user.id,
            )
        )
        requirement = BidMaterialRequirement(
            requirement_uuid=str(uuid.uuid4()),
            project_id=project_db.id,
            parse_run_id=run_db.id,
            format_item_key="technical_composition:7_3_1",
            package_key="technical",
            package_title="技术标",
            section_key="technical_composition:7_3_1",
            item_title="7.3.1 营业执照及资质证明",
            requirement_type="profile",
            profile_category="certificate",
            material_key="technical_composition:7_3_1:business_license",
            title="营业执照资料",
            fulfillment_mode="enterprise_profile",
            status="approved",
            priority="high",
            submitted_profile_item_uuid=profile.item_uuid,
            created_by=user.id,
        )
        db.add(requirement)
        db.flush()
        db.add(
            BidDraftSection(
                draft_uuid=str(uuid.uuid4()),
                project_id=project_db.id,
                parse_run_id=run_db.id,
                section_key="technical_composition:7_3_1",
                section_title="7.3.1 营业执照及资质证明",
                section_type="technical",
                draft_mode="formal",
                draft_status="ready",
                content_markdown="# 7.3.1 营业执照及资质证明\n本章节引用企业资料库中的营业执照资料。",
                placeholders_json=dumps_json([]),
                source_requirement_ids_json=dumps_json([requirement.id]),
                generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                generator_type="rule",
                generator_model="test",
                review_status="draft",
                created_by=user.id,
            )
        )
        db.commit()

        monkeypatch.setattr(bidding_technical_word_export, "get_object_bytes", lambda object_name, bucket=None: PNG_1X1)

        docx_bytes = build_technical_bid_draft_export_document(db, project_db, run_db)
        with ZipFile(BytesIO(docx_bytes)) as archive:
            names = set(archive.namelist())
            document_xml = archive.read("word/document.xml").decode("utf-8")
            rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            content_types_xml = archive.read("[Content_Types].xml").decode("utf-8")
            media_bytes = archive.read("word/media/image1.png")

        assert "附件资料" in document_xml
        assert "营业执照.png" in document_xml
        assert '<a:blip r:embed="rIdImage1"' in document_xml
        assert "word/media/image1.png" in names
        assert media_bytes == PNG_1X1
        assert 'Target="media/image1.png"' in rels_xml
        assert 'ContentType="image/png"' in content_types_xml
    finally:
        db.close()


def test_technical_bid_word_export_api_downloads_docx(client):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        db = SessionLocal()
        try:
            project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
            run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            run_db.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "status": "generated",
                        "components": [
                            {
                                "source_item_no": "7.3.10",
                                "component_key": "quality_plan",
                                "component_title": "施工组织设计",
                                "classification": "tender_extracted_content",
                            }
                        ],
                    }
                }
            )
            db.add(
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_10",
                    section_title="7.3.10 施工组织设计",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.10 施工组织设计\n本工程施工组织设计草稿正文。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="draft",
                    created_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-draft/export",
            params={"run_uuid": run.run_uuid},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert response.content[:2] == b"PK"
        document_xml = _docx_document_xml(response.content)
        assert "施工组织设计" in document_xml
        assert "本工程施工组织设计草稿正文" in document_xml
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)


def test_technical_bid_final_word_export_api_downloads_docx(client):
    old_bidding = _set_flag("feature_bidding_mvp", True)
    try:
        user = _create_user("admin")
        headers = _login(client, user)
        project, run = _create_project_run_and_file(user)
        db = SessionLocal()
        try:
            project_db = db.query(BidProject).filter(BidProject.id == project.id).first()
            run_db = db.query(BidParseRun).filter(BidParseRun.id == run.id).first()
            run_db.summary_json = dumps_json(
                {
                    "technical_composition_plan": {
                        "status": "generated",
                        "components": [
                            {
                                "source_item_no": "7.3.10",
                                "component_key": "construction_organization",
                                "component_title": "施工组织设计",
                                "classification": "tender_extracted_content",
                            }
                        ],
                    }
                }
            )
            db.add(
                BidDraftSection(
                    draft_uuid=str(uuid.uuid4()),
                    project_id=project_db.id,
                    parse_run_id=run_db.id,
                    section_key="technical_composition:7_3_10",
                    section_title="7.3.10 施工组织设计",
                    section_type="technical",
                    draft_mode="formal",
                    draft_status="ready",
                    content_markdown="# 7.3.10 施工组织设计\n本工程施工组织设计正式正文。",
                    placeholders_json=dumps_json([]),
                    generation_decision_json=dumps_json({"package_key": "technical", "source": "technical_composition"}),
                    generator_type="rule",
                    generator_model="test",
                    review_status="accepted",
                    created_by=user.id,
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/technical-final/export",
            params={"run_uuid": run.run_uuid},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert response.content[:2] == b"PK"
        document_xml = _docx_document_xml(response.content)
        header_xml = _docx_part_xml(response.content, "word/header1.xml")
        footer_xml = _docx_part_xml(response.content, "word/footer1.xml")
        assert "技术标投标文件草稿" not in document_xml
        assert "建设工程施工招标" in document_xml
        assert "本工程施工组织设计正式正文" in document_xml
        assert "BIZ-4c-2 技术标组成识别测试" in header_xml
        assert "技术标部分" in footer_xml
        assert " PAGE " in footer_xml
        assert " NUMPAGES " in footer_xml
    finally:
        _set_flag("feature_bidding_mvp", old_bidding)


def test_component_source_no_accepts_independent_multilevel_numbering():
    assert bidding_draft_sections._technical_component_source_no(
        {
            "source_item_no": "1.4.2",
            "component_key": "switch_socket_installation",
            "component_title": "\u5f00\u5173\u3001\u63d2\u5ea7\u5b89\u88c5\u5de5\u7a0b",
        }
    ) == "1.4.2"
    assert bidding_draft_sections._technical_component_source_no(
        {"component_title": "2.3.4.5 \u4e13\u9879\u65bd\u5de5\u65b9\u6848"}
    ) == "2.3.4.5"


def test_generic_pending_marker_preserves_unknown_input_and_formalizes_safe_commitment():
    content = (
        "\u3010\u5f85\u786e\u8ba4\uff1a\u6211\u65b9\u62df\u6295\u5165\u672c\u5206\u9879\u5de5\u7a0b\u7684\u7ba1\u7406\u4eba\u5458\u53ca\u52b3\u52a8\u529b\u8ba1\u5212\uff0c\u5305\u62ec\u5177\u4f53\u540d\u5355\u548c\u4eba\u6570\u3011\n"
        "\u3010\u5f85\u786e\u8ba4\uff1a\u5f00\u5173\u3001\u63d2\u5ea7\u5177\u4f53\u4f4d\u7f6e\u5206\u5e03\u56fe\u9700\u57fa\u4e8e\u7535\u6c14\u65bd\u5de5\u6df1\u5316\u8bbe\u8ba1\u8865\u5145\u3011"
    )

    updated, replacement_count = bidding_draft_sections._formalize_scheme_pending_markers(
        content,
        section_no="1.4.2",
        preserve_unresolved=True,
    )

    assert replacement_count == 1
    assert "\u3010\u5f85\u786e\u8ba4\uff1a\u6211\u65b9\u62df\u6295" in updated
    assert "\u7ed3\u5408\u73b0\u573a\u6761\u4ef6\u6df1\u5316\u5b8c\u5584" in updated
    assert len(bidding_draft_sections._pending_confirmation_markers(updated)) == 1


def test_requirement_fact_sentences_are_scoped_to_component_title():
    context = {
        "technical_requirements": {
            "material_procurement": {
                "label": "\u6750\u6599\u91c7\u8d2d",
                "summary": "\u53d1\u5305\u65b9\u6709\u6743\u5bf9\u4e0d\u5408\u683c\u4f9b\u5e94\u5546\u8ffd\u7a76\u8d23\u4efb",
            }
        }
    }

    stone = bidding_draft_sections._technical_template_requirement_fact_sentences(
        context,
        ("material_procurement",),
        subject="\u5730\u9762\u94fa\u77f3\u6750\u5de5\u7a0b",
    )
    socket = bidding_draft_sections._technical_template_requirement_fact_sentences(
        context,
        ("material_procurement",),
        subject="\u5f00\u5173\u3001\u63d2\u5ea7\u5b89\u88c5\u5de5\u7a0b",
    )

    assert stone != socket
    assert "\u5730\u9762\u94fa\u77f3\u6750\u5de5\u7a0b" in stone[0]
    assert "\u5f00\u5173\u3001\u63d2\u5ea7\u5b89\u88c5\u5de5\u7a0b" in socket[0]


def test_llm_editorial_supplement_section_is_formalized_when_all_items_are_safe():
    content = """# 1.2.4 木饰面工程

## 待人工完善
- 请补充企业资质、项目管理团队具体信息及类似办公空间木饰面施工业绩。
- 请根据现场实际情况细化基层做法及与其他专业的接口处理。
- 如企业有内部工法或更严格的验收标准，请补充说明。
"""

    updated, audit = bidding_draft_sections._formalize_llm_editorial_supplement_sections(content)

    assert audit["status"] == "formalized"
    assert audit["formalized_item_count"] == 3
    assert "待人工完善" not in updated
    assert "## 深化实施与资料衔接" in updated
    assert "相应资格审查及资信章节统一响应" in updated
    assert "经发包人及监理审批后实施" in updated
    assert "企业标准高于招标文件及现行规范时从严执行" in updated


def test_llm_editorial_supplement_section_keeps_unknown_hard_fact_as_blocker():
    content = """# 1.2.1 墙面瓷砖粘贴工程

## 待人工完善
- 请补充瓷砖品牌及具体规格型号。
"""

    updated, audit = bidding_draft_sections._formalize_llm_editorial_supplement_sections(content)

    assert audit["status"] == "not_applicable"
    assert "## 待人工完善" in updated
    assert "请补充瓷砖品牌及具体规格型号" in updated
