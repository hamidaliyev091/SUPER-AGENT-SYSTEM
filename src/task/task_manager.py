"""Task Manager (Phase 2) - authoritative task lifecycle and durable state.

Implements the INTERFACES.md s7 TaskManager surface on top of the Phase 1
core contracts and the Phase 2 TaskStore. Authority rules (ARCHITECTURE.md
s5.2): the Task Manager owns task creation, validation, lifecycle
transitions (all except DONE), durable state, checkpoints, recovery
coordination, and the authoritative TargetAuthorizationContext. It never
authorizes tools, executes tools, verifies results, selects models, or
declares DONE.

Design notes:
- All reads go through the durable store (read-through); every mutation is
  persisted BEFORE the method returns, so a task is never observable in a
  state that is not durably recorded (CONTINUITY.md s5).
- update_task has a deliberately narrow surface: lifecycle state, objective,
  success criteria, completion contract, permission mode, resource limits
  and target scopes cannot be changed through it (state changes go through
  transition(); scope changes go through update_target_authorization which
  only permits narrowing).
- v1 is single-process and single-top-level-task (ARCHITECTURE.md s20); no
  locking is added at this layer.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import List, Optional

from core import (
    Checkpoint,
    CompletionContract,
    ContinuityState,
    ContractError,
    Decision,
    FailureRecord,
    Plan,
    PolicyContext,
    ResourceLimits,
    TERMINAL_STATES,
    TargetAuthorizationContext,
    Task,
    TaskState,
    ValidationError,
    VerificationState,
    execution_gate,
    parse_iso,
    utcnow_iso,
    validate_completion_contract,
    validate_resource_limits,
    validate_target_authorization,
    validate_transition,
)
from core.enums import EffortLevel, PermissionMode, RiskLevel
from core.versions import POLICY_VERSION, RULE_VERSION, TASK_SCHEMA_VERSION
from continuity.task_store import TaskIntegrityError, TaskNotFoundError, TaskStore

UNSET = object()
RESUME_AUTHORIZATION_KINDS = frozenset({"USER", "SYSTEM_RECOVERY"})
INTERRUPTIBLE_STATES = frozenset(
    {TaskState.RUNNING, TaskState.OBSERVING, TaskState.VERIFYING, TaskState.REPAIRING}
)


class TaskGateBlockedError(ContractError):
    """The execution gate (TASK_SCHEMA.md s33) refused a transition to RUNNING."""

    def __init__(self, problems: List[str]):
        super().__init__("execution gate blocked: " + "; ".join(problems))
        self.problems = problems


@dataclass
class CreateTaskRequest:
    """Input for TaskManager.create_task."""
    objective: str
    permissionMode: PermissionMode
    effortLevel: EffortLevel
    resourceLimits: ResourceLimits
    targetAuthorizationContext: TargetAuthorizationContext
    defaultRiskLevel: RiskLevel = RiskLevel.MEDIUM
    requirements: list = field(default_factory=list)
    successCriteria: list = field(default_factory=list)
    completionContract: Optional[CompletionContract] = None
    parentTaskId: Optional[str] = None


@dataclass
class UpdateTaskRequest:
    """Narrow update surface; only these fields may change outside transition()."""
    plan: object = UNSET
    appendDecisions: list = field(default_factory=list)
    appendFailures: list = field(default_factory=list)
    verification: object = UNSET
    continuity: object = UNSET
    addSubtaskIds: list = field(default_factory=list)


@dataclass
class RecoveryResult:
    """Structured recovery outcome (CONTINUITY.md s11 plus NONE_NEEDED)."""
    taskId: str
    outcome: str
    reason: str = ""
    task: Optional[Task] = None


class TaskManager:
    """Authoritative task lifecycle, durable state, and target authorization.

    The only path to lifecycle state changes (DONE excepted - that belongs
    to the Completion Engine, Phase 6). Every mutation is durably persisted
    before it becomes observable.
    """

    def __init__(self, store: TaskStore):
        self.store = store

    # -- creation --------------------------------------------------------

    def create_task(self, request: CreateTaskRequest) -> Task:
        """Validate and durably create a task in CREATED state."""
        now = utcnow_iso()
        if not request.objective.strip():
            raise ValidationError("objective must not be empty")
        problems = validate_resource_limits(request.resourceLimits)
        if problems:
            raise ValidationError("invalid resourceLimits: " + "; ".join(problems))
        tac = request.targetAuthorizationContext
        problems = validate_target_authorization(tac)
        if problems:
            raise ValidationError("invalid targetAuthorizationContext: " + "; ".join(problems))
        for criterion in request.successCriteria:
            if (not criterion.id.strip() or not criterion.description.strip()
                    or not criterion.verificationMethod.strip()):
                raise ValidationError("every success criterion needs id, description and verificationMethod")
        if request.successCriteria and not any(c.mandatory for c in request.successCriteria):
            raise ValidationError("at least one success criterion must be mandatory")
        if request.completionContract is not None:
            problems = validate_completion_contract(request.completionContract)
            if problems:
                raise ValidationError("invalid completionContract: " + "; ".join(problems))
        if request.parentTaskId is not None:
            self.store.load_task(request.parentTaskId)  # parent must already exist
        tac = replace(
            tac,
            createdAt=tac.createdAt or now,
            updatedAt=now,
            authorizationReference=tac.authorizationReference or "auth-" + uuid.uuid4().hex,
        )
        task = Task(
            id=uuid.uuid4().hex,
            schemaVersion=TASK_SCHEMA_VERSION,
            objective=request.objective,
            requirements=list(request.requirements),
            successCriteria=list(request.successCriteria),
            completionContract=request.completionContract,
            permissionMode=request.permissionMode,
            effortLevel=request.effortLevel,
            policyContext=PolicyContext(
                policyVersion=POLICY_VERSION,
                ruleVersion=RULE_VERSION,
                permissionMode=request.permissionMode,
                defaultRiskLevel=request.defaultRiskLevel,
            ),
            targetAuthorizationContext=tac,
            resourceLimits=request.resourceLimits,
            state=TaskState.CREATED,
            verification=VerificationState(),
            continuity=ContinuityState(),
            createdAt=now,
            updatedAt=now,
            parentTaskId=request.parentTaskId,
        )
        self.store.save_task(task)
        return task

    def get_task(self, task_id: str) -> Task:
        """Return the authoritative durable copy of the task."""
        return self.store.load_task(task_id)

    # -- lifecycle -------------------------------------------------------

    def transition(self, task_id: str, target: TaskState, *,
                   by_completion_engine: bool = False,
                   authorization: Optional[dict] = None) -> Task:
        """Perform one validated lifecycle transition and persist it.

        Rules (TASK_SCHEMA.md s22): DONE requires by_completion_engine=True
        and current state VERIFYING; resuming from WAITING_USER/BLOCKED
        requires explicit USER or SYSTEM_RECOVERY authorization; entering
        RUNNING is gated by the execution gate (s33).
        """
        task = self.store.load_task(task_id)
        validate_transition(task.state, target, by_completion_engine=by_completion_engine)
        if task.state in {TaskState.WAITING_USER, TaskState.BLOCKED} and target is TaskState.RECOVERING:
            self._record_resume_authorization(authorization, task)
        if target is TaskState.RUNNING:
            problems = execution_gate(task)
            if problems:
                raise TaskGateBlockedError(problems)
        task.state = target
        task.updatedAt = utcnow_iso()
        self.store.save_task(task)
        return task

    @staticmethod
    def _record_resume_authorization(authorization: Optional[dict], task: Task) -> None:
        if not isinstance(authorization, dict):
            raise ValidationError("resume from WAITING_USER/BLOCKED requires explicit authorization")
        kind = authorization.get("kind")
        actor = authorization.get("actor")
        if kind not in RESUME_AUTHORIZATION_KINDS or not isinstance(actor, str) or not actor.strip():
            raise ValidationError(
                "resume authorization must be {kind: USER|SYSTEM_RECOVERY, actor: non-empty string}")
        task.decisions.append(Decision(
            decisionId=uuid.uuid4().hex,
            taskId=task.id,
            timestamp=utcnow_iso(),
            decision="resume_authorized",
            rationale=f"resume authorized by {kind} actor {actor!r} (TASK_SCHEMA.md s22)",
        ))

    def cancel(self, task_id: str, reason: str = "") -> Task:
        """Cancel a non-terminal task; terminal tasks cannot be cancelled."""
        task = self.store.load_task(task_id)
        validate_transition(task.state, TaskState.CANCELLED)
        if reason:
            task.decisions.append(Decision(
                decisionId=uuid.uuid4().hex,
                taskId=task.id,
                timestamp=utcnow_iso(),
                decision="cancelled",
                rationale=reason,
            ))
        task.state = TaskState.CANCELLED
        task.updatedAt = utcnow_iso()
        self.store.save_task(task)
        return task

    # -- updates ---------------------------------------------------------

    def update_task(self, task_id: str, update: UpdateTaskRequest) -> Task:
        """Apply a narrow update; state/objective/criteria/contract/limits
        and target scopes are deliberately not updatable here."""
        task = self.store.load_task(task_id)
        if task.state in TERMINAL_STATES:
            raise ValidationError(f"task in terminal state {task.state.value} cannot be updated")
        changed = False
        if update.plan is not UNSET:
            if update.plan is not None and not isinstance(update.plan, Plan):
                raise ValidationError("plan must be a Plan or None")
            task.plan = update.plan
            changed = True
        for decision in update.appendDecisions:
            if not isinstance(decision, Decision):
                raise ValidationError("appendDecisions entries must be Decision records")
            if not decision.decisionId.strip() or decision.taskId != task_id:
                raise ValidationError("decision needs a non-empty decisionId and matching taskId")
            task.decisions.append(decision)
            changed = True
        for failure in update.appendFailures:
            if not isinstance(failure, FailureRecord):
                raise ValidationError("appendFailures entries must be FailureRecord records")
            if not failure.failureId.strip() or failure.taskId != task_id:
                raise ValidationError("failure needs a non-empty failureId and matching taskId")
            task.failures.append(failure)
            changed = True
        if update.verification is not UNSET:
            if not isinstance(update.verification, VerificationState):
                raise ValidationError("verification must be a VerificationState")
            task.verification = update.verification
            changed = True
        if update.continuity is not UNSET:
            if not isinstance(update.continuity, ContinuityState):
                raise ValidationError("continuity must be a ContinuityState")
            task.continuity = update.continuity
            changed = True
        for subtask_id in update.addSubtaskIds:
            if subtask_id in task.subtaskIds:
                continue
            self.store.load_task(subtask_id)  # subtask must exist
            task.subtaskIds.append(subtask_id)
            changed = True
        if not changed:
            raise ValidationError("update contains no changes")
        task.updatedAt = utcnow_iso()
        self.store.save_task(task)
        return task

    # -- target authorization --------------------------------------------

    def update_target_authorization(self, task_id: str, new_tac: TargetAuthorizationContext) -> Task:
        """Replace the authoritative TargetAuthorizationContext with a
        NARROWER one. Expansion or shrinking of deniedTargets raises.
        Creates a new authorization version (new authorizationReference).
        Policy Engine evaluation of scope changes arrives in Phase 3."""
        task = self.store.load_task(task_id)
        if task.state in TERMINAL_STATES:
            raise ValidationError(f"task in terminal state {task.state.value} cannot change scope")
        old = task.targetAuthorizationContext
        problems = validate_target_authorization(new_tac)
        if problems:
            raise ValidationError("invalid targetAuthorizationContext: " + "; ".join(problems))
        scope_pairs = [
            ("allowedReadPaths", old.allowedReadPaths, new_tac.allowedReadPaths),
            ("allowedWritePaths", old.allowedWritePaths, new_tac.allowedWritePaths),
            ("allowedSearchPaths", old.allowedSearchPaths, new_tac.allowedSearchPaths),
            ("allowedPackages", old.allowedPackages, new_tac.allowedPackages),
            ("allowedPackageOperations", old.allowedPackageOperations, new_tac.allowedPackageOperations),
            ("allowedNetworkDomains", old.allowedNetworkDomains, new_tac.allowedNetworkDomains),
            ("allowedNetworkDestinations", old.allowedNetworkDestinations, new_tac.allowedNetworkDestinations),
            ("allowedUIActions", old.allowedUIActions, new_tac.allowedUIActions),
            ("allowedProcesses", old.allowedProcesses, new_tac.allowedProcesses),
            ("allowedAndroidSettings", old.allowedAndroidSettings, new_tac.allowedAndroidSettings),
            ("allowedResources", old.allowedResources, new_tac.allowedResources),
        ]
        for name, old_scope, new_scope in scope_pairs:
            if not set(new_scope) <= set(old_scope):
                raise ValidationError(f"scope expansion rejected: {name} must stay within the current scope")
        if not set(old.deniedTargets) <= set(new_tac.deniedTargets):
            raise ValidationError("deniedTargets may only be added, never removed")
        if old.expiresAt is not None:
            old_exp = parse_iso(old.expiresAt)
            new_exp = parse_iso(new_tac.expiresAt) if new_tac.expiresAt is not None else None
            if new_exp is None or old_exp is None or new_exp > old_exp:
                raise ValidationError("authorization expiry may only be kept or shortened")
        now = utcnow_iso()
        new_tac = replace(
            new_tac,
            createdAt=old.createdAt,
            updatedAt=now,
            authorizationReference="auth-" + uuid.uuid4().hex,
        )
        task.targetAuthorizationContext = new_tac
        task.decisions.append(Decision(
            decisionId=uuid.uuid4().hex,
            taskId=task.id,
            timestamp=now,
            decision="target_authorization_narrowed",
            rationale="scope narrowed to a subset of the previous authorization",
            policyImplications="scope narrowing only; expansion needs a new task-level authorization",
        ))
        task.updatedAt = now
        self.store.save_task(task)
        return task

    # -- checkpoints and recovery ----------------------------------------

    def checkpoint(self, task_id: str) -> Checkpoint:
        """Persist a durable snapshot of the current authoritative state
        (CONTINUITY.md s6). Stored as a separate integrity-protected file."""
        task = self.store.load_task(task_id)
        if task.state in TERMINAL_STATES:
            raise ValidationError(f"cannot checkpoint a task in terminal state {task.state.value}")
        checkpoint = Checkpoint(
            checkpointId=uuid.uuid4().hex,
            taskId=task.id,
            timestamp=utcnow_iso(),
            lifecycleState=task.state,
            objective=task.objective,
            successCriteria=list(task.successCriteria),
            currentPlan=task.plan,
            recentFailures=list(task.failures[-5:]),
            remainingBudget=task.resourceLimits,
            policyContext=task.policyContext,
            authorizationContext=task.targetAuthorizationContext,
            verificationStatus=task.verification,
            knownSideEffects=list(task.continuity.knownSideEffects),
            unknownSideEffects=list(task.continuity.unknownSideEffects),
            continuitySummary=task.continuity.nextSafeAction or "",
        )
        self.store.save_checkpoint(checkpoint)
        return checkpoint

    def recover(self, task_id: str) -> RecoveryResult:
        """Coordination half of crash recovery (CONTINUITY.md s10).

        Loads durable state and integrity-validates it (corruption -> BLOCKED,
        never silently reconstructed). Interruptible states move to
        RECOVERING. The action-journal inspection and external-state
        verification steps arrive with the Phase 7 Continuity Manager;
        until then the caller must verify external state before authorizing
        RECOVERING -> RUNNING.
        """
        try:
            task = self.store.load_task(task_id)
        except TaskIntegrityError as exc:
            return RecoveryResult(taskId=task_id, outcome="BLOCKED",
                                  reason=f"authoritative state failed integrity validation: {exc}")
        except TaskNotFoundError as exc:
            return RecoveryResult(taskId=task_id, outcome="FAILED", reason=str(exc))
        if task.state in TERMINAL_STATES:
            return RecoveryResult(taskId=task_id, outcome="NONE_NEEDED", task=task,
                                  reason=f"task already in terminal state {task.state.value}")
        if task.state in {TaskState.WAITING_USER, TaskState.BLOCKED}:
            return RecoveryResult(taskId=task_id, outcome="WAITING_USER", task=task,
                                  reason="explicit USER or SYSTEM_RECOVERY authorization required to resume")
        if task.state in INTERRUPTIBLE_STATES:
            task.state = TaskState.RECOVERING
            task.updatedAt = utcnow_iso()
            self.store.save_task(task)
            return RecoveryResult(
                taskId=task_id, outcome="RESUME", task=task,
                reason="restored from durable state and moved to RECOVERING; "
                       "verify external state before authorizing RECOVERING -> RUNNING")
        return RecoveryResult(taskId=task_id, outcome="RESUME", task=task,
                              reason=f"task is in {task.state.value}; no interrupted execution to recover from")
