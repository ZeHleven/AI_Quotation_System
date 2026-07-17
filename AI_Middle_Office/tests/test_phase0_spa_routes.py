import pytest

from app import main as app_main
from app.core.config import settings


@pytest.fixture(autouse=True)
def frontend_files(tmp_path, monkeypatch):
    frontend_dir = tmp_path / "frontend"
    vite_dist_dir = frontend_dir / "ai-web" / "dist"
    vite_dist_dir.mkdir(parents=True)

    (frontend_dir / "app.html").write_text("<html><body>legacy app</body></html>", encoding="utf-8")
    (frontend_dir / "index.html").write_text("<html><body>legacy index</body></html>", encoding="utf-8")
    (frontend_dir / "admin.html").write_text("<html><body>legacy admin</body></html>", encoding="utf-8")
    (vite_dist_dir / "index.html").write_text('<html><body><div id="app"></div></body></html>', encoding="utf-8")

    previous_flag = settings.feature_vite_frontend
    monkeypatch.setattr(app_main, "_FRONTEND_DIR", str(frontend_dir))
    monkeypatch.setattr(app_main, "_VITE_DIST_DIR", str(vite_dist_dir))
    object.__setattr__(settings, "feature_vite_frontend", True)
    yield
    object.__setattr__(settings, "feature_vite_frontend", previous_flag)


def test_phase0_login_and_permissions_routes_are_served(client):
    assert client.get("/login").status_code == 200
    assert client.get("/no-access").status_code == 200
    assert client.get("/admin/permissions").status_code == 200


def test_root_and_legacy_portal_redirect_to_unified_login(client):
    for path in ("/", "/app.html"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in {302, 307}
        assert response.headers["location"] == "/login"


def test_legacy_business_html_routes_are_preserved(client):
    assert client.get("/index.html").status_code == 200
    assert client.get("/admin.html").status_code == 200


def test_legacy_portal_remains_available_when_vite_is_disabled(client):
    object.__setattr__(settings, "feature_vite_frontend", False)

    response = client.get("/app.html")

    assert response.status_code == 200
    assert "legacy app" in response.text


def test_phase1_dashboard_route_is_served_by_spa(client):
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert 'id="app"' in response.text


def test_quote_new_falls_back_to_legacy_workspace_when_vite_is_disabled(client):
    object.__setattr__(settings, "feature_vite_frontend", False)

    response = client.get("/quote/new", follow_redirects=False)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/index.html"
