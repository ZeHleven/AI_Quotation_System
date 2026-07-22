from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_budget_pricing_module_is_feature_gated_and_fail_closed():
    app = _source("App.vue")
    projects = _source("BudgetProjects.vue")
    pricing = _source("BudgetProjectPricing.vue")

    assert "module.key === 'budget_pricing'" in app
    assert "['available', 'forbidden'].includes(budgetPricingModule.value?.status)" in app
    assert ':pricing-feature-available="budgetPricingFeatureAvailable"' in app
    assert "pricingFeatureAvailable: { type: Boolean, default: false }" in projects
    assert "featureAvailable: { type: Boolean, default: false }" in pricing
    assert "hasOwn(props.project?.capabilities, key)" in pricing
    assert "readinessCapability('can_create_pricing_run')" in pricing
    assert "projectCapability('can_create_pricing_run')" in pricing
    assert "!projectArchived.value" in pricing
    assert "当前账号无项目成本计价权限" in pricing
    assert "cost_viewer、cost_editor 或 cost_approver" in pricing


def test_pricing_run_creation_uses_only_formal_import_pointers_and_readiness_quota():
    source = _source("BudgetProjectPricing.vue")
    create_block = source.split("async function createPricingRun()", 1)[1].split(
        "async function selectRun()", 1
    )[0]

    assert "source_import_batch_id: Number(props.project.active_import_batch_id)" in create_block
    assert "source_import_revision_id: Number(props.project.active_import_revision_id)" in create_block
    assert (
        "expected_enterprise_quota_version_id: "
        "Number(readiness.value.active_quota_version.id)"
    ) in create_block
    assert "selectedImport" not in create_block
    assert "formalPointersMatch.value" in source


def test_budget_pricing_api_contract_is_wired():
    api = _source("budgetProjectApi.js")

    assert "/pricing-readiness" in api
    assert "/pricing-runs'" in api
    assert "/pricing-runs/' + runId + '/lines" in api
    assert "/lines/' + lineId + '/candidates" in api
    assert "currentPricingDraft: (projectId, params)" in api
    assert "updatePricingDraftTotalsConfig: (projectId, payload)" in api
    assert "currentPricingDraftQuoteJob: (projectId, params)" in api


def test_partial_pricing_and_backend_status_contracts_are_visible():
    source = _source("BudgetProjectPricing.vue")

    assert "当前为部分计价，成本尚不完整" in source
    assert "未匹配项不会按 0 元伪装为完整成本" in source
    match_options = source.split("const matchStatusOptions = [", 1)[1].split("]", 1)[0]
    for status in (
        "auto_matched",
        "manual_matched",
        "ambiguous",
        "unmatched",
        "unit_conflict",
    ):
        assert f"value: '{status}'" in match_options
    assert "value: 'matched'" not in match_options
    assert "data_error" not in source

    pricing_options = source.split("const pricingStatusOptions = [", 1)[1].split("]", 1)[0]
    for status in (
        "priced",
        "quantity_unresolved",
        "missing_unit_price",
        "pending_match",
        "unit_conflict",
        "numeric_overflow",
    ):
        assert f"value: '{status}'" in pricing_options
    for label in ("自动匹配", "人工匹配", "工程量待解决", "定额单价缺失", "数值超限"):
        assert label in source
    assert "pricingStatusLabel(row.pricing_status)" in source


def test_pricing_draft_modes_are_loaded_and_mutated_independently():
    source = _source("BudgetProjectPricing.vue")

    assert "currentPricingDraft(projectId.value, { pricing_mode: selectedDraftMode.value })" in source
    assert "currentPricingDraftQuoteJob(projectId.value, { pricing_mode: selectedDraftMode.value })" in source
    assert "pricing_mode: selectedDraftMode.value" in source
    assert "watch(\n  selectedDraftMode," in source
    assert "draftModeOf(draft.value) === selectedDraftMode.value ? { expected_revision: draftRevision.value }" in source
    assert "切换模式并重建" not in source


