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
    assert "activatePricingRun: (projectId, runId)" in api
    assert "/pricing-runs/' + runId + '/activate" in api
    assert "archivePricingRun: (projectId, runId)" in api
    assert "/pricing-runs/' + runId + '/archive" in api
    assert "/pricing-runs/' + runId + '/lines" in api
    assert "/lines/' + lineId + '/candidates" in api
    assert "currentPricingDraft: (projectId, params)" in api
    assert "updatePricingDraftTotalsConfig: (projectId, payload)" in api
    assert "currentPricingDraftQuoteJob: (projectId, params)" in api
    assert "pricingDraftResourceDetails: (projectId, params)" in api
    assert "/pricing-draft/resource-details" in api
    assert "exportPricingDraftStatistics: (projectId, params)" in api
    assert "/pricing-draft/statistics-export" in api
    assert "responseType: 'blob'" in api
    assert "pricingDraftProcurementStatistics: (projectId, params)" in api
    assert "/pricing-draft/procurement-statistics" in api
    assert "materializeProjectQuota: (projectId, lineId, payload)" in api
    assert "createProjectQuotaResource: (projectId, lineId, payload)" in api
    assert "updateProjectQuotaResource: (projectId, lineId, resourceId, payload)" in api
    assert "deleteProjectQuotaResource: (projectId, lineId, resourceId, payload)" in api
    assert "syncProjectQuotaToEnterprise: (projectId, lineId, payload)" in api


def test_project_quota_row_and_standalone_resource_workbench_are_visible():
    source = _source("BudgetProjectPricing.vue")
    template = source.split("<script setup>", 1)[0]
    projects = _source("BudgetProjects.vue")

    for label in (
        "工料机明细",
        "新增工料机",
        "删除工料机",
        "保存并重算项目定额",
        "保存后同步到企业定额库",
        "企业定额同步状态",
    ):
        assert label in template
    for field in (
        "resource_code",
        "component_type",
        "resource_name",
        "worker_or_subtype",
        "category",
        "specification",
        "brand",
        "unit",
        "quantity",
        "unit_price",
        "amount",
        "tax_rate",
        "work_content",
        "calculation_rule",
    ):
        assert f"projectQuotaEditor.form.{field}" in template
    assert ':show-header="false"' in source
    assert "matched-quota-aligned-table" in source
    assert "projectQuotaMainItem(row)" in source
    assert 'class="project-quota-relation"' in source
    assert 'class="budget-panel project-quota-resource-workbench"' in template
    assert 'id="project-quota-resource-title"' in template
    assert '@row-click="selectProjectQuota(row)"' in template
    assert '@row-click="startEditProjectQuotaResource"' in template
    assert "projectQuotaEditor.visible" in template
    assert "当前编辑的是项目定额工作副本" not in template
    assert "当前清单项目：" not in template
    assert "增删改只影响当前项目和当前报价草稿" not in template
    assert "<BudgetProjectPricing" in projects


def test_project_quota_resource_row_click_toggles_editor_for_the_same_resource():
    source = _source("BudgetProjectPricing.vue")
    edit_handler = source.split(
        "function startEditProjectQuotaResource(resource) {",
        1,
    )[1].split(
        "function recalculateProjectQuotaEditorAmount()",
        1,
    )[0]

    assert '@row-click="startEditProjectQuotaResource"' in source
    assert "projectQuotaEditor.resource === resource" in edit_handler
    assert "projectQuotaEditor.resource.resource_uuid === resource.resource_uuid" in edit_handler
    assert "projectQuotaEditor.visible && projectQuotaEditor.mode === 'edit' && isCurrentResource" in edit_handler
    assert "projectQuotaEditor.visible = false" in edit_handler
    assert "return" in edit_handler


def test_partial_pricing_and_backend_status_contracts_are_visible():
    source = _source("BudgetProjectPricing.vue")

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


def test_quote_draft_action_bar_hides_generation_and_rebuild_buttons():
    source = _source("BudgetProjectPricing.vue")
    action_bar = source.split(
        '<div class="draft-actions">',
        1,
    )[1].split(
        "</div>",
        1,
    )[0]

    assert '@click="startDraftQuoteJob"' not in action_bar
    assert '@click="saveDraft"' not in action_bar
    assert "一键生成报价" not in action_bar
    assert "draftActionLabel" not in action_bar
    assert "const draftActionLabel" not in source


