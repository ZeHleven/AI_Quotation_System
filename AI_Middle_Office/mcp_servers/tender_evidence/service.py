from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .auth import ScopeTokenError, TenderScope
from .contracts import (
    DocumentManifest,
    EvidenceBlock,
    EvidenceRefInput,
    ResultStatus,
    ToolEnvelope,
)
from .local_repository import (
    EvidenceNotFoundError,
    TenderCaseNotFoundError,
    TenderEvidenceRepositoryError,
)
from .repository import TenderEvidenceRepository
from .query_planner import (
    DEFAULT_COVERAGE_SELECTION_POLICY,
    PREDICATE_AWARE_MARGINAL_GAIN_POLICY,
    CoverageNeedMatch,
    PlannedEvidenceResult,
    TenderQueryPlan,
    match_block_coverage_needs,
    match_block_sufficiency_needs,
    merge_planned_results,
    plan_tender_query,
)
from .retrieval_router import route_tender_query
from .selective_graph import (
    VerifiedExactReference,
    decide_graph_trigger,
    exact_reference_target_matches,
    extract_verified_exact_references,
    is_verified_table_parent_seed,
    verified_structural_children,
)


class TenderCapabilityDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class _ContextGroupMember:
    block: EvidenceBlock
    coverage_matches: tuple[CoverageNeedMatch, ...]
    complementary_need_indexes: tuple[int, ...]
    graph_path: dict[str, object] | None = None


