"""Verification integration: separate verifier (LEVEL_2), approval-gated
evidence collection, and verification budget consumption."""
import unittest

from tests.support.harness import VerificationTestBase, criterion
from tests.support.fakes import FakeVerifier

from core import CompletionContract, ResourceLimits, VerificationResult, utcnow_iso
from core.enums import (
    ApprovalDecision,
    IndependenceLevel,
    VerificationStatus,
)
from completion import CompletionEngine
from verification import (
    VerificationEngine,
    VerificationMethodSpec,
    VerificationResultStore,
    output_satisfies,
)


class VerificationIntegrationTests(VerificationTestBase):

    def test_separate_verifier_evidence_is_level_2(self):
        verifier = FakeVerifier()
        task = self.make_task()
        evidence = verifier.collect(task, task.successCriteria[0])
        unregistered = self.engine.assessIndependence(
            [evidence], requiredLevel=IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER)
        self.assertFalse(unregistered.sufficient)  # unknown collector = LEVEL_0
        registered = VerificationEngine(
            self.mgr, self.pipeline, self.store,
            methods=dict(self.engine.methods),
            verifier_identity=self.engine.verifier_identity,
            separate_verifiers={verifier.identity})
        result = registered.assessIndependence(
            [evidence], requiredLevel=IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER)
        self.assertTrue(result.sufficient)
        self.assertIs(result.level, IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER)

    def test_level_2_evidence_satisfies_completion_requirement(self):
        verifier = FakeVerifier(output={"content": "HELLO"})
        criteria = [criterion()]
        contract = CompletionContract(
            objective="write a file", successCriteria=criteria,
            requiredIndependenceLevel=IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER)
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(criteria=criteria, completionContract=contract)
        # Phase 5 already downgrades LEVEL_1 evidence below the required
        # LEVEL_2 to INCONCLUSIVE, so completion continues
        self.engine.verify(task.id)
        completion = CompletionEngine(self.mgr, self.store)
        decision = completion.evaluate(task.id)
        self.assertEqual(decision.decision.value, "CONTINUE")
        self.assertEqual(decision.reasonCode, "VERIFICATION_INCONCLUSIVE")
        # a separate verifier independently obtains evidence; a new result
        # at LEVEL_2 replaces the old one
        evidence = verifier.collect(task, criteria[0])
        VerificationResultStore(self.root).save(VerificationResult(
            taskId=task.id, criterionId="c1", result=VerificationStatus.PASS,
            verifier=verifier.identity,
            independenceLevel=IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER,
            verificationMethod="read-content", timestamp=utcnow_iso(),
            evidence=[evidence]))
        decision = completion.evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")

    def test_verification_evidence_under_human_approval(self):
        methods = dict(self._methods())
        methods["install-check"] = VerificationMethodSpec(
            method="install-check", toolId="package.install",
            arguments={"package": "com.example.app"},
            assessor=output_satisfies(lambda out: True,
                                      "install approved and executed"))
        self.engine.methods = methods
        task = self.make_task(criteria=[criterion(method="install-check")])
        self.assertEqual(self.engine.verify(task.id).status.value, "INCONCLUSIVE")
        self.approver.enqueue(ApprovalDecision.APPROVE)
        report = self.engine.verify(task.id)
        self.assertEqual(report.status.value, "PASS")
        records = self.store.journal_for(task.id).records()
        installs = [r for r in records if r["eventType"] == "ACTION_STARTED"
                    and r["payload"].get("operationId") == "package.install"]
        self.assertEqual(len(installs), 1)

    def test_verification_consumes_resource_budget(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(resourceLimits=ResourceLimits(actionSteps=1))
        report = self.engine.verify(task.id)  # consumes the single step
        self.assertEqual(report.status.value, "PASS")
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")
        used = self.store.journal_for(task.id).count_event_type("ACTION_STARTED")
        self.assertEqual(used, 1)


if __name__ == "__main__":
    unittest.main()
