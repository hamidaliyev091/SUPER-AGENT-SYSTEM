"""Core data contracts (Phase 1).

Field sets follow the frozen canonical documents: INTERFACES.md, TASK_SCHEMA.md,
POLICY.md, VERIFICATION.md, CONTINUITY.md, SECURITY.md. Serialization is
deterministic JSON (sorted keys). Deserialization is strict: unknown fields,
missing required fields, wrong types, and invalid enum values raise
ValidationError (fail closed per POLICY.md s4).
"""
from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, field, fields as _dc_fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Union, get_args, get_origin, get_type_hints

from .enums import (
    ActorType,
    ApprovalDecision,
    CompletionDecisionValue,
    EffortLevel,
    Idempotency,
    IndependenceLevel,
    PermissionMode,
    PolicyDecisionValue,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TaskState,
    TargetType,
    VerificationStatus,
)


class ContractError(Exception):
    """Base class for contract violations."""


class ValidationError(ContractError):
    """Raised when a value violates the canonical schema (fail closed)."""


def utcnow_iso() -> str:
    """Current time as a UTC ISO-8601 timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; returns None when unparseable (fail closed)."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        out = {}
        for f in _dc_fields(obj):
            if not f.repr:
                continue  # e.g. Tool.execute - not part of the data contract
            value = getattr(obj, f.name)
            if callable(value):
                continue
            out[f.name] = _to_jsonable(value)
        return out
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_jsonable(value) for key, value in obj.items()}
    return obj


def _convert(name: str, hint: Any, value: Any) -> Any:
    origin = get_origin(hint)
    args = get_args(hint)
    if origin is Union:
        if value is None and type(None) in args:
            return None
        last_error = None
        for arg in args:
            if arg is type(None):
                continue
            try:
                return _convert(name, arg, value)
            except ValidationError as exc:
                last_error = exc
        raise ValidationError(f"field {name!r}: {last_error}")
    if origin is list:
        if not isinstance(value, list):
            raise ValidationError(f"field {name!r}: expected list, got {type(value).__name__}")
        return [_convert(name, args[0], item) for item in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ValidationError(f"field {name!r}: expected object, got {type(value).__name__}")
        if args and args[1] is not str:
            return {key: _convert(name, args[1], item) for key, item in value.items()}
        return dict(value)
    if hint is list:
        if not isinstance(value, list):
            raise ValidationError(f"field {name!r}: expected list, got {type(value).__name__}")
        return value
    if hint is dict:
        if not isinstance(value, dict):
            raise ValidationError(f"field {name!r}: expected object, got {type(value).__name__}")
        return dict(value)
    if hint is Any:
        return value
    if hint is bool:
        if not isinstance(value, bool):
            raise ValidationError(f"field {name!r}: expected bool, got {type(value).__name__}")
        return value
    if hint is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"field {name!r}: expected int, got {type(value).__name__}")
        return value
    if hint is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"field {name!r}: expected number, got {type(value).__name__}")
        return float(value)
    if hint is str:
        if not isinstance(value, str):
            raise ValidationError(f"field {name!r}: expected string, got {type(value).__name__}")
        return value
    if isinstance(hint, type):
        if issubclass(hint, Enum):
            if isinstance(value, hint):
                return value
            try:
                return hint(value)
            except ValueError:
                raise ValidationError(
                    f"field {name!r}: {value!r} is not a valid {hint.__name__}"
                ) from None
        if is_dataclass(hint):
            return hint.from_dict(value)
    raise ValidationError(f"field {name!r}: unsupported type {hint!r}")


def _from_dict(cls: type, data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValidationError(f"{cls.__name__}: expected object, got {type(data).__name__}")
    known = {f.name for f in _dc_fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValidationError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
    hints = get_type_hints(cls)
    kwargs: dict = {}
    for f in _dc_fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(f.name, hints[f.name], data[f.name])
        elif f.default is MISSING and f.default_factory is MISSING:
            raise ValidationError(f"{cls.__name__}: missing required field {f.name!r}")
    return kwargs


class _Contract:
    """Common strict serialization for all contract types."""

    def to_dict(self) -> dict:
        return _to_jsonable(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**_from_dict(cls, data))

    @classmethod
    def from_json(cls, text: str):
        return cls.from_dict(json.loads(text))

@dataclass
class ActorIdentity(_Contract):
    """SECURITY.md s5 - identifiable actor for every security-relevant action."""
    actorId: str
    actorType: ActorType
    taskId: Optional[str] = None
    parentTaskId: Optional[str] = None


@dataclass
class Target(_Contract):
    """A canonicalized operation target (TASK_SCHEMA.md s17)."""
    type: TargetType
    value: str


@dataclass
class Requirement(_Contract):
    id: str
    description: str
    source: str = "user"
    mandatory: bool = True


@dataclass
class SuccessCriterion(_Contract):
    """VERIFICATION.md s5 - observable, testable, with a verification method."""
    id: str
    description: str
    verificationMethod: str
    evidenceRequirements: list[str] = field(default_factory=list)
    mandatory: bool = True
    riskLevel: RiskLevel = RiskLevel.LOW


@dataclass
class CompletionContract(_Contract):
    """VERIFICATION.md s4 - must be persisted before autonomous execution."""
    objective: str
    successCriteria: list[SuccessCriterion] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    riskClassification: RiskLevel = RiskLevel.MEDIUM
    requiredIndependenceLevel: IndependenceLevel = IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME
    resourceConstraints: Optional[dict] = None
    policyConstraints: Optional[dict] = None


@dataclass
class PolicyContext(_Contract):
    """INTERFACES.md s6. The model must not modify it directly."""
    policyVersion: str
    ruleVersion: str
    permissionMode: PermissionMode
    defaultRiskLevel: RiskLevel
    actorContext: Optional[ActorIdentity] = None
    environmentContext: dict = field(default_factory=dict)
    authorizationContext: dict = field(default_factory=dict)


@dataclass
class TargetAuthorizationContext(_Contract):
    """TASK_SCHEMA.md s12 - canonical target authorization schema."""
    schemaVersion: str
    allowedReadPaths: list[str] = field(default_factory=list)
    allowedWritePaths: list[str] = field(default_factory=list)
    allowedSearchPaths: list[str] = field(default_factory=list)
    allowedPackages: list[str] = field(default_factory=list)
    allowedPackageOperations: list[str] = field(default_factory=list)
    allowedNetworkDomains: list[str] = field(default_factory=list)
    allowedNetworkDestinations: list[str] = field(default_factory=list)
    allowedUIActions: list[str] = field(default_factory=list)
    allowedProcesses: list[str] = field(default_factory=list)
    allowedAndroidSettings: list[str] = field(default_factory=list)
    allowedResources: list[str] = field(default_factory=list)
    deniedTargets: list[str] = field(default_factory=list)
    createdAt: str = ""
    updatedAt: str = ""
    expiresAt: Optional[str] = None
    authorizationReference: str = ""

    def is_active(self, now: Optional[str] = None) -> bool:
        """Expired or unparseable authorization fails closed (TASK_SCHEMA s19)."""
        if self.expiresAt is None:
            return True
        expires = parse_iso(self.expiresAt)
        if expires is None:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now is None:
            current = datetime.now(timezone.utc)
        else:
            current = parse_iso(now)
            if current is None:
                return False
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
        return current < expires


@dataclass
class ResourceLimits(_Contract):
    """INTERFACES.md s22 / TASK_SCHEMA.md s20. Externally enforced."""
    wallClockTime: Optional[int] = None  # seconds
    actionSteps: Optional[int] = None
    modelCalls: Optional[int] = None
    retryCount: Optional[int] = None
    delegationCount: Optional[int] = None
    network: Optional[int] = None  # bytes
    storage: Optional[int] = None  # bytes


@dataclass
class PlanStep(_Contract):
    id: str
    description: str


@dataclass
class Plan(_Contract):
    id: str
    steps: list[PlanStep] = field(default_factory=list)
    createdAt: str = ""
    updatedAt: str = ""


@dataclass
class Decision(_Contract):
    """CONTINUITY.md s25 - durable important-decision record."""
    decisionId: str
    taskId: str
    timestamp: str
    decision: str
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    affectedComponents: list[str] = field(default_factory=list)
    policyImplications: str = ""

@dataclass
class FailureRecord(_Contract):
    """INTERFACES.md s27 / TASK_SCHEMA.md s25. Durable, material failures."""
    failureId: str
    taskId: str
    timestamp: str
    category: str
    sideEffectState: SideEffectState
    retryCount: int = 0
    actionId: Optional[str] = None
    operation: Optional[str] = None
    target: Optional[Target] = None
    error: Optional[Error] = None
    description: str = ""
    retryable: bool = False
    recoveryRecommendation: Optional[str] = None


@dataclass
class VerificationState(_Contract):
    """Current verification status of a task (TASK_SCHEMA.md s24)."""
    overall: Optional[VerificationStatus] = None
    criterionResults: dict[str, VerificationStatus] = field(default_factory=dict)
    updatedAt: str = ""


@dataclass
class Checkpoint(_Contract):
    """CONTINUITY.md s6 - recoverable snapshot of task state."""
    checkpointId: str
    taskId: str
    timestamp: str
    lifecycleState: TaskState
    objective: str = ""
    successCriteria: list[SuccessCriterion] = field(default_factory=list)
    currentPlan: Optional[Plan] = None
    completedSteps: list[str] = field(default_factory=list)
    activeStep: Optional[str] = None
    pendingActions: list[str] = field(default_factory=list)
    recentFailures: list[FailureRecord] = field(default_factory=list)
    resourceUsage: dict = field(default_factory=dict)
    remainingBudget: Optional[ResourceLimits] = None
    policyContext: Optional[PolicyContext] = None
    authorizationContext: Optional[TargetAuthorizationContext] = None
    verificationStatus: Optional[VerificationState] = None
    knownSideEffects: list[str] = field(default_factory=list)
    unknownSideEffects: list[str] = field(default_factory=list)
    continuitySummary: str = ""


@dataclass
class ContinuityState(_Contract):
    """CONTINUITY.md s3/s17 - durable continuity information (light, Phase 1).

    Enriched by the Continuity Manager in Phase 7; the fields here are the
    schema minimum needed for the authoritative Task representation.
    """
    knownSideEffects: list[str] = field(default_factory=list)
    unknownSideEffects: list[str] = field(default_factory=list)
    nextSafeAction: Optional[str] = None
    lastCheckpointId: Optional[str] = None
    updatedAt: str = ""


@dataclass
class Task(_Contract):
    """TASK_SCHEMA.md s2 - the canonical authoritative Task object."""
    id: str
    schemaVersion: str
    objective: str
    requirements: list[Requirement]
    successCriteria: list[SuccessCriterion]
    completionContract: Optional[CompletionContract]
    permissionMode: PermissionMode
    effortLevel: EffortLevel
    policyContext: PolicyContext
    targetAuthorizationContext: TargetAuthorizationContext
    resourceLimits: ResourceLimits
    state: TaskState
    verification: VerificationState
    continuity: ContinuityState
    createdAt: str
    updatedAt: str
    plan: Optional[Plan] = None
    decisions: list[Decision] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    parentTaskId: Optional[str] = None
    subtaskIds: list[str] = field(default_factory=list)

@dataclass
class Error(_Contract):
    """INTERFACES.md s30 - structured error."""
    code: str
    message: str
    retryable: bool
    severity: str = "ERROR"
    context: Optional[dict] = None


@dataclass
class Tool(_Contract):
    """INTERFACES.md s11 / SECURITY.md s17. Metadata is descriptive only;
    authoritative classifications come from POLICY_RULES.md."""
    id: str
    name: str
    description: str
    inputSchema: dict
    outputSchema: dict
    riskLevel: RiskLevel
    sideEffect: SideEffect
    reversibility: Reversibility
    requiredPermissions: list[str] = field(default_factory=list)
    version: str = "1.0"
    operationId: Optional[str] = None
    idempotency: Optional[Idempotency] = None
    resourceRequirements: Optional[dict] = None
    verificationRequirements: Optional[dict] = None
    execute: Optional[Callable] = field(default=None, repr=False, compare=False)


@dataclass
class Evidence(_Contract):
    """SECURITY.md s21 - evidence with provenance and integrity metadata."""
    evidenceId: str
    timestamp: str
    source: str
    collectorIdentity: str
    contentHash: Optional[str] = None
    provenance: Optional[str] = None
    criterionId: Optional[str] = None
    validationStatus: Optional[str] = None


@dataclass
class ToolResult(_Contract):
    """INTERFACES.md s13."""
    success: bool
    sideEffectState: SideEffectState
    timestamp: str
    output: Optional[dict] = None
    evidence: list[Evidence] = field(default_factory=list)
    error: Optional[Error] = None
    sideEffects: list[SideEffect] = field(default_factory=list)


@dataclass
class ActionRequest(_Contract):
    """INTERFACES.md s9 - a proposal, never an authorization."""
    taskId: str
    actor: ActorIdentity
    toolId: str
    target: Target
    arguments: dict
    reason: str
    requestedRiskLevel: Optional[RiskLevel] = None
    requestedSideEffect: Optional[SideEffect] = None
    requestedReversibility: Optional[Reversibility] = None
    requestedIdempotency: Optional[Idempotency] = None


@dataclass
class PolicyRequest(_Contract):
    """INTERFACES.md s9 / POLICY_RULES.md s5 - complete authorization input.

    The requested* fields are informational only; the Policy Engine must
    independently resolve authoritative values from registered rules.
    """
    policyVersion: str
    ruleVersion: str
    taskId: str
    taskState: TaskState
    permissionMode: PermissionMode
    effortLevel: EffortLevel
    actorContext: ActorIdentity
    environmentContext: dict
    authorizationContext: dict
    targetAuthorizationContext: TargetAuthorizationContext
    toolId: str
    operationId: str
    target: Target
    structuredArguments: dict
    requestedRiskLevel: Optional[RiskLevel] = None
    requestedSideEffect: Optional[SideEffect] = None
    requestedReversibility: Optional[Reversibility] = None
    requestedIdempotency: Optional[Idempotency] = None


@dataclass
class ApprovalRequest(_Contract):
    """INTERFACES.md s21. Single-use; scope-bound; time-bounded."""
    taskId: str
    operation: str
    target: Target
    riskLevel: RiskLevel
    sideEffect: SideEffect
    reversibility: Reversibility
    idempotency: Idempotency
    explanation: str
    consequences: str
    scope: dict
    expiresAt: str
    policyVersion: str
    ruleVersion: str
    approvalReference: str
    nonce: str
    arguments: Optional[dict] = None


@dataclass
class ApprovalResult(_Contract):
    """INTERFACES.md s21."""
    approvalReference: str
    decision: ApprovalDecision
    approverIdentity: ActorIdentity
    timestamp: str


@dataclass
class PolicyDecision(_Contract):
    """INTERFACES.md s10."""
    decision: PolicyDecisionValue
    reason: str
    policyVersion: str
    ruleVersion: str
    approvalRequirement: Optional[ApprovalRequest] = None
    constraints: list[dict] = field(default_factory=list)


@dataclass
class VerificationResult(_Contract):
    """VERIFICATION.md s6. INCONCLUSIVE must never produce DONE."""
    taskId: str
    criterionId: str
    result: VerificationStatus
    verifier: str
    independenceLevel: IndependenceLevel
    verificationMethod: str
    timestamp: str
    evidence: list[Evidence] = field(default_factory=list)
    evidenceFreshness: Optional[str] = None
    failureReason: Optional[str] = None
    notes: Optional[str] = None
    integrityMetadata: Optional[dict] = None


@dataclass
class CompletionDecision(_Contract):
    """VERIFICATION.md s17 / INTERFACES.md s20. Only the Completion Engine
    may produce a decision with value DONE (enforced in later phases;
    the value union follows ADR-002)."""
    taskId: str
    decision: CompletionDecisionValue
    schemaVersion: str
    timestamp: str
    reasonCode: Optional[str] = None
    reason: str = ""
    criterionResults: Optional[dict] = None
    policyCompliance: Optional[dict] = None
    resourceCompliance: Optional[dict] = None
    evidenceReferences: list[str] = field(default_factory=list)
    verificationReferences: list[str] = field(default_factory=list)
    policyVersion: Optional[str] = None
    integrityMetadata: Optional[dict] = None
