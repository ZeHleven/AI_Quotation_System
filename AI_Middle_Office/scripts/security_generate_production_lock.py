"""Generate a hash-locked production requirements file for the app image.

Run this script inside the exact pinned Python/Alpine base image used by the
production Dockerfile.  It downloads one compatible wheel for every package in
the resolved runtime inventory, verifies the wheel's SHA-256 digest against the
official PyPI JSON API, and emits a deterministic pip ``--require-hashes`` lock
file plus a machine-readable verification manifest.

The script intentionally rejects source distributions, unpinned requirements,
duplicate normalized project names, yanked files, and any wheel whose local
digest is not published by official PyPI.
"""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")
_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_name(value: str) -> str:
    return _NORMALIZE_RE.sub("-", value).lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pins(path: Path) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    seen: set[str] = set()

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(
                f"{path}:{line_number}: expected a plain exact pin like name==version"
            )
        name, version = match.groups()
        normalized = _normalize_name(name)
        if normalized in seen:
            raise ValueError(f"duplicate normalized project name: {name}")
        seen.add(normalized)
        pins.append((name, version))

    if not pins:
        raise ValueError(f"no pinned requirements found in {path}")
    return pins


def _wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
            and "/" not in name[: -len(".dist-info/METADATA")].rstrip("/")
        ]
        if len(metadata_names) != 1:
            raise ValueError(
                f"{path.name}: expected exactly one top-level dist-info/METADATA, "
                f"found {len(metadata_names)}"
            )
        message = email.message_from_bytes(archive.read(metadata_names[0]))

    name = str(message.get("Name") or "").strip()
    version = str(message.get("Version") or "").strip()
    if not name or not version:
        raise ValueError(f"{path.name}: wheel metadata is missing Name or Version")
    return name, version


def _fetch_json(url: str, *, attempts: int = 4) -> dict[str, Any]:
    request = Request(
        url,
        headers={"User-Agent": "ai-middle-office-production-lock/1.0"},
        method="GET",
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status} for {url}")
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected JSON payload for {url}")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"failed to read official PyPI metadata for {url}: {last_error}")


def _verify_with_pypi(
    *,
    project_name: str,
    version: str,
    wheel_path: Path,
    digest: str,
    pypi_base_url: str,
) -> str:
    metadata_url = (
        f"{pypi_base_url.rstrip('/')}/{quote(project_name, safe='')}/"
        f"{quote(version, safe='')}/json"
    )
    payload = _fetch_json(metadata_url)
    files = payload.get("urls")
    if not isinstance(files, list):
        raise RuntimeError(f"official PyPI response has no release files: {metadata_url}")

    filename_matches = [item for item in files if item.get("filename") == wheel_path.name]
    if len(filename_matches) != 1:
        raise RuntimeError(
            f"official PyPI does not list exactly one file named {wheel_path.name} "
            f"for {project_name}=={version}"
        )

    release_file = filename_matches[0]
    if release_file.get("packagetype") != "bdist_wheel":
        raise RuntimeError(f"official PyPI file is not a wheel: {wheel_path.name}")
    if bool(release_file.get("yanked")):
        reason = release_file.get("yanked_reason") or "no reason supplied"
        raise RuntimeError(f"official PyPI file is yanked: {wheel_path.name}: {reason}")

    published_digest = str((release_file.get("digests") or {}).get("sha256") or "")
    if not published_digest or published_digest.lower() != digest.lower():
        raise RuntimeError(
            f"SHA-256 mismatch for {wheel_path.name}: "
            f"local={digest}, official={published_digest or 'missing'}"
        )
    return metadata_url


def _assert_target_environment() -> dict[str, str]:
    alpine_release_path = Path("/etc/alpine-release")
    if not alpine_release_path.is_file():
        raise RuntimeError("lock generation must run inside the pinned Alpine image")
    alpine_release = alpine_release_path.read_text(encoding="utf-8").strip()
    if not alpine_release.startswith("3.23"):
        raise RuntimeError(
            f"expected Alpine 3.23.x, found {alpine_release or 'unknown'}"
        )

    implementation = platform.python_implementation()
    python_version = platform.python_version()
    machine = platform.machine().lower()
    if implementation != "CPython" or not python_version.startswith("3.11."):
        raise RuntimeError(
            f"expected CPython 3.11.x, found {implementation} {python_version}"
        )
    if machine not in {"x86_64", "amd64"}:
        raise RuntimeError(f"expected amd64/x86_64, found {machine}")

    return {
        "alpine_release": alpine_release,
        "python_implementation": implementation,
        "python_version": python_version,
        "machine": machine,
    }


