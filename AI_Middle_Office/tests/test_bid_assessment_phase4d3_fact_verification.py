from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.v1 import bid_assessment_runtime_lab as runtime_lab_api
from app.core.database import Base
from app.models.bid_assessment import (
    BidAssessment,
    BidAssessmentScope,
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.bid_assessment_config import (
    BidEnterpriseSnapshot,
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.bid_assessment_documents import (
    BidDocumentParseHead,
    BidDocumentParseRun,
    BidDocumentParseUnit,
    BidEvidenceFragment,
)
from app.models.bid_assessment_eventing import (
    BidAuditLog,
    BidIdempotencyRecord,
    BidOutboxEvent,
)
from app.models.bid_assessment_release import (
    BidEnterpriseBusinessBaseline,
    BidEnterpriseEvidenceItem,
    BidEnterpriseEvidencePackage,
    BidEnterpriseEvidencePackageItem,
    BidFactComparisonLink,
    BidHardGateComparisonBaseline,
    BidHardGateComparisonEvidenceLink,
)
from app.models.bid_assessment_results import (
    BidFactAssertion,
    BidFactCoverage,
    BidFactEvidenceLink,
    BidHardGateResult,
    BidResolvedFact,
    BidResolvedFactHead,
)
from app.models.bid_assessment_runtime import BidAnalysisRun
from app.schemas.bid_assessment import BidHardGateComparisonBaselineCreateIn
from app.services import (
    bid_enterprise_business_baseline as business_baseline_service,
    bid_enterprise_capability as enterprise_service,
    bid_hard_gate_fact_verification as verification_service,
    bid_mvp1_executor as mvp1_executor_service,
    bid_mvp1_authority as authority_service,
    bid_run_bootstrap as bootstrap_service,
    bid_run_validation as run_validation_service,
)
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_hard_gate_fact_verification import (
    BidHardGateFactVerificationError,
    COMPARABLE_FACT_SPECS,
    _normalize_fact_set,
    build_hard_gate_comparison_draft,
    freeze_hard_gate_comparison_baseline,
    get_hard_gate_comparison_baseline,
    preview_hard_gate_comparison_baseline,
    validate_hard_gate_comparison_baseline_at,
)
from app.services.bid_mvp1_authority import AuthorityOutput


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def _unknown_command() -> dict[str, object]:
    facts = [
        {
            "fact_slot": fact_slot,
            "source_side": str(spec["side"]),
            "verification_status": "unknown",
            "value_type": None,
            "canonical_value": None,
            "evidence_item_ids": [],
            "evidence_atom_ids": [],
            "note": "当前权威资料不足，保持 unknown 并进入跟进项。",
        }
        for fact_slot, spec in reversed(sorted(COMPARABLE_FACT_SPECS.items()))
    ]
    return {
        "assessment_id": "assessment-phase4d3",
        "source_run_id": "run-phase4d3-source",
        "business_baseline_id": "business-baseline-phase4d3",
        "reviewed_as_of": datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        "review_note": "逐项复核 16 个硬门输入事实及权威证据。",
        "facts": facts,
    }


@pytest.fixture()
def phase4d3_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase4d3.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[
            BidAssessment.__table__,
            BidDocumentManifest.__table__,
            BidAssessmentScope.__table__,
            BidManifestDocument.__table__,
            BidEnterpriseSnapshot.__table__,
            BidEnterpriseEvidenceItem.__table__,
            BidEnterpriseEvidencePackage.__table__,
            BidEnterpriseEvidencePackageItem.__table__,
            BidEnterpriseBusinessBaseline.__table__,
            BidAnalysisRun.__table__,
            BidDocumentParseRun.__table__,
            BidDocumentParseHead.__table__,
            BidDocumentParseUnit.__table__,
            BidEvidenceFragment.__table__,
            BidFactAssertion.__table__,
            BidFactEvidenceLink.__table__,
            BidFactCoverage.__table__,
            BidResolvedFact.__table__,
            BidResolvedFactHead.__table__,
            BidHardGateResult.__table__,
            BidHardGateComparisonBaseline.__table__,
            BidHardGateComparisonEvidenceLink.__table__,
            BidFactComparisonLink.__table__,
            BidAuditLog.__table__,
            BidIdempotencyRecord.__table__,
        ],
    )
    # Bootstrap and validation integration exercises shared runtime tables
    # beyond the focused persistence set above.
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    locator = {
        "page_no": 1,
        "fragment_role": "evidence_atom",
        "is_citable": True,
    }
    tender_value = {
        "project_name": "Phase 4D-3 合成项目",
        "procurer_name": "合成招标人",
    }
    enterprise_value = {"legal_name": "旗胜合成验证有限公司"}
    item_hash = canonical_hash({"item": "I01", "revision": "v1"})
    atom_text_hash = canonical_hash({"text": "合成项目招标人为合成招标人。"})
    source_tender_assertion = BidFactAssertion(
        id="assertion-source-tender-overview",
        assessment_id="assessment-phase4d3",
        run_id="run-phase4d3-source",
        task_id="task-source-tender",
        source_task_attempt_id="attempt-source-tender",
        fact_catalog_version_id="facts-phase4d3",
        fact_slot="tender.overview",
        scope_type="assessment",
        scope_id="assessment-phase4d3",
        value_type="project_identity",
        value_json=tender_value,
        value_hash=canonical_hash(tender_value),
        source_type="document",
        confidence="high",
        status="accepted",
        asserted_at=now - timedelta(minutes=2),
        assertion_hash=canonical_hash({"source": "tender-overview"}),
        reason_codes_json=["SYNTHETIC_GOVERNED_SOURCE"],
        created_at=now - timedelta(minutes=2),
    )
    source_enterprise_assertion = BidFactAssertion(
        id="assertion-source-enterprise-identity",
        assessment_id="assessment-phase4d3",
        run_id="run-phase4d3-source",
        task_id="task-source-enterprise",
        source_task_attempt_id="attempt-source-enterprise",
        fact_catalog_version_id="facts-phase4d3",
        fact_slot="enterprise.identity.legal_name",
        scope_type="assessment",
        scope_id="assessment-phase4d3",
        value_type="enterprise_identity",
        value_json=enterprise_value,
        value_hash=canonical_hash(enterprise_value),
        source_type="enterprise",
        confidence="medium",
        status="accepted",
        asserted_at=now - timedelta(minutes=2),
        assertion_hash=canonical_hash({"source": "enterprise-identity"}),
        reason_codes_json=["SYNTHETIC_GOVERNED_SOURCE"],
        created_at=now - timedelta(minutes=2),
    )
    rows = [
        BidAssessment(
            id="assessment-phase4d3",
            title="Phase 4D-3 合成事实核验",
            client_name="隔离测试",
            lifecycle_status="active",
            business_status="preliminary_ready",
            current_manifest_id="manifest-phase4d3",
            active_run_id="run-phase4d3-target",
            created_by=1,
            row_version=1,
            created_at=now,
            updated_at=now,
        ),
        BidDocumentManifest(
            id="manifest-phase4d3",
            assessment_id="assessment-phase4d3",
            version=1,
            manifest_hash="1" * 64,
            committed_by=1,
            created_at=now,
        ),
        BidAssessmentScope(
            id="scope-phase4d3",
            assessment_id="assessment-phase4d3",
            version=1,
            scope_type="lot",
            selected_lot_snapshot_json={
                "lot_id": "lot-phase4d3",
                "lot_name": "合成一标段",
            },
            scope_hash="2" * 64,
            created_by=1,
            created_at=now,
        ),
        BidManifestDocument(
            manifest_id="manifest-phase4d3",
            document_version_id="document-version-phase4d3",
            role="tender_document",
            order_no=0,
            created_at=now,
        ),
        BidEnterpriseSnapshot(
            id="enterprise-phase4d3",
            version="enterprise-phase4d3-v1",
            as_of=now - timedelta(minutes=5),
            snapshot_hash="3" * 64,
            source_catalog_version="phase4d3-test-v1",
            status="frozen",
            created_by=1,
            frozen_by=1,
            frozen_at=now - timedelta(minutes=5),
            row_version=1,
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        ),
        BidRuleSet(
            id="rules-phase4d3",
            version="rules-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/rules",
            artifact_hash=canonical_hash({"artifact": "rules-phase4d3"}),
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            effective_from=now - timedelta(minutes=5),
            effective_to=None,
            test_cases_ref="memory://phase4d3/rules/tests",
            row_version=1,
        ),
        BidFactCatalogVersion(
            id="facts-phase4d3",
            version="facts-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/facts",
            artifact_hash=canonical_hash({"artifact": "facts-phase4d3"}),
            schema_version="v1",
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            row_version=1,
        ),
        BidPromptBundle(
            id="prompts-phase4d3",
            version="prompts-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/prompts",
            artifact_hash=canonical_hash({"artifact": "prompts-phase4d3"}),
            bundle_schema_version="v1",
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            row_version=1,
        ),
        BidToolRegistryVersion(
            id="tools-phase4d3",
            version="tools-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/tools",
            artifact_hash=canonical_hash({"artifact": "tools-phase4d3"}),
            registry_schema_version="v1",
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            row_version=1,
        ),
        BidModelProfileVersion(
            id="models-phase4d3",
            version="models-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/models",
            artifact_hash=canonical_hash({"artifact": "models-phase4d3"}),
            role_routing_json={},
            provider_identifiers_json={},
            model_identifiers_json={},
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            row_version=1,
        ),
        BidFormulaCatalogVersion(
            id="formulas-phase4d3",
            version="formulas-phase4d3-v1",
            status="active",
            active_slot_key="active",
            artifact_ref="memory://phase4d3/formulas",
            artifact_hash=canonical_hash({"artifact": "formulas-phase4d3"}),
            rounding_policy_json={},
            authored_by=1,
            reviewed_by=1,
            reviewed_at=now - timedelta(minutes=5),
            activated_at=now - timedelta(minutes=5),
            row_version=1,
        ),
        BidEnterpriseEvidenceItem(
            id="enterprise-item-I01",
            status="frozen",
            evidence_class="official_document",
            source_record_id="enterprise-register-I01",
            source_version="v1",
            source_label="企业主体权威资料",
            original_filename="synthetic-business-license.pdf",
            mime_type="application/pdf",
            size_bytes=128,
            content_sha256="4" * 64,
            item_hash=item_hash,
            object_ref="local://phase4d3/item-I01",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            uploaded_by=1,
            uploaded_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=5),
        ),
        BidEnterpriseEvidencePackage(
            id="evidence-package-phase4d3",
            version="enterprise-evidence-phase4d3-v1",
            status="frozen",
            package_label="Phase 4D-3 合成资料包",
            change_note="明确映射 I01，其余槽位保持 unknown",
            as_of=now - timedelta(minutes=4),
            manifest_json={"schema": "bid.enterprise.evidence-package.v1"},
            candidate_hash="5" * 64,
            package_hash="6" * 64,
            frozen_by=1,
            frozen_at=now - timedelta(minutes=4),
            created_at=now - timedelta(minutes=4),
        ),
        BidEnterpriseEvidencePackageItem(
            id="package-item-I01",
            package_id="evidence-package-phase4d3",
            evidence_item_id="enterprise-item-I01",
            slot_code="I01",
            mapping_note="人工确认营业执照对应法定主体",
            created_at=now - timedelta(minutes=4),
        ),
        BidEnterpriseBusinessBaseline(
            id="business-baseline-phase4d3",
            version="enterprise-business-phase4d3-v1",
            snapshot_id="enterprise-phase4d3",
            evidence_package_id="evidence-package-phase4d3",
            evidence_package_hash="6" * 64,
            status="frozen",
            verification_outcome="verified_with_follow_up",
            reviewer_id=1,
            review_note="I01 有权威资料，其余槽位等待补充",
            slot_reviews_json=[
                {
                    "slot_code": "I01",
                    "evidence_item_id": "enterprise-item-I01",
                    "verification_status": "partial",
                }
            ],
            source_hashes_json={"snapshot_hash": "3" * 64},
            candidate_hash="7" * 64,
            baseline_hash="8" * 64,
            reviewed_at=now - timedelta(minutes=3),
            created_at=now - timedelta(minutes=3),
        ),
        BidAnalysisRun(
            id="run-phase4d3-source",
            assessment_id="assessment-phase4d3",
            scope_id="scope-phase4d3",
            manifest_id="manifest-phase4d3",
            enterprise_snapshot_id="enterprise-phase4d3",
            rule_set_id="rules-phase4d3",
            fact_catalog_version_id="facts-phase4d3",
            prompt_bundle_id="prompts-phase4d3",
            tool_registry_version_id="tools-phase4d3",
            model_profile_version_id="models-phase4d3",
            formula_catalog_version_id="formulas-phase4d3",
            run_sequence=1,
            run_kind="preliminary",
            status="succeeded",
            retryable=False,
            input_fingerprint="9" * 64,
            input_hash="a" * 64,
            evaluation_time=now - timedelta(minutes=2),
            current_stage="P4",
            started_at=now - timedelta(minutes=3),
            finished_at=now - timedelta(minutes=2),
            row_version=1,
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=2),
        ),
        BidAnalysisRun(
            id="run-phase4d3-target",
            assessment_id="assessment-phase4d3",
            scope_id="scope-phase4d3",
            manifest_id="manifest-phase4d3",
            enterprise_snapshot_id="enterprise-phase4d3",
            rule_set_id="rules-phase4d3",
            fact_catalog_version_id="facts-phase4d3",
            prompt_bundle_id="prompts-phase4d3",
            tool_registry_version_id="tools-phase4d3",
            model_profile_version_id="models-phase4d3",
            formula_catalog_version_id="formulas-phase4d3",
            run_sequence=2,
            run_kind="reanalysis",
            status="running",
            retryable=False,
            input_fingerprint="b" * 64,
            input_hash="c" * 64,
            evaluation_time=now,
            current_stage="P1",
            started_at=now,
            row_version=1,
            created_at=now,
            updated_at=now,
        ),
        BidDocumentParseRun(
            id="parse-phase4d3",
            document_version_id="document-version-phase4d3",
            parser_profile_version="phase4d3-synthetic-v1",
            input_hash="d" * 64,
            status="succeeded",
            retryable=False,
            requested_at=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            result_ref="local://phase4d3/parse",
            result_hash="e" * 64,
            quality_grade="high",
            quality_score=100,
            page_count=1,
            sheet_count=0,
            ocr_status="not_applicable",
            warning_count=0,
            warnings_json=[],
            row_version=1,
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=4),
        ),
        BidDocumentParseHead(
            document_version_id="document-version-phase4d3",
            current_run_id="parse-phase4d3",
            row_version=1,
            created_at=now - timedelta(minutes=4),
            updated_at=now - timedelta(minutes=4),
        ),
        BidDocumentParseUnit(
            id="unit-phase4d3",
            run_id="parse-phase4d3",
            unit_type="page",
            unit_key="page:1",
            ordinal=0,
            page_no=1,
            section_path_json=["招标公告"],
            content_source="native",
            status="succeeded",
            text_hash=atom_text_hash,
            text_length=14,
            ocr_status="not_applicable",
            created_at=now - timedelta(minutes=4),
        ),
        BidEvidenceFragment(
            id="atom-phase4d3-overview",
            parse_run_id="parse-phase4d3",
            document_version_id="document-version-phase4d3",
            parse_unit_id="unit-phase4d3",
            locator_type="page_bbox",
            locator_json=locator,
            locator_hash=canonical_hash(locator),
            normalized_text="合成项目招标人为合成招标人。",
            text_hash=atom_text_hash,
            ordinal=0,
            created_at=now - timedelta(minutes=4),
        ),
        source_tender_assertion,
        source_enterprise_assertion,
        BidFactEvidenceLink(
            assertion_id=source_tender_assertion.id,
            evidence_fragment_id="atom-phase4d3-overview",
            manifest_id="manifest-phase4d3",
            parse_run_id="parse-phase4d3",
            document_version_id="document-version-phase4d3",
            evidence_text_hash=atom_text_hash,
            locator_hash=canonical_hash(locator),
            context_read=True,
            link_hash=canonical_hash({"source": "tender-overview-link"}),
            created_at=now - timedelta(minutes=2),
        ),
    ]
    db.add_all(rows)
    for fact_slot, assertion, value_type, value in (
        (
            "tender.overview",
            source_tender_assertion,
            "project_identity",
            tender_value,
        ),
        (
            "enterprise.identity.legal_name",
            source_enterprise_assertion,
            "enterprise_identity",
            enterprise_value,
        ),
    ):
        resolved_id = f"resolved-source-{fact_slot.replace('.', '-')}"
        db.add(
            BidResolvedFact(
                id=resolved_id,
                run_id="run-phase4d3-source",
                fact_slot=fact_slot,
                scope_type="assessment",
                scope_id="assessment-phase4d3",
                status="supported",
                value_type=value_type,
                value_json=value,
                source_assertion_ids_json=[assertion.id],
                reason_codes_json=["CONSISTENT_ACCEPTED_ASSERTIONS"],
                resolution_hash=canonical_hash({"resolved": fact_slot}),
                created_at=now - timedelta(minutes=2),
            )
        )
        db.add(
            BidResolvedFactHead(
                run_id="run-phase4d3-source",
                fact_slot=fact_slot,
                scope_type="assessment",
                scope_id="assessment-phase4d3",
                resolved_fact_id=resolved_id,
                row_version=1,
                created_at=now - timedelta(minutes=2),
                updated_at=now - timedelta(minutes=2),
            )
        )
    db.commit()
    try:
        yield db, now
    finally:
        db.close()
        engine.dispose()


