"""Delegation and subagents (Phase 17): narrowing-only subtask creation,
delegation budgets, and the malicious-subagent boundary."""
import unittest

from tests.support.harness import VerificationTestBase, criterion

from core import (
    ActionRequest,
    ActorIdentity,
    CompletionContract,
    ResourceLimits,
    Target,
    TargetAuthorizationContext,
    ValidationError,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    EffortLevel,
    PermissionMode,
    TargetType,
    TaskState,
)
from completion import CompletionEngine
from delegation import SubagentManager
from task import CreateTaskRequest


class DelegationTests(VerificationTestBase):

    def setUp(self):
        super().setUp()
        self.subagents = SubagentManager(self.mgr)

    def parent_task(self, **overrides):
        task = self.make_task(stop_at=TaskState.RUNNING, **overrides)
        return task

    def subtask_request(self, parent_id, tac, limits):
        criteria = [criterion("c1")]
        return CreateTaskRequest(
            objective="subtask objective",
            permissionMode=PermissionMode.AUTO,
            effortLevel=EffortLevel.STANDARD,
            resourceLimits=limits,
            targetAuthorizationContext=tac,
            successCriteria=criteria,
            completionContract=CompletionContract(
                objective="subtask objective", successCriteria=criteria),
            parentTaskId=parent_id,
        )

    def test_narrowed_subtask_creation(self):
        parent = self.parent_task()
        now = utcnow_iso()
        narrow_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=["/data/out/sub/**"],
            allowedWritePaths=["/data/out/sub/**"],
            createdAt=now, updatedAt=now,
        )
        subtask = self.subagents.create_subtask(
            parent.id, self.subtask_request(
                parent.id, narrow_tac, ResourceLimits(actionSteps=10)))
        parent = self.mgr.get_task(parent.id)
        self.assertIn(subtask.id, parent.subtaskIds)
        self.assertEqual(self.mgr.get_task(subtask.id).parentTaskId, parent.id)
        self.assertEqual(self.subagents.list_subtasks(parent.id)[0].id, subtask.id)

    def test_scope_expansion_rejected(self):
        parent = self.parent_task()
        now = utcnow_iso()
        wider_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedWritePaths=["/etc/**"],  # not covered by parent /data/out/**
            createdAt=now, updatedAt=now,
        )
        with self.assertRaises(ValidationError):
            self.subagents.create_subtask(
                parent.id, self.subtask_request(
                    parent.id, wider_tac, ResourceLimits(actionSteps=10)))

    def test_limit_expansion_rejected(self):
        parent = self.parent_task()  # parent actionSteps=100
        now = utcnow_iso()
        narrow_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedWritePaths=["/data/out/**"],
            createdAt=now, updatedAt=now,
        )
        with self.assertRaises(ValidationError):
            self.subagents.create_subtask(
                parent.id, self.subtask_request(
                    parent.id, narrow_tac, ResourceLimits(actionSteps=200)))

    def test_delegation_budget_enforced(self):
        parent = self.parent_task(
            resourceLimits=ResourceLimits(actionSteps=100, delegationCount=1))
        now = utcnow_iso()
        narrow_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedWritePaths=["/data/out/**"],
            createdAt=now, updatedAt=now,
        )
        first = self.subagents.create_subtask(
            parent.id, self.subtask_request(
                parent.id, narrow_tac, ResourceLimits(actionSteps=10)))
        self.assertIsNotNone(first)
        with self.assertRaises(ValidationError):
            self.subagents.create_subtask(
                parent.id, self.subtask_request(
                    parent.id, narrow_tac, ResourceLimits(actionSteps=10)))

    def test_malicious_subagent_cannot_escape_its_scope(self):
        parent = self.parent_task()
        now = utcnow_iso()
        narrow_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedWritePaths=["/data/out/sub/**"],
            createdAt=now, updatedAt=now,
        )
        subtask = self.subagents.create_subtask(
            parent.id, self.subtask_request(
                parent.id, narrow_tac, ResourceLimits(actionSteps=10)))
        subtask = self.mgr.get_task(subtask.id)
        for state in (TaskState.VALIDATING, TaskState.PLANNING,
                      TaskState.READY, TaskState.RUNNING):
            subtask = self.mgr.transition(subtask.id, state)
        request = ActionRequest(
            taskId=subtask.id,
            actor=ActorIdentity(actorId="sub-1", actorType=ActorType.SUBAGENT,
                                taskId=subtask.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/evil.txt"),
            arguments={"path": "/data/out/evil.txt", "content": "x"},
            reason="subagent scope escape attempt")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_subtask_completes_through_the_same_engines(self):
        parent = self.parent_task()
        now = utcnow_iso()
        narrow_tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=["/data/out/**"],
            allowedWritePaths=["/data/out/**"],
            allowedPackages=parent.targetAuthorizationContext.allowedPackages,
            allowedPackageOperations=parent.targetAuthorizationContext.allowedPackageOperations,
            createdAt=now, updatedAt=now,
        )
        subtask = self.subagents.create_subtask(
            parent.id, self.subtask_request(
                parent.id, narrow_tac, ResourceLimits(actionSteps=20)))
        self.files["/data/out/x.txt"] = "HELLO"
        subtask = self.mgr.get_task(subtask.id)
        for state in (TaskState.VALIDATING, TaskState.PLANNING,
                      TaskState.READY, TaskState.RUNNING,
                      TaskState.OBSERVING, TaskState.VERIFYING):
            subtask = self.mgr.transition(subtask.id, state)
        report = self.engine.verify(subtask.id)
        self.assertEqual(report.status.value, "PASS")
        decision = CompletionEngine(self.mgr, self.store).evaluate(subtask.id)
        self.assertEqual(decision.decision.value, "DONE")
        self.assertIs(self.mgr.get_task(subtask.id).state, TaskState.DONE)


if __name__ == "__main__":
    unittest.main()