def _download_wheels(
    *, resolved_path: Path, wheel_dir: Path, index_url: str
) -> None:
    environment = os.environ.copy()
    environment.pop("PIP_EXTRA_INDEX_URL", None)
    environment.pop("PIP_TRUSTED_HOST", None)
    environment["PIP_CONFIG_FILE"] = os.devnull

    command = [
        sys.executable,
        "-m",
        "pip",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--index-url",
        index_url,
        "--timeout",
        "60",
        "--retries",
        "5",
        "--progress-bar",
        "off",
        "--dest",
        str(wheel_dir),
        "--requirement",
        str(resolved_path),
    ]
    subprocess.run(command, check=True, env=environment)


def _write_outputs(
    *,
    pins: list[tuple[str, str]],
    wheels: dict[str, dict[str, str]],
    resolved_path: Path,
    lock_output: Path,
    manifest_output: Path,
    target: dict[str, str],
    index_url: str,
    pypi_base_url: str,
    base_image: str,
) -> None:
    lock_lines = [
        "# Generated for the pinned AI Middle Office production image.",
        f"# Base image: {base_image}",
        "# Every selected wheel was verified against official PyPI metadata.",
        "# Install with --require-hashes --only-binary=:all: --no-deps.",
        "",
    ]
    package_manifest: list[dict[str, str]] = []

    for original_name, version in pins:
        normalized = _normalize_name(original_name)
        wheel = wheels[normalized]
        lock_lines.extend(
            [
                f"{original_name}=={version} \\",
                f"    --hash=sha256:{wheel['sha256']}",
            ]
        )
        package_manifest.append(
            {
                "name": original_name,
                "normalized_name": normalized,
                "version": version,
                "filename": wheel["filename"],
                "sha256": wheel["sha256"],
                "official_pypi_metadata": wheel["official_pypi_metadata"],
            }
        )

    lock_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    lock_output.write_text("\n".join(lock_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "base_image": base_image,
        "target": target,
        "download_index": index_url,
        "verification_api": pypi_base_url,
        "resolved_requirements_sha256": _sha256_file(resolved_path),
        "package_count": len(package_manifest),
        "packages": package_manifest,
    }
    manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolved", required=True, type=Path)
    parser.add_argument("--lock-output", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    parser.add_argument(
        "--index-url", default="https://mirrors.aliyun.com/pypi/simple/"
    )
    parser.add_argument("--pypi-base-url", default="https://pypi.org/pypi")
    parser.add_argument("--base-image", required=True)
    args = parser.parse_args()

    resolved_path = args.resolved.resolve(strict=True)
    pins = _read_pins(resolved_path)
    target = _assert_target_environment()

    with tempfile.TemporaryDirectory(prefix="production-lock-wheels-") as temp_dir:
        wheel_dir = Path(temp_dir)
        _download_wheels(
            resolved_path=resolved_path,
            wheel_dir=wheel_dir,
            index_url=args.index_url,
        )

        wheel_paths = sorted(wheel_dir.glob("*.whl"))
        if len(wheel_paths) != len(pins):
            raise RuntimeError(
                f"expected {len(pins)} wheels, downloaded {len(wheel_paths)}"
            )

        expected = {_normalize_name(name): version for name, version in pins}
        wheels: dict[str, dict[str, str]] = {}
        for index, wheel_path in enumerate(wheel_paths, start=1):
            wheel_name, wheel_version = _wheel_identity(wheel_path)
            normalized = _normalize_name(wheel_name)
            if normalized not in expected:
                raise RuntimeError(f"unexpected wheel downloaded: {wheel_path.name}")
            if normalized in wheels:
                raise RuntimeError(f"multiple wheels downloaded for {wheel_name}")
            if wheel_version != expected[normalized]:
                raise RuntimeError(
                    f"version mismatch for {wheel_name}: "
                    f"expected {expected[normalized]}, found {wheel_version}"
                )

            digest = _sha256_file(wheel_path)
            metadata_url = _verify_with_pypi(
                project_name=wheel_name,
                version=wheel_version,
                wheel_path=wheel_path,
                digest=digest,
                pypi_base_url=args.pypi_base_url,
            )
            wheels[normalized] = {
                "filename": wheel_path.name,
                "sha256": digest,
                "official_pypi_metadata": metadata_url,
            }
            if index == len(wheel_paths) or index % 10 == 0:
                print(
                    f"verified {index}/{len(wheel_paths)} wheels against official PyPI",
                    flush=True,
                )

        missing = sorted(set(expected) - set(wheels))
        if missing:
            raise RuntimeError(f"missing verified wheels: {', '.join(missing)}")

        _write_outputs(
            pins=pins,
            wheels=wheels,
            resolved_path=resolved_path,
            lock_output=args.lock_output,
            manifest_output=args.manifest_output,
            target=target,
            index_url=args.index_url,
            pypi_base_url=args.pypi_base_url,
            base_image=args.base_image,
        )

    print(f"lock_file={args.lock_output}")
    print(f"manifest={args.manifest_output}")
    print(f"packages={len(pins)}")
    print(f"lock_sha256={_sha256_file(args.lock_output)}")
    print(f"manifest_sha256={_sha256_file(args.manifest_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
