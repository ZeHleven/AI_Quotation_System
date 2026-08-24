import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from app.services import bid_mvp1_execute_preflight as preflight_service
from app.services.bid_mvp1_execute_preflight import (
    EXECUTE_PREFLIGHT_SCHEMA,
    build_execute_preflight,
)


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *_args, **_kwargs):
        return self

    def one_or_none(self):
        return self.row


class _Db:
    def __init__(self, row):
        self.row = row

    def query(self, _model):
        return _Query(self.row)


def _runtime_access(*, access_mode: str, worker_running: bool) -> dict[str, object]:
    execute = access_mode == "execute"
    return {
        "local_lab": True,
        "access_mode": access_mode,
        "execution_enabled": execute,
        "write_enabled": execute,
        "worker_enabled": execute,
        "worker_running": worker_running,
        "model_calls_enabled": execute,
        "model_provider": "deepseek-v4-flash",
        "retrieval_mode": "rq2b",
    }


def _ready_settings(tmp_path: Path, *, api_key: str) -> tuple[SimpleNamespace, Path, Path]:
    object_root = tmp_path / "objects"
    embedding_root = tmp_path / "embedding-snapshot"
    object_root.mkdir()
    embedding_root.mkdir()
    return (
        SimpleNamespace(
            bid_upload_local_root=str(object_root),
            bid_evidence_semantic_model_path=str(embedding_root),
            bid_evidence_reranker_model_path="",
            bid_assessment_model_api_key=api_key,
            feature_bid_assessment_phase4_enterprise_capability=False,
        ),
        object_root,
        embedding_root,
    )


def test_view_only_preflight_defers_authority_and_never_exposes_secret_or_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret = "sk-never-return-this-secret"
    fake_settings, object_root, embedding_root = _ready_settings(
        tmp_path, api_key=secret
    )
    monkeypatch.setattr(preflight_service, "settings", fake_settings)
    version = "mvp1-local-deepseek-v4-flash-1.0.1"
    result = build_execute_preflight(
        _Db(SimpleNamespace(version=version, status="active")),
        runtime_access=_runtime_access(access_mode="view-only", worker_running=False),
        expected_model_profile_version=version,
        rq2_runtime_ready=True,
        authority_epoch="view-only-process",
        view_only_secret_isolated=True,
    )

    assert result["schema"] == EXECUTE_PREFLIGHT_SCHEMA
    Draft202012Validator(
        json.loads(
            (ROOT / "schemas" / "bid_assessment" / "v1" / "execute-preflight-v2.schema.json")
            .read_text(encoding="utf-8")
        )
    ).validate(result)
    assert result["launch_ready"] is True
    assert result["current_process_ready"] is False
    assert result["restart_required"] is True
    assert set(result["deferred_codes"]) == {
        "MODEL_CREDENTIAL",
        "WORKER_LIFECYCLE",
        "WRITE_AUTHORITY",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert secret not in serialized
    assert str(object_root) not in serialized
    assert str(embedding_root) not in serialized

    unsafe = build_execute_preflight(
        _Db(SimpleNamespace(version=version, status="active")),
        runtime_access=_runtime_access(access_mode="view-only", worker_running=False),
        expected_model_profile_version=version,
        rq2_runtime_ready=True,
        authority_epoch="unsafe-view-only-process",
        view_only_secret_isolated=False,
    )
    assert "VIEW_ONLY_SECRET_FENCE" in unsafe["blocking_codes"]
    assert unsafe["launch_ready"] is False


def test_execute_preflight_requires_all_authoritative_runtime_checks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_settings, _, _ = _ready_settings(
        tmp_path, api_key="sk-valid-local-test-key-value"
    )
    monkeypatch.setattr(preflight_service, "settings", fake_settings)
    version = "mvp1-local-deepseek-v4-flash-1.0.1"
    result = build_execute_preflight(
        _Db(SimpleNamespace(version=version, status="active")),
        runtime_access=_runtime_access(access_mode="execute", worker_running=True),
        expected_model_profile_version=version,
        rq2_runtime_ready=True,
        authority_epoch="execute-process",
        view_only_secret_isolated=True,
    )

    assert result["blocking_codes"] == []
    Draft202012Validator(
        json.loads(
            (ROOT / "schemas" / "bid_assessment" / "v1" / "execute-preflight-v2.schema.json")
            .read_text(encoding="utf-8")
        )
    ).validate(result)
    assert result["deferred_codes"] == []
    assert result["launch_ready"] is True
    assert result["current_process_ready"] is True
    assert result["restart_required"] is False
    assert len(result["authority_fingerprint"]) == 64


def test_missing_semantic_snapshot_blocks_rq2_execute(monkeypatch, tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    object_root.mkdir()
    monkeypatch.setattr(
        preflight_service,
        "settings",
        SimpleNamespace(
            bid_upload_local_root=str(object_root),
            bid_evidence_semantic_model_path=str(tmp_path / "missing"),
            bid_evidence_reranker_model_path="",
            bid_assessment_model_api_key="sk-valid-local-test-key-value",
            feature_bid_assessment_phase4_enterprise_capability=False,
        ),
    )
    version = "mvp1-local-deepseek-v4-flash-1.0.1"

    result = build_execute_preflight(
        _Db(SimpleNamespace(version=version, status="active")),
        runtime_access=_runtime_access(access_mode="execute", worker_running=True),
        expected_model_profile_version=version,
        rq2_runtime_ready=True,
        authority_epoch="execute-process",
        view_only_secret_isolated=True,
    )

    assert "SEMANTIC_SNAPSHOT" in result["blocking_codes"]
    assert result["launch_ready"] is False
    assert result["current_process_ready"] is False


def test_phase4b5_contract_and_frontend_freeze_authority_recheck() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "bid_assessment" / "v1" / "execute-preflight-v2.schema.json")
        .read_text(encoding="utf-8")
    )
    profile = json.loads(
        (ROOT / "contracts" / "bid_assessment" / "v1" / "phase4b5-runtime-operations-profile.json")
        .read_text(encoding="utf-8")
    )
    frontend = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    api = (WEB_ROOT / "bidAssessmentRuntimeLabApi.js").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1").read_text(
        encoding="utf-8"
    )

    assert schema["properties"]["schema"]["const"] == EXECUTE_PREFLIGHT_SCHEMA
    assert profile["authority_transition"]["browser_can_elevate"] is False
    assert profile["run_lifecycle_ui"]["strong_etag_required"] is True
    assert "async function requireFreshExecute" in frontend
    assert "preflight.value.current_process_ready === true" in frontend
    assert "currentRunCommandContext" in frontend
    assert "executePreflight:" in api
    assert "cancelRun:" in api
    assert "retryRun:" in api
    assert '$env:BID_ASSESSMENT_MODEL_API_KEY = "local-view-only-disabled"' in launcher
    assert '$env:DEEPSEEK_API_KEY = "local-view-only-disabled"' in launcher
