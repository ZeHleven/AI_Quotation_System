"""Bounded Phase 4 local-agent state machine.

The package deliberately has no import-time model provider, tool, network, or
database side effects.
"""

from app.agents.bid_assessment_local.contracts import (
    TASK_ACTION_SCHEMA,
    normalize_task_action,
)

__all__ = ["TASK_ACTION_SCHEMA", "normalize_task_action"]
