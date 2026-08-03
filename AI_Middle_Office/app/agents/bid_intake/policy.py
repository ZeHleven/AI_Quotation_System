from __future__ import annotations

from typing import Any
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.bid_policy_catalog import (
    active_bid_policy_version,
    bid_policy_path,
)

from .contracts import (
    AssessmentDraft,
    DocumentManifest,
    PolicyDecision,
    PolicyEvaluation,
    PolicyFactorInput,
    PolicyFactorRating,
    PolicyFactorResult,
    PolicyFactorSource,
    PolicyRuleHit,
    ToolResult,
    ToolResultStatus,
)


class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactorDefinition(_StrictConfig):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    weight: float = Field(gt=0, le=100)
    critical_unknown: bool = False
    guidance: str = Field(min_length=1)


class HardRuleDefinition(_StrictConfig):
    id: str = Field(min_length=1)
    factor_id: str = Field(min_length=1)
    ratings: list[PolicyFactorRating] = Field(min_length=1)
    level: str = "hard"
    outcome: str
    message: str = Field(min_length=1)


class DecisionThresholds(_StrictConfig):
    recommend_quote_min: float = Field(ge=0, le=100)
    conditional_quote_min: float = Field(ge=0, le=100)
    min_coverage_for_quote: float = Field(ge=0, le=100)
    min_coverage_for_conditional: float = Field(ge=0, le=100)


class PolicyTopic(_StrictConfig):
    description: str = Field(min_length=1)


class BidPolicyConfig(_StrictConfig):
    schema_version: str
    policy_version: str
    status: str
    owner_role: str
    scope: str
    title: str
    required_documents: list[str] = Field(min_length=1)
    rating_scores: dict[PolicyFactorRating, float]
    decision_thresholds: DecisionThresholds
    factors: list[FactorDefinition] = Field(min_length=1)
    hard_rules: list[HardRuleDefinition] = Field(default_factory=list)
    topics: dict[str, PolicyTopic] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "BidPolicyConfig":
        factor_ids = [item.id for item in self.factors]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("policy factors must be unique")
        if abs(sum(item.weight for item in self.factors) - 100) > 0.001:
            raise ValueError("policy factor weights must sum to 100")
        if set(self.rating_scores) != set(PolicyFactorRating):
            raise ValueError("rating_scores must cover every rating")
        if any(
            item.factor_id not in set(factor_ids)
            for item in self.hard_rules
        ):
            raise ValueError("hard rule references an unknown factor")
        thresholds = self.decision_thresholds
        if (
            thresholds.conditional_quote_min
            > thresholds.recommend_quote_min
        ):
            raise ValueError("conditional threshold exceeds quote threshold")
        return self


