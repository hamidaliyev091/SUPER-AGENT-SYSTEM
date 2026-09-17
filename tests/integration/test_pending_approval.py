"""Durable approval (Phase 14 WS7): ASK pauses a task, a human answers, and
only then does the action run.

Policy is still the only authorizer here. These tests pin the properties
that make an approval real rather than a formality: the question survives a
restart; nothing executes while it is open; the grant is bound to exactly
one action; it is spent by one execution; it expires; an unreadable approval
store stops the run instead of being read as consent.
"""
import unittest

from tests.support.harness import VerificationTestBase
from tests.unit.test_verification_engine import make_tac

from core import Tool, ToolCall, ToolResult, ValidationError, utcnow_iso
from core.enums import (
    Idempotency,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TargetType,
    TaskState,
)
from completion import CompletionEngine
from continuity import RecoveryManager
from execution import DurableApprover, ExecutionPipeline, PendingApprovalStore
from models import ModelPortDriver, ScriptedModelPort
from orchestration import Orchestrator

URL = "https://example.com/start"
EXPIRED = "2000-01-01T00:00:00.000000Z"


def network_tac(domains=("example.com",)):
    """The harness TAC plus a network scope, so open_url can be authorized."""
    tac = make_tac()
    tac.allowedNetworkDomains = list(domains)
    tac.allowedNetworkDestinations = list(domains)
    return tac


