from __future__ import annotations

import re
from pathlib import Path


POLICY_SKILL_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "bid-decision-policy"
)
POLICY_RULES_DIR = POLICY_SKILL_DIR / "rules"
ACTIVE_VERSION_FILE = POLICY_SKILL_DIR / "active_version.txt"
POLICY_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")


class BidPolicyCatalogError(RuntimeError):
    pass


def active_bid_policy_version() -> str:
    try:
        version = ACTIVE_VERSION_FILE.read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise BidPolicyCatalogError(
            "ACTIVE_BID_POLICY_VERSION_UNAVAILABLE"
        ) from exc
    _validate_version(version)
    if not bid_policy_path(version).is_file():
        raise BidPolicyCatalogError("ACTIVE_BID_POLICY_FILE_MISSING")
    return version


def bid_policy_path(version: str) -> Path:
    normalized = str(version or "").strip()
    _validate_version(normalized)
    candidate = (
        POLICY_RULES_DIR / f"{normalized}.yaml"
    ).resolve()
    rules_dir = POLICY_RULES_DIR.resolve()
    if candidate.parent != rules_dir:
        raise BidPolicyCatalogError("INVALID_BID_POLICY_PATH")
    return candidate


def bid_policy_available(version: str) -> bool:
    try:
        return bid_policy_path(version).is_file()
    except BidPolicyCatalogError:
        return False


def list_bid_policy_versions() -> list[str]:
    try:
        candidates = POLICY_RULES_DIR.iterdir()
    except OSError as exc:
        raise BidPolicyCatalogError(
            "BID_POLICY_CATALOG_UNAVAILABLE"
        ) from exc
    versions: list[str] = []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".yaml":
            continue
        version = path.stem
        try:
            _validate_version(version)
        except BidPolicyCatalogError:
            continue
        versions.append(version)
    return sorted(set(versions))


def _validate_version(version: str) -> None:
    if not POLICY_VERSION_PATTERN.fullmatch(version):
        raise BidPolicyCatalogError("INVALID_BID_POLICY_VERSION")
