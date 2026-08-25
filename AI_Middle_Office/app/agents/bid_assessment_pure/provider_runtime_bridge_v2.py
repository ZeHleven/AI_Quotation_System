"""Bridge Provider Boundary V2 to the stable dynamic Action Loop port.

V2 failures never fall back silently.  V2-N makes the accepted next action the
only action authority; a second call may generate only that action's payload.
V2-O keeps the first control decision Tool-free and exposes only the accepted
minimal Tool subset during a separate native Function Calling invocation.
"""

from __future__ import annotations

import json

from .answer_contracts import AnswerDraft, InteractionBlock
from .action_runtime import (
    ActionLoopContractRejected,
    AnswerAction,
    MainAgentActionProvider,
    MainAgentDecisionRequest,
    MainAgentModelActionKind,
    MainAgentModelDecision,
    MainAgentProviderOutcome,
    ToolCallBatchAction,
)
from .provider_orchestration_v2 import (
    ProviderAnswerContextBundle,
    ProviderDecisionAnswerOrchestratorV2,
    ProviderToolCallsOutcomeV2,
)
from .provider_decision_v2 import (
    ProviderNextActionRecoveryConstraintV2,
    ProviderNextActionRecoveryReason,
    RuntimeTerminalAnswerAuthorizationV2,
)
from .provider_ingress_v2 import (
    ProviderBoundaryFailureCode,
    ProviderBoundaryRejected,
)
from .retrieval_convergence_v2 import (
    RetrievalConvergenceDecisionV2,
    RetrievalConvergenceGateV2,
    tool_batch_observation,
)
from .runtime import (
    ContextAssemblyResult,
    ContextEntryKind,
    ContextExcludedEntry,
    ContextExclusionReason,
    ContextSnapshot,
)
from .tool_runtime import RegistrySnapshot, canonical_hash


_ANSWER_CONTEXT_EXCLUDED_KINDS = frozenset(
    {
        ContextEntryKind.OUTPUT_CONTRACT,
        ContextEntryKind.TOOL_CONTRACT,
        ContextEntryKind.ACTIVE_TOOL_CALL,
        ContextEntryKind.ACTIVE_TOOL_RESULT,
    }
)
_MAX_GROUNDING_AWARE_TERMINAL_RETRIES = 1
_MAX_PRE_ANSWER_EVIDENCE_READ_ATTEMPTS = 1
_EVIDENCE_UPGRADE_GUARD_CODES = frozenset(
    {
        "citation_not_ready",
        "grounding_not_in_context",
        "grounding_status_not_publishable",
        "limitation_receipt_invalid",
        "support_matrix_unsatisfied",
    }
)


