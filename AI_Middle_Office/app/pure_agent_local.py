"""Explicit local-only FastAPI factory for the Pure Agent daily-use entry.

Uvicorn must call ``create_app`` with ``--factory`` after the dedicated start
script has set the isolation environment.  Importing this module alone does
not read business files, load BCE, read secrets, install Runtime authority, or
start a service.
"""

from __future__ import annotations

import os
from pathlib import Path


def _required_path(name: str) -> Path:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise RuntimeError(f"required local startup setting is missing: {name}")
    return Path(raw).resolve(strict=False)


def create_app():
    """Preflight, materialize accepted adapters, then install one Runtime."""

    from app.core.config import settings
    from scripts.preflight_bid_pure_agent_local import (
        build_local_preflight_report,
    )

    preflight = build_local_preflight_report(settings)

    # Import the regular application only after the read-only isolation gate.
    # It supplies the established auth, Conversation API and Vite shell; this
    # factory does not modify production main.py or any lifespan hook.
    from app.main import app
    from app.api.v1 import bid_assessment_pure_agent as conversation_api

    conversation_api.configure_pure_agent_local_preflight_report(preflight)
    app.state.bid_pure_agent_local_preflight = preflight.model_dump(mode="json")
    if not preflight.runtime_install_allowed:
        failed = ",".join(preflight.failed_codes)
        raise RuntimeError(f"Pure Agent local Preflight rejected startup: {failed}")

    from app.agents.bid_assessment_pure.local_bootstrap import (
        LocalRuntimeBootstrapRequest,
    )
    from scripts.bid_pure_agent_local_runtime import (
        FrozenLocalRuntimeConfig,
        materialize_local_runtime,
    )

    try:
        materialized = materialize_local_runtime(
            FrozenLocalRuntimeConfig(
                pdf_path=_required_path("BID_PURE_AGENT_LOCAL_PDF"),
                embedding_model_path=_required_path(
                    "BID_PURE_AGENT_LOCAL_EMBEDDING_MODEL"
                ),
                secret_env_file=_required_path(
                    "BID_PURE_AGENT_LOCAL_SECRET_ENV_FILE"
                ),
                provider_timeout_seconds=max(
                    30,
                    min(
                        int(os.getenv("BID_PURE_AGENT_LOCAL_MODEL_TIMEOUT", "180")),
                        300,
                    ),
                ),
            )
        )
    except Exception:
        raise RuntimeError(
            "Pure Agent local Runtime inputs could not be materialized"
        ) from None

    bootstrap = conversation_api.bootstrap_pure_agent_local_runtime(
        request=LocalRuntimeBootstrapRequest(
            activation_ref="activation:pure-agent-local-daily",
            requested_by_ref="user:local-operator",
            target_environment="isolated_local_development",
            install_requested=True,
        ),
        adapters=materialized.adapters,
        max_pulses_per_dispatch=max(
            1,
            min(int(os.getenv("BID_PURE_AGENT_LOCAL_MAX_PULSES", "48")), 64),
        ),
    )
    if not bootstrap.runtime_available:
        raise RuntimeError(
            "Pure Agent local Runtime Bootstrap did not grant authority"
        )
    app.state.bid_pure_agent_local_bootstrap = bootstrap.model_dump(mode="json")
    app.state.bid_pure_agent_local_sources = {
        "document_sha256": materialized.document_sha256,
        "document_page_count": materialized.document_page_count,
        "enterprise_baseline_version": materialized.enterprise_baseline_version,
    }
    return app

