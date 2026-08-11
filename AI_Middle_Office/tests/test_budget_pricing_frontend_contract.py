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
    assert "projectEnterpriseQuotaItems: (projectId, params)" in api
    assert "/pricing-draft/enterprise-quota-items" in api
    assert "materializeProjectQuota: (projectId, lineId, payload)" in api
    assert "addProjectQuota: (projectId, lineId, payload)" in api
    assert "replaceProjectQuota: (projectId, lineId, payload)" in api
    assert "deleteProjectQuota: (projectId, lineId, payload)" in api
    assert "createProjectQuotaResource: (projectId, lineId, payload)" in api
    assert "updateProjectQuotaResource: (projectId, lineId, resourceId, payload)" in api
    assert "deleteProjectQuotaResource: (projectId, lineId, resourceId, payload)" in api
    assert "syncProjectQuotaToEnterprise: (projectId, lineId, payload)" in api
    assert "export async function budgetBlobErrorMessage" in api
    assert "const text = await data.text()" in api
    assert "BUDGET_PRICING_EXPORT_SOURCE_FILE_NOT_RETAINED" in api
    assert "await budgetBlobErrorMessage(error, '原格式报价 Excel 导出失败')" in _source("BudgetProjectPricing.vue")


def test_project_quota_row_and_inline_resource_workbench_are_visible():
    source = _source("BudgetProjectPricing.vue")
    template = source.split("<script setup>", 1)[0]
    projects = _source("BudgetProjects.vue")

    for label in (
        "<strong>明细项</strong>",
        "新增明细项",
        "保存并重算项目定额",
        "保存后同步到企业定额库",
    ):
        assert label in template
    assert "明细项（第三级）" not in template
    assert "点击字段旁的编辑图标可单独修改" not in template
    for detail_label in (
        "费用类别",
        "编码",
        "名称",
        "规格",
        "品牌",
        "单位",
        "含量",
        "单价",
        "金额",
    ):
        assert f"label: '{detail_label}'" in source
    detail_fields = source.split("const projectQuotaDetailFields =", 1)[1].split("]", 1)[0]
    assert "label: '类型'" not in detail_fields
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
    assert "projectQuotaMainItems(row)" in source
    assert 'class="project-quota-relation"' in source
    assert '<Teleport' in template
    assert ':to="projectQuotaInlineHost"' in template
    assert ':ref="captureProjectQuotaInlineHost"' in template
    assert 'class="project-quota-inline-workbench"' in template
    assert 'id="project-quota-inline-host"' in template
    assert template.count('id="project-quota-inline-host"') == 1
    matched_table = template.split(':data="projectQuotaMainItems(row)"', 1)[1].split("</el-table>", 1)[0]
    assert 'row-key="entry_key"' in matched_table
    assert ':expand-row-keys="projectQuotaExpandedEntryKeys(row)"' in matched_table
    assert 'type="expand" width="28" class-name="project-quota-detail-expand-cell"' in matched_table
    assert 'v-if="isProjectQuotaRowActive(row) && isProjectQuotaItemActive(quotaRow)"' in matched_table
    assert ':data-quota-entry-key="quotaRow.entry_key"' in matched_table
    assert matched_table.index('id="project-quota-inline-host"') < matched_table.index('v-for="column in projectQuotaMainColumns"')
    assert "function refreshProjectQuotaInlineHost()" in source
    assert "document.getElementById('project-quota-inline-host')" in source
    assert source.count("refreshProjectQuotaInlineHost()") >= 3
    assert '@row-click="(quotaRow) => selectProjectQuota(row, quotaRow)"' in template
    assert "beginProjectQuotaFieldEdit(resource, field)" in template
    assert "saveProjectQuotaInlineField" in template
    assert ':aria-label="`编辑${field.label}`"' in template
    assert 'aria-label="保存字段修改"' in template
    assert 'aria-label="取消字段修改"' in template
    assert "@dblclick" not in template.split('class="project-quota-inline-workbench"', 1)[1].split("</Teleport>", 1)[0]
    assert 'class="project-quota-detail-row project-quota-detail-row--item"' in template
    workbench = template.split('class="project-quota-inline-workbench"', 1)[1].split("</Teleport>", 1)[0]
    assert ">操作<" in workbench
    assert ">添加明细<" in workbench
    assert ">删除明细<" in workbench
    assert '@command="(command) => handleProjectQuotaResourceAction(command, resource)"' in workbench
    assert "企业定额同步状态" not in template
    assert "<el-table" not in workbench
    assert "projectQuotaEditor.visible" in template
    assert "当前编辑的是项目定额工作副本" not in template
    assert "当前清单项目：" not in template
    assert "增删改只影响当前项目和当前报价草稿" not in template
    assert "<BudgetProjectPricing" in projects


def test_project_quota_resource_fields_use_independent_inline_editors():
    source = _source("BudgetProjectPricing.vue")
    detail_cell = source.split(
        'class="project-quota-detail-cell"',
        1,
    )[1].split(
        'class="project-quota-inline-editor"',
        1,
    )[0]
    edit_handler = source.split(
        "async function saveProjectQuotaInlineField() {",
        1,
    )[1].split(
        "const setProjectQuotaSnapshot",
        1,
    )[0]

    assert '@click.stop="beginProjectQuotaFieldEdit(resource, field)"' in source
    assert detail_cell.index('class="project-quota-field-edit"') < detail_cell.index('class="project-quota-detail-value"')
    assert '@dblclick="projectQuotaCanEdit && startEditProjectQuotaResource(resource)"' not in source
    assert '@row-click="startEditProjectQuotaResource"' not in source
    assert "[field.key]: value" in edit_handler
    assert "expected_snapshot_revision" in edit_handler
    assert "expected_resource_revision" in edit_handler
    assert "await loadDraft(true)" in edit_handler
    assert "清单价格和统计信息已同步重算" in edit_handler
    assert "grid-template-columns:54px 68px 86px 62px 58px 34px" in source
    assert "gap:2px" in source
    assert ".project-quota-detail-cell:nth-child(2) .project-quota-detail-value" in source
    assert ".project-quota-detail-cell:nth-child(6) .project-quota-detail-value" in source
    assert "text-overflow:clip" in source
    assert "justify-content:flex-start;gap:1px" in source
    assert ".project-quota-detail-row .is-number{justify-content:flex-end" in source


def test_project_quota_actions_and_pinned_enterprise_picker_are_wired():
    pricing = _source("BudgetProjectPricing.vue")
    picker = _source("EnterpriseQuotaMiniPanel.vue")
    quota_workbench = _source("EnterpriseQuotaWorkbench.vue")

    for label in (">新增</el-button>", ">删除</el-button>", ">替换</el-button>"):
        assert label in pricing
    for handler in (
        "openProjectQuotaAddition(row)",
        "deleteCurrentProjectQuota(row, quotaRow)",
        "openProjectQuotaReplacement(row, quotaRow)",
        "applyProjectQuotaFromLibrary",
    ):
        assert handler in pricing
    assert 'defer\n    v-if="pricingAvailable' in pricing
    assert 'class="project-quota-name-cell"' in pricing
    assert 'aria-label="定额项操作"' in pricing
    assert ':disabled="!canManageDraft" @click.stop="deleteCurrentProjectQuota(row, quotaRow)"' in pricing
    assert "if (!isProjectQuotaRowActive(row) || !projectQuotaWorkbench.snapshot)" in pricing
    assert "await selectProjectQuota(row)" in pricing
    assert 'v-if="hasProjectQuotaItem(row)"' in pricing
    assert "暂无定额，当前清单价格已按剩余价格来源重新计算。" in pricing
    assert "await loadDraft(true)\n    await nextTick()\n    ElMessage.success('定额已删除，清单价格、来源统计和费用汇总已同步更新')" in pricing
    assert 'label="操作" width="205" fixed="right"' not in pricing
    assert '.project-quota-inline-workbench{position:sticky;left:0;box-sizing:border-box;width:min(1360px,calc(100vw - 72px))' in pricing
    assert 'position:fixed' in picker
    assert 'z-index:2100' in picker
    assert "emit('select', props.multiple ? selectedRows.value : selectedItem.value)" in picker
    assert "{{ confirmLabel }}" in picker
    assert "已选 {{ selectedRows.length }} 条" in picker
    assert ":selectable=\"['add', 'replace'].includes(enterpriseQuotaPanel.mode)\"" in pricing
    assert ':multiple="enterpriseQuotaPanel.mode === \'add\'"' in pricing
    assert "enterpriseQuotaPanel.mode === 'add' ? '勾选新增' : '勾选替换'" in pricing
    assert "budgetProjectApi.projectEnterpriseQuotaItems" in picker
    assert ':project-id="projectId"' in pricing
    assert 'overflow-x: hidden' in quota_workbench
    assert 'width: 100%' in quota_workbench
    assert "['11%', '7%', '17%', '14%', '14%', '7%', '9%', '18%']" in quota_workbench
    assert 'width: 3%' in quota_workbench
    assert 'restoreProjectQuotaInlineRow' in pricing
    assert "const projectQuotaExpandedEntryKeys = (row)" in pricing
    assert ':expand-row-keys="professionalExpandedRowKeys"' in pricing
    assert '@expand-change="syncProfessionalExpandedRows"' in pricing
    assert 'professionalExpandedRowKeys.value = [...professionalExpandedRowKeys.value, refreshedRowId]' in pricing


def test_project_quota_resource_save_refreshes_prices_and_statistics_before_success():
    pricing = _source("BudgetProjectPricing.vue")
    save_handler = pricing.split(
        "async function saveProjectQuotaResource() {",
        1,
    )[1].split(
        "async function deleteProjectQuotaResourceRow() {",
        1,
    )[0]

    refresh_index = save_handler.index("await loadDraft(true)")
    render_index = save_handler.index("await nextTick()", refresh_index)
    success_index = save_handler.index("ElMessage.success(", render_index)

    assert refresh_index < render_index < success_index
    assert "工料机已新增，清单价格、来源统计和费用汇总已同步更新" in save_handler
    assert "工料机已更新，清单价格、来源统计和费用汇总已同步更新" in save_handler


def test_project_quota_add_opens_multi_select_enterprise_picker_and_submits_items():
    pricing = _source("BudgetProjectPricing.vue")
    template = pricing.split("<script setup>", 1)[0]
    add_handler = pricing.split(
        "async function openProjectQuotaAddition(row) {",
        1,
    )[1].split(
        "async function openProjectQuotaReplacement(row, quotaRow = null) {",
        1,
    )[0]
    apply_handler = pricing.split(
        "async function applyProjectQuotaFromLibrary(selection) {",
        1,
    )[1].split(
        "async function deleteCurrentProjectQuota(row, quotaRow = null) {",
        1,
    )[0]

    picker_index = add_handler.index("openEnterpriseQuotaPanel({")
    assert "mode: 'add'" in add_handler
    assert picker_index >= 0
    assert "startCreateProjectQuotaResource()" not in add_handler
    assert "disabledItemIds: existingIds" in add_handler
    assert "const adding = enterpriseQuotaPanel.mode === 'add'" in apply_handler
    assert "budgetProjectApi.addProjectQuota" in apply_handler
    assert "pricing_mode: selectedDraftMode.value" in apply_handler
    assert "enterprise_quota_item_ids: items.map" in apply_handler
    assert "expected_snapshot_revision" in apply_handler
    assert "row.has_project_quota = true" in apply_handler
    assert "await loadDraft(true)" in apply_handler
    assert "定额已新增，清单价格、统计信息和费用汇总已同步更新" in apply_handler
    assert '@select="applyProjectQuotaFromLibrary"' in template


def test_draft_reload_replaces_stale_breakdown_preview_after_project_quota_change():
    pricing = _source("BudgetProjectPricing.vue")
    refresh_handler = pricing.split(
        "const refreshDraftBreakdownInputs = (row) => {",
        1,
    )[1].split(
        "const draftBreakdownInputValue = (row, key) => {",
        1,
    )[0]
    line_loader = pricing.split(
        "async function loadDraftLines() {",
        1,
    )[1].split(
        "async function saveDraftLinePrice(row, clear = false) {",
        1,
    )[0]

    assert "draftBreakdownEditing[`${lineId}:${key}`] !== true" in refresh_handler
    assert "draftBreakdownInputs[lineId][key] = draftBreakdownInitialValue(row, key)" in refresh_handler
    assert "refreshDraftBreakdownInputs(row)" in line_loader


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
        "报价来源",
    )
    positions = [professional.index(f'label="{label}"') for label in labels]
    assert positions == sorted(positions)
    assert 'ref="professionalTableRef"' in professional
    assert '@row-click="toggleProfessionalQuotaRow"' in professional
    assert professional.index('type="expand"') < professional.index('label="序号"')
    assert 'type="expand" width="28" class-name="project-quota-expand-cell"' in professional
    assert 'fixed="left"' not in professional
    assert 'label="税费" min-width="31" align="right" show-overflow-tooltip' in professional
    assert 'label="报价来源" min-width="102" class-name="professional-source-column"' in professional
    assert 'label="名称" min-width="64" show-overflow-tooltip' in professional
    assert 'label="项目特征" min-width="72" show-overflow-tooltip' in professional
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
        "draftPriceSourceLabel(row)",
        "draftPriceSourceMeta(row)",
        '@click.stop="openCostBasis(row)"',
        '@keyup.enter="openCostBasis(row)"',
        "selectProjectQuota(row, quotaRow)",
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
    assert "key: '__expand_spacer'" not in quota_columns
    assert ':show-header="false"' in source
    assert ':label="column.label"' not in source
    assert "projectQuotaMainFieldValue(quotaRow, column)" in source
    assert "project_feature:" in source
    assert "measure_fee:" in source
    assert "management_fee:" in source
    assert "tax_fee:" in source
    assert 'type="expand" width="28" class-name="project-quota-detail-expand-cell"' in source
    assert "{ key: 'quota_code', label: '定额编码', minWidth: 30" in quota_columns
    assert "{ key: 'tax_fee', label: '税费', minWidth: 31" in quota_columns
    assert "{ key: '__source_spacer', minWidth: 102" in quota_columns
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
    workbench = template.split('to="#project-quota-inline-host"', 1)[0].rsplit("<Teleport", 1)[1]

    assert "pricingWorkspaceView === 'professional'" in workbench


def test_clicking_active_project_quota_row_collapses_its_details():
    source = _source("BudgetProjectPricing.vue")
    handler = source.split("async function selectProjectQuota(row, quotaRow = null) {", 1)[1].split(
        "async function openProjectQuotaAddition(row)",
        1,
    )[0]
    collapse = source.split("function collapseProjectQuotaDetails() {", 1)[1].split(
        "async function selectProjectQuota",
        1,
    )[0]

    assert '@row-click="(quotaRow) => selectProjectQuota(row, quotaRow)"' in source
    assert "quotaRow" in handler
    assert "projectQuotaWorkbench.snapshot" in handler
    assert "isProjectQuotaRowActive(row)" in handler
    assert "isProjectQuotaItemActive(quotaRow)" in handler
    assert "collapseProjectQuotaDetails()" in handler
    assert "return" in handler
    for reset in (
        "projectQuotaInlineReady.value = false",
        "projectQuotaInlineHost.value = null",
        "projectQuotaWorkbench.row = null",
        "projectQuotaWorkbench.snapshot = null",
        "projectQuotaWorkbench.entryKey = null",
        "projectQuotaEditor.visible = false",
    ):
        assert reset in collapse


def test_quick_review_cost_basis_opens_inline_source_drawer():
    pricing = _source("BudgetProjectPricing.vue")
    api = _source("budgetProjectApi.js")
    quota_workbench = _source("EnterpriseQuotaWorkbench.vue")
    quota_api = _source("enterpriseQuotaV2Api.js")
    mini_panel = _source("EnterpriseQuotaMiniPanel.vue")

    for needle in (
        'class="source-basis-tag"',
        'title="点击查看成本依据"',
        "openCostBasis(row)",
        "costBasisDrawer.visible",
        "匹配到的企业定额项目",
        "匹配到的账户定额项目",
        "AI 估价依据",
        "查看企业定额库",
        "openEnterpriseQuotaLibraryItem",
        "activeEnterpriseItemId",
        "activeEnterpriseVersionId",
        "item?.active_version",
        "该报价引用的条目不属于当前生效的企业定额版本，已阻止跳转。",
        "EnterpriseQuotaMiniPanel",
        "openEnterpriseQuotaPanel",
        "enterpriseQuotaPanel.visible",
        "selected_account_quota_item_id",
        "selected_enterprise_quota_item_id",
    ):
        assert needle in pricing

    assert "在当前生效企业定额库中查看" not in pricing
    assert 'label="成本依据"' not in pricing
    assert "window.open(`/admin/account-quotas" not in pricing
    assert "window.open('', '_blank')" not in pricing
    library_url = pricing.split("const enterpriseQuotaLibraryUrl = computed(() => {", 1)[1].split(
        "const quickReviewLabel",
        1,
    )[0]
    assert "enterprise_quota_version: 'active'" in library_url
    assert "enterprise_quota_version_id: String(costBasisDrawer.activeEnterpriseVersionId)" in library_url
    assert "enterprise_quota_item_id: String(costBasisDrawer.activeEnterpriseItemId)" in library_url
    assert "window.open('about:blank', '_blank')" in library_url
    assert "openedTab.location.replace(new URL(enterpriseQuotaLibraryUrl.value, window.location.origin).href)" in library_url
    assert "浏览器阻止了新标签页，请允许弹出窗口后重试" in library_url
    assert "openedTab.opener = null" not in library_url
    assert "openEnterpriseQuotaPanel" not in library_url
    assert 'class="enterprise-quota-mini"' in mini_panel
    assert 'v-show="modelValue"' in mini_panel
    assert '@click.stop="close"' in mini_panel
    assert "悬浮窗已钉在报价页面" not in mini_panel
    assert ">已钉住<" not in mini_panel
    assert "当前生效：{{ activeVersionLabel }}" in mini_panel
    assert 'response?.data?.active_version || data?.active_version || null' in mini_panel
    assert "勾选替换" in mini_panel
    assert "quotaV2Items" in mini_panel
    assert "items.value = quotaV2Items(response)" in mini_panel
    assert "masterItems" in quota_api
    assert "enterpriseQuotaItemDetail" in api
    assert "accountQuotaItemDetail" in api
    assert "routeRequiresActiveVersion" in quota_workbench
    assert "routeActiveVersionId" in quota_workbench
    assert "(routeRequiresActiveVersion ? routeActiveVersion : null)" in quota_workbench
    assert "applyActiveQuotaItemRoute" in quota_workbench
    assert "'excel-row-route-target'" in quota_workbench
    assert "Number(row.entity_id) === routeQuotaItemId" in quota_workbench
    assert "document.querySelector('.excel-row-route-target')?.scrollIntoView({ block: 'center' })" in quota_workbench
    assert "if (routeRequiresActiveVersion && routeQuotaItemId && window.opener) window.opener = null" in quota_workbench
    assert "activeItem" in quota_api
    assert ':expected-version-id="readiness?.active_quota_version?.id"' in pricing


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


def test_attention_kpi_locates_the_pending_line_across_pages():
    source = _source("BudgetProjectPricing.vue")

    for needle in (
        'class="warning attention-kpi"',
        '@click="focusNextAttentionLine"',
        ':row-class-name="professionalDraftRowClass"',
        "const draftLineNeedsAttention = (line) =>",
        "effectiveUnitPrice === null",
        "line?.quantity_status !== 'valid'",
        "calculationQuantity <= 0",
        "async function allDraftLinesForAttention()",
        "page_size: pageSize",
        "draftLinePage.value = Math.floor(target.index / draftLinePageSize) + 1",
        "pricingWorkspaceView.value = 'professional'",
        "targetElement.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })",
        "再次点击定位下一条",
        "is-attention-focus",
    ):
        assert needle in source