class YamlBidPolicy:
    """Versioned, deterministic bid/no-bid policy loaded from the Skill."""

    def __init__(self, config: BidPolicyConfig):
        self._config = config

    @classmethod
    def from_active(cls) -> "YamlBidPolicy":
        return cls.from_version(active_bid_policy_version())

    @classmethod
    def from_version(cls, version: str) -> "YamlBidPolicy":
        path = bid_policy_path(version)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError("BID_POLICY_LOAD_FAILED") from exc
        return cls.from_payload(payload, expected_version=version)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        expected_version: str | None = None,
    ) -> "YamlBidPolicy":
        config = BidPolicyConfig.model_validate(payload)
        if (
            expected_version is not None
            and config.policy_version != expected_version
        ):
            raise RuntimeError("BID_POLICY_VERSION_MISMATCH")
        return cls(config)

    @property
    def version(self) -> str:
        return self._config.policy_version

    @property
    def config_snapshot(self) -> dict[str, Any]:
        return self._config.model_dump(mode="json")

    @property
    def prompt_context(self) -> dict[str, Any]:
        return {
            "policy_version": self.version,
            "rating_definitions": {
                rating.value: score
                for rating, score in self._config.rating_scores.items()
            },
            "required_policy_factors": [
                {
                    "factor_id": item.id,
                    "name": item.name,
                    "guidance": item.guidance,
                    "critical_unknown": item.critical_unknown,
                }
                for item in self._config.factors
            ],
        }

    def get_rule(self, *, topic: str) -> ToolResult:
        normalized = str(topic or "").strip()
        configured = self._config.topics.get(normalized)
        if configured is not None:
            data: dict[str, Any] = {
                "topic": normalized,
                "description": configured.description,
            }
            if normalized == "评分口径":
                data["factors"] = [
                    item.model_dump(mode="json")
                    for item in self._config.factors
                ]
                data["rating_scores"] = {
                    key.value: value
                    for key, value in self._config.rating_scores.items()
                }
                data["decision_thresholds"] = (
                    self._config.decision_thresholds.model_dump(
                        mode="json"
                    )
                )
            if normalized == "立项硬门槛":
                data["hard_rules"] = [
                    item.model_dump(mode="json")
                    for item in self._config.hard_rules
                ]
            return ToolResult(
                status=ToolResultStatus.OK,
                data={
                    "policy_version": self.version,
                    "rule": data,
                },
                trace_id=f"policy-{uuid4()}",
            )
        return ToolResult(
            status=ToolResultStatus.NO_RESULT,
            data={
                "topic": normalized,
                "available_topics": sorted(self._config.topics),
            },
            trace_id=f"policy-{uuid4()}",
            message="No policy rule matched the requested topic.",
        )

    def evaluate(
        self,
        *,
        draft: AssessmentDraft,
        manifest: DocumentManifest,
    ) -> PolicyEvaluation:
        active_types = {
            item.document_type
            for item in manifest.active_documents
            if item.parse_status != "failed"
        }
        document_gaps = [
            item
            for item in self._config.required_documents
            if item not in active_types
        ]
        inputs = {item.factor_id: item for item in draft.policy_factors}
        configured_factor_ids = {
            item.id for item in self._config.factors
        }
        unexpected_factor_ids = sorted(
            set(inputs) - configured_factor_ids
        )
        if unexpected_factor_ids:
            raise ValueError(
                "UNKNOWN_POLICY_FACTOR:"
                + ",".join(unexpected_factor_ids)
            )
        factor_results = [
            self._evaluate_factor(definition, inputs.get(definition.id))
            for definition in self._config.factors
        ]
        result_by_id = {item.factor_id: item for item in factor_results}
        hard_hits = [
            PolicyRuleHit(
                rule_id=rule.id,
                level="hard",
                message=rule.message,
                factor_id=rule.factor_id,
                outcome=rule.outcome,
                evidence_refs=result_by_id[
                    rule.factor_id
                ].evidence_refs,
            )
            for rule in self._config.hard_rules
            if result_by_id[rule.factor_id].rating in set(rule.ratings)
        ]
        known_weight = sum(
            item.weight for item in factor_results if item.known
        )
        score = round(
            sum(item.weighted_score for item in factor_results),
            2,
        )
        coverage = round(known_weight, 2)
        critical_unknowns = [
            item.factor_id
            for item in factor_results
            if item.critical_unknown and not item.known
        ]
        status, decision = self._resolve_decision(
            score=score,
            coverage=coverage,
            document_gaps=document_gaps,
            critical_unknowns=critical_unknowns,
            hard_hits=hard_hits,
        )
        return PolicyEvaluation(
            policy_version=self.version,
            status=status,
            decision=decision,
            hard_rule_hits=hard_hits,
            required_document_gaps=document_gaps,
            score=None if document_gaps else score,
            coverage=coverage,
            factor_results=factor_results,
            critical_unknown_factors=critical_unknowns,
            notes=[
                "总分、覆盖率和规则命中均由版本化 PolicyEngine 确定性计算。",
                "unknown 按 0 分计算，但会单独显示为信息缺失。",
            ],
        )

    def _evaluate_factor(
        self,
        definition: FactorDefinition,
        factor: PolicyFactorInput | None,
    ) -> PolicyFactorResult:
        if factor is None:
            factor = PolicyFactorInput(
                factor_id=definition.id,
                rating=PolicyFactorRating.UNKNOWN,
                summary="Agent未提供该经营因素。",
                source_type=PolicyFactorSource.UNKNOWN,
                confidence=0,
            )
        rating_score = float(
            self._config.rating_scores[factor.rating]
        )
        return PolicyFactorResult(
            factor_id=definition.id,
            name=definition.name,
            weight=definition.weight,
            rating=factor.rating,
            rating_score=rating_score,
            weighted_score=round(
                definition.weight * rating_score / 100,
                2,
            ),
            known=factor.rating != PolicyFactorRating.UNKNOWN,
            critical_unknown=definition.critical_unknown,
            summary=factor.summary,
            source_type=factor.source_type,
            source_note=factor.source_note,
            evidence_refs=factor.evidence_refs,
        )

    def _resolve_decision(
        self,
        *,
        score: float,
        coverage: float,
        document_gaps: list[str],
        critical_unknowns: list[str],
        hard_hits: list[PolicyRuleHit],
    ) -> tuple[str, PolicyDecision]:
        if hard_hits:
            return (
                "special_approval_required",
                PolicyDecision.RECOMMEND_NO_QUOTE,
            )
        if document_gaps or critical_unknowns:
            return "not_evaluable", PolicyDecision.NEED_SUPPLEMENT
        thresholds = self._config.decision_thresholds
        if (
            score >= thresholds.recommend_quote_min
            and coverage >= thresholds.min_coverage_for_quote
        ):
            return "passed", PolicyDecision.RECOMMEND_QUOTE
        if (
            score >= thresholds.conditional_quote_min
            and coverage
            >= thresholds.min_coverage_for_conditional
        ):
            return "warning", PolicyDecision.CONDITIONAL_QUOTE
        if coverage < thresholds.min_coverage_for_conditional:
            return "not_evaluable", PolicyDecision.NEED_SUPPLEMENT
        return "warning", PolicyDecision.RECOMMEND_NO_QUOTE


class InMemoryBidPolicy(YamlBidPolicy):
    """Backward-compatible name; now loads the active versioned Skill."""

    def __init__(self):
        loaded = YamlBidPolicy.from_active()
        super().__init__(loaded._config)
