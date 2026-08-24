"""Versioned, repository-backed Skill artifacts for Phase 4A-1.

The catalog is append-only and contains no import paths or executable code.
Plans freeze the catalog identity/hash and a compact SkillBinding copy.  A
historical TaskContract is rebuilt by loading the exact retained artifact and
verifying its canonical hash; there is no mutable "active skill" pointer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.services.bid_assessment_eventing import canonical_hash, canonical_json


SKILL_ROOT = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "bid_assessment"
    / "v1"
    / "skills"
)
DEFAULT_SKILL_CATALOG = "catalog-1.0.0.json"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
CONTRACT_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")


@dataclass(frozen=True)
class SkillBinding:
    skill_id: str
    skill_version: str
    skill_hash: str
    executor_kind: str
    action_contract: str
    output_schema: str

    def as_dict(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "skill_hash": self.skill_hash,
            "executor_kind": self.executor_kind,
            "action_contract": self.action_contract,
            "output_schema": self.output_schema,
        }


@dataclass(frozen=True)
class SkillArtifact:
    skill_id: str
    skill_version: str
    executor_kind: str
    artifact_hash: str
    task_bindings: Mapping[str, Mapping[str, str]]

    def binding_for(self, task_type: str) -> SkillBinding:
        task_binding = self.task_bindings.get(str(task_type))
        if task_binding is None:
            raise RuntimeError(f"BID_SKILL_TASK_NOT_SUPPORTED:{task_type}")
        return SkillBinding(
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            skill_hash=self.artifact_hash,
            executor_kind=self.executor_kind,
            action_contract=str(task_binding["action_contract"]),
            output_schema=str(task_binding["output_schema"]),
        )


@dataclass(frozen=True)
class SkillCatalog:
    catalog_ref: str
    catalog_id: str
    catalog_version: str
    catalog_hash: str
    artifacts: Mapping[tuple[str, str], SkillArtifact]
    task_index: Mapping[str, SkillArtifact]

    @property
    def version(self) -> str:
        return f"{self.catalog_id}@{self.catalog_version}"

    def binding_for_task(self, task_type: str) -> SkillBinding:
        artifact = self.task_index.get(str(task_type))
        if artifact is None:
            raise RuntimeError(f"BID_SKILL_BINDING_NOT_FOUND:{task_type}")
        return artifact.binding_for(str(task_type))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"BID_SKILL_ARTIFACT_UNREADABLE:{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"BID_SKILL_ARTIFACT_INVALID:{path.name}")
    return value


def _load_artifact(path: Path) -> tuple[SkillArtifact, dict[str, Any]]:
    payload = _load_json(path)
    if set(payload) != {
        "schema",
        "skill_id",
        "skill_version",
        "executor_kind",
        "task_bindings",
    } or payload.get("schema") != "bid.skill.artifact.v1":
        raise RuntimeError(f"BID_SKILL_ARTIFACT_SHAPE_INVALID:{path.name}")
    skill_id = str(payload.get("skill_id") or "")
    skill_version = str(payload.get("skill_version") or "")
    executor_kind = str(payload.get("executor_kind") or "")
    bindings = payload.get("task_bindings")
    if (
        ID_PATTERN.fullmatch(skill_id) is None
        or VERSION_PATTERN.fullmatch(skill_version) is None
        or executor_kind not in {"deterministic", "langgraph"}
        or not isinstance(bindings, dict)
        or not bindings
    ):
        raise RuntimeError(f"BID_SKILL_ARTIFACT_METADATA_INVALID:{path.name}")
    normalized_bindings: dict[str, Mapping[str, str]] = {}
    for task_type, binding in bindings.items():
        if (
            not isinstance(task_type, str)
            or not task_type
            or not isinstance(binding, dict)
            or set(binding) != {"action_contract", "output_schema"}
            or CONTRACT_PATTERN.fullmatch(str(binding.get("action_contract") or "")) is None
            or CONTRACT_PATTERN.fullmatch(str(binding.get("output_schema") or "")) is None
        ):
            raise RuntimeError(f"BID_SKILL_TASK_BINDING_INVALID:{path.name}:{task_type}")
        normalized_bindings[task_type] = MappingProxyType(
            {
                "action_contract": str(binding["action_contract"]),
                "output_schema": str(binding["output_schema"]),
            }
        )
    artifact = SkillArtifact(
        skill_id=skill_id,
        skill_version=skill_version,
        executor_kind=executor_kind,
        artifact_hash=canonical_hash(payload),
        task_bindings=MappingProxyType(normalized_bindings),
    )
    return artifact, json.loads(canonical_json(payload))


@lru_cache(maxsize=8)
def load_skill_catalog(catalog_filename: str = DEFAULT_SKILL_CATALOG) -> SkillCatalog:
    if Path(catalog_filename).name != catalog_filename:
        raise RuntimeError("BID_SKILL_CATALOG_PATH_INVALID")
    catalog_path = SKILL_ROOT / catalog_filename
    payload = _load_json(catalog_path)
    if set(payload) != {"schema", "catalog_id", "catalog_version", "artifacts"}:
        raise RuntimeError("BID_SKILL_CATALOG_SHAPE_INVALID")
    catalog_id = str(payload.get("catalog_id") or "")
    catalog_version = str(payload.get("catalog_version") or "")
    filenames = payload.get("artifacts")
    if (
        payload.get("schema") != "bid.skill.catalog.v1"
        or ID_PATTERN.fullmatch(catalog_id) is None
        or VERSION_PATTERN.fullmatch(catalog_version) is None
        or not isinstance(filenames, list)
        or not filenames
        or len(filenames) != len(set(filenames))
    ):
        raise RuntimeError("BID_SKILL_CATALOG_METADATA_INVALID")

    artifacts: dict[tuple[str, str], SkillArtifact] = {}
    task_index: dict[str, SkillArtifact] = {}
    hashed_artifacts: list[dict[str, Any]] = []
    for filename in filenames:
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise RuntimeError("BID_SKILL_ARTIFACT_PATH_INVALID")
        artifact, artifact_payload = _load_artifact(SKILL_ROOT / filename)
        key = (artifact.skill_id, artifact.skill_version)
        if key in artifacts:
            raise RuntimeError(f"BID_SKILL_ARTIFACT_DUPLICATE:{artifact.skill_id}")
        artifacts[key] = artifact
        for task_type in artifact.task_bindings:
            if task_type in task_index:
                raise RuntimeError(f"BID_SKILL_TASK_BINDING_AMBIGUOUS:{task_type}")
            task_index[task_type] = artifact
        hashed_artifacts.append(
            {
                "filename": filename,
                "artifact_hash": artifact.artifact_hash,
                "artifact": artifact_payload,
            }
        )

    catalog_hash = canonical_hash(
        {
            "schema": str(payload["schema"]),
            "catalog_id": catalog_id,
            "catalog_version": catalog_version,
            "artifacts": hashed_artifacts,
        }
    )
    return SkillCatalog(
        catalog_ref=catalog_filename,
        catalog_id=catalog_id,
        catalog_version=catalog_version,
        catalog_hash=catalog_hash,
        artifacts=MappingProxyType(artifacts),
        task_index=MappingProxyType(task_index),
    )


def verify_frozen_skill_binding(
    *,
    catalog_ref: str,
    catalog_version: str,
    catalog_hash: str,
    task_type: str,
    binding: Mapping[str, Any],
) -> SkillBinding:
    catalog = load_skill_catalog(str(catalog_ref))
    if catalog.version != str(catalog_version) or catalog.catalog_hash != str(catalog_hash):
        raise RuntimeError("BID_SKILL_CATALOG_HASH_MISMATCH")
    expected = catalog.binding_for_task(str(task_type))
    if dict(binding) != expected.as_dict():
        raise RuntimeError(f"BID_SKILL_BINDING_HASH_MISMATCH:{task_type}")
    return expected
