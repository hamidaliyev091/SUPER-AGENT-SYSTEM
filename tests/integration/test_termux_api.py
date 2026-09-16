"""Termux:API integration (Phase 13): live battery status through the
pipeline on-device (skipped when the binary is absent), plus the
fail-closed behavior of the notification operation."""
import shutil
import unittest

from tests.support.harness import VerificationTestBase, criterion
from tests.support.fakes import fake_tool

from core import ActionRequest, ActorIdentity, CompletionContract
from core.enums import ActorType, TaskState
from completion import CompletionEngine
from platforms.termux import TermuxEnvironmentAdapter
from verification import VerificationMethodSpec, output_satisfies


class TermuxApiTests(VerificationTestBase):

    def test_notification_operation_is_policy_denied(self):
        """termux_api.send_notification has no complete matrix row (ADR-004):
        registering a tool changes nothing - policy still denies."""
        task = self.make_task(stop_at=TaskState.RUNNING)
        notification_tool, calls = fake_tool("termux_api.send_notification")
        self.tools["termux_api.send_notification"] = notification_tool
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="termux_api.send_notification",
            target=None,
            arguments={"title": "hello", "text": "world"},
            reason="notification attempt")
        result = self.pipeline.execute(request)
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")
        self.assertEqual(len(calls), 0)  # never reached the tool

    @unittest.skipUnless(shutil.which("termux-battery-status"),
                         "termux-api not installed on this device")
    def test_live_battery_status_through_pipeline(self):
        """LIVE device test: real battery status via the Termux:API binary,
        through policy, audit, verification, and completion."""
        self.tools.update(TermuxEnvironmentAdapter().tools())
        criteria = [criterion("c1", method="read-content"),
                    criterion("c2", method="battery-live")]
        methods = dict(self._methods())
        methods["battery-live"] = VerificationMethodSpec(
            method="battery-live", toolId="termux_api.battery_status",
            arguments={},
            assessor=output_satisfies(
                lambda out: isinstance(out.get("percentage"), int),
                "battery percentage reported"))
        self.engine.methods = methods
        self.files["/data/out/x.txt"] = "HELLO"
        task = self.make_task(
            criteria=criteria,
            completionContract=CompletionContract(
                objective="status", successCriteria=criteria))
        report = self.engine.verify(task.id)
        by_id = {r.criterionId: r.result.value for r in report.criterionResults}
        self.assertEqual(by_id["c1"], "PASS")
        self.assertEqual(by_id["c2"], "PASS", by_id)
        decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
        self.assertEqual(decision.decision.value, "DONE")
        battery_events = [r for r in self.store.journal_for(task.id).records()
                          if r["eventType"] == "ACTION_STARTED"
                          and r["payload"].get("operationId") ==
                          "termux_api.battery_status"]
        self.assertEqual(len(battery_events), 1)


if __name__ == "__main__":
    unittest.main()
