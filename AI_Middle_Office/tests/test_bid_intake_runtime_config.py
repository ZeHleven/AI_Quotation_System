from __future__ import annotations

from app.agents.bid_intake.runtime_config import (
    DEFAULT_DEEPSEEK_CHAT_URL,
    DEFAULT_ZHIPU_CHAT_URL,
    model_configuration_summary,
    resolve_bid_intake_fallback_model_config,
    resolve_bid_intake_model_config,
)


MODEL_ENV_NAMES = (
    "BID_INTAKE_MODEL_API_URL",
    "BID_INTAKE_MODEL_API_KEY",
    "BID_INTAKE_MODEL_ID",
    "BID_INTAKE_FALLBACK_MODEL_API_URL",
    "BID_INTAKE_FALLBACK_MODEL_API_KEY",
    "BID_INTAKE_FALLBACK_MODEL_ID",
    "BID_INTAKE_FALLBACK_ENABLED",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_CHAT_URL",
    "DEEPSEEK_MODEL",
    "BIDDING_LLM_MODEL",
    "ZHIPU_API_KEY",
    "GLM_VISION_URL",
    "GLM_VISION_MODEL",
)


def _clear_model_environment(monkeypatch) -> None:
    for name in MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_explicit_bid_intake_model_configuration_wins(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv(
        "BID_INTAKE_MODEL_API_URL",
        "https://model.example/v1/chat/completions",
    )
    monkeypatch.setenv("BID_INTAKE_MODEL_API_KEY", "agent-key")
    monkeypatch.setenv("BID_INTAKE_MODEL_ID", "agent-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fallback-key")

    config = resolve_bid_intake_model_config()

    assert config is not None
    assert config.api_url == "https://model.example/v1/chat/completions"
    assert config.api_key == "agent-key"
    assert config.model_id == "agent-model"
    assert config.source == "bid_intake_explicit"


def test_incomplete_explicit_configuration_does_not_silently_fallback(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv(
        "BID_INTAKE_MODEL_API_URL",
        "https://model.example/v1/chat/completions",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fallback-key")

    assert resolve_bid_intake_model_config() is None


def test_existing_deepseek_route_is_reused_without_key_duplication(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "existing-key")
    monkeypatch.setenv("BIDDING_LLM_MODEL", "deepseek-v4-pro")

    config = resolve_bid_intake_model_config()
    summary = model_configuration_summary()

    assert config is not None
    assert config.api_url == DEFAULT_DEEPSEEK_CHAT_URL
    assert config.api_key == "existing-key"
    assert config.model_id == "deepseek-v4-pro"
    assert config.source == "deepseek_fallback"
    assert summary == {
        "model_configured": True,
        "model_config_source": "deepseek_fallback",
        "model_id": "deepseek-v4-pro",
        "fallback_model_configured": False,
        "fallback_model_config_source": None,
        "fallback_model_id": None,
    }


def test_agent_model_id_can_override_shared_deepseek_model(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "existing-key")
    monkeypatch.setenv("BIDDING_LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv(
        "BID_INTAKE_MODEL_ID",
        "deepseek-v4-flash",
    )

    config = resolve_bid_intake_model_config()

    assert config is not None
    assert config.api_url == DEFAULT_DEEPSEEK_CHAT_URL
    assert config.api_key == "existing-key"
    assert config.model_id == "deepseek-v4-flash"
    assert config.source == "bid_intake_model_override"


def test_no_supported_model_configuration_is_not_ready(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)

    assert resolve_bid_intake_model_config() is None
    assert model_configuration_summary()["model_configured"] is False


def test_existing_zhipu_route_is_reused_as_automatic_fallback(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("GLM_VISION_MODEL", "glm-tool-model")

    primary = resolve_bid_intake_model_config()
    fallback = resolve_bid_intake_fallback_model_config(primary)
    summary = model_configuration_summary()

    assert fallback is not None
    assert fallback.api_url == DEFAULT_ZHIPU_CHAT_URL
    assert fallback.api_key == "zhipu-key"
    assert fallback.model_id == "glm-tool-model"
    assert fallback.source == "zhipu_fallback"
    assert summary["fallback_model_configured"] is True
    assert summary["fallback_model_id"] == "glm-tool-model"


def test_explicit_fallback_model_configuration_wins(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv(
        "BID_INTAKE_FALLBACK_MODEL_API_URL",
        "https://fallback.example/v1/chat/completions",
    )
    monkeypatch.setenv(
        "BID_INTAKE_FALLBACK_MODEL_API_KEY",
        "fallback-key",
    )
    monkeypatch.setenv(
        "BID_INTAKE_FALLBACK_MODEL_ID",
        "fallback-model",
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("GLM_VISION_MODEL", "glm-tool-model")

    fallback = resolve_bid_intake_fallback_model_config()

    assert fallback is not None
    assert fallback.api_url == (
        "https://fallback.example/v1/chat/completions"
    )
    assert fallback.model_id == "fallback-model"
    assert fallback.source == "bid_intake_explicit_fallback"


def test_fallback_can_be_disabled_for_cost_control(
    monkeypatch,
) -> None:
    _clear_model_environment(monkeypatch)
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    monkeypatch.setenv("GLM_VISION_MODEL", "glm-tool-model")
    monkeypatch.setenv("BID_INTAKE_FALLBACK_ENABLED", "false")

    assert resolve_bid_intake_fallback_model_config() is None