class _DecisionContextProjectorV2:
    def __init__(
        self,
        *,
        projection_contract: str,
        snapshot_prefix: str,
        max_tool_observations: int = 4,
    ) -> None:
        self._projection_contract = projection_contract
        self._snapshot_prefix = snapshot_prefix
        self._max_tool_observations = max_tool_observations

    def project(self, context: ContextAssemblyResult) -> ContextAssemblyResult:
        eligible_projection = tuple(
            entry
            for entry in context.projection_entries
            if entry.kind not in _ANSWER_CONTEXT_EXCLUDED_KINDS
        )
        tool_observations = sorted(
            (
                (item[0], entry.entry_ref)
                for entry in eligible_projection
                if (item := tool_batch_observation(entry)) is not None
            ),
            key=lambda item: item[0],
        )
        retained_tool_observation_refs = {
            entry_ref
            for _, entry_ref in tool_observations[-self._max_tool_observations :]
        }
        retained_projection = tuple(
            entry
            for entry in eligible_projection
            if tool_batch_observation(entry) is None
            or entry.entry_ref in retained_tool_observation_refs
        )
        retained_refs = {entry.entry_ref for entry in retained_projection}
        retained_receipts = tuple(
            entry
            for entry in context.snapshot.included_entries
            if entry.entry_ref in retained_refs
        )
        removed_receipts = tuple(
            entry
            for entry in context.snapshot.included_entries
            if entry.entry_ref not in retained_refs
        )
        projected_exclusions = (
            *context.snapshot.excluded_entries,
            *(
                ContextExcludedEntry(
                    entry_ref=entry.entry_ref,
                    source_ref=entry.source_ref,
                    lane=entry.lane,
                    reason=ContextExclusionReason.NOT_RELEVANT,
                    protection_class=entry.protection_class,
                )
                for entry in removed_receipts
            ),
        )
        if len(projected_exclusions) > 1000:
            raise ActionLoopContractRejected(
                "V2 Answer Context exceeded the exclusion receipt limit"
            )

        projection_hash = canonical_hash(
            [entry.model_dump(mode="json") for entry in retained_projection]
        )
        removed_tokens = sum(entry.token_count for entry in removed_receipts)
        estimated_tokens = max(
            0,
            (context.snapshot.estimated_input_tokens or 0) - removed_tokens,
        )
        dependency_refs = tuple(
            dict.fromkeys(
                (
                    context.snapshot.policy_snapshot_ref,
                    context.snapshot.prompt_template_ref,
                    context.snapshot.model_profile_ref,
                    context.snapshot.context_profile_ref,
                    context.snapshot.authorization_snapshot_ref,
                    *(entry.source_ref for entry in retained_receipts),
                    *(entry.source_version_ref for entry in retained_receipts),
                )
            )
        )
        snapshot_body = {
            "snapshot_sequence": context.snapshot.snapshot_sequence,
            "task_ref": context.snapshot.task_ref,
            "state_version": context.snapshot.state_version,
            "consumer": context.snapshot.consumer.value,
            "status": context.snapshot.status.value,
            "request_hash": canonical_hash(
                {
                    "source_snapshot_ref": context.snapshot.snapshot_ref,
                    "source_snapshot_hash": context.snapshot.snapshot_hash,
                    "projection_contract": self._projection_contract,
                }
            ),
            "policy_snapshot_ref": context.snapshot.policy_snapshot_ref,
            "prompt_template_ref": context.snapshot.prompt_template_ref,
            "model_profile_ref": context.snapshot.model_profile_ref,
            "model_profile_hash": context.snapshot.model_profile_hash,
            "context_profile_ref": context.snapshot.context_profile_ref,
            "context_profile_hash": context.snapshot.context_profile_hash,
            "registry_snapshot_ref": None,
            "registry_snapshot_hash": None,
            "authorization_snapshot_ref": (
                context.snapshot.authorization_snapshot_ref
            ),
            "dependency_refs": dependency_refs,
            "included_entries": [
                entry.model_dump(mode="json") for entry in retained_receipts
            ],
            "excluded_entries": [
                entry.model_dump(mode="json") for entry in projected_exclusions
            ],
            "compression_receipts": [],
            "included_refs": [entry.entry_ref for entry in retained_receipts],
            "excluded_refs": [entry.entry_ref for entry in projected_exclusions],
            "limitation_messages": context.snapshot.limitation_messages,
            "estimated_input_tokens": estimated_tokens,
            "effective_input_budget": context.snapshot.effective_input_budget,
            "reserved_output_tokens": context.snapshot.reserved_output_tokens,
            "safety_margin_tokens": context.snapshot.safety_margin_tokens,
            "projection_hash": projection_hash,
        }
        snapshot_hash = canonical_hash(snapshot_body)
        snapshot = ContextSnapshot(
            snapshot_ref=(
                f"{self._snapshot_prefix}:"
                f"{snapshot_hash.removeprefix('sha256:')}"
            ),
            snapshot_hash=snapshot_hash,
            **snapshot_body,
        )
        return ContextAssemblyResult(
            snapshot=snapshot,
            projection_entries=retained_projection,
        )


class DecisionContextAnswerProjectorV2(_DecisionContextProjectorV2):
    """Derive one bounded, hash-bound Answer-only Context."""

    def __init__(self, *, max_tool_observations: int = 4) -> None:
        super().__init__(
            projection_contract="bid.pure-agent.answer-context.v2",
            snapshot_prefix="answer-context-v2",
            max_tool_observations=max_tool_observations,
        )


class DecisionContextTerminalProjectorV2(_DecisionContextProjectorV2):
    """Remove Tool authority after deterministic retrieval saturation."""

    def __init__(self, *, max_tool_observations: int = 4) -> None:
        super().__init__(
            projection_contract="bid.pure-agent.terminal-decision-context.v2",
            snapshot_prefix="terminal-decision-context-v2",
            max_tool_observations=max_tool_observations,
        )


