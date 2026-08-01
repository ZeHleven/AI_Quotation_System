from __future__ import annotations

import json
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.dependencies import get_current_user
from app.main import app
from app.models.bid_intake_runtime import (
    BidIntakeAgentRun,
    BidIntakeAssessment,
    BidIntakePolicyCalibrationLabel,
    BidIntakeWorkerHeartbeat,
)
from app.models.bidding import BidProject
from app.models.bidding import BidProjectFile
from app.models.tender_evidence import (
    BidEvidenceDocument,
    BidEvidenceManifest,
)
from app.models.user import User
from app.services.bid_intake_runtime import (
    claim_agent_run,
    create_assessment_run,
    fail_claimed_agent_run,
    touch_worker_heartbeat,
)


def _seed() -> tuple[User, BidProject]:
    db = SessionLocal()
    try:
        user = User(
            username=f"phase4a-{uuid.uuid4().hex[:10]}",
            hashed_password="test",
            role="manager",
            quota=100,
            is_active=True,
        )
        db.add(user)
        db.flush()
        project = BidProject(
            project_uuid=str(uuid.uuid4()),
            project_name="Phase 4a API 测试",
            status="draft",
            owner_user_id=user.id,
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        source_file = BidProjectFile(
            file_uuid=str(uuid.uuid4()),
            project_id=project.id,
            file_type="tender_document",
            original_filename="招标文件.txt",
            content_type="text/plain",
            size_bytes=100,
            sha256=uuid.uuid4().hex * 2,
            parser_status="parsed",
            parser_version="phase4b-test",
            extracted_text="投标截止时间测试",
            segments_json="[]",
            page_count=1,
            section_count=1,
            uploaded_by=user.id,
        )
        db.add(source_file)
        db.flush()
        evidence_document = BidEvidenceDocument(
            evidence_document_uuid=str(uuid.uuid4()),
            project_id=project.id,
            source_file_id=source_file.id,
            document_key="tender-document",
            document_type="tender_document",
            version_no=1,
            original_filename=source_file.original_filename,
            sha256=source_file.sha256,
            parser_version=source_file.parser_version,
            parse_status="ready",
            active=True,
            created_by=user.id,
        )
        db.add(evidence_document)
        manifest = BidEvidenceManifest(
            manifest_uuid=str(uuid.uuid4()),
            project_id=project.id,
            version_no=1,
            manifest_hash=f"manifest-{uuid.uuid4().hex}",
            snapshot_json=json.dumps(
                {
                    "case_id": project.project_uuid,
                    "manifest_version": 1,
                    "manifest_hash": "placeholder-hash",
                    "documents": [],
                }
            ),
            active=True,
            created_by=user.id,
        )
        db.add(manifest)
        db.commit()
        db.refresh(user)
        db.refresh(project)
        list(user.role_assignments)
        db.expunge(user)
        db.expunge(project)
        return user, project
    finally:
        db.close()


def test_runtime_api_create_query_and_idempotent_decision(
    client,
    monkeypatch,
) -> None:
    user, project = _seed()
    monkeypatch.setenv("BID_INTAKE_AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("BID_INTAKE_MCP_URL", "http://mcp.test/mcp")
    monkeypatch.setenv(
        "TENDER_MCP_JWT_SECRET",
        "phase4b-test-secret-at-least-32-characters",
    )
    monkeypatch.setenv(
        "BID_INTAKE_MODEL_API_URL",
        "http://model.test/chat/completions",
    )
    monkeypatch.setenv("BID_INTAKE_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("BID_INTAKE_MODEL_ID", "test-model")
    previous_feature = settings.feature_bidding_mvp
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        db = SessionLocal()
        try:
            touch_worker_heartbeat(
                db,
                worker_id="phase4b-api-test-worker",
                status="online",
                process_id=1234,
                capabilities={
                    "mcp_configured": True,
                    "model_configured": True,
                    "policy_configured": True,
                    "active_policy_version": (
                        "qs_bid_decision_policy_2026_01"
                    ),
                    "checkpoint_backend": "sqlalchemy",
                },
            )
            db.commit()
        finally:
            db.close()

        readiness_response = client.get(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                "/bid-intake/readiness"
            )
        )
        assert readiness_response.status_code == 200
        readiness = readiness_response.json()["data"]
        assert readiness["ready_to_start"] is True
        assert readiness["worker"]["online_count"] >= 1
        assert readiness["evidence"]["ready_document_count"] == 1
        assert readiness["policy"] == {
            "active_version": "qs_bid_decision_policy_2026_01",
            "configured": True,
        }

        created_response = client.post(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                "/bid-intake/assessments"
            ),
            json={
                "analysis_goal": "判断是否应进入报价立项。",
                "max_attempts": 3,
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()["data"]
        assessment_uuid = created["assessment"]["assessment_uuid"]
        run_uuid = created["run"]["run_uuid"]
        assert created["run"]["status"] == "queued"
        assert (
            created["assessment"]["policy_version"]
            == "qs_bid_decision_policy_2026_01"
        )
        assert created["dispatch_mode"] == "dedicated_worker_poll"

        detail_response = client.get(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                f"/bid-intake/assessments/{assessment_uuid}"
            )
        )
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["runs"][0]["run_uuid"] == run_uuid

        db = SessionLocal()
        try:
            run = (
                db.query(BidIntakeAgentRun)
                .filter(BidIntakeAgentRun.run_uuid == run_uuid)
                .one()
            )
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            run.status = "waiting_human"
            run.phase = "human_review"
            assessment.status = "waiting_human"
            db.commit()
        finally:
            db.close()

        decision_uuid = str(uuid.uuid4())
        decision_url = (
            f"/api/v1/admin/bidding/projects/{project.project_uuid}"
            f"/bid-intake/assessments/{assessment_uuid}/runs/{run_uuid}"
            "/decision"
        )
        command = {
            "decision_uuid": decision_uuid,
            "action": "approved_with_conditions",
            "report_version": 1,
            "manifest_version": 1,
            "conditions": ["成本部复核付款条件"],
        }
        first = client.post(decision_url, json=command)
        second = client.post(decision_url, json=command)
        assert first.status_code == 202
        assert first.json()["data"]["idempotent"] is False
        assert second.status_code == 202
        assert second.json()["data"]["idempotent"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(
            settings,
            "feature_bidding_mvp",
            previous_feature,
        )


def test_runtime_api_blocks_start_when_worker_is_offline(
    client,
    monkeypatch,
) -> None:
    user, project = _seed()
    monkeypatch.setenv("BID_INTAKE_AGENT_RUNTIME_ENABLED", "true")
    previous_feature = settings.feature_bidding_mvp
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = lambda: user
    db = SessionLocal()
    try:
        for heartbeat in db.query(BidIntakeWorkerHeartbeat).all():
            heartbeat.status = "stopped"
        db.commit()
    finally:
        db.close()
    try:
        readiness_response = client.get(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                "/bid-intake/readiness"
            )
        )
        assert readiness_response.status_code == 200
        readiness = readiness_response.json()["data"]
        assert readiness["ready_to_start"] is False
        assert "WORKER_OFFLINE" in readiness["blockers"]

        created_response = client.post(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                "/bid-intake/assessments"
            ),
            json={"analysis_goal": "不应进入队列。", "max_attempts": 3},
        )
        assert created_response.status_code == 409
        detail = created_response.json()["detail"]
        assert detail["code"] == "BID_INTAKE_RUNTIME_NOT_READY"
        assert "WORKER_OFFLINE" in detail["blockers"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(
            settings,
            "feature_bidding_mvp",
            previous_feature,
        )


def test_runtime_api_cancels_active_run_and_rejects_late_worker_write(
    client,
    monkeypatch,
) -> None:
    user, project = _seed()
    monkeypatch.setenv("BID_INTAKE_AGENT_RUNTIME_ENABLED", "true")
    previous_feature = settings.feature_bidding_mvp
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        db = SessionLocal()
        try:
            created = create_assessment_run(
                db,
                project=project,
                current_user=user,
                analysis_goal="验证运行终止与旧租约隔离。",
            )
            db.commit()
            assessment_uuid = created.assessment.assessment_uuid
            run_uuid = created.run.run_uuid
        finally:
            db.close()

        db = SessionLocal()
        try:
            claim = claim_agent_run(
                db,
                worker_id="cancel-test-worker",
                run_uuid=run_uuid,
                lease_seconds=3600,
            )
            assert claim is not None
            old_lease_token = claim.lease_token
            db.commit()
        finally:
            db.close()

        cancel_url = (
            f"/api/v1/admin/bidding/projects/{project.project_uuid}"
            f"/bid-intake/assessments/{assessment_uuid}/runs/{run_uuid}"
            "/cancel"
        )
        first = client.post(cancel_url)
        assert first.status_code == 202
        assert first.json()["data"]["idempotent"] is False
        cancelled_run = first.json()["data"]["run"]
        assert cancelled_run["status"] == "cancelled"
        assert cancelled_run["phase"] == "cancelled"
        assert cancelled_run["lease_expires_at"] is None
        assert cancelled_run["events"][-1]["event_type"] == "run_cancelled"

        repeated = client.post(cancel_url)
        assert repeated.status_code == 202
        assert repeated.json()["data"]["idempotent"] is True

        db = SessionLocal()
        try:
            late_failure_applied = fail_claimed_agent_run(
                db,
                run_uuid=run_uuid,
                lease_token=old_lease_token,
                error_code="LATE_WORKER_FAILURE",
                error_message="旧 Worker 晚返回不应覆盖取消状态。",
            )
            assert late_failure_applied is False
            assert (
                claim_agent_run(
                    db,
                    worker_id="another-worker",
                    run_uuid=run_uuid,
                )
                is None
            )
            run = (
                db.query(BidIntakeAgentRun)
                .filter(BidIntakeAgentRun.run_uuid == run_uuid)
                .one()
            )
            assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == run.assessment_id)
                .one()
            )
            assert run.status == "cancelled"
            assert run.lease_token is None
            assert assessment.status == "cancelled"
        finally:
            db.close()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(
            settings,
            "feature_bidding_mvp",
            previous_feature,
        )


