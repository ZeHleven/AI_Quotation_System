param(
    [string]$BaseUrl = "http://127.0.0.1:9000/api/v1",
    [string]$PythonPath = "C:\Users\12521\miniconda3\python.exe",
    [string]$SeedFile = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonPath)) {
    throw "Python not found: $PythonPath"
}

$backendRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $SeedFile) {
    $SeedFile = Join-Path $backendRoot "data\imports\cost_seed_readable.xlsx"
}
if (-not (Test-Path $SeedFile)) {
    throw "Seed Excel not found: $SeedFile"
}

$password = "Biz2a!" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$env:BIZ2A_SMOKE_PASSWORD = $password
$env:BIZ2A_BACKEND_ROOT = $backendRoot
$env:BIZ2A_SEED_FILE = (Resolve-Path $SeedFile)
$env:BIZ2A_BASE_URL = $BaseUrl

$smokeCode = @'
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

backend_root = os.environ["BIZ2A_BACKEND_ROOT"]
sys.path.insert(0, backend_root)

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole


base_url = os.environ["BIZ2A_BASE_URL"].rstrip("/")
root_url = base_url[:-7] if base_url.endswith("/api/v1") else base_url.rsplit("/api/v1", 1)[0]
password = os.environ["BIZ2A_SMOKE_PASSWORD"]
seed_file = Path(os.environ["BIZ2A_SEED_FILE"])
suffix = uuid.uuid4().hex[:8]


