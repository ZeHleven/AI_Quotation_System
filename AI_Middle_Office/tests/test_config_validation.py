import os
import subprocess
import sys
from pathlib import Path

import pytest

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
                "paths={route.path for route in app.routes}; "
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
                "paths={route.path for route in app.routes}; "
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


def test_phase3f_executor_requires_context_task_runtime_and_signing_key():
    settings = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase3_tool_executor=False,
    )
    assert settings.feature_bid_assessment_phase3_tool_executor is False

    with pytest.raises(RuntimeError, match="TOOL_CONTEXT"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_tool_executor=True,
            feature_bid_assessment_phase3_tool_context=False,
            feature_bid_assessment_phase3_task_runtime=True,
            bid_tool_scope_signing_key="x" * 32,
        )
    with pytest.raises(RuntimeError, match="TASK_RUNTIME"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_tool_executor=True,
            feature_bid_assessment_phase3_tool_context=True,
            feature_bid_assessment_phase3_task_runtime=False,
            bid_tool_scope_signing_key="x" * 32,
        )
    with pytest.raises(RuntimeError, match="BID_TOOL_SCOPE_SIGNING_KEY"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_tool_executor=True,
            feature_bid_assessment_phase3_tool_context=True,
            feature_bid_assessment_phase3_task_runtime=True,
            bid_tool_scope_signing_key="short",
        )


def test_phase3g_run_validation_requires_task_runtime_and_run_lifecycle():
    settings = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase3_run_validation=False,
    )
    assert settings.feature_bid_assessment_phase3_run_validation is False

    with pytest.raises(RuntimeError, match="TASK_RUNTIME"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_run_validation=True,
            feature_bid_assessment_phase3_task_runtime=False,
            feature_bid_assessment_phase3_run_lifecycle=True,
        )
    with pytest.raises(RuntimeError, match="RUN_LIFECYCLE"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_run_validation=True,
            feature_bid_assessment_phase3_task_runtime=True,
            feature_bid_assessment_phase3_run_lifecycle=False,
        )


def test_pdf_c2_native_layout_profile_dependencies_fail_closed():
    profile = "bid-document-parser-profile-v2-pdf-native-layout"
    disabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_pdf_c2_native_layout=False,
    )
    assert disabled.feature_bid_assessment_pdf_c2_native_layout is False

    with pytest.raises(RuntimeError, match="PDF_C2_NATIVE_LAYOUT"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_pdf_c2_native_layout=False,
        )
    with pytest.raises(RuntimeError, match="V1_RUNTIME"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_v1_runtime=False,
            feature_bid_assessment_phase2_document_worker=True,
        )
    with pytest.raises(RuntimeError, match="PHASE2_DOCUMENT_WORKER"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_v1_runtime=True,
            feature_bid_assessment_phase2_document_worker=False,
        )
    with pytest.raises(RuntimeError, match="PDF-C3"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_v1_runtime=True,
            feature_bid_assessment_phase2_document_worker=True,
            feature_bid_assessment_phase4_evidence_mcp=True,
        )
    enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        bid_document_parser_profile_version=profile,
        feature_bid_assessment_pdf_c2_native_layout=True,
        feature_bid_assessment_v1_runtime=True,
        feature_bid_assessment_phase2_document_worker=True,
    )
    assert enabled.feature_bid_assessment_pdf_c2_native_layout is True


def test_rq1a_structure_profile_dependencies_fail_closed():
    profile = "bid-document-parser-profile-v3-pdf-structure-rq1a"
    with pytest.raises(RuntimeError, match="PDF_C2_NATIVE_LAYOUT"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_rq1a_structure_aggregation=True,
            feature_bid_assessment_pdf_c2_native_layout=False,
        )
    with pytest.raises(RuntimeError, match="RQ1A_STRUCTURE_AGGREGATION"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_document_parser_profile_version=profile,
            feature_bid_assessment_rq1a_structure_aggregation=False,
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_v1_runtime=True,
            feature_bid_assessment_phase2_document_worker=True,
        )
    enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        bid_document_parser_profile_version=profile,
        feature_bid_assessment_rq1a_structure_aggregation=True,
        feature_bid_assessment_pdf_c2_native_layout=True,
        feature_bid_assessment_v1_runtime=True,
        feature_bid_assessment_phase2_document_worker=True,
    )
    assert enabled.feature_bid_assessment_rq1a_structure_aggregation is True