def _fact_verification_settings() -> SimpleNamespace:
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
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_enterprise_evidence_import=True,
        feature_bid_assessment_phase4_fact_verification=True,
    )


def _runtime_request(*, access_mode: str, path: str) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            bid_mvp1_access_mode=access_mode,
            bid_mvp1_worker_enabled=access_mode == "execute",
            bid_mvp1_worker_running=access_mode == "execute",
            bid_mvp1_model_calls_enabled=False,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "app": app,
            "state": {"trace_id": "phase4d3-test"},
        }
    )


def _review_command(db, *, reviewed_as_of: datetime) -> dict[str, object]:
    draft = build_hard_gate_comparison_draft(
        db,
        assessment_id="assessment-phase4d3",
        source_run_id="run-phase4d3-source",
        business_baseline_id="business-baseline-phase4d3",
    )
    draft.pop("schema", None)
    draft["reviewed_as_of"] = reviewed_as_of
    draft["review_note"] = "逐项核验 16 项事实；I01 保持 partial，不作为硬门通过。"
    for fact in draft["facts"]:
        if fact["fact_slot"] == "tender.overview":
            fact["verification_status"] = "supported"
        elif fact["fact_slot"] == "enterprise.identity.legal_name":
            fact["verification_status"] = "partial"
    return draft


