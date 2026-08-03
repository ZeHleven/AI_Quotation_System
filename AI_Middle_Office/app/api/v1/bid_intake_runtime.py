from __future__ import annotations

import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.responses import api_ok
from app.dependencies import get_current_user
from app.models.bid_intake_runtime import (
    BidIntakeAgentRun,
    BidIntakeAssessment,
)
from app.models.bidding import BidProject
from app.models.user import User
from app.services.bid_intake_runtime import (
    DEFAULT_ANALYSIS_GOAL,
    BidIntakeRuntimeConflict,
    BidIntakeRuntimeNotFound,
    build_project_runtime_readiness,
    cancel_agent_run,
    create_assessment_run,
    get_assessment,
    get_run,
    queue_human_decision,
    retry_failed_run,
    serialize_assessment,
    serialize_human_decision,
    serialize_run,
)
from app.services.bid_policy_calibration import (
    BidPolicyCalibrationConflict,
    BidPolicyCalibrationNotFound,
    build_calibration_report,
    create_calibration_label,
    current_calibration_label,
    serialize_calibration_label,
)
from app.services.bid_policy_candidates import (
    blind_evaluate_policy_candidate,
    generate_policy_candidate,
    list_policy_candidates,
    serialize_policy_candidate,
)
from app.services.bid_policy_dataset_ops import (
    build_current_dataset_quality,
    freeze_calibration_dataset,
    list_calibration_datasets,
    list_calibration_samples,
    review_calibration_label,
    serialize_calibration_dataset,
    serialize_calibration_review,
)
from app.services.rbac import has_any_role


router = APIRouter()
BIDDING_ROLES = {
    "admin",
    "system_admin",
    "staff",
    "manager",
    "quote_user",
    "quote_operator",
}
VIEW_ALL_ROLES = {"admin", "system_admin", "manager", "quote_operator"}
CALIBRATION_MANAGER_ROLES = {"admin", "system_admin", "manager"}


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAssessmentRequest(StrictRequest):
    analysis_goal: str = Field(
        default=DEFAULT_ANALYSIS_GOAL,
        min_length=1,
        max_length=2000,
    )
    max_attempts: int = Field(default=3, ge=1, le=10)


class HumanDecisionRequest(StrictRequest):
    decision_uuid: uuid.UUID = Field(default_factory=uuid.uuid4)
    action: str = Field(min_length=1, max_length=40)
    report_version: int = Field(ge=1)
    manifest_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)
    conditions: list[str] = Field(default_factory=list, max_length=20)


class CalibrationActualOutcomeRequest(StrictRequest):
    bid_submitted: bool | None = None
    won_bid: bool | None = None
    realized_margin_rate: float | None = Field(
        default=None,
        ge=-100,
        le=100,
    )
    payment_overdue: bool | None = None
    major_delivery_issue: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


class CalibrationLabelRequest(StrictRequest):
    expected_current_label_version: int = Field(default=0, ge=0)
    dataset_split: Literal["development", "holdout"]
    label_basis: Literal[
        "pre_bid_expert_review",
        "actual_project_outcome",
        "combined",
    ]
    expected_decision: Literal[
        "recommend_quote",
        "conditional_quote",
        "recommend_no_quote",
        "need_supplement",
    ]
    hard_stop_expected: bool = False
    rationale: str = Field(min_length=1, max_length=4000)
    actual_outcome: CalibrationActualOutcomeRequest | None = None


class CalibrationReviewRequest(StrictRequest):
    action: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=4000)


class CalibrationDatasetFreezeRequest(StrictRequest):
    freeze_note: str | None = Field(default=None, max_length=2000)


class CalibrationCandidateRequest(StrictRequest):
    dataset_uuid: uuid.UUID


def _runtime_enabled() -> bool:
    return os.environ.get(
        "BID_INTAKE_AGENT_RUNTIME_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_bidding_access(current_user: User) -> None:
    if not settings.feature_bidding_mvp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NOT_FOUND",
        )
    if not has_any_role(current_user, BIDDING_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PERMISSION_DENIED",
        )


def _ensure_runtime_enabled() -> None:
    if not _runtime_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NOT_FOUND",
        )


def _get_project(
    db: Session,
    project_uuid: str,
    current_user: User,
    *,
    require_runtime: bool = True,
) -> BidProject:
    _ensure_bidding_access(current_user)
    if require_runtime:
        _ensure_runtime_enabled()
    project = (
        db.query(BidProject)
        .filter(BidProject.project_uuid == project_uuid)
        .one_or_none()
    )
    can_view_all = has_any_role(current_user, VIEW_ALL_ROLES)
    can_manage = (
        can_view_all
        or project is not None
        and (
            project.created_by == current_user.id
            or project.owner_user_id == current_user.id
        )
    )
    if project is None or not can_manage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BID_PROJECT_NOT_FOUND",
        )
    return project


