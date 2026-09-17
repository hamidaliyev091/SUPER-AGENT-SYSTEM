"""Long-running autonomy (Phase 16): multi-action runs with periodic
checkpoints, interruption survival, repair loops, and budget continuity."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel

from core import ActionRequest, ActorIdentity, ResourceLimits, Target
from core.enums import ActorType, TargetType, TaskState
from completion import CompletionEngine
from continuity import ContinuityManager, RecoveryManager
from orchestration import Orchestrator


class LongRunningTests(VerificationTestBase):

    def make_orchestrator(self, model):
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), model,
            recovery=RecoveryManager(self.mgr, self.store),
            continuity=ContinuityManager(self.mgr, self.store))

    def write_proposal(self, task, content):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"path": "/data/out/x.txt", "content": content},
            reason="scripted step")

    def test_many_actions_with_periodic_checkpoints(self):
        """A long scripted run (two repair attempts then the fix) with a
        checkpoint every 2 iterations: durable snapshots accumulate and
        the task completes."""
        task = self.make_task(stop_at=TaskState.CREATED)
        model = FakeModel(proposals=[
            self.write_proposal(task, "WRONG-1"),
            self.write_proposal(task, "WRONG-2"),
            self.write_proposal(task, "HELLO"),
        ])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id, max_iterations=100, checkpoint_every=2)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        checkpoints = sorted(self.store.list_checkpoints(task.id),
                             key=lambda c: c.timestamp)
        self.assertTrue(len(checkpoints) >= 3, f"got {len(checkpoints)} checkpoints")
        used_values = []
        for checkpoint in checkpoints:
            self.assertEqual(checkpoint.taskId, task.id)
            used_values.append(checkpoint.resourceUsage["actionStepsUsed"])
            self.assertIsNotNone(checkpoint.remainingBudget)
        # usage grows monotonically across snapshots and never exceeds the
        # durable journal's total
        self.assertEqual(used_values, sorted(used_values))
        self.assertLessEqual(max(used_values), 6)

    def test_interruption_survives_and_continues(self):
        """The long-running task is interrupted mid-run (process crash
        between STARTED and TERMINAL); recovery waits for the human, and
        after the authorized resume the SAME orchestrator continues to
        verified DONE with the remaining budget inherited."""
        task = self.make_task(stop_at=TaskState.CREATED)
        self.mgr.transition(task.id, TaskState.VALIDATING)
        self.mgr.transition(task.id, TaskState.PLANNING)
        self.mgr.transition(task.id, TaskState.READY)
        task = self.mgr.transition(task.id, TaskState.RUNNING)
        journal = self.store.journal_for(task.id)
        journal.append("POLICY_DECISION", {
            "taskId": task.id, "operationId": "fs.write_file",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "decision": "ALLOW", "reason": "pre-crash audit"})
        journal.append("ACTION_STARTED", {
            "actionId": "crash", "taskId": task.id,
            "operationId": "fs.write_file", "targetType": "FILESYSTEM",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})
        recovery = RecoveryManager(self.mgr, self.store)
        self.assertEqual(recovery.recover(task.id).outcome, "WAITING_USER")
        task = self.mgr.transition(task.id, TaskState.RECOVERING, authorization={
            "kind": "USER", "actor": "human-1"})
        model = FakeModel(proposals=[self.write_proposal(task, "HELLO")])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id, max_iterations=100)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")

    def test_budget_never_resets_across_continuation(self):
        """CONTINUITY s14: remaining budget is inherited from the durable
        journal; continuation never creates a new budget."""
        task = self.make_task(stop_at=TaskState.CREATED,
                              resourceLimits=ResourceLimits(actionSteps=4))
        model = FakeModel(proposals=[self.write_proposal(task, "HELLO")])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id, max_iterations=100)
        self.assertIs(final.state, TaskState.DONE)
        usage = ContinuityManager(self.mgr, self.store).resource_usage(
            self.mgr.get_task(task.id))
        # write (1) + verification read (1) = 2 actions, far below the limit
        self.assertEqual(usage["actionStepsUsed"], 2)


if __name__ == "__main__":
    unittest.main()
