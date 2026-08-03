from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
APP_VUE = ROOT_DIR / "ai-web" / "src" / "App.vue"
UNIFIED_QUOTES = ROOT_DIR / "ai-web" / "src" / "UnifiedQuotes.vue"
UNIFIED_QUOTE_API = ROOT_DIR / "ai-web" / "src" / "unifiedQuoteApi.js"
BUDGET_PRICING = ROOT_DIR / "ai-web" / "src" / "BudgetProjectPricing.vue"
BUDGET_PROJECT_API = ROOT_DIR / "ai-web" / "src" / "budgetProjectApi.js"
LEGACY_QUOTE = ROOT_DIR / "index.html"


def test_navigation_keeps_project_and_conversation_quote_entries():
    source = APP_VUE.read_text(encoding="utf-8")

    assert "<span>项目报价</span>" in source
    assert "@click=\"navigate('/quotes')\"" in source
    assert "<span>对话报价</span>" in source
    assert "@click=\"openQuickQuote('quick')\"" in source
    assert "if (pathname === '/quotes') return 'unifiedQuotes'" in source
    assert "if (pathname === '/quotes/new') return 'unifiedQuoteNew'" in source


def test_project_quote_workspace_supports_list_create_and_chat_handoff():
    source = UNIFIED_QUOTES.read_text(encoding="utf-8")
    api_source = UNIFIED_QUOTE_API.read_text(encoding="utf-8")

    assert "当前为双入口调试阶段" in source
    assert "新建项目报价" in source
    assert "进入对话报价" in source
    assert "账户定额、企业定额、AI 估价" in source
    assert "aimo_quote_job_handoff" in source
    assert "/index.html?" in source
    assert "listQuoteJobs" in api_source
    assert "listQuoteHistory" in api_source
    assert "listBudgetProjects" in api_source
    assert "createQuoteJob" in api_source
    assert "createBudgetProject" in api_source


def test_chat_quote_uses_summary_and_budget_detail_keeps_workspace_views():
    pricing = BUDGET_PRICING.read_text(encoding="utf-8")
    legacy = LEGACY_QUOTE.read_text(encoding="utf-8")

    for label in ("快速审核", "专业全字段", "费用汇总", "版本记录"):
        assert label in pricing

    assert "pricingWorkspaceView" in pricing
    assert "quoteWorkflowSteps" in pricing
    assert "<span>当前报价合计</span>" in pricing
    assert "<span>账户定额</span>" in pricing
    assert "<span>企业定额</span>" in pricing
    assert "draftAccountQuotaCount" in pricing
    assert "draftEnterpriseQuotaCount" in pricing
    assert "draftManualChangeCount > 0" in pricing
    assert "draftAttentionCount > 0" in pricing
    assert '<div class="draft-meta">' not in pricing
    assert '<div class="pricing-metrics draft-metrics">' not in pricing
    assert "<span>草稿行数</span>" not in pricing
    assert "<span>待补价</span>" not in pricing
    assert "高级计价策略" not in pricing
    assert 'class="pricing-context"' not in pricing
    assert "当前可用企业定额" not in pricing
    assert ">刷新计价</el-button>" not in pricing
    assert "生成不可变计价版本（P2-1）" not in pricing
    assert 'description="暂无报价版本"' in pricing
    assert "archiveSelectedPricingRun" in pricing
    assert "activateSelectedPricingRun" in pricing
    assert 'title="报价摘要"' in legacy
    assert "quote-result-summary" in legacy
    assert "对话窗口只保留报价结果和内部测试指标" in legacy
    assert "进入详细报价界面" in legacy
    assert "openDetailedQuoteWorkspace" in legacy
    assert "window.location.href = `/admin/budget-projects/${projectId}`" in legacy


def test_professional_view_keeps_all_quote_fields_in_budget_detail_workspace():
    pricing = BUDGET_PRICING.read_text(encoding="utf-8")

    labels = (
        "序号",
        "名称",
        "项目特征",
        "单位",
        "工程量",
        "不含税综合单价",
        "不含税综合合价",
        "人工费",
        "主材费",
        "辅材费",
        "机械费",
        "管理费",
        "措施费",
        "税费",
    )
    for label in labels:
        assert label in pricing


