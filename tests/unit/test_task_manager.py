"""TaskManager lifecycle-authority tests (INTERFACES.md s7, TASK_SCHEMA.md s22/s33)."""
import json
import tempfile
import unittest
from pathlib import Path

from continuity.task_store import TaskStore
from task.task_manager import (
    CreateTaskRequest,
    TaskGateBlockedError,
    TaskManager,
    UpdateTaskRequest,
)

from core import (
    Checkpoint,
    CompletionContract,
    ContinuityState,
    Decision,
    FailureRecord,
    Plan,
    PlanStep,
    ResourceLimits,
    SuccessCriterion,
    TargetAuthorizationContext,
    Task,
    ValidationError,
    VerificationState,
    utcnow_iso,
)
from core.enums import (
    EffortLevel,
    PermissionMode,
    RiskLevel,
    SideEffectState,
    TaskState,
    VerificationStatus,
)


def make_request(**overrides):
    now = utcnow_iso()
    criterion = SuccessCriterion(id="c1", description="build passes", verificationMethod="run tests")
    defaults = dict(
        objective="make the build pass",
        permissionMode=PermissionMode.AUTO,
        effortLevel=EffortLevel.STANDARD,
        resourceLimits=ResourceLimits(actionSteps=50, wallClockTime=3600),
        targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now),
        successCriteria=[criterion],
        completionContract=CompletionContract(
            objective="make the build pass", successCriteria=[criterion]),
    )
    defaults.update(overrides)
    return CreateTaskRequest(**defaults)


class TaskManagerTestBase(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = TaskStore(self.root)
        self.mgr = TaskManager(self.store)

    def create_and_advance(self, request=None):
        task = self.mgr.create_task(request or make_request())
        for state in (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                      TaskState.RUNNING, TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        return task


class TaskCreationTests(TaskManagerTestBase):

    def test_create_starts_in_created_state(self):
        task = self.mgr.create_task(make_request())
        self.assertIs(task.state, TaskState.CREATED)

    def test_ids_are_unique(self):
        first = self.mgr.create_task(make_request())
        second = self.mgr.create_task(make_request())
        self.assertNotEqual(first.id, second.id)

    def test_creation_is_durable(self):
        task = self.mgr.create_task(make_request())
        reloaded = TaskManager(TaskStore(self.root)).get_task(task.id)
        self.assertEqual(reloaded, task)

    def test_empty_objective_rejected(self):
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(objective="   "))

    def test_empty_resource_limits_rejected(self):
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(resourceLimits=ResourceLimits()))

    def test_invalid_completion_contract_rejected(self):
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(
                completionContract=CompletionContract(objective="o")))

    def test_criterion_without_method_rejected(self):
        bad = SuccessCriterion(id="c2", description="d", verificationMethod="")
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(successCriteria=[bad]))

    def test_no_mandatory_criterion_rejected(self):
        weak = SuccessCriterion(id="c2", description="d", verificationMethod="m",
                                mandatory=False)
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(successCriteria=[weak]))

    def test_expired_target_authorization_rejected(self):
        now = utcnow_iso()
        with self.assertRaises(ValidationError):
            self.mgr.create_task(make_request(targetAuthorizationContext=TargetAuthorizationContext(
                schemaVersion="1.0", createdAt=now, updatedAt=now,
                expiresAt="2000-01-01T00:00:00Z")))

    def test_missing_parent_rejected(self):
        with self.assertRaises(Exception):
            self.mgr.create_task(make_request(parentTaskId="no-such-parent"))

    def test_get_unknown_task_raises(self):
        with self.assertRaises(Exception):
            self.mgr.get_task("nope")


