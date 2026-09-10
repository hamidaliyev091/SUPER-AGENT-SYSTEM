"""Policy Engine (Phase 3) - deterministic authorization.

Implements the evaluation precedence of POLICY_RULES.md s46 in order:

  versions -> task state -> permission mode/effort -> TAC -> explicit
  denial -> protected targets -> capability scope -> registration ->
  arguments -> risk rule -> mandatory confirmation -> HIGH/CRITICAL
  non-idempotency -> operation-specific rule -> target-specific rule ->
  permission matrix -> final decision.

A DENY at any earlier stage is final. ASK decisions carry an ApprovalRequest
(INTERFACES.md s10/s21); the engine never collects approval itself. The
requested* fields of PolicyRequest are informational and never influence
the authoritative decision (POLICY_RULES.md s5). environmentContext is
untrusted data and is never read for authority (P-RULE-14).

Decision semantics for undefined matrix combinations follow POLICY_RULES.md
s20: DENY. See ADR-004 for the resulting v1 behaviors.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from core import (
    ApprovalRequest,
    ApprovalResult,
    PolicyDecision,
    PolicyRequest,
    Reversibility,
    RiskLevel,
    SideEffect,
    TaskState,
    Target,
    TargetType,
    Idempotency,
    PolicyDecisionValue,
    parse_iso,
    utcnow_iso,
)
from core.enums import ApprovalDecision
from core.versions import PROTECTED_PATHS_REGISTRY_VERSION, POLICY_VERSION, RULE_VERSION

from .canonical import Canonicalizer, match_scope
from .registry import (
    COMPATIBLE_VERSIONS,
    MATRIX,
    MODE_INDEX,
    OPERATIONS,
    OperationRule,
    PROTECTED_PATH_CLASSES,
)

# Policy.md s5: states that permit new execution. RECOVERING requires
# recovery validation first; PLANNING/READY precede execution (ADR-004).
EXECUTION_PERMITTING_STATES = frozenset({
    TaskState.RUNNING, TaskState.OBSERVING, TaskState.VERIFYING, TaskState.REPAIRING,
})

_MUTATING_CLASSES = frozenset({SideEffect.MUTATING, SideEffect.DESTRUCTIVE})


@dataclass(frozen=True)
class RuntimePathMapping:
    """Versioned concrete protected-path mapping (PROTECTED_PATHS.md s13-s14).

    paths maps protection class ids (P0..P8) to concrete absolute path
    patterns for one runtime. The version must equal the registry version;
    anything else is stale and fails closed.
    """
    version: str
    paths: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        seen = {}
        for class_id, patterns in self.paths.items():
            if class_id not in PROTECTED_PATH_CLASSES:
                raise ValueError(f"unknown protection class {class_id!r}")
            for pattern in patterns:
                if pattern in seen:
                    raise ValueError(
                        f"ambiguous runtime mapping: {pattern!r} appears in "
                        f"{seen[pattern]!r} and {class_id!r}")
                seen[pattern] = class_id


@dataclass(frozen=True)
class ProtectedPathRegistry:
    """Versioned protected-target registry (PROTECTED_PATHS.md).

    The runtime mapping is mandatory before filesystem mutation is
    authorized; without it, mutation is denied (P-RULE-51/52/53).
    """
    version: str
    mapping: Optional[RuntimePathMapping] = None

    def matches_protected(self, canonical_path: str) -> bool:
        if self.mapping is None:
            return True  # cannot classify -> conservative
        for patterns in self.mapping.paths.values():
            if match_scope(canonical_path, patterns):
                return True
        return False


class PolicyEngine:
    """Centralized authorization gate. Deterministic for identical inputs."""

    def __init__(
        self,
        operations: Optional[Dict[str, OperationRule]] = None,
        protected_paths: Optional[ProtectedPathRegistry] = None,
        canonicalizer: Optional[Canonicalizer] = None,
        policy_version: str = POLICY_VERSION,
        rule_version: str = RULE_VERSION,
        approval_ttl_seconds: int = 300,
    ):
        self.operations = operations if operations is not None else OPERATIONS
        self.protected_paths = (
            protected_paths if protected_paths is not None
            else ProtectedPathRegistry(version=PROTECTED_PATHS_REGISTRY_VERSION)
        )
        self.canonicalizer = canonicalizer or Canonicalizer()
        if (policy_version, rule_version) not in COMPATIBLE_VERSIONS:
            raise ValueError(
                f"incompatible policy/rule versions {(policy_version, rule_version)!r}")
        self.policy_version = policy_version
        self.rule_version = rule_version
        self.approval_ttl_seconds = approval_ttl_seconds

    # -- public API -------------------------------------------------------

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """The full s46 evaluation pipeline. Never raises for malformed
        requests; malformed inputs are DENY (fail closed)."""
        if not isinstance(request, PolicyRequest):
            return self._deny(request, "malformed policy request")
        # 1. policy/rule version compatibility (POLICY.md s6)
        if (request.policyVersion, request.ruleVersion) != (self.policy_version, self.rule_version):
            return self._deny(request, "policy/rule version mismatch")
        # 2. task lifecycle state (POLICY.md s5, P-INV-23)
        if request.taskState not in EXECUTION_PERMITTING_STATES:
            return self._deny(request, f"task state {request.taskState.value} does not permit execution")
        # 3/4. permission mode and effort level (POLICY_RULES.md s18-s19)
        if getattr(request.permissionMode, "value", None) not in MODE_INDEX:
            return self._deny(request, "invalid permission mode")
        if getattr(request.effortLevel, "value", None) not in ("FOCUSED", "STANDARD", "DEEP", "ULTRA"):
            return self._deny(request, "invalid effort level")
        # 5. target authorization context (POLICY_RULES.md s7, P-RULE-33/34)
        tac = request.targetAuthorizationContext
        if tac is None or not tac.schemaVersion.strip() or not tac.is_active():
            return self._deny(request, "missing, invalid, or expired TargetAuthorizationContext")
        # 9/10. registered operation; out-of-scope capabilities (s33-s35)
        rule = self.operations.get(request.operationId)
        if rule is None:
            return self._deny(request, "unregistered operation")
        if rule.outOfScope:
            return self._deny(request, f"operation {rule.operationId} is out of scope in v1")
        # 11. structured arguments (s5)
        arguments = request.structuredArguments
        if not isinstance(arguments, dict):
            return self._deny(request, "structuredArguments must be an object")
        for arg in rule.requiredArgs:
            value = arguments.get(arg)
            if not isinstance(value, str) or not value.strip():
                return self._deny(request, f"missing required argument {arg!r}")
        # canonicalize and match the target (steps 6/7/16)
        target = request.target
        if rule.targetKind is not None:
            if target is None or target.type is not rule.targetKind:
                return self._deny(request, "target missing or wrong target kind")
            canonical = self.canonicalizer.canonicalize(target.type, target.value)
            if canonical is None:
                return self._deny(request, "ambiguous or invalid target (canonicalization failed)")
            if match_scope(canonical, tac.deniedTargets):
                return self._deny(request, "target explicitly denied")
            if rule.targetKind is TargetType.FILESYSTEM and rule.sideEffect in _MUTATING_CLASSES:
                decision = self._check_protected_paths(request, canonical)
                if decision is not None:
                    return decision
            if rule.scopeUnsatisfiable:
                return self._deny(request, "no v1 target scope can authorize this operation")
            if rule.requiredScopes:
                matched = any(
                    match_scope(canonical, getattr(tac, field, ()))
                    for field in rule.requiredScopes)
                if not matched:
                    return self._deny(request, "target not in authorized scope")
            if rule.packageOperationGate:
                if rule.operationId not in tac.allowedPackageOperations:
                    return self._deny(request, "operation not listed in allowedPackageOperations")
            if rule.destinationArg:
                destination = arguments.get(rule.destinationArg)
                if not isinstance(destination, str) or not destination.strip():
                    return self._deny(request, f"missing required argument {rule.destinationArg!r}")
                dest_canonical = self.canonicalizer.canonicalize(TargetType.FILESYSTEM, destination)
                if dest_canonical is None:
                    return self._deny(request, "ambiguous destination path")
                if not match_scope(dest_canonical, tac.allowedWritePaths):
                    return self._deny(request, "download destination not in allowedWritePaths")
        # 12. authoritative risk rule exists - the registry entry itself.
        # 13. mandatory human confirmation (s25, P-RULE-25/27)
        if rule.mandatoryConfirmation:
            return self._ask(request, rule, target if rule.targetKind else None,
                             "operation requires synchronous human confirmation")
        # 14. unsafe HIGH/CRITICAL non-idempotent behavior (s17, P-RULE-06)
        if rule.riskLevel in (RiskLevel.HIGH, RiskLevel.CRITICAL) and \
                rule.idempotency in (Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN):
            return self._ask(request, rule, target if rule.targetKind else None,
                             "HIGH/CRITICAL non-idempotent operation without safe deduplication")
        # 15. operation-specific rule (s31: accessibility tap/type_text/submit)
        if rule.forcedDecision is not None:
            return self._decide(request, rule.forcedDecision, "operation-specific rule")
        # 17. permission matrix (s20); undefined combination -> DENY
        decision = self._matrix(rule, request.permissionMode.value)
        if decision is None:
            return self._deny(request, "classification combination is not defined by the permission matrix")
        return self._decide(request, decision, "permission matrix")

    # -- helpers ----------------------------------------------------------

    def _check_protected_paths(self, request: PolicyRequest, canonical_path: str) -> Optional[PolicyDecision]:
        """Filesystem mutation gate: registry version + runtime mapping
        (P-RULE-51/52/53) and protected-target matching (s12-s14)."""
        registry = self.protected_paths
        if registry.version != PROTECTED_PATHS_REGISTRY_VERSION:
            return self._deny(request, "protected-path registry missing or version mismatch")
        if registry.mapping is None:
            return self._deny(request, "missing runtime protected-path mapping")
        if registry.mapping.version != registry.version:
            return self._deny(request, "stale runtime protected-path mapping")
        if registry.matches_protected(canonical_path):
            return self._deny(request, "protected target")
        return None

    @staticmethod
    def _matrix(rule: OperationRule, permission_mode: str) -> Optional[PolicyDecisionValue]:
        """POLICY_RULES.md s20. UNKNOWN reversibility is treated as
        IRREVERSIBLE and UNKNOWN idempotency as NON_IDEMPOTENT (s16/s17).
        Undefined combinations return None -> DENY."""
        mode_index = MODE_INDEX[permission_mode]
        if rule.riskLevel is RiskLevel.CRITICAL:
            return MATRIX[(RiskLevel.CRITICAL,)][mode_index]
        reversibility = rule.reversibility
        if reversibility is Reversibility.UNKNOWN:
            reversibility = Reversibility.IRREVERSIBLE
        key = (rule.riskLevel, rule.sideEffect, reversibility)
        if key in MATRIX:
            return MATRIX[key][mode_index]
        # READ_ONLY rows with "any" reversibility (LOW/MEDIUM)
        if rule.sideEffect is SideEffect.READ_ONLY and rule.riskLevel in (
                RiskLevel.LOW, RiskLevel.MEDIUM):
            return MATRIX[(rule.riskLevel, SideEffect.READ_ONLY)][mode_index]
        return None

    def _deny(self, request: PolicyRequest, reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision=PolicyDecisionValue.DENY,
            reason=reason,
            policyVersion=request.policyVersion,
            ruleVersion=request.ruleVersion,
        )

    def _decide(self, request: PolicyRequest, decision: PolicyDecisionValue,
                reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision=decision,
            reason=reason,
            policyVersion=request.policyVersion,
            ruleVersion=request.ruleVersion,
        )

    def _ask(self, request: PolicyRequest, rule: OperationRule,
             target: Optional[Target], reason: str) -> PolicyDecision:
        """ASK with a single-use, time-bounded ApprovalRequest (s21)."""
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.approval_ttl_seconds))
        approval = ApprovalRequest(
            taskId=request.taskId,
            operation=rule.operationId,
            target=target or Target(type=TargetType.RESOURCE, value=rule.operationId),
            riskLevel=rule.riskLevel,
            sideEffect=rule.sideEffect,
            reversibility=rule.reversibility,
            idempotency=rule.idempotency,
            explanation=reason,
            consequences=f"{rule.sideEffect.value} / {rule.reversibility.value}",
            scope={"operation": rule.operationId},
            expiresAt=expires_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            policyVersion=request.policyVersion,
            ruleVersion=request.ruleVersion,
            approvalReference="appr-" + uuid.uuid4().hex,
            nonce=uuid.uuid4().hex,
        )
        return PolicyDecision(
            decision=PolicyDecisionValue.ASK,
            reason=reason,
            policyVersion=request.policyVersion,
            ruleVersion=request.ruleVersion,
            approvalRequirement=approval,
        )

    def validate_approval(self, approval_request: ApprovalRequest,
                          approval_result: ApprovalResult) -> Optional[str]:
        """Policy validation of a collected approval (INTERFACES s10/s21,
        SECURITY s8). Returns None when valid, otherwise the failure reason.
        Single-use consumption is enforced by the pipeline via the journal."""
        if not isinstance(approval_result, ApprovalResult):
            return "approval result malformed"
        if approval_result.approvalReference != approval_request.approvalReference:
            return "approval reference mismatch"
        if approval_result.decision is not ApprovalDecision.APPROVE:
            return "approval not granted"
        if not approval_result.approverIdentity or not approval_result.approverIdentity.actorId.strip():
            return "approver identity missing"
        expires_at = parse_iso(approval_request.expiresAt)
        if expires_at is None:
            return "approval expiry unparseable"
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires_at:
            return "approval expired"
        return None
