from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.v1 import bid_assessment_runtime_lab as runtime_lab_api
from app.core.database import Base
from app.models.bid_assessment import BidAssessment, BidAssessmentScope, BidDocumentManifest
from app.models.bid_assessment_config import BidEnterpriseSnapshot
from app.models.bid_assessment_documents import BidEvidenceFragment
from app.models.bid_assessment_eventing import BidAuditLog
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidencePackage,
    BidMvpReleaseCandidate,
)
from app.models.bid_assessment_results import (
    BidClaimCitation,
    BidHardGateResult,
    BidPreliminaryDecision,
    BidPreliminaryReport,
    BidReportClaim,
    BidReportValidation,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.models.bid_run_validation import BidRunValidation
from app.models.user import User
from app.schemas.bid_assessment import BidMvpReleaseCandidateCreateIn
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_mvp_release_candidate import (
    MVP_RC_GATE_CODES,
    MVP_RC_QUALITY_CODES,
    BidMvpReleaseCandidateError,
    freeze_mvp_release_candidate,
    preview_mvp_release_candidate,
)
from app.services import bid_mvp_release_candidate as release_service


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def _runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        feature_bid_assessment_v1_runtime=True,
        feature_bid_assessment_phase4_mvp=True,
        feature_bid_assessment_phase4_plan_continuation=True,
        feature_bid_assessment_phase4_local_agent=True,
        feature_bid_assessment_phase4_evidence_mcp=True,
        feature_bid_assessment_phase4_model_executor=True,
        feature_bid_assessment_phase4_fact_authority=True,
        feature_bid_assessment_phase4_preliminary_report=True,
        feature_bid_assessment_phase4_mvp0_trace=True,
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
    )


def _runtime_request(*, access_mode: str) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            bid_mvp1_access_mode=access_mode,
            bid_mvp1_worker_enabled=access_mode == "execute",
            bid_mvp1_worker_running=access_mode == "execute",
            bid_mvp1_model_calls_enabled=access_mode == "execute",
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/bid-assessment-runtime-lab/release-candidates",
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4c3-test"},
        }
    )


def _command(*, nonpass_note: str | None = None) -> dict[str, object]:
    return {
        "run_id": "run-phase4c3",
        "review_note": "业务负责人已复核七项硬门、报告正文、限制项与原文引用。",
        "gate_reviews": [
            {
                "gate_code": code,
                "disposition": "confirmed",
                "note": nonpass_note if code == "HG05" else None,
            }
            for code in MVP_RC_GATE_CODES
        ],
        "quality_reviews": [
            {"code": code, "disposition": "confirmed", "note": None}
            for code in MVP_RC_QUALITY_CODES
        ],
    }


