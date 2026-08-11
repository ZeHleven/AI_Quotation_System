from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = ROOT / "deploy" / "app-node" / "nginx" / "ai-middle-office-https.conf"
ACTIVATION_SCRIPT = ROOT / "deploy" / "app-node" / "scripts" / "phase8-free-performance-activate.sh"
BUILD_SCRIPT = ROOT / "deploy" / "app-node" / "scripts" / "phase8-free-performance-build.sh"
OVERLAY_DOCKERFILE = ROOT / "deploy" / "app-node" / "performance-overlay.Dockerfile"


def test_nginx_free_compression_and_cache_contract_preserves_security_headers():
    config = NGINX_CONFIG.read_text(encoding="utf-8")

    assert "gzip on;" in config
    assert "gzip_vary on;" in config
    assert "application/javascript" in config
    assert "application/json" in config
    assert "image/svg+xml" in config
    assert "brotli" not in "\n".join(
        line for line in config.splitlines() if not line.lstrip().startswith("#")
    ).lower()

    assert "map $uri $ai_cache_control" in config
    assert "$ai_cache_control" in config
    assert "public, max-age=31536000, immutable" in config
    assert 'default "no-store";' in config
    assert "proxy_hide_header Cache-Control;" in config
    assert "proxy_hide_header Expires;" in config
    assert config.count("add_header Cache-Control") == 1

    first_location = config.index("    location = /docs")
    server_header_block = config[:first_location]
    nested_locations = config[first_location:]
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    ):
        assert f"add_header {header}" in server_header_block
    assert "add_header" not in nested_locations


def test_offline_activation_is_integrity_checked_and_rolls_back_on_failure():
    script = ACTIVATION_SCRIPT.read_text(encoding="utf-8")

    assert "PUBLIC_ACCESS_ENABLED=false" in script
    assert "sha256sum" in script
    assert "docker load --input" in script
    assert "--no-build api worker" in script
    assert "docker pull" not in script
    assert "docker build" not in script
    assert "nginx -t" in script
    assert "systemctl reload nginx" in script
    assert "nginx_cache_security_preflight" in script
    assert "for _nginx_preflight_attempt in $(seq 1 20)" in script
    assert script.index("PREFLIGHT_LOGIN_HEADERS=") < script.index("RUNTIME_CHANGED=1")
    after_runtime_switch = script.split("RUNTIME_CHANGED=1", 1)[1]
    before_final_login_gate = after_runtime_switch.split("LOGIN_HEADERS=", 1)[0]
    assert "systemctl reload nginx" not in before_final_login_gate
    assert "trap rollback ERR" in script
    assert "ROLLBACK|complete" in script
    assert "content-encoding:[[:space:]]*gzip" in script
    assert "max-age=31536000.*immutable" in script
    assert "/api/v1/admin/codex-worker/" in script
    assert "/api/v1/admin/dwg-quantity-trial/" in script


def test_overlay_build_is_offline_scanned_and_keeps_the_non_root_runtime():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    dockerfile = OVERLAY_DOCKERFILE.read_text(encoding="utf-8")

    assert "sha256sum --check" in script
    assert "--network none" in script
    assert "--pull=false" in script
    assert "public.ecr.aws/aquasecurity/trivy:0.72.0" in script
    assert "TRIVY_SCANNER_DIGEST" in script
    assert "TRIVY_DB_SHA256" in script
    assert "TRIVY_DB_METADATA_SHA256" in script
    assert "docker run --rm" in script
    assert "--input" in script
    assert "--offline-scan" in script
    assert "--skip-db-update" in script
    assert "--skip-java-db-update" in script
    assert "--skip-check-update" in script
    assert "--scanners vuln,secret" in script
    assert "trivy_database_snapshot_unchanged" in script
    assert "docker save --output" in script
    assert "/var/run/docker.sock" not in script
    assert "docker compose" not in script
    assert "docker pull" not in script

    assert "ARG BASE_IMAGE=ai-middle-office-app:20260805_161737" in dockerfile
    assert "COPY --chown=root:root ai-web/dist" in dockerfile
    assert "USER 10001:10001" in dockerfile