def test_rq1b_parse_quality_gate_dependencies_fail_closed():
    profile = "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "bid_document_parser_profile_version": profile,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
    }
    with pytest.raises(RuntimeError, match="RQ1A_STRUCTURE_AGGREGATION"):
        Settings(
            **common,
            feature_bid_assessment_rq1a_structure_aggregation=False,
            feature_bid_assessment_rq1b_parse_quality_gate=True,
        )
    with pytest.raises(RuntimeError, match="RQ1B_PARSE_QUALITY_GATE"):
        Settings(
            **common,
            feature_bid_assessment_rq1a_structure_aggregation=True,
            feature_bid_assessment_rq1b_parse_quality_gate=False,
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq1a_structure_aggregation=True,
        feature_bid_assessment_rq1b_parse_quality_gate=True,
    )
    assert enabled.feature_bid_assessment_rq1b_parse_quality_gate is True


def test_pdf_c3_role_aware_retrieval_dependencies_fail_closed():
    parser_profile = "bid-document-parser-profile-v2-pdf-native-layout"
    retrieval_profile = "bid-evidence-retrieval-profile-v2-role-aware"
    phase3 = {
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "bid_tool_scope_signing_key": "x" * 32,
    }
    with pytest.raises(RuntimeError, match="PDF_C2_NATIVE_LAYOUT"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            bid_evidence_retrieval_profile_version=retrieval_profile,
            **phase3,
        )
    with pytest.raises(RuntimeError, match="EVIDENCE_MCP"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            bid_document_parser_profile_version=parser_profile,
            bid_evidence_retrieval_profile_version=retrieval_profile,
            **phase3,
        )
    with pytest.raises(RuntimeError, match="BID_EVIDENCE_RETRIEVAL_PROFILE_VERSION"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_pdf_c2_native_layout=True,
            feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
            feature_bid_assessment_phase4_evidence_mcp=True,
            bid_document_parser_profile_version=parser_profile,
            bid_evidence_retrieval_profile_version=(
                "bid-evidence-retrieval-profile-v1-legacy"
            ),
            **phase3,
        )
    enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_pdf_c2_native_layout=True,
        feature_bid_assessment_pdf_c3_role_aware_retrieval=True,
        feature_bid_assessment_phase4_evidence_mcp=True,
        bid_document_parser_profile_version=parser_profile,
        bid_evidence_retrieval_profile_version=retrieval_profile,
        **phase3,
    )
    assert enabled.feature_bid_assessment_pdf_c3_role_aware_retrieval is True
    assert enabled.bid_evidence_retrieval_profile_version == retrieval_profile


def test_rq1c_query_optimizer_dependencies_fail_closed():
    parser_profile = "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
    retrieval_profile = "bid-evidence-retrieval-profile-v2-role-aware"
    query_profile = "bid-evidence-query-optimizer-profile-v1-rq1c"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_rq1a_structure_aggregation": True,
        "feature_bid_assessment_rq1b_parse_quality_gate": True,
        "feature_bid_assessment_pdf_c3_role_aware_retrieval": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "bid_tool_scope_signing_key": "x" * 32,
        "bid_document_parser_profile_version": parser_profile,
        "bid_evidence_retrieval_profile_version": retrieval_profile,
    }
    with pytest.raises(RuntimeError, match="RQ1C_QUERY_OPTIMIZER"):
        Settings(
            **common,
            feature_bid_assessment_rq1c_query_optimizer=False,
            bid_evidence_query_optimizer_profile_version=query_profile,
        )
    with pytest.raises(RuntimeError, match="QUERY_OPTIMIZER_PROFILE_VERSION"):
        Settings(
            **common,
            feature_bid_assessment_rq1c_query_optimizer=True,
            bid_evidence_query_optimizer_profile_version="tender-query-planner-v1",
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq1c_query_optimizer=True,
        bid_evidence_query_optimizer_profile_version=query_profile,
    )
    assert enabled.feature_bid_assessment_rq1c_query_optimizer is True
    assert enabled.bid_evidence_query_optimizer_profile_version == query_profile


