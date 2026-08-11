import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = ROOT / "deploy" / "app-node" / "release"


def test_release_configuration_tracks_the_current_production_baseline():
    baseline = json.loads((RELEASE_DIR / "production-baseline.json").read_text(encoding="utf-8"))
    assert baseline == {
        "schema_version": 1,
        "production_commit": "52ccdc12c36bf69265a6d49abbe506b6f0d9c351",
        "image_tag": "20260811-nonagent-52ccdc1",
        "image_id": "sha256:3d96b0f2ad187676793c005740a663c8195aaaaf09c568146cf807b77a7f7dac",
        "database_head": "20260808_0082",
        "deployed_at": "2026-08-11T21:09:02+08:00",
        "agent_runtime_deployed": False,
    }


def test_focused_test_map_is_valid_and_does_not_request_the_full_suite():
    payload = json.loads((RELEASE_DIR / "test-map.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    names = {rule["name"] for rule in payload["rules"]}
    assert {"budget-pricing", "budget-projects", "enterprise-quota", "auth-and-rbac"} <= names
    for rule in payload["rules"]:
        assert rule["patterns"]
        assert rule["tests"]
        for test in rule["tests"]:
            assert test.startswith("tests/test_") and test.endswith(".py")
            assert (ROOT / "AI_Middle_Office" / test).is_file()


def test_local_release_tool_has_required_approval_and_transport_gates():
    script = (RELEASE_DIR / "Prepare-AiRelease.ps1").read_text(encoding="utf-8")
    required = (
        "ApproveSensitiveTests",
        "ApproveAgentRelease",
        "ApproveMigration",
        "agent_runtime_allowed",
        "test-map.json",
        "release-manifest.json",
        "/api/v1/auth/login",
        "/api/v1/files",
        "ChunkSizeMB must be between 5 and 45",
        "docker",
        "save",
    )
    for marker in required:
        assert marker in script
    assert "scp " not in script.lower()
    assert "PRIVATE KEY" not in script
    assert 'Invoke-Checked $python @(\"-m\", \"pytest\", \"-q\")' not in script
    assert "no-migration releases only" in script


def test_ecs_release_manager_syntax_and_safety_contract():
    manager = RELEASE_DIR / "ecs-ai-release"
    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    assert bash, "bash is required to validate the ECS release manager"
    completed = subprocess.run(
        [bash, "-n", str(manager)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    script = manager.read_text(encoding="utf-8")
    required = (
        "flock",
        "idle_gate",
        "mysqldump",
        "database_backup=",
        "backup_temp_dir",
        "automatic_application_rollback",
        "ReadonlyRootfs",
        "no-new-privileges:true",
        "cleanup_transfer",
        "validate_release_uploader",
        "get_effective_roles",
        "migration_release_requires_dedicated_runbook",
        "rollback_release_is_not_current_image",
        "rollback.sh",
    )
    for marker in required:
        assert marker in script
    assert "docker system prune" not in script
    assert "docker image prune" not in script
    assert "rm -rf /" not in script
    assert "scp " not in script.lower()


def test_confirmation_updates_only_private_git_operational_state():
    script = (RELEASE_DIR / "Confirm-AiRelease.ps1").read_text(encoding="utf-8")
    assert "--git-common-dir" in script
    assert "ai-release-production-baseline.json" in script
    assert "Set-Content" in script
    assert "git commit" not in script.lower()
    assert "git push" not in script.lower()


def test_one_time_bootstrap_is_hash_verified_and_small_enough_for_ssm():
    script = (RELEASE_DIR / "New-AiReleaseBootstrap.ps1").read_text(encoding="utf-8")
    for marker in ("GzipStream", "ToBase64String", "sha256sum -c", "/usr/local/sbin/ai-release"):
        assert marker in script
    assert "scp " not in script.lower()


def test_image_version_label_does_not_invalidate_the_dependency_layer():
    dockerfile = (ROOT / "deploy" / "app-node" / "Dockerfile").read_text(encoding="utf-8")
    dependency_layer = dockerfile.index("RUN python -m pip install")
    version_argument = dockerfile.index("ARG APP_VERSION")
    version_label = dockerfile.index("LABEL org.opencontainers.image.title")
    assert dependency_layer < version_argument < version_label


def test_root_release_wrapper_uses_the_guarded_powershell_entrypoint():
    wrapper = (ROOT / "release.cmd").read_text(encoding="utf-8")
    assert "-NoProfile -ExecutionPolicy Bypass" in wrapper
    assert "Prepare-AiRelease.ps1" in wrapper
    assert "%*" in wrapper
