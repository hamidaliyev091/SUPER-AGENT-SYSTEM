"""SUPER AGENT SYSTEM - Core contracts (Phase 1).

Platform-independent data structures, enumerated value sets, and validation
rules implementing the frozen canonical documents. The Core must never import
Pi, Android, Termux, or model-provider code.
"""
from .contracts import (
    ActionRequest,
    ActorIdentity,
    ApprovalRequest,
    ApprovalResult,
    Checkpoint,
    CompletionContract,
    CompletionDecision,
    ContinuityState,
    ContractError,
    Decision,
    Error,
    Evidence,
    FailureRecord,
    Plan,
    PlanStep,
    PolicyContext,
    PolicyDecision,
    PolicyRequest,
    Requirement,
    ResourceLimits,
    SuccessCriterion,
    Target,
    TargetAuthorizationContext,
    Task,
    Tool,
    ToolResult,
    ValidationError,
    VerificationResult,
    VerificationState,
    parse_iso,
    utcnow_iso,
)
from .enums import (
    ActionStatus,
    ActorType,
    ApprovalDecision,
    CompletionDecisionValue,
    EffortLevel,
    Idempotency,
    IndependenceLevel,
    ModelRole,
    PermissionMode,
    PolicyDecisionValue,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TargetType,
    TaskState,
    VerificationStatus,
)
from .validation import (
    EXECUTABLE_STATES,
    TERMINAL_STATES,
    can_transition,
    execution_gate,
    validate_completion_contract,
    validate_resource_limits,
    validate_target_authorization,
    validate_task,
    validate_transition,
)
from .versions import (
    POLICY_VERSION,
    PROTECTED_PATHS_REGISTRY_VERSION,
    RULE_VERSION,
    TASK_SCHEMA_VERSION,
)

__all__ = [name for name in dir() if not name.startswith("_")]
