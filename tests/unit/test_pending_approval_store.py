"""PendingApprovalStore (Phase 14 WS7): the durable half of human approval.

The store is where an approval can go wrong quietly - a grant that outlives
its request, a grant reused for a second action, a decision written over a
tampered file. Each of those is pinned here.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from continuity.task_store import TaskIntegrityError
from execution import DurableApprover, PendingApprovalStore

from core import (
    ActorIdentity,
    ApprovalRequest,
    Target,
    ValidationError,
    utcnow_iso,
)
from core.enums import (
    ApprovalDecision,
    Idempotency,
    Reversibility,
    RiskLevel,
    SideEffect,
    TargetType,
)

EXPIRED = "2000-01-01T00:00:00.000000Z"
TASK = "t1"


def in_seconds(seconds):
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def approval_request(operation="android.open_url", target_value="example.com",
                     arguments=None, expires_at=None, reference="appr-1"):
    return ApprovalRequest(
        taskId=TASK, operation=operation,
        target=Target(type=TargetType.NETWORK_DOMAIN, value=target_value),
        riskLevel=RiskLevel.MEDIUM, sideEffect=SideEffect.EXTERNAL_EFFECT,
        reversibility=Reversibility.UNKNOWN, idempotency=Idempotency.UNKNOWN,
        explanation="hands control to another app",
        consequences="EXTERNAL_EFFECT / UNKNOWN",
        scope={"operation": operation},
        expiresAt=expires_at or in_seconds(300),
        policyVersion="0.6", ruleVersion="1.0",
        approvalReference=reference, nonce="n1",
        arguments=arguments if arguments is not None else {"url": "https://example.com/x"})


class PendingApprovalStoreTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = PendingApprovalStore(self.root)

    def request(self, **overrides):
        return self.store.request(approval_request(**overrides))

    def test_a_request_is_persisted_with_its_scope_and_is_pending(self):
        record = self.request()

        self.assertEqual(record["status"], "PENDING")
        self.assertEqual(record["operation"], "android.open_url")
        self.assertEqual(record["targetValue"], "example.com")
        self.assertEqual(record["arguments"], {"url": "https://example.com/x"})
        self.assertTrue(self.store.has_pending(TASK))
        self.assertEqual(self.store.pending(TASK)[0]["approvalReference"], "appr-1")
        # and it is readable by a fresh instance, i.e. it is on disk
        self.assertIsNotNone(PendingApprovalStore(self.root).get(TASK, "appr-1"))

    def test_the_same_reference_is_recorded_once(self):
        first = self.request()
        again = self.store.request(approval_request(arguments={"url": "https://other/x"}))
        self.assertEqual(again["arguments"], first["arguments"])

    def test_a_grant_is_single_use(self):
        self.request()
        self.store.grant(TASK, "appr-1", approved_by="hamid")
        self.assertEqual(self.store.claims(TASK)[0]["decidedBy"], "hamid")

        self.store.consume(TASK, "appr-1")

        self.assertEqual(self.store.claims(TASK), [])
        with self.assertRaises(ValidationError):
            self.store.consume(TASK, "appr-1")

    def test_a_grant_never_outlives_its_request(self):
        record = self.request()
        self.store.grant(TASK, "appr-1", approved_by="hamid")
        granted = self.store.get(TASK, "appr-1")
        self.assertEqual(granted["grantExpiresAt"], record["requestExpiresAt"])

    def test_an_expired_request_cannot_be_granted(self):
        self.request(expires_at=EXPIRED)
        with self.assertRaises(ValidationError):
            self.store.grant(TASK, "appr-1", approved_by="hamid")

    def test_a_denied_request_is_never_a_grant(self):
        self.request()
        self.store.deny(TASK, "appr-1", denied_by="hamid", reason="not now")
        record = self.store.get(TASK, "appr-1")
        self.assertEqual(record["status"], "DENIED")
        self.assertEqual(record["denialReason"], "not now")
        self.assertFalse(self.store.has_pending(TASK))

    def test_decisions_require_a_pending_request(self):
        with self.assertRaises(ValidationError):
            self.store.grant(TASK, "appr-ghost", approved_by="hamid")
        self.request()
        self.store.deny(TASK, "appr-1", denied_by="hamid")
        with self.assertRaises(ValidationError):
            self.store.grant(TASK, "appr-1", approved_by="hamid")

    def test_a_tampered_record_is_refused(self):
        self.request()
        path = self.root / TASK / "approvals" / "appr-1.json"
        envelope = json.loads(path.read_text())
        envelope["payload"]["operation"] = "settings.write"
        path.write_text(json.dumps(envelope))

        with self.assertRaises(TaskIntegrityError):
            self.store.all_records(TASK)


class GrantMatchingTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = PendingApprovalStore(self.root)

    def grant(self, **kwargs):
        request = approval_request(**kwargs)
        self.store.request(request)
        self.store.grant(TASK, request.approvalReference, approved_by="hamid")
        return request

    def test_a_grant_matches_the_same_operation_target_and_arguments(self):
        granted = self.grant()
        self.assertIsNotNone(self.store.find_grant(TASK, granted))
        self.assertIsNotNone(self.store.find_grant(
            TASK, approval_request(reference="appr-2")))

    def test_a_grant_does_not_match_another_target(self):
        self.grant()
        self.assertIsNone(self.store.find_grant(
            TASK, approval_request(target_value="evil.example")))

    def test_a_grant_does_not_match_other_arguments(self):
        self.grant()
        self.assertIsNone(self.store.find_grant(
            TASK, approval_request(arguments={"url": "https://example.com/other"})))

    def test_a_grant_does_not_match_another_operation(self):
        self.grant()
        self.assertIsNone(self.store.find_grant(
            TASK, approval_request(operation="android.launch_package")))

    def test_a_consumed_grant_is_not_offered_again(self):
        granted = self.grant()
        self.store.consume(TASK, "appr-1")
        self.assertIsNone(self.store.find_grant(TASK, granted))

    def test_an_expired_grant_is_not_offered(self):
        granted = self.grant()
        # the grant was valid when given; time passed before anyone used it
        record = self.store.get(TASK, "appr-1")
        record["grantExpiresAt"] = EXPIRED
        self.store._write(TASK, record)

        self.assertIsNone(self.store.find_grant(TASK, granted))

    def test_an_unexpired_grant_still_works(self):
        granted = self.grant()
        self.assertIsNotNone(self.store.find_grant(TASK, granted, arguments=None))


class DurableApproverTests(unittest.TestCase):
    """The HumanApproval port over the store."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = PendingApprovalStore(self.root)
        self.notified = []
        self.approver = DurableApprover(self.store, notifier=self.notified.append)

    def test_an_unanswered_ask_records_the_question_and_approves_nothing(self):
        result = self.approver.request(approval_request())

        self.assertIsNone(result)          # fail closed: no approval
        self.assertTrue(self.store.has_pending(TASK))
        self.assertEqual(self.notified[0]["operation"], "android.open_url")

    def test_a_stored_grant_becomes_an_approval_of_the_current_request(self):
        self.approver.request(approval_request(reference="appr-1"))
        self.store.grant(TASK, "appr-1", approved_by="hamid")

        result = self.approver.request(approval_request(reference="appr-2"))

        self.assertIsNotNone(result)
        # the result names the request being answered now, so the pipeline's
        # reference check and single-use journal rule are untouched
        self.assertEqual(result.approvalReference, "appr-2")
        self.assertIs(result.decision, ApprovalDecision.APPROVE)
        self.assertEqual(result.approverIdentity.actorId, "hamid")
        self.assertIsNotNone(self.store.get(TASK, "appr-1")["consumedAt"])

    def test_a_grant_is_not_used_twice(self):
        self.approver.request(approval_request(reference="appr-1"))
        self.store.grant(TASK, "appr-1", approved_by="hamid")
        self.approver.request(approval_request(reference="appr-2"))

        second = self.approver.request(approval_request(reference="appr-3"))

        self.assertIsNone(second)
        self.assertEqual([r["status"] for r in self.store.all_records(TASK)],
                         ["GRANTED", "PENDING"])

    def test_a_notifier_that_fails_cannot_block_the_pause(self):
        def explode(record):
            raise RuntimeError("no notification channel")

        approver = DurableApprover(self.store, notifier=explode)

        self.assertIsNone(approver.request(approval_request()))
        self.assertTrue(self.store.has_pending(TASK))


if __name__ == "__main__":
    unittest.main()