def test_calibration_gold_label_is_versioned_and_report_is_read_only(
    client,
) -> None:
    user, project = _seed()
    assessment_uuid = str(uuid.uuid4())
    db = SessionLocal()
    try:
        manifest = (
            db.query(BidEvidenceManifest)
            .filter(BidEvidenceManifest.project_id == project.id)
            .one()
        )
        manifest.snapshot_json = json.dumps(
            {
                "case_id": project.project_uuid,
                "manifest_version": manifest.version_no,
                "manifest_hash": manifest.manifest_hash,
                "documents": [],
            },
            ensure_ascii=False,
        )
        assessment = BidIntakeAssessment(
            assessment_uuid=assessment_uuid,
            project_id=project.id,
            manifest_id=manifest.id,
            manifest_version=manifest.version_no,
            manifest_hash=manifest.manifest_hash,
            policy_version="qs_bid_decision_policy_2026_01",
            analysis_goal="形成历史校准金标。",
            status="waiting_human",
            report_version=1,
            recommendation="need_supplement",
            gate_status="supplement_required",
            assessment_json=json.dumps(
                {
                    "project_summary": "历史项目校准样本",
                    "recommendation": "need_supplement",
                    "project_facts": [],
                    "dimension_reviews": [
                        {
                            "dimension": "project_basics",
                            "status": "unresolved",
                            "summary": "经营信息尚未补齐。",
                            "evidence_refs": [],
                        }
                    ],
                    "key_findings": [],
                    "missing_materials": [],
                    "conflicts": [],
                    "risks": [],
                    "policy_factors": [],
                    "unresolved_questions": ["补充经营信息"],
                    "confidence": 0,
                    "termination_reason": "历史快照。",
                },
                ensure_ascii=False,
            ),
            policy_evaluation_json=json.dumps(
                {"decision": "need_supplement"}
            ),
            created_by=user.id,
        )
        db.add(assessment)
        db.flush()
        assessment_id = assessment.id
        db.commit()
    finally:
        db.close()

    previous_feature = settings.feature_bidding_mvp
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = lambda: user
    label_url = (
        f"/api/v1/admin/bidding/projects/{project.project_uuid}"
        f"/bid-intake/assessments/{assessment_uuid}/calibration-label"
    )
    payload = {
        "expected_current_label_version": 0,
        "dataset_split": "development",
        "label_basis": "pre_bid_expert_review",
        "expected_decision": "need_supplement",
        "hard_stop_expected": False,
        "rationale": "资料不足，应先补充经营信息。",
    }
    try:
        created = client.post(label_url, json=payload)
        assert created.status_code == 201
        label = created.json()["data"]["label"]
        assert label["label_version"] == 1
        assert label["source_report_version"] == 1

        stale = client.post(label_url, json=payload)
        assert stale.status_code == 409
        assert stale.json()["detail"] == "STALE_CALIBRATION_LABEL_VERSION"

        payload["expected_current_label_version"] = 1
        payload["rationale"] = "修订后的独立判断理由。"
        updated = client.post(label_url, json=payload)
        assert updated.status_code == 201
        assert updated.json()["data"]["label"]["label_version"] == 2

        current = client.get(label_url)
        assert current.status_code == 200
        active_label_uuid = current.json()["data"]["label"]["label_uuid"]
        assert (
            current.json()["data"]["label"]["dataset_split"]
            == "development"
        )
        split_change = client.post(
            label_url,
            json={
                **payload,
                "expected_current_label_version": 2,
                "dataset_split": "holdout",
            },
        )
        assert split_change.status_code == 409
        assert (
            split_change.json()["detail"]
            == "CALIBRATION_DATASET_SPLIT_FROZEN"
        )

        db = SessionLocal()
        try:
            source_assessment = (
                db.query(BidIntakeAssessment)
                .filter(BidIntakeAssessment.id == assessment_id)
                .one()
            )
            second_assessment = BidIntakeAssessment(
                assessment_uuid=str(uuid.uuid4()),
                project_id=source_assessment.project_id,
                manifest_id=source_assessment.manifest_id,
                manifest_version=source_assessment.manifest_version,
                manifest_hash=source_assessment.manifest_hash,
                policy_version=source_assessment.policy_version,
                analysis_goal="验证项目级分层冻结。",
                status="waiting_human",
                report_version=1,
                recommendation="need_supplement",
                gate_status="supplement_required",
                assessment_json=source_assessment.assessment_json,
                policy_evaluation_json=(
                    source_assessment.policy_evaluation_json
                ),
                created_by=user.id,
            )
            db.add(second_assessment)
            db.commit()
            second_assessment_uuid = second_assessment.assessment_uuid
        finally:
            db.close()
        project_split = client.post(
            (
                f"/api/v1/admin/bidding/projects/{project.project_uuid}"
                f"/bid-intake/assessments/{second_assessment_uuid}"
                "/calibration-label"
            ),
            json={
                **payload,
                "expected_current_label_version": 0,
                "dataset_split": "holdout",
            },
        )
        assert project_split.status_code == 409
        assert (
            project_split.json()["detail"]
            == "CALIBRATION_PROJECT_SPLIT_FROZEN"
        )

        review_url = (
            "/api/v1/admin/bidding/bid-intake/calibration/labels/"
            f"{active_label_uuid}/review"
        )
        self_review = client.post(
            review_url,
            json={
                "action": "approved",
                "note": "创建人不能复核自己的金标。",
            },
        )
        assert self_review.status_code == 409
        assert (
            self_review.json()["detail"]
            == "CALIBRATION_REVIEWER_MUST_DIFFER"
        )

        db = SessionLocal()
        try:
            reviewer = User(
                username=f"phase4f-reviewer-{uuid.uuid4().hex[:8]}",
                hashed_password="test",
                role="manager",
                quota=100,
                is_active=True,
            )
            db.add(reviewer)
            db.commit()
            db.refresh(reviewer)
            list(reviewer.role_assignments)
            db.expunge(reviewer)
        finally:
            db.close()
        app.dependency_overrides[get_current_user] = lambda: reviewer
        approved = client.post(
            review_url,
            json={
                "action": "approved",
                "note": "第二位总经办人员独立复核通过。",
            },
        )
        assert approved.status_code == 201
        review_uuid = approved.json()["data"]["review"]["review_uuid"]
        repeated_review = client.post(
            review_url,
            json={
                "action": "approved",
                "note": "第二位总经办人员独立复核通过。",
            },
        )
        assert repeated_review.status_code == 201
        assert (
            repeated_review.json()["data"]["review"]["review_uuid"]
            == review_uuid
        )
        sample_pool = client.get(
            "/api/v1/admin/bidding/bid-intake/calibration/samples",
            params={"review_status": "approved"},
        )
        assert sample_pool.status_code == 200
        sample = next(
            item
            for item in sample_pool.json()["data"]
            if item["label_uuid"] == active_label_uuid
        )
        assert sample["review_status"] == "approved"
        assert sample["can_review"] is False
        app.dependency_overrides[get_current_user] = lambda: user

        db = SessionLocal()
        try:
            staff = User(
                username=f"phase4d-staff-{uuid.uuid4().hex[:8]}",
                hashed_password="test",
                role="staff",
                quota=100,
                is_active=True,
            )
            db.add(staff)
            db.flush()
            project_row = (
                db.query(BidProject)
                .filter(BidProject.id == project.id)
                .one()
            )
            project_row.owner_user_id = staff.id
            db.commit()
            db.refresh(staff)
            list(staff.role_assignments)
            db.expunge(staff)
        finally:
            db.close()
        app.dependency_overrides[get_current_user] = lambda: staff
        masked = client.get(label_url)
        assert masked.status_code == 200
        masked_data = masked.json()["data"]
        assert masked_data["can_manage"] is False
        assert masked_data["label"]["masked"] is True
        assert "expected_decision" not in masked_data["label"]
        forbidden = client.post(
            label_url,
            json={
                **payload,
                "expected_current_label_version": 2,
            },
        )
        assert forbidden.status_code == 403
        app.dependency_overrides[get_current_user] = lambda: user

        report = client.get(
            "/api/v1/admin/bidding/bid-intake/calibration/report"
        )
        assert report.status_code == 200
        report_data = report.json()["data"]
        assert report_data["dataset_case_count"] == 1
        assert report_data["activation_allowed"] is False
        assert "case_results" not in report_data["candidate"]

        db = SessionLocal()
        try:
            labels = (
                db.query(BidIntakePolicyCalibrationLabel)
                .filter(
                    BidIntakePolicyCalibrationLabel.assessment_id
                    == assessment_id
                )
                .order_by(
                    BidIntakePolicyCalibrationLabel.label_version.asc()
                )
                .all()
            )
            assert [item.active for item in labels] == [False, True]
            assert labels[1].supersedes_label_id == labels[0].id
            first_case = json.loads(labels[0].case_snapshot_json)
            second_case = json.loads(labels[1].case_snapshot_json)
            assert first_case["manifest"] == second_case["manifest"]
            assert first_case["assessment"] == second_case["assessment"]
            assert (
                first_case["gold_label"]["rationale"]
                != second_case["gold_label"]["rationale"]
            )
        finally:
            db.close()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(
            settings,
            "feature_bidding_mvp",
            previous_feature,
        )


