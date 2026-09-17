"""Production verification methods (Phase 14 WS8): code assessors over real
device observations.

A verification method is the only thing standing between "the action ran"
and "the task is done", so each assessor is pinned on both sides: it PASSes
on the state the criterion asked for, and it never PASSes on anything else -
a missing requirement, a missing field, or an observation of the wrong kind
is INCONCLUSIVE or FAIL, never a pass by absence.
"""
import unittest

from verification import default_methods, parse_requirements
from verification.android_methods import criterion_for

from core import SuccessCriterion, ToolResult, utcnow_iso
from core.enums import SideEffectState, VerificationStatus


def observation(output):
    return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                      timestamp=utcnow_iso(), output=output)


def ui(foreground="com.android.settings", previous="com.termux", texts=()):
    return observation({
        "snapshotId": "snap-1", "foregroundPackage": foreground,
        "previousForegroundPackage": previous, "nodeCount": len(texts),
        "nodes": [{"ref": f"n{i}", "text": text, "clickable": True}
                  for i, text in enumerate(texts)]})


def screenshot(sha="a" * 64, size=2048, kind="image"):
    return observation({"artifact": {"kind": kind, "sha256": sha, "bytes": size},
                        "capturedAt": "now"})


class MethodRegistryTests(unittest.TestCase):

    def test_every_method_declares_a_tool_and_an_assessor(self):
        for name, spec in default_methods().items():
            self.assertEqual(spec.method, name)
            self.assertTrue(spec.toolId)
            self.assertTrue(callable(spec.assessor))

    def test_requirements_are_parsed_from_key_value_entries(self):
        criterion = criterion_for("ui-foreground-package-is", "settings is open",
                                  package="com.android.settings")
        self.assertEqual(criterion.evidenceRequirements,
                         ["package=com.android.settings"])
        self.assertEqual(parse_requirements(criterion),
                         {"package": "com.android.settings"})

    def test_a_malformed_requirement_is_ignored_not_guessed(self):
        criterion = SuccessCriterion(
            id="c1", description="d", verificationMethod="ui-foreground-package-is",
            evidenceRequirements=["no separator", "=novalue", "package=com.x"])
        self.assertEqual(parse_requirements(criterion), {"package": "com.x"})

    def test_the_read_method_asks_for_the_declared_path(self):
        criterion = criterion_for("file-content-equals", "the file is right",
                                  path="/data/out/x.txt", expected="HELLO")
        spec = default_methods()["file-content-equals"]

        self.assertEqual(spec.build_arguments(None, criterion),
                         {"path": "/data/out/x.txt"})


class FileContentTests(unittest.TestCase):

    assess = staticmethod(default_methods()["file-content-equals"].assessor)

    def criterion(self, **requirements):
        return criterion_for("file-content-equals", "d", **requirements)

    def test_pass_only_on_the_exact_expected_content(self):
        result = observation({"path": "/data/out/x.txt", "content": "HELLO",
                              "exists": True})
        status, _ = self.assess(result, None, self.criterion(expected="HELLO"))
        self.assertIs(status, VerificationStatus.PASS)

    def test_fail_when_the_content_differs(self):
        result = observation({"path": "/data/out/x.txt", "content": "HELLO\n",
                              "exists": True})
        status, _ = self.assess(result, None, self.criterion(expected="HELLO"))
        self.assertIs(status, VerificationStatus.FAIL)

    def test_inconclusive_when_the_criterion_states_no_expected_value(self):
        result = observation({"path": "/data/out/x.txt", "content": "HELLO"})
        status, reason = self.assess(result, None, self.criterion())
        self.assertIs(status, VerificationStatus.INCONCLUSIVE)
        self.assertIn("expected", reason)

    def test_inconclusive_when_nothing_was_read(self):
        status, _ = self.assess(None, None, self.criterion(expected="HELLO"))
        self.assertIs(status, VerificationStatus.INCONCLUSIVE)


