import os
import subprocess
import sys


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