def create_smoke_users():
    db = SessionLocal()
    users = {}
    try:
        for role, legacy_role in (("admin", "admin"), ("staff", "user")):
            username = f"biz2a_smoke_{role}_{suffix}"
            user = User(
                username=username,
                hashed_password=get_password_hash(password),
                role=legacy_role,
                role_version=1,
                quota=20,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz2a_runtime_smoke"))
            users[role] = {"id": user.id, "username": username}
        db.commit()
        return users
    finally:
        db.close()


def expect(response, status_code, label):
    expected = status_code if isinstance(status_code, (list, tuple, set)) else [status_code]
    if response.status_code not in expected:
        raise SystemExit(f"{label} returned HTTP {response.status_code}, expected {expected}. Body: {response.text}")
    if response.content:
        try:
            return response.json()
        except ValueError:
            return response.text
    return None


def request(client, method, path, *, token=None, status_code=200, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, f"{base_url}{path}", headers=headers, timeout=60.0, **kwargs)
    return expect(response, status_code, f"{method} {path}")


def login(client, username):
    data = request(
        client,
        "POST",
        "/auth/login",
        data={"username": username, "password": password},
    )
    return data["access_token"]


def data_of(payload):
    return payload.get("data") if isinstance(payload, dict) else payload


users = create_smoke_users()

with httpx.Client(follow_redirects=True) as client:
    health = expect(client.get(f"{root_url}/health/ready", timeout=30.0), 200, "GET /health/ready")
    if health.get("status") != "ready":
        raise SystemExit(f"FastAPI is not ready: {health}")

    admin_token = login(client, users["admin"]["username"])
    staff_token = login(client, users["staff"]["username"])

    feature_response = client.get(f"{base_url}/admin/cost-items", headers={"Authorization": f"Bearer {staff_token}"}, timeout=60.0)
    if feature_response.status_code == 404:
        raise SystemExit("Cost DB route is not registered. Restart FastAPI with the current code before running this smoke.")
    if feature_response.status_code == 403 and "FEATURE_DISABLED" in feature_response.text:
        raise SystemExit("FEATURE_COST_DB is not enabled for the running FastAPI process.")
    expect(feature_response, 200, "GET /admin/cost-items feature check")

    item_name = f"BIZ-2a smoke \u6c34\u6ce5\u7802\u6d46\u627e\u5e73 {suffix}"
    payload = {
        "category": "\u7b2c\u4e8c\u7ae0\u3001\u697c\u5730\u9762\u5de5\u7a0b",
        "subcategory": "\u697c\u5730\u9762",
        "item_name": item_name,
        "spec": "\u539a\u5ea6:30mm\u5185",
        "unit": "\u33a1",
        "price_type": "combined",
        "client_tax_excluded_price": 31.0,
        "client_labor_price": 15.147,
        "client_auxiliary_material_price": 10.5989,
        "client_direct_fee": 25.7459,
        "client_management_profit": 5.149,
        "subcontract_composite_price": 20.76284,
        "subcontract_labor_price": 12.215,
        "subcontract_main_material_price": 0,
        "subcontract_auxiliary_material_price": 8.547,
        "crew_benchmark_price": 25.0,
        "notes": "BIZ-2a runtime smoke",
    }
    created = data_of(request(client, "POST", "/admin/cost-items", token=admin_token, json=payload))
    if created["status"] != "draft" or round(created["price"], 5) != round(created["subcontract_composite_price"], 5):
        raise SystemExit(f"Create path did not create expected draft item: {created}")
    if round(created["client_labor_price"], 3) != 15.147 or round(created["subcontract_auxiliary_material_price"], 3) != 8.547:
        raise SystemExit(f"Create path did not persist cost breakdown fields: {created}")

    staff_list = data_of(request(client, "GET", "/admin/cost-items", token=staff_token, params={"keyword": item_name}))
    if not any(item["id"] == created["id"] for item in staff_list):
        raise SystemExit("Staff read path did not include admin-created item")

    fuzzy_category_list = data_of(request(client, "GET", "/admin/cost-items", token=staff_token, params={"category": "\u697c\u5730\u9762"}))
    if not any(item["id"] == created["id"] for item in fuzzy_category_list):
        raise SystemExit("Category fuzzy filter did not include admin-created item")
    keyword_notes_list = data_of(request(client, "GET", "/admin/cost-items", token=staff_token, params={"keyword": "runtime smoke"}))
    if not any(item["id"] == created["id"] for item in keyword_notes_list):
        raise SystemExit("Keyword filter did not search notes/category fields")

    staff_write = client.post(f"{base_url}/admin/cost-items", headers={"Authorization": f"Bearer {staff_token}"}, json=payload, timeout=60.0)
    expect(staff_write, 403, "staff POST /admin/cost-items")

    patched = data_of(request(
        client,
        "PATCH",
        f"/admin/cost-items/{created['id']}",
        token=admin_token,
        json={"subcontract_composite_price": 23.5, "subcontract_labor_price": 13.5, "change_reason": "BIZ-2a smoke price update"},
    ))
    if round(patched["price"], 2) != 23.50 or round(patched["subcontract_labor_price"], 2) != 13.50 or not patched.get("history") or patched["history"][-1]["change_type"] != "price_change":
        raise SystemExit(f"Price patch path did not record expected history: {patched}")

    activated = data_of(request(client, "POST", f"/admin/cost-items/{created['id']}/activate", token=admin_token))
    if activated["status"] != "active":
        raise SystemExit("Activate path did not set active")
    activated_again = data_of(request(client, "POST", f"/admin/cost-items/{created['id']}/activate", token=admin_token))
    if activated_again["status"] != "active":
        raise SystemExit("Activate idempotency path failed")

    missing_reason = client.post(f"{base_url}/admin/cost-items/{created['id']}/archive", headers={"Authorization": f"Bearer {admin_token}"}, json={}, timeout=60.0)
    expect(missing_reason, 422, "archive active without reason")

    archived = data_of(request(
        client,
        "POST",
        f"/admin/cost-items/{created['id']}/archive",
        token=admin_token,
        json={"reason": "BIZ-2a runtime smoke archive"},
    ))
    if archived["status"] != "archived":
        raise SystemExit("Archive path did not set archived")
    reactivate = client.post(f"{base_url}/admin/cost-items/{created['id']}/activate", headers={"Authorization": f"Bearer {admin_token}"}, timeout=60.0)
    expect(reactivate, 409, "reactivate archived item")

    with seed_file.open("rb") as handle:
        preview = data_of(request(
            client,
            "POST",
            "/admin/cost-items/import/preview",
            token=admin_token,
            files={"file": (seed_file.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        ))
    if preview["item_count"] < 100:
        raise SystemExit(f"Expected substantial import preview from seed workbook, got {preview['item_count']}")
    first_preview_item = next((item for item in preview["items"] if item.get("client_labor_price") is not None or item.get("subcontract_labor_price") is not None), None)
    if first_preview_item is None:
        raise SystemExit("Import preview did not expose labor/material breakdown fields")
    first_import = data_of(request(client, "POST", "/admin/cost-items/import/confirm", token=admin_token, json={"batch_id": preview["batch_id"]}))
    second_import = data_of(request(client, "POST", "/admin/cost-items/import/confirm", token=admin_token, json={"batch_id": preview["batch_id"]}))
    if first_import != second_import:
        raise SystemExit("Import confirm idempotency failed")

    cost_page = expect(client.get(f"{root_url}/admin/cost-db", timeout=30.0), 200, "GET /admin/cost-db")
    if isinstance(cost_page, str) and "<div id=\"app\"" not in cost_page and "/assets/index-" not in cost_page:
        raise SystemExit("/admin/cost-db did not return the Vite shell")

print(json.dumps({
    "status": "passed",
    "smoke_admin": users["admin"]["username"],
    "smoke_staff": users["staff"]["username"],
    "created_item_id": created["id"],
    "patched_history_count": len(patched.get("history", [])),
    "fuzzy_category_count": len(fuzzy_category_list),
    "keyword_notes_count": len(keyword_notes_list),
    "import_preview_count": preview["item_count"],
    "import_created_count": first_import.get("created_count", 0),
    "import_updated_count": first_import.get("updated_count", 0),
    "import_skipped_count": first_import.get("skipped_count", 0),
}, ensure_ascii=False))
'@

$tempSmokeScript = Join-Path ([System.IO.Path]::GetTempPath()) ("biz2a_cost_db_smoke_{0}.py" -f [guid]::NewGuid().ToString("N"))
Set-Content -Path $tempSmokeScript -Value $smokeCode -Encoding UTF8
try {
    $resultJson = & $PythonPath $tempSmokeScript
    if ($LASTEXITCODE -ne 0) {
        throw "BIZ-2a cost DB smoke failed"
    }
} finally {
    Remove-Item -LiteralPath $tempSmokeScript -Force -ErrorAction SilentlyContinue
}

$resultJson | ConvertFrom-Json | Format-List
