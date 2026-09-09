"""Enum contract tests: the frozen value sets must not drift."""
import unittest

from core.enums import (
    ActionStatus,
    ActorType,
    ApprovalDecision,
    CompletionDecisionValue,
    EffortLevel,
    Idempotency,
    IndependenceLevel,
    ModelRole,
    PermissionMode,
    PolicyDecisionValue,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TargetType,
    TaskState,
    VerificationStatus,
)


class EnumContractTests(unittest.TestCase):

    def _values(self, enum_cls):
        return {member.value for member in enum_cls}

    def test_permission_modes(self):
        self.assertEqual(self._values(PermissionMode), {"PLAN", "ASK", "AUTO", "DANGEROUS"})

    def test_effort_levels(self):
        self.assertEqual(self._values(EffortLevel), {"FOCUSED", "STANDARD", "DEEP", "ULTRA"})

    def test_task_states(self):
        self.assertEqual(
            self._values(TaskState),
            {"CREATED", "VALIDATING", "PLANNING", "READY", "RUNNING", "OBSERVING",
             "VERIFYING", "REPAIRING", "RECOVERING", "WAITING_USER", "BLOCKED",
             "DONE", "FAILED", "CANCELLED"},
        )

    def test_risk_levels(self):
        self.assertEqual(self._values(RiskLevel), {"LOW", "MEDIUM", "HIGH", "CRITICAL"})

    def test_side_effects(self):
        self.assertEqual(
            self._values(SideEffect),
            {"READ_ONLY", "MUTATING", "DESTRUCTIVE", "EXTERNAL_EFFECT"},
        )

    def test_reversibility(self):
        self.assertEqual(
            self._values(Reversibility),
            {"REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE", "UNKNOWN"},
        )

    def test_idempotency(self):
        self.assertEqual(
            self._values(Idempotency),
            {"IDEMPOTENT", "CONDITIONALLY_IDEMPOTENT", "NON_IDEMPOTENT", "UNKNOWN"},
        )

    def test_policy_decision_values(self):
        self.assertEqual(self._values(PolicyDecisionValue), {"ALLOW", "ASK", "DENY"})

    def test_completion_decision_values(self):
        # ADR-002 union of INTERFACES.md s20 and VERIFICATION.md s17.
        self.assertEqual(
            self._values(CompletionDecisionValue),
            {"DONE", "CONTINUE", "REPAIR", "BLOCKED", "WAITING_USER", "FAILED"},
        )

    def test_verification_statuses(self):
        self.assertEqual(self._values(VerificationStatus), {"PASS", "FAIL", "INCONCLUSIVE"})

    def test_side_effect_states(self):
        self.assertEqual(
            self._values(SideEffectState),
            {"KNOWN_NONE", "KNOWN_COMPLETED", "KNOWN_PARTIAL", "KNOWN_FAILED", "UNKNOWN"},
        )

    def test_action_statuses(self):
        self.assertEqual(
            self._values(ActionStatus),
            {"PLANNED", "AUTHORIZED", "STARTED", "SUCCEEDED", "FAILED",
             "CANCELLED", "UNKNOWN"},
        )

    def test_actor_types(self):
        self.assertEqual(
            self._values(ActorType),
            {"USER", "TOP_LEVEL_AGENT", "SUBAGENT", "VERIFIER", "SYSTEM", "TOOL",
             "RUNTIME_ADAPTER"},
        )

    def test_model_roles(self):
        self.assertEqual(
            self._values(ModelRole),
            {"PLANNER", "RESEARCHER", "CODER", "REVIEWER", "VERIFIER", "GENERAL_AGENT"},
        )

    def test_approval_decisions(self):
        self.assertEqual(self._values(ApprovalDecision), {"APPROVE", "DENY"})

    def test_independence_levels(self):
        self.assertEqual(
            self._values(IndependenceLevel),
            {"LEVEL_0_SELF", "LEVEL_1_INDEPENDENT_RUNTIME", "LEVEL_2_SEPARATE_VERIFIER"},
        )

    def test_target_types(self):
        self.assertEqual(
            self._values(TargetType),
            {"FILESYSTEM", "PACKAGE", "NETWORK_DOMAIN", "NETWORK_DESTINATION",
             "ANDROID_SETTING", "UI", "PROCESS", "RESOURCE"},
        )


if __name__ == "__main__":
    unittest.main()