def test_rq1d_field_aware_lexical_dependencies_fail_closed():
    parser_profile = "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
    retrieval_profile = "bid-evidence-retrieval-profile-v2-role-aware"
    query_profile = "bid-evidence-query-optimizer-profile-v1-rq1c"
    lexical_profile = "bid-evidence-lexical-profile-v1-rq1d"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_rq1a_structure_aggregation": True,
        "feature_bid_assessment_rq1b_parse_quality_gate": True,
        "feature_bid_assessment_pdf_c3_role_aware_retrieval": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "bid_tool_scope_signing_key": "x" * 32,
        "bid_document_parser_profile_version": parser_profile,
        "bid_evidence_retrieval_profile_version": retrieval_profile,
    }
    with pytest.raises(RuntimeError, match="RQ1D_FIELD_AWARE_LEXICAL"):
        Settings(
            **common,
            feature_bid_assessment_rq1c_query_optimizer=False,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            bid_evidence_lexical_search_profile_version=lexical_profile,
        )
    with pytest.raises(RuntimeError, match="LEXICAL_SEARCH_PROFILE_VERSION"):
        Settings(
            **common,
            feature_bid_assessment_rq1c_query_optimizer=True,
            bid_evidence_query_optimizer_profile_version=query_profile,
            feature_bid_assessment_rq1d_field_aware_lexical=True,
            bid_evidence_lexical_search_profile_version=(
                "bid-evidence-lexical-profile-v0-single-field"
            ),
        )
    with pytest.raises(RuntimeError, match="RQ1D_FIELD_AWARE_LEXICAL"):
        Settings(
            **common,
            feature_bid_assessment_rq1c_query_optimizer=True,
            bid_evidence_query_optimizer_profile_version=query_profile,
            feature_bid_assessment_rq1d_field_aware_lexical=False,
            bid_evidence_lexical_search_profile_version=lexical_profile,
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq1c_query_optimizer=True,
        bid_evidence_query_optimizer_profile_version=query_profile,
        feature_bid_assessment_rq1d_field_aware_lexical=True,
        bid_evidence_lexical_search_profile_version=lexical_profile,
    )
    assert enabled.feature_bid_assessment_rq1d_field_aware_lexical is True
    assert enabled.bid_evidence_lexical_search_profile_version == lexical_profile


def test_rq2a_semantic_recall_dependencies_and_frozen_model_fail_closed():
    semantic_profile = "bid-evidence-semantic-profile-v1-rq2a-bce"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_rq1a_structure_aggregation": True,
        "feature_bid_assessment_rq1b_parse_quality_gate": True,
        "feature_bid_assessment_pdf_c3_role_aware_retrieval": True,
        "feature_bid_assessment_rq1c_query_optimizer": True,
        "feature_bid_assessment_rq1d_field_aware_lexical": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "bid_tool_scope_signing_key": "x" * 32,
        "bid_document_parser_profile_version": (
            "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
        ),
        "bid_evidence_retrieval_profile_version": (
            "bid-evidence-retrieval-profile-v2-role-aware"
        ),
        "bid_evidence_query_optimizer_profile_version": (
            "bid-evidence-query-optimizer-profile-v1-rq1c"
        ),
        "bid_evidence_lexical_search_profile_version": (
            "bid-evidence-lexical-profile-v1-rq1d"
        ),
    }
    with pytest.raises(RuntimeError, match="RQ2A_SEMANTIC_RECALL"):
        Settings(
            **{**common, "feature_bid_assessment_rq1d_field_aware_lexical": False,
               "bid_evidence_lexical_search_profile_version": (
                   "bid-evidence-lexical-profile-v0-single-field"
               )},
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=semantic_profile,
            bid_evidence_semantic_provider_id="bce-milvus",
        )
    with pytest.raises(RuntimeError, match="SEMANTIC_SEARCH_PROFILE_VERSION"):
        Settings(
            **common,
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=(
                "bid-evidence-semantic-profile-v0-disabled"
            ),
            bid_evidence_semantic_provider_id="bce-milvus",
        )
    with pytest.raises(RuntimeError, match="frozen BCE"):
        Settings(
            **common,
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=semantic_profile,
            bid_evidence_semantic_provider_id="bce-milvus",
            bid_evidence_semantic_dimension=384,
        )
    with pytest.raises(RuntimeError, match="offline loading"):
        Settings(
            **common,
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=semantic_profile,
            bid_evidence_semantic_provider_id="bce-milvus",
            bid_evidence_semantic_model_offline=False,
        )
    with pytest.raises(RuntimeError, match="offline loading"):
        Settings(
            **common,
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=semantic_profile,
            bid_evidence_semantic_provider_id="bce-milvus",
            bid_evidence_semantic_model_path="C:/models/unversioned-bce",
        )
    with pytest.raises(RuntimeError, match="Milvus/lease"):
        Settings(
            **common,
            feature_bid_assessment_rq2a_semantic_recall=True,
            bid_evidence_semantic_search_profile_version=semantic_profile,
            bid_evidence_semantic_provider_id="bce-milvus",
            bid_evidence_semantic_collection="enterprise_quotation_rag",
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq2a_semantic_recall=True,
        bid_evidence_semantic_search_profile_version=semantic_profile,
        bid_evidence_semantic_provider_id="bce-milvus",
    )
    assert enabled.feature_bid_assessment_rq2a_semantic_recall is True
    assert enabled.bid_evidence_semantic_dimension == 768


