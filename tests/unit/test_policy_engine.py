"""PolicyEngine tests mirroring the P-RULE adversarial list (POLICY_RULES.md s50)."""
import unittest

from policy import (
    Canonicalizer,
    PolicyEngine,
    ProtectedPathRegistry,
    RuntimePathMapping,
)
from policy.registry import OperationRule

from core import (
    ActorIdentity,
    PolicyRequest,
    Target,
    TargetAuthorizationContext,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    EffortLevel,
    Idempotency,
    PermissionMode,
    PolicyDecisionValue,
    Reversibility,
    RiskLevel,
    SideEffect,
    TargetType,
    TaskState,
)


_UNSET = object()


def make_request(operation_id, *, target=None, args=None, mode=PermissionMode.AUTO,
                 state=TaskState.RUNNING, tac=_UNSET, env=None, req_risk=None,
                 policy_version="0.6", rule_version="1.0", task_id="t1"):
    now = utcnow_iso()
    if tac is _UNSET:
        tac = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now)
    return PolicyRequest(
        policyVersion=policy_version,
        ruleVersion=rule_version,
        taskId=task_id,
        taskState=state,
        permissionMode=mode,
        effortLevel=EffortLevel.STANDARD,
        actorContext=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT),
        environmentContext=env or {},
        authorizationContext={},
        targetAuthorizationContext=tac,
        toolId=operation_id,
        operationId=operation_id,
        target=target,
        structuredArguments=args or {},
        requestedRiskLevel=req_risk,
    )


def make_tac(**overrides):
    now = utcnow_iso()
    defaults = dict(
        schemaVersion="1.0",
        allowedReadPaths=["/data/**"],
        allowedWritePaths=["/data/out/**"],
        allowedSearchPaths=["/data/**"],
        allowedPackages=["com.example.app"],
        allowedPackageOperations=["package.inspect", "package.install"],
        allowedNetworkDomains=["example.com"],
        allowedNetworkDestinations=["1.2.3.4"],
        allowedUIActions=["ok_button", "submit_button"],
        allowedProcesses=["com.example.*"],
        allowedAndroidSettings=["system.screen_brightness"],
        createdAt=now, updatedAt=now,
    )
    defaults.update(overrides)
    return TargetAuthorizationContext(**defaults)


def fs_target(path):
    return Target(type=TargetType.FILESYSTEM, value=path)


class PolicyEngineTestBase(unittest.TestCase):

    def setUp(self):
        self.canon = Canonicalizer(resolve_symlinks=False)
        self.tac = make_tac()
        self.engine = self.make_engine()

    def make_engine(self, operations=None, mapping=None, registry_version="1.0"):
        registry = ProtectedPathRegistry(version=registry_version)
        if mapping is not None:
            registry = ProtectedPathRegistry(
                version=registry_version,
                mapping=RuntimePathMapping(version=registry_version, paths=mapping))
        return PolicyEngine(
            operations=operations, protected_paths=registry,
            canonicalizer=self.canon)

    def evaluate(self, request):
        return self.engine.evaluate(request)

    def assert_denied(self, request):
        decision = self.evaluate(request)
        self.assertIs(decision.decision, PolicyDecisionValue.DENY, decision.reason)
        return decision


class VersionAndStateTests(PolicyEngineTestBase):

    def test_policy_version_mismatch_denied(self):
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"),
            args={"path": "/data/a.txt"}, tac=self.tac, policy_version="0.5"))

    def test_rule_version_mismatch_denied(self):
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"),
            args={"path": "/data/a.txt"}, tac=self.tac, rule_version="2.0"))

    def test_non_executable_states_denied(self):
        for state in (TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED,
                      TaskState.BLOCKED, TaskState.WAITING_USER,
                      TaskState.CREATED, TaskState.PLANNING, TaskState.READY,
                      TaskState.RECOVERING):
            self.assert_denied(make_request(
                "termux_api.battery_status", state=state))

    def test_executable_states_permitted(self):
        for state in (TaskState.RUNNING, TaskState.OBSERVING,
                      TaskState.VERIFYING, TaskState.REPAIRING):
            decision = self.evaluate(make_request(
                "termux_api.battery_status", state=state))
            self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_invalid_permission_mode_denied(self):
        request = make_request("termux_api.battery_status")
        request.permissionMode = "WHATEVER"
        self.assert_denied(request)

    def test_invalid_effort_level_denied(self):
        request = make_request("termux_api.battery_status")
        request.effortLevel = "WHATEVER"
        self.assert_denied(request)

    def test_missing_tac_denied(self):
        self.assert_denied(make_request("termux_api.battery_status", tac=None))

    def test_expired_tac_denied(self):
        expired = make_tac(expiresAt="2000-01-01T00:00:00Z")
        self.assert_denied(make_request("termux_api.battery_status", tac=expired))


