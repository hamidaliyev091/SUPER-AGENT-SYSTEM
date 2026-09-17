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
"""
from __future__ import annotations

from typing import Optional

from core import ActionRequest, Task
from core.enums import (
    CompletionDecisionValue,
    TaskState,
)

_START_SEQUENCE = (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                   TaskState.RUNNING)
_REENTRY_SEQUENCE = (TaskState.RECOVERING, TaskState.READY, TaskState.RUNNING)
_FINAL_STATES = frozenset({TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED})
_HUMAN_STATES = frozenset({TaskState.WAITING_USER, TaskState.BLOCKED})


class Orchestrator:
    """The loop owner. It decides nothing: it only feeds proposals to the
    pipeline and applies the engines' authoritative decisions."""

    def __init__(self, task_manager, pipeline, verification, completion,
                 model, recovery=None, continuity=None):
        self.task_manager = task_manager
        self.pipeline = pipeline
        self.verification = verification
        self.completion = completion
        self.model = model
        self.recovery = recovery
        self.continuity = continuity

    def run(self, task_id: str, max_iterations: int = 200,
            checkpoint_every: Optional[int] = None) -> Task:
        """Drive the task until a terminal state, a human-required state,
        or the iteration guard. Returns the authoritative task.

        checkpoint_every > 0 persists a durable checkpoint after every N
        loop iterations (CONTINUITY s7: periodic state persistence for
        long-running autonomy); the snapshot never resets budgets (s14)."""
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

            if state is TaskState.CREATED:
                task = self._advance(task_id, _START_SEQUENCE)
                continue

            if state is TaskState.RUNNING:
                proposal = self.model.propose_action(task)
                if proposal is None:
                    task = self.task_manager.transition(task_id, TaskState.OBSERVING)
                    continue
                if not isinstance(proposal, ActionRequest):
                    continue  # malformed proposals are ignored, never executed
                result = self.pipeline.execute(proposal)
                self.model.observe(task, result)
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
                    task = self._advance(task_id, (TaskState.READY, TaskState.RUNNING))
                    continue
                return self.task_manager.get_task(task_id)

            if state is TaskState.REPAIRING:
                task = self._advance(task_id, _REENTRY_SEQUENCE)
                continue

            if state is TaskState.RECOVERING:
                task = self._advance(task_id, (TaskState.READY, TaskState.RUNNING))
                continue

        return self.task_manager.get_task(task_id)

    def _advance(self, task_id: str, states) -> Task:
        task = None
        for state in states:
            task = self.task_manager.transition(task_id, state)
        return task