class TenderEvidenceService:
    def __init__(
        self,
        repository: TenderEvidenceRepository,
        *,
        scope_provider: Callable[[], TenderScope],
        per_query_candidate_top_k: int | None = None,
        enable_semantic_fact_companion: bool = False,
        enable_atomic_fact_slots: bool = False,
        enable_candidate_coverage_selection: bool = False,
        enable_evidence_sufficiency_assessment: bool = False,
        candidate_coverage_selection_policy: str = (
            DEFAULT_COVERAGE_SELECTION_POLICY
        ),
        enable_adjacent_candidate_expansion: bool = False,
        enable_context_evidence_groups: bool = False,
        enable_structured_context_groups: bool = False,
        enable_controlled_second_round: bool = False,
        enable_selective_graph_expansion: bool = False,
    ):
        if (
            enable_adjacent_candidate_expansion
            and enable_context_evidence_groups
        ):
            raise ValueError(
                "adjacent candidate expansion and context evidence "
                "groups are mutually exclusive experiments"
            )
        if enable_structured_context_groups and (
            enable_adjacent_candidate_expansion
            or enable_context_evidence_groups
        ):
            raise ValueError(
                "structured context groups must be evaluated without "
                "the legacy adjacent-context experiments"
            )
        if (
            enable_controlled_second_round
            and not enable_candidate_coverage_selection
        ):
            raise ValueError(
                "controlled second-round retrieval requires "
                "candidate coverage selection"
            )
        if (
            enable_controlled_second_round
            and not enable_structured_context_groups
        ):
            raise ValueError(
                "controlled second-round retrieval requires "
                "structured context groups"
            )
        if enable_controlled_second_round and (
            enable_adjacent_candidate_expansion
            or enable_context_evidence_groups
        ):
            raise ValueError(
                "controlled second-round retrieval must be evaluated "
                "without the legacy adjacent-context experiments"
            )
        if enable_selective_graph_expansion and (
            enable_adjacent_candidate_expansion
            or enable_context_evidence_groups
            or enable_structured_context_groups
            or enable_controlled_second_round
        ):
            raise ValueError(
                "selective graph expansion must be evaluated without "
                "other context-expansion experiments"
            )
        self._repository = repository
        self._scope_provider = scope_provider
        self._per_query_candidate_top_k = (
            None
            if per_query_candidate_top_k is None
            else min(max(int(per_query_candidate_top_k), 1), 20)
        )
        self._enable_semantic_fact_companion = bool(
            enable_semantic_fact_companion
        )
        self._enable_atomic_fact_slots = bool(enable_atomic_fact_slots)
        self._enable_candidate_coverage_selection = bool(
            enable_candidate_coverage_selection
        )
        self._enable_evidence_sufficiency_assessment = bool(
            enable_evidence_sufficiency_assessment
        )
        self._candidate_coverage_selection_policy = str(
            candidate_coverage_selection_policy
            or DEFAULT_COVERAGE_SELECTION_POLICY
        ).strip()
        self._enable_adjacent_candidate_expansion = bool(
            enable_adjacent_candidate_expansion
        )
        self._enable_context_evidence_groups = bool(
            enable_context_evidence_groups
        )
        self._enable_structured_context_groups = bool(
            enable_structured_context_groups
        )
        self._enable_controlled_second_round = bool(
            enable_controlled_second_round
        )
        self._enable_selective_graph_expansion = bool(
            enable_selective_graph_expansion
        )

    def _scope_for(self, capability: str) -> TenderScope:
        try:
            scope = self._scope_provider()
        except ScopeTokenError:
            raise
        if not scope.allows(capability):
            raise TenderCapabilityDeniedError(
                f"service token does not allow capability: {capability}"
            )
        return scope

    def get_manifest(self) -> DocumentManifest:
        scope = self._scope_for("read_manifest")
        return self._repository.get_manifest(case_id=scope.case_id)

    def search_tender_evidence(
        self,
        *,
        query: str,
        top_k: int = 5,
    ) -> ToolEnvelope:
        trace_id = _trace_id()
        try:
            scope = self._scope_for("search_tender_evidence")
            normalized_query = query.strip()
            if not normalized_query:
                return _failed(
                    trace_id,
                    error_code="invalid_query",
                    message="query must not be empty",
                )
            if len(normalized_query) > 500:
                return _failed(
                    trace_id,
                    error_code="query_too_long",
                    message="query must contain at most 500 characters",
                )
            bounded_top_k = min(max(int(top_k), 1), 20)
            plan = plan_tender_query(
                normalized_query,
                enable_semantic_fact_companion=(
                    self._enable_semantic_fact_companion
                ),
                enable_atomic_fact_slots=self._enable_atomic_fact_slots,
                enable_candidate_coverage_selection=(
                    self._enable_candidate_coverage_selection
                ),
                enable_evidence_sufficiency_assessment=(
                    self._enable_evidence_sufficiency_assessment
                ),
                candidate_coverage_selection_policy=(
                    self._candidate_coverage_selection_policy
                ),
            )
            configured_candidate_top_k = (
                bounded_top_k
                if self._per_query_candidate_top_k is None
                else self._per_query_candidate_top_k
            )
            per_query_top_k = min(
                max(configured_candidate_top_k, bounded_top_k, 5),
                20,
            )
            ranked_results = []
            query_tasks: list[dict[str, object]] = []
            for query_index, planned_query in enumerate(plan.queries):
                route = route_tender_query(planned_query)
                blocks = self._repository.search(
                    case_id=scope.case_id,
                    query=planned_query,
                    top_k=per_query_top_k,
                    search_mode=route.mode,
                )
                executed_mode = route.mode
                fallback_triggered = False
                if not blocks and route.fallback_mode is not None:
                    fallback_triggered = True
                    executed_mode = route.fallback_mode
                    blocks = self._repository.search(
                        case_id=scope.case_id,
                        query=planned_query,
                        top_k=per_query_top_k,
                        search_mode=route.fallback_mode,
                    )
                ranked_results.append((planned_query, blocks))
                task_payload = route.to_payload(
                    query_id=f"q{query_index + 1}",
                    query_kind=(
                        "original" if query_index == 0 else "atomic"
                    ),
                )
                task_payload.update(
                    {
                        "executed_mode": executed_mode,
                        "fallback_triggered": fallback_triggered,
                        "result_count": len(blocks),
                    }
                )
                query_tasks.append(task_payload)
            fact_slot_query_tasks: list[dict[str, object]] = []
            for query_index, planned_query in enumerate(
                plan.fact_slot_queries
            ):
                route = route_tender_query(planned_query)
                blocks = self._repository.search(
                    case_id=scope.case_id,
                    query=planned_query,
                    top_k=per_query_top_k,
                    search_mode=route.mode,
                )
                executed_mode = route.mode
                fallback_triggered = False
                if not blocks and route.fallback_mode is not None:
                    fallback_triggered = True
                    executed_mode = route.fallback_mode
                    blocks = self._repository.search(
                        case_id=scope.case_id,
                        query=planned_query,
                        top_k=per_query_top_k,
                        search_mode=route.fallback_mode,
                    )
                ranked_results.append((planned_query, blocks))
                task_payload = route.to_payload(
                    query_id=f"slot{query_index + 1}",
                    query_kind="atomic_fact_slot",
                )
                task_payload.update(
                    {
                        "fact_slot_type": plan.fact_slot_types[query_index],
                        "executed_mode": executed_mode,
                        "fallback_triggered": fallback_triggered,
                        "result_count": len(blocks),
                    }
                )
                fact_slot_query_tasks.append(task_payload)
            supporting_query_tasks: list[dict[str, object]] = []
            for query_index, planned_query in enumerate(
                plan.supporting_queries
            ):
                route = route_tender_query(planned_query)
                blocks = self._repository.search(
                    case_id=scope.case_id,
                    query=planned_query,
                    top_k=per_query_top_k,
                    search_mode=route.mode,
                )
                executed_mode = route.mode
                fallback_triggered = False
                if not blocks and route.fallback_mode is not None:
                    fallback_triggered = True
                    executed_mode = route.fallback_mode
                    blocks = self._repository.search(
                        case_id=scope.case_id,
                        query=planned_query,
                        top_k=per_query_top_k,
                        search_mode=route.fallback_mode,
                    )
                ranked_results.append((planned_query, blocks))
                task_payload = route.to_payload(
                    query_id=f"support{query_index + 1}",
                    query_kind="supporting_fact",
                )
                task_payload.update(
                    {
                        "executed_mode": executed_mode,
                        "fallback_triggered": fallback_triggered,
                        "result_count": len(blocks),
                    }
                )
                supporting_query_tasks.append(task_payload)
            (
                ranked_results,
                structured_context_summary,
            ) = self._attach_structural_context(
                case_id=scope.case_id,
                ranked_results=ranked_results,
            )
            plan_payload = plan.to_payload()
            plan_payload.update(
                {
                    "per_query_candidate_top_k": per_query_top_k,
                    "final_top_k": bounded_top_k,
                    "query_tasks": query_tasks,
                    "fact_slot_query_tasks": fact_slot_query_tasks,
                    "supporting_query_tasks": (
                        supporting_query_tasks
                    ),
                    "routing_summary": _routing_summary(query_tasks),
                    "fact_slot_routing_summary": (
                        _routing_summary(fact_slot_query_tasks)
                    ),
                    "supporting_routing_summary": (
                        _routing_summary(supporting_query_tasks)
                    ),
                    "structured_context_summary": (
                        structured_context_summary
                    ),
                }
            )
            planned_results = merge_planned_results(
                plan=plan,
                ranked_results=ranked_results,
                top_k=bounded_top_k,
            )
            adjacent_candidates, adjacent_summary = (
                self._adjacent_candidates(
                    case_id=scope.case_id,
                    ranked_results=ranked_results,
                    planned_results=planned_results,
                )
            )
            if adjacent_candidates:
                planned_results = merge_planned_results(
                    plan=plan,
                    ranked_results=ranked_results,
                    top_k=bounded_top_k,
                    supplemental_blocks=adjacent_candidates,
                )
            plan_payload["adjacent_expansion_summary"] = (
                adjacent_summary
            )
            context_groups, context_group_summary = (
                self._context_evidence_groups(
                    case_id=scope.case_id,
                    plan=plan,
                    ranked_results=ranked_results,
                    planned_results=planned_results,
                )
            )
            (
                structured_sibling_groups,
                structured_sibling_summary,
            ) = self._structured_sibling_context_groups(
                plan=plan,
                ranked_results=ranked_results,
                planned_results=planned_results,
            )
            if self._enable_structured_context_groups:
                context_groups = structured_sibling_groups
            (
                retry_ranked_results,
                retry_query_tasks,
                controlled_retry_summary,
            ) = self._controlled_second_round(
                case_id=scope.case_id,
                plan=plan,
                ranked_results=ranked_results,
                planned_results=planned_results,
                context_groups=context_groups,
                per_query_top_k=per_query_top_k,
            )
            if retry_ranked_results:
                (
                    retry_ranked_results,
                    retry_structural_summary,
                ) = self._attach_structural_context(
                    case_id=scope.case_id,
                    ranked_results=retry_ranked_results,
                )
                controlled_retry_summary[
                    "retry_structural_context_summary"
                ] = retry_structural_summary
                ranked_results = [
                    *ranked_results,
                    *retry_ranked_results,
                ]
                preferred_retry_parent_ids = (
                    _first_tabular_parent_ids(
                        retry_ranked_results
                    )
                )
                controlled_retry_summary[
                    "preferred_parent_count"
                ] = len(preferred_retry_parent_ids)
                (
                    structured_sibling_groups,
                    structured_sibling_summary,
                ) = self._structured_sibling_context_groups(
                    plan=plan,
                    ranked_results=ranked_results,
                    planned_results=planned_results,
                    preferred_parent_ids=(
                        preferred_retry_parent_ids
                    ),
                )
                if self._enable_structured_context_groups:
                    context_groups = structured_sibling_groups
                final_covered_need_indexes = (
                    _covered_need_indexes(
                        planned_results=planned_results,
                        context_groups=context_groups,
                    )
                )
                controlled_retry_summary[
                    "final_covered_need_count"
                ] = len(final_covered_need_indexes)
                controlled_retry_summary[
                    "remaining_uncovered_need_count"
                ] = max(
                    0,
                    len(plan.coverage_need_queries)
                    - len(final_covered_need_indexes),
                )
                controlled_retry_summary[
                    "integration_policy"
                ] = "preserve_first_round_anchors"
            (
                selective_graph_groups,
                selective_graph_summary,
            ) = self._selective_graph_context_groups(
                query=normalized_query,
                case_id=scope.case_id,
                plan=plan,
                planned_results=planned_results,
            )
            if self._enable_selective_graph_expansion:
                context_groups = selective_graph_groups
            plan_payload["context_evidence_group_summary"] = (
                context_group_summary
            )
            plan_payload["structured_sibling_group_summary"] = (
                structured_sibling_summary
            )
            plan_payload["controlled_retry_query_tasks"] = (
                retry_query_tasks
            )
            plan_payload["controlled_retry_routing_summary"] = (
                _routing_summary(retry_query_tasks)
            )
            plan_payload["controlled_retry_summary"] = (
                controlled_retry_summary
            )
            plan_payload["selective_graph_expansion_summary"] = (
                selective_graph_summary
            )
            plan_payload["total_search_query_count"] = (
                len(query_tasks)
                + len(fact_slot_query_tasks)
                + len(supporting_query_tasks)
                + len(retry_query_tasks)
                + int(
                    selective_graph_summary.get(
                        "reference_lookup_count",
                        0,
                    )
                )
            )
            covered_need_indexes = {
                index
                for item in planned_results
                for index in item.coverage_need_indexes
            }
            coverage_selection_summary: dict[str, object] = {
                "enabled": bool(plan.coverage_need_queries),
                "policy": plan.coverage_selection_policy,
                "need_count": len(plan.coverage_need_queries),
                "covered_need_count": len(covered_need_indexes),
                "selected_evidence_count": sum(
                    1
                    for item in planned_results
                    if item.selected_by_coverage
                ),
                "protected_baseline_anchor_count": sum(
                    1
                    for item in planned_results
                    if item.protected_baseline_anchor
                ),
                "promoted_evidence_count": sum(
                    1
                    for item in planned_results
                    if item.promoted_by_coverage
                ),
            }
            if (
                plan.coverage_selection_policy
                == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
            ):
                promoted_items = sorted(
                    (
                        item
                        for item in planned_results
                        if item.promoted_by_coverage
                    ),
                    key=lambda item: (
                        item.promotion_sequence
                        if item.promotion_sequence is not None
                        else 10**9
                    ),
                )
                final_covered_count = len(covered_need_indexes)
                baseline_covered_count = (
                    promoted_items[0].coverage_before_count
                    if promoted_items
                    and promoted_items[0].coverage_before_count
                    is not None
                    else final_covered_count
                )
                baseline_covered_need_indexes = (
                    list(
                        promoted_items[
                            0
                        ].coverage_before_need_indexes
                    )
                    if promoted_items
                    else sorted(covered_need_indexes)
                )
                decision_reason_codes = (
                    ["unsupported_relation_shape"]
                    if not plan.coverage_relation_shape_supported
                    else (
                        ["positive_net_gain_promotion"]
                        if promoted_items
                        else ["no_positive_net_gain"]
                    )
                )
                coverage_selection_summary.update(
                    {
                        "enabled": True,
                        "relation_shape_supported": bool(
                            plan.coverage_relation_shape_supported
                        ),
                        "relation_shape_reason": (
                            plan.coverage_relation_shape_reason
                        ),
                        "baseline_covered_need_count": (
                            baseline_covered_count
                        ),
                        "baseline_covered_need_indexes": (
                            baseline_covered_need_indexes
                        ),
                        "final_covered_need_count": (
                            final_covered_count
                        ),
                        "final_covered_need_indexes": sorted(
                            covered_need_indexes
                        ),
                        "total_net_coverage_gain": sum(
                            item.net_coverage_gain
                            for item in promoted_items
                        ),
                        "query_diversity_preserved": all(
                            item.query_diversity_preserved is True
                            for item in promoted_items
                        ),
                        "max_promotions_per_case": 2,
                        "decision_reason_codes": (
                            decision_reason_codes
                        ),
                        "promotion_audits": [
                            {
                                "promoted_evidence_id": (
                                    item.block.evidence_id
                                ),
                                "replacement_evidence_id": (
                                    item.replacement_evidence_id
                                ),
                                "replacement_position": (
                                    item.replacement_position
                                ),
                                "promotion_sequence": (
                                    item.promotion_sequence
                                ),
                                "coverage_before_count": (
                                    item.coverage_before_count
                                ),
                                "coverage_after_count": (
                                    item.coverage_after_count
                                ),
                                "coverage_before_need_indexes": list(
                                    item.coverage_before_need_indexes
                                ),
                                "coverage_after_need_indexes": list(
                                    item.coverage_after_need_indexes
                                ),
                                "added_need_indexes": list(
                                    item.added_need_indexes
                                ),
                                "net_coverage_gain": (
                                    item.net_coverage_gain
                                ),
                                "victim_exclusive_need_indexes": list(
                                    item.victim_exclusive_need_indexes
                                ),
                                "query_diversity_preserved": (
                                    item.query_diversity_preserved
                                ),
                            }
                            for item in promoted_items
                        ],
                    }
                )
            plan_payload["coverage_selection_summary"] = (
                coverage_selection_summary
            )
            sufficiency_matches_by_evidence: dict[
                str,
                tuple[CoverageNeedMatch, ...],
            ] = {}
            if plan.sufficiency_strategy:
                for item in planned_results:
                    sufficiency_matches_by_evidence[
                        item.block.evidence_id
                    ] = match_block_sufficiency_needs(
                        plan=plan,
                        block=item.block,
                        rrf_score=item.rrf_score,
                    )
                for members in context_groups.values():
                    for member in members:
                        sufficiency_matches_by_evidence.setdefault(
                            member.block.evidence_id,
                            match_block_sufficiency_needs(
                                plan=plan,
                                block=member.block,
                            ),
                        )
            sufficiency_covered_indexes = {
                match.need_index
                for matches_for_evidence
                in sufficiency_matches_by_evidence.values()
                for match in matches_for_evidence
            }
            sufficiency_need_count = len(
                plan.sufficiency_need_queries
            )
            if not plan.sufficiency_strategy:
                sufficiency_status = "not_assessed"
                sufficiency_reason_codes = ["feature_disabled"]
            elif not plan.sufficiency_relation_shape_supported:
                sufficiency_status = "not_assessed"
                sufficiency_reason_codes = [
                    "unsupported_relation_shape"
                ]
            elif (
                sufficiency_need_count > 0
                and len(sufficiency_covered_indexes)
                == sufficiency_need_count
            ):
                sufficiency_status = "candidate_sufficient"
                sufficiency_reason_codes = [
                    "all_relation_needs_directly_covered"
                ]
            else:
                sufficiency_status = "insufficient"
                sufficiency_reason_codes = [
                    (
                        "no_relation_need_directly_covered"
                        if not sufficiency_covered_indexes
                        else "partial_relation_need_coverage"
                    )
                ]
            plan_payload["evidence_sufficiency_summary"] = {
                "enabled": bool(plan.sufficiency_strategy),
                "strategy": plan.sufficiency_strategy,
                "relation_shape_supported": bool(
                    plan.sufficiency_relation_shape_supported
                ),
                "relation_shape_reason": (
                    plan.sufficiency_relation_shape_reason
                ),
                "required_need_count": sufficiency_need_count,
                "covered_need_count": len(
                    sufficiency_covered_indexes
                ),
                "covered_need_indexes": sorted(
                    sufficiency_covered_indexes
                ),
                "sufficiency_status": sufficiency_status,
                "decision_reason_codes": sufficiency_reason_codes,
                "changes_result_selection": False,
                "additional_search_query_count": 0,
            }
            if not planned_results:
                return ToolEnvelope(
                    status=ResultStatus.NO_RESULT,
                    data={
                        "query": normalized_query,
                        "query_plan": plan_payload,
                        "matches": [],
                    },
                    retryable=False,
                    trace_id=trace_id,
                    message="no evidence matched the query in the scoped case",
                )
            matches = []
            for rank, item in enumerate(planned_results, start=1):
                group_members = context_groups.get(
                    item.block.evidence_id,
                    (),
                )
                sufficiency_matches = (
                    sufficiency_matches_by_evidence.get(
                        item.block.evidence_id,
                        (),
                    )
                )
                matches.append(
                    {
                    "score_rank": rank,
                    "query_rrf_score": item.rrf_score,
                    "matched_queries": list(item.matched_queries),
                    "coverage_need_indexes": list(
                        item.coverage_need_indexes
                    ),
                    "coverage_need_types": list(
                        item.coverage_need_types
                    ),
                    "sufficiency_need_indexes": [
                        match.need_index
                        for match in sufficiency_matches
                    ],
                    "sufficiency_need_types": [
                        match.need_type
                        for match in sufficiency_matches
                    ],
                    "selected_by_coverage": (
                        item.selected_by_coverage
                    ),
                    "protected_baseline_anchor": (
                        item.protected_baseline_anchor
                    ),
                    "promoted_by_coverage": (
                        item.promoted_by_coverage
                    ),
                    "candidate_sources": list(
                        item.candidate_sources
                    ),
                    **(
                        {
                            "replacement_evidence_id": (
                                item.replacement_evidence_id
                            ),
                            "replacement_position": (
                                item.replacement_position
                            ),
                            "promotion_sequence": (
                                item.promotion_sequence
                            ),
                            "net_coverage_gain": (
                                item.net_coverage_gain
                            ),
                            "coverage_before_count": (
                                item.coverage_before_count
                            ),
                            "coverage_after_count": (
                                item.coverage_after_count
                            ),
                            "coverage_before_need_indexes": list(
                                item.coverage_before_need_indexes
                            ),
                            "coverage_after_need_indexes": list(
                                item.coverage_after_need_indexes
                            ),
                            "added_need_indexes": list(
                                item.added_need_indexes
                            ),
                            "victim_exclusive_need_indexes": list(
                                item.victim_exclusive_need_indexes
                            ),
                            "query_diversity_preserved": (
                                item.query_diversity_preserved
                            ),
                        }
                        if plan.coverage_selection_policy
                        == PREDICATE_AWARE_MARGINAL_GAIN_POLICY
                        else {}
                    ),
                    "context_evidence_group": {
                        "schema_version": (
                            "tender-evidence-context-group/v1"
                        ),
                        "anchor_evidence_id": (
                            item.block.evidence_id
                        ),
                        "member_count": len(group_members),
                        "members": [
                            {
                                "coverage_need_indexes": [
                                    match.need_index
                                    for match in member.coverage_matches
                                ],
                                "coverage_need_types": [
                                    match.need_type
                                    for match in member.coverage_matches
                                ],
                                "complementary_need_indexes": list(
                                    member.complementary_need_indexes
                                ),
                                "sufficiency_need_indexes": [
                                    match.need_index
                                    for match in (
                                        sufficiency_matches_by_evidence.get(
                                            member.block.evidence_id,
                                            (),
                                        )
                                    )
                                ],
                                "sufficiency_need_types": [
                                    match.need_type
                                    for match in (
                                        sufficiency_matches_by_evidence.get(
                                            member.block.evidence_id,
                                            (),
                                        )
                                    )
                                ],
                                "graph_path": member.graph_path,
                                "max_coverage_score": max(
                                    (
                                        match.score
                                        for match
                                        in member.coverage_matches
                                    ),
                                    default=0.0,
                                ),
                                "excerpt": _excerpt(
                                    member.block.content
                                ),
                                "evidence_ref": (
                                    member.block.to_ref(
                                        context_read=False,
                                        quote=_excerpt(
                                            member.block.content,
                                            limit=300,
                                        ),
                                    ).model_dump(mode="json")
                                ),
                            }
                            for member in group_members
                        ],
                    },
                    "structural_context_group": {
                        "schema_version": (
                            "tender-evidence-structural-context-group/v1"
                        ),
                        "anchor_evidence_id": (
                            item.block.evidence_id
                        ),
                        "member_count": len(
                            item.block.structural_context
                        ),
                        "members": [
                            {
                                "relation": parent.relation,
                                "excerpt": _excerpt(parent.content),
                                "evidence_ref": (
                                    parent.evidence_ref.model_dump(
                                        mode="json"
                                    )
                                ),
                            }
                            for parent in item.block.structural_context
                        ],
                    },
                    "excerpt": _excerpt(item.block.content),
                    "evidence_ref": item.block.to_ref(
                        context_read=False,
                        quote=_excerpt(item.block.content, limit=300),
                    ).model_dump(mode="json"),
                    }
                )
            return ToolEnvelope(
                status=ResultStatus.OK,
                data={
                    "query": normalized_query,
                    "query_plan": plan_payload,
                    "matches": matches,
                },
                trace_id=trace_id,
            )
        except TenderCaseNotFoundError:
            return _failed(
                trace_id,
                error_code="scoped_case_not_found",
                message="the scoped tender case is unavailable",
            )
        except TenderEvidenceRepositoryError:
            return _failed(
                trace_id,
                error_code="repository_error",
                message="tender evidence repository is unavailable",
                retryable=True,
            )

    def _attach_structural_context(
        self,
        *,
        case_id: str,
        ranked_results: Sequence[
            tuple[str, Sequence[EvidenceBlock]]
        ],
    ) -> tuple[
        list[tuple[str, list[EvidenceBlock]]],
        dict[str, object],
    ]:
        summary: dict[str, object] = {
            "enabled": self._enable_structured_context_groups,
            "candidate_count": 0,
            "contextualized_candidate_count": 0,
            "parent_count": 0,
            "section_parent_count": 0,
            "table_header_parent_count": 0,
            "sheet_header_parent_count": 0,
            "lookup_count": 0,
            "error_count": 0,
        }
        copied = [
            (query, list(blocks))
            for query, blocks in ranked_results
        ]
        if not self._enable_structured_context_groups:
            return copied, summary
        evidence_ids = list(
            dict.fromkeys(
                block.evidence_id
                for _, blocks in copied
                for block in blocks
            )
        )
        summary["candidate_count"] = len(evidence_ids)
        if not evidence_ids:
            return copied, summary
        try:
            summary["lookup_count"] = 1
            contexts = self._repository.get_structural_context(
                case_id=case_id,
                evidence_ids=evidence_ids,
                max_heading_lookback=12,
            )
        except TenderEvidenceRepositoryError:
            summary["error_count"] = 1
            return copied, summary

        contextualized_ids: set[str] = set()
        relation_counts = {
            "section_parent": 0,
            "table_header_parent": 0,
            "sheet_header_parent": 0,
        }
        enriched: list[tuple[str, list[EvidenceBlock]]] = []
        for query, blocks in copied:
            enriched_blocks: list[EvidenceBlock] = []
            for block in blocks:
                parents = contexts.get(block.evidence_id, [])
                if parents:
                    contextualized_ids.add(block.evidence_id)
                    for parent in parents:
                        relation_counts[parent.relation] += 1
                    block = block.model_copy(
                        update={
                            "structural_context": [
                                item.model_copy(deep=True)
                                for item in parents
                            ]
                        },
                        deep=True,
                    )
                enriched_blocks.append(block)
            enriched.append((query, enriched_blocks))
        summary["contextualized_candidate_count"] = len(
            contextualized_ids
        )
        summary["parent_count"] = sum(relation_counts.values())
        for relation, count in relation_counts.items():
            summary[f"{relation}_count"] = count
        return enriched, summary

    def _structured_sibling_context_groups(
        self,
        *,
        plan: TenderQueryPlan,
        ranked_results: Sequence[
            tuple[str, Sequence[EvidenceBlock]]
        ],
        planned_results: Sequence[PlannedEvidenceResult],
        preferred_parent_ids: set[str] | None = None,
    ) -> tuple[
        dict[str, tuple[_ContextGroupMember, ...]],
        dict[str, object],
    ]:
        summary: dict[str, object] = {
            "enabled": self._enable_structured_context_groups,
            "eligible_parent_count": 0,
            "selected_parent_count": 0,
            "member_count": 0,
            "max_parent_groups": 1,
            "max_members_per_group": 3,
            "candidate_pool_only": True,
            "additional_retrieval_query_count": 0,
            "preferred_parent_count": len(
                preferred_parent_ids or set()
            ),
            "preferred_parent_applied": False,
        }
        if (
            not self._enable_structured_context_groups
            or not plan.coverage_need_queries
        ):
            return {}, summary

        ranked_candidates: list[EvidenceBlock] = []
        candidate_rank: dict[str, int] = {}
        for _, blocks in ranked_results:
            for block in blocks:
                if block.evidence_id in candidate_rank:
                    continue
                candidate_rank[block.evidence_id] = (
                    len(candidate_rank) + 1
                )
                ranked_candidates.append(block)
        final_ids = {
            item.block.evidence_id for item in planned_results
        }
        planned_rank = {
            item.block.evidence_id: index
            for index, item in enumerate(planned_results)
        }
        candidates_by_parent: dict[str, list[EvidenceBlock]] = {}
        parent_relation: dict[str, str] = {}
        for block in ranked_candidates:
            parent = _tabular_parent(block)
            if parent is None:
                continue
            parent_id, relation = parent
            candidates_by_parent.setdefault(parent_id, []).append(
                block
            )
            parent_relation[parent_id] = relation

        eligible: list[
            tuple[
                int,
                float,
                int,
                str,
                PlannedEvidenceResult,
                tuple[_ContextGroupMember, ...],
            ]
        ] = []
        for parent_id, candidates in candidates_by_parent.items():
            if (
                preferred_parent_ids
                and parent_id not in preferred_parent_ids
            ):
                continue
            anchors = [
                item
                for item in planned_results
                if _tabular_parent_id(item.block) == parent_id
            ]
            if not anchors:
                continue
            best_anchor = min(
                anchors,
                key=lambda item: planned_rank[item.block.evidence_id],
            )
            matches_by_id = {
                block.evidence_id: match_block_coverage_needs(
                    plan=plan,
                    block=block,
                    require_answer_signal=True,
                )
                for block in candidates
            }
            best_by_need: dict[
                int,
                tuple[float, int, EvidenceBlock, CoverageNeedMatch],
            ] = {}
            for block in candidates:
                for match in matches_by_id[block.evidence_id]:
                    candidate = (
                        match.score,
                        -candidate_rank[block.evidence_id],
                        block,
                        match,
                    )
                    current = best_by_need.get(match.need_index)
                    if current is None or candidate[:2] > current[:2]:
                        best_by_need[match.need_index] = candidate
            if not best_by_need:
                continue

            member_blocks: list[EvidenceBlock] = []
            for need_index in sorted(best_by_need):
                block = best_by_need[need_index][2]
                if (
                    block.evidence_id in final_ids
                    or block.evidence_id
                    in {
                        item.evidence_id
                        for item in member_blocks
                    }
                ):
                    continue
                member_blocks.append(block)
                if len(member_blocks) >= 3:
                    break
            if not member_blocks:
                continue
            members = tuple(
                _ContextGroupMember(
                    block=block,
                    coverage_matches=matches_by_id[
                        block.evidence_id
                    ],
                    complementary_need_indexes=tuple(
                        sorted(
                            {
                                match.need_index
                                for match in matches_by_id[
                                    block.evidence_id
                                ]
                            }.difference(
                                best_anchor.coverage_need_indexes
                            )
                        )
                    ),
                )
                for block in member_blocks
            )
            eligible.append(
                (
                    len(best_by_need),
                    sum(
                        value[0] for value in best_by_need.values()
                    ),
                    -planned_rank[best_anchor.block.evidence_id],
                    parent_id,
                    best_anchor,
                    members,
                )
            )
        summary["eligible_parent_count"] = len(eligible)
        if not eligible:
            return {}, summary
        eligible.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2],
                item[3],
            )
        )
        selected = eligible[0]
        anchor = selected[4]
        members = selected[5]
        summary["selected_parent_count"] = 1
        summary["member_count"] = len(members)
        summary["selected_parent_relation"] = parent_relation.get(
            selected[3]
        )
        summary["preferred_parent_applied"] = bool(
            preferred_parent_ids
            and selected[3] in preferred_parent_ids
        )
        return {
            anchor.block.evidence_id: members
        }, summary

    def _controlled_second_round(
        self,
        *,
        case_id: str,
        plan: TenderQueryPlan,
        ranked_results: Sequence[
            tuple[str, Sequence[EvidenceBlock]]
        ],
        planned_results: Sequence[PlannedEvidenceResult],
        context_groups: dict[
            str,
            tuple[_ContextGroupMember, ...],
        ],
        per_query_top_k: int,
    ) -> tuple[
        list[tuple[str, list[EvidenceBlock]]],
        list[dict[str, object]],
        dict[str, object],
    ]:
        need_count = len(plan.coverage_need_queries)
        covered_need_indexes = _covered_need_indexes(
            planned_results=planned_results,
            context_groups=context_groups,
        )
        summary: dict[str, object] = {
            "enabled": self._enable_controlled_second_round,
            "triggered": False,
            "trigger_policy": "partial_coverage_only",
            "max_round_count": 1,
            "max_retry_query_count": 2,
            "required_need_count": need_count,
            "initial_covered_need_count": len(
                covered_need_indexes
            ),
            "initial_uncovered_need_count": max(
                0,
                need_count - len(covered_need_indexes),
            ),
            "executed_retry_query_count": 0,
            "new_candidate_count": 0,
            "final_covered_need_count": len(
                covered_need_indexes
            ),
            "remaining_uncovered_need_count": max(
                0,
                need_count - len(covered_need_indexes),
            ),
            "skip_reason": None,
            "integration_policy": (
                "preserve_first_round_anchors"
            ),
        }
        if not self._enable_controlled_second_round:
            summary["skip_reason"] = "feature_disabled"
            return [], [], summary
        if need_count == 0:
            summary["skip_reason"] = "no_assessable_fact_needs"
            return [], [], summary
        if not covered_need_indexes:
            summary["skip_reason"] = "zero_coverage_safety_guard"
            return [], [], summary
        if len(covered_need_indexes) >= need_count:
            summary["skip_reason"] = "already_candidate_sufficient"
            return [], [], summary

        existing_queries = {
            str(query).strip()
            for query in (
                *plan.queries,
                *plan.fact_slot_queries,
                *plan.supporting_queries,
            )
            if str(query).strip()
        }
        retry_queries: list[tuple[int, str, str]] = []
        seen_queries: set[str] = set()
        for need_index, (need_query, need_type) in enumerate(
            zip(
                plan.coverage_need_queries,
                plan.coverage_need_types,
            )
        ):
            normalized = str(need_query or "").strip()
            if (
                need_index in covered_need_indexes
                or not normalized
                or normalized in existing_queries
                or normalized in seen_queries
            ):
                continue
            retry_queries.append(
                (need_index, normalized, need_type)
            )
            seen_queries.add(normalized)
            if len(retry_queries) >= 2:
                break
        if not retry_queries:
            summary["skip_reason"] = "no_distinct_uncovered_query"
            return [], [], summary

        initial_candidate_ids = {
            block.evidence_id
            for _, blocks in ranked_results
            for block in blocks
        }
        retry_ranked_results: list[
            tuple[str, list[EvidenceBlock]]
        ] = []
        retry_query_tasks: list[dict[str, object]] = []
        retry_candidate_ids: set[str] = set()
        for retry_index, (
            need_index,
            retry_query,
            need_type,
        ) in enumerate(retry_queries, start=1):
            route = route_tender_query(retry_query)
            blocks = self._repository.search(
                case_id=case_id,
                query=retry_query,
                top_k=per_query_top_k,
                search_mode=route.mode,
            )
            executed_mode = route.mode
            fallback_triggered = False
            if not blocks and route.fallback_mode is not None:
                fallback_triggered = True
                executed_mode = route.fallback_mode
                blocks = self._repository.search(
                    case_id=case_id,
                    query=retry_query,
                    top_k=per_query_top_k,
                    search_mode=route.fallback_mode,
                )
            retry_ranked_results.append(
                (retry_query, list(blocks))
            )
            retry_candidate_ids.update(
                block.evidence_id for block in blocks
            )
            task_payload = route.to_payload(
                query_id=f"retry{retry_index}",
                query_kind="uncovered_fact_retry",
            )
            task_payload.update(
                {
                    "coverage_need_index": need_index,
                    "coverage_need_type": need_type,
                    "executed_mode": executed_mode,
                    "fallback_triggered": fallback_triggered,
                    "result_count": len(blocks),
                }
            )
            retry_query_tasks.append(task_payload)

        summary["triggered"] = True
        summary["skip_reason"] = None
        summary["executed_retry_query_count"] = len(
            retry_query_tasks
        )
        summary["new_candidate_count"] = len(
            retry_candidate_ids.difference(initial_candidate_ids)
        )
        return (
            retry_ranked_results,
            retry_query_tasks,
            summary,
        )

    def _adjacent_candidates(
        self,
        *,
        case_id: str,
        ranked_results: Sequence[
            tuple[str, Sequence[EvidenceBlock]]
        ],
        planned_results: Sequence[PlannedEvidenceResult],
    ) -> tuple[list[EvidenceBlock], dict[str, object]]:
        summary: dict[str, object] = {
            "enabled": self._enable_adjacent_candidate_expansion,
            "seed_count": 0,
            "context_read_count": 0,
            "context_block_count": 0,
            "added_candidate_count": 0,
            "existing_candidate_count": 0,
            "filtered_document_count": 0,
            "filtered_section_count": 0,
            "filtered_non_direct_count": 0,
            "error_count": 0,
        }
        if not self._enable_adjacent_candidate_expansion:
            return [], summary

        seeds = [
            item
            for item in planned_results
            if item.selected_by_coverage
        ][:5]
        summary["seed_count"] = len(seeds)
        if not seeds:
            return [], summary

        primary_ids = {
            block.evidence_id
            for _, blocks in ranked_results
            for block in blocks
        }
        added_ids: set[str] = set()
        adjacent_candidates: list[EvidenceBlock] = []
        for seed in seeds:
            try:
                summary["context_read_count"] = (
                    int(summary["context_read_count"]) + 1
                )
                context_blocks = self._repository.get_context(
                    case_id=case_id,
                    evidence_id=seed.block.evidence_id,
                    before_blocks=1,
                    after_blocks=1,
                )
            except TenderEvidenceRepositoryError:
                summary["error_count"] = (
                    int(summary["error_count"]) + 1
                )
                continue
            summary["context_block_count"] = (
                int(summary["context_block_count"])
                + len(context_blocks)
            )
            for candidate in context_blocks:
                if candidate.evidence_id == seed.block.evidence_id:
                    continue
                if (
                    candidate.document_id != seed.block.document_id
                    or candidate.document_version
                    != seed.block.document_version
                ):
                    summary["filtered_document_count"] = (
                        int(summary["filtered_document_count"]) + 1
                    )
                    continue
                if (
                    abs(
                        candidate.block_order
                        - seed.block.block_order
                    )
                    != 1
                ):
                    summary["filtered_non_direct_count"] = (
                        int(summary["filtered_non_direct_count"]) + 1
                    )
                    continue
                if _normalized_section(candidate) != _normalized_section(
                    seed.block
                ):
                    summary["filtered_section_count"] = (
                        int(summary["filtered_section_count"]) + 1
                    )
                    continue
                if candidate.evidence_id in primary_ids:
                    summary["existing_candidate_count"] = (
                        int(summary["existing_candidate_count"]) + 1
                    )
                    continue
                if candidate.evidence_id in added_ids:
                    continue
                added_ids.add(candidate.evidence_id)
                adjacent_candidates.append(candidate)
        summary["added_candidate_count"] = len(adjacent_candidates)
        return adjacent_candidates, summary

    def _context_evidence_groups(
        self,
        *,
        case_id: str,
        plan: TenderQueryPlan,
        ranked_results: Sequence[
            tuple[str, Sequence[EvidenceBlock]]
        ],
        planned_results: Sequence[PlannedEvidenceResult],
    ) -> tuple[
        dict[str, tuple[_ContextGroupMember, ...]],
        dict[str, object],
    ]:
        summary: dict[str, object] = {
            "enabled": self._enable_context_evidence_groups,
            "anchor_count": len(planned_results),
            "seed_count": 0,
            "context_read_count": 0,
            "context_block_count": 0,
            "grouped_anchor_count": 0,
            "member_count": 0,
            "existing_candidate_count": 0,
            "filtered_document_count": 0,
            "filtered_section_count": 0,
            "filtered_non_direct_count": 0,
            "filtered_no_coverage_count": 0,
            "error_count": 0,
            "max_members_per_anchor": 1,
        }
        if not self._enable_context_evidence_groups:
            return {}, summary

        seeds = [
            item
            for item in planned_results
            if item.selected_by_coverage
        ][:5]
        summary["seed_count"] = len(seeds)
        if not seeds:
            return {}, summary

        primary_ids = {
            block.evidence_id
            for _, blocks in ranked_results
            for block in blocks
        }
        attached_ids: set[str] = set()
        groups: dict[str, tuple[_ContextGroupMember, ...]] = {}
        for seed in seeds:
            try:
                summary["context_read_count"] = (
                    int(summary["context_read_count"]) + 1
                )
                context_blocks = self._repository.get_context(
                    case_id=case_id,
                    evidence_id=seed.block.evidence_id,
                    before_blocks=1,
                    after_blocks=1,
                )
            except TenderEvidenceRepositoryError:
                summary["error_count"] = (
                    int(summary["error_count"]) + 1
                )
                continue
            summary["context_block_count"] = (
                int(summary["context_block_count"])
                + len(context_blocks)
            )
            candidates: list[
                tuple[
                    int,
                    int,
                    float,
                    int,
                    _ContextGroupMember,
                ]
            ] = []
            for candidate in context_blocks:
                if candidate.evidence_id == seed.block.evidence_id:
                    continue
                if (
                    candidate.document_id != seed.block.document_id
                    or candidate.document_version
                    != seed.block.document_version
                ):
                    summary["filtered_document_count"] = (
                        int(summary["filtered_document_count"]) + 1
                    )
                    continue
                if (
                    abs(
                        candidate.block_order
                        - seed.block.block_order
                    )
                    != 1
                ):
                    summary["filtered_non_direct_count"] = (
                        int(summary["filtered_non_direct_count"]) + 1
                    )
                    continue
                if _normalized_section(candidate) != _normalized_section(
                    seed.block
                ):
                    summary["filtered_section_count"] = (
                        int(summary["filtered_section_count"]) + 1
                    )
                    continue
                if candidate.evidence_id in primary_ids:
                    summary["existing_candidate_count"] = (
                        int(summary["existing_candidate_count"]) + 1
                    )
                    continue
                if candidate.evidence_id in attached_ids:
                    continue
                coverage_matches = match_block_coverage_needs(
                    plan=plan,
                    block=candidate,
                    adjacent_context=True,
                    require_answer_signal=False,
                )
                if not coverage_matches:
                    summary["filtered_no_coverage_count"] = (
                        int(summary["filtered_no_coverage_count"]) + 1
                    )
                    continue
                complementary_need_indexes = tuple(
                    sorted(
                        {
                            match.need_index
                            for match in coverage_matches
                        }.difference(seed.coverage_need_indexes)
                    )
                )
                member = _ContextGroupMember(
                    block=candidate,
                    coverage_matches=coverage_matches,
                    complementary_need_indexes=(
                        complementary_need_indexes
                    ),
                )
                candidates.append(
                    (
                        int(bool(complementary_need_indexes)),
                        len(complementary_need_indexes),
                        max(
                            match.score
                            for match in coverage_matches
                        ),
                        -candidate.block_order,
                        member,
                    )
                )
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    -item[0],
                    -item[1],
                    -item[2],
                    -item[3],
                    item[4].block.evidence_id,
                )
            )
            selected_member = candidates[0][4]
            attached_ids.add(selected_member.block.evidence_id)
            groups[seed.block.evidence_id] = (selected_member,)

        summary["grouped_anchor_count"] = len(groups)
        summary["member_count"] = sum(
            len(members) for members in groups.values()
        )
        return groups, summary

    def _selective_graph_context_groups(
        self,
        *,
        query: str,
        case_id: str,
        plan: TenderQueryPlan,
        planned_results: Sequence[PlannedEvidenceResult],
    ) -> tuple[
        dict[str, tuple[_ContextGroupMember, ...]],
        dict[str, object],
    ]:
        decision = decide_graph_trigger(query)
        summary: dict[str, object] = {
            "enabled": self._enable_selective_graph_expansion,
            **decision.to_payload(),
            "graph_call_count": 0,
            "max_hops": 1,
            "max_seed_anchors": 2,
            "max_expanded_evidence": 4,
            "seed_count": 0,
            "context_read_count": 0,
            "reference_lookup_count": 0,
            "unresolvable_reference_count": 0,
            "resolved_reference_count": 0,
            "missing_reference_target_count": 0,
            "grouped_anchor_count": 0,
            "expanded_evidence_count": 0,
            "path_count": 0,
            "edge_type_counts": {},
            "filtered_existing_count": 0,
            "filtered_unverified_count": 0,
            "error_count": 0,
            "additional_llm_tokens": 0,
            "skip_reason": None,
            "paths": [],
        }
        if not self._enable_selective_graph_expansion:
            summary["skip_reason"] = "feature_disabled"
            return {}, summary
        if not decision.triggered:
            summary["skip_reason"] = "trigger_contract_not_satisfied"
            return {}, summary

        summary["graph_call_count"] = 1
        try:
            manifest = self._repository.get_manifest(case_id=case_id)
        except TenderEvidenceRepositoryError:
            summary["error_count"] = 1
            summary["skip_reason"] = "manifest_unavailable"
            return {}, summary
        document_role_by_id = {
            item.document_id: item.document_type
            for item in manifest.documents
            if item.active and item.parse_status != "failed"
        }
        document_role_by_key = {
            item.document_key: item.document_type
            for item in manifest.documents
            if item.active and item.parse_status != "failed"
        }

        seed_specs: list[
            tuple[
                PlannedEvidenceResult,
                bool,
                tuple[VerifiedExactReference, ...],
            ]
        ] = []
        for item in planned_results:
            structural = is_verified_table_parent_seed(item.block)
            references = extract_verified_exact_references(
                item.block.content
            )
            if not structural and not references:
                continue
            seed_specs.append((item, structural, references))
            if len(seed_specs) >= 2:
                break
        summary["seed_count"] = len(seed_specs)
        if not seed_specs:
            summary["skip_reason"] = "no_verifiable_seed"
            return {}, summary

        anchor_ids = {
            item.block.evidence_id for item in planned_results
        }
        attached_ids: set[str] = set()
        groups: dict[str, list[_ContextGroupMember]] = {}
        paths: list[dict[str, object]] = []
        edge_counts: dict[str, int] = {}

        def attach(
            *,
            seed: PlannedEvidenceResult,
            target: EvidenceBlock,
            edge_type: str,
            reference_type: str,
        ) -> None:
            if len(attached_ids) >= 4:
                return
            if (
                target.evidence_id in anchor_ids
                or target.evidence_id in attached_ids
            ):
                summary["filtered_existing_count"] = int(
                    summary["filtered_existing_count"]
                ) + 1
                return
            coverage_matches = match_block_coverage_needs(
                plan=plan,
                block=target,
                require_answer_signal=False,
            )
            path = {
                "hop_count": 1,
                "seed_evidence_id": seed.block.evidence_id,
                "edge_type": edge_type,
                "target_evidence_id": target.evidence_id,
                "target_document_role": (
                    document_role_by_id.get(target.document_id)
                    or document_role_by_key.get(target.document_key)
                    or "unknown"
                ),
                "reference_type": reference_type,
                "target_locator": target.locator.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            }
            groups.setdefault(seed.block.evidence_id, []).append(
                _ContextGroupMember(
                    block=target,
                    coverage_matches=coverage_matches,
                    complementary_need_indexes=tuple(
                        sorted(
                            {
                                match.need_index
                                for match in coverage_matches
                            }.difference(
                                seed.coverage_need_indexes
                            )
                        )
                    ),
                    graph_path=path,
                )
            )
            attached_ids.add(target.evidence_id)
            paths.append(path)
            edge_counts[edge_type] = edge_counts.get(edge_type, 0) + 1

        for seed, structural, references in seed_specs:
            if len(attached_ids) >= 4:
                break
            if structural:
                try:
                    summary["context_read_count"] = int(
                        summary["context_read_count"]
                    ) + 1
                    context_blocks = self._repository.get_context(
                        case_id=case_id,
                        evidence_id=seed.block.evidence_id,
                        before_blocks=0,
                        after_blocks=4,
                    )
                except TenderEvidenceRepositoryError:
                    summary["error_count"] = int(
                        summary["error_count"]
                    ) + 1
                    context_blocks = []
                for target in verified_structural_children(
                    seed=seed.block,
                    context_blocks=context_blocks,
                    max_children=4 - len(attached_ids),
                ):
                    attach(
                        seed=seed,
                        target=target,
                        edge_type="child_of_section_or_table",
                        reference_type="verified_table_parent",
                    )
                    if len(attached_ids) >= 4:
                        break

            for reference in references:
                if len(attached_ids) >= 4:
                    break
                if not reference.resolvable:
                    summary["unresolvable_reference_count"] = int(
                        summary["unresolvable_reference_count"]
                    ) + 1
                    continue
                summary["reference_lookup_count"] = int(
                    summary["reference_lookup_count"]
                ) + 1
                try:
                    candidates = self._repository.search(
                        case_id=case_id,
                        query=reference.lookup_query,
                        top_k=20,
                        search_mode="exact",
                    )
                except TenderEvidenceRepositoryError:
                    summary["error_count"] = int(
                        summary["error_count"]
                    ) + 1
                    continue
                target = next(
                    (
                        item
                        for item in candidates
                        if exact_reference_target_matches(
                            reference=reference,
                            target_document_role=(
                                document_role_by_id.get(item.document_id)
                                or document_role_by_key.get(
                                    item.document_key
                                )
                                or "unknown"
                            ),
                            block=item,
                        )
                    ),
                    None,
                )
                if target is None:
                    summary["missing_reference_target_count"] = int(
                        summary["missing_reference_target_count"]
                    ) + 1
                    continue
                summary["resolved_reference_count"] = int(
                    summary["resolved_reference_count"]
                ) + 1
                attach(
                    seed=seed,
                    target=target,
                    edge_type="exactly_references",
                    reference_type=reference.reference_type,
                )

        frozen_groups = {
            anchor_id: tuple(members)
            for anchor_id, members in groups.items()
            if members
        }
        summary["grouped_anchor_count"] = len(frozen_groups)
        summary["expanded_evidence_count"] = len(attached_ids)
        summary["path_count"] = len(paths)
        summary["edge_type_counts"] = edge_counts
        summary["paths"] = paths
        if not attached_ids:
            summary["skip_reason"] = "no_verified_relation_target"
        return frozen_groups, summary

    def read_evidence_context(
        self,
        *,
        evidence_id: str,
        before_blocks: int = 0,
        after_blocks: int = 0,
    ) -> ToolEnvelope:
        trace_id = _trace_id()
        try:
            scope = self._scope_for("read_evidence_context")
            normalized_id = evidence_id.strip()
            if not normalized_id or len(normalized_id) > 160:
                return _failed(
                    trace_id,
                    error_code="invalid_evidence_id",
                    message="evidence_id is invalid",
                )
            bounded_before = min(max(int(before_blocks), 0), 5)
            bounded_after = min(max(int(after_blocks), 0), 5)
            blocks = self._repository.get_context(
                case_id=scope.case_id,
                evidence_id=normalized_id,
                before_blocks=bounded_before,
                after_blocks=bounded_after,
            )
            self._repository.record_context_read(
                case_id=scope.case_id,
                assessment_id=scope.assessment_id,
                agent_run_id=scope.agent_run_id,
                subject=scope.subject,
                evidence_id=normalized_id,
                trace_id=trace_id,
            )
            return ToolEnvelope(
                status=ResultStatus.OK,
                data={
                    "selected_evidence_id": normalized_id,
                    "blocks": [
                        {
                            "content": item.content,
                            "evidence_ref": item.to_ref(
                                context_read=item.evidence_id == normalized_id,
                                quote=_excerpt(item.content, limit=300),
                            ).model_dump(mode="json"),
                        }
                        for item in blocks
                    ],
                },
                trace_id=trace_id,
            )
        except EvidenceNotFoundError:
            return ToolEnvelope(
                status=ResultStatus.NO_RESULT,
                data={"selected_evidence_id": evidence_id, "blocks": []},
                retryable=False,
                trace_id=trace_id,
                error_code="evidence_not_found",
                message="evidence_id was not found in the scoped case",
            )
        except TenderCaseNotFoundError:
            return _failed(
                trace_id,
                error_code="scoped_case_not_found",
                message="the scoped tender case is unavailable",
            )
        except TenderEvidenceRepositoryError:
            return _failed(
                trace_id,
                error_code="repository_error",
                message="tender evidence repository is unavailable",
                retryable=True,
            )

    def compare_document_versions(self, *, document_key: str) -> ToolEnvelope:
        trace_id = _trace_id()
        try:
            scope = self._scope_for("compare_document_versions")
            normalized_key = document_key.strip()
            if not normalized_key or len(normalized_key) > 160:
                return _failed(
                    trace_id,
                    error_code="invalid_document_key",
                    message="document_key is invalid",
                )
            versions, conflicts = self._repository.get_document_versions(
                case_id=scope.case_id,
                document_key=normalized_key,
            )
            if not versions:
                return ToolEnvelope(
                    status=ResultStatus.NO_RESULT,
                    data={
                        "document_key": normalized_key,
                        "versions": [],
                        "conflicts": [],
                    },
                    trace_id=trace_id,
                    message="document_key was not found in the scoped case",
                )
            status = (
                ResultStatus.OK
                if len(versions) >= 2
                else ResultStatus.PARTIAL
            )
            return ToolEnvelope(
                status=status,
                data={
                    "document_key": normalized_key,
                    "versions": versions,
                    "conflicts": [
                        item.model_dump(mode="json") for item in conflicts
                    ],
                },
                retryable=False,
                trace_id=trace_id,
                message=(
                    None
                    if len(versions) >= 2
                    else "only one document version is available"
                ),
            )
        except TenderCaseNotFoundError:
            return _failed(
                trace_id,
                error_code="scoped_case_not_found",
                message="the scoped tender case is unavailable",
            )
        except TenderEvidenceRepositoryError:
            return _failed(
                trace_id,
                error_code="repository_error",
                message="tender evidence repository is unavailable",
                retryable=True,
            )

    def validate_evidence_refs(
        self,
        *,
        refs: Sequence[EvidenceRefInput],
        manifest_version: int,
    ) -> ToolEnvelope:
        trace_id = _trace_id()
        try:
            scope = self._scope_for("validate_evidence_refs")
            if len(refs) > 200:
                return _failed(
                    trace_id,
                    error_code="too_many_evidence_refs",
                    message="at most 200 evidence references can be validated",
                )
            validation = self._repository.validate_refs(
                case_id=scope.case_id,
                refs=refs,
                manifest_version=manifest_version,
            )
            traced_ids = self._repository.get_context_read_ids(
                case_id=scope.case_id,
                assessment_id=scope.assessment_id,
                agent_run_id=scope.agent_run_id,
                evidence_ids=[
                    str(item["evidence_id"])
                    for item in validation
                ],
            )
            for item in validation:
                evidence_id = str(item["evidence_id"])
                item["context_read_traced"] = evidence_id in traced_ids
            invalid_count = sum(not bool(item["valid"]) for item in validation)
            untraced_count = sum(
                not bool(item["context_read_traced"]) for item in validation
            )
            return ToolEnvelope(
                # The validation operation itself completed successfully even
                # when a supplied reference is invalid. Callers must inspect
                # all_valid/results; PARTIAL is reserved for infrastructure
                # returning an incomplete validation set.
                status=ResultStatus.OK,
                data={
                    "manifest_version": manifest_version,
                    "all_valid": invalid_count == 0,
                    "all_context_read": untraced_count == 0,
                    "invalid_count": invalid_count,
                    "untraced_count": untraced_count,
                    "results": validation,
                },
                trace_id=trace_id,
            )
        except TenderCaseNotFoundError:
            return _failed(
                trace_id,
                error_code="scoped_case_not_found",
                message="the scoped tender case is unavailable",
            )
        except TenderEvidenceRepositoryError:
            return _failed(
                trace_id,
                error_code="repository_error",
                message="tender evidence repository is unavailable",
                retryable=True,
            )


