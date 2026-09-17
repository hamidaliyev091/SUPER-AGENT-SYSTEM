"""ObservationStore: durable, tamper-evident observations and their
artifacts.

An observation is the loop's memory - the model reasons about it on the
next turn - so it carries the same integrity envelope as the rest of the
authoritative state, and its retention is bounded so a task that observes
forever cannot fill the device.
"""
import json
import tempfile
import unittest
from pathlib import Path

from continuity.observation_store import ObservationStore, render_observation
from continuity.task_store import TaskIntegrityError


class ObservationStoreTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = ObservationStore(self.root)
        self.task = "task-1"

    def record(self, summary, **kwargs):
        return self.store.record(self.task, "action", summary, {"summary": summary},
                                 **kwargs)

    def record_paths(self):
        return sorted((self.root / self.task / "observations").glob("*.json"))

    # -- records --------------------------------------------------------------

    def test_a_record_survives_a_restart_and_keeps_its_identity(self):
        written = self.record("launched settings", action_id="act-1")

        record = ObservationStore(self.root).all_records(self.task)[0]

        self.assertEqual(record["observationId"], written["observationId"])
        self.assertEqual(record["actionId"], "act-1")
        self.assertEqual(record["kind"], "action")
        self.assertEqual(record["data"], {"summary": "launched settings"})

    def test_a_tampered_record_raises_rather_than_reading_plausible(self):
        # A history that can be edited is not evidence: the envelope is
        # verified on read and a mismatch is an error, never a silent skip.
        self.record("launched settings")
        path = self.record_paths()[0]
        envelope = json.loads(path.read_text())
        envelope["payload"]["summary"] = "wrote the policy file"
        path.write_text(json.dumps(envelope))

        with self.assertRaises(TaskIntegrityError):
            self.store.all_records(self.task)

    def test_records_read_back_in_the_order_they_happened(self):
        for index in range(3):
            self.record(f"step {index}")

        summaries = [record["summary"] for record in self.store.all_records(self.task)]

        self.assertEqual(summaries, ["step 0", "step 1", "step 2"])

    def test_recent_is_bounded_and_keeps_the_newest(self):
        for index in range(5):
            self.record(f"step {index}")

        recent = self.store.recent(self.task, limit=2)

        self.assertEqual([record["summary"] for record in recent],
                         ["step 3", "step 4"])

    def test_a_non_positive_limit_reads_nothing(self):
        self.record("step 0")

        self.assertEqual(self.store.recent(self.task, limit=0), [])
        self.assertEqual(self.store.recent(self.task, limit=-1), [])

    def test_latest_is_the_newest_record(self):
        self.record("first")
        self.record("second")

        self.assertEqual(self.store.latest(self.task)["summary"], "second")

    def test_an_unknown_task_has_no_observations(self):
        self.assertEqual(self.store.all_records("ghost"), [])
        self.assertIsNone(self.store.latest("ghost"))

    # -- artifacts ------------------------------------------------------------

    def test_an_artifact_is_content_addressed_and_written_once(self):
        first = self.store.write_artifact(self.task, b"\x89PNG-first")
        second = self.store.write_artifact(self.task, b"\x89PNG-first")

        self.assertEqual(first, second)
        self.assertEqual(len(list((self.root / self.task / "artifacts").iterdir())), 1)
        self.assertEqual(self.store.read_artifact(self.task, first["sha256"]),
                         b"\x89PNG-first")

    def test_a_corrupted_artifact_is_not_returned_as_valid(self):
        descriptor = self.store.write_artifact(self.task, b"\x89PNG-first")
        path = Path(descriptor["path"])
        path.write_bytes(b"\x89PNG-tampered")

        self.assertIsNone(self.store.read_artifact(self.task, descriptor["sha256"]))

    def test_write_artifact_refuses_something_that_is_not_bytes(self):
        with self.assertRaises(ValueError):
            self.store.write_artifact(self.task, "not bytes")

    # -- retention ------------------------------------------------------------

    def test_sweep_keeps_the_newest_and_reports_what_it_dropped(self):
        for index in range(5):
            self.record(f"step {index}")

        removed = self.store.sweep(self.task, keep=2)

        self.assertEqual(removed, 3)
        self.assertEqual([record["summary"] for record in self.store.all_records(self.task)],
                         ["step 3", "step 4"])

    def test_sweep_removes_only_the_artifacts_nothing_references(self):
        kept = self.store.write_artifact(self.task, b"\x89PNG-kept")
        orphan = self.store.write_artifact(self.task, b"\x89PNG-orphan")
        self.record("captured the screen", artifact=kept)

        self.store.sweep_artifacts(self.task)

        artifacts = {path.name for path in (self.root / self.task / "artifacts").iterdir()}
        self.assertEqual(artifacts, {Path(kept["path"]).name})
        self.assertNotIn(Path(orphan["path"]).name, artifacts)

    def test_sweep_on_an_unknown_task_is_a_no_op(self):
        self.assertEqual(self.store.sweep("ghost"), 0)
        self.assertEqual(self.store.sweep_artifacts("ghost"), 0)

    # -- rendering ------------------------------------------------------------

    def test_a_record_is_rendered_as_bounded_text(self):
        record = self.record("launched settings",
                             artifact={"path": "/x/abc.png", "bytes": 10,
                                       "sha256": "a" * 64})

        rendered = render_observation(record)

        self.assertIn("launched settings", rendered)
        self.assertIn("abc.png", rendered)
        self.assertIn("10 bytes", rendered)

    def test_rendering_bounds_what_the_model_can_be_given(self):
        record = self.store.record(self.task, "action", "large", {"blob": "x" * 5000})

        rendered = render_observation(record, max_chars=100)

        self.assertIn("[truncated", rendered)
        self.assertLess(len(rendered), 400)


if __name__ == "__main__":
    unittest.main()