class TaskTransitionTests(TaskManagerTestBase):

    def test_happy_path_transitions_persisted(self):
        task = self.create_and_advance()
        self.assertIs(task.state, TaskState.VERIFYING)
        on_disk = self.mgr.get_task(task.id)
        self.assertIs(on_disk.state, TaskState.VERIFYING)

    def test_invalid_transition_rejected_and_state_unchanged(self):
        task = self.mgr.create_task(make_request())
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.RUNNING)
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.CREATED)

    def test_done_requires_completion_engine(self):
        task = self.mgr.create_task(make_request())
        for state in (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                      TaskState.RUNNING):
            task = self.mgr.transition(task.id, state)
        # generic path never reaches DONE
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.DONE)
        # completion-engine path requires VERIFYING
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.DONE, by_completion_engine=True)
        task = self.mgr.transition(task.id, TaskState.OBSERVING)
        task = self.mgr.transition(task.id, TaskState.VERIFYING)
        done = self.mgr.transition(task.id, TaskState.DONE, by_completion_engine=True)
        self.assertIs(done.state, TaskState.DONE)

    def test_gate_blocks_running_without_criteria(self):
        task = self.mgr.create_task(make_request(successCriteria=[]))
        self.mgr.transition(task.id, TaskState.VALIDATING)
        self.mgr.transition(task.id, TaskState.PLANNING)
        self.mgr.transition(task.id, TaskState.READY)
        with self.assertRaises(TaskGateBlockedError) as ctx:
            self.mgr.transition(task.id, TaskState.RUNNING)
        self.assertTrue(any("success criteria" in p for p in ctx.exception.problems))
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.READY)

    def test_resume_requires_explicit_authorization(self):
        task = self.mgr.create_task(make_request())
        self.mgr.transition(task.id, TaskState.WAITING_USER)
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.RECOVERING)
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.RECOVERING,
                                authorization={"kind": "MODEL", "actor": "m"})
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.RECOVERING,
                                authorization={"kind": "USER", "actor": " "})
        resumed = self.mgr.transition(task.id, TaskState.RECOVERING,
                                      authorization={"kind": "SYSTEM_RECOVERY", "actor": "recovery-1"})
        self.assertIs(resumed.state, TaskState.RECOVERING)
        self.assertTrue(any(d.decision == "resume_authorized" for d in resumed.decisions))

    def test_cancel_and_terminal_dead_end(self):
        task = self.mgr.create_task(make_request())
        cancelled = self.mgr.cancel(task.id, reason="user changed their mind")
        self.assertIs(cancelled.state, TaskState.CANCELLED)
        self.assertTrue(any(d.decision == "cancelled" for d in cancelled.decisions))
        with self.assertRaises(ValidationError):
            self.mgr.cancel(task.id)
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.VALIDATING)


class TaskUpdateTests(TaskManagerTestBase):

    def test_update_plan_and_records(self):
        task = self.mgr.create_task(make_request())
        plan = Plan(id="p1", steps=[PlanStep(id="s1", description="do it")])
        decision = Decision(decisionId="d1", taskId=task.id, timestamp=utcnow_iso(),
                            decision="chose approach A")
        failure = FailureRecord(failureId="f1", taskId=task.id, timestamp=utcnow_iso(),
                                category="TOOL_FAILURE",
                                sideEffectState=SideEffectState.KNOWN_FAILED)
        updated = self.mgr.update_task(task.id, UpdateTaskRequest(
            plan=plan,
            appendDecisions=[decision],
            appendFailures=[failure],
        ))
        self.assertEqual(updated.plan, plan)
        self.assertIn(decision, updated.decisions)
        self.assertIn(failure, updated.failures)
        self.assertGreaterEqual(updated.updatedAt, task.updatedAt)
        self.assertEqual(self.mgr.get_task(task.id).plan, plan)

    def test_update_verification_and_continuity(self):
        task = self.mgr.create_task(make_request())
        verification = VerificationState(overall=VerificationStatus.INCONCLUSIVE)
        continuity = ContinuityState(nextSafeAction="verify file exists")
        updated = self.mgr.update_task(task.id, UpdateTaskRequest(
            verification=verification, continuity=continuity))
        self.assertIs(updated.verification.overall, VerificationStatus.INCONCLUSIVE)
        self.assertEqual(updated.continuity.nextSafeAction, "verify file exists")

    def test_empty_update_rejected(self):
        task = self.mgr.create_task(make_request())
        with self.assertRaises(ValidationError):
            self.mgr.update_task(task.id, UpdateTaskRequest())

    def test_update_on_terminal_rejected(self):
        task = self.mgr.create_task(make_request())
        self.mgr.cancel(task.id)
        with self.assertRaises(ValidationError):
            self.mgr.update_task(task.id, UpdateTaskRequest(
                appendDecisions=[Decision(decisionId="d1", taskId=task.id,
                                          timestamp=utcnow_iso(), decision="x")]))

    def test_update_wrong_task_id_rejected(self):
        task = self.mgr.create_task(make_request())
        other = self.mgr.create_task(make_request())
        with self.assertRaises(ValidationError):
            self.mgr.update_task(task.id, UpdateTaskRequest(
                appendDecisions=[Decision(decisionId="d1", taskId=other.id,
                                          timestamp=utcnow_iso(), decision="x")]))

    def test_add_subtasks_requires_existence(self):
        parent = self.mgr.create_task(make_request())
        child = self.mgr.create_task(make_request())
        updated = self.mgr.update_task(parent.id, UpdateTaskRequest(addSubtaskIds=[child.id]))
        self.assertEqual(updated.subtaskIds, [child.id])
        # duplicate-only update is a no-op and rejected as no-change
        with self.assertRaises(ValidationError):
            self.mgr.update_task(parent.id, UpdateTaskRequest(addSubtaskIds=[child.id]))
        with self.assertRaises(Exception):
            self.mgr.update_task(parent.id, UpdateTaskRequest(addSubtaskIds=["ghost"]))


