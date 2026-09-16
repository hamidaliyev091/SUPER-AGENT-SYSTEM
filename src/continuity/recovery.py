"""Recovery Manager (Phase 7).

Implements the canonical safe-resume protocol (CONTINUITY.md s27, s10-s12,
s14-s15):

    load durable state -> integrity validation -> task state ->
    policy/authorization/resource validation -> action journal inspection
    -> identify STARTED/UNKNOWN actions -> classify side effects ->
    verify external state (probe) -> RESUME / RETRY / VERIFY_FIRST /
    WAITING_USER / BLOCKED

Invariants:
- An interrupted operation is NEVER assumed to have no side effect (s9/s19).
- A blind retry happens only for operations whose registry classification
  makes re-execution safe (s12/s13): READ_ONLY, or MUTATING with
  IDEMPOTENT + REVERSIBLE classifications.
- Recovery NEVER creates a new resource budget (s26).
- Authorization is revalidated on resume: the RECOVERING -> READY ->
  RUNNING path re-runs the execution gate (validate_task: schema, policy
  versions, target authorization activity, limits).
- Every recovery decision is journaled (RECOVERY_DECISION) and the
  task's durable continuity state is updated before any transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core import Task, utcnow_iso
from core.enums import (
    Idempotency,
    Reversibility,
    SideEffect,
    TaskState,
)

from policy.registry import OPERATIONS

from task.task_manager import RecoveryResult


@dataclass
class InterruptedAction:
    """One journaled STARTED action without a terminal record (s8.3)."""
    actionId: str
    operationId: str
    target: Optional[str]
    sideEffect: Optional[SideEffect]
    idempotency: Optional[Idempotency]
    retrySafe: bool


def _classify(payload: dict) -> InterruptedAction:
    operation = payload.get("operationId", "")
    rule = OPERATIONS.get(operation)
    side = rule.sideEffect if rule is not None else None
    idem = rule.idempotency if rule is not None else None
    retry_safe = side is SideEffect.READ_ONLY or (
        side is SideEffect.MUTATING
        and idem is Idempotency.IDEMPOTENT
        and rule is not None
        and rule.reversibility is Reversibility.REVERSIBLE)
    return InterruptedAction(
        actionId=payload.get("actionId", ""),
        operationId=operation,
        target=payload.get("target"),
        sideEffect=side,
        idempotency=idem,
        retrySafe=retry_safe,
    )


class RecoveryManager:
    """Safe-resume protocol. The orchestrator may supply an external-state
    probe: probe(task, action_payload) -> VERIFIED_SUCCESS | VERIFIED_FAILURE
    | UNKNOWN (s10.9, s11: VERIFY_FIRST preferred when external state can
    establish what happened)."""

    def __init__(self, task_manager, store, external_probe: Optional[Callable] = None):
        self.task_manager = task_manager
        self.store = store
        self.external_probe = external_probe

    # -- public API -----------------------------------------------------------

    def recover(self, task_id: str, external_probe: Optional[Callable] = None) -> RecoveryResult:
        probe = external_probe or self.external_probe
        base = self.task_manager.recover(task_id)
        if base.outcome != "RESUME":
            return base
        task = base.task
        journal = self.store.journal_for(task_id)
        try:
            interrupted = self._interrupted_actions(journal)
        except Exception as exc:  # JournalError: tampered/unreadable audit trail
            return self._block(task, f"action journal failed integrity validation: {exc}")

        if not task.targetAuthorizationContext.is_active():
            return self._block(task, "target authorization context is expired or invalid "
                                     "(authorization is not automatically permanent, s15)")

        if task.state is not TaskState.RECOVERING:
            if not interrupted:
                return RecoveryResult(
                    taskId=task_id, outcome="RESUME", task=task,
                    reason=f"task is {task.state.value}; no interrupted execution found")
            task = self.task_manager.transition(task_id, TaskState.RECOVERING)

        if not interrupted:
            self._journal_decision(journal, "RESUME", [],
                                   "no interrupted execution to recover from")
            return self._resume(task, "no interrupted execution found; resumed")

        safe = [a for a in interrupted if a.retrySafe]
        unsafe = [a for a in interrupted if not a.retrySafe]
        resolutions: Dict[str, str] = {}
        for action in interrupted:
            payload = self._payload_for(journal, action.actionId)
            resolutions[action.actionId] = probe(task, payload) if probe else "UNKNOWN"

        if not unsafe:
            return self._retry(task, safe, resolutions, journal)

        if probe is None:
            return self._waiting_user(task, unsafe, journal)

        if any(value == "UNKNOWN" for value in resolutions.values()):
            self._journal_decision(journal, "VERIFY_FIRST", [a.actionId for a in interrupted],
                                   "external inspection inconclusive; verification required",
                                   resolutions)
            self._update_continuity(
                task, next_safe_action="verify external state for interrupted actions "
                                       f"{[a.actionId for a in interrupted]}")
            return RecoveryResult(
                taskId=task.id, outcome="VERIFY_FIRST", task=task,
                reason="external probe was inconclusive; verify external state "
                       "before resuming execution")
        if any(value == "VERIFIED_FAILURE" for value in resolutions.values()):
            return self._retry(task, unsafe, resolutions, journal)
        self._journal_decision(journal, "RESUME", [a.actionId for a in interrupted],
                               "external state confirms interrupted actions took effect",
                               resolutions)
        return self._resume(task, "external state verified; resumed")

    # -- stages ------------------------------------------------------------------

    def _interrupted_actions(self, journal) -> List[InterruptedAction]:
        records = journal.records()
        terminal_ids = {r["payload"].get("actionId")
                        for r in records if r["eventType"] == "ACTION_TERMINAL"}
        out = []
        for record in records:
            if record["eventType"] != "ACTION_STARTED":
                continue
            if record["payload"].get("actionId") not in terminal_ids:
                out.append(_classify(record["payload"]))
        return out

    @staticmethod
    def _payload_for(journal, action_id: str) -> dict:
        for record in journal.records():
            if record["eventType"] == "ACTION_STARTED" and \
                    record["payload"].get("actionId") == action_id:
                return record["payload"]
        return {}

    # -- outcomes ----------------------------------------------------------------

    def _retry(self, task, actions, resolutions, journal) -> RecoveryResult:
        """s12/s26: a retry consumes budget; without remaining budget the
        task blocks instead."""
        remaining = self._remaining_action_steps(task, journal)
        if remaining is not None and remaining < 1:
            return self._block(task, "remaining action budget insufficient for retry (s26)")
        self._journal_decision(journal, "RETRY", [a.actionId for a in actions],
                               "interrupted actions are safe to re-execute "
                               "(READ_ONLY or IDEMPOTENT+REVERSIBLE, s12/s13)",
                               resolutions)
        return self._resume(task, f"retry permitted for {len(actions)} interrupted action(s)",
                            outcome="RETRY")

    def _waiting_user(self, task, unsafe, journal) -> RecoveryResult:
        unknown = [f"{a.operationId}:{a.target or ''} (action {a.actionId})" for a in unsafe]
        self._journal_decision(journal, "WAITING_USER", [a.actionId for a in unsafe],
                               "interrupted side-effecting actions of unknown effect; "
                               "blind retry is forbidden (s9/s12)", {})
        self._update_continuity(
            task, unknown_side_effects=unknown,
            next_safe_action=f"human verification required for {[a.actionId for a in unsafe]}")
        task = self.task_manager.transition(task.id, TaskState.WAITING_USER)
        return RecoveryResult(
            taskId=task.id, outcome="WAITING_USER", task=task,
            reason=f"unknown side effects for {[a.actionId for a in unsafe]}; "
                   "human verification required")

    def _block(self, task, reason) -> RecoveryResult:
        if task.state in (TaskState.RECOVERING, TaskState.RUNNING,
                          TaskState.OBSERVING, TaskState.VERIFYING,
                          TaskState.REPAIRING, TaskState.READY):
            task = self.task_manager.transition(task.id, TaskState.BLOCKED)
        return RecoveryResult(taskId=task.id, outcome="BLOCKED", task=task, reason=reason)

    def _resume(self, task, reason, outcome="RESUME") -> RecoveryResult:
        """RECOVERING -> READY -> RUNNING: the READY->RUNNING execution gate
        revalidates schema, policy versions, authorization, and limits (s27)."""
        try:
            task = self.task_manager.transition(task.id, TaskState.READY)
            task = self.task_manager.transition(task.id, TaskState.RUNNING)
        except Exception as exc:
            return self._block(task, f"resume revalidation failed: {exc}")
        return RecoveryResult(taskId=task.id, outcome=outcome, task=task, reason=reason)

    # -- helpers -------------------------------------------------------------------

    @staticmethod
    def _remaining_action_steps(task: Task, journal) -> Optional[int]:
        if task.resourceLimits.actionSteps is None:
            return None
        used = journal.count_event_type("ACTION_STARTED")
        return max(0, task.resourceLimits.actionSteps - used)

    def _journal_decision(self, journal, decision, action_ids, reason, resolutions=None) -> None:
        journal.append("RECOVERY_DECISION", {
            "decision": decision,
            "actionIds": action_ids,
            "resolutions": resolutions or {},
            "reason": reason,
        })

    def _update_continuity(self, task, known_side_effects=None,
                           unknown_side_effects=None, next_safe_action=None) -> None:
        from core import ContinuityState
        from task import UpdateTaskRequest
        continuity = ContinuityState(
            knownSideEffects=list(task.continuity.knownSideEffects)
            + list(known_side_effects or []),
            unknownSideEffects=list(task.continuity.unknownSideEffects)
            + list(unknown_side_effects or []),
            nextSafeAction=next_safe_action,
            lastCheckpointId=task.continuity.lastCheckpointId,
            updatedAt=utcnow_iso(),
        )
        self.task_manager.update_task(task.id, UpdateTaskRequest(continuity=continuity))
