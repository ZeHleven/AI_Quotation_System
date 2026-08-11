from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.rbac import get_available_modules, get_default_home_path, serialize_user_for_rbac


HOME_FEATURE_FLAGS = (
    "feature_unified_quotes",
    "feature_dashboard_quote",
    "feature_project_progress",
    "feature_cost_db",
    "feature_enterprise_profile",
    "feature_budget_projects",
    "feature_budget_pricing",
    "feature_account_quotas",
)


@pytest.fixture(autouse=True)
def enable_role_home_features():
    previous = {name: getattr(settings, name) for name in HOME_FEATURE_FLAGS}
    for name in HOME_FEATURE_FLAGS:
        object.__setattr__(settings, name, True)
    yield
    for name, value in previous.items():
        object.__setattr__(settings, name, value)


def user_with_roles(*roles: str):
    return SimpleNamespace(
        id=1,
        username="role_home_user",
        role="none",
        role_version=1,
        quota=5,
        is_active=True,
        must_change_password=False,
        dingtalk_user_id=None,
        role_assignments=[SimpleNamespace(role=role) for role in roles],
    )


@pytest.mark.parametrize(
    ("roles", "expected_path"),
    [
        (("system_admin",), "/admin/dashboard"),
        (("admin",), "/admin/dashboard"),
        (("quote_operator",), "/admin/dashboard"),
        (("viewer",), "/admin/dashboard"),
        (("manager",), "/admin/projects"),
        (("project_manager",), "/admin/projects"),
        (("project_member",), "/admin/project-tasks/my"),
        (("project_viewer",), "/admin/projects"),
        (("cost_editor",), "/admin/cost-db"),
        (("enterprise_profile_editor",), "/admin/enterprise-profile"),
        (("staff",), "/quote/new"),
        (("quote_user",), "/quote/new"),
        (("staff", "cost_viewer"), "/admin/cost-db"),
        (("staff", "project_member"), "/admin/project-tasks/my"),
        (("quote_operator", "cost_viewer"), "/admin/dashboard"),
    ],
)
def test_role_default_home_matrix(roles, expected_path):
    assert get_default_home_path(user_with_roles(*roles)) == expected_path


def test_pending_specialty_module_does_not_become_default_home():
    object.__setattr__(settings, "feature_cost_db", False)
    object.__setattr__(settings, "feature_budget_projects", False)
    object.__setattr__(settings, "feature_budget_pricing", False)

    assert get_default_home_path(user_with_roles("cost_viewer")) == "/no-access"


def test_module_metadata_separates_trial_stage_from_runtime_status():
    modules = {item["key"]: item for item in get_available_modules(user_with_roles("quote_user"))}

    assert modules["dwg_trial"] == {
        "key": "dwg_trial",
        "name": "图纸识图",
        "path": "/admin/dwg-trial",
        "status": "available",
        "stage": "trial",
    }
    assert modules["bidding"]["stage"] == "trial"
    assert modules["agent_center"]["stage"] == "trial"
    assert modules["unified_quotes"] == {
        "key": "unified_quotes",
        "name": "报价工作台",
        "path": "/quote/new",
        "status": "available",
        "stage": "trial",
    }

    admin_modules = {item["key"]: item for item in get_available_modules(user_with_roles("admin"))}
    assert admin_modules["account_quotas"] == {
        "key": "account_quotas",
        "name": "账户定额库",
        "path": "/admin/account-quotas",
        "status": "available",
        "stage": "trial",
    }


def test_serialized_user_exposes_default_home_path():
    payload = serialize_user_for_rbac(user_with_roles("project_member"))

    assert payload["default_home_path"] == "/admin/project-tasks/my"
    assert payload["roles"] == ["project_member"]


def test_staff_only_role_exposes_exactly_five_business_modules():
    modules = {item["key"]: item for item in get_available_modules(user_with_roles("staff"))}

    assert set(modules) == {"unified_quotes", "legacy_quote", "bidding", "cost_db", "account_quotas"}
    assert modules["unified_quotes"]["status"] == "available"
    assert modules["legacy_quote"]["status"] == "available"
    assert modules["bidding"]["status"] in {"available", "pending"}
    assert modules["cost_db"]["status"] in {"available", "pending"}
    assert modules["account_quotas"]["status"] in {"available", "pending"}
