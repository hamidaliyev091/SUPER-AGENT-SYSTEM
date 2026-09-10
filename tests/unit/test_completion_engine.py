"""CompletionEngine tests (Phase 6): the only path to authoritative DONE."""
import json
import unittest

from completion import CompletionDecisionStore, CompletionEngine
from verification import VerificationResultStore
from continuity.task_store import TaskIntegrityError, _digest

from core import (
    Evidence,
    FailureRecord,
    ResourceLimits,
    ValidationError,
    VerificationResult,
    utcnow_iso,
)
from core.enums import (
    CompletionDecisionValue,
    IndependenceLevel,
    SideEffectState,
    TaskState,
    VerificationStatus,
)
from task import UpdateTaskRequest

try:
    from test_verification_engine import VerificationTestBase, criterion
except ImportError:
    from tests.unit.test_verification_engine import VerificationTestBase, criterion


class CompletionTestBase(VerificationTestBase):

    def setUp(self):
        super().setUp()
        self.completion = CompletionEngine(self.mgr, self.store)


class DoneAuthorizationTests(CompletionTestBase):

    def test_all_criteria_pass_authorizes_done(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.DONE)
        self.assertEqual(decision.reasonCode, "ALL_CRITERIA_PASSED")
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.DONE)
        self.assertEqual(decision.criterionResults["c1"], "PASS")
        self.assertIs(decision.policyCompliance["compliant"], True)
        self.assertIs(decision.resourceCompliance["compliant"], True)
        self.assertIsNotNone(decision.integrityMetadata)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("COMPLETION_DECISION", events)
        stored = CompletionDecisionStore(self.root).load(task.id)
        self.assertIs(stored.decision, CompletionDecisionValue.DONE)

    def test_model_cannot_declare_done(self):
        task = self.make_task()
        with self.assertRaises(ValidationError):
            self.mgr.transition(task.id, TaskState.DONE)  # no engine flag
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.VERIFYING)

    def test_evaluate_before_verification_continues(self):
        task = self.make_task()
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.CONTINUE)
        self.assertIs(self.mgr.get_task(task.id).state, TaskState.VERIFYING)

    def test_evaluate_requires_verifying_state(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.CONTINUE)

    def test_evaluate_after_done_is_safe(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        self.completion.evaluate(task.id)
        second = self.completion.evaluate(task.id)
        self.assertIs(second.decision, CompletionDecisionValue.CONTINUE)

    def test_non_mandatory_criterion_does_not_block_done(self):
        self.files["/data/out/x.txt"] = "HELLO"
        criteria = [criterion("c1"), criterion("c2", method="ghost", mandatory=False)]
        task = self.make_task(criteria=criteria)
        self.engine.verify(task.id)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.DONE)
        self.assertEqual(decision.criterionResults["c2"], "INCONCLUSIVE")


