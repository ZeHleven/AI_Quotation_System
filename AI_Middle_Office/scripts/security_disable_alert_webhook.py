"""Disable a compromised alert webhook without printing its value."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


def disable_alert_webhook(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    newline = "\r\n" if "\r\n" in original else "\n"
    pattern = re.compile(r"^\s*ALERT_DINGTALK_WEBHOOK\s*=.*$")
    output: list[str] = []
    found = False
    changed = False
    for line in original.splitlines():
        if pattern.match(line):
            found = True
            changed = changed or line.strip() != "ALERT_DINGTALK_WEBHOOK="
            output.append("ALERT_DINGTALK_WEBHOOK=")
        else:
            output.append(line)
    if not found:
        output.extend(["", "ALERT_DINGTALK_WEBHOOK="])
        changed = True
    if not changed:
        return False

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(newline.join(output) + newline)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    changed = disable_alert_webhook(args.env_file)
    print("ALERT_DINGTALK_WEBHOOK_DISABLED" if changed else "ALERT_DINGTALK_WEBHOOK_ALREADY_DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
