"""Default-off Conversation API for the isolated bid-assessment Pure Agent."""

from __future__ import annotations

from typing import Annotated, Any
import asyncio
import hashlib
import logging
import threading
import time
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.agents.bid_assessment_pure.conversation_contracts import (
    AgentTaskView,
    CancelTaskRequest,
    ConversationMessagePage,
    ConversationView,
    CreateConversationRequest,
    MessageAdmissionView,
    PureAgentApiError,
    PureAgentApiErrorDetail,
    PureAgentApiSuccess,
    SlotSubmissionView,
    SubmitSlotInputRequest,
    SubmitUserMessageRequest,
    TaskCancellationView,
)
from app.agents.bid_assessment_pure.conversation_runtime import (
    ConversationApiRuntime,
    SlotSubmissionCommand,
)
from app.agents.bid_assessment_pure.event_contracts import SafeAgentEventPage
from app.agents.bid_assessment_pure.event_runtime import (
    SafeEventCursorRejected,
    SafeEventProjector,
    format_safe_event_sse,
    resolve_after_version,
)
from app.agents.bid_assessment_pure.local_bootstrap import (
    LocalBootstrapStatus,
    LocalPureAgentRuntimeAdapters,
    LocalRuntimeBootstrapRequest,
    LocalRuntimeBootstrapResult,
    bootstrap_local_pure_agent_runtime,
)
from app.agents.bid_assessment_pure.local_preflight import (
    LocalPreflightReport,
    PureAgentRuntimeStatusView,
)
from app.agents.bid_assessment_pure.repository import (
    PureAgentConflict,
    PureAgentFenceRejected,
    PureAgentNotFound,
    PureAgentPersistenceError,
    PureAgentRepository,
)
from app.agents.bid_assessment_pure.runtime_config import (
    PureAgentDisabledError,
    PureAgentFeatureConfig,
)
from app.agents.bid_assessment_pure.runtime_controller import (
    ContinuationTokenService,
    DisabledRuntimeDispatcher,
    RuntimeDispatchPort,
    RuntimeWakeReason,
    RuntimeWakeup,
)
from app.agents.bid_assessment_pure.slot_validation import SlotValidatorRegistry
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models.bid_assessment import (
    BidAssessment,
    BidDocumentManifest,
    BidManifestDocument,
)
from app.models.user import User
from app.services.rbac import has_admin_role


logger = logging.getLogger(__name__)

_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$"
_CONVERSATION_NAMESPACE = uuid.UUID("bc6a43ac-4caa-42bd-833a-c1572886f1fe")
_SSE_POLL_SECONDS = 1.0
_SSE_KEEPALIVE_SECONDS = 15.0
_slot_validators = SlotValidatorRegistry()
_runtime_dispatcher: RuntimeDispatchPort = DisabledRuntimeDispatcher()
_local_bootstrap_result: LocalRuntimeBootstrapResult | None = None
_local_preflight_report: LocalPreflightReport | None = None
_local_bootstrap_lock = threading.RLock()


def _require_feature_enabled() -> None:
    try:
        PureAgentFeatureConfig.from_application_settings(settings).require_enabled()
    except PureAgentDisabledError as exc:
        raise HTTPException(status_code=404, detail="resource not found") from exc


router = APIRouter(
    prefix="/bid-assessment-pure-agent",
    dependencies=[Depends(_require_feature_enabled)],
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=_IDEMPOTENCY_PATTERN,
    ),
]
ApiReference = Annotated[
    str,
    Path(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$",
    ),
]


def get_pure_agent_slot_validator_registry() -> SlotValidatorRegistry:
    """Return the registry installed by the explicit local Bootstrap."""

    with _local_bootstrap_lock:
        return _slot_validators


def configure_pure_agent_slot_validator_registry(
    registry: SlotValidatorRegistry,
) -> None:
    """Install one explicit Slot registry without enabling Runtime authority."""

    if not isinstance(registry, SlotValidatorRegistry):
        raise TypeError("Pure Agent Slot validator registry is invalid")
    global _slot_validators, _local_bootstrap_result
    with _local_bootstrap_lock:
        _slot_validators = registry
        _local_bootstrap_result = None


