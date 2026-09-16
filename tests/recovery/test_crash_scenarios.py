"""Crash and interruption scenarios over the full stack (Phase 8 tests/recovery)."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel

from core.enums import TaskState
from completion import CompletionEngine
from continuity import ContinuityManager, RecoveryManager


class CrashScenarioTests(VerificationTestBase):

    def _interrupt(self, task, operation_id):
        """Simulate a crash between ACTION_STARTED and ACTION_TERMINAL. The
        policy audit precedes STARTED in the real pipeline, so it must
        precede it here too."""
        journal = self.store.journal_for(task.id)
        journal.append("POLICY_DECISION", {
            "taskId": task.id, "operationId": operation_id,
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "decision": "ALLOW", "reason": "pre-crash audit"})
        journal.append("ACTION_STARTED", {
            "actionId": f"crash-{operation_id}", "taskId": task.id,
            "operationId": operation_id, "targetType": "FILESYSTEM",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})

    def test_crash_after_safe_action_retries_and_completes(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self._interrupt(task, "fs.read_file")  # READ_ONLY: safe to retry
        result = RecoveryManager(self.mgr, self.store).recover(task.id)
        self.assertEqual(result.outcome, "RETRY")
        # the orchestrator re-executes the interrupted read through policy
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.mgr.get_task(task.id)
        for state in (TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        self.assertEqual(self.engine.verify(task.id).status.value, "PASS")
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")

    def test_crash_with_unknown_effect_waits_then_completes_after_resume(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self._interrupt(task, "fs.write_file")  # MUTATING, UNKNOWN idempotency
        result = RecoveryManager(self.mgr, self.store).recover(task.id)
        self.assertEqual(result.outcome, "WAITING_USER")
        task = self.mgr.transition(task.id, TaskState.RECOVERING, authorization={
            "kind": "USER", "actor": "human-1"})
        task = self.mgr.transition(task.id, TaskState.READY)
        task = self.mgr.transition(task.id, TaskState.RUNNING)
        self.files["/data/out/x.txt"] = "HELLO"
        for state in (TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        self.assertEqual(self.engine.verify(task.id).status.value, "PASS")
        self.assertEqual(CompletionEngine(self.mgr, self.store)
                         .evaluate(task.id).decision.value, "DONE")

    def test_crash_before_any_action_resumes_directly(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        result = RecoveryManager(self.mgr, self.store).recover(task.id)
        self.assertEqual(result.outcome, "RESUME")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)

    def test_model_context_loss_does_not_lose_authority(self):
        """Context compaction: a replacement model continues from the
        durable brief, with unchanged authority (s22)."""
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        continuity = ContinuityManager(self.mgr, self.store)
        brief = continuity.prepare_for_compaction(task.id)  # s16.1
        # model context is lost; the replacement model sees only the brief
        replacement = FakeModel()
        self.assertIsNone(replacement.propose_action(task))  # no scripted authority
        self.assertEqual(brief.lifecycleState, "RUNNING")
        self.assertEqual(brief.authorizationStatus, "active")
        # durable state remains authoritative and completion still works
        task = self.mgr.get_task(task.id)
        for state in (TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        self.assertEqual(self.engine.verify(task.id).status.value, "PASS")
        self.assertEqual(CompletionEngine(self.mgr, self.store)
                         .evaluate(task.id).decision.value, "DONE")

    def test_recovered_task_inherits_remaining_budget(self):
        from core import ResourceLimits
        task = self.make_task(stop_at=TaskState.RUNNING,
                              resourceLimits=ResourceLimits(actionSteps=3))
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        task = self.mgr.get_task(task.id)
        for state in (TaskState.OBSERVING,):
            task = self.mgr.transition(task.id, state)
        result = RecoveryManager(self.mgr, self.store).recover(task.id)
        self.assertEqual(result.outcome, "RESUME")
        task = self.mgr.get_task(task.id)
        self.assertIs(task.state, TaskState.RUNNING)
        continuity = ContinuityManager(self.mgr, self.store)
        self.assertEqual(continuity.remaining_budget(task)["actionStepsRemaining"], 2)


if __name__ == "__main__":
    unittest.main()
