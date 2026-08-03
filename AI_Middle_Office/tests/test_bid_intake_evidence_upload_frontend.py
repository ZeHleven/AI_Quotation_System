from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_bid_intake_api_exposes_evidence_pipeline_contract() -> None:
    source = _source("bidIntakeApi.js")

    assert "createEvidenceParseJob" in source
    assert "evidenceParseJobs" in source
    assert "retryEvidenceParseJob" in source
    assert "/parse-jobs" in source
    assert "/index-status" in source


def test_bid_intake_workbench_exposes_upload_and_parse_progress() -> None:
    source = _source("BidIntakeAssessment.vue")

    assert "上传并解析招标资料" in source
    assert "系统自动识别" in source
    assert "formData.append('file_type', 'auto')" in source
    assert "本批资料类型" not in source
    assert "evidenceUpload.fileType" not in source
    assert 'v-model:file-list="evidenceUploadFiles"' in source
    assert ':auto-upload="false"' in source
    assert "uploadEvidenceFiles" in source
    assert "activeEvidenceJobs" in source
    assert "refreshEvidenceProgress" in source
    assert "retryEvidenceParseJob" in source
    assert "证据清单已自动更新" in source
    assert "loadReadiness()" in source


def test_bid_intake_upload_keeps_the_evidence_gate() -> None:
    source = _source("BidIntakeAssessment.vue")

    assert ':disabled="!readiness?.ready_to_start"' in source
    assert "ACTIVE_MANIFEST_REQUIRED" in source
    assert "READY_EVIDENCE_REQUIRED" in source


def test_bid_intake_workbench_exposes_live_agent_runtime_graph() -> None:
    workbench = _source("BidIntakeAssessment.vue")
    graph = _source("BidIntakeRunGraph.vue")

    assert "BidIntakeRunGraph" in workbench
    assert ":run=\"activeRun\"" in workbench
    assert "liveTraceStats" in workbench
    assert "window.setInterval(refreshSelected, 1200)" in workbench
    assert "bid-intake-agent-trace/v1" in graph
    assert "forceSimulation" in graph
    assert "ReAct、Tool、Observation" in graph
    assert "LLM 输入" in graph
    assert "行动计划" in graph
    assert "循环判断" in graph
    assert "发送 LLM" in graph
    assert "结果返回" in graph
    assert ".attr('r', 18)" in graph
    assert "不展示模型私有思维链" in graph
