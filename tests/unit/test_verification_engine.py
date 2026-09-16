"""VerificationEngine tests (Phase 5): independent verification of task results."""
import json
import tempfile
import unittest
from pathlib import Path

from continuity.task_store import TaskIntegrityError, TaskNotFoundError, TaskStore
from execution import ExecutionPipeline, QueueApprover
from policy import Canonicalizer, PolicyEngine, ProtectedPathRegistry, RuntimePathMapping
from task import CreateTaskRequest, TaskManager
from verification import (
    VerificationEngine,
    VerificationError,
    VerificationMethodSpec,
    VerificationResultStore,
    output_contains,
)

from core import (
    ActionRequest,
    ActorIdentity,
    CompletionContract,
    Evidence,
    ResourceLimits,
    SuccessCriterion,
    Target,
    TargetAuthorizationContext,
    Tool,
    ToolResult,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    EffortLevel,
    IndependenceLevel,
    PermissionMode,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TargetType,
    TaskState,
    VerificationStatus,
)


def criterion(cid="c1", method="read-content", risk=RiskLevel.LOW, mandatory=True):
    return SuccessCriterion(
        id=cid, description=f"file contains HELLO ({cid})",
        verificationMethod=method, mandatory=mandatory, riskLevel=risk)


def make_tac():
    now = utcnow_iso()
    return TargetAuthorizationContext(
        schemaVersion="1.0",
        allowedReadPaths=["/data/out/**"],
        allowedWritePaths=["/data/out/**"],
        allowedPackages=["com.example.app"],
        allowedPackageOperations=["package.install"],
        createdAt=now, updatedAt=now)


class VerificationTestBase(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = TaskStore(self.root)
        self.mgr = TaskManager(self.store)
        self.policy = PolicyEngine(
            canonicalizer=Canonicalizer(resolve_symlinks=False),
            protected_paths=ProtectedPathRegistry(
                version="1.0",
                mapping=RuntimePathMapping(version="1.0", paths={"P0": ("/repo/.supersystem/**",)})))
        self.approver = QueueApprover()
        self.files = {}
        self.reads = []
        self.writes = []
        self.tools = {
            "fs.write_file": Tool(
                id="fs.write_file", name="write_file", description="write a file",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._fake_write),
            "fs.read_file": Tool(
                id="fs.read_file", name="read_file", description="read a file",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._fake_read),
            "package.install": Tool(
                id="package.install", name="install", description="install package",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._fake_install),
        }
        self.pipeline = ExecutionPipeline(self.mgr, self.policy, self.tools,
                                          self.store, approver=self.approver)
        self.engine = VerificationEngine(
            self.mgr, self.pipeline, self.store,
            verifier_identity="verification-engine",
            methods=self._methods())

    def _fake_write(self, args, context):
        self.writes.append(args["path"])
        self.files[args["path"]] = args.get("content", "")
        return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso(), output={"written": args["path"]})

    def _fake_read(self, args, context):
        self.reads.append(args["path"])
        path = args["path"]
        if path in self.files:
            return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(),
                              output={"path": path, "content": self.files[path], "exists": True})
        return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                          timestamp=utcnow_iso(), output={"path": path, "exists": False})

    def _fake_install(self, args, context):
        return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso())

    @staticmethod
    def _methods():
        def self_report_assessor(tool_result, task, criterion):
            return VerificationStatus.PASS, "acting model claims success"

        def exploding_assessor(tool_result, task, criterion):
            raise RuntimeError("assessor boom")

        return {
            "read-content": VerificationMethodSpec(
                method="read-content", toolId="fs.read_file",
                arguments={"path": "/data/out/x.txt"},
                assessor=output_contains("content", "HELLO")),
            "self-report": VerificationMethodSpec(
                method="self-report", toolId=None, includeActionEvidence=True,
                assessor=self_report_assessor),
            "install-check": VerificationMethodSpec(
                method="install-check", toolId="package.install",
                arguments={"package": "com.example.app"},
                assessor=output_contains("installed", True)),
            "exploding": VerificationMethodSpec(
                method="exploding", toolId="fs.read_file",
                arguments={"path": "/data/out/x.txt"},
                assessor=exploding_assessor),
        }

    def make_task(self, criteria=None, stop_at=TaskState.VERIFYING, **overrides):
        criteria = list(criteria) if criteria is not None else [criterion()]
        defaults = dict(
            objective="write a file",
            permissionMode=PermissionMode.AUTO,
            effortLevel=EffortLevel.STANDARD,
            resourceLimits=ResourceLimits(actionSteps=100, wallClockTime=3600),
            targetAuthorizationContext=make_tac(),
            successCriteria=criteria,
            completionContract=CompletionContract(
                objective="write a file", successCriteria=criteria,
                requiredIndependenceLevel=IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME),
        )
        defaults.update(overrides)
        task = self.mgr.create_task(CreateTaskRequest(**defaults))
        if stop_at is not TaskState.CREATED:
            for state in (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                          TaskState.RUNNING, TaskState.OBSERVING, TaskState.VERIFYING):
                task = self.mgr.transition(task.id, state)
                if state is stop_at:
                    break
        return task

    def make_request(self, task, tool_id="fs.write_file", path="/data/out/x.txt"):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId=tool_id,
            target=Target(type=TargetType.FILESYSTEM, value=path),
            arguments={"path": path},
            reason="test action",
        )

    def acting_write(self, task, content):
        return self.pipeline.execute(ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"path": "/data/out/x.txt", "content": content},
            reason="acting model writes the file"))


