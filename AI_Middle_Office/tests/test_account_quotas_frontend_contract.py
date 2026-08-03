from pathlib import Path


FRONTEND_SRC = Path(__file__).resolve().parents[2] / "ai-web" / "src"


def _source(name: str) -> str:
    return (FRONTEND_SRC / name).read_text(encoding="utf-8")


def test_account_quota_route_navigation_and_feature_gate_are_wired():
    app = _source("App.vue")

    assert "import AccountQuotaLibrary from './AccountQuotaLibrary.vue'" in app
    assert "module.key === 'account_quotas'" in app
    assert "module.path === '/admin/account-quotas'" in app
    assert "accountQuotasModule.value?.status === 'available'" in app
    assert "accountQuotasFeatureAvailable.value && canAccessPermissions.value" in app
    assert 'v-if="canViewAccountQuotas"' in app
    assert "navigate('/admin/account-quotas')" in app
    assert "if (pathname === '/admin/account-quotas') return 'accountQuotas'" in app
    assert 'routeName === \'accountQuotas\'' in app
    assert '<AccountQuotaLibrary :feature-available="accountQuotasFeatureAvailable" />' in app


def test_account_quota_component_is_fail_closed_and_preserves_master_boundary():
    component = _source("AccountQuotaLibrary.vue")

    assert "featureAvailable: { type: Boolean, default: false }" in component
    assert "!props.featureAvailable || featureDisabled.value" in component
    assert "账户定额库尚未开启" in component
    assert "不会读取或修改企业定额主库" in component
    assert "企业定额 active 主库始终保持独立" in component
    assert "草稿经人工启用后才会参与账户定额模式匹配" in component


def test_account_quota_api_uses_current_rest_contract():
    api = _source("accountQuotaApi.js")

    assert "get('/admin/account-quotas', { params })" in api
    assert "post('/admin/account-quotas', payload)" in api
    assert "get(`/admin/account-quotas/${identifier}`)" in api
    assert "patch(`/admin/account-quotas/${identifier}`, payload)" in api
    assert "post(`/admin/account-quotas/${identifier}/status`, payload)" in api
    assert "batchStatus" in api
    assert "post('/admin/account-quotas/status/batch', payload)" in api
    assert "get(`/admin/account-quotas/${identifier}/history`, { params })" in api


def test_account_quota_fields_decimal_revision_and_status_payload_are_wired():
    component = _source("AccountQuotaLibrary.vue")

    for field in (
        "quota_code",
        "item_name",
        "item_features",
        "unit",
        "unit_price",
        "expected_revision",
    ):
        assert field in component

    assert "(?:\\.\\d{1,6})?" in component
    assert "Number(normalized) <= 0" in component
    assert "expected_revision: dialog.form.expected_revision" in component
    assert "target_status: targetStatus" in component
    assert "expected_revision: rowRevision(row)" in component
    assert "changeStatus(row, 'active')" in component
    assert "changeStatus(row, 'draft')" in component
    assert "changeStatus(row, 'archived')" in component
    assert "_bulk_selected" in component
    assert "selectedDraftQuotaRows" in component
    assert "selectedArchivableQuotaRows" in component
    assert "batchChangeStatus('active')" in component
    assert "batchChangeStatus('archived')" in component
    assert "批量启用草稿" in component
    assert "批量归档" in component
    assert "row.status !== 'archived'" in component
    assert "同步选中到基础定额" in component

    payload_block = component.split("function formPayload()", 1)[1].split(
        "async function handleEditConflict", 1
    )[0]
    assert "account_id" not in payload_block
    assert "notes: buildNotesPayload(dialog.form)" in payload_block

    create_block = component.split("await accountQuotaApi.create({", 1)[1].split("})", 1)[0]
    assert "source: 'manual'" in create_block
    assert "reason: dialog.form.reason.trim()" in create_block


def test_account_quota_detail_tabs_and_extension_fields_are_wired():
    component = _source("AccountQuotaLibrary.vue")

    for label in ("工序明细", "材料明细", "专业分包明细"):
        assert label in component
    for field in (
        "detail_type",
        "material_type",
        "loss_rate",
        "adjustment_factor",
        "real_content",
        "labor_fee",
        "main_material_fee",
        "auxiliary_material_fee",
        "subcontract_breakdown_source",
        "spec_model",
        "thickness_mm",
        "width_mm",
        "brand",
    ):
        assert field in component

    assert "detail_type: activeDetailType.value" in component
    assert "detail_type: form.detail_type || 'process'" in component
    assert "account_quota_detail_v1" in component
    assert "新增{{ activeTabConfig.shortLabel }}" in component
    assert "params = { page: page.value, page_size: pageSize, detail_type: activeDetailType.value }" in component
    assert "subcontractBreakdownSourceLabel" in component
    assert "function canEditRow(row)" in component
    assert ".editable-cell" in component
    assert ':disabled="!canEditRow(row)"' in component
    assert 'v-for="column in resourceColumns"' in component


def test_account_quota_conflicts_and_history_are_visible_without_overwrite():
    component = _source("AccountQuotaLibrary.vue")

    assert "error?.response?.status !== 409" in component
    assert "本次保存已被阻止" in component
    assert "dialog.conflict = true" in component
    assert "accountQuotaApi.history" in component
    assert "账户定额修订历史" in component
    assert "status_change" in component
    assert "created" in component
    assert "updated" in component