class ForegroundPackageTests(unittest.TestCase):

    assess = staticmethod(default_methods()["ui-foreground-package-is"].assessor)

    def test_pass_when_the_expected_app_is_in_front(self):
        criterion = criterion_for("ui-foreground-package-is", "d",
                                  package="com.android.settings")
        status, _ = self.assess(ui(), None, criterion)
        self.assertIs(status, VerificationStatus.PASS)

    def test_fail_when_another_app_is_in_front(self):
        criterion = criterion_for("ui-foreground-package-is", "d",
                                  package="com.android.settings")
        status, reason = self.assess(ui(foreground="com.termux"), None, criterion)
        self.assertIs(status, VerificationStatus.FAIL)
        self.assertIn("com.termux", reason)

    def test_inconclusive_without_a_declared_package(self):
        status, _ = self.assess(ui(), None,
                                criterion_for("ui-foreground-package-is", "d"))
        self.assertIs(status, VerificationStatus.INCONCLUSIVE)

    def test_inconclusive_when_the_observation_has_no_foreground_field(self):
        criterion = criterion_for("ui-foreground-package-is", "d", package="com.x")
        status, _ = self.assess(observation({"nodes": []}), None, criterion)
        self.assertIs(status, VerificationStatus.INCONCLUSIVE)

    def test_a_failed_collection_is_never_a_pass(self):
        failed = ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                            timestamp=utcnow_iso(), output={})
        criterion = criterion_for("ui-foreground-package-is", "d", package="com.x")
        status, _ = self.assess(failed, None, criterion)
        self.assertIsNot(status, VerificationStatus.PASS)


class NodeTextTests(unittest.TestCase):

    assess = staticmethod(default_methods()["ui-node-text-present"].assessor)

    def test_pass_when_any_visible_node_carries_the_text(self):
        criterion = criterion_for("ui-node-text-present", "d", text="Battery")
        status, _ = self.assess(ui(texts=["Settings", "Battery 78%"]), None, criterion)
        self.assertIs(status, VerificationStatus.PASS)

    def test_fail_when_no_node_carries_it(self):
        criterion = criterion_for("ui-node-text-present", "d", text="Battery")
        status, reason = self.assess(ui(texts=["Settings", "Network"]), None, criterion)
        self.assertIs(status, VerificationStatus.FAIL)
        self.assertIn("2 nodes", reason)

    def test_inconclusive_without_nodes(self):
        criterion = criterion_for("ui-node-text-present", "d", text="Battery")
        status, _ = self.assess(observation({"foregroundPackage": "com.x"}), None,
                                criterion)
        self.assertIs(status, VerificationStatus.INCONCLUSIVE)

    def test_matching_is_on_text_not_on_the_whole_record(self):
        criterion = criterion_for("ui-node-text-present", "d", text="com.android")
        status, _ = self.assess(ui(foreground="com.android.settings"), None, criterion)
        self.assertIs(status, VerificationStatus.FAIL)


class ForegroundChangedTests(unittest.TestCase):

    assess = staticmethod(default_methods()["foreground-package-changed"].assessor)
    criterion = staticmethod(lambda: criterion_for("foreground-package-changed", "d"))

    def test_pass_when_the_app_changed(self):
        status, _ = self.assess(ui(), None, self.criterion())
        self.assertIs(status, VerificationStatus.PASS)

    def test_fail_when_the_app_did_not_change(self):
        status, _ = self.assess(ui(foreground="com.termux", previous="com.termux"),
                                None, self.criterion())
        self.assertIs(status, VerificationStatus.FAIL)


class ScreenshotTests(unittest.TestCase):

    assess = staticmethod(default_methods()["screenshot-captured"].assessor)
    criterion = staticmethod(lambda: criterion_for("screenshot-captured", "d"))

    def test_pass_on_a_stored_image_artifact(self):
        status, reason = self.assess(screenshot(), None, self.criterion())
        self.assertIs(status, VerificationStatus.PASS)
        self.assertIn("2048", reason)

    def test_fail_without_a_digest(self):
        status, _ = self.assess(screenshot(sha=""), None, self.criterion())
        self.assertIs(status, VerificationStatus.FAIL)

    def test_fail_on_an_empty_artifact(self):
        status, _ = self.assess(screenshot(size=0), None, self.criterion())
        self.assertIs(status, VerificationStatus.FAIL)

    def test_fail_on_a_non_image_artifact(self):
        status, _ = self.assess(screenshot(kind="text"), None, self.criterion())
        self.assertIs(status, VerificationStatus.FAIL)

    def test_fail_when_no_capture_happened(self):
        status, _ = self.assess(observation({"capturedAt": "now"}), None,
                                self.criterion())
        self.assertIs(status, VerificationStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
