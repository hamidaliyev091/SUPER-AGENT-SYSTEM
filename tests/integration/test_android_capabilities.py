"""Android capability layer (Phase 14 v1 subset): package inspection and
settings read through the policy pipeline, live on-device when the
binaries exist."""
import shutil
import unittest

from tests.support.harness import VerificationTestBase

from core import ActionRequest, ActorIdentity, Target, TargetAuthorizationContext, utcnow_iso
from core.enums import ActorType, TargetType, TaskState
from platforms.termux import TermuxAndroidAdapter


class AndroidCapabilityTests(VerificationTestBase):

    def make_task_with_tac(self, **tac_fields):
        defaults = dict(
            allowedPackages=["com.termux"],
            allowedPackageOperations=["package.list", "package.inspect"],
            allowedAndroidSettings=["global.device_name"],
        )
        defaults.update(tac_fields)
        now = utcnow_iso()
        task = self.make_task(
            stop_at=TaskState.RUNNING,
            targetAuthorizationContext=TargetAuthorizationContext(
                schemaVersion="1.0",
                allowedReadPaths=["/data/out/**"],
                allowedWritePaths=["/data/out/**"],
                createdAt=now, updatedAt=now, **defaults))
        return task

    def request(self, task, tool_id, arguments, target=None):
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId=tool_id, target=target, arguments=arguments,
            reason="android capability test")

    def test_package_list_outside_authorized_packages_denied(self):
        task = self.make_task_with_tac()
        self.tools.update(TermuxAndroidAdapter(
            run_command=lambda argv: "package:com.termux\n").tools())
        result = self.pipeline.execute(self.request(
            task, "package.list", {"package": "com.evil.app"},
            target=Target(type=TargetType.PACKAGE, value="com.evil.app")))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    def test_settings_read_requires_authorized_setting(self):
        task = self.make_task_with_tac()
        self.tools.update(TermuxAndroidAdapter(
            run_command=lambda argv: "x\n").tools())
        result = self.pipeline.execute(self.request(
            task, "settings.read",
            {"namespace": "global", "setting": "adb_enabled"},
            target=Target(type=TargetType.ANDROID_SETTING, value="global.adb_enabled")))
        self.assertFalse(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "DENY")

    @unittest.skipUnless(shutil.which("pm"), "pm binary not available")
    def test_live_package_list_through_pipeline(self):
        task = self.make_task_with_tac()
        self.tools.update(TermuxAndroidAdapter().tools())
        result = self.pipeline.execute(self.request(
            task, "package.list", {"package": "com.termux"},
            target=Target(type=TargetType.PACKAGE, value="com.termux")))
        self.assertTrue(result.executed, result.policyDecision.reason)
        self.assertIn("com.termux", result.toolResult.output["packages"])

    @unittest.skipUnless(shutil.which("settings"), "settings binary not available")
    def test_live_settings_read_through_pipeline(self):
        """Device-agnostic: a working read reports the value; a device that
        denies the shell the settings permission surfaces a clean
        ANDROID_OPERATION_FAILED - never a crash."""
        task = self.make_task_with_tac()
        self.tools.update(TermuxAndroidAdapter().tools())
        result = self.pipeline.execute(self.request(
            task, "settings.read",
            {"namespace": "global", "setting": "device_name"},
            target=Target(type=TargetType.ANDROID_SETTING, value="global.device_name")))
        self.assertTrue(result.executed, result.policyDecision.reason)
        if result.toolResult.success:
            self.assertIn("value", result.toolResult.output)
        else:
            self.assertEqual(result.toolResult.error.code,
                             "ANDROID_OPERATION_FAILED")

    def test_invalid_namespace_rejected_by_tool(self):
        adapter = TermuxAndroidAdapter(run_command=lambda argv: "x")
        result = adapter.tools()["settings.read"].execute(
            {"namespace": "evil; rm -rf /", "setting": "x"}, {})
        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "ANDROID_OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
