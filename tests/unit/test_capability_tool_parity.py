"""The tool table and the policy registry must agree (Phase 14 WS3/WS10).

Two places describe the same operation: POLICY_RULES.md says how it may be
authorized (risk, side effect, reversibility, idempotency, target kind,
required scopes) and the adapter says what it does (the same metadata plus
an implementation). Nothing keeps them in sync by construction, so a drift
would silently give the executor a different classification than the
authorizer believed - and the classifications, not the model, decide what
may happen unattended.

These tests are that check, plus the two edges of the tool table: no
operation reaches the device through a general-purpose shell, and a
registered operation with no implementation is refused rather than tried.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from continuity import ObservationStore
from platforms.termux import AndroidCapabilityBridge, TermuxAndroidCapabilityAdapter
from platforms.termux.android_bridge import CAPABILITY_OPERATIONS
from platforms.termux.filesystem import TermuxFilesystemAdapter
from policy import OPERATIONS

#: A loopback address with nothing listening: the tool table is built and
#: inspected here, never called.
UNUSED_BRIDGE = "http://127.0.0.1:9"


class CapabilityToolParityTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        bridge = AndroidCapabilityBridge(base_url=UNUSED_BRIDGE, token="t",
                                         timeout=1)
        self.tools = TermuxAndroidCapabilityAdapter(
            bridge, ObservationStore(self.root)).tools()

    def test_the_tool_table_covers_the_whole_capability_channel(self):
        # neither direction may drift: every operation the client can send
        # has a tool, and every tool is an operation the device accepts
        self.assertEqual(set(self.tools), set(CAPABILITY_OPERATIONS))

    def test_every_implemented_operation_is_registered(self):
        for tool_id in self.tools:
            self.assertIn(tool_id, OPERATIONS,
                          f"{tool_id} has an implementation but no policy row")

    def test_implemented_operation_metadata_matches_its_policy_row(self):
        for tool_id, tool in self.tools.items():
            rule = OPERATIONS[tool_id]
            self.assertEqual(tool.riskLevel, rule.riskLevel, tool_id)
            self.assertEqual(tool.sideEffect, rule.sideEffect, tool_id)
            self.assertEqual(tool.reversibility, rule.reversibility, tool_id)
            self.assertEqual(tool.idempotency, rule.idempotency, tool_id)
            self.assertEqual(tool.operationId, rule.operationId, tool_id)

    def test_every_tool_declares_the_resource_it_needs(self):
        for tool_id, tool in self.tools.items():
            self.assertTrue(tool.resourceRequirements,
                            f"{tool_id} does not declare its resource needs")

    def test_no_operation_reaches_the_device_through_a_shell(self):
        for tool_id, tool in self.tools.items():
            self.assertNotIn("shell", tool_id)
            self.assertNotIn("exec", tool_id)
            self.assertIsNone(getattr(tool, "shell", None))

    def test_a_registered_operation_without_an_implementation_is_not_offered(self):
        # accessibility.submit is registered (HIGH, forced ASK) and has no
        # implementation: the registry is the authorization surface, not the
        # runtime surface, and the two are allowed to differ - the pipeline
        # refuses what the tool table cannot run.
        self.assertIn("accessibility.submit", OPERATIONS)
        self.assertNotIn("accessibility.submit", self.tools)


class FilesystemToolParityTests(unittest.TestCase):

    def test_filesystem_tool_classifications_match_their_policy_rows(self):
        tools = TermuxFilesystemAdapter(
            protected_mapping={"P0": ("/repo/.supersystem/**",)}).tools()

        self.assertIn("fs.write_file", tools)
        for tool_id, tool in tools.items():
            rule = OPERATIONS[tool_id]
            self.assertEqual(tool.riskLevel, rule.riskLevel, tool_id)
            self.assertEqual(tool.sideEffect, rule.sideEffect, tool_id)

    def test_mutation_tools_require_a_protected_path_mapping(self):
        # without a versioned mapping, filesystem mutation is not offered at
        # all: policy would deny it anyway (P-RULE-51)
        tools = TermuxFilesystemAdapter(protected_mapping=None).tools()

        self.assertNotIn("fs.write_file", tools)


if __name__ == "__main__":
    unittest.main()
