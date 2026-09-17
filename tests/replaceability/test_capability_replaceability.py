"""The governed outcome must not depend on the transport (Phase 14 WS10).

The capability channel has two implementations of one contract: the
reference server (tests/support/capability_server.py, HTTP over loopback)
and the local bridge below, which reaches the same device object in process.
In both runs the adapter, Policy, the pipeline, verification and completion
are the same objects - only the far side of the adapter changes.

If a run's governance depended on which one was behind it, the authority
layer would depend on a transport implementation, which is what
PROJECT_CONTRACT s2 forbids. The device itself is a test double either way;
what is compared is what the system *decided*.
"""
from __future__ import annotations

import re
import unittest

from tests.support.capability_harness import CapabilityHarness
from tests.support.capability_server import (
    LAUNCH_PACKAGE,
    CapabilityFailure,
    FakeDevice,
)

from core.enums import TaskState
from platforms.termux import AndroidCapabilityBridge, AndroidBridgeError
from platforms.termux.android_capabilities import TermuxAndroidCapabilityAdapter
from verification import criterion_for

#: The events that record what the system decided and what it observed.
GOVERNANCE_EVENTS = ("POLICY_DECISION", "ACTION_STARTED", "ACTION_TERMINAL",
                     "VERIFICATION_RESULT", "COMPLETION_DECISION")

#: Fields that differ between two runs of the same flow by construction:
#: per-run identifiers, and the hashes and digests taken over records that
#: contain them. What they are hashes *of* is decided by the same engines
#: in both runs, which is what this test is about.
VOLATILE = re.compile(r"(?i)taskid|actionid|reference|digest|hash|timestamp")


class LocalBridge:
    """The capability contract, in process: the same surface, the same
    structured failures, no HTTP. This is a second implementation of the
    transport, not a replacement for the device."""

    def __init__(self, device: FakeDevice):
        self.device = device

    def state(self) -> dict:
        return self.device.state_payload()

    def observe(self, operation: str, arguments: dict = None) -> dict:
        return self.execute(operation, arguments)

    def action(self, operation: str, arguments: dict = None) -> dict:
        return self.execute(operation, arguments)

    def execute(self, operation: str, arguments: dict = None) -> dict:
        try:
            return self.device.execute(operation, dict(arguments or {}))
        except CapabilityFailure as failure:
            raise AndroidBridgeError(failure.code, failure.message) from None


def governance_trace(store, task_id: str) -> list:
    """What the authority layer decided about a run, without the per-run
    identifiers: the shape two implementations must agree on."""
    trace = []
    for record in store.journal_for(task_id).records():
        if record["eventType"] not in GOVERNANCE_EVENTS:
            continue
        payload = record["payload"]
        trace.append((record["eventType"], tuple(sorted(
            (key, str(value)) for key, value in payload.items()
            if not VOLATILE.search(key) and not key.endswith("At")))))
    return trace


class CapabilityTransportReplaceabilityTests(CapabilityHarness):

    def setUp(self):
        super().setUp()
        # the first run goes over the real HTTP client, so "the transport"
        # in this test is the one the production path uses
        self.assertIsInstance(self.bridge, AndroidCapabilityBridge)

    def run_launch_flow(self, bridge) -> list:
        """One governed task - launch Settings, verified by a code assessor
        reading the device - over the given transport."""
        self.pipeline.tools = {
            **self.fs_tools,
            **TermuxAndroidCapabilityAdapter(bridge, self.observations).tools()}
        task = self.create_task([criterion_for("ui-foreground-package-is",
                                               "settings is open",
                                               package="com.android.settings")])
        self.script(self.call(LAUNCH_PACKAGE, package="com.android.settings"))
        launches_before = self.device.count(LAUNCH_PACKAGE)

        final = self.drive(task)

        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.device.count(LAUNCH_PACKAGE) - launches_before, 1)
        return governance_trace(self.store, task.id)

    def test_the_governed_outcome_does_not_depend_on_the_transport(self):
        over_http = self.run_launch_flow(self.bridge)
        in_process = self.run_launch_flow(LocalBridge(self.device))

        self.assertTrue(over_http)
        self.assertEqual(in_process, over_http)

    def test_both_transports_refuse_the_same_things(self):
        # a refusal is part of the contract too: the same code, from both
        for bridge in (self.bridge, LocalBridge(self.device)):
            with self.assertRaises(AndroidBridgeError) as caught:
                bridge.execute("android.shell", {"command": "id"})
            self.assertEqual(caught.exception.code, "OPERATION_NOT_SUPPORTED")
        self.device.fail(LAUNCH_PACKAGE, "CAPABILITY_UNAVAILABLE", times=2)
        for bridge in (self.bridge, LocalBridge(self.device)):
            with self.assertRaises(AndroidBridgeError) as caught:
                bridge.action(LAUNCH_PACKAGE, {"package": "com.android.settings"})
            self.assertEqual(caught.exception.code, "CAPABILITY_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
