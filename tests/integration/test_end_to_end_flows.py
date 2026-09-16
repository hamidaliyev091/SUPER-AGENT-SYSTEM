"""Integration flows across components: task -> plan -> actions -> verify ->
completion -> DONE, plus the repair and crash-recovery loops."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel, FakeRuntime

from core.enums import TaskState
from completion import CompletionEngine
from continuity import RecoveryManager


class EndToEndFlowTests(VerificationTestBase):

    def test_complete_flow_under_fake_runtime(self):
        """The same governed flow under two different fake runtimes yields
        identical outcomes: the Core has no runtime dependency."""
        results = []
        for runtime in (FakeRuntime("runtime-a"), FakeRuntime("runtime-b")):
            runtime.start()
            task = self.make_task()
            self.files["/data/out/x.txt"] = "HELLO"
            report = self.engine.verify(task.id)
            decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
            results.append((report.status.value, decision.decision.value,
                            self.mgr.get_task(task.id).state.value))
            runtime.stop()
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], ("PASS", "DONE", "DONE"))

    def test_repair_loop_flow(self):
        """FAIL -> REPAIR -> fix -> re-verify -> DONE."""
        task = self.make_task()
        self.files["/data/out/x.txt"] = "GOODBYE"
        report = self.engine.verify(task.id)
        self.assertEqual(report.status.value, "FAIL")
        completion = CompletionEngine(self.mgr, self.store)
        self.assertEqual(completion.evaluate(task.id).decision.value, "REPAIR")
        # repair: re-entry to RUNNING passes through RECOVERING -> READY so
        # the execution gate revalidates (ADR-009)
        task = self.mgr.transition(task.id, TaskState.REPAIRING)
        task = self.mgr.transition(task.id, TaskState.RECOVERING)
        task = self.mgr.transition(task.id, TaskState.READY)
        task = self.mgr.transition(task.id, TaskState.RUNNING)
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        for state in (TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        report = self.engine.verify(task.id)
        self.assertEqual(report.status.value, "PASS")
        decision = completion.evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.DONE)

    def test_crash_recovery_loop_flow(self):
        """Interrupted mutation -> WAITING_USER -> human resume -> verify ->
        DONE."""
        task = self.make_task(stop_at=TaskState.RUNNING)
        journal = self.store.journal_for(task.id)
        journal.append("POLICY_DECISION", {
            "taskId": task.id, "operationId": "fs.write_file",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "decision": "ALLOW", "reason": "pre-crash audit"})
        journal.append("ACTION_STARTED", {
            "actionId": "crash-1", "taskId": task.id,
            "operationId": "fs.write_file", "targetType": "FILESYSTEM",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})
        recovery = RecoveryManager(self.mgr, self.store)
        result = recovery.recover(task.id)
        self.assertEqual(result.outcome, "WAITING_USER")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.WAITING_USER)
        # human authorizes resume (TASK_SCHEMA s22)
        task = self.mgr.transition(task.id, TaskState.RECOVERING, authorization={
            "kind": "USER", "actor": "human-1"})
        task = self.mgr.transition(task.id, TaskState.READY)
        task = self.mgr.transition(task.id, TaskState.RUNNING)
        self.files["/data/out/x.txt"] = "HELLO"
        for state in (TaskState.OBSERVING, TaskState.VERIFYING):
            task = self.mgr.transition(task.id, state)
        self.assertEqual(self.engine.verify(task.id).status.value, "PASS")
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")

    def test_model_driven_action_flow(self):
        """FakeModel proposals flow through the pipeline unchanged in
        authority: policy decides, not the model."""
        task = self.make_task(stop_at=TaskState.RUNNING)
        proposal = self.make_request(task)  # in-scope write
        model = FakeModel(proposals=[proposal])
        request = model.propose_action(task)
        result = self.pipeline.execute(request)
        self.assertTrue(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "ALLOW")
        self.assertEqual(model.proposals_made, 1)


if __name__ == "__main__":
    unittest.main()
