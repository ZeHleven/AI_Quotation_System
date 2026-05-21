import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import dotenv_values, load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"
_RAW_ENV_VALUES = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
ENV_VALUES = {str(key).lstrip("\ufeff"): value for key, value in _RAW_ENV_VALUES.items()}
load_dotenv(ENV_FILE, override=False)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()
    file_value = ENV_VALUES.get(name)
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
    return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "*") -> List[str]:
    raw = _env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = _env("APP_NAME", "Enterprise AI Middle Office")
    app_version: str = _env("APP_VERSION", "1.0.0")
    app_env: str = _env("APP_ENV", "development")
    strict_config: bool = _env_bool("STRICT_CONFIG", False)
    log_level: str = _env("LOG_LEVEL", "INFO")

    database_url: str = _env("DATABASE_URL", "sqlite:///./sql_app.db")
    database_startup_wait_seconds: int = _env_int("DATABASE_STARTUP_WAIT_SECONDS", 120)
    database_startup_retry_interval_seconds: float = _env_float("DATABASE_STARTUP_RETRY_INTERVAL_SECONDS", 3.0)
    auto_create_tables: bool = _env_bool("AUTO_CREATE_TABLES", False)
    startup_compat_migrations: bool = _env_bool("STARTUP_COMPAT_MIGRATIONS", False)
    auto_run_db_migrations: bool = _env_bool("AUTO_RUN_DB_MIGRATIONS", True)
    jwt_secret_key: str = _env("JWT_SECRET_KEY", "your_super_secret_key_for_ai_middle_office")
    jwt_algorithm: str = _env("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = _env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24)
    system_admin_username: str = _env("SYSTEM_ADMIN_USERNAME", "admin")
    feature_vite_frontend: bool = _env_bool("FEATURE_VITE_FRONTEND", False)
    feature_dashboard_quote: bool = _env_bool("FEATURE_DASHBOARD_QUOTE", False)
    feature_client_inquiry: bool = _env_bool("FEATURE_CLIENT_INQUIRY", False)
    feature_dashboard_response: bool = _env_bool("FEATURE_DASHBOARD_RESPONSE", False)
    feature_execution: bool = _env_bool("FEATURE_EXECUTION", False)
    feature_dashboard_execution: bool = _env_bool("FEATURE_DASHBOARD_EXECUTION", False)
    feature_meeting_ai: bool = _env_bool("FEATURE_MEETING_AI", False)
    feature_audio_transcription: bool = _env_bool("FEATURE_AUDIO_TRANSCRIPTION", False)
    feature_business_ledger: bool = _env_bool("FEATURE_BUSINESS_LEDGER", False)
    feature_cost_db: bool = _env_bool("FEATURE_COST_DB", False)
    response_sla_minutes: int = _env_int("RESPONSE_SLA_MINUTES", 30)
    public_access_enabled: bool = _env_bool("PUBLIC_ACCESS_ENABLED", False)

    zhipu_api_key: str = _env("ZHIPU_API_KEY")
    webhook_secret: str = _env("WEBHOOK_SECRET")
    reload_secret: str = _env("RELOAD_SECRET")
    n8n_webhook_url_calc: str = _env("N8N_WEBHOOK_URL_CALC", "http://192.168.88.128:5678/webhook/budget-calc")
    n8n_webhook_url_push: str = _env("N8N_WEBHOOK_URL_PUSH", "http://192.168.88.128:5678/webhook/budget-push")
    rag_service_url: str = _env("RAG_SERVICE_URL", "http://192.168.88.128:8001")
    dify_app_version: str = _env("DIFY_APP_VERSION", "")
    dify_workflow_version: str = _env("DIFY_WORKFLOW_VERSION", "")
    dify_prompt_version: str = _env("DIFY_PROMPT_VERSION", "")
    dify_release_id: str = _env("DIFY_RELEASE_ID", "")
    rag_collection_alias: str = _env("RAG_COLLECTION_ALIAS", "enterprise_quotation_rag")
    task_queue_mode: str = _env("TASK_QUEUE_MODE", "local")
    celery_broker_url: str = _env("CELERY_BROKER_URL", "redis://192.168.88.128:6380/0")
    celery_result_backend: str = _env("CELERY_RESULT_BACKEND", "redis://192.168.88.128:6380/1")
    quote_task_time_limit_seconds: int = _env_int("QUOTE_TASK_TIME_LIMIT_SECONDS", 240)
    queue_health_timeout_seconds: float = _env_float("QUEUE_HEALTH_TIMEOUT_SECONDS", 1.5)
    ops_probe_timeout_seconds: float = _env_float("OPS_PROBE_TIMEOUT_SECONDS", 2.0)
    ops_stuck_job_minutes: int = _env_int("OPS_STUCK_JOB_MINUTES", 30)
    ops_log_scan_lines: int = _env_int("OPS_LOG_SCAN_LINES", 800)
    ops_log_max_files: int = _env_int("OPS_LOG_MAX_FILES", 6)
    ops_log_lookback_minutes: int = _env_int("OPS_LOG_LOOKBACK_MINUTES", 180)
    alert_dingtalk_webhook: str = _env("ALERT_DINGTALK_WEBHOOK", "")
    alert_dedup_minutes: int = _env_int("ALERT_DEDUP_MINUTES", 30)
    alert_rate_limit_count: int = _env_int("ALERT_RATE_LIMIT_COUNT", 3)
    alert_rate_limit_window_minutes: int = _env_int("ALERT_RATE_LIMIT_WINDOW_MINUTES", 5)
    alert_check_interval_seconds: int = _env_int("ALERT_CHECK_INTERVAL_SECONDS", 60)
    glm_vision_model: str = _env("GLM_VISION_MODEL", "glm-4v-flash")
    glm_vision_url: str = _env("GLM_VISION_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    model_gateway_timeout_seconds: int = _env_int("MODEL_GATEWAY_TIMEOUT_SECONDS", 20)
    model_gateway_failure_threshold: int = _env_int("MODEL_GATEWAY_FAILURE_THRESHOLD", 3)
    model_gateway_circuit_reset_seconds: int = _env_int("MODEL_GATEWAY_CIRCUIT_RESET_SECONDS", 60)
    model_gateway_cost_per_1k_chars: float = _env_float("MODEL_GATEWAY_COST_PER_1K_CHARS", 0.0)
    minio_enabled: bool = _env_bool("MINIO_ENABLED", False)
    minio_endpoint: str = _env("MINIO_ENDPOINT", "192.168.88.128:9002")
    minio_access_key: str = _env("MINIO_ACCESS_KEY", "quoteadmin")
    minio_secret_key: str = _env("MINIO_SECRET_KEY", "change-this-password")
    minio_secure: bool = _env_bool("MINIO_SECURE", False)
    minio_bucket: str = _env("MINIO_BUCKET", "quote-files")
    minio_presigned_expire_seconds: int = _env_int("MINIO_PRESIGNED_EXPIRE_SECONDS", 3600)
    minio_max_upload_mb: int = _env_int("MINIO_MAX_UPLOAD_MB", 50)
    rag_eval_enabled: bool = _env_bool("RAG_EVAL_ENABLED", True)
    rag_eval_top_k: int = _env_int("RAG_EVAL_TOP_K", 5)
    rag_eval_warn_hit_rate: float = _env_float("RAG_EVAL_WARN_HIT_RATE", 0.70)
    rag_eval_warn_mrr: float = _env_float("RAG_EVAL_WARN_MRR", 0.50)
    login_rate_limit: str = _env("LOGIN_RATE_LIMIT", "10/5minutes")

    http_proxy: str = _env("HTTP_PROXY", "http://127.0.0.1:7897")
    https_proxy: str = _env("HTTPS_PROXY", "http://127.0.0.1:7897")
    no_proxy: str = _env(
        "NO_PROXY",
        "192.168.88.128,192.168.0.0/16,192.168.88.0/24,localhost,127.0.0.1,127.0.0.0/8",
    )
    allowed_origins: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_ORIGINS", "*"))
    legacy_materials_file: Path = field(
        default_factory=lambda: Path(
            _env("LEGACY_MATERIALS_FILE", _env("MATERIALS_FILE", str(BASE_DIR / "rag_materials.json")))
        )
    )
    rag_eval_report_dir: Path = field(
        default_factory=lambda: Path(_env("RAG_EVAL_REPORT_DIR", str(BASE_DIR / "rag_eval_reports")))
    )

    @property
    def materials_file(self) -> Path:
        """Backward-compatible alias for the legacy JSON import file."""
        return self.legacy_materials_file

    def __post_init__(self) -> None:
        app_env = self.app_env.lower()
        database_url = self.database_url.lower()
        uses_external_database = not database_url.startswith("sqlite:")
        should_validate_secrets = self.strict_config or app_env in {"prod", "production"} or uses_external_database
        if not should_validate_secrets:
            return

        errors = []

        def require_secret(name: str, value: str, weak_values: set[str] | None = None) -> None:
            weak_values = weak_values or set()
            if not value or value in weak_values:
                errors.append(f"{name} must be set to a non-default secret")

        require_secret(
            "JWT_SECRET_KEY",
            self.jwt_secret_key,
            {"your_super_secret_key_for_ai_middle_office", "change-me-in-production"},
        )
        require_secret("WEBHOOK_SECRET", self.webhook_secret)
        require_secret("RELOAD_SECRET", self.reload_secret)
        require_secret("ZHIPU_API_KEY", self.zhipu_api_key)
        if self.minio_enabled:
            require_secret("MINIO_SECRET_KEY", self.minio_secret_key, {"change-this-password"})

        if errors:
            raise RuntimeError("Invalid production configuration: " + "; ".join(errors))

    def apply_proxy_env(self) -> None:
        if self.https_proxy:
            os.environ.setdefault("HTTPS_PROXY", self.https_proxy)
        if self.http_proxy:
            os.environ.setdefault("HTTP_PROXY", self.http_proxy)
        if self.no_proxy:
            os.environ.setdefault("NO_PROXY", self.no_proxy)
            os.environ.setdefault("no_proxy", self.no_proxy)


settings = Settings()
