"""The governed loop over a real capability channel (Phase 14 WS2/WS10).

Everything here runs over HTTP against the reference capability server
(tests/support/capability_server.py), through the real bridge client, the
real adapter, real Policy, the real pipeline and the real verification
methods. Only the model is scripted, and only at the ModelPort seam; the
wiring lives in tests/support/capability_harness.py.

What it pins:
- an observation reaches the next model request, and only the newest one
  claims to describe the device as it is now;
- a screenshot is stored content-addressed and described by the vision
  capability, which never acts;
- a device action authorized by Policy runs and its result is verified by
  code, then completed by the Completion Engine;
- an ASK-gated action pauses, survives a decision, and completes on resume;
- out-of-scope targets, a wrong token and an unavailable capability all
  fail closed and never reach the device.
"""
from __future__ import annotations

import json
import unittest
import urllib.request
from pathlib import Path

from tests.support.capability_harness import (
    CapabilityHarness,
    NODE_TARGET,
    TASK_TEXT,
)
from tests.support.capability_server import (
    LAUNCH_PACKAGE,
    OBSERVE_UI,
    SCREENSHOT,
    TYPE_TEXT,
)

from continuity.observation_store import KIND_ACTION, KIND_VISION
from core import SuccessCriterion
from core.enums import SideEffectState, TaskState, VerificationStatus
from orchestration.orchestrator import DEFAULT_STALL_LIMIT
from platforms.termux import (
    AndroidBridgeError,
    AndroidCapabilityBridge,
    TermuxAndroidCapabilityAdapter,
)
from verification import criterion_for


