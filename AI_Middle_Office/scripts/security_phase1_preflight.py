"""Read-only Phase 1 containment checks.

The command never prints secret values.  It validates the local application
environment and the repository's CentOS Compose security defaults before an
operator changes firewall rules or deploys the stack.
"""

from __future__ import annotations

import argparse
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_ENV = PROJECT_ROOT / "AI_Middle_Office" / ".env"
DEFAULT_COMPOSE = PROJECT_ROOT / "rag_docker" / "docker-compose.yml"

FALSE_VALUES = {"", "0", "false", "no", "off"}
WEAK_SECRETS = {
    "change-this-password",
    "change-me-in-production",
    "change-me-rag-reload-secret",
    "replace-with-strong-random-secret",
    "replace-with-strong-random-password",
    "replace-with-random-admin-user",
    "replace-with-random-internal-user",
    "your_super_secret_key_for_ai_middle_office",
}


@dataclass(frozen=True)
class CheckResult:
    level: str
    key: str
    message: str


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _is_false(value: str | None) -> bool:
    return (value or "").strip().lower() in FALSE_VALUES


def _secret_is_strong(value: str | None) -> bool:
    normalized = (value or "").strip()
    return len(normalized) >= 16 and normalized not in WEAK_SECRETS and not normalized.startswith("replace-")


