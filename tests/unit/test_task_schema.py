"""Task schema validation and execution-gate tests (TASK_SCHEMA.md s33/s36)."""
import unittest

from core import (
    CompletionContract,
    ContinuityState,
    PolicyContext,
    ResourceLimits,
    SuccessCriterion,
    TargetAuthorizationContext,
    Task,
    ValidationError,
    VerificationState,
    execution_gate,
    utcnow_iso,
    validate_task,
)
from core.enums import EffortLevel, PermissionMode, RiskLevel, TaskState


def make_task(**overrides):
    now = utcnow_iso()
    criterion = SuccessCriterion(id="c1", description="build succeeds", verificationMethod="run tests")
    defaults = dict(
        id="task-1",
        schemaVersion="1.0",
        objective="make the build pass",
        requirements=[],
        successCriteria=[criterion],
        completionContract=CompletionContract(
            objective="make the build pass",
            successCriteria=[criterion],
        ),
        permissionMode=PermissionMode.AUTO,
        effortLevel=EffortLevel.STANDARD,
        policyContext=PolicyContext(
            policyVersion="0.6",
            ruleVersion="1.0",
            permissionMode=PermissionMode.AUTO,
            defaultRiskLevel=RiskLevel.MEDIUM,
        ),
        targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            authorizationReference="auth-1",
        ),
        resourceLimits=ResourceLimits(actionSteps=50, wallClockTime=3600),
        state=TaskState.READY,
        verification=VerificationState(),
        continuity=ContinuityState(),
        createdAt=now,
        updatedAt=now,
    )
    defaults.update(overrides)
    return Task(**defaults)


class TaskValidationTests(unittest.TestCase):

    def test_valid_task_passes_gate(self):
        self.assertEqual(execution_gate(make_task()), [])

    def test_valid_task_is_schema_valid(self):
        self.assertEqual(validate_task(make_task()), [])

    def test_missing_success_criteria_blocked(self):
        problems = execution_gate(make_task(successCriteria=[]))
        self.assertTrue(any("success criteria" in p for p in problems))

    def test_no_mandatory_criterion_blocked(self):
        criterion = SuccessCriterion(id="c2", description="d", verificationMethod="m",
                                     mandatory=False)
        task = make_task(
            successCriteria=[criterion],
            completionContract=CompletionContract(
                objective="o", successCriteria=[criterion]))
        problems = execution_gate(task)
        self.assertTrue(any("mandatory" in p for p in problems))

    def test_executable_state_requires_completion_contract(self):
        task = make_task(state=TaskState.RUNNING, completionContract=None)
        problems = validate_task(task)
        self.assertTrue(any("CompletionContract" in p for p in problems))

    def test_created_state_may_have_no_completion_contract(self):
        task = make_task(state=TaskState.CREATED, completionContract=None)
        self.assertEqual(validate_task(task), [])

    def test_unsupported_schema_version_rejected(self):
        problems = validate_task(make_task(schemaVersion="0.9"))
        self.assertTrue(any("schemaVersion" in p for p in problems))

    def test_gate_requires_ready_state(self):
        problems = execution_gate(make_task(state=TaskState.PLANNING))
        self.assertTrue(any("READY" in p for p in problems))

    def test_empty_resource_limits_blocked(self):
        problems = execution_gate(make_task(resourceLimits=ResourceLimits()))
        self.assertTrue(any("no limits" in p for p in problems))

    def test_negative_resource_limit_blocked(self):
        problems = execution_gate(make_task(resourceLimits=ResourceLimits(actionSteps=-1)))
        self.assertTrue(any("negative" in p for p in problems))

    def test_empty_policy_versions_blocked(self):
        task = make_task()
        task.policyContext.policyVersion = ""
        self.assertTrue(any("policyVersion" in p for p in validate_task(task)))
        task.policyContext.policyVersion = "0.6"
        task.policyContext.ruleVersion = ""
        self.assertTrue(any("ruleVersion" in p for p in validate_task(task)))

    def test_expired_target_authorization_blocked(self):
        now = utcnow_iso()
        tac = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="2000-01-01T00:00:00Z", authorizationReference="auth-1")
        task = make_task(targetAuthorizationContext=tac)
        self.assertFalse(tac.is_active())
        problems = execution_gate(task)
        self.assertTrue(any("expired" in p for p in problems))

    def test_unparseable_expiry_fails_closed(self):
        now = utcnow_iso()
        tac = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="not-a-timestamp", authorizationReference="auth-1")
        self.assertFalse(tac.is_active())
        problems = execution_gate(make_task(targetAuthorizationContext=tac))
        self.assertTrue(any("expired" in p for p in problems))

    def test_invalid_permission_mode_fails_closed_on_load(self):
        data = make_task().to_dict()
        data["permissionMode"] = "WHATEVER"
        with self.assertRaises(ValidationError):
            Task.from_dict(data)

    def test_invalid_state_fails_closed_on_load(self):
        data = make_task().to_dict()
        data["state"] = "BOGUS"
        with self.assertRaises(ValidationError):
            Task.from_dict(data)

    def test_unknown_field_fails_closed_on_load(self):
        data = make_task().to_dict()
        data["secretBonusField"] = 1
        with self.assertRaises(ValidationError):
            Task.from_dict(data)

    def test_missing_required_field_fails_closed_on_load(self):
        data = make_task().to_dict()
        del data["objective"]
        with self.assertRaises(ValidationError):
            Task.from_dict(data)

    def test_round_trip_preserves_task_identity(self):
        task = make_task()
        restored = Task.from_dict(task.to_dict())
        self.assertEqual(restored.id, task.id)
        self.assertEqual(restored, task)


if __name__ == "__main__":
    unittest.main()
