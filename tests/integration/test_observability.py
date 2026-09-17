"""Observability (Phase 18): the complete task history is reconstructable
from durable records without model memory, and secrets never leak."""
import unittest

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel

from core import ActionRequest, ActorIdentity, Target
from core.enums import ActorType, TaskState, TargetType
from completion import CompletionEngine
from continuity import ContinuityManager, RecoveryManager
from observability import TaskObserver
from orchestration import Orchestrator


class ObservabilityTests(VerificationTestBase):

    def run_to_done(self, task, contents):
        model = FakeModel(proposals=[self.write_proposal(task, c) for c in contents])
        orchestrator = Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), model,
            recovery=RecoveryManager(self.mgr, self.store),
            continuity=ContinuityManager(self.mgr, self.store))
        return orchestrator.run(task.id, max_iterations=100)

    def write_proposal(self, task, content, secret=None):
        arguments = {"path": "/data/out/x.txt", "content": content}
        if secret is not None:
            arguments["apiKey"] = secret
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/data/out/x.txt"),
            arguments=arguments,
            reason="scripted step")

    def test_full_history_reconstructed_from_durable_records(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        final = self.run_to_done(task, ["WRONG", "HELLO"])
        self.assertIs(final.state, TaskState.DONE)
        observer = TaskObserver(self.store, self.mgr)
        history = observer.full_history(task.id)
        self.assertEqual(history["task"]["state"], "DONE")
        self.assertEqual(history["task"]["objective"], "write a file")
        self.assertEqual(len(history["actions"]), 4)  # 2 writes + 2 reads
        self.assertTrue(all(a["terminalState"] is not None
                            for a in history["actions"]))
        self.assertTrue(history["policyDecisions"])
        self.assertTrue(history["verificationResults"])
        self.assertEqual(len(history["modelCalls"]), 0)  # FakeModel is not a port
        self.assertEqual(history["task"]["usage"]["actionStepsUsed"], 4)

    def test_render_never_leaks_argument_secrets(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        model = FakeModel(proposals=[
            self.write_proposal(task, "HELLO", secret="SUPER-SECRET-API-KEY-123")])
        orchestrator = Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), model,
            recovery=RecoveryManager(self.mgr, self.store))
        orchestrator.run(task.id, max_iterations=100)
        rendered = TaskObserver(self.store, self.mgr).render(task.id)
        self.assertIn("DONE", rendered)
        self.assertNotIn("SUPER-SECRET-API-KEY-123", rendered)
        # the raw secret is nowhere in the durable records at all
        serialized = repr(self.store.journal_for(task.id).records())
        self.assertNotIn("SUPER-SECRET-API-KEY-123", serialized)

    def test_history_survives_without_model_memory(self):
        task = self.make_task(stop_at=TaskState.CREATED)
        self.run_to_done(task, ["HELLO"])
        # a fresh observer (fresh process) with no model context at all
        observer = TaskObserver(self.store, self.mgr)
        history = observer.full_history(task.id)
        self.assertEqual(history["actions"][0]["operationId"], "fs.write_file")
        self.assertEqual(history["verificationResults"][-1]["result"], "PASS")
        self.assertEqual(len(history["actions"][0]["argumentsSha256"]), 64)

    def test_tampered_journal_makes_views_fail_closed(self):
        import json
        from continuity.journal import JournalIntegrityError
        task = self.make_task(stop_at=TaskState.CREATED)
        self.run_to_done(task, ["HELLO"])
        path = self.root / task.id / "journal.jsonl"
        lines = path.read_text().splitlines()
        data = json.loads(lines[0])
        data["payload"]["reason"] = "tampered"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        with self.assertRaises(JournalIntegrityError):
            TaskObserver(self.store, self.mgr).full_history(task.id)


if __name__ == "__main__":
    unittest.main()
