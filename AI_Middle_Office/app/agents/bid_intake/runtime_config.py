from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_DEEPSEEK_CHAT_URL = (
    "https://api.deepseek.com/chat/completions"
)
DEFAULT_ZHIPU_CHAT_URL = (
    "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)


@dataclass(frozen=True)
class BidIntakeModelConfig:
    api_url: str
    api_key: str
    model_id: str
    source: str


def resolve_bid_intake_model_config() -> BidIntakeModelConfig | None:
    """Resolve an Agent-specific model or reuse the existing DeepSeek route.

    Explicit BID_INTAKE_* values always win. The fallback avoids duplicating
    the same API key in .env while keeping the worker's model adapter
    OpenAI-compatible.
    """

    explicit_url = _env("BID_INTAKE_MODEL_API_URL")
    explicit_key = _env("BID_INTAKE_MODEL_API_KEY")
    explicit_model = _env("BID_INTAKE_MODEL_ID")
    if explicit_model and not explicit_url and not explicit_key:
        deepseek_key = _env("DEEPSEEK_API_KEY")
        if not deepseek_key:
            return None
        return BidIntakeModelConfig(
            api_url=(
                _env("DEEPSEEK_CHAT_URL")
                or DEFAULT_DEEPSEEK_CHAT_URL
            ),
            api_key=deepseek_key,
            model_id=explicit_model,
            source="bid_intake_model_override",
        )
    if explicit_url or explicit_key or explicit_model:
        if not all((explicit_url, explicit_key, explicit_model)):
            return None
        return BidIntakeModelConfig(
            api_url=explicit_url,
            api_key=explicit_key,
            model_id=explicit_model,
            source="bid_intake_explicit",
        )

    deepseek_key = _env("DEEPSEEK_API_KEY")
    if not deepseek_key:
        return None
    return BidIntakeModelConfig(
        api_url=(
            _env("DEEPSEEK_CHAT_URL")
            or DEFAULT_DEEPSEEK_CHAT_URL
        ),
        api_key=deepseek_key,
        model_id=(
            _env("BIDDING_LLM_MODEL")
            or _env("DEEPSEEK_MODEL")
            or "deepseek-chat"
        ),
        source="deepseek_fallback",
    )


def resolve_bid_intake_fallback_model_config(
    primary: BidIntakeModelConfig | None = None,
) -> BidIntakeModelConfig | None:
    """Resolve an optional secondary model for provider-level failover.

    Explicit BID_INTAKE_FALLBACK_MODEL_* values win. When they are all empty,
    an already configured Zhipu OpenAI-compatible route is reused. The
    automatic route deliberately requires both ZHIPU_API_KEY and a configured
    GLM_VISION_MODEL so deployments do not silently guess a model id.
    """

    if not _env_bool("BID_INTAKE_FALLBACK_ENABLED", True):
        return None

    explicit_url = _env("BID_INTAKE_FALLBACK_MODEL_API_URL")
    explicit_key = _env("BID_INTAKE_FALLBACK_MODEL_API_KEY")
    explicit_model = _env("BID_INTAKE_FALLBACK_MODEL_ID")
    if explicit_url or explicit_key or explicit_model:
        if not all((explicit_url, explicit_key, explicit_model)):
            return None
        fallback = BidIntakeModelConfig(
            api_url=explicit_url,
            api_key=explicit_key,
            model_id=explicit_model,
            source="bid_intake_explicit_fallback",
        )
    else:
        zhipu_key = _env("ZHIPU_API_KEY")
        zhipu_model = _env("GLM_VISION_MODEL")
        if not zhipu_key or not zhipu_model:
            return None
        fallback = BidIntakeModelConfig(
            api_url=(
                _env("GLM_VISION_URL")
                or DEFAULT_ZHIPU_CHAT_URL
            ),
            api_key=zhipu_key,
            model_id=zhipu_model,
            source="zhipu_fallback",
        )

    if primary and (
        fallback.api_url == primary.api_url
        and fallback.model_id == primary.model_id
    ):
        return None
    return fallback


def model_configuration_summary() -> dict[str, str | bool | None]:
    config = resolve_bid_intake_model_config()
    fallback = resolve_bid_intake_fallback_model_config(config)
    return {
        "model_configured": config is not None,
        "model_config_source": config.source if config else None,
        "model_id": config.model_id if config else None,
        "fallback_model_configured": fallback is not None,
        "fallback_model_config_source": (
            fallback.source if fallback else None
        ),
        "fallback_model_id": fallback.model_id if fallback else None,
    }


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
