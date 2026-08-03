"""Current-environment smoke for the isolated Pricing Agent v1.

The script creates one small workbook in memory, exercises the real HTTP API,
and proves the frozen quote/budget record counts do not change.
"""

from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

import requests
from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models import registry as model_registry  # noqa: F401
from app.models.account import Account, AccountMembership
from app.models.budget_pricing import BudgetProjectPricingRun
from app.models.pricing_agent import PricingAgentRun, PricingArchiveFile
from app.models.quote_history import QuoteHistory
from app.models.quote_job import QuoteJob
from app.models.user import User


def _priced_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "报价清单"
    sheet.append(["项目编码", "项目名称", "项目特征", "单位", "工程量", "综合单价", "合价"])
    sheet.append(["SMOKE-001", "石膏板吊顶", "轻钢龙骨双层石膏板", "㎡", 10, 128.5, 1285])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _demand_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "需求清单"
    sheet.append(["项目编码", "项目名称", "项目特征", "单位", "工程量"])
    sheet.append(["SMOKE-001", "石膏板吊顶", "轻钢龙骨双层石膏板", "㎡", 2])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _snapshot_counts(db) -> dict[str, int]:
    return {
        "quote_jobs": db.query(QuoteJob).count(),
        "quote_history": db.query(QuoteHistory).count(),
        "budget_pricing_runs": db.query(BudgetProjectPricingRun).count(),
    }


def _smoke_user(db, username: str | None) -> User:
    query = (
        db.query(User)
        .join(AccountMembership, AccountMembership.user_id == User.id)
        .join(Account, Account.id == AccountMembership.account_id)
        .filter(
            User.is_active.is_(True),
            AccountMembership.status == "active",
            Account.status == "active",
        )
        .order_by(
            (User.username == (username or settings.system_admin_username)).desc(),
            AccountMembership.is_default.desc(),
            User.id.asc(),
        )
    )
    if username:
        query = query.filter(User.username == username)
    user = query.first()
    if user is None:
        raise RuntimeError("No active user with an active account membership is available for smoke")
    return user


def _data(response: requests.Response) -> dict:
    if not response.ok:
        raise RuntimeError(f"{response.request.method} {response.url} -> {response.status_code}: {response.text[:800]}")
    payload = response.json()
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def run_smoke(base_url: str, username: str | None = None) -> dict:
    base_url = base_url.rstrip("/")
    db = SessionLocal()
    try:
        user = _smoke_user(db, username)
        before = _snapshot_counts(db)
        token = create_access_token(
            {
                "sub": user.username,
                "role": user.role,
                "role_version": int(user.role_version or 1),
            }
        )
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {token}"}
    capabilities = _data(requests.get(f"{base_url}/pricing-agent/capabilities", headers=headers, timeout=15))
    if not capabilities.get("isolated_from_existing_quote_flow"):
        raise RuntimeError("Pricing Agent isolation capability is missing")
    expanded = next(item for item in capabilities["match_modes"] if item["value"] == "expanded")
    if expanded.get("available"):
        raise RuntimeError("Expanded mode must remain disabled during exact-only smoke")

    archive_response = requests.post(
        f"{base_url}/pricing-agent/archives",
        headers=headers,
        files={
            "file": (
                "pricing-agent-v1-smoke.xlsx",
                _priced_workbook(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        timeout=60,
    )
    archive = _data(archive_response)
    if archive.get("storage_backend") != "local" or int(archive.get("indexed_row_count") or 0) != 1:
        raise RuntimeError(f"Unexpected archive result: {archive}")

    demand = _data(
        requests.post(
            f"{base_url}/pricing-agent/demand-preview",
            headers=headers,
            files={
                "file": (
                    "pricing-agent-v1-smoke-demand.xlsx",
                    _demand_workbook(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            timeout=60,
        )
    )
    lines = demand.get("lines") or []
    if len(lines) != 1:
        raise RuntimeError(f"Expected one parsed demand line, got {len(lines)}")

    run = _data(
        requests.post(
            f"{base_url}/pricing-agent/runs",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "mode": "exact",
                "sources": ["archive"],
                "context": {
                    "city": "杭州市",
                    "project_type": "写字楼",
                    "decoration_level": "精装",
                },
                "lines": [
                    {
                        "row_key": lines[0]["row_key"],
                        "item_code": lines[0].get("item_code"),
                        "item_name": lines[0]["item_name"],
                        "specification": lines[0].get("specification"),
                        "quantity": lines[0].get("quantity"),
                        "unit": lines[0].get("unit"),
                    }
                ],
            },
            timeout=60,
        )
    )
    result_line = (run.get("result") or {}).get("lines", [None])[0]
    if not isinstance(result_line, dict):
        raise RuntimeError("Pricing Agent returned no result line")
    expected = {
        "selected_source": "archive",
        "source_label": "存档数据",
        "match_type": "code_exact",
        "unit_price": "128.500000",
        "total_price": "257.000000",
        "requires_review": False,
    }
    for key, value in expected.items():
        if result_line.get(key) != value:
            raise RuntimeError(f"Unexpected {key}: {result_line.get(key)!r}, expected {value!r}")
    if result_line.get("query_plan", {}).get("channels") != ["exact"]:
        raise RuntimeError("Exact smoke unexpectedly used a non-exact retrieval channel")

    expanded_response = requests.post(
        f"{base_url}/pricing-agent/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "mode": "expanded",
            "sources": ["archive"],
            "context": {
                "city": "杭州市",
                "project_type": "写字楼",
                "decoration_level": "精装",
            },
            "lines": [
                {
                    "row_key": "expanded-disabled-check",
                    "item_name": "石膏板吊顶",
                    "quantity": "1",
                    "unit": "㎡",
                }
            ],
        },
        timeout=30,
    )
    if expanded_response.status_code != 403:
        raise RuntimeError(f"Expanded mode guard returned {expanded_response.status_code}, expected 403")

    db = SessionLocal()
    try:
        after = _snapshot_counts(db)
        if after != before:
            raise RuntimeError(f"Frozen quote/budget tables changed: before={before}, after={after}")
        stored_archive = (
            db.query(PricingArchiveFile)
            .filter(PricingArchiveFile.archive_uuid == archive["archive_uuid"])
            .one()
        )
        stored_run = (
            db.query(PricingAgentRun)
            .filter(PricingAgentRun.run_uuid == run["run_uuid"])
            .one()
        )
        local_path = (
            Path(settings.pricing_agent_archive_local_root).resolve()
            / Path(stored_archive.storage_object_name)
        ).resolve()
        if not local_path.is_file():
            raise RuntimeError(f"Archive file was not persisted locally: {local_path}")
        pricing_counts = {
            "archive_files": db.query(PricingArchiveFile).count(),
            "agent_runs": db.query(PricingAgentRun).count(),
        }
        result = {
            "status": "passed",
            "base_url": base_url,
            "mode": stored_run.mode,
            "archive_uuid": stored_archive.archive_uuid,
            "run_uuid": stored_run.run_uuid,
            "storage_backend": stored_archive.storage_backend,
            "storage_file_exists": True,
            "indexed_rows": stored_archive.indexed_row_count,
            "selected_source": result_line["selected_source"],
            "unit_price": result_line["unit_price"],
            "total_price": result_line["total_price"],
            "expanded_guard": "passed",
            "frozen_counts_before": before,
            "frozen_counts_after": after,
            "pricing_agent_counts": pricing_counts,
        }
    finally:
        db.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001/api/v1")
    parser.add_argument("--username", default=None)
    args = parser.parse_args()
    print(json.dumps(run_smoke(args.base_url, args.username), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
