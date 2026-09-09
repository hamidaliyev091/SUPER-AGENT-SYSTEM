"""Contract serialization and structure tests (INTERFACES.md)."""
import unittest

from core import (
    ActionRequest,
    ActorIdentity,
    ApprovalRequest,
    ApprovalResult,
    CompletionContract,
    CompletionDecision,
    Decision,
    Error,
    Evidence,
    FailureRecord,
    Plan,
    PlanStep,
    PolicyDecision,
    PolicyRequest,
    SuccessCriterion,
    Target,
    TargetAuthorizationContext,
    Tool,
    ToolResult,
    ValidationError,
    VerificationResult,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    ApprovalDecision,
    CompletionDecisionValue,
    EffortLevel,
    Idempotency,
    IndependenceLevel,
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


class ContractSerializationTests(unittest.TestCase):

    def _roundtrip(self, obj):
        restored = type(obj).from_dict(obj.to_dict())
        self.assertEqual(restored, obj)
        self.assertEqual(restored.to_json(), obj.to_json())
        return restored

    def test_action_request_roundtrip(self):
        req = ActionRequest(
            taskId="t1",
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT, taskId="t1"),
            toolId="fs.read_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/file.txt"),
            arguments={"path": "/data/file.txt"},
            reason="read the config",
            requestedRiskLevel=RiskLevel.LOW,
        )
        self._roundtrip(req)

    def test_policy_request_roundtrip(self):
        now = utcnow_iso()
        req = PolicyRequest(
            policyVersion="0.6",
            ruleVersion="1.0",
            taskId="t1",
            taskState=TaskState.RUNNING,
            permissionMode=PermissionMode.AUTO,
            effortLevel=EffortLevel.STANDARD,
            actorContext=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT),
            environmentContext={},
            authorizationContext={},
            targetAuthorizationContext=TargetAuthorizationContext(
                schemaVersion="1.0", createdAt=now, updatedAt=now),
            toolId="fs.write_file",
            operationId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out.txt"),
            structuredArguments={"path": "/data/out.txt", "content": "x"},
        )
        self._roundtrip(req)

    def test_policy_decision_roundtrip(self):
        decision = PolicyDecision(
            decision=PolicyDecisionValue.ASK,
            reason="medium mutating operation in AUTO",
            policyVersion="0.6",
            ruleVersion="1.0",
        )
        self._roundtrip(decision)

    def test_approval_roundtrip(self):
        now = utcnow_iso()
        request = ApprovalRequest(
            taskId="t1",
            operation="fs.delete_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/x.txt"),
            riskLevel=RiskLevel.HIGH,
            sideEffect=SideEffect.DESTRUCTIVE,
            reversibility=Reversibility.IRREVERSIBLE,
            idempotency=Idempotency.NON_IDEMPOTENT,
            explanation="delete /data/x.txt",
            consequences="file is gone",
            scope={"paths": ["/data/x.txt"]},
            expiresAt="2030-01-01T00:00:00Z",
            policyVersion="0.6",
            ruleVersion="1.0",
            approvalReference="appr-1",
            nonce="nonce-1",
        )
        self._roundtrip(request)
        result = ApprovalResult(
            approvalReference="appr-1",
            decision=ApprovalDecision.APPROVE,
            approverIdentity=ActorIdentity(actorId="user", actorType=ActorType.USER),
            timestamp=now,
        )
        self._roundtrip(result)

    def test_verification_result_roundtrip(self):
        now = utcnow_iso()
        result = VerificationResult(
            taskId="t1",
            criterionId="c1",
            result=VerificationStatus.PASS,
            verifier="verifier-1",
            independenceLevel=IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME,
            verificationMethod="run tests",
            timestamp=now,
            evidence=[Evidence(
                evidenceId="e1", timestamp=now, source="test runner",
                collectorIdentity="verifier-1", contentHash="abc123",
                criterionId="c1",
            )],
        )
        self._roundtrip(result)

    def test_completion_decision_roundtrip(self):
        now = utcnow_iso()
        decision = CompletionDecision(
            taskId="t1",
            decision=CompletionDecisionValue.DONE,
            schemaVersion="1.0",
            timestamp=now,
            reasonCode="ALL_CRITERIA_PASSED",
        )
        self._roundtrip(decision)

    def test_failure_record_roundtrip(self):
        now = utcnow_iso()
        record = FailureRecord(
            failureId="f1",
            taskId="t1",
            timestamp=now,
            category="TOOL_FAILURE",
            sideEffectState=SideEffectState.UNKNOWN,
            retryCount=2,
            error=Error(code="TOOL_FAILURE", message="boom", retryable=False),
        )
        self._roundtrip(record)

    def test_failure_record_invalid_side_effect_state_rejected(self):
        data = {
            "failureId": "f1", "taskId": "t1", "timestamp": "2026-01-01T00:00:00Z",
            "category": "x", "sideEffectState": "NOT_A_STATE",
        }
        with self.assertRaises(ValidationError):
            FailureRecord.from_dict(data)

    def test_tool_execute_excluded_from_serialization(self):
        tool = Tool(
            id="fs.read_file", name="read_file", description="read a file",
            inputSchema={}, outputSchema={},
            riskLevel=RiskLevel.LOW, sideEffect=SideEffect.READ_ONLY,
            reversibility=Reversibility.REVERSIBLE,
            execute=lambda *a, **k: None,
        )
        serialized = tool.to_dict()
        self.assertNotIn("execute", serialized)
        self._roundtrip(Tool.from_dict(serialized))

    def test_target_authorization_roundtrip(self):
        now = utcnow_iso()
        tac = TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=["/data/**"],
            allowedWritePaths=["/data/out/**"],
            deniedTargets=["/data/out/secret.txt"],
            createdAt=now, updatedAt=now,
            authorizationReference="auth-1",
        )
        self._roundtrip(tac)

    def test_target_authorization_expiry(self):
        now = utcnow_iso()
        active = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now,
            expiresAt="2030-01-01T00:00:00Z")
        self.assertTrue(active.is_active(now="2029-01-01T00:00:00Z"))
        self.assertFalse(active.is_active(now="2031-01-01T00:00:00Z"))
        nonexpiring = TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now)
        self.assertTrue(nonexpiring.is_active())

    def test_plan_and_decision_roundtrip(self):
        plan = Plan(id="p1", steps=[PlanStep(id="s1", description="step one")])
        self._roundtrip(plan)
        decision = Decision(
            decisionId="d1", taskId="t1", timestamp=utcnow_iso(),
            decision="use stdlib-only persistence")
        self._roundtrip(decision)

    def test_deterministic_json_ordering(self):
        contract = CompletionContract(
            objective="o",
            successCriteria=[SuccessCriterion(id="c1", description="d", verificationMethod="m")],
        )
        first = contract.to_json()
        second = CompletionContract.from_json(first).to_json()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
