"""ExecutionPipeline tests: the controlled path from proposal to executed action."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from continuity.task_store import TaskStore
from execution import ExecutionPipeline, QueueApprover
from policy import Canonicalizer, PolicyEngine, ProtectedPathRegistry, RuntimePathMapping
from task import CreateTaskRequest, TaskManager

from core import (
    ActionRequest,
    ActorIdentity,
    CompletionContract,
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
    ApprovalDecision,
    EffortLevel,
    PermissionMode,
    RiskLevel,
    Reversibility,
    SideEffect,
    SideEffectState,
    TargetType,
    TaskState,
)


def make_criterion():
    return SuccessCriterion(id="c1", description="file written", verificationMethod="read file")


def make_tac(**overrides):
    now = utcnow_iso()
    defaults = dict(
        schemaVersion="1.0",
        allowedWritePaths=["/data/out/**"],
        allowedPackages=["com.example.app"],
        allowedPackageOperations=["package.install"],
        createdAt=now, updatedAt=now,
    )
    defaults.update(overrides)
    return TargetAuthorizationContext(**defaults)


class PipelineTestBase(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = TaskStore(self.root)
        self.mgr = TaskManager(self.store)
        self.engine = PolicyEngine(
            canonicalizer=Canonicalizer(resolve_symlinks=False),
            protected_paths=ProtectedPathRegistry(
                version="1.0",
                mapping=RuntimePathMapping(version="1.0", paths={"P0": ("/repo/.supersystem/**",)})))
        self.approver = QueueApprover()
        self.tools = {
            "fs.write_file": Tool(
                id="fs.write_file", name="write_file", description="write a file",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._fake_write),
            "package.install": Tool(
                id="package.install", name="install", description="install package",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._fake_install),
        }
        self.pipeline = ExecutionPipeline(self.mgr, self.engine, self.tools,
                                          self.store, approver=self.approver)
        self.writes = []
        self.installs = []

    def _fake_write(self, args, context):
        self.writes.append((args, context))
        return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso())

    def _fake_install(self, args, context):
        self.installs.append((args, context))
        return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso())

    def make_task(self, **overrides):
        defaults = dict(
            objective="write a file",
            permissionMode=PermissionMode.AUTO,
            effortLevel=EffortLevel.STANDARD,
            resourceLimits=ResourceLimits(actionSteps=100, wallClockTime=3600),
            targetAuthorizationContext=make_tac(),
            successCriteria=[make_criterion()],
            completionContract=CompletionContract(
                objective="write a file", successCriteria=[make_criterion()]),
        )
        defaults.update(overrides)
        task = self.mgr.create_task(CreateTaskRequest(**defaults))
        for state in (TaskState.VALIDATING, TaskState.PLANNING, TaskState.READY,
                      TaskState.RUNNING):
            task = self.mgr.transition(task.id, state)
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


class AllowDenyPathTests(PipelineTestBase):

    def test_allow_path_executes_and_journals(self):
        task = self.make_task()
        result = self.pipeline.execute(self.make_request(task))
        self.assertTrue(result.executed)
        self.assertTrue(result.toolResult.success)
        self.assertTrue(result.auditRecorded)
        self.assertTrue(result.journalTerminalRecorded)
        self.assertEqual(result.terminal_journal_state, "SUCCEEDED")
        self.assertEqual(len(self.writes), 1)
        journal = self.store.journal_for(task.id)
        self.assertEqual([r["eventType"] for r in journal.records()],
                         ["POLICY_DECISION", "ACTION_STARTED", "ACTION_TERMINAL"])

    def test_journal_redacts_arguments(self):
        task = self.make_task()
        request = self.make_request(task)
        request.arguments = {"path": "/data/out/x.txt", "apiKey": "SUPER-SECRET-123"}
        self.pipeline.execute(request)
        journal = self.store.journal_for(task.id)
        serialized = json.dumps([r["payload"] for r in journal.records()])
        self.assertIn("argumentsSha256", serialized)
        self.assertNotIn("SUPER-SECRET-123", serialized)

    def test_denied_action_never_executes(self):
        task = self.make_task()
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT),
            toolId="fs.do_evil", target=None, arguments={}, reason="evil")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.writes), 0)

    def test_denied_decision_is_audited(self):
        task = self.make_task()
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT),
            toolId="fs.do_evil", target=None, arguments={}, reason="evil")
        result = self.pipeline.execute(request)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertTrue(result.auditRecorded)
        journal = self.store.journal_for(task.id)
        self.assertEqual(journal.records()[0]["eventType"], "POLICY_DECISION")

    def test_unknown_task_denied(self):
        request = ActionRequest(
            taskId="ghost",
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments={"path": "/data/out/x.txt"}, reason="x")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_non_executable_task_state_denied(self):
        task = self.make_task()
        self.mgr.transition(task.id, TaskState.BLOCKED)
        result = self.pipeline.execute(self.make_request(task))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")


class ApprovalPathTests(PipelineTestBase):

    def install_request(self, task):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="package.install",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            arguments={"package": "com.example.app"}, reason="install app")

    def test_ask_with_approve_executes(self):
        task = self.make_task()
        self.approver.enqueue(ApprovalDecision.APPROVE)
        result = self.pipeline.execute(self.install_request(task))
        self.assertTrue(result.executed)
        self.assertIsNotNone(result.approvalResult)
        self.assertEqual(len(self.installs), 1)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("APPROVAL_REQUESTED", events)
        self.assertIn("APPROVAL_RESULT", events)

    def test_ask_with_deny_blocks(self):
        task = self.make_task()
        self.approver.enqueue(ApprovalDecision.DENY)
        result = self.pipeline.execute(self.install_request(task))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(self.installs), 0)

    def test_ask_timeout_blocks(self):
        task = self.make_task()
        result = self.pipeline.execute(self.install_request(task))
        self.assertFalse(result.executed)
        self.assertIn("timed out", result.policyDecision.reason)

    def test_approval_references_are_fresh_each_time(self):
        task = self.make_task()
        self.approver.enqueue(ApprovalDecision.APPROVE)
        first = self.pipeline.execute(self.install_request(task))
        self.assertTrue(first.executed)
        self.approver.enqueue(ApprovalDecision.APPROVE)
        second = self.pipeline.execute(self.install_request(task))
        self.assertTrue(second.executed)
        self.assertNotEqual(first.approvalResult.approvalReference,
                            second.approvalResult.approvalReference)
        consumed = self.store.journal_for(task.id).consumed_approval_references()
        self.assertEqual(len(consumed), 2)


class ResourceAndFailureTests(PipelineTestBase):

    def test_action_steps_budget_exhausted(self):
        task = self.make_task(resourceLimits=ResourceLimits(actionSteps=1))
        first = self.pipeline.execute(self.make_request(task))
        self.assertTrue(first.executed)
        second = self.pipeline.execute(self.make_request(task))
        self.assertFalse(second.executed)
        self.assertIn("budget", second.policyDecision.reason)
        self.assertEqual(len(self.writes), 1)

    def test_tool_failure_recorded_durably(self):
        def failing(args, context):
            return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                              timestamp=utcnow_iso())
        self.tools["fs.write_file"].execute = failing
        task = self.make_task()
        result = self.pipeline.execute(self.make_request(task))
        self.assertTrue(result.executed)
        self.assertFalse(result.toolResult.success)
        self.assertEqual(result.terminal_journal_state, "FAILED")
        reloaded = self.mgr.get_task(task.id)
        self.assertEqual(len(reloaded.failures), 1)
        self.assertEqual(reloaded.failures[0].operation, "fs.write_file")

    def test_tool_exception_becomes_unknown(self):
        def exploding(args, context):
            raise RuntimeError("boom")
        self.tools["fs.write_file"].execute = exploding
        task = self.make_task()
        result = self.pipeline.execute(self.make_request(task))
        self.assertTrue(result.executed)
        self.assertFalse(result.toolResult.success)
        self.assertIs(result.toolResult.sideEffectState, SideEffectState.UNKNOWN)
        self.assertEqual(result.terminal_journal_state, "UNKNOWN")
        self.assertTrue(any("boom" in err for err in result.errors))

    def test_tampered_journal_fails_closed(self):
        task = self.make_task()
        self.pipeline.execute(self.make_request(task))
        path = self.root / task.id / "journal.jsonl"
        lines = path.read_text().splitlines()
        data = json.loads(lines[0])
        data["payload"]["reason"] = "tampered"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = self.pipeline.execute(self.make_request(task))
        self.assertFalse(result.executed)
        self.assertIn("failure", result.policyDecision.reason)

    def test_journal_failure_before_started_blocks_execution(self):
        task = self.make_task()
        # make journal.jsonl a directory so appends fail with OSError
        (self.root / task.id / "journal.jsonl").mkdir(exist_ok=True)
        result = self.pipeline.execute(self.make_request(task))
        self.assertFalse(result.executed)
        self.assertEqual(len(self.writes), 0)


class MissingImplementationTests(PipelineTestBase):
    """The registry declares what Policy may authorize; the tool table
    declares what this runtime can run, and the two are not the same set
    (POLICY_RULES registers operations no adapter implements). An operation
    with no implementation must be refused - not attempted, not reported as
    an unknown side effect, and not escalated to a human who cannot make it
    work."""

    def ui_request(self, task, tool_id: str) -> ActionRequest:
        target = "com.example.app#n0"
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId=tool_id,
            target=Target(type=TargetType.UI, value=target),
            arguments={"target": target},
            reason="test action",
        )

    def ui_task(self):
        return self.make_task(targetAuthorizationContext=make_tac(
            allowedUIActions=["com.example.app#*"]))

    def test_a_registered_operation_without_an_implementation_is_refused(self):
        # accessibility.inspect_ui is LOW/READ_ONLY: Policy allows it in AUTO,
        # and no adapter implements it. Nothing ran, so nothing about its side
        # effect is unknown.
        task = self.ui_task()
        result = self.pipeline.execute(self.ui_request(task, "accessibility.inspect_ui"))

        self.assertFalse(result.executed)
        self.assertIsNone(result.toolResult)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertIn("no implementation", result.policyDecision.reason)
        self.assertEqual(result.terminal_journal_state, "CANCELLED")

    def test_a_refused_operation_leaves_no_started_record(self):
        # The audit says what Policy decided (ALLOW); the absence of an
        # ACTION_STARTED record is what says it was refused afterwards.
        task = self.ui_task()
        self.pipeline.execute(self.ui_request(task, "accessibility.inspect_ui"))
        records = self.store.journal_for(task.id).records()

        decisions = [r["payload"]["decision"] for r in records
                     if r["eventType"] == "POLICY_DECISION"]
        self.assertEqual(decisions, ["ALLOW"])
        self.assertEqual([r for r in records if r["eventType"] == "ACTION_STARTED"], [])

    def test_an_unimplemented_ask_operation_never_reaches_a_human(self):
        # accessibility.submit is forced ASK. Asking an operator to approve
        # something the runtime cannot do would be a dead end: the refusal
        # happens before the prompt, and no approval request is created.
        task = self.ui_task()
        self.pipeline.approver = mock.Mock()
        result = self.pipeline.execute(self.ui_request(task, "accessibility.submit"))

        self.pipeline.approver.request.assert_not_called()
        self.assertFalse(result.executed)
        self.assertIsNone(result.approvalResult)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertNotIn("APPROVAL_REQUESTED", events)

    def test_a_registered_tool_is_still_executed_normally(self):
        # the refusal is about the tool table, not about the registry: the
        # same request shape executes when an implementation exists
        task = self.ui_task()
        self.tools["accessibility.inspect_ui"] = Tool(
            id="accessibility.inspect_ui", name="inspect_ui", description="inspect",
            inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
            sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
            execute=lambda args, context: ToolResult(
                success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                timestamp=utcnow_iso()))

        result = self.pipeline.execute(self.ui_request(task, "accessibility.inspect_ui"))

        self.assertTrue(result.executed)
        self.assertEqual(result.terminal_journal_state, "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