def _trace_id() -> str:
    return f"tender-mcp-{uuid.uuid4().hex}"


def _excerpt(content: str, *, limit: int = 500) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _normalized_section(block: EvidenceBlock) -> str:
    return str(block.locator.section or "").strip().casefold()


def _tabular_parent(
    block: EvidenceBlock,
) -> tuple[str, str] | None:
    for parent in block.structural_context:
        if parent.relation not in {
            "table_header_parent",
            "sheet_header_parent",
        }:
            continue
        return (
            parent.evidence_ref.evidence_id,
            parent.relation,
        )
    return None


def _tabular_parent_id(block: EvidenceBlock) -> str | None:
    parent = _tabular_parent(block)
    return parent[0] if parent is not None else None


def _covered_need_indexes(
    *,
    planned_results: Sequence[PlannedEvidenceResult],
    context_groups: dict[
        str,
        tuple[_ContextGroupMember, ...],
    ],
) -> set[int]:
    covered = {
        need_index
        for item in planned_results
        for need_index in item.coverage_need_indexes
    }
    for members in context_groups.values():
        covered.update(
            match.need_index
            for member in members
            for match in member.coverage_matches
        )
    return covered


def _first_tabular_parent_ids(
    ranked_results: Sequence[
        tuple[str, Sequence[EvidenceBlock]]
    ],
) -> set[str]:
    parent_ids: set[str] = set()
    for _, blocks in ranked_results:
        for block in blocks:
            parent_id = _tabular_parent_id(block)
            if parent_id:
                parent_ids.add(parent_id)
                break
    return parent_ids


def _failed(
    trace_id: str,
    *,
    error_code: str,
    message: str,
    retryable: bool = False,
) -> ToolEnvelope:
    return ToolEnvelope(
        status=ResultStatus.FAILED,
        data=None,
        retryable=retryable,
        trace_id=trace_id,
        error_code=error_code,
        message=message,
    )


def _routing_summary(
    query_tasks: Sequence[dict[str, object]],
) -> dict[str, object]:
    requested = {"exact": 0, "semantic": 0, "hybrid": 0}
    executed = {"exact": 0, "semantic": 0, "hybrid": 0}
    fallback_count = 0
    for task in query_tasks:
        requested_mode = str(task.get("requested_mode") or "")
        executed_mode = str(task.get("executed_mode") or "")
        if requested_mode in requested:
            requested[requested_mode] += 1
        if executed_mode in executed:
            executed[executed_mode] += 1
        if bool(task.get("fallback_triggered")):
            fallback_count += 1
    return {
        "requested": requested,
        "executed": executed,
        "fallback_count": fallback_count,
    }