def test_pricing_filters_metrics_and_quota_code_keyword_are_wired():
    source = _source("BudgetProjectPricing.vue")

    assert "lineFilters = reactive({ keyword: '', match_status: '', pricing_status: '' })" in source
    assert "{ match_status: lineFilters.match_status }" in source
    assert "{ pricing_status: lineFilters.pricing_status }" in source
    assert 'placeholder="搜索项目名称、特征或定额编码"' in source
    assert "{ keyword: lineFilters.keyword.trim() }" in source
    for metric in (
        "quantity_unresolved_count",
        "missing_price_count",
        "numeric_overflow_count",
    ):
        assert f"summaryCount('{metric}')" in source


def test_null_money_is_not_rendered_as_zero_and_candidates_are_read_only():
    source = _source("BudgetProjectPricing.vue")

    assert "value === null || value === undefined || value === ''" in source
    assert "return '—'" in source
    assert "line_total" in source
    assert "effective_unit_cost" in source
    assert "quota_unit_price" in source
    assert "selected_quota" in source
    assert "completeness_status" in source
    assert "priced_subtotal" in source
    assert "P2-1 仅供核对，不能在此选定或修改候选" in source
    for mutation_name in (
        "selectCandidate",
        "confirmCandidate",
        "updateCandidate",
        "applyCandidate",
    ):
        assert mutation_name not in source


def test_budget_pricing_quote_header_stage_one_fields_are_visible():
    source = _source("BudgetProjectPricing.vue")

    for label in (
        "项目名称",
        "特征描述",
        "区域",
        "主材采购方式",
        "工程量",
        "不含税综合单价",
        "不含税综合合价",
        "人工费",
        "主材费",
        "辅材费",
        "税金",
        "主材费不含损耗",
        "损耗率",
        "机械费",
        "综合费",
        "管理费",
        "利润费",
        "措施费",
        "甲供材单价",
        "甲供材损耗金",
        "备注",
    ):
        assert f'label="{label}"' in source

    for expression in (
        "rowRegion(row)",
        "rowWorkArea(row)",
        "materialSupplyMode(row)",
        "quoteUnitPrice(row)",
        "feeUnitValue(row, 'labor')",
        "feeUnitValue(row, 'main_material')",
        "feeUnitValue(row, 'auxiliary_material')",
        "formatRate(lossRate(row))",
        "ownerMaterialUnitPrice(row)",
        "ownerMaterialLossAmount(row)",
        "rowRemark(row)",
    ):
        assert expression in source

    assert "quote-line-table" in source


def test_budget_pricing_quote_header_stage_two_breakdown_editing_is_wired():
    source = _source("BudgetProjectPricing.vue")

    for needle in (
        "const draftBreakdownInputs = reactive({})",
        "const draftBreakdownEditing = reactive({})",
        "const draftBreakdownColumns = [",
        "draftBreakdownInputValue(row, column.key)",
        "setDraftBreakdownInput(row, column.key, $event)",
        "isDraftBreakdownEditing(row, column.key)",
        "beginDraftBreakdownEdit(row, column.key)",
        "formatBreakdownDisplay(row, column)",
        ':icon="Edit"',
        "draftPreviewUnitPrice(row) ?? quoteUnitPrice(row)",
        "draftPreviewLineTotal(row) ?? lineTotalCost(row)",
        "draftPreviewTaxAmount(row) ?? taxAmount(row)",
        "pricing_breakdown: breakdown",
        "pricing_breakdown_edit",
        "manual_breakdown: '拆分计价'",
    ):
        assert needle in source

    for key in (
        "labor_unit_cost",
        "main_material_unit_cost",
        "auxiliary_material_unit_cost",
        "machinery_unit_cost",
        "management_unit_cost",
        "profit_unit_cost",
        "measure_unit_cost",
        "owner_material_unit_price",
        "owner_material_loss_amount",
    ):
        assert key in source


def test_budget_pricing_quote_totals_panel_is_wired():
    source = _source("BudgetProjectPricing.vue")

    for needle in (
        "quoteStatCards",
        "quoteTotalsRows",
        "totalsExpanded",
        "totalsConfigInputs",
        "主材",
        "辅材",
        "人工费",
        "分包",
        "不含税",
        "含税",
        "报价上下浮百分比",
        "updatePricingDraftTotalsConfig(projectId.value",
        "quote_adjustment_percent",
        "measures_rate",
        "management_rate",
    ):
        assert needle in source
