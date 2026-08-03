from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from app.core.config import settings
from app.core.database import Base, SessionLocal
from app.core.security import get_password_hash
from app.models.bidding import (
    BidFileFormatPlan,
    BidMaterialRequirement,
    BidMaterialRequirementEvent,
    BidParseRun,
    BidProject,
)
from app.models.enterprise_profile import ENTERPRISE_PROFILE_STATUS_ACTIVE, EnterpriseProfileItem
from app.models.file_object import FileObject
from app.models.user import User, UserRole
from app.services.bidding_parser import dumps_json


PASSWORD = "secret123"


def _set_flag(name: str, value):
    old_value = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return old_value


def _create_user(role: str = "staff") -> User:
    username = f"biz4c1_{role}_{uuid.uuid4().hex[:10]}"
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
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz4c1 test seed"))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> dict:
    response = client.post("/api/v1/auth/login", data={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_project_run_and_plan(user: User, *, with_plan: bool = True) -> tuple[BidProject, BidParseRun, BidFileFormatPlan | None]:
    db = SessionLocal()
    try:
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="BIZ-4c-1 技术标资料需求测试",
            status="parsed",
            owner_user_id=user.id,
            created_by=user.id,
            summary_json=dumps_json({"biz_stage": "BIZ-4c-1"}),
        )
        db.add(project)
        db.flush()
        run = BidParseRun(
            run_uuid=str(uuid.uuid4()),
            project_id=project.id,
            status="completed",
            parser_version="test-parser",
            input_file_ids_json=dumps_json([]),
            summary_json=dumps_json({}),
            created_by=user.id,
            finished_at=datetime(2026, 7, 3, 10, 0),
        )
        db.add(run)
        db.flush()
        plan = None
        if with_plan:
            structure = {
                "packages": [
                    {
                        "package_key": "business",
                        "package_title": "商务标",
                        "package_type": "business",
                        "items": [
                            {
                                "item_key": "business:bid_letter",
                                "base_item_key": "bid_letter",
                                "item_title": "投标函",
                                "package_key": "business",
                                "content_type": "fixed_form",
                                "content_type_label": "固定表单",
                                "owner_role": "经营",
                                "generation_strategy": "manual_fill",
                                "requires_signature": True,
                                "requires_attachment": False,
                                "evidence": [
                                    {
                                        "source_file": "投标格式文件.docx",
                                        "source_location": "商务标目录第1项",
                                        "original_text": "投标函须按格式填写并盖章。",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "package_key": "technical",
                        "package_title": "技术标",
                        "package_type": "technical",
                        "items": [
                            {
                                "item_key": "technical:business_license",
                                "base_item_key": "business_license",
                                "item_title": "营业执照及资质证明",
                                "package_key": "technical",
                                "content_type": "qualification_attachment",
                                "content_type_label": "资格附件",
                                "owner_role": "经营",
                                "generation_strategy": "manual_upload",
                                "requires_signature": True,
                                "requires_attachment": True,
                                "evidence": [
                                    {
                                        "source_file": "投标格式文件.docx",
                                        "source_location": "技术标目录第1项",
                                        "original_text": "营业执照及资质证明需附扫描件。",
                                    }
                                ],
                            },
                            {
                                "item_key": "technical:construction_plan",
                                "base_item_key": "construction_plan",
                                "item_title": "施工组织设计",
                                "package_key": "technical",
                                "content_type": "draft_section",
                                "content_type_label": "正文章节",
                                "owner_role": "技术",
                                "generation_strategy": "generate_draft",
                                "requires_signature": False,
                                "requires_attachment": False,
                                "evidence": [
                                    {
                                        "source_file": "投标格式文件.docx",
                                        "source_location": "技术标目录第2项",
                                        "original_text": "施工组织设计应包含进度、质量、安全文明施工措施。",
                                    }
                                ],
                            },
                            {
                                "item_key": "technical:technical_deviation",
                                "base_item_key": "technical_deviation",
                                "item_title": "技术规格偏离表",
                                "package_key": "technical",
                                "content_type": "fixed_form",
                                "content_type_label": "固定表单",
                                "owner_role": "技术",
                                "generation_strategy": "manual_fill",
                                "requires_signature": True,
                                "requires_attachment": False,
                                "evidence": [
                                    {
                                        "source_file": "投标格式文件.docx",
                                        "source_location": "技术标目录第3项",
                                        "original_text": "技术规格偏离表须填写并盖章。",
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "packaging_requirements": [],
            }
            plan = BidFileFormatPlan(
                plan_uuid=str(uuid.uuid4()),
                project_id=project.id,
                parse_run_id=run.id,
                format_version="test-format",
                format_source="external_format_file",
                package_mode="separate_business_technical",
                review_status="confirmed",
                structure_json=dumps_json(structure),
                summary_json=dumps_json({"item_count": 3}),
                warnings_json=dumps_json([]),
                created_by=user.id,
                confirmed_by=user.id,
                confirmed_at=datetime(2026, 7, 3, 10, 5),
            )
            db.add(plan)
        db.commit()
        db.refresh(project)
        db.refresh(run)
        if plan:
            db.refresh(plan)
        return project, run, plan
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
            approved_at=datetime(2026, 7, 3, 10, 1),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    finally:
        db.close()


def _append_technical_format_item(plan: BidFileFormatPlan, item: dict) -> None:
    db = SessionLocal()
    try:
        plan_obj = db.query(BidFileFormatPlan).filter(BidFileFormatPlan.id == plan.id).first()
        structure = json.loads(plan_obj.structure_json or "{}")
        technical = next(package for package in structure["packages"] if package["package_key"] == "technical")
        technical["items"].append(item)
        plan_obj.structure_json = dumps_json(structure)
        db.commit()
    finally:
        db.close()


def _create_file_object(user: User, filename: str) -> FileObject:
    db = SessionLocal()
    try:
        file_obj = FileObject(
            file_id=str(uuid.uuid4()),
            username=user.username,
            purpose="bidding_material_requirement",
            bucket="test-bucket",
            object_name=f"bidding_material_requirement/{uuid.uuid4().hex}/{filename}",
            original_filename=filename,
            content_type="application/pdf",
            size_bytes=1234,
        )
        db.add(file_obj)
        db.commit()
        db.refresh(file_obj)
        return file_obj
    finally:
        db.close()


def test_bid_material_requirement_tables_are_registered_in_metadata():
    assert {
        "bid_material_requirements",
        "bid_material_requirement_events",
    }.issubset(set(Base.metadata.tables))


def test_generate_material_requirements_from_format_plan_and_profile_candidates(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, _ = _create_project_run_and_plan(user, with_plan=True)
    business_license = _create_active_profile(
        user,
        category="certificate",
        title="营业执照",
        summary="统一社会信用代码：91440000TEST",
    )
    construction_plan = _create_active_profile(
        user,
        category="technical_solution",
        title="施工组织设计通用方案",
        summary="包含进度、质量、安全文明施工措施的技术标素材。",
    )
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    summary = response.json()["summary"]
    assert summary["total"] >= 4
    assert summary["candidate_found_count"] >= 2

    license_rows = [row for row in rows if row["profile_category"] == "certificate"]
    assert license_rows
    assert {row["candidate_profile_item_uuid"] for row in license_rows} == {business_license.item_uuid}
    assert not any("签字盖章" in row["title"] for row in license_rows)
    assert {row["title"] for row in license_rows} == {"营业执照及资质证明资料"}

    solution_rows = [row for row in rows if row["profile_category"] == "technical_solution"]
    assert solution_rows
    assert solution_rows[0]["candidate_profile_item_uuid"] == construction_plan.item_uuid

    missing_row = next(row for row in rows if row["fulfillment_mode"] == "manual_fill" and row["status"] == "missing")
    old_mvp = _set_flag("feature_bidding_mvp", True)
    try:
        update_response = client.patch(
            f"/api/v1/admin/bidding/bid-draft/material-requirements/{missing_row['requirement_uuid']}",
            headers=headers,
            json={"submitted_value": "本项目技术规格无偏离。", "status": "approved", "notes": "人工确认"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)

    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["data"]["status"] == "approved"

    db = SessionLocal()
    try:
        requirement = (
            db.query(BidMaterialRequirement)
            .filter(BidMaterialRequirement.requirement_uuid == missing_row["requirement_uuid"])
            .first()
        )
        assert requirement.submitted_value == "本项目技术规格无偏离。"
        events = (
            db.query(BidMaterialRequirementEvent)
            .filter(BidMaterialRequirementEvent.requirement_id == requirement.id)
            .order_by(BidMaterialRequirementEvent.id.asc())
            .all()
        )
        assert events[-1].event_type == "updated"
    finally:
        db.close()


def test_material_requirement_package_scope_keeps_business_and_technical_independent(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, _ = _create_project_run_and_plan(user, with_plan=True)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        business_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "business"},
        )
        technical_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
        technical_list = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "technical"},
        )
        business_outline = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "business"},
        )
        technical_outline = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "technical"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert business_response.status_code == 200, business_response.text
    assert technical_response.status_code == 200, technical_response.text
    business_rows = business_response.json()["data"]
    technical_rows = technical_response.json()["data"]
    assert business_rows
    assert technical_rows
    assert {row["package_key"] for row in business_rows} == {"business"}
    assert {row["package_key"] for row in technical_rows} == {"technical"}
    assert not any("施工组织设计" in row["item_title"] for row in business_rows)
    assert not any("投标函" in row["item_title"] for row in technical_rows)
    assert technical_list.status_code == 200, technical_list.text
    assert {row["package_key"] for row in technical_list.json()["data"]} == {"technical"}
    assert business_outline.status_code == 200, business_outline.text
    assert technical_outline.status_code == 200, technical_outline.text
    business_sections = business_outline.json()["data"]["sections"]
    technical_sections = technical_outline.json()["data"]["sections"]
    assert business_sections
    assert technical_sections
    assert {section["package_key"] for section in business_sections if section.get("package_key")} == {"business"}
    assert {section["package_key"] for section in technical_sections if section.get("package_key")} == {"technical"}


def test_legacy_enterprise_material_signature_rows_are_removed(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, run, _ = _create_project_run_and_plan(user, with_plan=True)
    legacy_uuid = str(uuid.uuid4())
    stale_uuid = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(
            BidMaterialRequirement(
                requirement_uuid=legacy_uuid,
                project_id=project.id,
                parse_run_id=run.id,
                format_item_key="technical:business_license",
                package_key="technical",
                package_title="技术标",
                section_key="technical_business_license",
                item_title="营业执照及资质证明",
                requirement_type="field",
                profile_category=None,
                material_key="technical:business_license:signature_stamp",
                title="营业执照及资质证明签字盖章确认",
                description="该目录项需要签字、盖章或授权签署确认，生成草稿前需明确签署口径。",
                fulfillment_mode="manual_fill",
                status="approved",
                priority="high",
                owner_role="经营",
                submitted_value="已人工确认盖章",
                created_by=user.id,
                updated_by=user.id,
                reviewed_by=user.id,
            )
        )
        db.add(
            BidMaterialRequirement(
                requirement_uuid=stale_uuid,
                project_id=project.id,
                parse_run_id=run.id,
                format_item_key="technical:business_license",
                package_key="technical",
                package_title="技术标",
                section_key="technical_business_license",
                item_title="营业执照及资质证明",
                requirement_type="profile",
                profile_category="certificate",
                material_key="technical:business_license:signature_stamp_profile",
                title="营业执照及资质证明签字盖章资料",
                description="上一版错误 key 产生的重复行。",
                fulfillment_mode="enterprise_profile",
                status="missing",
                priority="high",
                owner_role="经营",
                created_by=user.id,
            )
        )
        db.commit()
    finally:
        db.close()

    business_license = _create_active_profile(
        user,
        category="certificate",
        title="营业执照",
        summary="统一社会信用代码：91440000TEST",
    )
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        list_response = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "technical"},
        )
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert list_response.status_code == 200, list_response.text
    assert not any("营业执照及资质证明签字盖章" in row["title"] for row in list_response.json()["data"])
    assert not any(row["requirement_uuid"] == legacy_uuid for row in list_response.json()["data"])
    assert not any(row["requirement_uuid"] == stale_uuid for row in list_response.json()["data"])

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    license_rows = [row for row in rows if row["profile_category"] == "certificate"]
    assert {row["title"] for row in license_rows} == {"营业执照及资质证明资料"}
    assert license_rows[0]["candidate_profile_item_uuid"] == business_license.item_uuid
    assert not any(row["requirement_uuid"] == legacy_uuid for row in rows)
    assert not any(row["requirement_uuid"] == stale_uuid for row in rows)
    assert not any("营业执照及资质证明签字盖章" in row["title"] for row in rows)


def test_technical_draft_section_uses_selected_enterprise_profile_material(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, _ = _create_project_run_and_plan(user, with_plan=True)
    profile = _create_active_profile(
        user,
        category="technical_solution",
        title="施工组织设计通用措施库",
        summary="采用样板先行、分区施工、周计划协调、隐蔽验收和成品保护闭环管理。",
    )
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        generated = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
        assert generated.status_code == 200, generated.text
        material_row = next(
            row
            for row in generated.json()["data"]
            if row["profile_category"] == "technical_solution" and row["item_title"] == "施工组织设计"
        )
        patched = client.patch(
            f"/api/v1/admin/bidding/bid-draft/material-requirements/{material_row['requirement_uuid']}",
            headers=headers,
            json={
                "submitted_profile_item_uuid": profile.item_uuid,
                "status": "approved",
                "notes": "从企业资料库填入",
            },
        )
        assert patched.status_code == 200, patched.text
        outline = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "technical"},
        )
        assert outline.status_code == 200, outline.text
        section = next(section for section in outline.json()["data"]["sections"] if section["section_title"] == "施工组织设计")
        draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={
                "run_uuid": "latest",
                "section_key": section["section_key"],
                "package_key": "technical",
                "generator_type": "rule",
            },
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()["data"]
    enterprise_evidence = [item for item in draft["evidence"] if item.get("source_kind") == "enterprise_profile"]
    assert enterprise_evidence
    assert enterprise_evidence[0]["profile_item_uuid"] == profile.item_uuid
    assert "样板先行" in enterprise_evidence[0]["original_text"]
    assert "企业资料库" in draft["content_markdown"]


def test_technical_material_requirement_accepts_multiple_profiles_and_files(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, _ = _create_project_run_and_plan(user, with_plan=True)
    profile_a = _create_active_profile(
        user,
        category="technical_solution",
        title="施工组织设计质量措施",
        summary="质量措施：样板先行、隐蔽验收、实测实量和整改闭环。",
    )
    profile_b = _create_active_profile(
        user,
        category="technical_solution",
        title="施工组织设计安全措施",
        summary="安全措施：临电管理、动火审批、消防巡检和文明施工。",
    )
    file_a = _create_file_object(user, "quality-plan.pdf")
    file_b = _create_file_object(user, "safety-plan.pdf")
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        generated = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
        assert generated.status_code == 200, generated.text
        material_row = next(
            row
            for row in generated.json()["data"]
            if row["profile_category"] == "technical_solution" and row["item_title"] == "施工组织设计"
        )
        patched = client.patch(
            f"/api/v1/admin/bidding/bid-draft/material-requirements/{material_row['requirement_uuid']}",
            headers=headers,
            json={
                "submitted_profile_item_uuids": [profile_a.item_uuid, profile_b.item_uuid],
                "submitted_file_ids": [file_a.file_id, file_b.file_id],
                "status": "approved",
                "notes": "提交多份技术标资料",
            },
        )
        assert patched.status_code == 200, patched.text
        patched_row = patched.json()["data"]
        assert patched_row["submitted_profile_item_uuid"] == profile_a.item_uuid
        assert patched_row["submitted_profile_item_uuids"] == [profile_a.item_uuid, profile_b.item_uuid]
        assert patched_row["submitted_file_id"] == file_a.file_id
        assert patched_row["submitted_file_ids"] == [file_a.file_id, file_b.file_id]

        outline = client.get(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/outline",
            headers=headers,
            params={"run_uuid": "latest", "package_key": "technical"},
        )
        assert outline.status_code == 200, outline.text
        section = next(section for section in outline.json()["data"]["sections"] if section["section_title"] == "施工组织设计")
        draft_response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/sections/generate",
            headers=headers,
            json={
                "run_uuid": "latest",
                "section_key": section["section_key"],
                "package_key": "technical",
                "generator_type": "rule",
            },
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert draft_response.status_code == 200, draft_response.text
    evidence = draft_response.json()["data"]["evidence"]
    assert {item.get("profile_item_uuid") for item in evidence if item.get("source_kind") == "enterprise_profile"} >= {
        profile_a.item_uuid,
        profile_b.item_uuid,
    }
    assert {item.get("submitted_file_id") for item in evidence if item.get("submitted_file_id")} >= {
        file_a.file_id,
        file_b.file_id,
    }


def test_technical_solution_requirement_does_not_fallback_to_unrelated_profile(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, plan = _create_project_run_and_plan(user, with_plan=True)
    _append_technical_format_item(
        plan,
        {
            "item_key": "technical:site_office_storage",
            "base_item_key": "site_office_storage",
            "item_title": "办公室/工具间/材料间管理方案",
            "package_key": "technical",
            "content_type": "draft_section",
            "content_type_label": "正文章节",
            "owner_role": "技术",
            "generation_strategy": "generate_draft",
            "requires_signature": False,
            "requires_attachment": False,
            "evidence": [
                {
                    "source_file": "投标格式文件.docx",
                    "source_location": "技术标目录第11项",
                    "original_text": "办公室、工具间、材料间的管理方案。",
                }
            ],
        },
    )
    _create_active_profile(
        user,
        category="technical_solution",
        title="类似工程业绩清单",
        summary="近三年类似工程项目名称、合同金额和建设单位清单。",
    )
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["data"] if row["item_title"] == "办公室/工具间/材料间管理方案")
    assert row["profile_category"] == "technical_solution"
    assert row["status"] == "missing"
    assert row["candidate_profile_item_uuid"] is None
    assert row["candidates"] == []


def test_project_performance_requirement_keeps_specific_similar_project_query(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, plan = _create_project_run_and_plan(user, with_plan=True)
    _append_technical_format_item(
        plan,
        {
            "item_key": "technical:similar_experience",
            "base_item_key": "similar_experience",
            "item_title": "类似工程业绩",
            "package_key": "technical",
            "content_type": "qualification_attachment",
            "content_type_label": "业绩附件",
            "owner_role": "经营",
            "generation_strategy": "manual_upload",
            "requires_signature": False,
            "requires_attachment": True,
            "evidence": [
                {
                    "source_file": "投标格式文件.docx",
                    "source_location": "技术标目录第7项",
                    "original_text": "投标人近三年已完成的类似工程经验。",
                }
            ],
        },
    )
    _create_active_profile(
        user,
        category="project_performance",
        title="项目管理与业绩表格截图素材包",
        summary="项目管理机构和业绩汇总表格截图。",
    )
    contract_profile = _create_active_profile(
        user,
        category="project_performance",
        title="广东拓斯达写字楼精装修工程合同",
        summary="类似工程业绩合同扫描件。",
    )
    old_mvp = _set_flag("feature_bidding_mvp", True)
    old_profile = _set_flag("feature_enterprise_profile", True)
    try:
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest", "package_key": "technical"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)
        _set_flag("feature_enterprise_profile", old_profile)

    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["data"] if row["item_title"] == "类似工程业绩")
    assert row["status"] == "candidate_found"
    assert row["candidate_profile_item_uuid"] == contract_profile.item_uuid
    assert row["normalized"]["keyword"] == "类似工程业绩"
    assert row["candidates"][0]["title"] == "广东拓斯达写字楼精装修工程合同"


def test_generate_material_requirements_requires_format_plan(client):
    user = _create_user("staff")
    headers = _login(client, user)
    project, _, _ = _create_project_run_and_plan(user, with_plan=False)
    old_mvp = _set_flag("feature_bidding_mvp", True)
    try:
        response = client.post(
            f"/api/v1/admin/bidding/projects/{project.project_uuid}/bid-draft/material-requirements/generate",
            headers=headers,
            json={"run_uuid": "latest"},
        )
    finally:
        _set_flag("feature_bidding_mvp", old_mvp)

    assert response.status_code == 422
    assert response.json()["detail"] == "BID_FILE_FORMAT_PLAN_REQUIRED"
