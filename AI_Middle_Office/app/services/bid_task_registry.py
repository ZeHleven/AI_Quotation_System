"""Deterministic standard-task registry for bid-assessment Planner v1.

The registry is deliberately local and immutable for one code release.  A
Planner proposal may select task types from the governed catalog, but it may
not invent execution profiles, budgets, completion contracts, or tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.services.bid_assessment_eventing import canonical_hash


TASK_CATALOG_ROOT = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "bid_assessment"
    / "v1"
)
DEFAULT_TASK_CATALOG = "task-catalog.json"


@dataclass(frozen=True)
class TaskPolicy:
    task_type: str
    category: str
    objective: str
    tool_profile: str
    context_profile: str
    budget_profile: str
    completion_contract: str
    allowed_tools: tuple[str, ...]
    priority: int

    def definition(
        self,
        *,
        task_key: str,
        depends_on: tuple[str, ...] = (),
        required_fact_slots: tuple[str, ...] = (),
        skill_binding: Mapping[str, str] | None = None,
        freeze_allowed_tools: bool = False,
    ) -> dict[str, Any]:
        definition: dict[str, Any] = {
            "task_key": task_key,
            "task_type": self.task_type,
            "objective": self.objective,
            "depends_on": list(depends_on),
            "required_fact_slots": list(required_fact_slots),
            "tool_profile": self.tool_profile,
            "context_profile": self.context_profile,
            "budget_profile": self.budget_profile,
            "completion_contract": self.completion_contract,
        }
        if freeze_allowed_tools:
            definition["allowed_tools"] = list(self.allowed_tools)
        if skill_binding is not None:
            definition["skill_binding"] = dict(skill_binding)
        return definition


@dataclass(frozen=True)
class StandardTaskRegistry:
    catalog_ref: str
    catalog_id: str
    catalog_version: str
    registry_hash: str
    task_order: tuple[str, ...]
    policies: Mapping[str, TaskPolicy]

    @property
    def version(self) -> str:
        return f"{self.catalog_id}@{self.catalog_version}"


_CATEGORY_DEFAULTS: dict[str, dict[str, Any]] = {
    "document_scope_version": {
        "tool_profile": "DOCUMENT_SCOPE_READ_V1",
        "context_profile": "DOCUMENT_SCOPE_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "document_scope_result_v1",
        "allowed_tools": (
            "facts.query",
            "evidence.search",
            "evidence.read",
            "documents.outline",
            "tables.read_region",
            "documents.compare_versions",
            "tool_result.read_slice",
        ),
        "priority": 10,
    },
    "tender_fact_extraction": {
        "tool_profile": "TENDER_FACT_EXTRACTION_V1",
        "context_profile": "TENDER_FACTS_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "fact_assertion_candidates_v1",
        "allowed_tools": (
            "facts.query",
            "evidence.search",
            "evidence.read",
            "documents.outline",
            "tables.read_region",
            "tool_result.read_slice",
        ),
        "priority": 30,
    },
    "enterprise_data": {
        "tool_profile": "ENTERPRISE_GOVERNED_READ_V1",
        "context_profile": "ENTERPRISE_SNAPSHOT_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "enterprise_fact_candidates_v1",
        "allowed_tools": (
            "facts.query",
            "enterprise.profile.query",
            "enterprise.qualifications.query",
            "enterprise.personnel.query",
            "enterprise.projects.search",
            "enterprise.capacity.query",
            "enterprise.financial_capacity.query",
            "enterprise.customer_history.query",
            "enterprise.bid_history.query",
            "enterprise.bid_rate_card.query",
            "quota.cost_items.search",
        ),
        "priority": 40,
    },
    "hard_gate": {
        "tool_profile": "HARD_GATE_READ_CALCULATE_V1",
        "context_profile": "HARD_GATE_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "hard_gate_result_v1",
        "allowed_tools": (
            "facts.query",
            "enterprise.qualifications.query",
            "enterprise.personnel.query",
            "enterprise.capacity.query",
            "enterprise.financial_capacity.query",
            "calculate.bid_workload",
            "calculate.fund_occupation_cost",
        ),
        "priority": 50,
    },
    "dimension_analysis": {
        "tool_profile": "DIMENSION_ANALYSIS_V1",
        "context_profile": "DIMENSION_ANALYSIS_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "dimension_result_v1",
        "allowed_tools": (
            "facts.query",
            "evidence.search",
            "evidence.read",
            "enterprise.projects.search",
            "enterprise.capacity.query",
            "enterprise.financial_capacity.query",
            "enterprise.customer_history.query",
            "enterprise.bid_history.query",
            "enterprise.bid_rate_card.query",
            "quota.cost_items.search",
            "calculate.bid_workload",
            "calculate.bid_labor_cost",
            "calculate.external_bid_expense",
            "calculate.fund_occupation_cost",
            "calculate.bid_investment_total",
            "calculate.project_economics",
            "calculate.payment_cashflow",
            "calculate.sensitivity_scenarios",
        ),
        "priority": 60,
    },
    "synthesis_validation_report": {
        "tool_profile": "SYNTHESIS_VALIDATION_V1",
        "context_profile": "SYNTHESIS_VALIDATION_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "synthesis_validation_result_v1",
        "allowed_tools": (
            "facts.query",
            "evidence.search",
            "evidence.read",
            "tool_result.read_slice",
        ),
        "priority": 70,
    },
}


_OBJECTIVE_OVERRIDES = {
    "bind_assessment_snapshot": "Bind and verify the immutable assessment, scope, manifest, enterprise, rule, prompt, tool, model, and formula versions for this Run.",
    "inventory_documents": "Build the document inventory only from the committed manifest and authoritative parse heads bound to this Run.",
    "build_coverage_baseline": "Create the initial fact-slot coverage baseline without inferring missing facts from filenames, MIME types, or parser hints.",
    "build_enterprise_snapshot": "Materialize the Run-pinned immutable enterprise capability snapshot as governed I01-I11 facts with exact record lineage.",
    "extract_tender_overview": "Extract governed tender overview fact candidates with evidence references.",
    "extract_critical_dates": "Extract governed critical-date fact candidates with evidence references.",
    "extract_qualification_requirements": "Extract governed qualification requirement fact candidates with evidence references.",
    "extract_rejection_clauses": "Extract governed rejection-clause fact candidates with evidence references.",
    "extract_guarantees_and_fees": "Extract governed guarantee and fee fact candidates with evidence references.",
    "validate_claim_evidence": "Validate that report claims are supported by resolved facts and direct evidence citations.",
    "validate_report_consistency": "Validate report, decision, gate, dimension, and evidence consistency before publication.",
    "generate_preliminary_report": "Generate a preliminary report only from validated governed outputs.",
    "generate_deep_report": "Generate a deep report only from validated governed outputs.",
}


_PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "build_enterprise_snapshot": {
        "tool_profile": "ENTERPRISE_SNAPSHOT_MATERIALIZE_V1",
        "context_profile": "ENTERPRISE_SNAPSHOT_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "enterprise_snapshot_fact_result_v1",
        "allowed_tools": (),
        "priority": 40,
    },
    "evaluate_final_decision": {
        "tool_profile": "DETERMINISTIC_DECISION_V1",
        "context_profile": "DECISION_INPUTS_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "decision_result_v1",
        "allowed_tools": ("facts.query",),
        "priority": 80,
    },
    "validate_claim_evidence": {
        "tool_profile": "REPORT_VALIDATION_V1",
        "context_profile": "REPORT_VALIDATION_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "claim_evidence_validation_v1",
        "priority": 90,
    },
    "validate_report_consistency": {
        "tool_profile": "REPORT_VALIDATION_V1",
        "context_profile": "REPORT_VALIDATION_V1",
        "budget_profile": "LOW_V1",
        "completion_contract": "report_consistency_validation_v1",
        "priority": 91,
    },
    "generate_preliminary_report": {
        "tool_profile": "REPORT_GOVERNED_READ_V1",
        "context_profile": "PRELIMINARY_REPORT_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "preliminary_report_draft_v1",
        "priority": 100,
    },
    "generate_deep_report": {
        "tool_profile": "REPORT_GOVERNED_READ_V1",
        "context_profile": "DEEP_REPORT_V1",
        "budget_profile": "HIGH_V1",
        "completion_contract": "deep_report_draft_v1",
        "priority": 100,
    },
    "generate_version_delta": {
        "tool_profile": "REPORT_GOVERNED_READ_V1",
        "context_profile": "VERSION_DELTA_V1",
        "budget_profile": "STANDARD_V1",
        "completion_contract": "version_delta_draft_v1",
        "priority": 100,
    },
}


def _default_objective(task_type: str) -> str:
    return f"Execute governed standard task: {task_type.replace('_', ' ')}."


@lru_cache(maxsize=8)
def load_standard_task_registry(
    catalog_filename: str = DEFAULT_TASK_CATALOG,
) -> StandardTaskRegistry:
    if Path(catalog_filename).name != catalog_filename:
        raise RuntimeError("BID_TASK_CATALOG_PATH_INVALID")
    catalog_path = TASK_CATALOG_ROOT / catalog_filename
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_tasks = list(catalog.get("tasks") or [])
    if len(catalog_tasks) != 49:
        raise RuntimeError("BID_TASK_CATALOG_COUNT_INVALID")

    policies: dict[str, TaskPolicy] = {}
    task_order: list[str] = []
    for item in catalog_tasks:
        task_type = str(item.get("task_type") or "")
        category = str(item.get("category") or "")
        if not task_type or task_type in policies:
            raise RuntimeError("BID_TASK_CATALOG_TASK_TYPE_INVALID")
        if category not in _CATEGORY_DEFAULTS:
            raise RuntimeError(f"BID_TASK_CATALOG_CATEGORY_INVALID:{category}")
        values = dict(_CATEGORY_DEFAULTS[category])
        values.update(_PROFILE_OVERRIDES.get(task_type, {}))
        policy = TaskPolicy(
            task_type=task_type,
            category=category,
            objective=_OBJECTIVE_OVERRIDES.get(task_type, _default_objective(task_type)),
            tool_profile=str(values["tool_profile"]),
            context_profile=str(values["context_profile"]),
            budget_profile=str(values["budget_profile"]),
            completion_contract=str(values["completion_contract"]),
            allowed_tools=tuple(values["allowed_tools"]),
            priority=int(values["priority"]),
        )
        policies[task_type] = policy
        task_order.append(task_type)

    registry_payload = {
        "catalog_id": str(catalog["catalog_id"]),
        "catalog_version": str(catalog["catalog_version"]),
        "tasks": [
            {
                "task_type": policies[task_type].task_type,
                "category": policies[task_type].category,
                "tool_profile": policies[task_type].tool_profile,
                "context_profile": policies[task_type].context_profile,
                "budget_profile": policies[task_type].budget_profile,
                "completion_contract": policies[task_type].completion_contract,
                "allowed_tools": list(policies[task_type].allowed_tools),
                "priority": policies[task_type].priority,
            }
            for task_type in task_order
        ],
    }
    return StandardTaskRegistry(
        catalog_ref=catalog_filename,
        catalog_id=str(catalog["catalog_id"]),
        catalog_version=str(catalog["catalog_version"]),
        registry_hash=canonical_hash(registry_payload),
        task_order=tuple(task_order),
        policies=MappingProxyType(policies),
    )


def load_frozen_task_registry(
    *,
    catalog_ref: str,
    registry_version: str,
    registry_hash: str,
) -> StandardTaskRegistry:
    registry = load_standard_task_registry(str(catalog_ref))
    if registry.version != str(registry_version) or registry.registry_hash != str(registry_hash):
        raise RuntimeError("BID_TASK_CATALOG_HASH_MISMATCH")
    return registry
