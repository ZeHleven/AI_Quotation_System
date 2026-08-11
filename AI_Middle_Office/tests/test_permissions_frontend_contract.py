from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(name: str) -> str:
    return (ROOT / "ai-web" / "src" / name).read_text(encoding="utf-8")


def test_permissions_dialog_exposes_add_and_remove_function_choices():
    source = _source("App.vue")

    for text in (
        "账号功能权限",
        "管理功能权限",
        "勾选表示保留或新增；取消勾选表示移除",
        "将新增的功能",
        "将移除的功能",
        "保存功能授权",
        "实际可用功能",
    ):
        assert text in source

    assert 'v-model="grantDialog.selectedRoles"' in source
    assert "grantAddedFunctions" in source
    assert "grantRemovedFunctions" in source
    assert "grantRoleStatusLabel" in source
    assert "functionLabelsForRoles" in source
    assert "roleImplications" in source


def test_permissions_dialog_saves_the_complete_role_set_in_one_request():
    source = _source("App.vue")
    handler = source.split("async function saveFunctionalPermissions() {", 1)[1].split(
        "async function openEvents(user)",
        1,
    )[0]

    assert "grantHasRoleChanges.value" in handler
    assert "grantDialog.note.trim()" in handler
    assert "grantSelectedRoleSet.value.has(role.value)" in handler
    assert "await api.put(`/admin/users/${grantDialog.user.id}/roles`" in handler
    assert "roles," in handler
    assert "功能授权已更新" in handler


def test_permissions_dialog_has_compact_status_styling():
    style = _source("style.css")

    for selector in (
        ".permission-role-option.is-added",
        ".permission-role-option.is-removed",
        ".permission-change-card.is-add",
        ".permission-change-card.is-remove",
        ".permission-role-grid",
    ):
        assert selector in style
