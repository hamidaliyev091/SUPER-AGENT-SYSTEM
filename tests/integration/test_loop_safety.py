"""Loop safety (Phase 14 WS6): a bounded autonomous loop.

An agent loop that can run forever, or that can stop for a limit while
still looking runnable, is a hazard on a phone. These tests pin the bounds
(iterations, wall clock, repeated failures, stall, token budget) and the
two properties that matter most: every limit ends in BLOCKED with a durably
recorded reason, and an action whose terminal state never became durable is
handed to recovery instead of being retried.
"""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import ScriptedModelPort, UncooperativeModel

from continuity import RecoveryManager
from continuity.journal import JournalError
from completion import CompletionEngine
from core import Tool, ToolCall, ToolResult, utcnow_iso
from core.enums import (
    Idempotency,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
    TaskState,
)
from models import ModelPortDriver
from orchestration import Orchestrator


class TerminalFailingJournal:
    """A journal whose ACTION_TERMINAL append fails, as a device that lost
    power between an action and its audit record would."""

    def __init__(self, journal):
        self._journal = journal

    def append(self, event_type, payload, timestamp=None):
        if event_type == "ACTION_TERMINAL":
            raise JournalError("simulated terminal journal failure")
        return self._journal.append(event_type, payload, timestamp=timestamp)

    def __getattr__(self, name):
        return getattr(self._journal, name)


class LoopSafetyTests(VerificationTestBase):

    def make_orchestrator(self, model, driver=None, recovery=True):
        if driver is None and not hasattr(model, "propose_action"):
            driver = ModelPortDriver(self.mgr, self.store, model)
        return Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver or model,
            recovery=RecoveryManager(self.mgr, self.store) if recovery else None)

    def write_call(self, content="HELLO", path="/data/out/x.txt"):
        return ToolCall(id="tc-write", name="fs.write_file",
                        arguments={"path": path, "content": content})

    def driver(self, port, **kwargs):
        return ModelPortDriver(self.mgr, self.store, port, **kwargs)

    def failing_capability(self):
        """A registered operation whose tool always fails."""
        def boom(args, context):
            return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                              timestamp=utcnow_iso())
        self.tools["android.screenshot"] = Tool(
            id="android.screenshot", name="android.screenshot", description="capture",
            inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
            sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
            idempotency=Idempotency.IDEMPOTENT, execute=boom)
        return ToolCall(id="tc-shot", name="android.screenshot", arguments={})

    @staticmethod
    def stop_reasons(task):
        return [d.rationale for d in task.decisions if d.decision == "loop_stopped"]

    # -- limits ---------------------------------------------------------------

    def test_iteration_limit_blocks_with_a_durable_reason(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[])
        final = self.make_orchestrator(port).run(task.id, max_iterations=12,
                                                 stall_limit=None)

        self.assertIs(final.state, TaskState.BLOCKED)
        reasons = self.stop_reasons(final)
        self.assertEqual(len(reasons), 1)
        self.assertIn("iteration limit reached (12)", reasons[0])

    def test_deadline_blocks_the_run(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[self.write_call()])
        final = self.make_orchestrator(port).run(task.id, deadline_seconds=0)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertIn("wall-clock deadline", self.stop_reasons(final)[0])
        self.assertEqual(self.writes, [])   # nothing ran after the deadline

    def test_repeated_failure_limit_blocks(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        call = self.failing_capability()
        port = ScriptedModelPort(turns=[[call], [call], [call]])
        final = self.make_orchestrator(port).run(task.id, max_iterations=40,
                                                 stall_limit=None, failure_limit=2)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertGreaterEqual(len(final.failures), 2)
        self.assertIn("repeated-failure limit reached",
                      " ".join(self.stop_reasons(final)))

    def test_stalling_proposal_blocks_the_run(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        call = self.write_call("GOODBYE")   # succeeds, never satisfies the criterion
        port = ScriptedModelPort(turns=[[call]] * 10)
        final = self.make_orchestrator(port).run(task.id, max_iterations=60)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertIn("no progress", self.stop_reasons(final)[0])

    def test_a_silent_model_does_not_spin_forever(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        final = self.make_orchestrator(UncooperativeModel()).run(
            task.id, max_iterations=100)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertIn("no progress", self.stop_reasons(final)[0])

    def test_token_budget_blocks_the_run(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("GOODBYE")]],
                                 usage={"total_tokens": 100})
        driver = self.driver(port, token_budget=50)
        final = self.make_orchestrator(port, driver=driver).run(task.id,
                                                                max_iterations=40)

        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertIn("token budget exhausted", self.stop_reasons(final)[0])
        events = [r["eventType"] for r in self.store.journal_for(task.id).records()]
        self.assertEqual(events.count("MODEL_CALL"), 1)

    def test_a_limit_stop_is_never_reported_as_done(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])
        # the write satisfies the criterion, but the deadline stops the loop
        # before verification can decide - DONE must not be granted
        final = self.make_orchestrator(port).run(
            task.id, deadline_seconds=0, max_iterations=1)

        self.assertIsNot(final.state, TaskState.DONE)
        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertFalse(final.verification.criterionResults)

    # -- unknown side effects --------------------------------------------------

    def test_action_without_a_durable_terminal_record_is_not_retried(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        real_journal_for = self.store.journal_for
        self.store.journal_for = lambda task_id: TerminalFailingJournal(
            real_journal_for(task_id))
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])

        final = self.make_orchestrator(port).run(task.id, max_iterations=30)

        # the write happened once and must not be repeated on a guess: its
        # side effect is unknown, so recovery hands it to a human
        self.assertEqual(self.writes, ["/data/out/x.txt"])
        self.assertIs(final.state, TaskState.WAITING_USER)
        events = [r["eventType"] for r in real_journal_for(task.id).records()]
        self.assertIn("RECOVERY_DECISION", events)

    def test_action_without_a_terminal_record_blocks_without_recovery(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        real_journal_for = self.store.journal_for
        self.store.journal_for = lambda task_id: TerminalFailingJournal(
            real_journal_for(task_id))
        port = ScriptedModelPort(turns=[[self.write_call("HELLO")]])

        final = self.make_orchestrator(port, recovery=False).run(task.id)

        self.assertEqual(self.writes, ["/data/out/x.txt"])
        self.assertIs(final.state, TaskState.BLOCKED)
        self.assertIn("not durable", self.stop_reasons(final)[0])


if __name__ == "__main__":
    unittest.main()
