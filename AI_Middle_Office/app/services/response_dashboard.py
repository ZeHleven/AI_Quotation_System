from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.client_inquiry import DIRECTION_INBOUND, ClientInquiry
from app.models.user import User


CN_TZ = ZoneInfo("Asia/Shanghai")
VALID_RANGES = {"today", "week", "month", "last_30_days"}
LOW_SAMPLE_THRESHOLD = 5


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


def _db_time(value: datetime) -> datetime:
    return value.astimezone(CN_TZ).replace(tzinfo=None)


def _format_dt(value: datetime | None) -> str | None:
    local_value = _to_local(value)
    if not local_value:
        return None
    return local_value.strftime("%Y-%m-%d %H:%M:%S")


def _range_bounds(range_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    now = now or _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_name == "today":
        start = today_start
    elif range_name == "week":
        start = today_start - timedelta(days=today_start.weekday())
    elif range_name == "month":
        start = today_start.replace(day=1)
    else:
        start = today_start - timedelta(days=29)
    return start, now


def _duration_minutes(inquiry: ClientInquiry) -> float | None:
    if inquiry.time_source == "default":
        return None
    inquiry_time = _to_local(inquiry.inquiry_time)
    response_time = _to_local(inquiry.first_response_time)
    if not inquiry_time or not response_time:
        return None
    delta_seconds = (response_time - inquiry_time).total_seconds()
    if delta_seconds < 0:
        return None
    return round(delta_seconds / 60, 4)


def _avg_minutes(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _source_label(value: str | None) -> str:
    return value or "未标注来源"


def _group_summary(items: list[ClientInquiry], durations: dict[str, float], *, key_fn) -> list[dict]:
    grouped: dict[str, list[ClientInquiry]] = defaultdict(list)
    for item in items:
        grouped[key_fn(item)].append(item)
    result = []
    for key, rows in sorted(grouped.items(), key=lambda pair: pair[0]):
        row_durations = [durations[row.inquiry_id] for row in rows if row.inquiry_id in durations]
        result.append(
            {
                "key": key,
                "sample_count_total": len(rows),
                "sample_count_in_avg": len(row_durations),
                "sample_count_excluded_default_time": sum(1 for row in rows if row.time_source == "default"),
                "avg_first_response_minutes": _avg_minutes(row_durations),
            }
        )
    return result


def build_response_speed_dashboard(db: Session, *, range_name: str = "last_30_days") -> dict:
    range_name = range_name if range_name in VALID_RANGES else "last_30_days"
    start, end = _range_bounds(range_name)
    inquiries = (
        db.query(ClientInquiry)
        .filter(
            ClientInquiry.direction == DIRECTION_INBOUND,
            ClientInquiry.inquiry_time >= _db_time(start),
            ClientInquiry.inquiry_time <= _db_time(end),
        )
        .order_by(ClientInquiry.inquiry_time.asc(), ClientInquiry.id.asc())
        .all()
    )
    responder_ids = {item.responder_id for item in inquiries if item.responder_id}
    users = {}
    if responder_ids:
        users = {user.id: user.username for user in db.query(User).filter(User.id.in_(responder_ids)).all()}

    durations = {
        inquiry.inquiry_id: duration
        for inquiry in inquiries
        if (duration := _duration_minutes(inquiry)) is not None
    }
    included = list(durations.values())
    sla_minutes = max(1, int(settings.response_sla_minutes or 30))
    sla_pass_count = sum(1 for value in included if value <= sla_minutes)
    overdue_count = sum(1 for value in included if value > sla_minutes)

    by_source = _group_summary(inquiries, durations, key_fn=lambda item: _source_label(item.source))
    by_responder = _group_summary(
        inquiries,
        durations,
        key_fn=lambda item: users.get(item.responder_id, f"user:{item.responder_id}"),
    )
    for item in by_responder:
        matching_user_id = next((user_id for user_id, username in users.items() if username == item["key"]), None)
        item["responder_id"] = matching_user_id
        item["username"] = item.pop("key")
    for item in by_source:
        item["source"] = item.pop("key")

    return {
        "timezone": "Asia/Shanghai",
        "range": range_name,
        "range_start": _format_dt(start),
        "range_end": _format_dt(end),
        "sla_minutes": sla_minutes,
        "sample_count_total": len(inquiries),
        "sample_count_in_avg": len(included),
        "sample_count_excluded_default_time": sum(1 for item in inquiries if item.time_source == "default"),
        "avg_first_response_minutes": _avg_minutes(included),
        "sla_pass_rate": _rate(sla_pass_count, len(included)),
        "overdue_count": overdue_count,
        "by_source": by_source,
        "by_responder": by_responder,
        "empty_state": len(inquiries) == 0,
        "low_sample_warning": 0 < len(inquiries) < LOW_SAMPLE_THRESHOLD,
    }
