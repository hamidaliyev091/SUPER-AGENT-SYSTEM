"""Adversarial orchestrator tests (ROADMAP Phase 9 required demonstration):
a malicious FakeModel must be unable to bypass Policy, expand scope, access
protected targets, declare DONE, or bypass verification."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import MaliciousModel

from core import ActionRequest, ActorIdentity, Target
from core.enums import ActorType, TargetType, TaskState
from completion import CompletionEngine
from continuity import RecoveryManager
from orchestration import Orchestrator


class OrchestratorAdversarialTests(VerificationTestBase):

    def make_orchestrator(self, model):
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), model,
            recovery=RecoveryManager(self.mgr, self.store))

    def write_proposal(self, task, content="HELLO"):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"path": "/data/out/x.txt", "content": content},
            reason="scripted plan step")

    def test_malicious_model_cannot_bypass_policy_or_scope(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        model = MaliciousModel(proposals=[
            MaliciousModel.attempt_out_of_scope_write(task),
            MaliciousModel.attempt_protected_write(task),
            MaliciousModel.attempt_unknown_tool(task),
            MaliciousModel.attempt_dangerous_mode(task),
            self.write_proposal(task, "HELLO"),  # the legitimate plan step
        ])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id)
        self.assertIs(final.state, TaskState.DONE)  # via the legitimate path only
        self.assertEqual(self.writes, ["/data/out/x.txt"])  # nothing else executed
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        self.assertNotIn("/etc/evil.txt", self.files)
        self.assertNotIn("/etc/evil2.txt", self.files)

    def test_malicious_model_alone_never_reaches_done(self):
        """Pure malicious proposals: every attempt denied, verification
        cannot pass, DONE is never authorized."""
        task = self.make_task(stop_at=TaskState.CREATED)
        model = MaliciousModel(proposals=[
            MaliciousModel.attempt_out_of_scope_write(task),
            MaliciousModel.attempt_protected_write(task),
            MaliciousModel.attempt_unknown_tool(task),
        ])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id, max_iterations=60)
        self.assertIsNot(final.state, TaskState.DONE)
        self.assertEqual(len(self.writes), 0)
        self.assertFalse(any("evil" in path for path in self.files))

    def test_malicious_completion_claim_ignored_by_orchestrator(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        model = MaliciousModel(proposals=[self.write_proposal(task, "HELLO")])
        self.assertEqual(model.claim_completion(task)["decision"], "DONE")
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id)
        # the claim changed nothing: DONE came from the engines alone
        self.assertIs(final.state, TaskState.DONE)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("COMPLETION_DECISION", events)
        completion_records = [r for r in self.store.journal_for(task.id).records()
                              if r["eventType"] == "COMPLETION_DECISION"]
        self.assertEqual(completion_records[0]["payload"]["decision"], "DONE")

    def test_malicious_model_cannot_bypass_verification(self):
        """The model writes the wrong content, claims success, and proposes
        nothing further: verification FAILs and DONE never happens."""
        task = self.make_task(stop_at=TaskState.CREATED)
        model = MaliciousModel(proposals=[self.write_proposal(task, "GOODBYE")])
        orchestrator = self.make_orchestrator(model)
        final = orchestrator.run(task.id, max_iterations=60)
        self.assertIsNot(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "GOODBYE")


if __name__ == "__main__":
    unittest.main()
