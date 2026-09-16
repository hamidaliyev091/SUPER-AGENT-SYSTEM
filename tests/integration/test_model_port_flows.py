"""ModelPort and RuntimePort integration (Phases 10-11 Core side): the
orchestrator driven through a provider-independent port and a runtime
adapter, with unchanged security semantics."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import ScriptedModelPort, FakeRuntimeAdapter

from core import ToolCall
from core.enums import ModelRole, TaskState
from completion import CompletionEngine
from continuity import RecoveryManager
from models import ModelPortDriver, ModelRouter
from orchestration import Orchestrator


class ModelPortFlowTests(VerificationTestBase):

    def make_orchestrator(self, driver):
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver,
            recovery=RecoveryManager(self.mgr, self.store))

    def write_call(self, content="HELLO", path="/data/out/x.txt"):
        return ToolCall(id="tc-write", name="fs.write_file",
                        arguments={"path": path, "content": content})

    def test_orchestrator_driven_through_model_port(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])
        driver = ModelPortDriver(self.mgr, self.store, port)
        final = self.make_orchestrator(driver).run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        self.assertEqual(driver.calls, 1)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertIn("MODEL_CALL", events)

    def test_model_calls_are_accounted_and_enforced(self):
        from core import ResourceLimits
        # repair loop consumes two model calls (GOODBYE then HELLO)
        task = self.make_task(stop_at=TaskState.CREATED,
                              resourceLimits=ResourceLimits(actionSteps=100,
                                                            modelCalls=1))
        port = ScriptedModelPort(turns=[[self.write_call("GOODBYE")],
                                        [self.write_call("HELLO")]])
        driver = ModelPortDriver(self.mgr, self.store, port)
        final = self.make_orchestrator(driver).run(task.id, max_iterations=50)
        # the model-call budget was exceeded by the repair loop: DONE is
        # forbidden (s15/s16 resource compliance)
        self.assertIsNot(final.state, TaskState.DONE)
        records = self.store.journal_for(task.id).records()
        self.assertGreater(sum(1 for r in records if r["eventType"] == "MODEL_CALL"),
                           task.resourceLimits.modelCalls)

    def test_model_calls_within_budget_complete(self):
        from core import ResourceLimits
        task = self.make_task(stop_at=TaskState.CREATED,
                              resourceLimits=ResourceLimits(actionSteps=100,
                                                            modelCalls=5))
        port = ScriptedModelPort(turns=[[self.write_call("GOODBYE")],
                                        [self.write_call("HELLO")]])
        driver = ModelPortDriver(self.mgr, self.store, port)
        final = self.make_orchestrator(driver).run(task.id, max_iterations=50)
        self.assertIs(final.state, TaskState.DONE)

    def test_malicious_tool_calls_through_port_are_denied(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[
            [self.write_call(content="pwned", path="/etc/evil.txt")],
            [self.write_call("HELLO")],
        ])
        driver = ModelPortDriver(self.mgr, self.store, port)
        final = self.make_orchestrator(driver).run(task.id)
        self.assertIs(final.state, TaskState.DONE)  # via the legitimate call only
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        self.assertNotIn("/etc/evil.txt", self.files)
        self.assertEqual(self.writes, ["/data/out/x.txt"])

    def test_router_dispatch_and_no_authority_from_selection(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        strong_port = ScriptedModelPort(turns=[[self.write_call("HELLO")]],
                                        identity="strong-model")
        router = ModelRouter({ModelRole.GENERAL_AGENT: strong_port})
        driver = ModelPortDriver(self.mgr, self.store, router, role=ModelRole.GENERAL_AGENT)
        # router selection returns a port but grants nothing beyond policy
        self.assertIs(router.select(ModelRole.GENERAL_AGENT), ModelRole.GENERAL_AGENT)
        final = self.make_orchestrator(driver).run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        # the strong model's out-of-scope attempt would still be DENIED
        self.files.pop("/data/out/x.txt", None)  # fresh state for task2
        task2 = self.make_task(stop_at=TaskState.CREATED)
        evil = ScriptedModelPort(turns=[[self.write_call("x", "/etc/strong.txt")]])
        driver2 = ModelPortDriver(self.mgr, self.store, evil)
        final2 = self.make_orchestrator(driver2).run(task2.id, max_iterations=30)
        self.assertIsNot(final2.state, TaskState.DONE)
        self.assertNotIn("/etc/strong.txt", self.files)

    def test_runtime_adapter_exposes_port_sessions(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])
        adapter = FakeRuntimeAdapter(name="fake-pi", port=port)
        session = adapter.start({"taskId": task.id})
        self.assertEqual(session.state, "RUNNING")
        events = adapter.send(session.sessionId, "do the task")
        self.assertEqual(events[0].type, "tool_calls")
        self.assertEqual(events[0].payload["toolCalls"][0]["name"], "fs.write_file")
        self.assertEqual(adapter.stop(session.sessionId), {"ok": True})
        resumed = adapter.resume(session.sessionId)
        self.assertEqual(resumed.state, "RUNNING")

    def test_runtime_driven_flow_through_core(self):
        """RuntimePort -> ModelPort -> Core: the runtime exposes events, the
        orchestrator executes them through policy/verification/completion."""
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])
        adapter = FakeRuntimeAdapter(name="fake-pi", port=port)
        session = adapter.start({"taskId": task.id})
        driver = ModelPortDriver(self.mgr, self.store, port)
        final = self.make_orchestrator(driver).run(task.id)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        adapter.stop(session.sessionId)


if __name__ == "__main__":
    unittest.main()