def test_phase4d3_command_requires_every_comparable_fact_once() -> None:
    command = _unknown_command()
    parsed = BidHardGateComparisonBaselineCreateIn.model_validate(command)
    assert len(parsed.facts) == 16
    assert {fact.fact_slot for fact in parsed.facts} == set(COMPARABLE_FACT_SPECS)

    duplicate = deepcopy(command)
    duplicate["facts"][-1] = deepcopy(duplicate["facts"][0])
    with pytest.raises(ValidationError):
        BidHardGateComparisonBaselineCreateIn.model_validate(duplicate)


def test_phase4d3_unknown_cannot_claim_value_or_evidence() -> None:
    command = _unknown_command()
    command["facts"][0].update(
        {
            "value_type": "string",
            "canonical_value": "未经核验的候选",
            "evidence_atom_ids": ["atom-unverified"],
        }
    )
    with pytest.raises(ValidationError):
        BidHardGateComparisonBaselineCreateIn.model_validate(command)


def test_phase4d3_supported_fact_requires_correct_evidence_side() -> None:
    command = _unknown_command()
    tender = next(
        fact for fact in command["facts"] if fact["fact_slot"] == "tender.overview"
    )
    tender.update(
        {
            "verification_status": "supported",
            "value_type": "project_identity",
            "canonical_value": {"project_name": "测试项目"},
            "evidence_item_ids": ["enterprise-item-not-allowed"],
        }
    )
    with pytest.raises(ValidationError):
        BidHardGateComparisonBaselineCreateIn.model_validate(command)


