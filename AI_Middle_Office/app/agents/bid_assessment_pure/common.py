"""Shared, side-effect-free contract primitives for the Pure Agent runtime."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
STEP_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

Reference = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=REFERENCE_PATTERN),
]
StepId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=STEP_ID_PATTERN),
]
ToolName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=TOOL_NAME_PATTERN),
]


def validate_public_locator(value: str) -> str:
    """Reject transport endpoints, absolute paths, and traversal from model output."""

    candidate = value.strip()
    lowered = candidate.lower()
    if (
        not candidate
        or "://" in lowered
        or lowered.startswith("file:")
        or candidate.startswith(("/", "\\"))
        or "\\" in candidate
        or re.match(r"^[A-Za-z]:[/\\]", candidate)
        or ".." in candidate.split("/")
    ):
        raise ValueError("locator must be an opaque, non-transport source location")
    return candidate


class StrictContract(BaseModel):
    """Closed immutable contract used at Pure Agent runtime boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class StrictContentContract(BaseModel):
    """Closed immutable contract that preserves evidence text byte-for-byte."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )
