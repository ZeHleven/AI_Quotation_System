from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_budget_project_component_uses_current_backend_contract():
    source = _source("BudgetProjects.vue")

    assert "selectedImport.value?.remap_revision" in source
    assert "row?.capabilities" in source
    for capability in ("can_edit", "can_archive", "can_upload", "can_remap", "can_activate_import"):
        assert capability in source


def test_archived_and_read_only_projects_cannot_mutate_mappings():
    source = _source("BudgetProjects.vue")

    assert "projectStatus(row) !== 'archived'" in source
    assert "!detailArchived.value" in source
    assert "mappingColumnDisabled" in source
    assert "!canRemapCurrent.value || isLockedIgnoreColumn(column)" in source


def test_budget_module_entry_is_hidden_when_feature_is_not_available():
    source = _source("App.vue")

    assert "budgetProjectsFeatureAvailable" in source
    assert "budgetProjectsModule.value?.status === 'available'" in source
    assert "budgetProjectsFeatureAvailable.value && canViewBudgetProjectsByRole.value" in source
    assert ':feature-available="budgetProjectsFeatureAvailable"' in source


def test_project_detail_hides_summary_import_and_standardization_panels():
    source = _source("BudgetProjects.vue")
    detail_template = source.split("<div v-else>", 1)[1].split("<el-dialog", 1)[0]

    for heading in ("导入甲方清单", "导入批次", "Sheet 表头映射", "标准清单"):
        assert f"<strong>{heading}</strong>" not in detail_template
    for hidden_summary in (
        "当前为成本计价准备模式",
        "企业定额部分计价已启用",
        'class="budget-metrics"',
        "<span>项目状态</span>",
        "<span>当前 Sheet</span>",
    ):
        assert hidden_summary not in detail_template


def test_import_confirmation_and_activation_contract_is_visible_and_wired():
    component = _source("BudgetProjects.vue")
    api = _source("budgetProjectApi.js")

    assert "canConfirmImport" in component
    assert "canActivateImport" in component
    assert "确认后该批次将冻结映射" in component
    assert "设为当前批次" in component
    assert "/confirm`" in api
    assert "/activate`" in api
    assert "activeImport:" in api
    assert "/active-import`" in api
    assert "listImportRevisions:" in api
    assert "/revisions`" in api


def test_remap_uses_optimistic_revision_and_nested_batch_capabilities():
    source = _source("BudgetProjects.vue")

    assert "expected_remap_revision: Number(selectedImport.value?.remap_revision ?? 0)" in source
    assert "batchCapability(selectedImport.value, 'can_remap')" in source
    assert "batchCapability(row, 'can_confirm')" in source
    assert "batchCapability(row, 'can_activate')" in source
    assert "budgetProjectApi.activeImport(activeProjectId)" in source


def test_hidden_mapping_workflow_keeps_price_layer_and_unique_quantity_guards():
    source = _source("BudgetProjects.vue")

    assert "isLockedPriceColumn" in source
    assert "isLockedLayerQuantityColumn" in source
    assert "isLockedIgnoreColumn" in source
    assert "mappingChanged" in source
    assert "filters = reactive({ keyword: '', status: 'active' })" in source


def test_import_workbook_limit_error_formats_sheet_objects():
    api = _source("budgetProjectApi.js")

    assert "function budgetSheetLimitText(sheet)" in api
    assert "sheet.sheet_name || sheet.name || sheet.source_sheet" in api
    assert "sheet.row_count != null" in api
    assert "sheet.column_count != null" in api
    assert "detail.sheets.map(budgetSheetLimitText)" in api
    assert "BUDGET_IMPORT_WORKBOOK_LIMIT_EXCEEDED" in api
    assert "单个 Sheet 上限" in api