def test_phase4d3_supported_fact_must_be_machine_comparable() -> None:
    command = _unknown_command()
    tender = next(
        fact for fact in command["facts"] if fact["fact_slot"] == "tender.overview"
    )
    tender.update(
        {
            "verification_status": "supported",
            "value_type": "string",
            "canonical_value": "自由文本不能作为 supported 比较值",
            "evidence_atom_ids": ["atom-current-citable"],
        }
    )
    parsed = BidHardGateComparisonBaselineCreateIn.model_validate(command)
    with pytest.raises(
        BidHardGateFactVerificationError,
        match="BID_HARD_GATE_COMPARISON_VALUE_NOT_COMPARABLE",
    ):
        _normalize_fact_set(parsed.model_dump())


def test_phase4d3_fact_hash_is_stable_and_order_independent() -> None:
    command = BidHardGateComparisonBaselineCreateIn.model_validate(
        _unknown_command()
    ).model_dump()
    first = _normalize_fact_set(command)
    reordered = deepcopy(command)
    reordered["facts"] = list(reversed(reordered["facts"]))
    second = _normalize_fact_set(reordered)
    assert first == second
    assert all(len(str(fact["fact_hash"])) == 64 for fact in first)


def test_phase4d3_draft_does_not_project_an_incompatible_resolved_value_type(
    phase4d3_runtime,
) -> None:
    db, now = phase4d3_runtime
    assertion = BidFactAssertion(
        id="assertion-source-deadline-text",
        assessment_id="assessment-phase4d3",
        run_id="run-phase4d3-source",
        task_id="task-source-deadline",
        source_task_attempt_id="attempt-source-deadline",
        fact_catalog_version_id="facts-phase4d3",
        fact_slot="tender.submission.deadline",
        scope_type="assessment",
        scope_id="assessment-phase4d3",
        value_type="text",
        value_json="2026年 月 日 时",
        value_hash=canonical_hash("2026年 月 日 时"),
        source_type="document",
        confidence="medium",
        status="accepted",
        asserted_at=now - timedelta(minutes=2),
        assertion_hash=canonical_hash({"source": "deadline-text"}),
        reason_codes_json=["SOURCE_TEMPLATE_VALUE"],
        created_at=now - timedelta(minutes=2),
    )
    resolved = BidResolvedFact(
        id="resolved-source-deadline-text",
        run_id="run-phase4d3-source",
        fact_slot="tender.submission.deadline",
        scope_type="assessment",
        scope_id="assessment-phase4d3",
        status="partial",
        value_type="text",
        value_json="2026年 月 日 时",
        source_assertion_ids_json=[assertion.id],
        reason_codes_json=["SOURCE_TEMPLATE_VALUE"],
        resolution_hash=canonical_hash({"resolved": "deadline-text"}),
        created_at=now - timedelta(minutes=2),
    )
    db.add_all(
        [
            assertion,
            BidFactEvidenceLink(
                assertion_id=assertion.id,
                evidence_fragment_id="atom-phase4d3-overview",
                manifest_id="manifest-phase4d3",
                parse_run_id="parse-phase4d3",
                document_version_id="document-version-phase4d3",
                evidence_text_hash=db.get(
                    BidEvidenceFragment,
                    "atom-phase4d3-overview",
                ).text_hash,
                locator_hash=db.get(
                    BidEvidenceFragment,
                    "atom-phase4d3-overview",
                ).locator_hash,
                context_read=True,
                link_hash=canonical_hash({"source": "deadline-text-link"}),
                created_at=now - timedelta(minutes=2),
            ),
            resolved,
            BidResolvedFactHead(
                run_id="run-phase4d3-source",
                fact_slot="tender.submission.deadline",
                scope_type="assessment",
                scope_id="assessment-phase4d3",
                resolved_fact_id=resolved.id,
                row_version=1,
                created_at=now - timedelta(minutes=2),
                updated_at=now - timedelta(minutes=2),
            ),
        ]
    )
    db.flush()

    draft = build_hard_gate_comparison_draft(
        db,
        assessment_id="assessment-phase4d3",
        source_run_id="run-phase4d3-source",
        business_baseline_id="business-baseline-phase4d3",
    )
    deadline = next(
        fact
        for fact in draft["facts"]
        if fact["fact_slot"] == "tender.submission.deadline"
    )
    assert deadline["verification_status"] == "unknown"
    assert deadline["value_type"] is None
    assert deadline["canonical_value"] is None
    assert deadline["evidence_atom_ids"] == []


