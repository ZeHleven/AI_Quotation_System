"""Interactively store a signed DingTalk robot configuration without echoing it."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.services.dingtalk_webhook import (
    describe_dingtalk_webhook_input,
    normalize_dingtalk_webhook_input,
    validate_dingtalk_custom_robot_config,
)


def update_env_file(path: Path, values: dict[str, str]) -> None:
    original = path.read_text(encoding="utf-8-sig")
    newline = "\r\n" if "\r\n" in original else "\n"
    target_keys = set(values)
    remaining = dict(values)
    output: list[str] = []

    for line in original.splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=", line)
        key = match.group(1) if match else ""
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        elif key in target_keys:
            # Remove duplicate definitions so an old credential cannot remain
            # earlier or later in the file with ambiguous dotenv precedence.
            continue
        else:
            output.append(line)
    if remaining:
        output.append("")
        output.extend(f"{key}={value}" for key, value in remaining.items())

    mode = path.stat().st_mode
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(newline.join(output) + newline)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure a signed DingTalk alert robot")
    parser.add_argument("--env-file", type=Path, default=BASE_DIR / ".env")
    args = parser.parse_args()

    if not args.env_file.is_file():
        raise SystemExit(f"environment file not found: {args.env_file}")

    webhook = normalize_dingtalk_webhook_input(
        getpass.getpass("粘贴新机器人 Webhook（输入隐藏）: ")
    )
    sign_secret = getpass.getpass("粘贴新机器人加签密钥 SEC...（输入隐藏）: ").strip()
    try:
        validate_dingtalk_custom_robot_config(webhook, sign_secret)
    except ValueError as exc:
        diagnostic = describe_dingtalk_webhook_input(webhook)
        print(
            "脱敏诊断："
            f"HTTPS={'是' if diagnostic['https'] else '否'}，"
            f"主机={diagnostic['host']}，"
            f"官方主机={'是' if diagnostic['official_host'] else '否'}，"
            f"标准路径={'是' if diagnostic['standard_path'] else '否'}，"
            f"含access_token={'是' if diagnostic['has_access_token'] else '否'}"
        )
        raise SystemExit(f"配置未写入：{exc}") from None

    update_env_file(
        args.env_file,
        {
            "ALERT_DINGTALK_WEBHOOK": webhook,
            "ALERT_DINGTALK_SECRET": sign_secret,
        },
    )
    print("DINGTALK_SIGNED_ALERT_CONFIG_SAVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
