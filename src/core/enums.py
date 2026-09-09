"""Frozen enumerated value sets used by the Core contracts.

The members of every enum below are normative. They mirror the value sets
declared by the canonical frozen documents (TASK_SCHEMA.md, INTERFACES.md,
POLICY_RULES.md, VERIFICATION.md, CONTINUITY.md, SECURITY.md) and must not
drift without a governance revision.
"""
from enum import Enum


class _ValueEnum(str, Enum):
    """String enum; serializes as its plain value."""

    def __str__(self) -> str:  # pragma: no cover - consistency helper
        return self.value


class PermissionMode(_ValueEnum):
    """INTERFACES.md s4 / TASK_SCHEMA.md s9."""
    PLAN = "PLAN"
    ASK = "ASK"
    AUTO = "AUTO"
    DANGEROUS = "DANGEROUS"


class EffortLevel(_ValueEnum):
    """INTERFACES.md s5 / TASK_SCHEMA.md s10."""
    FOCUSED = "FOCUSED"
    STANDARD = "STANDARD"
    DEEP = "DEEP"
    ULTRA = "ULTRA"


class TaskState(_ValueEnum):
    """TASK_SCHEMA.md s21 - canonical lifecycle states."""
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    RECOVERING = "RECOVERING"
    WAITING_USER = "WAITING_USER"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RiskLevel(_ValueEnum):
    """PROJECT_CONTRACT.md s10."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SideEffect(_ValueEnum):
    """POLICY_RULES.md s15 - authoritative side-effect values."""
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"


class Reversibility(_ValueEnum):
    """POLICY_RULES.md s16. UNKNOWN is treated as IRREVERSIBLE for authorization."""
    REVERSIBLE = "REVERSIBLE"
    PARTIALLY_REVERSIBLE = "PARTIALLY_REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"
    UNKNOWN = "UNKNOWN"


class Idempotency(_ValueEnum):
    """POLICY_RULES.md s17. UNKNOWN is treated as NON_IDEMPOTENT for authorization."""
    IDEMPOTENT = "IDEMPOTENT"
    CONDITIONALLY_IDEMPOTENT = "CONDITIONALLY_IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    UNKNOWN = "UNKNOWN"


class PolicyDecisionValue(_ValueEnum):
    """POLICY.md s3 / INTERFACES.md s10 - the only authorization outcomes."""
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class CompletionDecisionValue(_ValueEnum):
    """Completion Engine decision values.

    Union of INTERFACES.md s20 {DONE, CONTINUE, REPAIR, BLOCKED, WAITING_USER}
    and VERIFICATION.md s17 {DONE, REPAIR, WAITING_USER, BLOCKED, FAILED}.
    See DECISIONS.md ADR-002. DONE authorization semantics are unchanged and
    remain governed solely by VERIFICATION.md s19.
    """
    DONE = "DONE"
    CONTINUE = "CONTINUE"
    REPAIR = "REPAIR"
    BLOCKED = "BLOCKED"
    WAITING_USER = "WAITING_USER"
    FAILED = "FAILED"


class VerificationStatus(_ValueEnum):
    """VERIFICATION.md s3. INCONCLUSIVE must never authorize DONE."""
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class SideEffectState(_ValueEnum):
    """INTERFACES.md s13. UNKNOWN must not be assumed to mean failed or succeeded."""
    KNOWN_NONE = "KNOWN_NONE"
    KNOWN_COMPLETED = "KNOWN_COMPLETED"
    KNOWN_PARTIAL = "KNOWN_PARTIAL"
    KNOWN_FAILED = "KNOWN_FAILED"
    UNKNOWN = "UNKNOWN"


class ActionStatus(_ValueEnum):
    """CONTINUITY.md s8.1 - action journal statuses."""
    PLANNED = "PLANNED"
    AUTHORIZED = "AUTHORIZED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ActorType(_ValueEnum):
    """SECURITY.md s5 - actor model."""
    USER = "USER"
    TOP_LEVEL_AGENT = "TOP_LEVEL_AGENT"
    SUBAGENT = "SUBAGENT"
    VERIFIER = "VERIFIER"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"
    RUNTIME_ADAPTER = "RUNTIME_ADAPTER"


class ModelRole(_ValueEnum):
    """INTERFACES.md s15."""
    PLANNER = "PLANNER"
    RESEARCHER = "RESEARCHER"
    CODER = "CODER"
    REVIEWER = "REVIEWER"
    VERIFIER = "VERIFIER"
    GENERAL_AGENT = "GENERAL_AGENT"


class ApprovalDecision(_ValueEnum):
    """INTERFACES.md s21 - human approval outcomes."""
    APPROVE = "APPROVE"
    DENY = "DENY"


class IndependenceLevel(_ValueEnum):
    """VERIFICATION.md s9."""
    LEVEL_0_SELF = "LEVEL_0_SELF"
    LEVEL_1_INDEPENDENT_RUNTIME = "LEVEL_1_INDEPENDENT_RUNTIME"
    LEVEL_2_SEPARATE_VERIFIER = "LEVEL_2_SEPARATE_VERIFIER"


class TargetType(_ValueEnum):
    """Canonical target kinds (TASK_SCHEMA.md s12/s17)."""
    FILESYSTEM = "FILESYSTEM"
    PACKAGE = "PACKAGE"
    NETWORK_DOMAIN = "NETWORK_DOMAIN"
    NETWORK_DESTINATION = "NETWORK_DESTINATION"
    ANDROID_SETTING = "ANDROID_SETTING"
    UI = "UI"
    PROCESS = "PROCESS"
    RESOURCE = "RESOURCE"