def test_phase4d3_draft_freeze_idempotency_and_authority_drift(
    phase4d3_runtime,
    monkeypatch,
) -> None:
    db, now = phase4d3_runtime
    monkeypatch.setattr(
        verification_service,
        "settings",
        _fact_verification_settings(),
    )
    before = {
        "baseline": db.query(BidHardGateComparisonBaseline).count(),
        "evidence_link": db.query(BidHardGateComparisonEvidenceLink).count(),
        "audit": db.query(BidAuditLog).count(),
    }
    command = _review_command(db, reviewed_as_of=now)
    assert {
        fact["fact_slot"]
        for fact in command["facts"]
        if fact["verification_status"] == "partial"
    } == {"enterprise.identity.legal_name"}
    assert before == {
        "baseline": db.query(BidHardGateComparisonBaseline).count(),
        "evidence_link": db.query(BidHardGateComparisonEvidenceLink).count(),
        "audit": db.query(BidAuditLog).count(),
    }

    first = preview_hard_gate_comparison_baseline(db, actor_id=1, command=command)
    second = preview_hard_gate_comparison_baseline(db, actor_id=1, command=command)
    assert first["candidate_hash"] == second["candidate_hash"]
    assert first["status_counts"] == {"supported": 1, "partial": 1, "unknown": 14}
    assert db.query(BidHardGateComparisonBaseline).count() == 0

    frozen = freeze_hard_gate_comparison_baseline(
        db,
        actor_id=1,
        command=command,
        request_id="phase4d3-freeze",
        expected_candidate_hash=first["candidate_hash"],
        now=now,
    )
    db.commit()
    assert frozen.created is True
    assert frozen.projection["verification_outcome"] == "verified_with_follow_up"
    assert db.query(BidHardGateComparisonEvidenceLink).count() == 2
    validate_hard_gate_comparison_baseline_at(
        db,
        baseline=frozen.baseline,
        effective_at=now,
    )

    replay = freeze_hard_gate_comparison_baseline(
        db,
        actor_id=1,
        command=command,
        request_id="phase4d3-replay",
        expected_candidate_hash=first["candidate_hash"],
        now=now,
    )
    assert replay.created is False
    assert replay.baseline.id == frozen.baseline.id

    drifted = deepcopy(command)
    drifted["review_note"] = "候选已发生变化"
    with pytest.raises(
        BidHardGateFactVerificationError,
        match="BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_MISMATCH",
    ):
        freeze_hard_gate_comparison_baseline(
            db,
            actor_id=1,
            command=drifted,
            request_id="phase4d3-candidate-drift",
            expected_candidate_hash=first["candidate_hash"],
            now=now,
        )

    atom = db.get(BidEvidenceFragment, "atom-phase4d3-overview")
    original_atom_hash = str(atom.text_hash)
    atom.text_hash = "f" * 64
    db.flush()
    with pytest.raises(BidHardGateFactVerificationError):
        validate_hard_gate_comparison_baseline_at(
            db,
            baseline=frozen.baseline,
            effective_at=now,
        )
    atom.text_hash = original_atom_hash
    db.flush()

    business = db.get(BidEnterpriseBusinessBaseline, "business-baseline-phase4d3")
    business.baseline_hash = "0" * 64
    db.flush()
    with pytest.raises(
        BidHardGateFactVerificationError,
        match="BID_HARD_GATE_COMPARISON_AUTHORITY_DRIFT",
    ):
        validate_hard_gate_comparison_baseline_at(
            db,
            baseline=frozen.baseline,
            effective_at=now,
        )
    projection = get_hard_gate_comparison_baseline(
        db,
        assessment_id="assessment-phase4d3",
    )
    assert projection is not None
    assert projection["current"] is False
    assert "AUTHORITY_DRIFT" in str(projection["stale_code"])