def test_policy_candidate_generation_fails_closed_with_small_dataset(
    client,
) -> None:
    user, _ = _seed()
    previous_feature = settings.feature_bidding_mvp
    object.__setattr__(settings, "feature_bidding_mvp", True)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        report = client.get(
            "/api/v1/admin/bidding/bid-intake/calibration/report"
        )
        assert report.status_code == 200
        assert report.json()["data"]["can_manage"] is True

        quality = client.get(
            "/api/v1/admin/bidding/bid-intake/calibration/quality"
        )
        assert quality.status_code == 200
        assert quality.json()["data"]["ready_to_freeze"] is False

        freeze = client.post(
            "/api/v1/admin/bidding/bid-intake/calibration/datasets",
            json={},
        )
        assert freeze.status_code == 409
        detail = freeze.json()["detail"]
        assert (
            detail["code"]
            == "CALIBRATION_DATASET_NOT_READY"
        )
        assert "MIN_TOTAL_CASES" in detail["failed_checks"]

        listing = client.get(
            "/api/v1/admin/bidding/bid-intake/calibration/candidates"
        )
        assert listing.status_code == 200
        assert listing.json()["data"] == []

        missing_dataset = client.post(
            "/api/v1/admin/bidding/bid-intake/calibration/candidates",
            json={"dataset_uuid": str(uuid.uuid4())},
        )
        assert missing_dataset.status_code == 404
        assert (
            missing_dataset.json()["detail"]
            == "CALIBRATION_DATASET_NOT_FOUND"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        object.__setattr__(
            settings,
            "feature_bidding_mvp",
            previous_feature,
        )