class PassFailTests(VerificationTestBase):

    def test_pass_when_observed_state_satisfies_criterion(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.PASS)
        result = report.criterionResults[0]
        self.assertIs(result.result, VerificationStatus.PASS)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].collectorIdentity, "verification-engine")
        self.assertIs(result.independenceLevel,
                      IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("VERIFICATION_RESULT", events)

    def test_technical_success_but_requirement_failed_is_detected(self):
        """Phase 5 exit criterion: an action that executes successfully can
        still fail the actual task requirement."""
        self.files["/data/out/x.txt"] = "GOODBYE"
        task = self.make_task()
        acting = self.acting_write(task, "GOODBYE")
        self.assertTrue(acting.executed)  # technically successful action
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.FAIL)
        self.assertIs(report.criterionResults[0].result, VerificationStatus.FAIL)
        self.assertIn("expected", report.criterionResults[0].failureReason)

    def test_unknown_verification_method_is_inconclusive(self):
        task = self.make_task(criteria=[criterion(method="ghost")])
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)

    def test_verify_requires_verifying_state(self):
        task = self.make_task(stop_at=TaskState.RUNNING)
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertTrue(any("VERIFYING" in note for note in report.notes))

    def test_no_criteria_is_inconclusive(self):
        task = self.make_task()
        report = self.engine.verify(task.id, criteria=[])
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)

    def test_non_mandatory_criterion_does_not_block_pass(self):
        self.files["/data/out/x.txt"] = "HELLO"
        criteria = [criterion("c1"), criterion("c2", method="ghost", mandatory=False)]
        task = self.make_task(criteria=criteria)
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.PASS)
        by_id = {r.criterionId: r.result for r in report.criterionResults}
        self.assertIs(by_id["c2"], VerificationStatus.INCONCLUSIVE)

    def test_collection_tool_failure_is_inconclusive(self):
        task = self.make_task()  # file was never written -> read fails
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertIn("collection", report.criterionResults[0].failureReason)

    def test_assessor_exception_is_inconclusive(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(criteria=[criterion(method="exploding")])
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)


class PolicyGovernanceTests(VerificationTestBase):

    def test_evidence_collection_passes_through_policy(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("ACTION_STARTED", events)  # audited like any action
        self.assertEqual(self.reads, ["/data/out/x.txt"])  # went through the pipeline

    def test_policy_denied_collection_is_inconclusive(self):
        """s12.1: verification does not bypass Policy - an unauthorized read
        cannot become evidence."""
        self.files["/etc/passwd"] = "root:x:0:0"
        task = self.make_task()
        methods = dict(self._methods())
        methods["read-content"] = VerificationMethodSpec(
            method="read-content", toolId="fs.read_file",
            arguments={"path": "/etc/passwd"},
            assessor=output_contains("content", "root"))
        self.engine.methods = methods
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertNotIn("/etc/passwd", self.reads)
        with self.assertRaises(VerificationError):
            self.engine.collectEvidence(task.id, criterion())

    def test_ask_verification_action_fails_closed_without_approval(self):
        task = self.make_task(criteria=[criterion(method="install-check")])
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)


