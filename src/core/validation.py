"""Validation rules for Core contracts (Phase 1).

Implements TASK_SCHEMA.md s21-s22 (lifecycle transitions), s33 (execution
gate), s36 (schema validation), s19 (authorization expiry), and the
mandatory-field rules of INTERFACES.md s3.

Note on DONE: the lifecycle table deliberately excludes DONE. Transitioning
to DONE is only possible through validate_transition(..., by_completion_engine=True)
with current state VERIFYING. Phase 6 enforces that only the Completion Engine
passes that flag; the contract surface already makes the generic path fail.
"""
from __future__ import annotations

from typing import List

from .contracts import (
    CompletionContract,
    ResourceLimits,
    TargetAuthorizationContext,
    Task,
    ValidationError,
)
from .enums import TaskState
from .versions import TASK_SCHEMA_VERSION

EXECUTABLE_STATES = frozenset(
    {
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.OBSERVING,
        TaskState.VERIFYING,
        TaskState.REPAIRING,
        TaskState.RECOVERING,
    }
)
TERMINAL_STATES = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED})
NON_EXECUTABLE_STATES = frozenset(
    {TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED, TaskState.BLOCKED, TaskState.WAITING_USER}
)

# TASK_SCHEMA.md s22. DONE is intentionally absent: it is reachable only
# through the Completion Engine path (see validate_transition).
LIFECYCLE: dict = {
    TaskState.CREATED: frozenset({TaskState.VALIDATING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.VALIDATING: frozenset({TaskState.PLANNING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.PLANNING: frozenset({TaskState.READY, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.OBSERVING, TaskState.RECOVERING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.OBSERVING: frozenset({TaskState.VERIFYING, TaskState.RECOVERING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.VERIFYING: frozenset({TaskState.REPAIRING, TaskState.RECOVERING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.REPAIRING: frozenset({TaskState.RUNNING, TaskState.RECOVERING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.RECOVERING: frozenset({TaskState.RUNNING, TaskState.READY, TaskState.PLANNING, TaskState.WAITING_USER, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING_USER: frozenset({TaskState.RECOVERING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.RECOVERING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.DONE: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    """Pure transition-table lookup. DONE is never in the table."""
    return target in LIFECYCLE.get(current, frozenset())


def validate_transition(current: TaskState, target: TaskState, *, by_completion_engine: bool = False) -> None:
    """Raise ValidationError if the transition violates TASK_SCHEMA.md s22.

    by_completion_engine=True is the ONLY way to reach DONE, and only from
    VERIFYING. Everything else must be in the documented transition table.
    """
    if target is TaskState.DONE:
        if not by_completion_engine:
            raise ValidationError("DONE may only be authorized by the Completion Engine")
        if current is not TaskState.VERIFYING:
            raise ValidationError(f"only VERIFYING may transition to DONE, got {current.value}")
        return
    if not can_transition(current, target):
        raise ValidationError(f"invalid lifecycle transition {current.value} -> {target.value}")


def validate_completion_contract(cc: CompletionContract) -> List[str]:
    """VERIFICATION.md s4/s5 checks. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    if not cc.objective.strip():
        problems.append("completionContract.objective is empty")
    if not cc.successCriteria:
        problems.append("completionContract has no success criteria")
    mandatory = [c for c in cc.successCriteria if c.mandatory]
    if not mandatory:
        problems.append("completionContract has no mandatory success criterion")
    for criterion in cc.successCriteria:
        if not criterion.id.strip():
            problems.append("success criterion has empty id")
        if not criterion.description.strip():
            problems.append(f"success criterion {criterion.id!r} has empty description")
        if not criterion.verificationMethod.strip():
            problems.append(f"success criterion {criterion.id!r} has empty verificationMethod")
    return problems


def validate_target_authorization(tac: TargetAuthorizationContext) -> List[str]:
    """TASK_SCHEMA.md s12/s19/s36 checks. Expired or unparseable expiresAt
    fails closed."""
    problems: List[str] = []
    if not tac.schemaVersion.strip():
        problems.append("targetAuthorizationContext.schemaVersion is empty")
    if not tac.is_active():
        problems.append("targetAuthorizationContext is expired or expiresAt is unparseable")
    return problems


def validate_resource_limits(rl: ResourceLimits) -> List[str]:
    """TASK_SCHEMA.md s20: at least one limit; no negative values."""
    problems: List[str] = []
    fields = [
        ("wallClockTime", rl.wallClockTime),
        ("actionSteps", rl.actionSteps),
        ("modelCalls", rl.modelCalls),
        ("retryCount", rl.retryCount),
        ("delegationCount", rl.delegationCount),
        ("network", rl.network),
        ("storage", rl.storage),
    ]
    if all(value is None for _, value in fields):
        problems.append("resourceLimits defines no limits (autonomous tasks require at least one)")
    for name, value in fields:
        if value is not None and value < 0:
            problems.append(f"resourceLimits.{name} is negative")
    return problems


def validate_task(task: Task) -> List[str]:
    """TASK_SCHEMA.md s36 schema validation plus the structural rules of
    INTERFACES.md s3. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    if not task.id.strip():
        problems.append("task.id is empty")
    if task.schemaVersion != TASK_SCHEMA_VERSION:
        problems.append(
            f"unsupported task schemaVersion {task.schemaVersion!r} (expected {TASK_SCHEMA_VERSION!r})"
        )
    if not task.objective.strip():
        problems.append("task.objective is empty")
    if not task.successCriteria:
        problems.append("task has no success criteria")
    mandatory = [c for c in task.successCriteria if c.mandatory]
    if not mandatory:
        problems.append("task has no mandatory success criterion")
    for criterion in task.successCriteria:
        if not criterion.id.strip():
            problems.append("success criterion has empty id")
        if not criterion.verificationMethod.strip():
            problems.append(f"success criterion {criterion.id!r} has empty verificationMethod")
    if not task.policyContext.policyVersion.strip():
        problems.append("policyContext.policyVersion is empty")
    if not task.policyContext.ruleVersion.strip():
        problems.append("policyContext.ruleVersion is empty")
    problems.extend(validate_target_authorization(task.targetAuthorizationContext))
    problems.extend(validate_resource_limits(task.resourceLimits))
    if task.completionContract is not None:
        problems.extend(validate_completion_contract(task.completionContract))
    elif task.state in EXECUTABLE_STATES:
        problems.append(f"task in {task.state.value} requires a CompletionContract")
    return problems


def execution_gate(task: Task) -> List[str]:
    """TASK_SCHEMA.md s33: a task may only enter RUNNING when everything is
    valid and the current state is READY. Returns a list of problems."""
    problems = validate_task(task)
    if task.state is not TaskState.READY:
        problems.append(f"execution gate requires state READY, got {task.state.value}")
    return problems
