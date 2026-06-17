from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_trace_id
from app.core.responses import api_ok, api_page
from app.dependencies import get_current_user
from app.models.agent import AgentFinding, AgentRun, AgentSuggestion, AgentSuggestionEvent, AgentToolCall
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.user import User
from app.services.agent_daily_review import (
    DAILY_REVIEW_TRIGGER_SOURCE,
    build_quote_review_closure_summary,
    build_daily_quote_review_summary,
    list_quote_review_suggestions,
    run_daily_quote_review,
)
from app.services.agent_daily_scheduler import (
    build_quote_review_todo_summary,
    get_quote_review_scheduler_status,
    list_quote_review_scheduler_history,
)
from app.services.agent_llm_explanation import build_agent_llm_explanation, build_agent_llm_explanation_with_llm
from app.services.agent_quote_review import (
    QUOTE_REVIEW_AGENT_TYPE,
    create_quote_review_agent_run,
    serialize_agent_finding,
    serialize_agent_run,
    serialize_agent_suggestion,
    serialize_agent_suggestion_event,
    serialize_agent_tool_call,
)
from app.services.quote_job_numbers import find_quote_job_by_identifier
from app.services.rbac import has_admin_role, has_any_role


router = APIRouter()


class QuoteReviewAgentRunIn(BaseModel):
    quote_job_id: str
    quote_history_id: Optional[int] = Field(default=None, ge=1)
    confirmed_only: bool = False


class DailyQuoteReviewRunIn(BaseModel):
    review_date: Optional[str] = None
    dry_run: bool = False
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class AgentSuggestionDecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: Optional[str] = None


class AgentSuggestionExecuteIn(BaseModel):
    note: Optional[str] = None


class AgentSuggestionFinalConfirmIn(BaseModel):
    accepted_agent_result: bool
    final_result: dict = Field(default_factory=dict)
    note: Optional[str] = None


def _ensure_agent_enabled() -> None:
    if not settings.feature_agent_assistants:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _ensure_daily_review_enabled() -> None:
    _ensure_agent_enabled()
    if not settings.feature_agent_daily_review:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _ensure_llm_explanation_enabled() -> None:
    _ensure_agent_enabled()
    if not settings.feature_agent_llm_explanation:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FEATURE_DISABLED")


def _can_view_all_agent_runs(user: User) -> bool:
    return has_admin_role(user) or has_any_role(user, {"quote_operator"})


def _ensure_can_manage_daily_review(user: User) -> None:
    if not _can_view_all_agent_runs(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")


def _get_accessible_quote_job(db: Session, job_id: str, current_user: User) -> QuoteJob:
    job = find_quote_job_by_identifier(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="QUOTE_JOB_NOT_FOUND")
    if not _can_view_all_agent_runs(current_user) and job.username != current_user.username:
        raise HTTPException(status_code=404, detail="QUOTE_JOB_NOT_FOUND")
    return job


def _latest_pushed_quote_history(db: Session, job_id: str) -> QuoteHistory | None:
    return (
        db.query(QuoteHistory)
        .filter(
            QuoteHistory.quote_job_id == job_id,
            QuoteHistory.pushed_to_dingtalk.is_(True),
        )
        .order_by(QuoteHistory.created_at.desc(), QuoteHistory.id.desc())
        .first()
    )


def _get_manual_audit_history(db: Session, payload: QuoteReviewAgentRunIn, job: QuoteJob) -> QuoteHistory | None:
    if payload.quote_history_id:
        history = db.query(QuoteHistory).filter(QuoteHistory.id == payload.quote_history_id).first()
        if not history or history.quote_job_id != job.job_id or not history.pushed_to_dingtalk:
            raise HTTPException(status_code=404, detail="QUOTE_HISTORY_NOT_FOUND")
        return history
    history = _latest_pushed_quote_history(db, job.job_id)
    if payload.confirmed_only and not history:
        raise HTTPException(status_code=409, detail="QUOTE_NOT_PUSHED")
    return history


def _get_accessible_run(db: Session, run_id: str, current_user: User) -> AgentRun:
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="AGENT_RUN_NOT_FOUND")
    if not _can_view_all_agent_runs(current_user) and run.created_by != current_user.username:
        raise HTTPException(status_code=404, detail="AGENT_RUN_NOT_FOUND")
    return run


def _get_accessible_suggestion(db: Session, suggestion_id: str, current_user: User) -> AgentSuggestion:
    suggestion = db.query(AgentSuggestion).filter(AgentSuggestion.suggestion_id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="AGENT_SUGGESTION_NOT_FOUND")
    _get_accessible_run(db, suggestion.run_id, current_user)
    return suggestion


def _suggestions_for_run(db: Session, run_id: str) -> list[AgentSuggestion]:
    return (
        db.query(AgentSuggestion)
        .filter(AgentSuggestion.run_id == run_id)
        .order_by(AgentSuggestion.id.asc())
        .all()
    )


def _suggestion_events_for_run(db: Session, run_id: str) -> list[AgentSuggestionEvent]:
    return (
        db.query(AgentSuggestionEvent)
        .filter(AgentSuggestionEvent.run_id == run_id)
        .order_by(AgentSuggestionEvent.id.asc())
        .all()
    )