def check_app_env(values: dict[str, str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for key in ("PUBLIC_ACCESS_ENABLED", "ALLOW_SELF_REGISTRATION"):
        if _is_false(values.get(key)):
            results.append(CheckResult("PASS", key, "disabled"))
        else:
            results.append(CheckResult("FAIL", key, "must remain disabled during Phase 1"))

    for key in ("JWT_SECRET_KEY", "WEBHOOK_SECRET", "RELOAD_SECRET", "ZHIPU_API_KEY"):
        if _secret_is_strong(values.get(key)):
            results.append(CheckResult("PASS", key, "configured and not a known placeholder"))
        else:
            results.append(CheckResult("FAIL", key, "missing, too short, or a known placeholder"))

    if _is_false(values.get("MINIO_ENABLED")) or _secret_is_strong(values.get("MINIO_SECRET_KEY")):
        results.append(CheckResult("PASS", "MINIO_SECRET_KEY", "safe for the current MinIO mode"))
    else:
        results.append(CheckResult("FAIL", "MINIO_SECRET_KEY", "enabled MinIO requires a strong secret"))

    if not values.get("ALERT_DINGTALK_WEBHOOK", "").strip():
        results.append(CheckResult("PASS", "ALERT_DINGTALK", "disabled"))
    elif _secret_is_strong(values.get("ALERT_DINGTALK_SECRET")):
        results.append(CheckResult("PASS", "ALERT_DINGTALK", "webhook signing is configured"))
    else:
        results.append(CheckResult("FAIL", "ALERT_DINGTALK", "configured webhook requires a strong signing secret"))

    runtime_database = values.get("DATABASE_URL", "").strip()
    migration_database = values.get("MIGRATION_DATABASE_URL", "").strip()
    if runtime_database.lower().startswith("mysql"):
        if not migration_database:
            results.append(CheckResult("WARN", "MIGRATION_DATABASE_URL", "dedicated migration account not configured"))
        else:
            try:
                runtime_url = make_url(runtime_database)
                migration_url = make_url(migration_database)
            except Exception:
                results.append(CheckResult("FAIL", "MIGRATION_DATABASE_URL", "database URL is invalid"))
            else:
                same_account = (
                    runtime_url.username == migration_url.username
                    and runtime_url.host == migration_url.host
                    and runtime_url.port == migration_url.port
                    and runtime_url.database == migration_url.database
                )
                if same_account:
                    results.append(CheckResult("FAIL", "MIGRATION_DATABASE_URL", "must use a distinct database account"))
                else:
                    results.append(CheckResult("PASS", "MIGRATION_DATABASE_URL", "dedicated migration connection configured"))
    return results


def check_compose(compose_text: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    if '"19530:19530"' not in compose_text and '"9091:9091"' not in compose_text:
        results.append(CheckResult("PASS", "MILVUS_HOST_PORTS", "not published on the host"))
    else:
        results.append(CheckResult("FAIL", "MILVUS_HOST_PORTS", "Milvus data/management ports are published"))

    expected_private_bindings = (":8001:8001", ":6380:6379", ":9002:9000", ":9003:9001")
    for binding in expected_private_bindings:
        matching = [line.strip() for line in compose_text.splitlines() if binding in line]
        safe = bool(matching) and all("INTERNAL_BIND_ADDRESS" in line for line in matching)
        results.append(
            CheckResult(
                "PASS" if safe else "FAIL",
                f"BIND_{binding.split(':')[1]}",
                "requires an explicit private bind address" if safe else "missing an explicit private bind address",
            )
        )

    insecure_fallbacks = (":-change-", ":-quoteadmin", ":-milvus-minio")
    if any(token in compose_text for token in insecure_fallbacks):
        results.append(CheckResult("FAIL", "COMPOSE_SECRET_DEFAULTS", "known credential fallback remains"))
    else:
        results.append(CheckResult("PASS", "COMPOSE_SECRET_DEFAULTS", "critical credentials are required"))
    return results


def check_rag_env(values: dict[str, str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    raw_address = values.get("INTERNAL_BIND_ADDRESS", "")
    try:
        address = ipaddress.ip_address(raw_address)
        safe_address = (address.is_private or address.is_loopback) and not address.is_unspecified
    except ValueError:
        safe_address = False
    results.append(
        CheckResult(
            "PASS" if safe_address else "FAIL",
            "INTERNAL_BIND_ADDRESS",
            "private/loopback address configured" if safe_address else "must be an explicit private or loopback IP",
        )
    )

    for key in (
        "RELOAD_SECRET",
        "MILVUS_MINIO_ACCESS_KEY",
        "MILVUS_MINIO_SECRET_KEY",
        "QUOTE_MINIO_ROOT_USER",
        "QUOTE_MINIO_ROOT_PASSWORD",
    ):
        results.append(
            CheckResult(
                "PASS" if _secret_is_strong(values.get(key)) else "FAIL",
                key,
                "configured and not a known placeholder" if _secret_is_strong(values.get(key)) else "missing, too short, or a placeholder",
            )
        )
    return results


def _print_results(results: Iterable[CheckResult]) -> int:
    failed = False
    for result in results:
        print(f"[{result.level}] {result.key}: {result.message}")
        failed = failed or result.level == "FAIL"
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 public-exposure containment preflight")
    parser.add_argument("--app-env", type=Path, default=DEFAULT_APP_ENV)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--rag-env", type=Path, help="Optional deployed /opt/rag_service/.env copy")
    args = parser.parse_args()

    results: list[CheckResult] = []
    if not args.app_env.exists():
        results.append(CheckResult("FAIL", "APP_ENV_FILE", f"not found: {args.app_env}"))
    else:
        results.extend(check_app_env(read_env(args.app_env)))

    if not args.compose.exists():
        results.append(CheckResult("FAIL", "COMPOSE_FILE", f"not found: {args.compose}"))
    else:
        results.extend(check_compose(args.compose.read_text(encoding="utf-8-sig")))

    if args.rag_env:
        if not args.rag_env.exists():
            results.append(CheckResult("FAIL", "RAG_ENV_FILE", f"not found: {args.rag_env}"))
        else:
            results.extend(check_rag_env(read_env(args.rag_env)))
    else:
        results.append(CheckResult("WARN", "RAG_ENV_FILE", "not checked; pass --rag-env on the deployment host"))

    return _print_results(results)


if __name__ == "__main__":
    raise SystemExit(main())
