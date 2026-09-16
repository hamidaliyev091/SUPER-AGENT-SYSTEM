"""Core security invariants (ROADMAP Phase 8 required list).

Each test corresponds to one required adversarial scenario; the expected
behavior is always fail-closed (DENY / no execution / no DONE / BLOCKED).
"""
import json
import unittest

from tests.support.harness import VerificationTestBase, criterion
from tests.support.fakes import MaliciousModel

from core import (
    ActionRequest,
    ActorIdentity,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ResourceLimits,
    Target,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    Idempotency,
    Reversibility,
    RiskLevel,
    SideEffect,
    TargetType,
    TaskState,
)


class SecurityInvariantsTests(VerificationTestBase):

    def test_unauthorized_target_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/etc/passwd"),
            arguments={"path": "/etc/passwd", "content": "x"},
            reason="unauthorized target")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.writes), 0)

    def test_protected_target_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        result = self.pipeline.execute(
            MaliciousModel.attempt_protected_write(task))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.writes), 0)

    def test_policy_bypass_attempt_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        result = self.pipeline.execute(
            MaliciousModel.attempt_dangerous_mode(task))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.writes), 0)

    def test_malformed_policy_request_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        garbage = self.pipeline.execute(object())
        self.assertFalse(garbage.executed)
        self.assertIsNotNone(garbage.policyDecision)
        no_args = self.pipeline.execute(ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="x", actorType=ActorType.TOP_LEVEL_AGENT),
            toolId="fs.write_file", target=None, arguments="not-a-dict",
            reason="malformed"))
        self.assertFalse(no_args.executed)

    def test_invalid_arguments_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        result = self.pipeline.execute(ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="x", actorType=ActorType.TOP_LEVEL_AGENT),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"nonsense": True},  # missing required "path" argument
            reason="missing args"))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_scope_expansion_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        result = self.pipeline.execute(
            MaliciousModel.attempt_out_of_scope_write(task))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_subagent_privilege_escalation_denied(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="sub-1", actorType=ActorType.SUBAGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=None,
            arguments={"path": "/root/.ssh/authorized_keys", "content": "key"},
            reason="subagent escalation attempt")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_false_completion_claim_has_no_effect(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        claim = MaliciousModel().claim_completion(task)
        self.assertEqual(claim["decision"], "DONE")  # the model claims...
        with self.assertRaises(Exception):
            self.mgr.transition(task.id, TaskState.DONE)
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.RUNNING)

    def test_verification_failure_blocks_done(self):
        from completion import CompletionEngine
        self.files["/data/out/x.txt"] = "GOODBYE"
        task = self.make_task()
        self.engine.verify(task.id)
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "REPAIR")
        self.assertIsNot(self.mgr.get_task(task.id).state, TaskState.DONE)

    def test_inconclusive_blocks_done(self):
        from completion import CompletionEngine
        task = self.make_task(criteria=[criterion(method="ghost")])
        self.engine.verify(task.id)
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "CONTINUE")
        self.assertIsNot(self.mgr.get_task(task.id).state, TaskState.DONE)

    def test_corrupted_durable_state_rejected(self):
        from continuity.task_store import TaskIntegrityError
        task = self.make_task(stop_at=TaskState.RUNNING)
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text())
        data["payload"]["objective"] = "corrupted"
        path.write_text(json.dumps(data))
        with self.assertRaises(TaskIntegrityError):
            self.mgr.get_task(task.id)

    def test_unknown_side_effect_never_blind_retried(self):
        from continuity import RecoveryManager
        task = self.make_task(stop_at=TaskState.RUNNING)
        journal = self.store.journal_for(task.id)
        journal.append("ACTION_STARTED", {
            "actionId": "interrupted", "taskId": task.id,
            "operationId": "fs.write_file", "targetType": "FILESYSTEM",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})
        result = RecoveryManager(self.mgr, self.store).recover(task.id)
        self.assertEqual(result.outcome, "WAITING_USER")

    def test_resource_exhaustion_blocks(self):
        task = self.make_task(stop_at=TaskState.RUNNING,
                              resourceLimits=ResourceLimits(actionSteps=1))
        first = self.acting_write(task, "HELLO")
        self.assertTrue(first.executed)
        second = self.pipeline.execute(self.make_request(task))
        self.assertFalse(second.executed)
        self.assertIn("budget", second.policyDecision.reason)

    def test_action_journal_failure_blocks_execution(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        (self.root / task.id / "journal.jsonl").mkdir(exist_ok=True)
        result = self.pipeline.execute(self.make_request(task))
        self.assertFalse(result.executed)
        self.assertEqual(len(self.writes), 0)

    def test_expired_authorization_denied(self):
        from continuity.task_store import _digest
        task = self.make_task(stop_at=TaskState.RUNNING)
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text())
        data["payload"]["targetAuthorizationContext"]["expiresAt"] = \
            "2000-01-01T00:00:00+00:00"
        data["integrity"]["digest"] = _digest(data["payload"])
        path.write_text(json.dumps(data))
        result = self.pipeline.execute(self.make_request(task))
        self.assertFalse(result.executed)

    def test_expired_human_approval_rejected(self):
        request = ApprovalRequest(
            taskId="t", operation="package.install",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            riskLevel=RiskLevel.MEDIUM, sideEffect=SideEffect.MUTATING,
            reversibility=Reversibility.REVERSIBLE, idempotency=Idempotency.UNKNOWN,
            explanation="install", consequences="appears",
            scope={}, expiresAt="2000-01-01T00:00:00+00:00",
            policyVersion=self.policy.policy_version,
            ruleVersion=self.policy.rule_version,
            approvalReference="ref-1", nonce="n1",
        )
        result = ApprovalResult(
            approvalReference="ref-1",
            decision=ApprovalDecision.APPROVE,
            approverIdentity=ActorIdentity(actorId="human-1",
                                           actorType=ActorType.USER),
            timestamp=utcnow_iso(),
        )
        problem = self.policy.validate_approval(request, result)
        self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main()
