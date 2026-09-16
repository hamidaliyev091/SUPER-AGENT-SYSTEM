"""Continuity Manager (Phase 7).

CONTINUITY.md s6/s7/s16.1/s17/s26: enriched checkpoints (journal-derived
resource accounting, computed remaining budget, continuity summary) and
the continuity brief for compaction and model/runtime handoff.

The brief is a derived operational aid and MUST NOT replace authoritative
durable state (s17): task.json, the integrity-enveloped checkpoints, and
the hash-chained Action Journal remain authoritative.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from core import Checkpoint, ResourceLimits, Task, parse_iso, utcnow_iso
from core.enums import TaskState

from .task_store import TaskIntegrityError, TaskNotFoundError


@dataclass
class ContinuityBrief:
    """s17: concise continuity representation for compaction/handoff."""
    taskId: str
    objective: str
    lifecycleState: str
    completedWork: List[str] = field(default_factory=list)
    activeWork: List[str] = field(default_factory=list)
    nextSafeAction: Optional[str] = None
    importantDecisions: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    verificationStatus: Optional[str] = None
    resourceStatus: str = ""
    authorizationStatus: str = ""
    knownSideEffects: List[str] = field(default_factory=list)
    unknownSideEffects: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    doNotRepeatBlindly: List[str] = field(default_factory=list)

    def text(self) -> str:
        """Compact rendering for model handoff (a derived aid only)."""
        lines = [
            f"Task {self.taskId} [{self.lifecycleState}]",
            f"Objective: {self.objective}",
            f"Completed: {', '.join(self.completedWork) or '(nothing durable)'}",
            f"Active: {', '.join(self.activeWork) or '(none)'}",
            f"Next safe action: {self.nextSafeAction or '(none known)'}",
            f"Verification: {self.verificationStatus or 'unset'}",
            f"Resources: {self.resourceStatus}",
            f"Authorization: {self.authorizationStatus}",
            f"Known side effects: {', '.join(self.knownSideEffects) or '(none)'}",
            f"Unknown side effects: {', '.join(self.unknownSideEffects) or '(none)'}",
            f"Risks: {', '.join(self.risks) or '(none)'}",
            f"Do not repeat blindly: {', '.join(self.doNotRepeatBlindly) or '(none)'}",
            f"Constraints: {', '.join(self.constraints) or '(none)'}",
            f"Decisions: {'; '.join(self.importantDecisions) or '(none)'}",
            f"Failures: {'; '.join(self.failures) or '(none)'}",
        ]
        return "\n".join(lines)


class ContinuityManager:
    """Enriched checkpoints, resource-accounting continuity, continuity brief."""

    def __init__(self, task_manager, store):
        self.task_manager = task_manager
        self.store = store

    # -- resource accounting (s14/s26) ----------------------------------------

    def resource_usage(self, task: Task) -> dict:
        """Journal-derived usage. Never resets merely because continuity
        was restored (s14)."""
        records = self.store.journal_for(task.id).records()  # raises on tampering
        actions = sum(1 for r in records if r["eventType"] == "ACTION_STARTED")
        last_ts = records[-1]["timestamp"] if records else task.createdAt
        started = parse_iso(task.createdAt)
        ended = parse_iso(last_ts)
        wall = None
        if started is not None and ended is not None:
            wall = max(0.0, (ended - started).total_seconds())
        return {"actionStepsUsed": actions, "wallClockSeconds": wall}

    def remaining_budget(self, task: Task) -> dict:
        usage = self.resource_usage(task)
        limits = task.resourceLimits
        remaining = {}
        if limits.actionSteps is not None:
            remaining["actionStepsRemaining"] = max(0, limits.actionSteps - usage["actionStepsUsed"])
        if limits.wallClockTime is not None and usage["wallClockSeconds"] is not None:
            remaining["wallClockSecondsRemaining"] = max(
                0.0, limits.wallClockTime - usage["wallClockSeconds"])
        return remaining

    # -- checkpoints (s6/s7) ----------------------------------------------------

    def checkpoint(self, task_id: str) -> Checkpoint:
        """Enriched durable snapshot. Adds journal-derived resource usage,
        computed remaining budget, and derived progress fields to the
        TaskManager minimum."""
        task = self.store.load_task(task_id)
        if task.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            raise ValueError(f"cannot checkpoint a task in terminal state {task.state.value}")
        records = self.store.journal_for(task.id).records()
        started_payloads = {r["payload"].get("actionId"): r["payload"]
                            for r in records if r["eventType"] == "ACTION_STARTED"}
        terminal_ids = {r["payload"].get("actionId")
                        for r in records if r["eventType"] == "ACTION_TERMINAL"}
        completed = []
        for r in records:
            if r["eventType"] != "ACTION_TERMINAL" or \
                    r["payload"].get("terminalState") != "SUCCEEDED":
                continue
            start = started_payloads.get(r["payload"].get("actionId")) or {}
            completed.append(start.get("operationId", "?"))
        active = [payload.get("operationId", "?")
                  for action_id, payload in started_payloads.items()
                  if action_id not in terminal_ids]
        usage = self.resource_usage(task)
        remaining = self.remaining_budget(task)
        limits = task.resourceLimits
        remaining_limits = ResourceLimits(
            actionSteps=remaining.get("actionStepsRemaining"),
            wallClockTime=(int(remaining["wallClockSecondsRemaining"])
                           if "wallClockSecondsRemaining" in remaining else None),
        ) if (limits.actionSteps is not None or limits.wallClockTime is not None) else None
        checkpoint = Checkpoint(
            checkpointId=uuid.uuid4().hex,
            taskId=task.id,
            timestamp=utcnow_iso(),
            lifecycleState=task.state,
            objective=task.objective,
            successCriteria=list(task.successCriteria),
            currentPlan=task.plan,
            completedSteps=completed,
            activeStep=active[0] if active else None,
            pendingActions=[step.description for step in (task.plan.steps if task.plan else [])],
            recentFailures=list(task.failures[-5:]),
            resourceUsage=usage,
            remainingBudget=remaining_limits,
            policyContext=task.policyContext,
            authorizationContext=task.targetAuthorizationContext,
            verificationStatus=task.verification,
            knownSideEffects=list(task.continuity.knownSideEffects),
            unknownSideEffects=list(task.continuity.unknownSideEffects),
            continuitySummary=self.continuity_brief(task.id).text(),
        )
        self.store.save_checkpoint(checkpoint)
        return checkpoint

    # -- continuity brief (s17) ------------------------------------------------

    def continuity_brief(self, task_id) -> ContinuityBrief:
        task = self.store.load_task(task_id)
        records = self.store.journal_for(task.id).records()
        started_payloads = {r["payload"].get("actionId"): r["payload"]
                            for r in records if r["eventType"] == "ACTION_STARTED"}
        terminal_ids = {r["payload"].get("actionId")
                        for r in records if r["eventType"] == "ACTION_TERMINAL"}
        completed = []
        for r in records:
            if r["eventType"] != "ACTION_TERMINAL" or \
                    r["payload"].get("terminalState") != "SUCCEEDED":
                continue
            start = started_payloads.get(r["payload"].get("actionId")) or {}
            completed.append(f"{start.get('operationId', '?')}({start.get('target', '')})")
        interrupted = [
            f"{payload.get('operationId', '?')} ({payload.get('target', '')})"
            for action_id, payload in started_payloads.items()
            if action_id not in terminal_ids
        ]
        usage = self.resource_usage(task)
        limits = task.resourceLimits
        resource_status = f"actions {usage['actionStepsUsed']}"
        if limits.actionSteps is not None:
            resource_status += f"/{limits.actionSteps}"
        if usage["wallClockSeconds"] is not None:
            resource_status += f", wallClock {usage['wallClockSeconds']:.0f}s"
            if limits.wallClockTime is not None:
                resource_status += f"/{limits.wallClockTime}s"
        return ContinuityBrief(
            taskId=task.id,
            objective=task.objective,
            lifecycleState=task.state.value,
            completedWork=completed,
            activeWork=interrupted,
            nextSafeAction=task.continuity.nextSafeAction,
            importantDecisions=[d.rationale or d.decision for d in task.decisions[-5:]],
            failures=[f"{f.category}:{f.operation or ''}" for f in task.failures[-5:]],
            risks=list(task.continuity.unknownSideEffects),
            verificationStatus=(task.verification.overall.value
                                if task.verification.overall else None),
            resourceStatus=resource_status,
            authorizationStatus=("active" if task.targetAuthorizationContext.is_active()
                                 else "expired/invalid"),
            knownSideEffects=list(task.continuity.knownSideEffects),
            unknownSideEffects=list(task.continuity.unknownSideEffects),
            constraints=[
                f"permissionMode={task.permissionMode.value}",
                f"effortLevel={task.effortLevel.value}",
            ],
            doNotRepeatBlindly=list(interrupted),
        )

    # -- compaction preparation (s16.1) ----------------------------------------

    def prepare_for_compaction(self, task_id) -> ContinuityBrief:
        """s16.1: ensure a durable snapshot and a brief exist before
        compaction. Persistence failure raises - compaction must be
        deferred or the task moved to a safe non-executable state."""
        self.checkpoint(task_id)
        return self.continuity_brief(task_id)