def _raise_runtime_error(exc: Exception) -> None:
    if isinstance(exc, BidIntakeRuntimeNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, BidIntakeRuntimeConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise exc


def _raise_calibration_error(exc: Exception) -> None:
    if isinstance(exc, BidPolicyCalibrationNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, BidPolicyCalibrationConflict):
        details = getattr(exc, "details", None) or {}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                {"code": str(exc), **details}
                if details
                else str(exc)
            ),
        ) from exc
    raise exc


def _ensure_calibration_manage_access(current_user: User) -> None:
    if not has_any_role(current_user, CALIBRATION_MANAGER_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CALIBRATION_MANAGE_PERMISSION_DENIED",
        )


@router.post(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments",
    status_code=status.HTTP_201_CREATED,
    summary="创建报价资料研判与立项辅助 Agent 任务",
)
def create_bid_intake_assessment(
    project_uuid: str,
    payload: CreateAssessmentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    readiness = build_project_runtime_readiness(
        db,
        project_id=project.id,
    )
    if not readiness["ready_to_start"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "BID_INTAKE_RUNTIME_NOT_READY",
                "blockers": readiness["blockers"],
            },
        )
    try:
        created = create_assessment_run(
            db,
            project=project,
            current_user=current_user,
            analysis_goal=payload.analysis_goal,
            max_attempts=payload.max_attempts,
        )
        db.commit()
        db.refresh(created.assessment)
        db.refresh(created.run)
    except Exception as exc:
        db.rollback()
        _raise_runtime_error(exc)
    return api_ok(
        {
            "assessment": serialize_assessment(db, created.assessment),
            "run": serialize_run(db, created.run),
            "dispatch_mode": "dedicated_worker_poll",
        },
        message="研判任务已创建，等待专用 Agent Worker 领取",
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/bid-intake/readiness",
    summary="查询项目研判 Agent 的安全就绪状态",
)
def get_bid_intake_readiness(
    project_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(
        db,
        project_uuid,
        current_user,
        require_runtime=False,
    )
    return api_ok(
        build_project_runtime_readiness(
            db,
            project_id=project.id,
        )
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments",
    summary="查询项目的报价资料研判任务",
)
def list_bid_intake_assessments(
    project_uuid: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    assessments = (
        db.query(BidIntakeAssessment)
        .filter(BidIntakeAssessment.project_id == project.id)
        .order_by(
            BidIntakeAssessment.created_at.desc(),
            BidIntakeAssessment.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return api_ok(
        [serialize_assessment(db, item) for item in assessments],
        total=len(assessments),
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}",
    summary="查询研判、运行轨迹和人工决策详情",
)
def get_bid_intake_assessment(
    project_uuid: str,
    assessment_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
    except Exception as exc:
        _raise_runtime_error(exc)
    return api_ok(
        serialize_assessment(
            db,
            assessment,
            include_runs=True,
            include_events=True,
        )
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/calibration-label",
    summary="查询研判快照的总经办校准金标",
)
def get_bid_intake_calibration_label(
    project_uuid: str,
    assessment_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(
        db,
        project_uuid,
        current_user,
        require_runtime=False,
    )
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
    except Exception as exc:
        _raise_runtime_error(exc)
    can_manage = has_any_role(
        current_user,
        CALIBRATION_MANAGER_ROLES,
    )
    serialized = serialize_calibration_label(
        current_calibration_label(
            db,
            assessment_id=assessment.id,
        )
    )
    if serialized is not None and not can_manage:
        serialized = {
            "label_version": serialized["label_version"],
            "active": serialized["active"],
            "dataset_split": serialized["dataset_split"],
            "source_report_version": (
                serialized["source_report_version"]
            ),
            "created_at": serialized["created_at"],
            "masked": True,
        }
    return api_ok(
        {
            "label": serialized,
            "can_manage": can_manage,
        }
    )


@router.post(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/calibration-label",
    status_code=status.HTTP_201_CREATED,
    summary="为历史研判快照记录总经办校准金标",
)
def label_bid_intake_assessment_for_calibration(
    project_uuid: str,
    assessment_uuid: str,
    payload: CalibrationLabelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(
        db,
        project_uuid,
        current_user,
        require_runtime=False,
    )
    _ensure_calibration_manage_access(current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
        label = create_calibration_label(
            db,
            project=project,
            assessment=assessment,
            current_user=current_user,
            expected_current_label_version=(
                payload.expected_current_label_version
            ),
            dataset_split=payload.dataset_split,
            label_basis=payload.label_basis,
            expected_decision=payload.expected_decision,
            hard_stop_expected=payload.hard_stop_expected,
            rationale=payload.rationale,
            actual_outcome=(
                payload.actual_outcome.model_dump(mode="json")
                if payload.actual_outcome is not None
                else None
            ),
        )
        db.commit()
        db.refresh(label)
    except Exception as exc:
        db.rollback()
        _raise_calibration_error(exc)
    return api_ok(
        {"label": serialize_calibration_label(label)},
        message="总经办金标已形成不可变快照",
    )


@router.get(
    "/admin/bidding/bid-intake/calibration/report",
    summary="只读比较active与候选立项标准的历史回放结果",
)
def get_bid_policy_calibration_report(
    candidate_policy_version: str | None = Query(
        default=None,
        min_length=3,
        max_length=64,
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    try:
        report = build_calibration_report(
            db,
            candidate_policy_version=candidate_policy_version,
        )
        report["can_manage"] = has_any_role(
            current_user,
            CALIBRATION_MANAGER_ROLES,
        )
    except Exception as exc:
        _raise_calibration_error(exc)
    return api_ok(report)


@router.get(
    "/admin/bidding/bid-intake/calibration/samples",
    summary="查询待复核与已复核的真实金标样本池",
)
def get_bid_policy_calibration_samples(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    review_status: Literal[
        "pending",
        "approved",
        "rejected",
    ] | None = Query(default=None),
    dataset_split: Literal[
        "development",
        "holdout",
    ] | None = Query(default=None),
    expected_decision: Literal[
        "recommend_quote",
        "conditional_quote",
        "recommend_no_quote",
        "need_supplement",
    ] | None = Query(default=None),
    hard_stop_expected: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=160),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    payload = list_calibration_samples(
        db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        review_status=review_status,
        dataset_split=dataset_split,
        expected_decision=expected_decision,
        hard_stop_expected=hard_stop_expected,
        search=search,
    )
    return api_ok(
        payload["items"],
        total=payload["total"],
        page=payload["page"],
        page_size=payload["page_size"],
    )


@router.post(
    "/admin/bidding/bid-intake/calibration/labels/"
    "{label_uuid}/review",
    status_code=status.HTTP_201_CREATED,
    summary="由不同人员复核不可变金标",
)
def review_bid_policy_calibration_label(
    label_uuid: str,
    payload: CalibrationReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    try:
        review = review_calibration_label(
            db,
            label_uuid=label_uuid,
            action=payload.action,
            note=payload.note,
            current_user=current_user,
        )
        db.commit()
        db.refresh(review)
    except Exception as exc:
        db.rollback()
        _raise_calibration_error(exc)
    return api_ok(
        {"review": serialize_calibration_review(review)},
        message="复核结果已形成不可变记录",
    )


@router.get(
    "/admin/bidding/bid-intake/calibration/quality",
    summary="检查已复核金标的数据集冻结就绪度",
)
def get_bid_policy_calibration_quality(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    return api_ok(build_current_dataset_quality(db))


@router.get(
    "/admin/bidding/bid-intake/calibration/datasets",
    summary="查询不可变校准数据集版本",
)
def get_bid_policy_calibration_datasets(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    rows = list_calibration_datasets(db, limit=limit)
    return api_ok(
        [serialize_calibration_dataset(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/admin/bidding/bid-intake/calibration/datasets",
    status_code=status.HTTP_201_CREATED,
    summary="冻结复核通过且满足质量门的校准数据集",
)
def create_bid_policy_calibration_dataset(
    payload: CalibrationDatasetFreezeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    try:
        row = freeze_calibration_dataset(
            db,
            current_user=current_user,
            freeze_note=payload.freeze_note,
        )
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        _raise_calibration_error(exc)
    return api_ok(
        serialize_calibration_dataset(row),
        message="校准数据集已冻结，后续样本变更不会影响该版本",
    )


@router.get(
    "/admin/bidding/bid-intake/calibration/candidates",
    summary="查询冻结的总经办立项标准候选提案",
)
def get_bid_policy_candidates(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    rows = list_policy_candidates(db, limit=limit)
    return api_ok(
        [serialize_policy_candidate(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/admin/bidding/bid-intake/calibration/candidates",
    status_code=status.HTTP_201_CREATED,
    summary="仅使用development金标生成受约束的阈值候选提案",
)
def create_bid_policy_candidate(
    payload: CalibrationCandidateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    try:
        row = generate_policy_candidate(
            db,
            current_user=current_user,
            dataset_uuid=str(payload.dataset_uuid),
        )
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        _raise_calibration_error(exc)
    return api_ok(
        serialize_policy_candidate(row),
        message="候选标准已冻结为待审提案，未修改active版本",
    )


@router.post(
    "/admin/bidding/bid-intake/calibration/candidates/"
    "{proposal_uuid}/blind-evaluate",
    summary="对冻结候选执行一次性Holdout聚合盲测",
)
def blind_evaluate_bid_policy_candidate(
    proposal_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_bidding_access(current_user)
    _ensure_calibration_manage_access(current_user)
    try:
        row = blind_evaluate_policy_candidate(
            db,
            proposal_uuid=proposal_uuid,
            current_user=current_user,
        )
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        _raise_calibration_error(exc)
    return api_ok(
        serialize_policy_candidate(row),
        message="Holdout盲测已冻结；候选仍未获得active发布权限",
    )


@router.get(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/runs/{run_uuid}",
    summary="查询单次 Agent 运行与 Checkpoint 摘要",
)
def get_bid_intake_run(
    project_uuid: str,
    assessment_uuid: str,
    run_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
        run = get_run(
            db,
            assessment_id=assessment.id,
            run_uuid=run_uuid,
        )
    except Exception as exc:
        _raise_runtime_error(exc)
    return api_ok(
        serialize_run(
            db,
            run,
            include_events=True,
            include_decisions=True,
        )
    )


@router.post(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/runs/{run_uuid}/decision",
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交人工决策并排队恢复 LangGraph",
)
def decide_bid_intake_run(
    project_uuid: str,
    assessment_uuid: str,
    run_uuid: str,
    payload: HumanDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
        run = get_run(
            db,
            assessment_id=assessment.id,
            run_uuid=run_uuid,
        )
        decision, idempotent = queue_human_decision(
            db,
            assessment=assessment,
            run=run,
            current_user=current_user,
            decision_uuid=str(payload.decision_uuid),
            action=payload.action,
            report_version=payload.report_version,
            manifest_version=payload.manifest_version,
            note=payload.note,
            conditions=payload.conditions,
        )
        db.commit()
        db.refresh(decision)
    except Exception as exc:
        db.rollback()
        _raise_runtime_error(exc)
    return api_ok(
        {
            "decision": serialize_human_decision(decision),
            "idempotent": idempotent,
            "run_status": (
                run.status if not idempotent else "already_queued_or_applied"
            ),
        },
        message="人工决策已保存，等待 Agent 从暂停点恢复",
    )


@router.post(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/runs/{run_uuid}/retry",
    summary="从最近 Checkpoint 重试失败的 Agent 运行",
)
def retry_bid_intake_run(
    project_uuid: str,
    assessment_uuid: str,
    run_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
        run = get_run(
            db,
            assessment_id=assessment.id,
            run_uuid=run_uuid,
        )
        retry_failed_run(db, assessment=assessment, run=run)
        db.commit()
        db.refresh(run)
    except Exception as exc:
        db.rollback()
        _raise_runtime_error(exc)
    return api_ok(
        serialize_run(db, run, include_events=True),
        message="失败任务已重新入队",
    )


@router.post(
    "/admin/bidding/projects/{project_uuid}/bid-intake/assessments/"
    "{assessment_uuid}/runs/{run_uuid}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="终止进行中的 Agent 研判并保留审计轨迹",
)
def cancel_bid_intake_run(
    project_uuid: str,
    assessment_uuid: str,
    run_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_uuid, current_user)
    try:
        assessment = get_assessment(
            db,
            project_id=project.id,
            assessment_uuid=assessment_uuid,
        )
        run = get_run(
            db,
            assessment_id=assessment.id,
            run_uuid=run_uuid,
        )
        run, idempotent = cancel_agent_run(
            db,
            assessment=assessment,
            run=run,
            current_user=current_user,
        )
        db.commit()
        db.refresh(run)
    except Exception as exc:
        db.rollback()
        _raise_runtime_error(exc)
    return api_ok(
        {
            "run": serialize_run(db, run, include_events=True),
            "idempotent": idempotent,
        },
        message=(
            "该研判此前已经终止"
            if idempotent
            else "终止指令已保存，运行轨迹继续保留"
        ),
    )
