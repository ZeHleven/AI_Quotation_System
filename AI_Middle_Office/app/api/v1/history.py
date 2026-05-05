from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.responses import api_page
from app.dependencies import get_current_user
from app.models.quote_history import QuoteHistory
from app.models.user import User


router = APIRouter()


@router.get("/history", summary="查询报价历史（本人；admin 可查全部）")
async def get_history(
    page: int = 1,
    page_size: int = 20,
    username: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(QuoteHistory)
    if current_user.role != "admin":
        query = query.filter(QuoteHistory.username == current_user.username)
    elif username:
        query = query.filter(QuoteHistory.username == username)

    total = query.count()
    records = (
        query.order_by(QuoteHistory.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return api_page([
        {
            "id": r.id,
            "username": r.username,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            "total_amount": r.total_amount,
            "item_count": r.item_count,
            "payload_json": r.payload_json,
        } for r in records
    ], total=total, page=page, page_size=page_size)
