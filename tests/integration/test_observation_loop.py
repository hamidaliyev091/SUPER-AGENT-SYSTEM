"""Observation loop (Phase 14 WS5): what the system did is recorded
durably and replayed into the next model request.

The property under test is the one that makes a governed agent real: the
model reasons about observed state, not about what it assumed. The tests
also pin the boundaries - an observation is data, the model cannot write
one, and a tool cannot make the driver read a file outside the task's own
artifact directory.
"""
import unittest
from types import SimpleNamespace

from tests.support.harness import VerificationTestBase
from tests.support.fakes import ScriptedModelPort

from core import ActionRequest, ActorIdentity, Tool, ToolCall, ToolResult, utcnow_iso
from core.enums import (
    ActorType,
    Idempotency,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TaskState,
)
from completion import CompletionEngine
from continuity import ObservationStore, RecoveryManager
from models import ModelPortDriver
from orchestration import Orchestrator

#: A real 1x1 PNG, so an artifact here is an image and not a stand-in.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


class StubVision:
    """A vision capability double: reads bytes, returns text, nothing else."""

    def __init__(self, description="Settings is open, showing a search field."):
        self.description = description
        self.calls = []

    def describe_image(self, image_bytes, prompt):
        self.calls.append((image_bytes, prompt))
        return self.description


def capability_result(tool_id, tool_result, action_id="act-1"):
    """The slice of an ExecutionResult the driver reads."""
    return SimpleNamespace(actionId=action_id,
                           actionRequest=SimpleNamespace(toolId=tool_id),
                           toolResult=tool_result)


