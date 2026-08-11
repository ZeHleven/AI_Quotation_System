import os
import subprocess
import sys
from pathlib import Path

from app.core.config import Settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _run_settings_print(env_updates: dict[str, str], expression: str):
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "STRICT_CONFIG": "false",
            "DATABASE_URL": "sqlite:///./sql_app.db",
        }
    )
    env.pop("LEGACY_MATERIALS_FILE", None)
    env.pop("MATERIALS_FILE", None)
    env.pop("RAG_EVAL_REPORT_DIR", None)
    env.update(env_updates)

    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from app.core.config import Settings; s = Settings(); print({expression})",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_strict_config_rejects_default_or_missing_secrets():
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "STRICT_CONFIG": "true",
            "JWT_SECRET_KEY": "your_super_secret_key_for_ai_middle_office",
            "WEBHOOK_SECRET": "",
            "RELOAD_SECRET": "",
            "ZHIPU_API_KEY": "",
            "MINIO_ENABLED": "true",
            "MINIO_SECRET_KEY": "change-this-password",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", "from app.core.config import Settings; Settings()"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "Invalid production configuration" in result.stderr
    assert "JWT_SECRET_KEY" in result.stderr


def test_startup_schema_helpers_default_to_disabled():
    env = os.environ.copy()
    env.pop("AUTO_CREATE_TABLES", None)
    env.pop("STARTUP_COMPAT_MIGRATIONS", None)
    env.update(
        {
            "APP_ENV": "development",
            "STRICT_CONFIG": "false",
            "DATABASE_URL": "sqlite:///./sql_app.db",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.core.config import Settings; "
                "s = Settings(); "
                "print(s.auto_create_tables, s.startup_compat_migrations)"
            ),
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "False False"


def test_migration_database_url_falls_back_for_local_compatibility():
    settings = Settings(
        app_env="development",
        strict_config=False,
        database_url="sqlite:///./local-test.db",
        migration_database_url="",
    )

    assert settings.alembic_database_url == "sqlite:///./local-test.db"


def test_migration_database_url_can_use_a_dedicated_account():
    settings = Settings(
        app_env="development",
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        migration_database_url="sqlite:///./migrator.db",
    )

    assert settings.alembic_database_url == "sqlite:///./migrator.db"


def test_public_access_rejects_shared_runtime_and_migration_account():
    common = {
        "PUBLIC_ACCESS_ENABLED": "true",
        "DATABASE_URL": "sqlite:///./shared.db",
        "MIGRATION_DATABASE_URL": "sqlite:///./shared.db",
        "JWT_SECRET_KEY": "strong-public-jwt-secret",
        "WEBHOOK_SECRET": "strong-public-webhook-secret",
        "RELOAD_SECRET": "strong-public-reload-secret",
        "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
        "MINIO_ENABLED": "false",
        "CORS_ALLOW_ORIGINS": "https://quote.example.com",
        "TRUSTED_HOSTS": "quote.example.com",
    }

    result = _run_settings_print(common, "s.public_access_enabled")

    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL must use a distinct database account" in result.stderr


def test_public_access_rejects_same_account_with_different_query_parameters():
    common = {
        "PUBLIC_ACCESS_ENABLED": "true",
        "DATABASE_URL": "mysql+pymysql://shared:password@db.internal/app?charset=utf8mb4",
        "MIGRATION_DATABASE_URL": (
            "mysql+pymysql://shared:password@db.internal/app?ssl_check_hostname=false"
        ),
        "JWT_SECRET_KEY": "strong-public-jwt-secret",
        "WEBHOOK_SECRET": "strong-public-webhook-secret",
        "RELOAD_SECRET": "strong-public-reload-secret",
        "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
        "MINIO_ENABLED": "false",
        "CORS_ALLOW_ORIGINS": "https://quote.example.com",
        "TRUSTED_HOSTS": "quote.example.com",
    }

    result = _run_settings_print(common, "s.public_access_enabled")

    assert result.returncode != 0
    assert "MIGRATION_DATABASE_URL must use a distinct database account" in result.stderr


def test_legacy_materials_file_prefers_new_env_name():
    result = _run_settings_print(
        {
            "LEGACY_MATERIALS_FILE": "C:/tmp/new_legacy_materials.json",
            "MATERIALS_FILE": "C:/tmp/old_materials.json",
        },
        "s.legacy_materials_file.as_posix()",
    )

    assert result.returncode == 0
    assert result.stdout.strip().endswith("/new_legacy_materials.json")


def test_materials_file_remains_backward_compatible_alias():
    result = _run_settings_print(
        {"MATERIALS_FILE": "C:/tmp/old_materials.json"},
        "(s.legacy_materials_file.as_posix(), s.materials_file.as_posix())",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "('C:/tmp/old_materials.json', 'C:/tmp/old_materials.json')"


def test_rag_eval_report_dir_is_independent_from_materials_file():
    result = _run_settings_print(
        {
            "MATERIALS_FILE": "C:/tmp/old_materials.json",
            "RAG_EVAL_REPORT_DIR": "C:/tmp/rag_reports",
        },
        "(s.legacy_materials_file.as_posix(), s.rag_eval_report_dir.as_posix())",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "('C:/tmp/old_materials.json', 'C:/tmp/rag_reports')"


def test_proxy_settings_allow_explicit_empty_values():
    result = _run_settings_print(
        {
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NO_PROXY": "",
        },
        "(s.http_proxy, s.https_proxy, s.no_proxy)",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "('', '', '')"


def test_external_database_enables_secret_validation_without_app_env_production():
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "STRICT_CONFIG": "false",
            "DATABASE_URL": "mysql+pymysql://user:pass@127.0.0.1:3306/app",
            "JWT_SECRET_KEY": "your_super_secret_key_for_ai_middle_office",
            "WEBHOOK_SECRET": "",
            "RELOAD_SECRET": "",
            "ZHIPU_API_KEY": "",
            "MINIO_ENABLED": "false",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", "from app.core.config import Settings; Settings()"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "Invalid production configuration" in result.stderr
    assert "JWT_SECRET_KEY" in result.stderr


def test_public_access_disables_internal_experimental_routes():
    public_secrets = {
        "PUBLIC_ACCESS_ENABLED": "true",
        "DATABASE_URL": "sqlite:///./runtime.db",
        "MIGRATION_DATABASE_URL": "sqlite:///./migrator.db",
        "JWT_SECRET_KEY": "strong-public-jwt-secret",
        "WEBHOOK_SECRET": "strong-public-webhook-secret",
        "RELOAD_SECRET": "strong-public-reload-secret",
        "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
        "MINIO_ENABLED": "false",
        "CORS_ALLOW_ORIGINS": "https://quote.example.com",
        "TRUSTED_HOSTS": "quote.example.com",
    }
    public_result = _run_settings_print(
        public_secrets,
        "s.internal_experimental_routes_enabled",
    )
    internal_result = _run_settings_print(
        {"PUBLIC_ACCESS_ENABLED": "false"},
        "s.internal_experimental_routes_enabled",
    )

    assert public_result.returncode == 0
    assert public_result.stdout.strip() == "False"
    assert internal_result.returncode == 0
    assert internal_result.stdout.strip() == "True"


def test_configured_dingtalk_webhook_requires_signing_secret():
    result = _run_settings_print(
        {
            "STRICT_CONFIG": "true",
            "JWT_SECRET_KEY": "strong-public-jwt-secret",
            "WEBHOOK_SECRET": "strong-public-webhook-secret",
            "RELOAD_SECRET": "strong-public-reload-secret",
            "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
            "MINIO_ENABLED": "false",
            "CORS_ALLOW_ORIGINS": "https://quote.example.com",
            "TRUSTED_HOSTS": "quote.example.com",
            "ALERT_DINGTALK_WEBHOOK": (
                "https://oapi.dingtalk.com/robot/send?access_token=test-access-token"
            ),
            "ALERT_DINGTALK_SECRET": "short",
        },
        "s.alert_dingtalk_webhook",
    )

    assert result.returncode != 0
    assert "ALERT_DINGTALK_SECRET" in result.stderr


def test_public_app_does_not_mount_internal_experimental_routes():
    env = os.environ.copy()
    env.update(
        {
            "PUBLIC_ACCESS_ENABLED": "true",
            "DATABASE_URL": "sqlite:///./runtime.db",
            "MIGRATION_DATABASE_URL": "sqlite:///./migrator.db",
            "ALLOW_SELF_REGISTRATION": "false",
            "JWT_SECRET_KEY": "strong-public-jwt-secret",
            "WEBHOOK_SECRET": "strong-public-webhook-secret",
            "RELOAD_SECRET": "strong-public-reload-secret",
            "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
            "MINIO_ENABLED": "false",
            "CORS_ALLOW_ORIGINS": "https://quote.example.com",
            "TRUSTED_HOSTS": "quote.example.com",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.main import app; "
                "paths={route.path for route in app.routes "
                "if getattr(route, 'path', None)}; "
                "print(any('/admin/codex-worker/' in path for path in paths), "
                "any('/admin/dwg-quantity-trial/' in path for path in paths))"
            ),
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False False"


def test_public_access_rejects_wildcard_network_boundaries():
    result = _run_settings_print(
        {
            "PUBLIC_ACCESS_ENABLED": "true",
            "DATABASE_URL": "sqlite:///./runtime.db",
            "MIGRATION_DATABASE_URL": "sqlite:///./migrator.db",
            "JWT_SECRET_KEY": "strong-public-jwt-secret",
            "WEBHOOK_SECRET": "strong-public-webhook-secret",
            "RELOAD_SECRET": "strong-public-reload-secret",
            "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
            "MINIO_ENABLED": "false",
            "CORS_ALLOW_ORIGINS": "*",
            "TRUSTED_HOSTS": "*",
        },
        "s.public_access_enabled",
    )

    assert result.returncode != 0
    assert "CORS_ALLOW_ORIGINS" in result.stderr
    assert "TRUSTED_HOSTS" in result.stderr


def test_public_app_disables_interactive_api_documentation():
    env = os.environ.copy()
    env.update(
        {
            "PUBLIC_ACCESS_ENABLED": "true",
            "DATABASE_URL": "sqlite:///./runtime.db",
            "MIGRATION_DATABASE_URL": "sqlite:///./migrator.db",
            "JWT_SECRET_KEY": "strong-public-jwt-secret",
            "WEBHOOK_SECRET": "strong-public-webhook-secret",
            "RELOAD_SECRET": "strong-public-reload-secret",
            "ZHIPU_API_KEY": "strong-public-zhipu-api-key",
            "MINIO_ENABLED": "false",
            "CORS_ALLOW_ORIGINS": "https://quote.example.com",
            "TRUSTED_HOSTS": "quote.example.com",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.main import app; "
                "paths={route.path for route in app.routes "
                "if getattr(route, 'path', None)}; "
                "print('/docs' in paths, '/redoc' in paths, '/openapi.json' in paths)"
            ),
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False False False"


def test_production_image_disables_automatic_database_migrations_by_default():
    dockerfile = (REPOSITORY_ROOT / "deploy" / "app-node" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "AUTO_RUN_DB_MIGRATIONS=false" in dockerfile