def test_current_draft_filters_are_visible_and_wired():
    source = _source("BudgetProjectPricing.vue")

    assert "draftFilters = reactive({ keyword: '', match_status: '', pricing_status: '' })" in source
    assert "{ match_status: draftFilters.match_status }" in source
    assert "{ pricing_status: draftFilters.pricing_status }" in source
    assert 'placeholder="搜索项目名称、特征或来源行"' in source
    assert "{ keyword: draftFilters.keyword.trim() }" in source


def test_version_history_is_compact_and_activation_refreshes_quick_review():
    source = _source("BudgetProjectPricing.vue")
    projects = _source("BudgetProjects.vue")
    version_template = source.split(
        "<template v-if=\"pricingWorkspaceView === 'versions'\">",
        1,
    )[1].split("</template>", 1)[0]
    activate_block = source.split(
        "async function activateSelectedPricingRun() {",
        1,
    )[1].split(
        "async function archiveSelectedPricingRun() {",
        1,
    )[0]

    assert "<strong>版本记录</strong>" in version_template
    assert "<span>版本号</span>" in version_template
    assert ":label=\"pricingRunVersionLabel(run)\"" in version_template
    assert "@click=\"archiveSelectedPricingRun\"" in version_template
    assert "归档" in version_template
    assert "@click=\"activateSelectedPricingRun\"" in version_template
    assert "启用" in version_template
    assert "<el-table" not in version_template
    assert "pricingRunHasSnapshot(selectedRun)" in version_template
    assert "const pricingRunHasSnapshot = (run) => run?.has_draft_snapshot === true" in source
    assert "缺少完整快照" in source
    assert "pricingWorkspaceView.value = 'quick'" in activate_block
    assert "await refreshPricing()" in activate_block
    assert "emit('version-activated')" in activate_block
    assert '@version-activated="loadDetail"' in projects


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


def test_budget_pricing_professional_fields_are_exact_and_ordered():
    source = _source("BudgetProjectPricing.vue")
    professional = source.split(
        'v-else-if="pricingWorkspaceView === \'professional\'"',
        1,
    )[1].split(
        '<el-pagination',
        1,
    )[0]
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
        "措施费",
        "管理费",
        "税费",
    )
    positions = [professional.index(f'label="{label}"') for label in labels]
    assert positions == sorted(positions)
    assert 'ref="professionalTableRef"' in professional
    assert '@row-click="toggleProfessionalQuotaRow"' in professional
    assert professional.index('type="expand"') < professional.index('label="序号"')
    assert 'fixed="left" class-name="project-quota-expand-cell"' in professional
    assert '<template #expand="{ expanded }">' in professional
    assert "{{ expanded ? '−' : '+' }}" in professional
    assert 'class="project-quota-relation" @click.stop' in professional
    assert 'class="project-quota-relation-branch"' in professional
    for removed_label in (
        "区域",
        "主材采购方式",
        "价格来源",
        "工艺与避坑备注",
        "调整",
        "主材费不含损耗",
        "损耗率",
        "综合费",
        "利润费",
        "甲供材单价",
        "甲供材损耗金",
    ):
        assert f'label="{removed_label}"' not in professional

    for expression in (
        "draftRowSequence($index)",
        "feeUnitValue(row, 'labor')",
        "feeUnitValue(row, 'main_material')",
        "feeUnitValue(row, 'auxiliary_material')",
        "feeUnitValue(row, 'machinery')",
        "feeUnitValue(row, 'measure')",
        "feeUnitValue(row, 'management')",
        "draftPreviewTaxAmount(row) ?? taxAmount(row) ?? 0",
        "selectProjectQuota(row)",
    ):
        assert expression in professional
    assert "professionalTableRef.value?.toggleRowExpansion(row)" in source


def test_budget_pricing_basis_page_is_removed():
    source = _source("BudgetProjectPricing.vue")

    assert "{ value: 'basis', label: '报价依据' }" not in source
    assert 'class="users-table quote-line-table quote-basis-table"' not in source
    assert '<el-table-column label="报价依据"' not in source
    assert "['quick', 'professional'].includes(pricingWorkspaceView)" in source


def test_draft_header_uses_concise_sync_and_export_labels():
    source = _source("BudgetProjectPricing.vue")
    header = source.split(
        '<div class="draft-actions">',
        1,
    )[1].split(
        "</div>",
        1,
    )[0]

    assert '@click="openAccountQuotaSync">同步至账户定额</el-button>' in header
    assert '@click="exportOriginalFormatPricingDraft">导出</el-button>' in header
    assert "同步到账户定额" not in header
    assert "导出原格式报价 Excel" not in header


