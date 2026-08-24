"""Read-only preflight contracts for the explicit local Pure Agent entry.

The probe deliberately performs no model import, file-content extraction,
network call, database migration, or Runtime installation.  It reports only
safe capability facts and leaves every execution switch fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path
import sqlite3
from typing import Literal, Mapping, Protocol
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from sqlalchemy.engine import make_url

from .common import Reference, StrictContract
from .tool_runtime import canonical_hash


EXPECTED_LOCAL_MIGRATION_HEAD = "20260821_0110"


class LocalPreflightSettingsView(Protocol):
    app_env: str
    public_access_enabled: bool
    database_url: str
    feature_vite_frontend: bool
    feature_bid_assessment_pure_agent: bool
    feature_bid_assessment_pure_agent_runtime: bool
    bid_assessment_pure_agent_continuation_secret: str


class LocalPreflightCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class LocalPreflightCheck(StrictContract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    status: LocalPreflightCheckStatus
    required: bool = True
    message: str = Field(min_length=1, max_length=240)


class LocalPreflightReport(StrictContract):
    """Safe, hash-bound report suitable for the local status endpoint."""

    schema_name: Literal["bid.pure-agent.local-preflight.v1"] = (
        "bid.pure-agent.local-preflight.v1"
    )
    report_ref: Reference
    report_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ready: bool
    runtime_install_allowed: bool
    checks: tuple[LocalPreflightCheck, ...] = Field(min_length=1, max_length=32)

    @classmethod
    def build(
        cls,
        *,
        checks: tuple[LocalPreflightCheck, ...],
    ) -> "LocalPreflightReport":
        if not checks:
            raise ValueError("local preflight requires at least one check")
        codes = tuple(check.code for check in checks)
        if len(codes) != len(set(codes)):
            raise ValueError("local preflight check codes must be unique")
        ready = all(
            check.status is LocalPreflightCheckStatus.PASSED
            for check in checks
            if check.required
        )
        body = {
            "ready": ready,
            "runtime_install_allowed": ready,
            "checks": [check.model_dump(mode="json") for check in checks],
        }
        digest = canonical_hash(body)
        return cls(
            **body,
            report_ref=f"local-preflight:{digest.removeprefix('sha256:')}",
            report_hash=digest,
        )

    @model_validator(mode="after")
    def validate_report(self) -> "LocalPreflightReport":
        body = self.model_dump(
            mode="json",
            exclude={"schema_name", "report_ref", "report_hash"},
        )
        digest = canonical_hash(body)
        if self.report_hash != digest:
            raise ValueError("local preflight report hash does not match")
        if self.report_ref != f"local-preflight:{digest.removeprefix('sha256:')}":
            raise ValueError("local preflight report ref does not match")
        if self.runtime_install_allowed != self.ready:
            raise ValueError("only a ready preflight may allow Runtime installation")
        return self

    @property
    def failed_codes(self) -> tuple[str, ...]:
        return tuple(
            check.code
            for check in self.checks
            if check.required and check.status is LocalPreflightCheckStatus.FAILED
        )


@dataclass(frozen=True, slots=True)
class LocalPreflightProbeConfig:
    """Internal path-bearing inputs; never returned by the public report."""

    activation: str
    bind_host: str
    local_data_root: Path | None
    sqlite_database_path: Path | None
    vite_index_path: Path | None
    frozen_dataset_path: Path | None
    frozen_pdf_path: Path | None
    embedding_model_path: Path | None
    secret_env_file: Path | None
    expected_migration_head: str = EXPECTED_LOCAL_MIGRATION_HEAD
    provider_chat_url: str = "https://api.deepseek.com/chat/completions"
    external_mcp_enabled: bool = False
    milvus_enabled: bool = False
    ocr_vision_enabled: bool = False
    expected_package_versions: Mapping[str, str] | None = None
    required_application_packages: tuple[str, ...] = (
        "fastapi",
        "uvicorn",
        "slowapi",
        "SQLAlchemy",
        "alembic",
        "bcrypt",
    )


class LocalPreflightProbe:
    """Collect metadata-only facts for the isolated daily-use entry."""

    _LOCAL_ENVS = frozenset({"dev", "development", "local", "test"})
    _OFFICIAL_DEEPSEEK_PATHS = frozenset(
        {"/chat/completions", "/v1/chat/completions"}
    )
    _DEFAULT_PACKAGE_VERSIONS = {
        "numpy": "1.26.4",
        "torch": "2.6.0",
        "transformers": "4.44.2",
        "sentence-transformers": "3.1.0",
    }

    def evaluate(
        self,
        *,
        settings: LocalPreflightSettingsView,
        config: LocalPreflightProbeConfig,
    ) -> LocalPreflightReport:
        checks: list[LocalPreflightCheck] = []

        def add(code: str, passed: bool, ok: str, failed: str) -> None:
            checks.append(
                LocalPreflightCheck(
                    code=code,
                    status=(
                        LocalPreflightCheckStatus.PASSED
                        if passed
                        else LocalPreflightCheckStatus.FAILED
                    ),
                    message=ok if passed else failed,
                )
            )

        add(
            "EXPLICIT_ACTIVATION",
            config.activation == "explicit",
            "显式本地启动授权已提供。",
            "缺少显式本地启动授权。",
        )
        add(
            "LOOPBACK_BIND",
            config.bind_host.strip().lower() in {"127.0.0.1", "localhost", "::1"},
            "服务绑定目标为本机回环地址。",
            "服务绑定目标不是本机回环地址。",
        )
        add(
            "LOCAL_APP_ENV",
            str(settings.app_env).strip().lower() in self._LOCAL_ENVS,
            "应用环境为本地开发环境。",
            "应用环境不属于允许的本地开发环境。",
        )
        add(
            "PUBLIC_ACCESS_DISABLED",
            not bool(settings.public_access_enabled),
            "公网访问已关闭。",
            "公网访问必须关闭。",
        )
        add(
            "SURFACE_ENABLED",
            bool(settings.feature_bid_assessment_pure_agent)
            and bool(settings.feature_vite_frontend),
            "Pure Agent 页面与 API 显式开启。",
            "Pure Agent 页面或 Vite 入口未显式开启。",
        )
        add(
            "RUNTIME_SWITCH_ENABLED",
            bool(settings.feature_bid_assessment_pure_agent_runtime),
            "Pure Agent Runtime 开关已显式开启。",
            "Pure Agent Runtime 开关未显式开启。",
        )
        add(
            "CONTINUATION_SECRET_READY",
            len(
                str(settings.bid_assessment_pure_agent_continuation_secret).encode(
                    "utf-8"
                )
            )
            >= 32,
            "本地 Continuation Secret 满足最小长度。",
            "本地 Continuation Secret 缺失或过短。",
        )

        database_path, database_reason = self._local_sqlite_path(
            settings.database_url,
            config.local_data_root,
            config.sqlite_database_path,
        )
        add(
            "ISOLATED_SQLITE_DATABASE",
            database_path is not None,
            "数据库为显式本地目录内的 SQLite。",
            database_reason,
        )
        schema_ready = bool(
            database_path
            and self._sqlite_head(database_path) == config.expected_migration_head
        )
        add(
            "DATABASE_SCHEMA_HEAD",
            schema_ready,
            "本地数据库迁移版本与隔离开发 head 一致。",
            "本地数据库缺失、不可只读访问或迁移版本不一致。",
        )

        add(
            "VITE_BUNDLE_AVAILABLE",
            self._is_file(config.vite_index_path),
            "本地 Vite 页面构建产物存在。",
            "本地 Vite 页面构建产物缺失。",
        )
        add(
            "FROZEN_DATASET_AVAILABLE",
            self._is_file(config.frozen_dataset_path),
            "冻结业务输入清单存在。",
            "冻结业务输入清单缺失。",
        )
        add(
            "FROZEN_PDF_AVAILABLE",
            self._is_file(config.frozen_pdf_path),
            "冻结招标 PDF 存在；Preflight 未读取其内容。",
            "冻结招标 PDF 缺失。",
        )
        add(
            "EMBEDDING_SNAPSHOT_AVAILABLE",
            self._model_snapshot_available(config.embedding_model_path),
            "冻结 BCE Embedding 快照元数据完整。",
            "冻结 BCE Embedding 快照缺失或元数据不完整。",
        )
        add(
            "SECRET_ENV_FILE_AVAILABLE",
            self._is_file(config.secret_env_file),
            "SecretEnvFile 存在；Preflight 未读取其内容。",
            "SecretEnvFile 缺失。",
        )
        add(
            "OFFICIAL_PROVIDER_ALLOWLIST",
            self._official_provider(config.provider_chat_url),
            "Provider 目标为官方 DeepSeek HTTPS 白名单。",
            "Provider 目标不在官方 DeepSeek HTTPS 白名单。",
        )

        expected_versions = dict(
            config.expected_package_versions or self._DEFAULT_PACKAGE_VERSIONS
        )
        add(
            "FROZEN_PYTHON_RUNTIME",
            all(self._distribution_version(name) == version for name, version in expected_versions.items()),
            "Embedding 运行依赖版本与已验收冻结环境一致。",
            "Embedding 运行依赖缺失或版本偏离已验收冻结环境。",
        )
        add(
            "APPLICATION_RUNTIME_AVAILABLE",
            all(
                self._distribution_version(name) is not None
                for name in config.required_application_packages
            ),
            "本地 FastAPI 应用运行依赖完整。",
            "本地 FastAPI 应用运行依赖缺失。",
        )
        add(
            "EXTERNAL_MCP_DISABLED",
            not config.external_mcp_enabled,
            "外部 MCP 已关闭。",
            "外部 MCP 必须关闭。",
        )
        add(
            "MILVUS_DISABLED",
            not config.milvus_enabled,
            "Milvus 已关闭。",
            "Milvus 必须关闭。",
        )
        add(
            "OCR_VISION_DISABLED",
            not config.ocr_vision_enabled,
            "OCR/视觉链路已关闭。",
            "OCR/视觉链路必须关闭。",
        )
        return LocalPreflightReport.build(checks=tuple(checks))

    @staticmethod
    def _is_file(path: Path | None) -> bool:
        return bool(path and path.is_file())

    @staticmethod
    def _model_snapshot_available(path: Path | None) -> bool:
        if path is None or not path.is_dir() or not (path / "config.json").is_file():
            return False
        return any(
            (path / name).is_file()
            for name in ("model.safetensors", "pytorch_model.bin")
        )

    @classmethod
    def _official_provider(cls, raw_url: str) -> bool:
        endpoint = urlsplit(str(raw_url).strip())
        return bool(
            endpoint.scheme == "https"
            and endpoint.hostname == "api.deepseek.com"
            and endpoint.path in cls._OFFICIAL_DEEPSEEK_PATHS
            and not endpoint.username
            and not endpoint.password
            and not endpoint.query
            and not endpoint.fragment
        )

    @staticmethod
    def _distribution_version(name: str) -> str | None:
        try:
            value = metadata.version(name)
        except metadata.PackageNotFoundError:
            return None
        return value.split("+")[0]

    @staticmethod
    def _local_sqlite_path(
        database_url: str,
        local_root: Path | None,
        expected_path: Path | None,
    ) -> tuple[Path | None, str]:
        try:
            url = make_url(str(database_url))
        except Exception:
            return None, "DATABASE_URL 无法解析。"
        if url.get_backend_name().lower() != "sqlite" or not url.database:
            return None, "日常本地入口只允许文件型 SQLite。"
        if local_root is None or expected_path is None:
            return None, "未显式配置本地数据目录或 SQLite 文件。"
        try:
            root = local_root.resolve(strict=False)
            actual = Path(url.database).resolve(strict=False)
            expected = expected_path.resolve(strict=False)
            actual.relative_to(root)
        except (OSError, ValueError):
            return None, "SQLite 不在显式本地数据目录内。"
        if actual != expected:
            return None, "DATABASE_URL 与显式本地 SQLite 文件不一致。"
        return actual, ""

    @staticmethod
    def _sqlite_head(path: Path) -> str | None:
        if not path.is_file():
            return None
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2) as connection:
                row = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone()
        except (OSError, sqlite3.Error):
            return None
        return str(row[0]) if row else None


class PureAgentRuntimeStatusView(StrictContract):
    schema_name: Literal["bid.pure-agent.runtime-status.v1"] = (
        "bid.pure-agent.runtime-status.v1"
    )
    surface_enabled: bool
    execution_switch_enabled: bool
    preflight_ready: bool
    runtime_available: bool
    startup_status: Literal[
        "not_configured",
        "preflight_blocked",
        "bootstrap_disabled",
        "bootstrap_incomplete",
        "ready",
    ]
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