class TaskAuthorizationTests(TaskManagerTestBase):

    def _task_with_scopes(self):
        now = utcnow_iso()
        return self.mgr.create_task(make_request(targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=["/data/**", "/tmp/**"],
            allowedWritePaths=["/data/out/**"],
            deniedTargets=["/data/secret.txt"],
            createdAt=now, updatedAt=now)))

    def test_narrowing_succeeds_and_versions(self):
        task = self._task_with_scopes()
        now = utcnow_iso()
        narrowed = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=["/data/**"],
            allowedWritePaths=[],
            deniedTargets=["/data/secret.txt", "/data/private.txt"],
            createdAt=now, updatedAt=now)
        updated = self.mgr.update_target_authorization(task.id, narrowed)
        self.assertEqual(updated.targetAuthorizationContext.allowedReadPaths, ["/data/**"])
        self.assertNotEqual(updated.targetAuthorizationContext.authorizationReference,
                            task.targetAuthorizationContext.authorizationReference)
        self.assertTrue(any(d.decision == "target_authorization_narrowed" for d in updated.decisions))

    def test_expansion_rejected(self):
        task = self._task_with_scopes()
        now = utcnow_iso()
        expanded = TargetAuthorizationContext(
            schemaVersion="1.0", allowedReadPaths=["/etc/**"],
            createdAt=now, updatedAt=now)
        with self.assertRaises(ValidationError):
            self.mgr.update_target_authorization(task.id, expanded)

    def test_denied_target_removal_rejected(self):
        task = self._task_with_scopes()
        now = utcnow_iso()
        weakened = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now)
        with self.assertRaises(ValidationError):
            self.mgr.update_target_authorization(task.id, weakened)

    def test_expiry_extension_rejected(self):
        now = utcnow_iso()
        task = self.mgr.create_task(make_request(targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="2030-01-01T00:00:00Z")))
        longer = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="2040-01-01T00:00:00Z")
        with self.assertRaises(ValidationError):
            self.mgr.update_target_authorization(task.id, longer)
        shorter = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="2029-01-01T00:00:00Z")
        updated = self.mgr.update_target_authorization(task.id, shorter)
        self.assertEqual(updated.targetAuthorizationContext.expiresAt, "2029-01-01T00:00:00Z")


class TaskCheckpointAndRecoveryTests(TaskManagerTestBase):

    def test_checkpoint_content_and_durability(self):
        task = self.mgr.create_task(make_request())
        checkpoint = self.mgr.checkpoint(task.id)
        self.assertEqual(checkpoint.taskId, task.id)
        self.assertEqual(checkpoint.objective, task.objective)
        self.assertEqual(checkpoint.lifecycleState, task.state)
        self.assertEqual(self.store.list_checkpoints(task.id)[0].checkpointId,
                         checkpoint.checkpointId)
        reloaded = TaskManager(TaskStore(self.root)).store.list_checkpoints(task.id)
        self.assertEqual(len(reloaded), 1)

    def test_checkpoint_on_terminal_rejected(self):
        task = self.mgr.create_task(make_request())
        self.mgr.cancel(task.id)
        with self.assertRaises(ValidationError):
            self.mgr.checkpoint(task.id)

    def test_recover_ready_resumes_without_change(self):
        task = self.mgr.create_task(make_request())
        for state in (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY):
            self.mgr.transition(task.id, state)
        result = self.mgr.recover(task.id)
        self.assertEqual(result.outcome, "RESUME")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.READY)

    def test_recover_interruptible_moves_to_recovering(self):
        task = self.create_and_advance()  # ends in VERIFYING
        result = self.mgr.recover(task.id)
        self.assertEqual(result.outcome, "RESUME")
        self.assertIs(result.task.state, TaskState.RECOVERING)
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RECOVERING)

    def test_recover_waiting_user_requires_authorization(self):
        task = self.mgr.create_task(make_request())
        self.mgr.transition(task.id, TaskState.WAITING_USER)
        result = self.mgr.recover(task.id)
        self.assertEqual(result.outcome, "WAITING_USER")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.WAITING_USER)

    def test_recover_terminal_is_none_needed(self):
        task = self.mgr.create_task(make_request())
        self.mgr.cancel(task.id)
        result = self.mgr.recover(task.id)
        self.assertEqual(result.outcome, "NONE_NEEDED")

    def test_recover_missing_task_fails(self):
        result = self.mgr.recover("ghost")
        self.assertEqual(result.outcome, "FAILED")

    def test_recover_tampered_state_blocks(self):
        task = self.mgr.create_task(make_request())
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["payload"]["objective"] = "hijacked"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = self.mgr.recover(task.id)
        self.assertEqual(result.outcome, "BLOCKED")
        self.assertIsNone(result.task)


if __name__ == "__main__":
    unittest.main()