def configure_pure_agent_runtime_dispatcher(dispatcher: RuntimeDispatchPort) -> None:
    """Reset the API dispatcher; ready authority must use the full Bootstrap."""

    if not callable(getattr(dispatcher, "dispatch", None)):
        raise TypeError("Pure Agent Runtime dispatcher is invalid")
    if bool(getattr(dispatcher, "available", False)):
        raise RuntimeError(
            "ready Pure Agent dispatcher requires bootstrap_pure_agent_local_runtime"
        )
    global _runtime_dispatcher, _local_bootstrap_result
    with _local_bootstrap_lock:
        _runtime_dispatcher = dispatcher
        _local_bootstrap_result = None


def get_pure_agent_runtime_dispatcher() -> RuntimeDispatchPort:
    with _local_bootstrap_lock:
        return _runtime_dispatcher


def get_pure_agent_local_bootstrap_result() -> LocalRuntimeBootstrapResult | None:
    """Expose only the last in-process Bootstrap receipt, never its adapters."""

    with _local_bootstrap_lock:
        return _local_bootstrap_result


def configure_pure_agent_local_preflight_report(
    report: LocalPreflightReport | None,
) -> None:
    """Publish one safe local Preflight receipt without granting authority."""

    if report is not None and not isinstance(report, LocalPreflightReport):
        raise TypeError("Pure Agent local Preflight report is invalid")
    global _local_preflight_report
    with _local_bootstrap_lock:
        _local_preflight_report = report


def get_pure_agent_local_preflight_report() -> LocalPreflightReport | None:
    with _local_bootstrap_lock:
        return _local_preflight_report


def bootstrap_pure_agent_local_runtime(
    *,
    request: LocalRuntimeBootstrapRequest,
    adapters: LocalPureAgentRuntimeAdapters,
    max_pulses_per_dispatch: int = 64,
) -> LocalRuntimeBootstrapResult:
    """The sole application-level, manually invoked local Runtime installer.

    This function is deliberately not called by module import or FastAPI
    lifespan.  It only composes dependencies; the first Agent pulse still
    requires a later committed Conversation wakeup.
    """

    global _local_bootstrap_result
    with _local_bootstrap_lock:
        _local_bootstrap_result = None

        def install(dispatcher: RuntimeDispatchPort) -> None:
            global _runtime_dispatcher, _slot_validators
            _runtime_dispatcher = dispatcher
            _slot_validators = (
                adapters.slot_validators
                if dispatcher.available
                else SlotValidatorRegistry()
            )

        result = bootstrap_local_pure_agent_runtime(
            request=request,
            settings=settings,
            session_factory=SessionLocal,
            continuation_secret=getattr(
                settings,
                "bid_assessment_pure_agent_continuation_secret",
                "",
            ),
            adapters=adapters,
            installer=install,
            max_pulses_per_dispatch=max_pulses_per_dispatch,
        )
        _local_bootstrap_result = result
        return result


def _runtime_available(dispatcher: RuntimeDispatchPort | None = None) -> bool:
    active = dispatcher or get_pure_agent_runtime_dispatcher()
    config = PureAgentFeatureConfig.from_application_settings(settings)
    return bool(
        config.enabled
        and config.runtime_enabled
        and active.available
        and _continuation_tokens().available
    )


def _runtime_status_view() -> PureAgentRuntimeStatusView:
    config = PureAgentFeatureConfig.from_application_settings(settings)
    with _local_bootstrap_lock:
        preflight = _local_preflight_report
        bootstrap = _local_bootstrap_result
        runtime_available = _runtime_available(_runtime_dispatcher)

    reasons: list[str] = []
    if preflight is not None:
        reasons.extend(preflight.failed_codes)
    if bootstrap is not None:
        reasons.extend(bootstrap.reason_codes)

    if runtime_available:
        startup_status = "ready"
    elif preflight is not None and not preflight.ready:
        startup_status = "preflight_blocked"
    elif bootstrap is None:
        startup_status = "not_configured"
    elif bootstrap.status is LocalBootstrapStatus.INCOMPLETE:
        startup_status = "bootstrap_incomplete"
    else:
        startup_status = "bootstrap_disabled"

    return PureAgentRuntimeStatusView(
        surface_enabled=config.enabled,
        execution_switch_enabled=config.runtime_enabled,
        provider_boundary_v2_enabled=config.provider_boundary_v2_enabled,
        provider_boundary_mode=config.provider_boundary_mode,
        preflight_ready=bool(preflight and preflight.ready),
        runtime_available=runtime_available,
        startup_status=startup_status,
        reason_codes=tuple(dict.fromkeys(reasons))[:32],
    )


