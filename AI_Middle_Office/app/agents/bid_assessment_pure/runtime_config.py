"""Fail-closed Pure Agent feature configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


PURE_AGENT_FEATURE_ENV = "FEATURE_BID_ASSESSMENT_PURE_AGENT"
PURE_AGENT_RUNTIME_ENV = "FEATURE_BID_ASSESSMENT_PURE_AGENT_RUNTIME"


class PureAgentDisabledError(RuntimeError):
    pass


class ApplicationSettingsView(Protocol):
    feature_bid_assessment_pure_agent: bool
    feature_bid_assessment_pure_agent_runtime: bool


@dataclass(frozen=True, slots=True)
class PureAgentFeatureConfig:
    enabled: bool = False
    runtime_enabled: bool = False

    @classmethod
    def from_application_settings(
        cls,
        settings: ApplicationSettingsView,
    ) -> "PureAgentFeatureConfig":
        return cls(
            enabled=settings.feature_bid_assessment_pure_agent,
            runtime_enabled=bool(
                getattr(settings, "feature_bid_assessment_pure_agent_runtime", False)
            ),
        )

    def require_enabled(self) -> None:
        if not self.enabled:
            raise PureAgentDisabledError(
                "Pure Agent runtime is disabled by default and has no active execution authority"
            )

    def require_runtime_enabled(self) -> None:
        self.require_enabled()
        if not self.runtime_enabled:
            raise PureAgentDisabledError(
                "Pure Agent local Runtime Controller is disabled by default"
            )