def test_matched_quota_main_fields_match_professional_fields():
    source = _source("BudgetProjectPricing.vue")
    quota_columns = source.split(
        "const projectQuotaMainColumns = [",
        1,
    )[1].split(
        "]",
        1,
    )[0]
    labels = (
        "定额编码",
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
        "措施费",
        "管理费",
        "税费",
    )
    positions = [quota_columns.index(f"label: '{label}'") for label in labels]
    assert positions == sorted(positions)
    assert source.count('class="matched-quota-aligned-table"') == 1
    assert source.count('v-for="column in projectQuotaMainColumns"') == 1
    assert quota_columns.index("key: '__expand_spacer'") < quota_columns.index("key: 'quota_code'")
    assert ':show-header="false"' in source
    assert ':label="column.label"' not in source
    assert "projectQuotaMainFieldValue(quotaRow, column)" in source
    assert "project_feature:" in source
    assert "measure_fee:" in source
    assert "management_fee:" in source
    assert "tax_fee:" in source
    assert ".project-quota-relation-branch{" in source
    assert "border-bottom:2px solid #60a5fa" in source
    assert "border-left:2px solid #60a5fa" in source

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
        "导出统计",
        "exportPricingStatistics",
        "budgetProjectApi.exportPricingDraftStatistics",
        "报价统计.xlsx",
    ):
        assert needle in source


def test_statistics_export_supports_single_or_multiple_selected_sheets():
    source = _source("BudgetProjectPricing.vue")

    assert "导出统计 Excel" not in source
    assert '@click="openStatisticsExportDialog">导出统计</el-button>' in source
    assert 'v-model="statisticsExportDialog.visible"' in source
    assert 'v-model="statisticsExportDialog.sections"' in source
    assert ":disabled=\"statisticsExportDialog.sections.length === 0\"" in source
    for value, label in (
        ("summary", "统计汇总"),
        ("main_material", "主材明细"),
        ("auxiliary_material", "辅材明细"),
        ("labor", "人工明细"),
    ):
        assert f"value: '{value}', label: '{label}'" in source
    assert "sections: selectedSections.join(',')" in source
    assert "请至少选择一项导出内容" in source
    assert "confirmStatisticsExport" in source


def test_labor_and_material_totals_open_resource_library_shaped_details():
    source = _source("BudgetProjectPricing.vue")

    assert source.count('size="98vw"') >= 2
    assert 'max-height="calc(100vh - 185px)"' in source
    for binding in (
        "detailBucket: 'main_material'",
        "detailBucket: 'auxiliary_material'",
        "detailBucket: 'labor'",
        "@click=\"openResourceDetail(item)\"",
        "budgetProjectApi.pricingDraftResourceDetails",
        "resourceDetailDrawer",
        "resourceDetailDrawerTitle",
        "exportCurrentResourceDetail",
        "exportPricingStatistics([resourceDetailDrawer.bucket])",
        'class="resource-detail-export"',
    ):
        assert binding in source

    for header in (
        'label="编码"',
        'label="类型"',
        'label="项目名称"',
        'label="工作内容"',
        'label="计算规则"',
        'label="单位"',
        'label="不含税人工单价"',
        'label="人工总价"',
        'label="分类"',
        'label="材料编码"',
        'label="材料名称"',
        'label="规格"',
        'label="品牌"',
        'label="除税单价"',
        'label="总价"',
        'label="数量"',
    ):
        assert header in source

    detail_drawer = source.split(
        'v-model="resourceDetailDrawer.visible"',
        1,
    )[1].split(
        'v-model="procurementDrawer.visible"',
        1,
    )[0]
    assert 'label="含量"' not in detail_drawer
    assert 'label="项目特征及工作内容"' not in detail_drawer


def test_procurement_statistics_show_material_quantities_labor_trades_and_gaps():
    source = _source("BudgetProjectPricing.vue")

    for needle in (
        "采购统计",
        "采购与用工统计",
        "openProcurementStatistics",
        "budgetProjectApi.pricingDraftProcurementStatistics",
        "materialKindCount",
        "laborTradeCount",
        "unresolvedLineCount",
        "材料采购",
        "人工工种",
        "待补资源",
        'label="采购数量"',
        'label="用工数量"',
        "不会被虚构为采购量",
    ):
        assert needle in source


