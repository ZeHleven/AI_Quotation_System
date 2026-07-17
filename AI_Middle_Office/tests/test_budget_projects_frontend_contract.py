from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_budget_project_component_uses_current_backend_contract():
    source = _source("BudgetProjects.vue")

    assert "selectedImport.remap_revision" in source
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


def test_price_columns_and_layer_quantities_are_locked_and_quantity_is_unique():
    source = _source("BudgetProjects.vue")

    assert "价格/金额列已锁定忽略" in source
    assert "分层工程量列已锁定忽略" in source
    assert "每个 Sheet 只允许一个合计工程量列" in source
    assert "mappingChanged" in source
    assert "filters = reactive({ keyword: '', status: 'active' })" in source