class ProviderBoundaryV2MainAgentActionProvider:
    """Expose V2 through the stable V1 Action Loop port."""

    def __init__(
        self,
        *,
        orchestrator: ProviderDecisionAnswerOrchestratorV2,
        v1_compatibility_provider: MainAgentActionProvider | None = None,
        answer_context_projector: DecisionContextAnswerProjectorV2 | None = None,
        convergence_gate: RetrievalConvergenceGateV2 | None = None,
        terminal_context_projector: DecisionContextTerminalProjectorV2 | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        # Kept only as a constructor compatibility boundary for existing local
        # composition. V2-N never invokes it or accepts another action choice.
        del v1_compatibility_provider
        self._convergence_gate = convergence_gate or RetrievalConvergenceGateV2()
        convergence_policy = self._convergence_gate.policy
        self._answer_context_projector = (
            answer_context_projector
            or DecisionContextAnswerProjectorV2(
                max_tool_observations=(
                    convergence_policy.max_tool_observations_in_terminal_context
                )
            )
        )
        self._terminal_context_projector = (
            terminal_context_projector
            or DecisionContextTerminalProjectorV2(
                max_tool_observations=(
                    convergence_policy.max_tool_observations_in_terminal_context
                )
            )
        )

    async def decide(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentProviderOutcome:
        convergence = self._convergence_gate.evaluate(context)
        guard_feedback_refs = self._answer_guard_feedback_refs(context)
        if len(guard_feedback_refs) > _MAX_GROUNDING_AWARE_TERMINAL_RETRIES:
            return self._guard_retry_exhausted_outcome(
                request=request,
                guard_feedback_refs=guard_feedback_refs,
            )
        guard_recovery_constraint = self._answer_guard_recovery_constraint(
            context,
            guard_feedback_refs=guard_feedback_refs,
            visible_tool_names=request.visible_tool_names,
        )
        (
            pending_candidate_refs,
            evidence_atom_count,
            evidence_read_attempts,
        ) = self._evidence_upgrade_state(context)
        readiness_recovery_constraint = (
            self._pre_answer_evidence_readiness_constraint(
                pending_candidate_refs=pending_candidate_refs,
                evidence_atom_count=evidence_atom_count,
                evidence_read_attempts=evidence_read_attempts,
                visible_tool_names=request.visible_tool_names,
            )
        )
        recovery_constraint = (
            guard_recovery_constraint or readiness_recovery_constraint
        )
        if (
            pending_candidate_refs
            and evidence_atom_count == 0
            and recovery_constraint is None
        ):
            reason_code = (
                "evidence_read_unavailable"
                if "evidence_read" not in request.visible_tool_names
                else "evidence_read_attempt_exhausted"
            )
            return self._evidence_readiness_fallback_outcome(
                request=request,
                reason_code=reason_code,
                candidate_count=len(pending_candidate_refs),
                evidence_atom_count=evidence_atom_count,
                evidence_read_attempts=evidence_read_attempts,
            )
        active_request = request
        active_context = context
        active_registry = registry_snapshot
        if convergence.saturated and recovery_constraint is None:
            active_context = self._terminal_context_projector.project(context)
            active_request = self._request_for_projected_context(
                request,
                context=active_context,
                registry_snapshot=None,
            )
            active_registry = None
            guard_feedback_refs = self._answer_guard_feedback_refs(active_context)
        if (
            convergence.saturated
            and guard_feedback_refs
            and recovery_constraint is None
        ):
            authorization = RuntimeTerminalAnswerAuthorizationV2.build(
                request=active_request,
                context=active_context,
                convergence=convergence,
                guard_feedback_refs=guard_feedback_refs,
            )
            try:
                _, answer = await self._orchestrator.generate_answer(
                    decision_request=active_request,
                    terminal_authorization=authorization,
                    bundle=ProviderAnswerContextBundle(
                        context=active_context,
                        guard_feedback_refs=guard_feedback_refs,
                    ),
                )
            except ProviderBoundaryRejected as exc:
                if not self._is_grounding_required_answer_failure(exc):
                    raise
                schema_recovery_constraint = (
                    self._answer_schema_evidence_recovery_constraint(
                        context,
                        visible_tool_names=request.visible_tool_names,
                    )
                )
                if schema_recovery_constraint is not None:
                    return await self._execute_evidence_upgrade(
                        request=request,
                        context=context,
                        registry_snapshot=registry_snapshot,
                        convergence=convergence,
                        recovery_constraint=schema_recovery_constraint,
                    )
                return self._evidence_readiness_fallback_outcome(
                    request=request,
                    reason_code="answer_schema_grounding_unresolved",
                    candidate_count=len(pending_candidate_refs),
                    evidence_atom_count=evidence_atom_count,
                    evidence_read_attempts=evidence_read_attempts,
                )
            proposal = MainAgentModelDecision(
                action_kind=MainAgentModelActionKind.ANSWER,
                concise_basis=authorization.concise_basis,
                answer=AnswerAction(
                    draft=answer.projection.to_canonical(
                        context_snapshot_ref=request.context_snapshot_ref,
                        state_version=request.origin_state_version,
                    )
                ),
            )
            return self._outcome(
                request=request,
                proposal=proposal,
                concise_basis=authorization.concise_basis,
                provider_result_ref=answer.provider_result_ref,
                provider_response_hash=answer.provider_response_hash,
                provider_receipt_ref=answer.provider_receipt_ref,
            )
        selected = await self._orchestrator.decide_next_action(
            request=active_request,
            context=active_context,
            registry_snapshot=active_registry,
            convergence=convergence,
            allow_native_tool_calls=False,
            recovery_constraint=recovery_constraint,
        )
        if isinstance(selected, ProviderToolCallsOutcomeV2):
            raise ActionLoopContractRejected(
                "control-only V2 decision emitted native Function Calls"
            )

        if selected.decision.action_kind == "retrieve":
            if (
                (convergence.saturated and recovery_constraint is None)
                or active_registry is None
            ):
                raise ActionLoopContractRejected(
                    "retrieval-saturated decision attempted another Tool call"
                )
            tool_selection = await self._orchestrator.decide_retrieval_tool_calls(
                request=active_request,
                control_selection=selected,
                context=active_context,
                registry_snapshot=active_registry,
            )
            proposal = ToolCallBatchAction(
                model_turn_ref=tool_selection.proposals[0].model_turn_ref,
                calls=tool_selection.proposals,
            )
            return self._outcome(
                request=request,
                proposal=proposal,
                concise_basis=selected.decision.concise_basis,
                provider_result_ref=tool_selection.provider_result_ref,
                provider_response_hash=tool_selection.provider_response_hash,
                provider_receipt_ref=tool_selection.provider_receipt_ref,
            )

        if selected.decision.action_kind is MainAgentModelActionKind.ANSWER:
            answer_context = (
                active_context
                if convergence.saturated
                else self._answer_context_projector.project(context)
            )
            try:
                _, answer = await self._orchestrator.generate_answer(
                    decision_request=active_request,
                    next_action=selected,
                    bundle=ProviderAnswerContextBundle(
                        context=answer_context,
                        guard_feedback_refs=(
                            self._answer_guard_feedback_refs(answer_context)
                        ),
                    ),
                )
            except ProviderBoundaryRejected as exc:
                if not self._is_grounding_required_answer_failure(exc):
                    raise
                schema_recovery_constraint = (
                    self._answer_schema_evidence_recovery_constraint(
                        context,
                        visible_tool_names=request.visible_tool_names,
                    )
                )
                if schema_recovery_constraint is not None:
                    return await self._execute_evidence_upgrade(
                        request=request,
                        context=context,
                        registry_snapshot=registry_snapshot,
                        convergence=convergence,
                        recovery_constraint=schema_recovery_constraint,
                    )
                return self._evidence_readiness_fallback_outcome(
                    request=request,
                    reason_code="answer_schema_grounding_unresolved",
                    candidate_count=len(pending_candidate_refs),
                    evidence_atom_count=evidence_atom_count,
                    evidence_read_attempts=evidence_read_attempts,
                )
            proposal = MainAgentModelDecision(
                action_kind=MainAgentModelActionKind.ANSWER,
                concise_basis=selected.decision.concise_basis,
                answer=AnswerAction(
                    draft=answer.projection.to_canonical(
                        context_snapshot_ref=request.context_snapshot_ref,
                        state_version=request.origin_state_version,
                    )
                ),
            )
            return self._outcome(
                request=request,
                proposal=proposal,
                concise_basis=selected.decision.concise_basis,
                provider_result_ref=answer.provider_result_ref,
                provider_response_hash=answer.provider_response_hash,
                provider_receipt_ref=answer.provider_receipt_ref,
            )

        if convergence.saturated and selected.decision.action_kind is not (
            MainAgentModelActionKind.REQUEST_INFORMATION
        ):
            raise ActionLoopContractRejected(
                "retrieval-saturated decision must answer or request information"
            )
        locked_payload = await self._orchestrator.generate_locked_action_payload(
            request=active_request,
            selected=selected,
            context=active_context,
            registry_snapshot=active_registry,
        )
        proposal = MainAgentModelDecision(
            action_kind=selected.decision.action_kind,
            concise_basis=selected.decision.concise_basis,
            plan_request=locked_payload.plan_request,
            information_request=locked_payload.information_request,
        )
        return self._outcome(
            request=request,
            proposal=proposal,
            concise_basis=selected.decision.concise_basis,
            provider_result_ref=locked_payload.provider_result_ref,
            provider_response_hash=locked_payload.provider_response_hash,
            provider_receipt_ref=locked_payload.provider_receipt_ref,
        )

    @staticmethod
    def _pre_answer_evidence_readiness_constraint(
        *,
        pending_candidate_refs: tuple[str, ...],
        evidence_atom_count: int,
        evidence_read_attempts: int,
        visible_tool_names: tuple[str, ...],
    ) -> ProviderNextActionRecoveryConstraintV2 | None:
        """Require one bounded candidate-to-Evidence upgrade before Answer."""

        if (
            not pending_candidate_refs
            or evidence_atom_count > 0
            or evidence_read_attempts
            >= _MAX_PRE_ANSWER_EVIDENCE_READ_ATTEMPTS
            or "evidence_read" not in visible_tool_names
        ):
            return None
        return ProviderNextActionRecoveryConstraintV2(
            reason_code=(
                ProviderNextActionRecoveryReason.PRE_ANSWER_EVIDENCE_READINESS
            ),
            required_tool_names=("evidence_read",),
            candidate_refs=pending_candidate_refs,
        )

    @staticmethod
    def _evidence_upgrade_state(
        context: ContextAssemblyResult,
    ) -> tuple[tuple[str, ...], int, int]:
        """Return pending candidate refs and bounded evidence-read progress."""

        evidence_atom_refs = {
            entry.entry_ref
            for entry in context.projection_entries
            if entry.kind is ContextEntryKind.EVIDENCE_ATOM
        }
        upgraded_refs = set(evidence_atom_refs)
        candidate_refs: dict[str, None] = {}
        evidence_read_call_refs: set[str] = set()
        for entry in context.projection_entries:
            if entry.kind is not ContextEntryKind.OBSERVATION:
                continue
            try:
                payload = json.loads(entry.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            projection = payload.get("artifact_projection", payload)
            if (
                not isinstance(projection, dict)
                or projection.get("projection_kind") != "tool_batch_result"
            ):
                continue
            for call_index, call in enumerate(projection.get("calls") or ()):
                if (
                    not isinstance(call, dict)
                    or call.get("accepted_for_context") is not True
                ):
                    continue
                tool_name = call.get("tool_name")
                if tool_name == "evidence_read":
                    call_ref = call.get("call_ref")
                    evidence_read_call_refs.add(
                        call_ref
                        if isinstance(call_ref, str) and call_ref
                        else f"{entry.entry_ref}:{call_index}"
                    )
                result = call.get("result")
                if not isinstance(result, dict) or result.get("ok") is not True:
                    continue
                data_projection = result.get("data_projection")
                if not isinstance(data_projection, dict):
                    continue
                projection_kind = data_projection.get("kind")
                if projection_kind == "evidence_read_receipts":
                    for evidence in data_projection.get("evidence") or ():
                        if not isinstance(evidence, dict):
                            continue
                        evidence_ref = evidence.get("evidence_ref")
                        if isinstance(evidence_ref, str) and evidence_ref:
                            upgraded_refs.add(evidence_ref)
                    continue
                if projection_kind != "search_candidates":
                    continue
                for candidate in data_projection.get("candidates") or ():
                    if not isinstance(candidate, dict):
                        continue
                    evidence_ref = candidate.get("evidence_ref")
                    if isinstance(evidence_ref, str) and evidence_ref:
                        if (
                            evidence_ref not in candidate_refs
                            and len(candidate_refs) >= 64
                        ):
                            continue
                        candidate_refs.setdefault(evidence_ref, None)
        for evidence_ref in upgraded_refs:
            candidate_refs.pop(evidence_ref, None)
        return (
            tuple(candidate_refs),
            len(evidence_atom_refs),
            len(evidence_read_call_refs),
        )

    @classmethod
    def _answer_schema_evidence_recovery_constraint(
        cls,
        context: ContextAssemblyResult,
        *,
        visible_tool_names: tuple[str, ...],
    ) -> ProviderNextActionRecoveryConstraintV2 | None:
        candidate_refs, _, evidence_read_attempts = cls._evidence_upgrade_state(
            context
        )
        if (
            not candidate_refs
            or evidence_read_attempts
            >= _MAX_PRE_ANSWER_EVIDENCE_READ_ATTEMPTS
            or "evidence_read" not in visible_tool_names
        ):
            return None
        return ProviderNextActionRecoveryConstraintV2(
            reason_code=(
                ProviderNextActionRecoveryReason.ANSWER_SCHEMA_EVIDENCE_UPGRADE
            ),
            required_tool_names=("evidence_read",),
            candidate_refs=candidate_refs,
        )

    @staticmethod
    def _is_grounding_required_answer_failure(
        exc: ProviderBoundaryRejected,
    ) -> bool:
        failure = exc.failure
        return (
            failure.code is ProviderBoundaryFailureCode.ANSWER_SCHEMA_INVALID
            and any(
                ".grounding_refs" in issue.path
                and issue.error_type == "required_for_kind"
                for issue in failure.validation_issues
            )
        )

    async def _execute_evidence_upgrade(
        self,
        *,
        request: MainAgentDecisionRequest,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
        convergence: RetrievalConvergenceDecisionV2,
        recovery_constraint: ProviderNextActionRecoveryConstraintV2,
    ) -> MainAgentProviderOutcome:
        if registry_snapshot is None:
            return self._evidence_readiness_fallback_outcome(
                request=request,
                reason_code="evidence_read_unavailable",
                candidate_count=len(recovery_constraint.candidate_refs),
                evidence_atom_count=0,
                evidence_read_attempts=0,
            )
        selected = await self._orchestrator.decide_next_action(
            request=request,
            context=context,
            registry_snapshot=registry_snapshot,
            convergence=convergence,
            allow_native_tool_calls=False,
            recovery_constraint=recovery_constraint,
        )
        if isinstance(selected, ProviderToolCallsOutcomeV2):
            raise ActionLoopContractRejected(
                "evidence-upgrade control decision emitted native Function Calls"
            )
        tool_selection = await self._orchestrator.decide_retrieval_tool_calls(
            request=request,
            control_selection=selected,
            context=context,
            registry_snapshot=registry_snapshot,
        )
        proposal = ToolCallBatchAction(
            model_turn_ref=tool_selection.proposals[0].model_turn_ref,
            calls=tool_selection.proposals,
        )
        return self._outcome(
            request=request,
            proposal=proposal,
            concise_basis=selected.decision.concise_basis,
            provider_result_ref=tool_selection.provider_result_ref,
            provider_response_hash=tool_selection.provider_response_hash,
            provider_receipt_ref=tool_selection.provider_receipt_ref,
        )

    @staticmethod
    def _answer_guard_feedback_refs(
        context: ContextAssemblyResult,
    ) -> tuple[str, ...]:
        candidates: list[tuple[int, str]] = []
        for entry in context.projection_entries:
            if entry.kind is not ContextEntryKind.OBSERVATION:
                continue
            try:
                payload = json.loads(entry.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            projection = payload.get("artifact_projection", payload)
            if not isinstance(projection, dict) or projection.get(
                "projection_kind"
            ) != "answer_guard_feedback":
                continue
            if projection.get("accepted") is True:
                continue
            status = str(projection.get("status") or "").lower()
            if status not in {"rejected", "failed"}:
                continue
            observation = payload.get("observation")
            sequence = (
                observation.get("action_sequence", 0)
                if isinstance(observation, dict)
                else 0
            )
            candidates.append(
                (sequence if isinstance(sequence, int) else 0, entry.entry_ref)
            )
        ordered = sorted(candidates, key=lambda item: (item[0], item[1]))[-64:]
        return tuple(dict.fromkeys(entry_ref for _, entry_ref in ordered))

    @classmethod
    def _answer_guard_recovery_constraint(
        cls,
        context: ContextAssemblyResult,
        *,
        guard_feedback_refs: tuple[str, ...],
        visible_tool_names: tuple[str, ...],
    ) -> ProviderNextActionRecoveryConstraintV2 | None:
        """Require evidence upgrade when a rejected Answer still has readable leads."""

        if not guard_feedback_refs or "evidence_read" not in visible_tool_names:
            return None
        active_feedback_ref = guard_feedback_refs[-1]
        guard_codes: set[str] = set()
        for entry in context.projection_entries:
            if entry.kind is not ContextEntryKind.OBSERVATION:
                continue
            try:
                payload = json.loads(entry.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            projection = payload.get("artifact_projection", payload)
            if not isinstance(projection, dict):
                continue
            projection_kind = projection.get("projection_kind")
            if (
                entry.entry_ref == active_feedback_ref
                and projection_kind == "answer_guard_feedback"
            ):
                for issue in projection.get("issues") or ():
                    if not isinstance(issue, dict):
                        continue
                    code = issue.get("code")
                    if isinstance(code, str):
                        guard_codes.add(code)
        candidate_refs, _, evidence_read_attempts = cls._evidence_upgrade_state(
            context
        )
        if not guard_codes.intersection(_EVIDENCE_UPGRADE_GUARD_CODES):
            return None
        if (
            not candidate_refs
            or evidence_read_attempts
            >= _MAX_PRE_ANSWER_EVIDENCE_READ_ATTEMPTS
        ):
            return None
        return ProviderNextActionRecoveryConstraintV2(
            reason_code=(
                ProviderNextActionRecoveryReason.ANSWER_GUARD_EVIDENCE_UPGRADE
            ),
            required_tool_names=("evidence_read",),
            candidate_refs=candidate_refs,
        )

    @classmethod
    def _guard_retry_exhausted_outcome(
        cls,
        *,
        request: MainAgentDecisionRequest,
        guard_feedback_refs: tuple[str, ...],
    ) -> MainAgentProviderOutcome:
        receipt_body = {
            "schema_name": "bid.pure-agent.terminal-guard-fallback.v2",
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "state_version": request.origin_state_version,
            "guard_feedback_refs": guard_feedback_refs,
            "retry_limit": _MAX_GROUNDING_AWARE_TERMINAL_RETRIES,
        }
        receipt_hash = canonical_hash(receipt_body)
        block_hash = canonical_hash(
            {
                "receipt_hash": receipt_hash,
                "block_kind": "interaction",
            }
        )
        draft = AnswerDraft(
            response_language="zh-CN",
            blocks=(
                InteractionBlock(
                    block_id=(
                        "interaction:terminal-guard-fallback:"
                        + block_hash.removeprefix("sha256:")
                    ),
                    text=(
                        "当前可引用证据仍不足以形成可靠回答，我已停止重复检索和生成，"
                        "避免把未核验线索当成结论。请缩小问题范围，或补充能够分别覆盖"
                        "招标要求与企业能力的原始资料后继续。"
                    ),
                ),
            ),
            context_snapshot_ref=request.context_snapshot_ref,
            state_version=request.origin_state_version,
        )
        concise_basis = (
            "Grounding-aware Answer retry limit reached; returned a Runtime-owned "
            "actionable limitation without factual claims"
        )
        return cls._outcome(
            request=request,
            proposal=MainAgentModelDecision(
                action_kind=MainAgentModelActionKind.ANSWER,
                concise_basis=concise_basis,
                answer=AnswerAction(draft=draft),
            ),
            concise_basis=concise_basis,
            provider_result_ref=(
                "runtime-terminal-guard-fallback:"
                + receipt_hash.removeprefix("sha256:")
            ),
            provider_response_hash=canonical_hash(draft),
            provider_receipt_ref=(
                "runtime-terminal-guard-receipt:"
                + receipt_hash.removeprefix("sha256:")
            ),
        )

    @classmethod
    def _evidence_readiness_fallback_outcome(
        cls,
        *,
        request: MainAgentDecisionRequest,
        reason_code: str,
        candidate_count: int,
        evidence_atom_count: int,
        evidence_read_attempts: int,
    ) -> MainAgentProviderOutcome:
        messages = {
            "evidence_read_unavailable": (
                "已定位到相关候选资料，但当前证据读取能力不可用，无法安全形成"
                "资格或风险结论。请稍后重试，或由管理员检查证据读取能力。"
            ),
            "evidence_read_attempt_exhausted": (
                "已尝试读取候选资料，但仍未形成可引用证据。我已停止重复检索，"
                "避免把搜索线索当成正式结论。请缩小问题范围或补充原始资料后继续。"
            ),
            "answer_schema_grounding_unresolved": (
                "现有回答未能稳定绑定到可引用证据，我已停止继续生成，避免输出"
                "无依据的业务判断。请缩小问题范围或补充对应原始资料后继续。"
            ),
        }
        text = messages.get(
            reason_code,
            "当前证据尚未达到安全回答条件，请补充或核验原始资料后继续。",
        )
        receipt_body = {
            "schema_name": "bid.pure-agent.evidence-readiness-fallback.v2",
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "state_version": request.origin_state_version,
            "reason_code": reason_code,
            "candidate_count": candidate_count,
            "evidence_atom_count": evidence_atom_count,
            "evidence_read_attempts": evidence_read_attempts,
            "evidence_read_attempt_limit": (
                _MAX_PRE_ANSWER_EVIDENCE_READ_ATTEMPTS
            ),
        }
        receipt_hash = canonical_hash(receipt_body)
        draft = AnswerDraft(
            response_language="zh-CN",
            blocks=(
                InteractionBlock(
                    block_id=(
                        "interaction:evidence-readiness-fallback:"
                        + receipt_hash.removeprefix("sha256:")
                    ),
                    text=text,
                ),
            ),
            context_snapshot_ref=request.context_snapshot_ref,
            state_version=request.origin_state_version,
        )
        concise_basis = (
            "Pre-Answer Evidence Readiness could not be satisfied; returned a "
            f"Runtime-owned actionable receipt ({reason_code})"
        )
        return cls._outcome(
            request=request,
            proposal=MainAgentModelDecision(
                action_kind=MainAgentModelActionKind.ANSWER,
                concise_basis=concise_basis,
                answer=AnswerAction(draft=draft),
            ),
            concise_basis=concise_basis,
            provider_result_ref=(
                "runtime-evidence-readiness-fallback:"
                + receipt_hash.removeprefix("sha256:")
            ),
            provider_response_hash=canonical_hash(draft),
            provider_receipt_ref=(
                "runtime-evidence-readiness-receipt:"
                + receipt_hash.removeprefix("sha256:")
            ),
        )

    @staticmethod
    def _request_for_projected_context(
        request: MainAgentDecisionRequest,
        *,
        context: ContextAssemblyResult,
        registry_snapshot: RegistrySnapshot | None,
    ) -> MainAgentDecisionRequest:
        body = request.model_dump(
            mode="json",
            exclude={"request_ref", "request_hash"},
        )
        body.update(
            {
                "context_snapshot_ref": context.snapshot.snapshot_ref,
                "context_snapshot_hash": context.snapshot.snapshot_hash,
                "registry_snapshot_ref": (
                    None
                    if registry_snapshot is None
                    else registry_snapshot.snapshot_ref
                ),
                "registry_snapshot_hash": (
                    None
                    if registry_snapshot is None
                    else registry_snapshot.snapshot_hash
                ),
                "visible_tools_hash": (
                    None
                    if registry_snapshot is None
                    else registry_snapshot.visible_tools_hash
                ),
                "visible_tool_names": (
                    []
                    if registry_snapshot is None
                    else list(registry_snapshot.visible_tool_names)
                ),
            }
        )
        digest = canonical_hash(body)
        return MainAgentDecisionRequest(
            **body,
            request_ref=(
                "agent-decision-request:" + digest.removeprefix("sha256:")
            ),
            request_hash=digest,
        )

    @staticmethod
    def _outcome(
        *,
        request: MainAgentDecisionRequest,
        proposal: MainAgentModelDecision | ToolCallBatchAction,
        concise_basis: str,
        provider_result_ref: str,
        provider_response_hash: str,
        provider_receipt_ref: str,
    ) -> MainAgentProviderOutcome:
        body = {
            "request_ref": request.request_ref,
            "task_ref": request.task_ref,
            "origin_state_version": request.origin_state_version,
            "context_snapshot_ref": request.context_snapshot_ref,
            "registry_snapshot_ref": request.registry_snapshot_ref,
            "provider_result_ref": provider_result_ref,
            "provider_response_hash": provider_response_hash,
            "provider_receipt_ref": provider_receipt_ref,
            "proposal": proposal.model_dump(mode="json"),
            "concise_basis": concise_basis,
        }
        return MainAgentProviderOutcome(
            **body,
            outcome_hash=canonical_hash(body),
        )
