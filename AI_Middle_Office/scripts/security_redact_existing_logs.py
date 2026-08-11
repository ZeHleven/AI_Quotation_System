"""Redact known credential forms from existing text logs in place."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.core.logging import redact_log_text


def redact_file(path: Path, *, apply: bool) -> bool:
    try:
        original = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return False
    sanitized = redact_log_text(original)
    if sanitized == original:
        return False
    if not apply:
        return True

    mode = path.stat().st_mode
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            handle.write(sanitized)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"log directory not found: {args.root}")
    changed = 0
    scanned = 0
    for path in sorted(args.root.rglob("*.log")):
        if not path.is_file():
            continue
        scanned += 1
        changed += int(redact_file(path, apply=args.apply))
    mode = "applied" if args.apply else "dry-run"
    print(f"LOG_REDACTION_{mode.upper()} scanned={scanned} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