class BlockingAndRepairTests(CompletionTestBase):

    def test_inconclusive_never_produces_done(self):
        task = self.make_task(criteria=[criterion(method="ghost")])
        self.engine.verify(task.id)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.CONTINUE)
        self.assertEqual(decision.reasonCode, "VERIFICATION_INCONCLUSIVE")
        self.assertIsNot(self.mgr.get_task(task.id).state, TaskState.DONE)

    def test_mandatory_fail_repairs(self):
        self.files["/data/out/x.txt"] = "GOODBYE"
        task = self.make_task()
        self.engine.verify(task.id)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.REPAIR)
        self.assertEqual(decision.reasonCode, "MANDATORY_CRITERION_FAILED")

    def test_independent_evidence_missing_waits_for_user(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        # defense-in-depth: a PASS result backed only by Level 0 evidence
        VerificationResultStore(self.root).save(VerificationResult(
            taskId=task.id, criterionId="c1", result=VerificationStatus.PASS,
            verifier="x", independenceLevel=IndependenceLevel.LEVEL_0_SELF,
            verificationMethod="read-content", timestamp=utcnow_iso(),
            evidence=[Evidence(evidenceId="e1", timestamp=utcnow_iso(),
                               source="fs.read_file", collectorIdentity="acting-model",
                               criterionId="c1")]))
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.WAITING_USER)
        self.assertEqual(decision.reasonCode, "INDEPENDENT_EVIDENCE_MISSING")

    def test_critical_failure_present_fails(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        self.mgr.update_task(task.id, UpdateTaskRequest(appendFailures=[FailureRecord(
            failureId="f1", taskId=task.id, timestamp=utcnow_iso(),
            category="TOOL_FAILURE", sideEffectState=SideEffectState.UNKNOWN)]))
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.FAILED)
        self.assertEqual(decision.reasonCode, "CRITICAL_FAILURE_PRESENT")

    def test_stale_verification_requires_reverification(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        self.assertTrue(self.acting_write(task, "GOODBYE").executed)
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.CONTINUE)
        self.assertEqual(decision.reasonCode, "RECOVERY_REQUIRES_REVERIFICATION")

    def test_policy_compliance_failure_blocks(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        journal = self.store.journal_for(task.id)
        journal.append("ACTION_STARTED", {  # un-audited action (synthetic)
            "actionId": "evil", "taskId": task.id, "operationId": "fs.write_file",
            "targetType": "FILESYSTEM", "target": "/data/out/x.txt",
            "argumentsSha256": "x", "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.BLOCKED)
        self.assertEqual(decision.reasonCode, "POLICY_COMPLIANCE_FAILED")

    def test_resource_limit_exceeded_fails(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(resourceLimits=ResourceLimits(actionSteps=1))
        self.engine.verify(task.id)  # consumes the single allowed action step
        journal = self.store.journal_for(task.id)
        journal.append("POLICY_DECISION", {  # synthetic audited overrun
            "taskId": task.id, "operationId": "fs.read_file",
            "target": "/data/out/x.txt", "argumentsSha256": "x",
            "decision": "ALLOW", "reason": "synthetic"})
        journal.append("ACTION_STARTED", {
            "actionId": "overrun", "taskId": task.id, "operationId": "fs.read_file",
            "targetType": "FILESYSTEM", "target": "/data/out/x.txt",
            "argumentsSha256": "x", "actorId": "agent-1", "actorType": "TOP_LEVEL_AGENT"})
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.FAILED)
        self.assertEqual(decision.reasonCode, "RESOURCE_LIMIT_EXCEEDED")


class IntegrityTests(CompletionTestBase):

    def test_tampered_verification_result_blocks(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        path = self.root / task.id / "verification" / "c1.json"
        data = json.loads(path.read_text())
        data["payload"]["result"] = "FAIL"
        path.write_text(json.dumps(data))
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.BLOCKED)
        self.assertEqual(decision.reasonCode, "VERIFICATION_RECORD_INTEGRITY_FAILED")

    def test_tampered_journal_blocks(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        path = self.root / task.id / "journal.jsonl"
        lines = path.read_text().splitlines()
        data = json.loads(lines[0])
        data["payload"]["reason"] = "tampered"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.BLOCKED)
        self.assertEqual(decision.reasonCode, "POLICY_RECORD_INTEGRITY_FAILED")

    def test_completion_decision_tampering_detected(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        self.completion.evaluate(task.id)
        path = self.root / task.id / "completion" / "decision.json"
        data = json.loads(path.read_text())
        data["payload"]["decision"] = "CONTINUE"
        path.write_text(json.dumps(data))
        with self.assertRaises(TaskIntegrityError):
            CompletionDecisionStore(self.root).load(task.id)

    def test_invalid_completion_contract_blocks(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        # craft an envelope-valid task whose contract lost its mandatory criterion
        path = self.root / task.id / "task.json"
        data = json.loads(path.read_text())
        data["payload"]["completionContract"]["successCriteria"] = []
        data["integrity"]["digest"] = _digest(data["payload"])  # re-seal
        path.write_text(json.dumps(data))
        decision = self.completion.evaluate(task.id)
        self.assertIs(decision.decision, CompletionDecisionValue.BLOCKED)
        self.assertEqual(decision.reasonCode, "COMPLETION_CONTRACT_INVALID")


if __name__ == "__main__":
    unittest.main()
