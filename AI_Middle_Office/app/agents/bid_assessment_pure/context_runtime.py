"""Deterministic six-lane Context Assembler for B04-2.

This module governs authorized projections and budgets.  It does not decide an
Agent action, call a model, summarize with an LLM, retrieve a document, or grant
permission.  Candidate sources must scope-filter before returning safe content.
"""

from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Mapping, Protocol
import uuid

from .runtime import (
    ContextAssemblyRequest,
    ContextAssemblyResult,
    ContextAssemblyStatus,
    ContextCompressionLevel,
    ContextCompressionReceipt,
    ContextEntryCandidate,
    ContextEntryKind,
    ContextEntryValidity,
    ContextExcludedEntry,
    ContextExclusionReason,
    ContextIncludedEntry,
    ContextLane,
    ContextOmissionAction,
    ContextProfile,
    ContextProjectionEntry,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
    ModelContextProfile,
)
from .state import AgentTaskState, AgentTaskStatus
from .tool_runtime import RegistrySnapshot, canonical_hash


_SNAPSHOT_NAMESPACE = uuid.UUID("d7a45062-a5c7-49be-91c6-30cf9e527175")


class ContextRuntimeError(RuntimeError):
    """Safe base error for Context Runtime failures."""


class ContextSourceUnavailable(ContextRuntimeError):
    """No authorized candidate source is configured."""


class ContextCounterUnavailable(ContextRuntimeError):
    """No provider-aware counter is configured."""


class ContextStoreUnavailable(ContextRuntimeError):
    """No Context Snapshot store is configured."""


class ContextInvocationRejected(ContextRuntimeError):
    """Task, profile, registry, or candidate guards rejected assembly."""


def context_request_key(request: ContextAssemblyRequest) -> str:
    return (
        f"{request.task_ref}:{request.state_version}:"
        f"{request.consumer.value}:{request.snapshot_sequence}"
    )


class ContextCandidateSource(Protocol):
    """Return only already-authorized, bounded, safe candidate projections."""

    async def collect(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]: ...


class ContextTokenCounter(Protocol):
    """Count the final bounded projection without estimating from characters."""

    async def count(
        self,
        *,
        request: ContextAssemblyRequest,
        entries: tuple[ContextEntryCandidate, ...],
        model_profile: ModelContextProfile,
    ) -> int: ...


class ContextSnapshotStore(Protocol):
    async def save(self, snapshot: ContextSnapshot) -> None: ...

    async def load(self, snapshot_ref: str, *, task_ref: str) -> ContextSnapshot: ...