def test_rq2b_candidate_fusion_requires_the_full_rq2a_chain():
    fusion_profile = "bid-evidence-candidate-fusion-profile-v1-rq2b"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_rq1a_structure_aggregation": True,
        "feature_bid_assessment_rq1b_parse_quality_gate": True,
        "feature_bid_assessment_pdf_c3_role_aware_retrieval": True,
        "feature_bid_assessment_rq1c_query_optimizer": True,
        "feature_bid_assessment_rq1d_field_aware_lexical": True,
        "feature_bid_assessment_rq2a_semantic_recall": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "bid_tool_scope_signing_key": "x" * 32,
        "bid_document_parser_profile_version": (
            "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
        ),
        "bid_evidence_retrieval_profile_version": (
            "bid-evidence-retrieval-profile-v2-role-aware"
        ),
        "bid_evidence_query_optimizer_profile_version": (
            "bid-evidence-query-optimizer-profile-v1-rq1c"
        ),
        "bid_evidence_lexical_search_profile_version": (
            "bid-evidence-lexical-profile-v1-rq1d"
        ),
        "bid_evidence_semantic_search_profile_version": (
            "bid-evidence-semantic-profile-v1-rq2a-bce"
        ),
        "bid_evidence_semantic_provider_id": "bce-milvus",
    }
    with pytest.raises(RuntimeError, match="RQ2B_CANDIDATE_FUSION"):
        Settings(
            **{
                **common,
                "feature_bid_assessment_rq2a_semantic_recall": False,
                "bid_evidence_semantic_search_profile_version": (
                    "bid-evidence-semantic-profile-v0-disabled"
                ),
                "bid_evidence_semantic_provider_id": "disabled",
            },
            feature_bid_assessment_rq2b_candidate_fusion=True,
            bid_evidence_candidate_fusion_profile_version=fusion_profile,
        )
    with pytest.raises(RuntimeError, match="CANDIDATE_FUSION_PROFILE_VERSION"):
        Settings(
            **common,
            feature_bid_assessment_rq2b_candidate_fusion=True,
            bid_evidence_candidate_fusion_profile_version=(
                "bid-evidence-candidate-fusion-profile-v0-disabled"
            ),
        )
    with pytest.raises(RuntimeError, match="candidate fusion profile"):
        Settings(
            **common,
            feature_bid_assessment_rq2b_candidate_fusion=False,
            bid_evidence_candidate_fusion_profile_version=fusion_profile,
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq2b_candidate_fusion=True,
        bid_evidence_candidate_fusion_profile_version=fusion_profile,
    )
    assert enabled.feature_bid_assessment_rq2b_candidate_fusion is True
    assert enabled.bid_evidence_candidate_fusion_profile_version == fusion_profile


