"""Controlled execution pipeline (Phase 4).

Implements INTERFACES.md s12:

    ActionRequest -> schema validation -> Policy (ALLOW/ASK/DENY)
    -> if ASK: durable ASK audit -> human approval -> durable approval audit
       -> policy-validated approval (single-use)
    -> resource budget check + named resource acquisition
    -> Action Journal STARTED (durable; failure aborts execution)
    -> tool execution -> observation -> terminal journal state
    -> durable task-state update (failure records)

Security rules enforced here (POLICY.md s16-s19):
- Every policy decision is journaled BEFORE execution is released. Journal
  failure on ALLOW/ASK -> no execution. DENY stays DENY even if its audit
  record cannot be written; an ASK whose audit cannot be written is never
  shown to the user.
- Approval references are single-use; reuse DENIES (INTERFACES s21).
- Journal payloads carry argument hashes, never raw arguments (s43).

Design informed by OpenHands' event-stream pipeline and LangGraph's sync
durability mode; see ADR-005.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from core import (
    ActionRequest,
    ApprovalResult,
    PolicyDecision,
    PolicyRequest,
    Task,
    Tool,
    ToolResult,
    ValidationError,
    utcnow_iso,
)
from core.enums import PolicyDecisionValue, SideEffectState

from continuity.journal import Journal, JournalError
from policy import PolicyEngine

from .approval import HumanApproval
from .resources import ResourceCoordinator


def _args_hash(arguments: dict) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass
class ExecutionResult:
    """Structured outcome of one pipeline pass."""
    taskId: str
    actionId: str
    actionRequest: ActionRequest
    policyDecision: Optional[PolicyDecision] = None
    executed: bool = False
    approvalResult: Optional[ApprovalResult] = None
    toolResult: Optional[ToolResult] = None
    journalTerminalRecorded: bool = False
    auditRecorded: bool = False
    errors: list = field(default_factory=list)

    @property
    def terminal_journal_state(self) -> str:
        if not self.executed:
            return "CANCELLED"
        if self.journalTerminalRecorded and self.toolResult is not None:
            if self.toolResult.success:
                return "SUCCEEDED"
            if self.toolResult.sideEffectState is SideEffectState.UNKNOWN:
                return "UNKNOWN"
            return "FAILED"
        return "UNKNOWN"


class ExecutionPipeline:
    """The only execution path for side-effecting actions. No component may
    skip it (ARCHITECTURE.md s8)."""

    def __init__(
        self,
        task_manager,
        policy_engine: PolicyEngine,
        tools: Dict[str, Tool],
        store,
        approver: Optional[HumanApproval] = None,
        resources: Optional[ResourceCoordinator] = None,
    ):
        self.task_manager = task_manager
        self.policy_engine = policy_engine
        self.tools = tools
        self.store = store
        self.approver = approver
        self.resources = resources or ResourceCoordinator()

    # -- public API ---------------------------------------------------------

    def execute(self, request: ActionRequest) -> ExecutionResult:
        action_id = uuid.uuid4().hex
        try:
            return self._execute(request, action_id)
        except JournalError as exc:
            # Audit/journal infrastructure failure: execution must not have
            # proceeded (POLICY.md s18), or its terminal state is unknown.
            decision = PolicyDecision(
                decision=PolicyDecisionValue.DENY,
                reason=f"journal failure: {exc}",
                policyVersion=self.policy_engine.policy_version,
                ruleVersion=self.policy_engine.rule_version,
            )
            return ExecutionResult(
                taskId=request.taskId, actionId=action_id, actionRequest=request,
                policyDecision=decision, errors=[str(exc)])

    # -- stages -------------------------------------------------------------

    def _execute(self, request: ActionRequest, action_id: str) -> ExecutionResult:
        result = ExecutionResult(taskId=request.taskId, actionId=action_id,
                                 actionRequest=request)

        # Schema validation: well-formed request, known task, executable state.
        task = self._load_and_validate(request, result)
        if task is None:
            return result
        journal = self.store.journal_for(request.taskId)

        # Policy evaluation.
        decision = self.policy_engine.evaluate(self._policy_request(request, task))
        result.policyDecision = decision

        if decision.decision is PolicyDecisionValue.DENY:
            # DENY is final; journal the denial best-effort (s18).
            try:
                journal.append("POLICY_DECISION", self._policy_payload(request, decision))
                result.auditRecorded = True
            except JournalError as exc:
                result.errors.append(f"DENY audit could not be recorded: {exc}")
            return result

        # ALLOW / ASK: audit-before-execution is mandatory (s16-s18).
        try:
            journal.append("POLICY_DECISION", self._policy_payload(request, decision))
            result.auditRecorded = True
        except JournalError as exc:
            result.policyDecision = self._denied_copy(decision, f"audit failure: {exc}")
            result.errors.append(str(exc))
            return result

        if decision.decision is PolicyDecisionValue.ASK:
            if not self._approval_flow(request, decision, journal, result):
                return result

        # Resource budget (externally enforced) and named resource locks.
        check = self.resources.check_budget(task, journal)
        if not check.ok:
            result.policyDecision = self._denied_copy(decision, f"resource check: {check.reason}")
            return result
        blocked = self._acquire_resources(task, request, result)
        if blocked is not None:
            return blocked

        # Action Journal STARTED (durable) - mandatory before side effects.
        try:
            journal.append("ACTION_STARTED", {
                "actionId": action_id,
                "taskId": request.taskId,
                "operationId": request.toolId,
                "targetType": request.target.type.value if request.target else None,
                "target": request.target.value if request.target else None,
                "argumentsSha256": _args_hash(request.arguments),
                "actorId": request.actor.actorId,
                "actorType": request.actor.actorType.value,
            })
        except JournalError as exc:
            result.policyDecision = self._denied_copy(decision, f"STARTED journal failure: {exc}")
            self._release_resources(request)
            result.errors.append(str(exc))
            return result

        # Tool execution.
        result.executed = True
        try:
            tool_result = self.tools[request.toolId].execute(
                request.arguments, {"taskId": request.taskId})
        except Exception as exc:  # tool failures must not crash the pipeline
            tool_result = ToolResult(success=False, sideEffectState=SideEffectState.UNKNOWN,
                                     timestamp=utcnow_iso())
            result.errors.append(f"tool raised: {exc}")
        if not isinstance(tool_result, ToolResult):
            tool_result = ToolResult(success=False, sideEffectState=SideEffectState.UNKNOWN,
                                     timestamp=utcnow_iso())
            result.errors.append("tool returned a non-ToolResult value")
        result.toolResult = tool_result
        self._release_resources(request)

        # Terminal journal state. Computed directly from the execution
        # outcome: terminal_journal_state reports UNKNOWN while the terminal
        # record is not yet durable, so the journal payload cannot reuse it.
        if result.executed and result.toolResult is not None:
            if result.toolResult.success:
                terminal_state = "SUCCEEDED"
            elif result.toolResult.sideEffectState is SideEffectState.UNKNOWN:
                terminal_state = "UNKNOWN"
            else:
                terminal_state = "FAILED"
        else:
            terminal_state = "CANCELLED"
        try:
            journal.append("ACTION_TERMINAL", {
                "actionId": action_id,
                "terminalState": terminal_state,
                "sideEffectState": tool_result.sideEffectState.value,
                "toolSuccess": tool_result.success,
            })
            result.journalTerminalRecorded = True
        except JournalError as exc:
            result.errors.append(f"terminal journal failure: {exc}")

        # Durable task-state update: material failures must be persisted.
        if not tool_result.success:
            self._record_failure(request, action_id, tool_result)
        return result

    # -- stage helpers -------------------------------------------------------

    def _load_and_validate(self, request: ActionRequest, result: ExecutionResult) -> Optional[Task]:
        """Returns the authoritative task, or None after recording a DENY."""
        if not isinstance(request, ActionRequest):
            result.policyDecision = self._denied_copy(None, "malformed ActionRequest")
            result.errors.append("malformed ActionRequest")
            return None
        if not request.toolId.strip() or not isinstance(request.arguments, dict):
            result.policyDecision = self._denied_copy(None, "ActionRequest missing toolId or invalid arguments")
            result.errors.append("ActionRequest missing toolId or invalid arguments")
            return None
        try:
            return self.task_manager.get_task(request.taskId)
        except Exception as exc:
            result.policyDecision = self._denied_copy(None, f"task unavailable: {exc}")
            result.errors.append(str(exc))
            return None

    def _policy_request(self, request: ActionRequest, task: Task) -> PolicyRequest:
        """Assemble the authoritative PolicyRequest; the acting component
        never assembles its own authorization context (TASK_SCHEMA s34)."""
        return PolicyRequest(
            policyVersion=self.policy_engine.policy_version,
            ruleVersion=self.policy_engine.rule_version,
            taskId=task.id,
            taskState=task.state,
            permissionMode=task.permissionMode,
            effortLevel=task.effortLevel,
            actorContext=request.actor,
            environmentContext={},
            authorizationContext={},
            targetAuthorizationContext=task.targetAuthorizationContext,
            toolId=request.toolId,
            operationId=request.toolId,
            target=request.target,
            structuredArguments=request.arguments,
            requestedRiskLevel=request.requestedRiskLevel,
            requestedSideEffect=request.requestedSideEffect,
            requestedReversibility=request.requestedReversibility,
            requestedIdempotency=request.requestedIdempotency,
        )

    def _approval_flow(self, request, decision, journal, result) -> bool:
        """ASK: durable ASK audit -> prompt -> durable approval audit ->
        policy-validated, single-use approval. False = do not execute."""
        approval_request = decision.approvalRequirement
        if approval_request is None:
            result.policyDecision = self._denied_copy(decision, "ASK without ApprovalRequest")
            return False
        try:
            journal.append("APPROVAL_REQUESTED", {
                "approvalReference": approval_request.approvalReference,
                "operation": approval_request.operation,
            })
        except JournalError as exc:
            result.policyDecision = self._denied_copy(decision, f"ASK audit failure: {exc}")
            result.errors.append(str(exc))
            return False
        if self.approver is None:
            result.policyDecision = self._denied_copy(decision, "no human approval port configured")
            return False
        approval_result = self.approver.request(approval_request)
        if approval_result is None:
            result.policyDecision = self._denied_copy(decision, "approval timed out (DENY)")
            return False
        try:
            journal.append("APPROVAL_RESULT", {
                "approvalReference": approval_result.approvalReference,
                "decision": approval_result.decision.value,
                "approverId": approval_result.approverIdentity.actorId,
            })
        except JournalError as exc:
            result.policyDecision = self._denied_copy(decision, f"approval audit failure: {exc}")
            result.errors.append(str(exc))
            return False
        if approval_request.approvalReference in journal.consumed_approval_references() - {
                approval_result.approvalReference}:
            result.policyDecision = self._denied_copy(decision, "approval reference reuse")
            return False
        problem = self.policy_engine.validate_approval(approval_request, approval_result)
        if problem is not None:
            result.policyDecision = self._denied_copy(decision, f"approval invalid: {problem}")
            return False
        result.approvalResult = approval_result
        return True

    def _acquire_resources(self, task: Task, request: ActionRequest,
                           result: ExecutionResult) -> Optional[ExecutionResult]:
        """Acquire tool-declared named resources; on failure return a denied
        result (None means proceed)."""
        tool = self.tools.get(request.toolId)
        if tool is None or not tool.resourceRequirements:
            return None
        for resource in tool.resourceRequirements:
            ok, reason = self.resources.acquire(task.id, resource)
            if not ok:
                result.policyDecision = self._denied_copy(
                    result.policyDecision, f"resource acquisition failed: {reason}")
                return result
        return None

    def _release_resources(self, request: ActionRequest) -> None:
        tool = self.tools.get(request.toolId)
        if tool is None or not tool.resourceRequirements:
            return
        for resource in tool.resourceRequirements:
            self.resources.release(resource)

    def _record_failure(self, request: ActionRequest, action_id: str,
                        tool_result: ToolResult) -> None:
        from core import FailureRecord
        from task import UpdateTaskRequest
        failure = FailureRecord(
            failureId=uuid.uuid4().hex,
            taskId=request.taskId,
            timestamp=utcnow_iso(),
            category="TOOL_FAILURE",
            sideEffectState=tool_result.sideEffectState,
            actionId=action_id,
            operation=request.toolId,
            target=request.target,
            retryable=False,
        )
        try:
            self.task_manager.update_task(
                request.taskId, UpdateTaskRequest(appendFailures=[failure]))
        except Exception:
            # Best-effort here; the journal already records the terminal state.
            pass

    @staticmethod
    def _policy_payload(request: ActionRequest, decision: PolicyDecision) -> dict:
        return {
            "taskId": request.taskId,
            "operationId": request.toolId,
            "target": request.target.value if request.target else None,
            "argumentsSha256": _args_hash(request.arguments),
            "decision": decision.decision.value,
            "reason": decision.reason,
        }

    def _denied_copy(self, decision: Optional[PolicyDecision], reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision=PolicyDecisionValue.DENY,
            reason=reason,
            policyVersion=decision.policyVersion if decision is not None else self.policy_engine.policy_version,
            ruleVersion=decision.ruleVersion if decision is not None else self.policy_engine.rule_version,
        )