def test_budget_detail_workspace_hides_construction_note_column_but_keeps_simple_editor():
    pricing = BUDGET_PRICING.read_text(encoding="utf-8")
    budget_api = BUDGET_PROJECT_API.read_text(encoding="utf-8")
    legacy = LEGACY_QUOTE.read_text(encoding="utf-8")
    construction_drawer = pricing.split(
        'v-model="constructionNoteDrawer.visible"',
        1,
    )[1].split(
        'v-model="costBasisDrawer.visible"',
        1,
    )[0]

    assert 'label="施工提示"' not in pricing
    assert "工艺与避坑备注" in pricing
    assert "construction-note-drawer" in pricing
    assert "工艺做法与施工避坑" in construction_drawer
    assert "constructionNoteDrawer.row.item_name" in construction_drawer
    assert "报价边界 / 不含项" not in construction_drawer
    assert "需人工确认" not in construction_drawer
    assert "保存记录" not in construction_drawer
    assert "constructionNoteStatus" not in construction_drawer
    assert "constructionNoteTag" not in construction_drawer
    assert "updated_at" not in construction_drawer
    assert "line_revision" not in construction_drawer
    assert "sanitizeConstructionNote" in pricing
    assert "constructionPricingPhrases" in pricing
    assert "constructionRemark" in pricing
    assert (
        '<el-descriptions-item label="价格来源">{{ draftPriceSourceLabel(constructionNoteDrawer.row) }}</el-descriptions-item>'
        not in pricing
    )
    assert (
        '<el-descriptions-item label="取价说明">{{ draftPriceSourceMeta(constructionNoteDrawer.row) }}</el-descriptions-item>'
        not in pricing
    )
    assert "openConstructionNoteDrawer" in pricing
    assert "saveConstructionNote" in pricing

    assert "draftBreakdownInputValue(constructionNoteDrawer.row, 'remark')" in pricing
    assert "updatePricingDraftLineConstructionNote" in pricing
    assert "/construction-note" in budget_api
    assert "sanitizeConstructionNote" in legacy
    assert "constructionPricingPhrases" in legacy
    assert (
        '<el-descriptions-item label="价格来源">{{ previewUnitPriceSourceText(constructionNoteDrawerRow) }}</el-descriptions-item>'
        not in legacy
    )
    assert (
        '<el-descriptions-item label="报价依据">{{ costReferenceLabel(constructionNoteDrawerRow) }}</el-descriptions-item>'
        not in legacy
    )
    assert "工程量、单价、费用组成及版本记录，统一在详细报价界面查看" in legacy


def test_chat_quote_summary_shows_only_handoff_essentials():
    legacy = LEGACY_QUOTE.read_text(encoding="utf-8")

    assert "<span>当前报价合计</span>" in legacy
    assert "<span>企业定额</span>" in legacy
    assert "<span>AI 估价</span>" in legacy
    assert "内部测试命中率" in legacy
    assert "previewPricingSourceSummary" in legacy
    assert "/quote/jobs/${quoteJobId}/budget-workspace" in legacy
    assert "budgetPricingDraftId" in legacy
    assert "DEFAULT_DETAIL_BUDGET_PROJECT_ID" not in legacy
    assert "aimo_quote_detail_handoff" in legacy


def test_chat_quote_stream_uses_business_progress_instead_of_technical_events():
    legacy = LEGACY_QUOTE.read_text(encoding="utf-8")

    assert "quote-progress-bar" in legacy
    assert "quoteProgressPercent" in legacy
    assert "QUOTE_STAGE_PROGRESS" in legacy
    assert "页面会持续更新处理进度" in legacy
    assert "系统会自动完成整理和计价，您无需重复提交" in legacy
    for label in ("接收需求", "读取内容", "整理清单", "计算报价", "准备核对"):
        assert label in legacy

    assert "正在创建异步报价任务" not in legacy
    assert "异步报价任务已创建" not in legacy
    assert "任务号：${displayQuoteJobNumber(job)}" not in legacy
    assert "追踪ID：${traceId}" not in legacy
    assert "等待 Worker 接手" not in legacy
    assert "charQueue.push" not in legacy
    assert "appendAssistantContent(msgIndex, data.message" not in legacy


def test_chat_quote_hides_candidate_controls_and_shows_only_internal_source_metrics():
    legacy = LEGACY_QUOTE.read_text(encoding="utf-8")

    assert '<div v-if="false" aria-hidden="true">' in legacy
    assert "<strong>{{ previewPricingSourceSummary.enterpriseRecognized }} 项</strong>" in legacy
    assert "enterpriseRecognitionRateText" in legacy
    assert "summary.enterpriseRecognized = summary.enterpriseAuto + summary.v2Recognized" in legacy
    assert "企业定额库命中率" not in legacy
    assert "V2识别" not in legacy
