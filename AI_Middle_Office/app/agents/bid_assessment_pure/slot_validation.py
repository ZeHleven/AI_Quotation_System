"""Deterministic two-stage Slot validation without user-facing raw exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator

from .common import Reference, StrictContract
from .slots import Slot, SlotValidationIssue, ValidationStage
from .tool_runtime import canonical_hash


class BusinessValidationContext(StrictContract):
    user_ref: Reference
    tenant_ref: Reference
    conversation_ref: Reference
    task_ref: Reference
    slot_ref: Reference
    authorization_snapshot_ref: Reference


class BusinessRuleDecision(StrictContract):
    accepted: bool
    code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    field: str | None = None
    message: str | None = None
    guidance: str | None = None
    retryable: bool = True

    @model_validator(mode="after")
    def validate_shape(self) -> "BusinessRuleDecision":
        details = (self.code, self.message, self.guidance)
        if self.accepted and any(value is not None for value in details):
            raise ValueError("accepted business rule forbids error details")
        if not self.accepted and any(value is None for value in details):
            raise ValueError("rejected business rule requires code, message, and guidance")
        return self


class SlotBusinessValidator(Protocol):
    def validate(
        self,
        value: BaseModel,
        *,
        context: BusinessValidationContext,
    ) -> BusinessRuleDecision: ...


class SlotRequestDefinition(StrictContract):
    """Runtime-owned definition for one recoverable user-information Slot."""

    slot_kind: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    slot_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    description: str = Field(min_length=1, max_length=500)
    input_model_ref: Reference
    business_validator_refs: tuple[Reference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_validator_refs(self) -> "SlotRequestDefinition":
        if len(self.business_validator_refs) != len(
            set(self.business_validator_refs)
        ):
            raise ValueError("business_validator_refs must be unique")
        return self


class ModelVisibleSlotCapability(StrictContract):
    """Small semantic whitelist; internal validator references stay hidden."""

    slot_kind: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    description: str = Field(min_length=1, max_length=500)


class SlotCapabilitySnapshot(StrictContract):
    """Immutable authority used to gate and materialize Slot requests."""

    snapshot_ref: Reference
    snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    definitions: tuple[SlotRequestDefinition, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )

    @classmethod
    def build(
        cls,
        definitions: tuple[SlotRequestDefinition, ...] = (),
    ) -> "SlotCapabilitySnapshot":
        ordered = tuple(sorted(definitions, key=lambda item: item.slot_kind))
        body = {
            "definitions": [item.model_dump(mode="json") for item in ordered]
        }
        digest = canonical_hash(body)
        return cls(
            snapshot_ref=(
                "slot-capability-snapshot:"
                + digest.removeprefix("sha256:")
            ),
            snapshot_hash=digest,
            definitions=ordered,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> "SlotCapabilitySnapshot":
        kinds = tuple(item.slot_kind for item in self.definitions)
        names = tuple(item.slot_name for item in self.definitions)
        if len(kinds) != len(set(kinds)):
            raise ValueError("Slot capability kinds must be unique")
        if len(names) != len(set(names)):
            raise ValueError("Slot capability names must be unique")
        body = {
            "definitions": [
                item.model_dump(mode="json") for item in self.definitions
            ]
        }
        digest = canonical_hash(body)
        if self.snapshot_hash != digest or self.snapshot_ref != (
            "slot-capability-snapshot:" + digest.removeprefix("sha256:")
        ):
            raise ValueError("Slot capability snapshot identity drifted")
        return self

    def resolve(self, slot_kind: str) -> SlotRequestDefinition | None:
        return next(
            (
                definition
                for definition in self.definitions
                if definition.slot_kind == slot_kind
            ),
            None,
        )

    def model_visible_capabilities(
        self,
    ) -> tuple[ModelVisibleSlotCapability, ...]:
        return tuple(
            ModelVisibleSlotCapability(
                slot_kind=definition.slot_kind,
                description=definition.description,
            )
            for definition in self.definitions
        )


@dataclass(frozen=True)
class SlotInputDefinition:
    model: type[BaseModel]
    format_guidance: str


@dataclass(frozen=True)
class FormatValidationResult:
    accepted: bool
    value: BaseModel | None
    issues: tuple[SlotValidationIssue, ...]


@dataclass(frozen=True)
class BusinessValidationResult:
    accepted: bool
    value: BaseModel | None
    issues: tuple[SlotValidationIssue, ...]


class SlotValidatorRegistry:
    """Small explicit registry for Pydantic input and business validators."""

    def __init__(self) -> None:
        self._input_models: dict[str, SlotInputDefinition] = {}
        self._business_validators: dict[str, SlotBusinessValidator] = {}
        self._slot_definitions: dict[str, SlotRequestDefinition] = {}

    def register_input_model(
        self,
        model_ref: str,
        model: type[BaseModel],
        *,
        format_guidance: str,
    ) -> None:
        if model_ref in self._input_models:
            raise ValueError(f"duplicate Slot input model: {model_ref}")
        if not issubclass(model, BaseModel):
            raise TypeError("Slot input model must extend Pydantic BaseModel")
        if not format_guidance.strip():
            raise ValueError("Slot format guidance must not be empty")
        self._input_models[model_ref] = SlotInputDefinition(
            model=model,
            format_guidance=format_guidance.strip(),
        )

    def register_business_validator(
        self,
        validator_ref: str,
        validator: SlotBusinessValidator,
    ) -> None:
        if validator_ref in self._business_validators:
            raise ValueError(f"duplicate Slot business validator: {validator_ref}")
        self._business_validators[validator_ref] = validator

    def register_slot_definition(
        self,
        definition: SlotRequestDefinition,
    ) -> None:
        """Register only executable definitions; never accept model-owned refs."""

        if definition.slot_kind in self._slot_definitions:
            raise ValueError(
                f"duplicate Slot request definition: {definition.slot_kind}"
            )
        if definition.input_model_ref not in self._input_models:
            raise ValueError(
                "Slot request definition input model is not registered"
            )
        missing_validators = tuple(
            validator_ref
            for validator_ref in definition.business_validator_refs
            if validator_ref not in self._business_validators
        )
        if missing_validators:
            raise ValueError(
                "Slot request definition business validator is not registered"
            )
        if any(
            item.slot_name == definition.slot_name
            for item in self._slot_definitions.values()
        ):
            raise ValueError(
                f"duplicate Slot request name: {definition.slot_name}"
            )
        self._slot_definitions[definition.slot_kind] = definition

    def freeze_capability_snapshot(self) -> SlotCapabilitySnapshot:
        return SlotCapabilitySnapshot.build(tuple(self._slot_definitions.values()))

    def validate_format(self, slot: Slot, candidate: Any) -> FormatValidationResult:
        definition = self._input_models.get(slot.input_model_ref)
        if definition is None:
            issue = SlotValidationIssue(
                slot_id=slot.slot_id,
                stage=ValidationStage.FORMAT,
                code="SLOT_INPUT_MODEL_UNAVAILABLE",
                field=None,
                message="当前输入格式暂时无法校验。",
                guidance="请稍后重试，或联系管理员检查该输入项配置。",
                retryable=True,
            )
            return FormatValidationResult(False, None, (issue,))
        try:
            value = definition.model.model_validate(candidate)
        except ValidationError as exc:
            issues = tuple(
                SlotValidationIssue(
                    slot_id=slot.slot_id,
                    stage=ValidationStage.FORMAT,
                    code="SLOT_FORMAT_INVALID",
                    field=_safe_field_path(error.get("loc", ())),
                    message="输入格式不符合要求。",
                    guidance=definition.format_guidance,
                    retryable=True,
                )
                for error in exc.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )[:20]
            )
            if not issues:
                issues = (
                    SlotValidationIssue(
                        slot_id=slot.slot_id,
                        stage=ValidationStage.FORMAT,
                        code="SLOT_FORMAT_INVALID",
                        field=None,
                        message="输入格式不符合要求。",
                        guidance=definition.format_guidance,
                        retryable=True,
                    ),
                )
            return FormatValidationResult(False, None, issues)
        return FormatValidationResult(True, value, ())

    def validate_business(
        self,
        slot: Slot,
        value: BaseModel,
        *,
        context: BusinessValidationContext,
    ) -> BusinessValidationResult:
        issues: list[SlotValidationIssue] = []
        for validator_ref in slot.business_validator_refs:
            validator = self._business_validators.get(validator_ref)
            if validator is None:
                issues.append(
                    SlotValidationIssue(
                        slot_id=slot.slot_id,
                        stage=ValidationStage.BUSINESS,
                        code="SLOT_BUSINESS_VALIDATOR_UNAVAILABLE",
                        field=None,
                        message="当前输入暂时无法完成业务校验。",
                        guidance="请稍后重试，或联系管理员检查业务规则配置。",
                        retryable=True,
                    )
                )
                continue
            try:
                decision = validator.validate(value, context=context)
            except Exception:
                # Raw exceptions may contain internal data and never cross the
                # Slot boundary. Protected logs can retain the detailed trace.
                issues.append(
                    SlotValidationIssue(
                        slot_id=slot.slot_id,
                        stage=ValidationStage.BUSINESS,
                        code="SLOT_BUSINESS_VALIDATION_UNAVAILABLE",
                        field=None,
                        message="当前输入暂时无法完成业务校验。",
                        guidance="请稍后重试；如果问题持续存在，请联系管理员。",
                        retryable=True,
                    )
                )
                continue
            if not decision.accepted:
                issues.append(
                    SlotValidationIssue(
                        slot_id=slot.slot_id,
                        stage=ValidationStage.BUSINESS,
                        code=decision.code or "SLOT_BUSINESS_RULE_REJECTED",
                        field=decision.field,
                        message=decision.message or "输入内容不符合当前业务要求。",
                        guidance=decision.guidance or "请核对内容后重新输入。",
                        retryable=decision.retryable,
                    )
                )
        if issues:
            return BusinessValidationResult(False, None, tuple(issues))
        return BusinessValidationResult(True, value, ())


def _safe_field_path(location: tuple[Any, ...]) -> str | None:
    safe_parts = [str(part)[:64] for part in location if isinstance(part, (str, int))]
    return ".".join(safe_parts)[:128] or None