class DisabledContextCandidateSource:
    async def collect(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        del task, request
        raise ContextSourceUnavailable("context candidate source is disabled")


class StaticContextCandidateSource:
    """In-memory source for later authorized contract tests and local wiring."""

    def __init__(self, candidates: Mapping[str, tuple[ContextEntryCandidate, ...]]):
        self._candidates = MappingProxyType(dict(candidates))

    async def collect(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        del task
        try:
            return self._candidates[context_request_key(request)]
        except KeyError as exc:
            raise ContextSourceUnavailable(
                "no static context candidates are configured for this request"
            ) from exc


class DisabledContextTokenCounter:
    async def count(
        self,
        *,
        request: ContextAssemblyRequest,
        entries: tuple[ContextEntryCandidate, ...],
        model_profile: ModelContextProfile,
    ) -> int:
        del request, entries, model_profile
        raise ContextCounterUnavailable("provider-aware context counter is disabled")


class PrecountedContextTokenCounter:
    """Sum provider-precounted entries plus a versioned framing allowance."""

    async def count(
        self,
        *,
        request: ContextAssemblyRequest,
        entries: tuple[ContextEntryCandidate, ...],
        model_profile: ModelContextProfile,
    ) -> int:
        del request
        return model_profile.framing_tokens + sum(entry.token_count for entry in entries)


class DisabledContextSnapshotStore:
    async def save(self, snapshot: ContextSnapshot) -> None:
        del snapshot
        raise ContextStoreUnavailable("context snapshot store is disabled")

    async def load(self, snapshot_ref: str, *, task_ref: str) -> ContextSnapshot:
        del snapshot_ref, task_ref
        raise ContextStoreUnavailable("context snapshot store is disabled")


class InMemoryContextSnapshotStore:
    """Reference store; the durable SQL adapter remains a separate integration."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ContextSnapshot] = {}

    async def save(self, snapshot: ContextSnapshot) -> None:
        existing = self._snapshots.get(snapshot.snapshot_ref)
        if existing is not None and existing.snapshot_hash != snapshot.snapshot_hash:
            raise ContextInvocationRejected(
                "context snapshot reference was reused with different content"
            )
        self._snapshots[snapshot.snapshot_ref] = snapshot

    async def load(self, snapshot_ref: str, *, task_ref: str) -> ContextSnapshot:
        try:
            snapshot = self._snapshots[snapshot_ref]
        except KeyError as exc:
            raise ContextStoreUnavailable("context snapshot does not exist") from exc
        if snapshot.task_ref != task_ref:
            raise ContextStoreUnavailable("context snapshot does not exist")
        return snapshot


class ContextAssemblerRuntime:
    """Assemble one immutable projection; no Agent action order is implied."""

    _LANE_ORDER = {
        ContextLane.POLICY_PROTOCOL: 0,
        ContextLane.ACTIVE_CONTROL: 1,
        ContextLane.TOOL_CONTRACT_ACTIVE_CALLS: 2,
        ContextLane.OBSERVATION_GROUNDING: 3,
        ContextLane.RELEVANT_INTERACTION: 4,
        ContextLane.HISTORICAL_MEMORY: 5,
    }
    _COMPRESSION_ORDER = {
        ContextCompressionLevel.NONE: 0,
        ContextCompressionLevel.L0: 1,
        ContextCompressionLevel.L1: 2,
        ContextCompressionLevel.L2: 3,
        ContextCompressionLevel.L3: 4,
        ContextCompressionLevel.L4: 5,
    }
    _BLOCKING_STATUS = {
        ContextOmissionAction.FAIL: ContextAssemblyStatus.FAILED,
        ContextOmissionAction.ASK_USER: ContextAssemblyStatus.BLOCKED_ON_USER,
        ContextOmissionAction.NARROW: ContextAssemblyStatus.NEEDS_NARROWING,
        ContextOmissionAction.LIMIT: ContextAssemblyStatus.READY_WITH_LIMITS,
    }
    _STATUS_SEVERITY = {
        ContextAssemblyStatus.READY: 0,
        ContextAssemblyStatus.READY_WITH_LIMITS: 1,
        ContextAssemblyStatus.NEEDS_NARROWING: 2,
        ContextAssemblyStatus.BLOCKED_ON_USER: 3,
        ContextAssemblyStatus.FAILED: 4,
    }

    def __init__(
        self,
        candidate_source: ContextCandidateSource | None = None,
        token_counter: ContextTokenCounter | None = None,
        snapshot_store: ContextSnapshotStore | None = None,
    ) -> None:
        self._candidate_source = candidate_source or DisabledContextCandidateSource()
        self._token_counter = token_counter or DisabledContextTokenCounter()
        self._snapshot_store = snapshot_store or DisabledContextSnapshotStore()

    async def assemble(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
        model_profile: ModelContextProfile,
        context_profile: ContextProfile,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> ContextAssemblyResult:
        budget = self._validate_invocation(
            task=task,
            request=request,
            model_profile=model_profile,
            context_profile=context_profile,
            registry_snapshot=registry_snapshot,
        )
        candidates = tuple(
            sorted(
                await self._candidate_source.collect(task=task, request=request),
                key=lambda candidate: candidate.entry_ref,
            )
        )
        self._validate_candidates(request=request, candidates=candidates)
        foundation_limitations = self._foundation_limitations(
            request=request,
            candidates=candidates,
        )

        exclusions: list[ContextExcludedEntry] = []
        receipts: list[ContextCompressionReceipt] = []
        eligible: list[ContextEntryCandidate] = []
        for candidate in candidates:
            if candidate.validity in {
                ContextEntryValidity.SUPERSEDED,
                ContextEntryValidity.REVOKED,
                ContextEntryValidity.DELETED,
            }:
                reason = {
                    ContextEntryValidity.SUPERSEDED: ContextExclusionReason.SUPERSEDED,
                    ContextEntryValidity.REVOKED: ContextExclusionReason.REVOKED,
                    ContextEntryValidity.DELETED: ContextExclusionReason.DELETED,
                }[candidate.validity]
                exclusions.append(self._excluded(candidate, reason))
                if candidate.required:
                    foundation_limitations.append(
                        f"required_context_unavailable:{candidate.entry_ref}"
                    )
                continue
            eligible.append(candidate)

        eligible, dedupe_exclusions, dedupe_receipts = self._deduplicate(eligible)
        exclusions.extend(dedupe_exclusions)
        receipts.extend(dedupe_receipts)

        required = [candidate for candidate in eligible if candidate.required]
        required = self._ordered(required)
        required_tokens = await self._count(
            request=request,
            entries=tuple(required),
            model_profile=model_profile,
        )
        if foundation_limitations or required_tokens > budget:
            if required_tokens > budget:
                foundation_limitations.append("context_budget_exceeded:mandatory")
            status = self._status_for_candidates(required, default=ContextAssemblyStatus.FAILED)
            if foundation_limitations and required_tokens <= budget:
                status = ContextAssemblyStatus.FAILED
            return await self._freeze(
                request=request,
                model_profile=model_profile,
                context_profile=context_profile,
                registry_snapshot=registry_snapshot,
                status=status,
                selected=tuple(required),
                exclusions=tuple(exclusions),
                receipts=tuple(receipts),
                limitations=tuple(dict.fromkeys(foundation_limitations)),
                estimated_tokens=required_tokens,
                budget=budget,
                expose_projection=False,
            )

        selected = list(required)
        selected_refs = {candidate.entry_ref for candidate in selected}
        optional_groups: dict[str, list[ContextEntryCandidate]] = defaultdict(list)
        for candidate in eligible:
            if candidate.entry_ref not in selected_refs:
                optional_groups[candidate.stable_key].append(candidate)

        omitted_material: list[ContextEntryCandidate] = []
        for stable_key in sorted(
            optional_groups,
            key=lambda key: self._group_sort_key(optional_groups[key]),
        ):
            variants = sorted(optional_groups[stable_key], key=self._variant_sort_key)
            chosen = await self._choose_variant(
                request=request,
                model_profile=model_profile,
                context_profile=context_profile,
                budget=budget,
                selected=tuple(selected),
                variants=tuple(variants),
            )
            if chosen is None or len(selected) >= context_profile.max_entries:
                for candidate in variants:
                    exclusions.append(
                        self._excluded(candidate, ContextExclusionReason.BUDGET)
                    )
                    if candidate.material_if_omitted:
                        omitted_material.append(candidate)
                receipts.append(
                    ContextCompressionReceipt(
                        level=ContextCompressionLevel.L4,
                        input_entry_refs=tuple(
                            candidate.entry_ref for candidate in variants
                        ),
                        output_entry_ref=None,
                        before_tokens=max(
                            candidate.token_count for candidate in variants
                        ),
                        after_tokens=0,
                        lossless=False,
                    )
                )
                continue

            selected.append(chosen)
            for candidate in variants:
                if candidate.entry_ref == chosen.entry_ref:
                    continue
                reason = (
                    ContextExclusionReason.REPLACED_BY_COMPRESSION
                    if chosen.compression_level is not ContextCompressionLevel.NONE
                    else ContextExclusionReason.ALTERNATE_NOT_NEEDED
                )
                exclusions.append(self._excluded(candidate, reason, material=False))
            if chosen.compression_level is not ContextCompressionLevel.NONE:
                sources = chosen.derived_from_refs or tuple(
                    candidate.entry_ref
                    for candidate in variants
                    if candidate.compression_level is ContextCompressionLevel.NONE
                )
                before_tokens = sum(
                    candidate.token_count
                    for candidate in variants
                    if candidate.entry_ref in sources
                )
                if not before_tokens:
                    before_tokens = chosen.token_count
                receipts.append(
                    ContextCompressionReceipt(
                        level=chosen.compression_level,
                        input_entry_refs=sources or (chosen.entry_ref,),
                        output_entry_ref=chosen.entry_ref,
                        before_tokens=max(before_tokens, chosen.token_count),
                        after_tokens=chosen.token_count,
                        lossless=chosen.compression_level is ContextCompressionLevel.L0,
                    )
                )

        selected = self._ordered(selected)
        final_tokens = await self._count(
            request=request,
            entries=tuple(selected),
            model_profile=model_profile,
        )
        if final_tokens > budget:
            raise ContextInvocationRejected(
                "final provider-aware count exceeded the validated budget"
            )

        limitations: list[str] = []
        for candidate in selected:
            if candidate.validity is ContextEntryValidity.STALE:
                limitations.append(f"included_stale_context:{candidate.entry_ref}")
            elif candidate.validity is ContextEntryValidity.CONFLICTED:
                limitations.append(f"included_conflicted_context:{candidate.entry_ref}")
        if omitted_material:
            limitations.extend(
                f"material_context_omitted:{candidate.entry_ref}"
                for candidate in omitted_material
            )
            status = self._status_for_candidates(
                omitted_material,
                default=ContextAssemblyStatus.READY_WITH_LIMITS,
            )
        elif limitations:
            status = ContextAssemblyStatus.READY_WITH_LIMITS
        else:
            status = ContextAssemblyStatus.READY

        model_ready = status in {
            ContextAssemblyStatus.READY,
            ContextAssemblyStatus.READY_WITH_LIMITS,
        }
        return await self._freeze(
            request=request,
            model_profile=model_profile,
            context_profile=context_profile,
            registry_snapshot=registry_snapshot,
            status=status,
            selected=tuple(selected),
            exclusions=tuple(self._unique_exclusions(exclusions)),
            receipts=tuple(receipts),
            limitations=tuple(dict.fromkeys(limitations)),
            estimated_tokens=final_tokens,
            budget=budget,
            expose_projection=model_ready,
        )

    def _validate_invocation(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
        model_profile: ModelContextProfile,
        context_profile: ContextProfile,
        registry_snapshot: RegistrySnapshot | None,
    ) -> int:
        if task.status is not AgentTaskStatus.RUNNING:
            raise ContextInvocationRejected("model Context requires a running task")
        if request.task_ref != task.task_id or request.state_version != task.state_version:
            raise ContextInvocationRejected("Context request is stale or belongs to another task")
        if request.model_profile_ref != model_profile.profile_ref:
            raise ContextInvocationRejected("model profile reference does not match")
        if request.context_profile_ref != context_profile.profile_ref:
            raise ContextInvocationRejected("Context profile reference does not match")
        if context_profile.reserved_output_tokens > model_profile.max_output_tokens:
            raise ContextInvocationRejected("reserved output exceeds model capability")
        budget = context_profile.effective_input_budget(model_profile)
        if budget < 1:
            raise ContextInvocationRejected("profiles leave no effective input budget")
        if request.registry_snapshot_ref is None:
            if registry_snapshot is not None or request.visible_tool_names:
                raise ContextInvocationRejected("unexpected registry projection")
        else:
            if registry_snapshot is None:
                raise ContextInvocationRejected("registry snapshot is required")
            if registry_snapshot.snapshot_ref != request.registry_snapshot_ref:
                raise ContextInvocationRejected("registry snapshot reference does not match")
            if registry_snapshot.visible_tool_names != request.visible_tool_names:
                raise ContextInvocationRejected("visible tools differ from registry snapshot")
        return budget

    @staticmethod
    def _validate_candidates(
        *,
        request: ContextAssemblyRequest,
        candidates: tuple[ContextEntryCandidate, ...],
    ) -> None:
        refs = [candidate.entry_ref for candidate in candidates]
        if len(refs) != len(set(refs)):
            raise ContextInvocationRejected("Context candidate refs must be unique")
        for candidate in candidates:
            if candidate.authorization_snapshot_ref != request.authorization_snapshot_ref:
                raise ContextInvocationRejected(
                    "candidate was not projected under the active authorization snapshot"
                )

    @staticmethod
    def _foundation_limitations(
        *,
        request: ContextAssemblyRequest,
        candidates: tuple[ContextEntryCandidate, ...],
    ) -> list[str]:
        limitations: list[str] = []
        active = [
            candidate
            for candidate in candidates
            if candidate.validity
            not in {
                ContextEntryValidity.SUPERSEDED,
                ContextEntryValidity.REVOKED,
                ContextEntryValidity.DELETED,
            }
        ]
        if not any(candidate.kind is ContextEntryKind.POLICY and candidate.required for candidate in active):
            limitations.append("missing_required_context:policy")
        if not any(
            candidate.kind is ContextEntryKind.OUTPUT_CONTRACT and candidate.required
            for candidate in active
        ):
            limitations.append("missing_required_context:output_contract")
        if not any(candidate.kind is ContextEntryKind.TASK_STATE and candidate.required for candidate in active):
            limitations.append("missing_required_context:task_state")
        if not any(
            candidate.kind is ContextEntryKind.CURRENT_USER_MESSAGE
            and candidate.required
            and candidate.representation is ContextRepresentation.EXACT
            and request.user_message_ref in {candidate.entry_ref, candidate.source_ref}
            for candidate in active
        ):
            limitations.append("missing_required_context:current_user_message")

        for resource_ref in request.required_resource_refs:
            matches = [
                candidate
                for candidate in active
                if resource_ref in {candidate.entry_ref, candidate.source_ref}
            ]
            if not matches or not any(candidate.required for candidate in matches):
                limitations.append(f"missing_required_context:{resource_ref}")

        tool_contracts = [
            candidate
            for candidate in active
            if candidate.kind is ContextEntryKind.TOOL_CONTRACT
        ]
        projected_tools = [candidate.tool_name for candidate in tool_contracts]
        if (
            set(projected_tools) != set(request.visible_tool_names)
            or len(projected_tools) != len(request.visible_tool_names)
        ):
            limitations.append("visible_tool_contract_set_mismatch")

        protocol_pairs: dict[str, list[ContextEntryKind]] = defaultdict(list)
        for candidate in active:
            if candidate.protocol_pair_ref is not None:
                protocol_pairs[candidate.protocol_pair_ref].append(candidate.kind)
        required_pair = [
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        ]
        for pair_ref, kinds in protocol_pairs.items():
            if sorted(item.value for item in kinds) != sorted(
                item.value for item in required_pair
            ):
                limitations.append(f"incomplete_active_tool_pair:{pair_ref}")
        return limitations

    def _deduplicate(
        self,
        candidates: list[ContextEntryCandidate],
    ) -> tuple[
        list[ContextEntryCandidate],
        list[ContextExcludedEntry],
        list[ContextCompressionReceipt],
    ]:
        grouped: dict[
            tuple[
                str,
                str,
                ContextRepresentation,
                str | None,
                ContextEntryKind | None,
            ],
            list[ContextEntryCandidate],
        ] = defaultdict(list)
        for candidate in candidates:
            protocol_kind = (
                candidate.kind
                if candidate.kind
                in {
                    ContextEntryKind.ACTIVE_TOOL_CALL,
                    ContextEntryKind.ACTIVE_TOOL_RESULT,
                }
                else None
            )
            grouped[
                (
                    candidate.source_ref,
                    candidate.source_content_hash,
                    candidate.representation,
                    candidate.protocol_pair_ref,
                    protocol_kind,
                )
            ].append(candidate)
        kept: list[ContextEntryCandidate] = []
        exclusions: list[ContextExcludedEntry] = []
        receipts: list[ContextCompressionReceipt] = []
        for duplicates in grouped.values():
            ordered = sorted(
                duplicates,
                key=lambda candidate: (
                    not candidate.required,
                    -candidate.priority,
                    candidate.entry_ref,
                ),
            )
            winner = ordered[0]
            kept.append(winner)
            if len(ordered) == 1:
                continue
            removed = ordered[1:]
            exclusions.extend(
                self._excluded(candidate, ContextExclusionReason.DUPLICATE, material=False)
                for candidate in removed
            )
            receipts.append(
                ContextCompressionReceipt(
                    level=ContextCompressionLevel.L0,
                    input_entry_refs=tuple(candidate.entry_ref for candidate in ordered),
                    output_entry_ref=winner.entry_ref,
                    before_tokens=sum(candidate.token_count for candidate in ordered),
                    after_tokens=winner.token_count,
                    lossless=True,
                )
            )
        return kept, exclusions, receipts

    async def _choose_variant(
        self,
        *,
        request: ContextAssemblyRequest,
        model_profile: ModelContextProfile,
        context_profile: ContextProfile,
        budget: int,
        selected: tuple[ContextEntryCandidate, ...],
        variants: tuple[ContextEntryCandidate, ...],
    ) -> ContextEntryCandidate | None:
        exact = next(
            (
                candidate
                for candidate in variants
                if candidate.compression_level is ContextCompressionLevel.NONE
            ),
            None,
        )
        if exact is not None:
            exact_count = await self._count(
                request=request,
                entries=(*selected, exact),
                model_profile=model_profile,
            )
            if exact_count <= min(
                budget,
                context_profile.soft_compression_threshold_tokens,
            ):
                return exact

        for candidate in variants:
            if candidate is exact:
                continue
            count = await self._count(
                request=request,
                entries=(*selected, candidate),
                model_profile=model_profile,
            )
            if count <= budget:
                return candidate
        if exact is not None:
            exact_count = await self._count(
                request=request,
                entries=(*selected, exact),
                model_profile=model_profile,
            )
            if exact_count <= budget:
                return exact
        return None

    async def _count(
        self,
        *,
        request: ContextAssemblyRequest,
        entries: tuple[ContextEntryCandidate, ...],
        model_profile: ModelContextProfile,
    ) -> int:
        value = await self._token_counter.count(
            request=request,
            entries=entries,
            model_profile=model_profile,
        )
        if value < 0:
            raise ContextInvocationRejected("token counter returned a negative value")
        return value

    async def _freeze(
        self,
        *,
        request: ContextAssemblyRequest,
        model_profile: ModelContextProfile,
        context_profile: ContextProfile,
        registry_snapshot: RegistrySnapshot | None,
        status: ContextAssemblyStatus,
        selected: tuple[ContextEntryCandidate, ...],
        exclusions: tuple[ContextExcludedEntry, ...],
        receipts: tuple[ContextCompressionReceipt, ...],
        limitations: tuple[str, ...],
        estimated_tokens: int,
        budget: int,
        expose_projection: bool,
    ) -> ContextAssemblyResult:
        included = tuple(self._included(candidate) for candidate in selected)
        projection = tuple(self._projection(candidate) for candidate in selected)
        dependencies = tuple(
            dict.fromkeys(
                [
                    request.policy_snapshot_ref,
                    request.prompt_template_ref,
                    request.model_profile_ref,
                    request.context_profile_ref,
                    request.authorization_snapshot_ref,
                    *(
                        [request.registry_snapshot_ref]
                        if request.registry_snapshot_ref is not None
                        else []
                    ),
                    *(candidate.source_ref for candidate in selected),
                    *(candidate.source_version_ref for candidate in selected),
                ]
            )
        )
        projection_hash = canonical_hash(
            [entry.model_dump(mode="json") for entry in projection]
        )
        snapshot_body = {
            "snapshot_sequence": request.snapshot_sequence,
            "task_ref": request.task_ref,
            "state_version": request.state_version,
            "consumer": request.consumer.value,
            "status": status.value,
            "request_hash": canonical_hash(request),
            "policy_snapshot_ref": request.policy_snapshot_ref,
            "prompt_template_ref": request.prompt_template_ref,
            "model_profile_ref": model_profile.profile_ref,
            "model_profile_hash": model_profile.profile_hash,
            "context_profile_ref": context_profile.profile_ref,
            "context_profile_hash": context_profile.profile_hash,
            "registry_snapshot_ref": (
                None if registry_snapshot is None else registry_snapshot.snapshot_ref
            ),
            "registry_snapshot_hash": (
                None if registry_snapshot is None else registry_snapshot.snapshot_hash
            ),
            "authorization_snapshot_ref": request.authorization_snapshot_ref,
            "dependency_refs": list(dependencies),
            "included_entries": [entry.model_dump(mode="json") for entry in included],
            "excluded_entries": [entry.model_dump(mode="json") for entry in exclusions],
            "compression_receipts": [
                receipt.model_dump(mode="json") for receipt in receipts
            ],
            "included_refs": [entry.entry_ref for entry in included],
            "excluded_refs": [entry.entry_ref for entry in exclusions],
            "limitation_messages": list(limitations),
            "estimated_input_tokens": estimated_tokens,
            "effective_input_budget": budget,
            "reserved_output_tokens": context_profile.reserved_output_tokens,
            "safety_margin_tokens": context_profile.safety_margin_tokens,
            "projection_hash": projection_hash,
        }
        snapshot_hash = canonical_hash(snapshot_body)
        snapshot_ref = str(uuid.uuid5(_SNAPSHOT_NAMESPACE, snapshot_hash))
        snapshot = ContextSnapshot(
            snapshot_ref=snapshot_ref,
            snapshot_hash=snapshot_hash,
            **snapshot_body,
        )
        await self._snapshot_store.save(snapshot)
        return ContextAssemblyResult(
            snapshot=snapshot,
            projection_entries=projection if expose_projection else (),
        )

    @classmethod
    def _ordered(
        cls,
        candidates: list[ContextEntryCandidate],
    ) -> list[ContextEntryCandidate]:
        return sorted(
            candidates,
            key=lambda candidate: (
                cls._LANE_ORDER[candidate.lane],
                *cls._tool_protocol_order(candidate),
                -candidate.priority,
                candidate.entry_ref,
            ),
        )

    @staticmethod
    def _tool_protocol_order(
        candidate: ContextEntryCandidate,
    ) -> tuple[int, str, int]:
        if candidate.kind is ContextEntryKind.TOOL_CONTRACT:
            return 0, candidate.tool_name or "", 0
        if candidate.kind in {
            ContextEntryKind.ACTIVE_TOOL_CALL,
            ContextEntryKind.ACTIVE_TOOL_RESULT,
        }:
            return (
                1,
                candidate.protocol_pair_ref or "",
                0
                if candidate.kind is ContextEntryKind.ACTIVE_TOOL_CALL
                else 1,
            )
        return 2, "", 0

    @classmethod
    def _group_sort_key(
        cls,
        candidates: list[ContextEntryCandidate],
    ) -> tuple[int, int, str]:
        best = min(candidates, key=cls._variant_sort_key)
        return cls._LANE_ORDER[best.lane], -max(item.priority for item in candidates), best.stable_key

    @classmethod
    def _variant_sort_key(
        cls,
        candidate: ContextEntryCandidate,
    ) -> tuple[int, int, int, str]:
        protection = {
            ContextProtectionClass.MANDATORY_EXACT: 0,
            ContextProtectionClass.PROTECTED: 1,
            ContextProtectionClass.ELASTIC: 2,
        }[candidate.protection_class]
        return (
            cls._COMPRESSION_ORDER[candidate.compression_level],
            protection,
            -candidate.priority,
            candidate.entry_ref,
        )

    @classmethod
    def _status_for_candidates(
        cls,
        candidates: list[ContextEntryCandidate],
        *,
        default: ContextAssemblyStatus,
    ) -> ContextAssemblyStatus:
        statuses = [cls._BLOCKING_STATUS[candidate.omission_action] for candidate in candidates]
        return max(statuses, key=cls._STATUS_SEVERITY.get) if statuses else default

    @staticmethod
    def _included(candidate: ContextEntryCandidate) -> ContextIncludedEntry:
        projection_hash = canonical_hash(
            {
                "entry_ref": candidate.entry_ref,
                "content": candidate.content,
                "representation": candidate.representation.value,
            }
        )
        return ContextIncludedEntry(
            entry_ref=candidate.entry_ref,
            stable_key=candidate.stable_key,
            source_ref=candidate.source_ref,
            source_version_ref=candidate.source_version_ref,
            lane=candidate.lane,
            kind=candidate.kind,
            representation=candidate.representation,
            authority_label=candidate.authority_label,
            protection_class=candidate.protection_class,
            trust_class=candidate.trust_class,
            source_content_hash=candidate.source_content_hash,
            projection_hash=projection_hash,
            token_count=candidate.token_count,
            tool_name=candidate.tool_name,
            protocol_pair_ref=candidate.protocol_pair_ref,
        )

    @classmethod
    def _projection(cls, candidate: ContextEntryCandidate) -> ContextProjectionEntry:
        receipt = cls._included(candidate)
        return ContextProjectionEntry(
            **receipt.model_dump(mode="python"),
            content=candidate.content,
            untrusted_data=candidate.trust_class is ContextTrustClass.UNTRUSTED_DATA,
        )

    @staticmethod
    def _excluded(
        candidate: ContextEntryCandidate,
        reason: ContextExclusionReason,
        *,
        material: bool | None = None,
    ) -> ContextExcludedEntry:
        return ContextExcludedEntry(
            entry_ref=candidate.entry_ref,
            source_ref=candidate.source_ref,
            lane=candidate.lane,
            reason=reason,
            protection_class=candidate.protection_class,
            material_limitation=(
                candidate.material_if_omitted if material is None else material
            ),
        )

    @staticmethod
    def _unique_exclusions(
        exclusions: list[ContextExcludedEntry],
    ) -> list[ContextExcludedEntry]:
        unique: dict[str, ContextExcludedEntry] = {}
        for exclusion in exclusions:
            unique.setdefault(exclusion.entry_ref, exclusion)
        return list(unique.values())
