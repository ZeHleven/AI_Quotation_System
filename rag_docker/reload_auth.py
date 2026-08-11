"""Fail-closed authentication helpers for the destructive RAG reload API."""

from __future__ import annotations

import secrets


WEAK_RELOAD_SECRETS = frozenset(
    {
        "change-me-rag-reload-secret",
        "change-me-in-production",
        "replace-with-strong-random-secret",
    }
)
MIN_RELOAD_SECRET_LENGTH = 16


def verify_reload_secret(configured_secret: str | None, presented_secret: str | None) -> str:
    """Return ``ok``, ``invalid`` or ``unconfigured`` without leaking values."""

    configured = (configured_secret or "").strip()
    presented = (presented_secret or "").strip()
    if len(configured) < MIN_RELOAD_SECRET_LENGTH or configured in WEAK_RELOAD_SECRETS:
        return "unconfigured"
    if not presented or not secrets.compare_digest(configured, presented):
        return "invalid"
    return "ok"
