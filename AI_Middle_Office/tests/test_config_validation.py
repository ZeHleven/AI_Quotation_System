import os
import subprocess
import sys


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
