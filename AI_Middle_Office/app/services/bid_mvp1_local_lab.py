"""Read-only access to historical MVP-1 local-lab artifacts.

The deterministic P0-P4 runner was removed. This module now only validates
the isolated SQLite boundary and exposes frozen historical configuration for
the Runtime Lab viewer.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.models import registry as model_registry  # noqa: F401 - complete schema graph
from app.models.bid_assessment_config import (
    BidFactCatalogVersion,
    BidFormulaCatalogVersion,
    BidModelProfileVersion,
    BidPromptBundle,
    BidRuleSet,
    BidToolRegistryVersion,
)
from app.models.user import User
from app.services.bid_assessment_eventing import canonical_hash
from app.services.bid_mvp1_model_provider import (
    DEEPSEEK_PROVIDER_REF,
    DEEPSEEK_V4_FLASH_MODEL_ID,
    DEEPSEEK_V4_FLASH_PRICE_VERSION,
    DEEPSEEK_V4_FLASH_THINKING_MODE,
)
LOCAL_LAB_USERNAME = "mvp1-local"
LOCAL_LAB_BASE_VERSION = "mvp1-local-lab-1.1.0-phase4c1"
LOCAL_LAB_DEEPSEEK_VERSION = "mvp1-local-deepseek-v4-flash-1.1.0-phase4c1"
HISTORICAL_LOCAL_LAB_VERSIONS = frozenset(
    {
        "mvp1-local-lab-1.0.0",
        "mvp1-local-deepseek-v4-flash-1.0.1",
    }
)
LOCAL_ACCESS_VIEW_ONLY = "view-only"


def local_access_mode() -> str:
    """Return the read-only authority boundary for the retired workflow lab.

    The former ``execute`` mode drove the deterministic P0-P4 workflow.  That
    executor has been removed from the active architecture; the lab remains
    available only for inspecting historical runs until the pure Agent entry
    point is introduced.
    """

    mode = os.getenv("BID_MVP1_LOCAL_ACCESS_MODE", LOCAL_ACCESS_VIEW_ONLY).strip().lower()
    if mode != LOCAL_ACCESS_VIEW_ONLY:
        raise RuntimeError(
            "BID_MVP1_LOCAL_ACCESS_MODE must be view-only; "
            "the legacy P0-P4 workflow executor was removed"
        )
    return mode


def local_model_mode() -> str:
    return settings.bid_mvp1_local_model_mode.strip().lower()


def local_lab_version() -> str:
    return (
        LOCAL_LAB_DEEPSEEK_VERSION
        if local_model_mode() == DEEPSEEK_V4_FLASH_MODEL_ID
        else LOCAL_LAB_BASE_VERSION
    )


def build_local_model_profile_payload() -> tuple[dict, dict, dict]:
    mode = local_model_mode()
    if mode == DEEPSEEK_V4_FLASH_MODEL_ID:
        provider_ref = DEEPSEEK_PROVIDER_REF
        model_ref = DEEPSEEK_V4_FLASH_MODEL_ID
        reserved_cost = 100_000
        provider_identifiers = {
            provider_ref: {
                "adapter_kind": "openai_compatible_chat_completions",
                "endpoint_class": "external_https_official_deepseek",
                "thinking_mode": DEEPSEEK_V4_FLASH_THINKING_MODE,
                "response_format": "json_object",
                "price_currency": "USD",
                "price_version": DEEPSEEK_V4_FLASH_PRICE_VERSION,
            }
        }
        model_identifiers = {
            model_ref: {
                "provider_ref": provider_ref,
                "capability": "closed_task_action",
                "model_family": "DeepSeek-V4-Flash",
                "json_output": True,
                "tool_calls": True,
                "context_window_tokens": 1_048_576,
            }
        }
    else:
        provider_ref = "mvp1-local-deterministic"
        model_ref = "mvp1-local-closed-action"
        reserved_cost = 0
        provider_identifiers = {
            provider_ref: {
                "adapter_kind": "injected_test_provider",
                "endpoint_class": "no_network",
            }
        }
        model_identifiers = {
            model_ref: {
                "provider_ref": provider_ref,
                "capability": "closed_task_action",
            }
        }
    role_routing = {
        role: {
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "prompt_role": f"{role}.task-action.v1",
            "action_schema": "bid.task.action.v1",
            "replay_policy": "safe_idempotent",
            "max_attempts": 3 if mode == DEEPSEEK_V4_FLASH_MODEL_ID else 2,
            "timeout_seconds": 180 if mode == DEEPSEEK_V4_FLASH_MODEL_ID else 120,
            "reserved_cost_microunits": reserved_cost,
        }
        for role in (
            "local_research",
            "synthesizer",
            "evidence_validator",
            "report_writer",
        )
    }
    return role_routing, provider_identifiers, model_identifiers


def require_local_lab_boundary() -> None:
    if os.getenv("BID_MVP1_LOCAL_LAB", "").strip() != "1":
        raise RuntimeError("BID_MVP1_LOCAL_LAB must be exactly 1")
    if not settings.database_url.lower().startswith("sqlite:"):
        raise RuntimeError("MVP-1 local lab requires a SQLite database")
    if settings.bid_upload_storage_backend.strip().lower() != "local":
        raise RuntimeError("MVP-1 local lab requires local upload storage")
    if settings.task_queue_mode.strip().lower() != "local":
        raise RuntimeError("MVP-1 local lab forbids the external task queue")
    if local_model_mode() == DEEPSEEK_V4_FLASH_MODEL_ID:
        if not settings.feature_bid_assessment_phase4_deepseek_adapter:
            raise RuntimeError("MVP-1 DeepSeek mode requires the Phase 4B-1 adapter")


def initialize_local_lab() -> int:
    """Materialize a disposable lab schema and seed frozen local-only inputs.

    This is not an Alembic deployment path and does not stamp ``alembic_version``.
    The migration suite separately validates the 0083-0108 development topology.
    """

    require_local_lab_boundary()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        with db.begin():
            user = db.query(User).filter(User.username == LOCAL_LAB_USERNAME).one_or_none()
            if user is None:
                user = User(
                    username=LOCAL_LAB_USERNAME,
                    hashed_password="local-lab-no-password",
                    role="admin",
                    role_version=1,
                    quota=100000,
                    quota_reserved=0,
                    is_active=True,
                    must_change_password=False,
                )
                db.add(user)
                db.flush()
            list(user.role_assignments)
            _seed_frozen_versions(db, actor_id=int(user.id))
            return int(user.id)
    finally:
        db.close()


def validate_local_lab_read_only() -> int:
    """Validate an existing frozen lab without creating or updating rows."""

    require_local_lab_boundary()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == LOCAL_LAB_USERNAME).one_or_none()
        active_model = (
            db.query(BidModelProfileVersion)
            .filter(BidModelProfileVersion.active_slot_key == "active")
            .one_or_none()
        )
        if (
            user is None
            or active_model is None
            or str(active_model.version)
            not in {local_lab_version(), *HISTORICAL_LOCAL_LAB_VERSIONS}
        ):
            raise RuntimeError(
                "MVP-1 view-only database is not initialized for the selected "
                "model profile"
            )
        return int(user.id)
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "MVP-1 view-only database is not initialized"
        ) from exc
    finally:
        db.close()


def _seed_frozen_versions(db: Session, *, actor_id: int) -> None:
    lab_version = local_lab_version()
    if db.query(BidRuleSet).filter(BidRuleSet.active_slot_key == "active").count():
        active_model = (
            db.query(BidModelProfileVersion)
            .filter(BidModelProfileVersion.active_slot_key == "active")
            .one_or_none()
        )
        if active_model is None or str(active_model.version) != lab_version:
            raise RuntimeError(
                "MVP-1 local model mode differs from the frozen database profile; "
                "use a fresh isolated database"
            )
        return
    governed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    common = {
        "status": "active",
        "active_slot_key": "active",
        "authored_by": actor_id,
        "reviewed_by": actor_id,
        "reviewed_at": governed_at,
        "activated_at": governed_at,
        "row_version": 1,
    }
    role_routing, provider_identifiers, model_identifiers = (
        build_local_model_profile_payload()
    )
    seed_hash = canonical_hash({"version": lab_version})
    db.add_all(
        [
            BidRuleSet(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:rules")),
                version=lab_version,
                artifact_ref="local-lab://rules",
                artifact_hash=canonical_hash({"kind": "rules", "seed": seed_hash}),
                effective_from=governed_at,
                effective_to=None,
                test_cases_ref="local-lab://rules/tests",
                **common,
            ),
            BidFactCatalogVersion(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:facts")),
                version=lab_version,
                artifact_ref="local-lab://facts",
                artifact_hash=canonical_hash({"kind": "facts", "seed": seed_hash}),
                schema_version="mvp1",
                **common,
            ),
            BidPromptBundle(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:prompts")),
                version=lab_version,
                artifact_ref="local-lab://prompts",
                artifact_hash=canonical_hash({"kind": "prompts", "seed": seed_hash}),
                bundle_schema_version="v1",
                **common,
            ),
            BidToolRegistryVersion(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:tools")),
                version=lab_version,
                artifact_ref="local-lab://tools",
                artifact_hash=canonical_hash({"kind": "tools", "seed": seed_hash}),
                registry_schema_version="v1",
                **common,
            ),
            BidModelProfileVersion(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:models")),
                version=lab_version,
                artifact_ref="local-lab://models",
                artifact_hash=canonical_hash(
                    {
                        "role_routing": role_routing,
                        "provider_identifiers": provider_identifiers,
                        "model_identifiers": model_identifiers,
                    }
                ),
                role_routing_json=role_routing,
                provider_identifiers_json=provider_identifiers,
                model_identifiers_json=model_identifiers,
                **common,
            ),
            BidFormulaCatalogVersion(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_version}:formulas")),
                version=lab_version,
                artifact_ref="local-lab://formulas",
                artifact_hash=canonical_hash({"kind": "formulas", "seed": seed_hash}),
                rounding_policy_json={},
                **common,
            ),
        ]
    )
