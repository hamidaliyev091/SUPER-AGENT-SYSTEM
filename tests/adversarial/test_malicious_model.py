"""Adversarial model tests: a malicious acting model attacks every
authority boundary; the Core must deny or neutralize every attempt
(VERIFICATION.md s26, ROADMAP Phase 9 requirements)."""
import unittest

from tests.support.harness import VerificationTestBase, criterion
from tests.support.fakes import MaliciousModel

from core import ResourceLimits
from core.enums import TaskState
from completion import CompletionEngine
from verification import VerificationResultStore


class MaliciousModelTests(VerificationTestBase):

    def test_malicious_scope_expansion_never_executes(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        for attempt in (MaliciousModel.attempt_out_of_scope_write(task),
                        MaliciousModel.attempt_protected_write(task),
                        MaliciousModel.attempt_unknown_tool(task),
                        MaliciousModel.attempt_dangerous_mode(task)):
            result = self.pipeline.execute(attempt)
            self.assertFalse(result.executed, attempt.reason)
            self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.writes), 0)

    def test_malicious_completion_claim_cannot_done(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(stop_at=TaskState.RUNNING)
        model = MaliciousModel()
        claim = model.claim_completion(task)
        self.assertEqual(claim["decision"], "DONE")
        # claims are data: nothing in the Core consumes them
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)
        with self.assertRaises(Exception):
            self.mgr.transition(task.id, TaskState.DONE)
        completion = CompletionEngine(self.mgr, self.store)
        decision = completion.evaluate(task.id)  # authoritative path
        self.assertNotEqual(decision.decision.value, "DONE")

    def test_malicious_cannot_weaken_criteria(self):
        import dataclasses
        from task import UpdateTaskRequest
        task = self.make_task(stop_at=TaskState.RUNNING)
        # no update surface exists for criteria or the completion contract
        fields = {f.name for f in dataclasses.fields(UpdateTaskRequest)}
        self.assertNotIn("successCriteria", fields)
        self.assertNotIn("completionContract", fields)
        reloaded = self.mgr.get_task(task.id)
        self.assertEqual(len(reloaded.successCriteria), 1)
        self.assertTrue(reloaded.successCriteria[0].mandatory)

    def test_malicious_cannot_inflate_limits(self):
        task = self.make_task(stop_at=TaskState.RUNNING,
                              resourceLimits=ResourceLimits(actionSteps=1))
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        # the budget is exhausted; a second action is denied regardless of
        # what the model asks for
        second = self.acting_write(task, "MORE")
        self.assertFalse(second.executed)
        reloaded = self.mgr.get_task(task.id)
        self.assertEqual(reloaded.resourceLimits.actionSteps, 1)

    def test_malicious_fabricated_evidence_insufficient(self):
        """VERIFICATION.md Test 06 shape: acting-model-supplied evidence is
        Level 0 and cannot authorize DONE when independence is required."""
        task = self.make_task(criteria=[criterion(method="self-report")])
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        report = self.engine.verify(task.id)
        self.assertEqual(report.status.value, "INCONCLUSIVE")
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertNotEqual(decision.decision.value, "DONE")

    def test_malicious_cannot_forge_stored_results(self):
        import json
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        path = self.root / task.id / "verification" / "c1.json"
        data = json.loads(path.read_text())
        data["payload"]["result"] = "PASS"  # already PASS; tamper envelope anyway
        data["payload"]["notes"] = "forged"
        path.write_text(json.dumps(data))
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "BLOCKED")
        self.assertIsNot(self.mgr.get_task(task.id).state, TaskState.DONE)

    def test_malicious_approval_reuse_rejected(self):
        from core import ActionRequest, ActorIdentity, Target
        from core.enums import ActorType, ApprovalDecision, TargetType
        task = self.make_task(stop_at=TaskState.RUNNING)
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="package.install",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            arguments={"package": "com.example.app"}, reason="install")
        self.approver.enqueue(ApprovalDecision.APPROVE)
        first = self.pipeline.execute(request)
        self.assertTrue(first.executed)
        # references are single-use and durably consumed
        consumed = self.store.journal_for(task.id).consumed_approval_references()
        self.assertIn(first.approvalResult.approvalReference, consumed)
        self.approver.enqueue(ApprovalDecision.APPROVE)
        second = self.pipeline.execute(request)
        self.assertTrue(second.executed)
        self.assertNotEqual(first.approvalResult.approvalReference,
                            second.approvalResult.approvalReference)


if __name__ == "__main__":
    unittest.main()