def test_phase4d3_p1_materialization_resolver_priority_and_hg01_hg07(
    phase4d3_runtime,
    monkeypatch,
) -> None:
    db, now = phase4d3_runtime
    settings = _fact_verification_settings()
    monkeypatch.setattr(verification_service, "settings", settings)
    command = _review_command(db, reviewed_as_of=now)
    preview = preview_hard_gate_comparison_baseline(db, actor_id=1, command=command)
    frozen = freeze_hard_gate_comparison_baseline(
        db,
        actor_id=1,
        command=command,
        request_id="phase4d3-materialize-freeze",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    target_run = db.get(BidAnalysisRun, "run-phase4d3-target")
    target_run.hard_gate_comparison_baseline_id = str(frozen.baseline.id)
    target_run.hard_gate_comparison_baseline_hash = str(frozen.baseline.baseline_hash)
    db.flush()

    attempt = SimpleNamespace(id="attempt-phase4d3")
    enterprise_task = SimpleNamespace(
        id="task-phase4d3-enterprise",
        task_type="build_enterprise_snapshot",
    )
    monkeypatch.setattr(enterprise_service, "settings", settings)
    monkeypatch.setattr(
        enterprise_service,
        "lock_task_claim",
        lambda *_args, **_kwargs: (attempt, enterprise_task, target_run),
    )
    materialized = enterprise_service.materialize_enterprise_snapshot_facts(
        db,
        SimpleNamespace(),
        now=now,
    )
    assert materialized["materialized_count"] == 2
    assert set(materialized["unknown_fact_slots"]) == (
        set(COMPARABLE_FACT_SPECS) - {
            "tender.overview",
            "enterprise.identity.legal_name",
        }
    )
    assert db.query(BidFactComparisonLink).count() == 2

    candidate_value = {"legal_name": "未经核验的模型候选名称"}
    db.add(
        BidFactAssertion(
            id="assertion-unlinked-model-candidate",
            assessment_id="assessment-phase4d3",
            run_id="run-phase4d3-target",
            task_id="task-model-candidate",
            source_task_attempt_id="attempt-model-candidate",
            fact_catalog_version_id="facts-phase4d3",
            fact_slot="enterprise.identity.legal_name",
            scope_type="assessment",
            scope_id="assessment-phase4d3",
            value_type="enterprise_identity",
            value_json=candidate_value,
            value_hash=canonical_hash(candidate_value),
            source_type="system",
            confidence="high",
            status="accepted",
            asserted_at=now,
            assertion_hash=canonical_hash({"candidate": "unlinked-model"}),
            reason_codes_json=["MODEL_CANDIDATE"],
            created_at=now,
        )
    )
    db.flush()

    resolve_task = SimpleNamespace(id="task-phase4d3-resolve", task_type="resolve_facts")
    monkeypatch.setattr(
        authority_service,
        "lock_task_claim",
        lambda *_args, **_kwargs: (attempt, resolve_task, target_run),
    )
    authority_service.resolve_facts(db, SimpleNamespace(), now=now)
    resolved_rows = {
        str(row.fact_slot): row
        for row in db.query(BidResolvedFact)
        .join(
            BidResolvedFactHead,
            BidResolvedFactHead.resolved_fact_id == BidResolvedFact.id,
        )
        .filter(BidResolvedFactHead.run_id == target_run.id)
        .all()
    }
    assert resolved_rows["tender.overview"].status == "supported"
    enterprise_identity = resolved_rows["enterprise.identity.legal_name"]
    assert enterprise_identity.status == "partial"
    assert enterprise_identity.value_json == {"legal_name": "旗胜合成验证有限公司"}
    assert "VERIFIED_COMPARISON_OVERRIDES_CANDIDATE_ASSERTIONS" in set(
        enterprise_identity.reason_codes_json
    )

    gate_task_types = (
        "evaluate_deadline_gate",
        "evaluate_qualification_gate",
        "evaluate_personnel_performance_gate",
        "evaluate_legal_compliance_gate",
        "evaluate_guarantee_cash_gate",
        "evaluate_minimum_bid_capacity_gate",
        "evaluate_enterprise_prohibited_risk_gate",
    )
    for index, task_type in enumerate(gate_task_types, start=1):
        gate_task = SimpleNamespace(id=f"task-phase4d3-gate-{index}", task_type=task_type)
        monkeypatch.setattr(
            authority_service,
            "lock_task_claim",
            lambda *_args, _task=gate_task, **_kwargs: (
                attempt,
                _task,
                target_run,
            ),
        )
        authority_service.evaluate_hard_gate(db, SimpleNamespace(), now=now)
    gates = db.query(BidHardGateResult).filter(
        BidHardGateResult.run_id == target_run.id
    ).all()
    assert {str(row.gate_code) for row in gates} == {
        "HG01",
        "HG02",
        "HG03",
        "HG04",
        "HG05",
        "HG06",
        "HG07",
    }
    assert all(str(row.status) == "unknown" for row in gates)


def test_phase4d3_executor_accepts_comparison_materialization_output(
    monkeypatch,
) -> None:
    task = SimpleNamespace(
        id="task-phase4d3-executor",
        run_id="run-phase4d3-executor",
        task_type="build_enterprise_snapshot",
    )

    class _TaskQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def one(self):
            return task

    db = SimpleNamespace(query=lambda *_args, **_kwargs: _TaskQuery())
    claim = SimpleNamespace(
        task_id=task.id,
        task_contract={
            "skill_binding": {"executor_kind": "deterministic"},
        },
    )
    payload = {
        "schema": "bid.hard-gate.comparable-fact-materialization.v1",
        "comparison_baseline_id": "comparison-baseline-phase4d3",
        "comparison_baseline_hash": "a" * 64,
        "assertion_ids": ["assertion-phase4d3"],
        "unknown_fact_slots": [],
        "materialized_count": 1,
    }
    completed: list[AuthorityOutput] = []
    monkeypatch.setattr(
        mvp1_executor_service,
        "materialize_enterprise_snapshot_facts",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        mvp1_executor_service,
        "_complete",
        lambda _db, _claim, *, output, request_id: completed.append(output),
    )

    status = mvp1_executor_service._execute_claim(
        db,
        claim,
        tool_scope_signing_key="phase4d3-test-scope-key",
        request_id="phase4d3-executor",
    )

    assert status == "completed"
    assert completed[0].output_ref == (
        "hard-gate-comparison-facts:comparison-baseline-phase4d3"
    )
    assert completed[0].payload == payload


def test_phase4d3_run_bootstrap_binds_current_baseline_and_blocks_evidence_drift(
    phase4d3_runtime,
    monkeypatch,
) -> None:
    db, now = phase4d3_runtime
    fact_settings = _fact_verification_settings()
    monkeypatch.setattr(verification_service, "settings", fact_settings)
    command = _review_command(db, reviewed_as_of=now)
    preview = preview_hard_gate_comparison_baseline(db, actor_id=1, command=command)
    frozen = freeze_hard_gate_comparison_baseline(
        db,
        actor_id=1,
        command=command,
        request_id="phase4d3-bootstrap-freeze",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    target = db.get(BidAnalysisRun, "run-phase4d3-target")
    target.status = "succeeded"
    target.finished_at = now
    db.flush()

    bootstrap_settings = SimpleNamespace(
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_enterprise_capability=False,
        feature_bid_assessment_phase4_fact_verification=True,
    )
    monkeypatch.setattr(bootstrap_service, "settings", bootstrap_settings)
    monkeypatch.setattr(
        business_baseline_service,
        "settings",
        SimpleNamespace(
            feature_bid_assessment_phase4_enterprise_evidence_import=False,
        ),
    )
    monkeypatch.setattr(
        business_baseline_service,
        "validate_frozen_snapshot_metadata",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        business_baseline_service,
        "project_enterprise_snapshot",
        lambda *_args, **_kwargs: {"records": []},
    )

    atom = db.get(BidEvidenceFragment, "atom-phase4d3-overview")
    original_atom_hash = str(atom.text_hash)
    atom.text_hash = "f" * 64
    db.flush()
    with pytest.raises(bootstrap_service.BidRunInputNotReady) as stale_exc:
        bootstrap_service.bootstrap_run(
            db,
            assessment_id="assessment-phase4d3",
            scope_id="scope-phase4d3",
            manifest_id="manifest-phase4d3",
            run_kind="reanalysis",
            actor_type="user",
            actor_ref="user:1",
            actor_id=1,
            request_id="phase4d3-bootstrap-stale",
            evaluation_time=now,
        )
    assert stale_exc.value.reasons == (
        "hard_gate_comparison_baseline_missing_or_stale",
    )
    atom.text_hash = original_atom_hash
    db.flush()

    result = bootstrap_service.bootstrap_run(
        db,
        assessment_id="assessment-phase4d3",
        scope_id="scope-phase4d3",
        manifest_id="manifest-phase4d3",
        run_kind="reanalysis",
        actor_type="user",
        actor_ref="user:1",
        actor_id=1,
        request_id="phase4d3-bootstrap-current",
        evaluation_time=now,
    )
    db.commit()
    assert result.run.hard_gate_comparison_baseline_id == frozen.baseline.id
    assert result.run.hard_gate_comparison_baseline_hash == frozen.baseline.baseline_hash
    created_event = db.query(BidOutboxEvent).filter(
        BidOutboxEvent.run_id == result.run.id,
        BidOutboxEvent.event_type == "bid.run.created.v1",
    ).one()
    assert created_event.payload_json["hard_gate_comparison_baseline_hash"] == (
        frozen.baseline.baseline_hash
    )


def test_phase4d3_run_validation_marks_comparison_baseline_drift_stale(
    phase4d3_runtime,
    monkeypatch,
) -> None:
    db, now = phase4d3_runtime
    settings = _fact_verification_settings()
    monkeypatch.setattr(verification_service, "settings", settings)
    monkeypatch.setattr(run_validation_service, "settings", settings)
    command = _review_command(db, reviewed_as_of=now)
    preview = preview_hard_gate_comparison_baseline(db, actor_id=1, command=command)
    frozen = freeze_hard_gate_comparison_baseline(
        db,
        actor_id=1,
        command=command,
        request_id="phase4d3-validation-freeze",
        expected_candidate_hash=preview["candidate_hash"],
        now=now,
    )
    run = db.get(BidAnalysisRun, "run-phase4d3-target")
    run.hard_gate_comparison_baseline_id = str(frozen.baseline.id)
    run.hard_gate_comparison_baseline_hash = str(frozen.baseline.baseline_hash)
    db.flush()
    validation = SimpleNamespace(
        id="validation-phase4d3",
        input_hash="f" * 64,
        plan_revision_id=None,
        validator_version="bid-run-validator-v4",
    )
    assessment = db.get(BidAssessment, "assessment-phase4d3")
    current = run_validation_service._deterministic_validation(
        db,
        validation=validation,
        run=run,
        assessment=assessment,
    )
    current_check = next(
        item
        for item in current["checks"]
        if item["code"] == "HARD_GATE_COMPARISON_BASELINE_CURRENT"
    )
    assert current_check["passed"] is True

    business = db.get(BidEnterpriseBusinessBaseline, "business-baseline-phase4d3")
    business.baseline_hash = "0" * 64
    db.flush()
    stale = run_validation_service._deterministic_validation(
        db,
        validation=validation,
        run=run,
        assessment=assessment,
    )
    stale_check = next(
        item
        for item in stale["checks"]
        if item["code"] == "HARD_GATE_COMPARISON_BASELINE_CURRENT"
    )
    assert stale_check["passed"] is False
    assert stale["outcome"] == "stale"
    assert stale["failure_code"] == "BID_RUN_INPUT_STALE"


def test_phase4d3_api_acl_view_only_validate_freeze_and_idempotency(
    phase4d3_runtime,
    monkeypatch,
) -> None:
    db, now = phase4d3_runtime
    settings = _fact_verification_settings()
    monkeypatch.setattr(runtime_lab_api, "settings", settings)
    monkeypatch.setattr(verification_service, "settings", settings)
    command = _review_command(db, reviewed_as_of=now)
    payload = BidHardGateComparisonBaselineCreateIn.model_validate(command)
    user = SimpleNamespace(id=1)

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: False)
    hidden = runtime_lab_api.validate_runtime_lab_hard_gate_comparison_baseline(
        payload,
        _runtime_request(
            access_mode="execute",
            path=(
                "/api/v1/bid-assessment-runtime-lab/"
                "hard-gate-comparison-baselines/validate"
            ),
        ),
        current_user=user,
        db=db,
    )
    assert hidden.status_code == 404

    monkeypatch.setattr(runtime_lab_api, "has_admin_role", lambda _user: True)
    blocked = runtime_lab_api.validate_runtime_lab_hard_gate_comparison_baseline(
        payload,
        _runtime_request(
            access_mode="view-only",
            path=(
                "/api/v1/bid-assessment-runtime-lab/"
                "hard-gate-comparison-baselines/validate"
            ),
        ),
        current_user=user,
        db=db,
    )
    assert blocked.status_code == 403
    assert json.loads(blocked.body)["error"]["code"] == "BID_MVP1_VIEW_ONLY"
    assert db.query(BidHardGateComparisonBaseline).count() == 0

    execute_validate = _runtime_request(
        access_mode="execute",
        path=(
            "/api/v1/bid-assessment-runtime-lab/"
            "hard-gate-comparison-baselines/validate"
        ),
    )
    validated = runtime_lab_api.validate_runtime_lab_hard_gate_comparison_baseline(
        payload,
        execute_validate,
        current_user=user,
        db=db,
    )
    candidate_hash = str(validated["data"]["candidate_hash"])
    assert db.query(BidHardGateComparisonBaseline).count() == 0
    assert db.query(BidAuditLog).count() == 0

    execute_freeze = _runtime_request(
        access_mode="execute",
        path=(
            "/api/v1/bid-assessment-runtime-lab/"
            "hard-gate-comparison-baselines"
        ),
    )
    fenced = runtime_lab_api.create_runtime_lab_hard_gate_comparison_baseline(
        payload,
        execute_freeze,
        idempotency_key="phase4d3-candidate-fence-key",
        candidate_hash="f" * 64,
        current_user=user,
        db=db,
    )
    assert fenced.status_code == 409
    assert json.loads(fenced.body)["error"]["code"] == (
        "BID_HARD_GATE_COMPARISON_CANDIDATE_HASH_MISMATCH"
    )
    assert db.query(BidHardGateComparisonBaseline).count() == 0

    first = runtime_lab_api.create_runtime_lab_hard_gate_comparison_baseline(
        payload,
        execute_freeze,
        idempotency_key="phase4d3-freeze-idempotency-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=db,
    )
    assert first.status_code == 201
    assert first.headers["idempotency-replayed"] == "false"
    replay = runtime_lab_api.create_runtime_lab_hard_gate_comparison_baseline(
        payload,
        execute_freeze,
        idempotency_key="phase4d3-freeze-idempotency-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=db,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert db.query(BidHardGateComparisonBaseline).count() == 1
    assert db.query(BidAuditLog).count() == 1
    assert db.query(BidIdempotencyRecord).count() == 1

    drifted = deepcopy(command)
    drifted["review_note"] = "复用同一幂等键但请求内容发生变化"
    conflict = runtime_lab_api.create_runtime_lab_hard_gate_comparison_baseline(
        BidHardGateComparisonBaselineCreateIn.model_validate(drifted),
        execute_freeze,
        idempotency_key="phase4d3-freeze-idempotency-key",
        candidate_hash=candidate_hash,
        current_user=user,
        db=db,
    )
    assert conflict.status_code == 409
    assert json.loads(conflict.body)["error"]["code"] == "BID_IDEMPOTENCY_KEY_REUSED"
