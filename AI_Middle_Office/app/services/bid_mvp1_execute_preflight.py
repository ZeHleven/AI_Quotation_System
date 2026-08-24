"""Non-mutating readiness projection for the isolated MVP-1 execute mode.

The projection deliberately reports booleans and stable reason codes only.  It
must never return secret values, filesystem paths, or model artefact contents.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bid_assessment_config import BidEnterpriseSnapshot, BidModelProfileVersion
from app.models.bid_assessment_release import BidHardGateComparisonBaseline
from app.services.bid_enterprise_capability import validate_frozen_snapshot_metadata
from app.services.bid_enterprise_business_baseline import latest_business_snapshot
from app.services.bid_enterprise_evidence_import import latest_enterprise_evidence_package
from app.services.bid_hard_gate_fact_verification import (
    validate_hard_gate_comparison_baseline_at,
)


EXECUTE_PREFLIGHT_SCHEMA = "bid.runtime.execute-preflight.v2"
_DEEPSEEK_MODE = "deepseek-v4-flash"
_SUPPORTED_RETRIEVAL_MODES = frozenset({"legacy", "rq2b", "rq2c"})


def _check(
    code: str,
    label: str,
    status: str,
    *,
    required: bool,
    detail: str,
) -> dict[str, Any]:
    if status not in {"ready", "blocked", "deferred", "inactive"}:
        raise ValueError("unsupported preflight status")
    return {
        "code": code,
        "label": label,
        "status": status,
        "required": bool(required),
        "detail": detail,
    }


def _directory_ready(raw_path: str) -> bool:
    value = str(raw_path or "").strip()
    try:
        return bool(value and Path(value).is_dir())
    except (OSError, ValueError):
        return False


def build_execute_preflight(
    db: Session,
    *,
    runtime_access: dict[str, object],
    expected_model_profile_version: str,
    rq2_runtime_ready: bool,
    authority_epoch: str,
    view_only_secret_isolated: bool,
) -> dict[str, Any]:
    """Build a read-only, non-secret execute readiness snapshot."""

    local_lab = bool(runtime_access.get("local_lab"))
    access_mode = str(runtime_access.get("access_mode") or "view-only")
    execute_process = bool(
        local_lab
        and access_mode == "execute"
        and runtime_access.get("execution_enabled")
    )
    model_provider = str(runtime_access.get("model_provider") or "configured_gateway")
    retrieval_mode = str(runtime_access.get("retrieval_mode") or "configured")
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "LOCAL_LAB_BOUNDARY",
            "隔离边界",
            "ready" if local_lab else "blocked",
            required=True,
            detail=(
                "localhost、SQLite、本地对象目录和进程内队列边界已生效"
                if local_lab
                else "该进程不是隔离的本地 Runtime Lab"
            ),
        )
    )

    fact_verification_required = bool(
        getattr(settings, "feature_bid_assessment_phase4_fact_verification", False)
    )
    comparison_baseline_ready = False
    comparison_baseline_hash = ""
    if fact_verification_required:
        try:
            database_now = db.execute(select(func.current_timestamp())).scalar_one()
            comparison_baseline = db.query(BidHardGateComparisonBaseline).filter(
                BidHardGateComparisonBaseline.status == "frozen"
            ).order_by(
                BidHardGateComparisonBaseline.reviewed_at.desc(),
                BidHardGateComparisonBaseline.id.desc(),
            ).first()
            if comparison_baseline is not None:
                validate_hard_gate_comparison_baseline_at(
                    db,
                    baseline=comparison_baseline,
                    effective_at=database_now,
                )
                comparison_baseline_ready = True
                comparison_baseline_hash = str(comparison_baseline.baseline_hash)
        except Exception:
            comparison_baseline_ready = False
    if fact_verification_required:
        checks.append(
            _check(
                "HARD_GATE_COMPARISON_BASELINE",
                "硬门可比事实基线",
                "ready" if comparison_baseline_ready else "blocked",
                required=True,
                detail=(
                    "招标侧 Atom 与企业侧 Evidence Item 已逐项核验并冻结"
                    if comparison_baseline_ready
                    else "请先完成 16 个硬门输入事实的核验与比较基线冻结"
                ),
            )
        )

    business_baseline_required = bool(
        getattr(settings, "feature_bid_assessment_phase4_business_baseline", False)
    )
    business_baseline_ready = False
    business_baseline_hash = ""
    evidence_import_required = bool(
        getattr(
            settings,
            "feature_bid_assessment_phase4_enterprise_evidence_import",
            False,
        )
    )
    evidence_package_ready = False
    if evidence_import_required:
        try:
            evidence_package_ready = latest_enterprise_evidence_package(db) is not None
        except Exception:
            evidence_package_ready = False
    if business_baseline_required:
        try:
            database_now = db.execute(select(func.current_timestamp())).scalar_one()
            business_pair = latest_business_snapshot(
                db,
                effective_at=database_now,
            )
            if business_pair is not None:
                business_snapshot, business_baseline = business_pair
                validate_frozen_snapshot_metadata(db, business_snapshot)
                business_baseline_ready = True
                business_baseline_hash = str(business_baseline.baseline_hash or "")
        except Exception:
            business_baseline_ready = False
    checks.append(
        _check(
            "ENTERPRISE_EVIDENCE_PACKAGE",
            "真实企业能力资料包",
            (
                "ready"
                if evidence_import_required and evidence_package_ready
                else "blocked"
                if evidence_import_required
                else "inactive"
            ),
            required=evidence_import_required,
            detail=(
                "真实企业资料已按内容 Hash 冻结，并显式映射到 I01—I11"
                if evidence_import_required and evidence_package_ready
                else "请先上传企业资料并冻结 I01—I11 Evidence Package"
                if evidence_import_required
                else "Phase 4D-2 企业资料导入未启用"
            ),
        )
    )

    checks.append(
        _check(
            "ENTERPRISE_BUSINESS_BASELINE",
            "真实企业能力基线",
            (
                "ready"
                if business_baseline_required and business_baseline_ready
                else "blocked" if business_baseline_required else "inactive"
            ),
            required=business_baseline_required,
            detail=(
                "I01—I11 已经业务负责人逐项核验并冻结"
                if business_baseline_required and business_baseline_ready
                else (
                    "请先对最新企业快照完成 I01—I11 来源凭证核验"
                    if business_baseline_required
                    else "Phase 4D-1 真实企业基线未启用"
                )
            ),
        )
    )

    active_profile = None
    try:
        active_profile = (
            db.query(BidModelProfileVersion)
            .filter(BidModelProfileVersion.active_slot_key == "active")
            .one_or_none()
        )
    except Exception:
        active_profile = None
    profile_ready = bool(
        active_profile is not None
        and expected_model_profile_version
        and str(active_profile.version) == expected_model_profile_version
        and str(active_profile.status) == "active"
    )
    checks.append(
        _check(
            "FROZEN_MODEL_PROFILE",
            "冻结模型配置",
            "ready" if profile_ready else "blocked",
            required=True,
            detail=(
                "当前数据库的 active Model Profile 与启动模式一致"
                if profile_ready
                else "active Model Profile 缺失或与启动模式不一致"
            ),
        )
    )

    storage_value = str(settings.bid_upload_local_root or "").strip()
    try:
        storage_root = Path(storage_value) if storage_value else None
        storage_exists = bool(storage_root is not None and storage_root.is_dir())
        storage_writable = bool(storage_exists and os.access(storage_root, os.W_OK))
    except (OSError, ValueError):
        storage_exists = False
        storage_writable = False
    if storage_writable:
        storage_status = "ready"
        storage_detail = "本地对象目录已存在且具备写权限"
    elif not execute_process and not storage_exists:
        storage_status = "deferred"
        storage_detail = "execute 启动器将在受控本地目录内创建对象目录"
    else:
        storage_status = "blocked"
        storage_detail = "本地对象目录不可写"
    checks.append(
        _check(
            "LOCAL_OBJECT_STORE",
            "本地对象存储",
            storage_status,
            required=True,
            detail=storage_detail,
        )
    )

    if model_provider == _DEEPSEEK_MODE:
        credential_loaded = bool(settings.bid_assessment_model_api_key.strip())
        if execute_process:
            credential_status = "ready" if credential_loaded else "blocked"
            credential_detail = (
                "模型凭据已由 execute 进程加载"
                if credential_loaded
                else "execute 进程未加载模型凭据"
            )
        else:
            credential_status = "deferred"
            credential_detail = "view-only 不加载模型凭据；execute 启动时单独校验"
        checks.append(
            _check(
                "MODEL_CREDENTIAL",
                "模型凭据",
                credential_status,
                required=True,
                detail=credential_detail,
            )
        )
    else:
        checks.append(
            _check(
                "MODEL_CREDENTIAL",
                "模型凭据",
                "inactive",
                required=False,
                detail="确定性本地 Provider 不需要外部模型凭据",
            )
        )

    retrieval_supported = retrieval_mode in _SUPPORTED_RETRIEVAL_MODES
    checks.append(
        _check(
            "RETRIEVAL_PROFILE",
            "检索模式",
            "ready" if retrieval_supported else "blocked",
            required=True,
            detail=(
                f"已选择受控 {retrieval_mode} 检索链"
                if retrieval_supported
                else "检索模式不在本地 Runtime Lab 白名单"
            ),
        )
    )

    semantic_required = retrieval_mode in {"rq2b", "rq2c"}
    semantic_ready = _directory_ready(settings.bid_evidence_semantic_model_path)
    checks.append(
        _check(
            "SEMANTIC_SNAPSHOT",
            "BCE Embedding Snapshot",
            (
                "ready"
                if semantic_required and semantic_ready
                else "blocked" if semantic_required else "inactive"
            ),
            required=semantic_required,
            detail=(
                "固定 revision 的本地 Embedding Snapshot 可用"
                if semantic_required and semantic_ready
                else (
                    "固定 revision 的本地 Embedding Snapshot 缺失"
                    if semantic_required
                    else "legacy 检索不使用语义模型"
                )
            ),
        )
    )

    rq2_runtime_required = semantic_required
    checks.append(
        _check(
            "RQ2_RUNTIME",
            "RQ2 冻结运行依赖",
            (
                "ready"
                if rq2_runtime_required and rq2_runtime_ready
                else "blocked" if rq2_runtime_required else "inactive"
            ),
            required=rq2_runtime_required,
            detail=(
                "隔离的 RQ2 Worker 依赖可用"
                if rq2_runtime_required and rq2_runtime_ready
                else (
                    "隔离的 RQ2 Worker 依赖缺失"
                    if rq2_runtime_required
                    else "legacy 检索不需要 RQ2 运行依赖"
                )
            ),
        )
    )

    reranker_required = retrieval_mode == "rq2c"
    reranker_ready = _directory_ready(settings.bid_evidence_reranker_model_path)
    checks.append(
        _check(
            "RERANKER_SNAPSHOT",
            "BCE Reranker Snapshot",
            (
                "ready"
                if reranker_required and reranker_ready
                else "blocked" if reranker_required else "inactive"
            ),
            required=reranker_required,
            detail=(
                "固定 revision 的本地 Reranker Snapshot 可用"
                if reranker_required and reranker_ready
                else (
                    "固定 revision 的本地 Reranker Snapshot 缺失"
                    if reranker_required
                    else "当前检索模式不启用 RQ2-C 重排"
                )
            ),
        )
    )

    enterprise_required = bool(
        settings.feature_bid_assessment_phase4_enterprise_capability
    )
    enterprise_ready = False
    enterprise_snapshot_hash = ""
    if enterprise_required:
        try:
            enterprise_snapshot = (
                db.query(BidEnterpriseSnapshot)
                .filter(BidEnterpriseSnapshot.status == "frozen")
                .order_by(
                    BidEnterpriseSnapshot.as_of.desc(),
                    BidEnterpriseSnapshot.frozen_at.desc(),
                    BidEnterpriseSnapshot.id.desc(),
                )
                .first()
            )
            if enterprise_snapshot is not None:
                validate_frozen_snapshot_metadata(db, enterprise_snapshot)
                enterprise_ready = True
                enterprise_snapshot_hash = str(enterprise_snapshot.snapshot_hash or "")
        except Exception:
            enterprise_ready = False
    checks.append(
        _check(
            "ENTERPRISE_CAPABILITY_SNAPSHOT",
            "企业能力快照",
            (
                "ready"
                if enterprise_required and enterprise_ready
                else "blocked" if enterprise_required else "inactive"
            ),
            required=enterprise_required,
            detail=(
                "I01—I11 企业能力快照已冻结且 Hash 校验通过"
                if enterprise_required and enterprise_ready
                else (
                    "请先配置并冻结完整的 I01—I11 企业能力快照"
                    if enterprise_required
                    else "Phase 4C-1 企业能力快照未启用"
                )
            ),
        )
    )

    worker_running = bool(runtime_access.get("worker_running"))
    checks.append(
        _check(
            "WORKER_LIFECYCLE",
            "进程内 Worker",
            (
                "ready"
                if execute_process and worker_running
                else "blocked" if execute_process else "deferred"
            ),
            required=True,
            detail=(
                "execute Worker 已启动"
                if execute_process and worker_running
                else (
                    "execute 模式下 Worker 未运行"
                    if execute_process
                    else "view-only 不启动 Worker；切换需重启本地进程"
                )
            ),
        )
    )

    checks.append(
        _check(
            "WRITE_AUTHORITY",
            "写入权限",
            "ready" if execute_process else "deferred",
            required=True,
            detail=(
                "服务端 execute 写权限已显式启用"
                if execute_process
                else "view-only 不能在网页内提升权限；切换需重启本地进程"
            ),
        )
    )

    checks.append(
        _check(
            "VIEW_ONLY_SECRET_FENCE",
            "view-only 密钥围栏",
            (
                "inactive"
                if execute_process
                else "ready" if view_only_secret_isolated else "blocked"
            ),
            required=not execute_process,
            detail=(
                "execute 进程按模型 Gateway 合同持有凭据"
                if execute_process
                else (
                    "真实模型凭据已被禁用哨兵隔离"
                    if view_only_secret_isolated
                    else "view-only 进程可能读取到真实模型凭据"
                )
            ),
        )
    )

    blocking_codes = [
        str(item["code"])
        for item in checks
        if item["required"] and item["status"] == "blocked"
    ]
    deferred_codes = [
        str(item["code"])
        for item in checks
        if item["required"] and item["status"] == "deferred"
    ]
    launch_ready = not blocking_codes
    current_process_ready = bool(
        execute_process
        and launch_ready
        and not deferred_codes
        and runtime_access.get("write_enabled")
        and runtime_access.get("worker_running")
        and runtime_access.get("model_calls_enabled")
    )
    authority_payload = {
        "schema": EXECUTE_PREFLIGHT_SCHEMA,
        "access_mode": access_mode,
        "execution_enabled": bool(runtime_access.get("execution_enabled")),
        "write_enabled": bool(runtime_access.get("write_enabled")),
        "worker_running": worker_running,
        "model_calls_enabled": bool(runtime_access.get("model_calls_enabled")),
        "model_provider": model_provider,
        "retrieval_mode": retrieval_mode,
        "blocking_codes": blocking_codes,
        "deferred_codes": deferred_codes,
        "enterprise_snapshot_hash": enterprise_snapshot_hash,
        "enterprise_business_baseline_hash": business_baseline_hash,
        "authority_epoch": str(authority_epoch),
    }
    if fact_verification_required:
        authority_payload["hard_gate_comparison_baseline_hash"] = (
            comparison_baseline_hash
        )
    authority_fingerprint = hashlib.sha256(
        json.dumps(
            authority_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": EXECUTE_PREFLIGHT_SCHEMA,
        "local_lab": local_lab,
        "access_mode": access_mode,
        "model_provider": model_provider,
        "retrieval_mode": retrieval_mode,
        "launch_ready": launch_ready,
        "current_process_ready": current_process_ready,
        "restart_required": access_mode != "execute",
        "blocking_codes": blocking_codes,
        "deferred_codes": deferred_codes,
        "authority_fingerprint": authority_fingerprint,
        "checks": checks,
    }