def test_rq2c_lightweight_rerank_requires_frozen_rq2b_and_bce_profile():
    fusion_profile = "bid-evidence-candidate-fusion-profile-v1-rq2b"
    rerank_profile = "bid-evidence-rerank-profile-v1-rq2c-bce"
    common = {
        "strict_config": False,
        "database_url": "sqlite:///./runtime.db",
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase2_document_worker": True,
        "feature_bid_assessment_pdf_c2_native_layout": True,
        "feature_bid_assessment_rq1a_structure_aggregation": True,
        "feature_bid_assessment_rq1b_parse_quality_gate": True,
        "feature_bid_assessment_pdf_c3_role_aware_retrieval": True,
        "feature_bid_assessment_rq1c_query_optimizer": True,
        "feature_bid_assessment_rq1d_field_aware_lexical": True,
        "feature_bid_assessment_rq2a_semantic_recall": True,
        "feature_bid_assessment_rq2b_candidate_fusion": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "bid_tool_scope_signing_key": "x" * 32,
        "bid_document_parser_profile_version": (
            "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
        ),
        "bid_evidence_retrieval_profile_version": (
            "bid-evidence-retrieval-profile-v2-role-aware"
        ),
        "bid_evidence_query_optimizer_profile_version": (
            "bid-evidence-query-optimizer-profile-v1-rq1c"
        ),
        "bid_evidence_lexical_search_profile_version": (
            "bid-evidence-lexical-profile-v1-rq1d"
        ),
        "bid_evidence_semantic_search_profile_version": (
            "bid-evidence-semantic-profile-v1-rq2a-bce"
        ),
        "bid_evidence_semantic_provider_id": "bce-milvus",
        "bid_evidence_candidate_fusion_profile_version": fusion_profile,
    }
    with pytest.raises(RuntimeError, match="RQ2C_LIGHTWEIGHT_RERANK"):
        Settings(
            **{
                **common,
                "feature_bid_assessment_rq2b_candidate_fusion": False,
                "bid_evidence_candidate_fusion_profile_version": (
                    "bid-evidence-candidate-fusion-profile-v0-disabled"
                ),
            },
            feature_bid_assessment_rq2c_lightweight_rerank=True,
            bid_evidence_rerank_profile_version=rerank_profile,
            bid_evidence_reranker_provider_id="bce-cross-encoder-local",
        )
    with pytest.raises(RuntimeError, match="RERANK_PROFILE_VERSION"):
        Settings(
            **common,
            feature_bid_assessment_rq2c_lightweight_rerank=True,
            bid_evidence_rerank_profile_version=(
                "bid-evidence-rerank-profile-v0-disabled"
            ),
            bid_evidence_reranker_provider_id="bce-cross-encoder-local",
        )
    with pytest.raises(RuntimeError, match="frozen BCE reranker"):
        Settings(
            **common,
            feature_bid_assessment_rq2c_lightweight_rerank=True,
            bid_evidence_rerank_profile_version=rerank_profile,
            bid_evidence_reranker_provider_id="bce-cross-encoder-local",
            bid_evidence_reranker_model_revision="floating-main",
        )
    with pytest.raises(RuntimeError, match="offline loading"):
        Settings(
            **common,
            feature_bid_assessment_rq2c_lightweight_rerank=True,
            bid_evidence_rerank_profile_version=rerank_profile,
            bid_evidence_reranker_provider_id="bce-cross-encoder-local",
            bid_evidence_reranker_offline=False,
        )
    with pytest.raises(RuntimeError, match="rerank profile/provider"):
        Settings(
            **common,
            feature_bid_assessment_rq2c_lightweight_rerank=False,
            bid_evidence_rerank_profile_version=rerank_profile,
        )
    enabled = Settings(
        **common,
        feature_bid_assessment_rq2c_lightweight_rerank=True,
        bid_evidence_rerank_profile_version=rerank_profile,
        bid_evidence_reranker_provider_id="bce-cross-encoder-local",
    )
    assert enabled.feature_bid_assessment_rq2c_lightweight_rerank is True
    assert enabled.bid_evidence_reranker_batch_size == 8