def test_quote_source_counts_show_project_share():
    source = _source("BudgetProjectPricing.vue")

    for expression in (
        "draftSourcePercent(draftAccountQuotaCount)",
        "draftSourcePercent(draftEnterpriseQuotaCount)",
        "draftSourcePercent(draftSummaryCount('ai_estimate_count'))",
    ):
        assert expression in source

    assert source.count("占项目数 {{") == 3
    assert "const draftSourcePercent = (count) => {" in source
    assert "draftSummaryCount('line_count', 'row_count', 'standard_item_count')" in source
    assert "if (total <= 0) return '0.0%'" in source
    assert ".toFixed(1)}%`" in source


def test_quick_review_construction_note_field_is_removed():
    source = _source("BudgetProjectPricing.vue")
    quick_review = source.split(
        'v-if="pricingWorkspaceView === \'quick\'"',
        1,
    )[1].split(
        'v-else-if="pricingWorkspaceView === \'professional\'"',
        1,
    )[0]
    assert '<el-table-column label="施工提示"' not in quick_review
    assert "constructionNoteSummary(row)" not in quick_review
    assert "openConstructionNoteDrawer(row)" not in quick_review


def test_quick_review_uses_tax_exclusive_unit_price_and_removes_confirmation_actions():
    source = _source("BudgetProjectPricing.vue")
    quick_review = source.split(
        'v-if="pricingWorkspaceView === \'quick\'"',
        1,
    )[1].split(
        'v-else-if="pricingWorkspaceView === \'professional\'"',
        1,
    )[0]

    labels = (
        "施工项目",
        "项目特征",
        "单位",
        "工程量",
        "不含税单价",
        "不含税合价",
        "报价来源",
        "风险",
        "状态",
    )
    positions = [quick_review.index(f'label="{label}"') for label in labels]
    assert positions == sorted(positions)
    assert 'type="selection"' not in quick_review
    assert "draftPreviewUnitPrice(row) ?? draftLineUnitPrice(row)" in quick_review
    assert "{{ row.unit || '—' }}" in quick_review
    assert "row.unit ? ` (${row.unit})` : ''" not in quick_review
    assert 'label="系统建议价"' not in quick_review
    assert 'label="最终确认价"' not in quick_review
    assert 'label="操作"' not in quick_review
    assert "saveDraftLinePrice(row)" not in quick_review
    assert "estimateDraftLine(row)" not in quick_review


def test_quick_review_feature_is_separate_and_column_spacing_is_balanced():
    source = _source("BudgetProjectPricing.vue")
    quick_review = source.split(
        'v-if="pricingWorkspaceView === \'quick\'"',
        1,
    )[1].split(
        'v-else-if="pricingWorkspaceView === \'professional\'"',
        1,
    )[0]
    project_column = quick_review.split(
        '<el-table-column label="施工项目"',
        1,
    )[1].split(
        "</el-table-column>",
        1,
    )[0]

    assert 'label="项目特征" min-width="260" show-overflow-tooltip' in quick_review
    assert "{{ row.spec || row.project_feature || '无项目特征' }}" in quick_review
    assert "row.spec || row.project_feature" not in project_column
    assert '.quick-review-table:deep(.el-table__cell){padding:10px 0}' in source
    assert '.quick-review-table:deep(.cell){padding-right:11px;padding-left:11px;line-height:1.45}' in source
    assert '.quick-review-table:deep(th.el-table__cell .cell){white-space:nowrap}' in source


def test_quick_review_explains_each_non_normal_risk():
    source = _source("BudgetProjectPricing.vue")
    quick_review = source.split(
        'v-if="pricingWorkspaceView === \'quick\'"',
        1,
    )[1].split(
        'v-else-if="pricingWorkspaceView === \'professional\'"',
        1,
    )[0]

    assert 'class="quick-risk-cell"' in quick_review
    assert 'v-if="quickReviewReason(row)"' in quick_review
    assert "{{ quickReviewReason(row) }}" in quick_review
    for reason in (
        "工程量缺失、为 0 或尚未解析",
        "尚未匹配到有效的不含税单价",
        "单价来自 AI 估算，需人工确认",
        "清单单位与匹配定额单位不一致",
        "单价或合价数值超出系统安全范围",
    ):
        assert reason in source
    assert "row?.ai_estimate?.estimate || row?.ai_estimate || {}" in source
    assert "briefRiskText(aiRisk)" in source


def test_project_quota_resource_workbench_only_shows_in_professional_view():
    source = _source("BudgetProjectPricing.vue")
    template = source.split("<script setup>", 1)[0]
    workbench = template.split(
        'class="budget-panel project-quota-resource-workbench"',
        1,
    )[0].rsplit(
        "<section",
        1,
    )[1]

    assert "pricingWorkspaceView === 'professional'" in workbench