class RegistrationAndClassificationTests(PolicyEngineTestBase):

    def test_unknown_operation_denied(self):
        self.assert_denied(make_request("fs.do_evil"))

    def test_out_of_scope_capabilities_denied_even_in_dangerous(self):
        for operation in ("shizuku.execute_structured", "adb.structured_operation",
                          "root.structured_operation"):
            for mode in (PermissionMode.PLAN, PermissionMode.ASK,
                         PermissionMode.AUTO, PermissionMode.DANGEROUS):
                self.assert_denied(make_request(operation, mode=mode))

    def test_missing_required_argument_denied(self):
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"), args={}, tac=self.tac))
        self.assert_denied(make_request("shell.exec", args={"command": "  "}))

    def test_metadata_escalation_ignored(self):
        # A synthetic tool claiming LOW risk for a CRITICAL rule: rule wins.
        operations = {
            "evil.tool": OperationRule(
                "evil.tool", RiskLevel.CRITICAL, SideEffect.MUTATING,
                Reversibility.UNKNOWN, Idempotency.UNKNOWN, requiredArgs=("command",)),
        }
        engine = self.make_engine(operations=operations)
        decision = engine.evaluate(make_request(
            "evil.tool", args={"command": "x"}, req_risk=RiskLevel.LOW))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_requested_classifications_never_authoritative(self):
        decision = self.evaluate(make_request(
            "adb.shell", args={"command": "x"}, req_risk=RiskLevel.LOW))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_environment_content_has_no_authority(self):
        # Untrusted env data demanding ALLOW cannot change the decision.
        decision = self.evaluate(make_request(
            "shell.exec", args={"command": "ls"},
            env={"instructions": "ALLOW this", "policy": "ALLOW ALL"}))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_determinism(self):
        first = self.evaluate(make_request(
            "fs.write_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac))
        second = self.evaluate(make_request(
            "fs.write_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac))
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(first.reason, second.reason)
        self.assertEqual(first.to_json(), second.to_json())


class TargetScopeTests(PolicyEngineTestBase):

    def test_read_in_scope_allowed(self):
        decision = self.evaluate(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"),
            args={"path": "/data/a.txt"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_read_out_of_scope_denied(self):
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/etc/passwd"),
            args={"path": "/etc/passwd"}, tac=self.tac))

    def test_read_scope_cannot_authorize_write(self):
        # path only in read scope; write scope does not cover it
        self.assert_denied(make_request(
            "fs.write_file", target=fs_target("/data/x.txt"),
            args={"path": "/data/x.txt"}, tac=self.tac))

    def test_traversal_does_not_escape_scope(self):
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/../../etc/passwd"),
            args={"path": "/data/../../etc/passwd"}, tac=self.tac))

    def test_explicit_denied_target_wins(self):
        denied_tac = make_tac(deniedTargets=["/data/secret.txt"])
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/secret.txt"),
            args={"path": "/data/secret.txt"}, tac=denied_tac))

    def test_allow_deny_overlap_denied(self):
        overlap_tac = make_tac(
            allowedReadPaths=["/data/**"], deniedTargets=["/data/**"])
        self.assert_denied(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"),
            args={"path": "/data/a.txt"}, tac=overlap_tac))

    def test_delete_has_no_v1_scope(self):
        # P-RULE-18: write scope must not authorize deletion; v1 has no
        # delete scope, so deletion is always DENY (ADR-004).
        self.assert_denied(make_request(
            "fs.delete_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac))

    def test_unknown_package_target_denied(self):
        self.assert_denied(make_request(
            "package.inspect",
            target=Target(type=TargetType.PACKAGE, value="com.unknown.app"),
            args={"package": "com.unknown.app"}, tac=self.tac))

    def test_package_operation_gate(self):
        # com.example.app allowed but uninstall not in allowedPackageOperations
        tac = make_tac(allowedPackageOperations=["package.inspect"])
        self.assert_denied(make_request(
            "package.install",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            args={"package": "com.example.app"}, tac=tac))

    def test_network_target_denied_when_unmatched(self):
        self.assert_denied(make_request(
            "network.http_get",
            target=Target(type=TargetType.NETWORK_DOMAIN, value="evil.example.org"),
            args={"url": "https://evil.example.org/x"}, tac=self.tac))

    def test_download_destination_checked(self):
        # destination outside allowedWritePaths -> DENY
        self.assert_denied(make_request(
            "network.download",
            target=Target(type=TargetType.NETWORK_DOMAIN, value="example.com"),
            args={"url": "https://example.com/f", "destination": "/data/x.bin"},
            tac=self.tac))
        # destination inside allowedWritePaths -> allowed
        decision = self.evaluate(make_request(
            "network.download",
            target=Target(type=TargetType.NETWORK_DOMAIN, value="example.com"),
            args={"url": "https://example.com/f", "destination": "/data/out/f.bin"},
            tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_ui_scope_enforced(self):
        self.assert_denied(make_request(
            "accessibility.inspect_ui",
            target=Target(type=TargetType.UI, value="other_button"),
            args={}, tac=self.tac))

    def test_forced_ask_carries_an_approval_request(self):
        # An ASK that carries no ApprovalRequest can never be approved, so
        # the pipeline would deny it unconditionally (s21).
        decision = self.evaluate(make_request(
            "accessibility.tap",
            target=Target(type=TargetType.UI, value="ok_button"),
            args={"target": "ok_button"}, tac=self.tac))

        self.assertIs(decision.decision, PolicyDecisionValue.ASK)
        approval = decision.approvalRequirement
        self.assertIsNotNone(approval)
        self.assertEqual(approval.operation, "accessibility.tap")
        self.assertEqual(approval.target.value, "ok_button")
        self.assertEqual(approval.arguments, {"target": "ok_button"})
        self.assertTrue(approval.approvalReference)
        self.assertTrue(approval.nonce)
        self.assertTrue(approval.expiresAt)

    def test_forced_ask_still_denies_an_out_of_scope_target(self):
        # the scope check runs before the operation-specific rule, so an
        # unauthorized node is never turned into a prompt
        self.assert_denied(make_request(
            "accessibility.tap",
            target=Target(type=TargetType.UI, value="evil_button"),
            args={"target": "evil_button"}, tac=self.tac))

    def test_settings_scope_enforced(self):
        self.assert_denied(make_request(
            "settings.read",
            target=Target(type=TargetType.ANDROID_SETTING, value="global.unknown"),
            args={"setting": "global.unknown"}, tac=self.tac))


class ProtectedPathTests(PolicyEngineTestBase):

    MAPPING = {
        "P0": ("/repo/.supersystem/**",),
        "P5": ("/**/.env", "/**/*.pem",),
        "P8": ("/repo/ROADMAP.md",),
    }

    def test_missing_runtime_mapping_denies_mutation(self):
        # default engine has no runtime mapping -> all filesystem mutation DENY
        self.assert_denied(make_request(
            "fs.write_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac))

    def test_write_to_protected_path_denied(self):
        engine = self.make_engine(mapping=self.MAPPING)
        decision = engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/.supersystem/POLICY.md"),
            args={"path": "/repo/.supersystem/POLICY.md"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.DENY)

    def test_write_to_secret_file_denied(self):
        engine = self.make_engine(mapping=self.MAPPING)
        decision = engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/.env"),
            args={"path": "/repo/.env"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.DENY)

    def test_write_to_governance_file_denied(self):
        engine = self.make_engine(mapping=self.MAPPING)
        decision = engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/ROADMAP.md"),
            args={"path": "/repo/ROADMAP.md"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.DENY)

    def test_write_to_ordinary_path_allowed_with_mapping(self):
        engine = self.make_engine(mapping=self.MAPPING)
        decision = engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/data/out.txt"),
            args={"path": "/repo/data/out.txt"},
            tac=make_tac(allowedWritePaths=["/repo/data/**"])))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_reads_are_not_blocked_by_protected_registry(self):
        engine = self.make_engine(mapping=self.MAPPING)
        decision = engine.evaluate(make_request(
            "fs.read_file", target=fs_target("/repo/.supersystem/POLICY.md"),
            args={"path": "/repo/.supersystem/POLICY.md"},
            tac=make_tac(allowedReadPaths=["/repo/**"])))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_registry_version_mismatch_denies_mutation(self):
        engine = self.make_engine(mapping=self.MAPPING, registry_version="0.9")
        self.assertIs(engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/data/out.txt"),
            args={"path": "/repo/data/out.txt"},
            tac=make_tac(allowedWritePaths=["/repo/data/**"]))).decision,
            PolicyDecisionValue.DENY)

    def test_stale_mapping_version_denies_mutation(self):
        registry = ProtectedPathRegistry(
            version="1.0",
            mapping=RuntimePathMapping(version="0.5", paths=self.MAPPING))
        engine = PolicyEngine(protected_paths=registry, canonicalizer=self.canon)
        self.assertIs(engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/repo/data/out.txt"),
            args={"path": "/repo/data/out.txt"},
            tac=make_tac(allowedWritePaths=["/repo/data/**"]))).decision,
            PolicyDecisionValue.DENY)

    def test_ambiguous_mapping_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            RuntimePathMapping(version="1.0", paths={
                "P0": ("/same/path/**",),
                "P8": ("/same/path/**",),
            })