class CapabilityLoopTests(CapabilityHarness):

    def test_a_ui_observation_reaches_the_next_model_request(self):
        write = f"{self.workspace}/note.txt"
        self.script(self.call(OBSERVE_UI),
                    self.call("fs.write_file", path=write, content="observed"))
        task = self.create_task([criterion_for("file-content-equals", "note",
                                               path=write, expected="observed")])

        final = self.drive(task)

        self.assertIs(final.state, TaskState.DONE)
        second = self.port.requests[1].messages[-1]["content"]
        self.assertIn("com.termux", second)
        self.assertIn("Network & internet", second)
        self.assertIn("[current]", second)

    def test_an_earlier_observation_is_replayed_as_superseded(self):
        never = f"{self.workspace}/never-written.txt"
        self.script(self.call(OBSERVE_UI), self.call(OBSERVE_UI),
                    self.call(OBSERVE_UI))
        task = self.create_task([criterion_for("file-content-equals", "note",
                                               path=never, expected="never")])

        self.drive(task)

        third = self.port.requests[2].messages[-1]["content"]
        self.assertIn("[stale]", third)
        self.assertIn("[current]", third)

    # -- vision ----------------------------------------------------------------

    def test_a_screenshot_is_stored_and_described_but_never_acts(self):
        write = f"{self.workspace}/note.txt"
        self.script(self.call(SCREENSHOT),
                    self.call("fs.write_file", path=write, content="described"))
        task = self.create_task([criterion_for("file-content-equals", "note",
                                               path=write, expected="described")])

        self.drive(task)

        artifacts = sorted((Path(self.store.root) / task.id / "artifacts").glob("*.png"))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].read_bytes(), self.device.image)
        self.assertTrue(self.vision.calls)
        # the description is an observation, and it reaches the next request
        self.assertIn(KIND_VISION, self.kinds(task.id))
        self.assertEqual(len(self.port.requests), 2)
        self.assertIn(self.vision.description, self.port.requests[1].messages[-1]["content"])
        # ...and the described bytes are the bytes the device produced
        length, prompt = self.vision.calls[0]
        self.assertEqual(length, len(self.device.image))
        self.assertTrue(prompt)

    # -- acting and completing ------------------------------------------------

    def test_a_launch_authorized_by_policy_is_verified_and_completes(self):
        self.script(self.call(LAUNCH_PACKAGE, package="com.android.settings"))
        task = self.create_task([criterion_for(
            "ui-foreground-package-is", "settings is open",
            package="com.android.settings")])

        final = self.drive(task)

        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.device.count(LAUNCH_PACKAGE), 1)
        # the verdict came from a code assessor reading a real observation
        self.assertEqual(self.device.count(OBSERVE_UI), 1)
        self.assertIn(KIND_ACTION, self.kinds(task.id))

    def test_an_unauthorized_package_is_denied_and_never_reaches_the_device(self):
        self.script(self.call(LAUNCH_PACKAGE, package="com.evil.app"),
                    self.call(LAUNCH_PACKAGE, package="com.evil.app"),
                    self.call(LAUNCH_PACKAGE, package="com.evil.app"),
                    self.call(LAUNCH_PACKAGE, package="com.evil.app"))
        task = self.create_task([criterion_for(
            "ui-foreground-package-is", "the other app is open",
            package="com.evil.app")])

        final = self.drive(task)

        self.assertNotEqual(final.state, TaskState.DONE)
        self.assertEqual(self.device.asked(LAUNCH_PACKAGE), 0)
        denied = [record for record in self.store.journal_for(task.id).records()
                  if record["eventType"] == "POLICY_DECISION"
                  and record["payload"]["decision"] == "DENY"]
        self.assertTrue(denied)
        self.assertEqual(denied[0]["payload"]["operationId"], LAUNCH_PACKAGE)

    def test_a_wrong_token_is_refused_and_nothing_runs(self):
        stranger = AndroidCapabilityBridge(base_url=self.bridge.base_url,
                                           token="not-the-token", timeout=10)
        tools = TermuxAndroidCapabilityAdapter(stranger, self.observations).tools()

        result = tools[OBSERVE_UI].execute({}, {"taskId": "t1"})

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "ANDROID_UNAUTHORIZED")
        self.assertFalse(result.error.retryable)
        self.assertIs(result.sideEffectState, SideEffectState.KNOWN_FAILED)
        self.assertEqual(self.device.operations, [])

    def test_an_unavailable_capability_is_bounded_and_blocks(self):
        # The device refuses every attempt; the criterion is local, so the
        # only device traffic here is what the loop itself proposed.
        self.device.fail(OBSERVE_UI, "CAPABILITY_UNAVAILABLE", times=20)
        self.script(*[self.call(OBSERVE_UI) for _ in range(8)])
        never = f"{self.workspace}/never-written.txt"
        task = self.create_task([criterion_for("file-content-equals", "note",
                                               path=never, expected="never")])

        # an iteration budget large enough that the no-progress rule, not
        # the iteration limit, is what stops the run
        final = self.drive(task, max_iterations=40)

        self.assertIs(final.state, TaskState.BLOCKED)
        # bounded: the loop stopped on its own no-progress rule, not by
        # running out of iterations or of scripted turns - and it stopped
        # on the repeated turn, before asking the device a fourth time
        self.assertEqual(len(self.port.requests), DEFAULT_STALL_LIMIT + 1)
        self.assertEqual(self.device.asked(OBSERVE_UI), DEFAULT_STALL_LIMIT)
        self.assertEqual(self.device.count(OBSERVE_UI), 0)
        # the refusal was recorded as retryable, and recorded as a failure
        self.assertTrue(any(record["data"].get("error", {}).get("retryable")
                            for record in self.observations.all_records(task.id)))
        reasons = [decision.rationale for decision in final.decisions
                   if decision.decision == "loop_stopped"]
        self.assertTrue(any("no progress" in reason for reason in reasons))

    # -- approval ------------------------------------------------------------

    def test_an_ask_gated_action_pauses_and_completes_on_resume(self):
        self.script(self.call(TYPE_TEXT, observationId="snap-1", nodeRef="n0",
                              target=NODE_TARGET, text=TASK_TEXT),
                    self.call(TYPE_TEXT, observationId="snap-1", nodeRef="n0",
                              target=NODE_TARGET, text=TASK_TEXT))
        task = self.create_task([criterion_for("ui-node-text-present", "text",
                                               text=TASK_TEXT)])

        paused = self.drive(task)

        self.assertIs(paused.state, TaskState.WAITING_USER)
        self.assertEqual(self.device.count(TYPE_TEXT), 0)
        pending = self.approvals.pending(task.id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["operation"], TYPE_TEXT)
        self.assertEqual(pending[0]["arguments"]["text"], TASK_TEXT)

        self.approvals.grant(task.id, pending[0]["approvalReference"],
                             approved_by="operator")
        self.mgr.transition(task.id, TaskState.RECOVERING,
                            authorization={"kind": "USER", "actor": "operator"})
        final = self.drive(task)

        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.device.count(TYPE_TEXT), 1)
        self.assertEqual(self.device.texts()[0], TASK_TEXT)
        self.assertIsNotNone(
            self.approvals.get(task.id, pending[0]["approvalReference"])["consumedAt"])

    def test_a_denied_approval_runs_nothing(self):
        write = f"{self.workspace}/note.txt"
        self.script(self.call(TYPE_TEXT, observationId="snap-1", nodeRef="n0",
                              target=NODE_TARGET, text=TASK_TEXT),
                    self.call("fs.write_file", path=write, content="after denial"))
        task = self.create_task([criterion_for("file-content-equals", "note",
                                               path=write, expected="after denial")])

        paused = self.drive(task)
        pending = self.approvals.pending(task.id)[0]
        self.approvals.deny(task.id, pending["approvalReference"],
                            denied_by="operator", reason="not on this screen")

        self.assertIs(paused.state, TaskState.WAITING_USER)
        self.assertEqual(self.device.count(TYPE_TEXT), 0)

    # -- the channel itself --------------------------------------------------

    def test_the_channel_exposes_named_operations_and_no_shell(self):
        tools = TermuxAndroidCapabilityAdapter(self.bridge, self.observations).tools()

        self.assertNotIn("shell.exec", tools)
        self.assertNotIn("adb.shell", tools)
        for tool_id, tool in tools.items():
            self.assertTrue(tool_id.startswith(("android.", "accessibility.")))
            self.assertEqual(tool.operationId, tool_id)

    def test_the_device_reports_the_same_capabilities_the_client_requested(self):
        payload = self.bridge.state()

        data = payload["data"]
        self.assertTrue(data["available"])
        self.assertTrue(data["accessibilityConnected"])
        self.assertTrue(data["canTakeScreenshot"])
        self.assertIn(OBSERVE_UI, data["operations"])
        self.assertIn(SCREENSHOT, data["operations"])

    def test_the_client_refuses_an_operation_outside_the_closed_set(self):
        with self.assertRaises(AndroidBridgeError) as caught:
            self.bridge.execute("android.shell", {"command": "id"})

        self.assertEqual(caught.exception.code, "OPERATION_NOT_SUPPORTED")
        self.assertEqual(self.device.asked("android.shell"), 0)

    def test_the_device_refuses_an_operation_outside_the_closed_set(self):
        # ...and a client that asks anyway is refused by the device itself:
        # the channel's operation set is closed on both ends.
        request = urllib.request.Request(
            self.bridge.base_url + "/v1/android/execute",
            data=json.dumps({"operation": "android.shell",
                             "arguments": {"command": "id"}}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer test-token"},
            method="POST")

        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        # a refusal is a structured envelope, not a transport error
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "OPERATION_NOT_SUPPORTED")
        self.assertEqual(self.device.operations, [])

    def test_an_unassessable_criterion_never_completes(self):
        # the criterion declares no expected value, so the code assessor
        # cannot assess it: the result is INCONCLUSIVE and the Completion
        # Engine grants nothing (VERIFICATION s6). A criterion that cannot
        # be assessed must never quietly become a PASS.
        criterion = SuccessCriterion(id="c-unassessable", description="text",
                                     verificationMethod="ui-node-text-present",
                                     evidenceRequirements=[])
        self.script(self.call(OBSERVE_UI))
        task = self.create_task([criterion])

        final = self.drive(task, max_iterations=4)

        self.assertNotEqual(final.state, TaskState.DONE)
        results = [record["payload"]["result"]
                   for record in self.store.journal_for(task.id).records()
                   if record["eventType"] == "VERIFICATION_RESULT"]
        self.assertEqual(results, [VerificationStatus.INCONCLUSIVE.value])


if __name__ == "__main__":
    unittest.main()
