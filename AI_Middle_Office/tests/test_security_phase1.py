import sys
from pathlib import Path


RAG_DOCKER_DIR = Path(__file__).resolve().parents[2] / "rag_docker"
sys.path.insert(0, str(RAG_DOCKER_DIR))

from reload_auth import verify_reload_secret  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from security_phase1_preflight import check_app_env, check_compose, check_rag_env  # noqa: E402


def test_rag_reload_authentication_fails_closed_when_secret_is_missing_or_weak():
    assert verify_reload_secret("", "anything") == "unconfigured"
    assert verify_reload_secret("short", "short") == "unconfigured"
    assert verify_reload_secret("change-me-rag-reload-secret", "change-me-rag-reload-secret") == "unconfigured"
    assert verify_reload_secret("replace-with-strong-random-secret", "replace-with-strong-random-secret") == "unconfigured"


def test_rag_reload_authentication_uses_exact_secret_match():
    assert verify_reload_secret("strong-test-secret-123", "wrong") == "invalid"
    assert verify_reload_secret("strong-test-secret-123", "strong-test-secret-123") == "ok"


def test_phase1_preflight_rejects_public_mode_and_placeholder_secrets():
    results = check_app_env(
        {
            "PUBLIC_ACCESS_ENABLED": "true",
            "ALLOW_SELF_REGISTRATION": "false",
            "JWT_SECRET_KEY": "change-me-in-production",
            "WEBHOOK_SECRET": "strong-webhook-secret",
            "RELOAD_SECRET": "strong-reload-secret",
            "ZHIPU_API_KEY": "strong-zhipu-api-key",
            "MINIO_ENABLED": "false",
        }
    )

    failed_keys = {result.key for result in results if result.level == "FAIL"}
    assert "PUBLIC_ACCESS_ENABLED" in failed_keys
    assert "JWT_SECRET_KEY" in failed_keys


def test_phase1_preflight_rejects_unsigned_dingtalk_webhook():
    results = check_app_env(
        {
            "PUBLIC_ACCESS_ENABLED": "false",
            "ALLOW_SELF_REGISTRATION": "false",
            "JWT_SECRET_KEY": "strong-jwt-secret-value",
            "WEBHOOK_SECRET": "strong-webhook-secret",
            "RELOAD_SECRET": "strong-reload-secret",
            "ZHIPU_API_KEY": "strong-zhipu-api-key",
            "MINIO_ENABLED": "false",
            "ALERT_DINGTALK_WEBHOOK": (
                "https://oapi.dingtalk.com/robot/send?access_token=test-access-token"
            ),
            "ALERT_DINGTALK_SECRET": "",
        }
    )

    assert "ALERT_DINGTALK" in {result.key for result in results if result.level == "FAIL"}


def test_phase1_preflight_accepts_private_bindings_and_rejects_placeholders():
    compose_results = check_compose(
        '\n'.join(
            [
                '- "${INTERNAL_BIND_ADDRESS:?required}:8001:8001"',
                '- "${INTERNAL_BIND_ADDRESS:?required}:6380:6379"',
                '- "${INTERNAL_BIND_ADDRESS:?required}:9002:9000"',
                '- "${INTERNAL_BIND_ADDRESS:?required}:9003:9001"',
                'RELOAD_SECRET=${RELOAD_SECRET:?required}',
            ]
        )
    )
    assert not [result for result in compose_results if result.level == "FAIL"]

    rag_results = check_rag_env(
        {
            "INTERNAL_BIND_ADDRESS": "192.168.88.128",
            "RELOAD_SECRET": "replace-with-strong-random-secret",
            "MILVUS_MINIO_ACCESS_KEY": "strong-internal-user",
            "MILVUS_MINIO_SECRET_KEY": "strong-internal-password",
            "QUOTE_MINIO_ROOT_USER": "strong-quote-admin",
            "QUOTE_MINIO_ROOT_PASSWORD": "strong-quote-password",
        }
    )
    assert "RELOAD_SECRET" in {result.key for result in rag_results if result.level == "FAIL"}
