"""Persisted local Context Candidate Source and immutable Snapshot Store.

The adapters project already-authorized local persistence into the six-lane
Context contract.  They do not retrieve evidence, summarize with a model,
reconstruct incomplete Tool protocol messages, select an Action, or commit the
caller-owned database transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

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
    extract_persisted_evidence_atoms,
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


class PersistedContextProjectionPolicy(StrictContract):
    """Explicit local text and Registry authority visible to Context assembly."""

    policy_snapshot_ref: Reference
    prompt_template_ref: Reference
    system_policy: str = Field(min_length=1, max_length=32_768)
    output_contract: str = Field(min_length=1, max_length=32_768)
    registry_snapshot: RegistrySnapshot | None = None
    max_interaction_messages: int = Field(default=20, ge=0, le=50)


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
        candidates.extend(self._observation_candidates(task=task, request=request))

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
        return tuple(
            self._candidate(
                identity={
                    "kind": "resource_receipt",
                    "resource_ref": resource_ref,
                    "authorization_ref": request.authorization_snapshot_ref,
                },
                stable_key=f"resource:{canonical_hash(resource_ref)[7:23]}",
                source_ref=resource_ref,
                source_version_ref=request.authorization_snapshot_ref,
                request=request,
                lane=ContextLane.OBSERVATION_GROUNDING,
                kind=ContextEntryKind.GROUNDING,
                representation=ContextRepresentation.REF_ONLY,
                authority_label="authorized-resource-receipt",
                protection_class=ContextProtectionClass.PROTECTED,
                trust_class=ContextTrustClass.UNTRUSTED_DATA,
                content=canonical_json(
                    {
                        "resource_ref": resource_ref,
                        "authorization_bound": True,
                        "evidence_loaded": False,
                        "instruction": (
                            "This receipt is not evidence. Use an authorized Tool "
                            "before making resource-backed claims."
                        ),
                    }
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
                    evidence_atoms = extract_persisted_evidence_atoms(artifact)
                except PersistedEvidenceArtifactRejected as exc:
                    raise ContextSourceUnavailable(
                        "persisted Evidence Atom authority is invalid"
                    ) from exc
                candidates.extend(
                    self._evidence_atom_candidate(atom=atom, request=request)
                    for atom in evidence_atoms
                )
        return tuple(candidates)

    def _evidence_atom_candidate(
        self,
        *,
        atom: PersistedEvidenceAtomAuthority,
        request: ContextAssemblyRequest,
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
            authority_label="persisted-evidence-read-authority",
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
        content = canonical_json(source_payload)
        authority_label = "persisted-observation-artifact"
        compression_level = ContextCompressionLevel.L0
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