def test_phase3_closeout_complete_runtime_requires_the_full_a_to_g_chain():
    disabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase3_complete_runtime=False,
    )
    assert disabled.feature_bid_assessment_phase3_complete_runtime is False

    full_chain = {
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "bid_tool_scope_signing_key": "x" * 32,
    }
    enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase3_complete_runtime=True,
        **full_chain,
    )
    assert enabled.feature_bid_assessment_phase3_complete_runtime is True

    with pytest.raises(RuntimeError, match="complete Phase 3 A-G chain"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase3_complete_runtime=False,
            **full_chain,
        )

    for field_name, enabled_value in full_chain.items():
        if field_name == "bid_tool_scope_signing_key" or not enabled_value:
            continue
        incomplete = dict(full_chain)
        incomplete[field_name] = False
        with pytest.raises(RuntimeError, match="COMPLETE_RUNTIME") as exc_info:
            Settings(
                strict_config=False,
                database_url="sqlite:///./runtime.db",
                feature_bid_assessment_phase3_complete_runtime=True,
                **incomplete,
            )
        expected_env_name = field_name.upper()
        assert expected_env_name in str(exc_info.value)


def test_phase4a1_plan_continuation_requires_the_phase3_complete_profile():
    disabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_mvp=False,
        feature_bid_assessment_phase4_plan_continuation=False,
    )
    assert disabled.feature_bid_assessment_phase4_mvp is False
    assert disabled.feature_bid_assessment_phase4_plan_continuation is False

    with pytest.raises(RuntimeError, match="PHASE3_COMPLETE_RUNTIME"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_plan_continuation=True,
            feature_bid_assessment_phase3_complete_runtime=False,
        )
    with pytest.raises(RuntimeError, match="PHASE3_COMPLETE_RUNTIME"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_model_executor=True,
            feature_bid_assessment_phase3_complete_runtime=False,
        )


def test_phase4_mvp_and_slice_dependencies_fail_closed():
    phase3_chain = {
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "bid_tool_scope_signing_key": "x" * 32,
    }
    with pytest.raises(RuntimeError, match="requires the complete Phase 3 runtime"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_mvp=True,
            **phase3_chain,
        )

    with pytest.raises(RuntimeError, match="PHASE4_PLAN_CONTINUATION"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_mvp=False,
            feature_bid_assessment_phase4_local_agent=True,
            **phase3_chain,
        )

    phase4a2 = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_mvp=False,
        feature_bid_assessment_phase4_plan_continuation=True,
        feature_bid_assessment_phase4_local_agent=True,
        feature_bid_assessment_phase4_model_executor=True,
        **phase3_chain,
    )
    assert phase4a2.feature_bid_assessment_phase4_local_agent is True
    assert phase4a2.feature_bid_assessment_phase4_model_executor is True

    with pytest.raises(RuntimeError, match="FACT_AUTHORITY requires.*EVIDENCE_MCP"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_mvp=False,
            feature_bid_assessment_phase4_plan_continuation=True,
            feature_bid_assessment_phase4_local_agent=True,
            feature_bid_assessment_phase4_model_executor=True,
            feature_bid_assessment_phase4_fact_authority=True,
            **phase3_chain,
        )

    with pytest.raises(RuntimeError, match="complete Phase 4 MVP chain"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_mvp=False,
            feature_bid_assessment_phase4_plan_continuation=True,
            feature_bid_assessment_phase4_local_agent=True,
            feature_bid_assessment_phase4_model_executor=True,
            feature_bid_assessment_phase4_evidence_mcp=True,
            feature_bid_assessment_phase4_fact_authority=True,
            feature_bid_assessment_phase4_preliminary_report=True,
            **phase3_chain,
        )

    complete = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_mvp=True,
        feature_bid_assessment_phase4_plan_continuation=True,
        feature_bid_assessment_phase4_local_agent=True,
        feature_bid_assessment_phase4_model_executor=True,
        feature_bid_assessment_phase4_evidence_mcp=True,
        feature_bid_assessment_phase4_fact_authority=True,
        feature_bid_assessment_phase4_preliminary_report=True,
        **phase3_chain,
    )
    assert complete.feature_bid_assessment_phase4_mvp is True


