"""Termux platform adapters (Phase 12): real filesystem tools, Termux:API
environment tools, and durable runtime sessions - all through the Core
pipeline with unchanged security semantics."""
import json
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import VerificationTestBase
from tests.support.fakes import ScriptedModelPort

from core import (
    ActionRequest,
    ActorIdentity,
    CompletionContract,
    ResourceLimits,
    Target,
    TargetAuthorizationContext,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    PermissionMode,
    EffortLevel,
    TargetType,
    TaskState,
)
from task import CreateTaskRequest
from execution import ExecutionPipeline
from policy import Canonicalizer, PolicyEngine, ProtectedPathRegistry, RuntimePathMapping
from platforms.termux import (
    TermuxEnvironmentAdapter,
    TermuxFilesystemAdapter,
    TermuxRuntimeAdapter,
)


def scope_pattern(path: str) -> str:
    """A scope pattern for a path, in the form Policy canonicalizes to.

    `<tmp>/**` is not the same string as the canonical `/C:/.../**` a
    Windows path becomes (and `/tmp/**` on POSIX it already is), so the
    pattern is derived through the same canonicalizer Policy uses and the
    test declares what it means on both.
    """
    canonical = Canonicalizer(resolve_symlinks=False).canonicalize(
        TargetType.FILESYSTEM, path)
    assert canonical is not None, path
    return canonical.rstrip("/") + "/**"


