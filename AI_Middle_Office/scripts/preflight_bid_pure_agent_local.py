"""Read-only C08-1 Preflight CLI for the Pure Agent daily-use entry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.bid_assessment_pure.local_preflight import (  # noqa: E402
    LocalPreflightProbe,
    LocalPreflightProbeConfig,
    LocalPreflightReport,
    LocalPreflightSettingsView,
)


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    return Path(raw).resolve(strict=False) if raw else None


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_local_preflight_report(
    settings: LocalPreflightSettingsView,
) -> LocalPreflightReport:
    repository_root = PROJECT_ROOT.parent
    return LocalPreflightProbe().evaluate(
        settings=settings,
        config=LocalPreflightProbeConfig(
            activation=os.getenv("BID_PURE_AGENT_LOCAL_ACTIVATION", ""),
            bind_host=os.getenv("BID_PURE_AGENT_LOCAL_BIND_HOST", ""),
            local_data_root=_path_from_env("BID_PURE_AGENT_LOCAL_DATA_ROOT"),
            sqlite_database_path=_path_from_env(
                "BID_PURE_AGENT_LOCAL_DATABASE"
            ),
            vite_index_path=repository_root / "ai-web" / "dist" / "index.html",
            frozen_dataset_path=(
                PROJECT_ROOT
                / "evals"
                / "bid_assessment"
                / "v607-real-pdf-business-run.json"
            ),
            frozen_pdf_path=_path_from_env("BID_PURE_AGENT_LOCAL_PDF"),
            embedding_model_path=_path_from_env(
                "BID_PURE_AGENT_LOCAL_EMBEDDING_MODEL"
            ),
            secret_env_file=_path_from_env(
                "BID_PURE_AGENT_LOCAL_SECRET_ENV_FILE"
            ),
            provider_chat_url=os.getenv(
                "BID_PURE_AGENT_LOCAL_PROVIDER_CHAT_URL",
                "https://api.deepseek.com/chat/completions",
            ),
            external_mcp_enabled=_enabled("BID_PURE_AGENT_LOCAL_EXTERNAL_MCP"),
            milvus_enabled=_enabled("BID_PURE_AGENT_LOCAL_MILVUS"),
            ocr_vision_enabled=_enabled("BID_PURE_AGENT_LOCAL_OCR_VISION"),
        ),
    )


def main() -> int:
    from app.core.config import settings

    report = build_local_preflight_report(settings)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())