def test_quick_review_cost_basis_opens_inline_source_drawer():
    pricing = _source("BudgetProjectPricing.vue")
    api = _source("budgetProjectApi.js")
    quota_workbench = _source("EnterpriseQuotaWorkbench.vue")
    quota_api = _source("enterpriseQuotaV2Api.js")

    for needle in (
        'class="source-basis-tag"',
        'title="点击查看成本依据"',
        "openCostBasis(row)",
        "costBasisDrawer.visible",
        "匹配到的企业定额项目",
        "匹配到的账户定额项目",
        "AI 估价依据",
        "在当前生效企业定额库中查看",
        "openEnterpriseQuotaLibraryItem",
        "activeEnterpriseItemId",
        "activeEnterpriseVersionId",
        "item?.active_version",
        "该报价引用的条目不属于当前生效的企业定额版本，已阻止跳转。",
        "enterprise_quota_item_id",
        "enterprise_quota_version",
        "enterprise_quota_version_id",
        "window.open('', '_blank')",
        "newPage.opener = null",
        "newPage.location.href = url.toString()",
        "selected_account_quota_item_id",
        "selected_enterprise_quota_item_id",
    ):
        assert needle in pricing

    assert 'label="成本依据"' not in pricing
    assert "window.open(`/admin/account-quotas" not in pricing
    assert "enterpriseQuotaItemDetail" in api
    assert "accountQuotaItemDetail" in api
    assert "routeRequiresActiveVersion" in quota_workbench
    assert "routeActiveVersionId" in quota_workbench
    assert "(routeRequiresActiveVersion ? routeActiveVersion : null)" in quota_workbench
    assert "applyActiveQuotaItemRoute" in quota_workbench
    assert "activeItem" in quota_api


def test_enterprise_quota_workbench_defaults_to_active_version_before_draft():
    source = _source("EnterpriseQuotaWorkbench.vue")
    load_versions = source.split(
        "async function loadVersions(preferredId = null) {",
        1,
    )[1].split(
        "async function loadRows()",
        1,
    )[0]
    preference_chain = load_versions.split("const preferred =", 1)[1]

    explicit_preferred = "versions.value.find((item) => item.id === preferredId)"
    retained_selection = "versions.value.find((item) => item.id === selectedVersionId.value)"
    active_version = "versions.value.find((item) => isActiveVersion(item))"
    draft_version = (
        "versions.value.find((item) => item.schema_version === "
        "'enterprise-quota-v2' && item.status === 'draft')"
    )
    assert preference_chain.index(explicit_preferred) < preference_chain.index(active_version)
    assert preference_chain.index(retained_selection) < preference_chain.index(active_version)
    assert preference_chain.index(active_version) < preference_chain.index(draft_version)


def test_enterprise_quota_workbench_has_linked_server_side_category_filters():
    source = _source("EnterpriseQuotaWorkbench.vue")

    for needle in (
        ">大类</span>",
        'placeholder="全部大类"',
        ">小类</span>",
        "'请先选择大类'",
        ':disabled="!selectedMajorSectionId"',
        "availableChapterOptions",
        "handleMajorSectionChange",
        "handleChapterChange",
        "clearClassificationFilters",
        "major_section_id:",
        "chapter_id:",
        "data.classification?.major_sections",
        "data.classification?.chapters",
    ):
        assert needle in source


def test_cost_db_page_removes_legacy_query_purchase_and_cost_items_workbenches():
    app = _source("App.vue")
    cost_db_template = app.split(
        '<template v-else-if="routeName === \'costDb\'">',
        1,
    )[1].split(
        '<template v-else-if="routeName === \'dwgTrial\'">',
        1,
    )[0]

    assert "<EnterpriseQuotaWorkbench" in cost_db_template
    for legacy_ui in (
        "历史查询与采购入库",
        "历史 cost_items 维护区",
        "旧 cost_items 维护工作台",
        'v-model="costMasterActiveTab"',
        'name="purchaseImports"',
    ):
        assert legacy_ui not in cost_db_template

    cost_db_loader = app.rsplit(
        "if (routeName.value === 'costDb') {",
        1,
    )[1].split(
        "if (routeName.value === 'requirementStandardization') {",
        1,
    )[0]
    assert "await refreshCostMaster()" not in cost_db_loader
    assert "await loadCostItems()" not in cost_db_loader
    assert "openCostItemDetail" in cost_db_loader
