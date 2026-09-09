"""TaskStore durability and integrity tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from continuity.task_store import TaskIntegrityError, TaskNotFoundError, TaskStore

from core import (
    CompletionContract,
    ContinuityState,
    PolicyContext,
    ResourceLimits,
    SuccessCriterion,
    TargetAuthorizationContext,
    Task,
    VerificationState,
    utcnow_iso,
)
from core.enums import EffortLevel, PermissionMode, RiskLevel, TaskState


def make_task(task_id="task-1", state=TaskState.CREATED):
    now = utcnow_iso()
    criterion = SuccessCriterion(id="c1", description="build passes", verificationMethod="run tests")
    return Task(
        id=task_id,
        schemaVersion="1.0",
        objective="make the build pass",
        requirements=[],
        successCriteria=[criterion],
        completionContract=CompletionContract(
            objective="make the build pass", successCriteria=[criterion]),
        permissionMode=PermissionMode.AUTO,
        effortLevel=EffortLevel.STANDARD,
        policyContext=PolicyContext(
            policyVersion="0.6", ruleVersion="1.0",
            permissionMode=PermissionMode.AUTO, defaultRiskLevel=RiskLevel.MEDIUM),
        targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0", createdAt=now, updatedAt=now),
        resourceLimits=ResourceLimits(actionSteps=10),
        state=state,
        verification=VerificationState(),
        continuity=ContinuityState(),
        createdAt=now,
        updatedAt=now,
    )


class TaskStoreTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = TaskStore(self.root)

    def _task_path(self, task_id):
        return self.root / task_id / "task.json"

    def test_roundtrip(self):
        task = make_task()
        self.store.save_task(task)
        loaded = self.store.load_task(task.id)
        self.assertEqual(loaded, task)

    def test_id_stability(self):
        task = make_task()
        self.store.save_task(task)
        self.assertEqual(self.store.load_task(task.id).id, task.id)

    def test_atomic_write_leaves_no_tmp_files(self):
        self.store.save_task(make_task())
        leftovers = list(self.root.rglob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_persists_across_store_instances(self):
        task = make_task()
        self.store.save_task(task)
        reloaded = TaskStore(self.root).load_task(task.id)
        self.assertEqual(reloaded, task)

    def test_missing_task_raises(self):
        with self.assertRaises(TaskNotFoundError):
            self.store.load_task("does-not-exist")

    def test_tampered_payload_detected(self):
        task = make_task()
        self.store.save_task(task)
        path = self._task_path(task.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["payload"]["objective"] = "hijacked"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(TaskIntegrityError):
            self.store.load_task(task.id)

    def test_tampered_digest_detected(self):
        task = make_task()
        self.store.save_task(task)
        path = self._task_path(task.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["integrity"]["digest"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(TaskIntegrityError):
            self.store.load_task(task.id)

    def test_malformed_json_detected(self):
        task = make_task()
        self.store.save_task(task)
        self._task_path(task.id).write_text("{not json", encoding="utf-8")
        with self.assertRaises(TaskIntegrityError):
            self.store.load_task(task.id)

    def test_schema_violating_payload_detected(self):
        task = make_task()
        self.store.save_task(task)
        path = self._task_path(task.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["payload"]["state"] = "BOGUS"
        data["integrity"]["digest"] = __import__("hashlib").sha256(
            json.dumps(data["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(TaskIntegrityError):
            self.store.load_task(task.id)

    def test_checkpoint_roundtrip(self):
        from core import Checkpoint
        task = make_task()
        checkpoint = Checkpoint(
            checkpointId="cp-1", taskId=task.id, timestamp=utcnow_iso(),
            lifecycleState=task.state, objective=task.objective)
        self.store.save_checkpoint(checkpoint)
        self.assertEqual(self.store.load_checkpoint(task.id, "cp-1"), checkpoint)

    def test_list_checkpoints_sorted(self):
        from core import Checkpoint
        task = make_task()
        for i in range(3):
            checkpoint = Checkpoint(
                checkpointId=f"cp-{i}", taskId=task.id,
                timestamp=f"2026-01-0{i + 1}T00:00:00Z",
                lifecycleState=task.state)
            self.store.save_checkpoint(checkpoint)
        ids = [cp.checkpointId for cp in self.store.list_checkpoints(task.id)]
        self.assertEqual(ids, ["cp-0", "cp-1", "cp-2"])

    def test_missing_checkpoint_raises(self):
        with self.assertRaises(TaskNotFoundError):
            self.store.load_checkpoint("task-1", "nope")

    def test_tampered_checkpoint_detected(self):
        from core import Checkpoint
        task = make_task()
        checkpoint = Checkpoint(
            checkpointId="cp-1", taskId=task.id, timestamp=utcnow_iso(),
            lifecycleState=task.state, objective=task.objective)
        self.store.save_checkpoint(checkpoint)
        path = self.root / task.id / "checkpoints" / "cp-1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["payload"]["objective"] = "hijacked"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(TaskIntegrityError):
            self.store.load_checkpoint(task.id, "cp-1")


if __name__ == "__main__":
    unittest.main()
