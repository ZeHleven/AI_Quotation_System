from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents.bid_assessment_pure.local_preflight import (
    EXPECTED_LOCAL_MIGRATION_HEAD,
    LocalPreflightProbe,
    LocalPreflightProbeConfig,
    LocalPreflightReport,
)


def _file(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _ready_inputs(tmp_path: Path):
    data_root = tmp_path / ".local-pure-agent-daily"
    database = data_root / "runtime.db"
    data_root.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES (?)",
            (EXPECTED_LOCAL_MIGRATION_HEAD,),
        )
    embedding = tmp_path / "embedding"
    _file(embedding / "config.json")
    _file(embedding / "model.safetensors")
    settings = SimpleNamespace(
        app_env="local",
        public_access_enabled=False,
        database_url=f"sqlite:///{database.as_posix()}",
        feature_vite_frontend=True,
        feature_bid_assessment_pure_agent=True,
        feature_bid_assessment_pure_agent_runtime=True,
        bid_assessment_pure_agent_continuation_secret="x" * 32,
    )
    config = LocalPreflightProbeConfig(
        activation="explicit",
        bind_host="127.0.0.1",
        local_data_root=data_root,
        sqlite_database_path=database,
        vite_index_path=_file(tmp_path / "dist" / "index.html"),
        frozen_dataset_path=_file(tmp_path / "dataset.json"),
        frozen_pdf_path=_file(tmp_path / "frozen.pdf"),
        embedding_model_path=embedding,
        secret_env_file=_file(tmp_path / "secret.env"),
        expected_package_versions={"fixture-runtime": "1.0"},
        required_application_packages=("fixture-runtime",),
    )
    return settings, config


def test_ready_preflight_is_hash_bound_and_exposes_no_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, config = _ready_inputs(tmp_path)
    monkeypatch.setattr(
        LocalPreflightProbe,
        "_distribution_version",
        staticmethod(lambda name: "1.0" if name == "fixture-runtime" else None),
    )

    report = LocalPreflightProbe().evaluate(settings=settings, config=config)

    assert report.ready is True
    assert report.runtime_install_allowed is True
    assert report.failed_codes == ()
    serialized = report.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "secret.env" not in serialized


def test_preflight_rejects_remote_or_unversioned_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, config = _ready_inputs(tmp_path)
    settings.database_url = "mysql+pymysql://example.invalid/production"
    settings.public_access_enabled = True
    monkeypatch.setattr(
        LocalPreflightProbe,
        "_distribution_version",
        staticmethod(lambda _name: None),
    )

    report = LocalPreflightProbe().evaluate(settings=settings, config=config)

    assert report.ready is False
    assert report.runtime_install_allowed is False
    assert "PUBLIC_ACCESS_DISABLED" in report.failed_codes
    assert "ISOLATED_SQLITE_DATABASE" in report.failed_codes
    assert "DATABASE_SCHEMA_HEAD" in report.failed_codes
    assert "FROZEN_PYTHON_RUNTIME" in report.failed_codes


def test_preflight_report_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, config = _ready_inputs(tmp_path)
    monkeypatch.setattr(
        LocalPreflightProbe,
        "_distribution_version",
        staticmethod(lambda _name: "1.0"),
    )
    report = LocalPreflightProbe().evaluate(settings=settings, config=config)
    payload = report.model_dump(mode="json")
    payload["ready"] = False

    with pytest.raises(ValidationError):
        LocalPreflightReport.model_validate(payload)
