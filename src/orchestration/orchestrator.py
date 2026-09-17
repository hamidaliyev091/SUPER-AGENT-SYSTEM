"""Orchestrator (Phase 9): drives one task from creation to a terminal
state using a model double's proposals.

Authority boundaries (the orchestrator has NO special authority):
- Every action is a proposal; only the ExecutionPipeline executes, and
  only after Policy evaluation (INTERFACES.md s12).
- Verification is requested from the VerificationEngine; completion is
  evaluated by the CompletionEngine - the ONLY path to DONE.
- The acting model's completion claims are never read.
- Repair and continuation re-enter RUNNING through RECOVERING -> READY so
  the execution gate revalidates every re-entry (ADR-009).
- Resource limits are enforced by the pipeline and the completion engine.

Loop safety (Phase 14 WS6): an autonomous loop must be unable to run
forever, and it must never *look* finished when it stopped for a limit.
`run()` therefore bounds the run by iterations, wall-clock, repeated
failures, a stall detector on the proposals themselves, and the model's
own token budget; every one of those ends in an explicit BLOCKED task with
the reason recorded durably (a Decision on the task), never in a
still-RUNNING task that merely ran out of loop (ADR-020).

Durable approval (Phase 14 WS7): when policy answered ASK and no human has
decided yet, the pipeline executes nothing and the loop pauses the task in
WAITING_USER - a pending approval is a question, not a permission, and only
an authorized resume (CLI) carries the task past it (ADR-021).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from core import ActionRequest, Decision, Task, utcnow_iso
from core.enums import (
    CompletionDecisionValue,
    TaskState,
)
from task import UpdateTaskRequest

_START_SEQUENCE = (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                   TaskState.RUNNING)
_REENTRY_SEQUENCE = (TaskState.RECOVERING, TaskState.READY, TaskState.RUNNING)
_FINAL_STATES = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED})
_HUMAN_STATES = frozenset({TaskState.WAITING_USER, TaskState.BLOCKED})

#: Consecutive RUNNING turns that produce no new action before the loop
#: stops. A turn counts as progress when the model proposes something it
#: has not just proposed; repeating a proposal, or proposing nothing at
#: all, while the task is not complete, is a stall.
DEFAULT_STALL_LIMIT = 3

#: Durable decision recorded when the loop stops for a limit.
LOOP_STOPPED = "loop_stopped"

#: Recovery outcomes that let the loop carry on; anything else is a state
#: a human (or a later authorized run) must act on.
_RESUMING_OUTCOMES = frozenset({"RESUME", "RETRY"})


def _proposal_signature(proposal):
    """A comparable identity for a proposal: the same operation on the same
    arguments is the same proposal, however it was phrased."""
    if not isinstance(proposal, ActionRequest):
        return None
    try:
        arguments = json.dumps(proposal.arguments, sort_keys=True,
                               separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        arguments = str(proposal.arguments)
    return (proposal.toolId, arguments)


class Orchestrator:
    """The loop owner. It decides nothing: it only feeds proposals to the
    pipeline and applies the engines' authoritative decisions."""

    def __init__(self, task_manager, pipeline, verification, completion,
                 model, recovery=None, continuity=None, approvals=None):
        self.task_manager = task_manager
        self.pipeline = pipeline
        self.verification = verification
        self.completion = completion
        self.model = model
        self.recovery = recovery
        self.continuity = continuity
        self.approvals = approvals

    def run(self, task_id: str, max_iterations: int = 200,
            checkpoint_every: Optional[int] = None,
            deadline_seconds: Optional[float] = None,
            stall_limit: Optional[int] = DEFAULT_STALL_LIMIT,
            failure_limit: Optional[int] = None) -> Task:
        """Drive the task until a terminal state, a human-required state,
        or a declared limit. Returns the authoritative task.

        checkpoint_every > 0 persists a durable checkpoint after every N
        loop iterations (CONTINUITY s7: periodic state persistence for
        long-running autonomy); the snapshot never resets budgets (s14).

        deadline_seconds bounds the whole run; stall_limit bounds
        consecutive turns without a new action; failure_limit bounds the
        task's durable failure record. Every limit ends the run in BLOCKED
        with the reason recorded on the task.
        """
        deadline = None
        if deadline_seconds is not None:
            deadline = time.monotonic() + max(0.0, float(deadline_seconds))
        stalls = 0
        previous_signature = None

        for iteration in range(max_iterations):
            task = self.task_manager.get_task(task_id)
            state = task.state

            if self.continuity is not None and checkpoint_every \
                    and iteration > 0 and iteration % checkpoint_every == 0 \
                    and state not in _FINAL_STATES | _HUMAN_STATES:
                self.continuity.checkpoint(task_id)

            if state in _FINAL_STATES:
                return task
            if state in _HUMAN_STATES:
                return task  # only an authorized resume can continue (s22)

            reason = self._limit_reason(task, deadline, failure_limit)
            if reason is None and state is TaskState.RUNNING:
                # The model's own budget is checked before asking it again.
                reason = self._model_limit_reason(task)
            if reason is not None:
                return self._stop(task_id, reason)

            if state is TaskState.CREATED:
                task = self._advance(task_id, _START_SEQUENCE)
                continue

            if state is TaskState.RUNNING:
                proposal = self.model.propose_action(task)
                signature = _proposal_signature(proposal)
                if signature is not None and signature != previous_signature:
                    stalls = 0
                else:
                    stalls += 1
                    if stall_limit is not None and stalls >= stall_limit:
                        return self._stop(task_id, self._stall_reason(stalls))
                previous_signature = signature
                if proposal is None:
                    task = self.task_manager.transition(task_id, TaskState.OBSERVING)
                    continue
                if not isinstance(proposal, ActionRequest):
                    continue  # malformed proposals are ignored, never executed
                result = self.pipeline.execute(proposal)
                self.model.observe(task, result)
                if result.executed and not result.journalTerminalRecorded:
                    # The action ran but its terminal state is not durable,
                    # so its side effect is unknown. Continuing would be a
                    # blind retry of a possibly-applied action (s9/s12):
                    # recovery decides, not the loop.
                    task = self.task_manager.transition(task_id, TaskState.RECOVERING)
                    if self.recovery is None:
                        return self._stop(task_id,
                                          "action terminal state is not durable and "
                                          "no recovery manager is available")
                    outcome = self.recovery.recover(task_id)
                    if outcome.outcome not in _RESUMING_OUTCOMES:
                        return self.task_manager.get_task(task_id)
                    continue
                if self.approvals is not None:
                    try:
                        pending = self.approvals.has_pending(task_id)
                    except Exception as exc:
                        # Approval state that cannot be read is never "no
                        # approval waiting": the run stops instead of
                        # continuing past an unanswered ask (s15).
                        return self._stop(task_id, f"approval state is unreadable: {exc}")
                    if pending:
                        # Policy said ASK and no human has answered it yet, so
                        # nothing was executed. The task waits for a decision;
                        # only an authorized resume continues it (s22).
                        return self.task_manager.transition(task_id,
                                                             TaskState.WAITING_USER)
                task = self.task_manager.transition(task_id, TaskState.OBSERVING)
                continue

            if state is TaskState.OBSERVING:
                task = self.task_manager.transition(task_id, TaskState.VERIFYING)
                continue

            if state is TaskState.VERIFYING:
                self.verification.verify(task_id)
                decision = self.completion.evaluate(task_id)
                if decision.decision is CompletionDecisionValue.DONE:
                    return self.task_manager.get_task(task_id)
                if decision.decision is CompletionDecisionValue.REPAIR:
                    task = self.task_manager.transition(task_id, TaskState.REPAIRING)
                    continue
                if decision.decision is CompletionDecisionValue.CONTINUE:
                    task = self._advance(task_id, (TaskState.RECOVERING,))
                    continue
                return self.task_manager.get_task(task_id)

            if state is TaskState.REPAIRING:
                task = self._advance(task_id, _REENTRY_SEQUENCE)
                continue

            if state is TaskState.RECOVERING:
                # Either the loop itself just detected an action without a
                # durable terminal record (recovery already decided), or a
                # human/system resume authorized this re-entry. Either way
                # READY -> RUNNING re-runs the execution gate (ADR-009).
                task = self._advance(task_id, (TaskState.READY, TaskState.RUNNING))
                continue

        return self._stop(task_id, f"iteration limit reached ({max_iterations})")

    # -- limits ---------------------------------------------------------------

    def _limit_reason(self, task: Task, deadline, failure_limit) -> Optional[str]:
        if deadline is not None and time.monotonic() >= deadline:
            return "wall-clock deadline reached"
        if failure_limit is not None and len(task.failures) >= failure_limit:
            return (f"repeated-failure limit reached "
                    f"({len(task.failures)}/{failure_limit})")
        return None

    def _model_limit_reason(self, task: Task) -> Optional[str]:
        """The model port reports its own budget exhaustion (the driver
        sums tokens from the journal), so the loop stops instead of asking
        a provider that must not answer."""
        exhausted = getattr(self.model, "exhausted", None)
        if not callable(exhausted):
            return None
        return exhausted(task)

    @staticmethod
    def _stall_reason(stalls: int) -> str:
        return (f"no progress: {stalls} consecutive turns without a new action")

    def _stop(self, task_id: str, reason: str) -> Task:
        """End the run explicitly: record why and block the task.

        The loop must never hand back a task that still looks runnable when
        it stopped for a limit - a blocked task needs an authorized resume
        (TASK_SCHEMA s22), so a limit is visible to the operator instead of
        being silently retried.
        """
        task = self.task_manager.get_task(task_id)
        if task.state in _FINAL_STATES | _HUMAN_STATES:
            return task
        try:
            self.task_manager.update_task(task_id, UpdateTaskRequest(
                appendDecisions=[Decision(
                    decisionId=uuid.uuid4().hex, taskId=task_id,
                    timestamp=utcnow_iso(), decision=LOOP_STOPPED,
                    rationale=reason)]))
        except Exception:
            pass  # the stop itself must not depend on the record landing
        return self.task_manager.transition(task_id, TaskState.BLOCKED)

    def _advance(self, task_id: str, states) -> Task:
        task = None
        for state in states:
            task = self.task_manager.transition(task_id, state)
        return task