class IndependenceTests(VerificationTestBase):

    def test_high_risk_requires_independent_evidence(self):
        task = self.make_task(criteria=[criterion(risk=RiskLevel.HIGH,
                                                 method="self-report")])
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertIn("independen", report.criterionResults[0].failureReason)

    def test_acting_model_claims_insufficient_by_default(self):
        """s9: Level 0 self-evidence is insufficient where independent
        verification is mandatory (contract default is Level 1)."""
        task = self.make_task(criteria=[criterion(method="self-report")])
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        result = report.criterionResults[0]
        self.assertTrue(result.evidence)  # the acting side's journal claims exist
        self.assertIn("independen", result.failureReason)

    def test_self_evidence_sufficient_only_when_contract_allows(self):
        criteria = [criterion(method="self-report")]
        task = self.make_task(
            criteria=criteria,
            completionContract=CompletionContract(
                objective="write a file", successCriteria=criteria,
                requiredIndependenceLevel=IndependenceLevel.LEVEL_0_SELF))
        self.assertTrue(self.acting_write(task, "HELLO").executed)
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.PASS)

    def test_assess_independence_by_collector_identity(self):
        now = utcnow_iso()
        self_evidence = Evidence(
            evidenceId="e1", timestamp=now, source="fs.read_file",
            collectorIdentity="acting-model", criterionId="c1")
        engine_evidence = Evidence(
            evidenceId="e2", timestamp=now, source="fs.read_file",
            collectorIdentity="verification-engine", criterionId="c1")
        sufficient = self.engine.assessIndependence(
            [self_evidence, engine_evidence],
            requiredLevel=IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        self.assertTrue(sufficient.sufficient)
        self.assertIs(sufficient.level,
                      IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        insufficient = self.engine.assessIndependence(
            [self_evidence],
            requiredLevel=IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        self.assertFalse(insufficient.sufficient)
        self.assertIs(insufficient.level, IndependenceLevel.LEVEL_0_SELF)


class MutationAndDurabilityTests(VerificationTestBase):

    def test_mutation_invalidates_previous_verification(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        first = self.engine.verify(task.id)
        self.assertIs(first.status, VerificationStatus.PASS)
        self.assertTrue(self.acting_write(task, "GOODBYE").executed)
        second = self.engine.verify(task.id)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("VERIFICATION_INVALIDATED", events)
        self.assertIs(second.status, VerificationStatus.FAIL)  # re-verified fresh

    def test_invalidate_api_persists_tombstone(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        self.engine.invalidate(task.id, criterionId="c1", reason="external change")
        stored = VerificationResultStore(self.root).load(task.id, "c1")
        self.assertIs(stored.result, VerificationStatus.INCONCLUSIVE)
        task_state = self.mgr.get_task(task.id)
        self.assertIs(task_state.verification.criterionResults["c1"],
                      VerificationStatus.INCONCLUSIVE)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("VERIFICATION_INVALIDATED", events)

    def test_stored_result_is_integrity_protected(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        store = VerificationResultStore(self.root)
        self.assertIs(store.load(task.id, "c1").result, VerificationStatus.PASS)
        path = self.root / task.id / "verification" / "c1.json"
        data = json.loads(path.read_text())
        data["payload"]["result"] = "FAIL"
        path.write_text(json.dumps(data))
        with self.assertRaises(TaskIntegrityError):
            store.load(task.id, "c1")

    def test_verification_result_journaled_with_digest(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        records = [r for r in self.store.journal_for(task.id).records()
                   if r["eventType"] == "VERIFICATION_RESULT"]
        self.assertEqual(len(records), 1)
        self.assertIn("resultDigest", records[0]["payload"])

    def test_journal_failure_fails_closed(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        (self.root / task.id / "journal.jsonl").mkdir(exist_ok=True)
        report = self.engine.verify(task.id)
        self.assertIs(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertFalse((self.root / task.id / "verification").exists())

    def test_unknown_task_raises(self):
        with self.assertRaises(TaskNotFoundError):
            self.engine.verify("ghost")

    def test_task_verification_state_updated(self):
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task()
        self.engine.verify(task.id)
        state = self.mgr.get_task(task.id).verification
        self.assertIs(state.overall, VerificationStatus.PASS)
        self.assertIs(state.criterionResults["c1"], VerificationStatus.PASS)

    def test_high_risk_requires_full_provenance_metadata(self):
        incomplete = Evidence(evidenceId="e1", timestamp=utcnow_iso(), source="x",
                              collectorIdentity="y", criterionId="c1")
        self.assertIsNotNone(self.engine._check_provenance([incomplete]))
        complete = Evidence(evidenceId="e2", timestamp=utcnow_iso(), source="x",
                            collectorIdentity="y", contentHash="h", provenance="p",
                            validationStatus="VALIDATED", criterionId="c1")
        self.assertIsNone(self.engine._check_provenance([complete]))


if __name__ == "__main__":
    unittest.main()