def _continuation_tokens() -> ContinuationTokenService:
    return ContinuationTokenService(
        getattr(settings, "bid_assessment_pure_agent_continuation_secret", "")
    )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", ""))[:80]


def _ok(data: Any, request: Request) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "ok",
        "data": data,
        "error": None,
        "request_id": _request_id(request),
    }


def _error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    guidance: str | None = None,
) -> JSONResponse:
    payload = PureAgentApiError(
        code=status_code,
        message=message,
        error=PureAgentApiErrorDetail(
            code=code,
            retryable=retryable,
            guidance=guidance,
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


def _domain_error(request: Request, exc: PureAgentPersistenceError) -> JSONResponse:
    if isinstance(exc, PureAgentNotFound):
        return _error(
            request,
            status_code=404,
            code="PURE_AGENT_RESOURCE_NOT_FOUND",
            message="对话资源不存在或不可见",
            retryable=False,
        )
    if isinstance(exc, (PureAgentConflict, PureAgentFenceRejected)):
        return _error(
            request,
            status_code=409,
            code="PURE_AGENT_STATE_CONFLICT",
            message="对话状态已变化，当前操作未生效",
            retryable=True,
            guidance="请刷新当前状态后重试。",
        )
    return _error(
        request,
        status_code=503,
        code="PURE_AGENT_STORAGE_UNAVAILABLE",
        message="对话服务暂时不可用",
        retryable=True,
        guidance="请稍后重试。",
    )


def _tenant_ref(user: User) -> str:
    return f"principal:{int(user.id)}"


def _actor_ref(user: User) -> str:
    return f"user:{int(user.id)}"


def _authorization_snapshot_ref(user: User) -> str:
    return f"user-auth:{int(user.id)}:v{int(user.role_version or 1)}"


def _stable_ref(namespace: str, *, owner_id: int, idempotency_key: str) -> str:
    name = f"{namespace}:{int(owner_id)}:{idempotency_key}"
    return str(uuid.uuid5(_CONVERSATION_NAMESPACE, name))


def _event_ref(namespace: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def _assessment_visible(db: Session, *, assessment_ref: str, user: User) -> bool:
    owner = (
        db.query(BidAssessment.created_by)
        .filter(BidAssessment.id == assessment_ref)
        .one_or_none()
    )
    return owner is not None and (
        int(owner[0]) == int(user.id) or has_admin_role(user)
    )


def _validate_resource_references(
    db: Session,
    *,
    conversation: Any,
    request_body: SubmitUserMessageRequest,
) -> None:
    assessment_refs = {
        item.ref for item in request_body.resources if item.kind == "assessment"
    }
    document_version_refs = {
        item.ref
        for item in request_body.resources
        if item.kind == "bid_document_version"
    }
    if assessment_refs and assessment_refs != {conversation.assessment_id}:
        raise PureAgentNotFound("assessment reference was not found")
    if not document_version_refs:
        return
    if conversation.assessment_id is None:
        raise PureAgentNotFound("document version reference was not found")
    assessment = (
        db.query(BidAssessment.current_manifest_id)
        .filter(BidAssessment.id == conversation.assessment_id)
        .one_or_none()
    )
    if assessment is None or assessment[0] is None:
        raise PureAgentNotFound("document version reference was not found")
    visible_rows = (
        db.query(BidManifestDocument.document_version_id)
        .join(
            BidDocumentManifest,
            BidDocumentManifest.id == BidManifestDocument.manifest_id,
        )
        .filter(
            BidDocumentManifest.assessment_id == conversation.assessment_id,
            BidDocumentManifest.id == assessment[0],
            BidManifestDocument.document_version_id.in_(document_version_refs),
        )
        .all()
    )
    if {row[0] for row in visible_rows} != document_version_refs:
        raise PureAgentNotFound("document version reference was not found")


def _runtime(
    db: Session,
    dispatcher: RuntimeDispatchPort | None = None,
) -> ConversationApiRuntime:
    return ConversationApiRuntime(
        PureAgentRepository(db),
        slot_validators=get_pure_agent_slot_validator_registry(),
        runtime_available=_runtime_available(dispatcher),
        continuation_tokens=_continuation_tokens(),
    )


def _schedule_runtime(
    *,
    background_tasks: BackgroundTasks,
    dispatcher: RuntimeDispatchPort,
    task_ref: str,
    conversation_ref: str,
    state_version: int,
    reason: RuntimeWakeReason,
    seed: str,
) -> None:
    if not _runtime_available(dispatcher):
        return
    background_tasks.add_task(
        dispatcher.dispatch,
        RuntimeWakeup.build(
            task_ref=task_ref,
            conversation_ref=conversation_ref,
            observed_state_version=state_version,
            reason=reason,
            seed=seed,
        ),
    )


@router.get(
    "/runtime-status",
    response_model=PureAgentApiSuccess[PureAgentRuntimeStatusView],
    summary="读取本地 Pure Agent 安全启动状态",
    operation_id="getBidAssessmentPureAgentRuntimeStatus",
)
def get_runtime_status_endpoint(
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return safe status only; never expose paths, secrets, or adapters."""

    return _ok(_runtime_status_view(), request)


@router.post(
    "/conversations",
    response_model=PureAgentApiSuccess[ConversationView],
    summary="创建 Pure Agent 对话",
    operation_id="createBidAssessmentPureAgentConversation",
)
def create_conversation_endpoint(
    body: CreateConversationRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        if body.assessment_ref is not None and not _assessment_visible(
            db,
            assessment_ref=body.assessment_ref,
            user=current_user,
        ):
            raise PureAgentNotFound("assessment was not found")
        repository = PureAgentRepository(db)
        row = repository.create_conversation(
            owner_id=int(current_user.id),
            tenant_ref=_tenant_ref(current_user),
            assessment_id=body.assessment_ref,
            title=body.title,
            conversation_id=_stable_ref(
                "conversation",
                owner_id=int(current_user.id),
                idempotency_key=idempotency_key,
            ),
        )
        view = ConversationApiRuntime(
            repository,
            slot_validators=get_pure_agent_slot_validator_registry(),
            runtime_available=_runtime_available(),
            continuation_tokens=_continuation_tokens(),
        ).conversation_view(row)
        db.commit()
        return _ok(view, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_conversation_create_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.get(
    "/conversations/{conversation_ref}",
    response_model=PureAgentApiSuccess[ConversationView],
    summary="读取 Pure Agent 对话状态",
    operation_id="getBidAssessmentPureAgentConversation",
)
def get_conversation_endpoint(
    conversation_ref: ApiReference,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        runtime = _runtime(db)
        row = runtime.repository.load_owned_conversation(
            conversation_ref,
            owner_id=int(current_user.id),
        )
        return _ok(runtime.conversation_view(row), request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_conversation_read_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.get(
    "/conversations/{conversation_ref}/messages",
    response_model=PureAgentApiSuccess[ConversationMessagePage],
    summary="读取 Pure Agent 对话消息",
    operation_id="listBidAssessmentPureAgentMessages",
)
def list_messages_endpoint(
    conversation_ref: ApiReference,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        runtime = _runtime(db)
        runtime.repository.load_owned_conversation(
            conversation_ref,
            owner_id=int(current_user.id),
        )
        page = runtime.message_page(
            conversation_ref=conversation_ref,
            after_sequence=after_sequence,
            limit=limit,
        )
        return _ok(page, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_message_list_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.post(
    "/conversations/{conversation_ref}/messages",
    response_model=PureAgentApiSuccess[MessageAdmissionView],
    summary="提交开放用户消息",
    operation_id="submitBidAssessmentPureAgentMessage",
)
def submit_message_endpoint(
    conversation_ref: ApiReference,
    body: SubmitUserMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dispatcher: RuntimeDispatchPort = Depends(get_pure_agent_runtime_dispatcher),
):
    try:
        runtime = _runtime(db, dispatcher)
        conversation = runtime.repository.load_owned_conversation(
            conversation_ref,
            owner_id=int(current_user.id),
        )
        _validate_resource_references(
            db,
            conversation=conversation,
            request_body=body,
        )
        admission = runtime.repository.accept_user_message(
            conversation_id=conversation_ref,
            owner_id=int(current_user.id),
            user_input={
                "text": body.text,
                "resources": [item.model_dump(mode="json") for item in body.resources],
            },
            created_by_ref=_actor_ref(current_user),
            idempotency_key=idempotency_key,
            reply_to_message_id=body.reply_to_message_ref,
        )
        view = MessageAdmissionView(
            conversation_ref=conversation_ref,
            admission=admission.disposition,
            message=runtime.message_view(admission.message),
            task=runtime.task_view(admission.task),
            replayed=admission.replayed,
        )
        db.commit()
        _schedule_runtime(
            background_tasks=background_tasks,
            dispatcher=dispatcher,
            task_ref=admission.task.task_id,
            conversation_ref=conversation_ref,
            state_version=admission.task.state_version,
            reason=(
                RuntimeWakeReason.USER_MESSAGE
                if admission.disposition == "task_trigger"
                else RuntimeWakeReason.STEERING_MESSAGE
            ),
            seed=idempotency_key,
        )
        return _ok(view, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_message_submit_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.get(
    "/conversations/{conversation_ref}/tasks/{task_ref}",
    response_model=PureAgentApiSuccess[AgentTaskView],
    summary="读取 Pure Agent Task 状态",
    operation_id="getBidAssessmentPureAgentTask",
)
def get_task_endpoint(
    conversation_ref: ApiReference,
    task_ref: ApiReference,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        runtime = _runtime(db)
        state = runtime.repository.load_owned_task_state(
            task_ref,
            conversation_id=conversation_ref,
            owner_id=int(current_user.id),
        )
        return _ok(runtime.task_view(state), request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_task_read_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.get(
    "/conversations/{conversation_ref}/tasks/{task_ref}/events",
    response_model=PureAgentApiSuccess[SafeAgentEventPage],
    summary="读取 Pure Agent Task 安全事件",
    operation_id="listBidAssessmentPureAgentTaskEvents",
)
def list_task_events_endpoint(
    conversation_ref: ApiReference,
    task_ref: ApiReference,
    request: Request,
    after_version: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        repository = PureAgentRepository(db)
        task_record = repository.load_owned_task_record(
            task_ref,
            conversation_id=conversation_ref,
            owner_id=int(current_user.id),
        )
        page = SafeEventProjector(repository).page(
            task_record=task_record,
            after_version=after_version,
            limit=limit,
        )
        return _ok(page, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_event_list_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.get(
    "/conversations/{conversation_ref}/tasks/{task_ref}/events/stream",
    summary="订阅 Pure Agent Task 安全事件",
    operation_id="streamBidAssessmentPureAgentTaskEvents",
)
async def stream_task_events_endpoint(
    conversation_ref: ApiReference,
    task_ref: ApiReference,
    request: Request,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", min_length=1, max_length=160),
    ] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        repository = PureAgentRepository(db)
        task_record = repository.load_owned_task_record(
            task_ref,
            conversation_id=conversation_ref,
            owner_id=int(current_user.id),
        )
        start_version = resolve_after_version(
            task_ref=task_ref,
            last_event_id=last_event_id,
        )
        current = repository.load_task_state(task_ref, lock=False)
        if start_version > current.state_version:
            raise SafeEventCursorRejected("Last-Event-ID is ahead of current state")
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_event_stream_open_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())

    actor_id = int(current_user.id)

    async def event_generator():
        cursor = int(start_version)
        last_output_at = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            stream_db = SessionLocal()
            try:
                stream_repository = PureAgentRepository(stream_db)
                stream_task = stream_repository.load_owned_task_record(
                    task_ref,
                    conversation_id=conversation_ref,
                    owner_id=actor_id,
                )
                page = SafeEventProjector(stream_repository).page(
                    task_record=stream_task,
                    after_version=cursor,
                    limit=100,
                )
            except Exception:
                logger.exception(
                    "pure_agent_event_stream_read_failed",
                    extra={
                        "request_id": _request_id(request),
                        "actor_id": actor_id,
                        "conversation_ref": conversation_ref,
                        "task_ref": task_ref,
                        "after_version": cursor,
                    },
                )
                return
            finally:
                stream_db.close()

            for event in page.events:
                cursor = event.state_version
                last_output_at = time.monotonic()
                yield format_safe_event_sse(event)
                if event.terminal:
                    return

            if (
                page.current_status.value in {"completed", "failed", "cancelled"}
                and cursor >= page.current_state_version
            ):
                return
            if page.has_more:
                continue
            if time.monotonic() - last_output_at >= _SSE_KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_output_at = time.monotonic()
            await asyncio.sleep(_SSE_POLL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/conversations/{conversation_ref}/tasks/{task_ref}/slots/{slot_ref}/responses",
    response_model=PureAgentApiSuccess[SlotSubmissionView],
    summary="补充 Pending Slot 并从 Checkpoint 恢复",
    operation_id="submitBidAssessmentPureAgentSlot",
)
def submit_slot_endpoint(
    conversation_ref: ApiReference,
    task_ref: ApiReference,
    slot_ref: ApiReference,
    body: SubmitSlotInputRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dispatcher: RuntimeDispatchPort = Depends(get_pure_agent_runtime_dispatcher),
):
    try:
        result = _runtime(db, dispatcher).submit_slot_input(
            SlotSubmissionCommand(
                conversation_ref=conversation_ref,
                task_ref=task_ref,
                slot_ref=slot_ref,
                owner_id=int(current_user.id),
                created_by_ref=_actor_ref(current_user),
                tenant_ref=_tenant_ref(current_user),
                authorization_snapshot_ref=_authorization_snapshot_ref(current_user),
                expected_state_version=body.expected_state_version,
                resume_token=body.resume_token,
                candidate=body.candidate,
                idempotency_key=idempotency_key,
            )
        )
        db.commit()
        if result.accepted:
            _schedule_runtime(
                background_tasks=background_tasks,
                dispatcher=dispatcher,
                task_ref=result.task.task_ref,
                conversation_ref=conversation_ref,
                state_version=result.task.state_version,
                reason=RuntimeWakeReason.SLOT_RESUMED,
                seed=idempotency_key,
            )
        return _ok(result, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_slot_submit_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())


@router.post(
    "/conversations/{conversation_ref}/tasks/{task_ref}/cancel",
    response_model=PureAgentApiSuccess[TaskCancellationView],
    summary="取消 Pure Agent Task",
    operation_id="cancelBidAssessmentPureAgentTask",
)
def cancel_task_endpoint(
    conversation_ref: ApiReference,
    task_ref: ApiReference,
    body: CancelTaskRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        runtime = _runtime(db)
        commit = runtime.repository.cancel_task(
            task_id=task_ref,
            event_id=_event_ref("cancel", idempotency_key),
            requested_by_ref=_actor_ref(current_user),
            reason="user_requested",
            expected_state_version=body.expected_state_version,
            expected_owner_id=int(current_user.id),
            expected_conversation_id=conversation_ref,
        )
        view = TaskCancellationView(
            conversation_ref=conversation_ref,
            task=runtime.task_view(commit.state),
            replayed=commit.replayed,
        )
        db.commit()
        return _ok(view, request)
    except PureAgentPersistenceError as exc:
        db.rollback()
        return _domain_error(request, exc)
    except Exception:
        db.rollback()
        logger.exception(
            "pure_agent_task_cancel_failed",
            extra={"request_id": _request_id(request), "actor_id": int(current_user.id)},
        )
        return _domain_error(request, PureAgentPersistenceError())
