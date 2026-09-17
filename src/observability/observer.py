"""TaskObserver (Phase 18): read-only reconstruction of a task's execution
history from durable records.

The observer reads the verified journal, task state, checkpoints,
verification results, and completion decisions. It holds no authority and
writes nothing. It cannot leak argument secrets: the journal stores
argument hashes, never raw arguments (POLICY_RULES s43), and tool outputs
are not journaled at all - so the reconstructed history is safe for
ordinary logs. A tampered journal makes every view fail closed
(JournalIntegrityError) rather than showing unverified records.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core import Task

from continuity.continuity_manager import ContinuityManager
from completion.store import CompletionDecisionStore
from verification.result_store import VerificationResultStore


class TaskObserver:
    """Reconstructs the complete task history without model memory."""

    def __init__(self, store, task_manager):
        self.store = store
        self.task_manager = task_manager
        self.continuity = ContinuityManager(task_manager, store)
        self.verification_results = VerificationResultStore(store.root)
        self.completion_decisions = CompletionDecisionStore(store.root)

    def _records(self, task_id) -> List[dict]:
        return self.store.journal_for(task_id).records()  # verified; raises on tampering

    # -- views -------------------------------------------------------------------

    def task_view(self, task_id: str) -> dict:
        task = self.task_manager.get_task(task_id)
        tac = task.targetAuthorizationContext
        return {
            "taskId": task.id,
            "objective": task.objective,
            "state": task.state.value,
            "permissionMode": task.permissionMode.value,
            "effortLevel": task.effortLevel.value,
            "schemaVersion": task.schemaVersion,
            "createdAt": task.createdAt,
            "updatedAt": task.updatedAt,
            "criteria": [
                {"id": c.id, "description": c.description,
                 "method": c.verificationMethod, "mandatory": c.mandatory,
                 "status": task.verification.criterionResults.get(c.id,
                     (task.verification.overall.value
                      if task.verification.overall else None))}
                for c in task.successCriteria],
            "verificationOverall": (task.verification.overall.value
                                    if task.verification.overall else None),
            "targetAuthorization": {
                "writePaths": list(tac.allowedWritePaths),
                "readPaths": list(tac.allowedReadPaths),
                "packages": list(tac.allowedPackages),
                "packageOperations": list(tac.allowedPackageOperations),
                "active": tac.is_active(),
            },
            "limits": {
                field: getattr(task.resourceLimits, field)
                for field in ("wallClockTime", "actionSteps", "modelCalls",
                              "retryCount", "delegationCount", "network",
                              "storage")},
            "usage": self.continuity.resource_usage(task),
            "failures": len(task.failures),
            "subtaskIds": list(task.subtaskIds),
            "parentTaskId": task.parentTaskId,
        }

    def action_history(self, task_id: str) -> List[dict]:
        started = {}
        actions = []
        for record in self._records(task_id):
            payload = record["payload"]
            if record["eventType"] == "ACTION_STARTED":
                entry = {"actionId": payload.get("actionId"),
                         "operationId": payload.get("operationId"),
                         "target": payload.get("target"),
                         "argumentsSha256": payload.get("argumentsSha256"),
                         "actorId": payload.get("actorId"),
                         "actorType": payload.get("actorType"),
                         "startedAt": record["timestamp"],
                         "terminalState": None}
                started[entry["actionId"]] = entry
                actions.append(entry)
            elif record["eventType"] == "ACTION_TERMINAL":
                entry = started.get(payload.get("actionId"))
                if entry is not None:
                    entry["terminalState"] = payload.get("terminalState")
                    entry["toolSuccess"] = payload.get("toolSuccess")
                    entry["sideEffectState"] = payload.get("sideEffectState")
                    entry["terminalAt"] = record["timestamp"]
        return actions

    def policy_decisions(self, task_id: str) -> List[dict]:
        return [{"operationId": r["payload"].get("operationId"),
                 "decision": r["payload"].get("decision"),
                 "reason": r["payload"].get("reason"),
                 "timestamp": r["timestamp"]}
                for r in self._records(task_id)
                if r["eventType"] == "POLICY_DECISION"]

    def approval_history(self, task_id: str) -> List[dict]:
        out = []
        for r in self._records(task_id):
            if r["eventType"] == "APPROVAL_REQUESTED":
                out.append({"approvalReference": r["payload"].get("approvalReference"),
                            "operation": r["payload"].get("operation"),
                            "timestamp": r["timestamp"], "decision": None})
            elif r["eventType"] == "APPROVAL_RESULT":
                for entry in reversed(out):
                    if entry["approvalReference"] == r["payload"].get("approvalReference"):
                        entry["decision"] = r["payload"].get("decision")
                        break
        return out

    def verification_history(self, task_id: str) -> List[dict]:
        return [{"criterionId": r["payload"].get("criterionId"),
                 "result": r["payload"].get("result"),
                 "resultDigest": r["payload"].get("resultDigest"),
                 "timestamp": r["timestamp"]}
                for r in self._records(task_id)
                if r["eventType"] == "VERIFICATION_RESULT"]

    def failure_history(self, task_id: str) -> List[dict]:
        task = self.task_manager.get_task(task_id)
        return [{"failureId": f.failureId, "category": f.category,
                 "operation": f.operation,
                 "sideEffectState": f.sideEffectState.value,
                 "timestamp": f.timestamp}
                for f in task.failures]

    def recovery_history(self, task_id: str) -> List[dict]:
        return [{"decision": r["payload"].get("decision"),
                 "actionIds": r["payload"].get("actionIds"),
                 "reason": r["payload"].get("reason"),
                 "timestamp": r["timestamp"]}
                for r in self._records(task_id)
                if r["eventType"] == "RECOVERY_DECISION"]

    def model_usage(self, task_id: str) -> List[dict]:
        return [{"role": r["payload"].get("role"),
                 "usage": r["payload"].get("usage"),
                 "finishReason": r["payload"].get("finishReason"),
                 "timestamp": r["timestamp"]}
                for r in self._records(task_id)
                if r["eventType"] == "MODEL_CALL"]

    def full_history(self, task_id: str) -> dict:
        return {
            "task": self.task_view(task_id),
            "actions": self.action_history(task_id),
            "policyDecisions": self.policy_decisions(task_id),
            "approvals": self.approval_history(task_id),
            "verificationResults": self.verification_history(task_id),
            "failures": self.failure_history(task_id),
            "recoveryDecisions": self.recovery_history(task_id),
            "modelCalls": self.model_usage(task_id),
        }

    # -- rendering ----------------------------------------------------------------

    def render(self, task_id: str) -> str:
        """Compact text rendering safe for ordinary logs (no raw argument
        values exist in the durable records to leak, s43)."""
        history = self.full_history(task_id)
        task = history["task"]
        lines = [
            f"task {task['taskId']} [{task['state']}] - {task['objective']}",
            f"  permission={task['permissionMode']} effort={task['effortLevel']}",
            f"  authorization active={task['targetAuthorization']['active']}",
            f"  usage: actions={task['usage'].get('actionStepsUsed')} "
            f"wallClock={task['usage'].get('wallClockSeconds')}",
            f"  criteria: " + ", ".join(
                f"{c['id']}={c['status']}" for c in task["criteria"]),
            f"  actions ({len(history['actions'])}):",
        ]
        for action in history["actions"]:
            lines.append(
                f"    {action['operationId']} {action['target'] or ''} "
                f"-> {action['terminalState']} "
                f"[args sha256 {action['argumentsSha256'][:12]}...]")
        lines.append(f"  policy decisions: {len(history['policyDecisions'])}")
        lines.append(f"  approvals: {len(history['approvals'])}")
        lines.append(f"  verification results: {len(history['verificationResults'])}")
        lines.append(f"  failures: {len(history['failures'])}")
        lines.append(f"  recovery decisions: {len(history['recoveryDecisions'])}")
        lines.append(f"  model calls: {len(history['modelCalls'])}")
        return "\n".join(lines)
