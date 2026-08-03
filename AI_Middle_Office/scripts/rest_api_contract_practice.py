"""In-process REST API contract practice for asynchronous quote jobs.

The script creates a standalone FastAPI application backed by an in-memory
SQLite database. It does not import or mutate the running quotation API,
does not open a network port, and leaves no database file behind.

Covered contracts:
- 202 asynchronous resource creation.
- Idempotency replay and same-key/different-body conflict.
- Authentication, resource ownership, and admin read access.
- Idempotent cancellation and state-guarded retry.
- Stable error codes for 401/404/409/422/429.
- Cursor pagination.
- Trace-ID propagation.
- OpenAPI path and status documentation.

Run from ``AI_Middle_Office``:

    C:/Users/12521/miniconda3/python.exe -m scripts.rest_api_contract_practice
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool


CREATE_RATE_LIMIT = 3


class Base(DeclarativeBase):
    pass


class PracticeQuoteJob(Base):
    __tablename__ = "practice_api_quote_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str] = mapped_column(Text)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PracticeIdempotency(Base):
    __tablename__ = "practice_api_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "owner",
            "operation",
            "idempotency_key",
            name="uq_practice_api_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(96))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class Actor:
    username: str
    role: str


class PracticeApiError(Exception):
    def __init__(
        self,
        *,
        http_status: int,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


class QuoteJobCreate(BaseModel):
    message: str = Field(min_length=3, max_length=500)


app = FastAPI(
    title="REST API Contract Practice",
    version="1.0.0",
)
create_rate_counts: Counter[str] = Counter()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    return response


def _error_body(
    request: Request,
    *,
    http_status: int,
    error_code: str,
    message: str,
    details: Any,
) -> dict[str, Any]:
    return {
        "code": http_status,
        "message": message,
        "data": None,
        "error": {
            "code": error_code,
            "details": details,
        },
        "trace_id": request.state.trace_id,
    }


@app.exception_handler(PracticeApiError)
async def handle_practice_error(request: Request, exc: PracticeApiError):
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_body(
            request,
            http_status=exc.http_status,
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
        ),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            request,
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="VALIDATION_ERROR",
            message="请求参数不符合接口契约",
            details=jsonable_encoder(exc.errors()),
        ),
    )


def get_actor(
    x_user: str | None = Header(default=None, alias="X-User"),
    x_role: str = Header(default="user", alias="X-Role"),
) -> Actor:
    if not x_user:
        raise PracticeApiError(
            http_status=status.HTTP_401_UNAUTHORIZED,
            error_code="AUTH_REQUIRED",
            message="缺少有效身份",
        )
    role = x_role if x_role in {"user", "admin"} else "user"
    return Actor(username=x_user, role=role)


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_job(job: PracticeQuoteJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "owner": job.owner,
        "status": job.status,
        "message": job.message,
        "retry_of": job.retry_of,
        "created_at": job.created_at.isoformat(),
    }


def _success(
    *,
    http_status: int,
    message: str,
    data: Any,
    replayed: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "code": http_status,
            "message": message,
            "data": jsonable_encoder(data),
            "meta": {"replayed": replayed},
        },
        headers=headers or {},
    )


def _resolve_job(
    db: Session,
    *,
    job_id: str,
    actor: Actor,
) -> PracticeQuoteJob:
    job = db.scalar(
        select(PracticeQuoteJob).where(PracticeQuoteJob.job_id == job_id)
    )
    if job is None or (actor.role != "admin" and job.owner != actor.username):
        # Return 404 for non-owners so the API does not reveal resource existence.
        raise PracticeApiError(
            http_status=status.HTTP_404_NOT_FOUND,
            error_code="QUOTE_JOB_NOT_FOUND",
            message="报价任务不存在",
        )
    return job


@app.post(
    "/api/v1/practice/quote-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"description": "相同幂等请求重放"},
        409: {"description": "幂等键冲突"},
        422: {"description": "请求校验失败"},
        429: {"description": "超过创建速率"},
    },
)
def create_quote_job(
    payload: QuoteJobCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    operation = "create_quote_job"
    payload_hash = _request_hash(payload.model_dump())
    existing = db.scalar(
        select(PracticeIdempotency).where(
            PracticeIdempotency.owner == actor.username,
            PracticeIdempotency.operation == operation,
            PracticeIdempotency.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != payload_hash:
            raise PracticeApiError(
                http_status=status.HTTP_409_CONFLICT,
                error_code="IDEMPOTENCY_CONFLICT",
                message="相同幂等键不能对应不同请求内容",
                details={"operation": operation},
            )
        job = db.scalar(
            select(PracticeQuoteJob).where(
                PracticeQuoteJob.job_id == existing.job_id
            )
        )
        return _success(
            http_status=status.HTTP_200_OK,
            message="返回首次请求结果",
            data=_serialize_job(job),
            replayed=True,
            headers={"Idempotency-Replayed": "true"},
        )

    if create_rate_counts[actor.username] >= CREATE_RATE_LIMIT:
        raise PracticeApiError(
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code="QUOTE_CREATE_RATE_LIMITED",
            message="报价任务创建过于频繁",
            details={"limit": CREATE_RATE_LIMIT, "window": "practice-run"},
            headers={"Retry-After": "30"},
        )

    create_rate_counts[actor.username] += 1
    now = datetime.now(timezone.utc)
    job = PracticeQuoteJob(
        job_id=str(uuid.uuid4()),
        owner=actor.username,
        status="queued",
        message=payload.message,
        retry_of=None,
        created_at=now,
    )
    db.add(job)
    db.add(
        PracticeIdempotency(
            owner=actor.username,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            job_id=job.job_id,
            created_at=now,
        )
    )
    db.commit()
    return _success(
        http_status=status.HTTP_202_ACCEPTED,
        message="报价任务已接受",
        data=_serialize_job(job),
    )


@app.get("/api/v1/practice/quote-jobs/{job_id}")
def get_quote_job(
    job_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    job = _resolve_job(db, job_id=job_id, actor=actor)
    return _success(
        http_status=status.HTTP_200_OK,
        message="ok",
        data=_serialize_job(job),
    )


@app.get("/api/v1/practice/quote-jobs")
def list_quote_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: int | None = Query(default=None, ge=1),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    statement = select(PracticeQuoteJob)
    if actor.role != "admin":
        statement = statement.where(PracticeQuoteJob.owner == actor.username)
    if cursor is not None:
        statement = statement.where(PracticeQuoteJob.id < cursor)
    jobs = list(
        db.scalars(statement.order_by(PracticeQuoteJob.id.desc()).limit(limit))
    )
    next_cursor = jobs[-1].id if len(jobs) == limit else None
    return _success(
        http_status=status.HTTP_200_OK,
        message="ok",
        data={
            "items": [_serialize_job(job) for job in jobs],
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        },
    )


@app.post("/api/v1/practice/quote-jobs/{job_id}/cancel")
def cancel_quote_job(
    job_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    job = _resolve_job(db, job_id=job_id, actor=actor)
    if job.status == "cancelled":
        return _success(
            http_status=status.HTTP_200_OK,
            message="任务已经取消",
            data=_serialize_job(job),
            replayed=True,
        )
    if job.status not in {"queued", "running"}:
        raise PracticeApiError(
            http_status=status.HTTP_409_CONFLICT,
            error_code="QUOTE_JOB_STATE_CONFLICT",
            message="当前状态不允许取消",
            details={"current_status": job.status},
        )
    job.status = "cancelled"
    db.commit()
    return _success(
        http_status=status.HTTP_200_OK,
        message="任务已取消",
        data=_serialize_job(job),
    )


@app.post(
    "/api/v1/practice/quote-jobs/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_quote_job(
    job_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    original = _resolve_job(db, job_id=job_id, actor=actor)
    if original.status not in {"failed", "cancelled", "timed_out"}:
        raise PracticeApiError(
            http_status=status.HTTP_409_CONFLICT,
            error_code="QUOTE_JOB_STATE_CONFLICT",
            message="只有失败、取消或超时任务可以重试",
            details={"current_status": original.status},
        )

    operation = f"retry_quote_job:{original.job_id}"
    payload_hash = _request_hash({"job_id": original.job_id})
    existing = db.scalar(
        select(PracticeIdempotency).where(
            PracticeIdempotency.owner == actor.username,
            PracticeIdempotency.operation == operation,
            PracticeIdempotency.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        retry_job = db.scalar(
            select(PracticeQuoteJob).where(
                PracticeQuoteJob.job_id == existing.job_id
            )
        )
        return _success(
            http_status=status.HTTP_200_OK,
            message="返回首次重试结果",
            data=_serialize_job(retry_job),
            replayed=True,
        )

    now = datetime.now(timezone.utc)
    retry_job = PracticeQuoteJob(
        job_id=str(uuid.uuid4()),
        owner=original.owner,
        status="queued",
        message=original.message,
        retry_of=original.job_id,
        created_at=now,
    )
    db.add(retry_job)
    db.add(
        PracticeIdempotency(
            owner=actor.username,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            job_id=retry_job.job_id,
            created_at=now,
        )
    )
    db.commit()
    return _success(
        http_status=status.HTTP_202_ACCEPTED,
        message="重试任务已接受",
        data=_serialize_job(retry_job),
    )


def _expect(response, expected_status: int, label: str) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(
            f"{label}: expected {expected_status}, got {response.status_code}: "
            f"{response.text}"
        )
    return response.json()


def main() -> None:
    client = TestClient(app)
    checks: list[dict[str, Any]] = []

    def record(name: str, response, expected_status: int) -> dict[str, Any]:
        body = _expect(response, expected_status, name)
        checks.append(
            {
                "name": name,
                "status": response.status_code,
                "error_code": (body.get("error") or {}).get("code"),
            }
        )
        return body

    record(
        "missing authentication",
        client.post(
            "/api/v1/practice/quote-jobs",
            headers={"Idempotency-Key": "practice-auth-001"},
            json={"message": "厨房墙砖 10 平方米"},
        ),
        401,
    )
    validation = record(
        "schema validation",
        client.post(
            "/api/v1/practice/quote-jobs",
            headers={
                "X-User": "alice",
                "Idempotency-Key": "practice-validation-001",
            },
            json={"message": "x"},
        ),
        422,
    )
    assert validation["error"]["code"] == "VALIDATION_ERROR"

    create_headers = {
        "X-User": "alice",
        "Idempotency-Key": "practice-create-001",
        "X-Trace-ID": "practice-trace-001",
    }
    created_response = client.post(
        "/api/v1/practice/quote-jobs",
        headers=create_headers,
        json={"message": "厨房墙砖 10 平方米"},
    )
    created = record("async create", created_response, 202)
    job_id = created["data"]["job_id"]
    assert created["data"]["status"] == "queued"
    assert created_response.headers["X-Trace-ID"] == "practice-trace-001"

    replay_response = client.post(
        "/api/v1/practice/quote-jobs",
        headers=create_headers,
        json={"message": "厨房墙砖 10 平方米"},
    )
    replay = record("idempotent replay", replay_response, 200)
    assert replay["data"]["job_id"] == job_id
    assert replay["meta"]["replayed"] is True
    assert replay_response.headers["Idempotency-Replayed"] == "true"

    conflict = record(
        "idempotency conflict",
        client.post(
            "/api/v1/practice/quote-jobs",
            headers=create_headers,
            json={"message": "相同 Key 但不同的请求内容"},
        ),
        409,
    )
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    record(
        "owner reads resource",
        client.get(
            f"/api/v1/practice/quote-jobs/{job_id}",
            headers={"X-User": "alice"},
        ),
        200,
    )
    hidden = record(
        "other user cannot discover resource",
        client.get(
            f"/api/v1/practice/quote-jobs/{job_id}",
            headers={"X-User": "bob"},
        ),
        404,
    )
    assert hidden["error"]["code"] == "QUOTE_JOB_NOT_FOUND"
    record(
        "admin reads resource",
        client.get(
            f"/api/v1/practice/quote-jobs/{job_id}",
            headers={"X-User": "root", "X-Role": "admin"},
        ),
        200,
    )

    first_cancel = record(
        "first cancel",
        client.post(
            f"/api/v1/practice/quote-jobs/{job_id}/cancel",
            headers={"X-User": "alice"},
        ),
        200,
    )
    second_cancel = record(
        "repeated cancel",
        client.post(
            f"/api/v1/practice/quote-jobs/{job_id}/cancel",
            headers={"X-User": "alice"},
        ),
        200,
    )
    assert first_cancel["data"]["status"] == "cancelled"
    assert second_cancel["meta"]["replayed"] is True

    retry_headers = {
        "X-User": "alice",
        "Idempotency-Key": "practice-retry-001",
    }
    retry_created = record(
        "retry cancelled job",
        client.post(
            f"/api/v1/practice/quote-jobs/{job_id}/retry",
            headers=retry_headers,
        ),
        202,
    )
    retry_job_id = retry_created["data"]["job_id"]
    assert retry_created["data"]["retry_of"] == job_id

    retry_replay = record(
        "retry idempotent replay",
        client.post(
            f"/api/v1/practice/quote-jobs/{job_id}/retry",
            headers=retry_headers,
        ),
        200,
    )
    assert retry_replay["data"]["job_id"] == retry_job_id

    invalid_retry = record(
        "retry queued job rejected",
        client.post(
            f"/api/v1/practice/quote-jobs/{retry_job_id}/retry",
            headers={
                "X-User": "alice",
                "Idempotency-Key": "practice-retry-queued-001",
            },
        ),
        409,
    )
    assert invalid_retry["error"]["code"] == "QUOTE_JOB_STATE_CONFLICT"

    first_page = record(
        "cursor page one",
        client.get(
            "/api/v1/practice/quote-jobs?limit=1",
            headers={"X-User": "alice"},
        ),
        200,
    )
    assert len(first_page["data"]["items"]) == 1
    assert first_page["data"]["has_more"] is True
    second_page = record(
        "cursor page two",
        client.get(
            "/api/v1/practice/quote-jobs",
            params={
                "limit": 1,
                "cursor": first_page["data"]["next_cursor"],
            },
            headers={"X-User": "alice"},
        ),
        200,
    )
    assert len(second_page["data"]["items"]) == 1
    assert (
        first_page["data"]["items"][0]["job_id"]
        != second_page["data"]["items"][0]["job_id"]
    )

    rate_statuses: list[int] = []
    for index in range(4):
        response = client.post(
            "/api/v1/practice/quote-jobs",
            headers={
                "X-User": "rate-user",
                "Idempotency-Key": f"practice-rate-{index:03d}",
            },
            json={"message": f"限流练习任务 {index}"},
        )
        rate_statuses.append(response.status_code)
    assert rate_statuses == [202, 202, 202, 429]
    rate_limited = client.post(
        "/api/v1/practice/quote-jobs",
        headers={
            "X-User": "rate-user",
            "Idempotency-Key": "practice-rate-999",
        },
        json={"message": "再次触发限流"},
    )
    body = record("rate limited", rate_limited, 429)
    assert body["error"]["code"] == "QUOTE_CREATE_RATE_LIMITED"
    assert rate_limited.headers["Retry-After"] == "30"

    openapi = client.get("/openapi.json")
    _expect(openapi, 200, "openapi")
    schema = openapi.json()
    create_operation = schema["paths"]["/api/v1/practice/quote-jobs"]["post"]
    documented_responses = sorted(create_operation["responses"].keys())
    assert {"200", "202", "409", "422", "429"}.issubset(
        set(documented_responses)
    )

    with SessionLocal() as db:
        job_count = len(list(db.scalars(select(PracticeQuoteJob))))
        idempotency_count = len(list(db.scalars(select(PracticeIdempotency))))

    report = {
        "environment": {
            "network_port_opened": False,
            "database": "in-memory SQLite",
            "persistent_files_created": False,
        },
        "checks": checks,
        "idempotency": {
            "first_status": 202,
            "replay_status": 200,
            "same_job_id": replay["data"]["job_id"] == job_id,
            "conflict_status": 409,
        },
        "authorization": {
            "owner_status": 200,
            "other_user_status": 404,
            "admin_status": 200,
        },
        "state_machine": {
            "first_cancel_status": first_cancel["data"]["status"],
            "repeated_cancel_replayed": second_cancel["meta"]["replayed"],
            "retry_status": retry_created["data"]["status"],
            "retry_of": retry_created["data"]["retry_of"],
            "queued_retry_rejected": invalid_retry["error"]["code"],
        },
        "pagination": {
            "first_page_items": len(first_page["data"]["items"]),
            "second_page_items": len(second_page["data"]["items"]),
            "no_duplicate_between_pages": (
                first_page["data"]["items"][0]["job_id"]
                != second_page["data"]["items"][0]["job_id"]
            ),
        },
        "rate_limit": {
            "statuses": rate_statuses,
            "retry_after_seconds": rate_limited.headers["Retry-After"],
        },
        "trace": {
            "request_trace_id": "practice-trace-001",
            "response_trace_id": created_response.headers["X-Trace-ID"],
        },
        "openapi": {
            "documented_create_responses": documented_responses,
            "paths": sorted(schema["paths"].keys()),
        },
        "database_rows": {
            "quote_jobs": job_count,
            "idempotency_records": idempotency_count,
        },
        "all_assertions_passed": True,
    }
    client.close()
    engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
