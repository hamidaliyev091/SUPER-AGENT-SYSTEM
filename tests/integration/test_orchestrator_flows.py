"""Orchestrator end-to-end flows (Phase 9): creation to verified DONE."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel

from core import ActionRequest, ActorIdentity, Target
from core.enums import ActorType, TargetType, TaskState
from completion import CompletionEngine
from continuity import RecoveryManager
from orchestration import Orchestrator


class OrchestratorFlowTests(VerificationTestBase):

    def make_orchestrator(self, model, recovery=None):
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), model,
            recovery=recovery or RecoveryManager(self.mgr, self.store))

    def write_proposal(self, task, content):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"path": "/data/out/x.txt", "content": content},
            reason="scripted plan step")

    def test_happy_path_creation_to_done(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        model = FakeModel(proposals=[self.write_proposal(task, "HELLO")])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        self.assertEqual(model.proposals_made, 1)
        self.assertEqual(len(model.observations), 1)

    def test_repair_loop_via_orchestrator(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        model = FakeModel(proposals=[self.write_proposal(task, "GOODBYE"),
                                     self.write_proposal(task, "HELLO")])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("VERIFICATION_RESULT", events)
        results = [r for r in self.store.journal_for(task.id).records()
                   if r["eventType"] == "VERIFICATION_RESULT"]
        self.assertEqual(len(results), 2)  # FAIL then PASS

    def test_malformed_proposals_are_ignored(self):
        from tests.support.fakes import BuggyModel
        task = self.make_task(stop_at=TaskState.CREATED)
        orchestrator = self.make_orchestrator(BuggyModel(mode="garbage"))
        final = orchestrator.run(task.id, max_iterations=30)
        self.assertIsNot(final.state, TaskState.DONE)
        self.assertEqual(len(self.writes), 0)

    def test_uncooperative_model_never_done(self):
        from tests.support.fakes import UncooperativeModel
        task = self.make_task(stop_at=TaskState.CREATED)
        orchestrator = self.make_orchestrator(UncooperativeModel())
        final = orchestrator.run(task.id, max_iterations=30)
        self.assertIsNot(final.state, TaskState.DONE)

    def test_crash_during_run_recovers_and_completes(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        self.mgr.transition(task.id, TaskState.VALIDATING)
        self.mgr.transition(task.id, TaskState.PLANNING)
        self.mgr.transition(task.id, TaskState.READY)
        task = self.mgr.transition(task.id, TaskState.RUNNING)
        # crash: a mutating action STARTED without a terminal record
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
        # human authorizes resume; the orchestrator drives the rest
        task = self.mgr.transition(task.id, TaskState.RECOVERING, authorization={
            "kind": "USER", "actor": "human-1"})
        model = FakeModel(proposals=[self.write_proposal(task, "HELLO")])
        orchestrator = self.make_orchestrator(model, recovery=recovery)
        final = orchestrator.run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")


if __name__ == "__main__":
    unittest.main()
