"""The operator CLI, end to end (Phase 14 WS9).

`run`, `status` and the criterion declaration are the operator's authority
surface: this is where success is defined, scope is declared and limits are
set. The model reaches none of it. What is tested here is that the
declaration carries through the governed path unchanged - a task declared on
the command line runs to DONE through the real Policy, pipeline, verification
and completion engines, with only the model scripted (the phone model is a
device dependency; WS11 exercises it on hardware).

The provider router is the only thing patched: `ModelRouter` over a scripted
port is the same seam the real one occupies.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from platforms.termux import cli

from core import ToolCall
from core.enums import ModelRole, TaskState
from models import ModelRouter, ScriptedModelPort


class FakeVision:
    """The vision capability, scripted: it describes, it never acts."""

    def describe_image(self, image_bytes, prompt) -> str:
        return "a screen"


class CliRunTests(unittest.TestCase):
    """`cli run` with the model scripted behind the real router."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        self.workspace = self.state / "workspace"
        self.workspace.mkdir(parents=True)
        self.note = self.workspace / "note.txt"
        self.port = ScriptedModelPort()

    # -- driving the CLI ------------------------------------------------------

    def run_cli(self, argv):
        """Run the CLI with the phone models replaced by a scripted port."""
        router = ModelRouter({role: self.port for role in ModelRole})
        with mock.patch.object(cli, "GenieXBridge", return_value=mock.Mock()), \
                mock.patch.object(cli, "build_geniex_router",
                                  return_value=(router, FakeVision())):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = cli.main(argv)
        return code, out.getvalue()

    def script(self, *calls):
        self.port._turns = [list(calls)]

    def journal(self):
        """Every journal record of every task under this state directory."""
        events = []
        for path in sorted((self.state / "tasks").glob("*/journal.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        return events

    def write_note_run(self):
        """The canonical run: the model writes a file, the operator's
        criterion checks its content."""
        self.script(ToolCall(id="tc-1", name="fs.write_file",
                             arguments={"path": self.note.as_posix(),
                                        "content": "HELLO"}))
        return self.run_cli([
            "run", "write a note",
            "--state", str(self.state),
            "--max-iterations", "8",
            "--criterion",
            f"file-content-equals:path={self.note.as_posix()}:expected=HELLO"])

    # -- the happy path -------------------------------------------------------

    def test_a_declared_criterion_is_verified_and_the_run_exits_zero(self):
        code, printed = self.write_note_run()

        self.assertEqual(code, 0)
        self.assertIn("verdict   : DONE", printed)
        self.assertEqual(self.note.read_text(encoding="utf-8"), "HELLO")
        # the operator's criterion is what was assessed, and it passed
        self.assertIn("c1 [mandatory]", printed)
        self.assertIn("-> PASS", printed)
        self.assertIn("completion: DONE", printed)
        # the model was told the tool convention, and it was told it decides
        # nothing: the instructions carry the whole tool list
        system = self.port.requests[0].messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("fs.write_file", system["content"])
        self.assertIn("do not treat the task as finished", system["content"])

    def test_the_operator_scope_is_the_only_scope_that_authorizes(self):
        # A write outside the declared workspace is denied by Policy and the
        # file is never created, however the goal is worded.
        outside = Path(tempfile.mkdtemp()) / "escape.txt"
        self.addCleanup(shutil.rmtree, outside.parent, ignore_errors=True)
        self.script(ToolCall(id="tc-1", name="fs.write_file",
                             arguments={"path": outside.as_posix(),
                                        "content": "escaped"}))
        code, _printed = self.run_cli([
            "run", "write outside the workspace",
            "--state", str(self.state),
            "--max-iterations", "4",
            "--criterion",
            f"file-content-equals:path={self.note.as_posix()}:expected=HELLO"])

        self.assertEqual(code, 1)
        self.assertFalse(outside.exists())
        denied = [event["payload"] for event in self.journal()
                  if event["eventType"] == "POLICY_DECISION"
                  and event["payload"]["decision"] == "DENY"]
        self.assertEqual([(entry["operationId"], entry["reason"]) for entry in denied],
                         [("fs.write_file", "target not in authorized scope")])

    def test_missing_evidence_is_inconclusive_and_never_done(self):
        # The criterion names a file the run never writes: the assessor has
        # nothing to read, so it reports INCONCLUSIVE - and a task that is not
        # verified can never complete.
        self.script(ToolCall(id="tc-1", name="fs.write_file",
                             arguments={"path": self.note.as_posix(),
                                        "content": "HELLO"}))
        code, printed = self.run_cli([
            "run", "write a note",
            "--state", str(self.state),
            "--max-iterations", "4",
            "--criterion",
            f"file-content-equals:path={(self.workspace / 'missing.txt').as_posix()}"
            ":expected=HELLO"])

        self.assertEqual(code, 1)
        self.assertNotIn("verdict   : DONE", printed)
        self.assertEqual(self.results(), ["INCONCLUSIVE"])
        self.assertEqual(self.completions(), [])

    def test_an_empty_expected_value_is_never_a_pass(self):
        # The declaration is accepted (the requirement is present), and the
        # empty expectation is compared literally: a criterion the operator
        # wrote by mistake fails rather than passing on nothing.
        self.script(ToolCall(id="tc-1", name="fs.write_file",
                             arguments={"path": self.note.as_posix(),
                                        "content": "HELLO"}))
        code, printed = self.run_cli([
            "run", "write a note",
            "--state", str(self.state),
            "--max-iterations", "4",
            "--criterion",
            f"file-content-equals:path={self.note.as_posix()}:expected="])

        self.assertEqual(code, 1)
        self.assertNotIn("verdict   : DONE", printed)
        self.assertEqual(self.results(), ["FAIL"])

    def results(self):
        return [event["payload"]["result"] for event in self.journal()
                if event["eventType"] == "VERIFICATION_RESULT"]

    def completions(self):
        return [event["payload"]["decision"] for event in self.journal()
                if event["eventType"] == "COMPLETION_DECISION"]


class CriterionDeclarationTests(unittest.TestCase):
    """An operator's criterion is validated when it is written, not left to
    fail later as a task that can never complete."""

    def test_each_production_method_parses(self):
        cases = {
            "file-content-equals:path=/tmp/x:expected=hello": {
                "path": "/tmp/x", "expected": "hello"},
            "ui-foreground-package-is:package=com.android.settings": {
                "package": "com.android.settings"},
            "ui-node-text-present:text=HELLO": {"text": "HELLO"},
            "screenshot-captured": {},
            "foreground-package-changed": {},
        }
        for spec, expected in cases.items():
            criterion = cli.parse_criterion(spec, 1)
            self.assertEqual(criterion.evidenceRequirements,
                             [f"{k}={v}" for k, v in expected.items()], spec)

    def test_a_value_containing_a_colon_survives(self):
        # Windows paths and times are full of colons; the value must not be
        # truncated into a requirement of its own.
        criterion = cli.parse_criterion(
            "file-content-equals:path=C:/tmp/a:expected=12:30", 3)

        self.assertEqual(criterion.evidenceRequirements,
                         ["path=C:/tmp/a", "expected=12:30"])
        self.assertEqual(criterion.id, "c3")

    def test_an_unknown_method_is_refused_at_declaration(self):
        with self.assertRaises(cli.CliError) as caught:
            cli.parse_criterion("looks-good-to-me", 1)

        self.assertIn("unknown verification method", str(caught.exception))

    def test_a_missing_requirement_is_refused_at_declaration(self):
        # without it the criterion could only ever be INCONCLUSIVE, and the
        # operator would see a task that never finishes with no explanation
        with self.assertRaises(cli.CliError) as caught:
            cli.parse_criterion("file-content-equals:path=/tmp/x", 1)

        self.assertIn("expected=...", str(caught.exception))

    def test_a_criterion_is_mandatory_unless_marked_advisory(self):
        self.assertTrue(cli.parse_criterion("screenshot-captured", 1).mandatory)
        self.assertFalse(
            cli.parse_criterion("screenshot-captured:advisory", 1).mandatory)


class CliArgumentTests(unittest.TestCase):
    """Refusals the operator sees as an exit code and a message."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def run_cli(self, argv):
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stderr.getvalue()

    def test_a_run_without_a_criterion_is_refused(self):
        code, message = self.run_cli(["run", "do something",
                                      "--state", str(self.state)])

        self.assertEqual(code, 2)
        self.assertIn("never by the model", message)

    def test_an_unknown_criterion_is_refused_before_any_task_exists(self):
        code, message = self.run_cli([
            "run", "do something", "--state", str(self.state),
            "--criterion", "vibes"])

        self.assertEqual(code, 2)
        self.assertIn("unknown verification method", message)
        self.assertEqual(list(self.state.rglob("task.json")), [])

    def test_status_without_a_task_is_refused(self):
        code, message = self.run_cli(["status", "--state", str(self.state)])

        self.assertEqual(code, 2)
        self.assertIn("no tasks under", message)

    def test_deciding_without_naming_a_reference_lists_what_is_pending(self):
        code, message = self.run_cli(["approve", "--state", str(self.state)])

        self.assertEqual(code, 2)
        self.assertIn("name the approval reference", message)

    def test_tasks_lists_an_empty_state_directory(self):
        code, printed = self.run_cli(["tasks", "--state", str(self.state)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
