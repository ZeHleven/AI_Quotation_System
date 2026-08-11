from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


EXPECTED_BASE_SHA256 = {
    "app/main.py": "6883d2db07419f7e4f5de48ae8a71451a57e2c03d346285c2732f46a0f3831f0",
    "app/api/v1/quote.py": "91d46b93e7d0c8056f2323d69bfda3b49e1a5fcea7e89333c1cc4d0202515b25",
    "app/api/v1/quote_jobs.py": "4c780befbe401ad1f93884ec2fc05f3490971ceb436c89c2dba7758a7b848086",
    "app/api/v1/users.py": "32951f06aad875d20a81b6933589c9c704bfbe4d18e8cab2ce0a86a4ed269de6",
    "app/models/quote_job.py": "255c2b929a7acaf71adacb2b8e6d1be0b564b897532ac86f44bdcfb50521b0ae",
    "app/models/user.py": "2c208aa7a2646a6930c0d65b3473b66cb48d37ba23108aa9be2434812328afce",
    "app/services/quote_job_runner.py": "cad22c4629af8763878a988ccc0d1ab59a9ea435d21a5e05c89448b9f4e93e73",
}

NEW_FILES = {
    "app/api/v1/internal_n8n.py": "app/api/v1/internal_n8n.py",
    "app/services/quote_consistency.py": "app/services/quote_consistency.py",
    "alembic/versions/20260808_0082_add_quote_consistency_phase1.py": (
        "alembic/versions/20260808_0082_add_quote_consistency_phase1.py"
    ),
}


@dataclass
class Hunk:
    old_lines: list[str]
    new_lines: list[str]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"overlay anchor mismatch for {label}: expected 1, got {count}")
    return text.replace(old, new, 1)


def parse_patch(path: Path) -> dict[str, list[Hunk]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    result: dict[str, list[Hunk]] = {}
    current_file: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.startswith("+++ b/"):
            raw_path = line[6:].rstrip("\r\n")
            prefix = "AI_Middle_Office/"
            if not raw_path.startswith(prefix):
                raise RuntimeError(f"unexpected patch path: {raw_path}")
            current_file = raw_path[len(prefix) :]
            result.setdefault(current_file, [])
            index += 1
            continue

        if line.startswith("@@ "):
            if current_file is None or not re.match(r"^@@ -\d+", line):
                raise RuntimeError("patch hunk without a valid target")
            index += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while index < len(lines):
                item = lines[index]
                if item.startswith(("diff --git ", "@@ ", "--- ", "+++ ")):
                    break
                if item.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if not item or item[0] not in {" ", "+", "-"}:
                    raise RuntimeError(f"unexpected patch line: {item[:80]!r}")
                payload = item[1:]
                if item[0] in {" ", "-"}:
                    old_lines.append(payload)
                if item[0] in {" ", "+"}:
                    new_lines.append(payload)
                index += 1
            result[current_file].append(Hunk(old_lines=old_lines, new_lines=new_lines))
            continue

        index += 1

    if not result or any(not hunks for hunks in result.values()):
        raise RuntimeError("phase1 patch is empty or incomplete")
    return result


def apply_hunks(root: Path, patch_path: Path) -> None:
    for relative, hunks in parse_patch(patch_path).items():
        target = root / relative
        if not target.is_file():
            raise RuntimeError(f"patch target missing: {relative}")
        text = target.read_text(encoding="utf-8")
        for number, hunk in enumerate(hunks, start=1):
            old = "".join(hunk.old_lines)
            new = "".join(hunk.new_lines)
            text = replace_once(text, old, new, label=f"{relative} hunk {number}")
        target.write_text(text, encoding="utf-8", newline="\n")


def patch_main(root: Path) -> None:
    path = root / "app/main.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    history,\n    knowledge_candidates,",
        "    history,\n    internal_n8n,\n    knowledge_candidates,",
        label="main import internal_n8n",
    )
    text = replace_once(
        text,
        'app.include_router(history.router, prefix="/api/v1", tags=["History"])\n',
        'app.include_router(history.router, prefix="/api/v1", tags=["History"])\n'
        'app.include_router(internal_n8n.router, prefix="/api/v1", tags=["Internal N8N"])\n',
        label="main include internal_n8n router",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_rbac(root: Path) -> None:
    path = root / "app/services/rbac.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        "quota": user.quota,\n        "is_active": bool(user.is_active),',
        '        "quota": user.quota,\n'
        '        "quota_reserved": int(user.quota_reserved or 0),\n'
        '        "quota_available": max(0, int(user.quota or 0) - int(user.quota_reserved or 0)),\n'
        '        "is_active": bool(user.is_active),',
        label="rbac quota reservation fields",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def copy_new_files(root: Path, new_root: Path) -> None:
    for relative, source_relative in NEW_FILES.items():
        source = new_root / source_relative
        target = root / relative
        if not source.is_file():
            raise RuntimeError(f"new overlay source missing: {source_relative}")
        if target.exists():
            raise RuntimeError(f"new overlay target unexpectedly exists: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def validate_base(root: Path) -> None:
    for relative, expected in EXPECTED_BASE_SHA256.items():
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"base file missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"base sha256 mismatch for {relative}: expected {expected}, got {actual}"
            )


def validate_result(root: Path) -> None:
    required_fragments = {
        "app/main.py": ["internal_n8n.router"],
        "app/api/v1/quote.py": ["start_quote_push_attempt", 'payload["idempotency_key"]'],
        "app/api/v1/quote_jobs.py": ["reserve_quote_quota", "clone_quote_requirement_rows"],
        "app/models/quote_job.py": ["class QuoteQuotaReservation", "class QuotePushAttempt"],
        "app/models/user.py": ["quota_reserved"],
        "app/services/quote_job_runner.py": ["claim_quote_job", "consume_quote_quota"],
        "app/services/quote_review.py": ["def clone_quote_requirement_rows"],
        "app/services/rbac.py": ['"quota_available"'],
    }
    for relative, fragments in required_fragments.items():
        text = (root / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            if text.count(fragment) < 1:
                raise RuntimeError(f"overlay invariant missing in {relative}: {fragment}")
    for relative in NEW_FILES:
        if not (root / relative).is_file():
            raise RuntimeError(f"new overlay file missing after copy: {relative}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--new-root", required=True, type=Path)
    args = parser.parse_args()

    validate_base(args.root)
    apply_hunks(args.root, args.patch)
    patch_main(args.root)
    patch_rbac(args.root)
    copy_new_files(args.root, args.new_root)
    validate_result(args.root)
    print("RESULT|quote_consistency_overlay=applied")


if __name__ == "__main__":
    main()
