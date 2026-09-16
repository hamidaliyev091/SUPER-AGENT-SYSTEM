"""Continuity Manager + Recovery Manager tests (Phase 7)."""
import json
import unittest

from continuity import ContinuityManager, RecoveryManager

from core import ResourceLimits
from core.enums import TaskState

try:
    from test_verification_engine import VerificationTestBase, criterion, make_tac
except ImportError:
    from tests.unit.test_verification_engine import VerificationTestBase, criterion, make_tac


class ContinuityTestBase(VerificationTestBase):

    def setUp(self):
        super().setUp()
        self.continuity = ContinuityManager(self.mgr, self.store)
        self.recovery = RecoveryManager(self.mgr, self.store)

    def interrupt(self, task, operation_id, target="/data/out/x.txt"):
        """Simulate a crash between ACTION_STARTED and ACTION_TERMINAL.
        The policy audit precedes STARTED in the real pipeline."""
        journal = self.store.journal_for(task.id)
        journal.append("POLICY_DECISION", {
            "taskId": task.id,
            "operationId": operation_id,
            "target": target,
            "argumentsSha256": "x",
            "decision": "ALLOW",
            "reason": "pre-crash audit",
        })
        journal.append("ACTION_STARTED", {
            "actionId": f"interrupted-{operation_id}",
            "taskId": task.id,
            "operationId": operation_id,
            "targetType": "FILESYSTEM",
            "target": target,
            "argumentsSha256": "x",
            "actorId": "agent-1",
            "actorType": "TOP_LEVEL_AGENT",
        })


class ContinuityManagerTests(ContinuityTestBase):

    def test_checkpoint_carries_journal_derived_resource_usage(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        checkpoint = self.continuity.checkpoint(task.id)
        self.assertEqual(checkpoint.resourceUsage["actionStepsUsed"], 1)
        self.assertEqual(checkpoint.remainingBudget.actionSteps, 99)
        self.assertIn("fs.write_file", checkpoint.completedSteps)
        self.assertTrue(checkpoint.continuitySummary)
        self.assertIn(task.objective, checkpoint.continuitySummary)

    def test_continuity_brief_derived_from_durable_state(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        self.interrupt(task, "fs.read_file")
        brief = self.continuity.continuity_brief(task.id)
        self.assertEqual(brief.taskId, task.id)
        self.assertEqual(brief.lifecycleState, "RUNNING")
        self.assertTrue(brief.completedWork)
        self.assertTrue(brief.doNotRepeatBlindly)  # interrupted read listed
        self.assertEqual(brief.authorizationStatus, "active")
        self.assertIn("actions 2", brief.resourceStatus)
        self.assertIn(task.objective, brief.text())

    def test_prepare_for_compaction_persists_checkpoint_and_brief(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        brief = self.continuity.prepare_for_compaction(task.id)
        self.assertEqual(brief.taskId, task.id)
        self.assertEqual(len(self.store.list_checkpoints(task.id)), 1)

    def test_resource_accounting_survives_repeated_queries(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.assertEqual(self.continuity.resource_usage(task)["actionStepsUsed"], 0)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        usage = self.continuity.resource_usage(task)
        self.assertEqual(usage["actionStepsUsed"], 1)
        self.assertEqual(self.continuity.remaining_budget(task)["actionStepsRemaining"], 99)


class RecoveryManagerTests(ContinuityTestBase):

    def test_clean_resume_when_no_interruption(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "RESUME")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("RECOVERY_DECISION", events)

    def test_interrupted_read_only_action_is_safe_to_retry(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.read_file")
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "RETRY")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)

    def test_interrupted_mutating_action_never_blind_retried(self):
        """CONTINUITY.md s9/s12: a MUTATING operation with UNKNOWN idempotency
        must not be replayed blindly - the task waits for a human."""
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.write_file")
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "WAITING_USER")
        task = self.mgr.get_task(task.id)
        self.assertIs(task.state, TaskState.WAITING_USER)
        self.assertTrue(task.continuity.unknownSideEffects)
        self.assertTrue(any("fs.write_file" in entry
                            for entry in task.continuity.unknownSideEffects))
        self.assertIn("human", task.continuity.nextSafeAction)

    def test_probe_confirms_success_resumes(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.write_file")

        def probe(t, payload):
            return "VERIFIED_SUCCESS"
        result = self.recovery.recover(task.id, external_probe=probe)
        self.assertEqual(result.outcome, "RESUME")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)

    def test_probe_confirms_failure_retries(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.write_file")

        def probe(t, payload):
            return "VERIFIED_FAILURE"
        result = self.recovery.recover(task.id, external_probe=probe)
        self.assertEqual(result.outcome, "RETRY")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)

    def test_inconclusive_probe_requires_verification_first(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.write_file")

        def probe(t, payload):
            return "UNKNOWN"
        result = self.recovery.recover(task.id, external_probe=probe)
        self.assertEqual(result.outcome, "VERIFY_FIRST")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RECOVERING)

    def test_exhausted_budget_blocks_retry(self):
        task = self.make_task(stop_at=TaskState.RUNNING,
                              resourceLimits=ResourceLimits(actionSteps=1))
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        self.interrupt(task, "fs.read_file")  # retry would exceed the budget
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "BLOCKED")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.BLOCKED)

    def test_expired_authorization_blocks(self):
        from continuity.task_store import _digest
        task = self.make_task(stop_at=TaskState.RUNNING)
        # simulate a TAC that expired while the task was running (re-sealed
        # envelope; update_target_authorization correctly refuses to narrow
        # authorization onto an already-expired TAC)
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text())
        data["payload"]["targetAuthorizationContext"]["expiresAt"] = \
            "2000-01-01T00:00:00+00:00"
        data["integrity"]["digest"] = _digest(data["payload"])
        path.write_text(json.dumps(data))
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "BLOCKED")

    def test_tampered_journal_blocks_recovery(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.acting_write(task, "HELLO")
        path = self.root / task.id / "journal.jsonl"
        lines = path.read_text().splitlines()
        data = json.loads(lines[0])
        data["payload"]["reason"] = "tampered"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "BLOCKED")

    def test_corrupted_task_state_blocks(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text())
        data["payload"]["objective"] = "corrupted"
        path.write_text(json.dumps(data))
        result = self.recovery.recover(task.id)
        self.assertEqual(result.outcome, "BLOCKED")

    def test_recovery_decision_is_journaled(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        self.interrupt(task, "fs.write_file")
        self.recovery.recover(task.id)
        records = [r for r in self.store.journal_for(task.id).records()
                   if r["eventType"] == "RECOVERY_DECISION"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["payload"]["decision"], "WAITING_USER")


if __name__ == "__main__":
    unittest.main()