@router.post("/admin/agents/quote-review/runs", summary="创建报价复核 Agent 运行记录")
async def create_quote_review_run(
    payload: QuoteReviewAgentRunIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    job = _get_accessible_quote_job(db, payload.quote_job_id, current_user)
    history = _get_manual_audit_history(db, payload, job)
    run = create_quote_review_agent_run(
        db,
        job=job,
        created_by=current_user.username,
        trace_id=get_trace_id(),
        trigger_source="manual_audit" if history else "manual",
        trigger_ref_type="quote_history" if history else None,
        trigger_ref_id=str(history.id) if history else None,
        audit_only=True,
        audit_date=history.created_at.date() if history and history.created_at else None,
    )
    tool_calls = (
        db.query(AgentToolCall)
        .filter(AgentToolCall.run_id == run.run_id)
        .order_by(AgentToolCall.id.asc())
        .all()
    )
    findings = (
        db.query(AgentFinding)
        .filter(AgentFinding.run_id == run.run_id)
        .order_by(AgentFinding.id.asc())
        .all()
    )
    data = serialize_agent_run(run)
    if history:
        data["quote_history_id"] = history.id
    data["tool_calls"] = [serialize_agent_tool_call(row) for row in tool_calls]
    data["findings"] = [serialize_agent_finding(row) for row in findings]
    data["suggestions"] = [serialize_agent_suggestion(row) for row in _suggestions_for_run(db, run.run_id)]
    data["suggestion_events"] = [
        serialize_agent_suggestion_event(row) for row in _suggestion_events_for_run(db, run.run_id)
    ]
    return api_ok(data)


@router.post("/admin/agents/quote-review/daily-runs", summary="create daily quote review agent runs")
async def create_daily_quote_review_runs(
    payload: DailyQuoteReviewRunIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        data = run_daily_quote_review(
            db,
            review_date=payload.review_date,
            actor=current_user.username,
            dry_run=payload.dry_run,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_ok(data)


@router.get("/admin/agents/quote-review/daily-summary", summary="get daily quote review agent summary")
async def get_daily_quote_review_summary(
    review_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        data = build_daily_quote_review_summary(db, review_date=review_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_ok(data)


@router.get("/admin/agents/quote-review/scheduler-runs", summary="get daily quote review scheduler status")
async def get_quote_review_scheduler_runs(
    review_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        data = get_quote_review_scheduler_status(db, review_date=review_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_ok(data)


@router.get("/admin/agents/quote-review/scheduler-runs/history", summary="list daily quote review scheduler history")
async def list_quote_review_scheduler_run_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        rows, total, meta = list_quote_review_scheduler_history(
            db,
            date_from=date_from,
            date_to=date_to,
            status=status_filter,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_page(rows, total=total, page=page, page_size=page_size, **meta)


@router.get("/admin/agents/quote-review/todos", summary="get daily quote review todos")
async def get_quote_review_todos(
    review_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        data = build_quote_review_todo_summary(db, review_date=review_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_ok(data)


@router.get("/admin/agents/quote-review/closure-summary", summary="get quote review SLA and closure summary")
async def get_quote_review_closure_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    _ensure_can_manage_daily_review(current_user)
    try:
        data = build_quote_review_closure_summary(db, date_from=date_from, date_to=date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_ok(data)


@router.get("/admin/agents/runs", summary="list agent runs")
async def list_agent_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    query = db.query(AgentRun)
    if not _can_view_all_agent_runs(current_user):
        query = query.filter(AgentRun.created_by == current_user.username)
    if agent_type:
        query = query.filter(AgentRun.agent_type == agent_type)
    if target_id:
        query = query.filter(AgentRun.target_id == target_id)

    total = query.count()
    rows = (
        query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page(
        [serialize_agent_run(row, include_output=False) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        agent_type=agent_type or "",
    )


@router.get("/admin/agents/runs/{run_id}/llm-explanation", summary="get read-only agent explanation enhancement")
async def get_agent_run_llm_explanation(
    run_id: str,
    mode: Literal["rule", "deepseek"] = Query("rule"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_llm_explanation_enabled()
    run = _get_accessible_run(db, run_id, current_user)
    findings = (
        db.query(AgentFinding)
        .filter(AgentFinding.run_id == run.run_id)
        .order_by(AgentFinding.id.asc())
        .all()
    )
    suggestions = _suggestions_for_run(db, run.run_id)
    if mode == "rule":
        explanation = build_agent_llm_explanation(run, findings=findings, suggestions=suggestions)
        explanation["prompt_version"] = settings.agent_llm_prompt_version
        return api_ok(explanation)

    return api_ok(
        await build_agent_llm_explanation_with_llm(
            run,
            findings=findings,
            suggestions=suggestions,
            username=current_user.username,
            trace_id=get_trace_id(),
        )
    )


@router.get("/admin/agents/runs/{run_id}", summary="查询 Agent 运行详情")
async def get_agent_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    run = _get_accessible_run(db, run_id, current_user)
    data = serialize_agent_run(run)
    data["tool_calls"] = [
        serialize_agent_tool_call(row)
        for row in (
            db.query(AgentToolCall)
            .filter(AgentToolCall.run_id == run.run_id)
            .order_by(AgentToolCall.id.asc())
            .all()
        )
    ]
    data["findings"] = [
        serialize_agent_finding(row)
        for row in (
            db.query(AgentFinding)
            .filter(AgentFinding.run_id == run.run_id)
            .order_by(AgentFinding.id.asc())
            .all()
        )
    ]
    data["suggestions"] = [serialize_agent_suggestion(row) for row in _suggestions_for_run(db, run.run_id)]
    data["suggestion_events"] = [
        serialize_agent_suggestion_event(row) for row in _suggestion_events_for_run(db, run.run_id)
    ]
    return api_ok(data)


@router.get("/admin/agents/runs/{run_id}/tool-calls", summary="查询 Agent 工具调用轨迹")
async def get_agent_tool_calls(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    run = _get_accessible_run(db, run_id, current_user)
    rows = (
        db.query(AgentToolCall)
        .filter(AgentToolCall.run_id == run.run_id)
        .order_by(AgentToolCall.id.asc())
        .all()
    )
    return api_ok([serialize_agent_tool_call(row) for row in rows])


@router.get("/admin/agents/runs/{run_id}/findings", summary="查询 Agent 风险发现")
async def get_agent_findings(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    run = _get_accessible_run(db, run_id, current_user)
    rows = (
        db.query(AgentFinding)
        .filter(AgentFinding.run_id == run.run_id)
        .order_by(AgentFinding.id.asc())
        .all()
    )
    return api_ok([serialize_agent_finding(row) for row in rows])


@router.get("/admin/agents/runs/{run_id}/suggestions", summary="查询 Agent 优化建议")
async def get_agent_suggestions(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    run = _get_accessible_run(db, run_id, current_user)
    return api_ok([serialize_agent_suggestion(row) for row in _suggestions_for_run(db, run.run_id)])


@router.get("/admin/agents/suggestions/pending", summary="list pending agent suggestions")
async def list_pending_agent_suggestions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    review_date: Optional[str] = Query(None),
    status_filter: str = Query("open", alias="status"),
    trigger_source: Optional[str] = Query(DAILY_REVIEW_TRIGGER_SOURCE),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_daily_review_enabled()
    created_by = None if _can_view_all_agent_runs(current_user) else current_user.username
    try:
        rows, total = list_quote_review_suggestions(
            db,
            review_date=review_date,
            status_filter=status_filter,
            trigger_source=trigger_source,
            created_by=created_by,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return api_page(rows, total=total, page=page, page_size=page_size)


@router.get("/admin/agents/suggestions/{suggestion_id}/events", summary="get agent suggestion events")
async def get_agent_suggestion_events(
    suggestion_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    suggestion = _get_accessible_suggestion(db, suggestion_id, current_user)
    rows = (
        db.query(AgentSuggestionEvent)
        .filter(AgentSuggestionEvent.suggestion_id == suggestion.suggestion_id)
        .order_by(AgentSuggestionEvent.id.asc())
        .all()
    )
    return api_ok([serialize_agent_suggestion_event(row) for row in rows])


@router.post("/admin/agents/suggestions/{suggestion_id}/decision", summary="Agent 建议闭环已禁用")
async def decide_agent_suggestion_endpoint(
    suggestion_id: str,
    payload: AgentSuggestionDecisionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="AGENT_SUGGESTION_LOOP_DISABLED")


@router.post("/admin/agents/suggestions/{suggestion_id}/execute", summary="Agent 建议闭环已禁用")
async def execute_agent_suggestion_endpoint(
    suggestion_id: str,
    payload: AgentSuggestionExecuteIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="AGENT_SUGGESTION_LOOP_DISABLED")


@router.post("/admin/agents/suggestions/{suggestion_id}/final-confirm", summary="Agent 建议闭环已禁用")
async def final_confirm_agent_suggestion_endpoint(
    suggestion_id: str,
    payload: AgentSuggestionFinalConfirmIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_agent_enabled()
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="AGENT_SUGGESTION_LOOP_DISABLED")


@router.get("/admin/agents/catalog", summary="查询可用 Agent 清单")
async def get_agent_catalog(current_user: User = Depends(get_current_user)):
    _ensure_agent_enabled()
    can_quote_review = has_any_role(current_user, {"staff", "quote_user", "quote_operator", "admin", "system_admin"})
    return api_ok(
        [
            {
                "agent_type": QUOTE_REVIEW_AGENT_TYPE,
                "name": "报价后审计 Agent",
                "status": "available" if can_quote_review else "forbidden",
                "target_type": "quote_job",
                "mode": "post_audit_only",
                "engine": "rule_graph_v1",
                "llm_mode": settings.agent_llm_provider or "rule",
                "uses_rag": False,
                "uses_memory": False,
                "tools": ["market_price_web_search_v1"],
            }
        ]
    )