class TermuxPlatformTests(VerificationTestBase):

    def make_platform_task(self, read_scope, write_scope, protected):
        now = utcnow_iso()
        tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=[read_scope],
            allowedWritePaths=[write_scope],
            createdAt=now, updatedAt=now,
        )
        policy = PolicyEngine(
            canonicalizer=Canonicalizer(resolve_symlinks=False),
            protected_paths=ProtectedPathRegistry(
                version="1.0",
                mapping=RuntimePathMapping(version="1.0", paths=protected)))
        adapter = TermuxFilesystemAdapter(protected_mapping=protected)
        pipeline = ExecutionPipeline(self.mgr, policy, adapter.tools(),
                                     self.store, approver=self.approver)
        criteria = [self._criterion()]
        task = self.mgr.create_task(CreateTaskRequest(
            objective="use the termux filesystem",
            permissionMode=PermissionMode.AUTO,
            effortLevel=EffortLevel.STANDARD,
            resourceLimits=ResourceLimits(actionSteps=50, wallClockTime=3600),
            targetAuthorizationContext=tac,
            successCriteria=criteria,
            completionContract=CompletionContract(
                objective="use the termux filesystem", successCriteria=criteria),
        ))
        return task, pipeline

    @staticmethod
    def _criterion():
        from tests.support.harness import criterion
        return criterion()

    def action(self, task, tool_id, arguments, target=None):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId=tool_id,
            target=target,
            arguments=arguments,
            reason="platform test action")

    def test_real_filesystem_tools_execute_through_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, pipeline = self.make_platform_task(
                read_scope=scope_pattern(tmp),
                write_scope=scope_pattern(f"{tmp}/out"),
                protected={"P0": (scope_pattern(f"{tmp}/.supersystem"),)})
            for state in (TaskState.VALIDATING, TaskState.PLANNING,
                          TaskState.READY, TaskState.RUNNING):
                task = self.mgr.transition(task.id, state)
            write = pipeline.execute(self.action(
                task, "fs.write_file",
                {"path": f"{tmp}/out/x.txt", "content": "HELLO"},
                target=Target(type=TargetType.FILESYSTEM,
                              value=f"{tmp}/out/x.txt")))
            self.assertTrue(write.executed, write.policyDecision.reason)
            self.assertTrue(Path(f"{tmp}/out/x.txt").is_file())
            self.assertEqual(Path(f"{tmp}/out/x.txt").read_text(), "HELLO")
            read = pipeline.execute(self.action(
                task, "fs.read_file", {"path": f"{tmp}/out/x.txt"},
                target=Target(type=TargetType.FILESYSTEM,
                              value=f"{tmp}/out/x.txt")))
            self.assertTrue(read.executed)
            self.assertEqual(read.toolResult.output["content"], "HELLO")
            journal = [r["eventType"] for r in self.store.journal_for(task.id).records()]
            self.assertIn("ACTION_STARTED", journal)

    def test_filesystem_mutation_respects_protected_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, pipeline = self.make_platform_task(
                read_scope=scope_pattern(tmp), write_scope=scope_pattern(tmp),
                protected={"P0": (scope_pattern(f"{tmp}/.supersystem"),)})
            for state in (TaskState.VALIDATING, TaskState.PLANNING,
                          TaskState.READY, TaskState.RUNNING):
                task = self.mgr.transition(task.id, state)
            protected_write = pipeline.execute(self.action(
                task, "fs.write_file",
                {"path": f"{tmp}/.supersystem/POLICY.md", "content": "x"},
                target=Target(type=TargetType.FILESYSTEM,
                              value=f"{tmp}/.supersystem/POLICY.md")))
            self.assertFalse(protected_write.executed)
            self.assertEqual(protected_write.policyDecision.decision.value, "DENY")
            self.assertEqual(protected_write.policyDecision.reason, "protected target")
            self.assertFalse(Path(f"{tmp}/.supersystem/POLICY.md").exists())

    def test_filesystem_errors_report_known_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, pipeline = self.make_platform_task(
                read_scope=scope_pattern(tmp), write_scope=scope_pattern(tmp),
                protected={"P0": (scope_pattern(f"{tmp}/.supersystem"),)})
            for state in (TaskState.VALIDATING, TaskState.PLANNING,
                          TaskState.READY, TaskState.RUNNING):
                task = self.mgr.transition(task.id, state)
            result = pipeline.execute(self.action(
                task, "fs.read_file", {"path": f"{tmp}/missing.txt"},
                target=Target(type=TargetType.FILESYSTEM,
                              value=f"{tmp}/missing.txt")))
            self.assertTrue(result.executed)
            self.assertFalse(result.toolResult.success)

    def test_environment_adapter_reports_unavailable_without_binary(self):
        def missing(binary):
            raise OSError("binary not found")
        adapter = TermuxEnvironmentAdapter(run_command=missing)
        tools = adapter.tools()
        self.assertEqual(set(tools), {
            "termux_api.battery_status", "termux_api.wifi_status",
            "termux_api.device_info"})
        result = tools["termux_api.battery_status"].execute({}, {})
        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "TERMUX_API_UNAVAILABLE")

    def test_environment_adapter_parses_binary_output(self):
        adapter = TermuxEnvironmentAdapter(
            run_command=lambda binary: {"battery": {"percentage": 80}})
        result = adapter.tools()["termux_api.battery_status"].execute({}, {})
        self.assertTrue(result.success)
        self.assertEqual(result.output["battery"]["percentage"], 80)

    def test_runtime_adapter_sessions_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = TermuxRuntimeAdapter(tmp)
            session = adapter.start({"taskId": "task-1"})
            self.assertEqual(session.state, "RUNNING")
            adapter.stop(session.sessionId)
            # a fresh adapter instance (process restart) recovers the session
            restarted = TermuxRuntimeAdapter(tmp)
            resumed = restarted.resume(session.sessionId)
            self.assertEqual(resumed.state, "RUNNING")
            self.assertEqual(resumed.taskContext, {"taskId": "task-1"})
            self.assertEqual(resumed.sessionId, session.sessionId)

    def test_runtime_adapter_sends_model_port_events(self):
        from core import ToolCall
        with tempfile.TemporaryDirectory() as tmp:
            port = ScriptedModelPort(turns=[[ToolCall(
                id="tc1", name="fs.read_file",
                arguments={"path": "/data/out/x.txt"})]])
            adapter = TermuxRuntimeAdapter(tmp, port=port)
            session = adapter.start({"taskId": "task-1"})
            events = adapter.send(session.sessionId, "inspect the file")
            self.assertEqual(events[0].type, "tool_calls")
            self.assertEqual(events[0].payload["toolCalls"][0]["name"],
                             "fs.read_file")

    def test_tampered_session_fails_closed(self):
        from continuity.task_store import TaskIntegrityError
        with tempfile.TemporaryDirectory() as tmp:
            adapter = TermuxRuntimeAdapter(tmp)
            session = adapter.start({"taskId": "task-1"})
            path = Path(tmp) / "sessions" / f"{session.sessionId}.json"
            data = json.loads(path.read_text())
            data["payload"]["taskContext"] = {"taskId": "forged"}
            path.write_text(json.dumps(data))
            with self.assertRaises(TaskIntegrityError):
                adapter.resume(session.sessionId)


if __name__ == "__main__":
    unittest.main()
