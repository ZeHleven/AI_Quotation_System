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