def test_phase4c3_release_candidate_requires_enterprise_and_report_authority():
    phase3_chain = {
        "feature_bid_assessment_v1_runtime": True,
        "feature_bid_assessment_phase3_run_bootstrap": True,
        "feature_bid_assessment_phase3_planner": True,
        "feature_bid_assessment_phase3_task_runtime": True,
        "feature_bid_assessment_phase3_run_lifecycle": True,
        "feature_bid_assessment_phase3_tool_context": True,
        "feature_bid_assessment_phase3_tool_executor": True,
        "feature_bid_assessment_phase3_run_validation": True,
        "feature_bid_assessment_phase3_complete_runtime": True,
        "bid_tool_scope_signing_key": "x" * 32,
    }
    phase4_chain = {
        "feature_bid_assessment_phase4_mvp": True,
        "feature_bid_assessment_phase4_plan_continuation": True,
        "feature_bid_assessment_phase4_local_agent": True,
        "feature_bid_assessment_phase4_model_executor": True,
        "feature_bid_assessment_phase4_evidence_mcp": True,
        "feature_bid_assessment_phase4_fact_authority": True,
        "feature_bid_assessment_phase4_preliminary_report": True,
    }
    disabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        **phase3_chain,
        **phase4_chain,
    )
    assert disabled.feature_bid_assessment_phase4_mvp_release_candidate is False
    assert disabled.feature_bid_assessment_phase4_business_baseline is False
    assert disabled.feature_bid_assessment_phase4_enterprise_evidence_import is False
    assert disabled.feature_bid_assessment_phase4_fact_verification is False

    with pytest.raises(RuntimeError, match="MVP_RELEASE_CANDIDATE requires.*ENTERPRISE"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_mvp_release_candidate=True,
            **phase3_chain,
            **phase4_chain,
        )

    enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        **phase3_chain,
        **phase4_chain,
    )
    assert enabled.feature_bid_assessment_phase4_mvp_release_candidate is True

    with pytest.raises(RuntimeError, match="BUSINESS_BASELINE requires.*ENTERPRISE"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_business_baseline=True,
            **phase3_chain,
            **phase4_chain,
        )

    business_enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        feature_bid_assessment_phase4_business_baseline=True,
        **phase3_chain,
        **phase4_chain,
    )
    assert business_enabled.feature_bid_assessment_phase4_business_baseline is True

    with pytest.raises(RuntimeError, match="ENTERPRISE_EVIDENCE_IMPORT.*BUSINESS_BASELINE"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_enterprise_evidence_import=True,
            **phase3_chain,
            **phase4_chain,
        )

    evidence_import_enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_enterprise_evidence_import=True,
        **phase3_chain,
        **phase4_chain,
    )
    assert evidence_import_enabled.feature_bid_assessment_phase4_enterprise_evidence_import is True

    with pytest.raises(RuntimeError, match="FACT_VERIFICATION requires.*EVIDENCE_IMPORT"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_enterprise_capability=True,
            feature_bid_assessment_phase4_mvp_release_candidate=True,
            feature_bid_assessment_phase4_business_baseline=True,
            feature_bid_assessment_phase4_fact_verification=True,
            **phase3_chain,
            **phase4_chain,
        )

    fact_verification_enabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_enterprise_capability=True,
        feature_bid_assessment_phase4_mvp_release_candidate=True,
        feature_bid_assessment_phase4_business_baseline=True,
        feature_bid_assessment_phase4_enterprise_evidence_import=True,
        feature_bid_assessment_phase4_fact_verification=True,
        **phase3_chain,
        **phase4_chain,
    )
    assert fact_verification_enabled.feature_bid_assessment_phase4_fact_verification is True


def test_phase4b1_deepseek_adapter_and_local_mode_fail_closed():
    disabled = Settings(
        strict_config=False,
        database_url="sqlite:///./runtime.db",
        feature_bid_assessment_phase4_deepseek_adapter=False,
        bid_mvp1_local_model_mode="deterministic",
    )
    assert disabled.feature_bid_assessment_phase4_deepseek_adapter is False

    with pytest.raises(RuntimeError, match="DEEPSEEK_ADAPTER requires"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            feature_bid_assessment_phase4_deepseek_adapter=True,
            feature_bid_assessment_phase4_model_executor=False,
        )

    with pytest.raises(RuntimeError, match="BID_MVP1_LOCAL_MODEL_MODE"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_mvp1_local_model_mode="uncontrolled-provider",
        )

    with pytest.raises(RuntimeError, match="deepseek-v4-flash local mode"):
        Settings(
            strict_config=False,
            database_url="sqlite:///./runtime.db",
            bid_mvp1_local_model_mode="deepseek-v4-flash",
            feature_bid_assessment_phase4_deepseek_adapter=False,
        )