class ObservationLoopTests(VerificationTestBase):
    """Base wiring plus the observation-capable driver."""

    def make_orchestrator(self, driver):
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver,
            recovery=RecoveryManager(self.mgr, self.store))

    def driver(self, port, **kwargs):
        return ModelPortDriver(self.mgr, self.store, port,
                               observations=ObservationStore(self.root), **kwargs)

    def write_call(self, content="HELLO", path="/data/out/x.txt"):
        return ToolCall(id="tc-write", name="fs.write_file",
                        arguments={"path": path, "content": content})

    def screenshot_request(self, task):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="android.screenshot", target=None, arguments={},
            reason="observe the screen")

    def screenshot_tool(self, artifacts):
        """A capability tool whose result carries a content-addressed
        artifact, exactly as the real adapter's does."""

        def capture(args, context):
            descriptor = artifacts.write_artifact(context["taskId"], PNG, ".png")
            return ToolResult(
                success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                timestamp=utcnow_iso(),
                output={"artifact": {"kind": "image", **descriptor}})

        self.tools["android.screenshot"] = Tool(
            id="android.screenshot", name="android.screenshot", description="capture",
            inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
            sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
            idempotency=Idempotency.IDEMPOTENT, execute=capture)
        return self.tools["android.screenshot"]

    # -- replay ---------------------------------------------------------------

    def test_second_request_carries_the_first_observation(self):
        # the first action succeeds but does not satisfy the criterion, so
        # the loop asks the model again - and this time it can see what the
        # first action actually did
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("GOODBYE")],
                                        [self.write_call("HELLO")]])
        driver = self.driver(port)

        final = self.make_orchestrator(driver).run(task.id, max_iterations=30)

        self.assertIs(final.state, TaskState.DONE)
        self.assertGreaterEqual(len(port.requests), 2)
        first = port.requests[0].messages[0]["content"]
        second = port.requests[1].messages[0]["content"]
        self.assertNotIn("Recent observations", first)
        self.assertIn("Task objective: write a file", second)
        self.assertIn("Recent observations", second)
        self.assertIn("fs.write_file succeeded", second)
        self.assertIn("[current]", second)

    def test_observation_records_the_artifact_and_its_digest(self):
        store = ObservationStore(self.root)
        self.screenshot_tool(store)
        task = self.make_task(stop_at=TaskState.VERIFYING)
        driver = self.driver(ScriptedModelPort(turns=[]))

        driver.observe(task, self.pipeline.execute(self.screenshot_request(task)))

        records = store.all_records(task.id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "action")
        self.assertTrue(records[0]["summary"].startswith(
            "android.screenshot succeeded"))
        artifact = records[0]["artifact"]
        self.assertEqual(artifact["bytes"], len(PNG))
        self.assertEqual(store.read_artifact(task.id, artifact["sha256"]), PNG)
        # the image itself is never inlined into the record
        self.assertNotIn("imageBase64", str(records[0]))

    # -- boundaries -----------------------------------------------------------

    def test_denied_proposal_is_observed_as_not_executed(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("pwned", "/etc/evil.txt")]])
        driver = self.driver(port)

        self.make_orchestrator(driver).run(task.id, max_iterations=6)

        records = ObservationStore(self.root).all_records(task.id)
        self.assertTrue(records)
        self.assertIn("was not executed", records[0]["summary"])
        self.assertIn("DENY", records[0]["summary"])
        self.assertFalse(records[0]["data"]["executed"])
        self.assertNotIn("/etc/evil.txt", self.files)

    def test_tool_cannot_point_the_driver_at_a_foreign_file(self):
        store = ObservationStore(self.root)
        task = self.make_task(stop_at=TaskState.CREATED)
        vision = StubVision()
        driver = self.driver(ScriptedModelPort(turns=[]), vision=vision)

        driver.observe(task, capability_result("android.screenshot", ToolResult(
            success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
            timestamp=utcnow_iso(),
            output={"artifact": {"kind": "image", "path": "/etc/passwd",
                                 "sha256": "0" * 64, "bytes": 10}})))

        record = store.latest(task.id)
        self.assertIsNone(record["artifact"])   # foreign path ignored
        self.assertEqual(vision.calls, [])      # and never read

    def test_vision_describes_an_image_artifact_and_only_describes(self):
        store = ObservationStore(self.root)
        task = self.make_task(stop_at=TaskState.CREATED)
        descriptor = store.write_artifact(task.id, PNG, ".png")
        vision = StubVision()
        driver = self.driver(ScriptedModelPort(turns=[]), vision=vision)

        driver.observe(task, capability_result("android.screenshot", ToolResult(
            success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
            timestamp=utcnow_iso(),
            output={"artifact": {"kind": "image", **descriptor}})))

        records = store.all_records(task.id)
        self.assertEqual([r["kind"] for r in records], ["action", "vision.description"])
        self.assertEqual(records[1]["data"]["description"], vision.description)
        self.assertEqual(len(vision.calls), 1)
        self.assertEqual(vision.calls[0][0], PNG)
        # the description is text the model reads; it carries no decision
        self.assertNotIn("decision", records[1]["data"])

    def test_a_failing_vision_capability_does_not_break_the_loop(self):
        store = ObservationStore(self.root)
        task = self.make_task(stop_at=TaskState.CREATED)
        descriptor = store.write_artifact(task.id, PNG, ".png")

        class ExplodingVision:
            @staticmethod
            def describe_image(image_bytes, prompt):
                raise RuntimeError("vlm bridge down")

        driver = self.driver(ScriptedModelPort(turns=[]), vision=ExplodingVision())

        driver.observe(task, capability_result("android.screenshot", ToolResult(
            success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
            timestamp=utcnow_iso(),
            output={"artifact": {"kind": "image", **descriptor}})))

        self.assertEqual([r["kind"] for r in store.all_records(task.id)], ["action"])
        self.assertTrue(any("vision" in error for error in driver.errors))

    # -- request budget -------------------------------------------------------

    def test_observation_block_keeps_the_newest_and_drops_the_oldest(self):
        store = ObservationStore(self.root)
        task = self.make_task(stop_at=TaskState.CREATED)
        for index in range(4):
            store.record(task.id, "action", f"step-{index} " + "x" * 700,
                         {"index": index})
        driver = self.driver(ScriptedModelPort(turns=[]))

        block = driver.render_observations(task)

        self.assertIn("step-3", block)          # newest always present
        self.assertNotIn("step-0", block)       # oldest dropped, not truncated
        self.assertIn("[current]", block)
        self.assertLess(len(block), 3000)

    def test_no_observations_means_no_block(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        driver = self.driver(ScriptedModelPort(turns=[]))
        self.assertEqual(driver.render_observations(task), "")

    def test_token_budget_stops_model_calls(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")],
                                        [self.write_call("HELLO")]],
                                 usage={"total_tokens": 60})
        driver = self.driver(port, token_budget=100)

        first = driver.generate(task)
        self.assertEqual(first.finishReason, "tool_calls")
        self.assertEqual(driver.tokens_used(task), 60)
        self.assertIsNone(driver.exhausted(task))   # 60 of 100 spent

        second = driver.generate(task)
        self.assertEqual(second.finishReason, "tool_calls")

        third = driver.generate(task)               # 120 of 100 spent

        self.assertEqual(third.finishReason, "token_budget_exhausted")
        self.assertEqual(third.toolCalls, [])
        self.assertIsNotNone(driver.exhausted(task))
        self.assertEqual(driver.tokens_used(task), 120)  # no call was made
        self.assertEqual(len(port.requests), 2)
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertEqual(events.count("MODEL_CALL"), 2)

    def test_no_token_budget_means_no_limit(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[], usage={"total_tokens": 10 ** 9})
        driver = self.driver(port)
        driver.generate(task)
        self.assertIsNone(driver.exhausted(task))


if __name__ == "__main__":
    unittest.main()
