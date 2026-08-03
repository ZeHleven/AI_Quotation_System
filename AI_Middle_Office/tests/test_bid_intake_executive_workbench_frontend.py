from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_executive_workbench_exposes_business_and_runtime_states() -> None:
    source = _source("BidIntakeWorkbench.vue")

    assert "尚未研判" in source
    assert "正在研判" in source
    assert "研判完成" in source
    assert "研判未完整完成" in source
    assert "isIncompleteAssessment" in source
    assert "model_invocation_failed" in source
    assert "模型服务额度异常，本次尚未开始研判" in source
    assert "系统现已支持备用模型自动接管" in source
    assert "AGENT_TERMINATED_EARLY" in source
    assert "使用已有资料重新研判" in source
    assert "补充资料后复判" in source
    assert "workflowState" in source
    assert "总经办项目研判" in source


def test_executive_workbench_focuses_on_decision_and_missing_materials() -> None:
    source = _source("BidIntakeWorkbench.vue")

    assert "为什么这样建议" in source
    assert "还需要向甲方索取什么" in source
    assert "复制索取清单" in source
    assert "确认参与报价" not in source
    assert "openDecision('approved')" not in source
    assert "openDecision('rejected')" not in source
    assert "bidIntakeApi.decide" not in source
    assert "submitDecision" not in source
    assert "decisionDialog" not in source
    assert "上传补充资料" in source
    assert '@click="openSupplementUpload"' in source
    assert "supplementUploadOpen.value = true" in source
    assert "fileInput.click()" in source
    assert "使用补充资料重新研判" in source
    assert "openDecision('supplement_requested')" not in source
    assert "missing_materials" in source
    assert "copyMissingMaterialRequest" in source
    assert "缺失资料清单尚未形成" in source
    assert "missingListUnavailable" in source


def test_executive_workbench_keeps_agent_trace_behind_a_drawer() -> None:
    source = _source("BidIntakeWorkbench.vue")

    assert 'v-model="traceDrawerVisible"' in source
    assert "查看详细研判过程" in source
    assert "<BidIntakeRunGraph" in source
    assert ':run="activeRun"' in source
    assert "不展示模型私有思维链" in source
    assert "超过 90 秒没有收到新的运行事件" in source


def test_executive_workbench_can_cancel_a_live_run() -> None:
    source = _source("BidIntakeWorkbench.vue")
    api = _source("bidIntakeApi.js")

    assert "终止研判" in source
    assert "终止本次研判" in source
    assert "canCancelRun" in source
    assert "cancelRun" in source
    assert "本次运行已经停止" in source
    assert "重新发起研判" in source
    assert "bidIntakeApi.cancel" in source
    assert "/cancel" in api


def test_executive_workbench_auto_classifies_files_and_guards_reassessment() -> None:
    source = _source("BidIntakeWorkbench.vue")

    assert "自动识别资料类型" in source
    assert "formData.append('file_type', 'auto')" in source
    assert "本批资料类型" not in source
    assert "hasNewManifest" in source
    assert "current > previous" in source
    assert "canStartSupplementReview" in source


def test_main_route_uses_executive_workbench_and_policy_has_its_own_route() -> None:
    source = _source("App.vue")

    assert "<BidIntakeWorkbench" in source
    assert ':project="selectedBidIntakeAgentProject"' in source
    assert "/admin/bid-intake-agent?view=policy" in source
    assert "研判标准管理" in source
    assert "canManageBidIntakePolicy" in source


def test_executive_workbench_does_not_load_calibration_operations() -> None:
    source = _source("BidIntakeWorkbench.vue")

    assert "calibrationReport" not in source
    assert "calibrationCandidates" not in source
    assert "calibrationSamples" not in source
    assert "saveCalibrationLabel" not in source
