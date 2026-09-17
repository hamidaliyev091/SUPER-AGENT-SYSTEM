"""SubagentManager (Phase 17): controlled delegation via subtasks.

Security rules (ROADMAP Phase 17): a subagent can never obtain more
authority than its parent. Delegation is therefore creation with
enforced narrowing:

- every child target scope must be covered by a parent scope (per
  scope kind: read/write/search paths, packages, package operations,
  network domains/destinations, UI actions, processes, Android settings,
  resources);
- every child resource limit must not exceed the parent's (resource
  isolation: the child owns its own budget);
- delegation itself consumes the parent's delegationCount budget.

Subagents execute through the same pipeline as everyone else: a child's
TAC is the child's scope, so a malicious subagent is denied outside it by
Policy (the adversarial tests prove this).
"""
from __future__ import annotations

from typing import List

from core import Task, ValidationError
from policy.canonical import match_scope
from task import CreateTaskRequest, TaskManager, UpdateTaskRequest

_SCOPE_FIELDS = (
    "allowedReadPaths",
    "allowedWritePaths",
    "allowedSearchPaths",
    "allowedPackages",
    "allowedPackageOperations",
    "allowedNetworkDomains",
    "allowedNetworkDestinations",
    "allowedUIActions",
    "allowedProcesses",
    "allowedAndroidSettings",
    "allowedResources",
)

_LIMIT_FIELDS = (
    "wallClockTime",
    "actionSteps",
    "modelCalls",
    "retryCount",
    "delegationCount",
    "network",
    "storage",
)


class SubagentManager:
    """The only delegation path. No component may create subtasks that
    widen a parent's authority (ROADMAP Phase 17, POLICY_RULES s11)."""

    def __init__(self, task_manager: TaskManager):
        self.task_manager = task_manager

    def create_subtask(self, parent_id: str, request: CreateTaskRequest) -> Task:
        parent = self.task_manager.get_task(parent_id)
        self._validate_narrowing(parent, request)
        self._validate_delegation_budget(parent)
        request.parentTaskId = parent_id
        subtask = self.task_manager.create_task(request)
        self.task_manager.update_task(
            parent_id, UpdateTaskRequest(addSubtaskIds=[subtask.id]))
        return subtask

    # -- narrowing validation --------------------------------------------------

    @staticmethod
    def _validate_narrowing(parent: Task, request: CreateTaskRequest) -> None:
        """Every child scope must be covered by a parent scope, and every
        child limit must not exceed the parent's. Expansion raises."""
        parent_tac = parent.targetAuthorizationContext
        child_tac = request.targetAuthorizationContext
        for field in _SCOPE_FIELDS:
            parent_scopes = getattr(parent_tac, field)
            for scope in getattr(child_tac, field):
                if not match_scope(scope, parent_scopes):
                    raise ValidationError(
                        f"subagent scope {field} {scope!r} is not covered "
                        f"by the parent's {field}")
        for field in _LIMIT_FIELDS:
            parent_limit = getattr(parent.resourceLimits, field)
            child_limit = getattr(request.resourceLimits, field)
            if child_limit is not None and (parent_limit is None
                                            or child_limit > parent_limit):
                raise ValidationError(
                    f"subagent limit {field}={child_limit} exceeds the "
                    f"parent's {parent_limit}")

    def _validate_delegation_budget(self, parent: Task) -> None:
        limit = parent.resourceLimits.delegationCount
        if limit is not None and len(parent.subtaskIds) >= limit:
            raise ValidationError(
                f"delegation budget exhausted ({len(parent.subtaskIds)}/{limit})")

    def list_subtasks(self, parent_id: str) -> List[Task]:
        parent = self.task_manager.get_task(parent_id)
        return [self.task_manager.get_task(subtask_id)
                for subtask_id in parent.subtaskIds]