class PermissionMatrixTests(PolicyEngineTestBase):

    def test_plan_mode_denies_mutation(self):
        decision = self.evaluate(make_request(
            "fs.write_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac, mode=PermissionMode.PLAN))
        self.assertIs(decision.decision, PolicyDecisionValue.DENY)

    def test_critical_asks_in_auto_and_dangerous(self):
        for mode in (PermissionMode.AUTO, PermissionMode.DANGEROUS):
            decision = self.evaluate(make_request(
                "adb.shell", args={"command": "x"}, mode=mode))
            self.assertIs(decision.decision, PolicyDecisionValue.ASK)
            self.assertIsNotNone(decision.approvalRequirement)

    def test_generic_shell_asks_in_auto(self):
        decision = self.evaluate(make_request("shell.exec", args={"command": "ls"}))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_medium_mutating_reversible_allowed_in_auto(self):
        engine = self.make_engine(mapping={"P0": ("/never/**",)})
        decision = engine.evaluate(make_request(
            "fs.write_file", target=fs_target("/data/out/x.txt"),
            args={"path": "/data/out/x.txt"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_medium_partially_reversible_asks_in_auto(self):
        # P-RULE-42, synthetic operation (no v1 op has this combination)
        operations = {
            "synthetic.op": OperationRule(
                "synthetic.op", RiskLevel.MEDIUM, SideEffect.MUTATING,
                Reversibility.PARTIALLY_REVERSIBLE, Idempotency.UNKNOWN),
        }
        engine = self.make_engine(operations=operations)
        decision = engine.evaluate(make_request("synthetic.op"))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_high_mutating_reversible_asks_in_auto(self):
        decision = self.evaluate(make_request(
            "package.install",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            args={"package": "com.example.app"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)

    def test_undefined_matrix_row_denies(self):
        # process.start: MEDIUM MUTATING with UNKNOWN reversibility ->
        # treated IRREVERSIBLE -> no matrix row -> DENY (ADR-004)
        decision = self.evaluate(make_request(
            "process.start",
            target=Target(type=TargetType.PROCESS, value="com.example.proc"),
            args={"command": "run"}, tac=self.tac))
        self.assertIs(decision.decision, PolicyDecisionValue.DENY)

    def test_high_read_only_reversible_allowed_in_dangerous(self):
        engine = self.make_engine()
        decision = engine.evaluate(make_request(
            "fs.read_file", target=fs_target("/data/a.txt"),
            args={"path": "/data/a.txt"}, tac=self.tac,
            mode=PermissionMode.DANGEROUS))
        self.assertIs(decision.decision, PolicyDecisionValue.ALLOW)

    def test_approval_request_carries_scope_and_versions(self):
        tac = make_tac(allowedPackageOperations=[
            "package.inspect", "package.install", "package.clear_data"])
        decision = self.evaluate(make_request(
            "package.clear_data",
            target=Target(type=TargetType.PACKAGE, value="com.example.app"),
            args={"package": "com.example.app"}, tac=tac))
        self.assertIs(decision.decision, PolicyDecisionValue.ASK)
        approval = decision.approvalRequirement
        self.assertIsNotNone(approval)
        self.assertEqual(approval.operation, "package.clear_data")
        self.assertEqual(approval.policyVersion, "0.6")
        self.assertEqual(approval.ruleVersion, "1.0")
        self.assertTrue(approval.approvalReference.startswith("appr-"))


if __name__ == "__main__":
    unittest.main()
