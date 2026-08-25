"""Persisted local Context Candidate Source and immutable Snapshot Store.

The adapters project already-authorized local persistence into the six-lane
Context contract.  They do not retrieve evidence, summarize with a model,
reconstruct incomplete Tool protocol messages, select an Action, or commit the
caller-owned database transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from .common import Reference, StrictContract
from .context_runtime import (
    ContextAssemblerRuntime,
    ContextCounterUnavailable,
    ContextInvocationRejected,
    ContextSourceUnavailable,
    ContextStoreUnavailable,
)
from .repository import (
    ContextMessageRow,
    PersistedObservationArtifactRow,
    PersistedToolProtocolPairRow,
    PureAgentNotFound,
    PureAgentPersistenceError,
    PureAgentRepository,
)
from .persisted_evidence_adapters import (
    PersistedEvidenceArtifactRejected,
    PersistedEvidenceAtomAuthority,
    PersistedPriorAnswerEvidenceLineage,
    extract_persisted_evidence_atoms,
    index_persisted_evidence_atoms,
    load_prior_answer_evidence_lineage,
)
from .runtime import (
    ContextAssemblyRequest,
    ContextCompressionLevel,
    ContextEntryCandidate,
    ContextEntryKind,
    ContextLane,
    ContextOmissionAction,
    ContextProtectionClass,
    ContextRepresentation,
    ContextSnapshot,
    ContextTrustClass,
    ModelContextProfile,
    TokenCounterMode,
)
from .state import AgentTaskState
from .tool_runtime import RegistrySnapshot, canonical_hash, canonical_json


_MAX_CONTEXT_CONTENT_CHARS = 131_072
_MAX_COMPACT_SEARCH_CANDIDATES = 16
_MAX_COMPACT_SEARCH_EXCERPT_CHARS = 500
_MAX_COMPACT_DRAFT_TEXT_CHARS = 600
_MAX_PRIOR_ANSWER_LINEAGES = 4
_MAX_PRIOR_LINEAGE_EVIDENCE = 128


class AuthorizedResourceIdentity(StrictContract):
    """Runtime-owned display identity; never evidence for resource contents."""

    resource_ref: Reference
    resource_kind: Literal["bid_document", "enterprise_knowledge"]
    display_name: str = Field(min_length=1, max_length=500)
    resource_version_ref: Reference


class PersistedContextProjectionPolicy(StrictContract):
    """Explicit local text and Registry authority visible to Context assembly."""

    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    system_policy: str = Field(min_length=1, max_length=32_768)
    output_contract: str = Field(min_length=1, max_length=32_768)
    registry_snapshot: RegistrySnapshot | None = None
    resource_identities: tuple[AuthorizedResourceIdentity, ...] = Field(
        default_factory=tuple,
        max_length=128,
    )
    max_interaction_messages: int = Field(default=20, ge=0, le=50)

    @model_validator(mode="after")
    def validate_resource_identities(self) -> "PersistedContextProjectionPolicy":
        refs = tuple(item.resource_ref for item in self.resource_identities)
        if len(refs) != len(set(refs)):
            raise ValueError("resource identity refs must be unique")
        return self


class PersistedContextCandidateSource:
    """Build deterministic candidates from hash-verified local persistence."""

    def __init__(
        self,
        repository: PureAgentRepository,
        *,
        policy: PersistedContextProjectionPolicy,
    ) -> None:
        self._repository = repository
        self._policy = policy

    async def collect(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        try:
            return self._collect(task=task, request=request)
        except PureAgentPersistenceError as exc:
            raise ContextSourceUnavailable(
                "persisted Context candidates are unavailable"
            ) from exc

    def _collect(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        self._validate_request_policy(request)
        persisted_task = self._repository.load_task_state(task.task_id)
        scope = self._repository.load_local_task_scope(
            task_id=task.task_id,
            conversation_id=task.session_id,
        )
        if (
            persisted_task != task
            or request.task_ref != task.task_id
            or request.state_version != task.state_version
            or scope.task_state_version != task.state_version
            or scope.goal_ref != task.goal_ref
            or scope.plan_ref != task.plan_ref
            or scope.latest_checkpoint_ref != request.checkpoint_snapshot_ref
            or scope.conversation_status != "active"
            or scope.cancellation_fence_ref is not None
        ):
            raise ContextSourceUnavailable(
                "persisted Context scope no longer matches the Task"
            )

        current = self._repository.load_task_context_message(
            task_id=task.task_id,
            conversation_id=task.session_id,
            message_id=request.user_message_ref,
        )
        candidates: list[ContextEntryCandidate] = [
            self._policy_candidate(request),
            self._output_contract_candidate(request),
            self._task_candidate(task=task, scope=scope, request=request),
            self._current_message_candidate(current=current, request=request),
        ]
        candidates.extend(self._tool_contract_candidates(request))
        candidates.extend(
            self._tool_protocol_candidates(task=task, request=request)
        )
        candidates.extend(self._resource_receipts(request))
        observation_candidates = list(
            self._observation_candidates(task=task, request=request)
        )
        candidates.extend(observation_candidates)

        if task.plan_ref is not None:
            candidates.append(self._plan_candidate(task=task, request=request))
        if request.checkpoint_snapshot_ref is not None:
            candidates.append(self._checkpoint_candidate(task=task, request=request))

        history = self._repository.list_task_context_messages_before(
            task_id=task.task_id,
            conversation_id=task.session_id,
            before_sequence=current.sequence_no,
            limit=self._policy.max_interaction_messages,
        )
        lineage_candidates, lineage_authorities = (
            self._prior_answer_lineage_candidates(
                task=task,
                request=request,
                history=history,
            )
        )
        existing_evidence_refs = {
            candidate.entry_ref
            for candidate in observation_candidates
            if candidate.kind is ContextEntryKind.EVIDENCE_ATOM
        }
        for atom in lineage_authorities:
            if atom.evidence_ref in existing_evidence_refs:
                continue
            candidates.append(
                self._evidence_atom_candidate(
                    atom=atom,
                    request=request,
                    authority_label=(
                        "persisted-prior-answer-evidence-authority"
                    ),
                )
            )
            existing_evidence_refs.add(atom.evidence_ref)
        candidates.extend(lineage_candidates)
        candidates.extend(
            self._interaction_candidate(row=row, request=request) for row in history
        )
        return tuple(candidates)

    def _validate_request_policy(self, request: ContextAssemblyRequest) -> None:
        registry = self._policy.registry_snapshot
        if (
            request.policy_snapshot_ref != self._policy.policy_snapshot_ref
            or request.prompt_template_ref != self._policy.prompt_template_ref
        ):
            raise ContextSourceUnavailable(
                "Context request is outside the injected policy snapshots"
            )
        if registry is None:
            if request.registry_snapshot_ref is not None or request.visible_tool_names:
                raise ContextSourceUnavailable(
                    "Context request exposes a Registry that was not injected"
                )
            return
        if (
            request.registry_snapshot_ref != registry.snapshot_ref
            or request.visible_tool_names != registry.visible_tool_names
        ):
            raise ContextSourceUnavailable(
                "Context request Registry projection drifted"
            )

    def _policy_candidate(
        self,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        return self._candidate(
            identity={"kind": "policy", "ref": request.policy_snapshot_ref},
            stable_key="foundation:policy",
            source_ref=request.policy_snapshot_ref,
            source_version_ref=request.policy_snapshot_ref,
            request=request,
            lane=ContextLane.POLICY_PROTOCOL,
            kind=ContextEntryKind.POLICY,
            representation=ContextRepresentation.EXACT,
            authority_label="policy-snapshot",
            protection_class=ContextProtectionClass.MANDATORY_EXACT,
            trust_class=ContextTrustClass.TRUSTED_POLICY,
            content=self._policy.system_policy,
            required=True,
            material_if_omitted=True,
            priority=100,
            omission_action=ContextOmissionAction.FAIL,
        )

    def _output_contract_candidate(
        self,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        return self._candidate(
            identity={"kind": "output_contract", "ref": request.prompt_template_ref},
            stable_key="foundation:output-contract",
            source_ref=request.prompt_template_ref,
            source_version_ref=request.prompt_template_ref,
            request=request,
            lane=ContextLane.POLICY_PROTOCOL,
            kind=ContextEntryKind.OUTPUT_CONTRACT,
            representation=ContextRepresentation.EXACT,
            authority_label="output-contract",
            protection_class=ContextProtectionClass.MANDATORY_EXACT,
            trust_class=ContextTrustClass.TRUSTED_POLICY,
            content=self._policy.output_contract,
            required=True,
            material_if_omitted=True,
            priority=100,
            omission_action=ContextOmissionAction.FAIL,
        )

    def _task_candidate(
        self,
        *,
        task: AgentTaskState,
        scope: Any,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        content = canonical_json(
            {
                "task_state": task.model_dump(mode="json"),
                "persistence_fence": {
                    "task_row_version": scope.task_row_version,
                    "conversation_row_version": scope.conversation_row_version,
                },
            }
        )
        version_ref = self._version_ref(
            "task-state",
            {
                "task_ref": task.task_id,
                "state_version": task.state_version,
                "task_row_version": scope.task_row_version,
            },
        )
        return self._candidate(
            identity={"kind": "task_state", "version_ref": version_ref},
            stable_key="control:task-state",
            source_ref=task.task_id,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.ACTIVE_CONTROL,
            kind=ContextEntryKind.TASK_STATE,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label="persisted-task-state",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.TRUSTED_RUNTIME,
            content=content,
            required=True,
            material_if_omitted=True,
            priority=100,
            omission_action=ContextOmissionAction.FAIL,
            compression_level=ContextCompressionLevel.L0,
        )

    def _current_message_candidate(
        self,
        *,
        current: ContextMessageRow,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        content = canonical_json(current.content)
        source_hash = canonical_hash(current.content)
        if source_hash.removeprefix("sha256:") != current.content_hash:
            raise ContextSourceUnavailable("current Context message content drifted")
        version_ref = self._version_ref(
            "message-version",
            {"message_ref": current.message_ref, "content_hash": source_hash},
        )
        return self._candidate(
            identity={"kind": "current_user_message", "version_ref": version_ref},
            stable_key="control:current-user-message",
            source_ref=current.message_ref,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.ACTIVE_CONTROL,
            kind=ContextEntryKind.CURRENT_USER_MESSAGE,
            representation=ContextRepresentation.EXACT,
            authority_label="persisted-user-turn",
            protection_class=ContextProtectionClass.MANDATORY_EXACT,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=content,
            source_content_hash=source_hash,
            required=True,
            material_if_omitted=True,
            priority=100,
            omission_action=ContextOmissionAction.FAIL,
        )

    def _tool_contract_candidates(
        self,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        registry = self._policy.registry_snapshot
        if registry is None:
            return ()
        candidates: list[ContextEntryCandidate] = []
        for name in request.visible_tool_names:
            entry = registry.entry(name)
            content = canonical_json(entry.model_contract)
            version_ref = self._version_ref(
                "tool-contract-version",
                {
                    "registry_ref": registry.snapshot_ref,
                    "name": name,
                    "definition_hash": entry.definition_hash,
                },
            )
            candidates.append(
                self._candidate(
                    identity={"kind": "tool_contract", "version_ref": version_ref},
                    stable_key=f"tool-contract:{name}",
                    source_ref=registry.snapshot_ref,
                    source_version_ref=version_ref,
                    request=request,
                    lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
                    kind=ContextEntryKind.TOOL_CONTRACT,
                    representation=ContextRepresentation.EXACT,
                    authority_label="frozen-tool-contract",
                    protection_class=ContextProtectionClass.MANDATORY_EXACT,
                    trust_class=ContextTrustClass.TRUSTED_TOOL_CONTRACT,
                    content=content,
                    source_content_hash=canonical_hash(entry.model_contract),
                    required=True,
                    material_if_omitted=True,
                    priority=100,
                    omission_action=ContextOmissionAction.FAIL,
                    tool_name=name,
                )
            )
        return tuple(candidates)

    def _tool_protocol_candidates(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        registry = self._policy.registry_snapshot
        if registry is None or not task.observation_refs:
            return ()
        try:
            pairs = self._repository.list_context_tool_protocol_pairs(
                task_id=task.task_id,
                observation_ref=task.observation_refs[-1],
                registry_snapshot_ref=registry.snapshot_ref,
                registry_snapshot_hash=registry.snapshot_hash,
                visible_tools_hash=registry.visible_tools_hash,
                visible_tool_names=request.visible_tool_names,
            )
        except PureAgentNotFound:
            return ()
        candidates: list[ContextEntryCandidate] = []
        for pair in pairs:
            candidates.extend(
                self._tool_protocol_pair_candidates(pair=pair, request=request)
            )
        return tuple(candidates)

    def _tool_protocol_pair_candidates(
        self,
        *,
        pair: PersistedToolProtocolPairRow,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ContextEntryCandidate]:
        version_ref = self._version_ref(
            "tool-protocol-version",
            {
                "ledger_call_ref": pair.ledger_call_id,
                "input_hash": pair.input_hash,
                "output_hash": pair.output_hash,
            },
        )
        call = self._candidate(
            identity={"kind": "active_tool_call", "version_ref": version_ref},
            stable_key=f"tool-protocol:{pair.provider_tool_call_id}:call",
            source_ref=pair.call_ref,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
            kind=ContextEntryKind.ACTIVE_TOOL_CALL,
            representation=ContextRepresentation.EXACT,
            authority_label="persisted-tool-call-protocol",
            protection_class=ContextProtectionClass.MANDATORY_EXACT,
            trust_class=ContextTrustClass.TRUSTED_RUNTIME,
            content=canonical_json(pair.arguments),
            source_content_hash=pair.input_hash,
            required=True,
            material_if_omitted=True,
            priority=99,
            omission_action=ContextOmissionAction.FAIL,
            tool_name=pair.tool_name,
            protocol_pair_ref=pair.provider_tool_call_id,
        )
        result = self._candidate(
            identity={"kind": "active_tool_result", "version_ref": version_ref},
            stable_key=f"tool-protocol:{pair.provider_tool_call_id}:result",
            source_ref=pair.output_ref,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.TOOL_CONTRACT_ACTIVE_CALLS,
            kind=ContextEntryKind.ACTIVE_TOOL_RESULT,
            representation=ContextRepresentation.EXACT,
            authority_label="persisted-tool-result-protocol",
            protection_class=ContextProtectionClass.MANDATORY_EXACT,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=canonical_json(pair.output),
            source_content_hash=pair.output_hash,
            required=True,
            material_if_omitted=True,
            priority=99,
            omission_action=ContextOmissionAction.FAIL,
            tool_name=pair.tool_name,
            protocol_pair_ref=pair.provider_tool_call_id,
        )
        return call, result

    def _resource_receipts(
        self,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        identities = {
            item.resource_ref: item for item in self._policy.resource_identities
        }
        return tuple(
            self._candidate(
                identity={
                    "kind": "resource_receipt",
                    "resource_ref": resource_ref,
                    "authorization_ref": request.authorization_snapshot_ref,
                    "resource_identity": (
                        None
                        if resource_ref not in identities
                        else identities[resource_ref].model_dump(mode="json")
                    ),
                },
                stable_key=f"resource:{canonical_hash(resource_ref)[7:23]}",
                source_ref=resource_ref,
                source_version_ref=(
                    identities[resource_ref].resource_version_ref
                    if resource_ref in identities
                    else request.authorization_snapshot_ref
                ),
                request=request,
                lane=ContextLane.OBSERVATION_GROUNDING,
                kind=ContextEntryKind.GROUNDING,
                representation=ContextRepresentation.REF_ONLY,
                authority_label=(
                    "authorized-resource-identity-receipt"
                    if resource_ref in identities
                    else "authorized-resource-receipt"
                ),
                protection_class=ContextProtectionClass.PROTECTED,
                trust_class=ContextTrustClass.UNTRUSTED_DATA,
                content=canonical_json(
                    (
                        {
                            "schema_name": (
                                "bid.pure-agent.resource-identity-receipt.v1"
                            ),
                            "resource_ref": resource_ref,
                            "resource_kind": identities[resource_ref].resource_kind,
                            "display_name": identities[resource_ref].display_name,
                            "resource_version_ref": (
                                identities[resource_ref].resource_version_ref
                            ),
                            "authorization_bound": True,
                            "claim_scope": "resource_identity_and_load_status_only",
                            "instruction": (
                                "This receipt may identify the loaded resource but "
                                "cannot support claims about its business contents."
                            ),
                        }
                        if resource_ref in identities
                        else {
                            "resource_ref": resource_ref,
                            "authorization_bound": True,
                            "evidence_loaded": False,
                            "instruction": (
                                "This receipt is not evidence. Use an authorized Tool "
                                "before making resource-backed claims."
                            ),
                        }
                    )
                ),
                required=True,
                material_if_omitted=True,
                priority=95,
                omission_action=ContextOmissionAction.FAIL,
                compression_level=ContextCompressionLevel.L4,
            )
            for resource_ref in request.required_resource_refs
        )

    def _plan_candidate(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        if task.plan_ref is None:
            raise ContextSourceUnavailable("Task does not carry an active Plan")
        row = self._repository.load_task_plan(
            task_id=task.task_id,
            plan_id=task.plan_ref,
        )
        plan_hash = canonical_hash(row.body_json)
        if (
            row.status != "active"
            or plan_hash.removeprefix("sha256:") != row.plan_hash
        ):
            raise ContextSourceUnavailable(
                "persisted Plan is not active or its content drifted"
            )
        content = canonical_json(
            {
                "plan_ref": row.id,
                "plan_version": int(row.plan_version),
                "status": row.status,
                "plan": row.body_json,
            }
        )
        version_ref = self._version_ref(
            "plan-version",
            {"plan_ref": row.id, "plan_hash": plan_hash},
        )
        return self._candidate(
            identity={"kind": "plan_control", "version_ref": version_ref},
            stable_key="control:active-plan",
            source_ref=row.id,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.ACTIVE_CONTROL,
            kind=ContextEntryKind.PLAN_CONTROL,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label="persisted-plan",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.TRUSTED_RUNTIME,
            content=content,
            source_content_hash=plan_hash,
            required=True,
            material_if_omitted=True,
            priority=95,
            omission_action=ContextOmissionAction.FAIL,
            compression_level=ContextCompressionLevel.L0,
        )

    def _checkpoint_candidate(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        checkpoint_ref = request.checkpoint_snapshot_ref
        if checkpoint_ref is None:
            raise ContextSourceUnavailable("Context request has no Checkpoint")
        checkpoint = self._repository.load_task_checkpoint(
            task_id=task.task_id,
            checkpoint_id=checkpoint_ref,
        )
        content = canonical_json(
            {
                "checkpoint_ref": checkpoint.checkpoint_id,
                "slot_ref": checkpoint.slot_ref,
                "suspended_state_version": checkpoint.suspended_state_version,
                "execution_mode": checkpoint.execution_mode.value,
                "context_snapshot_ref": checkpoint.context_snapshot_ref,
                "suspended_action_ref": checkpoint.suspended_action_ref,
                "effect_fence_ref": checkpoint.effect_fence_ref,
                "status": checkpoint.status.value,
            }
        )
        version_ref = self._version_ref(
            "checkpoint-version",
            {
                "checkpoint_ref": checkpoint.checkpoint_id,
                "content": content,
            },
        )
        return self._candidate(
            identity={"kind": "slot_checkpoint", "version_ref": version_ref},
            stable_key="control:continuation-checkpoint",
            source_ref=checkpoint.checkpoint_id,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.ACTIVE_CONTROL,
            kind=ContextEntryKind.SLOT_CHECKPOINT,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label="persisted-continuation-checkpoint",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.TRUSTED_RUNTIME,
            content=content,
            required=True,
            material_if_omitted=True,
            priority=95,
            omission_action=ContextOmissionAction.FAIL,
            compression_level=ContextCompressionLevel.L0,
        )

    def _observation_candidates(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
    ) -> tuple[ContextEntryCandidate, ...]:
        candidates: list[ContextEntryCandidate] = []
        evidence_atoms: list[PersistedEvidenceAtomAuthority] = []
        for observation_ref in task.observation_refs:
            try:
                artifact = self._repository.load_context_observation_artifact(
                    task_id=task.task_id,
                    observation_ref=observation_ref,
                )
            except PureAgentNotFound:
                candidates.append(
                    self._observation_receipt_candidate(
                        task=task,
                        observation_ref=observation_ref,
                        request=request,
                    )
                )
            else:
                candidates.append(
                    self._observation_artifact_candidate(
                        artifact=artifact,
                        request=request,
                    )
                )
                try:
                    extracted_atoms = extract_persisted_evidence_atoms(artifact)
                except PersistedEvidenceArtifactRejected as exc:
                    raise ContextSourceUnavailable(
                        "persisted Evidence Atom authority is invalid"
                    ) from exc
                evidence_atoms.extend(extracted_atoms)
        try:
            evidence_by_ref = index_persisted_evidence_atoms(evidence_atoms)
        except PersistedEvidenceArtifactRejected as exc:
            raise ContextSourceUnavailable(
                "persisted Evidence Atom authority is invalid"
            ) from exc
        candidates.extend(
            self._evidence_atom_candidate(atom=atom, request=request)
            for atom in evidence_by_ref.values()
        )
        return tuple(candidates)

    def _prior_answer_lineage_candidates(
        self,
        *,
        task: AgentTaskState,
        request: ContextAssemblyRequest,
        history: tuple[ContextMessageRow, ...],
    ) -> tuple[
        tuple[ContextEntryCandidate, ...],
        tuple[PersistedEvidenceAtomAuthority, ...],
    ]:
        answer_rows = [
            row
            for row in history
            if row.role == "assistant" and row.message_type == "answer.committed"
        ][-_MAX_PRIOR_ANSWER_LINEAGES:]
        if not answer_rows:
            return (), ()
        lineages: list[PersistedPriorAnswerEvidenceLineage] = []
        authorities: list[PersistedEvidenceAtomAuthority] = []
        try:
            for row in answer_rows:
                try:
                    lineage, selected = load_prior_answer_evidence_lineage(
                        repository=self._repository,
                        current_task=task,
                        message_ref=row.message_ref,
                        allowed_scope_refs=request.required_resource_refs,
                    )
                except PureAgentNotFound:
                    # Older rendered messages may remain in history after their
                    # response version was superseded.  They are visible chat
                    # history, but no longer carry inheritable Answer authority.
                    continue
                if lineage is None:
                    continue
                lineages.append(lineage)
                authorities.extend(selected)
            indexed = index_persisted_evidence_atoms(authorities)
        except (PureAgentPersistenceError, PersistedEvidenceArtifactRejected) as exc:
            raise ContextSourceUnavailable(
                "prior committed Answer evidence lineage is invalid"
            ) from exc
        if len(indexed) > _MAX_PRIOR_LINEAGE_EVIDENCE:
            raise ContextSourceUnavailable(
                "prior committed Answer evidence exceeds the Context limit"
            )
        return (
            tuple(
                self._prior_answer_lineage_candidate(
                    lineage=lineage,
                    request=request,
                )
                for lineage in lineages
            ),
            tuple(indexed.values()),
        )

    def _prior_answer_lineage_candidate(
        self,
        *,
        lineage: PersistedPriorAnswerEvidenceLineage,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        content = canonical_json(lineage)
        return self._candidate(
            identity={
                "kind": "prior_answer_evidence_lineage",
                "lineage_hash": lineage.lineage_hash,
            },
            stable_key=f"prior-answer-lineage:{lineage.message_ref}",
            source_ref=lineage.response_ref,
            source_version_ref=lineage.response_artifact_ref,
            request=request,
            lane=ContextLane.RELEVANT_INTERACTION,
            kind=ContextEntryKind.EVIDENCE_PARENT,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label="persisted-prior-answer-evidence-lineage",
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=content,
            source_content_hash=canonical_hash(lineage),
            required=True,
            material_if_omitted=True,
            priority=88,
            omission_action=ContextOmissionAction.FAIL,
            compression_level=ContextCompressionLevel.L1,
        )

    def _evidence_atom_candidate(
        self,
        *,
        atom: PersistedEvidenceAtomAuthority,
        request: ContextAssemblyRequest,
        authority_label: str = "persisted-evidence-read-authority",
    ) -> ContextEntryCandidate:
        content = atom.context_content()
        return self._candidate(
            identity={"kind": "evidence_atom", "ref": atom.evidence_ref},
            entry_ref=atom.evidence_ref,
            stable_key=f"evidence:{atom.evidence_ref}",
            source_ref=atom.evidence_ref,
            source_version_ref=atom.source_version_ref,
            request=request,
            lane=ContextLane.OBSERVATION_GROUNDING,
            kind=ContextEntryKind.EVIDENCE_ATOM,
            representation=ContextRepresentation.EXACT,
            authority_label=authority_label,
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=content,
            source_content_hash=atom.content_hash,
            required=False,
            material_if_omitted=True,
            priority=92,
            omission_action=ContextOmissionAction.LIMIT,
        )

    def _observation_artifact_candidate(
        self,
        *,
        artifact: PersistedObservationArtifactRow,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        observation = artifact.observation
        source_payload = {
            "observation": observation.model_dump(mode="json"),
            "artifact": artifact.artifact,
        }
        projection = self._compact_observation_artifact(artifact.artifact)
        projected_payload = {
            "observation": observation.model_dump(mode="json"),
            "artifact_projection": projection,
            "artifact_receipt": {
                "artifact_ref": observation.artifact_ref,
                "artifact_hash": observation.artifact_hash,
                "artifact_content_loaded": False,
                "projection_kind": projection["projection_kind"],
            },
        }
        content = canonical_json(projected_payload)
        authority_label = "persisted-observation-compact-projection"
        compression_level = ContextCompressionLevel.L2
        if len(content) > _MAX_CONTEXT_CONTENT_CHARS:
            content = canonical_json(
                {
                    "observation": observation.model_dump(mode="json"),
                    "artifact_receipt": {
                        "artifact_ref": observation.artifact_ref,
                        "artifact_hash": observation.artifact_hash,
                        "artifact_content_loaded": False,
                        "reason": "context_projection_size_limit",
                    },
                }
            )
            authority_label = "persisted-observation-artifact-receipt"
            compression_level = ContextCompressionLevel.L4
        version_ref = self._version_ref(
            "observation-artifact-version",
            {
                "observation_hash": observation.observation_hash,
                "artifact_hash": observation.artifact_hash,
            },
        )
        return self._candidate(
            identity={"kind": "observation_artifact", "version_ref": version_ref},
            stable_key=f"observation:{observation.observation_ref}",
            source_ref=observation.observation_ref,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.OBSERVATION_GROUNDING,
            kind=ContextEntryKind.OBSERVATION,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label=authority_label,
            protection_class=ContextProtectionClass.PROTECTED,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=content,
            source_content_hash=canonical_hash(source_payload),
            required=False,
            material_if_omitted=True,
            priority=85,
            omission_action=ContextOmissionAction.LIMIT,
            compression_level=compression_level,
        )

    def _compact_observation_artifact(self, artifact: Any) -> dict[str, Any]:
        if not isinstance(artifact, dict):
            return {
                "projection_kind": "artifact_receipt",
                "schema_name": None,
                "content_projected": False,
            }
        schema_name = artifact.get("schema_name")
        if schema_name == "bid.pure-agent.capability.tool-batch-result.v1":
            return self._compact_tool_batch_artifact(artifact)
        if schema_name == "bid.pure-agent.capability.answer-result.v1":
            return self._compact_answer_artifact(artifact)
        return {
            "projection_kind": "artifact_receipt",
            "schema_name": schema_name if isinstance(schema_name, str) else None,
            "content_projected": False,
            "top_level_keys": sorted(str(key) for key in artifact)[:32],
        }

    def _compact_tool_batch_artifact(
        self,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        raw_calls = artifact.get("calls")
        calls = raw_calls if isinstance(raw_calls, list) else []
        return {
            "projection_kind": "tool_batch_result",
            "schema_name": artifact.get("schema_name"),
            "call_count": len(calls),
            "calls": [
                self._compact_tool_call(call)
                for call in calls[:64]
                if isinstance(call, dict)
            ],
        }

    def _compact_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        result = call.get("result")
        compact: dict[str, Any] = {
            "call_ref": call.get("call_ref"),
            "tool_name": call.get("tool_name"),
            "accepted_for_context": call.get("accepted_for_context") is True,
            "replayed": call.get("replayed") is True,
        }
        if isinstance(result, dict):
            compact["result"] = self._compact_tool_result(
                result,
                tool_name=str(call.get("tool_name") or ""),
            )
        denied_codes = [
            item.get("code")
            for item in (call.get("guard_decisions") or [])
            if isinstance(item, dict) and item.get("allowed") is False
        ]
        if denied_codes:
            compact["denied_guard_codes"] = list(dict.fromkeys(denied_codes))[:16]
        provenance = call.get("provenance")
        if isinstance(provenance, list):
            compact["provenance_receipts"] = [
                {
                    "output_ref": item.get("output_ref"),
                    "source_domain": item.get("source_domain"),
                    "source_scope_ref": item.get("source_scope_ref"),
                    "source_version_ref": item.get("source_version_ref"),
                    "locator": item.get("locator"),
                    "citable": item.get("citable") is True,
                }
                for item in provenance[:64]
                if isinstance(item, dict)
            ]
        return compact

    def _compact_tool_result(
        self,
        result: dict[str, Any],
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        if result.get("ok") is not True:
            error = result.get("error")
            compact_error: dict[str, Any] | None = None
            if isinstance(error, dict):
                compact_error = {
                    "code": error.get("code"),
                    "message": self._bounded_text(error.get("message"), 300),
                    "retryable": error.get("retryable") is True,
                }
            return {"ok": False, "error": compact_error}

        data = result.get("data")
        if not isinstance(data, dict):
            return {"ok": True, "data_projection": {"kind": "empty"}}
        if isinstance(data.get("candidates"), list):
            candidates = data["candidates"]
            return {
                "ok": True,
                "data_projection": {
                    "kind": "search_candidates",
                    "candidate_count": len(candidates),
                    "candidates": [
                        {
                            "evidence_ref": item.get("evidence_ref"),
                            "excerpt": self._bounded_text(
                                item.get("excerpt"),
                                _MAX_COMPACT_SEARCH_EXCERPT_CHARS,
                            ),
                            "locator": item.get("locator"),
                            "citable": item.get("citable") is True,
                        }
                        for item in candidates[:_MAX_COMPACT_SEARCH_CANDIDATES]
                        if isinstance(item, dict)
                    ],
                    "truncated": len(candidates) > _MAX_COMPACT_SEARCH_CANDIDATES,
                },
            }
        if isinstance(data.get("evidence"), list):
            evidence = data["evidence"]
            return {
                "ok": True,
                "data_projection": {
                    "kind": "evidence_read_receipts",
                    "evidence_count": len(evidence),
                    "evidence": [
                        {
                            "evidence_ref": item.get("evidence_ref"),
                            "locator": item.get("locator"),
                            "citable": item.get("citable") is True,
                            "content_projected_as": "evidence_atom",
                        }
                        for item in evidence[:32]
                        if isinstance(item, dict)
                    ],
                },
            }
        if isinstance(data.get("entries"), list):
            entries = data["entries"]
            return {
                "ok": True,
                "data_projection": {
                    "kind": "document_outline",
                    "entry_count": len(entries),
                    "entries": [
                        {
                            "title": self._bounded_text(item.get("title"), 300),
                            "level": item.get("level"),
                            "locator": item.get("locator"),
                        }
                        for item in entries[:32]
                        if isinstance(item, dict)
                    ],
                    "truncated": len(entries) > 32,
                },
            }
        return {
            "ok": True,
            "data_projection": {
                "kind": "result_receipt",
                "tool_name": tool_name,
                "data_keys": sorted(str(key) for key in data)[:32],
            },
        }

    def _compact_answer_artifact(
        self,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        validation = artifact.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        citation = artifact.get("citation_decision")
        citation = citation if isinstance(citation, dict) else {}
        issues = [
            item
            for item in (*self._dict_items(validation.get("issues")),
                         *self._dict_items(citation.get("issues")))
        ]
        issue_projection = [
            {
                "code": item.get("code"),
                "block_ref": item.get("block_ref"),
                "statement_ref": item.get("statement_ref"),
                "grounding_ref": item.get("grounding_ref"),
                "quote_ref": item.get("quote_ref"),
                "guard_message": self._bounded_text(item.get("message"), 300),
                "required_action": self._answer_issue_action(item.get("code")),
            }
            for item in issues[:64]
        ]
        required_actions = list(
            dict.fromkeys(
                item["required_action"]
                for item in issue_projection
                if item.get("required_action")
            )
        )
        execution_draft = artifact.get("execution_draft")
        execution_draft = execution_draft if isinstance(execution_draft, dict) else {}
        blocks = self._dict_items(execution_draft.get("blocks"))
        statement_support = self._dict_items(validation.get("statement_support"))
        return {
            "projection_kind": "answer_guard_feedback",
            "schema_name": artifact.get("schema_name"),
            "status": artifact.get("status"),
            "accepted": validation.get("accepted") is True,
            "instruction": (
                "不得原样重试被拒绝的 Answer。先按 required_actions 补齐或修正证据；"
                "跨招标资料与企业资料的比较结论必须让每个 Statement 同时满足所需"
                "source basis。只有 citable Evidence Atom 可用于最终引用。若一侧证据仍"
                "不足，应明确标记 unknown，并建立 Statement 与 Limitation 的双向引用。"
                "Resource Identity Receipt 只证明资料身份和加载状态，不能证明某项业务"
                "内容缺失。若当前 Context 中仍有 search_candidates，应先通过 "
                "evidence_read 将候选升级为 Evidence Atom，再重新生成 Answer。"
            ),
            "required_actions": required_actions,
            "issues": issue_projection,
            "draft_blocks": [
                self._compact_answer_block(block) for block in blocks[:128]
            ],
            "statement_support": [
                {
                    "statement_ref": item.get("statement_ref"),
                    "claim_type": item.get("claim_type"),
                    "epistemic_status": item.get("epistemic_status"),
                    "source_bases": item.get("source_bases") or [],
                    "grounding_refs": (item.get("grounding_refs") or [])[:32],
                    "limitation_refs": (item.get("limitation_refs") or [])[:16],
                    "citation_ready": item.get("citation_ready") is True,
                    "publishable": item.get("publishable") is True,
                }
                for item in statement_support[:128]
            ],
            "validated_grounding_refs": (
                validation.get("validated_grounding_refs") or []
            )[:128],
            "limitation_codes": validation.get("limitation_codes") or [],
        }

    def _compact_answer_block(self, block: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "block_type",
            "block_id",
            "claim_type",
            "epistemic_status",
            "grounding_refs",
            "quote_refs",
            "limitation_refs",
            "premise_or_trigger",
        )
        compact = {key: block[key] for key in keys if key in block}
        if "text" in block:
            compact["text"] = self._bounded_text(
                block.get("text"),
                _MAX_COMPACT_DRAFT_TEXT_CHARS,
            )
        return compact

    @staticmethod
    def _answer_issue_action(code: Any) -> str:
        actions = {
            "support_matrix_unsatisfied": (
                "acquire_citable_evidence_for_each_required_source_basis_then_retry"
            ),
            "limitation_receipt_invalid": (
                "upgrade_search_candidates_with_evidence_read_or_use_compatible_"
                "limitation_receipt"
            ),
            "citation_not_ready": "replace_search_candidate_with_evidence_read_atom",
            "grounding_ref_unknown": "select_a_grounding_ref_present_in_current_context",
            "grounding_not_in_context": "read_evidence_into_current_context_before_answer",
            "grounding_status_not_publishable": (
                "use_current_publishable_evidence_or_state_the_limitation"
            ),
            "grounding_source_not_current": "retrieve_current_source_version_evidence",
            "quote_ref_unknown": "remove_unknown_quote_or_read_authoritative_quote",
            "quote_span_invalid": "correct_or_remove_the_direct_quote",
            "conflict_groups_insufficient": (
                "retrieve_both_conflict_sides_or_report_the_unresolved_conflict"
            ),
        }
        return actions.get(str(code), "resolve_guard_issue_before_retrying_answer")

    @staticmethod
    def _dict_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)] + "…"

    def _observation_receipt_candidate(
        self,
        *,
        task: AgentTaskState,
        observation_ref: str,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        return self._candidate(
            identity={"kind": "observation_receipt", "ref": observation_ref},
            stable_key=f"observation:{observation_ref}",
            source_ref=observation_ref,
            source_version_ref=self._version_ref(
                "observation-head",
                {
                    "task_ref": task.task_id,
                    "state_version": task.state_version,
                    "observation_ref": observation_ref,
                },
            ),
            request=request,
            lane=ContextLane.OBSERVATION_GROUNDING,
            kind=ContextEntryKind.OBSERVATION,
            representation=ContextRepresentation.REF_ONLY,
            authority_label="persisted-observation-receipt",
            protection_class=ContextProtectionClass.ELASTIC,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=canonical_json(
                {
                    "observation_ref": observation_ref,
                    "observation_content_loaded": False,
                }
            ),
            required=False,
            material_if_omitted=True,
            priority=75,
            omission_action=ContextOmissionAction.LIMIT,
            compression_level=ContextCompressionLevel.L4,
        )

    def _interaction_candidate(
        self,
        *,
        row: ContextMessageRow,
        request: ContextAssemblyRequest,
    ) -> ContextEntryCandidate:
        content = canonical_json(
            {
                "role": row.role,
                "message_type": row.message_type,
                "content": row.content,
                "reply_to_message_ref": row.reply_to_message_ref,
            }
        )
        version_ref = self._version_ref(
            "interaction-version",
            {"message_ref": row.message_ref, "content_hash": row.content_hash},
        )
        return self._candidate(
            identity={"kind": "conversation_message", "version_ref": version_ref},
            stable_key=f"interaction:{row.message_ref}",
            source_ref=row.message_ref,
            source_version_ref=version_ref,
            request=request,
            lane=ContextLane.RELEVANT_INTERACTION,
            kind=ContextEntryKind.CONVERSATION_MESSAGE,
            representation=ContextRepresentation.STRUCTURED_PROJECTION,
            authority_label="persisted-conversation-message",
            protection_class=ContextProtectionClass.ELASTIC,
            trust_class=ContextTrustClass.UNTRUSTED_DATA,
            content=content,
            source_content_hash=f"sha256:{row.content_hash}",
            required=False,
            material_if_omitted=False,
            priority=50,
            omission_action=ContextOmissionAction.LIMIT,
            compression_level=ContextCompressionLevel.L1,
        )

    @classmethod
    def _candidate(
        cls,
        *,
        identity: Any,
        stable_key: str,
        source_ref: str,
        source_version_ref: str,
        request: ContextAssemblyRequest,
        lane: ContextLane,
        kind: ContextEntryKind,
        representation: ContextRepresentation,
        authority_label: str,
        protection_class: ContextProtectionClass,
        trust_class: ContextTrustClass,
        content: str,
        source_content_hash: str | None = None,
        required: bool,
        material_if_omitted: bool,
        priority: int,
        omission_action: ContextOmissionAction,
        compression_level: ContextCompressionLevel = ContextCompressionLevel.NONE,
        tool_name: str | None = None,
        protocol_pair_ref: str | None = None,
        entry_ref: str | None = None,
    ) -> ContextEntryCandidate:
        if len(content) > _MAX_CONTEXT_CONTENT_CHARS:
            raise ContextSourceUnavailable(
                "persisted Context candidate exceeds the bounded content contract"
            )
        return ContextEntryCandidate(
            entry_ref=entry_ref or cls._entry_ref(identity),
            stable_key=stable_key,
            source_ref=source_ref,
            source_version_ref=source_version_ref,
            source_content_hash=source_content_hash or canonical_hash(content),
            authorization_snapshot_ref=request.authorization_snapshot_ref,
            lane=lane,
            kind=kind,
            representation=representation,
            authority_label=authority_label,
            protection_class=protection_class,
            trust_class=trust_class,
            content=content,
            token_count=cls._conservative_token_estimate(content),
            required=required,
            material_if_omitted=material_if_omitted,
            priority=priority,
            compression_level=compression_level,
            omission_action=omission_action,
            tool_name=tool_name,
            protocol_pair_ref=protocol_pair_ref,
        )

    @staticmethod
    def _entry_ref(identity: Any) -> str:
        return "context-entry:" + canonical_hash(identity).removeprefix("sha256:")

    @staticmethod
    def _version_ref(prefix: str, value: Any) -> str:
        return f"{prefix}:" + canonical_hash(value).removeprefix("sha256:")

    @staticmethod
    def _conservative_token_estimate(content: str) -> int:
        return max(1, (len(content.encode("utf-8")) + 1) // 2 + 8)


class PersistedContextSnapshotStore:
    """Store Context receipts through the caller-owned repository transaction."""

    def __init__(self, repository: PureAgentRepository) -> None:
        self._repository = repository

    async def save(self, snapshot: ContextSnapshot) -> None:
        try:
            self._repository.store_context_snapshot(snapshot)
        except PureAgentPersistenceError as exc:
            raise ContextInvocationRejected(
                "persisted Context Snapshot was rejected"
            ) from exc

    async def load(self, snapshot_ref: str, *, task_ref: str) -> ContextSnapshot:
        try:
            return self._repository.load_context_snapshot(
                task_id=task_ref,
                snapshot_ref=snapshot_ref,
            )
        except PureAgentPersistenceError as exc:
            raise ContextStoreUnavailable(
                "persisted Context Snapshot does not exist"
            ) from exc


class PersistedConservativeContextTokenCounter:
    """Count source-estimated entries only for an explicitly matching profile."""

    async def count(
        self,
        *,
        request: ContextAssemblyRequest,
        entries: tuple[ContextEntryCandidate, ...],
        model_profile: ModelContextProfile,
    ) -> int:
        del request
        if model_profile.token_counter_mode is not TokenCounterMode.CONSERVATIVE_ESTIMATOR:
            raise ContextCounterUnavailable(
                "persisted Context requires a conservative-estimator model profile"
            )
        return model_profile.framing_tokens + sum(
            entry.token_count for entry in entries
        )


@dataclass(frozen=True, slots=True)
class PersistedContextAdapterFactories:
    """Repository-scoped C03-2 factories with no construction-time I/O."""

    projection_policy: PersistedContextProjectionPolicy

    def candidate_source(
        self,
        repository: PureAgentRepository,
    ) -> PersistedContextCandidateSource:
        return PersistedContextCandidateSource(
            repository,
            policy=self.projection_policy,
        )

    @staticmethod
    def snapshot_store(
        repository: PureAgentRepository,
    ) -> PersistedContextSnapshotStore:
        return PersistedContextSnapshotStore(repository)

    def context_assembler(
        self,
        repository: PureAgentRepository,
    ) -> ContextAssemblerRuntime:
        return ContextAssemblerRuntime(
            candidate_source=self.candidate_source(repository),
            token_counter=PersistedConservativeContextTokenCounter(),
            snapshot_store=self.snapshot_store(repository),
        )