@pytest.fixture()
def release_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase4c3.db').as_posix()}")
    tables = [
        User.__table__,
        BidAssessment.__table__,
        BidDocumentManifest.__table__,
        BidAssessmentScope.__table__,
        BidEnterpriseSnapshot.__table__,
        BidAnalysisRun.__table__,
        BidHardGateResult.__table__,
        BidPreliminaryDecision.__table__,
        BidReportClaim.__table__,
        BidEvidenceFragment.__table__,
        BidClaimCitation.__table__,
        BidReportValidation.__table__,
        BidPreliminaryReport.__table__,
        BidRunValidation.__table__,
        BidMvpReleaseCandidate.__table__,
        BidEnterpriseEvidencePackage.__table__,
        BidEnterpriseBusinessBaseline.__table__,
        BidAuditLog.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    now = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    db.add(User(id=1, username="phase4c3-reviewer", hashed_password="unused", role="admin"))
    db.add(
        BidAssessment(
            id="assessment-phase4c3",
            title="Phase 4C-3 合成验收项目",
            client_name="本地验收客户",
            lifecycle_status="active",
            business_status="preliminary_ready",
            current_manifest_id="manifest-phase4c3",
            active_run_id="run-phase4c3",
            created_by=1,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        BidDocumentManifest(
            id="manifest-phase4c3",
            assessment_id="assessment-phase4c3",
            version=1,
            manifest_hash="1" * 64,
            committed_by=1,
            created_at=now,
        )
    )
    db.add(
        BidAssessmentScope(
            id="scope-phase4c3",
            assessment_id="assessment-phase4c3",
            version=1,
            scope_type="lot",
            selected_lot_snapshot_json={"lot_name": "一标段"},
            scope_hash="2" * 64,
            created_by=1,
            created_at=now,
        )
    )
    db.add(
        BidEnterpriseSnapshot(
            id="enterprise-phase4c3",
            version="enterprise-phase4c3-v1",
            as_of=now,
            snapshot_hash="3" * 64,
            source_catalog_version="phase4c3-test-v1",
            status="frozen",
            created_by=1,
            frozen_by=1,
            frozen_at=now,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        BidAnalysisRun(
            id="run-phase4c3",
            assessment_id="assessment-phase4c3",
            scope_id="scope-phase4c3",
            manifest_id="manifest-phase4c3",
            enterprise_snapshot_id="enterprise-phase4c3",
            rule_set_id="rules-phase4c3",
            fact_catalog_version_id="facts-phase4c3",
            prompt_bundle_id="prompts-phase4c3",
            tool_registry_version_id="tools-phase4c3",
            model_profile_version_id="models-phase4c3",
            formula_catalog_version_id="formulas-phase4c3",
            run_sequence=2,
            run_kind="preliminary",
            status="succeeded",
            retryable=False,
            input_fingerprint="4" * 64,
            input_hash="5" * 64,
            evaluation_time=now,
            current_stage="P4",
            started_at=now,
            finished_at=now,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
    )

    gate_rows: list[BidHardGateResult] = []
    report_gates: list[dict[str, object]] = []
    for index, code in enumerate(MVP_RC_GATE_CODES, start=1):
        details = {
            "authority_version": "bid-hard-gate-authority-v2-phase4c1",
            "gate_code": code,
            "status": "pass",
            "severity": "block",
            "input_fact_slots": [f"test.slot.{index}"],
            "reason_codes": ["TEST_GATE_CONFIRMED"],
            "comparison": {"comparison_mode": "phase4c3_test"},
            "acceptance": {
                "label": f"{code} 验收",
                "explanation": "合成测试中的确定性硬门结果。",
                "next_actions": [],
            },
        }
        gate_rows.append(
            BidHardGateResult(
                id=f"gate-{code}",
                run_id="run-phase4c3",
                task_id=f"task-{code}",
                gate_code=code,
                status="pass",
                severity="block",
                reason_codes_json=["TEST_GATE_CONFIRMED"],
                input_fact_ids_json=[f"fact-{index}"],
                details_json=details,
                result_hash=canonical_hash(details),
                created_at=now,
            )
        )
        report_gates.append(
            {
                "gate_code": code,
                "status": "pass",
                "severity": "block",
                "reason_codes": ["TEST_GATE_CONFIRMED"],
                "input_fact_slots": [f"test.slot.{index}"],
                "comparison": {"comparison_mode": "phase4c3_test"},
                "acceptance": details["acceptance"],
            }
        )
    db.add_all(gate_rows)

    decision_input_hash = canonical_hash(
        {
            "gate_hashes": [str(row.result_hash) for row in gate_rows],
            "unknown_fact_count": 0,
            "rule_set_id": "rules-phase4c3",
            "formula_catalog_version_id": "formulas-phase4c3",
        }
    )
    decision_payload = {
        "authority_version": "bid-preliminary-decision-mvp1-v1",
        "run_id": "run-phase4c3",
        "decision": "conditional",
        "investment_level": "medium",
        "failed_gate_count": 0,
        "unknown_gate_count": 0,
        "unknown_fact_count": 0,
        "summary": "七项硬门已完成确定性检查。",
        "reason_codes": ["ALL_HARD_GATES_PASSED"],
        "input_hash": decision_input_hash,
    }
    db.add(
        BidPreliminaryDecision(
            id="decision-phase4c3",
            run_id="run-phase4c3",
            task_id="task-decision",
            rule_set_id="rules-phase4c3",
            formula_catalog_version_id="formulas-phase4c3",
            decision="conditional",
            investment_level="medium",
            failed_gate_count=0,
            unknown_gate_count=0,
            unknown_fact_count=0,
            summary="七项硬门已完成确定性检查。",
            reason_codes_json=["ALL_HARD_GATES_PASSED"],
            input_hash=decision_input_hash,
            decision_hash=canonical_hash(decision_payload),
            created_at=now,
        )
    )
    db.add(
        BidReportClaim(
            id="claim-phase4c3",
            run_id="run-phase4c3",
            task_id="task-claim",
            claim_order=1,
            claim_type="fact",
            text="投标截止时间以招标原文为准。",
            status="valid",
            support_fact_ids_json=["fact-1"],
            support_gate_ids_json=[],
            reason_codes_json=["CLAIM_SUPPORT_VALIDATED"],
            claim_hash="8" * 64,
            created_at=now,
        )
    )
    db.add(
        BidEvidenceFragment(
            id="atom-phase4c3",
            parse_run_id="parse-phase4c3",
            document_version_id="document-version-phase4c3",
            parse_unit_id="unit-phase4c3",
            locator_type="section",
            locator_json={"fragment_role": "evidence_atom", "is_citable": True},
            locator_hash="9" * 64,
            normalized_text="投标截止时间：2026年8月20日。",
            text_hash="a" * 64,
            ordinal=0,
            created_at=now,
        )
    )
    db.add(
        BidClaimCitation(
            id="citation-phase4c3",
            claim_id="claim-phase4c3",
            evidence_fragment_id="atom-phase4c3",
            document_version_id="document-version-phase4c3",
            locator_json={"page": 1},
            excerpt="投标截止时间：2026年8月20日。",
            excerpt_hash="b" * 64,
            citation_hash="c" * 64,
            created_at=now,
        )
    )
    report_validation_payload = {
        "validator_version": "bid-claim-validator-v1",
        "run_id": "run-phase4c3",
        "checks": [{"claim_id": "claim-phase4c3", "valid": True}],
        "decision_present": True,
        "gate_count": 7,
        "passed": True,
    }
    decision_hash = canonical_hash(decision_payload)
    report_validation_input_hash = canonical_hash(
        {
            "claim_hashes": ["8" * 64],
            "decision_hash": decision_hash,
            "gate_hashes": sorted(str(row.result_hash) for row in gate_rows),
        }
    )
    db.add(
        BidReportValidation(
            id="report-validation-phase4c3",
            run_id="run-phase4c3",
            task_id="task-report-validation",
            status="passed",
            validator_version="bid-claim-validator-v1",
            checks_json=report_validation_payload,
            input_hash=report_validation_input_hash,
            result_hash=canonical_hash(report_validation_payload),
            created_at=now,
        )
    )
    report_payload = {
        "schema": "bid.preliminary.report.mvp1.v1",
        "run_id": "run-phase4c3",
        "title": "Phase 4C-3 合成验收项目",
        "decision": {"code": "conditional"},
        "hard_gates": report_gates,
        "claims": [{"claim_id": "claim-phase4c3"}],
        "limitations": ["本报告仅用于本地隔离业务验收。"],
    }
    db.add(
        BidPreliminaryReport(
            id="report-phase4c3",
            assessment_id="assessment-phase4c3",
            run_id="run-phase4c3",
            decision_id="decision-phase4c3",
            validation_id="report-validation-phase4c3",
            report_version=1,
            status="ready",
            title="Phase 4C-3 初筛报告",
            executive_summary="七项硬门已完成确定性检查。",
            report_json=report_payload,
            report_hash=canonical_hash(report_payload),
            generated_at=now,
            created_at=now,
        )
    )
    run_validation_payload = {"run_id": "run-phase4c3", "outcome": "passed"}
    run_validation_hash = canonical_hash(run_validation_payload)
    run_validation_payload = {
        **run_validation_payload,
        "result_hash": run_validation_hash,
    }
    db.add(
        BidRunValidation(
            id="run-validation-phase4c3",
            assessment_id="assessment-phase4c3",
            run_id="run-phase4c3",
            source_event_id="phase4c3-validation-request",
            validation_key="phase4c3-validation-key",
            validator_version="bid-run-validator-v5",
            input_hash="e" * 64,
            status="passed",
            outcome="passed",
            retryable=False,
            attempt_count=1,
            fencing_token=1,
            result_json=run_validation_payload,
            result_hash=run_validation_hash,
            requested_at=now,
            started_at=now,
            finished_at=now,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_phase4c3_schema_command_and_ui_contract() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "bid_assessment" / "v1" / "mvp-release-candidate.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    command = BidMvpReleaseCandidateCreateIn.model_validate(_command())
    assert [item.gate_code for item in command.gate_reviews] == list(MVP_RC_GATE_CODES)
    assert {item.code for item in command.quality_reviews} == set(MVP_RC_QUALITY_CODES)

    frontend = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    api = (WEB_ROOT / "bidAssessmentRuntimeLabApi.js").read_text(encoding="utf-8")
    assert "validateReleaseAcceptance" in frontend
    assert "freezeReleaseCandidate" in frontend
    assert "X-MVP-RC-Candidate-Hash" in api
    assert "release-candidates/validate" in api
    launcher = (
        ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1"
    ).read_text(encoding="utf-8")
    assert "[switch]$EnableMvpReleaseCandidate" in launcher
    assert "FEATURE_BID_ASSESSMENT_PHASE4_MVP_RELEASE_CANDIDATE" in launcher


def test_release_candidate_post_is_hidden_from_non_admin_and_blocked_in_view_only(
    monkeypatch,
) -> None:
    payload = BidMvpReleaseCandidateCreateIn.model_validate(_command())
    monkeypatch.setattr(runtime_lab_api, "settings", _runtime_settings())
    user = SimpleNamespace(id=1)

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    denied = runtime_lab_api.create_runtime_lab_release_candidate(
        payload,
        _runtime_request(access_mode="execute"),
        idempotency_key="phase4c3-acl-denied-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=None,
    )
    assert denied.status_code == 404

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.create_runtime_lab_release_candidate(
        payload,
        _runtime_request(access_mode="view-only"),
        idempotency_key="phase4c3-view-only-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=None,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"


def test_preview_is_zero_persistence_stable_and_freeze_is_immutable(release_runtime) -> None:
    db = release_runtime
    before = (
        db.query(BidMvpReleaseCandidate).count(),
        db.query(BidAuditLog).count(),
    )
    first = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    second = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    assert first["candidate_hash"] == second["candidate_hash"]
    assert first["can_freeze"] is True
    assert first["acceptance_outcome"] == "accepted"
    assert before == (
        db.query(BidMvpReleaseCandidate).count(),
        db.query(BidAuditLog).count(),
    )

    frozen = freeze_mvp_release_candidate(
        db,
        actor_id=1,
        command=_command(),
        request_id="phase4c3-freeze",
        expected_candidate_hash=first["candidate_hash"],
        now=datetime(2026, 8, 17, 11, tzinfo=timezone.utc),
    )
    db.commit()
    assert frozen.created is True
    assert frozen.projection["release_hash"] == canonical_hash(
        frozen.projection["manifest"]
    )
    assert db.query(BidMvpReleaseCandidate).count() == 1
    assert db.query(BidAuditLog).count() == 1

    replay = freeze_mvp_release_candidate(
        db,
        actor_id=1,
        command=_command(),
        request_id="phase4c3-replay",
        expected_candidate_hash=first["candidate_hash"],
    )
    assert replay.created is False
    assert replay.release.id == frozen.release.id


def test_phase4d1_revalidation_binds_prior_rc_business_baseline_and_gate_delta(
    release_runtime,
    monkeypatch,
) -> None:
    db = release_runtime
    now = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    settings = _runtime_settings()
    settings.feature_bid_assessment_phase4_business_baseline = True
    monkeypatch.setattr(release_service, "settings", settings)
    db.add(
        BidEnterpriseEvidencePackage(
            id="evidence-package-phase4d2",
            version="enterprise-evidence-phase4d2-v1",
            status="frozen",
            package_label="Phase 4D-2 企业资料包",
            change_note="真实企业资料已由负责人映射",
            as_of=now - timedelta(minutes=2),
            manifest_json={"schema": "bid.enterprise.evidence-package.v1"},
            candidate_hash="c" * 64,
            package_hash="d" * 64,
            frozen_by=1,
            frozen_at=now - timedelta(minutes=2),
            created_at=now - timedelta(minutes=2),
        )
    )
    db.add(
        BidEnterpriseBusinessBaseline(
            id="business-baseline-phase4d1",
            version="enterprise-business-phase4d1-v1",
            snapshot_id="enterprise-phase4c3",
            evidence_package_id="evidence-package-phase4d2",
            evidence_package_hash="d" * 64,
            status="frozen",
            verification_outcome="verified",
            reviewer_id=1,
            review_note="I01—I11 已完成真实来源复核",
            slot_reviews_json=[],
            source_hashes_json={"snapshot_hash": "3" * 64},
            candidate_hash="0" * 64,
            baseline_hash="1" * 64,
            reviewed_at=now - timedelta(minutes=1),
            created_at=now - timedelta(minutes=1),
        )
    )
    db.add(
        BidAnalysisRun(
            id="run-phase4d1-source",
            assessment_id="assessment-phase4c3",
            scope_id="scope-phase4c3",
            manifest_id="manifest-phase4c3",
            enterprise_snapshot_id="enterprise-phase4c3",
            rule_set_id="rules-phase4c3",
            fact_catalog_version_id="facts-phase4c3",
            prompt_bundle_id="prompts-phase4c3",
            tool_registry_version_id="tools-phase4c3",
            model_profile_version_id="models-phase4c3",
            formula_catalog_version_id="formulas-phase4c3",
            run_sequence=1,
            run_kind="preliminary",
            status="succeeded",
            retryable=False,
            input_fingerprint="6" * 64,
            input_hash="7" * 64,
            evaluation_time=now - timedelta(hours=1),
            current_stage="P4",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1),
            row_version=1,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
    )
    source_gate_rows = []
    for code in MVP_RC_GATE_CODES:
        status = "fail" if code == "HG05" else "pass"
        details = {"gate_code": code, "status": status, "source": "phase4d1"}
        source_gate_rows.append(
            BidHardGateResult(
                id=f"source-gate-{code}",
                run_id="run-phase4d1-source",
                task_id=f"source-task-{code}",
                gate_code=code,
                status=status,
                severity="block",
                reason_codes_json=["SOURCE_RC_GATE"],
                input_fact_ids_json=[],
                details_json=details,
                result_hash=canonical_hash(details),
                created_at=now - timedelta(hours=1),
            )
        )
    db.add_all(source_gate_rows)
    source_decision_payload = {
        "run_id": "run-phase4d1-source",
        "decision": "no_bid",
        "input_hash": "8" * 64,
    }
    db.add(
        BidPreliminaryDecision(
            id="decision-phase4d1-source",
            run_id="run-phase4d1-source",
            task_id="source-task-decision",
            rule_set_id="rules-phase4c3",
            formula_catalog_version_id="formulas-phase4c3",
            decision="no_bid",
            investment_level="hold",
            failed_gate_count=1,
            unknown_gate_count=0,
            unknown_fact_count=0,
            summary="历史 RC 建议不参与",
            reason_codes_json=["HG05_FAILED"],
            input_hash="8" * 64,
            decision_hash=canonical_hash(source_decision_payload),
            created_at=now - timedelta(hours=1),
        )
    )
    db.add(
        BidMvpReleaseCandidate(
            id="release-phase4d1-source",
            version="mvp-rc-phase4d1-source",
            assessment_id="assessment-phase4c3",
            run_id="run-phase4d1-source",
            report_id="report-phase4c3",
            run_validation_id="run-validation-phase4c3",
            enterprise_snapshot_id="enterprise-phase4c3",
            status="frozen",
            acceptance_outcome="accepted_with_follow_up",
            reviewer_id=1,
            review_note="历史业务验收",
            review_json={},
            source_hashes_json={},
            manifest_json={},
            candidate_hash="9" * 64,
            release_hash="a" * 64,
            reviewed_at=now - timedelta(minutes=30),
            created_at=now - timedelta(minutes=30),
        )
    )
    db.flush()

    preview = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    assert preview["can_freeze"] is True
    assert preview["revalidation"]["source_release_candidate_id"] == (
        "release-phase4d1-source"
    )
    assert preview["revalidation"]["source_decision"] == "no_bid"
    assert preview["revalidation"]["target_decision"] == "conditional"
    assert preview["revalidation"]["decision_changed"] is True
    delta_map = {
        item["gate_code"]: item for item in preview["revalidation"]["gate_deltas"]
    }
    assert delta_map["HG05"] == {
        "gate_code": "HG05",
        "source_status": "fail",
        "target_status": "pass",
        "changed": True,
    }
    assert preview["source_hashes"]["enterprise_business_baseline_hash"] == "1" * 64
    assert preview["source_hashes"]["enterprise_evidence_package_hash"] == "d" * 64
    assert preview["revalidation"]["evidence_package_id"] == (
        "evidence-package-phase4d2"
    )
    assert preview["revalidation"]["evidence_package_hash"] == "d" * 64
    assert preview["source_hashes"]["source_release_hash"] == "a" * 64


def test_source_drift_and_non_atom_citation_fail_closed(release_runtime) -> None:
    db = release_runtime
    accepted = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    report = db.query(BidPreliminaryReport).one()
    report.report_json = {**dict(report.report_json), "title": "被篡改的报告"}
    db.flush()
    drifted = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    assert "REPORT_READY_AND_HASHED" in drifted["blocking_codes"]
    assert drifted["candidate_hash"] == accepted["candidate_hash"]
    with pytest.raises(
        BidMvpReleaseCandidateError,
        match="BID_MVP_RC_NOT_READY",
    ):
        freeze_mvp_release_candidate(
            db,
            actor_id=1,
            command=_command(),
            request_id="phase4c3-drift",
            expected_candidate_hash=accepted["candidate_hash"],
        )
    db.rollback()

    atom = db.query(BidEvidenceFragment).one()
    atom.locator_json = {"fragment_role": "retrieval_child", "is_citable": False}
    db.flush()
    invalid = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    assert "CITATIONS_ATOM_ONLY" in invalid["blocking_codes"]


def test_nonpass_gate_requires_explanation_but_can_be_business_accepted(
    release_runtime,
) -> None:
    db = release_runtime
    gate = db.query(BidHardGateResult).filter_by(gate_code="HG05").one()
    gate.status = "unknown"
    gate.details_json = {**dict(gate.details_json), "status": "unknown"}
    gate.result_hash = canonical_hash(gate.details_json)
    gates = db.query(BidHardGateResult).order_by(BidHardGateResult.gate_code.asc()).all()
    decision = db.query(BidPreliminaryDecision).one()
    decision.decision = "insufficient"
    decision.investment_level = "hold"
    decision.unknown_gate_count = 1
    decision.summary = "保证金能力输入不足，暂不作投标承诺。"
    decision.reason_codes_json = ["HARD_GATE_INPUT_INSUFFICIENT"]
    decision.input_hash = canonical_hash(
        {
            "gate_hashes": [str(row.result_hash) for row in gates],
            "unknown_fact_count": 0,
            "rule_set_id": "rules-phase4c3",
            "formula_catalog_version_id": "formulas-phase4c3",
        }
    )
    decision.decision_hash = canonical_hash(
        {
            "authority_version": "bid-preliminary-decision-mvp1-v1",
            "run_id": "run-phase4c3",
            "decision": "insufficient",
            "investment_level": "hold",
            "failed_gate_count": 0,
            "unknown_gate_count": 1,
            "unknown_fact_count": 0,
            "summary": decision.summary,
            "reason_codes": ["HARD_GATE_INPUT_INSUFFICIENT"],
            "input_hash": decision.input_hash,
        }
    )
    report_validation = db.query(BidReportValidation).one()
    report_validation.input_hash = canonical_hash(
        {
            "claim_hashes": ["8" * 64],
            "decision_hash": str(decision.decision_hash),
            "gate_hashes": sorted(str(row.result_hash) for row in gates),
        }
    )
    report = db.query(BidPreliminaryReport).one()
    report_payload = dict(report.report_json)
    report_payload["decision"] = {"code": "insufficient"}
    report_payload["hard_gates"] = [
        {**item, "status": "unknown"} if item["gate_code"] == "HG05" else item
        for item in report_payload["hard_gates"]
    ]
    report.report_json = report_payload
    report.report_hash = canonical_hash(report_payload)
    db.flush()

    missing_note = preview_mvp_release_candidate(db, actor_id=1, command=_command())
    assert "HG05_REVIEW_NOT_ACCEPTED" in missing_note["review_blocking_codes"]
    assert missing_note["acceptance_outcome"] == "accepted_with_follow_up"

    accepted = preview_mvp_release_candidate(
        db,
        actor_id=1,
        command=_command(nonpass_note="保证金能力证据待补齐，负责人确认仅作为后续跟进项。"),
    )
    assert accepted["can_freeze"] is True
    assert accepted["acceptance_outcome"] == "accepted_with_follow_up"
