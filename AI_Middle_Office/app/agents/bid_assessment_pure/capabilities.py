"""Fail-closed capability container; intentionally not an execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from .action_runtime import DynamicActionLoopRuntime
from .answer_runtime import GroundingIntegrityGuard
from .citation_runtime import AnswerBlockRenderer, CitationProjector
from .context_runtime import ContextAssemblerRuntime
from .decision_runtime import MainAgentDecisionRuntime
from .memory_runtime import MemoryCommitter, MemoryReader
from .provider_runtime import ProviderAdapter
from .response_runtime import AnswerCommitRuntime, ResponseVersionController
from .runtime_guards import RuntimeGuardSuite


@dataclass(slots=True)
class MainAgentRuntimeCapabilities:
    """Independent capabilities selected at each dynamic Action boundary.

    There is deliberately no ``run``, stage list, next-node edge, or fixed call
    order.  Every default that could read, write, count, plan, or call a provider
    remains fail-closed until an authorized adapter is injected.  The Guard
    Suite is active pure logic and has no external side effects.
    """

    decision: MainAgentDecisionRuntime = field(default_factory=MainAgentDecisionRuntime)
    context: ContextAssemblerRuntime = field(default_factory=ContextAssemblerRuntime)
    memory_reader: MemoryReader = field(default_factory=MemoryReader)
    memory_committer: MemoryCommitter = field(default_factory=MemoryCommitter)
    provider: ProviderAdapter = field(default_factory=ProviderAdapter)
    action_loop: DynamicActionLoopRuntime = field(
        default_factory=DynamicActionLoopRuntime
    )
    answer_guard: GroundingIntegrityGuard = field(default_factory=GroundingIntegrityGuard)
    citation_projector: CitationProjector = field(default_factory=CitationProjector)
    answer_renderer: AnswerBlockRenderer = field(default_factory=AnswerBlockRenderer)
    answer_committer: AnswerCommitRuntime = field(default_factory=AnswerCommitRuntime)
    response_versions: ResponseVersionController = field(
        default_factory=ResponseVersionController
    )
    guards: RuntimeGuardSuite = field(default_factory=RuntimeGuardSuite)