class PendingApprovalTests(VerificationTestBase):
    """ASK -> pause -> human decision -> resume."""

    def setUp(self):
        super().setUp()
        self.opened = []
        self.tools["android.open_url"] = Tool(
            id="android.open_url", name="open_url", description="open a URL",
            inputSchema={}, outputSchema={}, riskLevel=RiskLevel.MEDIUM,
            sideEffect=SideEffect.EXTERNAL_EFFECT, reversibility=Reversibility.UNKNOWN,
            idempotency=Idempotency.UNKNOWN, execute=self._open_url)
        self.approvals = PendingApprovalStore(self.root)
        self.pipeline = ExecutionPipeline(
            self.mgr, self.policy, self.tools, self.store,
            approver=DurableApprover(self.approvals))

    def _open_url(self, args, context):
        self.opened.append(args["url"])
        return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                          timestamp=utcnow_iso(), output={"opened": args["url"]})

    def open_url_call(self, url=URL):
        return ToolCall(id="tc-open", name="android.open_url", arguments={"url": url})

    def write_call(self, content="HELLO"):
        return ToolCall(id="tc-write", name="fs.write_file",
                        arguments={"path": "/data/out/x.txt", "content": content})

    def make_task_with_domain(self, domains=("example.com",)):
        return self.make_task(stop_at=TaskState.CREATED,
                              targetAuthorizationContext=network_tac(domains))

    def orchestrator(self, turns, approvals=None):
        driver = ModelPortDriver(self.mgr, self.store, ScriptedModelPort(turns=turns))
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver,
            recovery=RecoveryManager(self.mgr, self.store),
            approvals=self.approvals if approvals is None else approvals)

    def grant(self, task_id, reference, approved_by="operator"):
        return self.approvals.grant(task_id, reference, approved_by=approved_by)

    def resume(self, task_id, actor="operator"):
        """What the operator CLI does: authorize the re-entry, then run."""
        self.mgr.transition(task_id, TaskState.RECOVERING,
                            authorization={"kind": "USER", "actor": actor})

    # -- the pause ------------------------------------------------------------

    def test_ask_pauses_the_task_and_executes_nothing(self):
        task = self.make_task_with_domain()
        final = self.orchestrator([[self.open_url_call()]]).run(task.id)

        self.assertIs(final.state, TaskState.WAITING_USER)
        self.assertEqual(self.opened, [])          # nothing ran
        pending = self.approvals.pending(task.id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["operation"], "android.open_url")
        self.assertEqual(pending[0]["arguments"], {"url": URL})

    def test_the_pending_request_carries_what_a_human_needs_to_decide(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)

        record = self.approvals.pending(task.id)[0]
        self.assertEqual(record["targetType"], TargetType.NETWORK_DOMAIN.value)
        self.assertEqual(record["targetValue"], "example.com")
        self.assertEqual(record["riskLevel"], RiskLevel.MEDIUM.value)
        self.assertEqual(record["sideEffect"], SideEffect.EXTERNAL_EFFECT.value)
        self.assertTrue(record["explanation"])
        self.assertTrue(record["requestExpiresAt"])
        self.assertIsNotNone(record["requestExpiresAt"])

    def test_the_pause_survives_a_restart(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]

        # a new process: fresh store, fresh pipeline, fresh approver
        reopened = PendingApprovalStore(self.root)
        self.pipeline = ExecutionPipeline(
            self.mgr, self.policy, self.tools, self.store,
            approver=DurableApprover(reopened))
        self.approvals = reopened
        self.grant(task.id, reference)
        self.resume(task.id)
        final = self.orchestrator([[self.open_url_call()],
                                   [self.write_call()]]).run(task.id)

        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.opened, [URL])

    # -- the decision ---------------------------------------------------------

    def test_approved_then_resumed_runs_the_action_on_the_grant(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]

        self.grant(task.id, reference, approved_by="hamid")
        self.resume(task.id, actor="hamid")
        final = self.orchestrator([[self.open_url_call()],
                                   [self.write_call()]]).run(task.id)

        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.opened, [URL])
        record = self.approvals.get(task.id, reference)
        self.assertEqual(record["status"], "GRANTED")
        self.assertEqual(record["decidedBy"], "hamid")
        self.assertIsNotNone(record["consumedAt"])
        journal = self.store.journal_for(task.id).records()
        events = [r["eventType"] for r in journal]
        self.assertIn("APPROVAL_REQUESTED", events)
        self.assertIn("APPROVAL_RESULT", events)
        started = [r["payload"]["operationId"] for r in journal
                   if r["eventType"] == "ACTION_STARTED"]
        self.assertEqual(started.count("android.open_url"), 1)
        self.assertEqual(started.count("fs.write_file"), 1)

    def test_denied_action_never_runs(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]

        self.approvals.deny(task.id, reference, denied_by="hamid", reason="not now")
        self.resume(task.id, actor="hamid")
        final = self.orchestrator([[self.write_call()]]).run(task.id)

        # the model moved on to an allowed action, but the URL was never opened
        self.assertEqual(self.opened, [])
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.approvals.get(task.id, reference)["status"], "DENIED")

    def test_a_grant_is_spent_by_one_execution(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        self.grant(task.id, self.approvals.pending(task.id)[0]["approvalReference"])
        self.resume(task.id)

        # the same proposal comes again: the grant is gone, so it asks again
        final = self.orchestrator([[self.open_url_call()],
                                   [self.open_url_call()],
                                   [self.open_url_call()]]).run(task.id)

        self.assertEqual(self.opened, [URL])       # executed exactly once
        self.assertIs(final.state, TaskState.WAITING_USER)
        pending = self.approvals.pending(task.id)
        self.assertEqual(len(pending), 1)          # a new question, not the old one

    def test_a_grant_for_another_target_does_not_authorize_this_one(self):
        task = self.make_task_with_domain(domains=("example.com", "other.example"))
        self.orchestrator([[self.open_url_call("https://other.example/x")]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]
        self.grant(task.id, reference)
        self.resume(task.id)

        final = self.orchestrator([[self.open_url_call(URL)]]).run(task.id)

        self.assertEqual(self.opened, [])
        self.assertIs(final.state, TaskState.WAITING_USER)

    def test_a_grant_for_other_arguments_does_not_authorize_this_one(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]
        self.grant(task.id, reference)
        self.resume(task.id)

        # same host, different path: the grant was for one exact action
        final = self.orchestrator(
            [[self.open_url_call("https://example.com/somewhere-else")]]).run(task.id)

        self.assertEqual(self.opened, [])
        self.assertIs(final.state, TaskState.WAITING_USER)

    # -- refusals -------------------------------------------------------------

    def test_an_expired_request_cannot_be_granted(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        record = self.approvals.pending(task.id)[0]
        record["requestExpiresAt"] = EXPIRED
        self.approvals._write(task.id, record)

        with self.assertRaises(ValidationError):
            self.grant(task.id, record["approvalReference"])

    def test_an_expired_grant_is_not_used_and_the_task_asks_again(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]
        self.grant(task.id, reference)
        record = self.approvals.get(task.id, reference)
        record["grantExpiresAt"] = EXPIRED
        self.approvals._write(task.id, record)
        self.resume(task.id)

        final = self.orchestrator([[self.open_url_call()]]).run(task.id)

        self.assertEqual(self.opened, [])
        self.assertIs(final.state, TaskState.WAITING_USER)

    def test_a_non_pending_reference_cannot_be_decided_twice(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        reference = self.approvals.pending(task.id)[0]["approvalReference"]
        self.grant(task.id, reference)

        with self.assertRaises(ValidationError):
            self.grant(task.id, reference)
        with self.assertRaises(ValidationError):
            self.approvals.deny(task.id, reference, denied_by="hamid")

    def test_an_unknown_reference_is_refused(self):
        task = self.make_task_with_domain()
        with self.assertRaises(ValidationError):
            self.grant(task.id, "appr-does-not-exist")

    def test_unreadable_approval_state_stops_the_run(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)

        class Unreadable:
            def has_pending(self, task_id):
                raise RuntimeError("approval directory is corrupt")

        self.resume(task.id)
        final = self.orchestrator([[self.open_url_call()]],
                                  approvals=Unreadable()).run(task.id)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertEqual(self.opened, [])

    # -- the pipeline's own guarantees still hold ------------------------------

    def test_the_approver_cannot_mint_an_approval_policy_would_reject(self):
        task = self.make_task_with_domain()
        self.orchestrator([[self.open_url_call()]]).run(task.id)
        record = self.approvals.pending(task.id)[0]
        # an operator identity is required: a grant with no decider is not one
        record["status"] = "GRANTED"
        record["grantExpiresAt"] = record["requestExpiresAt"]
        record["decidedBy"] = "   "
        self.approvals._write(task.id, record)
        self.resume(task.id)

        final = self.orchestrator([[self.open_url_call()]]).run(task.id)

        self.assertEqual(self.opened, [])
        self.assertIsNot(final.state, TaskState.DONE)


if __name__ == "__main__":
    unittest.main()
