import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv
from sqlalchemy.engine import make_url


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


def _env_allow_empty(name: str, default: str = "") -> str:
    """Read settings where an explicit empty value disables the feature."""

    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    if name in ENV_VALUES:
        file_value = ENV_VALUES.get(name)
        return "" if file_value is None else str(file_value).strip()
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
    migration_database_url: str = _env("MIGRATION_DATABASE_URL", "")
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
    feature_unified_quotes: bool = _env_bool("FEATURE_UNIFIED_QUOTES", False)
    feature_enterprise_quota_v2_review: bool = _env_bool("FEATURE_ENTERPRISE_QUOTA_V2_REVIEW", False)
    feature_dashboard_quote: bool = _env_bool("FEATURE_DASHBOARD_QUOTE", False)
    feature_client_inquiry: bool = _env_bool("FEATURE_CLIENT_INQUIRY", False)
    feature_dashboard_response: bool = _env_bool("FEATURE_DASHBOARD_RESPONSE", False)
    feature_cost_db: bool = _env_bool("FEATURE_COST_DB", False)
    feature_project_cost_import: bool = _env_bool("FEATURE_PROJECT_COST_IMPORT", False)
    feature_requirement_standardization: bool = _env_bool("FEATURE_REQUIREMENT_STANDARDIZATION", False)
    feature_budget_projects: bool = _env_bool("FEATURE_BUDGET_PROJECTS", False)
    feature_budget_pricing: bool = _env_bool("FEATURE_BUDGET_PRICING", False)
    feature_budget_pricing_drafts: bool = _env_bool("FEATURE_BUDGET_PRICING_DRAFTS", False)
    feature_budget_pricing_ai_estimate: bool = _env_bool("FEATURE_BUDGET_PRICING_AI_ESTIMATE", False)
    feature_account_quotas: bool = _env_bool("FEATURE_ACCOUNT_QUOTAS", False)
    feature_account_quota_draft_sync: bool = _env_bool("FEATURE_ACCOUNT_QUOTA_DRAFT_SYNC", False)
    feature_pricing_agent: bool = _env_bool("FEATURE_PRICING_AGENT", False)
    feature_pricing_agent_expanded_match: bool = _env_bool("FEATURE_PRICING_AGENT_EXPANDED_MATCH", False)
    feature_pricing_agent_industry_estimate: bool = _env_bool("FEATURE_PRICING_AGENT_INDUSTRY_ESTIMATE", False)
    feature_pricing_agent_hybrid_search: bool = _env_bool("FEATURE_PRICING_AGENT_HYBRID_SEARCH", False)
    pricing_agent_hybrid_top_k: int = _env_int("PRICING_AGENT_HYBRID_TOP_K", 20)
    pricing_agent_hybrid_shard_rows: int = _env_int("PRICING_AGENT_HYBRID_SHARD_ROWS", 5000)
    pricing_agent_hybrid_min_vector_score: float = _env_float(
        "PRICING_AGENT_HYBRID_MIN_VECTOR_SCORE",
        0.72,
    )
    pricing_agent_archive_storage_backend: str = _env("PRICING_AGENT_ARCHIVE_STORAGE_BACKEND", "auto")
    pricing_agent_archive_local_root: str = _env(
        "PRICING_AGENT_ARCHIVE_LOCAL_ROOT",
        str(BASE_DIR / "data" / "pricing_agent_archives"),
    )
    pricing_agent_archive_max_upload_mb: int = _env_int("PRICING_AGENT_ARCHIVE_MAX_UPLOAD_MB", 30)
    pricing_agent_archive_account_quota_gb: int = _env_int("PRICING_AGENT_ARCHIVE_ACCOUNT_QUOTA_GB", 20)
    pricing_agent_archive_max_indexed_rows: int = _env_int("PRICING_AGENT_ARCHIVE_MAX_INDEXED_ROWS", 100000)
    feature_bidding_mvp: bool = _env_bool("FEATURE_BIDDING_MVP", False)
    feature_bidding_llm_review: bool = _env_bool("FEATURE_BIDDING_LLM_REVIEW", False)
    # New bid-assessment v1 runtime. It stays fail-closed until 0083-0086 are
    # migrated and the dedicated API/worker rollout gate is approved.
    feature_bid_assessment_v1_runtime: bool = _env_bool(
        "FEATURE_BID_ASSESSMENT_V1_RUNTIME",
        False,
    )
    bid_outbox_poll_seconds: float = _env_float("BID_OUTBOX_POLL_SECONDS", 1.0)
    bid_outbox_batch_size: int = _env_int("BID_OUTBOX_BATCH_SIZE", 20)
    bid_outbox_lease_seconds: int = _env_int("BID_OUTBOX_LEASE_SECONDS", 60)
    bid_outbox_max_attempts: int = _env_int("BID_OUTBOX_MAX_ATTEMPTS", 10)
    bid_public_event_retention_days: int = _env_int(
        "BID_PUBLIC_EVENT_RETENTION_DAYS",
        7,
    )
    bid_sse_poll_seconds: float = _env_float("BID_SSE_POLL_SECONDS", 1.0)
    bid_sse_keepalive_seconds: int = _env_int("BID_SSE_KEEPALIVE_SECONDS", 15)
    bid_upload_batch_ttl_days: int = _env_int("BID_UPLOAD_BATCH_TTL_DAYS", 7)
    bid_upload_max_files: int = _env_int("BID_UPLOAD_MAX_FILES", 100)
    bid_upload_max_file_bytes: int = _env_int("BID_UPLOAD_MAX_FILE_BYTES", 209715200)
    bid_upload_max_batch_bytes: int = _env_int("BID_UPLOAD_MAX_BATCH_BYTES", 1073741824)
    bid_upload_read_chunk_bytes: int = _env_int(
        "BID_UPLOAD_READ_CHUNK_BYTES",
        1048576,
    )
    bid_upload_minio_part_size_bytes: int = _env_int(
        "BID_UPLOAD_MINIO_PART_SIZE_BYTES",
        10485760,
    )
    bid_upload_processing_timeout_seconds: int = _env_int(
        "BID_UPLOAD_PROCESSING_TIMEOUT_SECONDS",
        3600,
    )
    bid_upload_object_prefix: str = _env(
        "BID_UPLOAD_OBJECT_PREFIX",
        "bid-assessment/uploading/v1",
    )
    bid_upload_orphan_grace_seconds: int = _env_int(
        "BID_UPLOAD_ORPHAN_GRACE_SECONDS",
        86400,
    )
    bid_upload_accepted_extensions: List[str] = field(
        default_factory=lambda: _env_list(
            "BID_UPLOAD_ACCEPTED_EXTENSIONS",
            "pdf,docx,xlsx,xlsm,png,jpg,jpeg,txt,md",
        )
    )
    feature_enterprise_profile: bool = _env_bool("FEATURE_ENTERPRISE_PROFILE", False)
    feature_no_cost_draft_capture: bool = _env_bool("FEATURE_NO_COST_DRAFT_CAPTURE", False)
    feature_project_progress: bool = _env_bool("FEATURE_PROJECT_PROGRESS", False)
    feature_dashboard_project: bool = _env_bool("FEATURE_DASHBOARD_PROJECT", False)
    feature_dashboard_business_lite: bool = _env_bool("FEATURE_DASHBOARD_BUSINESS_LITE", False)
    feature_agent_assistants: bool = _env_bool("FEATURE_AGENT_ASSISTANTS", False)
    feature_agent_daily_review: bool = _env_bool("FEATURE_AGENT_DAILY_REVIEW", False)
    feature_agent_llm_explanation: bool = _env_bool("FEATURE_AGENT_LLM_EXPLANATION", True)
    feature_pdf_tile_vision: bool = _env_bool("FEATURE_PDF_TILE_VISION", True)
    pdf_tile_vision_max_tiles: int = _env_int("PDF_TILE_VISION_MAX_TILES", 4)
    pdf_tile_vision_concurrency: int = _env_int("PDF_TILE_VISION_CONCURRENCY", 3)
    feature_pdf_direct_itemization: bool = _env_bool("FEATURE_PDF_DIRECT_ITEMIZATION", True)
    pdf_itemization_provider: str = _env("PDF_ITEMIZATION_PROVIDER", "glm")
    pdf_direct_itemization_max_images: int = _env_int("PDF_DIRECT_ITEMIZATION_MAX_IMAGES", 40)
    feature_pdf_ai_quantity_suggestion: bool = _env_bool("FEATURE_PDF_AI_QUANTITY_SUGGESTION", True)
    pdf_ai_quantity_suggestion_max_images: int = _env_int("PDF_AI_QUANTITY_SUGGESTION_MAX_IMAGES", 4)
    agent_llm_provider: str = _env("AGENT_LLM_PROVIDER", "rule")
    agent_llm_prompt_version: str = _env("AGENT_LLM_PROMPT_VERSION", "quote_review_explanation_v1")
    agent_llm_timeout_seconds: int = _env_int("AGENT_LLM_TIMEOUT_SECONDS", 20)
    budget_pricing_ai_provider: str = _env("BUDGET_PRICING_AI_PROVIDER", "rule")
    budget_pricing_ai_model: str = _env("BUDGET_PRICING_AI_MODEL", "deepseek-v4-flash")
    budget_pricing_ai_prompt_version: str = _env("BUDGET_PRICING_AI_PROMPT_VERSION", "budget_pricing_ai_estimate_p2_2c1")
    budget_pricing_ai_timeout_seconds: int = _env_int("BUDGET_PRICING_AI_TIMEOUT_SECONDS", 90)
    deepseek_api_key: str = _env("DEEPSEEK_API_KEY", "")
    deepseek_chat_url: str = _env("DEEPSEEK_CHAT_URL", "https://api.deepseek.com/chat/completions")
    deepseek_model: str = _env("DEEPSEEK_MODEL", "deepseek-chat")
    bidding_llm_provider: str = _env("BIDDING_LLM_PROVIDER", "deepseek")
    bidding_llm_model: str = _env("BIDDING_LLM_MODEL", "deepseek-v4-pro")
    bidding_llm_timeout_seconds: int = _env_int("BIDDING_LLM_TIMEOUT_SECONDS", 0)
    bidding_llm_max_objects: int = _env_int("BIDDING_LLM_MAX_OBJECTS", 25)
    feature_agent_market_web_search: bool = _env_bool("FEATURE_AGENT_MARKET_WEB_SEARCH", False)
    market_search_provider: str = _env("MARKET_SEARCH_PROVIDER", "tavily")
    market_search_api_key: str = _env("MARKET_SEARCH_API_KEY", _env("BING_SEARCH_API_KEY", ""))
    market_search_endpoint: str = _env("MARKET_SEARCH_ENDPOINT", "https://api.tavily.com/search")
    market_search_max_results: int = _env_int("MARKET_SEARCH_MAX_RESULTS", 5)
    market_search_timeout_seconds: int = _env_int("MARKET_SEARCH_TIMEOUT_SECONDS", 10)
    # Legacy Bing fields are retained only so older .env files do not break settings loading.
    bing_search_api_key: str = _env("BING_SEARCH_API_KEY", "")
    bing_search_endpoint: str = _env("BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
    agent_daily_review_timezone: str = _env("AGENT_DAILY_REVIEW_TIMEZONE", "Asia/Shanghai")
    agent_daily_review_run_time: str = _env("AGENT_DAILY_REVIEW_RUN_TIME", "18:30")
    agent_daily_review_max_jobs: int = _env_int("AGENT_DAILY_REVIEW_MAX_JOBS", 100)
    agent_daily_review_poll_seconds: int = _env_int("AGENT_DAILY_REVIEW_POLL_SECONDS", 60)
    agent_daily_review_catchup_minutes: int = _env_int("AGENT_DAILY_REVIEW_CATCHUP_MINUTES", 120)
    response_sla_minutes: int = _env_int("RESPONSE_SLA_MINUTES", 30)
    public_access_enabled: bool = _env_bool("PUBLIC_ACCESS_ENABLED", False)
    allow_self_registration: bool = _env_bool("ALLOW_SELF_REGISTRATION", False)

    zhipu_api_key: str = _env("ZHIPU_API_KEY")
    webhook_secret: str = _env("WEBHOOK_SECRET")
    reload_secret: str = _env("RELOAD_SECRET")
    n8n_webhook_url_calc: str = _env("N8N_WEBHOOK_URL_CALC", "http://192.168.88.128:5678/webhook/budget-calc-no-rag")
    n8n_webhook_url_push: str = _env("N8N_WEBHOOK_URL_PUSH", "http://192.168.88.128:5678/webhook/budget-push")
    rag_service_url: str = _env("RAG_SERVICE_URL", "http://192.168.88.128:8001")
    rag_reload_timeout_seconds: float = _env_float("RAG_RELOAD_TIMEOUT_SECONDS", 900.0)
    dify_app_version: str = _env("DIFY_APP_VERSION", "")
    dify_workflow_version: str = _env("DIFY_WORKFLOW_VERSION", "")
    dify_prompt_version: str = _env("DIFY_PROMPT_VERSION", "")
    dify_release_id: str = _env("DIFY_RELEASE_ID", "")
    rag_collection_alias: str = _env("RAG_COLLECTION_ALIAS", "enterprise_quotation_rag")
    task_queue_mode: str = _env("TASK_QUEUE_MODE", "local")
    celery_broker_url: str = _env("CELERY_BROKER_URL", "redis://192.168.88.128:6380/0")
    celery_result_backend: str = _env("CELERY_RESULT_BACKEND", "redis://192.168.88.128:6380/1")
    celery_worker_pool: str = _env("CELERY_WORKER_POOL", "threads")
    celery_worker_concurrency: int = _env_int("CELERY_WORKER_CONCURRENCY", 2)
    quote_task_time_limit_seconds: int = _env_int("QUOTE_TASK_TIME_LIMIT_SECONDS", 240)
    quote_n8n_timeout_seconds: int = _env_int("QUOTE_N8N_TIMEOUT_SECONDS", 180)
    quote_job_heartbeat_interval_seconds: float = _env_float("QUOTE_JOB_HEARTBEAT_INTERVAL_SECONDS", 15.0)
    queue_health_timeout_seconds: float = _env_float("QUEUE_HEALTH_TIMEOUT_SECONDS", 1.5)
    ready_check_external_services: bool = _env_bool("READY_CHECK_EXTERNAL_SERVICES", False)
    ops_probe_timeout_seconds: float = _env_float("OPS_PROBE_TIMEOUT_SECONDS", 2.0)
    ops_probe_attempts: int = _env_int("OPS_PROBE_ATTEMPTS", 2)
    ops_probe_retry_delay_seconds: float = _env_float("OPS_PROBE_RETRY_DELAY_SECONDS", 0.3)
    ops_stuck_job_minutes: int = _env_int("OPS_STUCK_JOB_MINUTES", 30)
    ops_log_scan_lines: int = _env_int("OPS_LOG_SCAN_LINES", 800)
    ops_log_max_files: int = _env_int("OPS_LOG_MAX_FILES", 6)
    ops_log_lookback_minutes: int = _env_int("OPS_LOG_LOOKBACK_MINUTES", 180)
    ops_log_current_minutes: int = _env_int("OPS_LOG_CURRENT_MINUTES", 30)
    alert_dingtalk_webhook: str = _env("ALERT_DINGTALK_WEBHOOK", "")
    alert_dingtalk_secret: str = _env("ALERT_DINGTALK_SECRET", "")
    alert_dedup_minutes: int = _env_int("ALERT_DEDUP_MINUTES", 30)
    alert_rate_limit_count: int = _env_int("ALERT_RATE_LIMIT_COUNT", 3)
    alert_rate_limit_window_minutes: int = _env_int("ALERT_RATE_LIMIT_WINDOW_MINUTES", 5)
    alert_check_interval_seconds: int = _env_int("ALERT_CHECK_INTERVAL_SECONDS", 60)
    quote_vision_provider: str = _env("QUOTE_VISION_PROVIDER", "glm")
    glm_vision_model: str = _env("GLM_VISION_MODEL", "glm-4v-flash")
    glm_vision_url: str = _env("GLM_VISION_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    openai_api_key: str = _env("OPENAI_API_KEY", "")
    openai_vision_model: str = _env("OPENAI_VISION_MODEL", "gpt-4.1")
    openai_responses_url: str = _env("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
    openai_drawing_agent_timeout_seconds: int = _env_int("OPENAI_DRAWING_AGENT_TIMEOUT_SECONDS", 120)
    openai_drawing_agent_max_views: int = _env_int("OPENAI_DRAWING_AGENT_MAX_VIEWS", 24)
    openai_drawing_agent_batch_size: int = _env_int("OPENAI_DRAWING_AGENT_BATCH_SIZE", 8)
    openai_drawing_agent_include_whole_page: bool = _env_bool("OPENAI_DRAWING_AGENT_INCLUDE_WHOLE_PAGE", True)
    dashscope_api_key: str = _env("DASHSCOPE_API_KEY", "")
    dashscope_base_url: str = _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    dashscope_vision_model: str = _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max")
    dashscope_evidence_model: str = _env("DASHSCOPE_EVIDENCE_MODEL", _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max"))
    dashscope_bill_summary_model: str = _env(
        "DASHSCOPE_BILL_SUMMARY_MODEL",
        _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max"),
    )
    dashscope_timeout_seconds: int = _env_int("DASHSCOPE_TIMEOUT_SECONDS", 120)
    dashscope_temperature: float = _env_float("DASHSCOPE_TEMPERATURE", 0.2)
    dashscope_drawing_agent_max_views: int = _env_int("DASHSCOPE_DRAWING_AGENT_MAX_VIEWS", 24)
    dashscope_drawing_agent_batch_size: int = _env_int("DASHSCOPE_DRAWING_AGENT_BATCH_SIZE", 8)
    dashscope_drawing_agent_include_whole_page: bool = _env_bool("DASHSCOPE_DRAWING_AGENT_INCLUDE_WHOLE_PAGE", True)
    drawing_layout_planner_provider: str = _env("DRAWING_LAYOUT_PLANNER_PROVIDER", "dashscope")
    drawing_layout_planner_model: str = _env(
        "DRAWING_LAYOUT_PLANNER_MODEL",
        _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max"),
    )
    drawing_layout_planner_max_pages: int = _env_int("DRAWING_LAYOUT_PLANNER_MAX_PAGES", 3)
    drawing_region_crop_max_regions: int = _env_int("DRAWING_REGION_CROP_MAX_REGIONS", 24)
    drawing_cad_view_detail_planner_model: str = _env(
        "DRAWING_CAD_VIEW_DETAIL_PLANNER_MODEL",
        _env("DRAWING_LAYOUT_PLANNER_MODEL", _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max")),
    )
    drawing_material_region_planner_model: str = _env(
        "DRAWING_MATERIAL_REGION_PLANNER_MODEL",
        _env("DRAWING_CAD_VIEW_DETAIL_PLANNER_MODEL", _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max")),
    )
    drawing_material_region_planner_max_pages: int = _env_int("DRAWING_MATERIAL_REGION_PLANNER_MAX_PAGES", 2)
    drawing_material_region_planner_max_cad_views: int = _env_int("DRAWING_MATERIAL_REGION_PLANNER_MAX_CAD_VIEWS", 8)
    drawing_material_region_planner_max_regions: int = _env_int("DRAWING_MATERIAL_REGION_PLANNER_MAX_REGIONS", 32)
    drawing_cad_view_detail_max_views: int = _env_int("DRAWING_CAD_VIEW_DETAIL_MAX_VIEWS", 24)
    drawing_cad_view_detail_max_regions: int = _env_int("DRAWING_CAD_VIEW_DETAIL_MAX_REGIONS", 48)
    drawing_highres_region_render_enabled: bool = _env_bool("DRAWING_HIGHRES_REGION_RENDER_ENABLED", True)
    drawing_highres_region_default_scale: float = _env_float("DRAWING_HIGHRES_REGION_DEFAULT_SCALE", 64.0)
    drawing_highres_region_max_scale: float = _env_float("DRAWING_HIGHRES_REGION_MAX_SCALE", 96.0)
    drawing_highres_region_max_pixels: int = _env_int("DRAWING_HIGHRES_REGION_MAX_PIXELS", 32000000)
    drawing_highres_region_min_width_px: int = _env_int("DRAWING_HIGHRES_REGION_MIN_WIDTH_PX", 1200)
    drawing_highres_region_min_height_px: int = _env_int("DRAWING_HIGHRES_REGION_MIN_HEIGHT_PX", 300)
    drawing_highres_region_use_for_ocr: bool = _env_bool("DRAWING_HIGHRES_REGION_USE_FOR_OCR", False)
    model_gateway_timeout_seconds: int = _env_int("MODEL_GATEWAY_TIMEOUT_SECONDS", 20)
    model_gateway_failure_threshold: int = _env_int("MODEL_GATEWAY_FAILURE_THRESHOLD", 3)
    model_gateway_circuit_reset_seconds: int = _env_int("MODEL_GATEWAY_CIRCUIT_RESET_SECONDS", 60)
    model_gateway_cost_per_1k_chars: float = _env_float("MODEL_GATEWAY_COST_PER_1K_CHARS", 0.0)
    minio_enabled: bool = _env_bool("MINIO_ENABLED", False)
    minio_endpoint: str = _env("MINIO_ENDPOINT", "192.168.88.128:9002")
    minio_access_key: str = _env("MINIO_ACCESS_KEY", "")
    minio_secret_key: str = _env("MINIO_SECRET_KEY", "")
    minio_secure: bool = _env_bool("MINIO_SECURE", False)
    minio_bucket: str = _env("MINIO_BUCKET", "quote-files")
    minio_presigned_expire_seconds: int = _env_int("MINIO_PRESIGNED_EXPIRE_SECONDS", 3600)
    minio_max_upload_mb: int = _env_int("MINIO_MAX_UPLOAD_MB", 50)
    # Development/test-only fallback for original-format budget exports when
    # object storage is intentionally disabled. The exporter still verifies
    # the workbook against the immutable import SHA-256.
    budget_pricing_local_source_root: str = _env("BUDGET_PRICING_LOCAL_SOURCE_ROOT", "")
    tender_evidence_body_storage_enabled: bool = _env_bool(
        "TENDER_EVIDENCE_BODY_STORAGE_ENABLED",
        False,
    )
    rag_eval_enabled: bool = _env_bool("RAG_EVAL_ENABLED", True)
    rag_eval_top_k: int = _env_int("RAG_EVAL_TOP_K", 5)
    rag_eval_warn_hit_rate: float = _env_float("RAG_EVAL_WARN_HIT_RATE", 0.70)
    rag_eval_warn_mrr: float = _env_float("RAG_EVAL_WARN_MRR", 0.50)
    login_rate_limit: str = _env("LOGIN_RATE_LIMIT", "10/5minutes")

    http_proxy: str = _env_allow_empty("HTTP_PROXY", "")
    https_proxy: str = _env_allow_empty("HTTPS_PROXY", "")
    no_proxy: str = _env_allow_empty(
        "NO_PROXY",
        "192.168.88.128,192.168.0.0/16,192.168.88.0/24,localhost,127.0.0.1,127.0.0.0/8",
    )
    allowed_origins: List[str] = field(default_factory=lambda: _env_list("CORS_ALLOW_ORIGINS", "*"))
    trusted_hosts: List[str] = field(default_factory=lambda: _env_list("TRUSTED_HOSTS", "*"))
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

    @property
    def internal_experimental_routes_enabled(self) -> bool:
        """Keep high-risk trial/POC routes available only in internal mode.

        These routes process untrusted CAD/PDF inputs and currently use local
        job artifacts.  They must not be mounted merely because the main
        application is switched to public-access mode.
        """

        return not self.public_access_enabled

    @property
    def alembic_database_url(self) -> str:
        """Use a dedicated migrator when configured; retain local compatibility."""

        return self.migration_database_url or self.database_url

    def __post_init__(self) -> None:
        app_env = self.app_env.lower()
        database_url = self.database_url.lower()
        uses_external_database = not database_url.startswith("sqlite:")
        should_validate_secrets = (
            self.strict_config
            or app_env in {"prod", "production"}
            or uses_external_database
            or self.public_access_enabled
        )
        if not should_validate_secrets:
            return

        errors = []

        def require_secret(
            name: str,
            value: str,
            weak_values: set[str] | None = None,
            minimum_length: int = 16,
        ) -> None:
            weak_values = weak_values or set()
            if len(value.strip()) < minimum_length or value in weak_values:
                errors.append(
                    f"{name} must be set to a non-default secret of at least "
                    f"{minimum_length} characters"
                )

        require_secret(
            "JWT_SECRET_KEY",
            self.jwt_secret_key,
            {"your_super_secret_key_for_ai_middle_office", "change-me-in-production"},
        )
        require_secret("WEBHOOK_SECRET", self.webhook_secret)
        require_secret("RELOAD_SECRET", self.reload_secret)
        require_secret("ZHIPU_API_KEY", self.zhipu_api_key)
        if self.public_access_enabled:
            if not self.allowed_origins or "*" in self.allowed_origins:
                errors.append(
                    "CORS_ALLOW_ORIGINS must list exact HTTPS origins in public mode"
                )
            else:
                invalid_origins = []
                for origin in self.allowed_origins:
                    parsed = urlsplit(origin)
                    if (
                        parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or parsed.path not in {"", "/"}
                        or parsed.query
                        or parsed.fragment
                    ):
                        invalid_origins.append(origin)
                if invalid_origins:
                    errors.append(
                        "CORS_ALLOW_ORIGINS must contain only origin-level HTTPS URLs "
                        "in public mode"
                    )

            if not self.trusted_hosts or "*" in self.trusted_hosts:
                errors.append("TRUSTED_HOSTS must list exact hostnames in public mode")
            elif any(
                "://" in host or "/" in host or "@" in host
                for host in self.trusted_hosts
            ):
                errors.append(
                    "TRUSTED_HOSTS must contain hostnames only, without schemes or paths"
                )

            if not self.migration_database_url:
                errors.append("MIGRATION_DATABASE_URL must use a dedicated migration account")
            else:
                try:
                    runtime_url = make_url(self.database_url)
                    migration_url = make_url(self.migration_database_url)
                except Exception:
                    errors.append("DATABASE_URL and MIGRATION_DATABASE_URL must be valid URLs")
                else:
                    same_account = (
                        runtime_url.username == migration_url.username
                        and runtime_url.host == migration_url.host
                        and runtime_url.port == migration_url.port
                        and runtime_url.database == migration_url.database
                    )
                    if same_account:
                        errors.append(
                            "MIGRATION_DATABASE_URL must use a distinct database account"
                        )
        if self.alert_dingtalk_webhook:
            require_secret("ALERT_DINGTALK_SECRET", self.alert_dingtalk_secret)
        if self.minio_enabled:
            require_secret("MINIO_ACCESS_KEY", self.minio_access_key)
            require_secret("MINIO_SECRET_KEY", self.minio_secret_key, {"change-this-password"})
        if not 65536 <= int(self.bid_upload_read_chunk_bytes) <= 8 * 1024 * 1024:
            errors.append(
                "BID_UPLOAD_READ_CHUNK_BYTES must be between 65536 and 8388608"
            )
        if not 5 * 1024 * 1024 <= int(self.bid_upload_minio_part_size_bytes) <= 64 * 1024 * 1024:
            errors.append(
                "BID_UPLOAD_MINIO_PART_SIZE_BYTES must be between 5242880 and 67108864"
            )
        upload_prefix = self.bid_upload_object_prefix.strip().strip("/")
        if (
            not upload_prefix
            or ".." in upload_prefix.split("/")
            or any(not segment.replace("-", "").replace("_", "").isalnum() for segment in upload_prefix.split("/"))
        ):
            errors.append("BID_UPLOAD_OBJECT_PREFIX must contain safe path segments only")

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
