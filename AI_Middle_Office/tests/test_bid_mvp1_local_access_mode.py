from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.bid_mvp1_local_lab import (
    LOCAL_ACCESS_VIEW_ONLY,
    local_access_mode,
)
from app.api.v1 import bid_assessment_runtime_lab as runtime_lab_api


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT.parent / "ai-web" / "src"


def test_local_access_mode_is_fail_safe_and_explicit(monkeypatch) -> None:
    monkeypatch.delenv("BID_MVP1_LOCAL_ACCESS_MODE", raising=False)
    assert local_access_mode() == LOCAL_ACCESS_VIEW_ONLY

    monkeypatch.setenv("BID_MVP1_LOCAL_ACCESS_MODE", "execute")
    with pytest.raises(RuntimeError, match="must be view-only"):
        local_access_mode()

    monkeypatch.setenv("BID_MVP1_LOCAL_ACCESS_MODE", "unsafe")
    with pytest.raises(RuntimeError, match="must be view-only"):
        local_access_mode()


def test_local_app_has_server_side_write_and_worker_fences() -> None:
    source = (ROOT / "app" / "mvp1_local.py").read_text(encoding="utf-8")
    assert '_SAFE_VIEW_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})' in source
    assert '"code": "BID_MVP1_VIEW_ONLY"' in source
    assert "validate_local_lab_read_only" in source
    assert "_worker_loop" not in source
    assert "process_local_lab_cycle" not in source


def test_capability_projection_requires_server_owned_execute_state(monkeypatch) -> None:
    monkeypatch.setattr(runtime_lab_api, "_mvp1_enabled", lambda: True)
    state = SimpleNamespace(
        bid_mvp1_access_mode="view-only",
        bid_mvp1_worker_enabled=False,
        bid_mvp1_worker_running=False,
        bid_mvp1_model_calls_enabled=False,
        bid_mvp1_model_provider="deepseek-v4-flash",
        bid_mvp1_retrieval_mode="rq2b",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    view_only = runtime_lab_api._runtime_access(request)
    assert view_only["execution_enabled"] is False
    assert view_only["write_enabled"] is False
    assert view_only["worker_running"] is False
    assert view_only["model_calls_enabled"] is False

    state.bid_mvp1_access_mode = "execute"
    state.bid_mvp1_worker_enabled = True
    state.bid_mvp1_worker_running = True
    state.bid_mvp1_model_calls_enabled = True
    retired_execute = runtime_lab_api._runtime_access(request)
    assert retired_execute["access_mode"] == "view-only"
    assert retired_execute["execution_enabled"] is False
    assert retired_execute["write_enabled"] is False
    assert retired_execute["worker_running"] is False
    assert retired_execute["model_calls_enabled"] is False


def test_launcher_is_view_only_without_workflow_dependency() -> None:
    source = (
        ROOT / "scripts" / "start_bid_assessment_mvp1_local.ps1"
    ).read_text(encoding="utf-8")
    assert '[ValidateSet("view-only")]' in source
    assert '[string]$AccessMode = "view-only"' in source
    assert '$env:BID_MVP1_LOCAL_ACCESS_MODE = $AccessMode' in source
    assert "LabDirectoryName must be a local .local-mvp1[-name] directory" in source
    assert '"$historicalLabDirectoryName-phase4c1"' in source
    assert '"$historicalLabDirectoryName-phase4c3"' in source
    assert 'import langgraph' not in source
    assert '$health.access_mode -ne $AccessMode' in source


def test_frontend_marks_fixed_workflow_as_removed() -> None:
    source = (WEB_ROOT / "BidAssessmentRuntimeLab.vue").read_text(encoding="utf-8")
    assert "Legacy Workflow Removed" in source
    assert "runtime:retired" in source
    assert "固定 P0—P4、Plan Revision、Task DAG 和 Continuation" in source
