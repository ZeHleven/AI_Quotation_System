from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_ok, api_page
from app.dependencies import require_admin
from app.models.knowledge_candidate import KnowledgeCandidate
from app.models.user import User
from app.schemas.knowledge_candidate import (
    KnowledgeCandidateApproveRequest,
    KnowledgeCandidateBuildRequest,
    KnowledgeCandidateRejectRequest,
)
from app.services.knowledge_candidates import (
    approve_candidate,
    build_knowledge_candidates,
    candidate_to_dict,
    rag_trace_insights,
    reject_candidate,
    summarize_candidates,
)
from app.services.material_sync import sync_materials_to_rag


router = APIRouter()


@router.post("/admin/knowledge_candidates/build", summary="Build knowledge candidates from quote feedback")
def build_candidates(
    payload: Optional[KnowledgeCandidateBuildRequest] = Body(default=None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = payload or KnowledgeCandidateBuildRequest()
    return api_ok(build_knowledge_candidates(db, payload))


@router.get("/admin/knowledge_candidates/summary", summary="Knowledge candidate summary")
def get_candidate_summary(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return api_ok(summarize_candidates(db))


@router.get("/admin/knowledge_candidates/rag_insights", summary="RAG trace quality insights")
def get_rag_trace_insights(
    days: Optional[int] = Query(None, ge=1, le=365),
    min_count: int = Query(1, ge=1, le=100),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return api_ok(rag_trace_insights(db, days=days, min_count=min_count, limit=limit))


@router.get("/admin/knowledge_candidates", summary="List knowledge candidates")
def list_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    candidate_kind: Optional[str] = None,
    source_type: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeCandidate)
    if status:
        query = query.filter(KnowledgeCandidate.status == status)
    if candidate_kind:
        query = query.filter(KnowledgeCandidate.candidate_kind == candidate_kind)
    if source_type:
        query = query.filter(KnowledgeCandidate.source_type == source_type)
    total = query.count()
    rows = (
        query.order_by(KnowledgeCandidate.created_at.desc(), KnowledgeCandidate.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return api_page([candidate_to_dict(item) for item in rows], total=total, page=page, page_size=page_size)


@router.get("/admin/knowledge_candidates/{candidate_id}", summary="Knowledge candidate detail")
def get_candidate(
    candidate_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    candidate = db.query(KnowledgeCandidate).filter(KnowledgeCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="knowledge candidate not found")
    return api_ok(candidate_to_dict(candidate))


@router.post("/admin/knowledge_candidates/{candidate_id}/approve", summary="Approve knowledge candidate")
async def approve_knowledge_candidate(
    candidate_id: int,
    payload: Optional[KnowledgeCandidateApproveRequest] = Body(default=None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = payload or KnowledgeCandidateApproveRequest()
    candidate = db.query(KnowledgeCandidate).filter(KnowledgeCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="knowledge candidate not found")
    try:
        result = approve_candidate(db, candidate=candidate, username=current_user.username, request=payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sync = await sync_materials_to_rag(db, current_user.username)
    result["sync_triggered"] = True
    result["sync_status"] = "success" if sync["success"] else "failed"
    result["sync_error"] = sync["error"]
    return api_ok(result)


@router.post("/admin/knowledge_candidates/{candidate_id}/reject", summary="Reject knowledge candidate")
def reject_knowledge_candidate(
    candidate_id: int,
    payload: Optional[KnowledgeCandidateRejectRequest] = Body(default=None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payload = payload or KnowledgeCandidateRejectRequest()
    candidate = db.query(KnowledgeCandidate).filter(KnowledgeCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="knowledge candidate not found")
    try:
        return api_ok(reject_candidate(db, candidate=candidate, username=current_user.username, review_note=payload.review_note))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
