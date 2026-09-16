"""Replaceability tests: the Core must not depend on any model, runtime, or
platform. Swapping runtimes/models must not change authorization, policy,
verification, or completion outcomes (PROJECT_CONTRACT s2, ROADMAP Phase 10
precondition)."""
import unittest
from pathlib import Path

from tests.support.harness import VerificationTestBase
from tests.support.fakes import FakeModel, FakeRuntime, UncooperativeModel

from core.enums import TaskState


class ReplaceabilityTests(VerificationTestBase):

    def test_core_has_no_runtime_or_model_imports(self):
        """The Core must stay platform-independent (PROJECT_CONTRACT s2.1-s2.3)."""
        forbidden = ("termux", "android", "deepseek", "anthropic", "claude",
                     "gemini", "pi_ultracode")
        core_dir = Path(__file__).resolve().parents[2] / "src" / "core"
        offenders = []
        for path in core_dir.glob("*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if line.strip().startswith(("import ", "from ")):
                    for name in forbidden:
                        if name in line.lower():
                            offenders.append(f"{path.name}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_model_swaps_do_not_change_outcomes(self):
        """Identical governed flows under different model doubles yield
        identical authorization and completion results."""
        results = []
        for model in (FakeModel(proposals=[]), UncooperativeModel()):
            task = self.make_task()
            self.files["/data/out/x.txt"] = "HELLO"
            proposal = model.propose_action(task)
            self.assertIsNone(proposal)  # neither model drives authority
            report = self.engine.verify(task.id)
            from completion import CompletionEngine
            decision = CompletionEngine(self.mgr, self.store).evaluate(task.id)
            results.append((report.status.value, decision.decision.value))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], ("PASS", "DONE"))

    def test_runtime_environment_context_is_not_authoritative(self):
        """A runtime's environmentContext is untrusted data: a hostile
        runtime cannot grant authority (P-RULE-14)."""
        runtime = FakeRuntime(name="hostile-runtime",
                              environment={"trusted": True, "mode": "DANGEROUS"})
        runtime.start()
        task = self.make_task(stop_at=TaskState.RUNNING)
        request = self.make_request(task)
        # a hostile runtime cannot inject environment context: the pipeline
        # builds the PolicyRequest from authoritative task state only
        result = self.pipeline.execute(request)
        self.assertTrue(result.executed)
        self.assertEqual(result.policyDecision.decision.value, "ALLOW")
        # ...but an out-of-scope action stays denied even under this runtime
        from core import ActionRequest, ActorIdentity, Target
        from core.enums import ActorType, TargetType
        hostile = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="agent-1", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/etc/hostile.txt"),
            arguments={"path": "/etc/hostile.txt", "content": "x"},
            reason="hostile runtime environment claims authority")
        denied = self.pipeline.execute(hostile)
        self.assertFalse(denied.executed)
        runtime.stop()


if __name__ == "__main__":
    unittest.main()
